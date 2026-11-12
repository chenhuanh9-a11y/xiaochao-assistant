"""One installation resource lease for startup, helpers, and managed repairs.

The runtime owns the OS lease until every resource reader/worker has stopped.
Its own idle author imports may reuse that lease, with short operations serialized
inside the process. Other processes fail before reading resources or caches.
The lock filename is intentionally compatible with the existing import helper.
"""
from contextlib import contextmanager
import errno
import os
from pathlib import Path, PurePosixPath
import stat
import tempfile
import threading


class ResourceTransactionError(ValueError):
    def __init__(self, code, *, details=None):
        super().__init__(code)
        self.code = code
        self.details = details or {}


_registry_guard = threading.RLock()
_states = {}


def _safe(path):
    for item in (path, *path.parents):
        try:
            info = item.lstat()
        except FileNotFoundError:
            continue
        if stat.S_ISLNK(info.st_mode) or getattr(info, 'st_file_attributes', 0) & 0x400:
            raise ResourceTransactionError('resource-linked-target')
    return path


def _state(root):
    root = _safe(Path(os.path.abspath(root)))
    if not root.is_dir():
        raise ResourceTransactionError('installation-root-missing')
    key = os.path.normcase(str(root.resolve()))
    with _registry_guard:
        value = _states.setdefault(key, {'gate': threading.Lock(), 'runtime': None, 'owner': None})
    return root, value


def _open_lock(root):
    directory = _safe(root / 'work/map-packages')
    directory.mkdir(parents=True, exist_ok=True)
    lock = _safe(directory / 'import.lock')
    stream = None
    try:
        stream = lock.open('a+b')
        if os.name == 'nt':
            import msvcrt
            stream.seek(0)
            msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl
            fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
        return stream
    except OSError as error:
        if stream is not None:
            stream.close()
        raise ResourceTransactionError('resource-busy') from error


class _RuntimeLease:
    def __init__(self, root, state, stream):
        self.root, self._state, self._stream = root, state, stream

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()

    def close(self):
        # Caller shuts down workers before closing; this also waits for any
        # final resource transaction, so its OS lease cannot disappear early.
        with self._state['gate']:
            if self._stream is not None:
                self._stream.close()
                self._stream = None
                self._state['runtime'] = None


def resource_runtime_lease(root):
    """Acquire immediately. Call close() only after all runtime workers stop.

Acquire before base repair, then call assert_resources_ready before resource or
cache initialization. Keeping these separate permits explicit pending repair.
"""
    root, state = _state(root)
    if not state['gate'].acquire(blocking=False):
        raise ResourceTransactionError('resource-busy')
    try:
        if state['runtime'] is not None:
            raise ResourceTransactionError('resource-runtime-already-active')
        lease = _RuntimeLease(root, state, _open_lock(root))
        state['runtime'] = lease
        return lease
    finally:
        state['gate'].release()


def _acquire_gate(state, *, wait=False, cancelled=None):
    if state['owner'] == threading.get_ident():
        # Reads inside a writer use resource_read_lock's explicit reuse path;
        # a nested writer is never permitted to wait on itself.
        raise ResourceTransactionError('resource-busy')
    if not wait:
        if not state['gate'].acquire(blocking=False):
            raise ResourceTransactionError('resource-busy')
        return
    while True:
        if cancelled is not None and cancelled():
            raise ResourceTransactionError('resource-read-cancelled')
        if state['gate'].acquire(timeout=.05):
            return


@contextmanager
def resource_write_lock(root, *, wait=False, cancelled=None):
    """Serialize a short transaction, reusing only this process's runtime lease."""
    root, state = _state(root)
    _acquire_gate(state, wait=wait, cancelled=cancelled)
    stream = None
    try:
        if state['runtime'] is None:
            stream = _open_lock(root)
        state['owner'] = threading.get_ident()
        yield root
    finally:
        state['owner'] = None
        if stream is not None:
            stream.close()
        state['gate'].release()


@contextmanager
def resource_read_lock(root, *, wait=False, cancelled=None):
    # A writer may validate its own pending candidate, but a second writer is
    # never reentrant. ResourceService already owns a writer during inspection.
    root, state = _state(root)
    if state['owner'] == threading.get_ident():
        yield root
    else:
        with resource_write_lock(root, wait=wait, cancelled=cancelled):
            yield root


def assert_installer_upgrade_ready(root):
    """Only the verified Setup coordinator may work through its own marker."""
    root = _safe(Path(os.path.abspath(root)))
    marker = _safe(root / 'work/pending-installer-upgrade.json')
    if not marker.exists():
        return
    state = _state(root)[1]
    permit = getattr(state['runtime'], '_installer_forward_permit', None)
    from installer_resource_lease import _InstallerPendingPermit
    if not isinstance(permit, _InstallerPendingPermit) or not permit.allows(root, marker):
        raise ResourceTransactionError('installer-upgrade-recovery-required',
                                       details={'pendingFile': 'work/pending-installer-upgrade.json'})


def assert_installer_resource_action(root, action, transaction_id, plan_sha256):
    """A coordinator's durable phase also limits the resource finish direction."""
    root = _safe(Path(os.path.abspath(root)))
    if not _safe(root / 'work/pending-installer-upgrade.json').exists():
        return
    assert_installer_upgrade_ready(root)
    record = _state(root)[1]['runtime']._installer_forward_permit.record
    bound = record.get('resourceTransactionId')
    if (record.get('planSha256') != plan_sha256 or bound not in (None, transaction_id)
            or (action == 'commit' and (record.get('status') not in {'native-complete', 'completed'}
                                      or bound != transaction_id))
            or (action == 'rollback' and record.get('status') != 'prepared')
            or action not in {'commit', 'rollback'}):
        raise ResourceTransactionError('installer-upgrade-direction-not-authorized')


def assert_resources_ready(root):
    """Call under the runtime lease or a short lock, before reading any resource."""
    root = _safe(Path(os.path.abspath(root)))
    assert_installer_upgrade_ready(root)
    _assert_resource_transactions_ready(root)


def _assert_resource_transactions_ready(root):
    """Resource-only checks, also used by the owner of a native Setup marker."""
    root = _safe(Path(os.path.abspath(root)))
    for name, code in (
        ('pending-resource-reset.json', 'resource-reset-recovery-required'),
        ('pending-upgrade.json', 'upgrade-recovery-required'),
        ('pending-base-repair.json', 'base-recovery-required'),
        ('pending-additions.json', 'additions-recovery-required'),
        ('pending-removal.json', 'removal-recovery-required'),
        ('pending-author-snapshot.json', 'author-snapshot-recovery-required'),
    ):
        if _safe(root / 'work/map-packages' / name).exists():
            raise ResourceTransactionError(code, details={'pendingFile': 'work/map-packages/' + name})
    # A fully written additive journal precedes marker publication. Discover
    # only bounded small control records here; payload verification belongs to
    # explicit recovery, not every runtime resource read.
    from map_package_upgrade import prepared_additions
    try:
        unfinished = prepared_additions(root)
    except (OSError, ValueError, KeyError, TypeError) as error:
        raise ResourceTransactionError('additions-recovery-required') from error
    if unfinished:
        raise ResourceTransactionError('additions-recovery-required', details={
            'pendingFile': unfinished[0][1].relative_to(root).as_posix()})


def _publication_targets(root, relatives):
    """Validate the staging plane and every destination before capability probes."""
    root = _safe(Path(os.path.abspath(root)))
    state = _safe(root / 'work/map-packages')
    state.mkdir(parents=True, exist_ok=True)
    device = state.stat().st_dev
    for relative in relatives:
        if (not isinstance(relative, str) or not relative or len(relative) > 240
                or '\\' in relative or ':' in relative or relative.startswith('/')
                or any(part in {'', '.', '..'} for part in relative.split('/'))):
            raise ResourceTransactionError('resource-unsafe-target')
        target = _safe(root.joinpath(*PurePosixPath(relative).parts))
        existing = target
        while not existing.exists():
            existing = existing.parent
        if existing.stat().st_dev != device:
            raise ResourceTransactionError('resource-cross-volume-target', details={'path': relative})
    return state


def _capability_details(stage, *, hardlinks='unknown', atomic_move='unknown'):
    # Fixed source-owned names only. Never serialize an installation path,
    # exception text or a filesystem volume/username into the startup report.
    return dict(resourceLocation='work/map-packages', resourceSubstage=stage,
                filesystemCapabilities=dict(hardlinks=hardlinks, atomicNoReplace=atomic_move))


def preflight_hardlinks(root, relatives):
    """Strict legacy protocol. Its retained same-object pins require links."""
    state = _publication_targets(root, relatives)
    try:
        with tempfile.TemporaryDirectory(prefix='link-probe-', dir=state) as directory:
            source, target = Path(directory) / 'source', Path(directory) / 'linked'
            source.write_bytes(b'resource-link-capability-v1')
            os.link(source, target)
            if not os.path.samefile(source, target) or target.read_bytes() != source.read_bytes():
                raise OSError('link identity mismatch')
    except OSError as error:
        raise ResourceTransactionError('resource-hardlinks-unavailable', details=
            _capability_details('hardlink-probe', hardlinks='unavailable')) from error


def rename_no_replace(source, target):
    """Atomically move an object without replacing any existing destination.

    Windows rename is no-replace. Linux requires renameat2(RENAME_NOREPLACE),
    never an exists check followed by the replacing POSIX rename operation.
    Callers must bind source bytes/identity and validate lexical ancestors.
    """
    source, target = _safe(Path(source)), _safe(Path(target))
    if os.name == 'nt':
        os.rename(source, target)
        return
    import ctypes
    api = ctypes.CDLL(None, use_errno=True)
    rename = getattr(api, 'renameat2', None)
    if rename is None:
        raise OSError(errno.ENOSYS, 'atomic-no-replace-unavailable')
    rename.argtypes = [ctypes.c_int, ctypes.c_char_p, ctypes.c_int, ctypes.c_char_p, ctypes.c_uint]
    rename.restype = ctypes.c_int
    if rename(-100, os.fsencode(source), -100, os.fsencode(target), 1):
        code = ctypes.get_errno()
        raise OSError(code, 'atomic-no-replace-failed')


def hardlinks_unsupported(error):
    """Only explicit missing-link capability permits an owned move fallback."""
    return (isinstance(error, OSError) and
            (getattr(error, 'winerror', None) in {1, 50} or
             (getattr(error, 'winerror', None) is None and error.errno in
              {errno.EINVAL, errno.ENOSYS, errno.ENOTSUP, errno.EOPNOTSUPP})))


def preflight_resource_publication(root, relatives):
    """Select only an actually tested protocol, before creating a transaction.

    Permission, quota, disk and unknown failures are not a signal to downgrade.
    Move mode needs independent durable identities in its caller's journal;
    legacy same-object witness users must keep calling preflight_hardlinks.
    """
    relatives = tuple(relatives)
    try:
        preflight_hardlinks(root, relatives)
        return 'hardlink-v1'
    except ResourceTransactionError as error:
        if error.code != 'resource-hardlinks-unavailable' or not hardlinks_unsupported(error.__cause__):
            raise
    state = _publication_targets(root, relatives)
    from map_package_upgrade import _write_new, _capture, _move_owned, _matches
    import hashlib
    stage = 'atomic-move-probe'
    try:
        with tempfile.TemporaryDirectory(prefix='move-probe-', dir=state) as directory:
            source, target = Path(directory) / 'source', Path(directory) / 'target'
            data, other = b'resource-atomic-move-v1', b'do-not-replace'
            row = dict(path='source', bytes=len(data), sha256=hashlib.sha256(data).hexdigest())
            _write_new(source, data)
            _write_new(target, other)
            identity = _capture(source, row)
            if not identity[1] or identity == [target.stat().st_dev, target.stat().st_ino]:
                raise OSError(errno.ENOTSUP, 'distinct-file-identity-unavailable')
            stage = 'atomic-no-replace-probe'
            try:
                _move_owned(source, target, row, identity, linkless=True)
            except OSError:
                if not _matches(source, row, identity) or target.read_bytes() != other:
                    raise OSError(errno.EIO, 'atomic-no-replace-probe-changed-objects')
            else:
                raise OSError(errno.EIO, 'atomic-no-replace-overwrote-target')
            target.unlink()  # Private capability probe only; not a resource.
            stage = 'atomic-move-probe'
            _move_owned(source, target, row, identity, linkless=True)
            if source.exists() or not _matches(target, row, identity):
                raise OSError(errno.EIO, 'atomic-move-identity-mismatch')
    except (OSError, ValueError) as error:
        raise ResourceTransactionError('resource-atomic-move-unavailable', details=
            _capability_details(stage, hardlinks='unsupported', atomic_move='unavailable')) from error
    return 'move-v1'
