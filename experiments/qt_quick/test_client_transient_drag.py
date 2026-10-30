"""Approved pointer protocol and real runtime ownership; synthetic capture only."""
import copy
import unittest
from unittest.mock import patch
import numpy as np
import test_client_live_runtime as fixtures
from client_live_runtime import _AlignmentHost

class TransientDragTests(unittest.TestCase):
    setUp = fixtures.LiveRuntimeContracts.setUp
    dispatch = fixtures.LiveRuntimeContracts.dispatch
    begin_and_capture = fixtures.LiveRuntimeContracts.begin_and_capture

    def event(self, phase, sequence, total=(0.,0.), delta=(0.,0.), **overrides):
        result=dict(phase=phase,gestureId="fixture-gesture",pointerSequence=sequence,
                    context=self.context,createdAt=1.,totalDelta=list(total),delta=list(delta))
        result.update(overrides)
        return self.runtime.observe_game_drag(result)

    def begin_drag(self):
        self.begin_and_capture()
        self.context=self.runtime.game_floor_click_context()
        self.assertTrue(self.event("down",1)["success"])

    def test_approved_drag_changes_only_transient_matrix_not_pose_root_reference_or_frame(self):
        self.begin_drag()
        before=self.runtime.snapshot();core=copy.deepcopy(before["core"])
        group=self.runtime._group;root=group.root_pose.copy()
        reference=self.runtime.reference_payload()
        matrix=np.array(before["render"]["matrix"])
        with patch.object(_AlignmentHost,"fit",side_effect=AssertionError("gesture must not fit")), \
             patch.object(self.runtime._steady,"remember_committed",side_effect=AssertionError("gesture must not remember")):
            moved=self.event("move",2,(13.,-7.),(13.,-7.))
            self.assertTrue(moved["success"])
            self.assertEqual(self.runtime.game_floor_click_context(),self.context)
            row=self.runtime.render_payload(moved["renderToken"])
            expected=matrix.copy();expected[:2,2]+=[13.,-7.]
            np.testing.assert_allclose(row["matrix"],expected)
            self.assertTrue(row["transient"])
            self.assertFalse(self.runtime.acknowledge_render_material(row["token"])["success"])
            self.assertEqual(self.runtime.snapshot()["core"],core)
            np.testing.assert_allclose(group.root_pose,root)
            self.assertIs(self.runtime._group,group)
            self.assertEqual(self.runtime.reference_payload()["token"],reference["token"])
            self.assertNotIn("transientOffset",self.runtime.reference_payload())
            up=self.event("up",3,(15.,-6.),(2.,1.))
            self.assertTrue(up["probeRequired"]);self.assertEqual(up["poseCommits"],0)
        self.assertEqual(before["frameId"],self.runtime.snapshot()["frameId"])
        self.assertEqual(before["generation"],self.runtime.snapshot()["generation"])

    def test_up_probe_and_later_passive_refresh_never_fit_commit_or_remember(self):
        self.begin_drag();self.event("move",2,(9.,4.),(9.,4.));self.event("up",3,(9.,4.))
        core=copy.deepcopy(self.runtime.snapshot()["core"]);group=self.runtime._group;root=group.root_pose.copy()
        count=self.capture.count
        with patch.object(_AlignmentHost,"fit",side_effect=AssertionError("post-drag probe must not fit")), \
             patch.object(self.runtime._steady,"remember_committed",side_effect=AssertionError("post-drag probe must not remember")):
            probe=self.dispatch("probe_map_after_drag",gestureId="fixture-gesture",pointerSequence=3,context=self.context)
            self.assertTrue(probe["success"],probe)
            self.assertEqual(probe["reason"],"post-drag-observation-current")
            later=self.dispatch("refresh_map")
            self.assertTrue(later["success"],later)
            self.assertFalse(later["fitAttempted"])
        self.assertEqual(self.capture.count,count+2)
        self.assertEqual(self.runtime.snapshot()["core"],core)
        self.assertIs(self.runtime._group,group);np.testing.assert_allclose(group.root_pose,root)

    def test_close_retires_offset_and_late_gesture_and_reopen_uses_persistent_root(self):
        self.begin_drag();base=np.array(self.runtime._render["matrix"])
        self.event("move",2,(20.,8.),(20.,8.));self.event("up",3,(20.,8.))
        self.dispatch("close_map")
        self.assertIsNone(self.runtime._transient_cycle)
        self.assertFalse(self.event("move",4,(25.,8.),(5.,0.))["success"])
        reopened=self.dispatch("map",inputSequence=1,openIntent=True)
        self.assertTrue(reopened["success"],reopened)
        np.testing.assert_allclose(self.runtime.render_payload(reopened["renderToken"])["matrix"],base)

    def test_zero_click_invalid_delta_and_stale_context_do_not_create_transient(self):
        self.begin_drag()
        self.assertFalse(self.event("move",2,(10.,0.),(1.,0.))["success"])
        self.assertIsNone(self.runtime._transient_cycle)
        up=self.event("up",3)
        self.assertTrue(up["success"]);self.assertFalse(up["probeRequired"])
        self.assertIsNone(self.runtime._transient_cycle)
        bad=dict(self.context,cycleId=100)
        self.assertFalse(self.event("down",4,context=bad)["success"])

    def test_original_calibrated_accumulator_path_to_runtime_drops_outside_and_reentry_and_keeps_up_delta(self):
        from client_map_input import DragMotionAccumulator
        self.begin_drag()
        base=np.array(self.runtime._render["matrix"])
        motion=DragMotionAccumulator((500.,500.),self.context["clientBounds"],[.2,.2,.8,.8])
        batch=motion.consume(dict(phase="move",motionSamples=[dict(x=510.,y=500.),dict(x=1350.,y=500.),
            dict(x=550.,y=500.),dict(x=560.,y=500.)]))
        self.assertEqual(batch["totalDelta"],[20.,0.])
        self.assertTrue(self.event("move",2,batch["totalDelta"],batch["delta"])["success"])
        final=motion.consume(dict(phase="up",x=563.,y=502.))
        self.assertEqual(final["totalDelta"],[23.,2.])
        self.assertTrue(self.event("up",3,final["totalDelta"],final["delta"])["probeRequired"])
        expected=base.copy();expected[:2,2]+=[23.,2.]
        np.testing.assert_allclose(self.runtime._render["matrix"],expected)

    def test_author_repaint_retains_cycle_offset_and_actual_probe_floor_conflict_revokes_render(self):
        self.begin_drag();base=np.array(self.runtime._render["matrix"])
        self.event("move",2,(12.,6.),(12.,6.));self.event("up",3,(12.,6.))
        changed=self.dispatch("set_route_profile",profileId="another-author")
        self.assertTrue(changed["success"],changed)
        expected=base.copy();expected[:2,2]+=[12.,6.]
        np.testing.assert_allclose(self.runtime._render["matrix"],expected)
        with patch("map_interface_observation.detect_selected_floor",return_value=dict(selectedFloor=2)), \
             patch.object(_AlignmentHost,"fit",side_effect=AssertionError("conflicting probe must not fit")):
            result=self.dispatch("refresh_map")
        self.assertFalse(result["success"])
        self.assertEqual(result["reason"],"current-frame-floor-conflict")
        self.assertIsNone(self.runtime._render)
        self.assertIsNone(self.runtime.reference_payload())

if __name__=="__main__":unittest.main()
