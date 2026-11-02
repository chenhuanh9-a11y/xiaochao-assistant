"""Small synthetic checks of the canonical footprint binding; no real assets."""
import hashlib
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

import cv2
import numpy as np
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parent))
import build_custom_map_package as builder


class MaskBindingTests(unittest.TestCase):
    def test_binary_c_order_hash_is_independent_of_mask_encoding(self):
        binary = np.array([[0, 1, 1], [1, 0, 1]], np.uint8)
        expected = hashlib.sha256(binary.tobytes(order="C")).hexdigest()
        for mask in (binary, binary.astype(bool), binary * 255, np.asfortranarray(binary)):
            self.assertEqual(builder.mask_sha256(mask), expected)
        self.assertEqual(builder.checked_mask_sha256(
            dict(shape=[2, 3], maskSha256=expected), binary, "m", "1"), expected)

    def test_same_shape_other_footprint_and_missing_hash_fail_closed(self):
        native = np.eye(8, dtype=np.uint8)
        other = np.fliplr(native)
        for topology in (dict(shape=[8, 8], maskSha256=builder.mask_sha256(other)),
                         dict(shape=[8, 8])):
            with self.subTest(topology=topology), self.assertRaisesRegex(ValueError, "mask hash mismatch"):
                builder.checked_mask_sha256(topology, native, "m", "1")
        with self.assertRaisesRegex(ValueError, "shape mismatch"):
            builder.checked_mask_sha256(dict(shape=[4, 16], maskSha256=builder.mask_sha256(native)),
                                       native, "m", "1")

    def _inputs(self, root):
        legacy, game, structures = (root / part for part in ("legacy", "game", "structures"))
        for path in (legacy / "blank/t1", legacy / "navigation", game / "maps/layout", structures):
            path.mkdir(parents=True)
        rgba = np.full((32, 40, 4), 255, np.uint8)
        image = cv2.imencode(".png", rgba)[1].tobytes()
        (legacy / "blank/t1/1.png").write_bytes(image)
        (legacy / "exit-icon.png").write_bytes(image)
        (legacy / "navigation/1.json").write_text("{}", "utf-8")
        (legacy / "catalog.json").write_text(json.dumps(dict(templates=[dict(
            id="t1", number=1, name="Fixture", floors=[dict(id="1", navigation="navigation/1.json")])])), "utf-8")
        (game / "index.json").write_text(json.dumps(dict(sourceVersion="fixture")), "utf-8")
        native = np.ones((32, 40), np.uint8)
        native[:4, :4] = 0
        np.savez_compressed(structures / "floor.npz", clean_mask=native)
        (structures / "structure-index.json").write_text(json.dumps(dict(entries=[dict(
            mode="nightmare", mapId="nightmare-03", floor="1", resourcePath="floor.npz")])), "utf-8")
        topology = dict(mapId="nightmare-03", floor="1", shape=list(native.shape),
                        maskSha256=builder.mask_sha256(native), points=[[10, 10]], pointRooms=["r"], links=[])
        topology_path = game / "maps/layout/topology-1.json"
        topology_path.write_text(json.dumps(topology), "utf-8")
        patches = root / "empty-patches.json"
        patches.write_text(json.dumps(dict(schema=builder.PATCH_SCHEMA, kind="room-grid",
                                          gameDataVersion="fixture", links=[])), "utf-8")
        args = ["--road-patches", str(patches), "--legacy", str(legacy), "--game-data", str(game), "--structures", str(structures),
                "--out", str(root / "out"), "--version", "fixture"]
        return args, topology_path, topology, native

    def test_builder_persists_verified_mask_hash_without_changing_topology(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            args, path, topology, native = self._inputs(root)
            original = path.read_bytes()
            with patch.object(builder, "register", return_value=(np.eye(3), 1.0)):
                self.assertEqual(builder.main(args), 0)
            manifest = json.loads((root / "out/manifest.json").read_text("utf-8"))
            floor = manifest["maps"][0]["floors"]["1"]
            self.assertEqual(floor["maskSha256"], builder.mask_sha256(native))
            # The package keeps only the road graph and its binding; the source stays untouched.
            packaged = json.loads((root / "out" / floor["topology"]).read_text("utf-8"))
            self.assertEqual(packaged, {key: topology[key] for key in builder.TOPOLOGY_KEYS})
            self.assertTrue(floor["image"].endswith(".webp"))
            self.assertEqual(path.read_bytes(), original)

    def test_builder_rejects_same_shape_wrong_mask_before_registration(self):
        with tempfile.TemporaryDirectory() as folder:
            args, path, topology, native = self._inputs(Path(folder))
            topology["maskSha256"] = builder.mask_sha256(np.fliplr(native))
            path.write_text(json.dumps(topology), "utf-8")
            with patch.object(builder, "register") as register:
                with self.assertRaisesRegex(ValueError, "mask hash mismatch"):
                    builder.main(args)
                register.assert_not_called()


class ArtAndIncrementTests(unittest.TestCase):
    """Redrawn art replaces the display image; the legacy geometry stays as the template."""

    def test_white_background_becomes_transparent_but_thin_light_strokes_stay(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "art.png"
            rgb = np.full((60, 80, 3), 255, np.uint8)
            rgb[10:50, 10:70] = (120, 150, 200)
            rgb[29:31, 10:70] = (250, 246, 236)  # Ivory inner wall stroke inside the map.
            cv2.imwrite(str(path), rgb[:, :, ::-1])
            rgba, solid = builder.read_art(path)
            self.assertEqual(rgba[0, 0, 3], 0)
            self.assertEqual(rgba[30, 40, 3], 255)
            self.assertTrue(solid[30, 40])

    def test_points_move_between_images_through_native(self):
        a = np.array([[2, 0, 10], [0, 2, 20], [0, 0, 1]], float)
        b = np.array([[4, 0, 0], [0, 4, 0], [0, 0, 1]], float)
        self.assertEqual(builder.through(a, b, 5, 5), (5.0, 7.5))

    def test_builder_uses_art_keeps_template_and_adds_increment_maps(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            args, path, topology, native = MaskBindingTests._inputs(None, root)
            topology["sourceLayout"] = "104005"
            path.write_text(json.dumps(topology), "utf-8")
            game = root / "game"
            (game / "renders/104005").mkdir(parents=True)
            render = np.zeros((*native.shape, 4), np.uint8)
            render[native > 0] = 255
            Image.fromarray(render, "RGBA").save(game / "renders/104005/1F.png")
            art = root / "art"
            for layout in ("104005", "104026"):
                (art / layout).mkdir(parents=True)
                drawn = np.full((16, 20, 3), 255, np.uint8)
                drawn[2:, 2:] = (120, 150, 200)
                Image.fromarray(drawn).save(art / layout / "1F.png")
            increment = root / "increment"
            (increment / "topology").mkdir(parents=True)
            (increment / "maps/104026/1").mkdir(parents=True)
            np.savez_compressed(increment / "maps/104026/1/structure.npz", shared_mask=native)
            extra = dict(topology, mapId="nightmare-layout-104026", sourceLayout="104026")
            (increment / "topology/1.json").write_text(json.dumps(extra), "utf-8")
            (increment / "editor-manifest.json").write_text(json.dumps(dict(
                schema=builder.SCHEMA, version="inc", sourceGameVersion="2.104.3085069", maps=[dict(
                    mapId="nightmare-layout-104026", templateId="nightmare-layout-104026", templateNumber=126,
                    name="新增", sourceLayout="104026", stairs=[dict(id="s", cost=1, bidirectional=True, semantics="x", **{
                        "from": dict(floor="1", x=10, y=10), "to": dict(floor="1", x=10, y=10)})],
                    floors={"1": dict(topology="topology/1.json", topologySha256=builder.sha256(increment / "topology/1.json"),
                                      maskSha256=builder.mask_sha256(native), entrances=[dict(label="正门", x=20, y=20)])})])),
                "utf-8")
            legacy_matrix = np.diag([2., 2., 1.])
            art_matrix = np.diag([2., 2., 1.])
            with patch.object(builder, "register", side_effect=[(legacy_matrix, .9), (art_matrix, .95), (art_matrix, .95)]):
                self.assertEqual(builder.main(args + ["--art", str(art), "--increment", str(increment)]), 0)
            manifest = json.loads((root / "out/manifest.json").read_text("utf-8"))
            self.assertEqual(manifest["art"]["includesExits"], True)
            self.assertEqual(manifest["sources"]["increment"]["gameVersion"], "2.104.3085069")
            first, added = manifest["maps"]
            floor = first["floors"]["1"]
            self.assertEqual(floor["imageSize"], [20, 16])
            self.assertEqual(floor["template"]["imageSize"], [40, 32])
            self.assertEqual(floor["template"]["matrixImageToNative"][0][0], 2.0)
            self.assertEqual(added["mapId"], "nightmare-layout-104026")
            self.assertEqual(added["stairs"][0], dict(id="s", cost=1, bidirectional=True, **{
                "from": dict(floor="1", x=10, y=10), "to": dict(floor="1", x=10, y=10)}))
            self.assertEqual(added["floors"]["1"]["entrances"], [dict(label="正门", x=10.0, y=10.0)])
            # The Android route editor template for 104026 is the native canvas at 0.7.
            self.assertEqual((added["templateId"], added["templateNumber"]), ("nightmare-layout-28", 28))
            self.assertEqual(added["floors"]["1"]["template"]["imageSize"], [28, 22])
            self.assertAlmostEqual(added["floors"]["1"]["template"]["matrixImageToNative"][0][0], 1 / .7, places=5)
            with Image.open(root / "out" / floor["image"]) as image:
                self.assertEqual((image.mode, image.getpixel((0, 0))[3]), ("RGBA", 0))


class RoadPatchTests(unittest.TestCase):
    """Confirmed links the game data lacks: checked by index, position and room."""

    def topology(self):
        return dict(points=[[630.0, 270.0], [690.0, 270.0], [750.0, 270.0]], pointRooms=["101:3", "101:3", "7:3"],
                    links=[[1, 2, "source-door"]])

    def link(self, **change):
        link = dict(mapId="m", floor="2", room="101:3", a=dict(index=0, x=630, y=270), b=dict(index=1, x=690, y=270))
        link.update(change)
        return link

    def test_shipped_patch_file_is_well_formed(self):
        document, grouped = builder.load_road_patches(builder.ROAD_PATCHES)
        self.assertEqual((document["gameDataVersion"], document["date"]), ("2.104.3059230", "2026-10-28"))
        self.assertEqual(len(document["links"]), 26)
        self.assertEqual(len(grouped), 13)
        for link in document["links"]:
            self.assertEqual(sorted((abs(link["a"]["x"] - link["b"]["x"]), abs(link["a"]["y"] - link["b"]["y"]))),
                             [0, builder.TILE])

    def test_missing_link_is_added_once_and_reported_when_the_game_has_it(self):
        data = self.topology()
        self.assertEqual(builder.apply_road_patches(data, [self.link()], "m/2"), (1, 0))
        self.assertEqual(data["links"][-1], [0, 1, "room-grid"])
        self.assertEqual(builder.apply_road_patches(data, [self.link()], "m/2"), (0, 1))
        self.assertEqual(len(data["links"]), 2)

    def test_tampered_patches_fail_the_build(self):
        for change, message in ((dict(a=dict(index=0, x=631, y=270)), "expected"),
                                (dict(room="7:3"), "in room"),
                                (dict(b=dict(index=9, x=690, y=270)), "does not exist"),
                                (dict(b=dict(index=2, x=750, y=270), room="101:3"), "in room")):
            with self.subTest(change=change), self.assertRaisesRegex(ValueError, message):
                builder.apply_road_patches(self.topology(), [self.link(**change)], "m/2")
        data = self.topology()
        data["pointRooms"][2] = "101:3"
        with self.assertRaisesRegex(ValueError, "not adjacent"):
            builder.apply_road_patches(data, [self.link(b=dict(index=2, x=750, y=270))], "m/2")

    def test_unknown_patch_map_fails_before_render_or_output(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            args, _path, _topology, _native = MaskBindingTests._inputs(None, root)
            patches = root / "unknown-patches.json"
            patches.write_text(json.dumps(dict(schema=builder.PATCH_SCHEMA, kind="room-grid",
                gameDataVersion="fixture", links=[self.link(mapId="unknown-map")])), "utf-8")
            with patch.object(builder, "register", return_value=(np.eye(3), 1.0)) as register, self.assertRaisesRegex(ValueError, "unknown-map/2"):
                builder.main(args + ["--road-patches", str(patches)])
            register.assert_not_called()
            self.assertFalse((root / "out").exists())

    def test_builder_applies_patches_and_records_them(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            args, path, topology, native = MaskBindingTests._inputs(None, root)
            topology.update(points=[[10, 10], [10, 70]], pointRooms=["r", "r"])
            path.write_text(json.dumps(topology), "utf-8")
            patches = root / "patches.json"
            document = dict(schema=builder.PATCH_SCHEMA, kind="room-grid", gameDataVersion="fixture", links=[dict(
                mapId="nightmare-03", floor="1", room="r", a=dict(index=0, x=10, y=10), b=dict(index=1, x=10, y=70))])
            patches.write_text(json.dumps(document), "utf-8")
            with patch.object(builder, "register", return_value=(np.eye(3), 1.0)):
                self.assertEqual(builder.main(args + ["--road-patches", str(patches)]), 0)
            manifest = json.loads((root / "out/manifest.json").read_text("utf-8"))
            self.assertEqual(manifest["sources"]["roadPatches"]["added"], 1)
            packaged = json.loads((root / "out/topology/nightmare-03/1.json").read_text("utf-8"))
            self.assertEqual(packaged["links"], [[0, 1, "room-grid"]])
            self.assertEqual(json.loads(path.read_text("utf-8"))["links"], [], "source game data untouched")
            # A patch for a floor the build does not have is an error, not a silent skip.
            document["links"][0]["floor"] = "2"
            patches.write_text(json.dumps(document), "utf-8")
            with patch.object(builder, "register", return_value=(np.eye(3), 1.0)), \
                    self.assertRaisesRegex(ValueError, "not built"):
                builder.main(args[:-4] + ["--out", str(root / "out2"), "--version", "x", "--road-patches", str(patches)])


if __name__ == "__main__":
    unittest.main(verbosity=2)
