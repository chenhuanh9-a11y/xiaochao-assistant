"""Context failures use real queued Session signals and complete QtClient/QML lifecycle."""
import unittest
import test_client_session as session_tests
from test_client_session import until
import test_client_season_preparation as preparation_tests


class ContextFailureTests(unittest.TestCase):
    setUp = session_tests.SessionTests.setUp
    tearDown = session_tests.SessionTests.tearDown

    def test_context_exception_restores_actual_and_allows_explicit_retry(self):
        self.runtime.fail = True
        self.assertTrue(self.session.reconfigure('difficult', 'other'))
        until(lambda: not self.session._jobs)
        self.assertEqual(self.session.currentMode, 'nightmare')
        self.assertFalse(self.session.snapshot.get('contextPending'))
        self.assertIn('fixture failure', self.session.message)
        self.assertTrue(self.session.snapshot['lastReceipt']['error'])
        self.runtime.fail = False
        self.assertTrue(self.session.reconfigure('difficult', 'other'))
        until(lambda: not self.session._jobs)
        self.assertEqual(self.session.appliedMode, 'difficult')

    def test_context_error_with_unreadable_snapshot_fails_closed(self):
        self.runtime.state.update(render={'liveAuthorization': True, 'token': 'old-render'},
                                  displayAuthorized=True, identityPhase='MEMBER_VERIFIED',
                                  reference={'key': 'old-reference'})
        self.session.refreshRuntime()
        original = self.runtime.snapshot
        def dispatch(action, payload, cancelled):
            def unavailable():
                raise RuntimeError('snapshot unavailable')
            self.runtime.snapshot = unavailable
            raise RuntimeError('original dispatch failure')
        self.runtime.dispatch = dispatch
        self.session.reconfigure('difficult', 'other')
        until(lambda: not self.session._jobs)
        self.assertEqual(self.session.currentMode, 'nightmare')
        self.assertFalse(self.session.snapshot.get('contextPending'))
        self.assertFalse(self.session.engineConnected)
        self.assertFalse(self.session.snapshot['liveEngineAvailable'])
        self.assertFalse(self.session.snapshot['liveAuthorization'])
        self.assertEqual(self.session.snapshot['availableCommands'], [])
        self.assertNotIn('render', self.session.snapshot)
        self.assertNotIn('reference', self.session.snapshot)
        self.assertNotIn('identityPhase', self.session.snapshot)
        self.assertFalse(self.session.snapshot.get('displayAuthorized'))
        self.assertIn('original dispatch failure', self.session.message)
        self.runtime.snapshot = original

    def test_superseding_action_rechecks_permissions_after_snapshot_failure(self):
        self.runtime.release.clear()
        self.runtime.fail = True
        self.session.reconfigure('difficult', 'other')
        until(self.runtime.started.is_set)
        original = self.runtime.snapshot
        def unavailable():
            raise RuntimeError('snapshot unavailable')
        self.runtime.snapshot = unavailable
        self.session.dispatch('capture_now')
        self.assertFalse(self.session.busy)
        self.assertEqual(self.session.snapshot['availableCommands'], [])
        self.assertIn('Unavailable command after context cancellation', self.session.message)
        self.runtime.release.set()
        until(lambda: not self.session._jobs)
        self.assertEqual([call[0] for call in self.runtime.calls], ['set_context'])
        self.runtime.snapshot = original

    def test_context_rejection_preserves_reason_and_actual_state(self):
        self.runtime.dispatch = lambda action, payload, cancelled: dict(
            action=action, requestContext=dict(payload), success=False,
            reason='controlled-context-rejection', message='controlled-context-rejection')
        self.session.reconfigure('difficult', 'other')
        until(lambda: not self.session._jobs)
        self.assertEqual(self.session.currentMode, 'nightmare')
        self.assertFalse(self.session.snapshot.get('contextPending'))
        self.assertEqual(self.session.message, 'controlled-context-rejection')
        self.assertFalse(self.session.snapshot['lastReceipt']['success'])

    def test_reconfigure_and_refresh_end_only_the_cancelled_attempt(self):
        self.runtime.release.clear()
        finished = []
        self.session.contextFinished.connect(finished.append)
        self.session.reconfigure('difficult', 'old')
        until(self.runtime.started.is_set)
        old_job = self.session._pending
        self.session.reconfigure('difficult', 'old')
        self.assertEqual(self.session._pending, old_job)
        self.assertEqual(finished, [])
        self.session.reconfigure('nightmare', 'new')
        self.assertEqual(finished, [('difficult', 'old', 's0')])
        self.session.refreshRuntime()
        self.assertEqual(finished[-1], ('nightmare', 'new', 's0'))
        self.assertFalse(self.session.snapshot.get('contextPending'))
        self.runtime.release.set()
        until(lambda: not self.session._jobs)
        self.assertEqual(len(finished), 2)  # Late cancelled workers cannot finish twice.

    def test_cancel_pending_restores_actual_without_erasing_newer_success(self):
        self.runtime.release.clear()
        self.runtime.fail = True
        self.session.reconfigure('difficult', 'cancelled')
        until(self.runtime.started.is_set)
        self.session.cancelPending('test-cancel-context')
        self.assertEqual(self.session.currentMode, 'nightmare')
        self.assertFalse(self.session.snapshot.get('contextPending'))
        self.runtime.release.set()
        until(lambda: not self.session._jobs)
        self.assertEqual(self.session.currentMode, 'nightmare')
        self.assertEqual(self.session.snapshot['lastReceipt'], {})

    def test_cancel_restores_actual_and_late_exception_cannot_replace_new_context(self):
        import threading
        entered, release = threading.Event(), threading.Event()
        original = self.runtime.dispatch
        def dispatch(action, payload, cancelled):
            if payload.get('profileId') == 'old':
                entered.set()
                release.wait(2)
                raise RuntimeError('old-context-error')
            return original(action, payload, cancelled)
        self.runtime.dispatch = dispatch
        try:
            self.session.reconfigure('difficult', 'old')
            until(entered.is_set)
            self.session.stop()
            self.assertEqual(self.session.currentMode, 'nightmare')
            self.assertFalse(self.session.snapshot.get('contextPending'))
            self.session.reconfigure('difficult', 'new')
            release.set()
            until(lambda: not self.session._jobs)
            self.assertEqual(self.session.appliedProfile, 'new')
            self.assertNotIn('old-context-error', self.session.message)
            self.assertFalse(self.session.snapshot.get('contextPending'))
        finally:
            release.set()


class ClientContextFailureTests(unittest.TestCase):
    run_scenario = preparation_tests.SeasonPreparationTests.run_scenario

    def test_real_client_terminal_hotkey_supersedes_context_without_stuck_pending(self):
        self.run_scenario('context-superseded')

    def test_real_client_home_failure_and_same_saved_target_retry(self):
        self.run_scenario('context-error')


if __name__ == '__main__':
    unittest.main()
