"""In-app tutorial: chapter data, packaged step images, progress file and the real Tk player."""
import json
from pathlib import Path
import sys
import tempfile
import time
import tkinter as tk
import unittest
from unittest.mock import Mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "app"))

from PIL import Image

import desktop_main_ui
from desktop_main_ui import ACTION_NAMES, DesktopMainUI
from desktop_tutorial import CHAPTERS, CHAPTER_IDS, FAQ, PAGE_ACTIONS, TutorialProgress, TutorialWindow, step_images
from native_ui_layout import MonitorContext, RectPx, UiDpiContext

IMAGE_ROOT = ROOT / "assets" / "tutorial"


class TutorialDataTests(unittest.TestCase):
    def test_chapters_are_unique_and_complete(self):
        self.assertEqual(len(CHAPTER_IDS), len(set(CHAPTER_IDS)))
        for chapter in CHAPTERS:
            self.assertTrue(chapter["title"] and chapter["summary"] and chapter["steps"], chapter["id"])
            self.assertTrue(chapter["page"] is None or chapter["page"] in DesktopMainUI.PAGES, chapter["id"])
            self.assertTrue(chapter["page"] is None or chapter["page"] in PAGE_ACTIONS, chapter["id"])
            for step in chapter["steps"]:
                self.assertTrue(step["title"] and step["text"], step)
                self.assertLessEqual(len(step["tips"]), 3, step["title"])
                self.assertTrue(set(step["keys"]) <= set(ACTION_NAMES), step["title"])
        self.assertTrue(all(question and answers for question, answers in FAQ))

    def test_every_step_image_is_shipped_bounded_and_used(self):
        names = step_images()
        self.assertEqual(len(names), len(set(names)))
        self.assertEqual(set(names), {path.name for path in IMAGE_ROOT.glob("*.webp")})
        total = 0
        for name in names:
            path = IMAGE_ROOT / name
            total += path.stat().st_size
            self.assertLessEqual(path.stat().st_size, 120_000, name)
            with Image.open(path) as image:
                self.assertEqual(image.format, "WEBP")
                self.assertLessEqual(image.width, 1120, name)
        self.assertLessEqual(total, 2_000_000)


class TutorialProgressTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.path = Path(self.folder.name) / "state" / "tutorial-state.json"

    def test_missing_or_corrupt_file_starts_fresh(self):
        self.assertEqual((TutorialProgress(self.path).done, TutorialProgress(self.path).invited), (set(), False))
        self.path.parent.mkdir(parents=True)
        for text in ("{broken", "[]", json.dumps(dict(done="start", invited="yes"))):
            self.path.write_text(text, encoding="utf-8")
            progress = TutorialProgress(self.path)
            self.assertEqual((progress.done, progress.invited), (set(), False), text)

    def test_marks_persist_and_unknown_chapters_are_dropped(self):
        progress = TutorialProgress(self.path)
        self.assertTrue(progress.mark_done("round"))
        self.assertFalse(progress.mark_done("round"))
        self.assertFalse(progress.mark_done("not-a-chapter"))
        progress.mark_invited()
        data = json.loads(self.path.read_text(encoding="utf-8"))
        data["done"].append("removed-chapter")
        self.path.write_text(json.dumps(data), encoding="utf-8")
        again = TutorialProgress(self.path)
        self.assertEqual((again.done, again.invited), ({"round"}, True))
        self.assertFalse(self.path.with_name(self.path.name + ".tmp").exists())

    def test_malformed_done_entries_do_not_break_startup(self):
        self.path.parent.mkdir(parents=True)
        self.path.write_text(json.dumps(dict(done=[{}, [], None, 1, "round", ["start"], "round", "unknown"])), encoding="utf-8")
        self.assertEqual(TutorialProgress(self.path).done, {"round"})

    def test_unwritable_location_never_raises(self):
        blocker = Path(self.folder.name) / "file"
        blocker.write_text("x", encoding="utf-8")
        progress = TutorialProgress(blocker / "tutorial-state.json")
        self.assertTrue(progress.mark_done("start"))
        progress.mark_invited()
        self.assertEqual(progress.done, {"start"})


class FakeAssistant:
    def __init__(self):
        self.text = "F10"
        self.calls = 0
        area = RectPx(0, 0, 1600, 1000)
        self.context = UiDpiContext(MonitorContext("test", area, area, 96), 96)

    def _ui_context_for_window(self, _window, **_kwargs):
        return self.context

    def hotkey_editor_snapshot(self):
        self.calls += 1
        return dict(bindings={"toggle-round": dict(text=self.text), "map": dict(text="G")})


class FakeUI:
    _label = DesktopMainUI._label
    _card = DesktopMainUI._card
    _ghost = DesktopMainUI._ghost
    _clickable = DesktopMainUI._clickable

    def __init__(self, root):
        self.root = root
        self.window = tk.Toplevel(root)
        self.window.withdraw()
        self.assistant = FakeAssistant()
        self._icons = None
        self.show = Mock()
        self.show_page = Mock()


class TutorialWindowTests(unittest.TestCase):
    def setUp(self):
        try:
            self.root = tk.Tk()
        except tk.TclError as error:
            self.skipTest(f"Real Tk unavailable: {error}")
        self.root.withdraw()
        self.addCleanup(self.destroy_root)
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.progress = TutorialProgress(Path(folder.name) / "tutorial-state.json")
        self.ui = FakeUI(self.root)
        self.changed = Mock()
        self.tutorial = TutorialWindow(self.ui, self.progress, IMAGE_ROOT, on_progress=self.changed)

    def destroy_root(self):
        # Each test owns a Tcl interpreter; cancel its queued timers before
        # deleting callback commands and creating the next test interpreter.
        for job in self.root.tk.splitlist(self.root.tk.call("after", "info")):
            self.root.tk.call("after", "cancel", job)
        self.root.destroy()

    def settle(self):
        for _ in range(4):
            self.root.update()
        self.root.after(90, lambda: None)
        self.root.update_idletasks()
        self.root.after(120)
        self.root.update()

    def wait_for_image(self):
        deadline = time.monotonic() + 3
        while self.tutorial._photo is None and time.monotonic() < deadline:
            self.root.update()
            time.sleep(.005)
        self.assertIsNotNone(self.tutorial._photo)

    def test_open_without_chapter_shows_index_and_one_window(self):
        self.tutorial.open()
        window = self.tutorial.window
        self.assertEqual(self.tutorial.index_view.winfo_manager(), "pack")
        self.assertEqual(self.tutorial.chapter_view.winfo_manager(), "")
        self.tutorial.open()
        self.assertIs(self.tutorial.window, window)

    def test_steps_are_bounded_and_finishing_marks_progress(self):
        self.tutorial.open("round")
        last = len(self.tutorial.chapter["steps"]) - 1
        self.assertEqual(str(self.tutorial.prev_button.cget("state")), "disabled")
        self.tutorial.go(-5)
        self.assertEqual(self.tutorial.step, 0)
        self.tutorial.go(99)
        self.assertEqual(self.tutorial.step, last)
        self.assertEqual(self.tutorial.next_button.cget("text"), "看完了")
        self.assertEqual(self.tutorial.page_button.winfo_manager(), "pack")
        self.tutorial._open_page()
        self.ui.show_page.assert_called_once_with("home")
        self.tutorial._next()
        self.assertIn("round", self.progress.done)
        self.changed.assert_called_once()
        self.assertIsNone(self.tutorial.chapter)
        self.assertEqual(self.tutorial._done_labels["round"].winfo_manager(), "pack")
        self.tutorial.open("round")
        self.tutorial.go(last)
        self.tutorial._next()
        self.changed.assert_called_once()  # Re-reading a chapter does not rewrite progress.

    def test_key_hint_follows_current_binding_without_rerendering_text(self):
        self.tutorial.open("round")
        self.assertIn("开始/结束本局 F10", self.tutorial.keys_label.cget("text"))
        self.ui.assistant.text = "Ctrl+F2"
        self.tutorial._sync_keys()
        self.assertIn("开始/结束本局 Ctrl+F2", self.tutorial.keys_label.cget("text"))
        self.tutorial.go(1)
        self.assertIn("游戏地图键 G", self.tutorial.keys_label.cget("text"))
        self.tutorial.go(2)
        self.assertEqual(self.tutorial.keys_label.winfo_manager(), "")
        self.assertEqual(self.tutorial.sketch_label.winfo_manager(), "pack")

    def test_only_current_image_is_held_and_close_releases_it(self):
        self.tutorial.open("start")
        self.tutorial.window.geometry("900x740")
        self.settle()
        self.tutorial._render_image(self.tutorial._image_generation)
        self.assertIsNotNone(self.tutorial._photo)
        self.assertEqual(self.tutorial._source[0], "start-1.webp")
        self.tutorial.go(1)
        self.assertIsNone(self.tutorial._source)
        self.tutorial._render_image(self.tutorial._image_generation)
        self.assertEqual(self.tutorial._source[0], "start-2.webp")
        stale = self.tutorial._image_generation - 1
        self.tutorial._render_image(stale)  # A superseded job never replaces the current step image.
        self.assertEqual(self.tutorial._source[0], "start-2.webp")
        self.tutorial.close()
        self.assertIsNone(self.tutorial._source)
        self.assertIsNone(self.tutorial._photo)
        self.assertEqual(self.tutorial.window.state(), "withdrawn")
        self.tutorial.open()
        self.assertEqual(self.tutorial.chapter["id"], "start")
        self.assertEqual(self.tutorial.step, 1)
        self.settle()
        self.assertIsNotNone(self.tutorial._photo)
        self.assertEqual(self.tutorial._source[0], "start-2.webp")

    def test_missing_image_falls_back_to_text(self):
        tutorial = TutorialWindow(self.ui, self.progress, ROOT / "missing-tutorial-images")
        tutorial.open("keys")
        tutorial.window.geometry("900x740")
        self.settle()
        tutorial._render_image(tutorial._image_generation)
        self.assertIn("配图暂不可用", tutorial.stage_label.cget("text"))
        self.assertIsNone(tutorial._photo)

    def test_escape_returns_to_index_then_closes(self):
        self.tutorial.open("custom")
        self.tutorial._escape()
        self.assertIsNone(self.tutorial.chapter)
        self.tutorial._escape()
        self.assertEqual(self.tutorial.window.state(), "withdrawn")

    def test_150_percent_dpi_navigation_and_reopen(self):
        area = RectPx(0, 0, 1920, 1440)
        self.ui.assistant.context = UiDpiContext(MonitorContext("test-150", area, area, 144), 144)
        self.tutorial.open("round")
        self.settle()
        self.assertAlmostEqual(self.tutorial.display.scale, 1.5)
        self.wait_for_image()
        for chapter in CHAPTERS:
            self.tutorial.show_chapter(chapter["id"])
            for step in range(len(chapter["steps"])):
                self.tutorial.go(step)
                self.root.update()
                next_button = self.tutorial.next_button
                self.assertTrue(next_button.winfo_ismapped())
                self.assertLessEqual(next_button.winfo_rooty() + next_button.winfo_height(),
                                     self.tutorial.window.winfo_rooty() + self.tutorial.window.winfo_height())
        self.tutorial.close()
        self.tutorial.open()
        self.settle()
        self.wait_for_image()

    def test_destroying_owner_drops_window_reference(self):
        self.tutorial.open("start")
        self.ui.window.destroy()
        self.root.update()
        self.assertIsNone(self.tutorial.window)
        self.assertIsNone(self.tutorial._photo)


class TutorialWiringTests(unittest.TestCase):
    def test_help_page_has_no_legacy_operation_images(self):
        source = Path(desktop_main_ui.__file__).read_text(encoding="utf-8")
        self.assertNotIn("HELP_IMAGE_FILES", source)
        self.assertNotIn("查看操作图", source)
        self.assertIn("self._build_tutorial_card(content)", source)


if __name__ == "__main__":
    unittest.main()
