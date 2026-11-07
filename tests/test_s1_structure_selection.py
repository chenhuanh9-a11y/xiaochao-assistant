"""Synthetic state contracts only; the separately run replay uses real pixels."""
import copy
import threading
import unittest
from types import SimpleNamespace

import numpy as np
from s1_structure_selection import (StructureCatalog, StructureCandidateScanner,
                                    StructureEvidenceSession, verified_pose)


def catalog(mode="difficult", key="geometry-v1"):
    return StructureCatalog("s1", mode, key, ("a", "b", "c"), ("-1", "-2", "-3", "-4"))


def pose():
    return dict(success=True, reason="shared-fit", matrixSourceToClient=np.eye(3),
        coordinateSpace="shared-structure-to-client", selectedRouteCurrentFrameResidual=dict(
            verified=True, poseVerifiedAgainstSharedStructure=True, withinThreePixels=.95))


def scan(supported=("a", "b"), *, cat=None, floor="-1", value=1, **kwargs):
    fit = lambda frame, mid, floor, cancel, **kw: pose() if mid in supported else dict(success=False, reason="no-chain-ratios")
    return StructureCandidateScanner(cat or catalog(), fit).scan(np.full((8, 8, 3), value, np.uint8),
        floor=floor, floor_source="operator-declared", **kwargs)


class ScannerContracts(unittest.TestCase):
    def test_selected_catalog_only_without_icon_or_entry_input(self):
        calls = []
        def fit(frame, mid, floor, cancel, **kwargs):
            calls.append((mid, floor, kwargs)); return pose()
        result = StructureCandidateScanner(catalog(), fit).scan(np.zeros((8, 9, 3), np.uint8),
            floor="-1", floor_source="operator-declared")
        self.assertEqual([r[0] for r in calls], ["a", "b", "c"])
        self.assertTrue(all(row[2]["observation_bounds"] == (0, 0, 9, 8) for row in calls))
        self.assertEqual(result["supportedMapIds"], ["a", "b", "c"])
        self.assertFalse(result["competitionComplete"])
        self.assertEqual(result["observedFloor"], "")

    def test_single_success_never_means_unique_identity(self):
        result = scan(("a",))
        self.assertTrue(result["scanComplete"])
        self.assertEqual(result["unknownMapIds"], ["b", "c"])
        self.assertEqual(result["excludedMapIds"], [])
        self.assertFalse(result["uniqueIdentityVerified"])
        self.assertFalse(result["automaticSelectionAuthorized"])

    def test_budget_retains_unknown_unscanned_competitors(self):
        ticks = iter([0, 0, 2, 2])
        result = StructureCandidateScanner(catalog(), lambda *a, **kw: pose(), clock=lambda: next(ticks)).scan(
            np.zeros((8, 8, 3), np.uint8), floor="-1", floor_source="operator-declared", budget_seconds=1)
        self.assertEqual(result["supportedMapIds"], ["a"])
        self.assertEqual(result["unscannedMapIds"], ["b", "c"])
        self.assertEqual(result["status"], "budget-unresolved")

    def test_cancellation_after_fit_revokes_partial_success(self):
        event = threading.Event()
        def fit(*args, **kw):
            event.set(); return pose()
        result = StructureCandidateScanner(catalog(), fit).scan(np.zeros((8, 8, 3), np.uint8),
            floor="-1", floor_source="operator-declared", cancelled=event.is_set)
        self.assertEqual(result["status"], "cancelled")
        self.assertFalse(result["supportedMapIds"])
        self.assertFalse(result["poses"])

    def test_invalid_or_wrong_coordinate_pose_is_unknown(self):
        for mutate in (lambda d: d.update(matrixSourceToClient=np.full((3,3), np.nan)),
                       lambda d: d.update(coordinateSpace="author-render-to-client"),
                       lambda d: d["selectedRouteCurrentFrameResidual"].update(verified=False),
                       lambda d: d["selectedRouteCurrentFrameResidual"].pop("poseVerifiedAgainstSharedStructure"),
                       lambda d: d.update(matrixSourceToClient=np.diag([-1, -1, 1]))):
            fit=pose();mutate(fit)
            self.assertFalse(verified_pose(fit))

    def test_floor_source_and_floor_are_explicit(self):
        scanner = StructureCandidateScanner(catalog(), lambda *a, **kw: pose())
        for floor, source in (("1", "operator-declared"), ("-1", "icon"), ("-1", "")):
            with self.assertRaises(ValueError):
                scanner.scan(np.zeros((8,8,3),np.uint8),floor=floor,floor_source=source)

    def test_s0_cannot_construct_s1_catalog(self):
        with self.assertRaises(ValueError):
            StructureCatalog("s0", "difficult", "key", ("a",), ("-1", "-2", "-3", "-4"))

    def test_rank_rows_have_no_entry_or_hard_gate_authority(self):
        rows = StructureCandidateScanner.rank_rows(scan(("a",)), SimpleNamespace(by_id={"a":SimpleNamespace(name="A")}))
        self.assertEqual(rows[0]["mapId"], "a")
        self.assertFalse(rows[0]["identityHardGatePassed"])
        self.assertFalse(rows[0]["strongSuggestion"])
        self.assertNotIn("difficultEntry",rows[0])
        self.assertNotIn("nativeSimilarity",rows[0])


class EvidenceContracts(unittest.TestCase):
    def setUp(self):
        self.session = StructureEvidenceSession(catalog(), "round-1")

    def commit(self, report):
        token = self.session.begin_observation(report["floor"])
        self.assertTrue(self.session.commit(report, token))
        return token

    def test_repeated_same_frame_is_not_new_identity_evidence(self):
        self.commit(scan());self.commit(scan())
        self.assertEqual(self.session.snapshot()["observationCount"], 1)

    def test_more_explored_and_cross_floor_support_does_not_remove_unknowns(self):
        self.commit(scan(("a","b","c"),value=1))
        self.commit(scan(("a","b"),value=2))
        self.commit(scan(("b",),floor="-2",value=3))
        state=self.session.snapshot()
        self.assertEqual(state["supportedOnEveryObservation"], ["b"])
        self.assertEqual(state["possibleMapIds"], ["a","b","c"])
        self.assertFalse(state["uniqueIdentityVerified"])
        self.assertIsNone(state["registeredGroup"])

    def test_candidate_switch_is_current_pose_not_group_or_identity(self):
        token=self.commit(scan())
        for mid in ("a","b"):
            result=self.session.choose_candidate(mid,token)
            self.assertTrue(result["success"])
            self.assertEqual(result["identityKind"],"candidate")
            self.assertTrue(result["requiresManualConfirmation"])
            self.assertFalse(result["displayAuthorized"])
            self.assertIsNone(result["groupId"])
        self.assertFalse(self.session.choose_candidate("c",token)["success"])

    def test_new_frame_or_floor_revokes_choice_before_scan_returns(self):
        old=self.commit(scan())
        token=self.session.begin_observation("-2")
        self.assertFalse(self.session.choose_candidate("a",old)["success"])
        self.assertFalse(self.session.choose_candidate("a",token)["success"])
        self.assertFalse(self.session.commit(scan(),old))
        self.assertFalse(self.session.commit(scan(),token))

    def test_season_mode_resource_generation_context_mismatch_is_rejected(self):
        for field,value in (("season","s0"),("mode","nightmare"),("resourceKey","v2"),
                            ("expectedMapIds",["a"])):
            result=scan();result[field]=value;token=self.session.begin_observation("-1")
            self.assertFalse(self.session.commit(result,token))

    def test_cancelled_report_never_replaces_session(self):
        report=scan();report["status"]="cancelled"
        token=self.session.begin_observation("-1")
        self.assertFalse(self.session.commit(report,token))

    def test_no_support_revokes_current_pose_but_retains_diagnostic_history(self):
        self.commit(scan());token=self.commit(scan((),value=2))
        self.assertFalse(self.session.choose_candidate("a",token)["success"])
        self.assertEqual(self.session.snapshot()["observationCount"],1)
        self.assertFalse(self.session.snapshot()["currentSupportedMapIds"])

    def test_unrelated_support_is_conflict_not_forced_intersection(self):
        self.commit(scan(("a",)));self.commit(scan(("b",),value=2))
        state=self.session.snapshot()
        self.assertTrue(state["conflictingSupport"])
        self.assertEqual(state["supportedOnEveryObservation"],[])
        self.assertEqual(state["possibleMapIds"],["a","b","c"])

    def test_evidence_bound_and_result_mutation_are_isolated(self):
        self.session=StructureEvidenceSession(catalog(),"round-1",max_observations=2)
        for value in (1,2,3):token=self.commit(scan(value=value))
        self.assertEqual(self.session.snapshot()["observationCount"],2)
        result=self.session.choose_candidate("a",token);result["pose"]["matrixSourceToClient"][0,0]=999
        self.assertEqual(self.session.choose_candidate("a",token)["pose"]["matrixSourceToClient"][0,0],1)

    def test_late_report_from_another_round_or_session_is_rejected(self):
        old=self.session
        token=old.begin_observation("-1")
        report=scan()
        old.end_round()
        for round_key in ("round-2", "round-1"):
            fresh=StructureEvidenceSession(catalog(),round_key)
            fresh_token=fresh.begin_observation("-1")
            self.assertNotEqual(token,fresh_token)
            self.assertFalse(fresh.commit(report,token))
            self.assertFalse(fresh.choose_candidate("a",token)["success"])
            self.assertTrue(fresh.commit(report,fresh_token))

    def test_same_generation_cannot_be_replaced_by_a_later_report(self):
        token=self.commit(scan())
        self.assertFalse(self.session.commit(scan(("c",),value=9),token))
        self.assertEqual(self.session.snapshot()["currentSupportedMapIds"],["a","b"])

    def test_end_round_clears_and_rejects_late_results(self):
        token=self.commit(scan());self.session.end_round()
        self.assertFalse(self.session.commit(scan(),token))
        self.assertFalse(self.session.choose_candidate("a",token)["success"])
        self.assertEqual(self.session.snapshot()["observationCount"],0)
        with self.assertRaises(ValueError):self.session.begin_observation("-1")


if __name__ == "__main__":
    unittest.main()
