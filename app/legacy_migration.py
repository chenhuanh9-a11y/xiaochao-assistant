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

from packaged_product_identity import PRODUCTION_APP_ID, PRODUCTION_PRODUCT
from resource_transaction import _safe, rename_no_replace


class MigrationError(ValueError):
    pass


def _file(path):
    path = _safe(path)
    info = path.stat()
    if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
        raise MigrationError('unsafe-file')
    digest = hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(block)
    return dict(bytes=info.st_size, sha256=digest.hexdigest())


def _save(path, value):
    temporary = _safe(path.with_suffix('.new'))
    # A torn journal write is preserved for inspection, never overwritten.
    with temporary.open('x', encoding='utf-8') as stream:
        json.dump(value, stream, ensure_ascii=True, sort_keys=True)
        stream.flush()
        os.fsync(stream.fileno())
    temporary.replace(path)


def _roots(source, program):
    try:
        source, program = Path(source).resolve(), Path(program).resolve()
        if source == program or source.is_relative_to(program) or program.is_relative_to(source):
            raise MigrationError('overlapping-roots')
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


def _inventory(source):
    # Exclude diagnostics by directory, not by image extension: confirmation
    # images and samples remain paired with their original JSON records.
    result = {}
    already_quarantined = _safe(source / 'work/map-packages/legacy-unverified').exists()
    for name in ('nightmare-memory', 'difficult-memory', 'seasons', 'user-routes', 'maps', 'map-packages'):
        root = _safe(source / 'work' / name)
        if not root.exists():
            continue
        for directory, dirs, files in os.walk(root, followlinks=False, onerror=_scan_error):
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
                    raise MigrationError('resource-import-busy-or-recovery-required')
                if filename.endswith(('.lock', '.tmp', '.new', '.log', '.zip')) or filename.startswith('events-'):
                    continue
                relative = path.relative_to(source / 'work').as_posix()
                if name == 'map-packages' and not already_quarantined:
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
    return result


def _source_file(source, relative):
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


def _migrate(source, program, identity):
    """Copy owned user and resource data to a fresh program/work, resumably.
    Errors expose stable codes only; absolute paths and OS exception text stay
    out of installer output and the persistent journal.
    """
    source, program = _roots(source, program)
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
        identity_binding = hashlib.sha256(json.dumps(identity, sort_keys=True, separators=(',', ':')).encode()).hexdigest()
        if 'source' in state and state.get('identity') != identity_binding:
            raise MigrationError('migration-identity-changed-or-unverifiable')
        # Never migrate across an unresolved installer/import/resource transaction.
        for relative in ('work/pending-installer-upgrade.json', 'work/qt-import-pending.json',
                         'work/qt-recovery-pending.json', 'work/map-packages/pending-upgrade.json',
                         'work/map-packages/pending-removal.json', 'work/map-packages/pending-additions.json',
                         'work/map-packages/pending-author-snapshot.json',
                         'work/map-packages/pending-base-repair.json', 'work/map-packages/import.lock'):
            if _safe(source / relative).exists():
                raise MigrationError('resource-transaction-recovery-required')
        # Existing receipt parsers validate package/dependency closure without
        # granting new import, replacement, display, or identity authority.
        if _safe(source / 'work/map-packages').exists():
            try:
                from managed_base_resources import _package_constraints
                _package_constraints(source)
            except (ValueError, OSError, KeyError, TypeError):
                raise MigrationError('resource-receipts-unverified') from None
        files = _inventory(source)
        binding = hashlib.sha256(str(source).encode('utf-8')).hexdigest()
        if state.get('source') not in (None, binding) or (state['files'] != _private_files(files) and 'source' in state):
            raise MigrationError('source-changed')
        state.update(source=binding, identity=identity_binding, files=_private_files(files))
        _save(journal, state)
        if destination.exists():
            if state['status'] not in {'publishing', 'complete'} or stage.exists():
                raise MigrationError('destination-conflict')
            if _inventory(program) != files:
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
                with _source_file(source, name).open('rb') as reader, temporary.open('xb') as writer:
                    for block in iter(lambda: reader.read(1024 * 1024), b''):
                        writer.write(block)
                    writer.flush()
                    os.fsync(writer.fileno())
                if _file(temporary) != expected:
                    raise MigrationError('copy-mismatch')
                rename_no_replace(temporary, output)
            if _inventory(source) != files:
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
        state['status'] = 'complete'
        state.pop('error', None)
        _save(journal, state)
        return dict(migrated=True, files=len(files), scope='settings-routes-and-map-resources-unverified',
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


def migrate(source, program, *, identity):
    """Serialize installer invocations; a crash lock requires manual inspection."""
    source, program = _roots(source, program)
    program.mkdir(parents=True, exist_ok=True)
    lock = _safe(program / '.legacy-migration.lock')
    try:
        handle = lock.open('x', encoding='ascii')
    except OSError:
        raise MigrationError('migration-busy-or-recovery-required') from None
    try:
        with handle:
            return _migrate(source, program, identity)
    finally:
        lock.unlink()
