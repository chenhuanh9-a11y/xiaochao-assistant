import hashlib
import json
from pathlib import Path
import sys
import tempfile
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import cv2
import numpy as np
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "app"))
from base_map_display import BaseMapDisplay
from custom_maps import CustomMapLibrary, SCHEMA, mask_sha256


class BaseMapDisplayTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.mask = np.ones((12, 16), np.uint8)
        self.ref = SimpleNamespace(shape=self.mask.shape, clean_mask=self.mask,
                                   path=self.root / "structure.npz")
        art = np.zeros((5, 6, 4), np.uint8)
        art[1:4, 1:5] = (240, 80, 20, 255)
        Image.fromarray(art).save(self.root / "base.png")
        (self.root / "topology.json").write_text("{}")
        self.matrix = np.array([[1, 0, 4], [0, 1, 3], [0, 0, 1]], float)
        sha = lambda name: hashlib.sha256((self.root / name).read_bytes()).hexdigest()
        self.row = dict(image="base.png", imageSha256=sha("base.png"), imageSize=[6, 5],
                        matrixImageToNative=self.matrix.tolist(), nativeShape=[12, 16],
                        topology="topology.json", topologySha256=sha("topology.json"),
                        maskSha256=mask_sha256(self.mask), entrances=[])
        self.manifest = dict(schema=SCHEMA, version="fixture1", maps=[dict(
            mapId="nightmare-layout-104026", name="fixture", floors={"1": self.row})])
        self.save()
        self.provider = BaseMapDisplay()

    def save(self):
        (self.root / "manifest.json").write_text(json.dumps(self.manifest), encoding="utf-8")
        self.library = CustomMapLibrary(self.root)

    def resolve(self, **kwargs):
        return self.provider.resolve(kwargs.get("library", self.library),
            kwargs.get("map_id", "nightmare-layout-104026"), kwargs.get("floor", "1"),
            kwargs.get("reference", self.ref))

    def test_authoritative_translation_not_size_ratio_and_no_route(self):
        result = self.resolve()
        self.assertEqual(result.reason, "")
        layer = result.layer
        art = np.asarray(Image.open(self.root / "base.png").convert("RGBA"))
        expected = cv2.warpAffine(art, self.matrix[:2], (16, 12))[:, :, [2, 1, 0, 3]]
        np.testing.assert_array_equal(layer["routeSource"], expected)
        np.testing.assert_array_equal(layer["matrixDisplayToReference"], np.eye(3))
        self.assertEqual(layer["displayKind"], "base-map")
        self.assertFalse(layer["routeSource"].flags.writeable)

    def test_both_mode_missing_assets_are_capability_failure(self):
        for map_id in ("nightmare-missing", "difficult-01"):
            self.assertEqual(self.resolve(map_id=map_id).reason, "base-map-unavailable")
        self.assertEqual(self.resolve(floor="2").reason, "base-map-floor-unavailable")

    def test_rejects_wrong_structure_even_same_size(self):
        self.mask[0, 0] = 0
        self.assertIsNone(self.resolve().layer)

    def test_rejects_invalid_affines_and_dimensions(self):
        for matrix in ([[1, 0, 0], [0, 0, 0], [0, 0, 1]],
                       [[1, 0, 0], [0, 1, 0], [0.1, 0, 1]],
                       [[float("nan"), 0, 0], [0, 1, 0], [0, 0, 1]]):
            self.row["matrixImageToNative"] = matrix
            self.save()
            self.assertIsNone(self.resolve().layer)
        self.row["matrixImageToNative"] = self.matrix.tolist()
        self.row["imageSize"] = [7, 5]
        self.save()
        self.assertIsNone(self.resolve().layer)

    def test_warm_cache_no_repeat_render_bounded(self):
        from base_map_display import render_game_layer
        with patch("base_map_display.render_game_layer", wraps=render_game_layer) as render:
            first = self.resolve().layer["routeSource"]
            self.assertIs(self.resolve().layer["routeSource"], first)
            self.assertEqual(render.call_count, 1)
        for _ in range(9):
            self.ref = SimpleNamespace(shape=self.mask.shape, clean_mask=self.mask.copy(), path=self.ref.path)
            self.assertIsNotNone(self.resolve().layer)
        self.assertLessEqual(len(self.provider._cache), 6)

    def test_deletion_and_reimport_do_not_retain_old_pixels(self):
        first = self.resolve().layer["routeSource"]
        data = (self.root / "base.png").read_bytes()
        (self.root / "base.png").unlink()
        self.assertEqual(self.resolve().reason, "base-map-file-missing")
        self.assertIsNone(self.provider.peek(self.library, "nightmare-layout-104026", "1", self.ref).layer)
        (self.root / "base.png").write_bytes(data)
        second = self.resolve().layer["routeSource"]
        self.assertIsNot(first, second)
        np.testing.assert_array_equal(first, second)

    def test_changed_file_or_manifest_needs_matching_context(self):
        self.assertIsNotNone(self.resolve().layer)
        Image.new("RGBA", (6, 5), "blue").save(self.root / "base.png")
        self.assertIsNone(self.resolve().layer)
        self.row["imageSha256"] = hashlib.sha256((self.root / "base.png").read_bytes()).hexdigest()
        old = self.library
        self.save()
        self.assertIsNone(self.resolve(library=old).layer)
        self.assertIsNotNone(self.resolve().layer)

    def test_mutable_mask_after_cache_cannot_reuse_authorization(self):
        self.assertIsNotNone(self.resolve().layer)
        self.mask[0, 0] = 0
        self.assertIsNone(self.resolve().layer)

    def test_render_change_rejected_and_exception_not_published(self):
        from base_map_display import render_game_layer
        def changing(*args):
            pixels = render_game_layer(*args)
            (self.root / "topology.json").write_text("{ } ")
            return pixels
        with patch("base_map_display.render_game_layer", side_effect=changing):
            self.assertIsNone(self.resolve().layer)
        self.assertEqual(len(self.provider._cache), 0)

    def test_ui_peek_does_not_access_files_hash_or_render(self):
        layer = self.resolve().layer
        with patch("base_map_display._stamp", side_effect=AssertionError("stat")), \
             patch("base_map_display.mask_sha256", side_effect=AssertionError("hash")), \
             patch("base_map_display.render_game_layer", side_effect=AssertionError("render")):
            self.assertIs(self.provider.peek(self.library, "nightmare-layout-104026", "1", self.ref).layer["routeSource"], layer["routeSource"])
            self.assertIsNone(self.provider.peek(self.library, "nightmare-layout-104026", "2", self.ref).layer)
        self.provider.clear()
        self.assertIsNone(self.provider.peek(self.library, "nightmare-layout-104026", "1", self.ref).layer)

    def test_slow_worker_never_blocks_peek_clear_or_publishes_after_clear(self):
        from base_map_display import render_game_layer
        entered, release, ui_done = threading.Event(), threading.Event(), threading.Event()
        results = []
        def slow(*args):
            entered.set()
            if not release.wait(5):
                raise RuntimeError("fixture timed out")
            return render_game_layer(*args)
        def ui():
            self.provider.peek(self.library, "nightmare-layout-104026", "1", self.ref)
            self.provider.clear()
            ui_done.set()
        with patch("base_map_display.render_game_layer", side_effect=slow):
            worker = threading.Thread(target=lambda: results.append(self.resolve()))
            worker.start()
            self.assertTrue(entered.wait(2))
            ui_thread = threading.Thread(target=ui)
            ui_thread.start()
            try:
                self.assertTrue(ui_done.wait(1), "UI must not wait behind worker decode")
            finally:
                release.set()
                worker.join(5)
                ui_thread.join(5)
        self.assertFalse(worker.is_alive())
        self.assertEqual(results[0].reason, "base-map-stale-preparation")
        self.assertIsNone(self.provider.peek(self.library, "nightmare-layout-104026", "1", self.ref).layer)

    def test_late_failure_cannot_evict_newer_success(self):
        self.resolve()
        original = self.provider._resolve
        entered, release = threading.Event(), threading.Event()
        results = []
        def delayed_failure(*args):
            if threading.current_thread().name == "old-validator":
                entered.set()
                release.wait(3)
                raise FileNotFoundError("old observation")
            return original(*args)
        with patch.object(self.provider, "_resolve", side_effect=delayed_failure):
            worker = threading.Thread(target=lambda: results.append(self.resolve()), name="old-validator")
            worker.start()
            self.assertTrue(entered.wait(2))
            # Same context, new file timestamp forces actual successful publication.
            data = (self.root / "base.png").read_bytes()
            (self.root / "base.png").write_bytes(data)
            newer = self.resolve().layer
            release.set()
            worker.join(3)
        self.assertEqual(results[0].reason, "base-map-file-missing")
        self.assertIs(self.provider.peek(self.library, "nightmare-layout-104026", "1", self.ref).layer["routeSource"], newer["routeSource"])


if __name__ == "__main__":
    unittest.main()
