"""Sequential source jobs; the scanner owns scheduling and runtime state."""
from dataclasses import dataclass
import time
from typing import Callable

import numpy as np


@dataclass(frozen=True, eq=False)
class FrameSnapshot:
    """Owned immutable pixels shared by all jobs of one observation."""
    pixels: np.ndarray

    def __post_init__(self):
        frame = self.pixels
        owned = np.frombuffer(frame.tobytes(order='C'), dtype=frame.dtype).reshape(frame.shape)
        object.__setattr__(self, 'pixels', owned)


@dataclass
class EntryEvidenceJob:
    map_id: str | None
    phase: str
    run: Callable[[], None]


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
