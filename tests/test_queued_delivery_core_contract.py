"""Headless worker/owner cleanup contracts, using the real alignment core.

The heavy fit and geometry IO are controlled substitutes. Queue.put is a
synchronous owner adapter: it commits a real core candidate, updates the
display record, calls the actual finish method, and releases the actual worker
delivery barrier. This deliberately does not execute or simulate full Tk poll.
The real-image geometry proof is a separate diagnostic, not this test's claim.
"""
import ast
import hashlib
import importlib.util
from pathlib import Path
import sys
import threading
import time
from types import SimpleNamespace
import unittest
from unittest.mock import Mock

import cv2
import numpy as np


ROOT = Path(__file__).parents[1]


def load_core():
    name = '_queued_delivery_contract_core'
    spec = importlib.util.spec_from_file_location(name, ROOT / 'app/alignment_core_v2.py')
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module  # Dataclasses resolve their defining module.
    spec.loader.exec_module(module)
    return module


CORE = load_core()


def subject_type():
    path = ROOT / 'app/identityv_daily_assistant.py'
    tree = ast.parse(path.read_text(encoding='utf-8-sig'))
    names = {
        '_alignment_v2_first_pose_worker_loop',
        '_alignment_v2_finish_worker_delivery',
        '_alignment_v2_queued_frame_matches_commit',
        '_alignment_v2_worker_pose_stale',
        '_alignment_v2_worker_base_pose_context',
    }
    body = [n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef) and n.name in names]
    assert len(body) == len(names)
    wrapper = ast.ClassDef(name='Subject', bases=[], keywords=[], body=body, decorator_list=[])
    namespace = dict(np=np, cv2=cv2, threading=threading, time=time, hashlib=hashlib,
                     map_translation_mask=lambda frame: None)
    exec(compile(ast.fix_missing_locations(ast.Module(body=[wrapper], type_ignores=[])), str(path), 'exec'), namespace)
    return namespace['Subject']


class QueuedDeliveryCoreContracts(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.Subject = subject_type()

    def setUp(self):
        s = self.s = self.Subject()
        s.alignment_core_v2 = CORE.AlignmentCoreV2(clock=lambda: 1.0)
        s.map_is_open = True
        s.active_map_cycle_id, s.current_floor = 5, '2'
        s.gate = SimpleNamespace(confirmed_id='nightmare-03')
        s.current_frame_generation = 6
        s.surface_visibility_generation = s._alignment_v2_reset_generation = 1
        s.route_profile_id = 'wang-qingxin'
        s.current_frame = np.zeros((8, 8, 3), np.uint8)
        self.verified = s.current_frame.copy()
        self.verified[0, 0] = 1  # Controlled out-of-observation RGB difference.
        s._alignment_v2_worker_lock = threading.Lock()
        s._alignment_v2_worker_running = s._alignment_v2_worker_pending = True
        s._alignment_v2_worker_failure_state = None
        self.drag, self.preview, self.zoom = np.zeros(2), np.zeros(2), np.eye(3)
        self.group = SimpleNamespace(generation=0)
        s._active_round_layer_group = lambda map_id: self.group
        s._active_open_cycle_drag_offset = lambda map_id: self.drag
        s._active_mouse_drag_preview_offset = lambda: self.preview
        s._active_open_cycle_zoom_transform = lambda map_id: self.zoom
        s._alignment_v2_retry_mode = lambda mode: mode in ('first-pose', 'group-recovery')
        s._alignment_v2_worker_fit_usable = lambda fit: bool(fit and fit.get('success'))
        s._alignment_v2_failed_input_unchanged = Mock(return_value=False)
        s._open_cycle_transient_motion_active = Mock(return_value=False)
        s._spatial_route_surface_is_visible = Mock(return_value=True)
        s._shared_structure_mode_enabled = Mock(return_value=True)
        # Geometry extraction is outside this scheduling/core contract.
        s._alignment_v2_structure_frame_equivalent = Mock(return_value=True)
        s._log_core_chain_event = Mock()
        s.current_alignment_candidate = {}
        s.alignment_core_v2.begin_observation(
            cycle_id=5, map_id='nightmare-03', floor='2', frame_generation=6)
        self.latest = self.request(s.current_frame.copy())
        s._alignment_v2_worker_latest_args = self.latest

    def request(self, frame, generation=6):
        token = (5, 'nightmare-03', '2', generation, 'display-A', 1)
        return (token, 'nightmare-03', '2', '2', frame, (0, 0, 8, 8),
                'first-pose', 'signature', 1,
                {'requestedRouteProfileId': 'wang-qingxin'})

    def candidate(self, generation=6):
        return CORE.AlignmentCandidate(
            'nightmare-03', '2', np.eye(3), 'initial-structure-v2', generation,
            current_frame_verified=True, residual_verified=True,
            global_residual_verified=True,
            metadata={'routeProfileId': 'wang-qingxin'})

    def commit(self):
        core = self.s.alignment_core_v2
        verdict = core.adjudicate(self.candidate())
        self.assertTrue(verdict.accepted)
        grant = core.authorize(verdict)
        self.assertIsNotNone(grant)
        self.assertTrue(core.commit(grant))
        self.s.current_alignment_candidate = dict(
            accepted=True, cycleId=5, mapId='nightmare-03', floor='2',
            routeProfileId='wang-qingxin', displayAuthorizationId=grant.authorization_id,
            verifiedFrame=self.verified)
        return grant

    def can_display(self, **changes):
        context = dict(cycle_id=self.s.active_map_cycle_id,
                       map_id=self.s.gate.confirmed_id, floor=self.s.current_floor,
                       frame_generation=self.s.current_frame_generation)
        context.update(changes)
        return self.s.alignment_core_v2.can_display(**context)

    def test_committed_equivalent_queue_never_runs_the_failing_second_job(self):
        """Actual worker barrier -> real core commit -> actual queue cleanup."""
        s = self.s
        calls = []
        initial = self.request(self.verified.copy())

        def fit(args, post_result):
            calls.append(args)
            self.assertFalse(post_result)
            self.assertEqual(len(calls), 1, 'Redundant cold solve would fail on this input')
            return dict(sensorMode='first-pose', fit={'success': True},
                        basePoseGeneration=None)

        def owner_delivery(event):
            self.assertIn('_workerDeliveryReady', event)
            grant = self.commit()
            s._alignment_v2_finish_worker_delivery()
            self.assertIs(s.alignment_core_v2.display_authorization, grant)
            event['_workerDeliveryReady'].set()

        s._alignment_v2_first_pose_worker = fit
        s.events = SimpleNamespace(put=owner_delivery)
        s._alignment_v2_first_pose_worker_loop(initial)
        self.assertEqual(len(calls), 1)
        self.assertIsNone(s._alignment_v2_worker_latest_args)
        self.assertFalse(s._alignment_v2_worker_running)
        self.assertTrue(self.can_display())
        s._alignment_v2_structure_frame_equivalent.assert_called_once_with(self.verified, semantic=True)

    def test_changed_new_observation_retains_work_and_revokes_old_core_grant(self):
        s = self.s
        old = self.commit()
        s.current_frame[3, 3] = 255
        s.current_frame_generation = 7
        s._alignment_v2_worker_latest_args = self.request(s.current_frame.copy(), generation=7)
        s._alignment_v2_structure_frame_equivalent.return_value = False
        self.assertTrue(s.alignment_core_v2.update_frame(frame_generation=7))
        self.assertFalse(old.active)
        s._alignment_v2_finish_worker_delivery()
        self.assertIsNotNone(s._alignment_v2_worker_latest_args)
        self.assertFalse(self.can_display())
        self.assertFalse(s.alignment_core_v2.commit(old))

    def test_lost_authorization_cannot_discard_even_equivalent_first_pose(self):
        grant = self.commit()
        self.s.alignment_core_v2.revoke_display(reason='current-pose-invalidated')
        self.s._alignment_v2_finish_worker_delivery()
        self.assertIsNotNone(self.s._alignment_v2_worker_latest_args)
        self.assertFalse(grant.active)
        self.assertFalse(self.can_display())

    def test_real_pose_staleness_rejects_generation_root_group_and_motion_changes(self):
        mutations = {
            'pose-generation': lambda: setattr(self.s.alignment_core_v2.pose_by_map['nightmare-03'], 'generation', 1),
            'pose-root': lambda: self.s.alignment_core_v2.pose_by_map['nightmare-03'].root_matrix.__setitem__((0, 2), 5),
            'group-generation': lambda: setattr(self.group, 'generation', 1),
            'drag': lambda: self.drag.__setitem__(0, 1),
            'zoom': lambda: self.zoom.__setitem__((0, 0), 2),
            'preview': lambda: self.preview.__setitem__(0, 1),
        }
        for name, mutate in mutations.items():
            with self.subTest(cause=name):
                self.setUp()
                self.commit()
                event = self.s._alignment_v2_worker_base_pose_context('nightmare-03')
                self.assertFalse(self.s._alignment_v2_worker_pose_stale('nightmare-03', event))
                mutate()
                self.assertTrue(self.s._alignment_v2_worker_pose_stale('nightmare-03', event))
                # The lifecycle owner revokes a failed pose; cleanup cannot
                # silently retain that grant or discard its replacement work.
                self.s.alignment_core_v2.revoke_display(reason='stale-worker-base')
                self.s._alignment_v2_finish_worker_delivery()
                self.assertIsNotNone(self.s._alignment_v2_worker_latest_args)
                self.assertFalse(self.can_display())

    def test_first_pose_result_cannot_overwrite_a_pose_created_while_sensing(self):
        event = self.s._alignment_v2_worker_base_pose_context('nightmare-03')
        self.assertIsNone(event['basePoseGeneration'])
        self.commit()
        self.assertTrue(self.s._alignment_v2_worker_pose_stale('nightmare-03', event))

    def check_late_delivery(self, transition):
        """Core gates only: no claim that this is full poll integration."""
        grant = self.commit()
        late = self.candidate()
        transition()
        self.s._alignment_v2_finish_worker_delivery()
        self.assertIsNone(self.s._alignment_v2_worker_latest_args)
        self.assertFalse(self.can_display())
        self.assertFalse(self.s.alignment_core_v2.commit(grant))
        verdict = self.s.alignment_core_v2.adjudicate(late)
        self.assertFalse(verdict.accepted)
        self.assertIsNone(self.s.alignment_core_v2.authorize(verdict))
        self.assertFalse(self.can_display())

    def test_floor_change_rejects_late_worker_and_old_authorization(self):
        def change():
            self.s.current_floor = '1'
            self.s.alignment_core_v2.begin_observation(
                cycle_id=5, map_id='nightmare-03', floor='1', frame_generation=7)
        self.check_late_delivery(change)

    def test_map_change_rejects_late_worker_and_old_authorization(self):
        def change():
            self.s.gate.confirmed_id = 'nightmare-04'
            self.s.alignment_core_v2.begin_observation(
                cycle_id=5, map_id='nightmare-04', floor='2', frame_generation=7)
        self.check_late_delivery(change)

    def test_close_rejects_late_worker_and_old_authorization(self):
        def change():
            self.s.map_is_open = False
            self.s.alignment_core_v2.close(cycle_id=5)
        self.check_late_delivery(change)

    def test_round_end_rejects_late_worker_and_clears_persistent_pose(self):
        def change():
            self.s.map_is_open = False
            self.s.active_map_cycle_id = None
            self.s._alignment_v2_reset_generation += 1
            self.s.alignment_core_v2.reset_round(reason='round-ended')
        self.check_late_delivery(change)
        self.assertEqual(self.s.alignment_core_v2.pose_by_map, {})


if __name__ == '__main__':
    unittest.main()
