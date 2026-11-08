"""Unreadable user data is set aside or left untouched, never silently overwritten."""
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "app"))
from custom_maps import GALLERY_SCHEMA, GalleryStore  # noqa: E402


class GalleryRecoveryTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.path = Path(self.folder.name) / "custom-galleries.json"

    def backups(self):
        return sorted(Path(self.folder.name).glob("custom-galleries.unreadable-*.json"))

    def test_corrupt_file_is_set_aside_before_the_next_save(self):
        self.path.write_text('{"schema": "xingtu-custom-galleries-v1", "galleries": [', "utf-8")
        store = GalleryStore(self.path)
        [backup] = self.backups()
        self.assertIn(backup.name, store.load_error)
        self.assertTrue(backup.read_text("utf-8").endswith("["))
        store.create("新图库")
        self.assertEqual([name for _id, name, _ro in GalleryStore(self.path).galleries()][1:], ["新图库"])
        self.assertEqual(len(self.backups()), 1)

    def test_bom_gallery_loads_without_recovery_or_rewriting(self):
        original = json.dumps(dict(schema=GALLERY_SCHEMA, futureField={"keep": True},
            galleries=[dict(id="mine", name="保留", maps={}, futureField=7)])).encode("utf-8-sig")
        self.path.write_bytes(original)
        store = GalleryStore(self.path)
        self.assertEqual(store.name("mine"), "保留")
        self.assertEqual(store.load_error, "")
        self.assertEqual(self.path.read_bytes(), original)
        self.assertEqual(self.backups(), [])

    def test_repeated_recovery_in_same_second_preserves_every_backup(self):
        originals = [b"first damaged gallery", b"second damaged gallery"]
        with mock.patch("custom_maps.time.strftime", return_value="20261102-120000"):
            for original in originals:
                self.path.write_bytes(original)
                store = GalleryStore(self.path)
                self.assertTrue(any(path.name in store.load_error for path in self.backups()))
                store.create("新图库")
        self.assertEqual(len(self.backups()), 2)
        self.assertCountEqual([path.read_bytes() for path in self.backups()], originals)

    def test_backup_failure_blocks_saves_and_preserves_original(self):
        original = b"damaged gallery"
        self.path.write_bytes(original)
        with mock.patch("custom_maps.os.replace", side_effect=PermissionError("locked")):
            store = GalleryStore(self.path)
        self.assertIn("无法另存备份", store.load_error)
        with self.assertRaises(OSError):
            store.create("不可保存")
        self.assertEqual(self.path.read_bytes(), original)
        self.assertEqual(self.backups(), [])

    def test_invalid_gallery_list_is_set_aside(self):
        self.path.write_text(json.dumps(dict(schema=GALLERY_SCHEMA, galleries=7)), "utf-8")
        store = GalleryStore(self.path)
        self.assertEqual(len(self.backups()), 1)
        self.assertIn("内容无效", store.load_error)

    def test_unknown_schema_is_never_overwritten(self):
        original = json.dumps(dict(schema="xingtu-custom-galleries-v9", galleries=[dict(id="a", name="新版")]))
        self.path.write_text(original, "utf-8")
        store = GalleryStore(self.path)
        self.assertIn("无法识别", store.load_error)
        with self.assertRaises(OSError):
            store.create("会丢")
        with self.assertRaises(OSError):
            store.import_gallery(dict(schema=GALLERY_SCHEMA, name="导入", maps={}))
        self.assertEqual(self.path.read_text("utf-8"), original)
        self.assertEqual(len(store.galleries()), 1)  # Only the built-in default.

    def test_failed_saves_roll_back_memory(self):
        store = GalleryStore(self.path)
        gallery = store.create("保留")
        with mock.patch.object(GalleryStore, "_save", return_value=False):
            self.assertFalse(store.delete(gallery))
            self.assertFalse(store.rename(gallery, "改名"))
            with self.assertRaisesRegex(OSError, "保存失败"):
                store.create("不会出现")
        self.assertEqual([name for _id, name, _ro in store.galleries()][1:], ["保留"])
        self.assertEqual([name for _id, name, _ro in GalleryStore(self.path).galleries()][1:], ["保留"])

    def test_missing_file_starts_empty_without_error(self):
        store = GalleryStore(self.path)
        self.assertEqual(store.load_error, "")
        store.create("第一个")
        self.assertTrue(self.path.is_file())


if __name__ == "__main__":
    unittest.main()
