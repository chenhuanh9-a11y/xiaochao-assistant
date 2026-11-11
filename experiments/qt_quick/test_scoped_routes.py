"""Real Qt JS planner and archive/store boundaries; tiny synthetic roads/art."""
from copy import deepcopy
import hashlib
import io
import json
import os
from pathlib import Path
import tempfile
import unittest
import uuid
import zipfile

from PIL import Image
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("QT_QUICK_BACKEND", "software")
from PySide6.QtGui import QGuiApplication

from custom_maps import GalleryStore, GALLERY_SCHEMA, RouteError
from route_scopes import SCOPES
from scoped_route_library import MODEL_FILE, ScopedRouteLibrary
import route_plaza
import route_submission

APP = QGuiApplication.instance() or QGuiApplication([])


def write_scoped_resources(root):
    root.mkdir(parents=True, exist_ok=True)
    files = {}
    for sid in ("classic-hard", "season-s1-hard", "season-s1-nightmare"):
        scope = SCOPES[sid]
        mid = "map-01" if sid == "classic-hard" else "106001" if scope.mode == "hard" else "107001"
        tid = "classic-hard-map-01" if sid == "classic-hard" else "season-s1-" + mid
        fids = ("1", "2") if sid == "classic-hard" else ("b1", "b2", "b3")
        floors = []
        for fid in fids:
            name = f"synthetic/{mid}/{fid}.png"
            target = root / name
            target.parent.mkdir(parents=True, exist_ok=True)
            Image.new("RGBA", (14, 14), (230, 240, 250, 255)).save(target)
            files[name] = hashlib.sha256(target.read_bytes()).hexdigest()
            edges = [(1 if x < 6 else 0) | (2 if y < 6 else 0) | (4 if x else 0) | (8 if y else 0)
                     for y in range(7) for x in range(7)]
            labels = [1 if x < 4 and y < 4 else 2 if x >= 4 and y >= 4 else 0 for y in range(7) for x in range(7)]
            raw = dict(version=1, width=14, height=14, step=2, cols=7, rows=7,
                       edges=[v for edge in edges for v in (edge, 1)],
                       labels=[v for label in labels for v in (label, 1)],
                       rooms=[dict(id=1, cell=16), dict(id=2, cell=40)])
            nav = f"synthetic/{mid}/{fid}.json"
            (root / nav).write_text(json.dumps(raw), "utf-8")
            files[nav] = hashlib.sha256((root / nav).read_bytes()).hexdigest()
            floors.append(dict(id=fid, label=fid, width=14, height=14, image=name, navigation=nav))
        template = dict(id=tid, mapId=mid, name="合成地图", difficulty=scope.mode, scenario=scope.scenario,
                        navigationSignature="a" * 64, artworkSignature="c" * 64, floors=floors)
        if scope.season == "s1":
            template.update(floorAssociationsReady=True, floorAssociationSignature="b" * 64,
                floorAssociations=[dict(kind="floor-association", association="same-source-room-instance",
                    geometryExplicit=True, fromFloor="b1", toFloor="b2", levelCost=100,
                    plannerPolicy=dict(id="bidirectional-source-door-sets-v1", enabled=True, direction="bidirectional"),
                    fromPorts=[dict(floor="b1", cell=16, mapPlaneXZ=[5, 5])],
                    toPorts=[dict(floor="b2", cell=40, mapPlaneXZ=[11, 11])])])
        catalog_path = root / scope.catalog
        catalog_path.parent.mkdir(parents=True, exist_ok=True)
        catalog = json.loads(catalog_path.read_text()) if catalog_path.exists() else dict(templates=[])
        catalog["templates"].append(template)
        catalog_path.write_text(json.dumps(catalog), "utf-8")
        files[scope.catalog] = hashlib.sha256(catalog_path.read_bytes()).hexdigest()
    (root / "manifest.json").write_text(json.dumps(dict(schema="xingtu-android-route-resources-v1",
        sourceCommit="synthetic-test", routeModelSha256=hashlib.sha256(MODEL_FILE.read_bytes()).hexdigest(), files=files)), "utf-8")


def add_synthetic_display_bindings(root):
    """Explicit identity-plane fixture, never a production registration."""
    import numpy as np
    from custom_maps import mask_sha256
    digest = mask_sha256(np.ones((14, 14), dtype=np.uint8))
    manifest = json.loads((root / "manifest.json").read_text())
    classic = json.loads((root / "classic-route-editor/catalog.json").read_text())["templates"][0]
    name = "pcport/readable-artwork.json"
    target = root / name
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(dict(classic=[dict(mapId="map-01", difficulty="hard", floors=[
        dict(gameFloor=int(f["id"]), displayAsset=f["image"], imageSize=[14, 14],
             matrixImageToNative=[1, 0, 0, 0, 1, 0], layerMaskSha256=digest) for f in classic["floors"]])])), "utf-8")
    manifest["files"][name] = hashlib.sha256(target.read_bytes()).hexdigest()
    name = "season-route-editor/catalog.json"
    catalog = json.loads((root / name).read_text())
    for template in catalog["templates"]:
        for floor in template["floors"]:
            floor["displayRegistration"] = dict(purpose="display-only", nativeMapId="readable-s1-" + template["mapId"],
                imageToNative=[1, 0, 0, 0, 1, 0], layerMaskSha256=digest)
    (root / name).write_text(json.dumps(catalog), "utf-8")
    manifest["files"][name] = hashlib.sha256((root / name).read_bytes()).hexdigest()
    (root / "manifest.json").write_text(json.dumps(manifest), "utf-8")


class ScopedRoutesTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name).resolve()
        self.resources = self.root / "resources"
        write_scoped_resources(self.resources)
        self.addCleanup(self.temp.cleanup)

    def library(self, sid):
        library = ScopedRouteLibrary(self.resources, sid)
        self.assertTrue(library.available, library.error)
        return library

    def targets(self, library, floor=None):
        graph = library.graph(library.maps()[0][0])
        floor = floor or library.floor_ids[0]
        return graph, [graph.pick(floor, 5, 5), graph.pick(floor, 11, 11)]

    def test_all_scopes_pick_plan_and_package_roundtrip(self):
        for sid in ("classic-hard", "season-s1-hard", "season-s1-nightmare"):
            with self.subTest(scope=sid):
                lib = self.library(sid)
                mid = lib.maps()[0][0]
                graph, points = self.targets(lib)
                points[1]["color"] = "#FF0000"
                doc = graph.document(points)
                self.assertEqual((doc["scenario"], doc["difficulty"], doc["sourceMapId"]), (lib.scope.scenario, lib.scope.mode, mid))
                if sid == "classic-hard":
                    self.assertEqual((doc["kind"], doc["artworkSignature"], doc["version"]), ("planned-route", "c" * 64, 2))
                self.assertEqual(len(doc["segments"]), 1)
                gallery = dict(id="personal", maps={mid: dict(points=points)})
                maps = route_submission.prepare_maps(lib, gallery)
                work, version = str(uuid.uuid4()), str(uuid.uuid4())
                draft = dict(id=version, work_id=work, revision=1, content_kind="nightmare-routes",
                             scenario=lib.scope.scenario, status="draft", packages=[])
                package = route_submission.build_package(draft, gallery["id"], maps, "合成路线", "星辰", scope_id=sid)
                record = dict(package_id=package.payload["package_id"], revision=package.payload["package_revision"],
                              mode=lib.scope.mode, manifest_bytes=package.payload["manifest_bytes"])
                restored = route_plaza.read_manifest(package.archive, record, draft)
                self.assertEqual(restored["schemaVersion"], 4)
                self.assertEqual(restored["routeCatalogId"], sid)
                imported, report = lib.import_maps(restored)
                self.assertEqual(report["droppedPoints"], 0)
                self.assertEqual(graph.document(imported[mid]["points"]), doc)
                self.assertEqual(package.payload["scenario"], lib.scope.scenario)

    def test_cross_floor_uses_declared_associations_and_new_start(self):
        lib = self.library("season-s1-hard")
        graph, first = self.targets(lib)
        end = graph.pick("-2", 11, 11)
        doc = graph.document([first[0], end])
        self.assertEqual([p["floor"] for p in doc["segments"][0]["parts"]], ["b1", "b2"])
        end["start"] = True
        self.assertEqual(graph.document([first[0], end])["segments"], [])

    def test_classic_missing_cross_floor_connection_is_not_invented(self):
        lib = self.library("classic-hard")
        graph, points = self.targets(lib)
        with self.assertRaisesRegex(RouteError, "跨层"):
            graph.document([points[0], graph.pick("2", 11, 11)])

    def test_bad_signature_room_and_foreign_scope_are_rejected(self):
        lib = self.library("season-s1-hard")
        graph, points = self.targets(lib)
        plan = graph.document(points)
        manifest = dict(routeCatalogId=lib.scope.id, scenario=lib.scope.scenario, mode=lib.scope.mode,
                        maps=[dict(routePlan=plan)])
        bad = deepcopy(manifest)
        bad["maps"][0]["routePlan"]["navigationSignature"] = "f" * 64
        with self.assertRaises(RouteError):
            lib.import_maps(bad)
        bad = deepcopy(manifest)
        bad["maps"][0]["routePlan"]["points"][0]["roomId"] = 500
        with self.assertRaises(RouteError):
            lib.import_maps(bad)
        with self.assertRaises(RouteError):
            self.library("season-s1-nightmare").import_maps(manifest)

    def test_scoped_store_reopens_and_cannot_cross_import(self):
        scope = SCOPES["season-s1-hard"]
        path = self.root / "galleries.json"
        store = GalleryStore(path, self.root / "absent.json", scope=scope)
        gid = store.create("自己的路线")
        graph, points = self.targets(self.library(scope.id))
        points.append(graph.pick("-3", 5, 5))
        points[-1]["start"] = True
        self.assertTrue(store.set_route(gid, "106001", points))
        saved = path.read_bytes()
        reopened = GalleryStore(path, self.root / "absent.json", scope=scope)
        self.assertEqual(reopened.route(gid, "106001"), points)
        wrong = GalleryStore(path, self.root / "absent.json", scope=SCOPES["season-s1-nightmare"])
        with self.assertRaises(ValueError):
            wrong.import_gallery(reopened.export(gid))
        self.assertEqual(path.read_bytes(), saved)
        self.assertTrue(wrong.load_error)

    def test_download_origin_survives_copy_and_file_export(self):
        scope = SCOPES["season-s1-hard"]
        store = GalleryStore(self.root / "galleries.json", self.root / "absent.json", scope=scope)
        lib = self.library(scope.id)
        _graph, points = self.targets(lib)
        work = str(uuid.uuid4())
        gid = store.install_plaza("下载路线", {"106001": dict(points=points)},
                                 dict(workId=work, versionId=str(uuid.uuid4()), revision=1))
        copied = store.create("个人副本", gid)
        self.assertFalse(store.readonly(copied))
        for gallery in (store.export(gid), store.export(copied)):
            self.assertEqual(gallery["originWorkId"], work)
            with self.assertRaisesRegex(route_plaza.AccountError, "个人使用"):
                route_submission.prepare_maps(lib, gallery)

    def test_resource_mutation_is_rejected_before_use(self):
        lib = self.library("classic-hard")
        image = lib.floor("map-01", "1").image_path
        image.write_bytes(b"changed")
        with self.assertRaises(RouteError):
            lib.floor("map-01", "1")

    def test_public_listing_parameters_and_scope_filter(self):
        calls = []
        def api(method, path):
            calls.append(path)
            return [dict(id="version", work_id="work", revision=2, content_kind="nightmare-routes",
                         status="approved", scenario="phosphorescence", modes=["hard"], hard_map_count=14)]
        rows = route_plaza.list_routes("路线", api=api, scope_id="season-s1-hard", sort="likes")
        self.assertEqual(len(rows), 1)
        self.assertEqual((rows[0].mode, rows[0].scenario, rows[0].map_count), ("hard", "phosphorescence", 14))
        self.assertIn("scenario=phosphorescence", calls[0])
        self.assertIn("sort=likes", calls[0])
        self.assertEqual(route_plaza.list_routes(api=api, scope_id="classic-hard"), [])

    def test_display_requires_exact_declared_structure(self):
        lib = self.library("classic-hard")
        with self.assertRaisesRegex(RouteError, "显示绑定"):
            lib.validate_reference("map-01", "1", __import__("numpy").ones((14, 14), dtype="uint8"))


if __name__ == "__main__":
    unittest.main()
