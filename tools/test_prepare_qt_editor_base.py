"""Bounded safety/closure checks; synthetic art is never a product resource."""
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from PIL import Image

import prepare_qt_editor_base as prepare


class PrepareEditorTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.source = self.root / "editor"
        self.source.mkdir()
        self.runtime = self.root / "runtime"
        (self.runtime / "assets/suju").mkdir(parents=True)
        (self.runtime / "assets/suju/private.png").write_bytes(b"author-display-not-for-copy")
        (self.runtime / "typed.json").write_text("{}")
        Image.new("RGBA", (2, 2)).save(self.source / "floor.png")
        Image.new("RGBA", (1, 1)).save(self.source / "exit-icon.png")
        topology = dict(mapId="fixture", floor="1", shape=[2, 2], maskSha256="a" * 64,
                        points=[[0, 0]], pointRooms=["r"], links=[])
        prepare.write(self.source / "floor.json", topology)
        floor = dict(image="floor.png", imageSha256=prepare.digest(self.source / "floor.png"),
                     topology="floor.json", topologySha256=prepare.digest(self.source / "floor.json"),
                     imageSize=[2, 2], nativeShape=[2, 2], maskSha256="a" * 64,
                     matrixImageToNative=[[1, 0, 0], [0, 1, 0], [0, 0, 1]])
        self.manifest = dict(schema="xingtu-custom-maps-v1", version="fixture", maps=[dict(mapId="fixture", floors={"1": floor})],
                             sources=dict(exitIconSha256=prepare.digest(self.source / "exit-icon.png")))
        self.save()

    def save(self):
        prepare.write(self.source / "manifest.json", self.manifest)
        self.expected = prepare.digest(self.source / "manifest.json")

    def run_prepare(self, target=None):
        with patch.object(prepare, "ROOT", self.root):
            return prepare.prepare(self.source, self.runtime, target or self.root / "work/next", self.expected, "fixture")

    def test_new_output_closure_and_author_omission(self):
        # Opening omitted author bytes would fail this test even before copy.
        original = prepare.digest
        def no_author_digest(path):
            self.assertNotIn("suju", Path(path).parts)
            return original(path)
        with patch.object(prepare, "digest", no_author_digest):
            receipt = self.run_prepare()
        target = self.root / "work/next"
        inventory = prepare.read(target / "resource-inventory.json")["files"]
        self.assertEqual(receipt["omittedAuthorFiles"], 1)
        self.assertFalse((target / "_internal/assets/suju").exists())
        self.assertEqual(set(inventory), {p.relative_to(target / "_internal").as_posix() for p in (target / "_internal").rglob("*") if p.is_file()})
        for name, row in inventory.items():
            self.assertEqual(row["sha256"], original(target / "_internal" / name))

    def test_existing_output_is_never_changed(self):
        target = self.root / "work/next"
        target.mkdir(parents=True)
        (target / "keep").write_text("unchanged")
        with self.assertRaisesRegex(ValueError, "must not exist"):
            self.run_prepare(target)
        self.assertEqual((target / "keep").read_text(), "unchanged")

    def test_manifest_and_traversal_fail_before_output(self):
        self.manifest["maps"][0]["floors"]["1"]["image"] = "../outside.png"
        self.save()
        with self.assertRaises(ValueError):
            self.run_prepare()
        self.assertFalse((self.root / "work/next").exists())
        self.expected = "0" * 64
        with self.assertRaisesRegex(ValueError, "authorized input hash"):
            self.run_prepare()

    def test_topology_binding_fails_before_output(self):
        self.manifest["maps"][0]["floors"]["1"]["nativeShape"] = [3, 3]
        self.save()
        with self.assertRaisesRegex(ValueError, "topology binding mismatch"):
            self.run_prepare()


if __name__ == "__main__":
    unittest.main()
