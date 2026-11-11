"""Durable, forward-only coordinator for Setup's resource/native boundary.

Only call while holding Setup's verified borrowed resource lease. This stdlib
module neither acquires a lease nor bypasses pending-resource checks. The caller
obtains owner_sid from the current Windows token, and supplies read-only native
and resource verifiers. A callback must return the literal True after verifying
current objects, not merely a cached receipt or an installer exit code.

All generations are immutable, exclusive publications of fsynced objects.
The legacy protocol retains hardlink identity pins. On a probed linkless volume,
move-v1 persists each object's identity/length/hash before an exclusive atomic
move; the anchor explicitly selects this evidence format. The marker is bound
the same way, without copying payloads or adopting equal bytes as the same object. Unknown objects
are never overwritten or adopted, including equal bytes in a different inode.
Only the owned pending marker is removed; all evidence and all old program and
resource bytes are retained. Directory fsync is used where Python supports it;
this is process-loss recovery, not a multi-file power-loss atomicity claim.
"""
from __future__ import annotations

import copy
import ctypes
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import uuid

from resource_transaction import preflight_resource_publication, rename_no_replace

SCHEMA = 'xingtu-installer-upgrade-v1'
PENDING = 'work/pending-installer-upgrade.json'
HISTORY = 'work/installer-upgrades'
MAX_METADATA_BYTES = 36 * 1024 * 1024
MAX_EVIDENCE_BYTES = 32 * 1024 * 1024
MAX_JSON_NODES = 1250000
MAX_JSON_DEPTH = 64
MAX_JSON_ITEMS = 20000
MAX_JSON_STRING = 4 * 1024 * 1024
MAX_JSON_KEY = 32768
MAX_GENERATIONS = 8
_STATES = {'prepared', 'native-in-progress', 'native-complete', 'completed', 'aborted'}
_BINDINGS = {'ownerSid', 'sessionId', 'targetIdentity', 'reportSha256', 'planSha256', 'restoreWatcher'}
_RECORD = {'schema', 'upgradeId', 'generation', 'previous', 'anchorSha256', 'pinId',
           'status', 'resourceTransactionId', 'nativeEvidence', 'nativeEvidenceSha256'}
_ANCHOR = {'schema', 'upgradeId', 'rootIdentity', 'directoryIdentities'} | _BINDINGS


class InstallerUpgradeError(ValueError):
    def __init__(self, code):
        self.code = 'installer-upgrade-' + code
        super().__init__(self.code)


def _fail(code):
    raise InstallerUpgradeError(code)


def _uuid(value):
    if not isinstance(value, str):
        _fail('invalid-session-or-transaction-id')
    try:
        parsed = uuid.UUID(value)
    except ValueError:
        _fail('invalid-session-or-transaction-id')
    if str(parsed) != value or parsed.version != 4:
        _fail('invalid-session-or-transaction-id')
    return value


def _sha(value):
    if not isinstance(value, str) or not re.fullmatch('[0-9a-f]{64}', value):
        _fail('invalid-digest')
    return value


def _sid(value):
    if (not isinstance(value, str) or len(value) > 184
            or not re.fullmatch(r'S-1-[0-9]+(?:-[0-9]+){1,15}', value)):
        _fail('invalid-owner-sid')
    return value


def _watcher_intent(value):
    if type(value) is not bool:
        _fail('invalid-watcher-intent')
    return value


def _target(value):
    keys = {'sourceCommit', 'displayVersion', 'numericVersion',
            'executableSha256', 'resourceInventorySha256'}
    if (type(value) is not dict or set(value) != keys
            or any(type(value[k]) is not str for k in keys)
            or not re.fullmatch('[0-9a-f]{40}', value['sourceCommit'])
            or len(value['displayVersion']) > 64
            or not re.fullmatch(r'[0-9]+\.[0-9]+\.[0-9]+(?:-[0-9A-Za-z.-]+)?', value['displayVersion'])
            or len(value['numericVersion']) > 23
            or not re.fullmatch(r'[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+', value['numericVersion'])
            or any(int(part) > 65535 for part in value['numericVersion'].split('.'))):
        _fail('invalid-target-identity')
    for key in ('executableSha256', 'resourceInventorySha256'):
        _sha(value[key])
    return copy.deepcopy(value)


def _identity(info):
    return [info.st_dev, info.st_ino]


def _valid_identity(value):
    if (type(value) is not list or len(value) != 2
            or any(type(n) is not int or not 0 <= n < 2 ** 256 for n in value)
            or value[1] == 0):
        _fail('invalid-object-identity')


def _safe(path):
    for part in (path, *path.parents):
        try:
            info = part.lstat()
        except FileNotFoundError:
            continue
        if stat.S_ISLNK(info.st_mode) or getattr(info, 'st_file_attributes', 0) & 0x400:
            _fail('linked-path')
        if part != path and not stat.S_ISDIR(info.st_mode):
            _fail('invalid-ancestor')
    return path


def _root(value):
    path = _safe(Path(os.path.abspath(value)))
    if not path.is_dir():
        _fail('root-not-directory')
    _valid_identity(_identity(path.stat()))
    return path


def _directory_identity(path):
    _safe(path)
    info = path.stat()
    if not stat.S_ISDIR(info.st_mode):
        _fail('not-directory')
    result = _identity(info)
    _valid_identity(result)
    return result


def _bounded(value, *, evidence=False):
    # Bound depth, node count, strings, integer size and types before encoding.
    # No NaN, arbitrary objects, cycles or lossy tuple conversions are accepted.
    remaining = MAX_JSON_NODES
    def visit(item, depth):
        nonlocal remaining
        remaining -= 1
        if remaining < 0 or depth > MAX_JSON_DEPTH:
            _fail('metadata-limit')
        kind = type(item)
        if item is None or kind is bool:
            return
        if kind is int and -(2 ** 256) < item < 2 ** 256:
            return
        if kind is str and len(item) <= MAX_JSON_STRING:
            return
        if kind is list and len(item) <= MAX_JSON_ITEMS:
            for child in item:
                visit(child, depth + 1)
            return
        if kind is dict and len(item) <= MAX_JSON_ITEMS:
            for key, child in item.items():
                if type(key) is not str or len(key) > MAX_JSON_KEY:
                    _fail('invalid-json-key')
                visit(key, depth + 1)
                visit(child, depth + 1)
            return
        _fail('invalid-json-value')
    visit(value, 0)
    try:
        data = json.dumps(value, ensure_ascii=False, sort_keys=True,
                          separators=(',', ':'), allow_nan=False).encode('utf-8')
    except (ValueError, UnicodeError, RecursionError) as error:
        raise InstallerUpgradeError('invalid-json-value') from error
    if len(data) > (MAX_EVIDENCE_BYTES if evidence else MAX_METADATA_BYTES):
        _fail('metadata-limit')
    return data


def _digest(data):
    return hashlib.sha256(data).hexdigest()


def _parse(data):
    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                _fail('duplicate-json-key')
            result[key] = value
        return result
    try:
        value = json.loads(data.decode('utf-8'), object_pairs_hook=unique,
                           parse_constant=lambda _: _fail('invalid-json-value'))
        # The writer uses this representation; alternate/trailing encodings
        # are not a new authority for an immutable record.
        if _bounded(value) != data:
            _fail('noncanonical-json')
        return value
    except (UnicodeError, json.JSONDecodeError, RecursionError) as error:
        raise InstallerUpgradeError('invalid-json') from error


def _read(path):
    _safe(path)
    if not stat.S_ISREG(path.lstat().st_mode):
        _fail('invalid-metadata-file')
    flags = (os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0)
             | getattr(os, 'O_NONBLOCK', 0) | getattr(os, 'O_BINARY', 0))
    fd = os.open(path, flags)
    with os.fdopen(fd, 'rb') as handle:
        before = os.fstat(handle.fileno())
        if not stat.S_ISREG(before.st_mode) or before.st_size > MAX_METADATA_BYTES:
            _fail('invalid-metadata-file')
        data = handle.read(MAX_METADATA_BYTES + 1)
        after = os.fstat(handle.fileno())
        stable = lambda s: (_identity(s), s.st_size, s.st_mtime_ns, s.st_ctime_ns)
        _safe(path)
        # CPython 3.13 Windows stat().st_ctime is birthtime but fstat()
        # reports ChangeTime. Compare two HANDLE observations, not mixed APIs.
        current_fd = os.open(path, flags)
        try:
            current = os.fstat(current_fd)
        finally:
            os.close(current_fd)
        _safe(path)
        if (len(data) != before.st_size or stable(before) != stable(after)
                or not stat.S_ISREG(current.st_mode) or stable(current) != stable(after)):
            _fail('object-changed-during-read')
    return data, dict(identity=_identity(before), bytes=len(data), sha256=_digest(data))


def _stamp(value):
    if type(value) is not dict or set(value) != {'identity', 'bytes', 'sha256'}:
        _fail('invalid-object-stamp')
    _valid_identity(value['identity'])
    _sha(value['sha256'])
    if type(value['bytes']) is not int or not 0 < value['bytes'] <= MAX_METADATA_BYTES:
        _fail('invalid-object-stamp')


def _directory_sync(path):
    if os.name == 'nt':
        return  # No portable Python directory FlushFileBuffers contract.
    fd = os.open(_safe(path), os.O_RDONLY | getattr(os, 'O_DIRECTORY', 0))
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def _mkdir(path, *, exclusive=False):
    _safe(path)
    path.mkdir(exist_ok=not exclusive)
    _directory_identity(path)
    _directory_sync(path.parent)


def _write_new(path, data):
    _safe(path)
    # A killed writer must never expose half a plan/generation as its final
    # name. Private abandoned objects remain evidence, never retry authority.
    temporary = _safe(path.with_name('.writing-' + uuid.uuid4().hex))
    with temporary.open('xb') as stream:
        stream.write(data)
        stream.flush()
        os.fsync(stream.fileno())
    rename_no_replace(temporary, path)
    _directory_sync(path.parent)


def _publish(directory, name, value, *, publication='hardlink-v1'):
    """Complete private object before exclusive publication, retaining its pin."""
    value = dict(value, pinId=str(uuid.uuid4()))
    data = _bounded(value)
    pin = directory / 'objects' / (value['pinId'] + '.json')
    _write_new(pin, data)
    # Private UUID pins let retries preserve, rather than adopt or overwrite,
    # objects left by an interruption before the visible publication.
    retained = directory / 'pins' / (value['pinId'] + '.json')
    _safe(retained)
    if publication == 'move-v1':
        _write_new(retained, _bounded(_read(pin)[1]))
    else:
        os.link(pin, retained)
        _directory_sync(retained.parent)
    name = name.removesuffix('.json') + '-' + _digest(data) + '.json'
    path = directory / 'generations' / name
    _safe(path)
    if publication == 'move-v1':
        rename_no_replace(pin, path)
        if _read(path)[1] != _parse(_read(retained)[0]):
            _fail('generation-identity-changed')
    else:
        os.link(retained, path)
    _directory_sync(path.parent)
    stamp = _read(path)[1]
    # A second immutable publication proves that this generation was exposed
    # before returning permission to start native mutation. If a later writer
    # removes the latest generation, it cannot silently turn in-progress back
    # into prepared. An interrupted receipt publication may be absent, never
    # inferred as proof that native did run.
    proof_id = str(uuid.uuid4())
    proof = _bounded(dict(generation=value['generation'], stamp=stamp, pinId=proof_id))
    proof_pin = directory / 'objects' / (proof_id + '.json')
    _write_new(proof_pin, proof)
    proof_path = directory / 'published' / (f"{value['generation']:06d}-" + _digest(proof) + '.json')
    _safe(proof_path)
    if publication == 'move-v1':
        proof_witness = directory / 'pins' / (proof_id + '.json')
        _write_new(proof_witness, _bounded(_read(proof_pin)[1]))
        rename_no_replace(proof_pin, proof_path)
        if _read(proof_path)[1] != _parse(_read(proof_witness)[0]):
            _fail('publication-proof-changed')
    else:
        os.link(proof_pin, proof_path)
    _directory_sync(proof_path.parent)
    return stamp


def _check_anchor(root, directory, anchor):
    if (type(anchor) is not dict or set(anchor) - {'publication', 'resourceAction'} != _ANCHOR
            or anchor['schema'] != SCHEMA
            or anchor.get('resourceAction', 'reset') not in ('reset', 'preserve', 'legacy')
            or 'publication' in anchor and anchor['publication'] != 'move-v1'):
        _fail('invalid-anchor')
    if _uuid(anchor['upgradeId']) != _uuid(anchor['sessionId']):
        _fail('session-mismatch')
    _sid(anchor['ownerSid']); _target(anchor['targetIdentity'])
    _watcher_intent(anchor['restoreWatcher'])
    _sha(anchor['reportSha256']); _sha(anchor['planSha256'])
    _valid_identity(anchor['rootIdentity'])
    if anchor['rootIdentity'] != _directory_identity(root):
        _fail('root-identity-changed')
    paths = {'work': root / 'work', 'history': root / HISTORY, 'upgrade': directory,
             **{name: directory / name for name in ('objects', 'pins', 'generations', 'published')}}
    identities = anchor['directoryIdentities']
    if type(identities) is not dict or set(identities) != set(paths):
        _fail('invalid-directory-identities')
    for name, path in paths.items():
        _valid_identity(identities[name])
        if identities[name] != _directory_identity(path):
            _fail('directory-identity-changed')
        if identities[name][0] != anchor['rootIdentity'][0]:
            _fail('cross-volume-history')


def _check_record(record, index, previous, anchor_sha, resource_action='reset'):
    if (type(record) is not dict or set(record) != _RECORD or record['schema'] != SCHEMA
            or type(record['generation']) is not int or record['generation'] != index
            or type(record['status']) is not str or record['status'] not in _STATES
            or record['anchorSha256'] != anchor_sha):
        _fail('invalid-generation')
    _uuid(record['upgradeId']); _uuid(record['pinId'])
    tx = record['resourceTransactionId']
    if resource_action != 'reset' and tx is not None:
        _fail('resource-binding-forbidden')
    if tx is not None:
        _uuid(tx)
    evidence, digest = record['nativeEvidence'], record['nativeEvidenceSha256']
    if record['status'] in {'native-complete', 'completed'}:
        if type(evidence) is not dict or not evidence or _digest(_bounded(evidence, evidence=True)) != digest:
            _fail('invalid-native-evidence')
        _sha(digest)
    elif evidence is not None or digest is not None:
        _fail('unexpected-native-evidence')
    if index == 0:
        if (record['status'] != 'prepared' or tx is not None or record['previous'] is not None):
            _fail('invalid-initial-generation')
        return
    _stamp(record['previous'])
    prior, stamp = previous
    if record['previous'] != stamp or record['upgradeId'] != prior['upgradeId']:
        _fail('generation-chain-changed')
    before, after = prior['status'], record['status']
    allowed = (before == 'prepared' and (
        after == 'aborted' and tx == prior['resourceTransactionId']
        or after == 'prepared' and prior['resourceTransactionId'] is None and tx is not None
        or after == 'native-in-progress' and (tx is not None or resource_action != 'reset')
        and tx == prior['resourceTransactionId'])
        or before == 'native-in-progress' and after == 'native-complete' and tx == prior['resourceTransactionId']
        or before == 'native-complete' and after == 'completed' and tx == prior['resourceTransactionId']
        and evidence == prior['nativeEvidence'] and digest == prior['nativeEvidenceSha256'])
    if not allowed:
        _fail('invalid-state-transition')


def _entries(directory):
    entries = []
    for path in _safe(directory).iterdir():
        entries.append(path)
        if len(entries) > MAX_GENERATIONS:
            _fail('invalid-generations')
    return sorted(entries, key=lambda p: p.name)


def _load(root, *, owner_sid, session_id=None, unpublished=False):
    marker = _safe(root / PENDING)
    present = marker.exists()
    if not present and session_id is None:
        return None
    if present:
        pointer_bytes, marker_stamp = _read(marker)
        pointer = _parse(pointer_bytes)
        if (type(pointer) is not dict or set(pointer) != {'schema', 'upgradeId', 'anchor', 'firstGeneration'}
                or pointer['schema'] != SCHEMA):
            _fail('invalid-pending')
        upgrade_id = _uuid(pointer['upgradeId'])
        if session_id is not None and upgrade_id != session_id:
            _fail('session-mismatch')
    else:
        upgrade_id = _uuid(session_id)
    directory = _safe(root / HISTORY / upgrade_id)
    retained, retained_stamp = _read(directory / 'pending-pointer.json')
    retained_value = _parse(retained)
    linkless = type(retained_value) is dict and retained_value.get('publication') == 'move-v1'
    if linkless:
        if set(retained_value) != {'publication', 'pointer', 'stamp'}:
            _fail('invalid-pending')
        _stamp(retained_value['stamp'])
        retained_stamp = retained_value['stamp']
        retained = _bounded(retained_value['pointer'])
        if len(retained) != retained_stamp['bytes'] or _digest(retained) != retained_stamp['sha256']:
            _fail('invalid-pending')
    if present:
        if retained != pointer_bytes or retained_stamp != marker_stamp:
            _fail('pending-identity-changed')
    else:
        pointer = _parse(retained)
        if (type(pointer) is not dict or set(pointer) != {'schema', 'upgradeId', 'anchor', 'firstGeneration'}
                or pointer['schema'] != SCHEMA or pointer['upgradeId'] != upgrade_id):
            _fail('invalid-pending')
    _stamp(pointer['anchor']); _stamp(pointer['firstGeneration'])
    anchor_bytes, anchor_stamp = _read(directory / 'anchor.json')
    if anchor_stamp != pointer['anchor']:
        _fail('anchor-changed')
    anchor = _parse(anchor_bytes)
    _check_anchor(root, directory, anchor)
    if linkless != (anchor.get('publication') == 'move-v1'):
        _fail('publication-mode-changed')
    if anchor['ownerSid'] != owner_sid:
        _fail('owner-sid-mismatch')
    if anchor['sessionId'] != upgrade_id:
        _fail('session-mismatch')
    entries = _entries(directory / 'generations')
    if not entries:
        _fail('invalid-generations')
    previous, stamps = None, []
    for index, path in enumerate(entries):
        if not re.fullmatch(rf'{index:06d}-[0-9a-f]{{64}}\.json', path.name):
            _fail('unknown-generation')
        data, stamp = _read(path)
        if path.name != f"{index:06d}-{stamp['sha256']}.json":
            _fail('generation-digest-changed')
        record = _parse(data)
        if type(record) is not dict:
            _fail('invalid-generation')
        pin_id = _uuid(record.get('pinId'))
        pin_data, pin_stamp = _read(directory / 'pins' / (pin_id + '.json'))
        if linkless:
            pin_stamp = _parse(pin_data)
            _stamp(pin_stamp)
            if stamp != pin_stamp:
                _fail('generation-identity-changed')
        elif stamp != pin_stamp or data != pin_data:
            _fail('generation-identity-changed')
        if index == 0 and stamp != pointer['firstGeneration']:
            _fail('first-generation-changed')
        _check_record(record, index, previous, anchor_stamp['sha256'], anchor.get('resourceAction', 'reset'))
        if record['upgradeId'] != upgrade_id:
            _fail('session-mismatch')
        previous = record, stamp
        stamps.append(stamp)
    seen_proofs = set()
    for path in _entries(directory / 'published'):
        data, proof_stamp = _read(path)
        proof = _parse(data)
        if (type(proof) is not dict or set(proof) != {'generation', 'stamp', 'pinId'}
                or type(proof['generation']) is not int or not 0 <= proof['generation'] < len(stamps)
                or proof['generation'] in seen_proofs):
            _fail('invalid-publication-proof')
        _uuid(proof['pinId']); _stamp(proof['stamp'])
        witness = (_parse(_read(directory / 'pins' / (proof['pinId'] + '.json'))[0]) if linkless
                   else _read(directory / 'objects' / (proof['pinId'] + '.json'))[1])
        _stamp(witness)
        if (path.name != f"{proof['generation']:06d}-{proof_stamp['sha256']}.json"
                or proof['stamp'] != stamps[proof['generation']]
                or witness != proof_stamp):
            _fail('publication-proof-changed')
        seen_proofs.add(proof['generation'])
    record, stamp = previous
    if not present and record['status'] not in {'completed', 'aborted'} and not (
            unpublished and record['status'] == 'prepared' and record['generation'] == 0):
        _fail('pending-missing')
    result = dict(anchor, **{k: v for k, v in record.items() if k not in {'schema', 'upgradeId'}},
                  pending=present, retainedDirectory=str(directory))
    return result, record, stamp, directory


def read_status(root, *, owner_sid):
    """Read pending bindings without changing state. Never infer completion."""
    result = _load(_root(root), owner_sid=_sid(owner_sid))
    return copy.deepcopy(result[0]) if result else dict(status='not-needed')


def _remove_owned_marker(path, expected):
    """Windows checks and deletes through one write/rename-denying handle."""
    _safe(path)
    native = None
    if os.name == 'nt':
        from ctypes import wintypes as wt
        import msvcrt
        native = ctypes.WinDLL('kernel32', use_last_error=True)
        native.CreateFileW.argtypes = [wt.LPCWSTR, wt.DWORD, wt.DWORD, wt.LPVOID, wt.DWORD, wt.DWORD, wt.HANDLE]
        native.CreateFileW.restype = wt.HANDLE
        native.CloseHandle.argtypes = [wt.HANDLE]
        native.SetFileInformationByHandle.argtypes = [wt.HANDLE, ctypes.c_int, wt.LPVOID, wt.DWORD]
        native.SetFileInformationByHandle.restype = wt.BOOL
        handle = native.CreateFileW(str(path), 0x80010000, 1, None, 3, 0x200000, None)
        if handle in (None, ctypes.c_void_p(-1).value):
            _fail('pending-open-denied')
        try:
            fd = msvcrt.open_osfhandle(int(handle), os.O_RDONLY | os.O_BINARY)
        except BaseException:
            native.CloseHandle(handle)
            raise
    else:
        fd = os.open(path, os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0))
    with os.fdopen(fd, 'rb') as stream:
        info = os.fstat(stream.fileno())
        if (not stat.S_ISREG(info.st_mode) or _identity(info) != expected['identity']
                or info.st_size != expected['bytes']
                or _digest(stream.read(MAX_METADATA_BYTES + 1)) != expected['sha256']):
            _fail('pending-identity-changed')
        if native is not None:
            disposition = ctypes.c_ubyte(1)
            if not native.SetFileInformationByHandle(msvcrt.get_osfhandle(stream.fileno()),
                                                     4, ctypes.byref(disposition), 1):
                _fail('pending-remove-denied')
        else:
            # Cooperative lease required. POSIX has no portable same-handle
            # unlink; the identity recheck does not claim hostile-writer safety.
            if _read(path)[1] != expected:
                _fail('pending-identity-changed')
            path.unlink()
    _directory_sync(path.parent)


class InstallerUpgradeJournal:
    def __init__(self, root, *, owner_sid, session_id, target_identity,
                 report_sha256, plan_sha256, restore_watcher=False, resource_action='reset'):
        if resource_action not in ('reset', 'preserve', 'legacy'):
            _fail('invalid-resource-action')
        self.resource_action = resource_action
        self.root = _root(root)
        self.bindings = dict(ownerSid=_sid(owner_sid), sessionId=_uuid(session_id),
                             targetIdentity=_target(target_identity), reportSha256=_sha(report_sha256),
                             planSha256=_sha(plan_sha256), restoreWatcher=_watcher_intent(restore_watcher))
        if resource_action != 'reset':
            self.bindings['resourceAction'] = resource_action
        self.root_identity = _directory_identity(self.root)

    def _load(self):
        if _directory_identity(self.root) != self.root_identity:
            _fail('root-identity-changed')
        result = _load(self.root, owner_sid=self.bindings['ownerSid'], session_id=self.bindings['sessionId'])
        if (result[0].get('resourceAction', 'reset') != self.resource_action
                or any(result[0].get(key) != value for key, value in self.bindings.items())):
            _fail('binding-mismatch')
        return result

    def status(self):
        return copy.deepcopy(self._load()[0])

    def begin(self):
        """Publish the durable block BEFORE any resource/program mutation."""
        if _safe(self.root / PENDING).exists():
            result = self.status()
            if result['status'] != 'prepared':
                _fail('already-started')
            return result
        if _directory_identity(self.root) != self.root_identity:
            _fail('root-identity-changed')
        publication = preflight_resource_publication(self.root, [PENDING, HISTORY])
        _mkdir(self.root / 'work')
        _mkdir(self.root / HISTORY)
        directory = self.root / HISTORY / self.bindings['sessionId']
        if directory.exists():
            # Only the complete first generation may have lost publication of
            # its pending pointer. Later generations can never restart native.
            result = _load(self.root, owner_sid=self.bindings['ownerSid'],
                           session_id=self.bindings['sessionId'], unpublished=True)
            if (result[0]['status'] != 'prepared' or result[0]['generation'] != 0
                    or any(result[0].get(k) != v for k, v in self.bindings.items())):
                _fail('binding-mismatch')
            pin = directory / 'pending-pointer.json'
            if result[0].get('publication') == 'move-v1':
                staged = directory / 'pending-pointer.stage.json'
                if _read(staged)[1] != _parse(_read(pin)[0])['stamp']:
                    _fail('pending-identity-changed')
                rename_no_replace(staged, self.root / PENDING)
            else:
                os.link(pin, self.root / PENDING)
            return self.status()
        final_directory = directory
        # Construct genesis away from its public name. A torn first write is
        # retained here and ignored on retry, with no program mutation allowed.
        directory = self.root / HISTORY / ('.initializing-' + uuid.uuid4().hex)
        _mkdir(directory, exclusive=True)
        for name in ('objects', 'pins', 'generations', 'published'):
            _mkdir(directory / name, exclusive=True)
        paths = {'work': self.root / 'work', 'history': self.root / HISTORY, 'upgrade': directory,
                 **{name: directory / name for name in ('objects', 'pins', 'generations', 'published')}}
        anchor = dict(schema=SCHEMA, upgradeId=self.bindings['sessionId'], **self.bindings,
                      rootIdentity=self.root_identity,
                      directoryIdentities={name: _directory_identity(path) for name, path in paths.items()})
        if publication == 'move-v1':
            anchor['publication'] = 'move-v1'
        _check_anchor(self.root, directory, anchor)
        _write_new(directory / 'anchor.json', _bounded(anchor))
        anchor_stamp = _read(directory / 'anchor.json')[1]
        record = dict(schema=SCHEMA, upgradeId=anchor['upgradeId'], generation=0, previous=None,
                      anchorSha256=anchor_stamp['sha256'], status='prepared', pinId=str(uuid.uuid4()), resourceTransactionId=None,
                      nativeEvidence=None, nativeEvidenceSha256=None)
        first = _publish(directory, '000000.json', record, publication=publication)
        pointer = dict(schema=SCHEMA, upgradeId=anchor['upgradeId'], anchor=anchor_stamp, firstGeneration=first)
        pin = directory / 'pending-pointer.json'
        _safe(self.root / PENDING)
        if publication == 'move-v1':
            staged = directory / 'pending-pointer.stage.json'
            _write_new(staged, _bounded(pointer))
            stamp = _read(staged)[1]
            _write_new(pin, _bounded(dict(publication='move-v1', pointer=pointer, stamp=stamp)))
            rename_no_replace(directory, final_directory)
            staged = final_directory / staged.name
            rename_no_replace(staged, self.root / PENDING)
            if _read(self.root / PENDING)[1] != stamp:
                _fail('pending-identity-changed')
        else:
            _write_new(pin, _bounded(pointer))
            rename_no_replace(directory, final_directory)
            pin = final_directory / pin.name
            os.link(pin, self.root / PENDING)
        _directory_sync(self.root / 'work')
        return self.status()

    def _verify(self, current, callback, *, native=False):
        if not callable(callback):
            _fail('verifier-required')
        state = copy.deepcopy(current[0])
        outcome = callback(state, copy.deepcopy(state['nativeEvidence'])) if native else callback(state)
        if outcome is not True:
            _fail('native-verification-failed' if native else 'resource-verification-failed')
        # A callback may take time or accidentally trigger another operation.
        # Never append/clear against the pre-callback view without rereading.
        if self._load()[:3] != current[:3]:
            _fail('changed-during-verification')

    def _append(self, current, **changes):
        if self._load()[:3] != current[:3]:
            _fail('generation-changed')
        record = dict(current[1], generation=current[1]['generation'] + 1,
                      previous=current[2], **changes)
        _check_record(record, record['generation'], (current[1], current[2]), record['anchorSha256'], self.resource_action)
        _publish(current[3], f"{record['generation']:06d}.json", record,
                 publication=current[0].get('publication', 'hardlink-v1'))
        return self.status()

    def bind_resource(self, transaction_id, *, verify_resource):
        transaction_id = _uuid(transaction_id)
        current = self._load()
        if self.resource_action != 'reset':
            _fail('resource-binding-forbidden')
        if current[0]['status'] != 'prepared':
            _fail('not-pre-native')
        bound = current[0]['resourceTransactionId']
        if bound is not None and bound != transaction_id:
            _fail('resource-transaction-mismatch')
        # Provide the proposed binding to the verifier, but compare the live
        # journal with the unchanged current record after the callback.
        proposed = copy.deepcopy(current[0]); proposed['resourceTransactionId'] = transaction_id
        if not callable(verify_resource) or verify_resource(proposed) is not True:
            _fail('resource-verification-failed')
        if self._load()[:3] != current[:3]:
            _fail('changed-during-verification')
        return current[0] if bound else self._append(current, resourceTransactionId=transaction_id)

    def start_native(self, *, verify_resource):
        current = self._load()
        # NOT retryable in native-in-progress: an uncertain start is blocked,
        # never authorization to run native mutation for a second time.
        if (current[0]['status'] != 'prepared'
                or current[0]['resourceTransactionId'] is None and self.resource_action == 'reset'):
            _fail('native-start-not-allowed')
        self._verify(current, verify_resource)
        return self._append(current, status='native-in-progress')

    def record_native_complete(self, evidence, *, verify_native):
        if type(evidence) is not dict or not evidence:
            _fail('invalid-native-evidence')
        evidence = _parse(_bounded(evidence, evidence=True))
        current = self._load()
        if current[0]['status'] in {'native-complete', 'completed'}:
            if evidence != current[0]['nativeEvidence']:
                _fail('native-evidence-changed')
            self._verify(current, verify_native, native=True)
            return current[0]
        if current[0]['status'] != 'native-in-progress':
            _fail('native-completion-not-allowed')
        proposed = copy.deepcopy(current[0]); proposed['nativeEvidence'] = evidence
        proposed['nativeEvidenceSha256'] = _digest(_bounded(evidence, evidence=True))
        if not callable(verify_native) or verify_native(copy.deepcopy(proposed), copy.deepcopy(evidence)) is not True:
            _fail('native-verification-failed')
        if self._load()[:3] != current[:3]:
            _fail('changed-during-verification')
        return self._append(current, status='native-complete', nativeEvidence=evidence,
                            nativeEvidenceSha256=proposed['nativeEvidenceSha256'])

    def assert_can_forward(self, *, verify_native):
        current = self._load()
        if current[0]['status'] not in {'native-complete', 'completed'}:
            _fail('native-completion-evidence-required')
        self._verify(current, verify_native, native=True)
        return current[0]

    def _clear(self, current):
        # The pin is only an authority after the entire bound chain validates.
        if self._load()[:3] != current[:3]:
            _fail('generation-changed')
        if current[0]['pending']:
            _directory_sync(current[3] / 'generations')
            data, pin_stamp = _read(current[3] / 'pending-pointer.json')
            if current[0].get('publication') == 'move-v1':
                pin_stamp = _parse(data)['stamp']
            _remove_owned_marker(self.root / PENDING, pin_stamp)
        return self.status()

    def finish_complete(self, *, verify_native, verify_resource):
        if self.resource_action == 'legacy':
            # Asset publication alone cannot prove historical QtIFW retirement.
            # The legacy owner must use finish_legacy with its own verifier.
            _fail('legacy-retirement-unverified')
        current = self._load()
        if current[0]['status'] not in {'native-complete', 'completed'}:
            _fail('native-completion-evidence-required')
        self._verify(current, verify_native, native=True)
        self._verify(current, verify_resource)
        if current[0]['status'] != 'completed':
            self._append(current, status='completed')
            current = self._load()
        return self._clear(current)

    def finish_legacy(self, *, verify_native, verify_resource, verify_retirement):
        """Only the legacy owner may clear its pending after actual retirement."""
        if self.resource_action != 'legacy':
            _fail('legacy-resource-action-required')
        current = self._load()
        if current[0]['status'] not in {'native-complete', 'completed'}:
            _fail('native-completion-evidence-required')
        self._verify(current, verify_native, native=True)
        self._verify(current, verify_resource)
        self._verify(current, verify_retirement)
        if current[0]['status'] != 'completed':
            self._append(current, status='completed')
            current = self._load()
        return self._clear(current)

    def abort_pre_native(self, *, verify_resource):
        current = self._load()
        if current[0]['status'] not in {'prepared', 'aborted'}:
            _fail('abort-after-native-forbidden')
        # Even an unbound preparation may have mutated resources before its
        # response was lost. No marker deletion based on transactionId=None.
        self._verify(current, verify_resource)
        if current[0]['status'] != 'aborted':
            self._append(current, status='aborted')
            current = self._load()
        return self._clear(current)
