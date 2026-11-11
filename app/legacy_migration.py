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
        if (not isinstance(state, dict) or set(state) - {'schema', 'status', 'files', 'source', 'error'}
                or type(state.get('schema')) is not int or state['schema'] != 1
                or state.get('status') not in {'pending', 'publishing', 'complete'}
                or not isinstance(state.get('files'), dict)):
            raise ValueError()
        digest = lambda value: isinstance(value, str) and re.fullmatch('[0-9a-f]{64}', value)
        if not digest(state.get('source')):
            raise ValueError()
        if 'error' in state and (not isinstance(state['error'], str)
                                or not re.fullmatch('[a-z][a-z0-9-]*', state['error'])):
            raise ValueError()
        for name, info in state['files'].items():
            if (not digest(name) or not isinstance(info, dict) or set(info) != {'bytes', 'sha256'}
                    or type(info['bytes']) is not int or info['bytes'] < 0 or not digest(info['sha256'])):
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
    for name in ('nightmare-memory', 'difficult-memory', 'seasons', 'user-routes'):
        root = _safe(source / 'work' / name)
        if not root.exists():
            continue
        for directory, dirs, files in os.walk(root, followlinks=False, onerror=_scan_error):
            for child in dirs:
                _safe(Path(directory) / child)
            dirs[:] = [d for d in dirs if d not in {'failures', 'runtime-successes', 'downloads', '__pycache__'}]
            for filename in files:
                path = Path(directory) / filename
                if filename.endswith(('.lock', '.tmp', '.new', '.log', '.zip')) or filename.startswith('events-'):
                    continue
                relative = path.relative_to(source / 'work').as_posix()
                result[relative] = _file(path)
    return result


def _migrate(source, program):
    """Copy core data to a fresh program/work, resumably and without clobbering.

    Missing/legacy unmarked identities and any map package control records are
    deliberate capability gaps until complete resource migration is supported.
    Errors expose stable codes only; absolute paths and OS exception text stay
    out of installer output and the persistent journal.
    """
    source, program = _roots(source, program)
    journal = _safe(program / 'legacy-migration.json')
    stage = _safe(program / '.legacy-migration-work')
    destination = _safe(program / 'work')
    program.mkdir(parents=True, exist_ok=True)
    state = dict(schema=1, status='pending', files={})
    if journal.exists():
        state = _load_journal(journal)
    try:
        profile = json.loads(_safe(source / '_internal/installation-profile.json').read_text(encoding='utf-8'))
        instance = profile['installerInstance']
        if (profile['product'] != PRODUCTION_PRODUCT or instance['product'] != PRODUCTION_PRODUCT
                or instance['id'] != 'production' or instance['appId'] != PRODUCTION_APP_ID):
            raise MigrationError('production-identity-required')
        # Do not acquire a source resource lock: that would modify old data.
        # Even an idle existing lock requires explicit inspection in this core release.
        for path in (source / 'work/map-packages', source / 'work/pending-installer-upgrade.json',
                     source / 'work/qt-import-pending.json', source / 'work/qt-recovery-pending.json'):
            if _safe(path).exists():
                raise MigrationError('resource-migration-or-recovery-required')
        files = _inventory(source)
        binding = hashlib.sha256(str(source).encode('utf-8')).hexdigest()
        if state.get('source') not in (None, binding) or (state['files'] != _private_files(files) and 'source' in state):
            raise MigrationError('source-changed')
        state.update(source=binding, files=_private_files(files))
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
                    rename_no_replace(temporary, program / ('.legacy-partial-' + uuid.uuid4().hex))
                with _safe(source / 'work' / name).open('rb') as reader, temporary.open('xb') as writer:
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
        state['status'] = 'complete'
        state.pop('error', None)
        _save(journal, state)
        return dict(migrated=True, files=len(files), scope='core-only')
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


def migrate(source, program):
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
            return _migrate(source, program)
    finally:
        lock.unlink()
