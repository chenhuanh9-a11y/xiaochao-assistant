"""Deterministic boundaries; synthetic fixtures are not game acceptance evidence."""
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
from types import SimpleNamespace
import unittest

from PIL import Image

from client_engine import EngineAdapter
from alignment_core_v2 import AlignmentCandidate, RoundPose


class EngineAdapterTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.frame = self.root / "frame.png"
        Image.new("RGB", (16, 12), "white").save(self.frame)
        self.context = dict(mode="nightmare", profileId="fixture-profile", mapId="fixture-map",
                            floor="1", frameId="frame-A", roundId=12, generation=8)

    def adapter(self, **kw):
        return EngineAdapter(allowed_read_root=self.root, **kw)

    def request(self, adapter, action="rank_saved_frame", **kw):
        return adapter.dispatch(action, dict(self.context, path=str(self.frame), **kw))

    def test_import_and_construction_have_no_live_or_tk_dependency(self):
        code = ("import sys; from client_engine import EngineAdapter; "
                "a=EngineAdapter(); assert a.snapshot()['liveEngineAvailable'] is False; "
                "assert not ({'tkinter','identityv_daily_assistant','identityv_live_mvp',"
                "'map_identity_engine','cv2'} & set(sys.modules))")
        subprocess.run([sys.executable, "-c", code], cwd=Path(__file__).parent,
                       check=True, capture_output=True, text=True)

    def test_default_is_attached_but_explicitly_unavailable_for_live_and_rank(self):
        a = self.adapter()
        self.assertTrue(a.snapshot()["engineConnected"])
        self.assertNotIn("start_round", a.snapshot()["availableCommands"])
        self.assertIn("load_saved_result", a.snapshot()["availableCommands"])
        self.assertFalse(self.request(a)["success"])
        receipt = a.dispatch("start_round", self.context)
        self.assertTrue(receipt["unavailable"])
        self.assertFalse(receipt["authorization"]["live"])

    def production_fixture(self, mode):
        """Real MapIdentityEngine.rank; injected empty resource observations.

        Tests the genuine rank contract and rejection path without distributing
        source assets. This does not validate image recognition accuracy.
        """
        from map_identity_engine import MapIdentityEngine
        engine = MapIdentityEngine.__new__(MapIdentityEngine)
        engine.pack = SimpleNamespace(mode=mode, maps=[SimpleNamespace(id="fixture-map", name="Fixture")])
        engine.identity_floors = ("1", "2", "B1")
        engine.entry_identity = SimpleNamespace(rank=lambda *args, **kw:
            ([], dict(complete=True, reason="no-fixture-observation", elapsedMs=0)))
        engine.entry_shared_reference_provider = None
        engine.entry_advisory = None
        engine.side_entry_identity = None
        engine.side_entry_error = "fixture-no-side-resources"
        engine.native_similarity_index = None
        engine._rank_source_structure_candidates = lambda *args, **kw: dict(candidateCount=0)
        return engine

    def test_genuine_shared_rank_for_both_modes_is_diagnostic_only(self):
        created = []
        def factory(mode):
            created.append((mode, threading.get_ident()))
            return self.production_fixture(mode)
        a = self.adapter(engine_factories={mode: lambda mode=mode: factory(mode) for mode in a_modes()})
        self.assertEqual(created, [])
        for mode in a_modes():
            self.context["mode"] = mode
            result = self.request(a, inputKind="synthetic")
            self.assertTrue(result["success"], result)
            self.assertEqual(result["cycleId"], 12)
            self.assertEqual(result["generation"], 8)
            self.assertEqual(result["frameId"], "frame-A")
            self.assertEqual(result["mode"], mode)
            self.assertEqual(result["evidenceKind"], "saved-synthetic-frame")
            self.assertFalse(result["candidates"][0]["identityHardGatePassed"])
            self.assertFalse(result["identityOrPoseAuthorized"])
            self.assertFalse(a.snapshot()["core"]["displayAuthorized"])
            self.assertEqual(result["authorization"], dict(live=False, scope="saved-frame-diagnostic"))
            json.dumps(result, allow_nan=False)
        self.assertEqual([row[0] for row in created], list(a_modes()))

    def test_cancel_before_dispatch_does_not_construct_engine(self):
        called = []
        a = self.adapter(engine_factories={"nightmare": lambda: called.append(True)})
        event = threading.Event(); event.set()
        result = a.dispatch("rank_saved_frame", self.context, event)
        self.assertTrue(result["cancelled"])
        self.assertEqual(called, [])

    def test_cancel_or_mode_change_during_rank_discards_result(self):
        for cancel in (True, False):
            entered, release, event = threading.Event(), threading.Event(), threading.Event()
            engine = self.production_fixture("nightmare")
            def rank(*args, **kwargs):
                entered.set()
                self.assertTrue(release.wait(5))
                return []
            engine.rank = rank
            a = self.adapter(engine_factories={"nightmare": lambda: engine})
            output = []
            worker = threading.Thread(target=lambda: output.append(a.dispatch(
                "rank_saved_frame", dict(self.context, path=str(self.frame)), event)))
            worker.start()
            self.assertTrue(entered.wait(5))
            if cancel:
                event.set()
            else:
                a.dispatch("set_game_mode", dict(mode="difficult"))
            release.set(); worker.join(5)
            self.assertFalse(worker.is_alive())
            self.assertFalse(output[0]["success"])
            self.assertTrue(output[0]["cancelled" if cancel else "stale"])
            self.assertNotIn("candidates", output[0])

    def test_wrong_mode_floor_checksum_and_outside_root_fail_closed(self):
        a = self.adapter(engine_factories={"nightmare": lambda: self.production_fixture("difficult")})
        self.assertIn("pack mode", self.request(a)["reason"])
        engine = self.production_fixture("nightmare"); engine.identity_floors = ("1",)
        a = self.adapter(engine_factories={"nightmare": lambda: engine})
        self.assertIn("checksum", self.request(a, sha256="0" * 64)["reason"])
        self.context["floor"] = "B1"
        self.assertTrue(self.request(a)["unavailable"])
        result = a.dispatch("load_saved_result", dict(path=str(self.root.parent / "other.json")))
        self.assertIn("allowed_read_root", result["reason"])

    def test_exact_replay_schema_preserves_kind_but_never_historical_authority(self):
        def record(path):
            return dict(file=path.name, sha256=hashlib.sha256(path.read_bytes()).hexdigest(), size=[16, 12])
        path = self.root / "replay.json"
        data = dict(schema="xingtu-qt-render-replay-v1", mapId="historical-map", profileId="historical-profile",
                    floor="2", matrix=[[1, 0, 7], [0, 1, 9], [0, 0, 1]],
                    evidenceKind="production-result-on-synthetic-frame", recordedCurrentFrameVerified=True,
                    liveAuthorization=True, frame=record(self.frame), route=record(self.frame))
        path.write_text(json.dumps(data), encoding="utf-8")
        a = self.adapter()
        result = a.execute("saved_result", dict(self.context, path=str(path)), threading.Event())
        self.assertTrue(result["success"], result)
        self.assertEqual(result["floor"], "1")
        self.assertEqual(result["replay"]["floor"], "2")
        self.assertEqual(result["mapId"], "fixture-map")
        self.assertEqual(result["replay"]["mapId"], "historical-map")
        self.assertEqual(result["replay"]["evidenceKind"], data["evidenceKind"])
        self.assertFalse(result["replay"]["liveAuthorization"])
        self.assertFalse(result["liveAuthorization"])
        self.assertIsNone(result["recordedMode"])
        self.assertFalse(a.snapshot()["core"]["displayAuthorized"])
        self.frame.write_bytes(b"changed")
        self.assertFalse(a.execute("saved_result", dict(path=str(path)))["success"])

    def test_round_pose_floor_contract_and_core_reset_are_genuine(self):
        pose = RoundPose("fixture-map", "fixture-profile", "1", [[1,0,40],[0,1,60],[0,0,1]],
                         floor_relations={"2": [[1,0,8],[0,1,-3],[0,0,1]]}, reliable=True)
        path = self.root / "pose.json"
        path.write_text(json.dumps(pose.snapshot()), encoding="utf-8")
        a = self.adapter(clock=lambda: 1)
        self.context["floor"] = "2"
        result = a.dispatch("load_saved_pose", dict(self.context, path=str(path)))
        self.assertEqual(result["pose"]["matrix"], [[1,0,48],[0,1,57],[0,0,1]])
        self.assertFalse(a.snapshot()["core"]["displayAuthorized"])
        core = a._core
        core.begin_observation(cycle_id=12, map_id="fixture-map", floor="1", frame_generation=8)
        candidate = AlignmentCandidate("fixture-map", "1", pose.root_matrix, "synthetic-contract-test", 8,
                                       confidence=1, anchor_count=4, current_frame_verified=True,
                                       residual_verified=True, global_residual_verified=True)
        decision = core.adjudicate(candidate)
        self.assertTrue(decision.accepted, decision.reason)
        token = core.authorize(decision)
        self.assertTrue(core.commit(token))
        self.assertTrue(core.snapshot()["displayAuthorized"])
        a.dispatch("reset_round")
        self.assertEqual(core.snapshot()["poses"], {})
        self.assertFalse(core.snapshot()["displayAuthorized"])


def a_modes():
    return ("nightmare", "difficult")


if __name__ == "__main__":
    unittest.main()
