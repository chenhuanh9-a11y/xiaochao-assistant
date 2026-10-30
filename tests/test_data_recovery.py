"""Unreadable user data is set aside or left untouched, never silently overwritten."""
from datetime import datetime
import json
import os
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest import mock
import zipfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "app"))
from custom_maps import GALLERY_SCHEMA, GalleryStore  # noqa: E402
from test_reopen_lifecycle import method  # noqa: E402


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


class SettingsRecoveryTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.load = method("_load_settings")
        self.load.__globals__.update(json=json, os=os, datetime=datetime)
        self.owner = SimpleNamespace(settings_path=Path(self.folder.name) / "ui-settings.json")

    def test_unreadable_settings_are_set_aside(self):
        self.owner.settings_path.write_text('{"hotkey": "G",', "utf-8")
        self.assertEqual(self.load(self.owner), {})
        [backup] = Path(self.folder.name).glob("ui-settings.unreadable-*.json")
        self.assertEqual(backup.read_text("utf-8"), '{"hotkey": "G",')
        self.assertIn(backup.name, self.owner.settings_read_error)
        self.assertFalse(self.owner._settings_write_blocked)
        self.assertFalse(self.owner.settings_path.exists())

    def test_settings_that_cannot_be_moved_block_every_save(self):
        self.owner.settings_path.write_text("[]", "utf-8")
        with mock.patch.object(os, "replace", side_effect=PermissionError("locked")):
            self.assertEqual(self.load(self.owner), {})
        self.assertTrue(self.owner._settings_write_blocked)
        self.assertIn("不会改写", self.owner.settings_read_error)
        save = method("_save_settings")
        self.assertFalse(save(self.owner))
        self.assertEqual(self.owner.settings_path.read_text("utf-8"), "[]")

    def test_readable_settings_load_unchanged(self):
        self.owner.settings_path.write_text('{"gameMode": "difficult"}', "utf-8")
        self.assertEqual(self.load(self.owner), {"gameMode": "difficult"})
        self.assertEqual(self.owner.settings_read_error, "")


class ModeResourceFailureTests(unittest.TestCase):
    def test_truncated_resource_keeps_the_previous_mode(self):
        apply = method("_apply_game_mode_setting")
        apply.__globals__.update(os=os, RESOURCE_ROOT=Path("resources"))
        for failure in (EOFError("No data left in file"), zipfile.BadZipFile("File is not a zip file")):
            with self.subTest(failure=type(failure).__name__):
                owner = SimpleNamespace(
                    game_mode="nightmare", preferred_route_profile_id="wang-qingxin",
                    game_mode_var=mock.Mock(get=lambda: "困难"), active_mode_var=mock.Mock(),
                    end_round=mock.Mock(), _sync_active_mode_ui=mock.Mock(), _log=mock.Mock(),
                    _install_map_mode=mock.Mock(side_effect=failure))
                apply(owner)
                self.assertEqual(owner.game_mode, "nightmare")
                self.assertEqual(owner.mode_resource_error, str(failure))
                owner.game_mode_var.set.assert_called_with("噩梦")
                self.assertIn("修复困难地图资源", owner.active_mode_var.set.call_args.args[0])


if __name__ == "__main__":
    unittest.main()
