"""Synthetic coordinate consumers and metadata sources; no Android art/roads."""
from copy import deepcopy
from pathlib import Path
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "app"))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools/maps"))
import route_plaza
from route_plaza_navigation import TEMPLATES
import sync_route_plaza_navigation as sync


class SyntheticLibrary:
    """Echo pick: observes native coordinates before real PC road snapping."""
    def __init__(self, template="nightmare-layout-01", metadata=None):
        self.template = template
        self.assets = {}
        self.picks = []
        for floor, grid in (metadata or TEMPLATES[template])["floors"].items():
            self.assets[floor] = SimpleNamespace(template_size=(grid["width"], grid["height"]),
                template_to_native=np.array([[2., 0., 4.], [0., 3., 5.], [0., 0., 1.]]),
                image_size=(7, 5), to_native=np.eye(3))

    def templates(self):
        return {"map": self.template}

    def floor(self, map_id, floor):
        assert map_id == "map"
        return self.assets[floor]

    def graph(self, map_id):
        assert map_id == "map"
        return SimpleNamespace(pick=self.pick)

    def pick(self, floor, x, y):
        self.picks.append((floor, x, y))
        return dict(floor=floor, x=x, y=y, kind="room", room="synthetic")


def plan_for(template="nightmare-layout-01", **fields):
    plan = dict(templateId=template, navigationVersion=1, navigationSignature=TEMPLATES[template]["navigationSignature"],
                points=[dict(floor="1", cell=0, kind="room")])
    plan.update(fields)
    return plan


def manifest_for(plan):
    return dict(maps=[dict(routePlan=plan)])


class RouteNavigationTests(unittest.TestCase):
    def assert_mismatch(self, library, plan, reason):
        maps, report = route_plaza.convert_manifest(library, manifest_for(plan))
        self.assertEqual(maps, {})
        self.assertEqual(report["versionMismatchMaps"], 1)
        self.assertEqual(report["versionMismatches"], [dict(templateId=plan["templateId"], reason=reason)])
        self.assertEqual(report["missingMaps"], 0)
        self.assertEqual(report["droppedPoints"], 0)
        self.assertEqual(library.picks, [])

    def test_every_registered_current_and_compatible_signature_preserves_formula(self):
        self.assertEqual(len(TEMPLATES), 26)
        self.assertEqual(sum(len(t["floors"]) for t in TEMPLATES.values()), 78)
        for tid, metadata in TEMPLATES.items():
            for signature in (metadata["navigationSignature"], *metadata["compatibleNavigationSignatures"]):
                with self.subTest(template=tid, signature=signature):
                    library = SyntheticLibrary(tid)
                    raw, expected = [], []
                    for floor, grid in metadata["floors"].items():
                        for cell in (0, grid["cols"] + 1, grid["cols"] * grid["rows"] - 1):
                            raw.append(dict(floor="minus-1" if floor == "B1" else floor, cell=cell, kind="room"))
                            x = min(grid["width"] - 1, (cell % grid["cols"]) * 2 + 1)
                            y = min(grid["height"] - 1, (cell // grid["cols"]) * 2 + 1)
                            expected.append((floor, x * 2 + 4, y * 3 + 5))
                    plan = plan_for(tid)
                    plan.update(navigationSignature=signature, points=raw)
                    maps, report = route_plaza.convert_manifest(library, manifest_for(plan))
                    self.assertEqual(library.picks, expected)
                    self.assertEqual(len(maps["map"]["points"]), 9)
                    self.assertEqual((report["maps"], report["versionMismatchMaps"], report["droppedPoints"],
                                      report["outOfRangePoints"], report["legacyNavigationMaps"]), (1, 0, 0, 0, 0))

    def test_unknown_malformed_or_other_template_signature_never_reaches_graph(self):
        other = TEMPLATES["nightmare-layout-02"]["navigationSignature"]
        for signature in ("f" * 64, other, "", None, True, [], {}):
            with self.subTest(signature=signature):
                plan = plan_for()
                plan["navigationSignature"] = signature
                self.assert_mismatch(SyntheticLibrary(), plan, "unknownNavigationSignature")

    def test_unsupported_navigation_versions_including_nonintegers_are_rejected(self):
        for version in (0, -1, 2, 99, "1", 1.0, True, None, [], {}):
            with self.subTest(version=version):
                plan = plan_for()
                plan["navigationVersion"] = version
                self.assert_mismatch(SyntheticLibrary(), plan, "unsupportedNavigationVersion")

    def test_missing_fields_use_registered_historical_grid_and_report_assumption(self):
        for removed in (("navigationSignature",), ("navigationVersion",), ("navigationSignature", "navigationVersion")):
            with self.subTest(removed=removed):
                plan = plan_for()
                for key in removed:
                    del plan[key]
                maps, report = route_plaza.convert_manifest(SyntheticLibrary(), manifest_for(plan))
                self.assertEqual((maps["map"]["points"][0]["x"], maps["map"]["points"][0]["y"]), (6., 8.))
                self.assertEqual((report["maps"], report["legacyNavigationMaps"], report["versionMismatchMaps"]), (1, 1, 0))
        plan["navigationVersion"] = 2
        self.assert_mismatch(SyntheticLibrary(), plan, "unsupportedNavigationVersion")
        plan = plan_for()
        del plan["navigationVersion"]
        plan["navigationSignature"] = "f" * 64
        self.assert_mismatch(SyntheticLibrary(), plan, "unknownNavigationSignature")

    def test_template_size_mismatch_rejects_whole_map_even_without_signature(self):
        for legacy in (False, True):
            with self.subTest(legacy=legacy):
                plan = plan_for(points=[dict(floor="1", cell=0), dict(floor="2", cell=0)])
                if legacy:
                    del plan["navigationSignature"]
                library = SyntheticLibrary()
                width, height = library.assets["2"].template_size
                library.assets["2"].template_size = (width + 1, height)
                self.assert_mismatch(library, plan, "gridMismatch")

    def test_missing_referenced_floor_and_inconsistent_grid_metadata_reject_map(self):
        library = SyntheticLibrary()
        del library.assets["1"]
        self.assert_mismatch(library, plan_for(), "gridMismatch")
        metadata = deepcopy(TEMPLATES)
        metadata["nightmare-layout-01"]["floors"]["1"]["cols"] += 1
        with patch.object(route_plaza, "NAVIGATION_TEMPLATES", metadata):
            self.assert_mismatch(SyntheticLibrary(), plan_for(), "gridMismatch")

    def test_image_size_fallback_for_old_resources_still_checks_geometry(self):
        library = SyntheticLibrary()
        asset = library.assets["1"]
        asset.image_size, asset.template_size, asset.template_to_native = asset.template_size, None, None
        points, dropped = route_plaza.convert_plan(library, "map", plan_for())
        self.assertEqual((points[0]["x"], points[0]["y"], dropped), (1., 1., 0))

    def test_out_of_range_cells_and_invalid_types_are_dropped_separately(self):
        grid = TEMPLATES["nightmare-layout-01"]["floors"]["1"]
        limit = grid["cols"] * grid["rows"]
        plan = plan_for(points=[dict(floor="1", cell=cell) for cell in
                               (0, limit, -1, limit + 999999, True, 1.5, "0", None, limit - 1)])
        library = SyntheticLibrary()
        maps, report = route_plaza.convert_manifest(library, manifest_for(plan))
        self.assertEqual((report["droppedPoints"], report["outOfRangePoints"]), (7, 3))
        self.assertEqual((report["maps"], report["versionMismatchMaps"]), (1, 0))
        self.assertEqual(len(library.picks), 2)
        self.assertEqual(len(maps["map"]["points"]), 2)
        with self.assertRaises(route_plaza.RouteError):
            route_plaza.cell_to_image(limit, grid["width"], grid["height"])

    def test_missing_local_template_takes_precedence_over_navigation_mismatch(self):
        plan = dict(templateId="nightmare-layout-17", navigationSignature="f" * 64, navigationVersion=99)
        maps, report = route_plaza.convert_manifest(SyntheticLibrary(), manifest_for(plan))
        self.assertEqual(maps, {})
        self.assertEqual((report["missingMaps"], report["versionMismatchMaps"], report["droppedPoints"]), (1, 0, 0))

    def test_unregistered_local_template_is_not_granted_grid_compatibility(self):
        library = SyntheticLibrary("nightmare-layout-17", TEMPLATES["nightmare-layout-01"])
        plan = dict(templateId=library.template, points=[dict(floor="1", cell=0)])
        self.assert_mismatch(library, plan, "unknownTemplateGrid")

    def test_direct_convert_plan_cannot_bypass_validation(self):
        plan = plan_for()
        plan["navigationVersion"] = 2
        with self.assertRaisesRegex(route_plaza.NavigationMismatch, "请更新 PC 版/资源包"):
            route_plaza.convert_plan(SyntheticLibrary(), "map", plan)
        plan = plan_for("nightmare-layout-02")
        with self.assertRaises(route_plaza.NavigationMismatch) as error:
            route_plaza.convert_plan(SyntheticLibrary(), "map", plan)
        self.assertEqual(error.exception.reason, "templateBindingMismatch")


class NavigationSyncTests(unittest.TestCase):
    def setUp(self):
        tid = "nightmare-layout-01"
        self.data = {}
        floors = [dict(id=floor, navigation=f"navigation/n01-{floor}.json", width=7, height=5, sha256="c" * 64)
                  for floor in ("minus-1", "1", "2")]
        previous = dict(id=tid, navigationSignature="b" * 64, floors=deepcopy(floors))
        current = dict(id=tid, navigationSignature="a" * 64, compatibleNavigationSignatures=["b" * 64], floors=floors)
        for root, template in ((sync.ASSET_ROOT, current), (sync.LEGACY_ROOT, previous)):
            self.data[root + "catalog.json"] = dict(navigationVersion=1, templates=[template])
            for floor in floors:
                self.data[root + floor["navigation"]] = dict(version=1, width=7, height=5, step=2, cols=4, rows=3,
                    edges=["PRIVATE_ROADS"], rooms=["PRIVATE_ROOMS"], source="PRIVATE_SOURCE")
        self.data[sync.ASSET_ROOT + "migration.json"] = dict(version=1, templates={tid: dict(signatures=["b" * 64])})

    def test_export_is_deterministic_and_only_contains_grid_identities(self):
        version, templates = sync.export_catalog(self.data.__getitem__)
        self.assertEqual(version, 1)
        template = templates["nightmare-layout-01"]
        self.assertEqual(template["compatibleNavigationSignatures"], ["b" * 64])
        self.assertEqual(template["floors"]["B1"], dict(width=7, height=5, step=2, cols=4, rows=3))
        rendered = sync.render(dict(commit="test"), version, templates)
        self.assertNotIn("PRIVATE_", rendered)
        self.assertEqual(rendered, sync.render(dict(commit="test"), *sync.export_catalog(self.data.__getitem__)))

    def test_changed_grid_unsupported_version_or_unreviewed_signature_cannot_be_exported(self):
        for path, change in (
            (sync.ASSET_ROOT + "navigation/n01-1.json", dict(step=4)),
            (sync.LEGACY_ROOT + "navigation/n01-1.json", dict(width=8)),
            (sync.ASSET_ROOT + "catalog.json", dict(navigationVersion=2)),
        ):
            with self.subTest(path=path):
                data = deepcopy(self.data)
                data[path].update(change)
                with self.assertRaises(ValueError):
                    sync.export_catalog(data.__getitem__)
        self.data[sync.ASSET_ROOT + "catalog.json"]["templates"][0]["compatibleNavigationSignatures"].append("e" * 64)
        with self.assertRaisesRegex(ValueError, "reviewed legacy grid source"):
            sync.export_catalog(self.data.__getitem__)


if __name__ == "__main__":
    unittest.main()
