"""Session/user-scoped ownership and bounded activate-only Qt IPC.

Windows QLocalServer permits multiple listeners on one pipe, so a retained
named mutex decides ownership first. No process enumeration, kill or foreign HWND.
"""
from __future__ import annotations

import ctypes
from ctypes import wintypes
import hashlib
import os
from pathlib import Path
import tempfile
import time

from PySide6.QtCore import QLockFile, QObject, QThread, QTimer, Signal
from PySide6.QtNetwork import QLocalServer, QLocalSocket

PRODUCTION_ID = "D04394A0-DACE-4FF9-874D-3DCB43EE53D1"
REQUEST = b"XINGTU_ACTIVATE_MAIN_V1\n"
ACK = b"XINGTU_ACTIVATE_ACCEPTED_V1\n"
HELLO = b"XINGTU_PRIMARY_V1 "


class SingleInstanceError(RuntimeError):
    pass


def instance_namespace(*, production, frozen, sandbox, probe_output=None):
    """Production survives upgrades/renames; probes cannot address production IPC."""
    if production and probe_output is None:
        return "production:" + PRODUCTION_ID
    kind = "probe" if probe_output is not None else ("isolated" if frozen else "source")
    paths = [os.path.normcase(str(Path(sandbox).resolve()))]
    if probe_output is not None:
        paths.append(os.path.normcase(str(Path(probe_output).resolve())))
    return kind + ":" + hashlib.sha256("\0".join(paths).encode()).hexdigest()


def _windows_user_session():
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    advapi = ctypes.WinDLL("advapi32", use_last_error=True)
    kernel.GetCurrentProcess.restype = wintypes.HANDLE
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel.LocalFree.argtypes = [ctypes.c_void_p]
    advapi.OpenProcessToken.argtypes = [wintypes.HANDLE, wintypes.DWORD, ctypes.POINTER(wintypes.HANDLE)]
    advapi.GetTokenInformation.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p,
                                         wintypes.DWORD, ctypes.POINTER(wintypes.DWORD)]
    advapi.ConvertSidToStringSidW.argtypes = [ctypes.c_void_p, ctypes.POINTER(wintypes.LPWSTR)]
    kernel.ProcessIdToSessionId.argtypes = [wintypes.DWORD, ctypes.POINTER(wintypes.DWORD)]
    token = wintypes.HANDLE()
    if not advapi.OpenProcessToken(kernel.GetCurrentProcess(), 0x0008, ctypes.byref(token)):
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        needed = wintypes.DWORD()
        advapi.GetTokenInformation(token, 1, None, 0, ctypes.byref(needed))
        data = ctypes.create_string_buffer(needed.value)
        if not advapi.GetTokenInformation(token, 1, data, needed, ctypes.byref(needed)):
            raise ctypes.WinError(ctypes.get_last_error())
        sid = ctypes.cast(data, ctypes.POINTER(ctypes.c_void_p))[0]
        text = wintypes.LPWSTR()
        if not advapi.ConvertSidToStringSidW(sid, ctypes.byref(text)):
            raise ctypes.WinError(ctypes.get_last_error())
        try:
            user = text.value
        finally:
            kernel.LocalFree(text)
    finally:
        kernel.CloseHandle(token)
    session = wintypes.DWORD()
    if not kernel.ProcessIdToSessionId(os.getpid(), ctypes.byref(session)):
        raise ctypes.WinError(ctypes.get_last_error())
    return f"{user}:{session.value}"


class _Ownership:
    def __init__(self, name):
        self.handle = None
        self.lock = None
        if os.name == "nt":
            self.kernel = ctypes.WinDLL("kernel32", use_last_error=True)
            self.kernel.CreateMutexW.argtypes = [ctypes.c_void_p, wintypes.BOOL, wintypes.LPCWSTR]
            self.kernel.CreateMutexW.restype = wintypes.HANDLE
            self.kernel.CloseHandle.argtypes = [wintypes.HANDLE]
            ctypes.set_last_error(0)
            handle = self.kernel.CreateMutexW(None, False, "Local\\" + name)
            error = ctypes.get_last_error()
            if not handle:
                raise ctypes.WinError(error)
            self.acquired = error != 183  # ERROR_ALREADY_EXISTS: never own this instance.
            if self.acquired:
                self.handle = handle
            else:
                self.kernel.CloseHandle(handle)
        else:
            self.lock = QLockFile(str(Path(tempfile.gettempdir()) / (name + ".lock")))
            self.lock.setStaleLockTime(0)  # Never evict a live but slow process by age.
            self.acquired = self.lock.tryLock(0)
            if not self.acquired and self.lock.error() != QLockFile.LockFailedError:
                raise OSError("Cannot access the session instance lock")

    def close(self):
        if self.handle is not None:
            self.kernel.CloseHandle(self.handle)
            self.handle = None
        if self.lock is not None and self.acquired:
            self.lock.unlock()
        self.acquired = False


class SingleInstanceGuard(QObject):
    activationRequested = Signal()

    def __init__(self, namespace, parent=None):
        super().__init__(parent)
        scope = _windows_user_session() if os.name == "nt" else f"{os.getuid()}:{os.getsid(0)}"
        self.name = "XingtuInstance-" + hashlib.sha256((scope + "\0" + namespace).encode()).hexdigest()[:40]
        self._owner = None
        self._server = None
        self._clients = set()

    def start(self, timeout_ms=3000):
        """True: primary. False: existing instance acknowledged activation."""
        if self._owner is not None:
            raise SingleInstanceError("Instance guard already started")
        ownership_error = None
        try:
            self._owner = _Ownership(self.name)
        except OSError as error:
            ownership_error = error
        if self._owner is None or not self._owner.acquired:
            if self._notify_existing(timeout_ms):
                return False
            reason = f"（权限检查：{ownership_error}）" if ownership_error else ""
            raise SingleInstanceError("星图已在当前用户会话运行或启动中，但暂时无法恢复其窗口。"
                "请稍后重试，并确认两次启动使用相同用户及权限；不会再创建第二个实例。" + reason)
        try:
            # Only the proven owner may recover an orphan socket. On Windows
            # named pipes disappear with their process; removeServer is a no-op.
            QLocalServer.removeServer(self.name)
            self._server = QLocalServer(self)
            self._server.setSocketOptions(QLocalServer.UserAccessOption)
            self._server.newConnection.connect(self._accept)
            if not self._server.listen(self.name):
                raise SingleInstanceError("无法建立星图的本地窗口恢复通道：" + self._server.errorString())
            return True
        except Exception:
            self.close()
            raise

    def _notify_existing(self, timeout_ms):
        deadline = time.monotonic() + max(0, timeout_ms) / 1000
        while time.monotonic() < deadline:
            socket = QLocalSocket()
            socket.connectToServer(self.name)
            if socket.waitForConnected(min(100, max(1, int((deadline-time.monotonic())*1000)))):
                hello = self._read_line(socket, deadline)
                pid = hello[len(HELLO):-1] if hello.startswith(HELLO) and hello.endswith(b"\n") else b""
                if pid.isdigit() and 0 < int(pid) <= 0xffffffff:
                    if os.name == "nt":
                        # A user-launched duplicate can grant its foreground
                        # permission to this same-user pipe's specific primary.
                        # Never use ASFW_ANY or activate a foreign HWND.
                        user = ctypes.WinDLL("user32", use_last_error=True)
                        user.AllowSetForegroundWindow.argtypes = [wintypes.DWORD]
                        user.AllowSetForegroundWindow(int(pid))
                    socket.write(REQUEST)
                    socket.flush()
                    if self._read_line(socket, deadline) == ACK:
                        socket.abort()
                        return True
            socket.abort()
            QThread.msleep(20)
        return False

    @staticmethod
    def _read_line(socket, deadline):
        reply = bytearray()
        while time.monotonic() < deadline and len(reply) < 128:
            if socket.bytesAvailable() or socket.waitForReadyRead(min(200, max(1, int((deadline-time.monotonic())*1000)))):
                reply.extend(bytes(socket.readAll()))
                if b"\n" in reply:
                    break
            if socket.state() == QLocalSocket.UnconnectedState:
                break
        return bytes(reply)

    def _accept(self):
        while self._server is not None and self._server.hasPendingConnections():
            socket = self._server.nextPendingConnection()
            socket.setReadBufferSize(129)
            self._clients.add(socket)
            socket.write(HELLO + str(os.getpid()).encode("ascii") + b"\n")
            socket.flush()
            buffer = bytearray()
            def consume(socket=socket, buffer=buffer):
                buffer.extend(bytes(socket.readAll()))
                if len(buffer) > 128 or (b"\n" in buffer and bytes(buffer) != REQUEST):
                    socket.abort()
                elif bytes(buffer) == REQUEST:
                    self.activationRequested.emit()
                    socket.write(ACK)
                    socket.flush()
                    socket.disconnectFromServer()
            socket.readyRead.connect(consume)
            socket.disconnected.connect(lambda socket=socket: (self._clients.discard(socket), socket.deleteLater()))
            QTimer.singleShot(1000, socket, socket.abort)
            consume()

    def close(self):
        for socket in tuple(self._clients):
            socket.abort()
        self._clients.clear()
        if self._server is not None:
            self._server.close()
            self._server = None
        if self._owner is not None:
            self._owner.close()
            self._owner = None


def restore_main_window(window):
    """Only the client's own main QWindow is passed here; no HWND searches."""
    window.showNormal()
    window.raise_()
    window.requestActivate()
