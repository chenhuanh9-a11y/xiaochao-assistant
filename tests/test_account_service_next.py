"""Account transport/session regression checks: fake APIs and temporary vaults only."""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import io
import json
import os
from pathlib import Path
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch
from urllib.error import HTTPError

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "app"))
import account_service as account


def tokens(access="synthetic-access", refresh="synthetic-refresh", seconds=600):
    stamp = lambda delta: datetime.fromtimestamp(time.time() + delta, timezone.utc).isoformat()
    return dict(access_token=access, refresh_token=refresh,
                access_expires_at=stamp(seconds), refresh_expires_at=stamp(86400))


class AccountTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="qt-account-test-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.vault = account.SessionVault(self.root / "session.bin",
            protect=lambda data: b"fixture-encrypted:" + data[::-1],
            unprotect=lambda data: data[len(b"fixture-encrypted:"):][::-1])
        self.calls = []
        def api(method, path, body=None, **kwargs):
            self.calls.append((method, path, body, kwargs))
            return tokens() if path in ("/v1/auth/login", "/v1/auth/refresh") else {"ok": True}
        self.api = api
        self.service = account.AccountService(self.root, vault=self.vault, api=api)

    def test_login_storage_contains_refresh_only_and_restore_rotates(self):
        self.service.login(" Test_User ", "synthetic-password")
        self.assertEqual(self.service.username, "test_user")
        self.assertTrue(self.service.snapshot()["loggedIn"])
        encrypted = self.vault.path.read_bytes()
        self.assertNotIn(b"synthetic-password", encrypted)
        self.assertNotIn(b"synthetic-refresh", encrypted)
        payload = json.loads(self.vault._unprotect(encrypted))
        self.assertEqual(set(payload), {"username", "refreshToken", "refreshExpires"})
        fresh = account.AccountService(self.root, vault=self.vault,
            api=lambda *args, **kw: tokens("next-access", "next-refresh"))
        self.assertFalse(fresh.snapshot()["loggedIn"])
        self.assertTrue(fresh.snapshot()["canRestore"])
        revision = fresh.revision
        self.assertEqual(fresh.restore(), "test_user")
        self.assertEqual(fresh.revision, revision)
        self.assertEqual(self.vault.load().refresh_token, "next-refresh")
        self.assertNotIn("next-refresh", repr(self.vault.load()))

    def test_invalid_input_never_calls_api(self):
        for identifier, password in [("", "123456"), ("ab", "123456"), ("valid", "short")]:
            with self.assertRaises(account.AccountError):
                self.service.login(identifier, password)
        self.assertEqual(self.calls, [])

    def test_transient_restore_keeps_saved_session_and_expired_restore_removes_it(self):
        self.service.login("tester", "synthetic-password")
        for status in (0, 401):
            def fail(*args, **kw):
                raise account.AccountError("synthetic failure", status)
            service = account.AccountService(self.root, vault=self.vault, api=fail)
            with self.assertRaises(account.AccountError):
                service.restore()
            self.assertEqual(service.snapshot()["canRestore"], status == 0)
            self.assertEqual(self.vault.path.exists(), status == 0)

    def test_logout_invalidates_login_before_network_finishes(self):
        entered, release = threading.Event(), threading.Event()
        def blocked(*args, **kw):
            entered.set()
            self.assertTrue(release.wait(3))
            return tokens()
        self.service._api = blocked
        work = self.service.prepare_login("tester", "synthetic-password")
        with ThreadPoolExecutor(1) as pool:
            future = pool.submit(work)
            try:
                self.assertTrue(entered.wait(2))
                start = time.monotonic()
                revoke = self.service.prepare_logout()
                self.assertLess(time.monotonic() - start, .25)
                self.assertIsNone(self.service.username)
            finally:
                release.set()
            with self.assertRaisesRegex(account.AccountError, "取消"):
                future.result(2)
            self.assertTrue(revoke())
        self.assertFalse(self.vault.path.exists())

    def test_logout_during_refresh_is_nonblocking_and_late_result_cannot_save(self):
        self.service.login("tester", "synthetic-password")
        stale = self.vault.load()
        self.service._session = stale
        entered, release = threading.Event(), threading.Event()
        def blocked(*args, **kw):
            entered.set()
            self.assertTrue(release.wait(3))
            return tokens("new-access", "new-refresh")
        self.service._api = blocked
        with ThreadPoolExecutor(1) as pool:
            future = pool.submit(self.service.restore)
            try:
                self.assertTrue(entered.wait(2))
                start = time.monotonic()
                self.service.prepare_logout()
                self.assertLess(time.monotonic() - start, .25)
            finally:
                release.set()
            with self.assertRaises(account.AccountError):
                future.result(2)
        self.assertIsNone(self.service.username)
        self.assertFalse(self.vault.path.exists())

    def test_concurrent_refresh_rotates_once_without_changing_identity(self):
        self.service.login("tester", "synthetic-password")
        self.service._session = self.vault.load()
        revision = self.service.revision
        entered, release = threading.Event(), threading.Event()
        refreshes = []
        def api(method, path, body=None, **kw):
            if path == "/v1/auth/refresh":
                refreshes.append(body["refresh_token"])
                entered.set()
                self.assertTrue(release.wait(3))
                return tokens("new-access", "new-refresh")
            return kw["token"]
        self.service._api = api
        with ThreadPoolExecutor(2) as pool:
            first = pool.submit(self.service.authorized, "GET", "/v1/fixture")
            try:
                self.assertTrue(entered.wait(2))
                second = pool.submit(self.service.authorized, "GET", "/v1/fixture")
            finally:
                release.set()
            self.assertEqual(first.result(2), "new-access")
            self.assertEqual(second.result(2), "new-access")
        self.assertEqual(refreshes, ["synthetic-refresh"])
        self.assertEqual(self.service.revision, revision)

    def test_authorized_late_response_and_old_failure_cannot_affect_new_login(self):
        self.service.login("tester", "synthetic-password")
        for fail in (False, True):
            entered, release = threading.Event(), threading.Event()
            def api(method, path, body=None, **kw):
                if path == "/v1/auth/login":
                    return tokens()
                entered.set()
                self.assertTrue(release.wait(3))
                if fail:
                    raise account.AccountError("blocked", 403, "ACCOUNT_BLOCKED")
                return {"private": "old account"}
            self.service._api = api
            with ThreadPoolExecutor(1) as pool:
                future = pool.submit(self.service.authorized, "GET", "/v1/fixture")
                try:
                    self.assertTrue(entered.wait(2))
                    self.service.login("new_user", "synthetic-password")
                finally:
                    release.set()
                with self.assertRaisesRegex(account.AccountError, "取消"):
                    future.result(2)
            self.assertEqual(self.service.username, "new_user")

    def test_second_unauthorized_response_invalidates_session(self):
        self.service.login("tester", "synthetic-password")
        calls = []
        def api(method, path, body=None, **kw):
            calls.append(path)
            if path == "/v1/auth/refresh":
                return tokens()
            raise account.AccountError("unauthorized", 401)
        self.service._api = api
        with self.assertRaises(account.AccountError):
            self.service.authorized("POST", "/v1/fixture", {})
        self.assertEqual(calls, ["/v1/fixture", "/v1/auth/refresh", "/v1/fixture"])
        self.assertIsNone(self.service.username)

    def test_cancel_queued_login_and_close_do_not_resurrect(self):
        work = self.service.prepare_login("tester", "synthetic-password")
        self.service.cancel_pending()
        with self.assertRaises(account.AccountError):
            work()
        self.assertEqual(self.calls, [])
        self.service.login("tester", "synthetic-password")
        saved = self.vault.path.read_bytes()
        self.service.close()
        self.assertEqual(self.vault.path.read_bytes(), saved)
        for work in (self.service.restore, lambda: self.service.authorized("GET", "/v1/fixture")):
            with self.assertRaises(account.AccountError):
                work()

    def test_failed_protection_never_falls_back_to_plaintext(self):
        def fail(data):
            raise OSError("synthetic DPAPI failure")
        self.vault._protect = fail
        self.service.login("tester", "synthetic-password")
        self.assertTrue(self.service.snapshot()["loggedIn"])
        self.assertFalse(self.service.snapshot()["sessionSaved"])
        self.assertFalse(self.vault.path.exists())
        self.assertFalse(self.vault.path.with_suffix(".tmp").exists())

    def test_logout_reports_unremovable_vault_and_can_retry_local_cleanup(self):
        self.service.login("tester", "synthetic-password")
        with patch.object(Path, "unlink", side_effect=PermissionError("fixture")), \
                patch.object(Path, "write_bytes", side_effect=PermissionError("fixture")):
            self.service.prepare_logout()
        self.assertFalse(self.service.snapshot()["loggedIn"])
        self.assertIn("重试退出", self.service.snapshot()["storageWarning"])
        self.assertTrue(self.vault.path.exists())
        self.service.prepare_logout()
        self.assertEqual(self.service.snapshot()["storageWarning"], "")
        self.assertFalse(self.vault.path.exists())

    @unittest.skipUnless(sys.platform == "win32", "Windows DPAPI only")
    def test_actual_dpapi_with_synthetic_refresh_in_temporary_vault(self):
        vault = account.SessionVault(self.root / "dpapi.bin")
        session = account.Session.from_tokens("fixture", tokens())
        self.assertTrue(vault.save(session))
        self.assertNotIn(b"synthetic-refresh", vault.path.read_bytes())
        self.assertEqual(vault.load().refresh_token, session.refresh_token)
        vault.clear()
        self.assertIsNone(vault.load())

    def test_corrupt_and_nonfinite_vault_is_removed(self):
        for data in (b"corrupt", self.vault._protect(json.dumps(dict(username="fixture",
                refreshToken="synthetic", refreshExpires=float("nan"))).encode())):
            self.vault.path.write_bytes(data)
            self.assertIsNone(self.vault.load())
            self.assertFalse(self.vault.path.exists())

    def test_transport_rejects_redirect_and_does_not_echo_error_body(self):
        sensitive = b'{"detail":"synthetic-password synthetic-refresh","error_code":"ACCOUNT_BLOCKED"}'
        failure = HTTPError("https://fixture.invalid", 403, "Forbidden", {}, io.BytesIO(sensitive))
        with patch.object(account._OPENER, "open", side_effect=failure):
            with self.assertRaises(account.AccountError) as caught:
                account.call("POST", "/v1/auth/login", {"password": "synthetic-password"})
        self.assertNotIn("synthetic", str(caught.exception))
        self.assertEqual(caught.exception.code, "ACCOUNT_BLOCKED")
        self.assertIsNone(account._NoRedirect().redirect_request(None, None, 302, "", {}, "https://elsewhere.invalid"))
        for override in ("http://localhost:123@elsewhere.invalid", "https://elsewhere.invalid", "http://127.0.0.1:123/path"):
            with patch.dict(os.environ, {"XINGTU_ACCOUNT_API_URL": override}):
                with self.assertRaises(account.AccountError):
                    account.api_root()


if __name__ == "__main__":
    unittest.main()
