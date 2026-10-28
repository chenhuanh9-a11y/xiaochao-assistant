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
        args = ["--legacy", str(legacy), "--game-data", str(game), "--structures", str(structures),
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


if __name__ == "__main__":
    unittest.main(verbosity=2)
