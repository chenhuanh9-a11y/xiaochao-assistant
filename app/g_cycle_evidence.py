"""Opt-in local G-attempt evidence, never a capture or UI authority.

Only the caller's bounded existing algorithm input is accepted. Admission copies pixels;
PNG encoding and disk work run on one daemon writer. Disable cancels queued work
and prevents an encoding job from committing; a commit already in progress may
finish before disable returns. shutdown() drains accepted jobs without blocking;
shutdown(wait=True) joins that writer. No thread
or filesystem operation occurs until explicit start on an enabled recorder.

Storage reuses diagnostic_storage's pinned directory chain and exclusive Windows
file handles. Windows retirement verifies and deletes the SAME handle. POSIX
supports bounded writes for offline testing but never automatic deletion: when
expiry/cap requires retirement it fails closed. Linux tests do not establish the
Windows native handle contract. Unknown, modified, linked, or interrupted files
block further writes and are never adopted or removed.
"""
from collections import Counter, deque
from dataclasses import dataclass
import hashlib
import io
import json
import math
import os
from pathlib import Path
import re
import stat
import threading
import time
import uuid

import numpy as np
from PIL import Image


MiB = 1024 * 1024
REGISTRY = '.g-cycle-ownership.json'
SCHEMA = 'g-cycle-evidence-v1'
TOKEN = re.compile(r'[A-Za-z0-9_.:-]{1,128}\Z')
NAME = re.compile(r'case-[a-f0-9]{32}\.(png|json)\Z')
META_KEYS = frozenset(('identity', 'session', 'round', 'cycle', 'opening', 'attempt',
    'frame', 'mode', 'map', 'floor', 'locked', 'outcome', 'reason', 'crop_bounds',
    'full_size', 'timings', 'solver', 'season', 'build_fingerprint', 'source_commit',
    'capture_generation', 'input_sequence', 'sample_role', 'evidence_kind',
    'roi_provenance', 'missing_reason', 'diagnostic_id', 'roi_count',
    'input_scope', 'observation_bounds', 'omitted_observation_count', 'fit_evidence'))
NUMBER_KEYS = frozenset(('round', 'cycle', 'opening', 'attempt', 'frame',
    'capture_generation', 'input_sequence', 'roi_count', 'omitted_observation_count'))
SOLVER_KEYS = frozenset(('score', 'confidence', 'angle', 'scale', 'x', 'y', 'inliers',
    'matches', 'candidates', 'residual', 'elapsed_ms', 'accepted', 'success',
    'matrixSourceToClient'))
RESULT_KEYS = frozenset(('identity', 'session', 'round', 'cycle', 'opening', 'attempt',
    'frame', 'mode', 'map', 'floor', 'locked', 'outcome', 'reason', 'capture_generation',
    'input_sequence', 'sample_role', 'evidence_kind', 'missing_reason', 'diagnostic_id'))
TIMING_KEYS = frozenset(('input_ms', 'capture_ms', 'ready_ms', 'solve_ms', 'queue_ms',
    'validate_ms', 'display_ms', 'total_ms', 'elapsed_ms', 'dispatch_ms'))


@dataclass(frozen=True)
class EvidenceLimits:
    max_cases: int = 100
    max_bytes: int = 256 * MiB
    max_age: int = 86400
    pending_count: int = 2  # Includes the active job, not only the deque.
    pending_bytes: int = 32 * MiB
    case_bytes: int = 16 * MiB
    roi_bytes: int = 12 * MiB

    def __post_init__(self):
        bounds = dict(max_cases=100, max_bytes=256 * MiB, max_age=86400,
                      pending_count=2, pending_bytes=32 * MiB,
                      case_bytes=16 * MiB, roi_bytes=12 * MiB)
        for key, maximum in bounds.items():
            if type(getattr(self, key)) is not int or not 0 < getattr(self, key) <= maximum:
                raise ValueError('invalid-evidence-limit')


def _number(value):
    return type(value) in (int, float) and math.isfinite(value) and abs(value) <= 1e12


def _solver_value(key, value):
    if key in ('success', 'accepted'):
        return type(value) is bool or _number(value)
    if key == 'matrixSourceToClient':
        return (type(value) in (list, tuple) and len(value) in (2, 3)
                and all(type(row) in (list, tuple) and len(row) == 3
                        and all(_number(v) for v in row) for row in value))
    return _number(value)


def _metadata(value, shape):
    if type(value) is not dict or set(value) - META_KEYS:
        raise ValueError('invalid-metadata')
    out = {}
    for key, item in value.items():
        if key in NUMBER_KEYS:
            valid = type(item) is int and 0 <= item < 2**53
            if key in ('opening', 'attempt', 'frame') and type(item) is str:
                valid = TOKEN.fullmatch(item) is not None
        elif key == 'locked':
            valid = type(item) is bool
        elif key == 'fit_evidence':
            from s1_fit_diagnostics import safe_evidence
            valid = type(item) is dict
            item = safe_evidence(item) if valid else None
        elif key == 'observation_bounds':
            valid = (type(item) is list and len(item) <= 16
                     and all(type(box) in (list, tuple) and len(box) == 4
                             and all(type(v) is int and 0 <= v <= 65536 for v in box)
                             and box[0] < box[2] and box[1] < box[3] for box in item))
            item = [list(box) for box in item] if valid else None
        elif key in ('timings', 'solver'):
            allowed = TIMING_KEYS if key == 'timings' else SOLVER_KEYS
            valid = (type(item) is dict and not set(item) - allowed
                     and all(_solver_value(k, v) if key == 'solver' else _number(v)
                             for k, v in item.items()))
            item = json.loads(json.dumps(item)) if valid else None
        elif key in ('crop_bounds', 'full_size'):
            length = 4 if key == 'crop_bounds' else 2
            valid = (type(item) in (tuple, list) and len(item) == length
                     and all(type(v) is int and 0 <= v <= 65536 for v in item))
            item = list(item) if valid else None
        else:
            valid = type(item) is str and TOKEN.fullmatch(item) is not None
        if not valid:
            raise ValueError('invalid-metadata')
        out[key] = item
    if not {'identity', 'session', 'round', 'cycle', 'opening', 'attempt', 'frame',
            'outcome', 'reason'} <= set(out):
        raise ValueError('missing-metadata')
    if out['outcome'] not in ('success', 'failure', 'cancelled', 'missing'):
        raise ValueError('invalid-outcome')
    if shape is not None:
        bounds, size = out.get('crop_bounds'), out.get('full_size')
        if bounds is None or size is None:
            raise ValueError('missing-crop-geometry')
        x, y, w, h = bounds
        if (w, h) != (shape[1], shape[0]) or min(w, h) <= 0 or x+w > size[0] or y+h > size[1]:
            raise ValueError('invalid-crop-geometry')
    return out


def _encode(pixels, shape):
    if pixels is None:
        return None
    # Explicit BGR -> RGB conversion; the raw hash below names the source order.
    array = np.frombuffer(pixels, dtype=np.uint8).reshape(shape)
    stream = io.BytesIO()
    Image.fromarray(array[:, :, ::-1]).save(stream, format='PNG', compress_level=1)
    return stream.getvalue()


def _read(fd, limit):
    os.lseek(fd, 0, os.SEEK_SET)
    if os.fstat(fd).st_size > limit:
        raise OSError('storage-byte-limit')
    result = bytearray()
    while len(result) <= limit:
        part = os.read(fd, min(65536, limit + 1 - len(result)))
        if not part:
            return bytes(result)
        result.extend(part)
    raise OSError('storage-byte-limit')


def _info(fd, data):
    s = os.fstat(fd)
    return dict(identity=[s.st_dev, s.st_ino], bytes=len(data),
                sha256=hashlib.sha256(data).hexdigest())


def _verify(fd, record, limit):
    s = os.fstat(fd)
    if (not stat.S_ISREG(s.st_mode) or s.st_nlink != 1
            or getattr(s, 'st_file_attributes', 0) & 0x400):
        raise OSError('storage-linked')
    data = _read(fd, limit)
    if _info(fd, data) != record:
        raise OSError('storage-ownership-changed')


def _retire(root, row, limit):
    if os.name != 'nt':
        raise OSError('retirement-unsupported')
    from failure_sampling import _open_delete_image, _mark_delete_image
    # Pin all case handles and verify the entire case before deleting anything.
    handles = []
    try:
        for name, record in row['files'].items():
            fd = _open_delete_image(root / name)
            handles.append(fd)
            _verify(fd, record, limit)
        for fd in handles:
            _mark_delete_image(fd)
    finally:
        for fd in handles:
            os.close(fd)


class _Store:
    def __init__(self, root, limits):
        self.root = Path(os.path.abspath(root))
        self.limits = limits

    def write(self, case_id, metadata, png, pixels, shape):
        from diagnostic_storage import _safe, _transaction, _open_fd, LOCK
        root, limits = self.root, self.limits
        _safe(root)
        # Do not write even a lock into an existing unowned directory.
        if root.exists() and not (root / REGISTRY).exists() and list(root.iterdir()):
            raise OSError('storage-unowned')
        with _transaction(root):
            try:
                journal = _open_fd(root / REGISTRY, os.O_RDWR)
            except FileNotFoundError:
                if set(p.name for p in root.iterdir()) != {LOCK}:
                    raise OSError('storage-unowned')
                journal = _open_fd(root / REGISTRY, os.O_RDWR | os.O_CREAT | os.O_EXCL)
                initial = json.dumps(dict(schema=SCHEMA, cases=[])).encode()
                if os.write(journal, initial) != len(initial):
                    os.close(journal)
                    raise OSError('storage-short-write')
            try:
                try:
                    inventory = json.loads(_read(journal, MiB))
                    rows = inventory['cases']
                    if inventory['schema'] != SCHEMA or type(rows) is not list or len(rows) > 100:
                        raise ValueError()
                    known = {REGISTRY, LOCK}
                    for row in rows:
                        if (set(row) != {'id', 'created', 'files'}
                                or not re.fullmatch(r'[a-f0-9]{32}', row['id'])
                                or not _number(row['created']) or type(row['files']) is not dict
                                or not 1 <= len(row['files']) <= 2):
                            raise ValueError()
                        expected_names = {'case-' + row['id'] + '.json', 'case-' + row['id'] + '.png'}
                        if not set(row['files']) <= expected_names or 'case-' + row['id'] + '.json' not in row['files']:
                            raise ValueError()
                        for name, record in row['files'].items():
                            if name in known or not NAME.fullmatch(name):
                                raise ValueError()
                            known.add(name)
                            fd = _open_fd(root / name, os.O_RDONLY)
                            try:
                                _verify(fd, record, limits.case_bytes)
                            finally:
                                os.close(fd)
                    if set(p.name for p in root.iterdir()) != known:
                        raise ValueError()
                except (ValueError, KeyError, TypeError):
                    raise OSError('storage-inventory-invalid') from None
                now = time.time()
                doc = dict(schema=SCHEMA, case_id=case_id, created=now, metadata=metadata,
                           image_status='saved' if png is not None else 'missing-input',
                           pixel_format='BGR', shape=list(shape) if shape else None,
                           raw_sha256=hashlib.sha256(pixels).hexdigest() if pixels else None,
                           png_sha256=hashlib.sha256(png).hexdigest() if png else None)
                payloads = {'case-' + case_id + '.json': json.dumps(doc, separators=(',', ':'), allow_nan=False).encode()}
                if png is not None:
                    payloads['case-' + case_id + '.png'] = png
                incoming = sum(map(len, payloads.values()))
                if incoming > limits.case_bytes or incoming + 2048 > limits.max_bytes:
                    raise OSError('case-byte-limit')
                def total():
                    return sum(r['bytes'] for row in rows for r in row['files'].values())
                while rows and (len(rows) >= limits.max_cases or total()+incoming+len(json.dumps(dict(schema=SCHEMA, cases=rows)).encode())+2048 > limits.max_bytes
                                or now - min(row['created'] for row in rows) > limits.max_age):
                    oldest = min(rows, key=lambda row: row['created'])
                    _retire(root, oldest, limits.case_bytes)
                    rows.remove(oldest)
                    self._save(journal, rows)
                records = {}
                for name, payload in payloads.items():
                    fd = _open_fd(root / name, os.O_RDWR | os.O_CREAT | os.O_EXCL)
                    try:
                        if os.write(fd, payload) != len(payload):
                            raise OSError('storage-short-write')
                        os.fsync(fd)
                        records[name] = _info(fd, payload)
                    finally:
                        os.close(fd)
                rows.append(dict(id=case_id, created=now, files=records))
                self._save(journal, rows)
            finally:
                os.close(journal)

    @staticmethod
    def _save(fd, rows):
        payload = json.dumps(dict(schema=SCHEMA, cases=rows), separators=(',', ':')).encode()
        os.lseek(fd, 0, os.SEEK_SET)
        if os.write(fd, payload) != len(payload):
            raise OSError('storage-short-write')
        os.ftruncate(fd, len(payload))
        os.fsync(fd)


class GCycleEvidenceRecorder:
    def __init__(self, root, *, enabled=False, limits=None, result_callback=None,
                 full_input=False):
        # A separate process-only opt-in. No capture is performed by this class.
        self.full_input = full_input is True
        self.limits = limits or EvidenceLimits()
        if result_callback is not None and not callable(result_callback):
            raise TypeError('invalid-result-callback')
        self._result_callback = result_callback
        self._store = _Store(root, self.limits)
        self._enabled = enabled is True
        self._closed = False
        self._stopping = False
        self._thread = None
        self._condition = threading.Condition()
        self._commit = threading.Lock()
        self._queue = deque()
        self._count = self._bytes = self._generation = 0
        self._counters = Counter()
        self._last = 'disabled' if not self._enabled else 'not-started'

    @property
    def accepting(self):
        with self._condition:
            return self._enabled and not self._closed and self._thread is not None

    def start(self):
        with self._condition:
            if not self._enabled or self._closed:
                return False
            if self._thread is None:
                self._thread = threading.Thread(target=self._run, name='g-cycle-evidence', daemon=True)
                self._thread.start()
            return True

    def submit(self, roi, metadata):
        with self._condition:
            if not self._enabled or self._closed or self._thread is None:
                return self._result('disabled' if not self._enabled else 'not-started')
            try:
                shape = None
                size = 0
                if roi is not None:
                    if (type(roi) is not np.ndarray or roi.dtype != np.uint8
                            or roi.ndim != 3 or roi.shape[2] != 3 or min(roi.shape[:2]) <= 0):
                        return self._result('invalid-roi')
                    size = roi.size
                    shape = roi.shape
                    if size > self.limits.roi_bytes:
                        return self._result('roi-byte-limit')
                clean = _metadata(metadata, shape)
            except (ValueError, TypeError, OverflowError):
                return self._result('invalid-metadata')
            charge = size + len(json.dumps(clean).encode()) + 1024
            if self._count >= self.limits.pending_count:
                return self._result('queue-full')
            if self._bytes + charge > self.limits.pending_bytes:
                return self._result('queue-byte-limit')
            pixels = roi.tobytes(order='C') if roi is not None else None
            case_id = uuid.uuid4().hex
            self._queue.append((case_id, clean, pixels, shape, charge, self._generation))
            self._count += 1
            self._bytes += charge
            self._condition.notify_all()
            if pixels is None:
                self._bump('missing-input')
            return self._result('queued', case_id)

    def _bump(self, status):
        self._counters[status] = min(2**63 - 1, self._counters[status] + 1)

    def _result(self, status, case_id=None):
        self._bump(status)
        self._last = status
        return dict(status=status, case_id=case_id)

    def snapshot(self):
        with self._condition:
            return dict(status=self._last, counters=dict(self._counters),
                        pending_count=self._count, pending_bytes=self._bytes,
                        running=self._thread is not None and self._thread.is_alive())

    def disable(self, wait=False):
        # Wait only for a disk commit already begun, never for PNG encoding.
        cancelled = []
        with self._commit:
            with self._condition:
                self._enabled = False
                self._closed = True
                self._generation += 1
                while self._queue:
                    job = self._queue.popleft()
                    cancelled.append(job)
                self._condition.notify_all()
        for job in cancelled:
            self._finish(job, 'cancelled')
        if wait and self._thread is not None:
            self._thread.join()

    @property
    def pendingWrites(self):
        with self._condition:
            return self._count

    def shutdown(self, wait=False):
        """Stop admission and drain accepted jobs; disable() instead cancels them."""
        with self._condition:
            self._enabled = False
            self._stopping = True
            self._condition.notify_all()
        if wait and self._thread is not None:
            self._thread.join()

    def cancel(self):
        """Close-coordinator compatibility: stop admission and drain accepted work."""
        self.shutdown(wait=False)

    def idle(self, timeout=5.0):
        with self._condition:
            return self._condition.wait_for(lambda: self._count == 0, timeout)

    def _finish(self, job, status):
        """Report once without locks; callback must be a nonblocking notification.

        Worker completions run on the writer; queued disable cancellations run on
        the disabling caller. Neither callback is a UI update or solver verdict.
        pendingWrites includes callbacks until they return, so lifecycle polling
        does not declare a job drained before its notification has been emitted.
        """
        case_id, metadata = job[:2]
        with self._condition:
            self._result(status)
        try:
            if self._result_callback is not None:
                # Scalar allowlist only: no paths, exception strings, pixels,
                # solver matrices, mutable nested objects, or arbitrary metadata.
                payload = dict(status=status, case_id=case_id,
                               metadata={key: metadata[key] for key in RESULT_KEYS
                                         if key in metadata})
                self._result_callback(payload)
        except BaseException:
            # A broken observer must not terminate the writer or change the
            # already determined saved/failed/cancelled outcome.
            with self._condition:
                self._bump('callback-error')
        finally:
            with self._condition:
                self._count -= 1
                self._bytes -= job[4]
                self._condition.notify_all()

    def _run(self):
        # Best effort worker-thread priority on Windows only; never reprioritize
        # the solver process on POSIX. Lack of priority support is not fatal.
        if os.name == 'nt':
            try:
                import ctypes
                kernel = ctypes.WinDLL('kernel32', use_last_error=True)
                kernel.GetCurrentThread.restype = ctypes.c_void_p
                kernel.SetThreadPriority.argtypes = [ctypes.c_void_p, ctypes.c_int]
                kernel.SetThreadPriority(kernel.GetCurrentThread(), -2)
            except (OSError, AttributeError):
                pass
        while True:
            with self._condition:
                self._condition.wait_for(lambda: self._queue or self._closed or self._stopping)
                if self._closed or (self._stopping and not self._queue):
                    return
                job = self._queue.popleft()
            case_id, metadata, pixels, shape, charge, generation = job
            status = 'storage-error'
            try:
                png = _encode(pixels, shape)
                with self._commit:
                    with self._condition:
                        cancelled = self._closed or generation != self._generation
                    if cancelled:
                        status = 'cancelled'
                    else:
                        self._store.write(case_id, metadata, png, pixels, shape)
                        status = 'saved' if png is not None else 'saved-missing-input'
            except Exception as error:
                # Do not persist exception text (which may contain paths/data).
                known = {'case-byte-limit', 'storage-unowned', 'storage-inventory-invalid',
                         'storage-ownership-changed', 'storage-byte-limit', 'retirement-unsupported'}
                status = str(error) if str(error) in known else 'storage-error'
            finally:
                self._finish(job, status)
