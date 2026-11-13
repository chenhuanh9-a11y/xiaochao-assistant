"""Bounded two-lane work; only the calling owner may consume or commit evidence.

No map qualification, identity mutation, image publication or withdrawal lives
here. Payloads cross by reference, not deep copy: producers must keep arguments
stable until a synchronous callback returns, and transfer terminal results.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from queue import Empty, Full, Queue
import threading
import time


@dataclass(frozen=True)
class RecognitionObservation:
    snapshot: object
    floor: str
    resource_key: str

    def matches(self, evidence):
        """Optional owner-side DTO scope check, never an identity decision."""
        return bool(isinstance(evidence, RecognitionEvidence) and evidence.map_id
            and evidence.floor == self.floor
            and evidence.frame_key == self.snapshot.frame_key
            and evidence.resource_key == self.resource_key)


@dataclass(frozen=True)
class RecognitionEvidence:
    map_id: str
    floor: str
    frame_key: str
    resource_key: str
    payload: object


class _LaneRetired(Exception):
    pass


@dataclass(eq=False)
class _Call:
    lane: str
    kind: str
    args: tuple
    kwargs: dict
    ready: threading.Event = field(default_factory=threading.Event)
    lock: object = field(default_factory=threading.Lock)
    value: object = None
    error: object = None
    claimed: bool = False

    def resolve(self, value=None, error=None):
        with self.lock:
            if not self.ready.is_set():
                self.value, self.error = value, error
                self.ready.set()


class _Run:
    def __init__(self):
        self.lock = threading.RLock()
        self.inbox = Queue(maxsize=2)
        self.retired = threading.Event()
        self.stops = {name: threading.Event() for name in ('candidate', 'structure')}
        self.emit_locks = {name: threading.Lock() for name in self.stops}
        self.calls = set()
        self.done = set()
        self.handled = set()
        self.reason = None
        self.winner = None
        self.lanes = {name: dict(state='not-started', result=None, error=None) for name in self.stops}

    def cancel_lane(self, name):
        with self.lock:
            self.stops[name].set()
            self.handled.add(name)
            for call in self.calls:
                if call.lane == name:
                    call.resolve(error=_LaneRetired())

    def retire(self, reason, winner=None):
        with self.lock:
            if not self.retired.is_set():
                self.reason, self.winner = reason, winner
                self.retired.set()
            # A new opening/close also retires a previous winner's remaining
            # computation, without withdrawing its externally owned material.
            for name, stopped in self.stops.items():
                if name != winner or reason != 'identity-committed':
                    stopped.set()
            for call in self.calls:
                call.resolve(error=_LaneRetired())
            while True:
                try:
                    self.inbox.get_nowait()
                except Empty:
                    break

    def emit(self, lane, kind, *args, **kwargs):
        # Even an incorrectly concurrent producer gets at most one outstanding
        # payload per lane, plus the other lane's one outstanding payload.
        serial = self.emit_locks[lane]
        while not serial.acquire(timeout=.01):
            if self.retired.is_set() or self.stops[lane].is_set():
                raise _LaneRetired()
        call = _Call(lane, kind, args, kwargs)
        try:
            with self.lock:
                if self.retired.is_set() or self.stops[lane].is_set():
                    raise _LaneRetired()
                self.calls.add(call)
            while True:
                if self.retired.is_set() or self.stops[lane].is_set():
                    raise _LaneRetired()
                try:
                    self.inbox.put(call, timeout=.01)
                    break
                except Full:
                    pass
            while not call.ready.wait(.01):
                if self.retired.is_set() or self.stops[lane].is_set():
                    raise _LaneRetired()
            if call.error is not None:
                raise call.error
            return call.value
        finally:
            with self.lock:
                self.calls.discard(call)
            serial.release()

    def snapshot(self):
        with self.lock:
            return dict(status=self.reason, winner=self.winner,
                lanes={name: dict(row, cancelRequested=self.stops[name].is_set())
                    for name, row in self.lanes.items()})


class RecognitionRace:
    """One engine's two latest-work queues, with callbacks on run's caller.

    A lane is ``lane(observation, cancelled, progress, entry)``. Its progress
    and entry functions synchronously return the owner's callback result.
    Owner callbacks receive ``(lane_name, *args, **kwargs)``; terminal/error
    callbacks receive ``(lane_name, returned_value/exception)``.

    Callback truthiness has no authority. Only identity_committed(), evaluated
    on the owner after consumption, can name a winner and cancel the other lane.
    The owner must still validate current frame/floor/resource and full identity
    qualification before committing (RecognitionObservation.matches is merely
    an optional DTO scope helper). Callbacks must not block on lane completion.
    Dispatch is claimed under the retirement lock after external cancel/clock
    hooks. Already claimed callbacks are in flight and may finish after a
    nonwaiting close; their runtime writes still require owner context checks.

    New runs supersede old runs. Each queue permits one running and one latest
    pending task, so even uncooperative old work cannot create detached workers
    or an unbounded executor backlog. close/cancel never joins a worker.
    """
    def __init__(self, *, clock=time.monotonic):
        from s1_private_experiment import LatestEvidenceQueue
        self.clock = clock
        self._lock = threading.RLock()
        self._queues = {name: LatestEvidenceQueue(thread_name='recognition-'+name)
            for name in ('candidate', 'structure')}
        self._sessions = set()
        self._active = None
        self._sequence = 0
        self._next_admission = 0
        self._cancel_generation = 0
        self._closed = False

    @property
    def busy(self):
        return any(queue.busy for queue in self._queues.values())

    def cancel(self):
        """Retire accepted work without closing this reusable engine's queues."""
        with self._lock:
            # Explicit cancellation revokes preflights independently of normal
            # submissions, which must remain able to supersede older work.
            self._cancel_generation += 1
            for session in tuple(self._sessions):
                session.retire('cancelled')
            if self._active is not None:
                self._active.retire('cancelled')
            for queue in self._queues.values():
                queue.discard_pending()

    def close(self):
        with self._lock:
            self._closed = True
            for session in tuple(self._sessions):
                session.retire('closed')
            if self._active is not None:
                self._active.retire('closed')
            for queue in self._queues.values():
                queue.shutdown()

    def cancel_lane(self, name):
        if name not in self._queues:
            raise ValueError('unknown-recognition-lane')
        with self._lock:
            if self._active is not None:
                self._active.cancel_lane(name)

    def _finished(self, session, name):
        with session.lock:
            session.done.add(name)
            done = len(session.done) == 2
        if done:
            with self._lock:
                self._sessions.discard(session)

    def _discarded(self, session, name):
        with session.lock:
            session.lanes[name]['state'] = 'discarded'
            session.handled.add(name)
        self._finished(session, name)

    def _work(self, session, name, observation, lane):
        try:
            with session.lock:
                if session.retired.is_set() or session.stops[name].is_set():
                    session.lanes[name]['state'] = 'discarded'
                    session.handled.add(name)
                    return
                session.lanes[name]['state'] = 'running'
            try:
                value = lane(observation, session.stops[name].is_set,
                    lambda *args, **kwargs: session.emit(name, 'progress', *args, **kwargs),
                    lambda *args, **kwargs: session.emit(name, 'entry', *args, **kwargs))
                with session.lock:
                    session.lanes[name].update(state='completed', result=value)
                session.emit(name, 'terminal', value)
            except _LaneRetired:
                with session.lock:
                    if session.lanes[name]['state'] == 'running':
                        session.lanes[name]['state'] = ('cancelled' if session.stops[name].is_set() else 'retired')
                    session.handled.add(name)
            except BaseException as error:
                with session.lock:
                    session.lanes[name].update(state='error', error=error)
                try:
                    session.emit(name, 'error', error)
                except _LaneRetired:
                    pass
        finally:
            self._finished(session, name)

    def run(self, observation, candidate, structure, *, identity_committed,
            on_progress=None, on_entry=None, on_terminal=None, on_error=None,
            cancelled=None, deadline=None):
        cancelled = cancelled or (lambda: False)
        session = _Run()
        with self._lock:
            if self._closed:
                session.retire('closed')
                return session.snapshot()
            self._next_admission += 1
            admission_sequence = self._next_admission
            cancel_generation = self._cancel_generation
        if cancelled():
            session.retire('cancelled')
            return session.snapshot()
        if deadline is not None and self.clock() >= deadline:
            session.retire('unresolved')
            return session.snapshot()
        if identity_committed() is True:
            session.retire('identity-committed')
            return session.snapshot()
        with self._lock:
            if self._closed:
                session.retire('closed')
                return session.snapshot()
            if cancel_generation != self._cancel_generation:
                session.retire('cancelled')
                return session.snapshot()
            if admission_sequence <= self._sequence:
                # A newer observation already submitted while this older
                # preflight was outside the lock. Do not retire its session.
                session.retire('superseded')
                return session.snapshot()
            for previous in tuple(self._sessions):
                previous.retire('superseded')
            if self._active is not None:
                self._active.retire('superseded')
            self._active = session
            self._sessions.add(session)
            self._sequence = admission_sequence
            for name, lane in (('candidate', candidate), ('structure', structure)):
                session.lanes[name]['state'] = 'queued'
                self._queues[name].submit(
                    lambda name=name, lane=lane: self._work(session, name, observation, lane),
                    lambda name=name: self._discarded(session, name), sequence=admission_sequence)
        callbacks = dict(progress=on_progress, entry=on_entry, terminal=on_terminal, error=on_error)
        try:
            while not session.retired.is_set():
                if identity_committed() is True:
                    session.retire('identity-committed')
                    break
                if cancelled():
                    session.retire('cancelled')
                    break
                if deadline is not None and self.clock() >= deadline:
                    session.retire('unresolved')
                    break
                with session.lock:
                    if len(session.handled) == 2:
                        session.retire('unresolved')
                        break
                timeout = min(.01, max(0., deadline-self.clock())) if deadline is not None else .01
                try:
                    call = session.inbox.get(timeout=timeout)
                except Empty:
                    continue
                is_cancelled = cancelled()
                expired = deadline is not None and self.clock() >= deadline
                with session.lock:
                    stale = session.retired.is_set() or session.stops[call.lane].is_set()
                    if not (stale or is_cancelled or expired):
                        call.claimed = True
                if stale or is_cancelled or expired:
                    call.resolve(error=_LaneRetired())
                    continue
                try:
                    if call.kind == 'error' and isinstance(call.args[0], (MemoryError, KeyboardInterrupt, SystemExit)):
                        raise call.args[0]
                    callback = callbacks[call.kind]
                    value = callback(call.lane, *call.args, **call.kwargs) if callback is not None else None
                    call.resolve(value)
                    if call.kind in ('terminal', 'error'):
                        with session.lock:
                            session.handled.add(call.lane)
                    # A commit performed by this callback wins even if it also
                    # cancelled the request; a concurrent close/supersede wins
                    # instead of allowing a retired owner to publish a winner.
                    if not session.retired.is_set() and identity_committed() is True:
                        session.retire('identity-committed', call.lane)
                except BaseException:
                    call.resolve(error=_LaneRetired())
                    raise
            return session.snapshot()
        finally:
            if not session.retired.is_set():
                session.retire('owner-error')
            with self._lock:
                if self._active is session:
                    self._active = None
