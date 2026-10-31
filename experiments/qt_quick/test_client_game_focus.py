"""Qt explicit-click facade checks with fake Win32 only; no windows are used."""
from __future__ import annotations

from types import SimpleNamespace
import unittest

from client_game_focus import GameFocusService


class QtFocusTests(unittest.TestCase):
    def native(self):
        calls = []
        state = dict(foreground=77)
        def focus(hwnd):
            calls.append(("focus", hwnd)); state["foreground"] = hwnd; return True
        user = SimpleNamespace(IsWindow=lambda hwnd: hwnd == 42,
                               GetForegroundWindow=lambda: state["foreground"],
                               IsIconic=lambda hwnd: False, SetForegroundWindow=focus)
        def api(): calls.append(("api",)); return user
        native = SimpleNamespace(get_user32=api, window_process_id=lambda hwnd: 700,
                                 window_text=lambda hwnd: "第五人格",
                                 process_image_path=lambda pid: "C:/game/dwrg.exe",
                                 identity_is_allowed=lambda title, image: True)
        return native, calls

    def test_constructor_is_inert_and_only_explicit_request_returns_receipt(self):
        native, calls = self.native()
        service = GameFocusService(lambda: 42, native=native)
        self.assertEqual(calls, [])
        self.assertEqual(service.result, {})
        result = service.requestFocus()
        self.assertEqual(result, dict(success=True, reason="focused", message="已切回游戏"))
        self.assertEqual(calls, [("api",), ("focus", 42)])
        self.assertEqual(service.result, result)
        result["reason"] = "cannot-mutate-service-state"
        self.assertEqual(service.result["reason"], "focused")

    def test_shutdown_or_owner_closed_prevents_native_api_access(self):
        native, calls = self.native()
        service = GameFocusService(lambda: 42, native=native)
        service.shutdown()
        self.assertEqual(service.requestFocus()["reason"], "assistant-closed")
        self.assertEqual(calls, [])
        service = GameFocusService(lambda: 42, native=native, is_closed=lambda: True)
        self.assertEqual(service.requestFocus()["reason"], "assistant-closed")
        self.assertEqual(calls, [])

    def test_binding_reader_remains_live_between_explicit_clicks(self):
        native, calls = self.native()
        binding = [42]
        service = GameFocusService(lambda: binding[0], native=native)
        self.assertTrue(service.requestFocus()["success"])
        binding[0] = None
        self.assertEqual(service.requestFocus()["reason"], "game-not-bound")
        self.assertEqual(calls, [("api",), ("focus", 42)])


if __name__ == "__main__": unittest.main()
