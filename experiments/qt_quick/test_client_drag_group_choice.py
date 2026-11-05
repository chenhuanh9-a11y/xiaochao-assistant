"""Drag -> explicit registered-member choice through the production Qt chain.

Real mailbox, native-input backend, Coordinator, Session QRunnable, runtime,
core scope checks, sibling staging and material ACK run here. OS capture/native
services, pixels, registry/ranking evidence, solver measurements, DIP conversion
and image/window sinks are controlled fixtures. This is not live game evidence.
The target fit must consume a newly captured post-drag frame and provide its own
canonical matrix: copying the source drag offset is not a target fit.
"""
from copy import deepcopy
import json
import time
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

import numpy as np
from client_live_runtime import _AlignmentHost
import test_client_drag_probe_presentation as drag_fixtures
import test_client_similarity_protocol as group_fixtures
from test_field_map_close_input import controls


class DragGroupChoiceContracts(unittest.TestCase):
    mode = "nightmare"
    dispatch = drag_fixtures.DragProbePresentationContracts.dispatch
    _stop_timers = drag_fixtures.DragProbePresentationContracts._stop_timers
    _cleanup_input_session = drag_fixtures.DragProbePresentationContracts._cleanup_input_session
    pointer = drag_fixtures.DragProbePresentationContracts.pointer

    def setUp(self):
        drag_fixtures.DragProbePresentationContracts.setUp(self)
        self.timeline = []
        self.frame = controls()
        self.frame[0, 0] = 1
        self.backend.capture = lambda hwnd, region: self.frame.copy()
        self.capture_markers = []
        original_capture = self.backend.capture
        def capture(hwnd, region):
            pixels = original_capture(hwnd, region)
            self.capture_markers.append(int(pixels[0, 0, 0]))
            return pixels
        self.backend.capture = capture
        target = SimpleNamespace(id=group_fixtures.IDS[1])
        self.engine.pack.maps.append(target)
        self.engine.pack.by_id[target.id] = target
        self.engine.qt_logical_group_registry = group_fixtures.explicit_registry(self.mode)
        self.engine.rank = Mock(return_value=group_fixtures.rows(mode=self.mode))
        self.engine.rank_established_group = Mock(side_effect=AssertionError("no automatic member competition"))
        self.fit_calls = []
        self.target_geometry_ok = True
        self.source_matrix = np.array([[.5,0,40],[0,.5,60],[0,0,1.]])
        self.target_matrix = np.array([[.5,0,173],[0,.5,219],[0,0,1.]])
        def fit(frame, map_id, floor, cancelled, *args, **kwargs):
            self.fit_calls.append(dict(mapId=map_id, floor=str(floor), marker=int(frame[0,0,0])))
            if map_id == target.id and not self.target_geometry_ok:
                return dict(success=False, reason="initial-structure-current-geometry-failed")
            return dict(success=True, matrixSourceToClient=(
                self.target_matrix.copy() if map_id == target.id else self.source_matrix.copy()),
                selectedRouteCurrentFrameResidual=dict(verified=True,
                    poseVerifiedAgainstCanonicalGeometry=True, profileRenderDerivedFromCanonical=True,
                    coordinatePackId="fixture-pack", validationBasis="shared-canonical-geometry-current-frame"))
        measured_fit = patch.object(_AlignmentHost, "fit", side_effect=fit)
        measured_fit.start(); self.addCleanup(measured_fit.stop)
        self.drive("end_round")
        self.drive("start_round")
        receipt = self.drive("capture_now")
        self.assertTrue(receipt["success"], receipt)
        self.assertTrue(self.runtime.snapshot()["groupLocked"])
        self.assertEqual(self.runtime.snapshot()["currentGroupMemberId"], group_fixtures.IDS[0])
        self.initial_cycle = self.runtime.snapshot()["cycleId"]
        self.initial_round = self.runtime.snapshot()["roundId"]
        self.fit_calls.clear()

    def settled(self):
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline:
            self.app.processEvents()
            if not self.session.busy and self.session._pool.activeThreadCount() == 0:
                self.app.processEvents()
                return
            time.sleep(.001)
        self.fail("bounded Session/coordinator delivery did not settle")

    def record(self, label, receipt=None):
        state = self.runtime.snapshot()
        item = dict(step=label, mode=self.mode, round=state.get("roundId"), cycle=state.get("cycleId"),
            frame=state.get("frameId"), current=state.get("currentGroupMemberId"),
            desired=state.get("desiredGroupMemberId"), status=state.get("groupMemberChoiceStatus"),
            transient=bool(self.runtime._transient_cycle_current()),
            renderMap=(state.get("render") or {}).get("mapId"),
            targetFitCalls=[dict(row) for row in self.fit_calls if row["mapId"]==group_fixtures.IDS[1]],
            pendingMap=self.coordinator.pendingAlignedData.get("mapId"))
        if receipt is not None:
            item["receipt"] = {key:receipt[key] for key in ("action", "success", "reason", "poseReason",
                "choiceApplied", "fitAttempted", "poseCommits", "pendingSiblingToken") if key in receipt}
        self.timeline.append(item)
        return item

    def drive(self, action, **payload):
        self.session.dispatchWithPayload(action, payload)
        self.settled()
        receipt = self.session.snapshot["lastReceipt"]
        self.record(action, receipt)
        return receipt

    def finish_drag(self):
        group = self.runtime._group
        root = group.root_pose.copy()
        self.pointer("down", 500, 500)
        self.assertIsNotNone(self.coordinator._pointer_drag)
        self.pointer("move", 530, 515)
        # Pixels supplied by the next actual capture represent the post-drag
        # frame; the earlier pre-drag observation has marker 1.
        self.frame[0, 0] = 2
        self.pointer("up", 540, 520)
        self.settled()
        self.assertIsNone(self.coordinator._pointer_drag)
        self.assertTrue(self.runtime._transient_cycle_current())
        self.assertEqual(self.runtime._transient_cycle["offset"], [40.,20.])
        np.testing.assert_allclose(group.root_pose, root)
        self.assertEqual(self.fit_calls, [], "drag/probe must not fit or commit a pose")
        self.assertEqual(self.runtime.snapshot()["cycleId"], self.initial_cycle)
        self.assertEqual(self.session.snapshot["lastReceipt"]["reason"], "post-drag-observation-current")
        self.record("drag-completed", self.session.snapshot["lastReceipt"])

    def choose_target(self):
        state = self.session.snapshot
        return self.drive("select_group_member", mapId=group_fixtures.IDS[1],
            **{key:state[key] for key in ("groupId", "groupGeneration", "cycleId", "floor")})

    def await_target_material(self):
        for _ in range(3):
            if self.coordinator.pendingAlignedData.get("mapId") == group_fixtures.IDS[1]:
                break
            self.drive("refresh_map")
        target_calls = [row for row in self.fit_calls if row["mapId"] == group_fixtures.IDS[1]]
        evidence = json.dumps(self.timeline, ensure_ascii=False, sort_keys=True)
        self.assertTrue(target_calls, "explicit target never reached current-frame fit: " + evidence)
        self.assertTrue(all(row["marker"]==2 for row in target_calls),
            "target fit used pre-drag pixels instead of a fresh observation: " + evidence)
        pending = self.coordinator.pendingAlignedData
        self.assertEqual(pending.get("mapId"), group_fixtures.IDS[1], evidence)
        self.assertEqual(self.runtime.snapshot()["currentGroupMemberId"], group_fixtures.IDS[0])
        self.assertEqual(self.runtime.snapshot()["cycleId"], self.initial_cycle)
        self.assertEqual(self.runtime.snapshot()["roundId"], self.initial_round)
        np.testing.assert_allclose(pending["canonicalMatrix"], self.target_matrix)
        self.assertFalse(pending.get("projectionOnly", False))
        return pending["token"]

    def test_drag_then_explicit_member_choice_gets_fresh_fit_and_waits_for_material(self):
        self.finish_drag()
        self.choose_target()
        token = self.await_target_material()
        staged = self.runtime.snapshot()["poseResult"]
        self.assertTrue(staged["pendingMaterial"])
        self.assertEqual(staged["mapId"], group_fixtures.IDS[1])
        self.assertEqual(staged["frameId"], self.coordinator.pendingAlignedData["frameId"])
        self.assertEqual(staged["sourceFrameId"], self.runtime.snapshot()["frameId"])
        self.coordinator.acceptSiblingMaterial(token, True)
        self.settled()
        state = self.runtime.snapshot()
        self.assertEqual(state["currentGroupMemberId"], group_fixtures.IDS[1])
        self.assertEqual(state["groupMemberChoiceStatus"], "confirmed")
        self.assertTrue(state["manualGroupMemberHeld"])
        self.assertEqual(state["poseResult"]["frameId"], state["frameId"])
        self.assertTrue(state["poseResult"]["materialAccepted"])
        self.assertNotIn("pendingMaterial", state["poseResult"])
        self.assertEqual(self.coordinator.alignedData.get("mapId"), group_fixtures.IDS[1])
        np.testing.assert_allclose(self.runtime._group.root_pose, self.target_matrix)
        self.engine.rank_established_group.assert_not_called()

    def test_rejected_target_keeps_desire_then_fresh_refresh_retries_same_cycle(self):
        self.finish_drag()
        self.target_geometry_ok = False
        self.choose_target()
        self.drive("refresh_map")
        self.assertEqual(self.runtime.snapshot()["currentGroupMemberId"], group_fixtures.IDS[0])
        self.assertEqual(self.runtime.snapshot()["desiredGroupMemberId"], group_fixtures.IDS[1])
        self.assertEqual(self.runtime.snapshot()["groupMemberChoiceStatus"], "awaiting-geometry")
        self.assertFalse(self.coordinator.pendingAlignedData)
        self.target_geometry_ok = True
        self.await_target_material()
        self.engine.rank_established_group.assert_not_called()

    def test_map_close_rejects_late_target_material_after_drag_choice(self):
        self.finish_drag()
        self.choose_target()
        token = self.await_target_material()
        self.keys._dispatch_action("escape", {"createdAt":1.})
        self.settled()
        self.coordinator.acceptSiblingMaterial(token, True)
        self.settled()
        self.assertFalse(self.runtime.snapshot()["mapOpen"])
        self.assertIsNone(self.runtime.render_payload(token))
        self.assertFalse(self.coordinator.alignedData)
        self.assertNotEqual(self.runtime.snapshot()["currentGroupMemberId"], group_fixtures.IDS[1])

    def test_drag_without_explicit_choice_remains_observation_only(self):
        self.finish_drag()
        for _ in range(3):
            self.drive("refresh_map")
        self.assertEqual(self.fit_calls, [])
        self.assertEqual(self.runtime.snapshot()["currentGroupMemberId"], group_fixtures.IDS[0])
        self.assertFalse(self.runtime.snapshot()["desiredGroupMemberId"])
        self.assertTrue(self.runtime._transient_cycle_current())
        self.engine.rank_established_group.assert_not_called()

    def test_fresh_target_capture_without_map_retires_source_and_does_not_fit(self):
        self.finish_drag()
        with patch("map_interface_observation.detect_map_interface", return_value=dict(detected=False)):
            self.choose_target()
        self.assertEqual(self.fit_calls, [])
        self.assertFalse(self.runtime.snapshot()["displayAuthorized"])
        self.assertFalse(self.coordinator.alignedData)
        self.assertFalse(self.coordinator.pendingAlignedData)
        self.assertEqual(self.runtime.snapshot()["desiredGroupMemberId"], group_fixtures.IDS[1])

    def test_fresh_target_capture_without_floor_does_not_reuse_old_floor_pose(self):
        self.finish_drag()
        with patch("floor_control_region.locate_vertical_floor_controls", return_value=dict(selectedFloor=None)):
            self.choose_target()
        self.assertEqual(self.fit_calls, [])
        self.assertFalse(self.runtime.snapshot()["displayAuthorized"])
        self.assertFalse(self.coordinator.alignedData)
        self.assertFalse(self.coordinator.pendingAlignedData)


class DifficultDragGroupChoiceContracts(DragGroupChoiceContracts):
    mode = "difficult"


if __name__ == "__main__":
    unittest.main()
