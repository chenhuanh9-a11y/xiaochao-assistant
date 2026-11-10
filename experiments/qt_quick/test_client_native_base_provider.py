"""Actual imported engine construction; no replacement engine or game IO."""
from pathlib import Path
import unittest
from unittest.mock import patch
from uuid import uuid4

import numpy as np

from client_imported_providers import build_imported_engine_factories, build_native_engine
from client_paths import RuntimePaths
from imported_native_base import decode_native_base
from imported_resource_activation import activate_catalog_locked
from imported_resource_archive import save_archive
from imported_resource_catalog import ResourceResolver
from imported_resource_selection import inspect_selection
from resource_transaction import resource_write_lock
import test_imported_native_base as base_fixture


class NativeBaseProviderTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        base_fixture.ImportedNativeBaseTests.setUpClass()
        cls.addClassCleanup(base_fixture.ImportedNativeBaseTests.doClassCleanups)

    def setUp(self):
        import tempfile
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.paths = RuntimePaths(Path(temporary.name).resolve(), False)
        self.resolver = ResourceResolver(self.paths.imported_resource_root)
        saved = save_archive(base_fixture.ImportedNativeBaseTests.archive, self.resolver.root)
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
            inspection = inspect_selection(self.resolver.root, proposed)
            engine = build_native_engine(inspection.views[self.refs['base']],
                inspection.views[self.refs['controls']], self.paths)
            self.assertTrue(engine.qt_native_base.ready)
        stamp = self.resolver.snapshot().catalog_stamp
        with resource_write_lock(self.resolver.root):
            activate_catalog_locked(self.resolver.root, document,
                expected_catalog_stamp=stamp, validate=validate)

    def test_default_factory_builds_actual_typed_engine_and_structural_display(self):
        from difficult_native_identity import NativeDifficultCandidateEngine
        from client_route_preferences import prepare_display_layer
        self.activate()
        with patch('client_providers.EngineFactories._build', side_effect=AssertionError('legacy build entered')):
            self.factories.prepare_context('nightmare')
        report = self.factories.capabilities()['nightmare']
        self.assertTrue(report['available'], report)
        engine = self.factories.get_engine('nightmare')
        self.assertIsInstance(engine, NativeDifficultCandidateEngine)
        self.assertEqual(report['inventory']['typedLayers'], 3)
        self.assertEqual(engine.qt_controls_profile.for_context('s0', 'nightmare').contract.floor_id_for(-1), 'B1')
        self.assertIsNone(engine.native_similarity_index)
        self.assertIsNone(engine.qt_logical_group_registry)
        self.assertIsNone(engine.qt_optional_displays)
        self.assertIsNone(engine.memory_store.portable_seed_path)
        self.assertEqual(engine.memory_store.root, self.paths.memory_root('nightmare'))
        self.assertFalse(engine.memory_store.root.exists(), 'construction wrote personal data')
        layer = prepare_display_layer(engine, 'fixture-map', '1', 'wang-qingxin')
        self.assertEqual(layer['effectiveProfileId'], 'shared-structure')
        self.assertEqual(layer['pixels'].shape, (8, 8, 4))
        np.testing.assert_array_equal(layer['matrixDisplayToReference'], np.eye(3))
        self.assertEqual(engine.pack.identity_profiles['fixture-map'], 'zhan10-speed')

    def test_missing_to_activated_and_later_catalog_generation_rebuilds_cache(self):
        self.factories.prepare_context('nightmare')
        self.assertFalse(self.factories.capabilities()['nightmare']['available'])
        self.activate()
        with patch('imported_native_base.decode_native_base', wraps=decode_native_base) as decode:
            first = self.factories.get_engine('nightmare')
            self.assertIs(self.factories.get_engine('nightmare'), first)
            self.assertEqual(decode.call_count, 1)
        self.activate()
        with self.assertRaisesRegex(ValueError, 'superseded'):
            first.qt_shared_provider('shared-structure', 'fixture-map', '1')
        second = self.factories.get_engine('nightmare')
        self.assertIsNot(first, second)
        self.assertIsNotNone(second.qt_shared_provider('shared-structure', 'fixture-map', '1'))

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
                def decode(*args, **kwargs):
                    bundle = decode_native_base(*args, **kwargs)
                    state['decoded'] = True
                    return bundle
                def read(reader, path):
                    if state['decoded'] and not state['raised'] and Path(path).name == target:
                        state['raised'] = True
                        raise PermissionError()
                    return original(reader, path)
                with patch('imported_native_base.decode_native_base', side_effect=decode), \
                     patch.object(ComponentResourceReader, '__call__', read):
                    with self.assertRaisesRegex(ProviderUnavailable, 'native-entry-construction-failed'):
                        self.factories.get_engine('nightmare')
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
