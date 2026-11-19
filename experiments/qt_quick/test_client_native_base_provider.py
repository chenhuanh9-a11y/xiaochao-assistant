"""Actual imported engine construction; no replacement engine or game IO."""
from pathlib import Path
import hashlib
import json
import unittest
import zipfile
from unittest.mock import patch
from uuid import uuid4

import numpy as np

from client_imported_providers import build_imported_engine_factories, build_native_engine
from client_paths import RuntimePaths
from imported_original_base import decode_original_base
from imported_resource_activation import activate_catalog_locked
from imported_resource_archive import save_archive
from imported_resource_catalog import ResourceResolver
from imported_resource_selection import inspect_selection
from resource_transaction import resource_write_lock
from client_imported_providers import SUPPORTED_CAPABILITIES
from original_qt_resource_fixtures import OriginalS0NightmareSourceFixture


class NativeBaseProviderTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import tempfile
        source_temp = tempfile.TemporaryDirectory(prefix='original-s0n-provider-source-')
        cls.addClassCleanup(source_temp.cleanup)
        cls.source = OriginalS0NightmareSourceFixture(Path(source_temp.name))
        cls.source.export()

    def setUp(self):
        import tempfile
        temporary = tempfile.TemporaryDirectory()
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
            contexts={'s0:nightmare': dict(recognitionBase=wire(self.refs['base']),
                controls=wire(self.refs['controls']), reviewedGroups=None)})
        def validate(proposed):
            # Use the prospective views, including the second validation while
            # catalog publication is pending; no old active resolver fallback.
            inspection = inspect_selection(self.resolver.root, proposed,
                supported_capabilities=self.resolver.supported_capabilities,
                project_registry_refs=self.resolver.project_registry_refs)
            engine = build_native_engine(inspection.views[self.refs['base']],
                inspection.views[self.refs['controls']], self.paths)
            self.assertTrue(engine.qt_native_base.ready)
        stamp = self.resolver.snapshot().catalog_stamp
        with resource_write_lock(self.resolver.root):
            activate_catalog_locked(self.resolver.root, document,
                expected_catalog_stamp=stamp, validate=validate)

    def test_default_factory_builds_actual_typed_engine_and_structural_display(self):
        from difficult_native_identity import NativeDifficultCandidateEngine
        from map_identity_engine import MapIdentityEngine
        from client_route_preferences import prepare_display_layer
        self.activate()
        with patch('client_providers.EngineFactories._build', side_effect=AssertionError('legacy build entered')):
            self.factories.prepare_context('nightmare')
        report = self.factories.capabilities()['nightmare']
        self.assertTrue(report['available'], report)
        engine = self.factories.get_engine('nightmare')
        self.assertIsInstance(engine, MapIdentityEngine)
        self.assertNotIsInstance(engine, NativeDifficultCandidateEngine)
        self.assertEqual(report['inventory']['typedLayers'], 78)
        self.assertEqual(engine.qt_controls_profile.for_context('s0', 'nightmare').contract.floor_id_for(-1), 'B1')
        self.assertEqual(engine.native_similarity_index.groups, ())
        self.assertEqual(len(engine.qt_logical_group_registry.groups), 3)
        self.assertIsNone(engine.qt_optional_displays)
        self.assertTrue(Path(engine.memory_store.portable_seed_path).is_relative_to(
            self.resolver.root))
        # The restored donor deliberately disables portable exemplars in a
        # source/private profile. It must not create a stand-in seed file.
        from client_providers import PRODUCTION
        self.assertEqual(engine.memory_store.portable_seed_path.name,
            'nightmare-portable-layout-seeds.json' if PRODUCTION else '.qt-no-portable-seeds.json')
        if not PRODUCTION:
            self.assertFalse(engine.memory_store.portable_seed_path.exists())
        self.assertEqual(engine.memory_store.root, self.paths.memory_root('nightmare'))
        self.assertFalse(engine.memory_store.root.exists(), 'construction wrote personal data')
        map_id = next(iter(engine.pack.by_id))
        layer = prepare_display_layer(engine, map_id, '1', 'shared-structure')
        self.assertEqual(layer['effectiveProfileId'], 'shared-structure')
        self.assertEqual(layer['pixels'].shape, (8, 8, 4))
        np.testing.assert_array_equal(layer['matrixDisplayToReference'], np.eye(3))
        self.assertEqual(engine.pack.identity_profiles[map_id], 'zhan10-speed')

    def test_donor_front_group_selection_is_exact_and_scoped_to_floor_and_entrance(self):
        self.activate()
        engine = self.factories.get_engine('nightmare')
        registry = engine.qt_logical_group_registry
        group = registry.group_for('nightmare-04', floor='1', entrance_kind='front')
        self.assertEqual(group['memberIds'], ('nightmare-04', 'nightmare-12', 'nightmare-19'))
        self.assertIsNone(registry.group_for('nightmare-01', floor='1', entrance_kind='front'))
        self.assertIsNone(registry.group_for('nightmare-04', floor='2', entrance_kind='front'))
        self.assertIsNone(registry.group_for('nightmare-04', floor='1', entrance_kind='side'))
        registry.validate(group, engine.qt_shared_provider)

    def test_missing_to_activated_and_later_catalog_generation_rebuilds_cache(self):
        self.factories.prepare_context('nightmare')
        self.assertFalse(self.factories.capabilities()['nightmare']['available'])
        self.activate()
        with patch('imported_original_base.decode_original_base', wraps=decode_original_base) as decode:
            first = self.factories.get_engine('nightmare')
            self.assertIs(self.factories.get_engine('nightmare'), first)
            self.assertEqual(decode.call_count, 1)
        self.activate()
        with self.assertRaisesRegex(ValueError, 'superseded'):
            first.qt_shared_provider('shared-structure', 'nightmare-01', '1')
        second = self.factories.get_engine('nightmare')
        self.assertIsNot(first, second)
        self.assertIsNotNone(second.qt_shared_provider('shared-structure', 'nightmare-01', '1'))

    def test_catalog_change_after_build_rejects_its_late_available_result(self):
        from client_providers import ProviderUnavailable
        self.activate()
        original = self.factories._build
        def build(*args, **kwargs):
            engine = original(*args, **kwargs)
            self.activate()
            return engine
        with patch.object(self.factories, '_build', side_effect=build):
            with self.assertRaisesRegex(ProviderUnavailable, 'superseded'):
                self.factories.get_engine('nightmare')
        self.assertFalse(self.factories.capabilities()['nightmare']['available'])
        self.assertNotIn('s0:nightmare', self.factories._engines)
        self.assertIsNotNone(self.factories.get_engine('nightmare'))

    def test_cached_engine_recovers_from_a_transient_catalog_read_failure(self):
        from client_providers import ProviderUnavailable
        self.activate()
        engine = self.factories.get_engine('nightmare')
        with patch.object(self.resolver, 'snapshot', side_effect=OSError('controlled read failure')):
            with self.assertRaises(ProviderUnavailable):
                self.factories.get_engine('nightmare')
        self.assertFalse(self.factories.capabilities()['nightmare']['available'])
        self.assertIs(self.factories.get_engine('nightmare'), engine)
        self.assertTrue(self.factories.capabilities()['nightmare']['available'])
        self.assertEqual(self.factories.capabilities()['nightmare']['resourceGaps'], [])

    def test_initial_engine_recovers_when_the_same_catalog_becomes_readable(self):
        from client_providers import ProviderUnavailable
        self.activate()
        generation = self.resolver.snapshot().generation
        with patch.object(self.resolver, 'snapshot', side_effect=OSError('controlled first read failure')):
            with self.assertRaises(ProviderUnavailable):
                self.factories.get_engine('nightmare')
        self.assertFalse(self.factories.capabilities()['nightmare']['available'])
        self.assertNotIn('s0:nightmare', self.factories._engines)
        self.assertEqual(self.resolver.snapshot().generation, generation)
        self.assertIsNotNone(self.factories.get_engine('nightmare'))
        self.assertTrue(self.factories.capabilities()['nightmare']['available'])
        self.assertEqual(self.factories.capabilities()['nightmare']['resourceGaps'], [])

    def test_engine_recovers_from_a_catalog_read_failure_during_construction(self):
        from client_providers import ProviderUnavailable
        self.activate()
        snapshot, calls = self.resolver.snapshot, []
        def read():
            calls.append(True)
            if len(calls) == 2:
                raise OSError('controlled construction read failure')
            return snapshot()
        with patch.object(self.resolver, 'snapshot', side_effect=read):
            with self.assertRaises(ProviderUnavailable):
                self.factories.get_engine('nightmare')
        self.assertIsNotNone(self.factories.get_engine('nightmare'))
        self.assertTrue(self.factories.capabilities()['nightmare']['available'])

    def test_engine_recovers_after_its_current_check_temporarily_fails(self):
        from client_providers import ProviderUnavailable
        self.activate()
        engine = self.factories.get_engine('nightmare')
        with patch.object(engine, 'qt_assert_resource_current', side_effect=OSError('controlled guard read failure')):
            with self.assertRaises(ProviderUnavailable):
                self.factories.get_engine('nightmare')
        self.assertNotIn('s0:nightmare', self.factories._engines)
        self.assertIsNotNone(self.factories.get_engine('nightmare'))
        self.assertTrue(self.factories.capabilities()['nightmare']['available'])

    def test_engine_recovers_when_payload_io_failure_was_wrapped_by_decoder(self):
        from client_providers import ProviderUnavailable
        from imported_resource_reader import ComponentResourceReader
        self.activate()
        original, calls = ComponentResourceReader.__call__, []
        def read(reader, path):
            calls.append(path)
            if len(calls) == 1:
                raise PermissionError('controlled payload read failure')
            return original(reader, path)
        with patch.object(ComponentResourceReader, '__call__', read):
            with self.assertRaises(ProviderUnavailable):
                self.factories.get_engine('nightmare')
        self.assertTrue(calls)
        self.assertIsNotNone(self.factories.get_engine('nightmare'))
        self.assertTrue(self.factories.capabilities()['nightmare']['available'])

    def test_post_decode_entry_read_failure_is_not_cached_as_a_ready_engine(self):
        from client_providers import ProviderUnavailable
        from imported_resource_reader import ComponentResourceReader
        self.activate()
        original = ComponentResourceReader.__call__
        for target in ('entries.json', 'entry-passports.json', 'side-door-anchors.json', 'side-entry-search.npz'):
            with self.subTest(target=target):
                self.factories.invalidate()
                state = dict(decoded=False, raised=False)
                calls = []
                def decode(*args, **kwargs):
                    bundle = decode_original_base(*args, **kwargs)
                    state['decoded'] = True
                    return bundle
                def read(reader, path):
                    calls.append((state['decoded'], Path(path).name))
                    if state['decoded'] and not state['raised'] and Path(path).name == target:
                        state['raised'] = True
                        raise PermissionError()
                    return original(reader, path)
                with patch('imported_original_base.decode_original_base', side_effect=decode), \
                     patch.object(ComponentResourceReader, '__call__', read):
                    try:
                        self.factories.get_engine('nightmare')
                    except ProviderUnavailable as error:
                        self.assertIn('original-entry-index-unavailable', str(error))
                    else:
                        self.fail(f'failed entry read was accepted; state={state}, calls={calls}')
                self.assertTrue(state['raised'])
                self.assertFalse(self.factories.capabilities()['nightmare']['available'])
                self.assertNotIn('s0:nightmare', self.factories._engines)
                engine = self.factories.get_engine('nightmare')
                local = engine.side_entry_identity
                self.assertTrue(all(error is None for error in
                    (engine.entry_identity.error, local.passport_error, local.side_door_error, local.side_search_error)))

    def test_superseded_binding_is_rejected_before_engine_construction(self):
        from client_providers import ProviderUnavailable
        self.activate()
        binding = self.factories._resource_binding('nightmare')
        self.factories.invalidate()
        with patch.object(self.factories, '_resource_binding', return_value=binding), \
             patch.object(self.factories, '_build', side_effect=AssertionError('obsolete build entered')):
            with self.assertRaisesRegex(ProviderUnavailable, 'superseded'):
                self.factories.get_engine('nightmare')
        self.assertFalse(self.factories.capabilities()['nightmare']['available'])

    def test_superseded_binding_failure_cannot_replace_current_report(self):
        from client_imported_providers import ImportedProviderUnavailable
        from client_providers import ProviderUnavailable
        self.activate()
        binding = self.factories._resource_binding('nightmare')
        self.factories.invalidate()
        before = self.factories.capabilities()
        failure = ImportedProviderUnavailable('old-read-failed', ('old-gap',),
                                               resource_revision=binding.resource_revision)
        with patch.object(self.factories, '_resource_binding', side_effect=failure):
            with self.assertRaises(ProviderUnavailable):
                self.factories.get_engine('nightmare')
        self.assertEqual(self.factories.capabilities(), before)


if __name__ == '__main__':
    unittest.main()
