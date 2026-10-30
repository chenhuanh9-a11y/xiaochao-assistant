"""Bounded adapter checks; test doubles and owned Qt windows only."""
from __future__ import annotations

import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import threading
import time
import unittest
import ctypes
from ctypes import wintypes

from PySide6.QtCore import QObject, QThread, Qt
from PySide6.QtGui import QGuiApplication, QWindow

from client_session import SessionService
from client_windows import (DisabledGlobalHotkeyBackend, WindowController,
                            WS_EX_NOACTIVATE, WS_EX_TOOLWINDOW, WS_EX_TRANSPARENT,
                            overlay_extended_style)

APP = QGuiApplication.instance() or QGuiApplication([])


def until(predicate, timeout=2):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        APP.processEvents()
        if predicate():
            return
        time.sleep(.001)
    raise AssertionError("Timed out waiting for a bounded test worker")


class Runtime:
    def __init__(self):
        self.state = dict(engineConnected=True, mode="nightmare", profileId="",
                          roundId=1, frameId="frame-1", roundActive=False,
                          availableCommands=["capture_now", "set_game_mode", "set_route_profile", "set_context"])
        self.calls = []
        self.started = threading.Event()
        self.release = threading.Event()
        self.release.set()
        self.fail = False
        self.bad_context = False

    def snapshot(self):
        return dict(self.state)

    def dispatch(self, action, payload, cancelled):
        self.calls.append((action, dict(payload), cancelled, QThread.currentThread()))
        self.started.set()
        self.release.wait(1)
        if self.fail:
            raise ValueError("fixture failure")
        if action == "set_game_mode":
            self.state["mode"] = payload["mode"]
        if action == "set_route_profile":
            self.state["profileId"] = payload["profileId"]
        if action == "set_context":
            self.state.update(mode=payload["mode"], profileId=payload["profileId"])
        receipt = dict(payload, action=action, success=True, message="fixture receipt")
        if self.bad_context:
            receipt["frameId"] = "wrong-frame"
        return receipt


class FakeWindow(QObject):
    def __init__(self):
        super().__init__()
        self._flags = Qt.Window
        self._visible = False
        self._opacity = 1.
        self.show_count = 0

    def flags(self): return self._flags
    def setFlags(self, flags): self._flags = flags
    def isVisible(self): return self._visible
    def opacity(self): return self._opacity
    def setOpacity(self, value): self._opacity = value
    def show(self): self._visible = True; self.show_count += 1
    def hide(self): self._visible = False


class SessionTests(unittest.TestCase):
    def setUp(self):
        self.runtime = Runtime()
        self.session = SessionService(self.runtime)

    def tearDown(self):
        self.runtime.release.set()
        self.session.shutdown()
        until(lambda: not self.session._jobs)

    def test_material_maintenance_never_supersedes_primary_work_or_receipt(self):
        # Maintenance is private; production never offers it as a UI command.
        self.assertNotIn("remember_render_material", self.runtime.state["availableCommands"])
        self.session.refreshRuntime()
        self.runtime.release.clear()
        self.session.dispatch("capture_now")
        until(self.runtime.started.is_set)
        primary = self.session._jobs[self.session._pending][0]
        generation = self.session._generation
        self.assertTrue(self.session.dispatchMaintenance("remember_render_material", {"renderToken": "ready-1"}))
        self.assertFalse(primary.cancelled.is_set())
        self.assertEqual(self.session._generation, generation)
        self.assertEqual(self.session.snapshot["pendingAction"], "capture_now")
        self.runtime.release.set()
        until(lambda: not self.session._jobs and not self.session._maintenance)
        self.assertEqual(self.session.snapshot["lastReceipt"]["action"], "capture_now")
        self.assertEqual([call[0] for call in self.runtime.calls], ["capture_now", "remember_render_material"])
        self.assertEqual(self.runtime.calls[-1][1], {"renderToken": "ready-1"})

    def test_maintenance_is_bounded_and_cancelled_with_its_lifetime(self):
        self.session.refreshRuntime()
        self.runtime.release.clear()
        self.assertTrue(self.session.dispatchMaintenance("remember_render_material", {"renderToken": "one"}))
        until(self.runtime.started.is_set)
        self.assertTrue(self.session.dispatchMaintenance("remember_render_material", {"renderToken": "two"}))
        self.assertFalse(self.session.dispatchMaintenance("remember_render_material", {"renderToken": "three"}))
        self.assertFalse(self.session.acquireResourceLease())
        jobs = tuple(self.session._maintenance.values())
        self.session.cancelPending("foreground-lost")
        self.assertTrue(all(job.cancelled.is_set() for job in jobs))
        self.runtime.release.set()
        until(lambda: not self.session._maintenance)

    def test_pending_author_context_keeps_actual_round_lock(self):
        self.runtime.state["roundActive"] = True
        self.session.refreshRuntime()
        self.runtime.release.clear()
        self.assertTrue(self.session.reconfigure("nightmare", "other-author"))
        self.assertTrue(self.session.snapshot["roundActive"])
        self.assertTrue(self.session.snapshot["contextPending"])
        self.assertFalse(self.session.snapshot["liveAuthorization"])
        self.runtime.release.set()
        until(lambda: not self.session._jobs)

    def test_active_mode_guard_preserves_context_generation_and_current_worker(self):
        self.runtime.state["roundActive"] = True
        self.runtime.release.clear()
        self.session.dispatch("capture_now")
        until(self.runtime.started.is_set)
        before = (self.session._mode, self.session._profile, self.session._generation, self.session._pending)
        job = self.session._jobs[self.session._pending][0]
        self.assertFalse(self.session.setMode("difficult"))
        self.assertFalse(self.session.reconfigure("difficult", "new-profile"))
        self.assertEqual((self.session._mode, self.session._profile, self.session._generation, self.session._pending), before)
        self.assertFalse(job.cancelled.is_set())
        self.assertEqual(len(self.runtime.calls), 1)
        self.runtime.release.set()
        until(lambda: not self.session.busy)

    def test_unattached_and_unsupported_commands_do_not_claim_live_state(self):
        unattached = SessionService()
        unattached.dispatch("start_round")
        self.assertFalse(unattached.engineConnected)
        self.assertIn("Unavailable", unattached.message)
        self.assertFalse(unattached.snapshot["commands"]["start_round"]["available"])
        self.session.dispatch("start_round")
        self.assertEqual(self.runtime.calls, [])
        self.assertNotIn("roundActive", unattached.snapshot)
        unattached.shutdown()

    def test_worker_is_off_ui_thread_completion_is_on_ui_thread_and_coalesces(self):
        self.runtime.release.clear()
        threads = []
        self.session.changed.connect(lambda: threads.append(QThread.currentThread()))
        self.session.dispatch("capture_now")
        self.session.dispatch("capture_now")
        until(self.runtime.started.is_set)
        self.assertEqual(len(self.runtime.calls), 1)
        self.assertTrue(self.session.busy)
        self.assertNotEqual(self.runtime.calls[0][3], APP.thread())
        self.runtime.release.set()
        until(lambda: not self.session._jobs)
        self.assertEqual(self.session.message, "fixture receipt")
        self.assertTrue(all(thread == APP.thread() for thread in threads))
        self.assertFalse(self.session.snapshot["roundActive"])

    def test_superseded_mode_rejects_old_completion(self):
        self.runtime.release.clear()
        self.session.dispatch("capture_now")
        until(self.runtime.started.is_set)
        self.session.setMode("difficult")
        self.assertTrue(self.runtime.calls[0][2].is_set())
        self.runtime.release.set()
        until(lambda: not self.session._jobs)
        self.assertEqual(self.session.currentMode, "difficult")
        self.assertEqual(self.session.stale_results, 1)
        self.assertEqual(self.session.snapshot["lastReceipt"]["action"], "set_context")

    def test_atomic_reconfigure_sends_both_desired_fields_and_rejects_old_completion(self):
        self.runtime.release.clear()
        self.session.dispatch("capture_now")
        until(self.runtime.started.is_set)
        self.assertEqual(self.session.snapshot["pendingAction"], "capture_now")
        self.assertTrue(self.session.reconfigure("difficult", "author-b"))
        self.assertTrue(self.runtime.calls[0][2].is_set())
        self.assertEqual((self.session.currentMode, self.session.currentProfile), ("difficult", "author-b"))
        self.assertEqual(self.session.snapshot["pendingAction"], "set_context")
        self.runtime.release.set()
        until(lambda: not self.session._jobs)
        action, payload, cancelled, thread = self.runtime.calls[-1]
        self.assertEqual(action, "set_context")
        self.assertEqual((payload["mode"], payload["profileId"]), ("difficult", "author-b"))
        self.assertEqual((self.session.currentMode, self.session.currentProfile), ("difficult", "author-b"))
        self.assertEqual(self.session.stale_results, 1)
        self.assertEqual(self.session.snapshot["lastReceipt"]["action"], "set_context")
        self.assertEqual(self.session.snapshot["pendingAction"], "")

    def test_atomic_reconfigure_rejects_lease_closed_invalid_or_unavailable_context(self):
        lease = self.session.acquireResourceLease()
        self.assertFalse(self.session.reconfigure("difficult", "author-b"))
        self.assertEqual((self.session.currentMode, self.session.currentProfile), ("nightmare", ""))
        self.session.releaseResourceLease(lease)
        self.assertFalse(self.session.reconfigure("unknown-mode", "author-b"))
        self.runtime.state["availableCommands"].remove("set_context")
        self.assertFalse(self.session.reconfigure("difficult", "author-b"))
        self.assertEqual(self.runtime.calls, [])
        self.assertEqual((self.session.currentMode, self.session.currentProfile), ("nightmare", ""))
        self.session.shutdown()
        self.assertFalse(self.session.reconfigure("difficult", "author-b"))

    def test_profile_round_and_frame_changes_cancel_pending_result(self):
        for field, value in (("profileId", "author-b"), ("roundId", 2), ("frameId", "frame-2")):
            self.runtime.release.clear()
            self.runtime.started.clear()
            self.session.dispatch("capture_now")
            until(self.runtime.started.is_set)
            self.runtime.state[field] = value
            self.session.refreshRuntime()
            self.runtime.release.set()
            until(lambda: not self.session._jobs)
        self.assertEqual(self.session.stale_results, 3)

    def test_worker_failure_bad_context_and_cancel(self):
        self.runtime.fail = True
        self.session.dispatch("capture_now")
        until(lambda: not self.session._jobs)
        self.assertIn("fixture failure", self.session.message)
        self.runtime.fail = False
        self.runtime.bad_context = True
        self.session.dispatch("capture_now")
        until(lambda: not self.session._jobs)
        self.assertEqual(self.session.stale_results, 1)
        self.assertEqual(self.session.snapshot["lastReceipt"], {})
        self.runtime.bad_context = False
        self.runtime.release.clear()
        self.runtime.started.clear()
        self.session.dispatch("capture_now")
        until(self.runtime.started.is_set)
        self.session.stop()
        self.runtime.release.set()
        until(lambda: not self.session._jobs)
        self.assertEqual(self.session.stale_results, 2)

    def test_runtime_frame_advancing_without_ui_poll_discards_old_receipt(self):
        self.runtime.release.clear()
        self.session.dispatch("capture_now")
        until(self.runtime.started.is_set)
        self.runtime.state["frameId"] = "new-frame-without-poll"
        self.runtime.release.set()
        until(lambda: not self.session._jobs)
        self.assertEqual(self.session.stale_results, 1)
        self.assertEqual(self.session.snapshot["lastReceipt"], {})
        self.assertEqual(self.session.snapshot["frameId"], "new-frame-without-poll")

    def test_explicit_request_result_pair_accepts_intended_round_frame_transition(self):
        def dispatch(action, payload, cancelled):
            self.runtime.state.update(roundId=2, frameId="accepted-frame-2", roundActive=True)
            return dict(success=True, requestContext=dict(payload), resultContext={
                key: self.runtime.state[key] for key in ("mode", "profileId", "roundId", "frameId")})
        self.runtime.dispatch = dispatch
        self.session.dispatch("capture_now")
        until(lambda: not self.session._jobs)
        self.assertEqual(self.session.stale_results, 0)
        self.assertEqual(self.session.snapshot["roundId"], 2)
        self.assertEqual(self.session.snapshot["frameId"], "accepted-frame-2")
        self.assertTrue(self.session.snapshot["lastReceipt"]["success"])

    def test_target_pair_cannot_accept_wrong_request_or_post_commit_frame_drift(self):
        for mismatch in ("request", "result"):
            def dispatch(action, payload, cancelled):
                request = dict(payload)
                target = {key: self.runtime.state[key] for key in ("mode", "profileId", "roundId", "frameId")}
                if mismatch == "request":
                    request["generation"] += 1
                else:
                    target["frameId"] = "previously-committed-frame"
                return dict(success=True, requestContext=request, resultContext=target)
            self.runtime.dispatch = dispatch
            self.session.dispatch("capture_now")
            until(lambda: not self.session._jobs)
            self.assertEqual(self.session.snapshot["lastReceipt"], {})
        self.assertEqual(self.session.stale_results, 2)

    def test_exclusive_resource_lease_blocks_runtime_and_preferences(self):
        lease = self.session.acquireResourceLease()
        self.assertTrue(lease)
        self.assertEqual(self.session.acquireResourceLease(), "")
        self.session.dispatch("capture_now")
        self.session.setMode("difficult")
        self.session.setRouteProfile("author-b")
        self.assertEqual(self.runtime.calls, [])
        self.assertEqual(self.session.currentMode, "nightmare")
        self.assertEqual(self.session.currentProfile, "")
        self.session.releaseResourceLease("not-the-lease")
        self.assertTrue(self.session.snapshot["resourceLocked"])
        self.session.releaseResourceLease(lease)
        self.runtime.state["roundActive"] = True
        self.assertEqual(self.session.acquireResourceLease(), "")

    def test_cancelled_worker_holds_idle_gate_until_finished_and_shutdown_is_final(self):
        self.runtime.release.clear()
        self.session.dispatch("capture_now")
        until(self.runtime.started.is_set)
        self.session.stop()
        self.assertEqual(self.session.acquireResourceLease(), "")
        self.session.shutdown()
        self.session.dispatch("capture_now")
        self.assertEqual(len(self.runtime.calls), 1)
        self.assertTrue(self.session.snapshot["closed"])


class WindowTests(unittest.TestCase):
    def test_only_registered_windows_and_passive_flags(self):
        controller = WindowController()
        window = FakeWindow()
        controller.show("foreign")
        self.assertIn("Unregistered", controller.message)
        controller.bindWindow("control", window)
        self.assertTrue(window.flags() & Qt.WindowDoesNotAcceptFocus)
        controller.show("control")
        controller.setClickThrough("control", True)
        self.assertTrue(window.flags() & Qt.WindowTransparentForInput)
        self.assertTrue(window.isVisible())
        controller.setClickThrough("control", False)
        self.assertFalse(window.flags() & Qt.WindowTransparentForInput)
        controller.toggleCollapsed("control")
        self.assertTrue(window.property("collapsed"))
        controller.setOpacity("control", .4)
        self.assertEqual(window.opacity(), .4)
        controller.setOpacity("control", float("nan"))
        self.assertEqual(window.opacity(), .4)
        controller.shutdown()
        self.assertFalse(window.isVisible())

    def test_owned_qwindow_no_focus_flags_and_pure_native_mapping(self):
        controller = WindowController()
        window = QWindow()
        controller.bindWindow("orb", window)
        self.assertTrue(window.flags() & Qt.WindowDoesNotAcceptFocus)
        self.assertTrue(window.flags() & Qt.WindowStaysOnTopHint)
        style = overlay_extended_style(0x100, True)
        self.assertTrue(style & WS_EX_NOACTIVATE)
        self.assertTrue(style & WS_EX_TOOLWINDOW)
        self.assertTrue(style & WS_EX_TRANSPARENT)
        self.assertEqual(overlay_extended_style(style, False) & WS_EX_TRANSPARENT, 0)
        if os.name == "nt" and QGuiApplication.platformName() == "windows":
            user = ctypes.WinDLL("user32", use_last_error=True)
            user.GetWindowLongW.argtypes = [wintypes.HWND, ctypes.c_int]
            user.GetWindowLongW.restype = wintypes.LONG
            actual = user.GetWindowLongW(int(window.winId()), -20)
            self.assertTrue(actual & WS_EX_NOACTIVATE)
            self.assertTrue(actual & WS_EX_TOOLWINDOW)
            controller.setClickThrough("orb", True)
            actual = user.GetWindowLongW(int(window.winId()), -20)
            self.assertTrue(actual & WS_EX_TRANSPARENT)
            controller.setClickThrough("orb", False)
            actual = user.GetWindowLongW(int(window.winId()), -20)
            self.assertFalse(actual & WS_EX_TRANSPARENT)
        window.destroy()

    def test_global_backend_disabled_and_reuses_product_conflicts(self):
        backend = DisabledGlobalHotkeyBackend()
        def primary(value):
            if len(value) == 1 and value.isalpha():
                return value.upper(), ord(value.upper())
            if value.upper() == "CTRL":
                return "Ctrl", 0x11
            raise ValueError("unknown key")
        self.assertFalse(backend.enabled)
        self.assertFalse(backend.start())
        chords = backend.configure({"one": "Ctrl+G", "two": "H"}, primary)
        self.assertEqual(chords["one"].name, "Ctrl+G")
        with self.assertRaises(ValueError):
            backend.configure({"one": "Ctrl+G", "two": "Control+G"}, primary)
        with self.assertRaises(ValueError):
            backend.configure({"one": "Ctrl", "two": "Ctrl+G"}, primary)


if __name__ == "__main__":
    unittest.main()
