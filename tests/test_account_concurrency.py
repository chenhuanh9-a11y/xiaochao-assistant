"""Account races use gated transports only: no network, credentials or DPAPI."""
from datetime import datetime, timedelta, timezone
from pathlib import Path
import sys
import tempfile
import threading
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'app'))
from account_service import AccountError, AccountService, Session, SessionVault


def tokens(name='new'):
    return dict(access_token=name, refresh_token=name + '-refresh',
                access_expires_at=(datetime.now(timezone.utc) + timedelta(hours=1)).isoformat(),
                refresh_expires_at=(datetime.now(timezone.utc) + timedelta(days=1)).isoformat(),
                recovery_code='ABCD-EFGH-JKMP-QRST')


class AccountConcurrencyTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.entered, self.release = threading.Event(), threading.Event()
        self.workers = []
        self.calls = []
        self.failure = None
        self.gated_path = '/v1/auth/refresh'
        def api(method, path, body=None, **kwargs):
            self.calls.append((path, body, kwargs))
            if path == self.gated_path:
                self.entered.set()
                if not self.release.wait(3):
                    raise AssertionError('test transport not released')
                if self.failure:
                    raise self.failure
            return tokens((body or {}).get('username', 'refreshed'))
        self.account = AccountService(Path(self.folder.name), api=api,
                                     vault=SessionVault(Path(self.folder.name) / 'session'))
        self.account._set(Session('alice', 'old', 'old-refresh', 0, 9999999999))
        self.addCleanup(self.drain)

    def drain(self):
        self.release.set()
        for thread, _, _ in self.workers:
            thread.join(4)
            self.assertFalse(thread.is_alive())

    def start(self, function):
        result, done = [], threading.Event()
        def run():
            try:
                result.append(function())
            except Exception as exc:
                result.append(exc)
            finally:
                done.set()
        thread = threading.Thread(target=run)
        self.workers.append((thread, result, done))
        thread.start()
        return result, done

    def refresh(self):
        output, done = self.start(lambda: self.account.authorized('GET', '/protected'))
        self.assertTrue(self.entered.wait(1))
        return output, done

    def test_username_read_does_not_wait_for_refresh_http(self):
        self.refresh()
        output, done = self.start(lambda: self.account.username)
        self.assertTrue(done.wait(.3), 'UI snapshot blocked behind HTTP')
        self.assertEqual(output, ['alice'])

    def test_refresh_single_flight(self):
        self.refresh()
        second, done = self.start(lambda: self.account.authorized('GET', '/protected'))
        self.release.set()
        self.assertTrue(done.wait(1))
        self.drain()
        self.assertEqual(sum(path == '/v1/auth/refresh' for path, _, _ in self.calls), 1)
        self.assertFalse(any(isinstance(x, Exception) for x in second))

    def test_old_refresh_cannot_replace_new_login(self):
        old, _ = self.refresh()
        login, done = self.start(lambda: self.account.login('bob', 'secret1'))
        self.assertTrue(done.wait(.3), 'new login held by refresh HTTP')
        self.release.set()
        self.drain()
        self.assertEqual(self.account.username, 'bob')
        self.assertIsInstance(old[0], AccountError)
        self.assertFalse(any(path == '/protected' for path, _, _ in self.calls))

    def test_old_refresh_error_cannot_clear_new_login(self):
        self.failure = AccountError('expired', 401)
        self.test_old_refresh_cannot_replace_new_login()

    def test_logout_during_refresh_is_immediate_and_stays_logged_out(self):
        self.refresh()
        _, done = self.start(self.account.logout)
        read, read_done = self.start(lambda: self.account.username)
        self.assertTrue(read_done.wait(.3), 'UI read waits for logout HTTP')
        self.assertEqual(read, [None])
        self.release.set()
        self.drain()
        self.assertIsNone(self.account.username)

    def test_late_login_does_not_restore_logged_out_session(self):
        self.gated_path = '/v1/auth/login'
        result, _ = self.start(lambda: self.account.login('bob', 'secret1'))
        self.assertTrue(self.entered.wait(1))
        self.account.logout()
        self.release.set()
        self.drain()
        self.assertIsNone(self.account.username)
        self.assertIsInstance(result[0], AccountError)

    def test_late_register_does_not_restore_session_or_recovery_code(self):
        self.gated_path = '/v1/auth/register'
        self.start(lambda: self.account.register('bob', 'secret1', 'bob@example.test'))
        self.assertTrue(self.entered.wait(1))
        self.account.logout()
        self.release.set()
        self.drain()
        self.assertIsNone(self.account.username)
        self.assertEqual(self.account.pending_recovery_code, '')

    def test_late_recovery_does_not_clear_new_login(self):
        self.gated_path = '/v1/auth/password-recovery'
        self.start(lambda: self.account.recover('alice', 'ABCD-EFGH-JKMP-QRST', 'secret2'))
        self.assertTrue(self.entered.wait(1))
        self.account.login('bob', 'secret1')
        self.release.set()
        self.drain()
        self.assertEqual(self.account.username, 'bob')

    def test_recovery_code_is_memory_only_until_acknowledged(self):
        code = self.account.register('bob', 'secret1', 'bob@example.test')
        self.assertEqual(self.account.pending_recovery_code, code)
        self.assertFalse(self.account.acknowledge_recovery_code('wrong'))
        self.assertEqual(self.account.pending_recovery_code, code)
        for file in Path(self.folder.name).iterdir():
            self.assertNotIn(code.encode(), file.read_bytes())
        self.assertTrue(self.account.acknowledge_recovery_code(code))
        self.assertEqual(self.account.pending_recovery_code, '')

    def test_refresh_preserves_pending_code_but_account_change_clears(self):
        code = self.account.register('bob', 'secret1', 'bob@example.test')
        self.account._session.access_expires = 0
        self.release.set()
        self.account.authorized('GET', '/protected')
        self.assertEqual(self.account.pending_recovery_code, code)
        self.account.login('alice', 'secret1')
        self.assertEqual(self.account.pending_recovery_code, '')

    def test_shutdown_revokes_inflight_register_and_clears_pending(self):
        self.gated_path = '/v1/auth/register'
        self.start(lambda: self.account.register('bob', 'secret1', 'bob@example.test'))
        self.assertTrue(self.entered.wait(1))
        self.account.shutdown()
        self.release.set()
        self.drain()
        self.assertEqual(self.account.pending_recovery_code, '')
        self.assertIsNone(self.account.username)
        with self.assertRaises(AccountError):
            self.account.login('bob', 'secret1')

    def test_late_blocked_authorized_response_cannot_clear_new_login(self):
        self.account._session.access_expires = 9999999999
        self.gated_path = '/protected'
        self.failure = AccountError('blocked', 403, 'ACCOUNT_BLOCKED')
        self.start(lambda: self.account.authorized('GET', '/protected'))
        self.assertTrue(self.entered.wait(1))
        self.account.login('bob', 'secret1')
        self.release.set()
        self.drain()
        self.assertEqual(self.account.username, 'bob')

    def test_refresh_failure_clears_current_session_and_pending_code(self):
        self.account.register('bob', 'secret1', 'bob@example.test')
        self.account._session.access_expires = 0
        self.failure = AccountError('expired', 401)
        self.release.set()
        with self.assertRaises(AccountError):
            self.account.authorized('GET', '/protected')
        self.assertIsNone(self.account.username)
        self.assertEqual(self.account.pending_recovery_code, '')

    def test_shutdown_during_refresh_cannot_restore_session(self):
        self.refresh()
        self.account.shutdown()
        self.account.shutdown()
        self.release.set()
        self.drain()
        self.assertIsNone(self.account.username)
        self.assertEqual(self.account.pending_recovery_code, '')

    def test_later_login_wins_even_if_earlier_login_returns_last(self):
        original = self.account._api
        def api(method, path, body=None, **kwargs):
            if path == '/v1/auth/login' and body['username'] == 'alice':
                self.entered.set()
                self.release.wait(3)
                return tokens('alice')
            return original(method, path, body, **kwargs)
        self.account._api = api
        first, _ = self.start(lambda: self.account.login('alice', 'secret1'))
        self.assertTrue(self.entered.wait(1))
        self.account.login('bob', 'secret1')
        self.release.set()
        self.drain()
        self.assertEqual(self.account.username, 'bob')
        self.assertIsInstance(first[0], AccountError)

    def test_refresh_only_logout_revokes_server_without_restoring_session(self):
        self.account._session.access_token = ''
        self.release.set()
        self.account.logout()
        self.assertIsNone(self.account.username)
        self.assertEqual([p for p, _, _ in self.calls], ['/v1/auth/refresh', '/v1/auth/logout'])
        self.assertEqual(self.calls[-1][2]['token'], 'refreshed')

    def test_logout_refresh_does_not_replace_concurrent_new_login(self):
        self.start(self.account.logout)
        self.assertTrue(self.entered.wait(1))
        self.assertIsNone(self.account.username)
        self.account.login('bob', 'secret1')
        self.release.set()
        self.drain()
        self.assertEqual(self.account.username, 'bob')
        logout = [kwargs for p, _, kwargs in self.calls if p == '/v1/auth/logout']
        self.assertEqual(logout, [dict(token='refreshed', installation=self.account.installation_id)])

    def test_logout_waits_off_ui_lock_and_uses_only_detached_session(self):
        self.refresh()
        self.start(self.account.logout)
        # Wait for local invalidation, not the blocked network worker.
        for _ in range(100):
            with self.account._lock:
                if self.account._session is None:
                    break
            threading.Event().wait(.005)
        self.assertIsNone(self.account.username)
        self.release.set()
        self.drain()
        self.assertIsNone(self.account.username)
        self.assertEqual([kw['token'] for p, _, kw in self.calls if p == '/v1/auth/logout'], ['refreshed'])

    def test_refresh_started_during_login_cannot_overwrite_committed_login(self):
        login_started, login_release = threading.Event(), threading.Event()
        original = self.account._api
        def api(method, path, body=None, **kwargs):
            if path == '/v1/auth/login':
                login_started.set()
                login_release.wait(3)
                return tokens('bob')
            return original(method, path, body, **kwargs)
        self.account._api = api
        self.addCleanup(login_release.set)
        _, login_done = self.start(lambda: self.account.login('bob', 'secret1'))
        self.assertTrue(login_started.wait(1))
        old, old_done = self.start(lambda: self.account.authorized('GET', '/protected'))
        self.assertTrue(old_done.wait(1))
        self.assertIsInstance(old[0], AccountError)
        self.assertFalse(self.entered.is_set())
        login_release.set()
        self.assertTrue(login_done.wait(1))
        self.release.set()
        self.drain()
        self.assertEqual(self.account.username, 'bob')
        self.assertIsInstance(old[0], AccountError)
        self.assertFalse(any(p == '/protected' for p, _, _ in self.calls))

    def test_authorized_result_started_during_login_is_rejected_after_commit(self):
        self.account._session.access_expires = 9999999999
        self.gated_path = '/protected'
        login_started, login_release = threading.Event(), threading.Event()
        original = self.account._api
        def api(method, path, body=None, **kwargs):
            if path == '/v1/auth/login':
                login_started.set()
                login_release.wait(3)
                return tokens('bob')
            return original(method, path, body, **kwargs)
        self.account._api = api
        self.addCleanup(login_release.set)
        _, login_done = self.start(lambda: self.account.login('bob', 'secret1'))
        self.assertTrue(login_started.wait(1))
        old, old_done = self.start(lambda: self.account.authorized('GET', '/protected'))
        self.assertTrue(old_done.wait(1))
        self.assertFalse(self.entered.is_set())
        login_release.set()
        self.assertTrue(login_done.wait(1))
        self.release.set()
        self.drain()
        self.assertEqual(self.account.username, 'bob')
        self.assertIsInstance(old[0], AccountError)

    def test_expired_old_session_cannot_cancel_inflight_login(self):
        login_started, login_release = threading.Event(), threading.Event()
        original = self.account._api
        def api(method, path, body=None, **kwargs):
            if path == '/v1/auth/login':
                login_started.set()
                login_release.wait(3)
                return tokens('bob')
            if path == '/v1/auth/refresh':
                raise AccountError('old session expired', 401)
            return original(method, path, body, **kwargs)
        self.account._api = api
        self.addCleanup(login_release.set)
        login, login_done = self.start(lambda: self.account.login('bob', 'secret1'))
        self.assertTrue(login_started.wait(1))
        with self.assertRaises(AccountError):
            self.account.authorized('GET', '/protected')
        login_release.set()
        self.assertTrue(login_done.wait(1))
        self.assertEqual(login, ['bob'])
        self.assertEqual(self.account.username, 'bob')

    def test_failed_login_releases_authorized_requests_for_original_session(self):
        original = self.account._api
        def api(method, path, body=None, **kwargs):
            if path == '/v1/auth/login':
                raise AccountError('bad login', 401)
            return original(method, path, body, **kwargs)
        self.account._api = api
        with self.assertRaises(AccountError):
            self.account.login('bob', 'secret1')
        self.release.set()
        self.account.authorized('GET', '/protected')
        self.assertEqual(self.account.username, 'alice')

    def test_older_auth_finally_cannot_release_newer_pending_auth(self):
        started = {name: threading.Event() for name in ('alice', 'bob')}
        release = {name: threading.Event() for name in ('alice', 'bob')}
        for event in release.values():
            self.addCleanup(event.set)
        def api(method, path, body=None, **kwargs):
            if path == '/v1/auth/login':
                name = body['username']
                started[name].set()
                release[name].wait(3)
                if name == 'alice':
                    raise AccountError('first login failed', 401)
                return tokens(name)
            raise AssertionError('must not authorize old session during new login')
        self.account._api = api
        _, first_done = self.start(lambda: self.account.login('alice', 'secret1'))
        self.assertTrue(started['alice'].wait(1))
        second, second_done = self.start(lambda: self.account.login('bob', 'secret1'))
        self.assertTrue(started['bob'].wait(1))
        release['alice'].set()
        self.assertTrue(first_done.wait(1))
        with self.assertRaises(AccountError):
            self.account.authorized('GET', '/protected')
        release['bob'].set()
        self.assertTrue(second_done.wait(1))
        self.assertEqual(second, ['bob'])


if __name__ == '__main__':
    unittest.main()
