"""Registration contracts only; synthetic frames are not native/live proof."""
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "app"), str(ROOT / "experiments/qt_quick")]

import s1_floor_observation as detector
from s1_native_resources import S1NativeContext, S1_RECORDED_CANDIDATE_CAPABILITY
from client_live_runtime import LiveRuntime
from s1_candidate_purpose import _NATIVE_S1_PREVIEW
from test_s1_candidate_runtime import Registry
from test_s1_manual_floor_runtime import CurrentFixtureCapture


class SourceReplayCapabilityTests(unittest.TestCase):
    def test_four_floor_evidence_is_consistent_without_live_or_identity_grant(self):
        self.assertEqual(detector.SOURCE_REPLAY_FLOORS, (-1, -2, -3, -4))
        context = S1NativeContext.__new__(S1NativeContext)
        floor = context.floor_observation_capability
        candidate = context.structure_candidate_capability
        self.assertEqual(tuple(floor["sourceReplayFloors"]), detector.SOURCE_REPLAY_FLOORS)
        self.assertEqual(floor["syntheticOnlyFloors"], [])
        self.assertEqual(candidate["observedFloors"], detector.SOURCE_REPLAY_FLOORS)
        self.assertEqual(candidate["contractFloors"], detector.ORDER)
        self.assertFalse(floor["liveValidated"])
        self.assertFalse(candidate["liveValidated"])
        self.assertEqual(floor["contract"], "s1-floor-only-manual-map-use")
        self.assertNotIn("automaticIdentityAvailable", candidate)
        self.assertNotIn("displayAuthorized", candidate)

    def runtime(self, *, purpose=None):
        registry = Registry()
        registry.capability.clear()
        registry.capability.update(S1_RECORDED_CANDIDATE_CAPABILITY)
        runtime = LiveRuntime(engine_factories=registry,
            capture_provider=CurrentFixtureCapture(), evidence_kind="live-client-capture",
            s1_candidate_purpose=purpose)
        self.addCleanup(runtime.shutdown)
        runtime.configure_settings(dict(mapSelectionMode="auto"))
        self.assertTrue(runtime.dispatch("set_context", dict(season="s1", mode="nightmare"))["success"])
        return runtime, registry

    def test_private_purpose_admission_uses_current_four_floor_registration(self):
        # This purpose-token seam uses only a synthetic transport. It does not
        # establish native capture, solver correctness or material delivery.
        runtime, registry = self.runtime(purpose=_NATIVE_S1_PREVIEW)
        with patch("s1_structure_selection.StructureCatalog.from_engine", return_value=registry.catalog):
            self.assertTrue(runtime.dispatch("start_round")["success"])
        self.assertFalse(runtime.snapshot()["liveEngineAvailable"])
        self.assertFalse(runtime.snapshot()["automaticIdentityAvailable"])
        self.assertFalse(runtime.snapshot()["identityLocked"])
        self.assertIsNone(runtime._render)

    def test_registration_alone_does_not_enable_ordinary_live_runtime(self):
        runtime, _ = self.runtime()
        result = runtime.dispatch("start_round")
        self.assertFalse(result["success"])
        self.assertTrue(result["unavailable"])
        self.assertFalse(runtime.snapshot()["liveEngineAvailable"])
        self.assertIsNone(runtime._render)

    def test_explicit_limited_capability_still_rejects_private_purpose(self):
        runtime, registry = self.runtime(purpose=_NATIVE_S1_PREVIEW)
        # An intentionally limited adapter remains a negative fixture even
        # though the production registry now has all four source-replayed rows.
        registry.capability["observedFloors"] = (-1, -2)
        result = runtime.dispatch("start_round")
        self.assertFalse(result["success"])
        self.assertTrue(result["unavailable"])
        self.assertIsNone(runtime._render)


if __name__ == "__main__":
    unittest.main()
