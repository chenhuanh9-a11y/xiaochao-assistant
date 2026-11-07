"""Read-only, fail-closed native-install completion evidence.

The current Setup must supply its digest-pinned ownership bytes and full target
program inventory, never the previous installation's editable ownership list.
This module grants no deletion, rollback, registry-write or shortcut-write right.
Evidence is meaningful only while the caller holds the installer resource lease.
Two complete reads must agree; replay reads the exact same objects again. This is
not a guarantee against a concurrent actor changing things after verification.

Only the default Windows backend can produce production evidence. Injectable
backends produce explicitly labelled fixture evidence, rejected by native replay.
No GUI, image, game or resource-decoding imports are used here.
"""
from __future__ import annotations

import base64
from contextlib import contextmanager
import ctypes
from ctypes import wintypes as W
import hashlib
import json
import ntpath
import os
import re
import struct
import uuid

APP_ID = '{D04394A0-DACE-4FF9-874D-3DCB43EE53D1}'
UNINSTALL_KEY = 'Software\\Microsoft\\Windows\\CurrentVersion\\Uninstall\\' + APP_ID + '_is1'
OWNERSHIP_NAME = '_installer-owned-files.txt'
SCHEMA = 'xingtu-native-install-observation-v1'
MAX_METADATA = 16 * 1024 * 1024
MAX_FILES = 20000
MAX_FILE = 16 * 1024 ** 3
MAX_LINK = 1024 * 1024
MAX_REG_KEYS = 256
MAX_REG_VALUES = 4096
_PROGRAM_EXCEPTIONS = frozenset(('_internal/references/build-fingerprint.txt',))
_EXCLUDED = ('work', 'assets', 'references', 'recognition', 'seasons',
             '_internal/assets', '_internal/references', '_internal/recognition',
             '_internal/seasons', '_internal/qt-author-base',
             '_internal/difficult-native-manifest.json')
_HASH = re.compile(r'[0-9a-f]{64}\Z')
_SID = re.compile(r'S-1-\d+(?:-\d+){1,15}\Z')


class NativeObservationError(ValueError):
    def __init__(self, code):
        self.code = 'native-observation-' + code
        super().__init__(self.code)


def _fail(code):
    raise NativeObservationError(code)


def _encoded(value):
    try:
        data = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'),
                          allow_nan=False).encode('utf-8')
    except (TypeError, ValueError, UnicodeError):
        _fail('invalid-data')
    if len(data) > MAX_METADATA:
        _fail('metadata-limit')
    return data


def _hash(data):
    return hashlib.sha256(data).hexdigest()


def _relative(value):
    if not isinstance(value, str) or len(value) > 32700 or '\x00' in value:
        _fail('invalid-program-path')
    value = value.replace('\\', '/')
    parts = value.split('/')
    if any(not part or part in ('.', '..') or part[-1:] in (' ', '.')
           or any(ord(c) < 32 or c in ':*?"<>|' for c in part)
           or re.fullmatch(r'(?:con|prn|aux|nul|com[1-9]|lpt[1-9])(?:\..*)?', part, re.I)
           for part in parts):
        _fail('invalid-program-path')
    return value


def _path(value):
    if not isinstance(value, str) or len(value) > 32700 or '/' in value or '\x00' in value:
        _fail('invalid-absolute-path')
    # No UNC, device namespaces, relative drive paths, ADS, DOS aliases or dots.
    if not re.match(r'^[a-zA-Z]:\\', value):
        _fail('local-drive-path-required')
    value = value.rstrip('\\')
    if len(value) == 2:
        return value + '\\'
    _relative(value[3:])
    if ntpath.normpath(value) != value:
        _fail('noncanonical-path')
    return value


def _same_path(a, b):
    return _path(a).casefold() == _path(b).casefold()


def program_paths_from_ownership(ownership_bytes):
    """Select program-only paths from CURRENT SETUP bytes, without any IO.

    Resource/editor exclusions are deliberate. Their separate transaction is the
    only authority for active resource state. Candidate payloads are program data.
    """
    if not isinstance(ownership_bytes, bytes) or not 0 < len(ownership_bytes) <= MAX_METADATA:
        _fail('invalid-ownership')
    try:
        names = ownership_bytes.decode('utf-8-sig').splitlines()
    except UnicodeError:
        _fail('invalid-ownership-encoding')
    if not 0 < len(names) <= MAX_FILES:
        _fail('ownership-count')
    selected, seen = [], set()
    for name in names:
        name = _relative(name)
        folded = name.casefold()
        if folded in seen:
            _fail('duplicate-ownership-path')
        seen.add(folded)
        if folded in _PROGRAM_EXCEPTIONS or not any(
                folded == p or folded.startswith(p + '/') for p in _EXCLUDED):
            # Native uninstall outputs are observed separately and never claimed
            # as a target payload by the Setup's program inventory.
            if re.match(r'unins\d{3}\.', folded):
                _fail('native-file-in-program-inventory')
            selected.append(name)
    required = {'星图.exe', 'release-report.json', OWNERSHIP_NAME}
    if not required.issubset({p.casefold() for p in selected}):
        _fail('missing-program-identity')
    return tuple(sorted(selected, key=str.casefold))


def _contract(root, target_identity, target_report_sha256, ownership_bytes,
              ownership_sha256, program_inventory, uninstall_exe, shortcut_targets):
    root, uninstall_exe = _path(os.fspath(root)), _path(os.fspath(uninstall_exe))
    if len(root) <= 3:
        _fail('install-root-is-drive-root')
    if not _same_path(ntpath.dirname(uninstall_exe), root) or not re.fullmatch(
            r'unins[0-9]{3}\.exe', ntpath.basename(uninstall_exe), re.I):
        _fail('uninstall-path-not-owned')
    fields = {'sourceCommit', 'displayVersion', 'numericVersion', 'executableSha256',
              'resourceInventorySha256'}
    if (not isinstance(target_identity, dict) or set(target_identity) != fields
            or not re.fullmatch('[0-9a-f]{40}', str(target_identity['sourceCommit']))
            or not re.fullmatch(r'[0-9A-Za-z.+_-]{1,100}', str(target_identity['displayVersion']))
            or not re.fullmatch(r'\d+\.\d+\.\d+\.\d+', str(target_identity['numericVersion']))
            or any(not isinstance(target_identity[k], str) or not _HASH.fullmatch(target_identity[k])
                   for k in ('executableSha256', 'resourceInventorySha256'))):
        _fail('invalid-target-identity')
    if any(not isinstance(v, str) or not _HASH.fullmatch(v)
           for v in (target_report_sha256, ownership_sha256)):
        _fail('invalid-target-hash')
    if not isinstance(ownership_bytes, bytes):
        _fail('invalid-ownership')
    if _hash(ownership_bytes) != ownership_sha256:
        _fail('ownership-hash-mismatch')
    names = program_paths_from_ownership(ownership_bytes)
    if not isinstance(program_inventory, dict) or set(program_inventory) != set(names):
        _fail('incomplete-program-inventory')
    inventory = {}
    for name in names:
        row = program_inventory[name]
        if (not isinstance(row, dict) or set(row) != {'bytes', 'sha256'}
                or type(row['bytes']) is not int or not 0 <= row['bytes'] <= MAX_FILE
                or not isinstance(row['sha256'], str) or not _HASH.fullmatch(row['sha256'])):
            _fail('invalid-program-inventory')
        inventory[name] = dict(row)
    for name, expected in (('星图.exe', target_identity['executableSha256']),
                           ('release-report.json', target_report_sha256),
                           (OWNERSHIP_NAME, ownership_sha256)):
        if inventory[name]['sha256'] != expected:
            _fail('target-inventory-identity-mismatch')
    if inventory[OWNERSHIP_NAME]['bytes'] != len(ownership_bytes):
        _fail('ownership-size-mismatch')
    if not isinstance(shortcut_targets, (list, tuple)) or len(shortcut_targets) != 3:
        _fail('explicit-shortcut-selection-required')
    shortcuts = []
    for row in shortcut_targets:
        if (not isinstance(row, dict) or set(row) != {'path', 'target', 'enabled'}
                or type(row['enabled']) is not bool):
            _fail('invalid-shortcut-selection')
        shortcuts.append(dict(path=_path(row['path']), target=_path(row['target']), enabled=row['enabled']))
    shortcuts.sort(key=lambda row: row['path'].casefold())
    if len({row['path'].casefold() for row in shortcuts}) != 3:
        _fail('duplicate-shortcut')
    result = dict(installRoot=root, appId=APP_ID, targetIdentity=dict(target_identity),
                  targetReportSha256=target_report_sha256, ownershipSha256=ownership_sha256,
                  programInventory=inventory, uninstallExe=uninstall_exe, shortcutTargets=shortcuts)
    _encoded(result)
    return result


def _raw(record, limit):
    if not isinstance(record, dict) or record.get('state') != 'file':
        _fail('required-file-missing')
    try:
        data = base64.b64decode(record['rawBase64'], validate=True)
    except (KeyError, TypeError, ValueError):
        _fail('raw-evidence-invalid')
    if len(data) > limit or len(data) != record.get('bytes') or _hash(data) != record.get('sha256'):
        _fail('raw-evidence-mismatch')
    return data


def _unicode_z(data, offset, end):
    if not 0 <= offset < end <= len(data):
        _fail('shortcut-string-bounds')
    for stop in range(offset, end - 1, 2):
        if data[stop:stop + 2] == b'\0\0':
            try:
                return data[offset:stop].decode('utf-16-le')
            except UnicodeError:
                _fail('shortcut-string-invalid')
    _fail('shortcut-string-unterminated')


def _shortcut_target(data):
    """Conservative MS-SHLLINK reader; never execute/resolve a link or invoke COM.

    Inno links containing a local LinkInfo path and no arguments are supported.
    ID-list-only, remote, environment-based and unknown links fail closed.
    """
    if (len(data) < 80 or data[:4] != struct.pack('<I', 0x4c)
            or data[4:20] != uuid.UUID('00021401-0000-0000-c000-000000000046').bytes_le):
        _fail('shortcut-invalid-header')
    flags = struct.unpack_from('<I', data, 20)[0]
    # Standard local link fields, Unicode, ForceNoLinkTrack/DisableLinkPathTracking.
    allowed = 0x1ff | 0x40000 | 0x80000 | 0x100000
    if flags & ~allowed or not flags & 2 or flags & 0x100:
        _fail('shortcut-unsupported-flags')
    offset = 76
    if flags & 1:
        if offset + 2 > len(data):
            _fail('shortcut-idlist-bounds')
        offset += 2 + struct.unpack_from('<H', data, offset)[0]
    if offset + 28 > len(data):
        _fail('shortcut-linkinfo-missing')
    size, header, info_flags, volume, local, network, suffix = struct.unpack_from('<7I', data, offset)
    end = offset + size
    if not 28 <= header <= size or end > len(data) or info_flags != 1 or network != 0:
        _fail('shortcut-unsupported-linkinfo')
    if not header <= volume < size or not header <= local < size or not header <= suffix < size:
        _fail('shortcut-linkinfo-bounds')
    if header >= 36:
        local_u, suffix_u = struct.unpack_from('<2I', data, offset + 28)
        if local_u < header or suffix_u < header:
            _fail('shortcut-unicode-bounds')
        base = _unicode_z(data, offset + local_u, end)
        tail = _unicode_z(data, offset + suffix_u, end)
    else:
        def ansi(position):
            stop = data.find(b'\0', offset + position, end)
            if stop < 0:
                _fail('shortcut-ansi-unterminated')
            try:
                # Non-ASCII ANSI paths depend on the producer's codepage. Unknown
                # codepage must not be guessed on a different machine.
                return data[offset + position:stop].decode('ascii')
            except UnicodeError:
                _fail('shortcut-ansi-codepage-unknown')
        base, tail = ansi(local), ansi(suffix)
    # CommonPathSuffix can be a filename already present at the end of BasePath.
    if tail and not base.casefold().endswith(tail.casefold()):
        base = ntpath.join(base, tail)
    target = _path(base)
    offset = end
    for bit in (4, 8, 16, 32, 64):
        if flags & bit:
            if offset + 2 > len(data):
                _fail('shortcut-stringdata-bounds')
            count = struct.unpack_from('<H', data, offset)[0]
            offset += 2
            length = count * (2 if flags & 128 else 1)
            if offset + length > len(data):
                _fail('shortcut-stringdata-bounds')
            try:
                value = data[offset:offset + length].decode('utf-16-le' if flags & 128 else 'ascii')
            except UnicodeError:
                _fail('shortcut-stringdata-invalid')
            if '\0' in value or (bit == 32 and value):
                _fail('shortcut-arguments-or-nul')
            offset += length
    # Extra data may contain tracking/property blocks, not alternative executable
    # paths, environment expansion or installer/Darwin indirection.
    while offset + 4 <= len(data):
        length = struct.unpack_from('<I', data, offset)[0]
        if length == 0:
            if offset + 4 != len(data):
                _fail('shortcut-trailing-data')
            return target
        if length < 8 or offset + length > len(data):
            _fail('shortcut-extra-bounds')
        signature = struct.unpack_from('<I', data, offset + 4)[0]
        if signature not in (0xa0000003, 0xa0000009):  # Tracker / PropertyStore
            _fail('shortcut-extra-unsupported')
        offset += length
    _fail('shortcut-terminal-missing')


def _registry_text(values, name):
    rows = [row for row in values if row.get('name', '').casefold() == name.casefold()]
    if len(rows) != 1 or rows[0].get('type') != 1:
        _fail('registry-required-value-invalid')
    try:
        data = base64.b64decode(rows[0]['rawBase64'], validate=True)
        value = data.decode('utf-16-le')
    except (KeyError, TypeError, ValueError, UnicodeError):
        _fail('registry-required-value-invalid')
    if not value.endswith('\0') or '\0' in value[:-1]:
        _fail('registry-required-string-invalid')
    return value[:-1]


def _capture_once(backend, contract):
    with backend.read_session():
        users = backend.users()
        if (not isinstance(users, dict) or set(users) != {'tokenSid', 'shellSid'}
                or not all(isinstance(v, str) and _SID.fullmatch(v) for v in users.values())
                or users['tokenSid'] != users['shellSid']):
            _fail('different-or-unknown-interactive-user')
        root = contract['installRoot']
        root_record = backend.directory(root)
        program = {}
        for name, expected in contract['programInventory'].items():
            row = backend.file(ntpath.join(root, name.replace('/', '\\')), required=True,
                               raw=name == 'release-report.json')
            if row.get('state') != 'file' or any(row.get(k) != expected[k] for k in ('bytes', 'sha256')):
                _fail('program-target-mismatch')
            program[name] = row
        try:
            report = json.loads(_raw(program['release-report.json'], MAX_METADATA).decode('utf-8-sig'))
        except (ValueError, UnicodeError):
            _fail('target-report-invalid')
        target = contract['targetIdentity']
        if (not isinstance(report, dict) or report.get('sourceDirty') is not False
                or report.get('installerSourceCommit', report.get('sourceCommit')) != target['sourceCommit']
                or any(report.get(k) != target[k] for k in target if k != 'sourceCommit')):
            _fail('target-report-identity-mismatch')
        uninstall = {}
        stem = contract['uninstallExe'][:-4]
        for extension in ('.exe', '.dat', '.msg'):
            uninstall[extension] = backend.file(stem + extension, required=extension != '.msg', raw=False)
        registry = backend.registry(users['tokenSid'])
        values = registry['values']
        if (not _same_path(_registry_text(values, 'InstallLocation'), root)
                or _registry_text(values, 'DisplayVersion') != target['displayVersion']
                or _registry_text(values, 'UninstallString').casefold() != (
                    '"' + contract['uninstallExe'] + '"').casefold()):
            _fail('uninstall-registration-target-mismatch')
        locations = backend.shortcut_locations()
        programs, desktop = _path(locations['programs']), _path(locations['desktop'])
        group_rows = [r for r in contract['shortcutTargets']
                      if not _same_path(r['path'], ntpath.join(desktop, '星图.lnk'))]
        if len(group_rows) != 2:
            _fail('invalid-shortcut-group')
        group = ntpath.dirname(group_rows[0]['path'])
        if (not group.casefold().startswith(programs.casefold() + '\\')
                or not _same_path(group, ntpath.dirname(group_rows[1]['path']))):
            _fail('shortcut-group-outside-current-user-programs')
        expected_shortcuts = {
            ntpath.join(group, '星图.lnk').casefold(): ntpath.join(root, '星图.exe'),
            ntpath.join(group, '卸载星图.lnk').casefold(): contract['uninstallExe'],
            ntpath.join(desktop, '星图.lnk').casefold(): ntpath.join(root, '星图.exe'),
        }
        if {r['path'].casefold() for r in contract['shortcutTargets']} != set(expected_shortcuts):
            _fail('shortcut-outside-current-user-fixed-locations')
        shortcuts = []
        for selected in contract['shortcutTargets']:
            path = selected['path']
            if not _same_path(selected['target'], expected_shortcuts[path.casefold()]):
                _fail('shortcut-target-not-owned')
            collisions = {}
            for suffix in ('', '.pif', '.url'):
                candidate = path[:-4] + suffix
                collisions[suffix] = backend.absent(candidate)
                if collisions[suffix]['state'] != 'absent':
                    _fail('shortcut-collision')
            row = backend.file(path, required=selected['enabled'], raw=True, limit=MAX_LINK)
            if (selected['enabled'] and row['state'] == 'file'
                    and not _same_path(backend.shortcut_target(_raw(row, MAX_LINK)), selected['target'])):
                _fail('shortcut-target-mismatch')
            shortcuts.append(dict(selection=selected, file=row, collisions=collisions))
        if users != backend.users() or root_record != backend.directory(root):
            _fail('identity-changed-during-read')
        return dict(users=users, root=root_record, program=program, uninstall=uninstall,
                    registry=registry, shortcuts=shortcuts)


def capture_native_completion(install_root, *, target_identity, target_report_sha256,
                              ownership_bytes, ownership_sha256, program_inventory,
                              uninstall_exe, shortcut_targets, _backend=None):
    """Return evidence only after two complete matching reads; never writes.

    ``_backend`` is an offline-test seam, not a platform override. Its evidence is
    marked fixture-only and cannot be replayed as native Windows evidence.
    """
    contract = _contract(install_root, target_identity, target_report_sha256, ownership_bytes,
                         ownership_sha256, program_inventory, uninstall_exe, shortcut_targets)
    backend = _backend if _backend is not None else WindowsReadOnlyBackend()
    first = _capture_once(backend, contract)
    second = _capture_once(backend, contract)
    if _encoded(first) != _encoded(second):
        _fail('changed-during-capture')
    result = dict(schema=SCHEMA, source='fixture-only' if _backend is not None else 'win32-read-only',
                  contract=contract, observation=second)
    result['sha256'] = _hash(_encoded(result))
    return result


def verify_native_completion(evidence, install_root, *, target_identity, target_report_sha256,
                             ownership_bytes, ownership_sha256, program_inventory,
                             uninstall_exe, shortcut_targets, _backend=None):
    """Re-read original completion evidence. Changed, missing or unknown blocks.

    Returns the matching evidence, never a weak Boolean that could swallow an
    unavailable observation. Caller must not infer success from an exception.
    """
    _validate_evidence(evidence, fixture=_backend is not None)
    contract = _contract(install_root, target_identity, target_report_sha256, ownership_bytes,
                         ownership_sha256, program_inventory, uninstall_exe, shortcut_targets)
    if _encoded(contract) != _encoded(evidence['contract']):
        _fail('evidence-contract-mismatch')
    backend = _backend if _backend is not None else WindowsReadOnlyBackend()
    _check_recorded_owner_root(backend, evidence, contract['installRoot'])
    current = capture_native_completion(install_root, target_identity=target_identity,
        target_report_sha256=target_report_sha256, ownership_bytes=ownership_bytes,
        ownership_sha256=ownership_sha256, program_inventory=program_inventory,
        uninstall_exe=uninstall_exe, shortcut_targets=shortcut_targets, _backend=_backend)
    if _encoded(current) != _encoded(evidence):
        _fail('evidence-no-longer-matches')
    return current


def _validate_evidence(evidence, *, fixture):
    if not isinstance(evidence, dict) or set(evidence) != {'schema', 'source', 'contract', 'observation', 'sha256'}:
        _fail('invalid-evidence')
    digest = evidence.get('sha256')
    unsigned = {k: v for k, v in evidence.items() if k != 'sha256'}
    if (evidence.get('schema') != SCHEMA or not isinstance(digest, str) or not _HASH.fullmatch(digest)
            or _hash(_encoded(unsigned)) != digest):
        _fail('evidence-integrity-mismatch')
    if evidence.get('source') != ('fixture-only' if fixture else 'win32-read-only'):
        _fail('evidence-backend-mismatch')
    if not isinstance(evidence['contract'], dict) or not isinstance(evidence['observation'], dict):
        _fail('invalid-evidence')


def _check_recorded_owner_root(backend, evidence, root):
    """Reject another owner/root before reading its files or user shell paths."""
    observed = evidence['observation']
    with backend.read_session():
        users = backend.users()
        if (users != observed.get('users') or not isinstance(users, dict)
                or set(users) != {'tokenSid', 'shellSid'}
                or not isinstance(users['tokenSid'], str) or not _SID.fullmatch(users['tokenSid'])
                or users['tokenSid'] != users['shellSid']):
            _fail('recorded-owner-mismatch')
        if backend.directory(root) != observed.get('root'):
            _fail('recorded-physical-root-mismatch')


def resume_context_from_evidence(evidence, install_root, *, _backend=None):
    """Reconstruct OLD target inputs without executing an installed/old helper.

    This only validates context/owner/root and current ownership bytes. The caller
    MUST still call verify_native_completion(evidence, root, **returned_context)
    before advancing its durable journal. A later Setup must never substitute its
    own newer inventory or target identity when finishing an older transaction.
    """
    _validate_evidence(evidence, fixture=_backend is not None)
    contract = evidence['contract']
    fields = {'installRoot', 'appId', 'targetIdentity', 'targetReportSha256', 'ownershipSha256',
              'programInventory', 'uninstallExe', 'shortcutTargets'}
    root = _path(os.fspath(install_root))
    if set(contract) != fields or contract['appId'] != APP_ID or not _same_path(root, contract['installRoot']):
        _fail('evidence-contract-mismatch')
    backend = _backend if _backend is not None else WindowsReadOnlyBackend()
    _check_recorded_owner_root(backend, evidence, root)
    with backend.read_session():
        row = backend.file(ntpath.join(root, OWNERSHIP_NAME), required=True, raw=True, limit=MAX_METADATA)
    ownership = _raw(row, MAX_METADATA)
    result = dict(target_identity=contract['targetIdentity'], target_report_sha256=contract['targetReportSha256'],
                  ownership_bytes=ownership, ownership_sha256=contract['ownershipSha256'],
                  program_inventory=contract['programInventory'], uninstall_exe=contract['uninstallExe'],
                  shortcut_targets=contract['shortcutTargets'])
    reconstructed = _contract(root, **result)
    if _encoded(reconstructed) != _encoded(contract):
        _fail('evidence-contract-mismatch')
    return result


def current_user_sid():
    """Read process/Shell SID equality for the journal's owner binding.

    No platform mock, environment variable or cached username can satisfy this
    production entrypoint. Failure means owner identity is unknown, not approval.
    """
    users = WindowsReadOnlyBackend().users()
    if (not isinstance(users, dict) or set(users) != {'tokenSid', 'shellSid'}
            or not isinstance(users['tokenSid'], str) or not _SID.fullmatch(users['tokenSid'])
            or users['tokenSid'] != users['shellSid']):
        _fail('different-or-unknown-interactive-user')
    return users['tokenSid']


class _FileInfo(ctypes.Structure):
    _fields_ = [('attributes', W.DWORD), ('created', W.FILETIME), ('accessed', W.FILETIME),
                ('written', W.FILETIME), ('volume', W.DWORD), ('sizeHigh', W.DWORD),
                ('sizeLow', W.DWORD), ('links', W.DWORD), ('indexHigh', W.DWORD), ('indexLow', W.DWORD)]


class _FileIdInfo(ctypes.Structure):
    _fields_ = [('volumeSerial', ctypes.c_ulonglong), ('fileId', ctypes.c_ubyte * 16)]


def _ticks(time):
    return (time.dwHighDateTime << 32) | time.dwLowDateTime


class WindowsReadOnlyBackend:
    """Bounded Win32 reads, current user only, no registry/shortcut write API.

    Ancestor directory handles are kept open without delete sharing throughout a
    pass. Files are read through one handle without write/delete sharing; paths,
    identity, single-link status and metadata are checked on that same handle.
    Registry reads open every ancestor with REG_OPTION_OPEN_LINK and verify its
    NT key name and absence of REG_LINK before enumerating only our product tree.
    """
    def __init__(self):
        if os.name != 'nt':
            _fail('windows-required')
        self.kernel = ctypes.WinDLL('kernel32', use_last_error=True)
        self.advapi = ctypes.WinDLL('advapi32', use_last_error=True)
        self.user = ctypes.WinDLL('user32', use_last_error=True)
        self.shell = ctypes.WinDLL('shell32', use_last_error=True)
        self.ole = ctypes.WinDLL('ole32', use_last_error=True)
        self.nt = ctypes.WinDLL('ntdll', use_last_error=True)
        declarations = {
            self.kernel: {
                'CreateFileW': ([W.LPCWSTR, W.DWORD, W.DWORD, W.LPVOID, W.DWORD, W.DWORD, W.HANDLE], W.HANDLE),
                'CloseHandle': ([W.HANDLE], W.BOOL), 'GetFileType': ([W.HANDLE], W.DWORD),
                'GetFileInformationByHandle': ([W.HANDLE, ctypes.POINTER(_FileInfo)], W.BOOL),
                'GetFileInformationByHandleEx': ([W.HANDLE, ctypes.c_int, W.LPVOID, W.DWORD], W.BOOL),
                'GetFinalPathNameByHandleW': ([W.HANDLE, W.LPWSTR, W.DWORD, W.DWORD], W.DWORD),
                'ReadFile': ([W.HANDLE, W.LPVOID, W.DWORD, ctypes.POINTER(W.DWORD), W.LPVOID], W.BOOL),
                'OpenProcess': ([W.DWORD, W.BOOL, W.DWORD], W.HANDLE),
                'GetCurrentProcess': ([], W.HANDLE), 'GetCurrentThread': ([], W.HANDLE),
                'GetCurrentProcessId': ([], W.DWORD),
                'LocalFree': ([W.LPVOID], W.LPVOID),
                'ProcessIdToSessionId': ([W.DWORD, ctypes.POINTER(W.DWORD)], W.BOOL),
            },
            self.user: {
                'GetShellWindow': ([], W.HWND),
                'GetWindowThreadProcessId': ([W.HWND, ctypes.POINTER(W.DWORD)], W.DWORD),
            },
            self.advapi: {
                'OpenProcessToken': ([W.HANDLE, W.DWORD, ctypes.POINTER(W.HANDLE)], W.BOOL),
                'OpenThreadToken': ([W.HANDLE, W.DWORD, W.BOOL, ctypes.POINTER(W.HANDLE)], W.BOOL),
                'GetTokenInformation': ([W.HANDLE, ctypes.c_int, W.LPVOID, W.DWORD, ctypes.POINTER(W.DWORD)], W.BOOL),
                'ConvertSidToStringSidW': ([W.LPVOID, ctypes.POINTER(W.LPWSTR)], W.BOOL),
                'RegOpenCurrentUser': ([W.DWORD, ctypes.POINTER(W.HANDLE)], W.LONG),
                'RegOpenKeyExW': ([W.HANDLE, W.LPCWSTR, W.DWORD, W.DWORD, ctypes.POINTER(W.HANDLE)], W.LONG),
                'RegCloseKey': ([W.HANDLE], W.LONG),
                'RegQueryValueExW': ([W.HANDLE, W.LPCWSTR, W.LPVOID, ctypes.POINTER(W.DWORD), W.LPVOID, ctypes.POINTER(W.DWORD)], W.LONG),
                'RegQueryInfoKeyW': ([W.HANDLE, W.LPWSTR, ctypes.POINTER(W.DWORD), W.LPVOID,
                    ctypes.POINTER(W.DWORD), ctypes.POINTER(W.DWORD), ctypes.POINTER(W.DWORD),
                    ctypes.POINTER(W.DWORD), ctypes.POINTER(W.DWORD), ctypes.POINTER(W.DWORD),
                    ctypes.POINTER(W.DWORD), ctypes.POINTER(W.FILETIME)], W.LONG),
                'RegEnumKeyExW': ([W.HANDLE, W.DWORD, W.LPWSTR, ctypes.POINTER(W.DWORD), W.LPVOID,
                                  W.LPWSTR, ctypes.POINTER(W.DWORD), ctypes.POINTER(W.FILETIME)], W.LONG),
                'RegEnumValueW': ([W.HANDLE, W.DWORD, W.LPWSTR, ctypes.POINTER(W.DWORD), W.LPVOID,
                                  ctypes.POINTER(W.DWORD), W.LPVOID, ctypes.POINTER(W.DWORD)], W.LONG),
            },
            self.shell: {
                'SHGetKnownFolderPath': ([W.LPVOID, W.DWORD, W.HANDLE, ctypes.POINTER(W.LPWSTR)], W.LONG),
                'SHGetPathFromIDListEx': ([W.LPVOID, W.LPWSTR, W.DWORD, W.DWORD], W.BOOL),
            },
            self.ole: {'CoTaskMemFree': ([W.LPVOID], None)},
            self.nt: {'NtQueryKey': ([W.HANDLE, ctypes.c_int, W.LPVOID, W.DWORD, ctypes.POINTER(W.DWORD)], W.LONG)},
        }
        for dll, specs in declarations.items():
            for name, (arguments, result) in specs.items():
                function = getattr(dll, name)
                function.argtypes, function.restype = arguments, result
        self._directories = None

    @contextmanager
    def read_session(self):
        if self._directories is not None:
            _fail('nested-read-session')
        self._directories = {}
        try:
            yield self
        finally:
            for handle, _ in reversed(list(self._directories.values())):
                self.kernel.CloseHandle(handle)
            self._directories = None

    def _open(self, path, *, directory=False, optional=False):
        # FILE_READ_ATTRIBUTES / GENERIC_READ; OPEN_EXISTING only, no writes.
        access = 0x80 if directory else 0x80000000
        share = 3 if directory else 1
        handle = self.kernel.CreateFileW(path, access, share, None, 3, 0x02200000, None)
        if handle in (None, ctypes.c_void_p(-1).value):
            error = ctypes.get_last_error()
            if optional and error in (2, 3):
                return None
            _fail('file-read-unavailable')
        return handle

    def _info(self, handle, *, directory):
        info = _FileInfo()
        if self.kernel.GetFileType(handle) != 1 or not self.kernel.GetFileInformationByHandle(handle, ctypes.byref(info)):
            _fail('file-identity-unavailable')
        if info.attributes & 0x400 or bool(info.attributes & 0x10) != directory or (not directory and info.links != 1):
            _fail('file-is-linked-or-wrong-kind')
        buf = ctypes.create_unicode_buffer(32768)
        count = self.kernel.GetFinalPathNameByHandleW(handle, buf, len(buf), 1)
        if not 0 < count < len(buf) or not re.match(r'^\\\\\?\\Volume\{[0-9a-f-]{36}\}\\', buf.value, re.I):
            _fail('physical-path-unavailable')
        full_id = _FileIdInfo()
        if not self.kernel.GetFileInformationByHandleEx(handle, 18, ctypes.byref(full_id), ctypes.sizeof(full_id)):
            _fail('full-file-identity-unavailable')
        if not any(full_id.fileId):
            _fail('empty-file-identity')
        result = dict(physicalPath=buf.value.rstrip('\\').casefold(),
                      identity=dict(volumeSerial=int(full_id.volumeSerial), fileId=bytes(full_id.fileId).hex()),
                      created=_ticks(info.created), attributes=int(info.attributes))
        if not directory:
            result.update(written=_ticks(info.written), links=int(info.links),
                          bytes=(int(info.sizeHigh) << 32) | int(info.sizeLow))
        return result

    def _parents(self, path, *, include_leaf=False, allow_missing=False):
        if self._directories is None:
            _fail('read-session-required')
        path = _path(path)
        components = path[3:].split('\\') if len(path) > 3 else []
        if not include_leaf and components:
            components.pop()
        cursor, rows, missing = path[:3], [], False
        for part in ['', *components]:
            if part:
                cursor = ntpath.join(cursor, part)
            key = cursor.casefold()
            if missing:
                rows.append(dict(path=cursor, state='absent'))
                continue
            cached = self._directories.get(key)
            if cached is None:
                handle = self._open(cursor, directory=True, optional=allow_missing)
                if handle is None:
                    missing = True
                    rows.append(dict(path=cursor, state='absent'))
                    continue
                try:
                    row = self._info(handle, directory=True)
                    if rows and row['physicalPath'] != rows[-1]['physicalPath'] + '\\' + part.casefold():
                        _fail('directory-path-alias')
                except BaseException:
                    self.kernel.CloseHandle(handle)
                    raise
                self._directories[key] = (handle, row)
            else:
                handle, row = cached
                if self._info(handle, directory=True) != row:
                    _fail('directory-changed')
            rows.append(dict(path=cursor, state='directory', **row))
        return rows

    def directory(self, path):
        rows = self._parents(path, include_leaf=True)
        return dict(state='directory', ancestors=rows)

    def file(self, path, *, required, raw=False, limit=MAX_FILE):
        path = _path(path)
        parents = self._parents(path, allow_missing=not required)
        if parents[-1]['state'] == 'absent':
            return dict(state='absent', ancestors=parents)
        handle = self._open(path, optional=not required)
        if handle is None:
            return dict(state='absent', ancestors=parents)
        try:
            info = self._info(handle, directory=False)
            if info['physicalPath'] != parents[-1]['physicalPath'] + '\\' + ntpath.basename(path).casefold():
                _fail('file-path-alias')
            if info['bytes'] > limit:
                _fail('file-size-limit')
            digest, chunks, total = hashlib.sha256(), [], 0
            buf, read = ctypes.create_string_buffer(1024 * 1024), W.DWORD()
            while True:
                if not self.kernel.ReadFile(handle, buf, len(buf), ctypes.byref(read), None):
                    _fail('file-read-unavailable')
                if not read.value:
                    break
                data = buf.raw[:read.value]
                total += len(data)
                if total > limit or (raw and total > MAX_METADATA):
                    _fail('file-size-limit')
                digest.update(data)
                if raw:
                    chunks.append(data)
            if total != info['bytes'] or self._info(handle, directory=False) != info:
                _fail('file-changed-during-read')
            result = dict(state='file', ancestors=parents, **info, sha256=digest.hexdigest())
            if raw:
                result['rawBase64'] = base64.b64encode(b''.join(chunks)).decode('ascii')
            return result
        finally:
            self.kernel.CloseHandle(handle)

    def absent(self, path):
        # Any existing object is a collision, including a directory or reparse
        # point. Do not read through that object or enumerate its contents.
        parents = self._parents(path, allow_missing=True)
        if parents[-1]['state'] == 'absent':
            return dict(state='absent', ancestors=parents)
        handle = self._open(_path(path), directory=True, optional=True)
        if handle is None:
            return dict(state='absent', ancestors=parents)
        self.kernel.CloseHandle(handle)
        _fail('shortcut-collision')

    def _sid(self, process):
        token = W.HANDLE()
        if not self.advapi.OpenProcessToken(process, 8, ctypes.byref(token)):
            _fail('token-unavailable')
        try:
            needed = W.DWORD()
            self.advapi.GetTokenInformation(token, 1, None, 0, ctypes.byref(needed))
            if not 0 < needed.value <= 65536:
                _fail('token-user-unavailable')
            data = ctypes.create_string_buffer(needed.value)
            if not self.advapi.GetTokenInformation(token, 1, data, needed, ctypes.byref(needed)):
                _fail('token-user-unavailable')
            sid = ctypes.cast(data, ctypes.POINTER(ctypes.c_void_p))[0]
            value = W.LPWSTR()
            if not self.advapi.ConvertSidToStringSidW(sid, ctypes.byref(value)):
                _fail('token-user-unavailable')
            try:
                return value.value
            finally:
                self.kernel.LocalFree(value)
        finally:
            self.kernel.CloseHandle(token)

    def users(self):
        thread_token = W.HANDLE()
        if self.advapi.OpenThreadToken(self.kernel.GetCurrentThread(), 8, True, ctypes.byref(thread_token)):
            self.kernel.CloseHandle(thread_token)
            _fail('impersonated-thread-not-supported')
        if ctypes.get_last_error() != 1008:  # ERROR_NO_TOKEN
            _fail('thread-token-unknown')
        hwnd, pid = self.user.GetShellWindow(), W.DWORD()
        if not hwnd or not self.user.GetWindowThreadProcessId(hwnd, ctypes.byref(pid)) or not pid.value:
            _fail('interactive-shell-unavailable')
        current_session, shell_session = W.DWORD(), W.DWORD()
        if (not self.kernel.ProcessIdToSessionId(self.kernel.GetCurrentProcessId(), ctypes.byref(current_session))
                or not self.kernel.ProcessIdToSessionId(pid, ctypes.byref(shell_session))
                or current_session.value != shell_session.value):
            _fail('interactive-session-mismatch')
        process = self.kernel.OpenProcess(0x1000, False, pid)
        if not process:
            _fail('interactive-shell-token-unavailable')
        try:
            return dict(tokenSid=self._sid(self.kernel.GetCurrentProcess()), shellSid=self._sid(process))
        finally:
            self.kernel.CloseHandle(process)

    def shortcut_locations(self):
        result = {}
        for name, guid in (('programs', 'a77f5d77-2e2b-44c3-a6a2-aba601054a51'),
                           ('desktop', 'b4bfcc3a-db2c-424c-b029-7fe99a87c641')):
            identifier = ctypes.create_string_buffer(uuid.UUID(guid).bytes_le)
            value = W.LPWSTR()
            # KF_FLAG_DONT_VERIFY only reads the configured location, no folder
            # creation and no implicit touching of another account's profile.
            if self.shell.SHGetKnownFolderPath(identifier, 0x4000, None, ctypes.byref(value)) != 0:
                _fail('current-user-shortcut-location-unavailable')
            try:
                result[name] = _path(value.value)
            finally:
                self.ole.CoTaskMemFree(value)
        return result

    def shortcut_target(self, data):
        target = _shortcut_target(data)
        flags = struct.unpack_from('<I', data, 20)[0]
        if flags & 1:
            size = struct.unpack_from('<H', data, 76)[0]
            pidl = data[78:78 + size]
            offset, items = 0, []
            while offset + 2 <= len(pidl):
                count = struct.unpack_from('<H', pidl, offset)[0]
                if count == 0:
                    if offset + 2 != len(pidl):
                        _fail('shortcut-idlist-trailing-data')
                    break
                if count < 3 or offset + count > len(pidl):
                    _fail('shortcut-idlist-invalid')
                items.append(pidl[offset:offset + count])
                offset += count
            else:
                _fail('shortcut-idlist-unterminated')
            # Only local MyComputer -> drive -> filesystem items are accepted.
            # Do not invoke arbitrary shell namespaces or network providers.
            if (len(items) < 3 or len(items[0]) != 20 or items[0][2] != 0x1f
                    or items[0][4:20] != uuid.UUID('20d04fe0-3aea-1069-a2d8-08002b30309d').bytes_le
                    or items[1][2] != 0x2f or len(items[1]) < 7
                    or not re.fullmatch(rb'[A-Za-z]:\\\x00', items[1][3:7])
                    or any(item[2] not in (0x31, 0x32, 0x35, 0x36) for item in items[2:])):
                _fail('shortcut-idlist-unsupported')
            buffer = ctypes.create_string_buffer(pidl)
            path = ctypes.create_unicode_buffer(32768)
            # Converts this already-bounded IDLIST only. Never Resolve,
            # ShellExecute, load a shortcut from disk or launch its target.
            if not self.shell.SHGetPathFromIDListEx(buffer, path, len(path), 0):
                _fail('shortcut-idlist-path-unavailable')
            if not _same_path(path.value, target):
                _fail('shortcut-idlist-target-conflict')
        return target

    def _key_name(self, key):
        needed = W.DWORD()
        self.nt.NtQueryKey(key, 3, None, 0, ctypes.byref(needed))
        if not 4 <= needed.value <= 65536:
            _fail('registry-physical-name-unavailable')
        buffer = ctypes.create_string_buffer(needed.value)
        if self.nt.NtQueryKey(key, 3, buffer, len(buffer), ctypes.byref(needed)) != 0:
            _fail('registry-physical-name-unavailable')
        length = struct.unpack_from('<I', buffer.raw)[0]
        if length % 2 or length + 4 > len(buffer):
            _fail('registry-physical-name-invalid')
        try:
            return buffer.raw[4:4 + length].decode('utf-16-le')
        except UnicodeError:
            _fail('registry-physical-name-invalid')

    def _check_key(self, key, expected_name):
        if self._key_name(key).casefold() != expected_name.casefold():
            _fail('registry-redirected')
        kind, size = W.DWORD(), W.DWORD()
        status = self.advapi.RegQueryValueExW(key, 'SymbolicLinkValue', None, ctypes.byref(kind), None, ctypes.byref(size))
        if status != 2:  # A present reserved link name is conservatively rejected.
            _fail('registry-link-or-unknown')

    def _key_info(self, key):
        subkeys, values, max_data, stamp = W.DWORD(), W.DWORD(), W.DWORD(), W.FILETIME()
        klass, size = ctypes.create_unicode_buffer(32768), W.DWORD(32768)
        status = self.advapi.RegQueryInfoKeyW(key, klass, ctypes.byref(size), None,
            ctypes.byref(subkeys), None, None, ctypes.byref(values), None, ctypes.byref(max_data), None, ctypes.byref(stamp))
        if status != 0 or size.value >= len(klass):
            _fail('registry-info-unavailable')
        if subkeys.value > MAX_REG_KEYS or values.value > MAX_REG_VALUES or max_data.value > MAX_METADATA // 2:
            _fail('registry-limit')
        return dict(lastWrite=_ticks(stamp), subkeyCount=subkeys.value,
                    valueCount=values.value, keyClass=klass.value, maxValueBytes=max_data.value)

    @staticmethod
    def _check_value(kind, data):
        if kind not in (0, 1, 2, 3, 4, 5, 7, 11):
            _fail('registry-value-type-unsupported')
        if kind in (4, 5, 11) and len(data) != (8 if kind == 11 else 4):
            _fail('registry-integer-invalid')
        if kind in (1, 2, 7):
            try:
                text = data.decode('utf-16-le')
            except UnicodeError:
                _fail('registry-text-invalid')
            if not text.endswith('\0\0' if kind == 7 else '\0'):
                _fail('registry-text-unterminated')
            if kind != 7 and '\0' in text[:-1]:
                _fail('registry-text-embedded-nul')

    def _tree(self, key, path, budget, depth=0):
        if depth > 16:
            _fail('registry-depth-limit')
        self._check_key(key, path)
        info = self._key_info(key)
        budget[0] += 1
        budget[1] += info['valueCount']
        if budget[0] > MAX_REG_KEYS or budget[1] > MAX_REG_VALUES:
            _fail('registry-limit')
        values, children, seen = [], [], set()
        for index in range(info['valueCount'] + 1):
            name, size = ctypes.create_unicode_buffer(16384), W.DWORD(16384)
            kind, length = W.DWORD(), W.DWORD(info['maxValueBytes'])
            data = ctypes.create_string_buffer(max(1, info['maxValueBytes']))
            status = self.advapi.RegEnumValueW(key, index, name, ctypes.byref(size), None,
                ctypes.byref(kind), data, ctypes.byref(length))
            if index == info['valueCount']:
                if status != 259:
                    _fail('registry-changed-during-read')
                break
            if status != 0 or size.value >= len(name) or length.value > info['maxValueBytes']:
                _fail('registry-value-unavailable')
            text, raw = name.value, data.raw[:length.value]
            if text.casefold() in seen or '\0' in text or len(text) != size.value:
                _fail('registry-duplicate-or-invalid-value')
            seen.add(text.casefold())
            self._check_value(kind.value, raw)
            budget[2] += len(raw) + len(text.encode('utf-16-le'))
            if budget[2] > MAX_METADATA // 2:
                _fail('registry-byte-limit')
            values.append(dict(name=text, type=kind.value, rawBase64=base64.b64encode(raw).decode('ascii')))
        seen.clear()
        for index in range(info['subkeyCount'] + 1):
            name, size, stamp = ctypes.create_unicode_buffer(256), W.DWORD(256), W.FILETIME()
            status = self.advapi.RegEnumKeyExW(key, index, name, ctypes.byref(size), None, None, None, ctypes.byref(stamp))
            if index == info['subkeyCount']:
                if status != 259:
                    _fail('registry-changed-during-read')
                break
            if status != 0 or not 0 < size.value < len(name) or '\\' in name.value:
                _fail('registry-child-unavailable')
            if name.value.casefold() in seen:
                _fail('registry-duplicate-child')
            seen.add(name.value.casefold())
            child = W.HANDLE()
            if self.advapi.RegOpenKeyExW(key, name.value, 8, 0x20119, ctypes.byref(child)) != 0:
                _fail('registry-child-unavailable')
            try:
                row = self._tree(child, path + '\\' + name.value, budget, depth + 1)
                if row['lastWrite'] != _ticks(stamp):
                    _fail('registry-changed-during-read')
                children.append(dict(name=name.value, evidence=row))
            finally:
                self.advapi.RegCloseKey(child)
        if self._key_info(key) != info:
            _fail('registry-changed-during-read')
        self._check_key(key, path)
        return dict(path=path, **info, values=sorted(values, key=lambda r: r['name'].casefold()),
                    subkeys=sorted(children, key=lambda r: r['name'].casefold()))

    def registry(self, sid):
        handles, current = [], W.HANDLE()
        # KEY_READ | KEY_WOW64_64KEY, production Setup uses 64-bit install mode.
        if self.advapi.RegOpenCurrentUser(0x20119, ctypes.byref(current)) != 0:
            _fail('current-user-registry-unavailable')
        handles.append(current)
        path = '\\REGISTRY\\USER\\' + sid
        try:
            self._check_key(current, path)
            for name in UNINSTALL_KEY.split('\\'):
                next_key = W.HANDLE()
                if self.advapi.RegOpenKeyExW(current, name, 8, 0x20119, ctypes.byref(next_key)) != 0:
                    _fail('uninstall-registry-unavailable')
                current = next_key
                handles.append(current)
                path += '\\' + name
                self._check_key(current, path)
            return self._tree(current, path, [0, 0, 0])
        finally:
            for key in reversed(handles):
                self.advapi.RegCloseKey(key)
