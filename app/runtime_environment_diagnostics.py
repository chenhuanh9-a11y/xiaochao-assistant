"""Read-only support diagnostics. No Qt objects or process discovery in workers.

Call freeze_runtime_inputs on the UI thread with plain JSON screen/input/capture
snapshots and the already-bound HWND/PID, then collect_runtime_environment in an
export worker. Native queries are limited to this process and that bound identity.
"""
from __future__ import annotations

import ctypes
import json
import platform
import sys
from dataclasses import dataclass


def unavailable(reason):
    return {"status": "unavailable", "reason": reason}


@dataclass(frozen=True)
class RuntimeInputs:
    payload: str


def freeze_runtime_inputs(*, screens=None, input_snapshot=None, capture_snapshot=None,
                          bound_identity=None):
    """Detach JSON values from mutable UI state; never retain QObject references.

    screens should contain actual QScreen geometry, availableGeometry,
    devicePixelRatio, logicalDotsPerInch and physicalDotsPerInch, sampled by UI.
    bound_identity is {"hwnd": int, "pid": int} from the existing binding.
    """
    return RuntimeInputs(json.dumps(dict(screens=screens, input=input_snapshot,
        capture=capture_snapshot, boundIdentity=bound_identity), allow_nan=False))


class WindowsDiagnostics:
    """Minimal Win32 token/window reads; never request elevation or debug rights."""
    def __init__(self):
        from ctypes import wintypes as w
        self.w = w
        self.kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        self.advapi = ctypes.WinDLL("advapi32", use_last_error=True)
        self.user = ctypes.WinDLL("user32", use_last_error=True)
        self.kernel.GetCurrentProcess.restype = w.HANDLE
        self.kernel.OpenProcess.argtypes = [w.DWORD, w.BOOL, w.DWORD]
        self.kernel.OpenProcess.restype = w.HANDLE
        self.kernel.CloseHandle.argtypes = [w.HANDLE]
        self.advapi.OpenProcessToken.argtypes = [w.HANDLE, w.DWORD, ctypes.POINTER(w.HANDLE)]
        self.advapi.GetTokenInformation.argtypes = [w.HANDLE, ctypes.c_int, ctypes.c_void_p,
                                                   w.DWORD, ctypes.POINTER(w.DWORD)]
        self.advapi.GetSidSubAuthorityCount.argtypes = [ctypes.c_void_p]
        self.advapi.GetSidSubAuthorityCount.restype = ctypes.POINTER(ctypes.c_ubyte)
        self.advapi.GetSidSubAuthority.argtypes = [ctypes.c_void_p, w.DWORD]
        self.advapi.GetSidSubAuthority.restype = ctypes.POINTER(w.DWORD)
        self.user.GetWindowThreadProcessId.argtypes = [w.HWND, ctypes.POINTER(w.DWORD)]
        self.user.GetWindowRect.argtypes = [w.HWND, ctypes.POINTER(w.RECT)]
        self.user.IsZoomed.argtypes = [w.HWND]
        self.user.IsIconic.argtypes = [w.HWND]

    @staticmethod
    def _check(ok, operation):
        if not ok:
            raise OSError(ctypes.get_last_error(), operation + " failed")

    def window_pid(self, hwnd):
        pid = self.w.DWORD()
        self._check(self.user.GetWindowThreadProcessId(hwnd, ctypes.byref(pid)),
                    "GetWindowThreadProcessId")
        return pid.value

    def integrity(self, pid=None):
        process = self.kernel.GetCurrentProcess() if pid is None else self.kernel.OpenProcess(0x1000, False, pid)
        self._check(process, "OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION)")
        token = self.w.HANDLE()
        try:
            self._check(self.advapi.OpenProcessToken(process, 0x0008, ctypes.byref(token)),
                        "OpenProcessToken(TOKEN_QUERY)")
            needed = self.w.DWORD()
            self.advapi.GetTokenInformation(token, 25, None, 0, ctypes.byref(needed))
            if not needed.value:
                self._check(False, "GetTokenInformation(TokenIntegrityLevel size)")
            buffer = ctypes.create_string_buffer(needed.value)
            self._check(self.advapi.GetTokenInformation(token, 25, buffer, needed, ctypes.byref(needed)),
                        "GetTokenInformation(TokenIntegrityLevel)")
            sid = ctypes.cast(buffer, ctypes.POINTER(ctypes.c_void_p))[0]
            count = self.advapi.GetSidSubAuthorityCount(sid)[0]
            if not count:
                raise ValueError("TokenIntegrityLevel SID has no subauthorities")
            rid = self.advapi.GetSidSubAuthority(sid, count - 1)[0]
            label = ("untrusted" if rid < 0x1000 else "low" if rid < 0x2000 else
                     "medium" if rid < 0x3000 else "high" if rid < 0x4000 else
                     "system" if rid < 0x5000 else "protected")
            return {"status": "available", "rid": rid, "level": label}
        finally:
            if token:
                self.kernel.CloseHandle(token)
            if pid is not None:
                self.kernel.CloseHandle(process)

    def window(self, hwnd):
        rect = self.w.RECT()
        self._check(self.user.GetWindowRect(hwnd, ctypes.byref(rect)), "GetWindowRect")
        result = dict(status="available", bounds=[rect.left, rect.top, rect.right, rect.bottom],
                      presentation="minimized" if self.user.IsIconic(hwnd) else
                      "maximized" if self.user.IsZoomed(hwnd) else "normal",
                      fullscreenMode=unavailable("exclusive versus borderless cannot be established by window geometry"))
        dpi = getattr(self.user, "GetDpiForWindow", None)
        if dpi:
            dpi.argtypes = [self.w.HWND]
            value = dpi(hwnd)
            result["dpi"] = value if value else unavailable("GetDpiForWindow returned zero")
        else:
            result["dpi"] = unavailable("GetDpiForWindow API absent")
        return result


def _query(call):
    try:
        return call()
    except Exception as error:
        # Native operation names/codes only; no arbitrary exception messages.
        result = unavailable(type(error).__name__)
        if isinstance(error, OSError):
            result.update(reason=str(error.strerror or "native query failed"),
                          errorCode=error.winerror if getattr(error, "winerror", None) is not None else error.errno)
        return result


def collect_runtime_environment(inputs: RuntimeInputs, *, native=None, platform_name=None):
    """Consume frozen values; injectable native boundary supports denial tests.

    Does not probe capture, install hooks, enumerate processes, read user files,
    mutate focus, or infer success from configured capture/hook settings.
    """
    values = json.loads(inputs.payload)
    result = dict(platform=dict(system=platform.system(), release=platform.release(),
                                version=platform.version()),
                  screens=values["screens"] if values["screens"] is not None else unavailable("UI screen snapshot not supplied"),
                  input=values["input"] if values["input"] is not None else unavailable("input hook snapshot not supplied"),
                  capture=values["capture"] if values["capture"] is not None else unavailable("capture snapshot not supplied"))
    bound = values["boundIdentity"]
    if (platform_name or sys.platform) != "win32":
        result.update(qtProcessIntegrity=unavailable("Windows token query unsupported on this platform"),
                      gameProcessIntegrity=unavailable("Windows token query unsupported on this platform"),
                      boundWindow=unavailable("Windows window query unsupported on this platform"))
        return result
    try:
        native = native if native is not None else WindowsDiagnostics()
    except Exception as error:
        failure = unavailable("native API initialization failed: " + type(error).__name__)
        result.update(qtProcessIntegrity=failure, gameProcessIntegrity=dict(failure), boundWindow=dict(failure))
        return result
    result["qtProcessIntegrity"] = _query(lambda: native.integrity())
    if not bound or not bound.get("hwnd") or not bound.get("pid"):
        result.update(gameProcessIntegrity=unavailable("bound HWND/PID not supplied"),
                      boundWindow=unavailable("bound HWND/PID not supplied"))
        return result
    try:
        hwnd, pid = int(bound["hwnd"]), int(bound["pid"])
        if hwnd <= 0 or pid <= 0:
            raise ValueError("nonpositive identity")
    except (TypeError, ValueError, OverflowError):
        result.update(gameProcessIntegrity=unavailable("bound HWND/PID invalid"),
                      boundWindow=unavailable("bound HWND/PID invalid"))
        return result
    identity = _query(lambda: native.window_pid(hwnd))
    if identity != pid:
        failure = identity if isinstance(identity, dict) else unavailable("bound HWND/PID identity changed")
        result.update(gameProcessIntegrity=failure, boundWindow=dict(failure))
        return result
    result["gameProcessIntegrity"] = _query(lambda: native.integrity(pid))
    result["boundWindow"] = _query(lambda: native.window(hwnd))
    return result
