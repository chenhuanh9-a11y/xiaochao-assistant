"""Private native-purpose lifecycle with synthetic capture/solver pixels.

These contracts exercise the real runtime display transaction, not game QA.
"""
import copy
import json
from pathlib import Path
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch

import numpy as np
from client_live_runtime import LiveRuntime
from s1_candidate_purpose import _NATIVE_S1_PREVIEW, private_native_candidate_purpose
from s1_structure_selection import StructureCandidateScanner
from test_s1_candidate_runtime import Registry
from test_s1_manual_floor_runtime import CurrentFixtureCapture


class NativeCandidateContracts(unittest.TestCase):
    def setUp(self):
        self.registry = Registry()
        self.registry.capability['contract'] = 's1-recorded-structure-candidate-floor-v2'
        self.capture = CurrentFixtureCapture()
        self.runtime = LiveRuntime(engine_factories=self.registry, capture_provider=self.capture,
            evidence_kind='live-client-capture', s1_candidate_purpose=_NATIVE_S1_PREVIEW)
        self.addCleanup(self.runtime.shutdown)
        self.runtime.configure_settings(dict(mapSelectionMode='auto'))
        self.floor, self.opened = '-1', True
        self.start_patch(patch('s1_structure_selection.StructureCatalog.from_engine', return_value=self.registry.catalog))
        self.start_patch(patch.object(self.runtime, '_observe', side_effect=lambda *a: (
            dict(detected=self.opened, mapOpen=self.opened), self.floor,
            dict(season='s1', mode='nightmare', mapOpen=self.opened,
                 selectedFloor=int(self.floor) if self.floor else None))))
        residual = dict(verified=True, poseVerifiedAgainstSharedStructure=True,
            poseVerifiedAgainstCanonicalGeometry=True, profileRenderDerivedFromCanonical=True,
            coordinatePackId='fixture-pack', validationBasis='shared-canonical-geometry-current-frame',
            withinThreePixels=1.)
        self.fit = dict(success=True, matrixSourceToClient=np.eye(3),
            coordinateSpace='shared-structure-to-client', selectedRouteCurrentFrameResidual=residual,
            observationComponent=dict(bounds=(0, 0, 1280, 720), center=(640, 360),
                x=0, y=0, width=1280, height=720, area=921600))
        self.display = dict(pixels=np.full((20, 24, 4), 255, np.uint8), resourceIdentity='fixture',
            displayKind='shared-structure', displayLabel='fixture', routeProfileId='shared-structure',
            matrixDisplayToReference=np.eye(3))
        self.start_patch(patch.object(self.runtime, '_display_layer', return_value=self.display))
        def scan(frame, **kwargs):
            return StructureCandidateScanner(self.registry.catalog,
                lambda *a, **kw: copy.deepcopy(self.fit)).scan(frame, **kwargs)
        self.registry.engine.qt_s1_structure_scan = scan
        self.assertTrue(self.runtime.dispatch('set_context', dict(season='s1', mode='nightmare'))['success'])

    def start_patch(self, patcher):
        result = patcher.start(); self.addCleanup(patcher.stop); return result

    def current_payload(self):
        return self.runtime.render_payload(self.runtime.snapshot().get("render", {}).get("token", ""))

    def show(self):
        self.assertTrue(self.runtime.dispatch('start_round')['success'])
        result = self.runtime.dispatch('capture_now')
        self.assertTrue(result['success'], result)
        payload = self.current_payload()
        self.assertIsNotNone(payload)
        self.assertTrue(payload['liveAuthorization'])
        self.assertEqual(payload['displayAuthorizationScope'], 'current-native-frame-candidate')
        state = self.runtime.snapshot()
        self.assertFalse(state['identityLocked'])
        self.assertFalse(state['automaticIdentityAvailable'])
        self.assertFalse(state['liveEngineAvailable'])
        self.assertFalse(state['recordedProvisionalDisplayEnabled'])
        self.assertEqual(self.runtime._map_id, '')
        self.assertFalse(self.runtime._gate.confirmed_id)
        return payload

    def test_start_display_switch_close_reopen_and_cancel(self):
        self.assertIn('start_round', self.runtime.snapshot()['availableCommands'])
        first = self.show()
        result = self.runtime.dispatch('next_candidate')
        self.assertTrue(result['success'], result)
        second = self.current_payload()
        self.assertNotEqual(first['mapId'], second['mapId'])
        self.assertFalse(self.runtime.snapshot()['identityLocked'])
        self.runtime.dispatch('close_map')
        self.assertIsNone(self.current_payload())
        self.assertFalse(self.runtime.snapshot()['structureEvidence']['currentSupportedMapIds'])
        reopened = self.show()
        self.assertNotEqual(first['frameId'], reopened['frameId'])
        self.runtime.dispatch('cancel')
        self.assertIsNone(self.current_payload())

    def test_first_material_precedes_scan_completion(self):
        entered, release = threading.Event(), threading.Event()
        def fit(frame, mid, *a, **kw):
            if mid == 'b':
                entered.set(); self.assertTrue(release.wait(5))
            return copy.deepcopy(self.fit)
        self.registry.engine.qt_s1_structure_scan = lambda frame, **kw: StructureCandidateScanner(
            self.registry.catalog, fit).scan(frame, **kw)
        self.runtime.dispatch('start_round')
        results = []
        worker = threading.Thread(target=lambda: results.append(self.runtime.dispatch('capture_now')))
        worker.start()
        try:
            self.assertTrue(entered.wait(5))
            self.assertIsNotNone(self.current_payload())
            self.assertTrue(self.runtime.snapshot()['structureScanInProgress'])
            self.assertFalse(self.runtime.snapshot()['identityLocked'])
        finally:
            release.set(); worker.join(5)
        self.assertFalse(worker.is_alive())
        self.assertTrue(results[0]['success'], results)

    def test_floor_probe_preserves_choice_then_changes_or_withdraws(self):
        first = self.show()
        self.runtime.dispatch('next_candidate')
        selected = self.current_payload()
        result = self.runtime.dispatch('observe_candidate_floor')
        self.assertTrue(result['unchanged'], result)
        self.assertEqual(self.current_payload()['token'], selected['token'])
        self.floor = '-2'
        self.assertTrue(self.runtime.dispatch('observe_candidate_floor')['success'])
        self.assertEqual(self.current_payload()['floor'], '-2')
        self.assertNotEqual(first['frameId'], self.current_payload()['frameId'])
        self.floor = None
        self.assertFalse(self.runtime.dispatch('observe_candidate_floor')['success'])
        self.assertIsNone(self.current_payload())

    def test_probe_capture_error_revokes_and_old_error_preserves_successor(self):
        self.show()
        with patch.object(self.capture, 'capture', side_effect=OSError('fixture capture failure')):
            result = self.runtime.dispatch('observe_candidate_floor')
        self.assertTrue(result['error'])
        self.assertIsNone(self.current_payload())
        self.assertFalse(self.runtime.snapshot()['structureEvidence']['currentSupportedMapIds'])
        self.show()
        successor = (self.runtime._epoch + 1, 'successor')
        def old_error(*a, **kw):
            self.runtime.invalidate('map-closed')
            self.runtime._capture_pending_owner = successor
            self.runtime._capture_pending = True
            raise OSError('late fixture error')
        with patch.object(self.capture, 'capture', side_effect=old_error):
            result = self.runtime.dispatch('observe_candidate_floor')
        self.assertTrue(result['stale'])
        self.assertIs(self.runtime._capture_pending_owner, successor)
        self.assertTrue(self.runtime._capture_pending)
        self.runtime._capture_pending_owner = None

    def test_settings_revoke_and_ui_values_cannot_grant_purpose(self):
        self.show()
        self.runtime.configure_settings(dict(mapSelectionMode='manual'))
        self.assertIsNone(self.current_payload())
        for value in (None, True, dict(candidateReady=True), 'native-s1-preview'):
            runtime = LiveRuntime(engine_factories=self.registry, capture_provider=CurrentFixtureCapture(),
                evidence_kind='live-client-capture', s1_candidate_purpose=value,
                recorded_s1_provisional_display=True, recorded_s1_streaming=True)
            try:
                runtime.configure_settings(dict(mapSelectionMode='auto', nativeCandidatePreviewAvailable=True))
                runtime.dispatch('set_context', dict(season='s1', mode='nightmare'))
                self.assertNotIn('start_round', runtime.snapshot()['availableCommands'])
                self.assertFalse(runtime.dispatch('start_round', dict(candidateReady=True))['success'])
            finally:
                runtime.shutdown()


class BakedPurposeContracts(unittest.TestCase):
    def test_only_exact_valid_auto_profile_mints_token(self):
        import build_qt_client as build
        with tempfile.TemporaryDirectory() as directory:
            profile = Path(directory) / 'installation-profile.json'
            with patch.object(sys, 'frozen', True, create=True), patch.object(sys, '_MEIPASS', directory, create=True):
                for name in ('s0-s1-test', 's1-floor-test', 's1-auto-test', 's1-playtest'):
                    profile.write_text(json.dumps(dict(product=build.PRODUCT,
                        installerInstance=dict(id=name, **build.INSTANCES[name]))))
                    self.assertIs(private_native_candidate_purpose(), _NATIVE_S1_PREVIEW if name in ('s1-auto-test', 's1-playtest') else None)


if __name__ == '__main__':
    unittest.main()
