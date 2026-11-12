"""Parallel recognition strategies; the caller alone commits runtime state.

Strategies offer evidence, never write identity or pixels. Lock qualification
belongs to each algorithm and the caller's current-context commit callback.
This module does not turn a candidate, score or pose into identity authority.
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import copy
from dataclasses import dataclass
from queue import Empty, Queue
import threading
import time

from entry_evidence_work import FrameSnapshot


@dataclass(frozen=True)
class RecognitionObservation:
    snapshot: FrameSnapshot
    floor: str
    resource_key: str


@dataclass(frozen=True)
class RecognitionEvidence:
    map_id: str
    floor: str
    frame_key: str
    resource_key: str
    payload: dict


class RecognitionRace:
    """Two bounded workers, one result consumer, no runtime state ownership.

    A strategy receives (observation, cancelled, offer_lock, offer_preview).
    Offers are copied into a queue; callbacks run only on the calling thread.
    Only the candidate strategy can supply prealignment. Both strategies can
    offer a qualified lock, and on_lock must accept it under the current
    runtime context. A rejected offer does not prevent the other branch winning.
    on_lock atomically withdraws a different preview when accepting identity;
    a rejected offer must have no presentation or identity side effects.

    The outer runtime queue serializes run(). A previous cancelled worker may
    drain in the background; new work stays local until a worker is free, so
    repeated requests cannot build an unbounded executor queue.
    """
    def __init__(self, *, clock=time.monotonic):
        self.clock = clock
        self._pool = ThreadPoolExecutor(max_workers=2, thread_name_prefix='structure-recognition')
        self._lock = threading.RLock()
        self._run_lock = threading.Lock()
        self._inflight = set()
        self._stops = set()
        self._closed = False

    def _submit(self, function):
        with self._lock:
            if self._closed or len(self._inflight) >= 2:
                return None
            future = self._pool.submit(function)
            self._inflight.add(future)
            future.add_done_callback(self._finished)
            return future

    def _finished(self, future):
        with self._lock:
            self._inflight.discard(future)

    def close(self, *, wait=True):
        with self._lock:
            self._closed = True
            for stopped in self._stops:
                stopped.set()
        self._pool.shutdown(wait=wait, cancel_futures=True)

    def run(self, observation, candidate, structure, *, on_lock, on_preview=None,
            withdraw_preview=None, cancelled=None, deadline=None):
        if not isinstance(observation.snapshot, FrameSnapshot):
            raise TypeError('recognition-frame-snapshot-required')
        if not self._run_lock.acquire(blocking=False):
            raise RuntimeError('recognition-race-already-running')
        stopped = threading.Event()
        events = Queue()
        cancelled = cancelled or (lambda: False)
        began = self.clock()
        preview = None
        submitted = []
        pending = dict(candidate=candidate, structure=structure)
        done, errors = set(), {}
        times = {name: {} for name in pending}
        time_lock = threading.Lock()
        with self._lock:
            self._stops.add(stopped)

        def obsolete():
            return stopped.is_set() or self._closed or cancelled()

        def milliseconds():
            return (self.clock()-began)*1000

        def result(status, winner=None, evidence=None):
            with time_lock:
                timing = copy.deepcopy(times)
            return dict(status=status, winner=winner, evidence=evidence, preview=preview,
                        elapsedMs=milliseconds(), branchTiming=timing, errors=dict(errors))

        def withdraw(reason):
            nonlocal preview
            if preview is not None:
                preview = None
                if withdraw_preview is not None:
                    withdraw_preview(reason)

        def matches(value):
            return (isinstance(value, RecognitionEvidence) and bool(value.map_id)
                and value.floor == observation.floor
                and value.frame_key == observation.snapshot.frame_key
                and value.resource_key == observation.resource_key)

        def worker(name, strategy):
            finished = threading.Event()
            with time_lock:
                times[name]['startedMs'] = milliseconds()

            def offer(kind, value):
                if not finished.is_set() and not obsolete():
                    value = copy.deepcopy(value)
                    with time_lock:
                        times[name].setdefault('first'+kind.title()+'OfferedMs', milliseconds())
                    events.put((name, kind, value))

            try:
                strategy(observation, obsolete,
                         lambda value: offer('lock', value),
                         lambda value: offer('preview', value))
            except BaseException as error:
                events.put((name, 'error', error))
            finally:
                finished.set()
                with time_lock:
                    times[name]['completedMs'] = milliseconds()
                events.put((name, 'done', None))

        try:
            while True:
                if obsolete():
                    withdraw('recognition-cancelled')
                    return result('cancelled')
                if deadline is not None and self.clock() >= deadline:
                    return result('unresolved')
                for name, strategy in tuple(pending.items()):
                    future = self._submit(lambda name=name, strategy=strategy: worker(name, strategy))
                    if future is None:
                        break
                    submitted.append(future)
                    del pending[name]
                if len(done) == 2:
                    return result('unresolved')
                wait = min(.01, max(0, deadline-self.clock())) if deadline is not None else .01
                try:
                    name, kind, value = events.get(timeout=wait)
                except Empty:
                    continue
                if obsolete():
                    withdraw('recognition-cancelled')
                    return result('cancelled')
                if kind == 'done':
                    done.add(name)
                elif kind == 'error':
                    if isinstance(value, (MemoryError, KeyboardInterrupt, SystemExit)):
                        raise value
                    errors[name] = dict(type=type(value).__name__, message=str(value))
                elif matches(value):
                    if kind == 'preview' and name == 'candidate' and on_preview is not None:
                        if on_preview(value) is True:
                            preview = value
                    elif kind == 'lock':
                        if on_lock(value, name, preview) is True:
                            # Acceptance is the runtime's atomic commit point.
                            # A later cancellation belongs to its newer state.
                            stopped.set()
                            if preview is not None and preview.map_id != value.map_id:
                                preview = None
                            return result('locked', name, value)
        except BaseException:
            withdraw('recognition-error')
            raise
        finally:
            stopped.set()
            for future in submitted:
                future.cancel()
            with self._lock:
                self._stops.discard(stopped)
            self._run_lock.release()
