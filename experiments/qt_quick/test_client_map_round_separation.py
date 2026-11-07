"""Map edges never grant round authority. All game/input/pixel IO is synthetic."""
import threading
import unittest
from unittest.mock import Mock, patch

import test_client_live_runtime as live_fixture
import test_client_map_intent_reset as input_fixture
import test_client_chest_intent as chest_fixture
from client_input import NativeShortcutBackend


class RuntimeSeparationTests(unittest.TestCase):
    setUp = live_fixture.LiveRuntimeContracts.setUp
    dispatch = live_fixture.LiveRuntimeContracts.dispatch

    def assert_idle(self, round_id=0):
        state = self.runtime.snapshot()
        for field in ('roundActive', 'roundPreparing', 'mapOpen', 'captureInProgress',
                      'mapRecoveryPending', 'displayAuthorized', 'mapLocked'):
            self.assertFalse(state.get(field), field)
        self.assertEqual(state['roundId'], round_id)
        self.assertEqual(self.capture.enable_count, 0)
        self.assertEqual(self.capture.count, 0)
        self.assertEqual(self.engine.rank_count, 0)
        self.assertIsNone(self.runtime._render)

    def test_idle_map_never_prepares_captures_or_changes_round_in_all_contexts(self):
        for season in ('s0', 's1'):
            for mode in ('nightmare', 'difficult'):
                with self.subTest(season=season, mode=mode):
                    self.dispatch('set_context', season=season, mode=mode)
                    with patch.object(self.runtime, '_start', side_effect=AssertionError('map started round')), \
                         patch.object(self.runtime, '_get_engine', side_effect=AssertionError('idle map loaded resources')):
                        for payload in ({}, {'openIntent': True}, {'openIntent': False}):
                            result = self.dispatch('map', **payload)
                            self.assertTrue(result['success'], result)
                            self.assertEqual(result['reason'], 'round-not-started')
                    self.assert_idle()

    def test_idle_sequences_are_admitted_once_without_resources_or_authority(self):
        first = self.dispatch('map', inputSequence=1, openIntent=True)
        self.assertEqual(first['reason'], 'round-not-started')
        self.assertEqual(self.dispatch('map', inputSequence=1, openIntent=False)['reason'],
                         'map-input-sequence-expired')
        self.assertEqual(self.dispatch('map', inputSequence=True)['reason'], 'map-input-sequence-expired')
        self.assertEqual(self.dispatch('map', inputSequence=2, openIntent='yes')['reason'], 'map-input-intent-invalid')
        cancelled = threading.Event(); cancelled.set()
        self.assertTrue(self.runtime.dispatch('map', {}, cancelled)['stale'])
        self.assert_idle()

    def test_explicit_prepare_map_does_not_call_start_and_reopens_same_round(self):
        for mode in ('nightmare', 'difficult'):
            with self.subTest(mode=mode):
                self.dispatch('end_round')
                self.dispatch('set_context', mode=mode)
                prepared = self.dispatch('start_round')
                self.assertTrue(prepared['success'], prepared)
                round_id = self.runtime.snapshot()['roundId']
                with patch.object(self.runtime, '_start', side_effect=AssertionError('map called start')):
                    opened = self.dispatch('map')
                    self.assertTrue(opened['success'], opened)
                    sheet = self.runtime.reference_payload()
                    self.assertIsNotNone(sheet)
                    self.assertTrue(self.dispatch('close_map')['success'])
                    self.assertEqual(self.runtime.reference_payload()['token'], sheet['token'])
                    self.assertTrue(self.dispatch('map')['success'])
                self.assertEqual(self.runtime.snapshot()['roundId'], round_id)
                self.assertTrue(self.runtime.snapshot()['roundActive'])

    def test_end_cancel_stop_reset_and_toggle_require_fresh_explicit_start(self):
        for terminal in ('end_round', 'stop_round', 'reset_round', 'cancel', 'toggle_round'):
            with self.subTest(terminal=terminal):
                self.dispatch('start_round')
                round_id = self.runtime.snapshot()['roundId']
                self.dispatch(terminal)
                self.assertEqual(self.dispatch('map')['reason'], 'round-not-started')
                self.assert_idle(round_id)

    def test_failed_prepare_does_not_allow_map_to_retry_start(self):
        with patch.object(self.runtime, '_get_engine', side_effect=RuntimeError('fixture unavailable')):
            result = self.dispatch('start_round')
            self.assertFalse(result['success'])
        self.assertEqual(self.dispatch('map')['reason'], 'round-not-started')
        self.assert_idle()

    def test_activation_gate_rechecks_explicit_preparation(self):
        result = self.runtime._ensure_round_active({}, None, allow_prepare=False)
        self.assertFalse(result['success'])
        self.assertEqual(result['reason'], 'round-not-started')
        self.assert_idle()

    def test_explicit_capture_now_keeps_existing_start_contract(self):
        result = self.dispatch('capture_now')
        self.assertTrue(result['success'], result)
        self.assertTrue(self.runtime.snapshot()['roundActive'])
        self.assertGreater(self.capture.count, 0)


class InputSeparationTests(unittest.TestCase):
    chain = input_fixture.MapIntentResetTests.chain
    press = input_fixture.MapIntentResetTests.press

    def test_idle_ordered_map_and_escape_do_not_dispatch_or_clear_preview(self):
        for mode in ('nightmare', 'difficult'):
            with self.subTest(mode=mode), self.chain(mode, prepared=False) as (o, live, keys, events, actions):
                notices = []
                o.coordinator.notificationRequested.connect(lambda *args: notices.append(args))
                o.coordinator._reference = {'token': 'independent-preview'}
                o.images.clear.reset_mock()
                for _ in range(3):
                    self.press(keys)
                    keys._dispatch_action('escape', dict(createdAt=1.0))
                self.assertEqual(actions, [])
                self.assertEqual(live.capture.count, 0)
                self.assertFalse(live.runtime.snapshot()['roundActive'])
                self.assertEqual(live.runtime.snapshot()['roundId'], 0)
                self.assertEqual(o.coordinator._reference['token'], 'independent-preview')
                o.images.clear.assert_not_called()
                self.assertFalse(keys._map_input.open_intent)
                self.assertEqual(len(notices), 1)
                self.assertEqual(notices[0][0], '本局尚未开始')
                self.assertIn('开始本局', notices[0][1])
                live.dispatch('start_round')
                o.session.snapshot = live.runtime.snapshot()
                self.press(keys)
                self.assertTrue(live.runtime.snapshot()['roundActive'])
                self.assertTrue(live.runtime.snapshot()['mapOpen'])
                self.assertTrue(actions[-1][1]['openIntent'])

    def test_pending_start_or_end_is_not_superseded_by_map_or_escape(self):
        for pending in ('start_round', 'prepare_manual_alignment', 'end_round', 'toggle_round'):
            with self.subTest(pending=pending), self.chain('nightmare', prepared=False) as (o, live, keys, events, actions):
                o.session.busy = True
                o.session.snapshot.update(pendingAction=pending)
                o.session.cancelPending.reset_mock()
                self.press(keys)
                keys._dispatch_action('escape', dict(createdAt=1.0))
                self.assertEqual(actions, [])
                o.session.cancelPending.assert_not_called()
                self.assertFalse(keys._map_input.open_intent)

    def test_idle_resource_failure_does_not_replace_start_hint(self):
        with self.chain('nightmare', prepared=False) as (o, live, keys, events, actions):
            notices = []
            o.coordinator.notificationRequested.connect(lambda *args: notices.append(args))
            o.resources.busy = True
            o.session.snapshot.update(resourceLocked=True, liveEngineAvailable=False,
                                      liveUnavailableReason='missing-assets')
            self.press(keys)
            self.assertEqual(actions, [])
            self.assertEqual(notices[0][0], '本局尚未开始')
            self.assertFalse(any(n[0] == '地图资源未就绪' for n in notices))

    def test_legacy_map_cannot_start_or_end_round_and_start_action_is_retained(self):
        with self.chain('nightmare', prepared=False) as (o, live, keys, events, actions):
            c = o.coordinator
            c._native_action('map')
            c._native_action('escape')
            o.session.dispatch.assert_not_called()
            self.assertEqual(actions, [])
            c.startRound = Mock()
            c._native_action('start-round')
            c.startRound.assert_called_once()
            c.startRound.reset_mock()
            c._native_action('toggle-round')
            c.startRound.assert_called_once()

    def test_map_and_start_bindings_stay_distinct(self):
        keys = NativeShortcutBackend(Mock())
        self.addCleanup(keys.shutdown)
        self.assertFalse(keys.configure({'map': 'G', 'start-round': 'G'}))
        self.assertEqual(keys.snapshot['status'], 'conflict')
        self.assertFalse(keys.configure({'map': 'F10', 'toggle-round': 'F10'}))
        self.assertTrue(keys.configure({'map': 'G', 'start-round': 'F10'}))


class ChestSeparationTests(unittest.TestCase):
    setUp = chest_fixture.IntentTests.setUp
    restart = chest_fixture.IntentTests.restart
    enable = chest_fixture.IntentTests.enable
    attach_round_runtime = chest_fixture.IntentTests.attach_round_runtime

    def test_idle_map_is_not_a_new_round_intent_even_if_stale_worker_claims_new_round(self):
        runtime, session, until = self.attach_round_runtime()
        self.enable()
        session.dispatch('end_round'); until(lambda: not session._jobs)
        self.assertTrue(self.adapter.snapshot['waitingForRound'])
        # This adversarial fixture still implements the old implicit map start.
        # Neither its new ID nor a successful map receipt may authorize input.
        session.dispatch('map'); until(lambda: not session._jobs)
        self.assertTrue(self.adapter.snapshot['waitingForRound'])
        self.assertFalse(self.adapter.snapshot['running'])
        self.assertIsNone(self.adapter._next_round_generation)
