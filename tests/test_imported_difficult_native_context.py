"""Mixed S0D data context over complete exported synthetic resources."""
from dataclasses import replace
from pathlib import Path
import tempfile
import unittest
import zipfile

from client_providers import SharedReferenceProvider
from imported_controls_profile import S0ControlsProfile
from imported_difficult_native_context import ImportedDifficultNativeContext
from imported_resource_packages import PackageView
import star_map_import as files
import test_export_s0_difficult_native_base_package as difficult_fixture
import test_imported_native_base as base_fixture


class ImportedDifficultNativeContextTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        temporary = tempfile.TemporaryDirectory(prefix='s0d-context-source-')
        cls.addClassCleanup(temporary.cleanup)
        cls.source = difficult_fixture.S0DifficultSourceFixture(Path(temporary.name))
        cls.source.export()
        cls.archive = cls.source.output
        with zipfile.ZipFile(cls.archive) as archive:
            cls.members = {p:archive.read(p) for p in archive.namelist()}

    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix='s0d-context-view-')
        self.addCleanup(temporary.cleanup)
        self.root, self.counter = Path(temporary.name), 0

    view = base_fixture.ImportedNativeBaseTests.view

    def components(self):
        view = self.view()
        bundle = difficult_fixture.decode_s0_difficult(view)
        controls = S0ControlsProfile(PackageView(view.resource_root,
            replace(view.component_ref, component_id='controls')))
        return view, bundle, controls

    def test_complete_references_keep_legacy_authority_and_pending_native_bindings(self):
        context = ImportedDifficultNativeContext(*self.components())
        self.assertEqual(len(context.pack.maps), 30)
        self.assertEqual(len(context.resources.entries), 58)
        self.assertEqual(len(context.legacy_bindings), 2)
        self.assertEqual(len(context.pending_native_bindings), 2)
        self.assertFalse(context.structure_candidate_capability['candidateReady'])
        self.assertFalse(context.structure_candidate_capability['operatorDeclaredScanReady'])
        self.assertTrue(context.live_gaps)
        provider = SharedReferenceProvider(context.root, context.resources, context.versions,
            native=context, check_current=context.assert_current)
        for mid, profile in ((difficult_fixture.IDS[0], 'shared-structure'),
                             (difficult_fixture.LEGACY, 'zhan10-speed')):
            for floor in ('1', '2'):
                self.assertTrue(context.has_reference_binding(mid, floor))
                reference = provider(profile, mid, floor)
                self.assertIsNotNone(reference)
                if mid == difficult_fixture.LEGACY:
                    self.assertEqual(reference.metadata['sourceKind'], 'legacy-shared-binary')
                    self.assertIs(reference.metadata['authorizesMapIdentity'], False)
                    self.assertIs(reference.metadata['authorizesStructureValidation'], False)
                    self.assertIsNone(reference.metadata['sourceLayout'])
                    self.assertEqual(reference.metadata['staticAcceptance'], 'working-draft-not-batch-accepted')
        self.assertFalse(context.has_reference_binding('unknown-map', '1'))
        self.assertIsNone(context.catalog)
        self.assertEqual(context.adapters.matrix_profile_to_canonical('shared-structure', difficult_fixture.LEGACY, '1')[0,2], -2)
        with self.assertRaises(files.ImportRejected):
            context.local('assets/author.png')

    def test_other_installation_cannot_supply_the_base_view(self):
        _, bundle, controls = self.components()
        other = self.view()
        with self.assertRaisesRegex(files.ImportRejected, 'base-bundle-mismatch'):
            ImportedDifficultNativeContext(other, bundle, controls)

    def test_actual_manifest_replacement_invalidates_metadata_and_cached_reference(self):
        context = ImportedDifficultNativeContext(*self.components())
        provider = SharedReferenceProvider(context.root, context.resources, context.versions,
            native=context, check_current=context.assert_current)
        self.assertIsNotNone(provider('zhan10-speed', difficult_fixture.LEGACY, '1'))
        manifest = context.root.parent.parent/'manifest.json'
        replacement = manifest.with_suffix('.replacement')
        replacement.write_bytes(manifest.read_bytes())
        replacement.replace(manifest)
        calls = (lambda:context.root, lambda:context.pending_native_bindings,
            lambda:context.legacy_bindings, lambda:context.pack,
            lambda:context.legacy_reference(difficult_fixture.LEGACY, '1'),
            lambda:provider('zhan10-speed', difficult_fixture.LEGACY, '1'))
        for call in calls:
            with self.subTest(call=call), self.assertRaises(files.ImportRejected):
                call()

    def test_returned_pending_and_legacy_metadata_cannot_rewrite_context(self):
        context = ImportedDifficultNativeContext(*self.components())
        context.pending_native_bindings.clear()
        bindings = context.legacy_bindings
        next(iter(bindings.values()))['sourceLayout'] = 'forged-native'
        self.assertEqual(len(context.pending_native_bindings), 2)
        self.assertTrue(all(row['sourceLayout'] is None for row in context.legacy_bindings.values()))


if __name__ == '__main__':
    unittest.main()
