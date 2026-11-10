"""Own one persistent C# grid backend across bounded fit/request scopes."""
from __future__ import annotations

from contextlib import contextmanager
import math
import threading
import time
from uuid import uuid4

from csharp_grid_backend import GridBackend, GridFailure


class CSharpGridRuntime:
    """Serialize Python fit work around one lazy, persistent ``GridBackend``.

    ``request_scope`` is the outer unit of work, typically one S1 runtime job.
    A nested ``fit_scope`` installs the backend in fast_structure_alignment's
    ContextVar without opening another backend request. A standalone
    ``fit_scope`` creates its own request for callers outside that job path.
    """

    def __init__(self, executable, cache_root, timeout_seconds=10, event_sink=None):
        try:
            timeout_seconds = float(timeout_seconds)
        except (TypeError, ValueError) as error:
            raise ValueError("grid runtime timeout must be positive and finite") from error
        if not math.isfinite(timeout_seconds) or timeout_seconds <= 0:
            raise ValueError("grid runtime timeout must be positive and finite")

        self.executable = executable
        self.cache_root = cache_root
        self.timeout_seconds = timeout_seconds
        self.event_sink = event_sink

        self._condition = threading.Condition()
        self._closing = threading.Event()
        self._active = False
        self._active_thread = None
        self._client = None
        self._session = uuid4().hex
        self._request_serial = 0
        self._request_failed = False
        self._local = threading.local()
        self._close_started = False
        self._close_done = False
        self._close_error = None

    @property
    def process_id(self):
        with self._condition:
            client = self._client
        return client.process_id if client is not None else None

    @property
    def calls(self):
        """Return an immutable snapshot of the active or most recent request."""
        with self._condition:
            client = self._client
        if client is None:
            return ()
        return tuple(dict(call) for call in client.calls)

    @property
    def request_failed(self):
        """Whether the current or most recent outer request saw a grid failure."""
        with self._condition:
            client = self._client
            failed = self._request_failed
        return failed or bool(getattr(client, "request_failed", False))

    def _mark_request_failed(self):
        with self._condition:
            self._request_failed = True

    def _raise_if_invalidated(self, state, cancelled=None):
        if self.request_failed:
            raise GridFailure("grid-request-failed")
        if self._closing.is_set():
            raise InterruptedError("grid-cancelled")
        if time.monotonic() >= state["deadline"]:
            raise InterruptedError("grid-timeout")
        if cancelled is not None and cancelled():
            raise InterruptedError("grid-cancelled")

    def _acquire_request(self, deadline, cancelled):
        with self._condition:
            while True:
                if self._closing.is_set():
                    raise GridFailure("grid-runtime-closed")
                if cancelled is not None and cancelled():
                    raise InterruptedError("grid-cancelled")
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise InterruptedError("grid-timeout")
                if not self._active:
                    self._active = True
                    self._active_thread = threading.get_ident()
                    self._request_serial += 1
                    return self._request_serial
                self._condition.wait(min(remaining, .05))

    def _release_request(self):
        with self._condition:
            self._active = False
            self._active_thread = None
            self._condition.notify_all()

    @contextmanager
    def request_scope(self, cancelled=None):
        """Run one serialized outer request and yield its combined cancel check.

        The check covers caller cancellation, this runtime closing, the
        end-to-end deadline (including owner-queue time), and any sticky
        backend failure already seen in this request.
        """
        active_state = getattr(self._local, "request_state", None)
        if active_state is not None:
            # A nested callback can itself derive from combined_cancel. Keep
            # it local to that scope instead of feeding it back into the outer
            # cancellation predicate.
            yield active_state["combined_cancel"]
            return

        deadline = time.monotonic() + self.timeout_seconds
        request_id = self._acquire_request(deadline, cancelled)
        try:
            with self._condition:
                if self._closing.is_set():
                    raise GridFailure("grid-runtime-closed")
                self._request_failed = False
                if self._client is None:
                    self._client = GridBackend(self.executable, self.cache_root, event_sink=self.event_sink)
                client = self._client

            state = {
                "client": client,
                "deadline": deadline,
                "cancelled": cancelled,
            }

            def combined_cancel():
                if self._closing.is_set() or time.monotonic() >= state["deadline"]:
                    return True
                if state["cancelled"] is not None and state["cancelled"]():
                    return True
                return self.request_failed

            state["combined_cancel"] = combined_cancel
            self._local.request_state = state
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise InterruptedError("grid-timeout")
            try:
                with client.request(self._session, request_id, remaining, combined_cancel):
                    yield combined_cancel
            except GridFailure:
                self._mark_request_failed()
                raise
            finally:
                if bool(getattr(client, "request_failed", False)):
                    self._mark_request_failed()
        finally:
            if hasattr(self._local, "request_state"):
                del self._local.request_state
            self._release_request()

    @contextmanager
    def fit_scope(self, cancelled=None):
        """Install the grid backend for one whole fit, reusing an outer request."""
        import fast_structure_alignment as fast

        state = getattr(self._local, "request_state", None)
        if state is None:
            with self.request_scope(cancelled) as combined_cancel:
                state = self._local.request_state
                try:
                    with fast.using_grid_backend(state["client"]):
                        yield combined_cancel
                    self._raise_if_invalidated(state, cancelled)
                except GridFailure:
                    self._mark_request_failed()
                    raise
            return

        try:
            with fast.using_grid_backend(state["client"]):
                yield state["combined_cancel"]
            self._raise_if_invalidated(state, cancelled)
        except GridFailure:
            self._mark_request_failed()
            raise

    def close(self):
        """Invalidate waiters and active grid work, drain it, then close once."""
        ident = threading.get_ident()
        with self._condition:
            if self._active and self._active_thread == ident:
                raise RuntimeError("cannot close grid runtime from inside its active request")
            if self._close_started:
                while not self._close_done:
                    self._condition.wait()
                error = self._close_error
                if error is not None:
                    raise error
                return

            self._close_started = True
            self._closing.set()
            self._condition.notify_all()
            while self._active:
                self._condition.wait()
            client = self._client

        error = None
        try:
            if client is not None:
                client.close()
        except BaseException as caught:
            error = caught
        finally:
            with self._condition:
                self._close_error = error
                self._close_done = True
                self._condition.notify_all()
        if error is not None:
            raise error
