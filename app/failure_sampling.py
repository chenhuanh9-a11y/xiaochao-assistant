"""Bounded sampling admission; called under the store's short submit lock."""
from collections import OrderedDict
from contextlib import contextmanager
import threading
import weakref

_transaction_guard = threading.Lock()
_transaction_locks = weakref.WeakValueDictionary()
_lock_name = '.automatic-sampling.lock'


@contextmanager
def automatic_storage_transaction(root, *, create=False):
    """One nonblocking root transaction shared by threads and processes.

    The reserved zero-byte lock file is persistent; it is never truncated or
    removed (which would split file locks across processes).
    """
    import os
    from pathlib import Path
    base = Path(root).resolve()
    directory = base / 'failures' / 'automatic'
    if directory.is_symlink() or directory.resolve() != directory:
        yield 'storage-redirected'
        return
    if not directory.exists() and not create:
        yield 'storage-absent'
        return
    if create:
        directory.mkdir(parents=True, exist_ok=True)
    if not _transaction_guard.acquire(blocking=False):
        yield 'automatic-storage-busy'
        return
    try:
        lock = _transaction_locks.setdefault(str(directory), threading.Lock())
    finally:
        _transaction_guard.release()
    if not lock.acquire(blocking=False):
        yield 'automatic-storage-busy'
        return
    descriptor = None
    locked = False
    try:
        path = directory / _lock_name
        if path.is_symlink() or (path.exists() and (not path.is_file() or path.stat().st_nlink != 1 or path.stat().st_size)):
            yield 'storage-lock-unavailable'
            return
        descriptor = os.open(path, os.O_RDWR | os.O_CREAT | getattr(os, 'O_BINARY', 0), 0o600)
        if os.fstat(descriptor).st_nlink != 1 or os.fstat(descriptor).st_size:
            yield 'storage-lock-unavailable'
            return
        try:
            if os.name == 'nt':
                import msvcrt
                msvcrt.locking(descriptor, msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
            locked = True
        except OSError:
            yield 'automatic-storage-busy'
            return
        yield 'ready'
    finally:
        if descriptor is not None:
            if locked:
                if os.name == 'nt':
                    import msvcrt
                    os.lseek(descriptor, 0, os.SEEK_SET)
                    msvcrt.locking(descriptor, msvcrt.LK_UNLCK, 1)
                else:
                    import fcntl
                    fcntl.flock(descriptor, fcntl.LOCK_UN)
            os.close(descriptor)
        lock.release()


def automatic_storage_inventory(root):
    """Bounded, journal-and-hash verified ownership; unknown files are preserved."""
    import hashlib
    import itertools
    import json
    from pathlib import Path
    import re
    base = Path(root).resolve()
    directory = base / 'failures' / 'automatic'
    result = dict(status='ready', images=[], rows=[], directory=directory)
    if directory.resolve() != directory or directory.is_symlink():
        return dict(result, status='storage-redirected')
    if not directory.exists():
        return result
    entries = list(itertools.islice(directory.iterdir(), 129))
    if len(entries) > 128:
        return dict(result, status='cleanup-entry-limit')
    journal = directory / 'automatic-failures.jsonl'
    if journal.exists():
        if journal.is_symlink() or not journal.is_file() or journal.stat().st_nlink != 1:
            return dict(result, status='journal-link-unavailable')
        if journal.stat().st_size > 256*1024:
            return dict(result, status='journal-byte-limit')
        with journal.open('rb') as stream:
            payload = stream.read(256*1024+1)
        if len(payload) > 256*1024:
            return dict(result, status='journal-byte-limit')
        for line in payload.splitlines():
            try:
                row = json.loads(line)
                if row.get('event') != 'automatic-failure-sample':
                    raise ValueError()
                result['rows'].append((line, row))
            except (ValueError, TypeError, AttributeError):
                return dict(result, status='journal-invalid')
    hashes = {row.get('samplePath'): row.get('sampleSha256') for _, row in result['rows'] if row.get('samplePath')}
    candidates = [p for p in entries if p.name not in (journal.name, _lock_name)]
    if len(candidates) > 6:
        return dict(result, status='cleanup-image-limit')
    for path in candidates:
        relative = path.relative_to(base).as_posix()
        if (not re.fullmatch(r'auto-[0-9a-f]{32}\.png', path.name) or path.is_symlink()
                or not path.is_file() or path.stat().st_nlink != 1
                or path.stat().st_size > 12*1024*1024 or not hashes.get(relative)):
            result['status'] = 'ownership-unavailable'
            continue
        with path.open('rb') as stream:
            payload = stream.read(12*1024*1024+1)
        if len(payload) > 12*1024*1024 or hashlib.sha256(payload).hexdigest() != hashes[relative]:
            result['status'] = 'ownership-changed'
            continue
        result['images'].append(path)
    return result


def replace_automatic_journal(directory, data):
    """Atomic replacement never truncates an existing inode or follows a link."""
    import os
    import tempfile
    if directory.is_symlink() or directory.resolve(strict=True) != directory:
        raise OSError('storage-redirected')
    journal = directory / 'automatic-failures.jsonl'
    if journal.is_symlink() or (journal.exists() and journal.stat().st_nlink != 1):
        raise OSError('journal-link-unavailable')
    descriptor, name = tempfile.mkstemp(prefix='.automatic-journal-', suffix='.tmp', dir=directory)
    try:
        with os.fdopen(descriptor, 'wb') as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(name, journal)
    finally:
        from pathlib import Path
        Path(name).unlink(missing_ok=True)


def cleanup_automatic_failure_samples(root, *, now=None):
    """Expire verified automatic objects; return explicit incomplete status."""
    with automatic_storage_transaction(root) as status:
        if status == 'storage-absent':
            return dict(status='ready', removed=0, complete=True)
        if status != 'ready':
            return dict(status=status, removed=0, complete=False)
        return _cleanup_automatic_failure_samples_locked(root, now=now)


def _cleanup_automatic_failure_samples_locked(root, *, now=None):
    """Internal: caller owns automatic_storage_transaction for the entire write."""
    from datetime import datetime
    import time
    now = time.time() if now is None else now
    inventory = automatic_storage_inventory(root)
    report = dict(status=inventory['status'], removed=0, complete=inventory['status'] == 'ready')
    if inventory['status'] != 'ready':
        return report
    for path in inventory['images']:
        if now - path.stat().st_mtime > 7*86400:
            path.unlink()
            report['removed'] += 1
    rows = []
    for line, row in inventory['rows']:
        try:
            sample = row.get('samplePath')
            still_owned = sample and any(p.name == sample.rsplit('/', 1)[-1] and p.exists() for p in inventory['images'])
            if still_owned or now - datetime.fromisoformat(row['recordedAt']).timestamp() <= 7*86400:
                rows.append(line)
        except (ValueError, TypeError, KeyError):
            return dict(report, status='journal-invalid-timestamp', complete=False)
    if inventory['directory'].is_dir():
        replace_automatic_journal(inventory['directory'], b'\n'.join(rows[-24:]) + (b'\n' if rows else b''))
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
