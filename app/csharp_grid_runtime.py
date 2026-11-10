"""Own one persistent C# grid backend and time only its numerical searches."""
from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass, field
import math
import threading
from uuid import uuid4

from csharp_grid_backend import GridBackend, GridFailure


@dataclass
class _Request:
    serial: int
    calls: list = field(default_factory=list)
    failure: tuple[type[Exception], str] | None = None


class CSharpGridRuntime:
    """Serialize Python fit work around one lazy, persistent ``GridBackend``.

    ``request_scope`` is the outer unit of work, typically one S1 runtime job.
    It installs the numerical search callable for every solver in that worker.
    Only invoking the callable opens a timed backend request. All searches in
    the same outer job share diagnostics and failure state.
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
        self._request = _Request(0)
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
            return tuple(dict(call) for call in self._request.calls)

    @property
    def request_failed(self):
        """Whether the current or most recent outer request saw a grid failure."""
        with self._condition:
            return self._request.failure is not None

    def _acquire_request(self, cancelled):
        with self._condition:
            while True:
                if self._closing.is_set():
                    raise GridFailure("grid-runtime-closed")
                if cancelled is not None and cancelled():
                    raise InterruptedError("grid-cancelled")
                if not self._active:
                    self._active = True
                    self._active_thread = threading.get_ident()
                    self._request = _Request(self._request.serial + 1)
                    return self._request
                self._condition.wait(.05)

    def _release_request(self):
        with self._condition:
            self._active = False
            self._active_thread = None
            self._condition.notify_all()

    @contextmanager
    def request_scope(self, cancelled=None):
        """Run one serialized outer request and yield its combined cancel check.

        Ordinary Qt work retains its own lifetime. A failed native search
        invalidates the whole job, even if a candidate loop catches its error.
        """
        outer_cancelled = getattr(self._local, "cancelled", None)
        if outer_cancelled is not None:
            # A nested callback may itself call outer_cancelled.
            def nested_cancelled():
                return outer_cancelled() or (cancelled is not None and cancelled())

            yield nested_cancelled
            self._check_request(self._request, nested_cancelled)
            return

        request = self._acquire_request(cancelled)

        def combined_cancel():
            return (request.failure is not None or self._closing.is_set()
                    or (cancelled is not None and cancelled()))

        self._local.cancelled = combined_cancel
        try:
            from fast_structure_alignment import using_grid_backend
            with using_grid_backend(self._search):
                yield combined_cancel
            self._check_request(request, combined_cancel)
        finally:
            del self._local.cancelled
            self._release_request()

    @staticmethod
    def _check_request(request, cancelled):
        if request.failure is not None:
            error_type, message = request.failure
            raise error_type(message)
        if cancelled():
            raise InterruptedError("grid-cancelled")

    def _search(self, points, prepared, scale, prior, cancelled):
        outer_cancelled = self._local.cancelled
        request = self._request
        try:
            self._check_request(request, outer_cancelled)
            if self._client is None:
                self._client = GridBackend(self.executable, self.cache_root, event_sink=self.event_sink)
            client = self._client
            with client.request(self._session, request.serial, self.timeout_seconds, outer_cancelled):
                try:
                    return client(points, prepared, scale, prior, cancelled)
                finally:
                    with self._condition:
                        request.calls.extend(client.calls)
        except Exception as error:
            with self._condition:
                if request.failure is None:
                    # Keep the reason without retaining frame/array tracebacks.
                    kind = InterruptedError if isinstance(error, InterruptedError) else GridFailure
                    request.failure = (kind, str(error))
            raise

    def fit_scope(self, cancelled=None):
        """Reuse the outer request, or open one for a standalone fit."""
        return self.request_scope(cancelled)

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
