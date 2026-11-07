"""Real S1 runtime/Session/coordinator ownership with synthetic IO only.

No native input, game pixels, DPI projection, or installed app is exercised.
"""
import copy
from dataclasses import replace
import sys
import threading
import time
import unittest
from unittest.mock import patch

import numpy as np
from client_coordinator import LiveImages
from client_session import SessionService
import s1_candidate_purpose as purpose
import test_client_recognition as qml
import test_client_coordinator as coordinator_fixture
import test_s1_native_candidate_preview as native
import test_s1_locked_identity as private
import test_s1_formal_round_lock as formal


class DragProbeContracts:
    candidate_purpose = purpose._NATIVE_S1_PREVIEW
    retained_lock = False
    mode = 'nightmare'

    def setUp(self):
        producer_class = (formal.FormalRoundLockContracts if self.retained_lock
            and self.candidate_purpose is purpose._NATIVE_S1_PRODUCTION_CANDIDATE
            else private.LockedIdentityContracts if self.retained_lock
            else native.NativeCandidateContracts)
        self.producer = producer_class()
        self.producer.candidate_purpose = self.candidate_purpose
        self.producer.setUp()
        self.addCleanup(self.producer.doCleanups)
        self.runtime, self.capture = self.producer.runtime, self.producer.capture
        self.runtime._monotonic = lambda: 1.
        if self.mode != 'nightmare':
            self.producer.registry.engine.pack.mode = self.mode
            self.producer.registry.catalog = replace(self.producer.registry.catalog, mode=self.mode)
            self.enterContext(patch('s1_structure_selection.StructureCatalog.from_engine',
                return_value=self.producer.registry.catalog))
            observe = self.runtime._observe.side_effect
            def mode_observe(*args):
                observation, floor, read = observe(*args)
                return observation, floor, dict(read, mode=self.mode)
            self.runtime._observe.side_effect = mode_observe
            self.assertTrue(self.runtime.dispatch('set_context',
                dict(season='s1', mode=self.mode))['success'])
        if self.retained_lock:
            self.pool, self.first = self.producer.locked()
        else:
            self.first = self.producer.show()
            self.pool = self.runtime._s1_private_pool
        self.fixture = coordinator_fixture.CoordinatorTests()
        self.fixture.app = qml.APP
        self.fixture.setUp()
        self.addCleanup(self.fixture.tearDown)
        self.coordinator = self.fixture.coordinator
        self.session = SessionService(self.runtime)
        self.addCleanup(self.cleanup_session)
        self.coordinator.runtime, self.coordinator.session = self.runtime, self.session
        self.coordinator.capture = self.capture
        self.coordinator._bound = ''
        self.coordinator._applied_context = (self.mode, '', 's1')
        self.coordinator.images = LiveImages()
        self.coordinator._convert_client_geometry = lambda row: row.update(deviceScale=1.)
        self.coordinator._sync_reference_payload = lambda row: None
        self.coordinator._pending_context = None
        self.session.actionRequested.connect(self.coordinator._action_requested)
        self.session.changed.connect(self.coordinator._session_changed)
        self.runtime.set_event_callback(self.coordinator.runtimeEvent.emit)
        self.session.refreshRuntime()
        qml.APP.processEvents()
        self.assertTrue(self.coordinator.alignedData)
        self.fixture.windows.hide.reset_mock()

    def cleanup_session(self):
        self.runtime.set_event_callback(None)
        self.session.stop()
        self.session._pool.waitForDone(3000)
        self.session.shutdown()
        qml.APP.processEvents()

    def drag(self, sequence=1):
        context = self.runtime.game_floor_click_context()
        self.assertIsNotNone(context)
        for phase, number, total, delta in (
                ('down', sequence, (0., 0.), (0., 0.)),
                ('move', sequence+1, (13., -7.), (13., -7.)),
                ('up', sequence+2, (13., -7.), (0., 0.))):
            result = self.runtime.observe_game_drag(dict(phase=phase, gestureId=str(sequence),
                pointerSequence=number, context=context, createdAt=1.,
                totalDelta=total, delta=delta))
            self.assertTrue(result['success'], result)
        self.session.refreshRuntime()
        qml.APP.processEvents()
        return dict(gestureId=str(sequence), pointerSequence=sequence+2, context=context)

    def queue_probe(self, payload):
        pending = []
        with patch.object(self.session._pool, 'start', side_effect=pending.append):
            self.session.dispatchWithPayload('probe_map_after_drag', payload)
        self.assertEqual(len(pending), 1)
        return pending[0]

    def settle(self):
        deadline = time.monotonic()+5
        while self.session.busy and time.monotonic() < deadline:
            qml.APP.processEvents()
            time.sleep(.002)
        qml.APP.processEvents()
        self.assertFalse(self.session.busy)

    def assert_withdrawn(self, token):
        qml.APP.processEvents()
        self.assertIsNone(self.runtime.render_payload(token))
        self.assertFalse(self.runtime.snapshot()['displayAuthorized'])
        self.assertFalse(self.coordinator.alignedData)

    def test_repeated_same_floor_drag_preserves_final_consumer_without_pose_writes(self):
        token = self.first['token']
        core = copy.deepcopy(self.runtime.snapshot()['core'])
        reference = self.runtime.reference_payload()
        initial = np.array(self.first['matrix'])
        pool_state = copy.deepcopy(self.pool.snapshot()) if self.pool else None
        for index in range(3):
            payload = self.drag(index*3+1)
            work = self.queue_probe(payload)
            self.assertEqual(self.coordinator.alignedData.get('token'), token)
            with patch.object(self.runtime, '_display_layer', side_effect=AssertionError('probe must not fit/materialize')):
                self.session._pool.start(work)
                self.settle()
            receipt = self.session.snapshot['lastReceipt']
            self.assertTrue(receipt['success'], receipt)
            self.assertEqual(receipt['reason'], 'post-drag-observation-current')
            self.assertFalse(receipt['persistentPoseUpdated'])
            self.assertEqual(receipt['poseCommits'], 0)
            self.assertFalse(receipt['fitAttempted'])
            self.assertEqual(self.coordinator.alignedData.get('token'), token)
            expected = initial.copy(); expected[:2, 2] += [(index+1)*13., (index+1)*-7.]
            np.testing.assert_allclose(self.coordinator.alignedData['matrix'], expected)
        self.fixture.windows.hide.assert_not_called()
        self.assertEqual(self.runtime.snapshot()['core'], core)
        if reference:
            self.assertEqual(self.runtime.reference_payload()['token'], reference['token'])
        if self.pool:
            self.assertEqual(self.pool.snapshot(), pool_state)

    def test_unknown_floor_withdraws_then_fresh_capture_recovers(self):
        self.drag()
        self.producer.floor = None
        result = self.runtime.dispatch('probe_map_after_drag')
        self.assertFalse(result['success'], result)
        self.assertEqual(result['reason'], 'current-floor-unavailable')
        self.session.refreshRuntime(); self.assert_withdrawn(self.first['token'])
        self.producer.floor = '-1'
        result = self.runtime.dispatch('capture_now')
        self.assertTrue(result['success'], result)
        self.session.refreshRuntime(); qml.APP.processEvents()
        self.assertTrue(self.coordinator.alignedData)
        self.assertNotEqual(self.coordinator.alignedData['token'], self.first['token'])

    def test_blocked_probe_retains_authorized_consumer_until_observation(self):
        entered, release = threading.Event(), threading.Event()
        self.addCleanup(release.set)
        original = self.capture.capture
        def blocked(*args, **kwargs):
            entered.set()
            if not release.wait(3):
                raise TimeoutError('fixture capture did not release')
            return original(*args, **kwargs)
        payload = self.drag()
        with patch.object(self.capture, 'capture', side_effect=blocked):
            self.session.dispatchWithPayload('probe_map_after_drag', payload)
            self.assertTrue(entered.wait(3))
            try:
                qml.APP.processEvents()
                self.assertIsNotNone(self.runtime.render_payload(self.first['token']))
                self.assertEqual(self.coordinator.alignedData.get('token'), self.first['token'])
                self.fixture.windows.hide.assert_not_called()
            finally:
                release.set()
                self.settle()

    def test_new_drag_revision_owns_display_over_old_probe_success_or_error(self):
        for error in (False, True):
            with self.subTest(error=error):
                self.drag(1 if not error else 10)
                original = self.capture.capture
                def newer_drag(*args, **kwargs):
                    self.drag(4 if not error else 13)
                    if error:
                        raise OSError('old-probe-after-new-gesture')
                    return original(*args, **kwargs)
                with patch.object(self.capture, 'capture', side_effect=newer_drag):
                    result = self.runtime.dispatch('probe_map_after_drag')
                self.assertFalse(result['success'], result)
                self.assertTrue(result['stale'], result)
                self.assertIsNotNone(self.runtime.render_payload(self.first['token']))
                self.session.refreshRuntime(); qml.APP.processEvents()
                self.assertEqual(self.coordinator.alignedData.get('token'), self.first['token'])

    def test_changed_floor_withdraws_without_fit_then_fresh_capture_recovers(self):
        self.drag()
        self.producer.floor = '-2'
        result = self.runtime.dispatch('probe_map_after_drag')
        self.assertFalse(result['success'], result)
        self.assertEqual(result['reason'], 'current-frame-floor-conflict')
        self.session.refreshRuntime(); self.assert_withdrawn(self.first['token'])
        result = self.runtime.dispatch('capture_now')
        self.assertTrue(result['success'], result)
        self.session.refreshRuntime(); qml.APP.processEvents()
        self.assertEqual(self.coordinator.alignedData['floor'], '-2')

    def test_capture_failure_withdraws_and_next_capture_recovers(self):
        self.drag()
        with patch.object(self.capture, 'capture', side_effect=OSError('fixture-capture-error')):
            result = self.runtime.dispatch('probe_map_after_drag')
        self.assertTrue(result['error'], result)
        self.session.refreshRuntime(); self.assert_withdrawn(self.first['token'])
        self.assertTrue(self.runtime.dispatch('capture_now')['success'])
        self.session.refreshRuntime(); qml.APP.processEvents()
        self.assertTrue(self.coordinator.alignedData)

    def test_current_capture_error_cannot_hand_off_unchecked_revoke_to_dispatch(self):
        self.drag()
        successors = []
        dispatch_code = self.runtime._dispatch_owned.__code__
        previous_trace = sys.gettrace()
        def handoff(frame, event, arg):
            # Force the exact exception hand-off interleaving if a probe lets
            # its current error escape into the generic dispatcher. A new
            # gesture is legal after the probe's ownership lock is released.
            if (event == 'line' and frame.f_code is dispatch_code and not successors
                    and frame.f_locals.get('action') == 'probe_map_after_drag'
                    and isinstance(frame.f_locals.get('error'), OSError)):
                self.drag(4)
                successors.append(self.runtime._refresh_render_owner())
            return handoff
        try:
            sys.settrace(handoff)
            with patch.object(self.capture, 'capture', side_effect=OSError('fixture-current-probe-error')):
                result = self.runtime.dispatch('probe_map_after_drag')
        finally:
            sys.settrace(previous_trace)
        self.assertTrue(result['error'], result)
        if successors:
            self.assertEqual(self.runtime._refresh_render_owner(), successors[0])
            self.assertIsNotNone(self.runtime.render_payload(self.first['token']))
        else:
            # Atomic current-owner failure consumes its grant before return,
            # without the unsafe unlocked exception hand-off above.
            self.session.refreshRuntime(); self.assert_withdrawn(self.first['token'])

    def test_negative_observation_revokes_and_second_miss_closes(self):
        self.drag()
        self.producer.opened = False
        for _ in range(2):
            result = self.runtime.dispatch('probe_map_after_drag')
            self.assertFalse(result['success'], result)
            self.assertEqual(result['reason'], 'map-not-observed')
            self.session.refreshRuntime(); self.assert_withdrawn(self.first['token'])
        self.assertFalse(self.runtime.snapshot()['mapOpen'])

    def test_close_end_focus_and_cancel_before_delivery_never_restore(self):
        for boundary in ('close_map', 'end_round', 'focus-lost', 'cancel'):
            with self.subTest(boundary=boundary):
                self.runtime.dispatch('close_map')
                if not self.runtime.snapshot()['roundActive']:
                    self.runtime.dispatch('start_round')
                result = self.runtime.dispatch('capture_now')
                self.assertTrue(result['success'], result)
                token = self.runtime.snapshot()['render']['token']
                work = self.queue_probe(self.drag())
                work.run()  # Successful result queued for Qt, not yet delivered.
                if boundary == 'focus-lost':
                    self.runtime.invalidate('focus-lost')
                    self.session.cancelPending('focus-lost')
                else:
                    self.session.dispatch(boundary)
                self.settle()
                self.assert_withdrawn(token)

    def test_capture_context_change_rejects_late_probe(self):
        self.drag()
        original = self.capture.capture
        def changed(*args, **kwargs):
            captured = original(*args, **kwargs)
            self.capture.generation += 1
            return captured
        with patch.object(self.capture, 'capture', side_effect=changed):
            result = self.runtime.dispatch('probe_map_after_drag')
        self.assertFalse(result['success'], result)
        self.assertTrue(result['stale'], result)
        self.session.refreshRuntime(); self.assert_withdrawn(self.first['token'])

    def test_late_capture_error_does_not_revoke_successor(self):
        self.drag()
        original = self.capture.capture
        successor = []
        def late_error(*args, **kwargs):
            self.runtime.dispatch('close_map')
            with patch.object(self.capture, 'capture', side_effect=original):
                result = self.runtime.dispatch('capture_now')
                self.assertTrue(result['success'], result)
            successor.append(self.runtime.snapshot()['render']['token'])
            raise OSError('obsolete-fixture-capture')
        with patch.object(self.capture, 'capture', side_effect=late_error):
            result = self.runtime.dispatch('probe_map_after_drag')
        self.assertTrue(result['stale'], result)
        self.assertIsNotNone(self.runtime.render_payload(successor[0]))
        self.session.refreshRuntime(); qml.APP.processEvents()
        self.assertEqual(self.coordinator.alignedData.get('token'), successor[0])


class PrivateCandidateDrag(DragProbeContracts, unittest.TestCase):
    pass


class FormalCandidateDrag(DragProbeContracts, unittest.TestCase):
    candidate_purpose = purpose._NATIVE_S1_PRODUCTION_CANDIDATE


class PrivateRetainedLockDrag(DragProbeContracts, unittest.TestCase):
    retained_lock = True


class FormalRetainedLockDrag(FormalCandidateDrag):
    retained_lock = True


class PrivateDifficultCandidateDrag(PrivateCandidateDrag):
    mode = 'difficult'


class FormalDifficultCandidateDrag(FormalCandidateDrag):
    mode = 'difficult'


class PrivateDifficultRetainedLockDrag(PrivateRetainedLockDrag):
    mode = 'difficult'


class FormalDifficultRetainedLockDrag(FormalRetainedLockDrag):
    mode = 'difficult'


class PrivateDifficulty2CandidateDrag(PrivateCandidateDrag):
    mode = 'difficulty2'


class FormalDifficulty2CandidateDrag(FormalCandidateDrag):
    mode = 'difficulty2'


class PrivateDifficulty2RetainedLockDrag(PrivateRetainedLockDrag):
    mode = 'difficulty2'


class FormalDifficulty2RetainedLockDrag(FormalRetainedLockDrag):
    mode = 'difficulty2'


if __name__ == '__main__':
    unittest.main()
