"""Targeted contracts for unimported season hiding, S1 keep-alive, and pre-lock toasts."""
import copy
import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("QT_QUICK_BACKEND", "software")

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import numpy as np

import s1_candidate_purpose as purpose
from client_coordinator import LiveImages
from client_paths import RESOURCE_ROOT
from client_settings import SettingsService
from backend import Backend
import test_client_recognition as qml
import test_s1_native_candidate_preview as native
import test_s1_native_candidate_qml as candidate_qml
import test_s1_structural_conflict_notice as conflict


class SeasonVisibilityTests(unittest.TestCase):
    def test_unimported_seasons_are_hidden_until_available(self):
        (RESOURCE_ROOT / "work").mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=RESOURCE_ROOT / "work") as folder:
            root = Path(folder)
            (root / ".qt-lab-owner").write_text("qt-quick-isolated-v1")
            settings = SettingsService(root)
            self.addCleanup(settings.shutdown)
            # Standalone mode (None) keeps full catalog.
            self.assertTrue(len(settings.seasonOptions) >= 2)
            # Managed imported mode with zero packages hides all unimported seasons.
            settings.setAvailableContexts([])
            self.assertEqual(settings.seasonOptions, [])
            # Importing only S1 nightmare exposes only S1 (not S0).
            settings.setAvailableContexts(["s1:nightmare"])
            self.assertEqual([row["id"] for row in settings.seasonOptions], ["s1"])
            self.assertTrue(settings.seasonOptions[0]["capabilities"]["nightmare"]["available"])
            self.assertFalse(settings.seasonOptions[0]["capabilities"]["difficult"]["available"])
            # Backend filters seasonOptions identically when availability callback is wired.
            with patch.object(Backend, "refresh"):
                backend = Backend(root)
                self.addCleanup(backend.shutdown)
                available = set()
                backend.setContextAvailability(lambda season, mode: f"{season}:{mode}" in available)
                self.assertEqual(backend.seasonOptions, [])
                available.add("s1:nightmare")
                self.assertEqual([row["id"] for row in backend.seasonOptions], ["s1"])


class S1KeepAliveToleranceTests(unittest.TestCase):
    base_setUp = candidate_qml.CandidateQmlContracts.setUp

    def setUp(self):
        self.base_setUp()
        self.producer.registry.capability["observedFloors"] = (-1, -2, -3, -4)
        self.coordinator.images = LiveImages()
        self.coordinator._convert_client_geometry = lambda row: row.update(deviceScale=1.0)
        self.coordinator._sync_reference_payload = lambda row: None
        self.coordinator._pending_context = None
        self.first = self.producer.show()
        qml.APP.processEvents()
        self.session.refreshRuntime()
        qml.APP.processEvents()
        self.assertTrue(self.coordinator._aligned)

    def settle(self):
        import time
        until = time.monotonic() + 5
        while self.session.busy and time.monotonic() < until:
            qml.APP.processEvents()
            time.sleep(0.002)
        qml.APP.processEvents()
        self.assertFalse(self.session.busy)

    def test_single_transient_map_close_miss_retains_overlay_and_second_closes(self):
        self.producer.opened = False
        self.session.dispatch("observe_candidate_floor")
        self.settle()
        qml.APP.processEvents()
        # First transient miss must NOT drop the valid overlay or close the map.
        self.assertEqual(self.session.snapshot["lastReceipt"]["reason"], "map-not-observed")
        self.assertTrue(self.coordinator._aligned)
        self.assertIsNotNone(self.runtime.render_payload(self.first["token"]))
        # Recovering on the next frame resets miss counter and keeps overlay intact.
        self.producer.opened = True
        self.session.dispatch("observe_candidate_floor")
        self.settle()
        qml.APP.processEvents()
        self.assertEqual(self.session.snapshot["lastReceipt"]["reason"], "candidate-floor-current")
        self.assertTrue(self.coordinator._aligned)
        # Two consecutive misses confirm genuine close and cleanly withdraw.
        self.producer.opened = False
        self.session.dispatch("observe_candidate_floor")
        self.settle()
        self.assertTrue(self.coordinator._aligned)
        self.session.dispatch("observe_candidate_floor")
        self.settle()
        qml.APP.processEvents()
        self.assertEqual(self.session.snapshot["lastReceipt"]["reason"], "candidate-map-closed")
        self.assertFalse(self.coordinator._aligned)
        self.assertIsNone(self.runtime.render_payload(self.first["token"]))


class S1PreLockToastTests(unittest.TestCase):
    settle = conflict.StructuralConflictNotice.settle

    def setUp(self):
        self.enterContext(patch.object(
            native.NativeCandidateContracts, "candidate_purpose",
            purpose._NATIVE_S1_PRODUCTION_CANDIDATE,
        ))
        conflict.StructuralConflictNotice.setUp(self)
        self.release.set()
        self.result = lambda ids, floor: conflict.evidence(ids, floor)

    def test_prelock_pool_progress_and_provisional_preview_emit_info_toasts_without_claiming_lock(self):
        self.producer.show()
        self.settle()
        titles = [n[0] for n in self.notices]
        self.assertIn("正在核对地图", titles, self.notices)
        self.assertFalse(any("锁定" in title for title in titles), self.notices)
        # No duplicate toast for same remaining count.
        progress_notices = [n for n in self.notices if n[0] == "正在核对地图"]
        self.assertTrue(all(n[2] == "info" for n in progress_notices))


class AlignmentSpeedContractTests(unittest.TestCase):
    def test_generic_index_lazy_reads_only_npz_chains_rays_for_requested_floor(self):
        import io
        from types import SimpleNamespace
        from generic_structure_index import GenericStructureIndex

        buf = io.BytesIO()
        chains = np.array([[[0, 0], [10, 0], [10, 20]]], dtype=np.float32)
        rays = np.array([[[-1, 0], [0, 1]]], dtype=np.float32)
        np.savez_compressed(buf, chains=chains, rays=rays)
        raw_npz = buf.getvalue()

        read_paths = []
        fast_source = SimpleNamespace(
            entries={
                ("shared-structure", "m1", "-1"): SimpleNamespace(path="m1-f1-fast.npz"),
                ("shared-structure", "m1", "-2"): SimpleNamespace(path="m1-f2-fast.npz"),
            },
            view=SimpleNamespace(path=lambda rel: rel),
            reader=lambda path: (read_paths.append(path) or raw_npz),
        )
        provider_calls = []
        index = GenericStructureIndex.from_provider(
            ("m1",), ("-1", "-2"),
            lambda *a: provider_calls.append(a),
            fast_source=fast_source,
            lazy=True,
        )
        # Construction touches zero floors.
        self.assertEqual(read_paths, [])
        self.assertEqual(provider_calls, [])
        # Querying floor -1 loads ONLY floor -1 from npz without touching clean_mask provider or floor -2.
        self.assertTrue(index.has_floor("-1"))
        self.assertEqual(read_paths, ["m1-f1-fast.npz"])
        self.assertEqual(provider_calls, [])

    def test_pose_prior_fallback_uses_scale_hint_when_chain_seeds_empty(self):
        import fast_structure_alignment as fast
        from types import SimpleNamespace

        frame = np.zeros((40, 40, 3), dtype=np.uint8)
        context = {"observed": np.ones((40, 40), dtype=np.uint8)}
        prepared = SimpleNamespace(pitch=0.0, chains=np.empty((0, 3, 2), np.float32),
                                   rays=np.empty((0, 2, 2), np.float32), shape=(40, 40))
        grid_calls = []

        def fake_grid(coarse, prep, scale, seed, cancelled):
            grid_calls.append((scale, seed))
            return []

        with patch("single_map_structure.current_part", return_value=context["observed"]), \
             patch.object(fast, "sample_edges", return_value=np.zeros((50, 2), np.float32)), \
             patch.object(fast, "_chain_seeds", return_value=[]), \
             patch.object(fast, "_grid", side_effect=fake_grid):
            fast.propose(frame, None, prepared, prior=None, context=context, scale_hint=0.42)
        self.assertEqual(len(grid_calls), 1)
        self.assertAlmostEqual(grid_calls[0][0], 0.42, places=5)
        self.assertIsNone(grid_calls[0][1])

    def test_known_scale_grid_runs_before_chain_seeds_and_skips_chains_on_strong_hit(self):
        import fast_structure_alignment as fast
        from types import SimpleNamespace

        frame = np.zeros((40, 40, 3), dtype=np.uint8)
        context = {"observed": np.ones((40, 40), dtype=np.uint8)}
        prepared = SimpleNamespace(pitch=0.0, chains=np.empty((0, 3, 2), np.float32),
                                   rays=np.empty((0, 2, 2), np.float32), shape=(40, 40))

        def strong_grid(coarse, prep, scale, seed, cancelled):
            return [(0.75, scale, np.array([10.0, 20.0], dtype=np.float64))]

        with patch("single_map_structure.current_part", return_value=context["observed"]), \
             patch.object(fast, "sample_edges", return_value=np.zeros((50, 2), np.float32)), \
             patch.object(fast, "_grid", side_effect=strong_grid), \
             patch.object(fast, "refine", side_effect=lambda pts, prep, s, t, **kw: (0.75, s, t)), \
             patch.object(fast, "_chain_seeds", side_effect=AssertionError("chains should be skipped")):
            proposals, _, diag = fast.propose(frame, None, prepared, prior=None, context=context, scale_hint=0.55)
        self.assertEqual(len(proposals), 1)
        self.assertEqual(diag["reason"], "fast-proposal")
        self.assertAlmostEqual(proposals[0][1], 0.55, places=4)

    def test_alignment_host_runs_s1_steady_before_current_geometry_and_falls_back(self):
        from types import SimpleNamespace
        from client_live_runtime import _AlignmentHost

        frame = np.zeros((64, 64, 3), dtype=np.uint8)
        engine = SimpleNamespace(
            pack=SimpleNamespace(mode="nightmare"),
            qt_adapters=None,
            qt_season="s1",
            qt_shared_provider=lambda *a: None,
        )
        expected = dict(success=True, reason="idvb0059-steady", strategy="idvb0059-steady")

        # 1) Steady hit skips current_geometry completely.
        steady_calls = []
        def steady_hit(host, f, mid, fl, cancel):
            steady_calls.append((mid, fl))
            return expected

        host = _AlignmentHost(engine, settings={"idvb0059Enabled": True}, s1_steady=steady_hit)
        with patch("single_map_structure.current_geometry", side_effect=AssertionError("must not extract geometry on steady hit")):
            res = host._fit_owned(frame, "m1", "-1", None, observation_bounds=(0, 0, 64, 64))
        self.assertIs(res, expected)
        self.assertEqual(steady_calls, [("m1", "-1")])

        # 2) Steady rejection cleanly falls back to current_geometry.
        geom_called = []
        host_miss = _AlignmentHost(engine, settings={"idvb0059Enabled": True},
                                   s1_steady=lambda *a: None)
        with patch("single_map_structure.current_geometry",
                   side_effect=lambda *a, **kw: (geom_called.append(True) or None)), \
             patch("nightmare_candidate.visible_map_components", return_value=[]):
            res_miss = host_miss._fit_owned(frame, "m1", "-1", None, observation_bounds=(0, 0, 64, 64))
        self.assertTrue(geom_called)
        self.assertFalse(res_miss["success"])

        # 3) Setting idvb0059Enabled=False bypasses s1_steady completely.
        host_off = _AlignmentHost(engine, settings={"idvb0059Enabled": False},
                                  s1_steady=lambda *a: (_ for _ in ()).throw(AssertionError("steady disabled")))
        with patch("single_map_structure.current_geometry", return_value=None), \
             patch("nightmare_candidate.visible_map_components", return_value=[]):
            res_off = host_off._fit_owned(frame, "m1", "-1", None, observation_bounds=(0, 0, 64, 64))
        self.assertFalse(res_off["success"])

    def test_prelock_preferred_reopen_decouples_recognition_and_wires_steady_probe(self):
        from s1_structure_selection import StructureCandidateScanner
        harness = native.NativeCandidateContracts()
        harness.setUp()
        self.addCleanup(harness.doCleanups)

        steady_seen = []
        def run(frame, mid, floor, cancelled, **kwargs):
            return copy.deepcopy(harness.fit)

        def scan_stub(frame, **kwargs):
            steady_seen.append(callable(getattr(harness.registry.engine, "_qt_s1_steady", None)))
            return StructureCandidateScanner(harness.registry.catalog, run).scan(frame, **kwargs)

        harness.registry.engine.qt_s1_structure_scan = scan_stub
        harness.runtime.set_event_callback(
            lambda row: harness.runtime.acknowledge_render_material(row["token"])
            if row["event"] == "render-ready" else None
        )
        # First opening populates preference and steady scale cache.
        first = harness.show()
        self.assertTrue(first["token"])
        self.assertTrue(harness.runtime._s1_steady_scales)
        self.assertIsNone(getattr(harness.registry.engine, "_qt_s1_steady", None))

        # Second opening (still unlocked) skips candidate_order and attaches _qt_s1_steady.
        harness.runtime.dispatch("close_map")
        harness.capture.frame[0, 0, 0] += 1
        with patch("s1_stream_priority.candidate_order",
                   side_effect=AssertionError("candidate_order must be skipped when preferred is set")):
            reopened = harness.runtime.dispatch("capture_now")
        self.assertTrue(reopened["success"], reopened)
        self.assertEqual(steady_seen, [True, True])
        self.assertIsNone(getattr(harness.registry.engine, "_qt_s1_steady", None))

        # forceCatalogue=True disables steady probe and invokes candidate_order.
        harness.runtime.dispatch("close_map")
        harness.capture.frame[0, 0, 0] += 1
        order_called = []
        import s1_stream_priority as priority
        orig_order = priority.candidate_order
        with patch("s1_stream_priority.candidate_order",
                   side_effect=lambda *a, **kw: (order_called.append(True) or orig_order(*a, **kw))):
            forced = harness.runtime.dispatch("capture_now", {"forceCatalogue": True})
        self.assertTrue(forced["success"], forced)
        self.assertTrue(order_called)
        self.assertEqual(steady_seen[-1], False)

        # end_round clears _s1_steady_scales.
        harness.runtime.dispatch("end_round")
        self.assertEqual(harness.runtime._s1_steady_scales, {})


if __name__ == "__main__":
    unittest.main()
