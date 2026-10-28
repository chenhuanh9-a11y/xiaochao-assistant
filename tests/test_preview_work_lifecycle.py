import ast
from collections import OrderedDict
from concurrent.futures import Future, CancelledError, ThreadPoolExecutor
from pathlib import Path
import queue
import time
import types
import unittest
import tempfile
import threading
import json
from PIL import Image, ImageOps

SOURCE = Path(__file__).resolve().parents[1] / 'app/map_resource_browser.py'
class Executor:
    def __init__(self): self.futures=[]
    def submit(self,*args):
        future=Future(); self.futures.append(future); return future
class Widget:
    def configure(self,**kw): pass
    def winfo_exists(self): return True
class PreviewLifecycleTests(unittest.TestCase):
    def setUp(self):
        tree=ast.parse(SOURCE.read_text(encoding='utf-8'))
        cls=next(n for n in tree.body if isinstance(n,ast.ClassDef) and n.name=='MapResourceBrowser')
        namespace={'time':time,'queue':queue,'CancelledError':CancelledError,'load_preview':lambda *a:None, 'WARNING':'orange'}
        wanted={'_request','_pump_preview_work','_cancel_preview_work','_drain','invalidate_resource_context'}
        for method in cls.body:
            if isinstance(method,ast.FunctionDef) and method.name in wanted:
                exec(compile(ast.Module(body=[method],type_ignores=[]),str(SOURCE),'exec'),namespace)
        self.obj=types.SimpleNamespace(_generation=0,_cache=OrderedDict(),_pending={},_preview_futures={},_preview_waiting=OrderedDict(),_executor=Executor(),_results=queue.Queue(),_cards=[],_close_preview=lambda:None,_schedule=lambda *a:None,_apply_image=lambda *a:None)
        for name in wanted: setattr(self.obj,name,types.MethodType(namespace[name],self.obj))
    def test_latest_request_is_bounded_and_eventually_submitted(self):
        widget=Widget()
        for i in range(1000): self.obj._request(SOURCE,(100+i,100+i),widget)
        self.assertEqual(len(self.obj._executor.futures),2)
        self.assertEqual(len(self.obj._preview_waiting),1)
        for f in self.obj._executor.futures[:]: f.set_result(None)
        self.obj._drain()
        self.assertEqual(len(self.obj._executor.futures),3)
        self.assertEqual(next(iter(self.obj._pending.values()))[1][2],(1099,1099))
    def test_invalidate_cancels_queued_and_drops_old_results(self):
        self.obj._request(SOURCE,(100,100),Widget())
        self.obj.invalidate_resource_context()
        self.assertTrue(self.obj._executor.futures[0].cancelled())
        self.obj._drain()
        self.assertFalse(self.obj._pending)
        self.assertFalse(self.obj._preview_futures)
    def test_running_old_generation_cannot_block_new_forever(self):
        self.obj._request(SOURCE,(100,100),Widget())
        f=self.obj._executor.futures[0]; f.set_running_or_notify_cancel()
        self.obj.invalidate_resource_context()
        f.set_result(None)
        self.obj._drain()
        self.assertFalse(self.obj._preview_futures)
    def test_waiting_queue_bound(self):
        for i in range(1000): self.obj._request(SOURCE,(100,100),Widget())
        self.assertEqual(len(self.obj._preview_waiting),64)
        self.assertEqual(len(self.obj._executor.futures),2)
    def test_twenty_bursts_reclaim_all_work(self):
        peak_active=peak_waiting=0
        for cycle in range(20):
            widget=Widget()
            for i in range(200):
                self.obj._request(SOURCE,(100+i,100+i),widget)
                peak_active=max(peak_active,len(self.obj._preview_futures))
                peak_waiting=max(peak_waiting,len(self.obj._preview_waiting))
            self.obj.invalidate_resource_context()
            self.obj._drain()
            self.assertFalse(self.obj._preview_futures)
            self.assertFalse(self.obj._pending)
            self.assertFalse(self.obj._preview_waiting)
        self.assertEqual(peak_active,2)
        self.assertEqual(peak_waiting,1)
        self.assertEqual(len(self.obj._executor.futures),40)
        self.assertEqual(sum(f.cancelled() for f in self.obj._executor.futures),40)
    def test_large_image_not_retained_in_byte_cache(self):
        pixels=types.SimpleNamespace(width=4000,height=4000,getbands=lambda:('R','G','B'))
        self.obj._request(SOURCE,(4000,4000),Widget())
        self.obj._executor.futures[0].set_result(pixels)
        self.obj._drain()
        self.assertFalse(self.obj._cache)

    def test_real_executor_decode_stress_and_shutdown(self):
        tree=ast.parse(SOURCE.read_text(encoding='utf-8'))
        loader=next(n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name=='load_preview')
        ns={'Image':Image,'ImageOps':ImageOps,'IMAGE_BG':'#ffffff','Path':Path}
        exec(compile(ast.Module(body=[loader],type_ignores=[]),str(SOURCE),'exec'),ns)
        self.obj._pump_preview_work.__func__.__globals__['load_preview']=ns['load_preview']
        pool=ThreadPoolExecutor(max_workers=2,thread_name_prefix='preview-stress-test')
        self.obj._executor=pool
        uploaded=[]
        self.obj._apply_image=lambda widget,pixels,caption: uploaded.append(pixels.size)
        peak_active=peak_waiting=peak_cache=0
        submitted=[]
        real_submit=pool.submit
        def submit(*args):
            f=real_submit(*args); submitted.append(f); return f
        pool.submit=submit
        started=time.perf_counter()
        try:
            with tempfile.TemporaryDirectory() as folder:
                path=Path(folder)/'sample.png'
                Image.new('RGB',(128,128),'red').save(path)
                for cycle in range(20):
                    widget=Widget()
                    for index in range(100):
                        self.obj._request(path,(32+index,32+index),widget)
                        peak_active=max(peak_active,len(self.obj._preview_futures))
                        peak_waiting=max(peak_waiting,len(self.obj._preview_waiting))
                    deadline=time.perf_counter()+5
                    while self.obj._preview_futures or self.obj._preview_waiting:
                        self.obj._drain()
                        peak_cache=max(peak_cache,sum(im.width*im.height*len(im.getbands()) for im in self.obj._cache.values()))
                        self.assertLess(time.perf_counter(),deadline,'decode drain stalled')
                        time.sleep(.001)
                    self.obj.invalidate_resource_context()
                    self.assertFalse(self.obj._pending)
                    self.assertFalse(self.obj._cache)
                self.assertEqual(len(uploaded),20)
                self.assertEqual(uploaded,[(128,128)]*20)
                self.assertLessEqual(peak_active,2)
                self.assertEqual(peak_waiting,1)
        finally:
            self.obj._cancel_preview_work()
            pool.shutdown(wait=True,cancel_futures=True)
        self.assertFalse(any(t.name.startswith('preview-stress-test') for t in threading.enumerate()))
        print(json.dumps({'realExecutor':True,'rounds':20,'requests':2000,'submitted':len(submitted),'cancelled':sum(f.cancelled() for f in submitted),'peakActive':peak_active,'peakWaiting':peak_waiting,'peakCacheBytes':peak_cache,'remainingActive':len(self.obj._preview_futures),'remainingPending':len(self.obj._pending),'threadsReclaimed':True,'elapsedMs':round((time.perf_counter()-started)*1000,2)}))

if __name__=='__main__': unittest.main()
