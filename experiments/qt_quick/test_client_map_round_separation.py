"""Map edges never grant round authority. All game/input/pixel IO is synthetic."""
from contextlib import contextmanager
import copy
import threading
import numpy as np
import unittest
from unittest.mock import Mock, patch

import test_client_live_runtime as live_fixture
import test_client_map_intent_reset as input_fixture
import test_client_chest_intent as chest_fixture
import test_client_resource_feedback as resource_fixture
from client_input import NativeShortcutBackend, ACTION_ORDER


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

    def test_binding_message_distinguishes_idle_from_explicit_preparation(self):
        for mode in ('nightmare', 'difficult'):
            with self.subTest(mode=mode), self.chain(mode, prepared=False) as (o, live, keys, events, actions):
                for prepared in (False, True):
                    if prepared:
                        live.dispatch('start_round')
                    o.session.snapshot = dict(live.runtime.snapshot(),
                        lastReceipt=dict(action='bind_capture', success=True))
                    o.coordinator._session_changed()
                    if prepared:
                        self.assertIn('本局已准备', o.coordinator.message)
                        self.assertNotIn('请先点击', o.coordinator.message)
                    else:
                        self.assertIn('请先点击“开始本局”', o.coordinator.message)
                    self.assertEqual(live.capture.count, 0)

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


class PhysicalMapPhaseTests(unittest.TestCase):
    """Independent game-open oracle; real input/coordinator/session/runtime chain.

    Only native key/window IO, map/floor detectors and fit pixels are fixtures.
    The listener is already enabled, and startRound/setInputEnabled are real.
    """
    chain = resource_fixture.ResourceFeedbackTests.chain
    press = resource_fixture.ResourceFeedbackTests.press
    drain = resource_fixture.ResourceFeedbackTests.drain

    @contextmanager
    def physical_game(self, mode):
        with self.chain(mode) as row:
            o, c, s, r, f, k, notices, actions, root = row
            f._build = lambda mode, *, season='s0': live_fixture.fixture_engine(mode)
            f.prepare(); s.refreshRuntime()
            # Exercise the actual enabled-listener start path. A fixture that
            # mocks setInputEnabled would miss its configure/disable phase reset.
            o.native.is_window = Mock(return_value=True)
            o.native.key_state = Mock(return_value=0)
            o.settings.persistedValues['actionHotkeys'] = [''] * len(ACTION_ORDER)
            o.settings.values = copy.deepcopy(o.settings.persistedValues)
            self.assertTrue(k.configureSettings(o.settings.persistedValues))
            self.assertTrue(k.enable()); k._timer.stop()
            game = {'open': False}
            def g():
                game['open'] = not game['open']
                self.press(k)
            def escape():
                game['open'] = False
                k._dispatch_action('escape', dict(createdAt=1.0))
            def floor_read(*args, **kwargs):
                return live_fixture.synthetic_read(mode) if game['open'] else dict(
                    selectedFloor=None, controlAxis='vertical', observedOrder=[],
                    centerPoints=[], labelEvidence=[])
            fit = dict(success=True,
                matrixSourceToClient=np.array([[.5, 0, 40], [0, .5, 60], [0, 0, 1]]),
                selectedRouteCurrentFrameResidual=dict(verified=True,
                    poseVerifiedAgainstCanonicalGeometry=True, profileRenderDerivedFromCanonical=True,
                    coordinatePackId='fixture-pack', validationBasis='shared-canonical-geometry-current-frame'))
            with patch('map_interface_observation.detect_map_interface',
                       side_effect=lambda *a, **kw: dict(detected=game['open'])), \
                 patch('floor_control_region.locate_vertical_floor_controls', side_effect=floor_read), \
                 patch.object(live_fixture._AlignmentHost, 'fit', side_effect=lambda *a, **kw: copy.deepcopy(fit)):
                yield row, game, g, escape

    def assert_closed_without_capture(self, row, game, before):
        o, c, s, r, f, k, notices, actions, root = row
        self.drain(o, s)
        self.assertFalse(game['open'])
        self.assertFalse(k._map_input.open_intent)
        self.assertFalse(r.snapshot()['mapOpen'])
        self.assertEqual(r._capture.count, before)
        self.assertFalse(r.snapshot()['mapRecoveryPending'])

    def assert_open_with_current_fit(self, row, game, before):
        o, c, s, r, f, k, notices, actions, root = row
        self.drain(o, s)
        self.assertTrue(game['open'])
        self.assertTrue(k._map_input.open_intent)
        self.assertTrue(r.snapshot()['mapOpen'])
        self.assertTrue(r.snapshot()['roundActive'])
        self.assertGreater(r._capture.count, before)
        self.assertTrue(r.snapshot()['displayAuthorized'])
        self.assertIsNotNone(r.render_payload(r.snapshot()['pendingRenderToken']))

    def test_idle_g_then_explicit_start_keeps_listener_and_physical_phase(self):
        for mode in ('nightmare', 'difficult'):
            with self.subTest(mode=mode), self.physical_game(mode) as (row, game, g, escape):
                o, c, s, r, f, k, notices, actions, root = row
                g()
                self.assertTrue(game['open']); self.assertTrue(k._map_input.open_intent)
                self.assertEqual(actions, []); self.assertEqual(r._capture.count, 0)
                with patch.object(k, 'configureSettings', wraps=k.configureSettings) as configure, \
                     patch.object(k, 'disable', wraps=k.disable) as disable:
                    c.startRound(); self.drain(o, s)
                configure.assert_not_called(); disable.assert_not_called()
                self.assertTrue(k.enabled); self.assertTrue(k._map_input.open_intent)
                round_id = r.snapshot()['roundId']
                for cycle in range(2):
                    before = r._capture.count
                    g(); self.assert_closed_without_capture(row, game, before)
                    g(); self.assert_open_with_current_fit(row, game, before)
                    self.assertEqual(r.snapshot()['roundId'], round_id)
                self.assertEqual([name for name, _ in actions],
                                 ['start_round', 'close_map', 'map', 'close_map', 'map'])

    def test_start_keeps_verified_map_phase_on_enabled_listener(self):
        # Isolate the second seam from ignored G: a previous verified visual
        # observation already established the current physical open state.
        for mode in ('nightmare', 'difficult'):
            with self.subTest(mode=mode), self.physical_game(mode) as (row, game, g, escape):
                o, c, s, r, f, k, notices, actions, root = row
                game['open'] = True
                self.assertTrue(k.observeMapState(True, k._map_input.sequence))
                with patch.object(k, 'configureSettings', wraps=k.configureSettings) as configure, \
                     patch.object(k, 'disable', wraps=k.disable) as disable:
                    c.startRound(); self.drain(o, s)
                configure.assert_not_called(); disable.assert_not_called()
                self.assertTrue(k.enabled); self.assertTrue(k._map_input.open_intent)
                self.assertEqual(r._capture.count, 0)
                g(); self.assert_closed_without_capture(row, game, 0)
                g(); self.assert_open_with_current_fit(row, game, 0)

    def test_pending_prepare_g_then_close_reopen_preserves_permission_and_phase(self):
        for mode in ('nightmare', 'difficult'):
            with self.subTest(mode=mode), self.physical_game(mode) as (row, game, g, escape):
                o, c, s, r, f, k, notices, actions, root = row
                entered, release = threading.Event(), threading.Event()
                original = r._get_engine
                def blocked(*args, **kwargs):
                    entered.set(); self.assertTrue(release.wait(5))
                    return original(*args, **kwargs)
                with patch.object(r, '_get_engine', side_effect=blocked):
                    c.startRound(); self.assertTrue(entered.wait(2))
                    work = s._jobs[s._pending][0]
                    try:
                        g()
                        self.assertTrue(game['open']); self.assertTrue(k._map_input.open_intent)
                        self.assertFalse(work.cancelled.is_set())
                        self.assertEqual([name for name, _ in actions], ['start_round'])
                        self.assertEqual(r._capture.count, 0)
                    finally:
                        release.set(); self.drain(o, s)
                round_id = r.snapshot()['roundId']
                self.assertTrue(r.snapshot()['roundPreparing'])
                self.assertFalse(r.snapshot()['roundActive'])
                g(); self.assert_closed_without_capture(row, game, 0)
                self.assertTrue(r.snapshot()['roundPreparing'])
                g(); self.assert_open_with_current_fit(row, game, 0)
                self.assertEqual(r.snapshot()['roundId'], round_id)
                self.assertEqual([name for name, _ in actions], ['start_round', 'close_map', 'map'])

    def test_multiple_pending_edges_and_escape_preserve_actual_last_phase(self):
        for mode in ('nightmare', 'difficult'):
            for closing in ('g', 'escape'):
                with self.subTest(mode=mode, closing=closing), self.physical_game(mode) as (row, game, g, escape):
                    o, c, s, r, f, k, notices, actions, root = row
                    entered, release = threading.Event(), threading.Event()
                    original = r._get_engine
                    def blocked(*args, **kwargs):
                        entered.set(); self.assertTrue(release.wait(5))
                        return original(*args, **kwargs)
                    with patch.object(r, '_get_engine', side_effect=blocked):
                        c.startRound(); self.assertTrue(entered.wait(2))
                        work = s._jobs[s._pending][0]
                        try:
                            g()
                            (g if closing == 'g' else escape)()
                            if closing == 'escape':
                                escape()
                            self.assertFalse(game['open']); self.assertFalse(k._map_input.open_intent)
                            self.assertFalse(work.cancelled.is_set())
                            self.assertEqual(r._capture.count, 0)
                            self.assertEqual([name for name, _ in actions], ['start_round'])
                        finally:
                            release.set(); self.drain(o, s)
                    g(); self.assert_open_with_current_fit(row, game, 0)
                    before = r._capture.count
                    escape(); self.assert_closed_without_capture(row, game, before)
                    escape(); self.assert_closed_without_capture(row, game, before)
                    g(); self.assert_open_with_current_fit(row, game, before)
