"""Explicit live capability survives S0 reporting and command admission.

Only engine construction and capture are fixtures. Factory reports, the offline
adapter, runtime snapshots and command admission use their actual implementations.
These tests do not verify resource decoding or recognition.
"""
from pathlib import Path
import tempfile
import unittest

from client_live_runtime import LiveRuntime
from client_providers import EngineFactories, ProviderUnavailable
from test_client_live_runtime import FixtureCapture, fixture_engine


class ExplicitLiveCapabilityTests(unittest.TestCase):
    def make_runtime(self, mode='difficult', *, live_ready=False, declared=True, gaps=None):
        temporary = tempfile.TemporaryDirectory(prefix='explicit-live-capability-')
        self.addCleanup(temporary.cleanup)
        engine = fixture_engine(mode)
        if declared:
            engine.qt_live_ready = live_ready
        engine.qt_live_gaps = (gaps if gaps is not None else
            () if live_ready or not declared else ('data-only-floor-observation-unvalidated',))
        factories = EngineFactories(Path(temporary.name).resolve())
        factories._build = lambda selected, *, season='s0': engine
        self.assertIs(factories.get_engine(mode, season='s0'), engine)
        capture = FixtureCapture()
        runtime = LiveRuntime(engine_factories=factories, capture_provider=capture, evidence_kind='synthetic')
        self.addCleanup(runtime.shutdown)
        self.assertTrue(runtime.dispatch('set_context', dict(season='s0', mode=mode))['success'])
        return runtime, factories, engine, capture

    def test_s0_factory_preserves_explicit_false_even_when_data_is_available(self):
        for mode in ('difficult', 'nightmare'):
            with self.subTest(mode=mode):
                _, factories, _, _ = self.make_runtime(mode)
                report = factories.capabilities('s0')[mode]
                self.assertTrue(report['available'])
                self.assertFalse(report['liveReady'])
                self.assertEqual(report['liveGaps'], ['data-only-floor-observation-unvalidated'])

    def test_actual_get_engine_requires_live_capability_but_allows_data_access(self):
        runtime, _, engine, _ = self.make_runtime()
        self.assertIs(runtime._get_engine('difficult', 's0', require_live=False), engine)
        with self.assertRaisesRegex(ProviderUnavailable, 'data-only-floor-observation-unvalidated'):
            runtime._get_engine('difficult', 's0', require_live=True)

    def test_snapshot_and_live_intents_reject_data_before_capture_or_rank(self):
        runtime, _, engine, capture = self.make_runtime()
        state = runtime.snapshot()
        self.assertFalse(state['liveEngineAvailable'])
        self.assertEqual(state['liveUnavailableReason'], 'data-only-floor-observation-unvalidated')
        for action in ('start_round', 'toggle_round', 'capture_now', 'map', 'reselect_map'):
            self.assertNotIn(action, state['availableCommands'])
        for action in ('start_round', 'capture_now', 'refresh_map'):
            with self.subTest(action=action):
                state = runtime.snapshot()
                payload = {key: state[key] for key in ('mode', 'profileId', 'roundId', 'frameId')}
                result = runtime.dispatch(action, dict(payload, generation=1))
                self.assertFalse(result['success'], result)
                self.assertTrue(result.get('unavailable'), result)
                self.assertEqual(result['reason'], 'data-only-floor-observation-unvalidated')
        self.assertEqual(capture.enable_count, 0)
        self.assertEqual(capture.count, 0)
        self.assertEqual(engine.rank_count, 0)
        self.assertFalse(runtime.snapshot()['roundPreparing'])

    def test_explicit_false_without_explanatory_gaps_is_still_unavailable(self):
        runtime, _, _, capture = self.make_runtime(gaps=())
        state = runtime.snapshot()
        self.assertFalse(state['liveEngineAvailable'])
        self.assertIn('live-contract-unavailable', state['liveUnavailableReason'])
        self.assertNotIn('start_round', state['availableCommands'])
        with self.assertRaisesRegex(ProviderUnavailable, 'live-contract-unavailable'):
            runtime._get_engine('difficult', 's0')
        self.assertFalse(runtime.dispatch('start_round')['success'])
        self.assertEqual(capture.enable_count, 0)

    def test_existing_s0_default_and_explicit_true_remain_usable(self):
        for declared in (False, True):
            with self.subTest(declared=declared):
                runtime, factories, engine, _ = self.make_runtime(live_ready=True, declared=declared)
                self.assertTrue(factories.capabilities()['difficult']['liveReady'])
                self.assertIs(runtime._get_engine('difficult', 's0'), engine)
                self.assertTrue(runtime.snapshot()['liveEngineAvailable'])
                self.assertIn('start_round', runtime.snapshot()['availableCommands'])


if __name__ == '__main__':
    unittest.main()
