"""Read the supported Xingtu QtIFW operation journal without loading Qt or an EXE.

This is provenance from native operation records, not an authenticity check or a
content hash. The retirement caller must pin/check the returned objects itself.
Only the repository's nested program + portable support layout is supported.

Format sources (QtIFW 4.10.0 and 4.11.0 have the same relevant layout):
https://github.com/qtproject/installer-framework/blob/4.11.0/src/libs/installer/binarycontent.cpp
https://github.com/qtproject/installer-framework/blob/4.11.0/src/libs/installer/fileio.cpp
https://github.com/qtproject/installer-framework/blob/4.11.0/src/libs/kdtools/updateoperation.cpp
https://github.com/qtproject/installer-framework/blob/4.11.0/src/libs/installer/extractarchiveoperation.cpp
https://github.com/qt/qtbase/blob/v6.7.2/src/corelib/serialization/qdatastream.h
https://github.com/qt/qtbase/blob/v6.7.2/src/corelib/text/qstring.cpp
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import os
from pathlib import Path
import stat
import struct
import xml.etree.ElementTree as ET


PROGRAM_COMPONENT = 'org.xingtu.qtifw.realtrial'
DATA_COMPONENT = 'org.xingtu.qtifw.trialdata'
COMPONENTS = frozenset((PROGRAM_COMPONENT, DATA_COMPONENT))
RELOCATABLE = '@RELOCATABLE_PATH@'
MAX_DAT = 128 * 1024 * 1024
MAX_OPERATIONS_BYTES = 32 * 1024 * 1024
MAX_LIST_BYTES = 32 * 1024 * 1024
MAX_RECORDS = 100_000
MAX_STRING = 1024 * 1024
DAT_COOKIE = 0xc2630a1c99d668f9
UNINSTALLER_MARKER = 0x12023234


class QtIfwInventoryError(ValueError):
    """Unsupported, incomplete or unsafe native ownership evidence."""


@dataclass(frozen=True)
class QtIfwShortcut:
    source: Path
    target: Path
    component: str


@dataclass(frozen=True)
class QtIfwOperation:
    name: str
    arguments: tuple[str, ...]
    component: str


@dataclass(frozen=True)
class QtIfwInventory:
    source_root: Path
    files: frozenset[Path]
    metadata_files: frozenset[Path]
    metadata_digests: dict[Path, tuple[int, str]]
    directories: frozenset[Path]
    shortcuts: tuple[QtIfwShortcut, ...]
    components: frozenset[str]
    operations: tuple[QtIfwOperation, ...]


def _fail(reason):
    raise QtIfwInventoryError('qtifw-inventory-' + reason)


def _key(path):
    return str(path).replace('\\', '/').casefold()


def _under(path, root):
    return _key(path) == _key(root) or _key(path).startswith(_key(root) + '/')


def _plain(path, *, directory=False):
    """Reject reparse points on the path; no directory traversal is performed."""
    for part in (path, *path.parents):
        try:
            info = part.lstat()
        except OSError:
            _fail('missing-object')
        if stat.S_ISLNK(info.st_mode) or getattr(info, 'st_file_attributes', 0) & 0x400:
            _fail('linked-object')
    info = path.lstat()
    if not (stat.S_ISDIR(info.st_mode) if directory else stat.S_ISREG(info.st_mode)):
        _fail('object-type')
    if not directory and info.st_nlink != 1:
        _fail('multiply-linked-file')


def _read(path, limit):
    _plain(path)
    with path.open('rb') as stream:
        before = os.fstat(stream.fileno())
        if before.st_size > limit:
            _fail('size-limit')
        data = stream.read(limit + 1)
        after = os.fstat(stream.fileno())
    if (len(data) != before.st_size or len(data) > limit or
            (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns) !=
            (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns)):
        _fail('file-changed')
    return data


class _Cursor:
    def __init__(self, data):
        self.data, self.pos = data, 0

    def take(self, size):
        if size < 0 or size > len(self.data) - self.pos:
            _fail('truncated-record')
        result = self.data[self.pos:self.pos + size]
        self.pos += size
        return result

    def number(self, fmt):
        return struct.unpack(fmt, self.take(struct.calcsize(fmt)))[0]

    def utf8(self):
        size = self.number('<q')
        if not 0 <= size <= MAX_STRING:
            _fail('string-size')
        try:
            return self.take(size).decode('utf-8', errors='strict')
        except UnicodeError:
            _fail('invalid-utf8')

    def finished(self):
        if self.pos != len(self.data):
            _fail('trailing-record-data')


def _operation_blobs(data):
    # QtIFW fileio writes native qint64, not QDataStream integers. Supported
    # Windows releases are little endian; .dat has no executable prefix.
    if len(data) < 80:
        _fail('short-dat')
    op_offset, op_size, meta_count, size, marker, cookie = struct.unpack('<5qQ', data[-48:])
    if cookie != DAT_COOKIE or marker != UNINSTALLER_MARKER or size != len(data):
        _fail('unsupported-footer')
    if not 1 <= meta_count <= 64:
        _fail('metadata-count')
    footer_start = len(data) - (64 + 16 * meta_count)
    if footer_start < 0 or not 16 <= op_size <= MAX_OPERATIONS_BYTES:
        _fail('operation-range')
    if op_offset < 0 or op_offset + op_size > footer_start:
        _fail('operation-range')
    collection_offset, collection_size = struct.unpack_from('<qq', data, footer_start)
    # Installed maintenance data persists no archive collections (three zero
    # counts; the index consists of two of them). Do not accept installer data.
    if (collection_offset != op_offset + op_size + 8 or collection_size != 16 or
            collection_offset + collection_size != footer_start or
            data[op_offset + op_size:footer_start] != bytes(24)):
        _fail('unsupported-collection-layout')
    end = 0
    for i in range(meta_count):
        start, length = struct.unpack_from('<qq', data, footer_start + 16 + 16 * i)
        if start != end or length <= 0 or start + length > op_offset:
            _fail('metadata-range')
        end = start + length
    if end != op_offset:
        _fail('metadata-range')
    cursor = _Cursor(data[op_offset:op_offset + op_size])
    count = cursor.number('<q')
    if not 1 <= count <= MAX_RECORDS:
        _fail('operation-count')
    result = [(cursor.utf8(), cursor.utf8()) for _ in range(count)]
    if cursor.number('<q') != count:
        _fail('operation-count-mismatch')
    cursor.finished()
    return result


def _xml(xml):
    if '<!DOCTYPE' in xml.upper() or '<!ENTITY' in xml.upper():
        _fail('xml-declaration')
    try:
        root = ET.fromstring(xml)
    except ET.ParseError:
        _fail('invalid-operation-xml')
    if root.tag != 'operation' or root.attrib:
        _fail('operation-xml-shape')
    if [child.tag for child in root] not in (['arguments'], ['arguments', 'values']):
        _fail('operation-xml-shape')
    args = []
    for arg in root[0]:
        if arg.tag != 'argument' or arg.attrib or len(arg):
            _fail('argument-shape')
        args.append(arg.text or '')
    values = {}
    if len(root) == 2:
        for value in root[1]:
            if (value.tag != 'value' or len(value) or
                    set(value.attrib) != {'name', 'type'} or value.attrib['name'] in values):
                _fail('value-shape')
            values[value.attrib['name']] = (value.attrib['type'], value.text or '')
    component = values.get('component', ('QString', ''))
    if component[0] != 'QString' or (component[1] and component[1] not in COMPONENTS):
        _fail('unknown-component')
    return tuple(args), values, component[1]


def _path(raw, root, *, external=False):
    if not raw or '\x00' in raw or any(ord(c) < 32 for c in raw):
        _fail('invalid-path')
    normalized = raw.replace('\\', '/')
    if normalized == RELOCATABLE or normalized.startswith(RELOCATABLE + '/'):
        normalized = root.as_posix() + normalized[len(RELOCATABLE):]
    if '@' in normalized or any(p in ('.', '..') for p in normalized.split('/')):
        _fail('unresolved-or-relative-path')
    # Reject ADS, device names, trailing aliases and UNC/device paths. A drive
    # colon is allowed only at position one on Windows.
    tail = normalized[2:] if len(normalized) > 1 and normalized[1] == ':' else normalized
    if ':' in tail or normalized.startswith('//'):
        _fail('unsupported-path')
    for piece in tail.split('/'):
        if piece and (piece[-1] in ' .' or piece.split('.')[0].upper() in
                      {'CON', 'PRN', 'AUX', 'NUL', *(f'COM{i}' for i in range(1, 10)),
                       *(f'LPT{i}' for i in range(1, 10))}):
            _fail('unsupported-path')
    path = Path(normalized)
    if not path.is_absolute() or (not external and not _under(path, root)):
        _fail('outside-source-root')
    return path


def _qstring_list(data):
    # Extract's .txt is *not text*: QDataStream << QStringList, default BE.
    cursor = _Cursor(data)
    count = cursor.number('>I')
    if not 0 <= count <= MAX_RECORDS:
        _fail('file-list-count')
    paths = []
    for _ in range(count):
        size = cursor.number('>I')
        # Qt 6.7 extended sizes cannot be produced within these byte bounds.
        if size in (0xffffffff, 0xfffffffe) or size > MAX_STRING or size % 2:
            _fail('file-list-string-size')
        try:
            paths.append(cursor.take(size).decode('utf-16-be', errors='strict'))
        except UnicodeError:
            _fail('invalid-utf16')
    cursor.finished()
    return paths


def _execute_path(raw, root, *, external=False):
    # ElevatedExecuteOperation sets requiresUnreplacedVariables(true): its
    # serialized arguments retain @TargetDir@, unlike Copy/Extract arguments.
    token = '@TargetDir@'
    normalized = raw.replace('\\', '/')
    if normalized == token or normalized.startswith(token + '/'):
        raw = RELOCATABLE + normalized[len(token):]
    return _path(raw, root, external=external)


def read_operations(dat_path: Path, source_root: Path) -> QtIfwInventory:
    """Return recorded files and shortcut pairs, or reject incomplete evidence.

    Outside shortcut locations and transient Copy/Execute sources are returned
    only in records, never read or included in ``files``. No record executes.
    Metadata includes only this .dat and the referenced Extract file lists;
    maintenance EXE/INI, components.xml and helper-created files need separate
    provenance in the caller. Files must currently exist as ordinary objects.
    """
    root, dat = Path(source_root).absolute(), Path(dat_path).absolute()
    _plain(root, directory=True)
    if dat.parent != root or dat.name != 'trial-maintenance.dat':
        _fail('unsupported-data-file')
    dat_bytes = _read(dat, MAX_DAT)
    metadata_bytes = {dat: dat_bytes}
    files, metadata, directories = set(), {dat}, set()
    shortcuts, records, components = [], [], set()
    support_copies, extracted, copies = set(), set(), {}
    for name, xml in _operation_blobs(dat_bytes):
        args, values, component = _xml(xml)
        if component:
            components.add(component)
        records.append(QtIfwOperation(name, args, component))
        if name == 'Extract':
            if len(args) != 2 or component != PROGRAM_COMPONENT:
                _fail('extract-shape')
            prefix = 'installer://' + component + '/'
            archive = args[0][len(prefix):] if args[0].startswith(prefix) else ''
            if not archive or '/' in archive or '\\' in archive or '.' not in archive:
                _fail('extract-archive')
            target = _path(args[1], root)
            if target != root:
                _fail('extract-target')
            item = values.get('files')
            if not item or item[0] != 'QString':
                _fail('unsupported-extract-file-list')
            index = _path(item[1], root)
            expected = root / 'installerResources' / component / (archive.rsplit('.', 1)[0] + '.txt')
            if index != expected:
                _fail('extract-index-path')
            if index in metadata_bytes:
                _fail('duplicate-extract-index')
            metadata_bytes[index] = _read(index, MAX_LIST_BYTES)
            metadata.add(index)
            for raw in _qstring_list(metadata_bytes[index]):
                path = _path(raw, root)
                if path != root and not _under(path, root / 'program'):
                    _fail('extract-file-scope')
                _plain(path, directory=path.is_dir())
                (directories if path.is_dir() else files).add(path)
                if not path.is_dir():
                    extracted.add(path)
        elif name == 'Copy':
            if len(args) != 2 or not component:
                _fail('copy-shape')
            source, target = _path(args[0], root, external=True), _path(args[1], root)
            if _under(target, root / '_installer-support') and target != root / '_installer-support':
                if component != DATA_COMPONENT or source.name != target.name or _under(source, root):
                    _fail('support-copy-shape')
                support_copies.add(target)
            elif _under(target, root / 'program') and target != root / 'program':
                if component != PROGRAM_COMPONENT or not (
                        _under(source, root / 'program') or _under(source, root / '_installer-support')):
                    _fail('program-copy-source')
            else:
                _fail('copy-target-scope')
            # Copy accepts a directory target dynamically. This implementation
            # only supports the builder's explicit full file-name arguments.
            _plain(target)
            if target in copies or target in extracted:
                _fail('duplicate-file-producer')
            copies[target] = source
            files.add(target)
        elif name == 'Mkdir':
            if len(args) != 1:
                _fail('mkdir-shape')
            path = _path(args[0], root)
            if not (path == root or _under(path, root / 'program') or _under(path, root / '_installer-support')):
                _fail('mkdir-scope')
            _plain(path, directory=True)
            directories.add(path)
        elif name == 'CreateShortcut':
            if len(args) != 2 or component != PROGRAM_COMPONENT:
                _fail('shortcut-shape')
            source, target = _path(args[0], root), _path(args[1], root, external=True)
            if (source not in (root / 'program/星图.exe', root / 'trial-maintenance.exe') or
                    target.suffix.lower() != '.lnk' or _under(target, root)):
                _fail('shortcut-scope')
            if any(_key(record.target) == _key(target) for record in shortcuts):
                _fail('duplicate-shortcut')
            shortcuts.append(QtIfwShortcut(source, target, component))
        elif name == 'Execute':
            # These are the only production commands emitted by the two
            # repository component scripts. Transient helper paths are never
            # read, launched, or used as a claim on that external file.
            if (not component or len(args) not in (4, 8) or args[0] != '{0}' or
                    _execute_path(args[1], root, external=True).name != 'qtifw-trial-helper.exe' or
                    _execute_path(args[3], root) != root):
                _fail('unsupported-execute')
            if len(args) == 4:
                if component != PROGRAM_COMPONENT or args[2] != 'verify-program':
                    _fail('unsupported-execute')
            elif (args[2] != 'noop' or args[4] != 'UNDOEXECUTE' or
                  _execute_path(args[5], root) != root / '_installer-support/qtifw-trial-helper.exe' or
                  args[6] != ('undo-data' if component == DATA_COMPONENT else 'disable-watcher') or
                  _execute_path(args[7], root) != root):
                _fail('unsupported-execute')
        elif name == 'MinimumProgress':
            if args:
                _fail('minimum-progress-shape')
        else:
            _fail('unsupported-operation:' + name)
    if components != COMPONENTS or root / '_installer-support/qtifw-trial-helper.exe' not in support_copies:
        _fail('incomplete-current-layout')
    if extracted.intersection(copies):
        _fail('duplicate-file-producer')
    # Every alias chain must end at an extracted file or explicit bootstrap
    # support copy. A cycle of Copy records cannot manufacture provenance.
    proven = extracted | support_copies
    for target in copies:
        path, chain = target, set()
        while path not in proven:
            if path in chain:
                _fail('cyclic-copy-source')
            chain.add(path)
            if path not in copies:
                _fail('unowned-copy-source')
            path = copies[path]
        proven.update(chain)
    files.update(metadata)
    digests = {path: (len(data), hashlib.sha256(data).hexdigest())
               for path, data in metadata_bytes.items()}
    return QtIfwInventory(root, frozenset(files), frozenset(metadata), digests, frozenset(directories),
                         tuple(shortcuts), frozenset(components), tuple(records))
