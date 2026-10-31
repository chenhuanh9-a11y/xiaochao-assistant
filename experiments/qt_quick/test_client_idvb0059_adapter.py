"""Original 0059 owner protocol through the Qt bridge, with no live authority."""
import copy
import os
from pathlib import Path
from types import SimpleNamespace, MethodType
import unittest
from unittest.mock import patch

import cv2
import numpy as np
from client_idvb0059_adapter import Idvb0059Adapter
from author_display_policy import shared_route_validation_spec
import idvb0059_runtime as original
from round_layer_group import RoundLayerGroup
from shared_structure_reference import SharedStructureReference


class BridgeTests(unittest.TestCase):
    def setUp(self):
        self.environment = patch.dict(os.environ, {}, clear=False)
        self.environment.start(); self.addCleanup(self.environment.stop)
        os.environ.pop("IDENTITYV_IDVB0059_STEADY", None)
        self.frame = np.zeros((240, 320, 3), np.uint8)
        self.reference = SharedStructureReference(np.ones((80, 80), np.uint8),
            np.ones((80, 80), np.uint8), {}, Path("synthetic-reference"))
        self.group = RoundLayerGroup("map", "shared-structure", "1", np.diag([.7, .7, 1.]),
                                     relations={"2": np.eye(3)})
        self.group.canonical_geometry_verified_floors = {"1", "2"}
        self.context = ("round-1", "frame-1")
        self.events = []
        self.host = SimpleNamespace(pack=SimpleNamespace(pack_id="synthetic-pack"),
            author_coordinate_adapters=SimpleNamespace(canonical_pack_id="synthetic-pack",
                matrix_profile_to_canonical=lambda *args: np.eye(3)),
            _shared_structure_mode_enabled=lambda: True,
            _active_round_layer_group=lambda mid: self.group if mid == "map" else None,
            _shared_structure_reference=lambda *args: self.reference,
            _open_cycle_transient_motion_active=lambda mid: False,
            current_frame_display_signature=(320, 240, 96),
            _log=lambda event, **values: self.events.append((event, values)))
        self.host._route_profile_validation_spec = MethodType(shared_route_validation_spec, self.host)
        self.bridge = Idvb0059Adapter()
        self.kw = dict(context=self.context, is_current=lambda captured: captured == self.context)

    def remember(self, **kwargs):
        return self.bridge.remember_committed(self.host, self.frame, "map", "1",
            dict(idvb0059Coverage={"synthetic": 1}), **self.kw,
            **dict(material_accepted=True, **kwargs))

    @staticmethod
    def accepted(*args, **kwargs):
        return dict(accepted=True, reason="controlled-verifier", diagnostics={},
                    matrixSourceToClient=np.array([[.7, 0, 8], [0, .7, 12]]), coverageState={})

    def test_original_proposal_and_current_residual_contract_are_preserved(self):
        original.remember_committed(self.host, "map", "1", self.frame, dict(idvb0059Coverage={"synthetic": 1}))
        self.assertTrue(self.remember())
        with patch("idvb0059_steady.solve", side_effect=self.accepted) as solve:
            old = original.try_alignment(self.host, self.frame, "map", "1")
            new = self.bridge.try_alignment(self.host, self.frame, "map", "1", **self.kw)
        self.assertEqual(solve.call_count, 2)
        np.testing.assert_array_equal(old["matrixSourceToClient"], new["matrixSourceToClient"])
        self.assertEqual(old["selectedRouteCurrentFrameResidual"], new["selectedRouteCurrentFrameResidual"])
        self.assertNotIn("liveAuthorization", new)
        self.assertFalse(hasattr(self.host, "prepared_owner"))

    def test_default_enabled_and_both_explicit_settings_and_environment_disable(self):
        self.assertTrue(self.remember())
        with patch("idvb0059_steady.solve", side_effect=self.accepted) as solve:
            self.assertTrue(self.bridge.try_alignment(self.host, self.frame, "map", "1", **self.kw)["success"])
            self.assertIsNone(self.bridge.try_alignment(self.host, self.frame, "map", "1", enabled=False, **self.kw))
            with patch.dict(os.environ, {"IDENTITYV_IDVB0059_STEADY": "0"}):
                self.assertIsNone(self.bridge.try_alignment(self.host, self.frame, "map", "1", **self.kw))
            self.assertEqual(solve.call_count, 1)

    def test_pending_rejected_or_transient_material_cannot_remember_a_prior(self):
        for accepted, transient in [(False, False), (None, False), (True, True)]:
            self.assertFalse(self.bridge.remember_committed(self.host, self.frame, "map", "1", **self.kw,
                material_accepted=accepted, transient=transient))
        with patch("idvb0059_steady.solve") as solve:
            self.assertIsNone(self.bridge.try_alignment(self.host, self.frame, "map", "1", **self.kw))
            solve.assert_not_called()

    def test_reset_during_original_solver_discards_late_proposal(self):
        self.assertTrue(self.remember())
        def late(*args, **kwargs):
            self.bridge.reset()
            return self.accepted()
        with patch("idvb0059_steady.solve", side_effect=late):
            fit = self.bridge.try_alignment(self.host, self.frame, "map", "1", **self.kw)
        self.assertEqual(fit, dict(success=False, reason="structure-work-obsolete"))
        with patch("idvb0059_steady.solve") as solve:
            self.assertIsNone(self.bridge.try_alignment(self.host, self.frame, "map", "1", **self.kw))
            solve.assert_not_called()

    def test_changed_group_reference_display_or_uncommitted_floor_cannot_reuse_scale(self):
        self.assertTrue(self.remember())
        with patch("idvb0059_steady.solve") as solve:
            self.assertIsNone(self.bridge.try_alignment(self.host, self.frame, "map", "2", **self.kw))
            self.host.current_frame_display_signature = (320, 240, 120)
            self.assertIsNone(self.bridge.try_alignment(self.host, self.frame, "map", "1", **self.kw))
            self.host.current_frame_display_signature = (320, 240, 96)
            self.reference = SharedStructureReference(self.reference.clean_mask, self.reference.validation_mask,
                {}, Path("replaced-reference"))
            self.assertIsNone(self.bridge.try_alignment(self.host, self.frame, "map", "1", **self.kw))
            solve.assert_not_called()
        self.assertTrue(self.remember())
        self.group = RoundLayerGroup("map", "shared-structure", "1", np.eye(3))
        self.group.canonical_geometry_verified_floors = {"1"}
        with patch("idvb0059_steady.solve") as solve:
            self.assertIsNone(self.bridge.try_alignment(self.host, self.frame, "map", "1", **self.kw))
            solve.assert_not_called()

    def test_stale_coverage_measurement_cannot_mutate_committed_prior(self):
        self.assertTrue(self.remember())
        def measure(*args, coverage_state=None):
            coverage_state["synthetic"] = 99
            self.context = ("round-2", "frame-2")
            return coverage_state
        with patch("idvb0059_steady.observe_coverage", side_effect=measure):
            self.assertFalse(self.bridge.remember_committed(self.host, self.frame, "map", "1", **self.kw,
                material_accepted=True))
        self.context = self.kw["context"]
        with patch("idvb0059_steady.solve", return_value=dict(accepted=False, reason="controlled", diagnostics={})) as solve:
            self.bridge.try_alignment(self.host, self.frame, "map", "1", **self.kw)
            self.assertEqual(solve.call_args.kwargs["coverage_state"], {"synthetic": 1})

    def test_missing_bridge_pack_identity_is_not_replaced_by_a_success_flag(self):
        self.assertTrue(self.remember())
        self.host.author_coordinate_adapters.canonical_pack_id = None
        with patch("idvb0059_steady.solve", side_effect=self.accepted):
            self.assertIsNone(self.bridge.try_alignment(self.host, self.frame, "map", "1", **self.kw))

    def test_tk_outer_finite_matrix_guard_remains_before_shared_validation_spec(self):
        self.assertTrue(self.remember())
        proposal = self.accepted()
        proposal["matrixSourceToClient"][0, 2] = np.nan
        with patch("idvb0059_steady.solve", return_value=proposal):
            self.assertIsNone(self.bridge.try_alignment(self.host, self.frame, "map", "1", **self.kw))

    def test_original_numeric_solver_same_synthetic_input(self):
        # A generated room footprint and its game-colour rendering; no recorded
        # game pixels, author art, PreparedReference injection or solver patches.
        mask = np.zeros((400, 400), np.uint8)
        for x, y, w, h in [(20, 20, 60, 50), (140, 30, 90, 75), (270, 55, 80, 100),
                            (50, 170, 95, 60), (210, 200, 110, 55), (75, 300, 50, 60), (230, 300, 60, 70)]:
            mask[y:y+h, x:x+w] = 1
        self.reference = SharedStructureReference(mask, mask, {}, Path("synthetic-rooms"))
        self.frame = np.full((720, 1280, 3), 16, np.uint8)
        self.frame[170:570, 400:800][mask > 0] = (100, 130, 170)
        self.host.current_frame_display_signature = (1280, 720, 96)
        self.group = RoundLayerGroup("map", "shared-structure", "1",
            np.array([[1., 0, 400], [0, 1., 170], [0, 0, 1.]]))
        self.group.canonical_geometry_verified_floors = {"1"}
        original.remember_committed(self.host, "map", "1", self.frame)
        self.assertTrue(self.bridge.remember_committed(self.host, self.frame, "map", "1", **self.kw,
            material_accepted=True))
        old = original.try_alignment(self.host, self.frame, "map", "1")
        new = self.bridge.try_alignment(self.host, self.frame, "map", "1", **self.kw)
        self.assertIsNotNone(old, self.events)
        self.assertTrue(old["success"] and new["success"])
        np.testing.assert_allclose(old["matrixSourceToClient"], new["matrixSourceToClient"], atol=1e-10)
        self.assertEqual(old["selectedRouteCurrentFrameResidual"]["verificationMethod"], "idvb0059-vpsg3-current-frame")
        self.assertNotIn("liveAuthorization", new)


if __name__ == "__main__":
    unittest.main()
