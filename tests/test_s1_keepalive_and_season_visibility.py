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


if __name__ == "__main__":
    unittest.main()
