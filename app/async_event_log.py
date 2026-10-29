"""Bounded diagnostic writer. Producers never perform disk IO or call user repr."""
from collections import deque
from datetime import datetime
import json
import threading
import time


def snapshot(value, budget=None, depth=0):
    if budget is None:
        budget = [512, 16384]
    budget[0] -= 1
    if budget[0] < 0 or depth > 8:
        return '<truncated>'
    if value is None or type(value) in (bool, int, float):
        return value
    if type(value) is str:
        text = value[:min(4096, budget[1])]
        budget[1] -= len(text)
        return text
    if type(value) is dict:
        result = {}
        for key, item in value.items():
            if budget[0] <= 0 or budget[1] <= 0:
                result['_truncated'] = True
                break
            budget[0] -= 1
            if type(key) is str:
                key = key[:min(256, budget[1])]
                budget[1] -= len(key)
                result[key] = snapshot(item, budget, depth + 1)
        return result
    if type(value) in (list, tuple):
        result = []
        for item in value:
            if budget[0] <= 0 or budget[1] <= 0:
                result.append('<truncated>')
                break
            result.append(snapshot(item, budget, depth + 1))
        return result
    return '<' + type(value).__name__ + '>'


class AsyncEventLog:
    # High-volume observations may be displaced before lifecycle/error records.
    NOISY = frozenset(('runtime-stage-completed', 'map-probe-observed',
                       'ui-event-poll-slow', 'hotkey-listener-health'))

    def __init__(self, path, capacity=1024):
        self.path = path
        self.capacity = capacity
        self.pending = deque()
        self.condition = threading.Condition()
        self.active = False
        self.closing = False
        self.dropped = 0
        self.failed = 0
        self.snapshot_failed = 0
        self.start_error = None
        self.sequence = 0
        self.thread = threading.Thread(target=self._run, name='diagnostic-writer', daemon=True)
        try:
            self.thread.start()
        except RuntimeError as error:
            self.start_error = type(error).__name__
            self.closing = True

    def emit(self, kind, values, timeline=True):
        try:
            row = snapshot(values)
        except Exception as error:
            # A shared diagnostic container may change during iteration.
            # Preserve the event without letting diagnostics break its caller.
            row = {'diagnosticSnapshotError': type(error).__name__}
        row.update(time=datetime.now().astimezone().isoformat(timespec='milliseconds'), event=kind)
        with self.condition:
            if 'diagnosticSnapshotError' in row:
                self.snapshot_failed += 1
            if self.closing:
                self.dropped += 1
                return
            if len(self.pending) >= self.capacity:
                victim = next((i for i, r in enumerate(self.pending) if r['event'] in self.NOISY), None)
                if victim is None or kind in self.NOISY:
                    self.dropped += 1
                    return
                del self.pending[victim]
                self.dropped += 1
            self.sequence += 1
            if timeline:
                row.update(timelineSequence=self.sequence, loggedMonotonicAt=time.perf_counter())
            self.pending.append(row)
            self.condition.notify()

    def _write(self, row):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open('a', encoding='utf-8', newline='\n') as handle:
            handle.write(json.dumps(row, ensure_ascii=False, separators=(',', ':')) + '\n')

    def _run(self):
        while True:
            with self.condition:
                self.condition.wait_for(lambda: self.pending or self.closing)
                if not self.pending:
                    return
                row = self.pending.popleft()
                self.active = True
                row['diagnosticDroppedTotal'] = self.dropped
                row['diagnosticWriteFailuresTotal'] = self.failed
            try:
                self._write(row)
            except Exception:
                with self.condition:
                    self.failed += 1
            finally:
                with self.condition:
                    self.active = False
                    self.condition.notify_all()

    def flush(self, timeout=2.0):
        with self.condition:
            return self.condition.wait_for(lambda: not self.pending and not self.active, timeout)

    def close(self):
        with self.condition:
            self.closing = True
            self.condition.notify_all()

    def status(self):
        with self.condition:
            return dict(pending=len(self.pending), active=self.active,
                        dropped=self.dropped, failed=self.failed,
                        snapshotFailed=self.snapshot_failed, startError=self.start_error)
