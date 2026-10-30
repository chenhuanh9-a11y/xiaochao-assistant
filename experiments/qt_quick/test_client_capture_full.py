"""Actual capture adapter contracts with private backend pixels; no native IO."""
import threading
import unittest
import numpy as np
from client_capture import ClientCaptureProvider, CaptureUnavailable
from client_live_runtime import LiveRuntime
from client_region_capture import ClientCaptureContext, CaptureContextChanged

class Native:
    def is_window(self, hwnd): return hwnd == 42
    def foreground_window(self): return 42

class Backend:
    def __init__(self):
        self.current=ClientCaptureContext((100,80),(10,20,110,100),96)
        self.captures=[]
        self.on_capture=None
    def context(self, hwnd):return self.current
    def capture(self, hwnd, region):
        self.captures.append((hwnd,region))
        if self.on_capture:self.on_capture()
        left,top,right,bottom=region
        image=np.zeros((bottom-top,right-left,3),np.uint8)
        image[:,:,0]=np.arange(left,right,dtype=np.uint8)
        image[:,:,1]=np.arange(top,bottom,dtype=np.uint8)[:,None]
        return image

class FullClientCaptureTests(unittest.TestCase):
    def setUp(self):
        self.backend=Backend();self.capture=ClientCaptureProvider(Native(),self.backend)
        self.addCleanup(self.capture.shutdown)
    def changed(self,width=200,height=160,dpi=144):
        self.backend.current=ClientCaptureContext((width,height),(20,30,20+width,30+height),dpi)
    def full(self):
        self.capture.bindFullClient(42);self.assertTrue(self.capture.enable())
    def assert_full(self,captured):
        width,height=captured.context.full_client_size
        self.assertEqual(captured.frame_bgr.shape,(height,width,3))
        self.assertEqual(captured.roi_client_bounds,(0,0,width,height))
        self.assertEqual(captured.requested_client_bounds,(0,0,width,height))
        self.assertEqual(captured.roi_screen_bounds,captured.context.client_screen_bounds)
        np.testing.assert_array_equal(LiveRuntime._full_frame(captured),captured.frame_bgr)
    def test_original_fixed_roi_mismatch_reproduces_exact_consumer_guard(self):
        self.capture.bind(42,(0,0,100,80));self.capture.enable();self.changed()
        captured=self.capture.capture()
        self.assertEqual(self.backend.captures,[(42,(0,0,100,80))])
        self.assertEqual(captured.frame_bgr.shape,(80,100,3))
        self.assertEqual(captured.context.full_client_size,(200,160))
        with self.assertRaisesRegex(ValueError,'live-runtime-requires-full-client-capture'):
            LiveRuntime._full_frame(captured)
    def test_full_intent_follows_grow_and_shrink_without_resize_or_padding(self):
        self.full();self.assertEqual(self.backend.captures,[])
        first=self.capture.capture();generation=self.capture.generation
        for width,height in [(200,160),(60,40)]:
            self.changed(width,height)
            self.assertFalse(self.capture.is_current(first.context,generation))
            current=self.capture.capture();self.assert_full(current)
            self.assertTrue(self.capture.is_current(current.context,generation))
            self.assertEqual(self.backend.captures[-1],(42,(0,0,width,height)))
            self.assertEqual(int(current.frame_bgr[-1,-1,0]),(width-1)%256)
            self.assertEqual(int(current.frame_bgr[-1,-1,1]),(height-1)%256)
    def test_dpi_and_origin_metadata_come_from_current_backend_context(self):
        self.full();old=self.capture.capture();generation=self.capture.generation
        self.changed(100,80,192)
        current=self.capture.capture();self.assert_full(current)
        self.assertEqual(current.context.dpi,192)
        self.assertEqual(current.context.client_screen_bounds,(20,30,120,110))
        self.assertFalse(self.capture.is_current(old.context,generation))
        self.assertTrue(self.capture.is_current(current.context,generation))
    def test_geometry_change_during_capture_rejects_then_new_transaction_is_full(self):
        self.full();self.backend.on_capture=lambda:self.changed()
        with self.assertRaises(CaptureContextChanged):self.capture.capture()
        self.backend.on_capture=None
        self.assert_full(self.capture.capture())
    def test_rebind_generation_and_cancel_during_capture_still_reject(self):
        self.full()
        def rebound():
            self.capture.bindFullClient(42);self.capture.enable()
        self.backend.on_capture=rebound
        with self.assertRaises(CaptureUnavailable):self.capture.capture()
        cancelled=threading.Event();self.backend.on_capture=cancelled.set
        with self.assertRaises(CaptureUnavailable):self.capture.capture(cancelled)
    def test_runtime_bind_uses_full_intent_instead_of_catalog_region(self):
        runtime=LiveRuntime(engine_factories={},capture_provider=self.capture,evidence_kind='synthetic')
        result=runtime.dispatch('bind_capture',dict(hwnd=42,region=[0,0,17,13]))
        self.assertTrue(result['success'],result)
        self.assertEqual(self.capture.snapshot['captureIntent'],'full-client')
        self.assertEqual(self.backend.captures,[])
        self.changed();self.capture.enable()
        self.assert_full(self.capture.capture())
    def test_full_guard_receipt_preserves_raw_reason_and_bounded_metadata(self):
        import json
        from test_client_live_runtime import fixture_engine
        class OldROIAdapter(ClientCaptureProvider):
            def bindFullClient(self, hwnd):
                self.bind(hwnd,(0,0,100,80))  # Explicit faulty legacy adapter.
        capture=OldROIAdapter(Native(),self.backend)
        self.addCleanup(capture.shutdown)
        runtime=LiveRuntime(engine_factories={'nightmare':fixture_engine},capture_provider=capture,evidence_kind='synthetic')
        self.assertTrue(runtime.dispatch('bind_capture',dict(hwnd=42))['success'])
        self.assertTrue(runtime.dispatch('start_round')['success'])
        self.changed()
        receipt=runtime.dispatch('capture_now')
        self.assertFalse(receipt['success'])
        self.assertTrue(receipt['error'])
        self.assertEqual(receipt['reason'],'ValueError:live-runtime-requires-full-client-capture')
        self.assertEqual(receipt['errorPhase'],'capture')
        diagnostics=receipt['captureDiagnostics']
        self.assertEqual(diagnostics['frameShape'],[80,100,3])
        self.assertEqual(diagnostics['frameDtype'],'uint8')
        self.assertEqual(diagnostics['roiClientBounds'],[0,0,100,80])
        self.assertEqual(diagnostics['requestedClientBounds'],[0,0,100,80])
        self.assertEqual(diagnostics['fullClientSize'],[200,160])
        self.assertEqual(diagnostics['clientBounds'],[20,30,220,190])
        self.assertEqual(diagnostics['dpi'],144)
        self.assertEqual(diagnostics['providerIntent'],'roi')
        self.assertEqual(diagnostics['providerGeneration'],capture.generation)
        self.assertEqual(diagnostics['providerGenerationSource'],'current-provider-at-error')
        self.assertNotIn('pixels',diagnostics)
        self.assertIn('_full_frame',receipt['traceback'])
        self.assertLessEqual(len(receipt['traceback']),8192)
        self.assertLessEqual(receipt['traceback'].count('File "'),12)
        json.dumps(receipt,allow_nan=False)
    def test_backend_capture_error_phase_is_preserved_on_capture_and_refresh(self):
        from test_client_live_runtime import fixture_engine
        runtime=LiveRuntime(engine_factories={'nightmare':fixture_engine},capture_provider=self.capture,evidence_kind='synthetic')
        self.assertTrue(runtime.dispatch('bind_capture',dict(hwnd=42))['success'])
        self.assertTrue(runtime.dispatch('start_round')['success'])
        def failed(hwnd,region):raise OSError('private-fixture-capture-failed')
        self.backend.capture=failed
        for action in ['capture_now','refresh_map']:
            runtime._map_open=True
            receipt=runtime.dispatch(action)
            self.assertFalse(receipt['success'])
            self.assertEqual(receipt['reason'],'OSError:private-fixture-capture-failed')
            self.assertEqual(receipt['errorPhase'],'capture')
            self.assertEqual(receipt['captureDiagnostics']['providerIntent'],'full-client')
            self.assertIn('private-fixture-capture-failed',receipt['traceback'])
            self.assertNotIn('frameShape',receipt['captureDiagnostics'])

    def test_full_intent_must_be_supported_by_the_injected_provider(self):
        class ROIOnly:
            def disable(self):pass
            def bind(self,*args):raise AssertionError('must not fall back to stale catalog ROI')
        runtime=LiveRuntime(engine_factories={},capture_provider=ROIOnly(),evidence_kind='synthetic')
        result=runtime.dispatch('bind_capture',dict(hwnd=42,region=[0,0,100,80]))
        self.assertFalse(result['success'])
        self.assertIn('full-client-capture-binding-unavailable',result['reason'])

if __name__=='__main__':unittest.main()
