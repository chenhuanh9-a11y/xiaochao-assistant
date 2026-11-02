"""Real native structs/decoder -> Qt queue -> backend -> Coordinator.

Only the OS thread/window/pointer and session runtime boundaries are fixtures.
No hook is installed and no keyboard/game input is sent.
"""
import ctypes
from ctypes import wintypes as W
from pathlib import Path
import time
from types import SimpleNamespace
import unittest

from PySide6.QtCore import QEvent, QCoreApplication, Qt, QUrl
from PySide6.QtGui import QKeyEvent
from PySide6.QtQuick import QQuickView
from unittest.mock import Mock, patch

from client_native_keys import NativeKey
from client_input import NativeShortcutBackend
import test_client_shortcut_delivery as fixtures
from test_client_settings import visual_item
from test_client_native_mouse import Function, Native


class ModifierShortcutTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        fixtures.ShortcutDeliveryTests.setUpClass()
        cls.app = fixtures.ShortcutDeliveryTests.app

    setUp = fixtures.ShortcutDeliveryTests.setUp
    configure = fixtures.ShortcutDeliveryTests.configure
    mouse = fixtures.ShortcutDeliveryTests.mouse

    def key(self, vk, down=True, scan=0, flags=0, message=None, drain=True):
        packet = NativeKey(vk, scan, flags, 0, 0)
        alt = vk in (0x12, 0xa4, 0xa5)
        if message is None:
            message = (0x104 if down else 0x105) if alt else (0x100 if down else 0x101)
        self.source._receive_key_message(message, ctypes.addressof(packet), self.foreground)
        if drain:
            self.app.processEvents()

    def physical(self, vk, down=True, drain=True):
        scans = {0xa0: 0x2a, 0xa1: 0x36, 0xa2: 0x1d, 0xa3: 0x1d, 0xa4: 0x38, 0xa5: 0x38,
                 0x2e: 0x53, 9: 0x0f, ord("C"): 0x2e}
        flags = int(vk in (0xa3, 0xa5, 0x2e)) | (0x20 if down and vk in (0xa4, 0xa5) else 0)
        self.key(vk, down, scans[vk], flags | (0 if down else 0x80),
                 0x104 if down and vk in (0xa4, 0xa5) else 0x100 if down else 0x101, drain)
        if hasattr(self, "physical_levels"):
            if down: self.physical_levels.add(vk)
            else: self.physical_levels.discard(vk)

    def test_tap_duration_short_long_and_499_500_501_ms_both_sides(self):
        now = time.monotonic()+1
        clock = SimpleNamespace(monotonic=lambda: now)
        with patch("client_native_keys.time", clock), patch("client_input.time", clock):
            for name, sides in (("SHIFT", (0xa0, 0xa1)), ("CTRL", (0xa2, 0xa3)), ("ALT", (0xa4, 0xa5))):
                self.configure({"map": name})
                for vk in sides:
                    for duration, accepted in ((.1, True), (.499, True), (.5, True), (.501, False), (1., False)):
                        with self.subTest(name=name, vk=vk, duration=duration):
                            start, count = now, len(self.maps)
                            self.physical(vk)
                            now = start+duration
                            self.physical(vk, False)
                            self.assertEqual(len(self.maps), count+int(accepted))
                            now += 1

    def test_repeat_down_does_not_restart_tap_duration(self):
        self.configure({"map": "CTRL"})
        now = time.monotonic()+1
        clock = SimpleNamespace(monotonic=lambda: now)
        with patch("client_native_keys.time", clock), patch("client_input.time", clock):
            for duration, accepted in ((.499, True), (.6, False)):
                with self.subTest(duration=duration):
                    start, count = now, len(self.maps)
                    self.physical(0xa3)
                    now = start+.45
                    self.physical(0xa3)
                    now = start+duration
                    self.physical(0xa3, False)
                    self.assertEqual(len(self.maps), count+int(accepted))
                    now += 1

    def test_tap_duration_uses_source_edges_not_gui_queue_delay(self):
        self.configure({"map": "ALT"})
        now = time.monotonic()+1
        clock = SimpleNamespace(monotonic=lambda: now)
        with patch("client_native_keys.time", clock), patch("client_input.time", clock):
            for duration, gui_delay, accepted in ((.1, 1.2, True), (.6, .1, False)):
                with self.subTest(duration=duration, gui_delay=gui_delay):
                    start, count = now, len(self.maps)
                    self.physical(0xa5, drain=False)
                    now = start+duration
                    self.physical(0xa5, False, drain=False)
                    self.assertEqual(len(self.maps), count)
                    now += gui_delay  # Both edges consumed in the same Qt turn.
                    self.app.processEvents()
                    self.assertEqual(len(self.maps), count+int(accepted))
                    now += 1

    def test_timed_taps_keep_combo_mouse_overlap_and_altgr_cancellation(self):
        now = time.monotonic()+1
        clock = SimpleNamespace(monotonic=lambda: now)
        with patch("client_native_keys.time", clock), patch("client_input.time", clock):
            for activity in ("key", "click", "wheel", "overlap", "altgr"):
                self.configure({"map": "CTRL" if activity == "altgr" else "SHIFT"})
                start = now
                if activity == "altgr":
                    self.key(0xa2, True, 0x21d, 0, 0x100)
                    now += .05
                    self.physical(0xa5); self.physical(0xa5, False)
                    now = start+.1
                    self.key(0xa2, False, 0x21d, 0x80, 0x101)
                else:
                    self.physical(0xa0); now += .05
                    if activity == "key": self.physical(ord("C")); self.physical(ord("C"), False)
                    elif activity == "click": self.mouse(1); self.mouse(2)
                    elif activity == "wheel": self.mouse(0x400)
                    else: self.physical(0xa1); self.physical(0xa1, False)
                    now = start+.1
                    self.physical(0xa0, False)
                self.assertFalse(self.maps, activity)
                now += 1

    def test_lost_ups_and_long_tap_allow_the_next_short_tap(self):
        self.configure({"map": "SHIFT"})
        self.physical_levels = set()
        self.source._user = SimpleNamespace(GetAsyncKeyState=lambda vk: 0x8000 if vk in self.physical_levels else 0)
        now = time.monotonic()+1
        clock = SimpleNamespace(monotonic=lambda: now)
        with patch("client_native_keys.time", clock), patch("client_input.time", clock):
            for vk in (0xa2, 0xa4, 0x2e): self.physical(vk)
            self.physical_levels.clear()  # Missing key-up packets.
            now += 1
            self.physical(0xa0); now += .1; self.physical(0xa0, False)
            self.assertEqual(len(self.maps), 1)
            now += 1
            self.physical(0xa0); now += 1; self.physical(0xa0, False)
            self.assertEqual(len(self.maps), 1)
            now += 1
            self.physical(0xa0); now += .1; self.physical(0xa0, False)
            self.assertEqual(len(self.maps), 2)

    def test_secure_chord_missing_ups_recovers_three_shift_taps(self):
        for reset in (lambda: None,
                      lambda: (self.backend.disable(), self.backend.enable()),
                      lambda: (self.backend.suspendGameBinding(), self.backend.resumeGameBinding())):
            self.configure({"map": "SHIFT"})
            # Hook packets arrive before this key's asynchronous state updates.
            levels = set()
            def read(vk):
                return 0x8000 if vk in levels else 0
            self.source._user = SimpleNamespace(GetAsyncKeyState=read)
            for vk in (0xa2, 0xa4, 0x2e):
                self.physical(vk); levels.add(vk)
            levels.clear()  # secure desktop swallowed all three key-up packets
            reset()
            start = len(self.maps)
            for index in range(3):
                self.physical(0xa0); levels.add(0xa0)
                self.physical(0xa0, False); levels.discard(0xa0)
                self.assertEqual(len(self.maps), start+index+1)

    def test_reconcile_keeps_real_held_keys_and_fails_closed_on_query_exception(self):
        self.configure({"map": "SHIFT"})
        levels, queried = set(), []
        def read(vk):
            queried.append(vk)
            if vk == 0xa0:
                raise AssertionError("must not query the current callback key")
            return 0x8000 if vk in levels else 0
        self.source._user = SimpleNamespace(GetAsyncKeyState=read)
        self.physical(ord("C")); levels.add(ord("C"))
        self.physical(0xa0); self.physical(0xa0, False)
        self.assertFalse(self.maps)
        # Query failure must retain the blocker and suppress this whole tap.
        self.source._user.GetAsyncKeyState = Mock(side_effect=OSError("fixture state unavailable"))
        self.physical(0xa0); self.physical(0xa0, False)
        self.assertFalse(self.maps)
        self.source._user.GetAsyncKeyState = read
        levels.clear()
        self.physical(0xa0); self.physical(0xa0, False)
        self.assertEqual(len(self.maps), 1)
        self.assertNotIn(0xa0, queried)

    def test_physical_modifiers_combinations_reverse_release_overlap_and_altgr(self):
        self.physical_levels = set()
        self.source._user = SimpleNamespace(GetAsyncKeyState=lambda vk: 0x8000 if vk in self.physical_levels else 0)
        for name, sides in (("SHIFT", (0xa0, 0xa1)), ("CTRL", (0xa2, 0xa3)), ("ALT", (0xa4, 0xa5))):
            self.configure({"map": name})
            for vk in sides:
                count = len(self.maps)
                self.physical(vk); self.physical(vk, False)
                self.assertEqual(len(self.maps), count+1)
                for reverse in (False, True):
                    count = len(self.maps)
                    self.physical(vk); self.physical(ord("C"))
                    for released in ((vk, ord("C")) if reverse else (ord("C"), vk)):
                        self.physical(released, False)
                    self.assertEqual(len(self.maps), count)
            count = len(self.maps)
            self.physical(sides[0]); self.physical(sides[1])
            self.physical(sides[0], False); self.physical(sides[1], False)
            self.assertEqual(len(self.maps), count)
        for name in ("CTRL", "ALT"):
            for reverse in (False, True):
                self.configure({"map": name})
                count = len(self.maps)
                self.key(0xa2, True, 0x21d, 0, 0x100)  # AltGr synthetic LCtrl form
                self.physical_levels.add(0xa2)
                self.physical(0xa5)
                for vk in ((0xa2, 0xa5) if reverse else (0xa5, 0xa2)):
                    if vk == 0xa2:
                        self.key(vk, False, 0x21d, 0x80, 0x101)
                        self.physical_levels.discard(vk)
                    else: self.physical(vk, False)
                self.assertEqual(len(self.maps), count)

    def test_left_right_modifiers_fire_once_only_on_clean_release(self):
        for name, sides in (("SHIFT", (0xa0, 0xa1)), ("CTRL", (0xa2, 0xa3)), ("ALT", (0xa4, 0xa5))):
            self.configure({"map": name})
            for vk in sides:
                count = len(self.maps)
                self.key(vk); self.key(vk)
                self.assertEqual(len(self.maps), count)
                self.key(vk, False)
                self.assertEqual(len(self.maps), count+1, (name, vk))
                self.key(vk, False)
                self.assertEqual(len(self.maps), count+1)

    def test_generic_native_forms_use_the_same_physical_release(self):
        for name, generic, scan, flags, side in (("SHIFT", 0x10, 0x36, 0, 0xa1),
                ("CTRL", 0x11, 0, 1, 0xa3), ("ALT", 0x12, 0, 1, 0xa5)):
            self.configure({"map": name})
            count = len(self.maps)
            self.key(generic, scan=scan, flags=flags)
            self.assertEqual(len(self.maps), count)
            self.key(side, False)
            self.assertEqual(len(self.maps), count+1)

    def test_alt_tab_ctrl_letter_shift_letter_and_other_modifiers_do_not_fire(self):
        for name, vk, other in (("ALT", 0xa4, 9), ("CTRL", 0xa2, ord("C")),
                               ("SHIFT", 0xa1, ord("Z")), ("CTRL", 0xa3, 0xa0)):
            self.configure({"map": name})
            count = len(self.maps)
            self.key(vk); self.key(other); self.key(other, False); self.key(vk, False)
            self.assertEqual(len(self.maps), count)
            self.key(vk); self.key(vk, False)
            self.assertEqual(len(self.maps), count+1)

    def test_unbound_key_already_down_cannot_become_a_modifier_tap(self):
        self.configure({"map": "CTRL"})
        self.key(ord("J")); self.key(0xa2); self.key(0xa2, False); self.key(ord("J"), False)
        self.assertFalse(self.maps)

    def test_management_modifier_is_native_owned_even_in_assistant_foreground(self):
        self.configure({"toggle-panel": "ALT"})
        self.coordinator.toggleControlsKeyboard = Mock()
        self.foreground = 77
        self.key(0xa5)
        self.assertFalse(self.actions)
        self.key(0xa5, False)
        self.assertEqual(self.actions, ["toggle-panel"])
        self.coordinator.toggleControlsKeyboard.assert_called_once()

    def test_focus_suspend_disable_recording_and_generation_cancel_pending_taps(self):
        self.configure({"map": "SHIFT"})
        self.key(0xa0); self.foreground = 77; self.key(0xa0, False)
        self.foreground = 42
        self.assertFalse(self.maps)
        for change in (lambda: (self.backend.suspendGameBinding(), self.backend.resumeGameBinding()),
                       lambda: (self.backend.disable(), self.backend.enable()),
                       lambda: (self.backend.setEditingBindings(True), self.backend.setEditingBindings(False)),
                       lambda: (self.backend.setTextEntryActive(True), self.backend.setTextEntryActive(False))):
            self.key(0xa0); change(); self.key(0xa0, False)
            self.assertFalse(self.maps)
        self.key(0xa0); self.configure({"map": "SHIFT"}); self.key(0xa0, False)
        self.assertFalse(self.maps)
        self.key(0xa0); self.key(0xa0, False)
        self.assertEqual(len(self.maps), 1)

    def test_mouse_click_or_wheel_while_modifier_held_is_not_a_solo_tap(self):
        for name, vk in (("SHIFT", 0xa0), ("CTRL", 0xa2), ("ALT", 0xa5)):
            self.configure({"map": name})
            for down, up in ((1, 2), (4, 8), (16, 32), (64, 128), (256, 512), (0x400, 0), (0x800, 0)):
                self.key(vk); self.mouse(down)
                if up: self.mouse(up)
                self.key(vk, False)
                self.assertFalse(self.maps, (name, down))

    def test_release_without_accepted_press_or_stale_release_does_not_fire(self):
        self.configure({"map": "CTRL"})
        self.key(0xa2, False)
        self.assertFalse(self.maps)
        self.key(0xa2)
        from unittest.mock import patch
        old = time.monotonic()-10
        with patch("client_native_keys.time", SimpleNamespace(monotonic=lambda: old)):
            self.key(0xa2, False)
        self.assertFalse(self.maps)
        self.key(0xa2); self.key(0xa2, False)
        self.assertEqual(len(self.maps), 1)

    def test_unavailable_mouse_observer_warns_and_only_solo_modifiers_pause(self):
        self.configure({"map": "CTRL", "candidate-prev": "K"})
        self.source.pointer_installed = False
        self.backend._pointer_failed("fixture raw mouse unavailable")
        self.assertIn("单独 Alt、Shift、Ctrl", self.backend.message)
        self.key(0xa2); self.key(0xa2, False)
        self.assertFalse(self.maps)
        self.key(ord("K")); self.key(ord("K"), False)
        self.assertEqual(self.actions, ["candidate-prev"])
        self.source.pointer_installed = True
        self.key(0xa2); self.key(0xa2, False)
        self.assertEqual(len(self.maps), 1)

    def test_run_raw_decode_failure_cancels_each_pending_modifier_tap(self):
        # Execute the actual source loop, callback and RawMouseObserver decoder.
        # Only DLL calls/message delivery are fixtures: no OS hook/input/window.
        for name, vk in (("SHIFT", 0xa0), ("CTRL", 0xa3), ("ALT", 0xa5)):
            with self.subTest(modifier=name):
                self.configure({"map": name})
                user = Native()
                calls, snapshots, failures = [], [], []
                self.source.pointerFailed.connect(failures.append)
                self.addCleanup(lambda slot=failures.append: self.source.pointerFailed.disconnect(slot))
                callback = None

                def install(kind, function, module, thread):
                    nonlocal callback
                    callback = function
                    calls.append("install-fixture")
                    return 123

                def key(down):
                    packet = NativeKey(vk, 0, 0, 0, 0)
                    message = (0x104 if down else 0x105) if name == "ALT" else (0x100 if down else 0x101)
                    callback(0, message, ctypes.addressof(packet))
                    self.app.processEvents()

                def fail_decode(*args):
                    raise OSError("fixture raw decode failure")

                # Repeat after a successful fresh tap: cancellation must not be
                # conditional on the one-shot diagnostic flag.
                steps = iter(("press", "raw", "release", "fresh", "press", "raw", "release", "fresh"))
                def get_message(pointer, *args):
                    step = next(steps, None)
                    if step is None:
                        return 0
                    if step == "raw":
                        message = ctypes.cast(pointer, ctypes.POINTER(W.MSG)).contents
                        message.hWnd, message.message = 321, 0xff
                        message.wParam, message.lParam, message.time = 1, 999, 1000
                    else:
                        if step == "fresh":
                            key(True); key(False)
                        else:
                            key(step == "press")
                        if step != "press":
                            snapshots.append(len(self.maps))
                        ctypes.cast(pointer, ctypes.POINTER(W.MSG)).contents.message = 0x8001
                    return 1

                for attr, function in dict(SetWindowsHookExW=install,
                        CallNextHookEx=lambda *args: calls.append("pass-through") or 7,
                        UnhookWindowsHookEx=lambda *args: calls.append("unhook") or 1,
                        GetAsyncKeyState=lambda vk: 0, PeekMessageW=lambda *args: 0,
                        GetMessageW=get_message, TranslateMessage=lambda *args: 1,
                        DispatchMessageW=lambda *args: 1, PostThreadMessageW=lambda *args: 1,
                        GetRawInputData=fail_decode).items():
                    setattr(user, attr, Function(function))
                kernel = SimpleNamespace(GetCurrentThreadId=Function(lambda: 7),
                    GetModuleHandleW=Function(lambda *args: 1), GetTickCount64=Function(lambda: 1000))
                start = len(self.maps)
                self.source._pointer_failure_reported = False
                with patch("client_native_keys.ctypes.WinDLL",
                           side_effect=lambda dll, **kw: user if dll == "user32" else kernel):
                    self.source._run()
                self.assertEqual(self.source.last_error, "")
                self.assertEqual(snapshots, [start, start+1, start+1, start+2])
                self.assertEqual(len(failures), 1)
                self.assertIn("fixture raw decode failure", failures[0])
                self.assertEqual(calls.count("pass-through"), 8)
                self.assertEqual(calls.count("unhook"), 1)
                self.assertEqual(len([call for call in user.calls if call[0] == "default"]), 2)

    def test_recorded_modifier_chord_without_primary_is_explicitly_rejected(self):
        self.settings.beginShortcutCapture("hotkey")
        self.settings.captureShortcutKey(Qt.Key_Control, Qt.ControlModifier.value, "", False)
        self.settings.captureShortcutKey(Qt.Key_Alt,
            Qt.ControlModifier.value | Qt.AltModifier.value, "", True)
        self.assertFalse(self.settings.shortcutCandidateReady)
        self.assertIn("需要一个主键", self.settings.shortcutMessage)
        self.assertFalse(self.settings.confirmShortcutCapture())
        self.settings.cancelShortcutCapture()
        self.assertEqual(self.settings.persistedValues["hotkey"], "G")

    def test_listener_initial_held_modifier_needs_release_then_fresh_press(self):
        self.configure({"map": "CTRL"})
        self.source._held = {0xa2}
        self.source._tap_pressed = {0xa2}
        self.backend._hook_started(dict(held=frozenset({0xa2}), generation=self.source.generation))
        self.key(0xa2); self.key(0xa2, False)
        self.assertFalse(self.maps)
        self.key(0xa2); self.key(0xa2, False)
        self.assertEqual(len(self.maps), 1)

    def test_actual_qml_modifier_record_save_restart_and_native_delivery(self):
        # A fresh picker is unbound; recording a bare modifier needs no
        # preliminary workaround or unrelated settings write.
        self.assertEqual(self.settings.values["actionHotkeys"][9], "")
        view = QQuickView()
        view.rootContext().setContextProperty("settingsService", self.settings)
        view.setSource(QUrl.fromLocalFile(str(Path(__file__).with_name("ClientSettings.qml"))))
        view.resize(720, 740); view.show(); self.app.processEvents()
        try:
            panel = view.rootObject()
            for key, name, vk, own in ((Qt.Key_Alt, "ALT", 0xa4, Qt.AltModifier),
                    (Qt.Key_Shift, "SHIFT", 0xa1, Qt.ShiftModifier),
                    (Qt.Key_Control, "CTRL", 0xa3, Qt.ControlModifier)):
                visual_item(panel, "record_hotkey").clicked.emit(); self.app.processEvents()
                QCoreApplication.sendEvent(view, QKeyEvent(QEvent.KeyPress, key, Qt.NoModifier))
                QCoreApplication.sendEvent(view, QKeyEvent(QEvent.KeyRelease, key, own))
                self.assertEqual(self.settings.shortcutCandidate, name)
                visual_item(panel, "shortcutCaptureConfirm").clicked.emit()
                self.storage.wait(lambda: not self.settings.busy)
                self.assertEqual(self.storage.service().persistedValues["hotkey"], name)
                self.assertEqual(self.backend.snapshot["bindings"]["map"], name)
                count = len(self.maps)
                self.key(vk); self.key(vk, False)
                self.assertEqual(len(self.maps), count+1)
            # A real service reload and fresh source/backend, not an old draft.
            restored = self.storage.service()
            source2 = fixtures.FixtureSource()
            backend2 = NativeShortcutBackend(self.native, source=source2)
            self.addCleanup(backend2.shutdown)
            self.assertTrue(backend2.configureSettings(restored.persistedValues))
            backend2.bindWindow(42); backend2.enable(); backend2.enableManagement()
            delivered = []
            backend2.actionTriggered.connect(delivered.append)
            for down in (True, False):
                packet = NativeKey(0xa2, 0, 0, 0, 0)
                source2._receive_key_message(0x100 if down else 0x101, ctypes.addressof(packet), 42)
                self.app.processEvents()
                self.assertEqual(delivered, [] if down else ["map"])
            visual_item(panel, "record_action_0").clicked.emit(); self.app.processEvents()
            for kind in (QEvent.KeyPress, QEvent.KeyRelease):
                QCoreApplication.sendEvent(view, QKeyEvent(kind, Qt.Key_Alt, Qt.NoModifier))
            self.assertEqual(self.settings.shortcutCandidate, "ALT")
            visual_item(panel, "shortcutCaptureConfirm").clicked.emit()
            self.storage.wait(lambda: not self.settings.busy)
            self.assertEqual(self.settings.applicationPanelShortcut, "")
            self.foreground = 77
            count = self.actions.count("toggle-panel")
            self.key(0xa4); self.key(0xa4, False)
            self.assertEqual(self.actions.count("toggle-panel"), count+1)
        finally:
            self.settings.cancelShortcutCapture(); view.close(); view.deleteLater(); self.app.processEvents()


if __name__ == "__main__":
    unittest.main()
