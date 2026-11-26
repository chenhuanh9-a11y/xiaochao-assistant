"""S1 map landmarks (important room names, minecart exits): data, binding, drawing.

The bundled table is checked for full S1 2.105 coverage (困难 22 / 噩梦 20
maps).  If the Android source and the 2.105 structure packages are available
(``XINGTU_ANDROID_LANDMARKS`` and ``XINGTU_S1_PACKAGES``), the generator is
re-run and must reproduce the bundled file byte for byte.
"""
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "app"))
sys.path.insert(0, str(ROOT / "tools" / "maps"))
import map_landmarks as ml  # noqa: E402

DATA = ROOT / "app" / "s1_map_landmarks.json"
HARD = [f"1060{n:02d}" for n in range(1, 26) if n not in (6, 9, 14)]
NIGHTMARE = [f"1070{n:02d}" for n in range(1, 21)]
NEW_HARD = [f"1060{n:02d}" for n in range(16, 26)]
NEW_NIGHTMARE = [f"1070{n:02d}" for n in range(16, 21)]
# Each floor's clean-mask digest in the 2.105.3133558 structure packages.
# 107001/107002 B2/B3 were revised in 2.105 (B1 unchanged).
REVISED = {
    ("107001", "-2"), ("107001", "-3"), ("107002", "-2"), ("107002", "-3"),
}


def load():
    return json.loads(DATA.read_text(encoding="utf-8"))


class BundledDataTests(unittest.TestCase):
    def setUp(self):
        self.data = load()

    def test_schema_version_and_validity(self):
        self.assertEqual(self.data["schema"], ml.SCHEMA)
        self.assertEqual(self.data["sourceVersion"], "2.105.3133558")
        self.assertTrue(ml._valid_document(self.data))
        self.assertEqual(self.data["importantRooms"], {
            "-1": [], "-2": ["维修站", "水晶房", "竞速区", "锅炉房"],
            "-3": ["寰星宫", "无火的熔炼房", "维修站", "水晶房", "跳舞房", "冶炼屋", "冲压房"]})

    def test_full_s1_coverage_for_hard_and_nightmare(self):
        modes = self.data["modes"]
        self.assertEqual(sorted(modes), ["difficult", "nightmare"])
        self.assertEqual(sorted(modes["difficult"]), HARD)
        self.assertEqual(sorted(modes["nightmare"]), NIGHTMARE)
        for mid in ("106006", "106009", "106014"):  # absent / retired in 2.105
            self.assertNotIn(mid, modes["difficult"])
        counts = {}
        for mode, maps in modes.items():
            floors = [(mid, fid, row) for mid, rows in maps.items() for fid, row in rows.items()]
            for mid, fid, row in floors:
                self.assertIn(fid, ("-1", "-2", "-3"), (mid, fid))
            counts[mode] = (len(maps), len(floors),
                            sum(i["kind"] == "room" for *_, r in floors for i in r["items"]),
                            sum(i["kind"] == "minecart" for *_, r in floors for i in r["items"]))
        self.assertEqual(counts, {"difficult": (22, 66, 943, 22), "nightmare": (20, 60, 883, 20)})

    def test_one_minecart_per_map_on_b1(self):
        for mode, maps in self.data["modes"].items():
            for mid, rows in maps.items():
                carts = [(fid, i) for fid, r in rows.items() for i in r["items"] if i["kind"] == "minecart"]
                self.assertEqual(len(carts), 1, mid)
                self.assertEqual(carts[0][0], "-1", mid)

    def test_new_and_revised_maps_are_present_on_every_floor(self):
        for mode, mids in (("difficult", NEW_HARD), ("nightmare", NEW_NIGHTMARE)):
            for mid in mids:
                rows = self.data["modes"][mode][mid]
                self.assertEqual(sorted(rows), ["-1", "-2", "-3"], mid)
                self.assertTrue(any(i["kind"] == "room" for r in rows.values() for i in r["items"]), mid)
        for mid, fid in REVISED:
            self.assertIn(fid, self.data["modes"]["nightmare"][mid])

    def test_display_names_follow_android_renames(self):
        labels = {i["label"] for maps in self.data["modes"].values() for rows in maps.values()
                  for r in rows.values() for i in r["items"]}
        for old in ("矿工休息区", "核心房间", "大型开采现场", "执笔手房间", "星光竞逐赛事通知区", "熔炼房",
                    "矿车撤离点-浅层", "矿车撤离点-深层"):
            self.assertNotIn(old, labels)
        for name in ("维修站", "寰星宫", "水晶房", "竞速区", "冶炼屋", "矿车出口"):
            self.assertIn(name, labels)

    def test_shown_room_counts_per_floor(self):
        shown = {"-1": 0, "-2": 0, "-3": 0}
        for maps in self.data["modes"].values():
            for rows in maps.values():
                for fid, row in rows.items():
                    shown[fid] += sum(i["kind"] == "room" for i in ml.important_items(row, fid, data=self.data))
        # Same totals as Android (B1 none; B2/B3 by important room kinds).
        self.assertEqual(shown, {"-1": 0, "-2": 168, "-3": 264})

    def test_minecart_spots_on_every_floor_follow_the_minecart_switch(self):
        """B1 exits plus the 「矿车」 evacuation points and 「矿车中转站」 on B2/B3."""
        spots = {"-1": {}, "-2": {}, "-3": {}}
        cart_only, rooms_only = ml.LandmarkPreferences(False, True), ml.LandmarkPreferences(True, False)
        for maps in self.data["modes"].values():
            for mid, rows in maps.items():
                for fid, row in rows.items():
                    items = ml.important_items(row, fid, data=self.data)
                    carts = [i for i in items if i["kind"] == "minecart"]
                    for item in carts:
                        key = item.get("caption", item["label"])
                        spots[fid][key] = spots[fid].get(key, 0) + 1
                    # No room switch decides a minecart spot, in either direction.
                    self.assertEqual(ml.select(items, cart_only), carts)
                    self.assertEqual(ml.select(items, ml.LandmarkPreferences(True, True)), items)
                    self.assertFalse(any(i["kind"] == "minecart" for i in ml.select(items, rooms_only)))
                    self.assertEqual(ml.select(items, ml.LandmarkPreferences(False, False)), [])
                    self.assertFalse(any(i["label"] in ml.CART_ROOMS for i in items if i["kind"] == "room"))
                    if fid in ("-2", "-3"):
                        self.assertTrue(carts, (mid, fid))
        self.assertEqual(spots, {"-1": {"矿车出口": 42, "中转站": 54},
                                 "-2": {"矿车": 62, "中转站": 54},
                                 "-3": {"矿车": 84}})


@unittest.skipUnless(os.environ.get("XINGTU_ANDROID_LANDMARKS") and os.environ.get("XINGTU_S1_PACKAGES"),
                     "needs the Android landmark source and the S1 2.105 structure packages")
class GeneratorReproductionTests(unittest.TestCase):
    def test_generator_reproduces_bundled_file(self):
        import build_s1_map_landmarks as builder
        packages = sorted(Path(os.environ["XINGTU_S1_PACKAGES"]).glob("*.xingtu-map"))
        data = builder.build(Path(os.environ["XINGTU_ANDROID_LANDMARKS"]), packages)
        text = json.dumps(data, ensure_ascii=False, separators=(",", ":"), sort_keys=True) + "\n"
        self.assertEqual(text, DATA.read_text(encoding="utf-8"))


def synthetic_doc(mask, *, items=None, floor="-2", mid="106001"):
    height, width = mask.shape
    return dict(schema=ml.SCHEMA, sourceVersion="test", importantRooms={"-1": [], "-2": ["水晶房"], "-3": []},
                modes=dict(difficult={mid: {floor: dict(
                    width=width, height=height, maskSha256=ml.mask_sha256(mask),
                    androidLayerMaskSha256="b" * 64, structureVersionKey="",
                    items=items if items is not None else [
                        dict(id="r1", kind="room", label="水晶房", x=60.0, y=60.0),
                        dict(id="r2", kind="room", label="普通房间", x=140.0, y=60.0),
                        dict(id="c1", kind="minecart", label="矿车出口", x=140.0, y=140.0)])}}))


CART_ROOM_ITEMS = [
    dict(id="r1", kind="room", label="水晶房", x=60.0, y=60.0),
    dict(id="e1", kind="room", label="矿车", x=140.0, y=140.0),
    dict(id="t1", kind="room", label="矿车中转站", x=60.0, y=140.0),
    # Same spot as the 矿车中转站 room: drawn once.
    dict(id="c1", kind="minecart", label="矿车出口", x=64.0, y=136.0),
]


class SyntheticTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.mask = np.zeros((200, 200), np.uint8)
        self.mask[20:180, 20:180] = 1
        self.path = Path(self.temp.name) / "landmarks.json"
        self.path.write_text(json.dumps(synthetic_doc(self.mask), ensure_ascii=False), encoding="utf-8")
        patcher = mock.patch.object(ml, "DATA_FILE", self.path)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.addCleanup(lambda: setattr(ml, "_DATA_PATH", None))
        ml._DATA_PATH = None
        self.addCleanup(ml.set_preferences, ml.preferences())
        ml.set_preferences(ml.LandmarkPreferences())
        self.pixels = np.zeros((200, 200, 4), np.uint8)
        self.pixels[..., 3] = 255

    def changed(self, out, base=None):
        base = self.pixels if base is None else base
        diff = np.any(np.asarray(out) != base, axis=2)
        ys, xs = np.nonzero(diff)
        return diff, (xs.min(), ys.min(), xs.max(), ys.max()) if len(xs) else None

    def test_preferences_from_settings_default_on(self):
        self.assertEqual(ml.LandmarkPreferences.from_settings({}), ml.LandmarkPreferences(True, True))
        self.assertEqual(ml.LandmarkPreferences.from_settings(dict(mapLandmarkRooms=False)),
                         ml.LandmarkPreferences(False, True))
        self.assertFalse(ml.LandmarkPreferences(False, False).enabled)

    def test_select_keeps_only_important_rooms_and_toggles(self):
        row = ml.floor_entry("106001", "-2")
        items = ml.important_items(row, "-2")
        self.assertEqual([i["label"] for i in items], ["水晶房", "矿车出口"])
        self.assertEqual([i["kind"] for i in ml.select(items, ml.LandmarkPreferences(False, True))], ["minecart"])
        self.assertEqual([i["kind"] for i in ml.select(items, ml.LandmarkPreferences(True, False))], ["room"])
        self.assertEqual(ml.select(items, ml.LandmarkPreferences(False, False)), [])

    def use_cart_rooms(self, floor="-2"):
        doc = synthetic_doc(self.mask, items=CART_ROOM_ITEMS, floor=floor)
        doc["importantRooms"] = {"-1": [], "-2": ["水晶房"], "-3": ["水晶房"]}
        self.path.write_text(json.dumps(doc, ensure_ascii=False), encoding="utf-8")
        ml._DATA_PATH = None

    def test_minecart_rooms_are_minecarts_and_merge_with_exits(self):
        for floor in ("-2", "-3"):
            self.use_cart_rooms(floor)
            items = ml.important_items(ml.floor_entry("106001", floor), floor)
            self.assertEqual([(i["kind"], i["label"], i.get("caption")) for i in items], [
                ("room", "水晶房", None), ("minecart", "矿车", "矿车"), ("minecart", "矿车中转站", "中转站")])
        # An exit away from every minecart room stays.
        row = dict(width=200, height=200, items=CART_ROOM_ITEMS[:3] + [
            dict(id="c2", kind="minecart", label="矿车出口", x=100.0, y=30.0)])
        self.assertEqual([i["label"] for i in ml.important_items(row, "-2") if i["kind"] == "minecart"],
                         ["矿车", "矿车中转站", "矿车出口"])

    def test_b2_b3_minecart_switch_draws_spots_room_switch_does_not(self):
        for floor in ("-2", "-3"):
            self.use_cart_rooms(floor)
            carts = ml.decorate_bgra(self.pixels, "106001", floor, self.mask, prefs=ml.LandmarkPreferences(False, True))
            diff, _ = self.changed(carts)
            self.assertTrue(diff[125:150, 130:150].any(), floor)   # 矿车 evacuation point icon
            self.assertTrue(diff[150:175, 125:155].any(), floor)   # its 「矿车」 caption below
            self.assertTrue(diff[125:150, 50:70].any(), floor)     # 中转站 icon
            self.assertFalse(diff[55:65, 50:70].any(), floor)      # 水晶房 hidden by the room switch
            rgb = carts[120:160, 120:160, [2, 1, 0]].reshape(-1, 3)
            self.assertTrue(np.any(np.all(np.abs(rgb.astype(int) - (255, 211, 111)) < 12, axis=1)))
            both = ml.decorate_bgra(self.pixels, "106001", floor, self.mask, prefs=ml.LandmarkPreferences(True, True))
            np.testing.assert_array_equal(self.changed(both)[0][100:, :], diff[100:, :])  # rooms switch: carts same
            rooms = ml.decorate_bgra(self.pixels, "106001", floor, self.mask, prefs=ml.LandmarkPreferences(True, False))
            self.assertFalse(self.changed(rooms)[0][100:, :].any(), floor)  # minecart switch off: none at all
            self.assertTrue(self.changed(rooms)[0][55:65, 50:70].any(), floor)
            off = ml.LandmarkPreferences(False, False)
            self.assertIs(ml.decorate_bgra(self.pixels, "106001", floor, self.mask, prefs=off), self.pixels)

    def test_editor_items_carry_minecart_captions(self):
        self.use_cart_rooms("-3")
        sha = ml.mask_sha256(self.mask)
        items = ml.editor_items("106001", "-3", dict(width=200, height=200, maskSha256=sha))
        self.assertEqual(items, [dict(kind="room", label="水晶房", x=60.0, y=60.0),
                                 dict(kind="minecart", label="矿车", caption="矿车", x=140.0, y=140.0),
                                 dict(kind="minecart", label="矿车中转站", caption="中转站", x=60.0, y=140.0)])

    def test_binding_requires_same_canvas_and_mask(self):
        sha = ml.mask_sha256(self.mask)
        self.assertIsNotNone(ml.bound_entry("106001", "-2", dict(width=200, height=200, maskSha256=sha)))
        self.assertIsNone(ml.bound_entry("106001", "-2", dict(width=201, height=200, maskSha256=sha)))
        self.assertIsNone(ml.bound_entry("106001", "-2", dict(width=200, height=200, maskSha256="0" * 64)))
        self.assertIsNotNone(ml.bound_entry("106001", "-2", dict(androidLayerMaskSha256="b" * 64)))
        self.assertIsNone(ml.bound_entry("106001", "-2", dict(androidLayerMaskSha256="c" * 64)))
        self.assertIsNone(ml.bound_entry("106001", "-1", dict(width=200, height=200, maskSha256=sha)))
        self.assertIsNone(ml.bound_entry("nightmare-01", "1", dict(width=200, height=200, maskSha256=sha)))

    def test_editor_items_map_native_to_image(self):
        sha = ml.mask_sha256(self.mask)
        half = np.diag([.5, .5, 1.])
        items = ml.editor_items("106001", "-2", dict(width=200, height=200, maskSha256=sha, toImage=half))
        self.assertEqual(items, [dict(kind="room", label="水晶房", x=30.0, y=30.0),
                                 dict(kind="minecart", label="矿车出口", x=70.0, y=70.0)])
        self.assertEqual(ml.editor_items("106001", "-2", dict(width=200, height=200, maskSha256="0" * 64)), [])

    def test_decorate_draws_at_native_positions_and_never_mutates_input(self):
        before = self.pixels.copy()
        out = ml.decorate_bgra(self.pixels, "106001", "-2", self.mask)
        np.testing.assert_array_equal(self.pixels, before)
        self.assertFalse(out.flags.writeable)
        diff, box = self.changed(out)
        self.assertTrue(diff[60, 50:70].any())        # 水晶房 at (60, 60)
        self.assertTrue(diff[125:150, 130:150].any())  # minecart near (140, 140)
        self.assertFalse(diff[55:65, 135:145].any())   # 普通房间 is not an important room
        # Minecart body uses the Android yellow.
        rgb = out[120:160, 120:160, [2, 1, 0]].reshape(-1, 3)
        self.assertTrue(np.any(np.all(np.abs(rgb.astype(int) - (255, 211, 111)) < 12, axis=1)))
        self.assertIs(ml.decorate_bgra(self.pixels, "106001", "-2", self.mask), out)  # cached

    def test_toggles_mismatch_and_s0_leave_pixels_unchanged(self):
        off = ml.LandmarkPreferences(False, False)
        self.assertIs(ml.decorate_bgra(self.pixels, "106001", "-2", self.mask, prefs=off), self.pixels)
        other = self.mask.copy()
        other[0, 0] = 1
        self.assertIs(ml.decorate_bgra(self.pixels, "106001", "-2", other), self.pixels)
        self.assertIs(ml.decorate_bgra(self.pixels, "nightmare-01", "1", self.mask), self.pixels)
        self.assertIs(ml.decorate_bgra(self.pixels, "106001", "-1", self.mask), self.pixels)
        rooms = ml.decorate_bgra(self.pixels, "106001", "-2", self.mask, prefs=ml.LandmarkPreferences(True, False))
        diff, _ = self.changed(rooms)
        self.assertTrue(diff[60, 50:70].any())
        self.assertFalse(diff[120:160, 120:160].any())

    def test_display_matrix_and_route_shift(self):
        small = np.zeros((100, 100, 4), np.uint8)
        small[..., 3] = 255
        out = ml.decorate_bgra(small, "106001", "-2", self.mask, np.diag([2., 2., 1.]),
                               prefs=ml.LandmarkPreferences(True, False))
        diff, box = self.changed(out, small)
        self.assertTrue(diff[30, 25:35].any())  # native (60, 60) -> display (30, 30)
        plain = ml.decorate_bgra(self.pixels, "106001", "-2", self.mask, prefs=ml.LandmarkPreferences(True, False))
        route = ml.decorate_bgra(self.pixels, "106001", "-2", self.mask, route=True,
                                 prefs=ml.LandmarkPreferences(True, False))
        self.assertLess(self.changed(route)[1][1], self.changed(plain)[1][1])  # shifted upwards

    def test_invalid_data_shows_nothing(self):
        self.path.write_text(json.dumps(dict(schema="other")), encoding="utf-8")
        ml._DATA_PATH = None
        self.assertIsNone(ml.load_data())
        self.assertTrue(ml.data_error())
        self.assertIs(ml.decorate_bgra(self.pixels, "106001", "-2", self.mask), self.pixels)
        bad = synthetic_doc(self.mask, items=[dict(id="x", kind="room", label="水晶房", x=500, y=10)])
        self.path.write_text(json.dumps(bad, ensure_ascii=False), encoding="utf-8")
        ml._DATA_PATH = None
        self.assertIsNone(ml.load_data())


class LibraryBindingTests(unittest.TestCase):
    def test_imported_structure_library_binds_mask_cache(self):
        from imported_structure_route_library import ImportedStructureRouteLibrary
        from route_scopes import SCOPES
        import cv2
        with tempfile.TemporaryDirectory() as folder:
            mask = np.zeros((30, 40), np.uint8)
            mask[5:25, 5:35] = 1
            cache = Path(folder) / "m.png"
            cv2.imwrite(str(cache), mask * 255)
            library = ImportedStructureRouteLibrary(None, SCOPES["season-s1-hard"], Path(folder))
            library._maps = {"106001": dict(name="x", floors={"-2": dict(path=cache, size=(40, 30), mask_cache=cache)})}
            binding = library.landmark_binding("106001", "-2")
            self.assertEqual((binding["width"], binding["height"]), (40, 30))
            self.assertEqual(binding["maskSha256"], ml.mask_sha256(mask))
            self.assertIsNone(library.landmark_binding("106001", "-3"))


if __name__ == "__main__":
    unittest.main()
