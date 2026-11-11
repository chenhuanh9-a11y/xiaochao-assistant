"""Sequential source jobs; the scanner owns scheduling and runtime state."""
from dataclasses import dataclass
from typing import Callable

import numpy as np


@dataclass
class EntryEvidenceJob:
    map_id: str | None
    phase: str
    run: Callable[[], None]


class EntryEvidenceWork:
    """A single worker owns each work item and executes its jobs sequentially."""
    def __init__(self, frame):
        self.frame = (np.frombuffer(frame.tobytes(order='C'), dtype=frame.dtype).reshape(frame.shape)
                      if frame is not None else None)
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
