"""Same synthetic inputs against the exact hotfix source and current code.

OS/pixels/resources/fit measurements are fixtures. Capture guards and runtime
commit/pose decisions run unchanged. This is not a real game test.
"""
import copy
import hashlib
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import types
import unittest
from unittest.mock import patch

import cv2
import numpy as np
import client_live_runtime as current
from difficult_map_ui import DifficultMapUI
from recognition_diagnostics import bounded_recognition, scale_observations
from test_client_live_runtime import FixtureCapture, fixture_engine

ROOT = Path(__file__).parents[2]
BASE = '980d64949dcec3bcfb747dbf8bacb6dfc00fbded'
FIXTURES = Path(__file__).parent / 'fixtures' / 'diagnostic_hotfix'
MANIFEST = json.loads((FIXTURES / 'manifest.json').read_text('utf-8'))


def baseline(relative, name):
    record = MANIFEST['files'][relative]
    payload = (FIXTURES / record['fixture']).read_bytes()
    if MANIFEST['sourceCommit'] != BASE or hashlib.sha256(payload).hexdigest() != record['sha256']:
        raise ValueError('diagnostic baseline fixture identity mismatch')
    source = payload.decode('utf-8-sig')
    module = types.ModuleType(name)
    module.__file__ = str(ROOT/relative)
    sys.modules[name] = module
    exec(compile(source, module.__file__, 'exec'), module.__dict__)
    return module


class NoninterferenceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.before_ui = baseline('app/difficult_map_ui.py', 'baseline_difficult_ui')
        cls.before_runtime = baseline('experiments/qt_quick/client_live_runtime.py', 'baseline_live_runtime')
        cls.before_observation = baseline('app/map_interface_observation.py', 'baseline_map_observation')

    def test_fixture_loading_never_requires_git_history(self):
        with patch('subprocess.check_output', side_effect=AssertionError('git unavailable')):
            module = baseline('app/difficult_map_ui.py', 'baseline_without_git')
        self.assertTrue(hasattr(module, 'DifficultMapUI'))
        self.assertEqual(MANIFEST['sourceCommit'], BASE)

    def test_same_pixels_difficult_scales_and_decisions_are_equal(self):
        with tempfile.TemporaryDirectory() as folder:
            one = np.zeros((22, 18), np.uint8); two = one.copy()
            cv2.putText(one, '1', (2,18), cv2.FONT_HERSHEY_SIMPLEX, .6, 255, 2)
            cv2.putText(two, '2', (2,18), cv2.FONT_HERSHEY_SIMPLEX, .6, 255, 2)
            path = Path(folder)/'templates.npz'
            np.savez(path, one=one, two=two, source_size=[1280,720])
            before, after = self.before_ui.DifficultMapUI(path), DifficultMapUI(path)
            rng = np.random.default_rng(7)
            frames = [np.zeros((720,1280,3),np.uint8), rng.integers(0,256,(720,1280,3),dtype=np.uint8)]
            for level in (80, 255):
                frame = np.zeros((720,1280,3),np.uint8)
                frame[60:82,690:708] = (one[:,:,None] > 0)*level
                frame[60:82,780:798] = (two[:,:,None] > 0)*level
                frames.append(frame)
            for frame in frames:
                for scale in (.5, 1., 1.5):
                    original, observed = before._observe_scale(frame,scale), after._observe_scale(frame,scale)
                    for key, value in original.items():
                        self.assertEqual(value, observed[key], key)
                    self.assertEqual(observed['scale'],scale)
                    if 'glyphs' in observed:
                        self.assertIn('spacing',observed); self.assertIn('dy',observed)
                original, observed = before.observe(frame), after.observe(frame)
                for key, value in original.items(): self.assertEqual(value, observed[key], key)
                self.assertEqual(len(observed['diagnosticScales']), len({round(frame.shape[1]/1280,4),round(frame.shape[0]/720,4),1.}))
                compact=bounded_recognition(dict(stage='floor-observation',scales=scale_observations(observed),
                    thresholds=observed.get('thresholds')))
                self.assertEqual(len(compact['scales']),len(observed['diagnosticScales']))
                self.assertIn('glyphs',compact['scales'][0])


    def test_same_pixels_nightmare_decision_and_floor_are_equal(self):
        import map_interface_observation as after
        rng = np.random.default_rng(12)
        for width,height in ((800,600),(1280,720),(1280,960)):
            for frame in (np.zeros((height,width,3),np.uint8), rng.integers(0,256,(height,width,3),dtype=np.uint8)):
                for name in ('detect_selected_floor','detect_map_interface'):
                    original = getattr(self.before_observation,name)(frame)
                    observed = getattr(after,name)(frame)
                    for key,value in original.items(): self.assertEqual(value,observed[key],key)

    def _runtime_result(self, module, mode, fit_success, live):
        capture, engine = FixtureCapture(), fixture_engine(mode)
        capture.generation = 3
        capture.is_current = lambda context, generation=None: generation == 3
        runtime = module.LiveRuntime(engine_factories={mode:lambda:engine},capture_provider=capture,
            evidence_kind='live-client-capture' if live else 'synthetic',clock=lambda:1)
        runtime._mode = mode
        events=[]; runtime.set_event_callback(events.append)
        fit = dict(success=fit_success,reason='fixture-fit' if fit_success else 'structure-current-geometry-failed',
            matrixSourceToClient=np.array([[.5,0,40],[0,.5,60],[0,0,1]]),
            diagnostics=dict(seedCount=2,queryChains=3,sourceChains=5,candidateCount=4,elapsedMs=2.5,bestResidual=.2),
            selectedRouteCurrentFrameResidual=dict(verified=True,poseVerifiedAgainstCanonicalGeometry=True,
                profileRenderDerivedFromCanonical=True,coordinatePackId='fixture-pack',
                validationBasis='shared-canonical-geometry-current-frame'))
        with patch('map_interface_observation.detect_map_interface',return_value={'detected':True}), \
             patch('map_interface_observation.detect_selected_floor',return_value={'selectedFloor':1}), \
             patch.object(module._AlignmentHost,'fit',return_value=copy.deepcopy(fit)):
            start=runtime.dispatch('start_round',{})
            self.assertTrue(start['success'],start)
            result=runtime.dispatch('capture_now',{})
            state=runtime.snapshot()
            render=state.get('render') or {}
            normalized=dict(success=result['success'],reason=result['reason'],liveAuthorization=result.get('liveAuthorization'),
                mapId=state.get('mapId'),floor=state.get('floor'),displayAuthorized=state.get('displayAuthorized'),
                poseStatus=(state.get('poseResult') or {}).get('status'),
                matrix=render.get('matrixSourceToClient'),core=runtime._core.can_display(cycle_id=runtime._cycle,
                    map_id=runtime._map_id,floor=runtime._floor,frame_generation=runtime._frame_generation))
            runtime.shutdown()
        return normalized, events

    def test_same_inputs_pose_decisions_and_live_authority_match_hotfix(self):
        for mode in ('nightmare','difficult'):
            for success in (False,True):
                for live in (False,True):
                    with self.subTest(mode=mode,success=success,live=live):
                        before,_=self._runtime_result(self.before_runtime,mode,success,live)
                        after,events=self._runtime_result(current,mode,success,live)
                        self.assertEqual(before,after)
                        diagnostics=[row for row in events if row['event']=='recognition-diagnostic']
                        self.assertTrue(diagnostics)
                        for row in diagnostics:
                            self.assertNotIn('captureDiagnostics',row)
                            self.assertLessEqual(len(json.dumps(row['recognitionDiagnostics'],separators=(',',':')).encode()),2048)

    def test_control_band_is_absent_when_small_frame_skips_all_pixel_detectors(self):
        from recognition_diagnostics import control_band
        frame=np.zeros((300,400,3),np.uint8)
        self.assertIsNone(control_band(frame,'nightmare',dict(detectionMode='shape-rejected'),
            dict(layout='unlocalized')))

    def test_foreground_and_cancellation_capture_authority_match_hotfix(self):
        import client_capture
        from test_client_capture_full import Native, Backend
        before = baseline('experiments/qt_quick/client_capture.py', 'baseline_capture')
        for kind in ('disabled', 'foreground', 'cancelled', 'destroyed'):
            results=[]
            for module in (before,client_capture):
                native,backend=Native(),Backend()
                capture=module.ClientCaptureProvider(native,backend)
                capture.bindFullClient(42)
                if kind != 'disabled': capture.enable()
                if kind == 'foreground': native.foreground_window=lambda:77
                if kind == 'destroyed': native.is_window=lambda hwnd:False
                try:
                    capture.capture(lambda:kind == 'cancelled')
                except Exception as error:
                    results.append((type(error).__name__,str(error),list(backend.captures)))
                finally: capture.shutdown()
            self.assertEqual(results[0],results[1],kind)
            self.assertFalse(results[0][2],kind)

    def test_capture_authorization_source_is_byte_identical_to_hotfix(self):
        for relative in ('app/client_region_capture.py',
                         'experiments/qt_quick/client_capture_exclusion.py'):
            self.assertEqual(hashlib.sha256((ROOT/relative).read_bytes()).hexdigest(),
                             MANIFEST['files'][relative]['sha256'], relative)

    def test_capture_source_change_is_only_foreground_diagnostic_metadata(self):
        original = (FIXTURES / MANIFEST['files']['experiments/qt_quick/client_capture.py']['fixture']).read_text('utf-8')
        start = original.index('                error.capture_diagnostics = dict(expectedHwnd=hwnd, foregroundHwnd=foreground)')
        end = original.index('                raise error', start)
        expected = original[:start] + "                error.capture_diagnostics = dict(expectedHwnd=hwnd, foregroundRole='other')\n" + original[end:]
        self.assertEqual(expected, (ROOT/'experiments/qt_quick/client_capture.py').read_text('utf-8'))

    def test_bad_diagnostic_sink_does_not_change_verdict(self):
        original=current.LiveRuntime._recognition_diagnostic
        def failing(self,*args,**kwargs):
            # Inject inside the metadata builder covered by production catch.
            with patch('recognition_diagnostics.bounded_recognition',side_effect=ValueError('injected')):
                return original(self,*args,**kwargs)
        reference,_=self._runtime_result(self.before_runtime,'nightmare',True,True)
        with patch.object(current.LiveRuntime,'_recognition_diagnostic',failing):
            observed,_=self._runtime_result(current,'nightmare',True,True)
        self.assertEqual(reference,observed)

    def test_bounded_evidence_is_json_and_removes_identifying_fields(self):
        value=dict(stage='pose-fit',seedCount=7,path='C:/private/player',username='player',
            diagnostics={str(i):[{'value':float(i),'nan':float('nan')}] * 100 for i in range(100)})
        evidence=bounded_recognition(value)
        encoded=json.dumps(evidence,separators=(',',':'),allow_nan=False).encode()
        self.assertLessEqual(len(encoded),2048)
        self.assertEqual(evidence['seedCount'],7)
        self.assertNotIn(b'player',encoded)


if __name__ == '__main__': unittest.main()
