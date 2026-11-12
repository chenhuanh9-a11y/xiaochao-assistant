"""Sequential source jobs; the scanner owns scheduling and runtime state."""
from dataclasses import dataclass, field
from copy import deepcopy
import hashlib
import json
from threading import Event, Lock
import time
from typing import Callable

import numpy as np


@dataclass(frozen=True, init=False)
class FrameSnapshot:
    """One immutable pixel owner for capture, admission and evidence jobs.

    Both digests retain their existing meanings: admission includes shape and
    dtype; the observation frame key is the digest of the raw pixel bytes.
    The stable view cannot be made writable because its backing store is bytes.
    """
    _pixels: bytes = field(repr=False)
    shape: tuple
    sha256: str
    frame_key: str
    _view: np.ndarray = field(repr=False, compare=False)

    def __init__(self, frame):
        if (not isinstance(frame, np.ndarray) or frame.dtype != np.uint8
                or frame.ndim not in (2, 3) or not frame.size):
            raise ValueError('invalid-frame')
        if frame.ndim == 3 and frame.shape[2] not in (1, 3, 4):
            raise ValueError('invalid-channels')
        pixels = frame.tobytes(order='C')
        shape = tuple(frame.shape)
        object.__setattr__(self, '_pixels', pixels)
        object.__setattr__(self, 'shape', shape)
        object.__setattr__(self, 'sha256', hashlib.sha256(
            json.dumps(shape).encode() + b'\0uint8\0' + pixels).hexdigest())
        object.__setattr__(self, 'frame_key', hashlib.sha256(pixels).hexdigest())
        object.__setattr__(self, '_view', np.frombuffer(pixels, dtype=np.uint8).reshape(shape))

    @property
    def pixels(self):
        return self._view


@dataclass
class EntryEvidenceJob:
    map_id: str | None
    phase: str
    run: Callable[[], None]


class SharedObservationDependencies:
    """Two consumers may share pure kernels, never their mutable reducers.

    The scan owns this object and its immutable frame. Completed values are
    copied before a consumer can mutate geometry caches or evidence rows. A
    retiring consumer stops waiting without cancelling the other one's kernel.
    """
    def __init__(self, snapshot, floor, *, max_bytes=64 * 1024 * 1024):
        if (not isinstance(snapshot, FrameSnapshot) or not isinstance(floor, str) or not floor
                or type(max_bytes) is not int or max_bytes < 0):
            raise ValueError('shared-observation-binding-invalid')
        self.snapshot, self.floor, self.max_bytes = snapshot, str(floor), max_bytes
        self._lock, self._values, self._bytes = Lock(), {}, 0

    def owns(self, frame):
        return frame is self.snapshot.pixels

    @staticmethod
    def _size(value):
        if isinstance(value, np.ndarray):
            return value.nbytes
        if isinstance(value, (bytes, str)):
            return len(value)
        if isinstance(value, dict):
            return sum(SharedObservationDependencies._size(v) for v in value.values())
        if isinstance(value, (list, tuple)):
            return sum(SharedObservationDependencies._size(v) for v in value)
        return 64

    @staticmethod
    def value_key(value):
        """Exact numerical inputs; no digest or mutable object equality."""
        if isinstance(value, np.ndarray):
            return ('array', value.dtype.str, value.shape, value.tobytes())
        if isinstance(value, dict):
            return tuple((key, SharedObservationDependencies.value_key(item))
                         for key, item in sorted(value.items()))
        if isinstance(value, (list, tuple)):
            return tuple(SharedObservationDependencies.value_key(item) for item in value)
        return value

    def get(self, key, compute, *, cancelled=None, reusable=None):
        cancelled = cancelled or (lambda: False)
        if cancelled():
            raise ValueError('entry-observation-consumer-cancelled')
        with self._lock:
            record = self._values.get(key)
            producer = record is None
            if producer:
                record = dict(ready=Event())
                self._values[key] = record
        if producer:
            try:
                value = compute()
                record['value'] = deepcopy(value)
                record['reusable'] = reusable is None or reusable(value)
                size = self._size(record['value']) + self._size(key)
                with self._lock:
                    if (not record['reusable']
                            or len(self._values) > 256 or self._bytes + size > self.max_bytes):
                        self._values.pop(key, None)
                    else:
                        self._bytes += size
            except BaseException as error:
                record['error'] = error
                with self._lock:
                    self._values.pop(key, None)
            finally:
                record['ready'].set()
        else:
            while not record['ready'].wait(.01):
                if cancelled():
                    raise ValueError('entry-observation-consumer-cancelled')
        if cancelled():
            raise ValueError('entry-observation-consumer-cancelled')
        if 'error' in record:
            raise record['error']
        if not producer and not record['reusable']:
            # A consumer with a later deadline must still perform its own work
            # after another consumer's incomplete per-source attempt.
            return compute()
        return deepcopy(record['value'])


class EntryEvidenceWork:
    """A single worker owns each work item and executes its jobs sequentially."""
    def __init__(self, frame):
        self.frame_snapshot = (frame if isinstance(frame, FrameSnapshot) else
                               FrameSnapshot(frame) if frame is not None else None)
        self.frame = self.frame_snapshot.pixels if self.frame_snapshot is not None else None
        self._complete = self._closed = self._failed = False
        self._pending = self._result = None
        self._steps = self._jobs()

    def _job(self, map_id, phase, function):
        def run():
            if self._closed or self._pending is not job:
                raise ValueError('entry-job-not-current')
            try:
                function()
            except BaseException:
                self._failed, self._complete = True, False
                self._close()
                raise
            self._pending = None
        job = EntryEvidenceJob(map_id, phase, run)
        return job

    def next_job(self):
        if self._closed:
            return None
        if self._pending is None:
            try:
                self._pending = next(self._steps)
            except StopIteration:
                self._complete = True
            except BaseException:
                self._failed, self._complete = True, False
                self._close()
                raise
        return self._pending

    def _close(self):
        self._closed, self._pending = True, None
        self._steps.close()

    def close(self):
        self._close()


class ObservationWorkScheduler:
    """One cooperative deadline/cancel loop for fit and source evidence jobs.

    Obtaining the next job only advances local reduction/planning. Expensive
    kernels belong in job.run(). In-flight kernels may overrun the deadline;
    an exhausted plan is still complete when its last kernel has returned.
    The consumer owns evidence qualification and any normal material stop.
    """
    def __init__(self, *, clock=time.monotonic):
        self.clock = clock

    def drive(self, work, *, deadline=None, cancelled=None, on_job_finished=None):
        cancelled = cancelled or (lambda: False)
        status = 'complete'
        try:
            job = work.next_job()
            while job is not None:
                if cancelled():
                    status = 'cancelled'
                    break
                if deadline is not None and self.clock() >= deadline:
                    status = 'budget-unresolved'
                    break
                job.run()
                if cancelled():
                    status = 'cancelled'
                    break
                terminal = on_job_finished(job) if on_job_finished is not None else None
                if terminal is not None:
                    status = terminal
                    break
                job = work.next_job()
            if cancelled():
                status = 'cancelled'
            return status
        finally:
            work.close()
