"""Complete S1D source fixture -> exported ZIP -> actual native decoder."""
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import zipfile

import imported_native_base as native
from s1_native_resources import MODE_LAYOUTS
import star_map_import as files
import test_imported_native_base as common_fixture
import test_export_s1_native_base_package as producer_fixture


class ImportedS1NativeBaseTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        temporary = tempfile.TemporaryDirectory(prefix='s1-native-decoder-source-')
        cls.addClassCleanup(temporary.cleanup)
        cls.source = producer_fixture.S1NativeBaseSourceFixture(Path(temporary.name).resolve())
        cls.source.export()
        cls.archive = cls.source.output
        with zipfile.ZipFile(cls.archive) as archive:
            cls.members = {name: archive.read(name) for name in archive.namelist()}

    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix='s1-native-decoder-view-')
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.counter = 0

    def view(self, changes=None, **kwargs):
        # Shared real ZIP mutation/save/view fixture; no decoder substitution.
        return common_fixture.ImportedNativeBaseTests.view(self, changes, **kwargs)

    @staticmethod
    def change(callback):
        return common_fixture.ImportedNativeBaseTests.change(callback)

    @staticmethod
    def decode(view):
        return native.decode_native_base(view, season='s1', mode='difficult')

    def test_complete_typed_bundle_preserves_proposal_only_roles_without_side_resources(self):
        view = self.view()
        with patch.object(view, 'read', wraps=view.read) as reads:
            bundle = self.decode(view)
        self.assertTrue(bundle.ready, bundle.gaps)
        self.assertEqual(set(bundle.pack.by_id), MODE_LAYOUTS['difficult'])
        self.assertEqual(bundle.pack.floors, ('-1', '-2', '-3', '-4'))
        self.assertEqual(len(bundle.resources.entries), 56)
        self.assertEqual(len(bundle.versions), 56)
        self.assertEqual(len(bundle.entry_identity.entries), 14)
        self.assertEqual(len(bundle.side_entry_identity.entries), 56)
        self.assertIsNone(bundle.entry_identity.error)
        self.assertIsNone(bundle.side_entry_identity.passport_error)
        self.assertEqual(bundle.side_entry_identity.side_door_error, 'not-nightmare')
        self.assertEqual(bundle.side_entry_identity.side_search_error, 'side-door-source-unavailable')
        self.assertEqual(bundle.side_entry_identity.side_doors, {})
        self.assertEqual(bundle.side_entry_identity.side_search, {})
        self.assertIsNone(bundle.exit_recognizer)
        for row, *_ in bundle.entry_identity.entries:
            self.assertEqual(row['floor'], '-1')
            self.assertIs(row['proposalOnly'], True)
            self.assertIs(row['authorizesPose'], False)
            self.assertIs(row['authorizesIdentity'], False)
        for row, *_ in bundle.side_entry_identity.entries:
            self.assertEqual(row['featureRole'], 'local-contour-proposals-not-semantic-side-exits')
            self.assertEqual(row['gates'], [])
        names = {str(call.args[0]) for call in reads.call_args_list}
        self.assertFalse(any(name.endswith(('side-door-anchors.json', 'side-entry-search.npz',
                                            'difficult-exit-icon.json')) for name in names))

    def test_missing_competitor_profile_and_floor_cannot_form_an_available_pool(self):
        cases = (
            lambda data: data['maps'].pop(),
            lambda data: data['maps'][0].update(identityProfileId='zhan10-speed'),
            lambda data: data['maps'][0]['layers'].update(B1=data['maps'][0]['layers'].pop('-4')),
        )
        for change in cases:
            with self.subTest(change=change), self.assertRaises(files.ImportRejected):
                self.decode(self.view({'references/map-pack.json': self.change(change)}))

    def test_unavailable_layer_stays_a_competitor_with_an_explicit_gap(self):
        view = self.view({'references/map-pack.json': self.change(
            lambda data: data['maps'][0]['layers'].update({'-4': None}))})
        bundle = self.decode(view)
        self.assertEqual(set(bundle.pack.by_id), MODE_LAYOUTS['difficult'])
        self.assertFalse(bundle.ready)
        self.assertEqual(len(bundle.gaps), 1)
        self.assertIn('native-layer-unavailable:', bundle.gaps[0])

    def test_entry_roles_cannot_claim_semantic_side_exits_or_identity(self):
        cases = (
            ('resource-context.json', lambda data: data['runtimeInterfaces'].update(entryEvidenceRole='semantic-side-exits')),
            ('references/entries/entries.json', lambda data: data['entries'][0].update(authorizesIdentity=True)),
            ('references/entries/entries.json', lambda data: data['entries'][0].update(authorizesPose=True)),
            ('references/entries/side-entries.json', lambda data: data['entries'][0].update(featureRole='semantic-side-exits')),
            ('references/entries/side-entries.json', lambda data: data['entries'][0].update(gates=[dict(center=[2, 2])])),
        )
        for path, change in cases:
            with self.subTest(path=path), self.assertRaises(files.ImportRejected):
                self.decode(self.view({path: self.change(change)}))

    def test_legacy_bridge_accepts_only_the_actual_identity_matrix(self):
        def translate(data):
            mid = next(iter(data['maps']))
            data['maps'][mid]['floors']['-1']['matrixCanonicalToLegacyRecognition'][0][2] = 1
        with self.assertRaisesRegex(files.ImportRejected, 'shared-canvas-matrix-invalid'):
            self.decode(self.view({'references/coordinate-pack.json': self.change(translate)}))

    def test_extractor_runtime_mismatch_is_an_explicit_data_gap(self):
        def mismatch(data):
            data['entries'][0]['extractorEnvironment']['opencv'] = 'unsupported-fixture-version'
        with self.assertRaises(native.NativeBaseUnavailable) as caught:
            self.decode(self.view({'references/entries/side-entries.json': self.change(mismatch)}))
        self.assertEqual(caught.exception.resource_gaps, ('native-base-extractor-runtime-mismatch',))

    def test_current_view_and_actual_payload_remain_required(self):
        current = [True]
        def guard():
            if not current[0]:
                raise ValueError('fixture catalog superseded')
        bundle = self.decode(self.view(assert_current=guard))
        current[0] = False
        with self.assertRaisesRegex(ValueError, 'superseded'):
            bundle.resources
        view = self.view()
        identity = json.loads(view.read('references/map-pack.json'))
        view.path(identity['maps'][0]['layers']['-1']).unlink()
        with self.assertRaises((files.ImportRejected, FileNotFoundError)):
            self.decode(view)


if __name__ == '__main__':
    unittest.main()
