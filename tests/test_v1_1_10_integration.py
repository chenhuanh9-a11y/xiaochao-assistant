"""Targeted tests for v1.1.10 additions:
1. S0 numeric map display renaming (internal map IDs preserved).
2. ImportedStructureRouteLibrary backed by an imported structure package
   (rendered neutralDisplay preference, clean_mask fallback, route drawing,
   render_layer, package_maps upload, import_maps download).
3. ClientProviders.native_display_layer using bundled neutralDisplay rendered PNG
   and falling back cleanly to structure_display_layer.
4. RoutePlazaService.refreshAvailability() unlocking downloads immediately when
   structure availability changes.
"""
from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest

import cv2
import numpy as np

from map_display_names import format_map_display_name, s0_numeric_index
from imported_structure_route_library import ImportedStructureRouteLibrary
from route_scopes import SCOPES
from shared_structure_reference import SharedStructureReference


def _png_bytes(rgba: np.ndarray) -> bytes:
    bgra = cv2.cvtColor(rgba, cv2.COLOR_RGBA2BGRA)
    ok, buf = cv2.imencode(".png", bgra)
    assert ok
    return buf.tobytes()


class S0NumericDisplayNamesTests(unittest.TestCase):
    def test_standard_and_legacy_s0_ids_format_as_numbers(self):
        self.assertEqual(format_map_display_name("nightmare-01", "左中右 / 右 I / 中└┐", season="s0"), "01号图")
        self.assertEqual(format_map_display_name("nightmare-09", "右中 / 中十 / 雷同图", season="s0"), "09号图")
        self.assertEqual(format_map_display_name("nightmare-21", "左右 / 右┛┏", season="s0"), "21号图")
        self.assertEqual(
            format_map_display_name("33956dfb-8e0d-4627-a26c-e1904d687084", "左中右 / 右┛ / 雷同图", season="s0"),
            "22号图",
        )
        self.assertEqual(
            format_map_display_name("4f5d6ac0-b1ae-4338-8d6e-b67cd1e3af51", "左中右 / 右 红房间", season="s0"),
            "23号图",
        )
        self.assertEqual(
            format_map_display_name("37d291a1-529d-4a0e-8938-e4754e5f7a0d", "左中右 / 右S / 雷同图2", season="s0"),
            "24号图",
        )
        self.assertEqual(format_map_display_name("nightmare-layout-104026", "新增地图 104026", season="s0"), "25号图")
        self.assertEqual(format_map_display_name("nightmare-layout-104027", "新增地图 104027", season="s0"), "26号图")
        self.assertEqual(format_map_display_name("difficult-03", "困难旧名", season="s0"), "03号图")
        self.assertEqual(s0_numeric_index("nightmare-05"), 5)

    def test_s1_and_synthetic_fixture_names_are_preserved(self):
        self.assertEqual(format_map_display_name("107001", "107001", season="s1"), "107001")
        self.assertEqual(format_map_display_name("nightmare-01", "S1自定义名", season="s1"), "S1自定义名")
        self.assertEqual(format_map_display_name("custom-fixture-a", "测试地图A", season="s0"), "测试地图A")


class RenderedNeutralDisplayTests(unittest.TestCase):
    def test_native_display_layer_prefers_bundled_rendered_png_and_falls_back(self):
        from client_providers import SharedReferenceProvider

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            rendered_rel = "assets/rendered/m1-1.png"
            rendered_path = root / rendered_rel
            rendered_path.parent.mkdir(parents=True, exist_ok=True)
            rgba = np.zeros((32, 48, 4), dtype=np.uint8)
            rgba[4:28, 6:42] = (30, 140, 220, 255)
            rendered_path.write_bytes(_png_bytes(rgba))

            neutral_doc = {
                "layers": [
                    {"mapId": "107001", "floor": "-1", "resourcePath": rendered_rel},
                ]
            }

            class _FakeView:
                def path(self, rel):
                    return root / rel

            class _FakeReader:
                def __init__(self):
                    self.view = _FakeView()

                def json(self, rel):
                    assert rel == "neutral.json"
                    return neutral_doc

                def __call__(self, path):
                    return Path(path).read_bytes()

            bundle = SimpleNamespace(interfaces={"neutralDisplay": "neutral.json"}, view=_FakeView())
            ref = SimpleNamespace(
                path=root / "dummy.npz",
                resource_root=root,
                map_id="107001",
                floor="-1",
                clean_mask=np.ones((64, 96), dtype=np.uint8) * 255,
                shape=(64, 96),
                metadata={"profile": "shared-structure", "mapId": "107001", "floor": "-1"},
            )
            provider = SimpleNamespace()
            layer = SharedReferenceProvider._rendered_neutral_display(provider, ref, bundle, _FakeReader())
            self.assertIsNotNone(layer)
            self.assertEqual(layer["displayKind"], "shared-structure")
            self.assertEqual(layer["displayLabel"], "默认渲染地图")
            self.assertEqual(layer["pixels"].shape, (32, 48, 4))
            # Scale matrix maps 48x32 display -> 96x64 reference (2x in x and y).
            self.assertAlmostEqual(layer["matrixDisplayToReference"][0][0], 2.0, places=5)
            self.assertAlmostEqual(layer["matrixDisplayToReference"][1][1], 2.0, places=5)

            # Missing floor returns None so caller falls back to structure_display_layer.
            ref_missing = SimpleNamespace(
                path=root / "dummy.npz",
                resource_root=root,
                map_id="107001",
                floor="-2",
                clean_mask=np.ones((64, 96), dtype=np.uint8) * 255,
                shape=(64, 96),
                metadata={"profile": "shared-structure", "mapId": "107001", "floor": "-2"},
            )
            self.assertIsNone(SharedReferenceProvider._rendered_neutral_display(provider, ref_missing, bundle, _FakeReader()))


class ImportedStructureRouteLibraryTests(unittest.TestCase):
    def test_materialize_draw_package_and_import_roundtrip(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            cache = root / "cache"
            pkg = root / "pkg"
            (pkg / "rendered").mkdir(parents=True, exist_ok=True)
            (pkg / "masks").mkdir(parents=True, exist_ok=True)

            # Floor -1 has a bundled rendered PNG; Floor -2 falls back to clean mask PNG.
            rendered = np.zeros((40, 60, 4), dtype=np.uint8)
            rendered[5:35, 5:55] = (45, 110, 180, 255)
            (pkg / "rendered" / "107001_-1.png").write_bytes(_png_bytes(rendered))

            mask = np.zeros((40, 60), dtype=np.uint8)
            mask[8:32, 8:52] = 255
            ok, mbuf = cv2.imencode(".png", mask)
            assert ok
            (pkg / "masks" / "107001_-2.png").write_bytes(mbuf.tobytes())

            # Test _materialize_display directly + full library contract via a lightweight subclass.
            class _FakeView:
                def path(self, rel):
                    return pkg / rel

            def fake_reader(path):
                return Path(path).read_bytes()

            lib = ImportedStructureRouteLibrary(None, SCOPES["season-s1-nightmare"], cache)
            self.assertFalse(lib.available)
            self.assertIn("导入", lib.error)

            out_dir = cache / "mat"
            out_dir.mkdir(parents=True, exist_ok=True)
            p1, sz1, m1 = ImportedStructureRouteLibrary._materialize_display(
                _FakeView(), fake_reader, lambda *a, **k: None, out_dir, "107001", "-1",
                display_rel="rendered/107001_-1.png", typed_row=None, mask_rel="masks/107001_-2.png",
            )
            p2, sz2, m2 = ImportedStructureRouteLibrary._materialize_display(
                _FakeView(), fake_reader, lambda *a, **k: None, out_dir, "107001", "-2",
                display_rel=None, typed_row=None, mask_rel="masks/107001_-2.png",
            )
            self.assertEqual(sz1, (60, 40))
            self.assertEqual(sz2, (60, 40))
            self.assertTrue(p1.is_file() and p2.is_file() and m1.is_file() and m2.is_file())

            # Populate library maps and verify pick / walkable A* / insertion_index / render_layer / package_maps / import_maps.
            lib._maps = {
                "107001": dict(
                    name="107001",
                    floors={
                        "-1": dict(path=p1, size=sz1, mask_rel="masks/107001_-2.png", mask_cache=m1, display_rel="rendered/107001_-1.png"),
                        "-2": dict(path=p2, size=sz2, mask_rel="masks/107001_-2.png", mask_cache=m2, display_rel=None),
                    },
                )
            }
            lib.version = "pkg-s1:1.0:abcdef123456"
            lib.error = ""
            self.assertTrue(lib.available)
            self.assertTrue(lib.manifest_digest())
            self.assertEqual(lib.maps(), [("107001", "107001")])

            graph = lib.graph("107001")
            picked = graph.pick("-1", 12.0, 15.0)
            self.assertIsInstance(picked, dict)
            self.assertEqual(picked["floor"], "-1")
            self.assertEqual(picked["kind"], "via")

            pts = [
                {"floor": "-1", "x": 12.0, "y": 15.0, "color": "#ff3b30", "start": False},
                {"floor": "-1", "x": 45.0, "y": 25.0, "color": "#ff3b30", "start": False},
            ]
            from custom_maps import insertion_index
            segs = graph.plan(pts)
            self.assertEqual(len(segs), 1)
            idx = insertion_index(pts, segs, graph, graph.pick("-1", 28.0, 20.0))
            self.assertEqual(idx, 1)

            ref = SimpleNamespace(
                path=root / "ref.npz",
                resource_root=root,
                map_id="107001",
                floor="-1",
                clean_mask=np.ones((40, 60), dtype=np.uint8) * 255,
                shape=(40, 60),
                metadata={"profile": "shared-structure", "mapId": "107001", "floor": "-1"},
            )
            rendered_overlay = lib.render_layer("107001", "-1", ref, pts, point_numbers=True)
            self.assertEqual(rendered_overlay.shape, (40, 60, 4))

            packed = lib.package_maps({"maps": {"107001": {"points": pts}}})
            self.assertEqual(len(packed), 1)
            self.assertEqual(packed[0]["mapEntryId"], "107001")
            manifest = {
                "routeCatalogId": SCOPES["season-s1-nightmare"].id,
                "scenario": SCOPES["season-s1-nightmare"].scenario,
                "mode": SCOPES["season-s1-nightmare"].mode,
                "maps": packed,
            }
            imported, report = lib.import_maps(manifest)
            self.assertIn("107001", imported)
            self.assertEqual(len(imported["107001"]["points"]), 2)
            self.assertEqual(report["maps"], 1)

            # Android-format cell-index points (e.g. cell, color, floor='b1', kind, roomId) must also import cleanly.
            android_manifest = {
                "routeCatalogId": SCOPES["season-s1-nightmare"].id,
                "scenario": SCOPES["season-s1-nightmare"].scenario,
                "mode": SCOPES["season-s1-nightmare"].mode,
                "maps": [
                    {
                        "mapEntryId": "107001",
                        "routePlan": {
                            "sourceMapId": "107001",
                            "points": [
                                {"cell": 65, "color": "#ff3b30", "floor": "b1", "kind": "room", "roomId": "r1"},
                                {"cell": [12, 10], "color": "#ff3b30", "floor": "b1", "kind": "via", "roomId": None},
                            ],
                        },
                    }
                ],
            }
            android_imported, android_report = lib.import_maps(android_manifest)
            self.assertIn("107001", android_imported)
            self.assertEqual(len(android_imported["107001"]["points"]), 2)
            self.assertEqual(android_imported["107001"]["points"][0]["floor"], "-1")
            self.assertEqual(android_report["droppedPoints"], 0)

    def test_xingtu_map_and_legacy_base_suffixes_both_accepted(self):
        from resource_package_types import BASE_SUFFIX, ROUTE_SUFFIX, PackageTypeError, validate_package_filename
        base_comps = [{"type": "recognition-base"}, {"type": "controls-profile"}]
        route_comps = [{"type": "display"}]
        self.assertEqual(validate_package_filename("s1.xingtu-map", base_comps), BASE_SUFFIX)
        self.assertEqual(validate_package_filename("s1.xingtu-base", base_comps), BASE_SUFFIX)
        self.assertEqual(validate_package_filename("author.xingtu-route", route_comps), ROUTE_SUFFIX)
        with self.assertRaises(PackageTypeError):
            validate_package_filename("author.xingtu-map", route_comps)

    def test_walkable_astar_routes_around_wall_obstacle(self):
        from imported_structure_route_library import _WalkableFloor
        with tempfile.TemporaryDirectory() as tmp:
            mask_path = Path(tmp) / "u_mask.png"
            # U-shaped corridor: vertical wall in the middle (x=40..56, y=0..60), open at bottom (y=64..92).
            mask = np.zeros((100, 100), dtype=np.uint8)
            mask[8:92, 8:92] = 255
            mask[8:64, 40:56] = 0
            cv2.imwrite(str(mask_path), mask)
            wf = _WalkableFloor((100, 100), mask_path)
            path = wf.shortest_path((20.0, 20.0), (76.0, 20.0))
            self.assertGreater(len(path), 2)
            # Path must detour below the wall (max y > 62) instead of cutting straight through x=48, y=20.
            self.assertGreater(max(y for _x, y in path), 62.0)

    def test_dynamic_s2_scope_registration_and_season_options(self):
        from route_scopes import SCOPES, ensure_season_scope, scope_for, season_options
        scope = ensure_season_scope("s2", "nightmare", season_title="测试新赛季", floors=("-1", "-2", "-3", "-4"))
        try:
            self.assertEqual(scope.season, "s2")
            self.assertEqual(scope.runtime_mode, "nightmare")
            self.assertIn(scope.id, SCOPES)
            self.assertEqual(scope_for(scope.scenario, scope.mode).id, scope.id)
            opts = season_options()
            self.assertTrue(any(row["id"] == "s2" and row["name"] == "测试新赛季" for row in opts))
        finally:
            SCOPES.pop(scope.id, None)


class RoutePlazaRefreshAvailabilityTests(unittest.TestCase):
    def test_refresh_availability_updates_structure_gate_immediately(self):
        from PySide6.QtCore import QCoreApplication, QObject, Signal
        from client_paths import WORK_ROOT
        from client_route_plaza import RoutePlazaService

        app = QCoreApplication.instance() or QCoreApplication([])
        del app

        class _FakeGalleries(QObject):
            changed = Signal()
            galleriesChanged = Signal()
            scopeChanged = Signal()

            def __init__(self):
                super().__init__()
                self.scope = SCOPES["season-s1-nightmare"]
                self.scopeId = self.scope.id

            def installedIds(self):
                return set()

            def installedMeta(self):
                return {}

            def submissionChoices(self):
                return []

            def plazaSources(self):
                return {}

        state = {"ready": False}
        blocked = []
        WORK_ROOT.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(dir=WORK_ROOT) as tmp:
            sandbox = Path(tmp) / "sandbox"
            sandbox.mkdir(parents=True, exist_ok=True)
            (sandbox / ".qt-lab-owner").write_text("qt-quick-isolated-v1\n", encoding="utf-8")
            service = RoutePlazaService(
                sandbox,
                _FakeGalleries(),
                structure_available=lambda season, mode: state["ready"] and (season, mode) == ("s1", "nightmare"),
            )
            service._active = True
            service.downloadBlocked.connect(blocked.append)
            route = SimpleNamespace(scope=SCOPES["season-s1-nightmare"])
            self.assertFalse(service._check_structure(route))
            self.assertEqual(len(blocked), 1)
            self.assertIn("导入", blocked[0])

            # Flip structure availability and call refreshAvailability()
            state["ready"] = True
            service.refreshAvailability()
            self.assertTrue(service._check_structure(route))
            service.shutdown()


if __name__ == "__main__":
    unittest.main()
