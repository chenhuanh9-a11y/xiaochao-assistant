"""Scoped Python client for the C# native-window named-pipe primitive.

This module transports native window metadata and BGR pixels only. Recognition,
capture state, generation fences, blank-frame policy, and RegionCapture remain
owned by the existing ClientCaptureProvider/client-region pipeline.
"""
from __future__ import annotations

from contextlib import contextmanager
import ctypes
from ctypes import wintypes
import json
import math
import os
import threading
import time

import numpy as np

from client_region_capture import CaptureContextChanged, ClientCaptureContext


MAX_JSON_BYTES = 65_536
MAX_FRAME_PIXELS = 32_000_000
MAX_FRAME_BYTES = MAX_FRAME_PIXELS * 3
IO_TIMEOUT_SECONDS = 2.0
_CANCEL_COMPLETION_MS = 1000
_MAX_IO_CHUNK = 64 * 1024


class NativeCaptureError(RuntimeError):
    """A bounded native protocol error that must not trigger DC fallback."""

    def __init__(self, code, message="native capture request rejected"):
        self.code = str(code)
        super().__init__(str(message)[:512])


class _NativeOperationError(OSError):
    """A valid peer error whose channel remains aligned and reusable."""


class _NativeScope:
    __slots__ = ("session", "request_id", "cancelled_token", "deadline", "entered",
                 "cancel_reason", "cancelled_at")

    def __init__(self, session, request_id, cancelled, deadline):
        self.session = session
        self.request_id = request_id
        self.cancelled_token = cancelled
        self.deadline = deadline
        self.entered = False
        self.cancel_reason = None
        self.cancelled_at = None

    def poll(self, *, closing=False):
        if closing:
            self.cancel("closed")
        elif self.cancel_reason is None:
            try:
                if _is_cancelled(self.cancelled_token):
                    self.cancel("cancelled")
                elif self.deadline is not None and time.monotonic() >= self.deadline:
                    self.cancel("deadline-exceeded")
            except Exception:
                self.cancel("cancelled")
        return self.cancel_reason

    def cancel(self, reason):
        if self.cancel_reason is None:
            self.cancel_reason = str(reason)
            self.cancelled_at = time.monotonic()


def _is_cancelled(value):
    if value is None:
        return False
    return bool(value.is_set() if hasattr(value, "is_set") else value())


def _positive_int(value, label):
    if type(value) is not int or value <= 0:
        raise ValueError(f"{label}-invalid")
    return value


def _context(value):
    if not isinstance(value, dict):
        raise ValueError("capture-context-invalid")
    size, bounds = value.get("fullClientSize"), value.get("clientScreenBounds")
    if not isinstance(size, list) or len(size) != 2 or not isinstance(bounds, list) or len(bounds) != 4:
        raise ValueError("capture-context-invalid")
    width, height = (_positive_int(item, "capture-context-size") for item in size)
    if width * height > MAX_FRAME_PIXELS:
        raise ValueError("capture-context-size-invalid")
    if any(type(item) is not int for item in bounds):
        raise ValueError("capture-context-bounds-invalid")
    left, top, right, bottom = bounds
    if right - left != width or bottom - top != height:
        raise ValueError("capture-context-bounds-size-mismatch")
    dpi = _positive_int(value.get("dpi"), "capture-context-dpi")
    return ClientCaptureContext((width, height), (left, top, right, bottom), dpi)


def _windows_api():
    if os.name != "nt":
        raise OSError("Native capture named pipes require Windows")
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.WaitNamedPipeW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD]
    kernel.WaitNamedPipeW.restype = wintypes.BOOL
    kernel.CreateFileW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD,
                                   wintypes.LPVOID, wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE]
    kernel.CreateFileW.restype = wintypes.HANDLE
    kernel.PeekNamedPipe.argtypes = [wintypes.HANDLE, wintypes.LPVOID, wintypes.DWORD,
                                     ctypes.POINTER(wintypes.DWORD), ctypes.POINTER(wintypes.DWORD),
                                     ctypes.POINTER(wintypes.DWORD)]
    kernel.PeekNamedPipe.restype = wintypes.BOOL
    kernel.ReadFile.argtypes = [wintypes.HANDLE, wintypes.LPVOID, wintypes.DWORD,
                                ctypes.POINTER(wintypes.DWORD), wintypes.LPVOID]
    kernel.ReadFile.restype = wintypes.BOOL
    kernel.WriteFile.argtypes = [wintypes.HANDLE, wintypes.LPCVOID, wintypes.DWORD,
                                 ctypes.POINTER(wintypes.DWORD), wintypes.LPVOID]
    kernel.WriteFile.restype = wintypes.BOOL
    kernel.CreateEventW.argtypes = [wintypes.LPVOID, wintypes.BOOL, wintypes.BOOL, wintypes.LPCWSTR]
    kernel.CreateEventW.restype = wintypes.HANDLE
    kernel.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
    kernel.WaitForSingleObject.restype = wintypes.DWORD
    kernel.GetOverlappedResult.argtypes = [wintypes.HANDLE, wintypes.LPVOID,
                                          ctypes.POINTER(wintypes.DWORD), wintypes.BOOL]
    kernel.GetOverlappedResult.restype = wintypes.BOOL
    kernel.CancelIoEx.argtypes = [wintypes.HANDLE, wintypes.LPVOID]
    kernel.CancelIoEx.restype = wintypes.BOOL
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel.CloseHandle.restype = wintypes.BOOL
    return kernel


class _OVERLAPPED(ctypes.Structure):
    _fields_ = [("Internal", ctypes.c_size_t), ("InternalHigh", ctypes.c_size_t),
                ("Offset", wintypes.DWORD), ("OffsetHigh", wintypes.DWORD),
                ("hEvent", wintypes.HANDLE)]


class NativeCaptureProxy:
    """A serialized, strictly scoped client for one native named pipe.

    ``request(session, id, cancelled, deadline)`` binds the outer computation
    identity to its current thread. Every native call must occur in that scope.
    ``deadline`` is an absolute ``time.monotonic()`` value. A missing deadline
    still gives each pipe I/O a real, cancellable two second limit.
    """

    def __init__(self, pipe_name):
        if not isinstance(pipe_name, str) or not pipe_name.strip():
            raise ValueError("native-pipe-name-required")
        self.pipe_name = pipe_name.strip()
        pipe_prefix = "\\\\.\\pipe\\"
        path = self.pipe_name if self.pipe_name.casefold().startswith(pipe_prefix) else pipe_prefix + self.pipe_name.lstrip("\\/")
        self._kernel = _windows_api()
        if not self._kernel.WaitNamedPipeW(path, int(IO_TIMEOUT_SECONDS * 1000)):
            error = ctypes.get_last_error()
            raise ctypes.WinError(error)
        handle = self._kernel.CreateFileW(path, 0x80000000 | 0x40000000, 0, None,
            3, 0x40000000, None)  # GENERIC_READ|WRITE, OPEN_EXISTING, FILE_FLAG_OVERLAPPED
        if handle == wintypes.HANDLE(-1).value:
            raise ctypes.WinError(ctypes.get_last_error())
        self._handle = handle
        self._thread_scope = threading.local()
        self._io_lock = threading.Lock()
        self._handle_lock = threading.Lock()
        self._closing = threading.Event()
        self._state_lock = threading.Lock()
        self._closed = False
        self._broken = False
        self._broken_reason = ""
        self._next_call_id = 0

    @property
    def metadata(self):
        """Return local transport state only; this property never issues RPC."""
        with self._state_lock:
            return dict(connected=not self._closed and not self._broken,
                closed=self._closed, broken=self._broken, brokenReason=self._broken_reason,
                nextCallId=self._next_call_id)

    @property
    def snapshot(self):
        return self.metadata

    @contextmanager
    def request(self, session, request_id, cancelled=None, deadline=None):
        if not isinstance(session, str) or not session or type(request_id) is not int:
            raise ValueError("native-request-identity-invalid")
        if deadline is not None and (type(deadline) not in (int, float) or not math.isfinite(deadline)):
            raise ValueError("native-request-deadline-invalid")
        if getattr(self._thread_scope, "value", None) is not None:
            raise NativeCaptureError("stale", "nested native request scope is not allowed")
        scope = _NativeScope(session, request_id, cancelled, float(deadline) if deadline is not None else None)
        scope.entered = True
        self._thread_scope.value = scope
        try:
            yield self
        finally:
            scope.entered = False
            if getattr(self._thread_scope, "value", None) is scope:
                del self._thread_scope.value

    def _scope(self):
        scope = getattr(self._thread_scope, "value", None)
        if scope is None or not scope.entered:
            raise NativeCaptureError("binding-unavailable", "native call requires its current request scope")
        reason = scope.poll(closing=self._closing.is_set())
        if reason:
            raise NativeCaptureError(reason, "native request scope is cancelled or stale")
        with self._state_lock:
            if self._broken:
                raise NativeCaptureError("stale", "native channel is broken and cannot be reused")
            if self._closed:
                raise NativeCaptureError("stale", "native channel is closed")
        return scope

    def is_window(self, hwnd):
        result = self._rpc("is_window", hwnd=_positive_int(hwnd, "hwnd"))
        if type(result) is not bool:
            self._protocol_broken("is-window-result-invalid")
        return result

    def foreground_window(self):
        result = self._rpc("foreground_window")
        if type(result) is not int or result < 0:
            self._protocol_broken("foreground-window-result-invalid")
        return result

    def context(self, hwnd):
        value = self._rpc("context", hwnd=_positive_int(hwnd, "hwnd"))
        try:
            return _context(value)
        except (TypeError, ValueError):
            self._protocol_broken("capture-context-invalid")

    def capture(self, hwnd, roi):
        if not isinstance(roi, (tuple, list)) or len(roi) != 4 or any(type(item) is not int for item in roi):
            raise ValueError("capture-roi-invalid")
        left, top, right, bottom = roi
        width, height = right - left, bottom - top
        if width <= 0 or height <= 0 or width * height > MAX_FRAME_PIXELS:
            raise ValueError("capture-roi-size-invalid")
        result, payload = self._rpc("capture", hwnd=_positive_int(hwnd, "hwnd"), roi=list(roi), _pixels=True)
        context = self._capture_result(result, width, height)
        if left < 0 or top < 0 or right > context.full_client_size[0] or bottom > context.full_client_size[1]:
            self._protocol_broken("capture-roi-outside-context")
        return self._pixels(payload, width, height)

    def full_capture(self, hwnd):
        result, payload = self._rpc("full_capture", hwnd=_positive_int(hwnd, "hwnd"), _pixels=True)
        if not isinstance(result, dict):
            self._protocol_broken("full-capture-result-invalid")
        context = self._capture_result(result, None, None)
        width, height = context.full_client_size
        if (result.get("width"), result.get("height")) != (width, height):
            self._protocol_broken("full-capture-shape-mismatch")
        return self._pixels(payload, width, height), context.client_screen_bounds

    def _capture_result(self, result, expected_width, expected_height):
        if not isinstance(result, dict):
            self._protocol_broken("capture-result-invalid")
        width, height = result.get("width"), result.get("height")
        if type(width) is not int or type(height) is not int or width <= 0 or height <= 0:
            self._protocol_broken("capture-shape-invalid")
        if width * height > MAX_FRAME_PIXELS:
            self._protocol_broken("capture-size-invalid")
        if expected_width is not None and (width, height) != (expected_width, expected_height):
            self._protocol_broken("capture-shape-mismatch")
        try:
            context = _context(result.get("context"))
        except (TypeError, ValueError):
            self._protocol_broken("capture-context-invalid")
        if width > context.full_client_size[0] or height > context.full_client_size[1]:
            self._protocol_broken("capture-context-shape-mismatch")
        return context

    def _pixels(self, payload, width, height):
        if len(payload) != width * height * 3:
            self._protocol_broken("native-bgr-payload-shape-mismatch")
        frame = np.frombuffer(payload, dtype=np.uint8).reshape(height, width, 3).copy(order="C")
        frame.setflags(write=False)
        return frame

    def _rpc(self, operation, *, _pixels=False, **arguments):
        scope = self._scope()
        with self._io_lock:
            scope = self._scope()
            with self._state_lock:
                self._next_call_id += 1
                call_id = self._next_call_id
            request = dict(session=scope.session, requestId=scope.request_id,
                           callId=call_id, operation=operation, **arguments)
            try:
                encoded = json.dumps(request, ensure_ascii=False, separators=(",", ":"), allow_nan=False).encode("utf-8")
            except (TypeError, ValueError) as error:
                raise ValueError("native-request-json-invalid") from error
            if not 1 <= len(encoded) <= MAX_JSON_BYTES:
                raise ValueError("native-request-too-large")
            message = len(encoded).to_bytes(4, "little") + encoded
            deadline = min(time.monotonic() + IO_TIMEOUT_SECONDS, scope.deadline) if scope.deadline is not None else time.monotonic() + IO_TIMEOUT_SECONDS
            try:
                self._write_all(message, scope, deadline)
                header_size = int.from_bytes(self._read_exact(4, scope, deadline), "little")
                if not 1 <= header_size <= MAX_JSON_BYTES:
                    self._protocol_broken("native-response-length-invalid")
                raw_header = self._read_exact(header_size, scope, deadline)
                try:
                    response = json.loads(raw_header.decode("utf-8"))
                except (UnicodeDecodeError, json.JSONDecodeError):
                    self._protocol_broken("native-response-json-invalid")
                if not isinstance(response, dict):
                    self._protocol_broken("native-response-object-required")
                payload_length = response.get("payloadLength")
                if type(payload_length) is not int or payload_length < 0 or payload_length > MAX_FRAME_BYTES:
                    self._protocol_broken("native-payload-length-invalid")
                response_payload = self._read_exact(payload_length, scope, deadline) if payload_length else b""
                self._validate_response_identity(response, scope, call_id)
                self._check_scope_cancelled_after_drain(scope)
                if type(response.get("ok")) is not bool:
                    self._protocol_broken("native-response-ok-invalid")
                if not response["ok"]:
                    if payload_length:
                        self._protocol_broken("native-error-has-payload")
                    self._raise_native_error(response.get("error"))
                expected_payload = operation in ("capture", "full_capture")
                if payload_length and not expected_payload:
                    self._protocol_broken("unexpected-native-payload")
                if expected_payload and payload_length == 0:
                    self._protocol_broken("native-capture-payload-empty")
                if expected_payload != _pixels:
                    self._protocol_broken("native-capture-call-shape-invalid")
                result = response.get("result")
                if expected_payload:
                    return result, response_payload
                return result
            except (NativeCaptureError, CaptureContextChanged, _NativeOperationError):
                raise
            except OSError as error:
                if not self._closing.is_set():
                    self._mark_broken(f"io:{getattr(error, 'winerror', None) or error.errno or 'error'}")
                reason = scope.poll(closing=self._closing.is_set())
                if reason:
                    raise NativeCaptureError(reason, "native request cancelled during pipe I/O") from error
                if getattr(error, "code", None):
                    raise
                raise NativeCaptureError("native-channel-failed", "native capture pipe failed") from error
            except Exception as error:
                if self._closing.is_set():
                    raise NativeCaptureError("closed", "native capture channel closed") from error
                if isinstance(error, (ValueError, TypeError)):
                    raise
                self._mark_broken(f"protocol:{type(error).__name__}")
                raise

    def _validate_response_identity(self, response, scope, call_id):
        if (response.get("session") != scope.session or type(response.get("requestId")) is not int
                or response.get("requestId") != scope.request_id or type(response.get("callId")) is not int
                or response.get("callId") != call_id):
            self._protocol_broken("native-response-identity-mismatch")

    def _raise_native_error(self, error):
        if not isinstance(error, dict) or not isinstance(error.get("code"), str):
            self._protocol_broken("native-error-invalid")
        code, message = error["code"], error.get("message", "native capture failed")
        message = str(message)[:512]
        if code == "native-error":
            raise _NativeOperationError(message)
        if code == "capture-context-changed":
            failure = CaptureContextChanged(message)
            failure.code = code
            raise failure
        if code == "capture-safe-fallback-unsupported":
            failure = NativeCaptureError(code, message)
            capability = error.get("captureCapability")
            failure.capture_capability = dict(capability) if isinstance(capability, dict) else {}
            failure.capture_diagnostics = dict(captureCapability=failure.capture_capability)
            raise failure
        if code in ("cancelled", "stale", "binding-unavailable"):
            raise NativeCaptureError(code, message)
        raise NativeCaptureError(code, message)

    def _check_scope_cancelled_after_drain(self, scope):
        reason = scope.poll(closing=self._closing.is_set())
        if reason:
            raise NativeCaptureError(reason, "native response drained after request cancellation")

    def _effective_deadline(self, scope, initial):
        reason = scope.poll(closing=self._closing.is_set())
        if reason:
            return scope.cancelled_at + IO_TIMEOUT_SECONDS
        return initial

    def _write_all(self, data, scope, deadline):
        offset = 0
        while offset < len(data):
            scope_reason = scope.poll(closing=self._closing.is_set())
            if scope_reason:
                if offset:
                    self._mark_broken("cancelled-partial-request")
                raise NativeCaptureError(scope_reason, "native request cancelled before send completed")
            chunk = data[offset:offset + _MAX_IO_CHUNK]
            transferred = self._overlapped_transfer(chunk, len(chunk), True, scope, deadline,
                                                   cancel_on_scope=True)
            if transferred <= 0:
                self._mark_broken("pipe-write-empty")
                raise OSError("native pipe write returned no bytes")
            offset += transferred

    def _read_exact(self, count, scope, deadline):
        if count == 0:
            return b""
        output = bytearray()
        while len(output) < count:
            self._check_pipe_state(scope)
            available = wintypes.DWORD()
            with self._handle_lock:
                handle = self._handle
                if self._closed or self._broken or not handle:
                    raise NativeCaptureError("stale", "native channel is closed or broken")
                ok = self._kernel.PeekNamedPipe(handle, None, 0, None, ctypes.byref(available), None)
            if not ok:
                error = ctypes.get_last_error()
                raise ctypes.WinError(error)
            if available.value == 0:
                effective = self._effective_deadline(scope, deadline)
                if time.monotonic() >= effective:
                    if scope.cancel_reason:
                        self._mark_broken("cancel-drain-timeout")
                        raise NativeCaptureError(scope.cancel_reason, "native response could not be drained before timeout")
                    self._mark_broken("native-read-timeout")
                    raise NativeCaptureError("native-timeout", "native response exceeded the two second I/O deadline")
                self._closing.wait(min(.01, max(0.0, effective - time.monotonic())))
                continue
            amount = min(count - len(output), int(available.value), _MAX_IO_CHUNK)
            chunk = self._overlapped_transfer(None, amount, False, scope, deadline, cancel_on_scope=False)
            if not chunk:
                self._mark_broken("pipe-read-empty")
                raise OSError("native pipe read returned no bytes")
            output.extend(chunk)
        return bytes(output)

    def _check_pipe_state(self, scope):
        if self._closing.is_set():
            self._mark_broken("channel-closed-during-rpc")
            raise NativeCaptureError("closed", "native channel is closing")
        with self._state_lock:
            if self._broken:
                raise NativeCaptureError("stale", "native channel is broken and cannot be reused")
            if self._closed:
                raise NativeCaptureError("closed", "native channel is closed")
        scope.poll(closing=self._closing.is_set())

    def _overlapped_transfer(self, data, count, write, scope, deadline, *, cancel_on_scope):
        overlap = _OVERLAPPED()
        transferred = wintypes.DWORD()
        storage = ctypes.create_string_buffer(data, len(data)) if write else ctypes.create_string_buffer(count)
        event = self._kernel.CreateEventW(None, True, False, None)
        if not event:
            raise ctypes.WinError(ctypes.get_last_error())
        overlap.hEvent = event
        pending = False
        try:
            with self._handle_lock:
                if self._closing.is_set() or self._closed or self._broken:
                    raise NativeCaptureError("closed", "native channel is closing")
                handle = self._handle
                if write:
                    started = self._kernel.WriteFile(handle, storage, count, ctypes.byref(transferred), ctypes.byref(overlap))
                else:
                    started = self._kernel.ReadFile(handle, storage, count, ctypes.byref(transferred), ctypes.byref(overlap))
                if not started:
                    error = ctypes.get_last_error()
                    if error != 997:  # ERROR_IO_PENDING
                        raise ctypes.WinError(error)
                    pending = True
            if started:
                if write:
                    return int(transferred.value)
                return bytes(storage.raw[:transferred.value])
            while True:
                reason = scope.poll(closing=self._closing.is_set())
                if self._closing.is_set():
                    raise NativeCaptureError("closed", "native channel is closing")
                if reason and cancel_on_scope:
                    self._mark_broken("cancelled-during-request-write")
                    raise NativeCaptureError(reason, "native pipe write cancelled")
                effective_deadline = self._effective_deadline(scope, deadline)
                remaining = effective_deadline - time.monotonic()
                if remaining <= 0:
                    self._mark_broken("native-io-timeout")
                    if scope.cancel_reason:
                        raise NativeCaptureError(scope.cancel_reason, "native pipe operation timed out while draining")
                    raise NativeCaptureError("native-timeout", "native pipe I/O exceeded its two second deadline")
                wait_ms = min(20, max(1, int(remaining * 1000)))
                waited = self._kernel.WaitForSingleObject(event, wait_ms)
                if waited == 0:
                    break
                if waited not in (0x102,):  # WAIT_TIMEOUT
                    raise OSError("native pipe wait failed")
            if not self._kernel.GetOverlappedResult(handle, ctypes.byref(overlap), ctypes.byref(transferred), False):
                error = ctypes.get_last_error()
                pending = error == 996  # ERROR_IO_INCOMPLETE is not a terminal result.
                raise ctypes.WinError(error)
            pending = False
            if write:
                return int(transferred.value)
            return bytes(storage.raw[:transferred.value])
        finally:
            if pending:
                self._cancel_and_drain(handle, overlap, transferred)
            self._kernel.CloseHandle(event)

    def _cancel_and_drain(self, handle, overlap, transferred):
        # The caller holds _io_lock and owns the OVERLAPPED, event and buffer.
        # close() can request cancellation but cannot close the pipe handle yet.
        completed = False
        try:
            self._kernel.CancelIoEx(handle, ctypes.byref(overlap))
            # ERROR_NOT_FOUND also races normal completion. Only this operation's
            # event and terminal status establish that Windows has released it.
            if self._kernel.WaitForSingleObject(overlap.hEvent, _CANCEL_COMPLETION_MS) == 0:
                ok = self._kernel.GetOverlappedResult(handle, ctypes.byref(overlap),
                                                      ctypes.byref(transferred), False)
                completed = bool(ok) or ctypes.get_last_error() != 996
        finally:
            if not completed:
                # Unwinding could free memory still owned by the kernel. Stop
                # this dedicated worker with its buffers alive; the host sees EOF.
                os._exit(70)

    def _protocol_broken(self, reason):
        self._mark_broken(reason)
        raise NativeCaptureError("protocol-error", reason)

    def _mark_broken(self, reason):
        with self._state_lock:
            self._broken = True
            if not self._broken_reason:
                self._broken_reason = str(reason)[:128]

    def close(self):
        self._closing.set()
        # Cancel pending overlapped I/O before waiting for the serialized call.
        with self._handle_lock:
            handle = self._handle
            if handle and not self._closed:
                self._kernel.CancelIoEx(handle, None)
        with self._io_lock:
            with self._handle_lock:
                if self._handle and not self._closed:
                    self._kernel.CloseHandle(self._handle)
                    self._handle = None
                with self._state_lock:
                    self._closed = True

    shutdown = close

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback):
        self.close()


__all__ = ["NativeCaptureError", "NativeCaptureProxy"]
