"""Production imported provider preparation/cache -> real RAM-only solver."""
from contextlib import ExitStack, contextmanager
import builtins
import io
import os
from pathlib import Path
import threading
import unittest
from unittest.mock import patch

import numpy as np

from client_providers import prepare_pose_solver, pose_inputs_current, ProviderUnavailable
from structure_pose_inputs import PoseInputsUnavailable
from structure_pose_solver import PreparedStructurePoseSolver
import test_client_native_base_provider as native_fixture


class PreparedAlignmentProviderTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        native_fixture.NativeBaseProviderTests.setUpClass()
        cls.addClassCleanup(native_fixture.NativeBaseProviderTests.doClassCleanups)

    def setUp(self):
        self.source = native_fixture.NativeBaseProviderTests('runTest')
        self.source.setUp()
        self.addCleanup(self.source.doCleanups)
        self.source.activate()
        self.engine = self.source.factories.get_engine('nightmare')
        self.mid = self.engine.pack.maps[0].id
        self.frame = np.zeros((720, 1280, 3), np.uint8)

    @contextmanager
    def no_resource_io(self):
        # Keep owner/read callback identities intact. Block the actual file
        # operations for warm preparation and numerical fit, with no exceptions
        # for manifests, metadata or alternate path spellings.
        def denied(*args, **kwargs):
            raise AssertionError('cached-compute-touched-component-resource')
        with ExitStack() as stack:
            for owner, name in ((Path, 'stat'), (Path, 'resolve'), (Path, 'read_bytes'),
                                (os, 'open'), (os, 'stat'), (os, 'lstat'),
                                (builtins, 'open'), (io, 'open')):
                stack.enter_context(patch.object(owner, name, side_effect=denied))
            yield

    def test_imported_preparation_reuses_ram_without_resource_io(self):
        first, cold = prepare_pose_solver(self.engine, self.mid, '1')
        self.assertIsInstance(first, PreparedStructurePoseSolver)
        self.assertFalse(cold['cacheHit'])
        with self.no_resource_io():
            second, warm = prepare_pose_solver(self.engine, self.mid, '1')
        with self.no_resource_io():
            fit = second.fit(self.frame, self.mid, '1', lambda: False)
            self.assertTrue(pose_inputs_current(self.engine, second.inputs))
        self.assertTrue(warm['cacheHit'])
        self.assertIs(second.inputs, first.inputs)
        self.assertFalse(fit['success'])
        # A positive control uses the real component reader with an extended
        # lexical alias that escapes the reader's exact-root fast path but
        # resolves back into the same component file.
        reference = second.inputs.reference('shared-structure', self.mid, '1')
        component_reader = self.engine.qt_native_base._values['reader']
        relative = Path(component_reader.relative_path(reference.path))
        component_root = self.engine.qt_native_base._root
        alias = component_root.parent / '..' / component_root.parent.name / component_root.name / relative
        with self.no_resource_io():
            with self.assertRaisesRegex(AssertionError, 'cached-compute-touched-component-resource'):
                component_reader(alias)

    def test_factory_invalidation_rejects_old_preparation_before_io(self):
        host, _ = prepare_pose_solver(self.engine, self.mid, '1')
        self.source.factories.invalidate()
        self.assertFalse(pose_inputs_current(self.engine, host.inputs))
        with self.no_resource_io():
            with self.assertRaisesRegex(ProviderUnavailable, 'resource-context-superseded'):
                prepare_pose_solver(self.engine, self.mid, '1')

    def test_runtime_prepares_temporal_input_and_checks_receipt_outside_fit(self):
        from client_idvb0059_adapter import Idvb0059Adapter
        from client_live_runtime import _prepare_alignment_host, _alignment_host_current
        adapter = Idvb0059Adapter()
        context = ('fixture-round', 'fixture-frame')
        current_calls = []
        def current(captured):
            current_calls.append(captured)
            return captured == context
        host, preparation, receipt = _prepare_alignment_host(self.engine, None, {},
            self.mid, '1', frame=self.frame,
            steady=(adapter, context, current, ('fixture-display',)))
        calls_after_preparation = tuple(current_calls)
        with self.no_resource_io():
            fit = host.fit(self.frame, self.mid, '1', lambda: False)
            self.assertTrue(_alignment_host_current(self.engine, host, receipt))
        self.assertFalse(fit['success'])
        self.assertEqual(tuple(current_calls), calls_after_preparation)
        self.assertEqual(preparation['kind'], 'prepared-ram')
        adapter.reset()
        self.assertFalse(_alignment_host_current(self.engine, host, receipt))

    def test_runtime_consumes_early_numerical_offer_on_its_calling_thread(self):
        from client_live_runtime import _fit_alignment_host
        host, _ = prepare_pose_solver(self.engine, self.mid, '1')
        caller, arithmetic_threads, timing = threading.get_ident(), [], {}
        published = threading.Event()
        def arithmetic(frame, map_id, floor, cancelled, *args, **kwargs):
            # Only the arithmetic sensor is replaced in this thread handoff
            # test. The actual prepared fit is covered by the other tests.
            arithmetic_threads.append(threading.get_ident())
            host.usable_fit_callback(dict(success=True, matrix=np.eye(3)))
            self.assertTrue(published.wait(2))
            return dict(success=False, reason='controlled-final-rejection')
        def consume(fit):
            self.assertEqual(threading.get_ident(), caller)
            np.testing.assert_array_equal(fit['matrix'], np.eye(3))
            published.set()
        with patch.object(host, '_fit_regions', side_effect=arithmetic):
            fit = _fit_alignment_host(host, self.frame, self.mid, '1', lambda: False,
                on_usable_fit=consume, timing=timing)
        self.assertEqual(fit['reason'], 'controlled-final-rejection')
        self.assertNotEqual(arithmetic_threads, [caller])
        self.assertIsNone(host.usable_fit_callback)
        self.assertEqual(timing['kind'], 'prepared-ram')

    def test_runtime_preserves_prior_pack_and_reference_across_preparations(self):
        from client_idvb0059_adapter import Idvb0059Adapter
        from client_live_runtime import _prepare_alignment_host
        from round_layer_group import RoundLayerGroup
        group = RoundLayerGroup(self.mid, 'shared-structure', '1', np.eye(3))
        group.canonical_geometry_verified_floors = {'1'}
        adapter = Idvb0059Adapter()
        context, signature = ('round', 'frame'), ('display',)
        current = lambda captured: captured == context
        host, _, _ = _prepare_alignment_host(self.engine, group, {}, self.mid, '1')
        # Coverage is the explicit sensor substitute; the native provider,
        # prepared resource, adapter, owner/spec and capture remain real.
        self.assertTrue(adapter.remember_committed(host, self.frame, self.mid, '1',
            dict(idvb0059Coverage={'synthetic': 1}), context=context, is_current=current,
            material_accepted=True, display_signature=signature))
        prepared, _, _ = _prepare_alignment_host(self.engine, group, {}, self.mid, '1',
            frame=self.frame, steady=(adapter, context, current, signature))
        self.assertIsNotNone(prepared.steady_input)
        self.assertEqual(prepared.pack.pack_id, self.engine.pack.pack_id)
        self.assertIs(prepared.steady_input.reference,
            host.inputs.reference('shared-structure', self.mid, '1'))
        reference = prepared.steady_input.reference
        group.verified_floors.add('2')
        expanded, _, _ = _prepare_alignment_host(self.engine, group, {}, self.mid, '1',
            frame=self.frame, steady=(adapter, context, current, signature))
        self.assertIsNotNone(expanded.steady_input)
        self.assertIs(expanded.steady_input.reference, reference)
        self.assertEqual(expanded.steady_input.coverage['synthetic'], 1)
        for query in (expanded.engine.entry_identity, expanded.engine.side_entry_identity):
            self.assertIs(query.shared_reference_provider('shared-structure', self.mid, '1'), reference)
        self.engine.qt_pose_input_cache.max_entries = 1
        prepare_pose_solver(self.engine, self.mid, '2', group=group)
        reloaded, preparation, _ = _prepare_alignment_host(self.engine, group, {}, self.mid, '1',
            frame=self.frame, steady=(adapter, context, current, signature))
        self.assertFalse(preparation['cacheHit'])
        self.assertIsNotNone(reloaded.steady_input)
        self.assertIs(reloaded.steady_input.reference, reference)
        self.assertEqual(reloaded.steady_input.coverage['synthetic'], 1)

    def test_runtime_publisher_error_cancels_and_joins_numerical_work(self):
        from client_live_runtime import _fit_alignment_host
        host, _ = prepare_pose_solver(self.engine, self.mid, '1')
        exited = threading.Event()
        def arithmetic(frame, map_id, floor, cancelled, *args, **kwargs):
            try:
                host.usable_fit_callback(dict(success=True))
                for _ in range(400):
                    if cancelled():
                        return dict(success=False, reason='structure-work-obsolete')
                    exited.wait(.005)
                raise AssertionError('runtime-error-did-not-cancel-compute')
            finally:
                exited.set()
        def consume(fit):
            raise ValueError('controlled-publication-error')
        with patch.object(host, '_fit_regions', side_effect=arithmetic):
            with self.assertRaisesRegex(ValueError, 'controlled-publication-error'):
                _fit_alignment_host(host, self.frame, self.mid, '1', lambda: False,
                    on_usable_fit=consume)
        self.assertTrue(exited.is_set())
        self.assertIsNone(host.usable_fit_callback)

    def test_cache_survives_disk_damage_until_next_preparation(self):
        first, _ = prepare_pose_solver(self.engine, self.mid, '1')
        reference = first.inputs.reference('shared-structure', self.mid, '1')
        original_mask = reference.clean_mask.copy()
        # Keep the real provider's already admitted reference so that the
        # next cold decode classifies the damaged bytes as unavailable.
        self.engine.qt_shared_provider('shared-structure', self.mid, '1')
        reference.path.write_bytes(b'changed-after-preparation')
        with self.no_resource_io():
            cached, info = prepare_pose_solver(self.engine, self.mid, '1')
            self.assertFalse(cached.fit(self.frame, self.mid, '1', lambda: False)['success'])
        self.assertTrue(info['cacheHit'])
        self.assertTrue(np.array_equal(reference.clean_mask, original_mask))
        self.engine.qt_pose_input_cache.invalidate()
        # The imported factory also keeps its admitted static payload snapshot;
        # clear it to make the next preparation exercise the damaged file on
        # disk instead of legitimately reusing that earlier RAM snapshot.
        self.engine.qt_pose_static_payloads = None
        with self.assertRaisesRegex(PoseInputsUnavailable, 'reference-decode-unavailable'):
            prepare_pose_solver(self.engine, self.mid, '1')


if __name__ == '__main__':
    unittest.main()
