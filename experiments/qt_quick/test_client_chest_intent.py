"""Real JSON/restart adapter tests; native input and worker sync are substituted."""
import json
import threading
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest import mock

import test_client_chest_auto_tap as fixtures
from client_chest_auto_tap import ChestAutoTapService
from chest_auto_tap import ChestAutoTap, SCHEMA


class RoundRuntime:
    """Real SessionService queue/receipts; no game, capture or native input."""
    def __init__(self):
        self.state = dict(engineConnected=True, season='s0', mode='nightmare', profileId='',
            roundId=1, frameId='', roundActive=True, roundPreparing=False,
            availableCommands=['start_round', 'end_round', 'stop_round', 'reset_round',
                               'toggle_round', 'map', 'capture_now', 'prepare_manual_alignment'])
        self.started, self.release = threading.Event(), threading.Event()
        self.release.set()
        self.fail_actions = set()
        self.ignore_cancel = False

    def snapshot(self):
        return dict(self.state)

    def dispatch(self, action, payload, cancelled):
        self.started.set()
        if not self.release.wait(2):
            raise RuntimeError('fixture was not released')
        success = action not in self.fail_actions and (self.ignore_cancel or not cancelled.is_set())
        if success:
            ending = action in {'end_round', 'stop_round', 'reset_round'} or (
                action == 'toggle_round' and (self.state['roundActive'] or self.state['roundPreparing']))
            if ending:
                self.state.update(roundActive=False, roundPreparing=False)
            else:
                if not (self.state['roundActive'] or self.state['roundPreparing']):
                    self.state['roundId'] += 1
                self.state.update(roundActive=action != 'start_round', roundPreparing=action == 'start_round')
        return dict(action=action, success=success, requestContext=dict(payload),
            resultContext={key: self.state[key] for key in ('season', 'mode', 'profileId', 'roundId', 'frameId')})


class IntentTests(unittest.TestCase):
    enable = fixtures.ChestTests.enable
    def setUp(self):
        fixtures.ChestTests.setUp(self)
        self.adapter.shutdown()
        self.path = Path(self.temp.name) / 'chest-auto-tap.json'
        self.path.write_text(json.dumps(dict(schema=SCHEMA, enabled=True,
            holdKey='F12', tapKey='F', intervalMs=90, future={'keep': 'opaque'})))
        self.held = False
        from client_session import SessionService
        self.owner.session = SessionService()
        self.syncs = []
        def sync_without_native_worker(service):
            enabled = (not service._closed and service.settings['enabled']
                       and service.available and not service._runtime_error
                       and not any(service._pending_releases.values()))
            (service._stop.clear if enabled else service._stop.set)()
            self.syncs.append(enabled)
        self.patch = mock.patch.object(ChestAutoTap, 'sync', sync_without_native_worker)
        self.patch.start()
        self.addCleanup(self.patch.stop)
        self.restart()

    def restart(self):
        self.adapter.shutdown()
        self.adapter = ChestAutoTapService(SimpleNamespace(settings_path=Path(self.temp.name)/'ui-settings.json'),
            self.owner, self.input, self.settings, native=self.native, key_is_down=lambda vk: self.held)
        self.adapter._service.available = True
        self.addCleanup(self.adapter.shutdown)
        self.service = self.adapter._service

    def test_focus_resume_preserves_explicit_intent_without_resaving(self):
        self.assertTrue(self.enable()['success'])
        self.focus = 99
        self.adapter.pollOnce()
        self.assertTrue(self.adapter.snapshot['enabled'])
        self.assertFalse(self.adapter.snapshot['running'])
        with mock.patch.object(self.service, '_save', side_effect=OSError('fixture readonly')) as save:
            self.focus = 42
            self.adapter.pollOnce()
        save.assert_not_called()
        self.assertTrue(self.adapter.snapshot['running'])

    def test_temporary_catalog_absence_pauses_without_revoking_intent(self):
        self.assertTrue(self.enable()['success'])
        original_rows = self.owner.gameWindows
        self.focus = 99
        self.owner.gameWindows = []
        self.adapter.pollOnce()
        self.assertTrue(self.adapter.snapshot['savedEnabled'])
        self.assertTrue(self.adapter.snapshot['enabled'])
        self.assertFalse(self.adapter.snapshot['running'])
        self.assertIsNone(self.service._target_window())
        self.focus = 42
        self.adapter.pollOnce()
        self.assertFalse(self.adapter.snapshot['running'])
        self.owner.gameWindows = original_rows
        self.held = True
        self.adapter.pollOnce()
        self.assertFalse(self.adapter.snapshot['running'])
        self.held = False
        self.adapter.pollOnce()
        self.assertTrue(self.adapter.snapshot['running'])

    def test_catalog_return_with_replaced_process_revokes_intent(self):
        self.assertTrue(self.enable()['success'])
        original_rows = self.owner.gameWindows
        self.owner.gameWindows = []
        self.adapter.pollOnce()
        self.owner.gameWindows = [dict(original_rows[0], pid=701)]
        self.pid = 701
        self.adapter.pollOnce()
        self.assertTrue(self.adapter.snapshot['savedEnabled'])
        self.assertFalse(self.adapter.snapshot['enabled'])
        self.assertFalse(self.adapter.snapshot['running'])

    def test_missing_catalog_during_restore_or_enable_waits_for_identity(self):
        self.assertTrue(self.enable()['success'])
        rows = self.owner.gameWindows
        for restart in (True, False):
            with self.subTest(restart=restart):
                self.owner.gameWindows = []
                if restart:
                    self.restart()
                else:
                    self.assertTrue(self.enable()['success'])
                self.adapter.pollOnce()
                self.assertTrue(self.adapter.snapshot['enabled'])
                self.assertFalse(self.adapter.snapshot['running'])
                self.owner.gameWindows = rows
                self.adapter.pollOnce()
                self.assertTrue(self.adapter.snapshot['running'])

    def test_real_coordinator_unknown_catalog_does_not_disarm_chest(self):
        from test_client_coordinator import CoordinatorTests
        fixture = CoordinatorTests()
        fixture.setUpClass()
        fixture.setUp()
        self.addCleanup(fixture.tearDown)
        coordinator = fixture.coordinator
        rows = [dict(self.owner.gameWindows[0], processCreateTime=123,
                     executable='fixture-game.exe')]
        coordinator._bound = '42'
        coordinator._binding_identity = coordinator._window_identity(rows[0])
        coordinator._window_rows = rows
        coordinator._catalog = mock.Mock(return_value=[])
        coordinator._binding_probe = mock.Mock(return_value=dict(status='unknown', reason='fixture-unreadable'))
        coordinator.capture.disable = mock.Mock()
        self.owner = coordinator
        self.restart()
        self.assertTrue(self.enable()['success'])
        coordinator._discover_game()
        self.assertEqual(coordinator.boundWindow, '42')
        self.assertTrue(coordinator._binding_suspended)
        self.assertTrue(self.adapter.snapshot['enabled'])
        self.assertFalse(self.adapter.snapshot['running'])
        coordinator._catalog.return_value = rows
        coordinator._discover_game()
        self.assertFalse(coordinator._binding_suspended)
        self.assertTrue(self.adapter.snapshot['running'])

    def test_real_worker_catalog_gap_releases_and_requires_new_gesture(self):
        import threading
        import time
        self.patch.stop()
        events = []
        pressed, released = threading.Event(), threading.Event()
        def send(vk, up):
            events.append((vk, up))
            (released if up else pressed).set()
            return 0
        with mock.patch('chest_auto_tap.key_down', lambda vk: self.held), \
             mock.patch('chest_auto_tap.foreground_window', lambda: self.focus), \
             mock.patch('chest_auto_tap.same_top_window', lambda a, b: bool(b and a == b)), \
             mock.patch('chest_auto_tap._send_key', send):
            try:
                self.enable()
                deadline = time.monotonic() + 1
                while not self.service.snapshot()['status'].startswith('等待按住'):
                    self.assertLess(time.monotonic(), deadline)
                    time.sleep(.002)
                self.held = True
                self.assertTrue(pressed.wait(1))
                rows = self.owner.gameWindows
                self.owner.gameWindows = []
                self.focus = 99
                self.adapter.pollOnce()
                self.assertTrue(released.wait(1))
                count = sum(not up for _, up in events)
                self.focus = 42
                self.adapter.pollOnce()
                time.sleep(.04)
                self.assertEqual(sum(not up for _, up in events), count)
                self.owner.gameWindows = rows
                self.adapter.pollOnce()
                time.sleep(.04)
                self.assertEqual(sum(not up for _, up in events), count)
                self.held = False
                self.adapter.pollOnce()
                deadline = time.monotonic() + 1
                while not self.service.snapshot()['status'].startswith('等待按住'):
                    self.assertLess(time.monotonic(), deadline)
                    time.sleep(.002)
                pressed.clear()
                self.held = True
                self.assertTrue(pressed.wait(1))
                self.assertTrue(self.adapter.snapshot['savedEnabled'])
            finally:
                self.held = False
                self.adapter.shutdown()
                if self.service._thread:
                    self.service._thread.join(1)
                    self.assertFalse(self.service._thread.is_alive())

    def test_compiled_binding_change_without_save_stops_conflicting_worker(self):
        self.assertTrue(self.enable()['success'])
        self.input.snapshot['bindings']['confirm-round'] = 'F12'
        self.input.changed.emit()
        self.assertFalse(self.adapter.snapshot['enabled'])
        self.assertFalse(self.adapter.snapshot['running'])
        self.assertIn('confirm-round', self.adapter.snapshot['error'])

    def test_pending_normal_release_waits_without_revoking_explicit_intent(self):
        self.assertTrue(self.enable()['success'])
        self.focus = 99
        self.adapter.pollOnce()
        self.service._pending_releases[ord('F')] = 0
        self.focus = 42
        self.adapter.pollOnce()
        self.assertTrue(self.adapter.snapshot['enabled'])
        self.assertFalse(self.adapter.snapshot['running'])
        self.assertIn('等待上次按键释放', self.adapter.snapshot['message'])
        self.service._pending_releases.clear()
        self.adapter.pollOnce()
        self.assertTrue(self.adapter.snapshot['running'])

    def test_real_restart_restores_only_explicit_qt_intent(self):
        self.assertFalse(self.adapter.snapshot['enabled'])
        self.assertTrue(self.enable()['success'])
        self.restart()
        self.assertTrue(self.adapter.snapshot['savedEnabled'])
        self.assertTrue(self.adapter.snapshot['enabled'])
        self.assertFalse(self.adapter.snapshot['running'])
        self.assertTrue(self.service._stop.is_set())
        self.adapter.pollOnce()
        self.assertTrue(self.adapter.snapshot['running'])
        self.assertEqual(json.loads(self.path.read_text())['future'], {'keep': 'opaque'})

    def test_disable_with_invalid_parameters_persists_false(self):
        self.enable()
        self.adapter.update(False, 'F', 'F', 'bad')
        self.restart()
        self.assertFalse(self.adapter.snapshot['savedEnabled'])
        self.assertFalse(self.adapter.snapshot['enabled'])

    def test_conflict_preserves_saved_intent_across_restart(self):
        self.enable()
        self.input.snapshot['bindings']['conflict'] = 'CTRL+F12'
        self.settings.saved.emit()
        self.assertFalse(self.adapter.snapshot['running'])
        self.assertTrue(self.adapter.snapshot['savedEnabled'])
        self.restart()
        self.adapter.pollOnce()
        self.assertFalse(self.adapter.snapshot['running'])
        self.assertTrue(self.adapter.snapshot['savedEnabled'])

    def conflict(self, key):
        self.assertTrue(self.enable()['success'])
        self.input.snapshot['bindings']['confirm-round'] = key
        self.settings.saved.emit()
        self.assertFalse(self.adapter.snapshot['running'])
        self.assertIn('confirm-round', self.adapter.snapshot['error'])

    def test_corrected_trigger_and_tap_conflicts_survive_following_polls(self):
        for conflict, hold, tap in [('F12', 'F4', 'F'), ('F', 'F12', 'E')]:
            with self.subTest(conflict=conflict):
                self.input.snapshot['bindings'].pop('confirm-round', None)
                self.restart()
                self.conflict(conflict)
                self.assertTrue(self.adapter.snapshot['savedEnabled'])
                self.assertTrue(self.adapter.update(True, hold, tap, '90')['success'])
                for _ in range(3):
                    self.adapter.pollOnce()
                state = self.adapter.snapshot
                self.assertEqual((state['holdKey'], state['tapKey']), (hold, tap))
                self.assertTrue(state['running'])
                self.assertEqual(state['error'], '')
                self.assertNotIn('confirm-round', state['message'])

    def test_still_conflicting_or_failed_save_cannot_clear_reason(self):
        self.conflict('F12')
        self.assertFalse(self.adapter.update(True, 'F12', 'E', '90')['success'])
        self.assertIn('confirm-round', self.adapter.snapshot['error'])
        with mock.patch.object(self.service, '_save', side_effect=OSError('disk full')):
            self.assertFalse(self.adapter.update(True, 'F4', 'F', '90')['success'])
        self.assertEqual(self.adapter.snapshot['holdKey'], 'F12')
        self.assertIn('confirm-round', self.adapter.snapshot['error'])
        self.assertFalse(self.adapter.snapshot['running'])

    def test_corrected_conflict_does_not_clear_native_fault_or_release_ledger(self):
        self.conflict('F12')
        self.service._fault('native key-up failed')
        self.service._pending_releases[ord('F')] = 4
        self.adapter.update(True, 'F4', 'F', '90')
        self.adapter.pollOnce()
        self.assertIn('native key-up failed', self.adapter.snapshot['error'])
        self.assertEqual(self.adapter.snapshot['pendingReleaseVks'], [ord('F')])
        self.assertFalse(self.adapter.snapshot['running'])
        self.assertNotIn('confirm-round', self.adapter.snapshot['error'])

    def test_fixing_ordinary_binding_waits_for_explicit_reenable(self):
        self.conflict('F12')
        self.input.snapshot['bindings']['confirm-round'] = 'F8'
        self.settings.saved.emit()
        self.adapter.pollOnce()
        self.assertTrue(self.adapter.snapshot['savedEnabled'])
        self.assertFalse(self.adapter.snapshot['enabled'])
        self.assertFalse(self.adapter.snapshot['running'])
        self.assertTrue(self.enable()['success'])
        self.adapter.pollOnce()
        self.assertTrue(self.adapter.snapshot['running'])
        self.assertEqual(self.adapter.snapshot['error'], '')

    def test_loaded_conflict_can_be_repaired_without_restart(self):
        self.conflict('F12')
        self.restart()
        self.assertIn('confirm-round', self.adapter.snapshot['error'])
        self.assertTrue(self.adapter.update(True, 'F4', 'F', '90')['success'])
        self.adapter.pollOnce()
        self.assertTrue(self.adapter.snapshot['running'])
        self.assertEqual(self.adapter.snapshot['error'], '')

    def test_native_fault_without_pending_release_survives_valid_update(self):
        self.conflict('F12')
        self.service._fault('native key-down failed')
        before = self.syncs.count(True)
        self.adapter.update(True, 'F4', 'F', '90')
        self.adapter.pollOnce()
        self.assertEqual(self.syncs.count(True), before)
        self.assertIn('native key-down failed', self.adapter.snapshot['error'])
        self.assertNotIn('confirm-round', self.adapter.snapshot['error'])
        self.assertFalse(self.adapter.snapshot['running'])

    def test_correcting_while_disabled_never_rearms_or_changes_false_intent(self):
        self.conflict('F12')
        self.assertTrue(self.adapter.update(False, 'F4', 'F', '90')['success'])
        self.adapter.pollOnce()
        self.assertFalse(self.adapter.snapshot['savedEnabled'])
        self.assertFalse(self.adapter.snapshot['enabled'])
        self.assertFalse(self.adapter.snapshot['running'])
        self.assertEqual(self.adapter.snapshot['error'], '')

    def test_corrected_conflict_waits_for_release_before_restarting(self):
        self.conflict('F12')
        self.held = True
        self.assertTrue(self.adapter.update(True, 'F4', 'F', '90')['success'])
        self.assertFalse(self.adapter.snapshot['running'])
        self.assertEqual(self.adapter.snapshot['error'], '')
        self.adapter.pollOnce()
        self.assertFalse(self.adapter.snapshot['running'])
        self.held = False
        self.adapter.pollOnce()
        self.assertTrue(self.adapter.snapshot['running'])

    def test_restart_while_held_waits_for_release_and_new_press(self):
        self.enable()
        self.held = True
        self.restart()
        self.adapter.pollOnce()
        self.assertFalse(self.adapter.snapshot['running'])
        self.assertIsNone(self.service._target_window())
        self.held = False
        self.adapter.pollOnce()
        self.assertTrue(self.adapter.snapshot['running'])
        self.focus = 99
        self.adapter.pollOnce()
        self.held = True
        self.focus = 42
        self.adapter.pollOnce()
        self.assertFalse(self.adapter.snapshot['running'])
        self.held = False
        self.adapter.pollOnce()
        self.assertTrue(self.adapter.snapshot['running'])

    def test_restored_intent_waits_for_first_valid_binding_then_disarms_on_loss(self):
        self.enable()
        self.owner.boundWindow = ''
        self.restart()
        self.adapter.pollOnce()
        self.assertFalse(self.adapter.snapshot['running'])
        self.owner.boundWindow = '42'
        self.adapter.pollOnce()
        self.assertTrue(self.adapter.snapshot['running'])
        self.owner.boundWindow = ''
        self.adapter.pollOnce()
        self.owner.boundWindow = '42'
        self.adapter.pollOnce()
        self.assertFalse(self.adapter.snapshot['running'])
        self.assertTrue(self.adapter.snapshot['savedEnabled'])

    def test_end_round_pauses_until_new_round_without_forgetting_or_resaving(self):
        self.owner.session._runtime = SimpleNamespace()
        self.owner.session._state.update(engineConnected=True, roundId=7, roundActive=True)
        self.enable()
        intent = self.adapter._intent_path.read_bytes()
        with mock.patch.object(self.service, '_save', side_effect=AssertionError('must not resave')):
            self.owner.session.actionRequested.emit('end_round', {})
            self.assertFalse(self.adapter.snapshot['running'])
            self.assertTrue(self.service._stop.is_set())
            self.assertTrue(self.adapter.snapshot['waitingForRound'])
            self.assertTrue(self.adapter.snapshot['enabled'])
            self.assertTrue(self.adapter.snapshot['savedEnabled'])
            self.assertFalse(self.adapter.snapshot['needsManualResume'])
            # The old active snapshot can remain visible until the worker ends.
            self.adapter.pollOnce()
            self.assertFalse(self.adapter.snapshot['running'])
            self.owner.session._state.update(roundActive=False)
            self.owner.session.changed.emit()
            self.assertFalse(self.adapter.snapshot['running'])
            self.owner.session.actionRequested.emit('start_round', {})
            self.owner.session._state.update(roundId=8, roundPreparing=True)
            self.owner.session.changed.emit()
            self.assertTrue(self.adapter.snapshot['running'])
            self.assertFalse(self.adapter.snapshot['waitingForRound'])
        self.assertEqual(self.adapter._intent_path.read_bytes(), intent)

    def test_round_resume_requires_current_ready_state_and_fresh_gesture(self):
        self.owner.session._runtime = SimpleNamespace()
        self.owner.session._state.update(engineConnected=True, roundId=7, roundActive=True)
        self.enable()
        self.owner.session.actionRequested.emit('end_round', {})
        self.owner.session.actionRequested.emit('start_round', {})
        for state in (
            dict(roundId=7, roundActive=True),
            dict(roundId=8, roundActive=False, roundPreparing=False),
            dict(roundId=8, roundActive=True, engineConnected=False),
            dict(roundId=8, roundActive=True, contextPending=True),
            dict(roundId=8, roundActive=True, resourceLocked=True),
        ):
            with self.subTest(state=state):
                self.owner.session._state = dict({'engineConnected': True}, **state)
                self.owner.session._leases = {'fixture'} if state.get('resourceLocked') else set()
                self.adapter.pollOnce()
                self.assertFalse(self.adapter.snapshot['running'])
        self.owner.session._leases.clear()
        self.owner.session._state = dict(engineConnected=True, roundId=8, roundActive=True)
        self.held = True
        self.focus = 99
        self.adapter.pollOnce()
        self.assertFalse(self.adapter.snapshot['running'])
        self.focus = 42
        self.adapter.pollOnce()
        self.assertFalse(self.adapter.snapshot['running'])
        self.held = False
        self.adapter.pollOnce()
        self.assertTrue(self.adapter.snapshot['running'])

    def test_round_transition_does_not_clear_safety_disarm_or_explicit_off(self):
        self.owner.session._runtime = SimpleNamespace()
        for cause in ('binding', 'conflict', 'fault', 'off'):
            with self.subTest(cause=cause):
                self.input.snapshot['bindings'].pop('conflict', None)
                self.owner.boundWindow = '42'
                self.restart()
                self.owner.session._state = dict(engineConnected=True, roundId=7, roundActive=True)
                self.enable()
                self.owner.session.actionRequested.emit('end_round', {})
                if cause == 'binding':
                    self.owner.boundWindow = ''
                elif cause == 'conflict':
                    self.input.snapshot['bindings']['conflict'] = 'F12'
                elif cause == 'fault':
                    self.service._fault('synthetic key-up failure')
                else:
                    self.adapter.update(False, 'F12', 'F', '90')
                self.adapter.pollOnce()
                self.assertFalse(self.adapter.snapshot['enabled'])
                self.assertEqual(self.adapter.snapshot['needsManualResume'], cause != 'off')
                self.owner.boundWindow = '42'
                self.input.snapshot['bindings'].pop('conflict', None)
                self.owner.session.actionRequested.emit('start_round', {})
                self.owner.session._state.update(roundId=8)
                self.adapter.pollOnce()
                self.assertFalse(self.adapter.snapshot['running'])
                self.assertEqual(self.adapter.snapshot['savedEnabled'], cause != 'off')

    def test_repeated_terminal_actions_preserve_round_pause_without_rearming(self):
        self.owner.session._runtime = SimpleNamespace()
        for action in ('end_round', 'stop_round', 'reset_round', 'toggle_round'):
            with self.subTest(action=action):
                self.owner.session._state = dict(engineConnected=True, roundId=7, roundPreparing=True)
                self.enable()
                for _ in range(2):
                    self.owner.session.actionRequested.emit(action, {})
                    self.adapter.pollOnce()
                    self.assertFalse(self.adapter.snapshot['running'])
                    self.assertTrue(self.adapter.snapshot['enabled'])
                self.owner.session.actionRequested.emit('start_round', {})
                self.owner.session._state.update(roundId=8)
                self.adapter.pollOnce()
                self.assertTrue(self.adapter.snapshot['running'])

    def test_end_round_does_not_forget_across_restart(self):
        self.enable()
        self.owner.session.actionRequested.emit('end_round', {})
        self.restart()
        self.assertTrue(self.adapter.snapshot['enabled'])

    def attach_round_runtime(self):
        from client_session import SessionService
        from test_client_session import until
        runtime = RoundRuntime()
        session = self.owner.session = SessionService(runtime)
        self.restart()
        def cleanup():
            runtime.release.set()
            session.shutdown()
            until(lambda: not session._jobs)
        self.addCleanup(cleanup)
        return runtime, session, until

    def test_real_session_end_failed_cancelled_and_successful_next_start(self):
        runtime, session, until = self.attach_round_runtime()
        self.enable()
        intent = self.adapter._intent_path.read_bytes()
        runtime.release.clear()
        session.dispatch('end_round')
        self.assertFalse(self.adapter.snapshot['running'])
        self.assertTrue(self.service._stop.is_set())
        until(runtime.started.is_set)
        self.adapter.pollOnce()
        self.assertFalse(self.adapter.snapshot['running'])
        runtime.release.set()
        until(lambda: not session._jobs)
        self.assertTrue(self.adapter.snapshot['waitingForRound'])
        runtime.fail_actions.add('start_round')
        session.dispatch('start_round')
        until(lambda: not session._jobs)
        self.assertFalse(session.snapshot['lastReceipt']['success'])
        self.assertFalse(self.adapter.snapshot['running'])
        runtime.fail_actions.clear()
        runtime.started.clear()
        runtime.release.clear()
        session.dispatch('start_round')
        until(runtime.started.is_set)
        session.cancelPending('fixture-cancelled-start')
        runtime.release.set()
        until(lambda: not session._jobs)
        self.assertFalse(self.adapter.snapshot['running'])
        # Every real next-round entry uses the same validated session transition.
        with mock.patch.object(self.service, '_save', side_effect=AssertionError('must not resave')):
            for action in ('start_round', 'map', 'capture_now', 'prepare_manual_alignment', 'toggle_round'):
                with self.subTest(action=action):
                    session.dispatch(action)
                    until(lambda: not session._jobs)
                    self.assertTrue(session.snapshot['lastReceipt']['success'])
                    self.assertTrue(self.adapter.snapshot['running'])
                    self.assertFalse(self.adapter.snapshot['waitingForRound'])
                    session.dispatch('end_round')
                    self.assertFalse(self.adapter.snapshot['running'])
                    until(lambda: not session._jobs)
                    self.assertTrue(self.adapter.snapshot['savedEnabled'])
        self.assertEqual(self.adapter._intent_path.read_bytes(), intent)

    def test_pre_end_start_result_cannot_revive_input_after_end_failure(self):
        runtime, session, until = self.attach_round_runtime()
        runtime.state.update(roundActive=False)
        session.refreshRuntime()
        self.enable()
        runtime.ignore_cancel = True  # Adversarial worker that ignores cancellation.
        runtime.fail_actions.add('end_round')
        runtime.release.clear()
        session.dispatch('start_round')
        until(runtime.started.is_set)
        session.dispatch('end_round')
        self.assertFalse(self.adapter.snapshot['running'])
        runtime.release.set()
        until(lambda: not session._jobs)
        session.refreshRuntime()  # Even observing the stale worker's new ID is insufficient.
        self.adapter.pollOnce()
        self.assertGreater(runtime.state['roundId'], 1)
        self.assertTrue(runtime.state['roundPreparing'])
        self.assertFalse(self.adapter.snapshot['running'])
        self.assertTrue(self.adapter.snapshot['waitingForRound'])

    def test_cancelled_next_start_cannot_revive_from_a_late_runtime_refresh(self):
        runtime, session, until = self.attach_round_runtime()
        self.enable()
        session.dispatch('end_round')
        until(lambda: not session._jobs)
        runtime.ignore_cancel = True
        runtime.started.clear()
        runtime.release.clear()
        session.dispatch('start_round')
        until(runtime.started.is_set)
        session.cancelPending('fixture-cancelled-start')
        runtime.release.set()
        until(lambda: not session._jobs)
        session.refreshRuntime()
        self.assertGreater(runtime.state['roundId'], 1)
        self.assertTrue(runtime.state['roundPreparing'])
        self.adapter.pollOnce()
        self.assertFalse(self.adapter.snapshot['running'])
        self.assertTrue(self.adapter.snapshot['waitingForRound'])

    def test_worker_releases_at_end_and_never_repeats_held_key_in_next_round(self):
        import time
        runtime, session, until = self.attach_round_runtime()
        self.patch.stop()
        pressed, released = threading.Event(), threading.Event()
        events = []
        def send(vk, up):
            events.append((vk, up))
            (released if up else pressed).set()
            return 0
        with mock.patch('chest_auto_tap.key_down', lambda vk: self.held), \
             mock.patch('chest_auto_tap._send_key', send), \
             mock.patch.object(self.service, '_in_game', lambda: self.service._target_window() == 42):
            try:
                self.enable()
                until(lambda: self.service.snapshot()['status'].startswith('等待按住'))
                self.held = True
                self.assertTrue(pressed.wait(1))
                session.dispatch('end_round')
                self.assertFalse(self.adapter.snapshot['running'])
                self.assertTrue(released.wait(1))
                until(lambda: not session._jobs)
                before = sum(not up for _, up in events)
                session.dispatch('start_round')
                until(lambda: not session._jobs)
                time.sleep(.04)
                self.assertEqual(sum(not up for _, up in events), before)
                self.assertFalse(self.adapter.snapshot['running'])
                self.held = False
                self.adapter.pollOnce()
                until(lambda: self.service.snapshot()['status'].startswith('等待按住'))
                pressed.clear()
                self.held = True
                self.assertTrue(pressed.wait(1))
                self.assertTrue(self.adapter.snapshot['savedEnabled'])
            finally:
                self.held = False
                self.adapter.shutdown()
                if self.service._thread:
                    self.service._thread.join(1)
                    self.assertFalse(self.service._thread.is_alive())

    def test_qml_distinguishes_round_wait_and_safety_pause_without_switch_reset(self):
        from PySide6.QtCore import QObject, QMetaObject, QUrl
        from PySide6.QtQml import QQmlComponent, QQmlEngine
        runtime, session, until = self.attach_round_runtime()
        engine = QQmlEngine()
        engine.rootContext().setContextProperty('chestAutoTapService', self.adapter)
        component = QQmlComponent(engine, QUrl.fromLocalFile(str(Path(__file__).with_name('ClientChestAutoTap.qml'))))
        root = component.create()
        self.assertIsNotNone(root, [error.toString() for error in component.errors()])
        try:
            root.setProperty('width', 400)
            switch = root.findChild(QObject, 'chestAutoTapEnabled')
            resume = root.findChild(QObject, 'chestResume')
            wait = root.findChild(QObject, 'chestRoundPause')
            self.enable()
            fixtures.APP.processEvents()
            self.assertTrue(switch.property('checked'))
            self.assertFalse(resume.property('visible'))
            session.dispatch('end_round')
            until(lambda: not session._jobs)
            self.assertTrue(switch.property('checked'))
            self.assertTrue(wait.property('visible'))
            self.assertFalse(resume.property('visible'))
            self.owner.boundWindow = ''
            self.adapter.pollOnce()
            fixtures.APP.processEvents()
            self.assertTrue(switch.property('checked'))
            self.assertTrue(resume.property('visible'))
            self.assertFalse(wait.property('visible'))
            self.owner.boundWindow = '42'
            session.dispatch('start_round')
            until(lambda: not session._jobs)
            self.assertFalse(self.adapter.snapshot['running'])
            QMetaObject.invokeMethod(resume, 'clicked')
            fixtures.APP.processEvents()
            self.assertTrue(self.adapter.snapshot['running'])
            self.assertTrue(switch.property('checked'))
            self.assertFalse(resume.property('visible'))
        finally:
            root.deleteLater()
            fixtures.APP.processEvents()

    def test_worker_fault_keeps_saved_intent(self):
        self.enable()
        self.service._fault('synthetic native failure')
        self.adapter.pollOnce()
        self.assertFalse(self.adapter.snapshot['running'])
        self.assertTrue(self.adapter.snapshot['savedEnabled'])
        self.restart()
        self.assertTrue(self.adapter.snapshot['savedEnabled'])

    def test_physical_read_exception_stays_stopped(self):
        self.adapter._key_is_down = mock.Mock(side_effect=OSError('unavailable'))
        self.enable()
        self.assertFalse(self.adapter.snapshot['running'])
        self.assertIsNone(self.service._target_window())
        self.assertTrue(self.adapter.snapshot['savedEnabled'])

    def test_legacy_tk_write_cannot_erase_qt_intent(self):
        self.enable()
        legacy = ChestAutoTap(self.path, target_window=lambda: None)
        self.assertTrue(legacy.update(dict(enabled=False))['success'])
        self.restart()
        self.assertTrue(self.adapter.snapshot['enabled'])

    def test_intent_requires_exact_schema_and_boolean(self):
        path = Path(self.temp.name) / 'qt-chest-intent.json'
        from client_chest_preferences import INTENT_SCHEMA
        for raw in ({'schema': INTENT_SCHEMA, 'enabled': 'true'},
                    {'schema': INTENT_SCHEMA, 'enabled': 1},
                    {'schema': 'earlier-experiment', 'enabled': True},
                    {'enabled': True}):
            with self.subTest(raw=raw):
                path.write_text(json.dumps(raw))
                self.restart()
                self.adapter.pollOnce()
                self.assertFalse(self.adapter.snapshot['enabled'])
                self.assertEqual(json.loads(path.read_text()), raw)

    def test_unknown_intent_fields_survive_and_broken_file_is_not_repaired(self):
        from client_chest_preferences import INTENT_SCHEMA
        path = Path(self.temp.name) / 'qt-chest-intent.json'
        path.write_text(json.dumps(dict(schema=INTENT_SCHEMA, enabled=False, future=['keep'])))
        self.enable()
        self.assertEqual(json.loads(path.read_text())['future'], ['keep'])
        path.write_text('{broken')
        self.assertFalse(self.adapter.update(False, 'F12', 'F', '90')['success'])
        self.assertFalse(self.adapter.snapshot['running'])
        self.assertEqual(path.read_text(), '{broken')
        self.restart()
        self.assertFalse(self.adapter.snapshot['enabled'])

    def test_real_worker_only_sends_after_release_then_press_and_releases_on_disable(self):
        import threading
        self.patch.stop()
        self.held = True
        pressed, released = threading.Event(), threading.Event()
        def send(vk, up):
            (released if up else pressed).set()
            return 0
        with mock.patch('chest_auto_tap.key_down', lambda vk: self.held), \
             mock.patch('chest_auto_tap._send_key', send), \
             mock.patch.object(self.service, '_in_game', lambda: self.service._target_window() == 42):
            self.enable()
            self.assertFalse(pressed.wait(.03))
            self.held = False
            self.adapter.pollOnce()
            self.assertFalse(pressed.wait(.03))
            self.held = True
            self.assertTrue(pressed.wait(1))
            self.adapter.update(False, 'F12', 'F', '90')
            self.assertTrue(released.wait(1))
            self.assertIsNone(self.service._target_window())
            self.assertFalse(self.adapter.snapshot['savedEnabled'])

    def test_corrupt_shared_file_not_overwritten(self):
        damaged = '{"enabled":true, broken'
        self.path.write_text(damaged)
        self.assertFalse(self.enable()['success'])
        self.assertEqual(self.path.read_text(), damaged)

    def test_damaged_known_shared_parameter_not_overwritten_on_restore(self):
        self.enable()
        raw = json.loads(self.path.read_text())
        raw['intervalMs'] = {'broken': 90}
        self.path.write_text(json.dumps(raw))
        self.restart()
        self.adapter.pollOnce()
        self.assertFalse(self.adapter.snapshot['running'])
        self.assertEqual(json.loads(self.path.read_text()), raw)

    def test_first_binding_action_does_not_erase_restored_intent(self):
        self.enable()
        self.owner.boundWindow = ''
        self.restart()
        self.owner.boundWindow = '42'
        self.input.snapshot['enabled'] = False
        self.input.changed.emit()
        self.owner.session.actionRequested.emit('bind_capture', {'hwnd': 42})
        self.input.snapshot['enabled'] = True
        self.input.changed.emit()
        self.assertTrue(self.adapter.snapshot['running'])

    def test_recycled_handle_with_new_process_disarms(self):
        self.enable()
        self.owner.gameWindows[0]['pid'] = 701
        self.pid = 701
        self.adapter.pollOnce()
        self.assertFalse(self.adapter.snapshot['running'])
        self.assertFalse(self.adapter.snapshot['enabled'])
        self.assertTrue(self.adapter.snapshot['savedEnabled'])

    def test_separate_process_restart_reads_only_qt_marker(self):
        import subprocess
        import sys
        script = r'''
import json, sys
from pathlib import Path
from types import SimpleNamespace
from PySide6.QtCore import QObject, Signal
from PySide6.QtGui import QGuiApplication
from client_chest_auto_tap import ChestAutoTapService
from chest_auto_tap import ChestAutoTap
app = QGuiApplication([])
class Owner(QObject):
    changed = Signal()
    saved = Signal()
owner, native_input, settings = Owner(), Owner(), Owner()
owner.boundWindow = '42'
owner.closing = owner.textEntryActive = False
owner.gameWindows = [dict(hwnd='42', pid=700)]
native_input.snapshot = dict(enabled=True, hwnd='42', bindings={})
native = SimpleNamespace(get_user32=lambda: SimpleNamespace(IsWindow=lambda h: True,
    GetForegroundWindow=lambda: 42), window_process_id=lambda h: 700,
    window_text=lambda h: 'game', process_image_path=lambda p: 'game/dwrg.exe',
    identity_is_allowed=lambda t, p: True)
ChestAutoTap.sync = lambda self: None
service = ChestAutoTapService(SimpleNamespace(settings_path=Path(sys.argv[1])/'ui-settings.json'),
    owner, native_input, settings, native=native, key_is_down=lambda vk: True)
service._service.available = True
if sys.argv[2] != 'read':
    result = service.update(sys.argv[2] == 'enable', 'F12', 'F', '90')
    assert result['success'], result
service.pollOnce()
assert not service.snapshot['running']
assert service._service._stop.is_set()
Path(sys.argv[1], 'process-result.json').write_text(json.dumps(service.snapshot))
service.shutdown()
'''
        def run(action):
            result = subprocess.run([sys.executable, '-c', script, self.temp.name, action],
                                    capture_output=True, text=True, timeout=15)
            self.assertEqual(result.returncode, 0, result.stderr)
            return json.loads((Path(self.temp.name) / 'process-result.json').read_text())
        self.assertFalse(run('read')['savedEnabled'])
        self.assertTrue(run('enable')['savedEnabled'])
        self.assertTrue(run('read')['enabled'])
        self.assertFalse(run('disable')['savedEnabled'])
        self.assertFalse(run('read')['enabled'])
