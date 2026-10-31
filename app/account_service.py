"""Xingtu account: the same account service the Android app signs in to.

Accounts live on ``api.xingchenmap.cn`` (``/v1/auth/*``). Login returns an
opaque Bearer access token (15 minutes) and a rotating refresh token (30
days). Only the refresh token and the display name are kept between launches,
encrypted with Windows DPAPI for the current Windows user; on other systems
the session lasts until the app closes. Passwords are never stored.

Every call blocks; the UI runs them on a worker thread.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
import json
import math
import os
from pathlib import Path
import re
import sys
import threading
import time
from urllib import error, request
from urllib.parse import quote, urlsplit
import uuid

PRODUCTION_ROOT = "https://api.xingchenmap.cn"
USER_AGENT = "XingtuDesktop/account"
TIMEOUT_SECONDS = 20
MAX_RESPONSE_BYTES = 2 * 1024 * 1024
REFRESH_MARGIN_SECONDS = 30
USERNAME = re.compile(r"^[a-z0-9_]{3,32}$")
SESSION_FILE = "account-session.bin"
INSTALLATION_FILE = "account-installation-id.txt"
BLOCKED_CODES = {"ACCOUNT_DISABLED", "ACCOUNT_BLOCKED", "NETWORK_BLOCKED", "SESSION_INVALID"}
STATUS_MESSAGES = {401: "登录已失效，请重新登录", 403: "账号暂时不能使用", 404: "内容不存在或已下架",
                   409: "内容已变化，请刷新后再试", 413: "内容超过大小上限", 429: "操作太频繁，请稍后再试"}


class _NoRedirect(request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None  # Never resend a token elsewhere; the 3xx surfaces as an HTTPError.


_OPENER = request.build_opener(_NoRedirect)


class AccountError(Exception):
    def __init__(self, message: str, status: int = 0, code: str = ""):
        super().__init__(message)
        self.status = status
        self.code = code


def api_root() -> str:
    """Production unless a local test server is set explicitly."""
    override = os.environ.get("XINGTU_ACCOUNT_API_URL", "").rstrip("/")
    if not override:
        return PRODUCTION_ROOT
    try:
        parsed = urlsplit(override)
        valid = (parsed.scheme == "http" and parsed.hostname in ("127.0.0.1", "localhost")
                 and parsed.port is not None and not parsed.username and not parsed.password
                 and not parsed.path and not parsed.query and not parsed.fragment)
    except ValueError:
        valid = False
    if not valid:
        raise AccountError("XINGTU_ACCOUNT_API_URL 只能指向本机测试服务器")
    return override


def _error_message(status: int, body: bytes) -> tuple[str, str]:
    try:
        data = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        data = None
    code = str(data.get("error_code") or "") if isinstance(data, dict) else ""
    # Server errors can echo submitted credentials; expose only bounded codes.
    if not re.fullmatch(r"[A-Z0-9_]{0,80}", code):
        code = ""
    if status in (400, 422):
        return "填写的内容格式不正确", code
    return STATUS_MESSAGES.get(status, f"服务器暂时无法处理（{status}）"), code


def call(method: str, path: str, body=None, *, token: str | None = None, installation: str | None = None):
    """One JSON request to the account service; raises AccountError."""
    if not isinstance(path, str) or not path.startswith("/v1/") or any(ch in path for ch in "\r\n#"):
        raise AccountError("账号服务请求路径无效")
    root = api_root()
    data = None if body is None else json.dumps(body, ensure_ascii=False).encode("utf-8")
    headers = {"Accept": "application/json", "User-Agent": USER_AGENT}
    if data is not None:
        headers["Content-Type"] = "application/json; charset=utf-8"
    if token:
        headers["Authorization"] = f"Bearer {token}"
        if installation:
            headers["X-Installation-ID"] = installation
    req = request.Request(root + path, data=data, method=method, headers=headers)
    try:
        with _OPENER.open(req, timeout=TIMEOUT_SECONDS) as response:
            payload = response.read(MAX_RESPONSE_BYTES + 1)
    except error.HTTPError as failure:
        message, code = _error_message(failure.code, failure.read(64 * 1024))
        raise AccountError(message, failure.code, code) from None
    except (error.URLError, TimeoutError, OSError):
        raise AccountError("网络连接失败，请检查网络后重试") from None
    if len(payload) > MAX_RESPONSE_BYTES:
        raise AccountError("服务器返回的内容过大")
    if not payload:
        return None
    try:
        return json.loads(payload.decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        raise AccountError("服务器返回的内容无法识别") from None


def _expiry(value) -> float:
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            raise ValueError("timezone required")
        return parsed.timestamp()
    except (ValueError, TypeError, OverflowError, OSError):
        raise AccountError("服务器返回的登录信息无效") from None


@dataclass(frozen=True)
class Session:
    username: str
    access_token: str = field(repr=False)
    refresh_token: str = field(repr=False)
    access_expires: float
    refresh_expires: float

    @classmethod
    def from_tokens(cls, username: str, tokens) -> "Session":
        if not isinstance(tokens, dict) or not all(isinstance(tokens.get(key), str) and tokens[key]
                                                   for key in ("access_token", "refresh_token")):
            raise AccountError("服务器返回的登录信息无效")
        session = cls(username, tokens["access_token"], tokens["refresh_token"],
                      _expiry(tokens.get("access_expires_at")), _expiry(tokens.get("refresh_expires_at")))
        if min(session.access_expires, session.refresh_expires) <= time.time():
            raise AccountError("服务器返回的登录信息已过期")
        return session


# Local session storage --------------------------------------------------------
def _dpapi(data: bytes, protect: bool) -> bytes:
    import ctypes
    from ctypes import wintypes

    class Blob(ctypes.Structure):
        _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_char))]

    crypt32, kernel32 = ctypes.WinDLL("crypt32", use_last_error=True), ctypes.WinDLL("kernel32", use_last_error=True)
    buffer = ctypes.create_string_buffer(data, len(data))
    source = Blob(len(data), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_char)))
    target = Blob()
    function = crypt32.CryptProtectData if protect else crypt32.CryptUnprotectData
    function.argtypes = [ctypes.POINTER(Blob), ctypes.c_void_p, ctypes.c_void_p,
                         ctypes.c_void_p, ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(Blob)]
    function.restype = wintypes.BOOL
    kernel32.LocalFree.argtypes = [ctypes.c_void_p]
    kernel32.LocalFree.restype = ctypes.c_void_p
    # CRYPTPROTECT_UI_FORBIDDEN: never show a prompt.
    if not function(ctypes.byref(source), None, None, None, None, 0x1, ctypes.byref(target)):
        raise OSError("DPAPI failed")
    try:
        return ctypes.string_at(target.pbData, target.cbData)
    finally:
        kernel32.LocalFree(target.pbData)


class SessionVault:
    """The refresh token at rest: DPAPI on Windows, nothing elsewhere."""

    def __init__(self, path: Path, *, protect=None, unprotect=None):
        self.path = Path(path)
        if protect is None and unprotect is None and sys.platform == "win32":
            protect, unprotect = (lambda data: _dpapi(data, True)), (lambda data: _dpapi(data, False))
        self._protect, self._unprotect = protect, unprotect
        self.clear_failed = False

    @property
    def persistent(self) -> bool:
        return self._protect is not None and self._unprotect is not None

    def load(self) -> Session | None:
        if not self.persistent:
            return None
        try:
            raw = json.loads(self._unprotect(self.path.read_bytes()).decode("utf-8"))
            if (not isinstance(raw["username"], str) or not raw["username"]
                    or not isinstance(raw["refreshToken"], str)):
                raise ValueError("invalid saved session")
            session = Session(raw["username"], "", raw["refreshToken"], 0.0, float(raw["refreshExpires"]))
        except FileNotFoundError:
            return None
        except (OSError, ValueError, KeyError, TypeError):
            self.clear()
            return None
        if (not session.refresh_token or not math.isfinite(session.refresh_expires)
                or session.refresh_expires <= time.time()):
            self.clear()
            return None
        return session

    def save(self, session: Session) -> bool:
        if not self.persistent:
            return False
        payload = json.dumps(dict(username=session.username, refreshToken=session.refresh_token,
                                  refreshExpires=session.refresh_expires)).encode("utf-8")
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            temporary = self.path.with_suffix(".tmp")
            temporary.write_bytes(self._protect(payload))
            os.replace(temporary, self.path)
            return True
        except OSError:
            self.clear()
            return False
        finally:
            try:
                self.path.with_suffix(".tmp").unlink(missing_ok=True)
            except OSError:
                pass

    def clear(self) -> bool:
        self.clear_failed = False
        try:
            self.path.unlink(missing_ok=True)
            return True
        except OSError:
            # Some file permissions allow truncation but not deletion. An empty
            # vault contains no credentials and cannot restore a session.
            try:
                self.path.write_bytes(b"")
                return True
            except OSError:
                self.clear_failed = True
                return False


# Account ----------------------------------------------------------------------
def check_username(username: str) -> str:
    username = str(username or "").strip().lower()
    if not USERNAME.match(username):
        raise AccountError("用户名需使用 3–32 位英文小写、数字或下划线")
    return username


def check_password(password: str) -> str:
    password = str(password or "")
    if not 6 <= len(password) <= 128:
        raise AccountError("密码需为 6–128 位")
    return password


class AccountService:
    def __init__(self, state_dir: Path, *, vault: SessionVault | None = None, api=call):
        self.state_dir = Path(state_dir)
        self._vault = vault or SessionVault(self.state_dir / SESSION_FILE)
        self._api = api
        self._lock = threading.RLock()
        self._refresh_lock = threading.Lock()
        self._session = self._vault.load()
        self._saved = self._session is not None
        self._closed = False
        # Identity/cancellation generation. Token rotation does not change it.
        self.revision = 0
        self.installation_id = self._installation_id()

    def _installation_id(self) -> str:
        path = self.state_dir / INSTALLATION_FILE
        try:
            value = path.read_text("utf-8").strip()
            if 16 <= len(value) <= 128 and not any(ch.isspace() for ch in value):
                return value
        except OSError:
            pass
        value = str(uuid.uuid4())
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(value, "utf-8")
        except OSError:
            pass
        return value

    @property
    def persistent(self) -> bool:
        return self._vault.persistent

    @property
    def username(self) -> str | None:
        with self._lock:
            return self._session.username if self._session else None

    def snapshot(self) -> dict:
        """Thread-safe public state; never returns a password or token."""
        with self._lock:
            session = self._session
            return dict(username=session.username if session else "",
                        loggedIn=bool(session and session.access_token),
                        canRestore=bool(session and not session.access_token),
                        persistent=self.persistent, sessionSaved=self._saved,
                        storageWarning=("无法清除本地会话文件；请重试退出，关闭后可能恢复旧会话"
                                        if getattr(self._vault, "clear_failed", False) else ""),
                        revision=self.revision)

    def _check(self, revision: int) -> None:
        if self._closed or revision != self.revision:
            raise AccountError("操作已取消", code="CANCELLED")

    def _current(self, revision: int) -> Session:
        with self._lock:
            self._check(revision)
            if self._session is None:
                raise AccountError("请先登录", 401)
            return self._session

    def _store(self, session: Session, revision: int) -> None:
        with self._lock:
            self._check(revision)
            self._session = session
            self._saved = bool(self._vault.save(session))

    def _clear(self) -> None:
        # Called under the short state lock; no network work belongs here.
        self.revision += 1
        self._session, self._saved = None, False
        self._vault.clear()

    def _invalidate(self, revision: int) -> None:
        with self._lock:
            self._check(revision)
            self._clear()

    def _client(self) -> dict:
        return dict(installation_id=self.installation_id)

    # Sign in / out ------------------------------------------------------------
    def login(self, identifier: str, password: str) -> str:
        return self.prepare_login(identifier, password)()

    def prepare_login(self, identifier: str, password: str):
        """Reserve identity now; execute returned operation on a worker.

        Reserving before enqueue prevents a queued login from starting a new
        session after logout/cancel. The caller releases the closure after use.
        """
        identifier = str(identifier or "").strip().lower()
        if not identifier or len(identifier) > 320:
            raise AccountError("请输入用户名或邮箱")
        if "@" not in identifier:
            identifier = check_username(identifier)
        password = check_password(password)
        with self._lock:
            self._check(self.revision)
            self._clear()
            revision = self.revision
        def work():
            with self._lock:
                self._check(revision)
            tokens = self._api("POST", "/v1/auth/login",
                               dict(username=identifier, password=password, **self._client()))
            self._store(Session.from_tokens(identifier, tokens), revision)
            return identifier
        return work

    def restore(self) -> str | None:
        return self.prepare_restore()()

    def prepare_restore(self):
        with self._lock:
            self._check(self.revision)
            revision, session = self.revision, self._session
        def work():
            with self._lock:
                self._check(revision)
            if session is None:
                return None
            current = self._current(revision)
            if not current.access_token or current.access_expires - REFRESH_MARGIN_SECONDS <= time.time():
                current = self._refreshed(current, revision)
            with self._lock:
                self._check(revision)
            return current.username
        return work

    def prepare_logout(self):
        """Forget locally immediately; returned worker revokes that session only."""
        with self._lock:
            self._check(self.revision)
            session = self._session
            self._clear()
        return lambda: self._revoke(session)

    def logout(self) -> bool:
        return self.prepare_logout()()

    def _revoke(self, session: Session | None) -> bool:
        if session is None:
            return True
        try:
            if not session.access_token or session.access_expires <= time.time():
                tokens = self._api("POST", "/v1/auth/refresh",
                                   dict(refresh_token=session.refresh_token, **self._client()))
                session = Session.from_tokens(session.username, tokens)
            self._api("POST", "/v1/auth/logout", token=session.access_token,
                      installation=self.installation_id)
            return True
        except AccountError:
            return False  # Local logout already completed, including disk removal.

    def cancel_pending(self) -> None:
        with self._lock:
            self.revision += 1

    def close(self) -> None:
        """Invalidate outstanding work, keeping the last saved session for restart."""
        with self._lock:
            self._closed = True
            self.revision += 1
            self._session = None

    # Authorized calls ---------------------------------------------------------
    def _refreshed(self, stale: Session, revision: int) -> Session:
        # Serialize rotating refresh tokens without holding the UI state lock.
        with self._refresh_lock:
            current = self._current(revision)
            if current is not stale:
                return current
            if stale.refresh_expires <= time.time():
                self._invalidate(revision)
                raise AccountError("登录已失效，请重新登录", 401)
            try:
                tokens = self._api("POST", "/v1/auth/refresh", dict(refresh_token=stale.refresh_token,
                                                                   **self._client()))
            except AccountError as failure:
                if failure.status in (400, 401, 403, 422):
                    self._invalidate(revision)
                    raise AccountError("登录已失效，请重新登录", failure.status, failure.code) from None
                raise
            try:
                session = Session.from_tokens(stale.username, tokens)
            except AccountError:
                self._invalidate(revision)
                raise
            self._store(session, revision)
            return session

    def authorized(self, method: str, path: str, body=None):
        with self._lock:
            revision = self.revision
        session = self._current(revision)
        if session.access_expires - REFRESH_MARGIN_SECONDS <= time.time():
            session = self._refreshed(session, revision)
        for attempt in range(2):
            with self._lock:
                self._check(revision)
            try:
                result = self._api(method, path, body, token=session.access_token, installation=self.installation_id)
            except AccountError as failure:
                with self._lock:
                    self._check(revision)
                if failure.status == 401 and not failure.code and attempt == 0:
                    session = self._refreshed(session, revision)
                    continue
                if failure.status == 401 or failure.code in BLOCKED_CODES:
                    self._invalidate(revision)
                raise
            with self._lock:
                self._check(revision)
            return result


def quoted(value: str) -> str:
    return quote(str(value), safe="")
