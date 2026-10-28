"""Headless checks: actual lifecycle/render methods, native Tk I/O substituted.

Small solid images are I/O fixtures, not replacements for the shipped QR codes.
No screenshot, scanner, game process or payment action runs in these checks.
"""
import ast
import gc
from pathlib import Path
import sys
import tempfile
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import patch
import weakref

from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "app"))
import support_window as support


class Scheduler:
    def __init__(self):
        self.jobs = {}
        self.next_id = 0

    def after_idle(self, callback):
        self.next_id += 1
        self.jobs[self.next_id] = callback
        return self.next_id

    def after_cancel(self, job):
        self.jobs.pop(job, None)

    def drain(self):
        while self.jobs:
            job = next(iter(self.jobs))
            self.jobs.pop(job)()


class Widget:
    def __init__(self):
        self.options = {}
        self.bindings = {}
        self.commands = {}
        self.visible = False
        self.destroy_callback = None

    def configure(self, **kwargs):
        self.options.update(kwargs)

    def winfo_width(self):
        return getattr(self, "width", 400)

    def winfo_height(self):
        return 400

    def grid(self, **kwargs):
        self.visible = True

    def grid_remove(self):
        self.visible = False

    def protocol(self, name, callback):
        self.commands[name] = callback

    def bind(self, event, callback, **kwargs):
        self.bindings[event] = callback

    def destroy(self):
        self.bindings["<Destroy>"](SimpleNamespace(widget=self))


class Photo:
    def __init__(self, image, **kwargs):
        self.size = image.size
        self.thread = threading.get_ident()


class SupportWindowTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.assets = Path(self.temp.name)
        for name, color in (("wechat.png", "black"), ("alipay.png", "blue")):
            with Image.new("RGB", (100, 100), color) as image:
                image.save(self.assets / name)
        self.photo_patch = patch.object(support.ImageTk, "PhotoImage", Photo)
        self.photo_patch.start()
        self.addCleanup(self.photo_patch.stop)

    def panel(self):
        # Substitute widget construction only; all selected/render/close methods are real.
        panel = support.SupportWindow.__new__(support.SupportWindow)
        panel.root, panel.window = Scheduler(), Widget()
        panel.asset_root = self.assets
        panel._on_close = None
        panel._closed, panel._method, panel._scale = False, "wechat", 1
        panel._render_job = panel._render_key = panel._photo = None
        panel._wrap_width = None
        panel._sources = {}
        panel.buttons = {method: Widget() for method in support.METHODS}
        panel.image_host, panel.image_label, panel.status = Widget(), Widget(), Widget()
        panel.intro, panel.note = Widget(), Widget()
        panel.retry_button = Widget()
        panel._bind_actions()
        self.addCleanup(panel.close)
        return panel

    def test_full_image_nearest_fit_preserves_colors_and_does_not_enlarge(self):
        with Image.new("RGB", (100, 100), "white") as image:
            image.paste("black", (20, 20, 80, 80))
            original = image.tobytes()
            for size in (64, 100, 150, 200):
                with support.fit_code(image, size) as fitted:
                    self.assertEqual(fitted.size, (min(size, 100),) * 2)
                    self.assertEqual(fitted.getpixel((0, 0)), (255, 255, 255))
                    self.assertLessEqual(len(fitted.getcolors()), 2)
            self.assertEqual(image.tobytes(), original)

    def test_switch_cancels_previous_load_and_only_publishes_selected_image(self):
        panel = self.panel()
        panel.retry()
        panel.select("alipay")
        self.assertEqual(len(panel.root.jobs), 1)
        panel.root.drain()
        self.assertEqual(set(panel._sources), {"alipay"})
        self.assertEqual(panel.status.options["text"], "使用支付宝扫一扫")
        self.assertEqual(panel._photo.thread, threading.get_ident())

    def test_escape_cancels_pending_load_and_close_callback_runs_once(self):
        panel = self.panel()
        closed = []
        panel._on_close = lambda: closed.append(True)
        panel.retry()
        self.assertEqual(panel.window.bindings["<Escape>"](), "break")
        panel.close()
        panel.root.drain()
        self.assertTrue(panel.closed)
        self.assertEqual(closed, [True])
        self.assertEqual(panel._sources, {})

    def test_titlebar_close_releases_images_and_sources(self):
        panel = self.panel()
        panel.retry()
        panel.root.drain()
        photo = weakref.ref(panel._photo)
        panel.window.commands["WM_DELETE_WINDOW"]()
        panel.image_label.options.clear()  # The destroyed native label owns no image.
        gc.collect()
        self.assertIsNone(photo())
        self.assertEqual(panel._sources, {})
        self.assertFalse(panel.root.jobs)

    def test_missing_file_clears_old_image_then_retry_recovers(self):
        panel = self.panel()
        panel.retry()
        panel.root.drain()
        (self.assets / "alipay.png").unlink()
        panel.select("alipay")
        self.assertIsNone(panel._photo)
        panel.root.drain()
        self.assertEqual(panel.image_label.options["image"], "")
        self.assertTrue(panel.retry_button.visible)
        with Image.new("RGB", (80, 80), "blue") as image:
            image.save(self.assets / "alipay.png")
        panel.retry()
        panel.root.drain()
        self.assertIsNotNone(panel._photo)
        self.assertFalse(panel.retry_button.visible)

    def test_repeated_resize_has_one_job_and_releases_replaced_photo(self):
        panel = self.panel()
        panel.retry()
        panel.root.drain()
        previous = weakref.ref(panel._photo)
        for scale in (1.5, 2, 1):
            panel._scale = scale
            panel._queue_render(force=True)
        self.assertEqual(len(panel.root.jobs), 1)
        panel.root.drain()
        gc.collect()
        self.assertIsNone(previous())
        self.assertEqual(len(panel._sources), 1)

    def test_child_destroy_event_does_not_close_parent(self):
        panel = self.panel()
        panel._destroy(SimpleNamespace(widget=Widget()))
        self.assertFalse(panel.closed)

    def test_same_dpi_window_resize_rewraps_copy_inside_body(self):
        panel = self.panel()
        panel.window.width = 320
        panel.window.bindings["<Configure>"](SimpleNamespace(widget=panel.window))
        for label in (panel.intro, panel.status, panel.note):
            self.assertEqual(label.options["wraplength"], 272)
        panel.window.width = 640
        panel._scale = 2
        panel.window.bindings["<Configure>"](SimpleNamespace(widget=panel.window))
        self.assertEqual(panel.intro.options["wraplength"], 544)

    def test_actual_desktop_callback_reuses_window_and_reopens_after_close(self):
        tree = ast.parse((ROOT / "app/desktop_main_ui.py").read_text("utf-8-sig"))
        cls = next(node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == "DesktopMainUI")
        method = next(node for node in cls.body if isinstance(node, ast.FunctionDef) and node.name == "_open_support_window")
        namespace = dict(open_support_window=support.open_support_window)
        exec(compile(ast.Module(body=[method], type_ignores=[]), "desktop_main_ui.py", "exec"), namespace)
        created = []

        class NativeWindowIO:
            def __init__(self, *args, **kwargs):
                self.closed, self.shown = False, 0
                created.append(self)

            def show(self):
                self.shown += 1

        owner = SimpleNamespace(window=object(), assistant=object(), _support_window=None)
        with patch.object(support, "SupportWindow", NativeWindowIO):
            for _ in range(2):
                namespace["_open_support_window"](owner)
            self.assertEqual(len(created), 1)
            self.assertEqual(created[0].shown, 2)
            created[0].closed = True
            namespace["_open_support_window"](owner)
            self.assertEqual(len(created), 2)


if __name__ == "__main__":
    unittest.main()
