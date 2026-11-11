"""Bounded sampling admission and fail-closed automatic evidence storage.

The legacy namespace is read-only during cleanup. V2 ownership binds original
file identity, size and digest; only Windows can retire an image by its handle.
"""
from collections import OrderedDict
from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import threading
import weakref

_transaction_guard = threading.Lock()
_transaction_locks = weakref.WeakValueDictionary()
_lock_name = '.automatic-sampling.lock'
_automatic_state = threading.local()
_IMAGE_BYTES = 32 * 1024 * 1024  # One lossless full-client PNG, including 4K BGRA.
_CONTEXT_IMAGE_BYTES = 64 * 1024 * 1024
_LEGACY_IMAGE_BYTES = 12 * 1024 * 1024
_IMAGE_COUNT = 6
_JOURNAL_BYTES = 256 * 1024


def _directory(root, namespace):
    if namespace not in ('automatic', 'automatic-v2'):
        raise ValueError('automatic-namespace-invalid')
    return Path(root).absolute() / 'failures' / namespace


def _require_transaction(root, namespace):
    directory = _directory(root, namespace)
    current = getattr(_automatic_state, 'journal', None)
    if os.name != 'nt' or current is None or current[0] != directory:
        raise OSError('automatic-safe-storage-unsupported')
    return directory


@contextmanager
def automatic_storage_transaction(root, *, create=False, namespace='automatic'):
    """Pin the directory chain and exclusively hold the journal on Windows."""
    directory = _directory(root, namespace)
    from diagnostic_storage import _safe, _pinned_root, _open_fd, _read_fd
    try:
        _safe(directory)
    except OSError:
        yield 'storage-redirected'; return
    if not directory.exists() and not create:
        yield 'storage-absent'; return
    if os.name != 'nt':
        yield 'automatic-safe-storage-unsupported'; return
    if getattr(_automatic_state, 'journal', None) is not None:
        yield 'automatic-storage-busy'; return
    if not _transaction_guard.acquire(blocking=False):
        yield 'automatic-storage-busy'; return
    try:
        lock = _transaction_locks.setdefault(str(directory), threading.Lock())
    finally:
        _transaction_guard.release()
    if not lock.acquire(blocking=False):
        yield 'automatic-storage-busy'; return
    descriptor = journal_fd = None
    try:
        with _pinned_root(directory):
            try:
                try: descriptor = _open_fd(directory / _lock_name, os.O_RDWR | os.O_CREAT | os.O_EXCL)
                except FileExistsError: descriptor = _open_fd(directory / _lock_name, os.O_RDWR)
                if os.fstat(descriptor).st_size:
                    yield 'storage-lock-unavailable'; return
                import msvcrt
                msvcrt.locking(descriptor, msvcrt.LK_NBLCK, 1)
                journal = directory / 'automatic-failures.jsonl'
                try: journal_fd = _open_fd(journal, os.O_RDWR | os.O_CREAT | os.O_EXCL)
                except FileExistsError: journal_fd = _open_fd(journal, os.O_RDWR)
                data = _read_fd(journal_fd)
                if len(data) > _JOURNAL_BYTES:
                    yield 'journal-byte-limit'; return
            except OSError:
                yield 'automatic-storage-handle-unavailable'; return
            _automatic_state.journal = (directory, journal_fd, data)
            try:
                yield 'ready'
            finally:
                _automatic_state.journal = None
    finally:
        if journal_fd is not None: os.close(journal_fd)
        if descriptor is not None: os.close(descriptor)
        lock.release()


def _image_path(root, row, namespace):
    relative = row.get('samplePath')
    if not isinstance(relative, str) or not re.fullmatch(
            r'failures/' + re.escape(namespace) + r'/auto-[0-9a-f]{32}\.png', relative):
        raise OSError('ownership-unavailable')
    return _directory(root, namespace) / relative.rsplit('/', 1)[-1]


def _validate_row(row):
    identity = row.get('sampleIdentity')
    if (not isinstance(identity, (list, tuple)) or len(identity) != 2
            or any(type(n) is not int or n < 0 for n in identity)
            or identity[1] == 0
            or type(row.get('sampleBytes')) is not int
            or not 0 < row['sampleBytes'] <= _IMAGE_BYTES
            or not isinstance(row.get('sampleSha256'), str)
            or not re.fullmatch(r'[0-9a-f]{64}', row['sampleSha256'])):
        raise OSError('ownership-unavailable')


def _image_info(fd):
    info = os.fstat(fd)
    if (not stat.S_ISREG(info.st_mode) or info.st_nlink != 1
            or getattr(info, 'st_file_attributes', 0) & 0x400):
        raise OSError('ownership-unavailable')
    return info


def _read_image_fd(fd, limit=_IMAGE_BYTES):
    """A PNG has its own bound, separate from bounded text journals."""
    if type(limit) is not int or not 0 < limit <= _IMAGE_BYTES:
        raise OSError('automatic-image-byte-limit')
    os.lseek(fd, 0, os.SEEK_SET)
    parts, total = [], 0
    while True:
        part = os.read(fd, min(65536, limit + 1 - total))
        if not part:
            return b''.join(parts)
        total += len(part)
        if total > limit:
            raise OSError('automatic-image-byte-limit')
        parts.append(part)


def _verified_image_fd(fd, row, limit=_IMAGE_BYTES):
    _validate_row(row)
    before = _image_info(fd)
    if ([before.st_dev, before.st_ino] != list(row['sampleIdentity'])
            or before.st_size != row['sampleBytes']):
        raise OSError('ownership-changed')
    data = _read_image_fd(fd, limit)
    after = _image_info(fd)
    if ([after.st_dev, after.st_ino] != list(row['sampleIdentity'])
            or after.st_size != len(data) or len(data) != row['sampleBytes']
            or hashlib.sha256(data).hexdigest() != row['sampleSha256']):
        raise OSError('ownership-changed')
    return data


def _open_image_read(path):
    if os.name == 'nt':
        from diagnostic_storage import _open_fd
        return _open_fd(path, os.O_RDONLY)
    # Read-only offline inventory is useful, but never authorizes POSIX mutation.
    return os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)


def automatic_storage_inventory(root, *, namespace='automatic'):
    """Read bounded ownership facts; callers MUST revalidate before using a file."""
    import itertools
    directory = _directory(root, namespace)
    base = Path(root).absolute()
    result = dict(status='ready', images=[], imageRows=[], missingRows=[], rows=[],
                  directory=directory, retainedAuxiliaryCount=0, retainedAuxiliaryBytes=0,
                  imageBytes=0)
    from diagnostic_storage import _safe
    try:
        _safe(directory)
        if not directory.exists():
            return result
        entries = list(itertools.islice(directory.iterdir(), 129))
        if len(entries) > 128:
            return dict(result, status='cleanup-entry-limit')
        auxiliary = [p for p in entries if re.fullmatch(r'\.automatic-journal-[a-z0-9_]{8}\.tmp', p.name)]
        result['retainedAuxiliaryCount'] = len(auxiliary)
        if len(auxiliary) > 4:
            return dict(result, status='automatic-auxiliary-count-limit')
        for path in auxiliary:
            info = path.lstat()
            if (not stat.S_ISREG(info.st_mode) or info.st_nlink != 1
                    or getattr(info, 'st_file_attributes', 0) & 0x400):
                return dict(result, status='automatic-auxiliary-link-unavailable')
            result['retainedAuxiliaryBytes'] += info.st_size
        if result['retainedAuxiliaryBytes'] > _JOURNAL_BYTES:
            return dict(result, status='automatic-auxiliary-byte-limit')
        # V2 has never written temporary journals. Any such object is unknown.
        if namespace == 'automatic-v2' and auxiliary:
            return dict(result, status='ownership-unavailable')
        journal = directory / 'automatic-failures.jsonl'
        candidates = [p for p in entries if p.name not in (journal.name, _lock_name) and p not in auxiliary]
        if len(candidates) > _IMAGE_COUNT:
            return dict(result, status='cleanup-image-limit')
        if journal in entries:
            _safe(journal)
            current = getattr(_automatic_state, 'journal', None)
            if current is not None and current[0] == directory:
                from diagnostic_storage import _read_fd
                payload = _read_fd(current[1])
                if payload != current[2]:
                    return dict(result, status='automatic-journal-changed')
            else:
                fd = _open_image_read(journal)
                try:
                    _image_info(fd)
                    payload = _read_image_fd(fd, _JOURNAL_BYTES)
                finally:
                    os.close(fd)
            if len(payload) > _JOURNAL_BYTES:
                return dict(result, status='journal-byte-limit')
            for line in payload.splitlines():
                try:
                    row = json.loads(line)
                    if row.get('event') == 'automatic-failure-image-intent':
                        return dict(result, status='automatic-image-recovery-required')
                    if row.get('event') != 'automatic-failure-sample':
                        raise ValueError()
                    result['rows'].append((line, row))
                except (ValueError, TypeError, AttributeError):
                    return dict(result, status='journal-invalid')
        bindings = {}
        for _, row in result['rows']:
            if not row.get('samplePath'):
                continue
            if namespace == 'automatic-v2':
                _image_path(root, row, namespace)
                _validate_row(row)
                if row['samplePath'] in bindings:
                    return dict(result, status='ownership-unavailable')
            bindings[row['samplePath']] = row
        present = set()
        for path in candidates:
            relative = path.relative_to(base).as_posix()
            row = bindings.get(relative)
            if not row or not re.fullmatch(r'auto-[0-9a-f]{32}\.png', path.name):
                return dict(result, status='ownership-unavailable')
            _safe(path)
            fd = _open_image_read(path)
            try:
                if namespace == 'automatic-v2':
                    payload = _verified_image_fd(fd, row)
                else:
                    _image_info(fd)
                    payload = _read_image_fd(fd, _LEGACY_IMAGE_BYTES)
                    if hashlib.sha256(payload).hexdigest() != row.get('sampleSha256'):
                        return dict(result, status='ownership-changed')
            finally:
                os.close(fd)
            result['imageBytes'] += len(payload)
            if result['imageBytes'] > (_CONTEXT_IMAGE_BYTES if namespace == 'automatic-v2' else _LEGACY_IMAGE_BYTES):
                return dict(result, status='automatic-image-byte-limit')
            result['images'].append(path)
            result['imageRows'].append(row)
            present.add(relative)
        result['missingRows'] = [r for key, r in bindings.items() if key not in present]
        return result
    except OSError as error:
        reason = str(error)
        if reason == 'diagnostic-storage-hardlinked':
            reason = 'ownership-unavailable'
        return dict(result, status=reason if reason in (
            'ownership-unavailable', 'ownership-changed', 'automatic-image-byte-limit') else 'storage-redirected')


def replace_automatic_journal(directory, data, *, namespace='automatic'):
    """Rewrite the same validated journal handle, never a pathname replacement."""
    from diagnostic_storage import _read_fd
    directory = Path(directory).absolute()
    if directory.name != namespace or namespace not in ('automatic', 'automatic-v2'):
        raise OSError('automatic-namespace-invalid')
    current = getattr(_automatic_state, 'journal', None)
    if os.name != 'nt' or current is None or current[0] != directory:
        raise OSError('automatic-safe-storage-unsupported')
    if len(data) > _JOURNAL_BYTES:
        raise OSError('journal-byte-limit')
    _, fd, expected = current
    if _read_fd(fd) != expected:
        raise OSError('automatic-journal-changed')
    os.lseek(fd, 0, os.SEEK_SET)
    if os.write(fd, data) != len(data):
        raise OSError('automatic-journal-short-write')
    os.ftruncate(fd, len(data))
    os.fsync(fd)
    _automatic_state.journal = (directory, fd, data)


def create_automatic_image(root, relative, payload, *, namespace='automatic-v2'):
    """CREATE_NEW/write/identify on one deny-write/delete Windows handle.

    Caller first persists an image intent. Failures leave that intent pending;
    partial images are never adopted, retried in-place, or path-deleted.
    """
    _require_transaction(root, namespace)
    if namespace != 'automatic-v2':
        raise OSError('automatic-legacy-read-only')
    path = _image_path(root, {'samplePath': relative}, namespace)
    if not isinstance(payload, bytes) or not 0 < len(payload) <= _IMAGE_BYTES:
        raise OSError('automatic-image-byte-limit')
    from diagnostic_storage import _open_fd
    fd = _open_fd(path, os.O_RDWR | os.O_CREAT | os.O_EXCL)
    try:
        info = _image_info(fd)
        if info.st_size:
            raise OSError('ownership-unavailable')
        if os.write(fd, payload) != len(payload):
            raise OSError('automatic-image-short-write')
        os.fsync(fd)
        info = _image_info(fd)
        row = dict(sampleIdentity=[info.st_dev, info.st_ino], sampleBytes=len(payload),
                   sampleSha256=hashlib.sha256(payload).hexdigest())
        _verified_image_fd(fd, row)
        return row
    finally:
        os.close(fd)


def read_automatic_image(root, row, *, limit=_IMAGE_BYTES, namespace='automatic-v2'):
    """Read/verify an original object; errors contain codes, never OS path text."""
    try:
        _require_transaction(root, namespace)
        path = _image_path(root, row, namespace)
        _validate_row(row)
        fd = _open_image_read(path)
        try:
            return _verified_image_fd(fd, row, limit)
        finally:
            os.close(fd)
    except FileNotFoundError:
        raise OSError('automatic-image-missing') from None
    except OSError as error:
        reason = str(error)
        if reason not in ('automatic-safe-storage-unsupported', 'ownership-unavailable',
                          'ownership-changed', 'automatic-image-byte-limit'):
            reason = 'automatic-image-read-unavailable'
        raise OSError(reason) from None


def _open_delete_image(path):
    import ctypes
    import msvcrt
    from ctypes import wintypes
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    create = kernel.CreateFileW
    create.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, ctypes.c_void_p,
                       wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE]
    create.restype = wintypes.HANDLE
    # GENERIC_READ | DELETE, FILE_SHARE_READ only, OPEN_EXISTING,
    # FILE_FLAG_OPEN_REPARSE_POINT. No path unlink/rename fallback exists.
    handle = create(str(path), 0x80000000 | 0x00010000, 1, None, 3, 0x00200000, None)
    if handle == wintypes.HANDLE(-1).value:
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        return msvcrt.open_osfhandle(handle, os.O_RDONLY | os.O_BINARY)
    except BaseException:
        close = kernel.CloseHandle
        close.argtypes = [wintypes.HANDLE]
        close.restype = wintypes.BOOL
        close(handle)
        raise


def _mark_delete_image(fd):
    import ctypes
    import msvcrt
    from ctypes import wintypes
    class FileDispositionInfo(ctypes.Structure):
        _fields_ = [('DeleteFile', ctypes.c_ubyte)]
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    dispose = kernel.SetFileInformationByHandle
    dispose.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD]
    dispose.restype = wintypes.BOOL
    value = FileDispositionInfo(1)
    if not dispose(msvcrt.get_osfhandle(fd), 4, ctypes.byref(value), ctypes.sizeof(value)):
        raise ctypes.WinError(ctypes.get_last_error())


def retire_automatic_image(root, row, *, namespace='automatic-v2'):
    """Verify and retire the same Windows file handle; never trust inventory IO."""
    _require_transaction(root, namespace)
    if namespace != 'automatic-v2':
        raise OSError('automatic-legacy-read-only')
    path = _image_path(root, row, namespace)
    _validate_row(row)  # Never fill missing identity from today's pathname.
    try:
        fd = _open_delete_image(path)
    except FileNotFoundError:
        return 'missing'  # Crash after deletion but before journal repair.
    try:
        _verified_image_fd(fd, row)
        _mark_delete_image(fd)
    finally:
        os.close(fd)
    return 'retired'


def _without_image(row):
    clean = dict(row)
    for key in ('samplePath', 'sampleSha256', 'sampleBytes', 'sampleIdentity'):
        clean.pop(key, None)
    clean['missingReason'] = 'automatic-image-retired'
    return clean


def _repair_retired_rows(inventory, retired, *, namespace):
    rows = [_without_image(row) if row.get('samplePath') in retired else row
            for _, row in inventory['rows']]
    payload = b''.join((json.dumps(row, ensure_ascii=False, separators=(',', ':')) + '\n').encode('utf-8') for row in rows)
    replace_automatic_journal(inventory['directory'], payload, namespace=namespace)


def _row_time(row):
    from datetime import datetime
    try:
        value = datetime.fromisoformat(row['recordedAt']).timestamp()
        import math
        if not math.isfinite(value):
            raise ValueError()
        return value
    except (ValueError, TypeError, KeyError, OverflowError):
        raise OSError('journal-invalid-timestamp') from None


def _retire_rows(root, inventory, rows, *, namespace, report):
    removed = 0
    for row in rows:
        outcome = retire_automatic_image(root, row, namespace=namespace)
        if outcome == 'retired':
            removed += 1
            report['removed'] += 1
        # Persist each retirement before moving to another object. On failure the
        # durable row remains, and the next inventory sees a recoverable missing.
        _repair_retired_rows(inventory, {row['samplePath']}, namespace=namespace)
        inventory = automatic_storage_inventory(root, namespace=namespace)
        if inventory['status'] != 'ready':
            raise OSError(inventory['status'])
    return removed


def _recover_automatic_image_intent_locked(root, *, namespace='automatic'):
    """Cancel only a proven missing V2 creation intent; preserve every object.

    Complete and partial images remain pending. An intent never proves final
    current-frame consent and never licenses adoption or deletion of a file.
    """
    if namespace != 'automatic-v2':
        return 'automatic-legacy-read-only'
    try:
        directory = _require_transaction(root, namespace)
        from diagnostic_storage import _read_fd
        _, fd, expected = _automatic_state.journal
        payload = _read_fd(fd)
        if payload != expected:
            return 'automatic-journal-changed'
        if len(payload) > _JOURNAL_BYTES:
            return 'journal-byte-limit'
        try:
            rows = [json.loads(line) for line in payload.splitlines()]
            if any(not isinstance(row, dict) or row.get('event') not in (
                    'automatic-failure-sample', 'automatic-failure-image-intent') for row in rows):
                return 'journal-invalid'
        except (ValueError, TypeError):
            return 'journal-invalid'
        intents = [row for row in rows if row.get('event') == 'automatic-failure-image-intent']
        if not intents:
            return 'ready'
        if len(intents) != 1:
            return 'automatic-image-recovery-limit'
        intent = intents[0]
        path = _image_path(root, intent, namespace)
        if (intent.get('imageTransactionSchema') != 'automatic-image-intent-v1'
                or type(intent.get('sampleBytes')) is not int
                or not 0 < intent['sampleBytes'] <= _IMAGE_BYTES
                or not isinstance(intent.get('sampleSha256'), str)
                or not re.fullmatch(r'[0-9a-f]{64}', intent['sampleSha256'])
                or sum(row.get('samplePath') == intent['samplePath'] for row in rows) != 1):
            return 'automatic-image-intent-invalid'
        try:
            image_fd = _open_image_read(path)
        except FileNotFoundError:
            pass
        else:
            os.close(image_fd)
            return 'automatic-image-recovery-required'
        rows.remove(intent)
        data = b''.join((json.dumps(row, ensure_ascii=False) + '\n').encode('utf-8') for row in rows)
        replace_automatic_journal(directory, data, namespace=namespace)
        return 'ready'
    except OSError:
        return 'automatic-image-recovery-unavailable'


def _operation_status(error):
    # No OS exception text (often absolute private paths) enters diagnostics.
    code = str(error)
    known = {
        'automatic-safe-storage-unsupported', 'automatic-legacy-read-only',
        'automatic-image-byte-limit', 'ownership-unavailable', 'ownership-changed',
        'automatic-image-recovery-required', 'automatic-image-recovery-limit',
        'automatic-image-intent-invalid', 'automatic-image-recovery-unavailable',
        'automatic-journal-changed', 'journal-byte-limit', 'journal-invalid',
        'journal-invalid-timestamp', 'automatic-journal-short-write',
        'storage-redirected', 'cleanup-entry-limit', 'cleanup-image-limit',
        'automatic-auxiliary-count-limit', 'automatic-auxiliary-link-unavailable',
        'automatic-auxiliary-byte-limit',
    }
    return code if code in known else 'automatic-storage-operation-failed'


def _recovering_inventory(root, namespace):
    inventory = automatic_storage_inventory(root, namespace=namespace)
    if inventory['status'] == 'automatic-image-recovery-required':
        status = _recover_automatic_image_intent_locked(root, namespace=namespace)
        if status != 'ready':
            raise OSError(status)
        inventory = automatic_storage_inventory(root, namespace=namespace)
    return inventory


def ensure_automatic_capacity(root, incoming_bytes, *, namespace='automatic-v2'):
    """Reserve space under the caller's transaction, retiring oldest owned rows."""
    report = dict(status='ready', removed=0, complete=True)
    try:
        _require_transaction(root, namespace)
        if namespace != 'automatic-v2':
            raise OSError('automatic-legacy-read-only')
        if type(incoming_bytes) is not int or not 0 < incoming_bytes <= _IMAGE_BYTES:
            raise OSError('automatic-image-byte-limit')
        inventory = _recovering_inventory(root, namespace)
        if inventory['status'] != 'ready':
            raise OSError(inventory['status'])
        if inventory['missingRows']:
            _repair_retired_rows(inventory, {r['samplePath'] for r in inventory['missingRows']}, namespace=namespace)
            inventory = automatic_storage_inventory(root, namespace=namespace)
            if inventory['status'] != 'ready':
                raise OSError(inventory['status'])
        ordered = sorted(inventory['imageRows'], key=_row_time)
        count, size, retire = len(ordered), inventory['imageBytes'], []
        while count + 1 > _IMAGE_COUNT or size + incoming_bytes > _CONTEXT_IMAGE_BYTES:
            row = ordered.pop(0)
            retire.append(row)
            count -= 1
            size -= row['sampleBytes']
        _retire_rows(root, inventory, retire, namespace=namespace, report=report)
    except OSError as error:
        report.update(status=_operation_status(error), complete=False)
    return report


def cleanup_automatic_failure_samples(root, *, now=None, namespace='automatic'):
    """Legacy is read-only; V2 safely expires proven original objects after 7d."""
    if namespace == 'automatic':
        inventory = automatic_storage_inventory(root, namespace=namespace)
        return dict(status=inventory['status'], removed=0, complete=inventory['status'] == 'ready',
                    retainedAuxiliaryCount=inventory['retainedAuxiliaryCount'],
                    retainedAuxiliaryBytes=inventory['retainedAuxiliaryBytes'])
    with automatic_storage_transaction(root, namespace=namespace) as status:
        if status == 'storage-absent':
            return dict(status='ready', removed=0, complete=True)
        if status != 'ready':
            return dict(status=status, removed=0, complete=False)
        return _cleanup_automatic_failure_samples_locked(root, now=now, namespace=namespace)


def _cleanup_automatic_failure_samples_locked(root, *, now=None, namespace='automatic'):
    import time
    if namespace == 'automatic':
        return cleanup_automatic_failure_samples(root, now=now, namespace=namespace)
    now = time.time() if now is None else now
    report = dict(status='ready', removed=0, complete=True)
    try:
        _require_transaction(root, namespace)
        inventory = _recovering_inventory(root, namespace)
        if inventory['status'] != 'ready':
            raise OSError(inventory['status'])
        expired = [row for row in inventory['imageRows'] if now - _row_time(row) > 7 * 86400]
        if inventory['missingRows']:
            _repair_retired_rows(inventory, {r['samplePath'] for r in inventory['missingRows']}, namespace=namespace)
            inventory = automatic_storage_inventory(root, namespace=namespace)
            if inventory['status'] != 'ready':
                raise OSError(inventory['status'])
        _retire_rows(root, inventory, expired, namespace=namespace, report=report)
    except OSError as error:
        report.update(status=_operation_status(error), complete=False)
    return report


class FailureSampling:
    interval_seconds = 60.0
    max_keys = 512
    max_disk_bytes = 512 * 1024 * 1024

    def __init__(self):
        self.saved = OrderedDict()
        self.pending = set()
        self.retry_at = 0.0
        self.errors = 0
        self.last_status = 'ready'

    def eligible(self, key, now):
        if now < self.retry_at:
            self.last_status = 'write-error-backoff'
            return False
        if key is not None and key in self.pending:
            self.last_status = 'pending'
            return False
        if key is not None and now - self.saved.get(key, float('-inf')) < self.interval_seconds:
            self.last_status = 'sample-cooldown'
            return False
        return True

    def completed(self, key, now, success):
        self.pending.discard(key)
        if success:
            self.saved[key] = now
            self.saved.move_to_end(key)
            while len(self.saved) > self.max_keys:
                self.saved.popitem(last=False)
            self.errors = 0
            self.retry_at = 0.0
            self.last_status = 'saved'
        else:
            self.errors = min(self.errors + 1, 5)
            self.retry_at = now + min(60.0, 2.0 ** self.errors)
            self.last_status = 'write-error-backoff'
