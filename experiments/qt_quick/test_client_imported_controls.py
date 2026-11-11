"""Actual LiveRuntime observation with packaged controls and independent pixels.

Only the identity/ranking engine is a fixture; floor decoding and geometry run
unchanged. These samples are synthetic, not recorded game acceptance.
"""
import unittest
from unittest.mock import patch

from client_live_runtime import LiveRuntime
from test_client_live_runtime import fixture_engine
import test_imported_s0_observation as pixels
import test_imported_s1_controls_profile as s1_profiles
import test_s1_manual_floor_runtime as manual_s1
from floor_observation_policy import game_floor_control_hit


class ImportedRuntimeControlsTests(unittest.TestCase):
    def test_s1_manual_and_candidate_observation_consume_actual_external_parameters(self):
        """Capabilities/engine are fixtures; profile and pixels are actual.

        Direct observation does not bypass the production start-round gap or
        qualify external source replay. Neither branch may borrow defaults.
        """
        fixture = s1_profiles.ImportedS1Controls()
        self.addCleanup(fixture.doCleanups)
        controls = fixture.profile()
        from s1_floor_observation import detect_s1_floor
        for mode in ('difficult', 'nightmare'):
            registry = manual_s1.FloorRegistry(mode)
            engine = registry.engine
            engine.qt_controls_profile = controls
            engine.qt_native_context.structure_candidate_capability = dict(candidateReady=True)
            runtime = LiveRuntime(engine_factories=registry, evidence_kind='recorded')
            self.addCleanup(runtime.shutdown)
            runtime._mode, runtime._season, runtime._engine = mode, 's1', engine
            for manual in (True, False):
                runtime._manual_alignment = manual
                with self.subTest(mode=mode, manual=manual), \
                     patch('s1_floor_observation.detect_s1_floor', wraps=detect_s1_floor) as observed:
                    observation, floor, read = runtime._observe(engine, s1_profiles.bright_frame((-3,)))
                    self.assertIs(observed.call_args.kwargs.get('controls'), controls)
                    self.assertTrue(observation['detected'], (observation, read))
                    self.assertIsNone(floor)
                    self.assertIsNone(read['selectedFloor'])
                    self.assertFalse(read['observedFloorValidated'])
                    self.assertEqual(read['validationScope'], 'external-controls-unreviewed')

    def test_live_observation_conflicts_and_hits_use_external_floor_mapping(self):
        fixture = pixels.ImportedS0ObservationTests()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        orders = {'nightmare': (1, 2, -1), 'difficult': (1, 2)}
        ids = {'nightmare': {-1: 'Lower', 1: 'Ground', 2: 'Upper'},
               'difficult': {1: 'Ground', 2: 'Upper'}}
        _package, controls = fixture.profile(orders=orders, ids=ids, fixed=(.75, .60, .08, .33))
        for mode, order in orders.items():
            with self.subTest(mode=mode):
                engine = fixture_engine(mode)
                engine.qt_controls_profile = controls
                runtime = LiveRuntime(engine_factories={mode: lambda: engine}, evidence_kind='synthetic')
                runtime._mode, runtime._season, runtime._engine = mode, 's0', engine
                frame = pixels._board(mode, order, 1, center_x=1500)
                with patch('s0_floor_templates._bank', side_effect=AssertionError('Built-in bank used')):
                    observation, floor, read = runtime._observe(engine, frame)
                self.assertTrue(observation['detected'], (observation, read))
                self.assertEqual(floor, 'Ground')
                self.assertFalse(runtime._floor_read_conflicts(read, 'Ground'))
                self.assertTrue(runtime._floor_read_conflicts(read, 'Upper'))
                self.assertEqual(runtime._floor_contract().floor_id_for(2), 'Upper')
                hit = game_floor_control_hit((1500, 820), (0, 0, frame.shape[1], frame.shape[0]), read, controls=controls)
                self.assertEqual(hit, 2)
                self.assertEqual(runtime._floor_contract().floor_id_for(hit), 'Upper')


if __name__ == '__main__':
    unittest.main()
