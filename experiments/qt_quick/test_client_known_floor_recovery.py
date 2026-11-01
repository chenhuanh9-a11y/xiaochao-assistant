"""Known-map geometry only; original side adjudicator, controlled locator IO."""
import copy
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import Mock

import numpy as np

from client_paths import APP_ROOT
if str(APP_ROOT) not in sys.path:
    sys.path.insert(0, str(APP_ROOT))
from client_known_floor_recovery import recover_registered_floor


MAP = "difficult-map-05"


def candidate(*, success=True, pose=True, map_id=MAP, floor="1"):
    return dict(mapId=map_id, floor=floor, profileId="shared-structure", score=12.,
        poseVerified=pose, competitionComplete=True,
        hypothesis=dict(chainCount=7, extentGrid=[4., 3.]),
        verification=dict(success=success, matrixSourceToClient=np.eye(3).tolist(),
            residual=dict(verified=success), regions=dict(regionalEdgeVerified=True)))


class KnownFloorRecoveryTests(unittest.TestCase):
    def setUp(self):
        self.frame = np.zeros((200, 300, 3), np.uint8)
        self.reference = SimpleNamespace(clean_mask=np.ones((40, 50), np.uint8))
        self.main = Mock(return_value=([], dict(complete=True)))
        self.side = SimpleNamespace(rank_entry=Mock(), has_registered_floor=Mock(return_value=True),
            rank=Mock(return_value=([], dict(complete=True, verificationComplete=True, competitiveHypotheses=0))),
            refine_candidate=Mock())
        self.engine = SimpleNamespace(identity_profile_id="shared-structure",
            entry_identity=SimpleNamespace(rank=self.main), side_entry_identity=self.side)
        self.group = SimpleNamespace(map_id=MAP, verified_floors={"2"}, selected_member=MAP)
        self.host = SimpleNamespace(engine=self.engine, group=self.group, _log=Mock(),
            settings={}, selected_member=MAP)

    def recover(self, cancelled=None):
        return recover_registered_floor(self.host, self.frame, MAP, "1", self.reference, cancelled)

    def test_main_fixed_map_pose_without_floor_supplies_geometry_only(self):
        row = candidate()
        row.pop("floor")  # actual main producer scopes its result by request
        self.main.return_value = ([row], dict(complete=True))
        before = copy.deepcopy(self.group.__dict__)
        result = self.recover()
        self.assertEqual(result["reason"], "registered-floor-main-geometry")
        np.testing.assert_array_equal(result["matrixSourceToClient"], np.eye(3))
        self.main.assert_called_once_with(self.frame, "1", map_id=MAP, budget_seconds=.35)
        self.side.rank.assert_not_called()
        self.assertEqual(self.group.__dict__, before)
        self.assertEqual(self.host.selected_member, MAP)

    def test_real_side_adjudicator_accepts_complete_unique_fixed_map_pose(self):
        self.side.rank.return_value = ([candidate()], dict(
            complete=True, verificationComplete=True, competitiveHypotheses=1))
        result = self.recover()
        self.assertEqual(result["reason"], "registered-floor-local-geometry")
        self.side.rank.assert_called_once_with(self.frame, "1", map_id=MAP, budget_seconds=.35)
        self.assertTrue(result["difficultEntryValidation"]["residual"]["verified"])
        self.assertEqual(self.host.selected_member, MAP)

    def test_incomplete_or_same_map_ambiguous_pose_never_becomes_a_fit(self):
        self.side.rank.return_value = ([candidate()], dict(
            complete=False, verificationComplete=True, competitiveHypotheses=1))
        self.assertIsNone(self.recover())
        self.side.rank.return_value = ([candidate(), candidate()], dict(
            complete=True, verificationComplete=True, competitiveHypotheses=2))
        self.assertIsNone(self.recover())  # identity-only is not a usable pose

    def test_single_regional_candidate_uses_original_refinement_and_verifier(self):
        weak = candidate(success=False)
        good = candidate()
        self.side.rank.return_value = ([weak], dict(
            complete=True, verificationComplete=True, competitiveHypotheses=1))
        self.side.refine_candidate.return_value = good
        self.assertEqual(self.recover()["reason"], "registered-floor-local-geometry")
        self.side.refine_candidate.assert_called_once_with(self.frame, weak)

    def test_foreign_candidate_or_refinement_cannot_change_selected_map(self):
        foreign = candidate(map_id="difficult-map-09-2")
        self.side.rank.return_value = ([foreign], dict(
            complete=True, verificationComplete=True, competitiveHypotheses=1))
        self.assertEqual(self.recover()["reason"], "registered-floor-candidate-scope-mismatch")
        self.side.rank.return_value = ([candidate(success=False)], dict(
            complete=True, verificationComplete=True, competitiveHypotheses=1))
        self.side.refine_candidate.return_value = foreign
        self.assertEqual(self.recover()["reason"], "registered-floor-candidate-scope-mismatch")
        self.assertEqual(self.host.selected_member, MAP)

    def test_no_recovery_for_verified_target_unregistered_or_non_entry_first(self):
        self.group.verified_floors.add("1")
        self.assertIsNone(self.recover())
        self.group.verified_floors.remove("1")
        self.side.has_registered_floor.return_value = False
        self.assertIsNone(self.recover())
        self.side.has_registered_floor.return_value = True
        self.engine.identity_profile_id = "author-profile"
        self.assertIsNone(self.recover())
        self.main.assert_not_called()
        self.side.rank.assert_not_called()

    def test_cancellation_before_and_after_each_producer_is_terminal(self):
        self.assertEqual(self.recover(lambda: True)["reason"], "structure-work-obsolete")
        self.main.assert_not_called()
        cancelled = [False]
        def main(*args, **kwargs):
            cancelled[0] = True
            return [candidate()], dict(complete=True)
        self.main.side_effect = main
        self.assertEqual(self.recover(lambda: cancelled[0])["reason"], "structure-work-obsolete")
        self.side.rank.assert_not_called()
        self.main.side_effect = None
        self.main.return_value = ([], dict(complete=True))
        cancelled[0] = False
        def side(*args, **kwargs):
            cancelled[0] = True
            return [candidate()], dict(complete=True, verificationComplete=True, competitiveHypotheses=1)
        self.side.rank.side_effect = side
        self.assertEqual(self.recover(lambda: cancelled[0])["reason"], "structure-work-obsolete")

    def test_resource_error_semantics_do_not_turn_missing_main_into_broad_search(self):
        self.main.side_effect = OSError("controlled missing main")
        self.assertEqual(self.recover()["reason"], "registered-floor-main-resource-unavailable")
        self.side.rank.assert_not_called()
        self.main.side_effect = None
        self.side.rank.side_effect = OSError("controlled missing side")
        self.assertIsNone(self.recover())


if __name__ == "__main__":
    unittest.main()
