"""Shared explicit-click focus contract with fake Win32 only; no Tk imports."""
from __future__ import annotations

import ast
from pathlib import Path
from types import SimpleNamespace
import unittest

from app.game_window_focus import NativeGameFocusAPI, focus_bound_game

ROOT = Path(__file__).resolve().parents[1]


class FakeFocus:
    def __init__(self):
        self.binding, self.closed, self.pid = 42, False, 700
        self.foreground, self.iconic, self.valid = 77, False, True
        self.title, self.image = "第五人格", "C:/game/dwrg.exe"
        self.accepted, self.confirm, self.restore = True, True, True
        self.calls, self.logs = [], []
        self.on_identity = self.on_restore = self.on_focus = None

    def get_user32(self):
        self.calls.append(("get_user32",))
        return self

    def IsWindow(self, hwnd):
        self.calls.append(("IsWindow", hwnd))
        return self.valid

    def GetForegroundWindow(self): return self.foreground
    def IsIconic(self, hwnd): return self.iconic

    def ShowWindow(self, hwnd, command):
        self.calls.append(("ShowWindow", hwnd, command))
        if self.restore:
            self.iconic = False
        if self.on_restore:
            self.on_restore()
        return True

    def SetForegroundWindow(self, hwnd):
        self.calls.append(("SetForegroundWindow", hwnd))
        if self.accepted and self.confirm:
            self.foreground = hwnd
        if self.on_focus:
            self.on_focus()
        return self.accepted

    def window_process_id(self, hwnd): return self.pid
    def window_text(self, hwnd): return self.title
    def process_image_path(self, pid): return self.image

    def identity_is_allowed(self, title, image):
        if self.on_identity:
            self.on_identity()
        return title == "第五人格" and image == "C:/game/dwrg.exe"

    def request(self):
        return focus_bound_game(
            get_bound_hwnd=lambda: self.binding, is_closed=lambda: self.closed,
            get_user32=self.get_user32, window_process_id=self.window_process_id,
            window_text=self.window_text, process_image_path=self.process_image_path,
            identity_is_allowed=self.identity_is_allowed,
            log=lambda event, **values: self.logs.append((event, values)),
        )

    def mutations(self):
        return [call for call in self.calls if call[0] in ("ShowWindow", "SetForegroundWindow")]


class FocusTests(unittest.TestCase):
    def test_single_normal_request_and_confirmed_foreground(self):
        fake = FakeFocus()
        result = fake.request()
        self.assertEqual(result, dict(success=True, reason="focused", message="已切回游戏"))
        self.assertEqual(fake.mutations(), [("SetForegroundWindow", 42)])
        self.assertEqual(fake.logs[-1][0], "desktop-game-focus")
        self.assertEqual(fake.logs[-1][1]["gameProcessId"], 700)

    def test_explicit_minimized_restore_then_one_focus_request(self):
        fake = FakeFocus(); fake.iconic = True
        self.assertTrue(fake.request()["success"])
        self.assertEqual(fake.mutations(), [("ShowWindow", 42, 9), ("SetForegroundWindow", 42)])
        self.assertTrue(fake.logs[-1][1]["restoreRequested"])

    def test_os_refusal_and_unconfirmed_success_are_distinct_without_retry(self):
        for accepted, reason in ((False, "foreground-denied"), (True, "foreground-not-confirmed")):
            fake = FakeFocus(); fake.accepted = accepted; fake.confirm = False
            self.assertEqual(fake.request()["reason"], reason)
            self.assertEqual(fake.mutations(), [("SetForegroundWindow", 42)])

    def test_already_foreground_validates_identity_and_performs_no_mutation(self):
        fake = FakeFocus(); fake.foreground = 42
        self.assertEqual(fake.request()["reason"], "already-foreground")
        self.assertEqual(fake.mutations(), [])
        fake.image = "C:/other/application.exe"
        self.assertEqual(fake.request()["reason"], "identity-mismatch")

    def test_closed_or_unbound_does_not_acquire_native_api(self):
        for hwnd in (None, 0, True, "42"):
            fake = FakeFocus(); fake.binding = hwnd
            self.assertEqual(fake.request()["reason"], "game-not-bound")
            self.assertEqual(fake.calls, [])
        fake = FakeFocus(); fake.closed = True
        self.assertEqual(fake.request()["reason"], "assistant-closed")
        self.assertEqual(fake.calls, [])

    def test_invalid_window_unknown_identity_and_wrong_title_fail_closed(self):
        for field, value, reason in (("valid", False, "game-window-unavailable"),
                                     ("pid", None, "identity-unavailable"),
                                     ("image", None, "identity-unavailable"),
                                     ("title", "Other application", "identity-mismatch")):
            fake = FakeFocus(); setattr(fake, field, value)
            self.assertEqual(fake.request()["reason"], reason)
            self.assertEqual(fake.mutations(), [])

    def test_binding_or_pid_change_while_identity_is_read_rejects_before_focus(self):
        for field, value in (("binding", 99), ("pid", 900)):
            fake = FakeFocus(); fake.on_identity = lambda: setattr(fake, field, value)
            self.assertEqual(fake.request()["reason"], "binding-changed")
            self.assertEqual(fake.mutations(), [])

    def test_restore_failure_or_close_during_restore_never_requests_focus(self):
        fake = FakeFocus(); fake.iconic = True; fake.restore = False
        self.assertEqual(fake.request()["reason"], "restore-not-confirmed")
        self.assertEqual(fake.mutations(), [("ShowWindow", 42, 9)])
        fake = FakeFocus(); fake.iconic = True
        fake.on_restore = lambda: setattr(fake, "closed", True)
        self.assertEqual(fake.request()["reason"], "assistant-closed")
        self.assertEqual(fake.mutations(), [("ShowWindow", 42, 9)])

    def test_binding_change_after_focus_request_is_not_reported_as_success(self):
        fake = FakeFocus(); fake.on_focus = lambda: setattr(fake, "binding", 99)
        self.assertEqual(fake.request()["reason"], "binding-changed")
        self.assertEqual(fake.mutations(), [("SetForegroundWindow", 42)])

    def test_native_failure_returns_existing_failure_reason(self):
        fake = FakeFocus()
        def unavailable(hwnd): raise OSError("fixture native failure")
        fake.IsWindow = unavailable
        self.assertEqual(fake.request()["reason"], "focus-request-failed")
        self.assertEqual(fake.mutations(), [])
        self.assertIsNone(NativeGameFocusAPI()._user32)

    def test_actual_legacy_method_delegates_without_importing_the_tk_application(self):
        source = ROOT / "app" / "identityv_daily_assistant.py"
        tree = ast.parse(source.read_text(encoding="utf-8"))
        method = next(node for node in ast.walk(tree)
                      if isinstance(node, ast.FunctionDef) and node.name == "focus_game_from_desktop")
        fake = FakeFocus()
        namespace = dict(focus_bound_game=focus_bound_game,
                         ctypes=SimpleNamespace(windll=SimpleNamespace(user32=fake)),
                         window_process_id=fake.window_process_id,
                         live_capture=SimpleNamespace(window_text=fake.window_text,
                             process_image_path=fake.process_image_path,
                             identityv_window_identity_is_allowed=fake.identity_is_allowed))
        exec(compile(ast.Module(body=[method], type_ignores=[]), str(source), "exec"), namespace)
        owner = SimpleNamespace(game_hwnd=42, closed=False, _log=lambda *args, **values: None)
        result = namespace["focus_game_from_desktop"](owner)
        self.assertEqual(result["reason"], "focused")
        self.assertEqual(fake.mutations(), [("SetForegroundWindow", 42)])


if __name__ == "__main__": unittest.main()
