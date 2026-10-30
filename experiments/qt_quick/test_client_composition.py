"""Composition receipts and display-only injection; no windows or native calls."""
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import Mock

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
        self.client.coordinator = SimpleNamespace(_revoke_runtime=Mock())

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
