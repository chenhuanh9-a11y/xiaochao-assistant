"""Default S1D factory -> recorded floor/scanner scope, without general live authority."""
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from uuid import uuid4

import numpy as np

from client_imported_providers import SUPPORTED_CAPABILITIES, build_imported_engine_factories, build_native_engine
from client_paths import RuntimePaths
from imported_resource_activation import activate_catalog_locked
from imported_resource_archive import save_archive
from imported_resource_catalog import ResourceResolver
from imported_resource_selection import inspect_selection
from resource_transaction import resource_write_lock
import test_export_s1_native_base_package as source_fixture


class ImportedS1ProviderTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        temporary = tempfile.TemporaryDirectory(prefix='s1-provider-source-')
        cls.addClassCleanup(temporary.cleanup)
        cls.source = source_fixture.S1NativeBaseSourceFixture(Path(temporary.name).resolve())
        cls.source.export()

    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix='s1-provider-active-')
        self.addCleanup(temporary.cleanup)
        self.paths = RuntimePaths(Path(temporary.name).resolve(), False)
        self.resolver = ResourceResolver(self.paths.imported_resource_root,
                                         supported_capabilities=SUPPORTED_CAPABILITIES)
        saved = save_archive(self.source.output, self.resolver.root)
        self.refs = {ref.component_id: ref for ref in saved.component_refs}
        self.factories = build_imported_engine_factories(self.resolver, self.paths)

    def activate(self):
        def wire(ref):
            return dict(packageId=ref.package_id, resourceVersion=ref.resource_version,
                componentId=ref.component_id, manifestSha256=ref.manifest_sha256)
        document = dict(schema='xingtu-resource-catalog-v1', generation=str(uuid4()), displays=[],
            contexts={'s1:difficult': dict(recognitionBase=wire(self.refs['base']),
                controls=wire(self.refs['controls']), reviewedGroups=None)})
        def validate(proposed):
            inspection = inspect_selection(self.resolver.root, proposed,
                                            supported_capabilities=SUPPORTED_CAPABILITIES)
            engine = build_native_engine(inspection.views[self.refs['base']],
                inspection.views[self.refs['controls']], self.paths, season='s1', mode='difficult')
            self.assertFalse(engine.qt_live_ready)
        stamp = self.resolver.snapshot().catalog_stamp
        with resource_write_lock(self.resolver.root):
            activate_catalog_locked(self.resolver.root, document,
                expected_catalog_stamp=stamp, validate=validate)

    def engine(self):
        return self.factories.get_engine('difficult', season='s1')

    def test_default_factory_constructs_data_and_shared_display_without_live_or_learning(self):
        from client_route_preferences import prepare_display_layer
        self.activate()
        with patch('client_providers.EngineFactories._build', side_effect=AssertionError('legacy builder entered')):
            engine = self.engine()
        report = self.factories.capabilities('s1')['difficult']
        self.assertTrue(report['available'], report)
        self.assertFalse(report['liveReady'])
        self.assertEqual(report['mapCount'], 14)
        self.assertEqual(report['floors'], ['-1', '-2', '-3', '-4'])
        self.assertEqual(report['inventory']['typedLayers'], 56)
        self.assertFalse(report['inventory']['semanticSideEntries'])
        self.assertTrue(report['candidateObservation']['candidateReady'])
        self.assertTrue(report['floorObservation']['manualMapObservation'])
        self.assertFalse(report['candidateObservation']['liveValidated'])
        self.assertFalse(engine.memory_store.sample_validator(np.zeros((8, 8, 3), np.uint8)))
        self.assertIsNone(engine.memory_store.portable_seed_path)
        self.assertFalse(engine.memory_store.root.exists())
        self.assertIsNone(engine.native_similarity_index)
        self.assertIsNone(engine.qt_logical_group_registry)
        self.assertIsNone(engine.qt_s1_private_coordinate_prepare)
        self.assertIsNone(engine.qt_s1_pose_prior)
        self.assertFalse(engine.side_entry_identity.semantic_side_entries)
        self.assertIsNone(engine.side_entry_identity.exit_icon_path)
        mid = self.source.ids[0]
        display = prepare_display_layer(engine, mid, '-1', 'shared-structure')
        self.assertEqual(display['displayKind'], 'shared-structure')
        np.testing.assert_array_equal(display['matrixDisplayToReference'], np.eye(3))
        self.assertEqual(display['pixels'].shape, (8, 8, 4))

    def test_actual_rank_uses_s1_scanner_and_keeps_all_unverified_competitors(self):
        self.activate()
        engine = self.engine()
        with patch.object(engine.entry_identity, 'rank', side_effect=AssertionError('old entry rank used')), \
             patch.object(engine.side_entry_identity, 'rank', side_effect=AssertionError('old side rank used')), \
             patch.object(engine, 'qt_s1_structure_rank', wraps=engine.qt_s1_structure_rank) as scanner:
            rows = engine.rank(np.zeros((1200, 1920, 3), np.uint8), floor='-1')
        self.assertEqual(scanner.call_count, 1)
        self.assertIsInstance(rows, list)
        report = engine.last_s1_structure_scan
        self.assertEqual(report['expectedMapIds'], list(self.source.ids))
        self.assertEqual(set(report['checkedMapIds']) | set(report['unscannedMapIds']), set(self.source.ids))
        self.assertEqual(report['excludedMapIds'], [])
        self.assertFalse(report['uniqueIdentityVerified'])
        self.assertFalse(report['identityHardGatePassed'])
        self.assertFalse(report['automaticSelectionAuthorized'])
        self.assertFalse(report['liveAuthorization'])
        self.assertFalse(engine.memory_store.root.exists())

    def test_current_catalog_is_required_by_scanner_and_cached_context(self):
        self.activate()
        first = self.engine()
        self.assertIs(self.engine(), first)
        self.activate()
        with self.assertRaisesRegex(ValueError, 'superseded'):
            first.qt_native_context.manifest
        with self.assertRaisesRegex(ValueError, 'superseded'):
            first.qt_s1_structure_scan(np.zeros((1200, 1920, 3), np.uint8), floor='-1')
        self.assertIsNot(self.engine(), first)


if __name__ == '__main__':
    unittest.main()
