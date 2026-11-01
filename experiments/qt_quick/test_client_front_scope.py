"""Entrance-scoped registration through runtime dispatch; controlled pixels/fit.

Rows mirror MapIdentityEngine's actual main/passport/local producer fields.
No game, native input, capture hook or real recognition accuracy is claimed.
"""
import copy
from pathlib import Path
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

import numpy as np

from client_engine import ranked_entrance_kind
from client_group_registry import ConfirmedFrontGroups
from client_live_runtime import LiveRuntime
from shared_structure_reference import SharedStructureReference
import test_client_live_runtime as fixtures


def producer_row(mid, floor="1", kind="main"):
    side = kind in ("side-entry-passport", "side-neighborhood", "local-structure")
    entry = dict(mapId=mid, floor=floor, entryKind=kind, profileId="shared-structure",
        verification=dict(success=True, matrixSourceToClient=np.eye(3).tolist()))
    # MapEntryIdentity.rank's real main candidate omits floor; the requesting
    # rank floor is its scope. Passports carry their own registered floor.
    if kind == "main":
        entry.pop("floor")
    if side:
        entry["locatorKind"] = "side"
    return dict(mapId=mid, entryKind=kind, score=1210., strongSuggestion=True,
        identityHardGatePassed=True, difficultEntry=entry,
        identityEvidence="side-structure-current-frame" if side else "tilemap-entry-neighborhood-current-frame")


class FrontScopeTests(unittest.TestCase):
    setUp = fixtures.LiveRuntimeContracts.setUp
    dispatch = fixtures.LiveRuntimeContracts.dispatch

    def configure(self, mode="nightmare", kind="main", floor="1"):
        self.engine = fixtures.fixture_engine(mode)
        prefix = "nightmare-" if mode == "nightmare" else "difficult-map-"
        self.a, self.b = prefix + "03", prefix + "09" if mode == "nightmare" else prefix + "10"
        items = [SimpleNamespace(id=mid, name=mid) for mid in (self.a, self.b)]
        self.engine.pack.maps = items
        self.engine.pack.by_id = {item.id: item for item in items}

        def reference(profile, mid, layer):
            mask = np.ones((20, 24), np.uint8)
            return SharedStructureReference(mask, mask, dict(
                binding=dict(profile="shared-structure", mapId=mid, floor=str(layer), variant="variant-01"),
                canvas=dict(shapeHW=[20, 24], coordinateSpace="fixture-pixels"),
                structureVersionKey="fixture-version"), Path("fixture.npz"))

        self.engine.qt_shared_provider = reference
        self.engine.qt_logical_group_registry = ConfirmedFrontGroups(self.engine.pack)
        self.engine.qt_logical_group_registry.group_for = Mock(
            wraps=self.engine.qt_logical_group_registry.group_for)
        self.engine.rank = Mock(return_value=[producer_row(self.a, floor, kind)])
        self.engine.qt_map_ui.observe = lambda *args: dict(mapOpen=True, floor=floor)
        self.runtime = LiveRuntime(engine_factories={mode: lambda: self.engine},
            capture_provider=self.capture, evidence_kind="synthetic", clock=lambda: 1.)
        self.dispatch("set_context", mode=mode)

    def start_capture(self):
        self.assertTrue(self.dispatch("start_round")["success"])
        return self.dispatch("capture_now")

    def choose(self, mid):
        state = self.runtime.snapshot()
        return self.dispatch("select_group_member", mapId=mid, **{
            key: state[key] for key in ("groupId", "groupGeneration", "cycleId", "floor")})

    def test_current_producer_front_side_and_unknown_are_distinct(self):
        engine = fixtures.fixture_engine()
        for kind in ("main", "main-entry-passport"):
            with self.subTest(kind=kind):
                self.assertEqual(ranked_entrance_kind(engine, [producer_row("fixture-map", kind=kind)], "1"), "front")
        main_passport = producer_row("fixture-map", kind="main-entry-passport")
        main_passport["entryKind"] = "main"  # final front wrapper's actual label
        main_passport["difficultEntry"]["locatorKind"] = "side"  # shared solver, not entrance type
        self.assertEqual(ranked_entrance_kind(engine, [main_passport], "1"), "front")
        regional = producer_row("fixture-map")
        regional["difficultEntry"]["verification"] = dict(success=False,
            regions=dict(regionalEdgeVerified=True), residual=dict(verified=True))
        self.assertEqual(ranked_entrance_kind(engine, [regional], "1"), "front")
        for kind in ("side-entry-passport", "side-neighborhood", "local-structure"):
            self.assertEqual(ranked_entrance_kind(engine, [producer_row("fixture-map", kind=kind)], "1"), "side")
        advisory = dict(mapId="fixture-map", entryKind="main", identityHardGatePassed=True)
        self.assertEqual(ranked_entrance_kind(engine, [advisory], "1"), "unknown")
        wrong_floor = producer_row("fixture-map")
        wrong_floor["difficultEntry"]["floor"] = "2"
        self.assertEqual(ranked_entrance_kind(engine, [wrong_floor], "1"), "unknown")
        contradicted = producer_row("fixture-map")
        contradicted["difficultEntry"]["entryCrossCheck"] = dict(contradicted=True)
        self.assertEqual(ranked_entrance_kind(engine, [contradicted], "1"), "unknown")

    def test_only_fresh_bound_native_front_condition_can_supply_entry_evidence(self):
        engine = fixtures.fixture_engine()
        condition = dict(kind="front", mode="nightmare", floor="1", domainId="bound-front")
        engine.native_similarity_index = SimpleNamespace(index_digest="current-index",
            condition_index=SimpleNamespace(conditions={"7": condition}))
        observed = dict(conditionId=7, domainId="bound-front", entryEstablished=False,
            completeForeignMapCompetition=True, currentCompetitionComplete=True,
            activeViews=["current-pose"], ambiguousViews=[], outsideCompetitors=[])
        row = dict(mapId="fixture-map", entryKind="main", nativeSimilarity=dict(
            mode="nightmare", floor="1", resourceKey="current-index", conditionObservations=[observed]))
        self.assertEqual(ranked_entrance_kind(engine, [row], "1"), "front")
        observed["entryEstablished"] = True
        self.assertEqual(ranked_entrance_kind(engine, [row], "1"), "unknown")
        observed["entryEstablished"] = False
        row["nativeSimilarity"]["resourceKey"] = "obsolete-index"
        self.assertEqual(ranked_entrance_kind(engine, [row], "1"), "unknown")

    def test_registered_member_side_unique_and_unknown_do_not_become_front_group(self):
        for mode in ("nightmare", "difficult"):
            for kind in ("side-entry-passport", "side-neighborhood", "local-structure", "unknown"):
                with self.subTest(mode=mode, kind=kind):
                    self.configure(mode, kind)
                    if kind == "unknown":
                        self.engine.rank.return_value = [dict(mapId=self.a, entryKind="main",
                            strongSuggestion=True, identityHardGatePassed=True)]
                    result = self.start_capture()
                    self.assertTrue(result["success"], result)
                    state = self.runtime.snapshot()
                    self.assertEqual(state["identityPhase"], "MEMBER_VERIFIED")
                    self.assertFalse(state["groupLocked"])
                    self.assertEqual(state["groupMemberRows"], [])
                    self.assertEqual(self.runtime.render_payload(result["renderToken"])["mapId"], self.a)
                    self.engine.qt_logical_group_registry.group_for.assert_not_called()
                    self.dispatch("end_round")

    def test_actual_confirmed_registry_is_called_with_front_f1_scope(self):
        self.configure()
        result = self.start_capture()
        self.assertTrue(result["success"], result)
        state = self.runtime.snapshot()
        self.assertEqual(state["identityPhase"], "GROUP_LOCKED")
        self.assertFalse(state["identityLocked"])
        self.assertEqual({row["mapId"] for row in state["groupMemberRows"]}, {self.a, self.b})
        self.engine.qt_logical_group_registry.group_for.assert_called_once_with(
            self.a, floor="1", entrance_kind="front")

    def test_cold_difficult_f2_does_not_rank_but_nightmare_f2_does(self):
        for mode in ("difficult", "nightmare"):
            with self.subTest(mode=mode):
                self.configure(mode, "side-neighborhood", "2")
                with patch("map_interface_observation.detect_selected_floor", return_value=dict(selectedFloor=2)):
                    result = self.start_capture()
                if mode == "difficult":
                    self.assertFalse(result["success"])
                    self.assertEqual(result["reason"], "difficult-f2-entry-recognition-disabled")
                    self.engine.rank.assert_not_called()
                    self.assertIsNone(self.runtime.reference_payload())
                else:
                    self.assertTrue(result["success"], result)
                    self.engine.rank.assert_called_once()
                    self.assertEqual(self.runtime.render_payload(result["renderToken"])["floor"], "2")
                self.dispatch("end_round")

    def test_difficult_known_ordinary_map_continues_current_f2_fit_without_rerank(self):
        self.configure("difficult", "side-neighborhood")
        self.assertTrue(self.start_capture()["success"])
        self.engine.rank.reset_mock()
        self.engine.qt_map_ui.observe = lambda *args: dict(mapOpen=True, floor="2")
        with patch("map_interface_observation.detect_selected_floor", return_value=dict(selectedFloor=2)):
            result = self.dispatch("capture_now")
        self.assertTrue(result["success"], result)
        self.engine.rank.assert_not_called()
        self.assertEqual((self.runtime.snapshot()["mapId"], self.runtime.snapshot()["floor"]), (self.a, "2"))
        self.assertEqual(self.runtime.render_payload(result["renderToken"])["floor"], "2")

    def test_manual_member_survives_same_front_result_and_f2_rejects_late_ack(self):
        for mode in ("nightmare", "difficult"):
            with self.subTest(mode=mode):
                self.configure(mode)
                self.assertTrue(self.start_capture()["success"])
                choice = self.choose(self.b)
                token = choice["pendingSiblingToken"]
                self.assertTrue(self.runtime.acknowledge_sibling(token, True)["success"])
                # A later front winner is group evidence, not permission to undo
                # the explicit member. Then no cold-entry gate applies to F2.
                self.assertTrue(self.dispatch("capture_now")["success"])
                self.assertEqual(self.runtime.snapshot()["mapId"], self.b)
                self.engine.rank.reset_mock()
                self.engine.qt_map_ui.observe = lambda *args: dict(mapOpen=True, floor="2")
                with patch("map_interface_observation.detect_selected_floor", return_value=dict(selectedFloor=2)):
                    result = self.dispatch("capture_now")
                self.assertTrue(result["success"], result)
                self.engine.rank.assert_not_called()
                self.assertFalse(self.runtime.acknowledge_sibling(token, True)["success"])
                self.assertTrue(self.runtime.snapshot()["manualGroupMemberHeld"])
                for layer in (self.runtime.render_payload(result["renderToken"]), self.runtime.reference_payload()):
                    self.assertEqual((layer["mapId"], layer["floor"]), (self.b, "2"))
                self.dispatch("end_round")
                self.assertFalse(self.runtime.snapshot()["manualGroupMemberHeld"])

    def test_late_front_rank_after_end_cannot_establish_registered_group(self):
        self.configure()
        self.assertTrue(self.dispatch("start_round")["success"])
        entered, release = threading.Event(), threading.Event()
        ranked = copy.deepcopy(self.engine.rank.return_value)
        def blocked(*args, **kwargs):
            entered.set()
            if not release.wait(2):
                raise AssertionError("bounded rank test did not release")
            return ranked
        self.engine.rank.side_effect = blocked
        output = []
        worker = threading.Thread(target=lambda: output.append(self.dispatch("capture_now")))
        worker.start()
        try:
            self.assertTrue(entered.wait(2))
            self.dispatch("end_round")
        finally:
            release.set()
            worker.join(2)
        self.assertFalse(worker.is_alive())
        self.assertFalse(output[0]["success"])
        self.assertFalse(self.runtime.snapshot()["groupLocked"])
        self.assertIsNone(self.runtime.reference_payload())


if __name__ == "__main__":
    unittest.main()
