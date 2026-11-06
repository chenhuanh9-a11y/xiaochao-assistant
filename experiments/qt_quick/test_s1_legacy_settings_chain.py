"""Actual Qt saves/session queues with a persisted legacy manual preference.

Inherits author/lock/close/end-round cases under manual rather than auto. Game
capture, matching, windows and rendered art remain explicit synthetic fixtures.
"""
import json
import threading
from unittest.mock import Mock, patch

import test_s1_author_settings_chain as chain
from client_settings import SettingsService


class S1LegacySettingsChainTests(chain.S1AuthorSettingsChainTests):
    selection_mode = "manual"

    def setUp(self):
        super().setUp()
        from season_context import modes_for_season
        capabilities = self.fx.registry.capabilities
        self.enterContext(patch.object(self.fx.registry, "capabilities", side_effect=lambda season="s0":
            {mode: row for mode, row in capabilities(season).items() if mode in modes_for_season(season)}))

    def wait_idle(self):
        self.until(lambda: not self.settings.busy and not self.session.busy
                   and not self.session._jobs and self.coordinator._pending_context is None)

    def assert_legacy_retained(self):
        self.assertEqual(self.settings.persistedValues["mapSelectionMode"], "manual")
        self.assertEqual(json.loads(self.settings.path.read_text())["mapSelectionMode"], "manual")
        self.assertEqual(self.runtime._settings["mapSelectionMode"], "manual")
        self.assertEqual(self.runtime.snapshot()["mapSelectionMode"], "auto")
        self.assertFalse(self.runtime.snapshot()["manualSelectionPending"])

    def test_actual_settings_reopen_start_and_map_reopen_remain_automatic(self):
        self.assert_legacy_retained()
        reopened = SettingsService(self.settings.sandbox)
        try:
            self.assertEqual(reopened.persistedValues["mapSelectionMode"], "manual")
            self.session.dispatch("end_round")
            self.wait_idle()
            # Startup's configure-before-context order, with a real disk reread.
            self.runtime.dispatch("set_context", dict(season="s0", mode="nightmare"))
            self.runtime.configure_settings(reopened.persistedValues)
            self.runtime.dispatch("set_context", dict(season="s1", mode="nightmare", profileId="shared-structure"))
            self.session.refreshRuntime()
            self.session.dispatch("start_round")
            self.wait_idle()
            self.session.dispatch("capture_now")
            self.wait_idle()
            first = self.fx.current_payload()
            self.assertIsNotNone(first)
            self.assertEqual(first["floor"], "-3")
            self.session.dispatch("close_map")
            self.wait_idle()
            self.assertIsNone(self.fx.current_payload())
            self.fx.floor = "-4"
            self.session.dispatch("capture_now")
            self.wait_idle()
            second = self.fx.current_payload()
            self.assertIsNotNone(second)
            self.assertEqual(second["floor"], "-4")
            self.assertNotEqual(first["frameId"], second["frameId"])
            self.assertIsNone(self.runtime.render_payload(first["token"]))
            self.assert_legacy_retained()
        finally:
            reopened.shutdown()
            self.assertTrue(reopened.pool.waitForDone(5000))

    def test_saved_season_switch_restores_s0_manual_without_rewriting_preference(self):
        self.session.dispatch("end_round")
        self.wait_idle()
        self.coordinator.requestGameSeason("s0")
        self.wait_idle()
        self.assertEqual(self.session.appliedSeason, "s0")
        self.assertEqual(self.runtime.snapshot()["mapSelectionMode"], "manual")
        self.assertTrue(self.runtime._manual_selection_unlocked())
        self.assertEqual(json.loads(self.settings.path.read_text())["mapSelectionMode"], "manual")
        picker = Mock()
        self.coordinator._manual_map_picker = picker
        self.coordinator.requestManualMapSelection()
        picker.open.assert_called_once_with(source="map-button")
        self.coordinator._native_action("manual-map-picker")
        picker.toggle.assert_called_once_with(source="hotkey")
        self.coordinator.requestGameSeason("s1")
        self.wait_idle()
        self.assertEqual(self.session.appliedSeason, "s1")
        self.assert_legacy_retained()
        self.session.dispatch("start_round")
        self.wait_idle()
        self.session.dispatch("capture_now")
        self.wait_idle()
        self.assertIsNotNone(self.fx.current_payload())

    def test_legacy_s1_button_hotkey_confirm_and_mode_write_cannot_activate_picker(self):
        picker = Mock()
        self.coordinator._manual_map_picker = picker
        previous = self.fx.current_payload()
        self.coordinator.requestManualMapSelection()
        self.coordinator._native_action("manual-map-picker")
        self.assertFalse(self.coordinator.confirmManualMap("a"))
        self.coordinator.setMapSelectionMode("auto")
        self.wait_idle()
        picker.open.assert_not_called()
        picker.toggle.assert_not_called()
        self.assertEqual(self.fx.current_payload()["token"], previous["token"])
        self.assert_identity_unchanged()
        self.assert_legacy_retained()

    def test_actual_legacy_preference_saves_preserve_native_lock(self):
        previous = self.fx.current_payload()
        generation = self.runtime._s1_solver_policy_generation
        for mode in ("auto", "manual"):
            self.assertTrue(self.settings.setValue("mapSelectionMode", mode))
            self.assertTrue(self.settings.saveFields(["mapSelectionMode"]))
            self.wait_idle()
            self.assertEqual(self.runtime._settings["mapSelectionMode"], mode)
            self.assertEqual(self.runtime.snapshot()["mapSelectionMode"], "auto")
            self.assert_identity_unchanged()
            self.assertEqual(self.runtime._s1_solver_policy_generation, generation)
            self.assertEqual(self.fx.current_payload()["token"], previous["token"])
        self.assert_legacy_retained()

    def test_qt_save_and_end_round_cannot_restore_blocked_old_material(self):
        self.session.dispatch("close_map")
        self.wait_idle()
        entered, release = threading.Event(), threading.Event()
        original = self.fx.registry.engine.qt_s1_structure_scan
        def delayed(*args, **kwargs):
            entered.set()
            self.assertTrue(release.wait(8))
            return original(*args, **kwargs)
        with patch.object(self.fx.registry.engine, "qt_s1_structure_scan", side_effect=delayed):
            try:
                self.session.dispatch("capture_now")
                self.until(entered.is_set)
                self.assertTrue(self.settings.setValue("mapSelectionMode", "auto"))
                self.assertTrue(self.settings.saveFields(["mapSelectionMode"]))
                self.until(lambda: not self.settings.busy)
                self.runtime.dispatch("end_round")
                self.assertIsNone(self.runtime._s1_private_pool)
            finally:
                release.set()
            self.wait_idle()
        self.assertIsNone(self.fx.current_payload())
        self.assertIsNone(self.runtime._s1_private_pool)
        self.assertFalse(self.runtime.snapshot()["roundActive"])
        self.assertFalse(self.coordinator.alignedData)


if __name__ == "__main__":
    import unittest
    unittest.main()
