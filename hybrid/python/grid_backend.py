"""Persistent protocol-2 C# Cartesian search, scoped to an outer Python request.

Only numerical candidates cross this boundary. A failed search raises; it never
substitutes Python candidates or grants pose, identity, or display authority.
"""
from __future__ import annotations

from collections import OrderedDict, deque
from contextlib import contextmanager
import json
import math
from pathlib import Path
import queue
import subprocess
import threading
import time
from uuid import uuid4

import numpy as np


class GridFailure(RuntimeError):
    pass


class GridBackend:
    def __init__(self, executable, cache_root, *, clock=time.monotonic, event_sink=None):
        self.executable = Path(executable).resolve()
        self.cache_root = Path(cache_root).resolve() / ("grid-" + uuid4().hex)
        self.cache_root.mkdir(parents=True, exist_ok=False)
        self._clock = clock
        self._event_sink = event_sink
        self._owner = threading.Lock()
        self._closing = threading.Event()
        self._closed = False
        self._context = None
        self._failure = None
        self._process = None
        self._reader = self._stderr_reader = None
        self._messages = None
        self._stderr = deque(maxlen=8)
        self._references = OrderedDict()
        self._reference_serial = self._search_serial = 0
        self.calls = []
        self.events = deque(maxlen=32)

    @property
    def process_id(self):
        return self._process.pid if self._process is not None else None

    @contextmanager
    def request(self, session, request_id, timeout_seconds, cancelled=lambda: False):
        if not isinstance(session, str) or not session or type(request_id) is not int:
            raise ValueError("grid request requires a session and integer id")
        if not math.isfinite(timeout_seconds) or timeout_seconds <= 0:
            raise ValueError("grid request timeout must be positive and finite")
        if not self._owner.acquire(blocking=False):
            raise GridFailure("grid-request-busy")
        try:
            if self._closing.is_set():
                raise GridFailure("grid-backend-closed")
            self._context = (session, request_id, self._clock() + timeout_seconds, cancelled)
            self._failure = None
            self.calls.clear()
            self.events.clear()
            yield self
            # Existing Python candidate loops may catch a failed candidate.
            # A failed cross-process computation still fails its outer request;
            # a later branch cannot turn missing C# evidence into success.
            self._check()
        finally:
            self._context = None
            self._owner.release()

    def _cancel_reason(self, extra=None):
        if self._closing.is_set():
            return "closed"
        if self._context is None:
            raise GridFailure("grid-outside-request")
        if self._context[3]() or (extra is not None and extra()):
            return "cancelled"
        if self._clock() >= self._context[2]:
            return "timeout"
        return None

    def _check(self, extra=None):
        if self._failure is not None:
            raise self._failure
        reason = self._cancel_reason(extra)
        if reason:
            raise InterruptedError("grid-" + reason)

    def _send(self, value):
        try:
            self._process.stdin.write(json.dumps(value, allow_nan=False, separators=(",", ":")) + "\n")
            self._process.stdin.flush()
        except (OSError, ValueError, AttributeError) as error:
            raise GridFailure("grid-transport-closed") from error

    def _start(self):
        if self._process is not None:
            if self._process.poll() is None:
                return
            self._retire()
        self._check()
        if not self.executable.is_file():
            raise GridFailure("grid-executable-missing")
        process = subprocess.Popen([str(self.executable)], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=subprocess.PIPE, text=True, encoding="utf-8", errors="strict", bufsize=1,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        self._process = process
        messages = self._messages = queue.Queue(maxsize=16)

        def enqueue(value):
            # Progress is diagnostic. Keep terminal/transport status bounded too,
            # rather than blocking the pipe reader during cancellation/teardown.
            try:
                messages.put_nowait(value)
            except queue.Full:
                if value.get("event") == "progress":
                    return
                messages.get_nowait()
                messages.put_nowait(value)

        def read():
            try:
                while line := process.stdout.readline(1024 * 1024):
                    if not line.endswith("\n"):
                        raise GridFailure("grid-response-too-large")
                    reply = json.loads(line)
                    if not isinstance(reply, dict):
                        raise GridFailure("grid-response-not-object")
                    enqueue(reply)
            except Exception as error:
                enqueue(dict(event="transport-error", reason=type(error).__name__))
            finally:
                enqueue(dict(event="transport-closed"))

        def read_errors():
            try:
                while chunk := process.stderr.read(2048):
                    self._stderr.append(chunk)
            except (OSError, ValueError):
                pass

        self._reader = threading.Thread(target=read, name="hybrid-grid-output")
        self._stderr_reader = threading.Thread(target=read_errors, name="hybrid-grid-errors")
        self._reader.start()
        self._stderr_reader.start()
        ready_deadline = min(self._context[2], self._clock() + 5)
        while True:
            self._check()
            if self._clock() >= ready_deadline:
                raise GridFailure("grid-startup-timeout")
            try:
                ready = messages.get(timeout=.01)
                break
            except queue.Empty:
                pass
        if ready.get("ready") is not True or ready.get("protocol") != 2 or ready.get("pid") != process.pid:
            raise GridFailure("grid-invalid-ready")

    def _reference(self, prepared):
        key = id(prepared)
        if key not in self._references:
            distance = np.asarray(prepared.distance)
            if distance.dtype != np.dtype("uint16") or tuple(distance.shape) != tuple(prepared.shape):
                raise GridFailure("grid-invalid-prepared-distance")
            self._reference_serial += 1
            path = self.cache_root / f"reference-{self._reference_serial}.u16"
            path.write_bytes(distance.astype("<u2", copy=False).tobytes(order="C"))
            self._references[key] = (prepared, path)
            if len(self._references) > 4:
                _, (_, obsolete) = self._references.popitem(last=False)
                obsolete.unlink()  # Only this instance's reproducible distance-field copy.
        self._references.move_to_end(key)
        return self._references[key][1]

    def __call__(self, points, prepared, scale, prior, cancelled):
        self._check(cancelled)
        try:
            self._start()
            path = self._reference(prepared)
            self._check(cancelled)
            self._search_serial += 1
            serial = self._search_serial
            session, request_id, deadline, _ = self._context
            began = self._clock()
            self._send(dict(op="search", session=session, id=serial, referencePath=str(path),
                timeoutMs=max(1, int((deadline - began) * 1000)), input=dict(points=np.asarray(points).tolist(),
                width=int(prepared.shape[1]), height=int(prepared.shape[0]), scale=float(scale),
                prior=None if prior is None else np.asarray(prior).tolist(), cancelled=False)))
            cancel_reason = None
            cancel_deadline = None
            while True:
                reason = self._cancel_reason(cancelled)
                if reason and cancel_reason is None:
                    cancel_reason = reason
                    cancel_deadline = self._clock() + 1
                    self._send(dict(op="cancel", session=session, id=serial))
                if cancel_deadline is not None and self._clock() >= cancel_deadline:
                    raise GridFailure("grid-cancel-response-timeout")
                try:
                    reply = self._messages.get(timeout=.005)
                except queue.Empty:
                    continue
                if reply.get("event") in ("transport-closed", "transport-error"):
                    raise GridFailure("grid-process-exited")
                if (reply.get("session"), reply.get("id")) != (session, serial):
                    continue
                self.events.append({key: reply[key] for key in
                    ("event", "phase", "completedPoints", "session", "id", "ok", "reason") if key in reply})
                if self._event_sink is not None and reply.get("event") in ("started", "progress"):
                    self._event_sink(dict(event=reply["event"], session=session, id=request_id, gridId=serial,
                        phase=reply.get("phase"), completedPoints=reply.get("completedPoints"), processId=self.process_id))
                if reply.get("event") != "result":
                    continue
                self.calls.append(dict(session=session, requestId=request_id, gridId=serial,
                    roundTripMs=(self._clock() - began) * 1000,
                    **{key: reply[key] for key in ("ok", "reason", "kernelMs", "loadMs", "elapsedMs") if key in reply},
                    candidateCount=len(reply.get("rows", []))))
                self._check(cancelled)
                if not reply.get("ok"):
                    if reply.get("reason") in ("cancelled", "timeout"):
                        raise InterruptedError("grid-" + reply["reason"])
                    raise GridFailure("grid-" + str(reply.get("reason", "failed")))
                return self._candidate_rows(reply.get("rows"), scale)
        except (GridFailure, OSError, ValueError) as error:
            # Includes InterruptedError. Retire on cancellation too, so a
            # startup cancellation cannot leave an unread ready response.
            self._failure = error
            try:
                self._retire()
            except GridFailure as cleanup_error:
                error.add_note(str(cleanup_error))
            raise

    @staticmethod
    def _candidate_rows(rows, scale):
        if not isinstance(rows, list) or len(rows) > 8:
            raise GridFailure("grid-invalid-candidates")
        result = []
        for row in rows:
            if not isinstance(row, list) or len(row) != 3:
                raise GridFailure("grid-invalid-candidate")
            score, candidate_scale, translation = row
            xy = np.asarray(translation, np.float64)
            if (xy.shape != (2,) or not np.isfinite(xy).all() or not math.isfinite(score)
                    or not 0 <= score <= 1 or candidate_scale != float(scale)):
                raise GridFailure("grid-invalid-candidate")
            result.append((float(score), candidate_scale, xy))
        return result

    def _retire(self):
        process = self._process
        if process is None:
            return
        forced = False
        try:
            if process.poll() is None:
                try:
                    self._send(dict(op="stop"))
                    process.stdin.close()
                except (GridFailure, OSError):
                    pass
                try:
                    process.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    forced = True
                    process.kill()  # Exactly the process owned by this backend; no broad process search.
                    process.wait(timeout=2)
            for thread in (self._reader, self._stderr_reader):
                if thread is not None:
                    thread.join(timeout=2)
            for stream in (process.stdin, process.stdout, process.stderr):
                try:
                    stream.close()
                except OSError:
                    # A killed child can leave TextIOWrapper's final flush
                    # reporting broken-pipe/invalid-handle. It is already dead;
                    # retain the original computation/transport failure.
                    pass
        finally:
            self._process = None
        if forced:
            raise GridFailure("grid-forced-stop")

    def close(self):
        self._closing.set()
        with self._owner:
            if self._closed:
                return
            self._retire()
            for _, path in self._references.values():
                path.unlink(missing_ok=True)
            self._references.clear()
            self.cache_root.rmdir()
            self._closed = True
