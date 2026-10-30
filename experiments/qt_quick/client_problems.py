"""Bounded current-run problems and serial asynchronous rotating text logs.

Only supplied diagnostic metadata is accepted. No frame/configuration is read.
pendingWrites includes the queued completion callback, not just active threads.
"""
from __future__ import annotations
from collections import deque
from copy import deepcopy
from datetime import datetime, timezone
import json
import logging
import math
from logging.handlers import RotatingFileHandler
import os
from pathlib import Path

from PySide6.QtCore import QObject, Property, QRunnable, QThreadPool, Qt, Signal, Slot

PHASE_LABELS = dict(capture="画面采集", input="快捷键", identity="地图识别", floor="楼层识别",
                    **{"recognition-alignment": "识别与贴合", "operation": "任务执行"})
RECENT_LIMIT = 32
PENDING_LIMIT = 64
LOG_MAX_BYTES = 1024 * 1024


def _text(value, limit=8192):
    if not isinstance(value, str):
        return ""
    raw = value.encode("utf-8", errors="replace")
    return value if len(raw) <= limit else raw[:limit-3].decode("utf-8", errors="ignore") + "…"


def _metadata(value, depth=0):
    if value is None or type(value) in (bool, int):
        return value
    if type(value) is float:
        return value if math.isfinite(value) else None
    if isinstance(value, str):
        return _text(value, 256)
    if depth < 3 and isinstance(value, dict):
        return {_text(key, 80): _metadata(item, depth+1) for key,item in list(value.items())[:16]
                if isinstance(key, str)}
    if depth < 3 and isinstance(value, (list, tuple)):
        return [_metadata(item, depth+1) for item in value[:16]]
    return None


def _details(row):
    return (f"时间：{row['localTime']}\nUTC：{row['timestampUTC']}\n"
            f"最近出现（UTC）：{row['lastTimestampUTC']} · 共 {row['count']} 次\n"
            f"问题：{row['title']}\n原因：{row['summary']}\n"
            f"阶段：{row['phaseLabel']}\n操作：{row['action'] or '未提供'}\n"
            f"地图 / 楼层：{row['mapId'] or '未识别'} / {row['floor'] or '未识别'}\n"
            f"模式：{row['mode'] or '未提供'}\n\n原始原因：\n{row['raw'] or '未提供'}\n"
            f"\n原始阶段：{row['phase']}\n采集诊断：\n"
            + json.dumps(row['captureDiagnostics'], ensure_ascii=False, indent=2)
            + ("\n\n原始调用栈：\n" + row['traceback'] if row['traceback'] else ""))


class _StrictHandler(RotatingFileHandler):
    def handleError(self, record):
        raise  # Surface disk errors to the service instead of silently losing text.


def _write_batch(path, rows):
    from client_paths import _no_links
    _no_links(path.parent)
    path.parent.mkdir(parents=True, exist_ok=True)
    _no_links(path)
    _no_links(path.with_name(path.stem + '-previous.jsonl'))
    handler = _StrictHandler(path, maxBytes=LOG_MAX_BYTES, backupCount=1, encoding='utf-8')
    # Keep the single backup inside diagnostic_export's existing events-*.jsonl glob.
    handler.namer = lambda name: name.removesuffix('.jsonl.1') + '-previous.jsonl'
    handler.setFormatter(logging.Formatter('%(message)s'))
    try:
        for row in rows:
            message = json.dumps(dict(event='qt-runtime-problem', **row), ensure_ascii=False)
            handler.emit(logging.LogRecord('xingtu.problems', logging.WARNING, '', 0, message, (), None))
    finally:
        handler.close()


class _Signals(QObject):
    done = Signal(object)


class _Write(QRunnable):
    def __init__(self, path, rows):
        super().__init__()
        self.path, self.rows, self.signals = path, rows, _Signals()

    def run(self):
        error = None
        try:
            _write_batch(self.path, self.rows)
        except Exception as failure:
            error = _text(str(failure), 1024)
        self.signals.done.emit(error)


class ProblemsService(QObject):
    changed = Signal()

    def __init__(self, paths, parent=None, *, build_fingerprint='UNKNOWN', source_commit=''):
        super().__init__(parent)
        stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
        self.log_path = Path(paths.work_root) / 'nightmare-live' / f'events-qt-{stamp}-{os.getpid()}.jsonl'
        self.pool = QThreadPool(self)
        self.pool.setMaxThreadCount(1)
        self._recent, self._queue = [], deque()
        self._accepting, self._job, self._sequence = True, None, 0
        self._write_error = ''
        self._inflight_rows, self._dropped = 0, 0
        self._build_fingerprint = _text(build_fingerprint, 64)
        self._source_commit = _text(source_commit, 40)

    recent = Property('QVariantList', lambda self: [{key: deepcopy(value) for key,value in row.items()
                       if key != '_signature'} for row in self._recent], notify=changed)
    pendingWrites = Property(bool, lambda self: bool(self._job is not None or self._queue), notify=changed)
    writeError = Property(str, lambda self: self._write_error, notify=changed)
    droppedWrites = Property(int, lambda self: self._dropped, notify=changed)

    @Slot(str, result=str)
    def detailText(self, problem_id):
        row = next((row for row in self._recent if row['id'] == problem_id), None)
        return _details(row) if row is not None else ''

    @Slot(object, result=bool)
    def record(self, event):
        if not self._accepting or not isinstance(event, dict) or event.get('kind') not in {'error', 'warning'}:
            return False
        now = datetime.now(timezone.utc)
        utc = now.isoformat(timespec='milliseconds').replace('+00:00', 'Z')
        limits = dict(title=256, summary=1024, raw=4096, traceback=8192, action=128, phase=64,
                      kind=16, mode=48, mapId=96, floor=32)
        row = {name: _text(event.get(name, ''), limits[name])
               for name in ('title', 'summary', 'raw', 'traceback', 'action', 'phase', 'kind', 'mode', 'mapId', 'floor')}
        row['title'] = row['title'] or '运行出现问题'
        row['summary'] = row['summary'] or '本次操作未完成，可复制下方原始原因进行排查。'
        row['phaseLabel'] = PHASE_LABELS.get(row['phase'], '任务执行')
        row.update(buildFingerprint=self._build_fingerprint, sourceCommit=self._source_commit)
        diagnostics = _metadata(event.get('captureDiagnostics', {}))
        encoded = json.dumps(diagnostics, ensure_ascii=False)
        row['captureDiagnostics'] = diagnostics if len(encoded.encode('utf-8')) <= 1024 else dict(summary=_text(encoded, 900), truncated=True)
        signature = tuple(row[name] for name in ('title', 'summary', 'raw', 'action', 'phase', 'kind', 'mode', 'mapId', 'floor'))
        existing = next((item for item in self._recent if item['_signature'] == signature), None)
        if existing is not None:
            existing.update(count=existing['count']+1, lastTimestampUTC=utc)
            self._recent.remove(existing); self._recent.insert(0, existing)
            self.changed.emit()
            return True  # Coalesced in memory; no per-refresh append or write.
        self._sequence += 1
        row.update(id=str(self._sequence), timestampUTC=utc, lastTimestampUTC=utc,
                   localTime=now.astimezone().strftime('%Y-%m-%d %H:%M:%S %Z'), count=1, _signature=signature)
        self._recent.insert(0, row); del self._recent[RECENT_LIMIT:]
        accepted = len(self._queue) + self._inflight_rows < PENDING_LIMIT
        if accepted:
            self._queue.append({key: deepcopy(value) for key,value in row.items() if key not in {'_signature', 'details'}})
            self._start_next()
        else:
            self._dropped += 1
            self._write_error = '问题日志队列已满，部分问题仅保留在本次运行列表。'
        self.changed.emit()
        return accepted

    def _start_next(self):
        if self._job is not None or not self._queue:
            return
        rows = [self._queue.popleft() for _ in range(min(16, len(self._queue)))]
        self._inflight_rows = len(rows)
        self._job = job = _Write(self.log_path, rows)
        job.signals.done.connect(self._finished, Qt.QueuedConnection)
        self.pool.start(job)

    @Slot(object)
    def _finished(self, error):
        self._job = None
        self._inflight_rows = 0
        if error is not None:
            self._write_error = '问题日志暂时无法写入：' + str(error)
        self._start_next()
        self.changed.emit()

    @Slot()
    def shutdown(self):
        self._accepting = False
        self._start_next()  # Accepted rows still drain, including queued callbacks.
        self.changed.emit()

    @Slot()
    def cancel(self):
        self.shutdown()
