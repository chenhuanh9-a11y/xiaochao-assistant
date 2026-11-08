"""S1 ignores the legacy S0 picker preference without granting capabilities.

Real runtime, synthetic capture/geometry/resources; no native game acceptance.
"""
import threading
import unittest
from unittest.mock import patch

from client_live_runtime import LiveRuntime
from test_s1_manual_floor_runtime import CurrentFixtureCapture
import test_s1_native_candidate_preview as native


class LegacySelectionContracts(unittest.TestCase):
    def setUp(self):
        native.NativeCandidateContracts.setUp(self)
        from season_context import modes_for_season
        capabilities = self.registry.capabilities
        self.start_patch(patch.object(self.registry, "capabilities", side_effect=lambda season="s0":
            {mode: row for mode, row in capabilities(season).items() if mode in modes_for_season(season)}))

    start_patch = native.NativeCandidateContracts.start_patch
    show = native.NativeCandidateContracts.show
    current_payload = native.NativeCandidateContracts.current_payload

    def test_startup_and_reopen_ignore_legacy_manual_and_missing_preference(self):
        for settings in ({"mapSelectionMode": "manual"}, {}):
            with self.subTest(settings=settings):
                self.runtime.dispatch("end_round")
                self.runtime.dispatch("set_context", dict(season="s0", mode="nightmare"))
                # Composition-root ordering: settings first, applied season next.
                self.runtime.configure_settings(settings)
                self.assertEqual(self.runtime._manual_selection_unlocked(), bool(settings))
                self.runtime.dispatch("set_context", dict(season="s1", mode="nightmare"))
                self.assertEqual(self.runtime._settings, settings)
                self.assertEqual(self.runtime.snapshot()["mapSelectionMode"], "auto")
                self.assertFalse(self.runtime._manual_selection_unlocked())
                previous = self.show()
                for floor in ("-2", "-3", "-4", "-1"):
                    self.runtime.dispatch("close_map")
                    self.floor = floor
                    current = self.show()
                    self.assertEqual(current["floor"], floor)
                    self.assertNotEqual(current["frameId"], previous["frameId"])
                    self.assertIsNone(self.runtime.render_payload(previous["token"]))
                    self.assertFalse(self.runtime.snapshot()["manualSelectionPending"])
                    previous = current
                self.runtime.dispatch("end_round")
                self.runtime.dispatch("set_context", dict(season="s0", mode="nightmare"))
                self.assertEqual(self.runtime.snapshot()["mapSelectionMode"], settings.get("mapSelectionMode", "auto"))
                self.assertEqual(self.runtime._manual_selection_unlocked(), bool(settings))

    def test_raw_settings_season_cannot_override_applied_runtime_season(self):
        self.runtime.configure_settings(dict(gameSeason="s0", mapSelectionMode="manual"))
        self.assertEqual(self.runtime.snapshot()["mapSelectionMode"], "auto")
        self.show()
        self.runtime.dispatch("end_round")
        self.runtime.dispatch("set_context", dict(season="s0", mode="nightmare"))
        self.runtime.configure_settings(dict(gameSeason="s1", mapSelectionMode="manual"))
        self.assertEqual(self.runtime.snapshot()["mapSelectionMode"], "manual")
        self.assertTrue(self.runtime._manual_selection_unlocked())

    def test_legacy_mode_never_grants_private_purpose(self):
        for purpose in (None, True, "native-s1-preview", {"candidateReady": True}):
            runtime = LiveRuntime(engine_factories=self.registry,
                capture_provider=CurrentFixtureCapture(), evidence_kind="live-client-capture",
                s1_candidate_purpose=purpose, recorded_s1_provisional_display=True,
                recorded_s1_streaming=True)
            try:
                runtime.configure_settings(dict(mapSelectionMode="manual", nativeCandidatePreviewAvailable=True))
                runtime.dispatch("set_context", dict(season="s1", mode="nightmare"))
                self.assertEqual(runtime.snapshot()["mapSelectionMode"], "auto")
                self.assertFalse(runtime._s1_provisional_enabled())
                self.assertNotIn("start_round", runtime.snapshot()["availableCommands"])
                self.assertFalse(runtime.dispatch("start_round")["success"])
                self.assertFalse(runtime.dispatch("capture_now")["success"])
                self.assertIsNone(runtime.render_payload(""))
            finally:
                runtime.shutdown()

    def test_legacy_mode_never_grants_invalid_resource_capability(self):
        self.runtime.configure_settings(dict(mapSelectionMode="manual"))
        for changes in ({"candidateReady": False}, {"contract": "wrong-purpose"},
                        {"observedFloors": (-1, -2)}):
            with self.subTest(changes=changes):
                with patch.dict(self.registry.capability, changes):
                    self.assertNotIn("start_round", self.runtime.snapshot()["availableCommands"])
                    self.assertFalse(self.runtime.dispatch("start_round")["success"])
                    self.assertFalse(self.runtime.dispatch("capture_now")["success"])
                    self.assertIsNone(self.current_payload())

    def test_settings_save_and_close_reject_blocked_old_capture(self):
        self.runtime.configure_settings(dict(mapSelectionMode="manual"))
        self.runtime.dispatch("start_round")
        entered, release = threading.Event(), threading.Event()
        original_scan = self.registry.engine.qt_s1_structure_scan
        result = []
        def delayed(*args, **kwargs):
            entered.set()
            self.assertTrue(release.wait(5))
            return original_scan(*args, **kwargs)
        with patch.object(self.registry.engine, "qt_s1_structure_scan", side_effect=delayed):
            thread = threading.Thread(target=lambda: result.append(self.runtime.dispatch("capture_now")))
            thread.start()
            try:
                self.assertTrue(entered.wait(5))
                self.runtime.configure_settings(dict(mapSelectionMode="auto"))
                self.runtime.dispatch("close_map")
            finally:
                release.set()
                thread.join(5)
        self.assertFalse(thread.is_alive())
        self.assertFalse(result[0]["success"])
        self.assertIsNone(self.current_payload())
        self.assertFalse(self.runtime.snapshot()["mapOpen"])
        self.show()


if __name__ == "__main__":
    unittest.main()
