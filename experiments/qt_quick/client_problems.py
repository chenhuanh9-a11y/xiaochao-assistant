"""Bounded current-run problems and serial asynchronous rotating text logs.

Only supplied diagnostic metadata is accepted. No frame/configuration is read.
pendingWrites includes the queued completion callback, not just active threads.
"""
from __future__ import annotations
from collections import OrderedDict, deque
from copy import deepcopy
from datetime import datetime, timezone
import json
import math
import os
import time
from pathlib import Path
from uuid import uuid4

from PySide6.QtCore import QObject, Property, QRunnable, QThreadPool, QTimer, Qt, Signal, Slot
from diagnostic_identity import DiagnosticStream
from diagnostic_privacy import safe_record

PHASE_LABELS = dict(capture="画面采集", input="快捷键", identity="地图识别", floor="楼层识别",
                    **{"recognition-alignment": "识别与贴合", "operation": "任务执行",
                       "presentation": "地图显示", "resource": "地图资源", "resources": "地图资源"})
RECENT_LIMIT = 32
PENDING_LIMIT = 64
LOG_MAX_BYTES = 1024 * 1024
SUMMARY_CHECKPOINT_MS = 60_000
TRACE_ORIGIN_FIELDS = ('invalidationOrigin', 'exceptionOrigin', 'exceptionAction')


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


def _trace_state(value):
    """Material state, excluding per-frame identity/clock values from dedup."""
    if isinstance(value, dict):
        result = {}
        for key, item in value.items():
            lowered = key.lower()
            if ('token' in lowered or 'generation' in lowered or lowered in
                    {'frame', 'frameid', 'timestamp', 'timestamputc', 'time', 'elapsed', 'latency'}):
                continue
            result[key] = bool(item) if lowered.endswith(('source', 'url')) else _trace_state(item)
        return result
    if isinstance(value, list):
        return [_trace_state(item) for item in value]
    return value


def _details(row):
    return (f"时间：{row['localTime']}\nUTC：{row['timestampUTC']}\n"
            f"最近出现（UTC）：{row['lastTimestampUTC']} · 共 {row['count']} 次\n"
            f"问题：{row['title']}\n原因：{row['summary']}\n"
            f"阶段：{row['phaseLabel']}\n操作：{row['action'] or '未提供'}\n"
            f"地图 / 楼层：{row['mapId'] or '未识别'} / {row['floor'] or '未识别'}\n"
            f"模式：{row['mode'] or '未提供'}\n\n原始原因：\n{row['raw'] or '未提供'}\n"
            + (f"\n资源错误代码：{row['errorCode']}\n资源依赖核对：\n"
               + json.dumps(row['errorDetails'], ensure_ascii=False, indent=2)
               if row.get('errorCode') else "")
            + f"\n原始阶段：{row['phase']}\n采集诊断：\n"
            + json.dumps(row['captureDiagnostics'], ensure_ascii=False, indent=2)
            + ("\n\n原始调用栈：\n" + row['traceback'] if row['traceback'] else ""))


class _BatchFailure(Exception):
    def __init__(self, failure, completed):
        super().__init__(str(failure))
        self.completed = completed


def _write_batch(path, rows):
    from diagnostic_storage import append_records
    completed = 0
    try:
        for start in range(0,len(rows),16):
            batch=[dict({'event': 'qt-runtime-problem'}, **row) for row in rows[start:start+16]]
            append_records(path,batch,file_limit=LOG_MAX_BYTES)
            completed += len(batch)
    except Exception as failure:
        raise _BatchFailure(failure, completed + getattr(failure, 'completed', 0)) from failure


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
            error = dict(message=_text(str(failure), 1024), completed=getattr(failure, 'completed', 0))
        self.signals.done.emit(error)


class ProblemsService(QObject):
    changed = Signal()
    recentChanged = Signal()

    def __init__(self, paths, parent=None, *, build_fingerprint='UNKNOWN', source_commit=''):
        super().__init__(parent)
        stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
        self.log_path = Path(paths.work_root) / 'nightmare-live' / f'events-qt-{stamp}-{os.getpid()}.jsonl'
        from diagnostic_storage import slot_path
        self.log_path = slot_path(self.log_path, 'problem')
        settings = getattr(paths, "settings_path", Path(paths.work_root) / "nightmare-memory" / "ui-settings.json")
        self.identity = DiagnosticStream(self.log_path.name, Path(settings).parent)
        self.recognition_path = self.log_path.with_name(self.log_path.name.replace('events-qt-', 'events-qt-recognition-', 1))
        self.recognition_identity = DiagnosticStream(self.recognition_path.name, Path(settings).parent)
        self.pool = QThreadPool(self)
        self.pool.setMaxThreadCount(1)
        self._recent, self._queue = [], deque()
        self._accepting, self._job, self._sequence = True, None, 0
        self._write_error = ''
        self._write_error_raw = ''
        self._inflight_rows, self._dropped = 0, 0
        self._build_fingerprint = _text(build_fingerprint, 64)
        self._source_commit = _text(source_commit, 40)
        self._trace_states = OrderedDict()
        self._recognition_next = OrderedDict()
        self._problem_groups = OrderedDict()
        self._summary_dirty = False
        self._summary_timer = QTimer(self)
        self._summary_timer.setSingleShot(True)
        self._summary_timer.setInterval(SUMMARY_CHECKPOINT_MS)
        self._summary_timer.timeout.connect(self._periodic_checkpoint)

    recent = Property('QVariantList', lambda self: [{key: deepcopy(value) for key,value in row.items()
                       if not key.startswith('_')} for row in self._recent], notify=recentChanged)
    pendingWrites = Property(bool, lambda self: bool(self._job is not None or self._queue), notify=changed)
    writeError = Property(str, lambda self: self._write_error, notify=changed)
    writeErrorDetails = Property(str, lambda self: self._write_error_raw, notify=changed)
    droppedWrites = Property(int, lambda self: self._dropped, notify=changed)

    def diagnosticSnapshot(self):
        """Copy export inputs on the UI owner thread without waiting for disk.

        Queue plus active batch is bounded by PENDING_LIMIT. Keep those rows
        even when their occurrence has left recent; the history merger takes
        max(count) across originals, checkpoints, recent, and persisted copies.
        """
        rows = []
        sources = (self._recent, self._queue,
                   self._job.rows if self._job is not None else ())
        snapshot_limit = RECENT_LIMIT + PENDING_LIMIT
        for source in sources:
            for row in source:
                if len(rows) >= snapshot_limit:
                    break
                if row.get('event', 'qt-runtime-problem') not in {
                        'qt-runtime-problem', 'qt-runtime-problem-summary'}:
                    continue
                rows.append({key: deepcopy(value) for key, value in row.items()
                             if not key.startswith('_')})
        return dict(recentProblems=rows, droppedWrites=self._dropped, diagnosticLoss=self.identity.loss(),
                    recognitionDiagnosticLoss=self.recognition_identity.loss(), writerHadError=bool(self._write_error),
                    pendingWrites=bool(self._job is not None or self._queue),
                    snapshotLimit=snapshot_limit)

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
                       kind=16, mode=48, mapId=96, floor=32, exceptionType=128,
                       exceptionOrigin=128, exceptionAction=128)
        row = {name: _text(event.get(name, ''), limits[name])
                for name in ('title', 'summary', 'raw', 'traceback', 'action', 'phase', 'kind', 'mode', 'mapId', 'floor', 'exceptionType', 'exceptionOrigin', 'exceptionAction')}
        row['title'] = row['title'] or '运行出现问题'
        row['summary'] = row['summary'] or '本次操作未完成，可复制下方原始原因进行排查。'
        row['phaseLabel'] = PHASE_LABELS.get(row['phase'], '任务执行')
        row.update(buildFingerprint=self._build_fingerprint, sourceCommit=self._source_commit)
        diagnostics = _metadata(event.get('captureDiagnostics', {}))
        encoded = json.dumps(diagnostics, ensure_ascii=False)
        row['captureDiagnostics'] = diagnostics if len(encoded.encode('utf-8')) <= 1024 else dict(summary=_text(encoded, 900), truncated=True)
        signature = tuple(row[name] for name in ('title', 'summary', 'raw', 'action', 'phase', 'kind', 'mode', 'mapId', 'floor'))
        signature += (row.get('exceptionType', ''), row.get('exceptionOrigin', ''), row.get('exceptionAction', ''),
                      row.get('errorCode', ''), json.dumps(row.get('errorDetails', {}), sort_keys=True))
        existing = next((item for item in self._recent if item['_signature'] == signature), None)
        if existing is not None:
            existing.update(count=existing['count']+1, lastTimestampUTC=utc)
            self._summary_dirty = True
            if not self._summary_timer.isActive():
                self._summary_timer.start()
            self._recent.remove(existing); self._recent.insert(0, existing)
            self.recentChanged.emit()
            self.changed.emit()
            return True  # Coalesced in memory; no per-refresh append or write.
        # Random run-local grouping preserves repeat accounting after disk
        # redaction; never persist a digest derived from private exception text.
        group = self._problem_groups.setdefault(signature, uuid4().hex)
        self._problem_groups.move_to_end(signature)
        while len(self._problem_groups) > 64:
            self._problem_groups.popitem(last=False)
        row['problemGroupId'] = group
        self._sequence += 1
        row.update(self.identity.stamp())
        row.update(id=str(self._sequence), occurrenceId=self.log_path.stem + ':' + row['sessionId'] + ':' + str(row['pid']) + ':' + str(row['seq']),
                   timestampUTC=utc, lastTimestampUTC=utc,
                   localTime=now.astimezone().strftime('%Y-%m-%d %H:%M:%S %Z'), count=1,
                   _signature=signature, _checkpoint_count=1)
        self._recent.insert(0, row)
        for evicted in self._recent[RECENT_LIMIT:]:
            if evicted['count'] > evicted['_checkpoint_count']:
                if len(self._queue) + self._inflight_rows < PENDING_LIMIT:
                    self._checkpoint(evicted)
                else:
                    # This count is about to leave bounded memory and cannot be
                    # retried. Expose the loss instead of silently claiming it
                    # was saved, or retaining an unbounded pending-count map.
                    self._dropped += 1
                    lost = self.identity.stamp()
                    self.identity.drop(lost, 'repeat-checkpoint-evicted')
                    self._write_error = '问题重复计数队列已满；被淘汰问题的最新次数未保存。'
                    self._write_error_raw = 'problem-repeat-count-checkpoint-dropped'
        del self._recent[RECENT_LIMIT:]
        accepted = len(self._queue) + self._inflight_rows < PENDING_LIMIT
        if accepted:
            self._queue.append({key: deepcopy(value) for key,value in row.items() if not key.startswith('_') and key != 'details'})
            self._start_next()
        else:
            self._dropped += 1
            self.identity.drop(row, 'queue-capacity')
            self._write_error = '问题日志队列已满，部分问题仅保留在本次运行列表。'
        self.recentChanged.emit()
        self.changed.emit()
        return accepted

    @Slot(object, result=bool)
    def recordTrace(self, event):
        if not self._accepting or not isinstance(event, dict):
            return False
        names = ('role', 'kind', 'stage', 'action', 'reason', 'mode', 'groupid', 'identityPhase',
                 'provisional', 'durablemap', 'floor', 'round', 'cycle', 'frame', 'generation',
                 'token', 'window', 'phase', 'status', 'visible', 'material', 'decision')
        row = {}
        for name in names:
            if name not in event:
                continue
            value = _metadata(event[name])
            encoded_value = json.dumps(value, ensure_ascii=False)
            row[name] = value if len(encoded_value.encode('utf-8')) <= 2048 else dict(summary=_text(encoded_value, 1900), truncated=True)
        if 'recognitionDiagnostics' in event:
            from recognition_diagnostics import bounded_recognition
            row['recognitionDiagnostics'] = bounded_recognition(event['recognitionDiagnostics'])
        raw_diagnostics = event.get('diag', event.get('diagnostics', {}))
        # Only stable origin fields affect dedup, never arbitrary per-frame
        # diagnostics. Keep independent origin lanes inside the existing 64-key
        # LRU so interleaved failures do not erase or repeatedly re-log each other.
        origin = {name: _text(raw_diagnostics[name], 128) for name in TRACE_ORIGIN_FIELDS
                  if isinstance(raw_diagnostics, dict) and isinstance(raw_diagnostics.get(name), str)
                  and raw_diagnostics[name]}
        if origin:
            row['origin'] = origin
        diagnostics = _metadata(raw_diagnostics)
        encoded = json.dumps(diagnostics, ensure_ascii=False)
        row['diag'] = diagnostics if len(encoded.encode('utf-8')) <= 2048 else dict(summary=_text(encoded, 1900), truncated=True)
        window = row.get('window')
        window_id = window.get('name', window.get('role', '')) if isinstance(window, dict) else window
        key = json.dumps([row.get('role'), row.get('stage'), window_id, origin], sort_keys=True)
        signature = json.dumps(_trace_state({name: value for name,value in row.items()
                               if name not in {'diag', 'recognitionDiagnostics', 'frame', 'generation', 'token'}}), sort_keys=True)
        previous = self._trace_states.get(key)
        stream = self.recognition_identity if 'recognitionDiagnostics' in row else self.identity
        row.update(stream.stamp())
        if 'recognitionDiagnostics' in row:
            lane = row['recognitionDiagnostics'].get('stage')
            now = time.monotonic()
            if now < self._recognition_next.get(lane, 0):
                stream.suppress(row, 'recognition-rate-limit')
                return True
            self._recognition_next[lane] = now + .25
            self._recognition_next.move_to_end(lane)
            while len(self._recognition_next) > 8:
                self._recognition_next.popitem(last=False)
        if previous is not None and previous[0] == signature and 'recognitionDiagnostics' not in row:
            self._trace_states[key] = (signature, previous[1]+1)
            self._trace_states.move_to_end(key)
            self.identity.suppress(row, "unchanged-trace-coalesced")
            return True  # No per-refresh UI notification or disk append.
        limit = PENDING_LIMIT // 2 if 'recognitionDiagnostics' in row else PENDING_LIMIT
        if len(self._queue) + self._inflight_rows >= limit:
            self._dropped += 1
            stream.drop(row, 'queue-capacity')
            self._write_error = '运行诊断日志队列已满，部分状态未写入。'
            self.changed.emit()
            return False
        now = datetime.now(timezone.utc)
        row.update(event='qt-recognition-diagnostic' if 'recognitionDiagnostics' in row else 'qt-runtime-transition', timestampUTC=now.isoformat(timespec='milliseconds').replace('+00:00', 'Z'),
                   buildFingerprint=self._build_fingerprint, sourceCommit=self._source_commit, count=1)
        if previous is not None and previous[1] > 1:
            row['previousRepeatedCount'] = previous[1]
        self._trace_states[key] = (signature, 1)
        self._trace_states.move_to_end(key)
        while len(self._trace_states) > 64:
            self._trace_states.popitem(last=False)
        self._queue.append(row)
        self._start_next()
        self.changed.emit()
        return True

    def _start_next(self):
        if self._job is not None or not self._queue:
            return
        # Keep recognition observations in their own rotating file. They must
        # never consume the retained problem-history byte budget.
        recognition = 'recognitionDiagnostics' in self._queue[0]
        rows = []
        while self._queue and len(rows) < 16 and ('recognitionDiagnostics' in self._queue[0]) == recognition:
            rows.append(self._queue.popleft())
        stream = self.recognition_identity if recognition else self.identity
        checkpoint = stream.checkpoint()
        if checkpoint is not None:
            rows[0]['diagnosticLoss'] = checkpoint
        self._inflight_rows = len(rows)
        self._job = job = _Write(self.recognition_path if recognition else self.log_path, rows)
        job.signals.done.connect(self._finished, Qt.QueuedConnection)
        self.pool.start(job)

    @Slot(object)
    def _finished(self, error):
        rows = self._job.rows if self._job is not None else ()
        self._job = None
        self._inflight_rows = 0
        if error is not None:
            completed = error.get('completed', 0) if isinstance(error, dict) else 0
            completed = completed if type(completed) is int and 0 <= completed <= len(rows) else 0
            # Confirmed prefix succeeded; the failing row may be partially
            # written and is conservatively counted as unconfirmed/lost.
            for row in rows[completed:]:
                stream = self.recognition_identity if row.get('logStream') == self.recognition_identity.name else self.identity
                stream.drop(row, 'write-failed')
                self._dropped += 1
            self._write_error_raw = str(error.get('message', '') if isinstance(error, dict) else error)
            self._write_error = '问题日志暂时无法保存，本次运行的列表仍可查看。请先复制问题的技术详情，避免关闭软件后丢失记录。'
        if not self._accepting:
            self.flushSummary()
        self._start_next()
        self.changed.emit()

    @Slot()
    def _periodic_checkpoint(self):
        if not self._accepting:
            return
        self.flushSummary()
        if self._summary_dirty:
            self._summary_timer.start()  # Full queue: retry once next interval.

    def _checkpoint(self, row):
        checkpoint = {key: deepcopy(value) for key, value in row.items() if not key.startswith('_')}
        checkpoint['event'] = 'qt-runtime-problem-summary'
        checkpoint.update(self.identity.stamp())
        self._queue.append(checkpoint)
        row['_checkpoint_count'] = row['count']

    @Slot(result=bool)
    def flushSummary(self):
        """Checkpoint coalesced counts periodically/on export/close, never per frame.

        Rows share occurrence identity with originals; readers take max(count).
        A full queue keeps dirty state so a later flush can retry.
        """
        if not self._summary_dirty:
            return True
        repeated = [row for row in self._recent if row["count"] > row["_checkpoint_count"]]
        if len(self._queue) + self._inflight_rows + len(repeated) > PENDING_LIMIT:
            return False
        for row in repeated:
            self._checkpoint(row)
        self._summary_dirty = False
        self._start_next()
        self.changed.emit()
        return True

    @Slot()
    def shutdown(self):
        self._summary_timer.stop()
        self.flushSummary()
        self._accepting = False
        self._start_next()  # Accepted rows still drain, including queued callbacks.
        self.changed.emit()

    @Slot()
    def cancel(self):
        self.shutdown()
