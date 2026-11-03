"""Xingtu account: the same account service the Android app signs in to.

Accounts live on ``api.xingchenmap.cn`` (``/v1/auth/*``). Login returns an
opaque Bearer access token (15 minutes) and a rotating refresh token (30
days). Only the refresh token and the display name are kept between launches,
encrypted with Windows DPAPI for the current Windows user; on other systems
the session lasts until the app closes. Passwords are never stored.

Every call blocks; the UI runs them on a worker thread.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import json
import os
from pathlib import Path
import re
import sys
import threading
import time
from urllib import error, request
from urllib.parse import quote
import uuid

PRODUCTION_ROOT = "https://api.xingchenmap.cn"
USER_AGENT = "XingtuDesktop/account"
TIMEOUT_SECONDS = 20
MAX_RESPONSE_BYTES = 2 * 1024 * 1024
REFRESH_MARGIN_SECONDS = 30
USERNAME = re.compile(r"^[a-z0-9_]{3,32}$")
EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
RECOVERY_CODE = re.compile(r"^[A-Z0-9]{4}(-[A-Z0-9]{4}){3}$")
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
    if not override.startswith(("http://127.0.0.1:", "http://localhost:")):
        raise AccountError("XINGTU_ACCOUNT_API_URL 只能指向本机测试服务器")
    return override


def _error_message(status: int, body: bytes) -> tuple[str, str]:
    try:
        data = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        data = None
    code = str(data.get("error_code") or "") if isinstance(data, dict) else ""
    detail = data.get("detail") if isinstance(data, dict) else None
    if isinstance(detail, dict):
        detail = detail.get("message")
    if isinstance(detail, list):
        detail = "填写的内容格式不正确" if status == 422 else None
    if isinstance(detail, str) and detail.strip():
        return detail.strip()[:200], code
    return STATUS_MESSAGES.get(status, f"服务器暂时无法处理（{status}）"), code


def call(method: str, path: str, body=None, *, token: str | None = None, installation: str | None = None):
    """One JSON request to the account service; raises AccountError."""
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
        return datetime.fromisoformat(str(value).replace("Z", "+00:00")).timestamp()
    except ValueError:
        raise AccountError("服务器返回的登录信息无效") from None


@dataclass
class Session:
    username: str
    access_token: str
    refresh_token: str
    access_expires: float
    refresh_expires: float

    @classmethod
    def from_tokens(cls, username: str, tokens) -> "Session":
        if not isinstance(tokens, dict) or not all(isinstance(tokens.get(key), str) and tokens[key]
                                                   for key in ("access_token", "refresh_token")):
            raise AccountError("服务器返回的登录信息无效")
        return cls(username, tokens["access_token"], tokens["refresh_token"],
                   _expiry(tokens.get("access_expires_at")), _expiry(tokens.get("refresh_expires_at")))


# Local session storage --------------------------------------------------------
def _dpapi(data: bytes, protect: bool) -> bytes:
    import ctypes
    from ctypes import wintypes

    class Blob(ctypes.Structure):
        _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_char))]

    crypt32, kernel32 = ctypes.windll.crypt32, ctypes.windll.kernel32
    buffer = ctypes.create_string_buffer(data, len(data))
    source = Blob(len(data), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_char)))
    target = Blob()
    function = crypt32.CryptProtectData if protect else crypt32.CryptUnprotectData
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
        if protect is None and sys.platform == "win32":
            protect, unprotect = (lambda data: _dpapi(data, True)), (lambda data: _dpapi(data, False))
        self._protect, self._unprotect = protect, unprotect

    @property
    def persistent(self) -> bool:
        return self._protect is not None

    def load(self) -> Session | None:
        if not self.persistent:
            return None
        try:
            raw = json.loads(self._unprotect(self.path.read_bytes()).decode("utf-8"))
            session = Session(str(raw["username"]), "", str(raw["refreshToken"]), 0.0, float(raw["refreshExpires"]))
        except FileNotFoundError:
            return None
        except (OSError, ValueError, KeyError, TypeError):
            self.clear()
            return None
        if not session.refresh_token or session.refresh_expires <= time.time():
            self.clear()
            return None
        return session

    def save(self, session: Session) -> None:
        if not self.persistent:
            return
        payload = json.dumps(dict(username=session.username, refreshToken=session.refresh_token,
                                  refreshExpires=session.refresh_expires)).encode("utf-8")
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            temporary = self.path.with_suffix(".tmp")
            temporary.write_bytes(self._protect(payload))
            os.replace(temporary, self.path)
        except OSError:
            pass  # Staying signed in is a convenience; this launch keeps working.

    def clear(self) -> None:
        try:
            self.path.unlink()
        except OSError:
            pass


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
        self._session = self._vault.load()
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

    def _set(self, session: Session | None) -> None:
        with self._lock:
            self._session = session
            self.revision += 1
            if session is None:
                self._vault.clear()
            else:
                self._vault.save(session)

    def _client(self) -> dict:
        return dict(installation_id=self.installation_id)

    # Sign in / out ------------------------------------------------------------
    def login(self, identifier: str, password: str) -> str:
        identifier = str(identifier or "").strip().lower()
        if not identifier or len(identifier) > 320:
            raise AccountError("请输入用户名或邮箱")
        if "@" not in identifier:
            identifier = check_username(identifier)
        tokens = self._api("POST", "/v1/auth/login", dict(username=identifier, password=check_password(password),
                                                         **self._client()))
        self._set(Session.from_tokens(identifier, tokens))
        return identifier

    def register(self, username: str, password: str, email: str) -> str:
        """Create an account and sign in; returns the one-time recovery code."""
        username, password = check_username(username), check_password(password)
        email = str(email or "").strip().lower()
        if not EMAIL.match(email) or len(email) > 320:
            raise AccountError("请输入有效的邮箱")
        tokens = self._api("POST", "/v1/auth/register", dict(username=username, password=password, email=email,
                                                            **self._client()))
        code = tokens.get("recovery_code") if isinstance(tokens, dict) else None
        session = Session.from_tokens(username, tokens)
        self._set(session)
        return code if isinstance(code, str) else ""

    def recover(self, identifier: str, recovery_code: str, new_password: str) -> str:
        """Reset the password with the recovery code; every session ends."""
        identifier = str(identifier or "").strip().lower()
        code = str(recovery_code or "").strip().upper()
        if not identifier:
            raise AccountError("请输入用户名或邮箱")
        if not RECOVERY_CODE.match(code):
            raise AccountError("恢复码格式为 XXXX-XXXX-XXXX-XXXX")
        result = self._api("POST", "/v1/auth/password-recovery",
                           dict(identifier=identifier, recovery_code=code, new_password=check_password(new_password),
                                preserve_recovery_code=True))
        self._set(None)
        message = result.get("message") if isinstance(result, dict) else None
        return message if isinstance(message, str) and message else "密码已重置，请用新密码登录"

    def logout(self) -> None:
        try:
            if self.username:
                self.authorized("POST", "/v1/auth/logout")  # Ends the server session too.
        except AccountError:
            pass  # Signing out locally always succeeds.
        finally:
            self._set(None)

    # Authorized calls ---------------------------------------------------------
    def _refreshed(self, stale: Session) -> Session:
        with self._lock:
            if self._session is not stale:
                if self._session is None:
                    raise AccountError("请先登录", 401)
                return self._session
            try:
                tokens = self._api("POST", "/v1/auth/refresh", dict(refresh_token=stale.refresh_token,
                                                                   **self._client()))
            except AccountError as failure:
                if failure.status in (400, 401, 403, 422):
                    self._set(None)
                    raise AccountError("登录已失效，请重新登录", failure.status, failure.code) from None
                raise
            session = Session.from_tokens(stale.username, tokens)
            self._set(session)
            return session

    def authorized(self, method: str, path: str, body=None):
        with self._lock:
            session = self._session
        if session is None:
            raise AccountError("请先登录", 401)
        if session.access_expires - REFRESH_MARGIN_SECONDS <= time.time():
            session = self._refreshed(session)
        try:
            return self._api(method, path, body, token=session.access_token, installation=self.installation_id)
        except AccountError as failure:
            if failure.status == 401 and not failure.code:
                session = self._refreshed(session)
                return self._api(method, path, body, token=session.access_token, installation=self.installation_id)
            if failure.code in BLOCKED_CODES:
                self._set(None)
            raise


def quoted(value: str) -> str:
    return quote(str(value), safe="")
