"""Native capture adapter tests using an in-process Windows named-pipe peer.

The peer only returns synthetic metadata and pixel bytes. These tests never
discover or capture a real window and never start the game or product client.
"""
from __future__ import annotations

import ctypes
from ctypes import wintypes
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch
import uuid


ROOT = Path(__file__).resolve().parents[1]
for directory in (ROOT / "app", ROOT / "experiments" / "qt_quick", ROOT / "hybrid" / "python"):
    if str(directory) not in sys.path:
        sys.path.insert(0, str(directory))

from client_capture import ClientCaptureProvider
from client_region_capture import CaptureContextChanged, ClientCaptureContext
from native_capture import NativeCaptureError, NativeCaptureProxy
from runtime_session import LiveRuntimeSession, RuntimeSession


_CONTEXT = dict(fullClientSize=[3, 2], clientScreenBounds=[101, 202, 104, 204], dpi=144)


class _RawReply:
    def __init__(self, data):
        self.data = data


class _NamedPipePeer:
    """Small real named-pipe fixture; all response content is synthetic."""

    def __init__(self, handler, *, read_gate=None, input_buffer=65536):
        if os.name != "nt":
            raise unittest.SkipTest("named-pipe transport tests require Windows")
        self.kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        self._declare_api()
        self.name = "xingtu-native-test-" + uuid.uuid4().hex
        self.path = "\\\\.\\pipe\\" + self.name
        self.handle = self.kernel.CreateNamedPipeW(self.path, 3, 0, 1, 65536, input_buffer, 0, None)
        if self.handle == wintypes.HANDLE(-1).value:
            raise ctypes.WinError(ctypes.get_last_error())
        self.handler = handler
        self.read_gate = read_gate
        self.requests = []
        self.errors = []
        self.stopped = threading.Event()
        self.connected = threading.Event()
        self.received = threading.Event()
        self.thread = threading.Thread(target=self._serve, name="native-pipe-fixture", daemon=True)
        self.thread.start()

    def _declare_api(self):
        k = self.kernel
        k.CreateNamedPipeW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD,
            wintypes.DWORD, wintypes.DWORD, wintypes.DWORD, wintypes.DWORD, wintypes.LPVOID]
        k.CreateNamedPipeW.restype = wintypes.HANDLE
        k.ConnectNamedPipe.argtypes = [wintypes.HANDLE, wintypes.LPVOID]
        k.ConnectNamedPipe.restype = wintypes.BOOL
        k.ReadFile.argtypes = [wintypes.HANDLE, wintypes.LPVOID, wintypes.DWORD,
            ctypes.POINTER(wintypes.DWORD), wintypes.LPVOID]
        k.ReadFile.restype = wintypes.BOOL
        k.WriteFile.argtypes = [wintypes.HANDLE, wintypes.LPCVOID, wintypes.DWORD,
            ctypes.POINTER(wintypes.DWORD), wintypes.LPVOID]
        k.WriteFile.restype = wintypes.BOOL
        k.DisconnectNamedPipe.argtypes = [wintypes.HANDLE]
        k.DisconnectNamedPipe.restype = wintypes.BOOL
        k.CloseHandle.argtypes = [wintypes.HANDLE]
        k.CloseHandle.restype = wintypes.BOOL

    def _read_exact(self, size):
        data = bytearray()
        while len(data) < size:
            storage = ctypes.create_string_buffer(size - len(data))
            received = wintypes.DWORD()
            if not self.kernel.ReadFile(self.handle, storage, len(storage), ctypes.byref(received), None):
                raise ctypes.WinError(ctypes.get_last_error())
            if received.value == 0:
                raise EOFError("client pipe closed")
            data.extend(storage.raw[:received.value])
        return bytes(data)

    def _write_all(self, data):
        offset = 0
        while offset < len(data):
            chunk = data[offset:offset + 16384]
            storage = ctypes.create_string_buffer(chunk, len(chunk))
            sent = wintypes.DWORD()
            if not self.kernel.WriteFile(self.handle, storage, len(chunk), ctypes.byref(sent), None):
                raise ctypes.WinError(ctypes.get_last_error())
            if sent.value == 0:
                raise EOFError("peer write returned no bytes")
            offset += sent.value

    def _serve(self):
        try:
            if not self.kernel.ConnectNamedPipe(self.handle, None):
                error = ctypes.get_last_error()
                if error != 535:  # ERROR_PIPE_CONNECTED
                    raise ctypes.WinError(error)
            self.connected.set()
            if self.read_gate is not None:
                self.read_gate.wait()
            while not self.stopped.is_set():
                header = self._read_exact(4)
                size = int.from_bytes(header, "little")
                body = self._read_exact(size)
                request = json.loads(body.decode("utf-8"))
                self.requests.append(request)
                self.received.set()
                reply = self.handler(request)
                if isinstance(reply, _RawReply):
                    self._write_all(reply.data)
                    continue
                if reply is None:
                    continue
                response, pixels = reply
                response_bytes = json.dumps(response, separators=(",", ":")).encode("utf-8")
                self._write_all(len(response_bytes).to_bytes(4, "little") + response_bytes + pixels)
        except (EOFError, OSError) as error:
            if not self.stopped.is_set():
                self.errors.append(error)
        except Exception as error:
            self.errors.append(error)
        finally:
            self.connected.set()

    def close(self):
        if self.handle:
            self.stopped.set()
            if self.read_gate is not None:
                self.read_gate.set()
            self.kernel.DisconnectNamedPipe(self.handle)
            self.kernel.CloseHandle(self.handle)
            self.handle = None
        self.thread.join(timeout=3)


class _SyntheticPeer:
    def __init__(self, *, capture_error=None, error_code=None, delay=0, mismatch_call_id=False):
        self.capture_error = capture_error
        self.error_code = error_code
        self.delay = delay
        self.mismatch_call_id = mismatch_call_id
        self.requests = []
        self.seen = threading.Event()
        self.full_calls = 0

    def __call__(self, request):
        self.requests.append(request)
        self.seen.set()
        if self.delay:
            time.sleep(self.delay)
        operation = request["operation"]
        call_id = request["callId"] + (1 if self.mismatch_call_id else 0)
        error_code = self.error_code if operation == "capture" else None
        if operation == "capture" and self.capture_error:
            error_code = self.capture_error
        if operation == "full_capture":
            self.full_calls += 1
        if error_code:
            response = dict(session=request["session"], requestId=request["requestId"], callId=call_id,
                ok=False, result=None, payloadLength=0,
                error=dict(code=error_code, message="synthetic native error",
                    captureCapability=dict(clientDc="failed", safeFallback="unavailable")
                    if error_code == "capture-safe-fallback-unsupported" else None))
            response["error"].pop("captureCapability", None) if error_code != "capture-safe-fallback-unsupported" else None
            return response, b""
        if operation == "is_window":
            result, pixels = True, b""
        elif operation == "foreground_window":
            result, pixels = 42, b""
        elif operation == "context":
            result, pixels = dict(_CONTEXT), b""
        elif operation == "capture":
            left, top, right, bottom = request["roi"]
            width, height = right - left, bottom - top
            pixels = bytes(range(width * height * 3))
            result = dict(width=width, height=height, context=dict(_CONTEXT))
        elif operation == "full_capture":
            result, pixels = dict(width=3, height=2, context=dict(_CONTEXT)), bytes(range(18))
        else:
            raise AssertionError(operation)
        response = dict(session=request["session"], requestId=request["requestId"], callId=call_id,
            ok=True, result=result, payloadLength=len(pixels))
        return response, pixels


@unittest.skipUnless(os.name == "nt", "named-pipe transport tests require Windows")
class HybridNativeCaptureTests(unittest.TestCase):
    def make_proxy(self, handler):
        peer = _NamedPipePeer(handler)
        proxy = NativeCaptureProxy(peer.name)
        self.addCleanup(peer.close)
        self.addCleanup(proxy.close)
        self.assertTrue(peer.connected.wait(1))
        return proxy, peer

    def test_real_named_pipe_operations_and_existing_provider_contract(self):
        peer_handler = _SyntheticPeer()
        proxy, peer = self.make_proxy(peer_handler)
        provider = ClientCaptureProvider(native=proxy, backend=proxy, full_capture=proxy.full_capture)
        provider.bind(42, (1, 0, 3, 2))
        with proxy.request("scope-a", 15):
            self.assertTrue(provider.enable())
            self.assertIsNone(provider.preflight())
            before_snapshot = len(peer_handler.requests)
            state = provider.snapshot
            self.assertTrue(state["enabled"])
            self.assertEqual(len(peer_handler.requests), before_snapshot)
            captured = provider.capture()
            self.assertEqual(captured.acquisition, "client-dc-roi")
            self.assertEqual(captured.roi_client_bounds, (1, 0, 3, 2))
            self.assertEqual(captured.context, ClientCaptureContext((3, 2), (101, 202, 104, 204), 144))
            self.assertEqual(captured.frame_bgr.shape, (2, 2, 3))
            self.assertEqual(captured.frame_bgr.tolist(), [[[0, 1, 2], [3, 4, 5]], [[6, 7, 8], [9, 10, 11]]])
            self.assertFalse(captured.frame_bgr.flags.writeable)
            frame, bounds = proxy.full_capture(42)
            self.assertEqual(frame.shape, (2, 3, 3))
            self.assertEqual(bounds, (101, 202, 104, 204))
        self.assertEqual([call["callId"] for call in peer_handler.requests],
            list(range(1, len(peer_handler.requests) + 1)))
        self.assertTrue(all((call["session"], call["requestId"]) == ("scope-a", 15)
            for call in peer_handler.requests))

    def test_native_dc_error_uses_original_provider_full_capture_fallback(self):
        fake = _SyntheticPeer(capture_error="native-error")
        proxy, _ = self.make_proxy(fake)
        provider = ClientCaptureProvider(native=proxy, backend=proxy, full_capture=proxy.full_capture)
        provider.bind(42, (1, 0, 3, 2))
        with proxy.request("fallback", 4):
            self.assertTrue(provider.enable())
            captured = provider.capture()
        self.assertEqual(captured.acquisition, "full-frame-fallback-crop")
        self.assertEqual(captured.frame_bgr.shape, (2, 2, 3))
        self.assertEqual(fake.full_calls, 1)

    def test_context_changed_and_safe_fallback_errors_keep_their_runtime_classification(self):
        fake = _SyntheticPeer(capture_error="capture-context-changed")
        proxy, _ = self.make_proxy(fake)
        with proxy.request("context", 2):
            with self.assertRaises(CaptureContextChanged) as caught:
                proxy.capture(42, (0, 0, 2, 1))
        self.assertEqual(caught.exception.code, "capture-context-changed")
        self.assertEqual(fake.full_calls, 0)
        proxy.close()

        fake = _SyntheticPeer(capture_error="capture-safe-fallback-unsupported")
        proxy, _ = self.make_proxy(fake)
        with proxy.request("fallback-error", 3):
            with self.assertRaises(RuntimeError) as caught:
                proxy.capture(42, (0, 0, 2, 1))
        self.assertEqual(caught.exception.code, "capture-safe-fallback-unsupported")
        self.assertEqual(caught.exception.capture_capability["safeFallback"], "unavailable")
        self.assertEqual(caught.exception.capture_diagnostics["captureCapability"],
            caught.exception.capture_capability)

    def test_scope_is_thread_local_and_metadata_does_not_issue_native_queries(self):
        fake = _SyntheticPeer()
        proxy, peer = self.make_proxy(fake)
        self.assertFalse(proxy.metadata["broken"])
        with self.assertRaises(NativeCaptureError) as outside:
            proxy.foreground_window()
        self.assertEqual(outside.exception.code, "binding-unavailable")
        with proxy.request("thread-owner", 1):
            failures = []
            thread = threading.Thread(target=lambda: self._capture_scope_failure(proxy, failures))
            thread.start(); thread.join(timeout=2)
            self.assertFalse(thread.is_alive())
            self.assertEqual(failures, ["binding-unavailable"])
            self.assertEqual(proxy.metadata["nextCallId"], 0)
        self.assertEqual(peer.requests, [])

    @staticmethod
    def _capture_scope_failure(proxy, failures):
        try:
            proxy.foreground_window()
        except NativeCaptureError as error:
            failures.append(error.code)

    def test_cancelled_request_drains_response_then_scope_refuses_more_calls(self):
        fake = _SyntheticPeer(delay=.15)
        proxy, peer = self.make_proxy(fake)
        cancelled = threading.Event()
        result = []

        def caller():
            try:
                with proxy.request("cancel-drain", 9, cancelled=cancelled):
                    try:
                        proxy.capture(42, (0, 0, 2, 1))
                    except NativeCaptureError as error:
                        result.append(error.code)
                    try:
                        proxy.foreground_window()
                    except NativeCaptureError as error:
                        result.append("again:" + error.code)
            except NativeCaptureError as error:
                result.append("scope:" + error.code)

        thread = threading.Thread(target=caller)
        thread.start()
        self.assertTrue(fake.seen.wait(1))
        cancelled.set()
        thread.join(timeout=3)
        self.assertFalse(thread.is_alive())
        self.assertEqual(result, ["cancelled", "again:cancelled"])
        self.assertFalse(proxy.metadata["broken"])
        self.assertEqual(len(peer.requests), 1)

    def test_bad_length_and_half_response_deadline_break_the_channel(self):
        peer = _NamedPipePeer(lambda request: _RawReply(b"\x00\x00\x00\x00"))
        proxy = NativeCaptureProxy(peer.name)
        self.addCleanup(peer.close)
        self.addCleanup(proxy.close)
        with proxy.request("bad-length", 1):
            with self.assertRaises(NativeCaptureError) as caught:
                proxy.is_window(42)
        self.assertEqual(caught.exception.code, "protocol-error")
        self.assertTrue(proxy.metadata["broken"])

        def half_reply(request):
            response = dict(session=request["session"], requestId=request["requestId"],
                callId=request["callId"], ok=True,
                result=dict(width=2, height=1, context=dict(_CONTEXT)), payloadLength=6)
            encoded = json.dumps(response, separators=(",", ":")).encode("utf-8")
            return _RawReply(len(encoded).to_bytes(4, "little") + encoded + b"\x01\x02\x03")

        fake = _NamedPipePeer(half_reply)
        proxy = NativeCaptureProxy(fake.name)
        self.addCleanup(fake.close)
        self.addCleanup(proxy.close)
        with proxy.request("half-frame", 2, deadline=time.monotonic() + .15):
            with self.assertRaises(NativeCaptureError) as caught:
                proxy.capture(42, (0, 0, 2, 1))
        self.assertEqual(caught.exception.code, "deadline-exceeded")
        self.assertTrue(proxy.metadata["broken"])

    def test_close_from_another_thread_wakes_a_half_response_reader(self):
        def half_reply(request):
            response = dict(session=request["session"], requestId=request["requestId"],
                callId=request["callId"], ok=True,
                result=dict(width=2, height=1, context=dict(_CONTEXT)), payloadLength=6)
            encoded = json.dumps(response, separators=(",", ":")).encode("utf-8")
            return _RawReply(len(encoded).to_bytes(4, "little") + encoded + b"\x01")

        fake = _NamedPipePeer(half_reply)
        proxy = NativeCaptureProxy(fake.name)
        self.addCleanup(fake.close)
        self.addCleanup(proxy.close)
        failures = []

        def caller():
            try:
                with proxy.request("close-reader", 1):
                    proxy.capture(42, (0, 0, 2, 1))
            except NativeCaptureError as error:
                failures.append(error.code)

        thread = threading.Thread(target=caller)
        thread.start()
        self.assertTrue(fake.received.wait(1))
        started = time.monotonic()
        proxy.close()
        thread.join(timeout=1)
        self.assertFalse(thread.is_alive())
        self.assertLess(time.monotonic() - started, 1.0)
        self.assertEqual(len(failures), 1)

    def test_pending_write_observes_completion_before_releasing_event(self):
        for completes_before_cancel in (False, True):
            with self.subTest(completes_before_cancel=completes_before_cancel):
                proxy, _ = self.make_proxy(_SyntheticPeer())
                cancelled = threading.Event()
                events, waits = [], []
                class Kernel:
                    def CreateEventW(self, *args):
                        return 42

                    def WriteFile(self, *args):
                        cancelled.set()
                        ctypes.set_last_error(997)  # ERROR_IO_PENDING
                        return False

                    def CancelIoEx(self, *args):
                        events.append("cancel")
                        ctypes.set_last_error(1168 if completes_before_cancel else 0)
                        return not completes_before_cancel

                    def WaitForSingleObject(self, event, milliseconds):
                        events.append(("signalled", event))
                        waits.append(milliseconds)
                        return 0

                    def GetOverlappedResult(self, *args):
                        events.append("completed" if completes_before_cancel else "aborted")
                        ctypes.set_last_error(0 if completes_before_cancel else 995)
                        return completes_before_cancel

                    def CloseHandle(self, event):
                        events.append(("close-event", event))
                        return True

                with patch.object(proxy, "_kernel", Kernel()), proxy.request("pending", 1, cancelled):
                    with self.assertRaises(NativeCaptureError) as failure:
                        proxy.is_window(42)
                self.assertEqual(failure.exception.code, "cancelled")
                self.assertEqual(events, ["cancel", ("signalled", 42),
                    "completed" if completes_before_cancel else "aborted", ("close-event", 42)])
                self.assertTrue(0 < waits[0] <= 2000)

    def test_unconfirmed_cancel_completion_terminates_owned_process(self):
        code = r"""
import ctypes, runpy, sys
from unittest.mock import Mock
runpy.run_path(sys.argv[1])
from native_capture import NativeCaptureProxy, _OVERLAPPED
proxy = NativeCaptureProxy.__new__(NativeCaptureProxy)
proxy._kernel = Mock()
proxy._kernel.WaitForSingleObject.return_value = int(sys.argv[2])
proxy._kernel.GetOverlappedResult.return_value = False
ctypes.set_last_error(996)  # ERROR_IO_INCOMPLETE even if the wait claims success.
proxy._cancel_and_drain(1, _OVERLAPPED(), ctypes.c_ulong())
"""
        for wait_result in (258, 0):
            with self.subTest(wait_result=wait_result):
                run = subprocess.run([sys.executable, "-B", "-c", code, str(Path(__file__).resolve()),
                    str(wait_result)], capture_output=True, text=True, timeout=8)
                self.assertEqual(run.returncode, 70, run.stderr)

    def test_close_cancels_real_kernel_pending_write(self):
        peer = _NamedPipePeer(_SyntheticPeer(), read_gate=threading.Event(), input_buffer=4096)
        self.addCleanup(peer.close)
        proxy = NativeCaptureProxy(peer.name)
        self.addCleanup(proxy.close)
        pending, outcomes = threading.Event(), []
        wait = proxy._kernel.WaitForSingleObject

        def wait_for_event(*arguments):
            pending.set()  # This call is reached only after WriteFile reports ERROR_IO_PENDING.
            return wait(*arguments)

        def caller():
            try:
                with proxy.request("p" * 60000, 1):
                    proxy.is_window(42)
            except NativeCaptureError as error:
                outcomes.append(error.code)

        with patch.object(proxy._kernel, "WaitForSingleObject", wait_for_event):
            thread = threading.Thread(target=caller)
            thread.start()
            try:
                self.assertTrue(pending.wait(1))
                proxy.close()
            finally:
                peer.read_gate.set()
                thread.join(timeout=2)
        self.assertFalse(thread.is_alive())
        self.assertEqual(outcomes, ["closed"])
        self.assertTrue(proxy.metadata["closed"])

    def test_peer_error_mappings_and_identity_mismatch(self):
        fake = _SyntheticPeer(error_code="binding-unavailable")
        proxy, _ = self.make_proxy(fake)
        with proxy.request("binding", 5):
            with self.assertRaises(NativeCaptureError) as caught:
                proxy.capture(42, (0, 0, 2, 1))
        self.assertEqual(caught.exception.code, "binding-unavailable")
        self.assertFalse(proxy.metadata["broken"])

        fake = _SyntheticPeer(mismatch_call_id=True)
        proxy, _ = self.make_proxy(fake)
        with proxy.request("identity", 6):
            with self.assertRaises(NativeCaptureError):
                proxy.is_window(42)
        self.assertTrue(proxy.metadata["broken"])

    def test_live_session_uses_shared_session_and_cannot_accept_recorded_frames(self):
        class FakeProxy:
            full_capture = staticmethod(lambda hwnd: (None, (0, 0, 1, 1)))
            closed = False

            @staticmethod
            def is_window(hwnd):
                return True

            def close(self):
                self.closed = True

        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            resources = root / "resources"
            resources.mkdir()
            proxy = FakeProxy()
            session = LiveRuntimeSession(resources, root / "data", proxy)
            try:
                self.assertIsInstance(session, RuntimeSession)
                self.assertFalse(hasattr(session, "set_frame"))
                self.assertEqual(session.snapshot()["evidenceKind"], "live-client-capture")
                self.assertFalse((resources / "work").exists())
                self.assertFalse((root / "data").exists())
            finally:
                session.close()
            self.assertTrue(proxy.closed)

    def test_client_capture_import_does_not_require_pyside6(self):
        code = r'''import importlib.abc,sys
sys.path[:0]=[r"%s",r"%s",r"%s"]
class BlockQt(importlib.abc.MetaPathFinder):
 def find_spec(self, fullname, path=None, target=None):
  if fullname == "PySide6" or fullname.startswith("PySide6."): raise ImportError("Qt import blocked for this check")
sys.meta_path.insert(0,BlockQt())
import client_capture
assert "PySide6" not in sys.modules
print("qt-free")''' % (ROOT / "app", ROOT / "experiments" / "qt_quick", ROOT / "hybrid" / "python")
        completed = subprocess.run([sys.executable, "-B", "-c", code], cwd=ROOT,
            env=dict(os.environ, PYTHONPATH=os.pathsep.join(str(item) for item in (
                ROOT / "app", ROOT / "experiments" / "qt_quick", ROOT / "hybrid" / "python"))),
            capture_output=True, text=True, timeout=10)
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertIn("qt-free", completed.stdout)


if __name__ == "__main__":
    unittest.main()
