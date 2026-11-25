"""Unit tests for dual-sided wall+interior elimination, scale tightening, and S0 universal recognition."""
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import cv2
import numpy as np

from structure_observation import (
    explored_interior_mask,
    sample_interior_points,
    has_interior_contradiction,
)
from universal_structure_eliminator import UniversalStructureEliminator
from s1_structure_selection import StructureCatalog
import s1_candidate_purpose as purpose


def _make_rect_map(shape=(260, 260), rects=()):
    mask = np.zeros(shape, dtype=np.uint8)
    for x0, y0, x1, y1 in rects:
        mask[y0:y1, x0:x1] = 255
    return mask


class ExploredInteriorObservationTests(unittest.TestCase):
    def test_explored_interior_mask_erodes_walls_crop_edges_and_anchor(self):
        query = np.zeros((120, 120), dtype=np.uint8)
        query[20:100, 20:100] = 1
        interior = explored_interior_mask(query, (0, 0, 120, 120), margin_px=7, anchor={"center": [60, 60]})
        # Center around anchor must be masked out.
        self.assertEqual(int(interior[60, 60]), 0)
        # Wall boundary pixels must be eroded away.
        self.assertEqual(int(interior[22, 60]), 0)
        # Deep interior away from anchor remains confirmed walkable.
        self.assertEqual(int(interior[35, 35]), 1)
        pts = sample_interior_points(interior, limit=200)
        self.assertGreater(len(pts), 50)

    def test_has_interior_contradiction_requires_coherent_cluster(self):
        # Scattered isolated points must NOT trigger contradiction.
        scattered = np.array([[i * 25, (i % 3) * 25] for i in range(10)], dtype=np.float64)
        self.assertFalse(has_interior_contradiction(scattered, min_points=10, min_span_px=14))
        # Coherent cluster inside wall/void triggers contradiction.
        cluster = np.array([[40 + (i % 5) * 4, 40 + (i // 5) * 4] for i in range(15)], dtype=np.float64)
        self.assertTrue(has_interior_contradiction(cluster, min_points=10, min_span_px=14))


class UniversalStructureEliminatorTests(unittest.TestCase):
    def _build_two_map_engine(self):
        # Map A: main room (40..160, 40..140) + right wing (160..230, 65..115)
        mask_a = _make_rect_map((260, 260), [(40, 40, 160, 140), (160, 65, 230, 115)])
        # Map B: identical main room (40..160, 40..140) + solid wall where Map A has right wing,
        # plus a bottom wing (65..115, 140..220) instead.
        mask_b = _make_rect_map((260, 260), [(40, 40, 160, 140), (65, 140, 115, 220)])
        refs = {
            "map_a": SimpleNamespace(clean_mask=mask_a, metadata={}),
            "map_b": SimpleNamespace(clean_mask=mask_b, metadata={}),
        }
        pack = SimpleNamespace(
            mode="nightmare",
            floors=("1",),
            by_id={"map_a": SimpleNamespace(name="Map A"), "map_b": SimpleNamespace(name="Map B")},
        )
        engine = SimpleNamespace(
            pack=pack,
            qt_season="s0",
            qt_shared_provider=lambda profile, mid, floor: refs.get(mid),
            qt_fast_structure_source=None,
            qt_resource_read_scope=None,
            _qt_grid_runtime=None,
        )
        catalog = StructureCatalog("s0", "nightmare", "test-s0-res-key", ("map_a", "map_b"), ("1",))
        return engine, catalog, mask_a, mask_b

    def test_dual_sided_eliminator_eliminates_conflicting_map_with_interior_and_wall(self):
        from fast_structure_alignment import prepare as prepare_fast

        engine, catalog, mask_a, mask_b = self._build_two_map_engine()
        eliminator = UniversalStructureEliminator(engine, catalog)

        # Render Map A at scale=1.0, tx=20, ty=20 on a 320x320 client frame.
        frame = np.zeros((320, 320, 3), dtype=np.uint8)
        query = np.zeros((320, 320), dtype=np.uint8)
        query[20:280, 20:280] = (mask_a > 0).astype(np.uint8)
        edge = (cv2.morphologyEx(query, cv2.MORPH_GRADIENT, np.ones((3, 3), np.uint8)) > 0)
        ctx = {
            "query": query,
            "observationBounds": (40, 40, 260, 200),
            "observed": edge,
        }

        def fake_poses(frame_in, context_in, prepared_in, coarse, points, *, scale_hint, cancelled):
            return [(0.9, 1.0, np.array([20.0, 20.0], dtype=np.float64))]

        with patch("single_map_structure.current_geometry", return_value=ctx), \
             patch("single_map_structure.current_part", return_value=edge), \
             patch("structure_observation.observed_wall_fragment_edges", return_value=edge), \
             patch("fast_structure_alignment.load_prepared",
                   side_effect=lambda ref, **kw: prepare_fast(ref.clean_mask)), \
             patch.object(eliminator, "_candidate_poses", side_effect=fake_poses):
            report = eliminator.prepare(frame, ("map_a", "map_b"), "1", cancelled=lambda: False, scale_hint=1.0)

        self.assertTrue(report["coordinateVerified"], report)
        self.assertEqual(report["contradictedMapIds"], ["map_b"])
        self.assertEqual(report["unknownMapIds"], ["map_a"])
        checks_by_id = {c["mapId"]: c for c in report["candidateChecks"]}
        self.assertFalse(checks_by_id["map_a"]["explicitContradiction"])
        self.assertTrue(checks_by_id["map_b"]["explicitContradiction"])
        self.assertIn("interior", checks_by_id["map_b"]["contradictionKind"])

    def test_full_pool_zero_compatible_stays_unknown_while_narrowed_pool_recovers(self):
        from fast_structure_alignment import prepare as prepare_fast

        engine, catalog, mask_a, _ = self._build_two_map_engine()
        eliminator = UniversalStructureEliminator(engine, catalog)
        frame = np.zeros((320, 320, 3), dtype=np.uint8)
        query = np.zeros((320, 320), dtype=np.uint8)
        query[60:240, 60:240] = 1
        edge = (cv2.morphologyEx(query, cv2.MORPH_GRADIENT, np.ones((3, 3), np.uint8)) > 0)
        ctx = {"query": query, "observationBounds": (60, 60, 240, 240), "observed": edge}

        with patch("single_map_structure.current_geometry", return_value=ctx), \
             patch("single_map_structure.current_part", return_value=edge), \
             patch("structure_observation.observed_wall_fragment_edges", return_value=edge), \
             patch("fast_structure_alignment.load_prepared",
                   side_effect=lambda ref, **kw: prepare_fast(ref.clean_mask)), \
             patch.object(eliminator, "_candidate_poses", return_value=[]):
            # Full pool with 0 compatible must stay unknown (never wipe out all candidates).
            full_report = eliminator.prepare(frame, ("map_a", "map_b"), "1", cancelled=lambda: False)
            self.assertFalse(full_report["coordinateVerified"])
            self.assertEqual(full_report["reason"], "universal-no-compatible-candidate")
            # Narrowed pool with 0 compatible signals contradiction recovery.
            narrow_report = eliminator.prepare(frame, ("map_b",), "1", cancelled=lambda: False)
            self.assertFalse(narrow_report["coordinateVerified"])
            self.assertEqual(narrow_report["reason"], "coordinate-or-catalogue-conflict")


class S0UniversalRuntimeActivationTests(unittest.TestCase):
    def test_s0_activates_universal_pool_only_when_engine_exposes_universal_hooks_and_auto_mode(self):
        from client_live_runtime import LiveRuntime
        from test_s1_manual_floor_runtime import CurrentFixtureCapture

        s0_pack = SimpleNamespace(
            mode="nightmare",
            floors=("1",),
            by_id={"m1": SimpleNamespace(name="M1")},
        )
        universal_engine = SimpleNamespace(
            pack=s0_pack,
            identity_floors=("1",),
            qt_season="s0",
            qt_s1_structure_scan=lambda *a, **kw: {},
            qt_s1_private_coordinate_prepare=lambda *a, **kw: {},
        )
        legacy_engine = SimpleNamespace(
            pack=s0_pack,
            identity_floors=("1",),
            qt_season="s0",
        )

        # 1) Universal S0 engine activates pool lock in auto mode, disables in manual mode.
        rt = LiveRuntime(
            engine_factories={"nightmare": lambda: universal_engine},
            capture_provider=CurrentFixtureCapture(),
            evidence_kind="live-client-capture",
            s1_candidate_purpose=purpose._NATIVE_S1_PRODUCTION_CANDIDATE,
        )
        self.addCleanup(rt.shutdown)
        rt._season = "s0"
        rt._engine = universal_engine
        rt.configure_settings({"mapSelectionMode": "auto", "manualMapLock": False})
        self.assertTrue(rt._uses_universal_structure_recognition())
        self.assertTrue(rt._s1_provisional_enabled())
        self.assertTrue(rt._identity_fields()["usesCandidatePoolLock"])
        # Switching to manual mode disables universal pool so manual picker works normally.
        rt.configure_settings({"mapSelectionMode": "manual", "manualMapLock": False})
        self.assertFalse(rt._uses_universal_structure_recognition())
        self.assertFalse(rt._s1_provisional_enabled())

        # 2) Legacy mock S0 engine without universal hooks keeps legacy S0 behavior.
        rt._engine = legacy_engine
        rt.configure_settings({"mapSelectionMode": "auto", "manualMapLock": False})
        self.assertFalse(rt._uses_universal_structure_recognition())
        self.assertFalse(rt._s1_provisional_enabled())


if __name__ == "__main__":
    unittest.main()
