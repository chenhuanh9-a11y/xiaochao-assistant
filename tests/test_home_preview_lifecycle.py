"""Headless production-method tests. No real GUI or game timing claim."""
import ast
from concurrent.futures import Future, ThreadPoolExecutor
from pathlib import Path
from types import SimpleNamespace
import unittest
import tempfile
import threading
from PIL import Image, ImageOps
from unittest.mock import Mock, patch
SOURCE=Path(__file__).parents[1]/'app'/'desktop_main_ui.py'
def subject():
    tree=ast.parse(SOURCE.read_text(encoding='utf-8'))
    names={'_poll_home_previews','_close_home_previews','_schedule_home_layout','_finish_home_layout','_layout_home'}
    methods=[n for n in ast.walk(tree) if isinstance(n,ast.FunctionDef) and n.name in names]
    wrapper=ast.ClassDef(name='Subject',bases=[],keywords=[],body=methods,decorator_list=[])
    namespace=dict(ImageTk=SimpleNamespace(PhotoImage=Mock(side_effect=lambda x,**kw:x)),_load_home_previews=Mock(),_hero_art=Mock(return_value='art'),FONT='font')
    exec(compile(ast.fix_missing_locations(ast.Module(body=[wrapper],type_ignores=[])),str(SOURCE),'exec'),namespace)
    return namespace['Subject'],namespace
class HomeLifecycle(unittest.TestCase):
    def test_thousand_resize_requests_retain_only_active_and_latest(self):
        cls,ns=subject(); s=cls(); s._closed=False; s.window=object(); futures=[]
        def submit(*args):
            f=Future(); futures.append(f); return f
        s._home_preview_executor=Mock(submit=Mock(side_effect=submit))
        labels=[Mock(winfo_exists=Mock(return_value=True)) for _ in range(3)]
        for i in range(1000):
            s._home_preview_context=i
            s._home_preview_pending=(i,labels,[('image','x')]*3,(120,80),8)
            s._poll_home_previews()
        self.assertEqual(len(futures),1)
        self.assertEqual(s._home_preview_pending[0],999)
        futures[0].set_result(['stale']*3); s._poll_home_previews()
        self.assertEqual(len(futures),2)
        ns['ImageTk'].PhotoImage.assert_not_called()
        futures[1].set_result(['latest']*3); s._poll_home_previews()
        self.assertEqual(ns['ImageTk'].PhotoImage.call_count,3)
        self.assertIsNone(s._home_preview_future)
        s._close_home_previews()
        s._home_preview_executor.shutdown.assert_called_once_with(wait=False,cancel_futures=True)
    def test_configure_debounced_and_destroy_cancels(self):
        cls,_=subject(); s=cls(); s._closed=False; s.root=Mock(); s.root.after.side_effect=range(1001)
        for _ in range(1000):s._schedule_home_layout()
        self.assertEqual(s.root.after_cancel.call_count,999)
        s._close_home_previews()
        self.assertEqual(s.root.after_cancel.call_count,1000)
    def test_layout_preserves_preview_and_reuses_same_art_size(self):
        cls,ns=subject(); s=cls(); s._scale=1; s._px=lambda x:x
        s._home_layout=None; s._home_preview_context='current'; s.window=object()
        for name in ('home_grid','home_main','home_side','hero_title','hero_art'):setattr(s,name,Mock())
        for width in range(1400,1600):
            s.home_grid.winfo_width.return_value=width; s._layout_home()
        self.assertEqual(s._home_preview_context,'current')
        self.assertEqual(ns['ImageTk'].PhotoImage.call_count,1)
    def test_no_decoding_on_refresh_thread(self):
        tree=ast.parse(SOURCE.read_text(encoding='utf-8'))
        method=next(n for n in ast.walk(tree) if isinstance(n,ast.FunctionDef) and n.name=='_refresh_home_resources')
        calls={ast.unparse(n.func) for n in ast.walk(method) if isinstance(n,ast.Call)}
        self.assertNotIn('Image.open',calls); self.assertNotIn('load_structure_preview',calls)
    def test_closed_window_drops_completed_pixels(self):
        cls,ns=subject(); s=cls(); s._closed=True; s._home_preview_context=1
        s._home_preview_active=(1,[Mock()]); s._home_preview_future=Future()
        s._home_preview_future.set_result(['pixels']); s._poll_home_previews()
        ns['ImageTk'].PhotoImage.assert_not_called()
    def test_real_worker_decodes_files_and_handles_missing_image(self):
        tree=ast.parse(SOURCE.read_text(encoding='utf-8'))
        node=next(n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name=='_load_home_previews')
        caller=threading.get_ident(); worker_ids=[]
        def rounded(image,*args):
            worker_ids.append(threading.get_ident()); return image
        ns=dict(Image=Image,ImageOps=ImageOps,IMAGE_BG='#ffffff',SURFACE='#ffffff',_rounded_image=rounded)
        exec(compile(ast.Module(body=[node],type_ignores=[]),str(SOURCE),'exec'),ns)
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'source.png'; Image.new('RGB',(240,160),'red').save(path)
            with ThreadPoolExecutor(max_workers=1) as executor:
                for _ in range(30):
                    images=executor.submit(ns['_load_home_previews'],[('image',path),('image',path),('image',path.with_name('missing.png'))],(120,80),8).result(timeout=5)
                    self.assertEqual([im.size if im else None for im in images],[(120,80),(120,80),None])
        self.assertEqual(len(worker_ids),60)
        self.assertTrue(all(ident!=caller for ident in worker_ids))
        self.assertEqual(len(set(worker_ids)),1)

    def test_decompression_bomb_is_a_failed_tile_not_failed_poll(self):
        tree=ast.parse(SOURCE.read_text(encoding='utf-8'))
        node=next(n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name=='_load_home_previews')
        ns=dict(Image=Image,ImageOps=ImageOps,IMAGE_BG='#ffffff',SURFACE='#ffffff',_rounded_image=lambda image,*args:image)
        exec(compile(ast.Module(body=[node],type_ignores=[]),str(SOURCE),'exec'),ns)
        with patch.object(Image,'open',side_effect=Image.DecompressionBombError('oversized')):
            with ThreadPoolExecutor(max_workers=1) as executor:
                future=executor.submit(ns['_load_home_previews'],[('image','oversized.png')],(120,80),8)
                self.assertEqual(future.result(timeout=5),[None])
                cls,env=subject(); s=cls(); s._closed=False; s._home_preview_context=1
                s._home_preview_active=(1,[Mock()]); s._home_preview_future=future
                s._poll_home_previews()
                self.assertIsNone(s._home_preview_future)
                env['ImageTk'].PhotoImage.assert_not_called()
        with patch.object(Image,'open',side_effect=RuntimeError('unexpected programming error')):
            with self.assertRaises(RuntimeError):
                ns['_load_home_previews']([('image','x')],(120,80),8)

if __name__=='__main__':unittest.main()
