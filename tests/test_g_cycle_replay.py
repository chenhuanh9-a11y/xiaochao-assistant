"""Stored synthetic bytes only; a replay never establishes map/pose truth."""
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
import zipfile

import numpy as np
from g_cycle_evidence import GCycleEvidenceRecorder
from replay_s1_crop_observation import load_g_cycle_case, replay


class CaseReplayTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)/'g-cycle-test-evidence'
        self.recorder = GCycleEvidenceRecorder(self.root, enabled=True, full_input=True)
        self.assertTrue(self.recorder.start())
        self.addCleanup(lambda: self.recorder.shutdown(wait=True))
        self.image = np.arange(30*40*3, dtype=np.uint8).reshape(30, 40, 3)

    def save(self, **updates):
        meta = dict(identity='fixture', session='fixture', round=1, cycle=1,
            opening='a'*32, attempt='b'*32, frame='frame-1', outcome='failure', reason='fit-failed',
            crop_bounds=[0, 0, 40, 30], full_size=[40, 30],
            input_scope='solver-client-input', sample_role='solver-client-input',
            roi_provenance='existing-solver-client-frame', observation_bounds=[[3, 4, 20, 25]])
        meta.update(updates)
        reply = self.recorder.submit(self.image, meta)
        self.assertEqual(reply['status'], 'queued')
        self.assertTrue(self.recorder.idle())
        return self.root/f"case-{reply['case_id']}.json"

    def test_full_saved_input_is_exact_and_has_no_synthetic_padding(self):
        path = self.save()
        image, meta, full, digest = load_g_cycle_case(path)
        np.testing.assert_array_equal(image, self.image)
        self.assertTrue(full)
        self.assertEqual(meta['observation_bounds'], [[3, 4, 20, 25]])
        self.assertEqual(digest, hashlib.sha256(path.with_suffix('.png').read_bytes()).hexdigest())

    def test_crop_never_claims_full_client_input(self):
        path = self.save(input_scope='algorithm-roi', sample_role='structure-input',
            crop_bounds=[5, 7, 40, 30], full_size=[1280, 720],
            roi_provenance='existing-structure-extractor')
        image, meta, full, _ = load_g_cycle_case(path)
        self.assertFalse(full)
        self.assertEqual(image.shape, (30, 40, 3))
        self.assertEqual(meta['crop_bounds'][:2], [5, 7])

    def test_tampered_png_is_rejected(self):
        path = self.save()
        with path.with_suffix('.png').open('ab') as file:
            file.write(b'changed')
        with self.assertRaisesRegex(ValueError, 'png-digest'):
            load_g_cycle_case(path)

    def test_tampered_raw_hash_is_rejected(self):
        path = self.save()
        doc = json.loads(path.read_text())
        doc['raw_sha256'] = '0'*64
        path.write_text(json.dumps(doc))
        with self.assertRaisesRegex(ValueError, 'raw-digest'):
            load_g_cycle_case(path)

    def test_invalid_id_cannot_select_another_file(self):
        path = self.save()
        doc = json.loads(path.read_text())
        doc['case_id'] = '../other'
        path.write_text(json.dumps(doc))
        with self.assertRaisesRegex(ValueError, 'input-unavailable'):
            load_g_cycle_case(path)

    def test_oversized_metadata_is_rejected_before_decoding_image(self):
        path = self.save()
        path.write_bytes(b' '*(64*1024+1))
        with self.assertRaisesRegex(ValueError, 'input-too-large'):
            load_g_cycle_case(path)

    def test_standard_diagnostics_export_excludes_full_input_case(self):
        from diagnostic_export import export_safe_diagnostic_bundle
        case = self.save()
        live = Path(self.temp.name)/'nightmare-live'
        live.mkdir()
        result = export_safe_diagnostic_bundle(Path(self.temp.name), live,
            app_version='synthetic', build_fingerprint='TEST1234',
            process_integrity=dict(level='fixture'), package_metadata=dict(status='fixture'),
            include_automatic_failure_evidence=True, failure_roots=(Path(self.temp.name),))
        with zipfile.ZipFile(result['path']) as archive:
            self.assertFalse(any('g-cycle-test-evidence' in name or case.stem in name
                                 or name.endswith('.png') for name in archive.namelist()))
            self.assertEqual(result['includedFailureImageCount'], 0)

    def test_case_cli_preserves_saved_structure_roi_observation_and_coordinates(self):
        from single_map_structure import current_geometry, observed_corner_chains
        frame = np.zeros((720, 1280, 3), np.uint8)
        frame[200:400, 300:600] = (150, 180, 190)
        original = current_geometry(frame)
        self.assertEqual(np.count_nonzero(original['query']), 61004)
        x0, y0, x1, y1 = original['observationBounds']
        self.image = frame[y0:y1, x0:x1].copy()
        self.assertEqual(self.image.shape, (216, 316, 3))
        case = self.save(input_scope='algorithm-roi', sample_role='structure-input',
            crop_bounds=[x0, y0, x1-x0, y1-y0], full_size=[1280, 720],
            roi_provenance='existing-structure-extractor',
            observation_bounds=[list(original['observationBounds'])])
        reference = np.zeros((40, 60), np.uint8)
        reference[5:35, 5:55] = 1
        reference_path = Path(self.temp.name)/'reference.npz'
        np.savez(reference_path, shared_mask=reference)
        output = Path(self.temp.name)/'replay.json'
        result = subprocess.run([sys.executable, str(Path(__file__).resolve().parents[1]/
            'tools/replay_s1_crop_observation.py'), '--case', str(case), '--reference',
            str(reference_path), '--output', str(output)], capture_output=True, text=True, timeout=20)
        self.assertEqual(result.returncode, 0, result.stderr)
        report = json.loads(output.read_text())
        row = next(row for row in report['rows'] if row['requestedScale'] == 1.)
        self.assertEqual(row['queryPixels'], 61004)
        self.assertEqual(row['cropLocalObservationBounds'], [0, 0, 316, 216])
        self.assertEqual(report['cropOriginClientXY'], [292, 192])
        self.assertEqual(report['originalCropShapeHW'], [216, 316])
        self.assertEqual(report['scope'], 'crop-observation-only')
        self.assertFalse(report['fullInputRecorded'])
        self.assertFalse(report['fullFrameReproduction'])
        self.assertFalse(report['fullSolverReproduction'])
        self.assertFalse(report['componentEligibilityReplayed'])
        chains = observed_corner_chains(frame, original)
        self.assertTrue(chains)
        np.testing.assert_allclose(row['exampleClientChainCenters'],
            [chain['points'][1] for chain in chains[:6]])

    def test_full_input_keeps_client_component_filter_instead_of_roi_bypass(self):
        frame = np.full((216, 316, 3), (150, 180, 190), np.uint8)
        reference = np.ones((30, 40), np.uint8)
        full = replay(frame, reference, [0, 0], scales=(1.,), retained_roi=False)
        self.assertEqual(full['rows'][0]['queryPixels'], 0)
        self.assertTrue(full['componentEligibilityReplayed'])

    def test_case_cli_rejects_floor_controls_as_structure_roi(self):
        case = self.save(sample_role='floor-control-input',
            roi_provenance='existing-floor-detector-region')
        output = Path(self.temp.name)/'replay.json'
        result = subprocess.run([sys.executable, str(Path(__file__).resolve().parents[1]/
            'tools/replay_s1_crop_observation.py'), '--case', str(case), '--reference',
            str(Path(self.temp.name)/'not-needed.npz'), '--output', str(output)],
            capture_output=True, text=True, timeout=20)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('case-not-structure-input', result.stderr)
        self.assertFalse(output.exists())


if __name__ == '__main__':
    unittest.main()
