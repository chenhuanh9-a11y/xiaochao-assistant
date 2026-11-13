"""Temporal 0059 capture stays outside pure mathematics; no device evidence."""
import os
import copy
from pathlib import Path
from types import MethodType, SimpleNamespace
import unittest
from unittest.mock import Mock, patch
import weakref

import numpy as np

from author_display_policy import shared_route_validation_spec
from client_idvb0059_adapter import Idvb0059Adapter
import idvb0059_runtime as original
from round_layer_group import RoundLayerGroup
from shared_structure_reference import SharedStructureReference
from structure_pose_solver import PreparedStructurePoseSolver


class _RamInputs:
    def __init__(self, reference, pack, adapters):
        self._reference, self.pack, self.adapters = reference, pack, adapters

    def new_geometry_engine(self):
        return SimpleNamespace(pack=self.pack, qt_adapters=self.adapters)

    def require_scope(self, map_id, floor):
        if (map_id, str(floor)) != ("map", "1"):
            raise ValueError("test RAM scope")

    def reference(self, profile, map_id, floor):
        self.require_scope(map_id, floor)
        return self._reference

    def prepared(self, reference, *, cancelled=None):
        return None  # This fixture supplies only the real 0059 reference.


class PreparedIdvb0059InputTests(unittest.TestCase):
    def setUp(self):
        self.environment = patch.dict(os.environ, {"IDENTITYV_IDVB0059_STEADY": "1"})
        self.environment.start()
        self.addCleanup(self.environment.stop)
        mask = np.zeros((400, 400), np.uint8)
        for x, y, width, height in ((20, 20, 60, 50), (140, 30, 90, 75), (270, 55, 80, 100),
                                   (50, 170, 95, 60), (210, 200, 110, 55),
                                   (75, 300, 50, 60), (230, 300, 60, 70)):
            mask[y:y+height, x:x+width] = 1
        mask = np.frombuffer(mask.tobytes(), np.uint8).reshape(mask.shape)
        self.reference = SharedStructureReference(mask, mask, dict(
            sourceKind="game-rendered-shared-structure",
            binding=dict(profile="shared-structure", mapId="map", floor="1", variant="variant-01")),
            Path("synthetic-0059-reference"))
        self.frame = np.full((720, 1280, 3), 16, np.uint8)
        self.frame[170:570, 400:800][mask > 0] = (100, 130, 170)
        self.group = RoundLayerGroup("map", "shared-structure", "1",
            np.array([[1., 0, 400], [0, 1., 170], [0, 0, 1.]]))
        self.group.canonical_geometry_verified_floors = {"1"}
        self.events = []
        self.pack = SimpleNamespace(pack_id="synthetic-0059-pack")
        self.adapters = SimpleNamespace(canonical_pack_id="synthetic-0059-pack",
                                       matrix_profile_to_canonical=lambda *args: np.eye(3))
        self.signature = (1280, 720, 96)
        self.host = SimpleNamespace(pack=self.pack, author_coordinate_adapters=self.adapters,
            _shared_structure_mode_enabled=lambda: True,
            _active_round_layer_group=lambda mid: self.group if mid == "map" else None,
            _shared_structure_reference=lambda *args: self.reference,
            _open_cycle_transient_motion_active=lambda mid: False,
            current_frame_display_signature=self.signature,
            _log=lambda event, **values: self.events.append((event, values)))
        self.host._route_profile_validation_spec = MethodType(shared_route_validation_spec, self.host)
        self.inputs = _RamInputs(self.reference, self.pack, self.adapters)
        self.adapter = Idvb0059Adapter()
        self.context, self.context_current = ("round", "frame", "resource"), True
        self.current = Mock(side_effect=lambda captured: self.context_current and captured == self.context)

    def remember(self):
        return self.adapter.remember_committed(self.host, self.frame, "map", "1",
            context=self.context, is_current=self.current, material_accepted=True,
            display_signature=self.signature)

    def capture(self, *, frame=None, map_id="map", floor="1", **kwargs):
        options = dict(context=self.context, is_current=self.current, display_signature=self.signature)
        options.update(kwargs)
        return self.adapter.capture_prepared_input(self.host, self.frame if frame is None else frame,
            map_id, floor, **options)

    def test_real_adapter_capture_keeps_false_current_outside_pure_fit(self):
        self.assertTrue(self.remember())
        data, generation = self.capture()
        self.assertIsNotNone(data)
        self.assertTrue(self.adapter.generation_current(generation))
        self.current.reset_mock()
        self.context_current = False
        # A real context callback still rejects the outer publication, while the
        # captured data itself can be evaluated independently of that callback.
        with patch.object(self.adapter, "_obsolete", side_effect=AssertionError("math called lifecycle")), \
             patch.object(self.adapter, "_capture", side_effect=AssertionError("math captured owner")), \
             patch("fast_structure_alignment.try_alignment", side_effect=AssertionError("0059 must precede fast")):
            fit = PreparedStructurePoseSolver(self.inputs, self.group, steady_input=data).fit(
                self.frame, "map", "1", lambda: False)
        self.assertTrue(fit["success"], self.events)
        self.assertEqual(fit["reason"], "idvb0059-steady")
        self.current.assert_not_called()
        self.assertFalse(self.current(self.context))

    def test_reset_after_capture_does_not_change_math_but_rejects_collection(self):
        self.assertTrue(self.remember())
        data, generation = self.capture()
        before = data.try_alignment(self.host, self.frame, "map", "1", cancelled=lambda: False)
        self.adapter.reset()
        self.current.reset_mock()
        after = data.try_alignment(self.host, self.frame, "map", "1", cancelled=lambda: False)
        self.assertTrue(before["success"] and after["success"], self.events)
        np.testing.assert_array_equal(before["matrixSourceToClient"], after["matrixSourceToClient"])
        self.current.assert_not_called()
        self.assertFalse(self.adapter.generation_current(generation))

    def test_no_prior_and_disabled_capture_preserve_none_fallback(self):
        with patch("idvb0059_steady.solve", side_effect=AssertionError("no prior solve")):
            data, generation = self.capture()
            self.assertIsNone(data)
            self.assertTrue(self.adapter.generation_current(generation))
        self.assertTrue(self.remember())
        self.current.reset_mock()
        self.assertIsNone(self.capture(enabled=False)[0])
        with patch.dict(os.environ, {"IDENTITYV_IDVB0059_STEADY": "0"}):
            self.assertIsNone(self.capture()[0])
        self.current.assert_not_called()

    def test_legacy_public_adapter_still_rejects_false_current_with_false_cancelled(self):
        self.assertTrue(self.remember())
        self.context_current = False
        self.current.reset_mock()
        fit = self.adapter.try_alignment(self.host, self.frame, "map", "1",
            context=self.context, is_current=self.current, cancelled=lambda: False,
            display_signature=self.signature)
        self.assertEqual(fit, dict(success=False, reason="structure-work-obsolete"))
        self.assertGreater(self.current.call_count, 0)

    def test_capture_qualifies_group_reference_floor_shape_signature_and_bridge(self):
        self.assertTrue(self.remember())
        with patch("idvb0059_steady.solve", side_effect=AssertionError("capture ran solver")):
            self.assertIsNone(self.capture(floor="2")[0])
            self.assertIsNone(self.capture(frame=self.frame[:700])[0])
            self.assertIsNone(self.capture(display_signature=(1280, 720, 120))[0])
            with patch.object(self.host, "_active_round_layer_group", return_value=None):
                self.assertIsNone(self.capture()[0])
            changed = SharedStructureReference(self.reference.clean_mask, self.reference.validation_mask,
                                               self.reference.metadata, Path("replacement"))
            with patch.object(self.host, "_shared_structure_reference", return_value=changed):
                self.assertIsNone(self.capture()[0])
            with patch.object(self.host, "_open_cycle_transient_motion_active", return_value=True):
                self.assertIsNone(self.capture()[0])
            with patch.object(self.adapters, "canonical_pack_id", None):
                self.assertIsNone(self.capture()[0])
            self.context_current = False
            self.assertIsNone(self.capture(cancelled=lambda: False)[0])

    def test_final_capture_callback_reset_cannot_return_an_old_prior(self):
        self.assertTrue(self.remember())
        calls = []
        def reset_at_final_check(context):
            calls.append(context)
            if len(calls) == 2:
                self.adapter.reset()
            return True
        data, generation = self.capture(is_current=reset_at_final_check)
        self.assertIsNone(data)
        self.assertEqual(len(calls), 2)
        self.assertFalse(self.adapter.generation_current(generation))

    def test_captured_coverage_is_isolated_and_data_retains_no_group_or_owner(self):
        self.assertTrue(self.remember())
        key = original._floor_key(self.host, "map", "1", self.frame)
        before = copy.deepcopy(self.adapter._state["floors"][key]["coverage"])
        data, _ = self.capture()
        self.assertEqual(data.coverage["referenceToken"], id(self.reference))
        self.assertEqual(data.coverage["entries"], before["entries"])
        with self.assertRaises(TypeError):
            data.coverage["entries"][next(iter(data.coverage["entries"]))]["refreshPending"] = False
        self.assertFalse(any(value is self.adapter or value is self.current or value is self.context
                             for value in vars(data).values()))
        group = weakref.ref(self.group)
        self.group = None
        self.assertIsNone(group())
        fit = data.try_alignment(self.host, self.frame, "map", "1", cancelled=lambda: False)
        self.assertTrue(fit["success"], self.events)
        self.assertEqual(self.adapter._state["floors"][key]["coverage"], before)

    def test_prepared_and_legacy_use_the_same_real_numeric_result_contract(self):
        self.assertTrue(self.remember())
        data, _ = self.capture()
        legacy = self.adapter.try_alignment(self.host, self.frame, "map", "1",
            context=self.context, is_current=self.current, display_signature=self.signature)
        self.current.reset_mock()
        prepared = data.try_alignment(self.host, self.frame, "map", "1", cancelled=lambda: False)
        self.assertTrue(legacy["success"] and prepared["success"], self.events)
        np.testing.assert_allclose(legacy["matrixSourceToClient"], prepared["matrixSourceToClient"], atol=1e-10)
        for name in ("verificationMethod", "coordinatePackId", "validationBasis",
                     "poseVerifiedAgainstSharedStructure", "poseVerifiedAgainstRenderedProfile"):
            self.assertEqual(legacy["selectedRouteCurrentFrameResidual"][name],
                             prepared["selectedRouteCurrentFrameResidual"][name])
        self.current.assert_not_called()

    def test_prepared_scope_and_finite_matrix_checks_do_not_query_live_spec(self):
        self.assertTrue(self.remember())
        data, _ = self.capture()
        with patch("idvb0059_steady.solve", side_effect=AssertionError("wrong scope solved")):
            self.assertIsNone(data.try_alignment(self.host, self.frame, "map", "2", cancelled=lambda: False))
            self.assertIsNone(data.try_alignment(self.host, self.frame[:700], "map", "1", cancelled=lambda: False))
        invalid = dict(accepted=True, reason="controlled invalid matrix", diagnostics={},
            matrixSourceToClient=np.array([[1., 0, np.nan], [0, 1., 170]]), coverageState={})
        with patch("idvb0059_steady.solve", return_value=invalid), \
             patch.object(self.host, "_route_profile_validation_spec", side_effect=AssertionError("live spec")):
            self.assertIsNone(data.try_alignment(self.host, self.frame, "map", "1", cancelled=lambda: False))


if __name__ == "__main__":
    unittest.main()
