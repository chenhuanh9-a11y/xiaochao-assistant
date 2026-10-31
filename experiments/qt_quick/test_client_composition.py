"""Composition receipts and display-only injection; no windows or native calls."""
from copy import deepcopy
from pathlib import Path
import json
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from client import QtClient
from client_providers import EngineFactories


class Settings:
    def __init__(self):
        self.persistedValues = dict(gameMode="nightmare", routeProfileId="author-one",
                                    modeRouteProfiles={"nightmare": "author-one", "difficult": "other"})
        self.values = deepcopy(self.persistedValues)
        self.busy = False
        self.message = "save failed"
        self.accept = True

    def setValue(self, key, value):
        self.values[key] = deepcopy(value)
        return True

    def saveFields(self, keys):
        self.saved_keys = list(keys)
        return self.accept


class CompositionTests(unittest.TestCase):
    def setUp(self):
        self.client = QtClient.__new__(QtClient)
        self.client._closed = False
        self.client.settings = Settings()
        self.client.resources = SimpleNamespace(busy=False)
        self.client.session = SimpleNamespace(busy=False, refreshRuntime=Mock())
        self.state = dict(mode="nightmare", profileId="author-one")
        self.client.runtime = SimpleNamespace(snapshot=lambda: dict(self.state))
        self.client.route_choices = SimpleNamespace(profiles=[dict(id="author-one", name="Author")])
        self.active = dict(inUse="")
        self.client.custom_galleries = SimpleNamespace(snapshot=self.active,
            setActiveGallery=lambda gallery: self.active.update(inUse=gallery))
        self.client.custom_display = SimpleNamespace(
            use_request=lambda gallery, mode, **kw: dict(success=mode == "nightmare", applied=False,
                profileId="custom:"+gallery if gallery else kw["fallback_profile"], message="proposal"),
            active_gallery=lambda profile, mode: profile[7:] if mode == "nightmare" and profile.startswith("custom:") else "",
            invalidate=Mock())
        self.client.coordinator = SimpleNamespace(_revoke_runtime=Mock(), closing=False)

    def test_route_metadata_refreshes_only_when_runtime_context_changes(self):
        self.client._route_preferences_context = None
        self.client.runtime.route_preferences_context = lambda: dict(self.state)
        self.client.settings.refreshRoutePreferences = Mock()
        self.client._refresh_route_preferences()
        self.client._refresh_route_preferences()
        self.assertEqual(self.client.settings.refreshRoutePreferences.call_count, 1)
        self.state["floor"] = "2"
        self.client._refresh_route_preferences()
        self.client._refresh_route_preferences(force=True)
        self.assertEqual(self.client.settings.refreshRoutePreferences.call_count, 3)

    def test_manual_mode_waits_for_existing_write_across_later_session_receipts(self):
        self.client._pending_manual_mode = None
        self.client.manual_selection = SimpleNamespace(set_key_labels=Mock())
        self.client.session.snapshot = dict(mapSelectionMode="manual", lastReceipt={
            "action": "set_map_selection_mode", "success": True})
        self.client.settings.persistedValues["mapSelectionMode"] = "auto"
        self.client.settings.busy = True
        self.client._sync_manual_selection()
        self.assertEqual(self.client._pending_manual_mode, "manual")
        self.client.session.snapshot["lastReceipt"] = dict(action="refresh_map", success=True)
        self.client.settings.busy = False
        self.client._sync_manual_selection()
        self.assertEqual(self.client.settings.saved_keys, ["mapSelectionMode"])
        self.assertEqual(self.client.settings.values["mapSelectionMode"], "manual")
        self.client.settings.persistedValues["mapSelectionMode"] = "manual"
        self.client._sync_manual_selection()
        self.assertIsNone(self.client._pending_manual_mode)

    def test_reverse_manual_mode_keeps_intent_while_old_write_will_change_baseline(self):
        self.client._pending_manual_mode = "manual"
        self.client.manual_selection = SimpleNamespace(set_key_labels=Mock())
        self.client.settings.persistedValues["mapSelectionMode"] = "auto"
        self.client.settings.busy = True  # The earlier manual preference is being written.
        self.client.session.snapshot = dict(mapSelectionMode="auto", lastReceipt={
            "action": "set_map_selection_mode", "success": True})
        self.client._sync_manual_selection()
        self.assertEqual(self.client._pending_manual_mode, "auto")
        self.client.session.snapshot["lastReceipt"] = dict(action="refresh_map", success=True)
        self.client.settings.persistedValues["mapSelectionMode"] = "manual"
        self.client.settings.busy = False
        self.client._sync_manual_selection()
        self.assertEqual(self.client.settings.values["mapSelectionMode"], "auto")
        self.assertEqual(self.client.settings.saved_keys, ["mapSelectionMode"])

    def test_recovery_launch_binds_pending_receipt_and_leaves_close_to_service(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.client.sandbox = root
            self.client.paths = SimpleNamespace(work_root=root / "work")
            (root / "work/map-packages").mkdir(parents=True)
            (root / "star-map-recovery-helper.exe").touch()
            process = Mock()
            with patch("star_map_recovery_helper.launch_recovery_helper", return_value=process) as launch:
                request_id = self.client._launch_recovery("recover", dict(planSha256="a" * 64))
            pending = json.loads((root / "work/map-packages/qt-recovery-pending.json").read_text())
            self.assertEqual(pending, dict(requestId=request_id, planSha256="a" * 64, action="recover"))
            self.assertIs(self.client._recovery_process, process)
            self.assertEqual(launch.call_args.args[0], root / "star-map-recovery-helper.exe")
            self.assertFalse(self.client.coordinator.closing)

    def test_restart_ignores_unmatched_receipt_then_publishes_matching_result(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.client.sandbox = root
            self.client.paths = SimpleNamespace(work_root=root / "work")
            folder = root / "work/map-packages"
            folder.mkdir(parents=True)
            expected = dict(requestId="a1", planSha256="a" * 64, action="recover")
            (folder / "qt-recovery-pending.json").write_text(json.dumps(expected))
            receipt = folder / "last-recovery-result.json"
            self.client.resources = SimpleNamespace(refresh=Mock(), recordRecoveryReceipt=Mock())
            receipt.write_text(json.dumps(dict(expected, requestId="other", success=True)))
            self.client._read_recovery_receipt()
            self.client.resources.refresh.assert_not_called()
            result = dict(expected, success=False, reason="backup-changed")
            receipt.write_text(json.dumps(result))
            self.client._read_recovery_receipt()
            self.client.resources.recordRecoveryReceipt.assert_called_once_with(result)
            self.client.resources.refresh.assert_called_once()

    def test_gallery_only_becomes_active_after_persistence_and_runtime_ack(self):
        receipt = self.client._use_custom_gallery("mine")
        self.assertTrue(receipt["success"])
        self.assertFalse(receipt["applied"])
        self.client._sync_custom_gallery_active()
        self.assertEqual(self.active["inUse"], "")
        self.client.settings.persistedValues = deepcopy(self.client.settings.values)
        self.client._sync_custom_gallery_active()
        self.assertEqual(self.active["inUse"], "")
        self.state["profileId"] = "custom:mine"
        self.client._sync_custom_gallery_active()
        self.assertEqual(self.active["inUse"], "mine")
        self.assertEqual(self.client.settings.saved_keys, ["routeProfileId", "modeRouteProfiles"])
        self.assertEqual(self.client.settings.persistedValues["modeRouteProfiles"]["difficult"], "other")

    def test_failed_save_or_unsupported_mode_never_claims_applied(self):
        self.client.settings.accept = False
        receipt = self.client._use_custom_gallery("mine")
        self.assertFalse(receipt["success"])
        self.assertFalse(receipt["applied"])
        self.assertEqual(self.active["inUse"], "")
        self.client.settings.persistedValues["gameMode"] = "difficult"
        receipt = self.client._use_custom_gallery("mine")
        self.assertFalse(receipt["success"])
        self.assertFalse(receipt["applied"])

    def test_gallery_changes_only_revoke_display_when_custom_is_selected(self):
        self.client._custom_gallery_changed()
        self.client.coordinator._revoke_runtime.assert_not_called()
        self.state["profileId"] = "custom:mine"
        self.client._custom_gallery_changed()
        self.client.coordinator._revoke_runtime.assert_called_once_with("custom-gallery-changed")
        self.client.session.refreshRuntime.assert_called_once_with()

    def test_restore_uses_actual_modes_previous_author_not_another_modes_draft(self):
        self.client._use_custom_gallery("mine")
        self.client.settings.persistedValues = deepcopy(self.client.settings.values)
        self.client.route_choices.mode = "difficult"
        self.client.route_choices.profiles = [dict(id="difficult-author", name="Other")]
        result = self.client._use_custom_gallery("")
        self.assertTrue(result["success"])
        self.assertEqual(self.client.settings.values["modeRouteProfiles"]["nightmare"], "author-one")

    def test_display_injection_is_mode_bound_and_does_not_prepare_engines(self):
        factories = EngineFactories(Path("work/not-a-resource-root"))
        sentinel = object()
        engines = {mode: SimpleNamespace(identity=sentinel) for mode in factories.MODES}
        factories._engines.update(engines)
        provider = Mock(side_effect=lambda profile, map_id, floor, reference, mode: mode)
        reports = factories.capabilities()
        factories.set_custom_display(provider)
        for mode, engine in engines.items():
            self.assertEqual(engine.qt_custom_display("custom:mine", "map", "1", sentinel), mode)
            self.assertIs(engine.identity, sentinel)
        self.assertEqual(reports, factories.capabilities())
        factories.set_custom_display(None)
        self.assertTrue(all(engine.qt_custom_display is None for engine in engines.values()))


if __name__ == "__main__":
    unittest.main()
