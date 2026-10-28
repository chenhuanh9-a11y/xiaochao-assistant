"""Exercise owner cleanup, with geometry IO isolated from worker scheduling."""
import ast
from pathlib import Path
from types import SimpleNamespace
import threading
import unittest
from unittest.mock import Mock

import cv2
import numpy as np


def subject_type():
    path = Path(__file__).parents[1] / 'app/identityv_daily_assistant.py'
    tree = ast.parse(path.read_text(encoding='utf-8-sig'))
    names = {'_alignment_v2_finish_worker_delivery', '_alignment_v2_queued_frame_matches_commit'}
    body = [n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef) and n.name in names]
    assert len(body) == len(names)
    wrapper = ast.ClassDef(name='Subject', bases=[], keywords=[], body=body, decorator_list=[])
    namespace = dict(np=np, cv2=cv2)
    exec(compile(ast.fix_missing_locations(ast.Module(body=[wrapper], type_ignores=[])), str(path), 'exec'), namespace)
    return namespace['Subject']


class QueuedDeliveryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls): cls.Subject = subject_type()

    def setUp(self):
        s = self.s = self.Subject()
        s.map_is_open = True
        s.active_map_cycle_id, s.current_floor = 5, '2'
        s.gate = SimpleNamespace(confirmed_id='nightmare-03')
        s.current_frame_generation = 6
        s.surface_visibility_generation = s._alignment_v2_reset_generation = 1
        s.route_profile_id = 'wang-qingxin'
        s.current_frame = np.zeros((8, 8, 3), np.uint8)
        verified = s.current_frame.copy()
        verified[0, 0] = 1  # Different RGB; geometry/semantics represented below.
        s._alignment_v2_worker_lock = threading.Lock()
        self.token = (5, 'nightmare-03', '2', 6, 'display', 1)
        self.latest = (self.token, 'nightmare-03', '2', '2', s.current_frame.copy(), (0,0,8,8),
                       'first-pose', 'signature', 1, {'requestedRouteProfileId':'wang-qingxin'})
        s._alignment_v2_worker_latest_args = self.latest
        s.current_alignment_candidate = dict(accepted=True, cycleId=5, mapId='nightmare-03', floor='2',
            routeProfileId='wang-qingxin', displayAuthorizationId=7, verifiedFrame=verified)
        s.alignment_core_v2 = SimpleNamespace(
            pose_by_map={'nightmare-03':SimpleNamespace(reliable=True, route_profile_id='wang-qingxin')},
            display_authorization=SimpleNamespace(authorization_id=7, source='structure'),
            can_display=Mock(return_value=True))
        s._alignment_v2_retry_mode = lambda mode: mode in ('first-pose', 'group-recovery')
        s._alignment_v2_failed_input_unchanged = Mock(return_value=False)
        s._open_cycle_transient_motion_active = Mock(return_value=False)
        s._spatial_route_surface_is_visible = Mock(return_value=True)
        s._shared_structure_mode_enabled = Mock(return_value=True)
        s._alignment_v2_structure_frame_equivalent = Mock(return_value=True)
        s._alignment_v2_worker_base_pose_context = Mock(return_value={'basePoseGeneration':1})
        s._log_core_chain_event = Mock()

    def test_rgb_animation_does_not_restart_first_pose_after_authorized_commit(self):
        self.s._alignment_v2_finish_worker_delivery()
        self.assertIsNone(self.s._alignment_v2_worker_latest_args)
        self.s._alignment_v2_structure_frame_equivalent.assert_called_once_with(
            self.s.current_alignment_candidate['verifiedFrame'], semantic=True)
        self.s.alignment_core_v2.can_display.assert_called_once_with(
            cycle_id=5, map_id='nightmare-03', floor='2', frame_generation=6)

    def test_changed_observation_remains_queued(self):
        self.s._alignment_v2_structure_frame_equivalent.return_value = False
        self.s._alignment_v2_finish_worker_delivery()
        self.assertIsNotNone(self.s._alignment_v2_worker_latest_args)

    def test_unconsumed_newer_frame_is_not_discarded(self):
        self.s.current_frame[1,1] = 2
        self.s._alignment_v2_finish_worker_delivery()
        self.assertIsNotNone(self.s._alignment_v2_worker_latest_args)
        self.s._alignment_v2_structure_frame_equivalent.assert_not_called()

    def test_legacy_mode_retains_exact_rgb_contract(self):
        self.s._shared_structure_mode_enabled.return_value = False
        self.s._alignment_v2_finish_worker_delivery()
        self.assertIsNotNone(self.s._alignment_v2_worker_latest_args)

    def test_floor_one_does_not_request_floor_two_semantics(self):
        self.assertTrue(self.s._alignment_v2_queued_frame_matches_commit(
            self.latest[4], self.s.current_alignment_candidate['verifiedFrame'], '1'))
        self.assertFalse(self.s._alignment_v2_structure_frame_equivalent.call_args.kwargs['semantic'])

    def test_existing_authorization_and_lifecycle_guards_remain_required(self):
        changes = (
            lambda: setattr(self.s, 'surface_visibility_generation', 2),
            lambda: setattr(self.s, 'route_profile_id', 'suju'),
            lambda: setattr(self.s, '_alignment_v2_reset_generation', 2),
            lambda: setattr(self.s.alignment_core_v2.can_display, 'return_value', False),
            lambda: setattr(self.s._open_cycle_transient_motion_active, 'return_value', True),
            lambda: setattr(self.s._spatial_route_surface_is_visible, 'return_value', False),
            lambda: setattr(self.s.alignment_core_v2.display_authorization, 'authorization_id', 8),
        )
        for change in changes:
            with self.subTest(change=change):
                self.setUp()
                change()
                self.s._alignment_v2_finish_worker_delivery()
                self.assertIsNotNone(self.s._alignment_v2_worker_latest_args)

    def test_decode_failure_does_not_discard_work(self):
        self.s._alignment_v2_structure_frame_equivalent.side_effect = ValueError('bad observation')
        self.s._alignment_v2_finish_worker_delivery()
        self.assertIsNotNone(self.s._alignment_v2_worker_latest_args)


if __name__ == '__main__': unittest.main()
