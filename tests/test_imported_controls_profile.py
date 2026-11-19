"""PackageView-backed decoder and external-template classifier seam tests."""
from io import BytesIO
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import cv2
import numpy as np

import imported_resource_packages as packages
from imported_controls_profile import S0ControlsProfile
from s0_floor_templates import LABELS, HEIGHT, WIDTH, classify_label


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':')).encode('utf-8')


def write_npy(array, *, allow_pickle=False):
    stream = BytesIO()
    array = np.asarray(array)
    np.lib.format.write_array(stream, array, version=(1, 0),
                              allow_pickle=allow_pickle or array.dtype.hasobject)
    return stream.getvalue()


def manual_one_patch():
    """Draw a standalone numeral independent of either template generator."""
    patch = np.full((64, 64), 33, np.uint8)
    cv2.putText(patch, '1', (23, 51), cv2.FONT_HERSHEY_SIMPLEX,
                1.5, 225, 2, cv2.LINE_8)
    return patch


def normalized_manual_vector(image):
    """Prepare the manually drawn glyph as a stored, external bank row."""
    crop = image.astype(np.float32)
    signed = crop - np.median(crop)
    strength = float(np.percentile(signed, 99))
    ink = np.clip(signed / strength * 255, 0, 255)
    stats = cv2.connectedComponentsWithStats((ink > 80).astype(np.uint8), 8)[2][1:]
    rows = [row for row in stats if .3 * crop.shape[0] < row[3] < .95 * crop.shape[0]]
    if len(rows) != 1:
        raise AssertionError(f'manual fixture did not produce one glyph: {rows}')
    dx, dy, dw, dh, _ = rows[0]
    scale = 40 / dh
    matrix = np.float32([[scale, 0, 60 - scale * (dx + (dw - 1) / 2)],
                         [0, scale, 31.5 - scale * (dy + (dh - 1) / 2)]])
    normalized = cv2.warpAffine(ink, matrix, (WIDTH, HEIGHT), flags=cv2.INTER_LINEAR)
    normalized = cv2.GaussianBlur(normalized, (0, 0), .5)
    vector = normalized.ravel() - normalized.mean()
    vector /= max(1e-9, float(np.linalg.norm(vector)))
    return vector.astype(np.float32)


def bank_vectors():
    rows = []
    manual = normalized_manual_vector(manual_one_patch())
    rows.append(manual)
    # Two rows per supported class so dropping any competitor is testable.
    for index, _label in enumerate(LABELS):
        if index == 0:
            rows.append(manual.copy())
            continue
        for sample in range(2):
            vector = np.zeros(HEIGHT * WIDTH, np.float32)
            left = 256 + index * 4 + sample * 2
            vector[left] = 2 ** -.5
            vector[left + 1] = -(2 ** -.5)
            rows.append(vector)
    return np.asarray(rows, np.float32), [label for label in LABELS for _ in range(2)]


class ProfilePackage:
    def __init__(self, root, *, entrypoint=None, vectors=None, labels=None,
                 component_contexts=None, guard=None, declared_npy=True, npy=None):
        self.root = Path(root)
        self.component_contexts = component_contexts or [
            {'season': 's0', 'mode': 'nightmare'},
            {'season': 's0', 'mode': 'difficult'},
        ]
        vectors, default_labels = bank_vectors() if vectors is None else (vectors, None)
        labels = default_labels if labels is None else labels
        self.entrypoint = entrypoint or self.document(labels)
        self.npy = write_npy(vectors) if npy is None else npy
        self.data = {'profile.json': canonical(self.entrypoint)}
        if declared_npy:
            self.data['templates.npy'] = self.npy
        self.package_root = self.root / 'packages' / 'demo' / 'v1'
        data_root = self.package_root / 'data' / 'controls'
        data_root.mkdir(parents=True)
        file_rows = []
        for name, raw in self.data.items():
            (data_root / name).write_bytes(raw)
            file_rows.append({'componentId': 'controls', 'path': name,
                              'bytes': len(raw), 'sha256': hashlib.sha256(raw).hexdigest()})
        manifest = {
            'schema': 'xingtu-resource-package', 'formatVersion': 1,
            'packageId': 'demo', 'resourceVersion': 'v1',
            'clientCompatibility': {'resourceProtocol': 1, 'requiredCapabilities': []},
            'components': [{
                'componentId': 'controls', 'type': 'controls-profile',
                'schemaVersion': 1, 'contexts': self.component_contexts,
                'entrypoint': 'profile.json', 'dependencies': {},
            }],
            'files': file_rows,
        }
        manifest_bytes = canonical(manifest)
        (self.package_root / 'manifest.json').write_bytes(manifest_bytes)
        ref = packages.ComponentRef('demo', 'v1', 'controls',
                                    hashlib.sha256(manifest_bytes).hexdigest())
        self.view = packages.PackageView(self.root, ref, assert_current=guard)

    @staticmethod
    def document(labels):
        return {
            'schema': 'xingtu-s0-controls-profile-v1',
            'detectorId': 's0-signed-templates-v1',
            'contexts': {
                's0:nightmare': {
                    'floors': [{'observedValue': -1, 'floorId': 'B1'},
                               {'observedValue': 1, 'floorId': '1'},
                               {'observedValue': 2, 'floorId': '2'}],
                    'visualOrder': [2, 1, -1],
                },
                's0:difficult': {
                    'floors': [{'observedValue': 1, 'floorId': '1'},
                               {'observedValue': 2, 'floorId': '2'}],
                    'visualOrder': [2, 1],
                },
            },
            'fixedSearchRegion': [.75, .15, .18, .45],
            'expandedSearchRegion': [.55, .04, .435, .89],
            'templateBank': {
                'path': 'templates.npy', 'labels': list(labels),
                'family': 'external-controls-v1', 'shapeHW': [64, 96],
                'digitHeight': 40,
            },
        }


class ImportedControlsProfileTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)

    def package(self, **kwargs):
        root = Path(self.temp.name) / f'package-{len(list(Path(self.temp.name).glob("package-*")))}'
        root.mkdir()
        return ProfilePackage(root, **kwargs)

    def test_reads_real_declared_package_and_exposes_immutable_s0_contexts(self):
        package = self.package()
        profile = S0ControlsProfile(package.view)
        self.assertEqual(profile.detector_id, 's0-signed-templates-v1')
        self.assertEqual(profile.for_context('s0', 'nightmare').visual_order, (2, 1, -1))
        self.assertEqual(profile.for_context('s0', 'difficult').contract.floor_ids, ('1', '2'))
        self.assertIsNone(profile.for_context('s1', 'nightmare'))
        self.assertEqual(profile.fixed_search_region, (.75, .15, .18, .45))
        self.assertEqual(profile.templates.vectors.shape, (len(LABELS) * 2, 6144))
        self.assertEqual(set(profile.templates.labels.tolist()), set(LABELS))
        self.assertFalse(profile.templates.vectors.flags.writeable)
        self.assertFalse(profile.templates.labels.flags.writeable)
        with self.assertRaises(TypeError):
            profile.contracts['s0:nightmare'] = None
        with self.assertRaises(ValueError):
            profile.templates.vectors.flags.writeable = True

    def test_classifier_uses_external_bank_for_an_independently_drawn_label(self):
        package = self.package()
        with patch('s0_floor_templates._bank', side_effect=AssertionError('synthetic fallback used')):
            profile = S0ControlsProfile(package.view)
            result = classify_label(manual_one_patch(), selected=False,
                                    templates=profile.templates)
        self.assertEqual(result['label'], 1, result)
        self.assertEqual(result['family'], 'external-controls-v1')
        self.assertEqual(set(result['scores']), set(LABELS))

    def test_entrypoint_must_match_component_s0_contexts_and_contracts(self):
        package = self.package(component_contexts=[{'season': 's0', 'mode': 'nightmare'}])
        with self.assertRaisesRegex(ValueError, 'contexts-component-mismatch'):
            S0ControlsProfile(package.view)

        doc = ProfilePackage.document(bank_vectors()[1])
        doc['contexts']['s0:nightmare']['floors'][0]['floorId'] = '../B1'
        package = self.package(entrypoint=doc)
        with self.assertRaisesRegex(ValueError, 'floor-invalid'):
            S0ControlsProfile(package.view)

        doc = ProfilePackage.document(bank_vectors()[1])
        doc['contexts']['s0:nightmare']['visualOrder'] = [2, 1, 1]
        package = self.package(entrypoint=doc)
        with self.assertRaisesRegex(ValueError, 'visual-order-invalid'):
            S0ControlsProfile(package.view)

        for mutate in ('missing-floor', 'unsupported-observation'):
            with self.subTest(mutate=mutate):
                doc = ProfilePackage.document(bank_vectors()[1])
                floors = doc['contexts']['s0:nightmare']['floors']
                if mutate == 'missing-floor':
                    floors.pop(0)
                else:
                    floors[0]['observedValue'] = -2
                package = self.package(entrypoint=doc)
                with self.assertRaisesRegex(ValueError, 'floor-contract-mismatch'):
                    S0ControlsProfile(package.view)

    def test_package_floor_ids_are_external_metadata_for_supported_observations(self):
        doc = ProfilePackage.document(bank_vectors()[1])
        floor_ids = {-1: 'Basement', 1: 'LevelOne', 2: 'LevelTwo'}
        for row in doc['contexts']['s0:nightmare']['floors']:
            row['floorId'] = floor_ids[row['observedValue']]
        package = self.package(entrypoint=doc)
        profile = S0ControlsProfile(package.view)
        contract = profile.for_context('s0', 'nightmare').contract
        self.assertEqual(contract.observed_values, (-1, 1, 2))
        self.assertEqual(contract.floor_id_for(-1), 'Basement')
        self.assertEqual(contract.floor_id_for(1), 'LevelOne')
        self.assertEqual(contract.floor_id_for(2), 'LevelTwo')

    def test_component_declaring_any_s1_context_is_rejected_explicitly(self):
        contexts = [
            {'season': 's0', 'mode': 'nightmare'},
            {'season': 's0', 'mode': 'difficult'},
            {'season': 's1', 'mode': 'nightmare'},
        ]
        package = self.package(component_contexts=contexts)
        with self.assertRaisesRegex(ValueError, 'component-context-unsupported'):
            S0ControlsProfile(package.view)

    def test_regions_detector_family_and_all_competitor_labels_are_required(self):
        for field, value, reason in (
                ('fixedSearchRegion', [0.9, 0.1, .2, .3], 'fixed-search-region-invalid'),
                ('fixedSearchRegion', [.1, .1, True, .3], 'fixed-search-region-invalid'),
                ('fixedSearchRegion', [10 ** 400, .1, .2, .3], 'fixed-search-region-invalid'),
                ('detectorId', 's1-four-floor-guess', 'detector-unsupported'),
                ('templateBank', None, 'template-metadata-invalid')):
            with self.subTest(field=field):
                doc = ProfilePackage.document(bank_vectors()[1])
                doc[field] = value
                package = self.package(entrypoint=doc)
                with self.assertRaisesRegex(ValueError, reason):
                    S0ControlsProfile(package.view)

        doc = ProfilePackage.document(bank_vectors()[1])
        doc['templateBank']['labels'] = [1] * len(doc['templateBank']['labels'])
        package = self.package(entrypoint=doc)
        with self.assertRaisesRegex(ValueError, 'template-labels-incomplete'):
            S0ControlsProfile(package.view)

    def test_npy_shape_dtype_length_nan_and_row_labels_are_checked(self):
        vectors, labels = bank_vectors()
        bad_cases = (
            ('shape', np.ones((12, 1024), np.float32), labels, 'npy-shape-or-dtype-invalid'),
            ('dtype', vectors.astype(np.float64), labels, 'npy-shape-or-dtype-invalid'),
            ('object', np.full(vectors.shape, None, dtype=object), labels,
             'npy-shape-or-dtype-invalid'),
            ('nan', np.where(np.arange(vectors.size).reshape(vectors.shape) == 0,
                             np.nan, vectors), labels, 'npy-nonfinite'),
            ('zero-norm', np.zeros_like(vectors), labels, 'npy-vector-normalization-invalid'),
        )
        for name, bad_vectors, bad_labels, reason in bad_cases:
            with self.subTest(name=name):
                package = self.package(vectors=bad_vectors, labels=bad_labels)
                with self.assertRaisesRegex(ValueError, reason):
                    S0ControlsProfile(package.view)

        header = BytesIO()
        np.lib.format.write_array_header_1_0(
            header, {'descr': '<f4', 'fortran_order': False, 'shape': (4097, 6144)})
        package = self.package(npy=header.getvalue())
        with self.assertRaisesRegex(ValueError, 'npy-shape-or-dtype-invalid'):
            S0ControlsProfile(package.view)

        valid = self.package()
        with self.subTest(name='payload-length'):
            package = self.package(npy=valid.npy[:-1])
            with self.assertRaisesRegex(ValueError, 'npy-payload-length-invalid'):
                S0ControlsProfile(package.view)

        with self.subTest(name='header'):
            package = self.package(npy=b'\x93NUMPY\x01\x00bad-header')
            with self.assertRaisesRegex(ValueError, 'npy-header-invalid'):
                S0ControlsProfile(package.view)

        doc = ProfilePackage.document(bank_vectors()[1])
        doc['templateBank']['labels'] = [1] * len(doc['templateBank']['labels'])
        package = self.package(entrypoint=doc)
        with self.assertRaisesRegex(ValueError, 'template-labels-incomplete'):
            S0ControlsProfile(package.view)

        doc = ProfilePackage.document(bank_vectors()[1])
        doc['templateBank']['labels'].pop()
        package = self.package(entrypoint=doc)
        with self.assertRaisesRegex(ValueError, 'template-label-count-mismatch'):
            S0ControlsProfile(package.view)

    def test_cached_context_checks_catalog_and_actual_manifest_identity(self):
        alive = [True]
        def check_current():
            if not alive[0]:
                raise packages.files.ImportRejected('resource-catalog-superseded')
        package = self.package(guard=check_current)
        profile = S0ControlsProfile(package.view)
        self.assertIsNotNone(profile.for_context('s0', 'nightmare'))
        alive[0] = False
        with self.assertRaisesRegex(ValueError, 'resource-catalog-superseded'):
            profile.for_context('s0', 'nightmare')

        package = self.package()
        profile = S0ControlsProfile(package.view)
        manifest = package.package_root/'manifest.json'
        replacement = manifest.with_suffix('.replacement')
        replacement.write_bytes(manifest.read_bytes())
        replacement.replace(manifest)
        with self.assertRaises(ValueError):
            profile.for_context('s0', 'nightmare')

    def test_undeclared_npy_and_stale_catalog_guard_fail_without_fallback(self):
        package = self.package(declared_npy=False)
        with self.assertRaisesRegex(ValueError, 'file-not-declared'):
            S0ControlsProfile(package.view)

        alive = [True]

        def assert_current():
            if not alive[0]:
                raise packages.files.ImportRejected('resource-catalog-superseded')

        package = self.package(guard=assert_current)
        alive[0] = False
        with self.assertRaisesRegex(ValueError, 'resource-catalog-superseded'):
            S0ControlsProfile(package.view)


if __name__ == '__main__':
    unittest.main()
