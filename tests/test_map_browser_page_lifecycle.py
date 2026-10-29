"""Real withdrawn Tk + synthetic local resources; no game or desktop input.

--baseline REV loads only the older browser module for red/green comparison.
No full desktop timing or visible-pixel latency claims are made here.
"""
from concurrent.futures import Future
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import time
import tkinter as tk
from types import SimpleNamespace, ModuleType
import unittest
from unittest.mock import patch

import numpy as np
from PIL import Image

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / 'app'))
if '--baseline' in sys.argv:
    i = sys.argv.index('--baseline')
    revision = sys.argv[i + 1]
    source = subprocess.check_output(['git', 'show', f'{revision}:app/map_resource_browser.py'],
                                     cwd=REPO, text=True, encoding='utf-8')
    baseline = ModuleType('map_resource_browser')
    exec(compile(source, f'{revision}:app/map_resource_browser.py', 'exec'), baseline.__dict__)
    sys.modules['map_resource_browser'] = baseline
    del sys.argv[i:i + 2]
import map_resource_browser as browser_module
from map_resource_overview import StructurePreview


class ControlledExecutor:
    def __init__(self):
        self.futures = []

    def submit(self, *args):
        future = Future()
        future.set_running_or_notify_cancel()
        self.futures.append(future)
        return future

    def shutdown(self, **kwargs):
        pass


class MapBrowserLifecycle(unittest.TestCase):
    def setUp(self):
        try:
            self.root = tk.Tk()
        except tk.TclError as error:
            self.skipTest(f'Tk unavailable: {error}')
        self.root.withdraw()
        self.folder = tempfile.TemporaryDirectory()
        self.path = Path(self.folder.name) / 'author.png'
        Image.new('RGB', (32, 24), '#123456').save(self.path)
        self.structure = Path(self.folder.name) / 'structure.npz'
        np.savez_compressed(self.structure, shared_mask=np.eye(24, dtype=np.uint8),
                            other_mask=np.ones((24, 24), dtype=np.uint8))
        maps = [SimpleNamespace(id=f'map-{i}', name=f'Map {i}', layers={'1': self.path}) for i in range(3)]
        entries = {('shared-structure', item.id, 'variant-01', '1'):
                   {'resourcePath': self.structure.name, 'sourceVersion': 'fixture-v1'} for item in maps}
        native = SimpleNamespace(resources=SimpleNamespace(entries=entries), legacy_bindings={},
                                 local=lambda name: Path(self.folder.name) / name)
        pack = SimpleNamespace(mode='nightmare', default_entry_floor='1', maps=maps,
                               route_profiles={item.id: 'author' for item in maps}, display_context=native)
        self.assistant = SimpleNamespace(pack=pack, author_route_catalog=None,
                                         _mode_route_options=lambda: [('author', 'Author')])
        self.browser = browser_module.MapResourceBrowser(self.root, self.assistant)
        self.browser.pack(fill='both', expand=True)

    def tearDown(self):
        executor = self.browser._executor
        self.browser.destroy()
        executor.shutdown(wait=True, cancel_futures=True)
        self.root.destroy()
        self.folder.cleanup()

    def drain(self):
        deadline = time.monotonic() + 5
        while self.browser._preview_futures or self.browser._preview_waiting:
            for future in tuple(self.browser._preview_futures.values()):
                try:
                    future.result(timeout=2)
                except Exception:
                    pass  # Delivery owns the error feedback, as in production.
            self.browser._drain()
            self.assertLess(time.monotonic(), deadline, 'preview queue did not settle')

    def test_gallery_reentry_reuses_widgets_and_filter_resource_changes_rebuild(self):
        b = self.browser
        b._overview = False
        b.refresh()
        first = [row['frame'] for row in b._cards]
        with patch.object(b, '_make_card', wraps=b._make_card) as build:
            for _ in range(10):
                b.refresh()
            self.assertEqual(build.call_count, 0)
        self.assertEqual([row['frame'] for row in b._cards], first)
        b.suspend()
        b.refresh()
        self.assertEqual([row['frame'] for row in b._cards], first)
        b.search_var.set('Map 1')
        b.refresh()
        self.assertEqual(len(b._cards), 1)
        self.assertFalse(first[0].winfo_exists())
        filtered = b._cards[0]['frame']
        self.path.unlink()
        b.invalidate_resource_context()
        b.refresh()
        self.assertFalse(filtered.winfo_exists())
        self.assertEqual(b._cards[0]['status'].cget('text'), '图片文件缺失')
        old_generation = b._generation
        self.assistant.pack = SimpleNamespace(**vars(self.assistant.pack))
        self.assistant.pack.mode = 'difficult'
        b.refresh()
        self.assertGreater(b._generation, old_generation)
        self.assertEqual(b.model.mode, 'difficult')

    def test_overview_decodes_off_ui_thread_and_reuses_pixels(self):
        b = self.browser
        owner = threading.get_ident()
        calls = []
        structure_loader, image_loader = browser_module.load_structure_preview, browser_module.load_preview
        def load_structure(*args):
            calls.append(('structure', threading.get_ident()))
            return structure_loader(*args)
        def load_image(*args):
            calls.append(('cover', threading.get_ident()))
            return image_loader(*args)
        with patch.object(browser_module, 'load_structure_preview', load_structure), \
             patch.object(browser_module, 'load_preview', load_image), \
             patch.object(b, '_apply_image', wraps=b._apply_image) as upload:
            b.refresh()
            self.assertTrue(calls or b._preview_futures)
            self.drain()
            self.assertTrue(all(thread != owner for _, thread in calls))
            self.assertEqual(upload.call_count, 4)
            self.assertEqual({kind for kind, _ in calls}, {'structure', 'cover'})
            decode_count = len(calls)
            b._render_overview()
            self.drain()
            self.assertEqual(len(calls), decode_count)
            self.assertEqual(upload.call_count, 8)
            # The displayed pixels are the real declared mask conversion.
            call = next(c for c in upload.call_args_list if c.args[1].size != (32, 24))
            widget, pixels, _ = call.args
            expected = structure_loader(StructurePreview('map-0', 'Map 0', '1', self.structure, 'shared_mask', 'fixture-v1'), pixels.size)
            self.assertEqual(pixels.tobytes(), expected.tobytes())
        self.assertLessEqual(len(b._cache), 48)

    def test_empty_gallery_reentry_and_search_resume_do_not_touch_destroyed_canvas(self):
        b = self.browser
        b._overview = False
        b.refresh()
        b.search_var.set('no-such-map')
        b.refresh()
        self.assertEqual(b._cards, [])
        empty = tuple(b.content.winfo_children())
        with patch.object(b, '_schedule', wraps=b._schedule) as schedule:
            b.refresh()
            self.assertFalse(any(call.args[1] == b._load_visible for call in schedule.call_args_list))
        self.assertEqual(tuple(b.content.winfo_children()), empty)
        b.search_var.set('Map 2')
        b._search_changed()
        b.suspend()
        self.assertIsNone(b._search_job)
        b._resume()
        self.assertIsNotNone(b._search_job)
        b._render_gallery()
        self.assertEqual([row['item'].name for row in b._cards], ['Map 2'])

    def test_refresh_rechecks_deleted_recreated_and_replaced_files_without_rebuilding(self):
        b = self.browser
        b.pack_forget()
        b.place(x=0, y=0, width=1200, height=800)
        b._overview = False
        b.refresh()
        self.root.update_idletasks()
        b._layout_cards()
        self.root.update_idletasks()
        b._load_visible()
        self.drain()
        row = b._cards[0]
        first_frame, first_photo = row['frame'], row['thumb']._map_photo
        with patch.object(b._executor, 'submit', wraps=b._executor.submit) as submit:
            for _ in range(3):
                b.refresh()
                b._load_visible()
            self.drain()
            self.assertEqual(submit.call_count, 0)
        self.assertIs(row['thumb']._map_photo, first_photo)
        self.path.unlink()
        b.refresh()
        b._load_visible()
        self.drain()
        self.assertIs(b._cards[0]['frame'], first_frame)
        self.assertIsNone(row['thumb']._map_photo)
        self.assertEqual(str(row['thumb'].cget('image')), '')
        self.assertEqual(row['status'].cget('text'), '图片文件缺失')
        for color in ('#ff0000', '#00ff00'):
            Image.new('RGB', (32, 24), color).save(self.path)
            b.refresh()
            b._load_visible()
            self.drain()
            self.assertIs(b._cards[0]['frame'], first_frame)
            shown = browser_module.ImageTk.getimage(row['thumb']._map_photo).convert('RGB')
            self.assertEqual(shown.getpixel((0, 0)), Image.new('RGB', (1, 1), color).getpixel((0, 0)))
            self.assertEqual(row['status'].cget('text'), '可预览 · 已收录')

    def test_hidden_overview_scale_change_is_scheduled_on_map_resume(self):
        b = self.browser
        b.refresh()
        self.drain()
        b.suspend()
        b.set_display_scale(1.5)
        self.assertNotEqual(b._overview_rendered, b._overview_signature())
        b._resume()
        self.assertIsNotNone(b._resize_job)
        b._render_overview()
        self.drain()
        self.assertEqual(b._overview_rendered, b._overview_signature())

    def test_suspended_result_waits_and_old_context_never_uploads(self):
        b = self.browser
        b._executor.shutdown(wait=True)
        b._executor = ControlledExecutor()
        b._active = True
        widget = tk.Label(b)
        pixels = Image.new('RGB', (10, 10))
        with patch.object(b, '_apply_image', wraps=b._apply_image) as upload:
            b._request(self.path, (10, 10), widget)
            future = b._executor.futures[-1]
            b.suspend()
            future.set_result(pixels)
            b._drain()
            self.assertEqual(upload.call_count, 0)
            self.assertEqual(len(b._preview_futures), 1)
            b._active = True
            b._drain()
            self.assertEqual(upload.call_count, 1)
            b._request(self.path, (11, 11), widget)
            old = b._executor.futures[-1]
            b.suspend()
            b.invalidate_resource_context()
            old.set_result(pixels)
            b._active = True
            b._drain()
            self.assertEqual(upload.call_count, 1)
            self.assertFalse(b._pending)
            self.assertFalse(b._preview_futures)
            b._request(self.path, (12, 12), widget)
            closing = b._executor.futures[-1]
            b.destroy()
            closing.set_result(pixels)
            self.assertEqual(upload.call_count, 1)
            self.assertFalse(b._jobs)

    def test_structure_array_version_and_file_stamp_own_cache_identity(self):
        b = self.browser
        b._active = True
        widget = tk.Label(b)
        asset = StructurePreview('map', 'Map', '1', self.structure, 'shared_mask', 'v1')
        with patch.object(b, '_apply_image', wraps=b._apply_image) as upload:
            b._request(self.structure, (24, 24), widget, structure=asset)
            self.drain()
            first = upload.call_args.args[1].tobytes()
            b._request(self.structure, (24, 24), widget, structure=StructurePreview('map', 'Map', '1', self.structure, 'other_mask', 'v1'))
            self.drain()
            self.assertNotEqual(first, upload.call_args.args[1].tobytes())
            self.assertEqual(len(b._cache), 2)
            b._request(self.structure, (24, 24), widget, structure=StructurePreview('map', 'Map', '1', self.structure, 'shared_mask', 'v2'))
            self.drain()
            self.assertEqual(len(b._cache), 3)
            np.savez_compressed(self.structure, shared_mask=np.zeros((24, 24), dtype=np.uint8))
            b._request(self.structure, (24, 24), widget, structure=asset)
            self.drain()
            self.assertNotEqual(first, upload.call_args.args[1].tobytes())

    def test_corrupt_structure_and_missing_cover_keep_failure_feedback(self):
        b = self.browser
        b._active = True
        widget = tk.Button(b)
        widget._map_photo = tk.PhotoImage(master=b, width=1, height=20)
        widget.configure(image=widget._map_photo)
        widget._preview_failure_text = '结构图不可用'
        widget._preview_disable_on_failure = True
        self.structure.write_bytes(b'not an npz')
        asset = StructurePreview('map', 'Map', '1', self.structure, 'shared_mask', 'v1')
        b._request(self.structure, (20, 20), widget, structure=asset)
        self.drain()
        self.assertEqual(widget.cget('text'), '结构图不可用')
        self.assertEqual(widget.cget('state'), 'disabled')
        cover = tk.Label(b)
        cover._preview_failure_text = '预览不可用'
        self.path.unlink()
        b._request(self.path, (20, 20), cover)
        self.assertEqual(cover.cget('text'), '预览不可用')
        self.assertFalse(b._pending)


if __name__ == '__main__':
    unittest.main()
