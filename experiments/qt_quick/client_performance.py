"""Bounded own-process metadata, never screenshots, process discovery or disk IO."""
from collections import deque
from copy import deepcopy
import time

from PySide6.QtCore import QObject, Property, QTimer, Signal, Slot
from qt_startup_state import STATE


def process_sample():
    import psutil
    process = psutil.Process()
    with process.oneshot():
        memory = process.memory_info()
        row = dict(workingSetBytes=memory.rss, threadCount=process.num_threads())
        # Windows private commit is NOT Linux VMS or the working set.
        private = getattr(memory, "private", None)
        if private is not None:
            row["privateBytes"] = private
        handles = getattr(process, "num_handles", None)
        if callable(handles):
            row["handleCount"] = handles()
        return row


class PerformanceService(QObject):
    changed = Signal()

    def __init__(self, parent=None, *, sampler=process_sample, clock=time.monotonic, start_timer=True):
        super().__init__(parent)
        self._sampler, self._clock, self._start = sampler, clock, clock()
        self._rows, self._count = deque(maxlen=120), 0
        self._maxima, self._status, self._closed = {}, "not-sampled", False
        self._timer = QTimer(self)
        self._timer.setInterval(10000)
        self._timer.timeout.connect(self.sample)
        if start_timer:
            self._timer.start()

    @Slot()
    def sample(self):
        if self._closed:
            return
        try:
            values = self._sampler()
            allowed = ("workingSetBytes", "privateBytes", "threadCount", "handleCount")
            row = {key: values[key] for key in allowed
                   if type(values.get(key)) is int and values[key] >= 0}
            if "workingSetBytes" not in row:
                raise ValueError("Working set unavailable")
            self._count += 1
            self._maxima = {key: max(value, self._maxima.get(key, 0)) for key, value in row.items()}
            row["elapsedMs"] = max(0., (self._clock()-self._start)*1000)
            self._rows.append(row)
            self._status = "available"
        except Exception:
            self._status = "sample-unavailable"
        self.changed.emit()

    def _snapshot(self):
        return dict(scope="own-process-only", status=self._status, sampleCount=self._count,
                    sampleIntervalMs=10000, retainedSampleLimit=120,
                    samples=deepcopy(list(self._rows)), maxima=dict(self._maxima),
                    startupMilestones=STATE.snapshot(),
                    startupOrigin=STATE.origin, frameEndpoint="frame-submitted-not-visible-pixels",
                    gpuMeasured=False, childProcessesMeasured=False)

    snapshot = Property("QVariantMap", _snapshot, notify=changed)

    @Slot()
    def shutdown(self):
        self._closed = True
        self._timer.stop()

    cancel = shutdown
