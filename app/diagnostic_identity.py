"""Random, resettable diagnostic identity; no hardware or account inputs.

Construction may read the application's settings directory. Producers only read
memory. A session is process-scoped; each stream counts attempted records,
including suppressed records, so gaps and bounded loss ranges are explicit.
"""
from collections import deque
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import threading
from uuid import uuid4

_guard = threading.RLock()
_process = (os.getpid(), uuid4().hex)
_installs = {}
_streams = {}


def _moment():
    return datetime.now(timezone.utc).isoformat(timespec='milliseconds')


class Installation:
    def __init__(self, settings_dir):
        self.path = Path(settings_dir) / 'diagnostic-identity.json'
        self.install_id = None
        self.status = 'not-loaded'
        self._load()

    def _safe(self):
        import stat
        for path in (self.path, *self.path.parents):
            if path.exists() or path.is_symlink():
                info = path.lstat()
                if (stat.S_ISLNK(info.st_mode) or info.st_nlink > 1 and path.is_file()
                        or getattr(info, 'st_file_attributes', 0) & 0x400):
                    raise ValueError('identity-path-linked')

    def _load(self):
        try:
            self._safe()
            self.path.parent.mkdir(parents=True, exist_ok=True)
            if not self.path.exists():
                value = uuid4().hex
                try:
                    with self.path.open('x', encoding='utf-8') as handle:
                        json.dump({'installId': value}, handle)
                except FileExistsError:
                    pass  # Another process won initial creation; read its ID.
            if self.path.stat().st_size > 256:
                raise ValueError('identity-too-large')
            value = json.loads(self.path.read_text('utf-8'))['installId']
            if not isinstance(value, str) or len(value) != 32 or any(c not in '0123456789abcdef' for c in value):
                raise ValueError('identity-invalid')
            self.install_id, self.status = value, 'persisted'
        except (OSError, ValueError, KeyError, TypeError) as error:
            self.status = 'unavailable:' + type(error).__name__

    def reset(self):
        import tempfile
        with _guard:
            self._safe()
            self.path.parent.mkdir(parents=True, exist_ok=True)
            value = uuid4().hex
            fd, name = tempfile.mkstemp(prefix='.diagnostic-identity-', dir=self.path.parent)
            try:
                with os.fdopen(fd, 'w', encoding='utf-8') as handle:
                    json.dump({'installId': value}, handle)
                    handle.flush()
                    os.fsync(handle.fileno())
                os.replace(name, self.path)
            finally:
                Path(name).unlink(missing_ok=True)
            self.install_id, self.status = value, 'persisted'
            return value


def installation(settings_dir):
    with _guard:
        key = str(Path(settings_dir).absolute())
        if key not in _installs:
            _installs[key] = Installation(settings_dir)
        return _installs[key]


class DiagnosticStream:
    def __init__(self, name, settings_dir=None):
        self.name = name
        self.installation = installation(settings_dir) if settings_dir is not None else None
        self.lock = threading.RLock()
        self.sequence = 0
        self.dropped = 0
        self.suppressed = 0
        self.suppression_reasons = {}
        self.ranges = deque(maxlen=16)
        self.omitted_ranges = 0
        self._checkpoint_dropped = None

    def stamp(self):
        global _process
        with _guard:
            if _process[0] != os.getpid():
                _process = (os.getpid(), uuid4().hex)
            pid, session = _process
            key = (session, pid, self.name)
            sequence = _streams.get(key, 0) + 1
            _streams[key] = sequence
        return dict(installId=self.installation.install_id if self.installation else None,
                    installIdStatus=self.installation.status if self.installation else 'settings-directory-not-supplied',
                    sessionId=session, pid=pid, logStream=self.name, seq=sequence)

    def drop(self, row, reason):
        with self.lock:
            self.dropped += 1
            seq, stamp = row['seq'], row.get('timestampUTC', row.get('time')) or _moment()
            if self.ranges and self.ranges[-1]['lastSeq'] + 1 == seq and self.ranges[-1]['reason'] == reason:
                self.ranges[-1].update(lastSeq=seq, lastAt=stamp, count=self.ranges[-1]['count'] + 1)
            else:
                if len(self.ranges) == self.ranges.maxlen:
                    self.omitted_ranges += 1
                self.ranges.append(dict(firstSeq=seq, lastSeq=seq, firstAt=stamp,
                                        lastAt=stamp, count=1, reason=reason))

    def suppress(self, row, reason):
        """Intentional sampling/coalescing is not a failed accepted write."""
        with self.lock:
            self.suppressed += 1
            self.suppression_reasons[reason] = self.suppression_reasons.get(reason, 0) + 1

    def loss(self):
        with self.lock:
            return dict(droppedTotal=self.dropped, lostTotal=self.dropped,
                        suppressedTotal=self.suppressed, suppressionReasons=dict(self.suppression_reasons), ranges=[dict(row) for row in self.ranges],
                        omittedRangeCount=self.omitted_ranges)

    def checkpoint(self, *, force=False):
        """Emit bounded loss ranges only initially or when actual loss changes.

        Live export snapshots call loss() directly. No event repeats a stale
        16-range payload just because another diagnostic record was accepted.
        """
        with self.lock:
            if not force and self._checkpoint_dropped == self.dropped:
                return None
            self._checkpoint_dropped = self.dropped
            return self.loss()
