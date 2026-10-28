"""Production consumer boundaries; synthetic events, no game/GUI claims."""
from types import SimpleNamespace
from unittest.mock import Mock
import unittest

from test_reopen_lifecycle import method


class StaleFailureDisplay(unittest.TestCase):
    def state(self, mode):
        return SimpleNamespace(
            game_mode=mode, alignment_core_v2=object(),
            _alignment_v2_runtime_is_enabled=lambda: True,
            _shared_structure_mode_enabled=lambda: False,
            _alignment_v2_worker_fit_usable=lambda fit: bool(fit and fit.get('success')),
            active_map_cycle_id=9, _alignment_v2_reset_generation=0,
            current_frame_generation=14, current_floor='1',
            gate=SimpleNamespace(confirmed_id='map'), route_profile_id='author',
            map_is_open=True, map_visual_suppressed=False, floor_required=False,
            _current_frame_floor_conflicts=lambda floor: False,
            _alignment_v2_seed_group_pose=Mock(),
            _alignment_v2_observation=Mock(return_value=None),
            _alignment_v2_requeue_latest_pose_recovery=Mock(),
            _log_core_chain_event=Mock(), _hide_spatial_route=Mock())

    def event(self, **overrides):
        return dict(alignmentV2WorkerFit={'success': False, 'reason': 'geometry-failed'},
                    workerToken=(9, 'map', '1', 12, 0, 0),
                    sourceFrameGeneration=12, routeProfileId='author', **overrides)

    def test_old_failure_never_opens_current_observation(self):
        call = method('_run_alignment_v2_for_current_frame_unlocked')
        for mode in ('nightmare', 'difficult'):
            for _ in range(20):
                state = self.state(mode)
                result = call(state, 'map', '1', '1', self.event())
                self.assertEqual(result['reason'], 'stale-worker-failure')
                state._alignment_v2_observation.assert_not_called()
                state._hide_spatial_route.assert_not_called()
                state._alignment_v2_requeue_latest_pose_recovery.assert_called_once()

    def test_closed_reset_floor_and_suppressed_do_not_requeue(self):
        call = method('_run_alignment_v2_for_current_frame_unlocked')
        for mode in ('nightmare', 'difficult'):
            for change in ({'map_is_open': False}, {'map_visual_suppressed': True},
                           {'active_map_cycle_id': 10}, {'_alignment_v2_reset_generation': 1},
                           {'current_floor': '2'}):
                state = self.state(mode)
                state.__dict__.update(change)
                result = call(state, 'map', '1', '1', self.event())
                self.assertEqual(result['reason'], 'stale-worker-failure')
                state._hide_spatial_route.assert_not_called()
                state._alignment_v2_observation.assert_not_called()
                state._alignment_v2_requeue_latest_pose_recovery.assert_not_called()

    def test_current_failure_and_successful_recovery_keep_normal_validation(self):
        call = method('_run_alignment_v2_for_current_frame_unlocked')
        for mode in ('nightmare', 'difficult'):
            for current, recovery in ((True, False), (False, True)):
                state = self.state(mode)
                event = self.event()
                if current:
                    event.update(sourceFrameGeneration=14, workerToken=(9,'map','1',14,0,0))
                if recovery:
                    event['alignmentV2WorkerRecoveryFit'] = {'success': True}
                result = call(state, 'map', '1', '1', event)
                self.assertEqual(result['reason'], 'observation-unavailable')
                state._alignment_v2_observation.assert_called_once()

    def test_discarded_failure_does_not_escalate_b1_policy(self):
        call = method('_record_b1_v2_worker_result')
        for reason in ('stale-worker-failure', 'structure-work-obsolete', 'presentation-profile-stale'):
            state = SimpleNamespace(_record_b1_relocalization_result=Mock())
            self.assertFalse(call(state, {'mapId': 'map', 'floor': 'B1'},
                                  runtime_result={'reason': reason, 'success': False}))
            state._record_b1_relocalization_result.assert_not_called()


if __name__ == '__main__':
    unittest.main()
