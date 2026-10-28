"""Custom maps: package loading, road-graph planning, galleries and the game layer."""
import hashlib
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock

import numpy as np
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "app"))
from custom_maps import (CustomMapLibrary, GalleryStore, RouteError, gallery_of, insertion_index,  # noqa: E402
                         marker_groups, profile_id, render_game_layer, FloorGraph, MapGraph, mask_sha256)

# Two floors of a 4x3 tile road grid (60 px tiles). Room "big" is 2x2 tiles,
# "hall" is a one-tile-wide corridor; a door joins them. Floor 2 is one room.
POINTS_1 = [[30, 30], [90, 30], [30, 90], [90, 90], [150, 30], [210, 30]]
ROOMS_1 = ["big", "big", "big", "big", "hall", "hall"]
LINKS_1 = [[0, 1, "room-grid"], [0, 2, "room-grid"], [1, 3, "room-grid"], [2, 3, "room-grid"],
           [1, 4, "source-door"], [4, 5, "room-grid"]]
POINTS_2 = [[30, 30], [90, 30], [30, 90], [90, 90], [210, 150]]
ROOMS_2 = ["top", "top", "top", "top", "island"]
LINKS_2 = [[0, 1, "room-grid"], [0, 2, "room-grid"], [1, 3, "room-grid"], [2, 3, "room-grid"]]


def write_package(root: Path, *, stairs=True):
    floors = {}
    for floor, points, rooms, links in (("1", POINTS_1, ROOMS_1, LINKS_1), ("2", POINTS_2, ROOMS_2, LINKS_2)):
        (root / "images" / "m").mkdir(parents=True, exist_ok=True)
        (root / "topology" / "m").mkdir(parents=True, exist_ok=True)
        art = np.zeros((90, 120, 4), np.uint8)
        art[:, :] = (120, 110, 100, 255)  # Art at half scale: 120x90 image -> 240x180 native.
        Image.fromarray(art, "RGBA").save(root / f"images/m/{floor}.png")
        (root / f"topology/m/{floor}.json").write_text(json.dumps(dict(points=points, pointRooms=rooms, links=links)))
        floors[floor] = dict(
            image=f"images/m/{floor}.png", imageSha256=hashlib.sha256((root / f"images/m/{floor}.png").read_bytes()).hexdigest(),
            imageSize=[120, 90], matrixImageToNative=[[2, 0, 0], [0, 2, 0], [0, 0, 1]], nativeShape=[180, 240],
            topology=f"topology/m/{floor}.json",
            topologySha256=hashlib.sha256((root / f"topology/m/{floor}.json").read_bytes()).hexdigest(),
            entrances=[dict(label="大门", x=10, y=10)] if floor == "1" else [])
    stair = [dict(id="s1", **{"from": dict(floor="1", x=208, y=32), "to": dict(floor="2", x=88, y=88)},
                  cost=100, bidirectional=True)] if stairs else []
    manifest = dict(schema="xingtu-custom-maps-v1", version="test", maps=[
        dict(mapId="m", templateNumber=1, name="测试图", floors=floors, stairs=stair)])
    (root / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False), "utf-8")


def target(graph, floor, x, y, color="#63CAFF"):
    point = graph.pick(floor, x, y)
    point["color"] = color
    return point


class LibraryTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.root = Path(self.folder.name)
        write_package(self.root)
        self.library = CustomMapLibrary(self.root)

    def tearDown(self):
        self.folder.cleanup()

    def test_missing_and_tampered_packages(self):
        self.assertFalse(CustomMapLibrary(self.root / "nope").available)
        self.assertTrue(self.library.available)
        self.assertEqual(self.library.maps(), [("m", "测试图")])
        (self.root / "images/m/2.png").write_bytes(b"changed")
        with self.assertRaisesRegex(ValueError, "校验失败"):
            CustomMapLibrary(self.root).floor("m", "2")

    def test_pick_room_versus_corridor(self):
        graph = self.library.graph("m")
        room = graph.pick("1", 85, 85)
        self.assertEqual((room["kind"], room["room"]), ("room", "big"))
        corridor = graph.pick("1", 152, 33)
        self.assertEqual((corridor["kind"], corridor["x"], corridor["y"]), ("via", 150.0, 30.0))
        self.assertIsNone(graph.pick("1", 230, 170), "far from any road point")

    def test_reference_requires_mask_identity_not_only_shape(self):
        mask = np.ones((180, 240), np.uint8)
        row = self.library._maps["m"]["floors"]["1"]
        with self.assertRaises(ValueError):
            self.library.validate_reference("m", "1", mask)  # Missing binding fails closed.
        row["maskSha256"] = mask_sha256(mask)
        self.library.validate_reference("m", "1", mask * 255)
        self.assertEqual(mask_sha256(mask), mask_sha256(np.asfortranarray(mask)))
        changed = mask.copy()
        changed[10, 10] = 0
        with self.assertRaises(ValueError):
            self.library.validate_reference("m", "1", changed)
        with self.assertRaises(ValueError):
            self.library.validate_reference("m", "1", mask[:100])

    def test_same_floor_shortest_path_can_leave_floor_away_from_goal(self):
        lower = FloorGraph([[0, 0], [1000, 0], [-10, 0]], ["a", "b", "c"],
                           [[0, 1, "road"], [0, 2, "road"]])
        upper = FloorGraph([[0, 0]], ["d"], [])
        graph = MapGraph({"1": lower, "2": upper}, [
            dict(**{"from": dict(floor="1", x=-10, y=0), "to": dict(floor="2", x=0, y=0)},
                 cost=1, bidirectional=False),
            dict(**{"from": dict(floor="2", x=0, y=0), "to": dict(floor="1", x=1000, y=0)},
                 cost=1, bidirectional=False)])
        cells, cost = graph.path(("1", 0), ("1", 1))
        self.assertEqual(cost, 12)
        self.assertEqual(cells, [("1", 0), ("1", 2), ("2", 0), ("1", 1)])

    def test_plan_through_door_and_stairs(self):
        graph = self.library.graph("m")
        points = [target(graph, "1", 30, 90), target(graph, "1", 210, 30), target(graph, "2", 30, 30, "#FF6B81")]
        segments = graph.plan(points)
        self.assertEqual(len(segments), 2)
        self.assertEqual(segments[1]["color"], "#FF6B81")
        self.assertEqual(segments[1]["cells"][0][0], "1")
        self.assertEqual(segments[1]["cells"][-1][0], "2")
        lines = graph.floor_lines(segments, "1")
        self.assertTrue(all(len(line) >= 2 for line, _color in lines))
        flat = [tuple(map(float, p)) for line, _c in lines for p in line]
        self.assertIn((210.0, 30.0), flat)
        self.assertNotIn((150.0, 30.0), flat, "straight runs keep only their ends")

    def test_no_path_and_missing_stairs(self):
        graph = self.library.graph("m")
        with self.assertRaisesRegex(RouteError, "第1段"):
            graph.plan([target(graph, "2", 30, 30), dict(floor="2", x=210, y=150, kind="via", room=None)])
        root = self.root / "nostairs"
        write_package(root, stairs=False)
        graph = CustomMapLibrary(root).graph("m")
        with self.assertRaisesRegex(RouteError, "跨层楼梯"):
            graph.plan([target(graph, "1", 30, 30), target(graph, "2", 30, 30)])

    def test_new_start_breaks_the_line(self):
        graph = self.library.graph("m")
        points = [target(graph, "1", 30, 30), target(graph, "1", 150, 30), target(graph, "1", 210, 30)]
        points[2]["start"] = True
        self.assertEqual(len(graph.plan(points)), 1)

    def test_markers_and_insertion(self):
        graph = self.library.graph("m")
        a, b = target(graph, "1", 30, 30), target(graph, "1", 210, 30)
        points = [a, b, dict(a)]
        self.assertEqual(sorted(label for *_xy, label in marker_groups(points, "1")), ["1/3", "2"])
        segments = graph.plan([a, b])
        via = target(graph, "1", 150, 30)
        self.assertEqual(insertion_index([a, b], segments, graph, via), 1)

    def test_game_layer_warps_art_and_draws_route(self):
        graph = self.library.graph("m")
        points = [target(graph, "1", 30, 90, "#FF6B81"), target(graph, "1", 210, 30, "#FF6B81")]
        layer = render_game_layer(self.library, "m", "1", points)
        self.assertEqual(layer.shape, (180, 240, 4))
        self.assertTrue((layer[:, :, 3] > 0).mean() > .9, "art fills the native canvas")
        pink = (layer[:, :, 2] > 230) & (layer[:, :, 1] < 140) & (layer[:, :, 0] > 100)
        self.assertGreater(int(pink.sum()), 50)
        empty = render_game_layer(self.library, "m", "2", [])
        self.assertEqual(empty.shape, (180, 240, 4))


class GalleryTests(unittest.TestCase):
    def test_legacy_routes_migrate_per_floor_without_changing_source_or_existing_gallery(self):
        with tempfile.TemporaryDirectory() as folder:
            folder = Path(folder)
            path, source = folder / "g.json", folder / "custom-routes.json"
            store = GalleryStore(path)
            existing = store.create("existing")
            legacy = dict(schema="xingtu-custom-routes-v1", useInGame=True, maps={"m": {
                floor: dict(points=[[index, index + 1] for index in range(60)], color="#e03150", snap=False)
                for floor in ("B1", "1", "2")}})
            original = json.dumps(legacy).encode()
            source.write_bytes(original)
            migrated = GalleryStore(path)
            self.assertEqual(migrated.migration_error, "")
            self.assertEqual(migrated.name(existing), "existing")
            additions = [(key, migrated.route(key, "m")) for key, _, readonly in migrated.galleries()
                         if not readonly and key != existing]
            self.assertEqual(len(additions), 3)
            self.assertEqual(sum(len(points) for _, points in additions), 180)
            for _, points in additions:
                self.assertTrue(points[0]["start"])
                self.assertEqual(points[-1]["x"], 59)
                self.assertEqual(points[0]["color"], "#e03150")
                self.assertEqual(len({point["floor"] for point in points}), 1)
            self.assertEqual(source.read_bytes(), original)
            before = path.read_bytes()
            self.assertEqual(len(GalleryStore(path).galleries()), len(migrated.galleries()))
            self.assertEqual(path.read_bytes(), before)
            migrated.delete(additions[0][0])
            self.assertEqual(len(GalleryStore(path).galleries()), len(migrated.galleries()))

    def test_legacy_migration_failure_keeps_original_and_retries_after_save_recovers(self):
        with tempfile.TemporaryDirectory() as folder:
            folder = Path(folder)
            source, path = folder / "custom-routes.json", folder / "g.json"
            original = json.dumps(dict(schema="xingtu-custom-routes-v1", maps={"m": {
                "1": dict(points=[[1, 2]], color="#abcdef")}})).encode()
            source.write_bytes(original)
            with mock.patch.object(GalleryStore, "_save", return_value=False):
                store = GalleryStore(path)
            self.assertTrue(store.migration_error)
            self.assertEqual(len(store.galleries()), 1)
            self.assertEqual(source.read_bytes(), original)
            self.assertEqual(len(GalleryStore(path).galleries()), 2)

    def test_legacy_migration_never_overwrites_unreadable_gallery(self):
        with tempfile.TemporaryDirectory() as folder:
            folder = Path(folder)
            path = folder / "g.json"
            path.write_bytes(b"unreadable existing galleries")
            (folder / "custom-routes.json").write_text(json.dumps(dict(schema="xingtu-custom-routes-v1", maps={})))
            store = GalleryStore(path)
            self.assertTrue(store.migration_error)
            self.assertEqual(path.read_bytes(), b"unreadable existing galleries")

    def test_default_is_readonly_and_player_galleries_persist(self):
        with tempfile.TemporaryDirectory() as folder:
            folder = Path(folder)
            default = folder / "default.json"
            default.write_text(json.dumps(dict(schema="xingtu-custom-gallery-v1", id="x", name="默认路线", maps={
                "m": dict(points=[dict(floor="1", x=1, y=2, kind="room", room="big", color="#ffd54f")])})), "utf-8")
            store = GalleryStore(folder / "g.json", default)
            self.assertEqual(store.galleries()[0], ("default", "默认路线", True))
            self.assertFalse(store.set_route("default", "m", []))
            self.assertEqual(store.route("default", "m")[0]["color"], "#FFD54F")
            copy = store.create("我的", copy_from="default")
            self.assertEqual(store.drawn_maps(copy), {"m"})
            self.assertTrue(store.set_route(copy, "m", []))
            self.assertTrue(store.rename(copy, "新名字"))
            reloaded = GalleryStore(folder / "g.json", default)
            self.assertEqual(reloaded.name(copy), "新名字")
            self.assertEqual(reloaded.drawn_maps(copy), set())
            exported = reloaded.export("default")
            imported = reloaded.import_gallery(exported)
            self.assertNotEqual(imported, "default")
            self.assertEqual(reloaded.drawn_maps(imported), {"m"})
            self.assertTrue(reloaded.delete(copy))
            self.assertIsNone(reloaded.name(copy))
            with self.assertRaises(ValueError):
                reloaded.import_gallery({"schema": "other"})

    def test_bad_points_are_dropped(self):
        with tempfile.TemporaryDirectory() as folder:
            store = GalleryStore(Path(folder) / "g.json", Path(folder) / "missing.json")
            gallery = store.create("x")
            store.set_route(gallery, "m", [dict(floor="9", x=1, y=1), dict(floor="1", x="a", y=1),
                                           dict(floor="1", x=1, y=1, color="red", start=True)])
            self.assertEqual(store.route(gallery, "m"),
                             [dict(floor="1", x=1.0, y=1.0, kind="via", room=None, color="#63CAFF", start=True)])

    def test_profile_ids(self):
        self.assertEqual(gallery_of(profile_id("abc")), "abc")
        self.assertIsNone(gallery_of("wang-qingxin"))
        self.assertIsNone(gallery_of("custom:"))


if __name__ == "__main__":
    unittest.main(verbosity=2)
