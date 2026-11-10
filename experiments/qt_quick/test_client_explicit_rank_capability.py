"""Data-only engines cannot acquire ranking permission through cached callers.

Resource binding/construction and capture are fixtures. Factory reporting,
offline dispatch, runtime admission and the native rank entry are real code.
"""
from pathlib import Path
import tempfile
import unittest

import numpy as np
from PIL import Image

from client_engine import EngineAdapter
from client_imported_providers import ImportedEngineFactories
from client_live_runtime import LiveRuntime
from client_paths import RuntimePaths
from client_providers import EngineFactories, ProviderResourceBinding, ProviderUnavailable
from difficult_native_identity import NativeDifficultCandidateEngine
from imported_resource_catalog import ResourceResolver
from season_context import SeasonContext
from test_client_live_runtime import FixtureCapture, fixture_engine


class ExplicitRankCapabilityTests(unittest.TestCase):
    def make_factory(self, *, imported=False, rank_ready=False):
        temporary = tempfile.TemporaryDirectory(prefix='explicit-rank-capability-')
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name).resolve()
        engine = fixture_engine('difficult')
        if rank_ready is not None:
            engine.qt_rank_ready = rank_ready
        engine.qt_live_ready = True
        engine.qt_rank_gaps = () if rank_ready or rank_ready is None else ('fixture-rank-unvalidated',)
        if imported:
            factories = ImportedEngineFactories(ResourceResolver(root/'resources'), RuntimePaths(root/'program', False))
            factories._resource_binding = lambda mode, season='s0': ProviderResourceBinding(
                SeasonContext(season=season, mode=mode), root, factories._revision)
        else:
            factories = EngineFactories(root)
        factories._build = lambda mode, *, season='s0': engine
        return root, factories, engine

    def test_factory_reports_keep_data_only_status_on_every_cache_hit(self):
        for imported in (False, True):
            with self.subTest(imported=imported):
                _, factories, engine = self.make_factory(imported=imported)
                for _ in range(2):
                    self.assertIs(factories.get_engine('difficult'), engine)
                    report = factories.capabilities()['difficult']
                    self.assertTrue(report.get('dataReady'))
                    self.assertIs(report.get('rankReady'), False)
                    self.assertFalse(report['available'])
                    self.assertFalse(report['liveReady'])
                    self.assertEqual(report.get('rankGaps'), ['fixture-rank-unvalidated'])

    def test_existing_default_and_explicit_true_keep_ranking_permission(self):
        for rank_ready in (None, True):
            with self.subTest(rank_ready=rank_ready):
                _, factories, _ = self.make_factory(rank_ready=rank_ready)
                factories.get_engine('difficult')
                report = factories.capabilities()['difficult']
                self.assertTrue(report['available'])
                self.assertTrue(report.get('dataReady'))
                self.assertTrue(report.get('rankReady'))
                self.assertTrue(report['liveReady'])

    def test_actual_saved_frame_dispatch_rejects_rank_before_and_after_cache(self):
        root, factories, engine = self.make_factory()
        frame = root/'frame.png'
        Image.new('RGB', (16, 12), 'white').save(frame)
        adapter = EngineAdapter(engine_factories=factories, allowed_read_root=root)
        context = dict(season='s0', mode='difficult', floor='1', path=str(frame))
        adapter.dispatch('set_context', context)
        for _ in range(2):
            result = adapter.dispatch('rank_saved_frame', context)
            self.assertFalse(result['success'], result)
            self.assertTrue(result.get('unavailable'), result)
            self.assertIn('fixture-rank-unvalidated', result['reason'])
        self.assertNotIn('rank_saved_frame', adapter.snapshot()['availableCommands'])
        self.assertEqual(engine.rank_count, 0)

    def test_actual_runtime_rejects_rank_flag_even_if_live_flag_is_true(self):
        _, factories, engine = self.make_factory()
        factories.get_engine('difficult')
        capture = FixtureCapture()
        runtime = LiveRuntime(engine_factories=factories, capture_provider=capture, evidence_kind='synthetic')
        self.addCleanup(runtime.shutdown)
        runtime.dispatch('set_context', dict(season='s0', mode='difficult'))
        self.assertIs(runtime._get_engine('difficult', require_live=False), engine)
        with self.assertRaises(ProviderUnavailable):
            runtime._get_engine('difficult', require_live=True)
        state = runtime.snapshot()
        self.assertFalse(state['liveEngineAvailable'])
        self.assertNotIn('start_round', state['availableCommands'])
        self.assertFalse(runtime.dispatch('start_round')['success'])
        self.assertEqual(capture.enable_count, 0)
        self.assertEqual(engine.rank_count, 0)

    def test_native_rank_entry_rejects_before_reading_any_solver_state(self):
        engine = NativeDifficultCandidateEngine.__new__(NativeDifficultCandidateEngine)
        engine.qt_rank_ready = False
        engine.qt_rank_gaps = ('fixture-rank-unvalidated',)
        with self.assertRaisesRegex(ValueError, 'fixture-rank-unvalidated'):
            engine.rank(np.zeros((8, 8, 3), np.uint8), floor='1')

    def test_missing_explanatory_gap_does_not_grant_rank_permission(self):
        _, factories, engine = self.make_factory()
        del engine.qt_rank_gaps
        factories.get_engine('difficult')
        report = factories.capabilities()['difficult']
        self.assertFalse(report['available'])
        self.assertFalse(report['liveReady'])
        self.assertEqual(report['rankGaps'], ['recognition-rank-unavailable'])
        self.assertIn('recognition-rank-unavailable', report['liveGaps'])

    def test_late_resource_failure_withdraws_every_capability_and_recovers(self):
        for imported in (False, True):
            with self.subTest(imported=imported):
                _, factories, engine = self.make_factory(imported=imported, rank_ready=True)
                factories.get_engine('difficult')
                self.assertTrue(factories.capabilities()['difficult']['liveReady'])
                binding = factories._resource_binding
                def fail_binding(*args, **kwargs):
                    raise ProviderUnavailable('fixture-binding-unavailable')
                def fail_current():
                    raise ValueError('fixture-context-superseded')
                if imported:
                    engine.qt_assert_resource_current = fail_current
                else:
                    factories._resource_binding = fail_binding
                with self.assertRaises(ProviderUnavailable):
                    factories.get_engine('difficult')
                report = factories.capabilities()['difficult']
                for field in ('available', 'dataReady', 'rankReady', 'liveReady'):
                    self.assertIs(report.get(field), False, (field, report))
                self.assertTrue(report.get('liveGaps'))
                self.assertTrue(report.get('rankGaps'))
                factories._resource_binding = binding
                if imported:
                    del engine.qt_assert_resource_current
                self.assertIs(factories.get_engine('difficult'), engine)
                recovered = factories.capabilities()['difficult']
                self.assertTrue(recovered['available'])
                self.assertTrue(recovered['liveReady'])
                self.assertEqual(recovered['rankGaps'], [])
                self.assertEqual(recovered['liveGaps'], [])

    def test_failed_construction_reports_no_data_rank_or_live_permission(self):
        _, factories, _ = self.make_factory(rank_ready=True)
        def fail(*args, **kwargs):
            raise ValueError('fixture-build-failed')
        factories._build = fail
        with self.assertRaises(ProviderUnavailable):
            factories.get_engine('difficult')
        report = factories.capabilities()['difficult']
        for field in ('available', 'dataReady', 'rankReady', 'liveReady'):
            self.assertIs(report.get(field), False, (field, report))


if __name__ == '__main__':
    unittest.main()
