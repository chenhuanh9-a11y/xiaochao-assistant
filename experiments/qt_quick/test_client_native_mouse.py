"""Raw mouse ABI/lifecycle and physical sampling; no actual input registration."""
import ctypes
from ctypes import wintypes as W
import unittest

from client_native_mouse import RawInputDevice, RawInputHeader, RawMouse, RawMouseObserver, mouse_phases


class Function:
    def __init__(self, function):
        self.function = function
    def __call__(self, *args):
        return self.function(*args)


class Native:
    def __init__(self):
        self.calls = []
        self.conflict = False
        self.fail_remove = False
        self.awareness = 2
        self.mouse = RawMouse()
        self.CreateWindowExW = Function(self.create)
        self.DestroyWindow = Function(lambda hwnd: self.record("destroy", hwnd))
        self.DefWindowProcW = Function(lambda *args: self.record("default", *args))
        self.RegisterRawInputDevices = Function(self.register)
        self.GetRegisteredRawInputDevices = Function(self.registered)
        self.GetRawInputData = Function(self.raw)
        self.GetThreadDpiAwarenessContext = Function(lambda: -4)
        self.GetAwarenessFromDpiAwarenessContext = Function(lambda context: self.awareness)
        self.GetForegroundWindow = Function(lambda: 42)

    def record(self, kind, *args):
        self.calls.append((kind, args))
        return 1

    def create(self, *args):
        self.record("create", *args)
        return 321

    def register(self, pointer, count, size):
        row = ctypes.cast(pointer, ctypes.POINTER(RawInputDevice)).contents
        self.record("register", row.usagePage, row.usage, row.flags, row.target)
        return 0 if self.fail_remove and row.flags == 1 else 1

    def registered(self, rows, count, size):
        ctypes.cast(count, ctypes.POINTER(W.UINT)).contents.value = int(self.conflict)
        if rows is not None and self.conflict:
            rows[0] = RawInputDevice(1, 2, 0, 123)
        return int(self.conflict) if rows is not None else 0

    def raw(self, handle, command, buffer, count, header_size):
        total = ctypes.sizeof(RawInputHeader) + ctypes.sizeof(RawMouse)
        ctypes.cast(count, ctypes.POINTER(W.UINT)).contents.value = total
        if buffer is None:
            return 0
        header = RawInputHeader(0, total, None, 1)
        ctypes.memmove(buffer, bytes(header) + bytes(self.mouse), total)
        return total

class RawMouseTests(unittest.TestCase):
    def test_abi_and_button_edges_do_not_use_device_delta_as_pixels(self):
        self.assertEqual(ctypes.sizeof(RawMouse), 24)
        self.assertEqual(RawMouse.buttonUnion.offset, 4)
        self.assertEqual(ctypes.sizeof(RawInputHeader), 24 if ctypes.sizeof(ctypes.c_void_p) == 8 else 16)
        native = Native()
        events = []
        observer = RawMouseObserver(lambda x, y, foreground, **kw: events.append((x, y, foreground, kw)),
                                    lambda foreground: foreground == 42, user=native, clock=lambda: 10., ticks=lambda: 1000)
        observer.start()
        native.mouse.lastX = 8000
        native.mouse.lastY = -5000
        message = W.MSG(hWnd=321, message=0xff, wParam=1, lParam=999, time=980, pt=W.POINT(200,150))
        for flags in (1, 0, 2):
            native.mouse.buttonUnion.buttons.flags = flags
            self.assertTrue(observer.handle(message))
        self.assertEqual([e[3]["phase"] for e in events], ["down", "move", "move", "up"])
        self.assertTrue(all(e[:3] == (200, 150, 42) for e in events))
        self.assertTrue(all(e[3]["created_at"] == 9.98 for e in events))
        self.assertEqual(len([c for c in native.calls if c[0] == "default"]), 3)
        observer.stop()

    def test_old_queued_press_is_not_restamped_as_new_input(self):
        native = Native()
        events, cancellations = [], []
        observer = RawMouseObserver(lambda *a, **kw: events.append((a,kw)), lambda fg: True,
            user=native, cancel=cancellations.append, clock=lambda: 10., ticks=lambda: 5000)
        observer.start()
        native.mouse.buttonUnion.buttons.flags = 1
        observer.handle(W.MSG(hWnd=321, message=0xff, wParam=1, lParam=999,
                                        time=1000, pt=W.POINT(200,150)))
        self.assertEqual(events, [])
        self.assertEqual(cancellations, ["raw-mouse-input-expired"])
        self.assertEqual(len([c for c in native.calls if c[0] == "default"]), 1)
        observer.stop()

    def test_message_only_registration_preserves_legacy_and_releases_own_target(self):
        native = Native()
        observer = RawMouseObserver(lambda *a, **k: None, lambda fg: False, user=native)
        observer.start()
        observer.start()
        create = next(c[1] for c in native.calls if c[0] == "create")
        self.assertEqual(create[1], "STATIC")
        self.assertEqual(create[3], 0)  # no WS_VISIBLE
        self.assertEqual(create[8].value, ctypes.c_void_p(-3).value)
        self.assertEqual([c[1] for c in native.calls if c[0] == "register"], [(1, 2, 0x100, 321)])
        observer.handle(W.MSG(hWnd=321, message=0xff, wParam=0, lParam=999))
        self.assertEqual(len([c for c in native.calls if c[0] == "default"]), 1)
        observer.stop()
        observer.stop()
        self.assertEqual([c[1] for c in native.calls if c[0] == "register"],
                         [(1, 2, 0x100, 321), (1, 2, 1, None)])
        self.assertEqual(len([c for c in native.calls if c[0] == "destroy"]), 1)

    def test_real_win32_message_for_another_target_or_type_is_not_consumed(self):
        observer = RawMouseObserver(lambda *a, **kw: None, lambda fg: True, user=Native())
        observer.start()
        self.assertFalse(observer.handle(W.MSG(hWnd=123, message=0xff)))
        self.assertFalse(observer.handle(W.MSG(hWnd=321, message=0x8001)))
        observer.stop()

    def test_existing_same_process_mouse_owner_is_not_overwritten(self):
        native = Native()
        native.conflict = True
        observer = RawMouseObserver(lambda *a, **k: None, lambda fg: False, user=native)
        with self.assertRaisesRegex(OSError, "already has an owner"):
            observer.start()
        observer.stop()
        self.assertFalse(native.calls)

    def test_remove_failure_does_not_skip_owned_window_cleanup(self):
        native = Native()
        observer = RawMouseObserver(lambda *a, **k: None, lambda fg: False, user=native)
        observer.start()
        native.fail_remove = True
        with self.assertRaisesRegex(OSError, "raw-input remove"):
            observer.stop()
        self.assertTrue(any(call[0] == "destroy" for call in native.calls))
        self.assertIsNone(observer.hwnd)
        self.assertTrue(observer.registered)  # Never misreport a failed REMOVE.

    def test_unverified_dpi_context_is_not_changed_or_used_as_physical_coordinates(self):
        native = Native()
        native.awareness = 0
        observer = RawMouseObserver(lambda *a, **k: None, lambda fg: False, user=native)
        with self.assertRaisesRegex(OSError, "per-monitor DPI"):
            observer.start()
        self.assertFalse(native.calls)


if __name__ == "__main__":
    unittest.main()
