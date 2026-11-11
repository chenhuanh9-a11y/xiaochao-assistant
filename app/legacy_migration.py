"""Explicit offline migration. No discovery, source writes, or resource adoption.

The caller must keep both applications closed. Publication is a no-replace
rename of the complete staged work tree; the journal lives outside that tree.
"""
from pathlib import Path
import hashlib
import json
import os
import re
import stat
import uuid
import time
from contextvars import ContextVar

from packaged_product_identity import PRODUCTION_APP_ID, PRODUCTION_PRODUCT
from resource_transaction import _safe, rename_no_replace


class MigrationError(ValueError):
    pass


operation_deadline = ContextVar('legacy_operation_deadline', default=None)


def _checkpoint():
    limit = operation_deadline.get()
    if limit is not None and time.monotonic() >= limit:
        raise MigrationError('legacy-operation-deadline-exceeded')


class _SourceImportLock:
    """Read-only lease of an existing root lock; never create source state."""
    def __init__(self, source):
        self.path = source / 'work/map-packages/import.lock'
        self.stream = None
        self.info = None

    def __enter__(self):
        try:
            _safe(self.path)
            try:
                before = self.path.stat()
            except FileNotFoundError:
                return self
            if not stat.S_ISREG(before.st_mode) or before.st_nlink != 1:
                raise MigrationError('resource-import-busy-or-recovery-required')
            # rb cannot create, truncate, or write the old installation. The
            # Windows CRT also denies pathname deletion while this is open.
            self.stream = self.path.open('rb')
            self.info = os.fstat(self.stream.fileno())
            if not os.path.samestat(before, self.info):
                raise MigrationError('resource-import-busy-or-recovery-required')
            if os.name == 'nt':
                import msvcrt
                msvcrt.locking(self.stream.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(self.stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
            self.verify()
            return self
        except (OSError, ValueError):
            if self.stream is not None:
                self.stream.close()
                self.stream = None
            raise MigrationError('resource-import-busy-or-recovery-required') from None

    def verify(self):
        try:
            _safe(self.path)
            if self.info is None:
                if self.path.exists():
                    raise ValueError()
                return
            current = self.path.stat()
            held = os.fstat(self.stream.fileno())
            if (not stat.S_ISREG(held.st_mode) or held.st_nlink != 1
                    or not os.path.samestat(self.info, held)
                    or not os.path.samestat(held, current)
                    or (held.st_size, held.st_mtime_ns) != (self.info.st_size, self.info.st_mtime_ns)):
                raise ValueError()
        except (OSError, ValueError, AttributeError):
            raise MigrationError('resource-import-busy-or-recovery-required') from None

    def allows(self, path):
        if path != self.path or self.stream is None:
            return False
        self.verify()
        return True

    def __exit__(self, *_):
        if self.stream is not None:
            self.stream.close()
            self.stream = None


class _SourceImportLocks:
    def __init__(self, leases):
        self.leases = tuple(leases)

    def verify(self):
        for lease in self.leases:
            lease.verify()

    def allows(self, path):
        for lease in self.leases:
            if lease.path == path and lease.stream is not None:
                lease.verify()
                return True
        return False


def _owns_import_lock(lease, path):
    # Only a currently-held OS lease for this exact path grants an exception.
    return type(lease) in (_SourceImportLock, _SourceImportLocks) and lease.allows(path)


def _check_source_transactions(source, import_lock=None):
    if type(import_lock) in (_SourceImportLock, _SourceImportLocks):
        import_lock.verify()
    for relative in ('work/pending-installer-upgrade.json', 'work/qt-import-pending.json',
                     'work/qt-recovery-pending.json', 'work/map-packages/pending-upgrade.json',
                     'work/map-packages/pending-removal.json', 'work/map-packages/pending-additions.json',
                     'work/map-packages/pending-author-snapshot.json',
                     'work/map-packages/pending-base-repair.json', 'work/map-packages/import.lock'):
        path = _safe(source / relative)
        if path.exists() and not _owns_import_lock(import_lock, path):
            raise MigrationError('resource-transaction-recovery-required')


def _file(path):
    _checkpoint()
    path = _safe(path)
    info = path.stat()
    if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
        raise MigrationError('unsafe-file')
    digest = hashlib.sha256()
    with path.open('rb') as stream:
        held = os.fstat(stream.fileno())
        if not os.path.samestat(info, held):
            raise MigrationError('file-changed-during-read')
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            _checkpoint()
            digest.update(block)
        after = os.fstat(stream.fileno())
        current = _safe(path).stat()
        if (not os.path.samestat(held, current) or after.st_nlink != 1
                or (held.st_size, held.st_mtime_ns) != (after.st_size, after.st_mtime_ns)
                or (after.st_size, after.st_mtime_ns) != (current.st_size, current.st_mtime_ns)):
            raise MigrationError('file-changed-during-read')
    return dict(bytes=info.st_size, sha256=digest.hexdigest())


def _save(path, value):
    _checkpoint()
    temporary = _safe(path.with_name('.legacy-writing-' + uuid.uuid4().hex))
    # Private torn writes are retained, but never published or read as state.
    with temporary.open('x', encoding='utf-8') as stream:
        json.dump(value, stream, ensure_ascii=True, sort_keys=True)
        stream.flush()
        os.fsync(stream.fileno())
    temporary.replace(path)


def _roots(source, program):
    try:
        original_source, original_program = Path(os.path.abspath(source)), Path(os.path.abspath(program))
        source, program = original_source.resolve(), original_program.resolve()
        if source == program or source.is_relative_to(program) or program.is_relative_to(source):
            raise MigrationError('overlapping-roots')
        # Resolution must not erase a junction/symlink before safety checks.
        _safe(original_source)
        _safe(original_program)
        return _safe(source), _safe(program)
    except (OSError, RuntimeError):
        raise MigrationError('invalid-roots') from None


def _private_files(files):
    return {hashlib.sha256(name.encode('utf-8')).hexdigest(): info
            for name, info in files.items()}


def _load_journal(journal):
    try:
        state = json.loads(journal.read_text(encoding='utf-8'))
        if (not isinstance(state, dict) or set(state) - {'schema', 'status', 'files', 'source', 'identity', 'partials', 'error'}
                or type(state.get('schema')) is not int or state['schema'] != 1
                or state.get('status') not in {'pending', 'publishing', 'complete'}
                or not isinstance(state.get('files'), dict)):
            raise ValueError()
        digest = lambda value: isinstance(value, str) and re.fullmatch('[0-9a-f]{64}', value)
        if not digest(state.get('source')):
            raise ValueError()
        if 'identity' in state and not digest(state['identity']):
            raise ValueError()
        if 'error' in state and (not isinstance(state['error'], str)
                                or not re.fullmatch('[a-z][a-z0-9-]*', state['error'])):
            raise ValueError()
        for name, info in state['files'].items():
            if (not digest(name) or not isinstance(info, dict) or set(info) != {'bytes', 'sha256'}
                    or type(info['bytes']) is not int or info['bytes'] < 0 or not digest(info['sha256'])):
                raise ValueError()
        partials = state.get('partials', [])
        if (not isinstance(partials, list) or any(not isinstance(row, dict)
                or set(row) != {'name', 'bytes', 'sha256'}
                or not re.fullmatch(r'\.legacy-partial-[0-9a-f]{32}', row['name'])
                or type(row['bytes']) is not int or row['bytes'] < 0 or not digest(row['sha256'])
                for row in partials)):
            raise ValueError()
        return state
    except (OSError, ValueError, TypeError, UnicodeError):
        raise MigrationError('invalid-journal') from None


def _scan_error(error):
    raise MigrationError('source-scan-error')


def _inventory(source, *, import_lock=None, extra_roots=None, prepared=False):
    # Exclude diagnostics by directory, not by image extension: confirmation
    # images and samples remain paired with their original JSON records.
    result = {}
    for name in ('nightmare-memory', 'difficult-memory', 'seasons', 'user-routes', 'maps', 'map-packages'):
        _checkpoint()
        root = _safe(source / 'work' / name)
        if not root.exists():
            continue
        for directory, dirs, files in os.walk(root, followlinks=False, onerror=_scan_error):
            _checkpoint()
            for child in dirs:
                _safe(Path(directory) / child)
            dirs[:] = [d for d in dirs if d not in {'failures', 'runtime-successes', 'downloads', '__pycache__'}]
            for filename in files:
                path = Path(directory) / filename
                if name == 'map-packages' and filename in {
                        'pending-upgrade.json', 'pending-removal.json', 'pending-additions.json',
                        'pending-author-snapshot.json', 'pending-resource-reset.json',
                        'pending-base-repair.json', 'qt-import-pending.json', 'qt-recovery-pending.json'}:
                    raise MigrationError('resource-transaction-recovery-required')
                if name == 'map-packages' and filename == 'import.lock':
                    if not _owns_import_lock(import_lock, path):
                        raise MigrationError('resource-import-busy-or-recovery-required')
                    continue
                if (filename.endswith(('.lock', '.tmp', '.new', '.log'))
                        or (filename.endswith('.zip') and name != 'map-packages')
                        or filename.startswith('events-')):
                    continue
                relative = path.relative_to(source / 'work').as_posix()
                if name == 'map-packages' and not prepared:
                    # Keep legacy receipts and snapshots out of the active
                    # installed-package namespace until current admission runs.
                    relative = 'map-packages/legacy-unverified/metadata/' + path.relative_to(root).as_posix()
                result[relative] = _file(path)
    package_root = _safe(source / 'work/map-packages')
    if package_root.exists():
        try:
            from managed_base_resources import _package_constraints
            _package_constraints(source)
            import star_map_import as importer
            ignored = {Path(name).name for name in (
                'managed-base-state.json', 'pending-base-repair.json', 'pending-upgrade.json',
                'pending-additions.json', 'pending-removal.json', 'pending-author-snapshot.json',
                'qt-import-pending.json', 'qt-recovery-pending.json', 'last-import-result.json',
                'last-recovery-result.json', 'qt-import-notice-seen.json', 'main-import-request.json')}
            for receipt in package_root.glob('*.json'):
                if receipt.name in ignored or re.fullmatch(r'recovery-request-[A-Za-z0-9-]{1,64}\.json', receipt.name) or re.fullmatch(r'helper-result-[a-z0-9_]{8}\.json', receipt.name):
                    continue
                raw = importer._json(receipt.read_bytes())
                package = receipt.stem
                if raw.get('packageId') != package:
                    raise MigrationError('resource-receipts-unverified')
                owned = importer._rows(raw.get('files'))
                dependencies = importer._rows(raw.get('requiresFiles'), dependencies=True) if 'requiresFiles' in raw else {}
                for row in (*owned.values(), *dependencies.values()):
                    origin = _safe(source / row['path'])
                    if _file(origin) != {'bytes': row['bytes'], 'sha256': row['sha256']}:
                        raise MigrationError('resource-dependency-bytes-unverified')
                    target = 'map-packages/legacy-unverified/payload/' + package + '/' + row['path']
                    previous = result.get(target)
                    info = {'bytes': row['bytes'], 'sha256': row['sha256']}
                    if previous is not None and previous != info:
                        raise MigrationError('resource-package-path-conflict')
                    result[target] = info
        except MigrationError:
            raise
        except (ValueError, OSError, KeyError, TypeError):
            raise MigrationError('resource-receipts-unverified') from None
    # Explicitly identified roots outside ``work`` are stored in a namespaced
    # quarantine inside the destination. They are never activated as current
    # map receipts or editor libraries by migration alone.
    for label, root in sorted((extra_roots or {}).items()):
        if not re.fullmatch(r'[a-z][a-z0-9-]{0,31}', label):
            raise MigrationError('invalid-extra-root-label')
        root = _safe(Path(root))
        if not root.exists():
            continue
        if not root.is_dir():
            raise MigrationError('extra-root-not-directory')
        for directory, dirs, files in os.walk(root, followlinks=False, onerror=_scan_error):
            _checkpoint()
            for child in dirs:
                _safe(Path(directory) / child)
            for filename in files:
                path = _safe(Path(directory) / filename)
                rel = path.relative_to(root).as_posix()
                if filename == 'import.lock' and path == root / 'work/map-packages/import.lock':
                    if not _owns_import_lock(import_lock, path):
                        raise MigrationError('resource-import-busy-or-recovery-required')
                    continue
                if (filename.startswith('pending-') or filename in {
                        'qt-import-pending.json', 'qt-recovery-pending.json',
                        'pending-resource-reset.json', 'pending-base-repair.json'}
                        or filename.endswith(('.lock', '.tmp', '.new'))):
                    raise MigrationError('resource-transaction-recovery-required')
                target = 'map-packages/legacy-unverified/asset-roots/' + label + '/' + rel
                info = _file(path)
                if target in result and result[target] != info:
                    raise MigrationError('asset-root-path-conflict')
                result[target] = info
        if (root / 'work/map-packages').is_dir():
            try:
                from managed_base_resources import _package_constraints
                _package_constraints(root)
            except (ValueError, OSError, KeyError, TypeError):
                raise MigrationError('resource-receipts-unverified') from None
    return result


def _source_file(source, relative):
    prefix = 'map-packages/legacy-unverified/asset-roots/'
    if relative.startswith(prefix):
        tail = relative[len(prefix):].split('/', 1)
        # Asset source lookup is resolved by _migrate's validated root map.
        raise MigrationError('asset-root-source-map-required')
    prefix = 'map-packages/legacy-unverified/metadata/'
    if relative.startswith(prefix):
        return _safe(source / 'work/map-packages' / relative[len(prefix):])
    prefix = 'map-packages/legacy-unverified/payload/'
    if relative.startswith(prefix):
        tail = relative[len(prefix):].split('/', 1)
        if len(tail) != 2:
            raise MigrationError('invalid-resource-payload-path')
        return _safe(source / tail[1])
    return _safe(source / 'work' / relative)


def _migrate(source, program, identity, *, import_lock=None, extra_roots=None):
    """Copy owned user and resource data to a fresh program/work, resumably.
    Errors expose stable codes only; absolute paths and OS exception text stay
    out of installer output and the persistent journal.
    """
    source, program = _roots(source, program)
    extra_roots = {key: _safe(Path(value)) for key, value in (extra_roots or {}).items()}
    journal = _safe(program / 'legacy-migration.json')
    stage = _safe(program / '.legacy-migration-work')
    destination = _safe(program / 'work')
    program.mkdir(parents=True, exist_ok=True)
    state = dict(schema=1, status='pending', files={}, partials=[])
    if journal.exists():
        state = _load_journal(journal)
    try:
        profile = json.loads(_safe(source / '_internal/installation-profile.json').read_text(encoding='utf-8'))
        instance = profile['installerInstance']
        if (profile['product'] != PRODUCTION_PRODUCT or instance['product'] != PRODUCTION_PRODUCT
                or instance['id'] != 'production' or instance['appId'] != PRODUCTION_APP_ID):
            raise MigrationError('production-identity-required')
        _validate_identity(identity, source)
        if extra_roots:
            identity_payload = dict(identity=identity,
                                    extraRoots={key: str(value) for key, value in sorted(extra_roots.items())})
        else:
            # Preserve resumability for journals produced by the original
            # work-only migration path.
            identity_payload = identity
        identity_binding = hashlib.sha256(json.dumps(identity_payload, sort_keys=True, separators=(',', ':')).encode()).hexdigest()
        if 'source' in state and state.get('identity') != identity_binding:
            raise MigrationError('migration-identity-changed-or-unverifiable')
        # Never migrate across an unresolved installer/import/resource transaction.
        _check_source_transactions(source, import_lock)
        # Existing receipt parsers validate package/dependency closure without
        # granting new import, replacement, display, or identity authority.
        if _safe(source / 'work/map-packages').exists():
            try:
                from managed_base_resources import _package_constraints
                _package_constraints(source)
            except (ValueError, OSError, KeyError, TypeError):
                raise MigrationError('resource-receipts-unverified') from None
        files = _inventory(source, import_lock=import_lock, extra_roots=extra_roots)
        binding = hashlib.sha256(str(source).encode('utf-8')).hexdigest()
        if state.get('source') not in (None, binding) or (state['files'] != _private_files(files) and 'source' in state):
            raise MigrationError('source-changed')
        state.update(source=binding, identity=identity_binding, files=_private_files(files))
        _save(journal, state)
        if destination.exists():
            if state['status'] not in {'publishing', 'complete'} or stage.exists():
                raise MigrationError('destination-conflict')
            if _inventory(program, prepared=True) != files:
                raise MigrationError('destination-changed')
        else:
            if state['status'] == 'complete':
                raise MigrationError('destination-missing')
            stage.mkdir(exist_ok=True)
            for name, expected in files.items():
                output = _safe(stage / name)
                if output.exists():
                    if _file(output) != expected:
                        raise MigrationError('staging-conflict')
                    continue
                output.parent.mkdir(parents=True, exist_ok=True)
                temporary = _safe(output.with_name(output.name + '.migration-copy'))
                # Preserve an interrupted private copy outside the publication tree.
                if temporary.exists():
                    partial = program / ('.legacy-partial-' + uuid.uuid4().hex)
                    info = _file(temporary)
                    rename_no_replace(temporary, partial)
                    state.setdefault('partials', []).append(dict(name=partial.name, **info))
                    _save(journal, state)
                asset_prefix = 'map-packages/legacy-unverified/asset-roots/'
                if name.startswith(asset_prefix):
                    parts = name[len(asset_prefix):].split('/', 1)
                    if len(parts) != 2 or parts[0] not in extra_roots:
                        raise MigrationError('asset-root-source-map-required')
                    origin = _safe(extra_roots[parts[0]] / parts[1])
                else:
                    origin = _source_file(source, name)
                with origin.open('rb') as reader, temporary.open('xb') as writer:
                    for block in iter(lambda: reader.read(1024 * 1024), b''):
                        _checkpoint()
                        writer.write(block)
                    writer.flush()
                    os.fsync(writer.fileno())
                if _file(temporary) != expected:
                    raise MigrationError('copy-mismatch')
                rename_no_replace(temporary, output)
            _check_source_transactions(source, import_lock)
            if _inventory(source, import_lock=import_lock, extra_roots=extra_roots) != files:
                raise MigrationError('source-changed')
            # Check every staged object, including unexpected files from a prior run.
            actual = {}
            for p in stage.rglob('*'):
                _safe(p)
                if not p.is_dir():
                    actual[p.relative_to(stage).as_posix()] = _file(p)
            if actual != files:
                raise MigrationError('staging-conflict')
            state['status'] = 'publishing'
            _save(journal, state)
            _check_source_transactions(source, import_lock)
            rename_no_replace(stage, destination)
        # Remove only torn temporary copies that this transaction recorded and
        # re-hashed. No source file or unknown sibling is eligible for cleanup.
        for partial in list(state.get('partials', [])):
            path = _safe(program / partial['name'])
            if not path.exists() or _file(path) != {k: partial[k] for k in ('bytes', 'sha256')}:
                raise MigrationError('partial-copy-changed')
            path.unlink()
            state['partials'].remove(partial)
            _save(journal, state)
        _check_source_transactions(source, import_lock)
        state['status'] = 'complete'
        state.pop('error', None)
        _save(journal, state)
        _check_source_transactions(source, import_lock)
        return dict(migrated=True, files=len(files),
                    inventorySha256=hashlib.sha256(json.dumps(_private_files(files), sort_keys=True,
                        separators=(',', ':')).encode('ascii')).hexdigest(),
                    scope='settings-routes-and-map-resources-unverified',
                    sourceKind=identity['sourceKind'], sourceUnchanged=True)
    except (OSError, ValueError, KeyError, TypeError) as error:
        code = str(error) if isinstance(error, MigrationError) else 'migration-io-or-format-error'
        state['error'] = code
        try:
            # Before a complete inventory there is no resumable state to persist.
            if 'source' in state:
                _save(journal, state)
        except (OSError, ValueError):
            pass
        raise MigrationError(code) from None


def _validate_identity(identity, source):
    """Require the native installer to prove exact product/root ownership.

    Hashes and canonical path come from the caller's read-only native probe;
    this helper independently binds the supplied values to the selected roots.
    """
    if (type(identity) is not dict or set(identity) != {
            'sourceKind', 'product', 'appId', 'installRoot', 'dataRoot',
            'uninstallRecordSha256', 'maintenanceToolSha256'}
            or any(type(identity.get(key)) is not str for key in (
                'product', 'appId', 'installRoot', 'dataRoot', 'uninstallRecordSha256', 'maintenanceToolSha256'))
            or identity.get('sourceKind') not in {'qtifw', 'inno'}
            or identity.get('product') != PRODUCTION_PRODUCT
            or identity.get('appId') != PRODUCTION_APP_ID
            or os.path.normcase(os.path.realpath(identity.get('installRoot', ''))) != os.path.normcase(str(source))
            or not re.fullmatch(r'[0-9a-f]{64}', str(identity.get('uninstallRecordSha256', '')))
            or not re.fullmatch(r'[0-9a-f]{64}', str(identity.get('maintenanceToolSha256', '')))):
        raise MigrationError('source-install-identity-unverified')
    expected_data = source / 'work'
    supplied = Path(identity['dataRoot'])
    if not supplied.is_absolute() or os.path.normcase(os.path.realpath(supplied)) != os.path.normcase(str(expected_data)):
        raise MigrationError('source-data-root-mismatch')


def migrate(source, program, *, identity, extra_roots=None):
    """Serialize installer invocations; a crash lock requires manual inspection."""
    source, program = _roots(source, program)
    from contextlib import ExitStack
    roots = dict(extra_roots or {})
    with ExitStack() as stack:
        leases = [stack.enter_context(_SourceImportLock(source))]
        for root in roots.values():
            leases.append(stack.enter_context(_SourceImportLock(Path(root))))
        import_lock = _SourceImportLocks(leases)
        # Do not create target evidence until every source lock is held.
        program.mkdir(parents=True, exist_ok=True)
        lock = _safe(program / '.legacy-migration.lock')
        try:
            handle = lock.open('x', encoding='ascii')
        except OSError:
            raise MigrationError('migration-busy-or-recovery-required') from None
        try:
            with handle:
                return _migrate(source, program, identity, import_lock=import_lock,
                                extra_roots=roots)
        finally:
            lock.unlink()
