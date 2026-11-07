"""Borrow Setup's live exclusive file object, never bypass the resource lease.

Windows only. Setup keeps its share=0 handle open through the complete native
transaction. Each one-folder helper duplicates that SAME object, checks the
live direct parent, creation time, executable, physical path and exclusive
sharing, then registers a process-local lease for the existing transaction API.
No source handle is closed and no PID-only or unlocked fallback is available.
"""
from contextlib import contextmanager
import ctypes
from ctypes import wintypes as wt
import hashlib
import json
import os
from pathlib import Path
import re

from resource_transaction import ResourceTransactionError, _RuntimeLease, _state, _safe


class _HandleOwner:
    def __init__(self, api, handle):
        self.api, self.handle = api, handle

    def close(self):
        if self.handle:
            self.api.CloseHandle(self.handle)
            self.handle = None


def _coordinator_marker_stamp(marker):
    # Reuse the bounded, nonblocking, handle-based metadata reader. In
    # particular do not compare Windows pathname ctime with handle ctime.
    from author_resource_snapshots import _metadata
    _raw, row = _metadata(_safe(marker))
    return tuple(row['identity']), row['bytes'], row['sha256']


class _InstallerPendingPermit:
    """Process-local permission for one already-validated coordinator marker.

    This is never serialized or accepted through the CLI. The native borrowed
    handle remains live, and every use rechecks the exact marker object/bytes.
    """
    def __init__(self, root, lease, marker, record, validate):
        self.validate = validate
        self.root, self.lease, self.marker = root, lease, marker
        self.record = json.loads(json.dumps(record))
        self.observation = self._observe()

    def _observe(self):
        return _coordinator_marker_stamp(self.marker)

    def allows(self, root, marker):
        try:
            actual_root, state = _state(root)
            return (actual_root == self.root and marker == self.marker
                    and state['runtime'] is self.lease
                    and isinstance(self.lease._stream, _HandleOwner)
                    and bool(self.lease._stream.handle)
                    and self._observe() == self.observation
                    and self.validate() == self.record
                    and self._observe() == self.observation)
        except (OSError, ValueError):
            return False


@contextmanager
def installer_coordinator_scope(root, lease, session_id, *, validate):
    """Permit own coordinator only after a strict journal read under the lease."""
    root, state = _state(root)
    if (state['runtime'] is not lease or not isinstance(lease._stream, _HandleOwner)
            or not lease._stream.handle or getattr(lease, '_installer_forward_permit', None) is not None):
        raise _error('coordinator-requires-verified-borrowed-lease')
    marker = _safe(root / 'work/pending-installer-upgrade.json')
    before = _coordinator_marker_stamp(marker)
    record = validate()
    info = root.stat()
    if (not isinstance(record, dict) or record.get('sessionId') != session_id
            or record.get('rootIdentity') != [info.st_dev, info.st_ino]):
        raise _error('coordinator-identity-mismatch')
    permit = _InstallerPendingPermit(root, lease, marker, record, validate)
    if permit.observation != before:
        raise _error('coordinator-marker-changed')
    lease._installer_forward_permit = permit
    try:
        yield record
    finally:
        lease._installer_forward_permit = None


class _FileInfo(ctypes.Structure):
    _fields_ = [('attributes', wt.DWORD), ('created', wt.FILETIME),
                ('accessed', wt.FILETIME), ('written', wt.FILETIME),
                ('volume', wt.DWORD), ('sizeHigh', wt.DWORD), ('sizeLow', wt.DWORD),
                ('links', wt.DWORD), ('indexHigh', wt.DWORD), ('indexLow', wt.DWORD)]


def _error(code):
    return ResourceTransactionError('installer-lease-' + code)


def _kernel():
    if os.name != 'nt':
        raise _error('windows-required')
    api = ctypes.WinDLL('kernel32', use_last_error=True)
    declarations = {
        'OpenProcess': ([wt.DWORD, wt.BOOL, wt.DWORD], wt.HANDLE),
        'GetCurrentProcess': ([], wt.HANDLE),
        'GetProcessId': ([wt.HANDLE], wt.DWORD),
        'CloseHandle': ([wt.HANDLE], wt.BOOL),
        'WaitForSingleObject': ([wt.HANDLE, wt.DWORD], wt.DWORD),
        'GetProcessTimes': ([wt.HANDLE] + [ctypes.POINTER(wt.FILETIME)] * 4, wt.BOOL),
        'QueryFullProcessImageNameW': ([wt.HANDLE, wt.DWORD, wt.LPWSTR, ctypes.POINTER(wt.DWORD)], wt.BOOL),
        'DuplicateHandle': ([wt.HANDLE, wt.HANDLE, wt.HANDLE, ctypes.POINTER(wt.HANDLE),
                             wt.DWORD, wt.BOOL, wt.DWORD], wt.BOOL),
        'GetFileType': ([wt.HANDLE], wt.DWORD),
        'GetFileInformationByHandle': ([wt.HANDLE, ctypes.POINTER(_FileInfo)], wt.BOOL),
        'GetFinalPathNameByHandleW': ([wt.HANDLE, wt.LPWSTR, wt.DWORD, wt.DWORD], wt.DWORD),
        'CreateFileW': ([wt.LPCWSTR, wt.DWORD, wt.DWORD, wt.LPVOID, wt.DWORD, wt.DWORD, wt.HANDLE], wt.HANDLE),
    }
    for name, (arguments, result) in declarations.items():
        getattr(api, name).argtypes, getattr(api, name).restype = arguments, result
    return api


def _final(api, handle):
    buffer = ctypes.create_unicode_buffer(32768)
    count = api.GetFinalPathNameByHandleW(handle, buffer, len(buffer), 1)  # normalized volume GUID
    if not 0 < count < len(buffer):
        raise _error('physical-path-unavailable')
    return buffer.value.rstrip('\\').casefold()


def _parent(api, handle, pid, created, executable, digest):
    if api.GetProcessId(handle) != pid or api.WaitForSingleObject(handle, 0) != 258:
        raise _error('parent-not-live')
    times = [wt.FILETIME() for _ in range(4)]
    if not api.GetProcessTimes(handle, *(ctypes.byref(value) for value in times)):
        raise _error('parent-time-unavailable')
    actual_created = str((times[0].dwHighDateTime << 32) | times[0].dwLowDateTime)
    if actual_created != str(created):
        raise _error('parent-reused')
    buffer, length = ctypes.create_unicode_buffer(32768), wt.DWORD(32768)
    if not api.QueryFullProcessImageNameW(handle, 0, buffer, ctypes.byref(length)):
        raise _error('parent-image-unavailable')
    actual = _safe(Path(buffer.value))
    expected = _safe(Path(executable))
    if os.path.normcase(str(actual.resolve())) != os.path.normcase(str(expected.resolve())):
        raise _error('parent-image-mismatch')
    if hashlib.sha256(actual.read_bytes()).hexdigest() != digest:
        raise _error('parent-image-changed')


def _duplicate_verified(root, *, parent_pid, parent_created, parent_executable,
                        parent_sha256, lock_handle):
    if (type(parent_pid) is not int or parent_pid <= 0 or parent_pid != os.getppid()
            or type(lock_handle) is not int or lock_handle <= 0
            or not re.fullmatch(r'[0-9]{1,20}', str(parent_created))
            or not re.fullmatch(r'[0-9a-f]{64}', str(parent_sha256))):
        raise _error('invalid-parent-contract')
    api = _kernel()
    parent = api.OpenProcess(0x100000 | 0x1000 | 0x40, False, parent_pid)
    if not parent:
        raise _error('parent-open-denied')
    duplicate, root_handle = wt.HANDLE(), None
    invalid = ctypes.c_void_p(-1).value
    try:
        _parent(api, parent, parent_pid, parent_created, parent_executable, parent_sha256)
        if not api.DuplicateHandle(parent, lock_handle, api.GetCurrentProcess(),
                                   ctypes.byref(duplicate), 0, False, 2):
            raise _error('duplicate-denied')
        # SAME_ACCESS without CLOSE_SOURCE. Directories/pipes/reparse/hardlinked
        # lock aliases are not a lease for this installation.
        info = _FileInfo()
        if (api.GetFileType(duplicate) != 1
                or not api.GetFileInformationByHandle(duplicate, ctypes.byref(info))
                or info.attributes & (0x10 | 0x400) or info.links != 1):
            raise _error('invalid-file-object')
        root = _safe(Path(os.path.abspath(root)))
        lock = _safe(root / 'work/map-packages/import.lock')
        root_handle = api.CreateFileW(str(root), 0, 7, None, 3, 0x02000000, None)
        if root_handle in (None, invalid):
            raise _error('root-open-denied')
        if _final(api, duplicate) != _final(api, root_handle) + '\\work\\map-packages\\import.lock':
            raise _error('wrong-lock-object')
        # A real exclusive file object denies both read and write opens even
        # when the probe itself shares everything. Access-denied is NOT proof.
        for access in (0x80000000, 0x40000000):
            ctypes.set_last_error(0)
            probe = api.CreateFileW(str(lock), access, 7, None, 3, 0x80, None)
            if probe not in (None, invalid):
                api.CloseHandle(probe)
                raise _error('not-exclusive')
            if ctypes.get_last_error() != 32:
                raise _error('exclusive-state-unverifiable')
        _parent(api, parent, parent_pid, parent_created, parent_executable, parent_sha256)
        owner = _HandleOwner(api, duplicate.value)
        duplicate = wt.HANDLE()
        return owner
    finally:
        if duplicate.value:
            api.CloseHandle(duplicate)
        if root_handle not in (None, invalid):
            api.CloseHandle(root_handle)
        api.CloseHandle(parent)


@contextmanager
def borrowed_installer_lease(root, **identity):
    """Public entry validates the OS object before any process-local reuse."""
    owner = _duplicate_verified(root, **identity)
    lease = None
    try:
        root, state = _state(root)
        if not state['gate'].acquire(blocking=False):
            raise _error('process-busy')
        try:
            if state['runtime'] is not None or state['owner'] is not None:
                raise _error('process-already-leased')
            lease = _RuntimeLease(root, state, owner)
            state['runtime'] = lease
        finally:
            state['gate'].release()
        yield lease
    finally:
        if lease is not None:
            lease.close()
        else:
            owner.close()
