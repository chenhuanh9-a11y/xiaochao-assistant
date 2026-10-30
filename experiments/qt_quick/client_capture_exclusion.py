"""UI-owned display-affinity lane for the original full-client fallback.

Only ``full_capture`` runs on a worker. All QWindow/native-affinity operations
run on this object's Qt owner thread. This does not hide, activate, restore or
read another application's windows, and never falls back without exclusion.
"""
from __future__ import annotations

import ctypes
from ctypes import wintypes
from dataclasses import dataclass, field
import os
import sys
import threading

from PySide6.QtCore import QEvent, QObject, QThread, Qt, Signal, Slot
from PySide6.QtGui import QGuiApplication, QPlatformSurfaceEvent, QWindow


class CaptureExclusionError(RuntimeError):
    pass


class _DisplayAffinity:
    """Same WDA_EXCLUDEFROMCAPTURE/previous-value contract as the Tk lane."""
    def __init__(self):
        if os.name != "nt" or sys.getwindowsversion().build < 19041:
            raise CaptureExclusionError("Display exclusion requires Windows build 19041 or later")
        self.user = ctypes.WinDLL("user32", use_last_error=True)
        self.dwm = ctypes.WinDLL("dwmapi", use_last_error=True)
        self.user.IsWindow.argtypes = [wintypes.HWND]
        self.user.IsWindow.restype = wintypes.BOOL
        self.user.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
        self.user.GetWindowThreadProcessId.restype = wintypes.DWORD
        self.user.GetWindowDisplayAffinity.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
        self.user.GetWindowDisplayAffinity.restype = wintypes.BOOL
        self.user.SetWindowDisplayAffinity.argtypes = [wintypes.HWND, wintypes.DWORD]
        self.user.SetWindowDisplayAffinity.restype = wintypes.BOOL
        self.dwm.DwmFlush.argtypes = []
        self.dwm.DwmFlush.restype = ctypes.c_long

    def is_window(self, hwnd):
        return bool(self.user.IsWindow(hwnd))

    def process_id(self, hwnd):
        pid = wintypes.DWORD()
        if not self.user.GetWindowThreadProcessId(hwnd, ctypes.byref(pid)):
            raise ctypes.WinError(ctypes.get_last_error())
        return pid.value

    def get(self, hwnd):
        value = wintypes.DWORD()
        if not self.user.GetWindowDisplayAffinity(hwnd, ctypes.byref(value)):
            raise ctypes.WinError(ctypes.get_last_error())
        return value.value

    def set(self, hwnd, value):
        if not self.user.SetWindowDisplayAffinity(hwnd, value):
            raise ctypes.WinError(ctypes.get_last_error())

    def flush(self):
        if self.dwm.DwmFlush() < 0:
            raise CaptureExclusionError("DwmFlush failed during display exclusion")


@dataclass
class _Request:
    prepared: threading.Event = field(default_factory=threading.Event)
    restored: threading.Event = field(default_factory=threading.Event)
    cancelled: threading.Event = field(default_factory=threading.Event)
    changed: list = field(default_factory=list)
    phase: str = "queued"
    error: Exception | None = None
    restore_error: Exception | None = None


class QtCaptureExclusion(QObject):
    """Retain until its pendingWrites is false during normal close/drain.

    Compose on UI: ``helper = QtCaptureExclusion(is_closing=...)`` then inject
    ``ClientCaptureProvider(full_capture=helper.full_capture)``. Add helper to
    coordinator.close_writers so cancel/drain also covers owner timeouts. Call
    shutdown after the worker has drained. No capture or native calls occur at
    construction. protectWindow is an explicit before-show enrollment seam;
    the application filter also enrolls newly created/shown Qt platform windows.
    """
    _prepareRequested = Signal(object)
    _restoreRequested = Signal(object)

    def __init__(self, *, windows=None, native=None, capture=None, is_closing=None,
                 timeout=5., parent=None):
        super().__init__(parent)
        self._app = QGuiApplication.instance()
        if self._app is None:
            raise CaptureExclusionError("A Qt application owner is required")
        self._assert_owner()
        self._windows = windows or self._app.allWindows
        self._native = native
        self._capture = capture
        self._is_closing = is_closing or (lambda: False)
        self._timeout = float(timeout)
        if self._timeout <= 0:
            raise ValueError("Owner acknowledgement timeout must be positive")
        self._request = None
        self._request_lock = threading.Lock()
        self._lane = threading.Lock()
        self._closed = False
        self._enrolling = set()
        self._prepareRequested.connect(self._prepare, Qt.QueuedConnection)
        self._restoreRequested.connect(self._restore, Qt.QueuedConnection)
        self._app.installEventFilter(self)

    def _assert_owner(self):
        if (QThread.currentThread() != self.thread()
                or self.thread() != self._app.thread()):
            raise CaptureExclusionError("Display exclusion belongs to the Qt GUI window owner")

    @property
    def pendingWrites(self):
        # Uses the existing close-writer drain protocol; no disk writes occur.
        with self._request_lock:
            return self._request is not None

    def cancel(self):
        with self._request_lock:
            request = self._request
        if request is not None:
            request.cancelled.set()

    def shutdown(self):
        self._assert_owner()
        self._closed = True
        self.cancel()
        if not self.pendingWrites:
            self._app.removeEventFilter(self)

    def full_capture(self, hwnd):
        if QThread.currentThread() == self.thread():
            raise CaptureExclusionError("Fallback capture must run on the capture worker")
        if not self._lane.acquire(blocking=False):
            raise CaptureExclusionError("A display-excluding capture is already in progress")
        request, failure, result = _Request(), None, None
        try:
            with self._request_lock:
                if self._request is not None or self._closed or self._is_closing():
                    raise CaptureExclusionError("Fallback capture is closing or still restoring")
                self._request = request
            try:
                self._prepareRequested.emit(request)
                if not request.prepared.wait(self._timeout):
                    request.cancelled.set()
                    raise CaptureExclusionError("Display capture owner timed out: prepared")
                self._check_request(request)
                capture = self._capture
                if capture is None:
                    from identityv_live_mvp import capture_client
                    capture = capture_client
                result = capture(hwnd)
                self._check_request(request)
            except Exception as error:
                failure = error
            finally:
                self._restoreRequested.emit(request)
                if not request.restored.wait(self._timeout):
                    request.cancelled.set()
                    raise CaptureExclusionError("Display capture owner timed out: restored") from failure
            if request.restore_error is not None:
                raise request.restore_error from failure
            if failure is not None:
                raise failure
            self._check_request(request)
            return result
        finally:
            self._lane.release()

    def _check_request(self, request):
        if request.error is not None:
            raise request.error
        if request.cancelled.is_set() or self._closed or self._is_closing():
            raise CaptureExclusionError("Fallback capture was cancelled")

    @Slot(object)
    def _prepare(self, request):
        self._assert_owner()
        try:
            self._check_request(request)
            request.phase = "preparing"
            if self._native is None:
                self._native = _DisplayAffinity()
            for window in self._windows():
                self._check_request(request)
                self.protectWindow(window)
        except Exception as error:
            request.error = error
        finally:
            request.phase = "capturing"
            request.prepared.set()

    def protectWindow(self, window):
        """UI-only, before show; enroll hidden windows and recreated handles too."""
        self._assert_owner()
        request = self._request
        if request is None or request.phase not in {"preparing", "capturing"}:
            return
        self._check_request(request)
        key = id(window)
        if key in self._enrolling:
            return
        self._enrolling.add(key)
        try:
            hwnd = int(window.winId())
            if any(row["live"] and row["hwnd"] == hwnd and row["window"] is window for row in request.changed):
                return
            if not self._native.is_window(hwnd) or self._native.process_id(hwnd) != os.getpid():
                raise CaptureExclusionError("Only this process's Qt windows can be excluded")
            previous = self._native.get(hwnd)
            self._native.set(hwnd, 0x11)
            request.changed.append(dict(hwnd=hwnd, window=window, previous=previous, live=True))
            self._native.flush()
        finally:
            self._enrolling.discard(key)

    def eventFilter(self, watched, event):
        request = self._request
        if request is not None and isinstance(watched, QWindow):
            if (event.type() == QEvent.PlatformSurface
                    and event.surfaceEventType() == QPlatformSurfaceEvent.SurfaceAboutToBeDestroyed):
                for row in request.changed:
                    if row["window"] is watched:
                        row["live"] = False
            elif event.type() in {QEvent.PlatformSurface, QEvent.Show, QEvent.WinIdChange}:
                try:
                    self.protectWindow(watched)
                except Exception as error:
                    request.error = error
                    request.cancelled.set()  # A possibly contaminated frame cannot escape.
        return False  # Never consume or redirect any window/input event.

    @Slot(object)
    def _restore(self, request):
        self._assert_owner()
        request.phase = "restoring"
        failures = []
        for row in reversed(request.changed):
            try:
                if row["live"] and self._native.is_window(row["hwnd"]):
                    if self._native.process_id(row["hwnd"]) != os.getpid():
                        continue  # A recycled foreign handle is never modified.
                    self._native.set(row["hwnd"], row["previous"])
            except Exception as error:
                failures.append(str(error))
        if request.changed:
            try:
                self._native.flush()
            except Exception as error:
                failures.append(str(error))
        if failures:
            request.restore_error = CaptureExclusionError("Display exclusion could not be restored: " + "; ".join(failures))
        with self._request_lock:
            if self._request is request:
                self._request = None
        request.phase = "done"
        request.restored.set()
        if self._closed:
            self._app.removeEventFilter(self)
