"""One fixed catalog publication, using existing owned metadata primitives.

Call the locked APIs only under resource_write_lock(ResourceRoot), after the
runtime's idle lease has drained affected workers. An immutable journal and
commit witness choose recovery direction; package files are never removed here.
Windows directory flush is not portable: this supports process-loss recovery,
without promising power-loss atomicity.
"""
import os
from pathlib import Path
import uuid

import map_package_upgrade as publication
import resource_transaction as locks
import star_map_import as files
from imported_resource_catalog import (PENDING_CATALOG, ResourceCatalogError,
                                       assert_catalog_ready, parse_catalog)
from resource_snapshot_io import read_stable_file


SCHEMA = 'xingtu-catalog-transaction-v1'
LIMIT = 2 * 1024 * 1024


def _reject(reason):
    raise ResourceCatalogError('resource-catalog-' + reason)


def _encoded(value):
    return publication._encoded(value)


def _read(path):
    return read_stable_file(path, LIMIT, no_links=files._no_links,
                            reject=_reject, identity=publication._identity)


def _stamp(path):
    return _read(path)[1]


def _write_bound(path, raw):
    identity = publication._write_new(path, raw)
    actual, stamp = _read(path)
    if actual != raw or stamp['identity'] != identity:
        _reject('staged-metadata-changed')
    return stamp


def _object(row, name):
    if not isinstance(row, dict) or set(row) != {'path', 'bytes', 'sha256', 'identity'} or row['path'] != name:
        _reject('invalid-object')
    files._rows([{key: row[key] for key in ('path', 'bytes', 'sha256')}])
    publication._valid_identity(row['identity'])
    return row


def _same(path, row):
    return row is not None and publication._matches(path, row, row['identity'])


def _load(root):
    pending = files._target(root, PENDING_CATALOG)
    pointer = files._json(_read(pending)[0])
    if set(pointer) != {'schema', 'transactionId', 'journal'} or pointer['schema'] != SCHEMA:
        _reject('invalid-pending')
    transaction_id = publication._transaction_id(pointer['transactionId'])
    directory, journal, committed = _load_retained(root, transaction_id, pointer['journal'])
    pin = directory / 'pending-pointer.json'
    if _read(pin)[0] != _encoded(pointer):
        _reject('pending-changed')
    publication._metadata_publication_identity(pin, pending, journal['publicationMode'])
    return directory, journal, committed


def _load_retained(root, transaction_id, journal_stamp):
    """Read the original journal/witness, including after pending is retired."""
    transaction_id = publication._transaction_id(transaction_id)
    directory = files._target(root, 'transactions/' + transaction_id)
    journal_stamp = _object(journal_stamp, 'journal.json')
    raw_journal, actual_stamp = _read(directory / 'journal.json')
    if actual_stamp != journal_stamp:
        _reject('journal-changed')
    journal = files._json(raw_journal)
    if (set(journal) != {'schema', 'transactionId', 'rootIdentity', 'publicationMode', 'old', 'new', 'retained'}
            or journal['schema'] != SCHEMA or journal['transactionId'] != transaction_id
            or journal['rootIdentity'] != publication._identity(root.stat())):
        _reject('invalid-journal')
    mode = publication._publication_mode(journal)
    if journal['old'] is not None:
        _object(journal['old'], 'catalog.json')
    new = _object(journal['new'], 'new-catalog.publishing')
    retained = _object(journal['retained'], 'new-catalog.json')
    if ((new['bytes'], new['sha256']) != (retained['bytes'], retained['sha256'])
            or not _same(directory / 'new-catalog.json', retained)):
        _reject('retained-catalog-changed')
    committed = files._target(directory, 'committed.json')
    is_committed = committed.exists()
    if not is_committed:
        # A lost commit marker and an interrupted marker publication are
        # indistinguishable. Preserve this narrow uncertain window; do not
        # downgrade a potentially committed catalog to rollback authority.
        for name in ('commit-pointer.json', 'commit-pointer.json.publishing',
                     'commit-pointer.json.publication.json',
                     'commit-pointer.json.publication.json.tmp'):
            if files._target(directory, name).exists():
                _reject('commit-outcome-uncertain')
    if is_committed:
        expected = _encoded(dict(schema=SCHEMA, transactionId=transaction_id,
                                 journal=journal_stamp, outcome='committed'))
        commit_pin = directory / 'commit-pointer.json'
        if _read(commit_pin)[0] != expected or _read(committed)[0] != expected:
            _reject('commit-outcome-changed')
        publication._metadata_publication_identity(commit_pin, committed, mode)
    return directory, journal, is_committed


def retained_catalog_outcome_locked(resource_root, transaction_id, *, expected_journal_stamp):
    """Inspect a bound retained transaction without adopting a new pending file.

    The caller holds the resource write lock. This does not recover anything or
    authorize rollback when a commit witness is missing/uncertain. A removal
    operation uses the exact journal stamp captured under the original pending
    marker, so normal catalog pending retirement does not lose its decision.
    """
    root = locks._safe(Path(resource_root).absolute())
    directory, journal, committed = _load_retained(root, transaction_id, expected_journal_stamp)
    _preflight(root, directory, journal, committed)
    return dict(transactionId=directory.name, committed=committed,
                old=journal['old'], new=journal['new'], publicationMode=journal['publicationMode'])


def _clear_pending(root, directory):
    current_directory, journal, _ = _load(root)
    if current_directory != directory:
        _reject('pending-changed')
    path = files._target(root, PENDING_CATALOG)
    identity = publication._metadata_publication_identity(directory / 'pending-pointer.json', path,
                                                          journal['publicationMode'])
    if not files._rollback_published(path, tuple(identity), _stamp(path)['sha256']):
        _reject('pending-changed')
    publication._directory_sync(path.parent)


def _preflight(root, directory, journal, committed):
    target = files._target(root, 'catalog.json')
    old, new, mode = journal['old'], journal['new'], journal['publicationMode']
    backup, source, unused = (directory / name for name in
                              ('old-catalog.json', 'new-catalog.publishing', 'uncommitted-catalog.json'))
    for path in (backup, source, unused):
        files._no_links(path)
    if backup.exists() and (old is None or not _same(backup, old)):
        _reject('backup-changed')
    if source.exists() and not _same(source, new):
        _reject('staged-catalog-changed')
    if unused.exists() and not _same(unused, new):
        _reject('rollback-catalog-changed')
    if mode == 'hardlink-v1' and not _same(source, new):
        _reject('staged-catalog-changed')
    already_old = _same(target, old)
    if committed:
        if not _same(target, new) or old is not None and not _same(backup, old):
            _reject('committed-target-changed')
    else:
        if target.exists() and not (already_old or _same(target, new)):
            _reject('rollback-target-changed')
        if old is not None and not already_old and not _same(backup, old):
            _reject('rollback-backup-missing')
        if mode == 'move-v1' and not any(_same(path, new) for path in (source, target, unused)):
            _reject('staged-catalog-changed')
    return target, backup, unused, already_old


def recover_catalog_locked(resource_root):
    root = locks._safe(Path(resource_root).absolute())
    if not files._target(root, PENDING_CATALOG).exists():
        return dict(status='catalog-ready')
    directory, journal, committed = _load(root)
    target, backup, unused, already_old = _preflight(root, directory, journal, committed)
    if not committed:
        mode = journal['publicationMode']
        if target.exists() and not already_old:
            publication._move_owned(target, unused, journal['new'], journal['new']['identity'],
                                     linkless=mode == 'move-v1')
        if journal['old'] is not None and not already_old:
            publication._move_owned(backup, target, journal['old'], journal['old']['identity'],
                                     linkless=mode == 'move-v1')
        publication._directory_sync(root)
        _preflight(root, directory, journal, False)
        if (journal['old'] is not None and not _same(target, journal['old'])
                or journal['old'] is None and target.exists()):
            _reject('rollback-target-changed')
    _clear_pending(root, directory)
    return dict(status='catalog-committed' if committed else 'catalog-rolled-back',
                transactionId=journal['transactionId'])


def activate_catalog_locked(resource_root, value, *, expected_catalog_stamp, validate,
                            cancelled=None, on_stage=None):
    """Publish a fully admitted selection; ``validate`` is the program decoder.

    Validation receives a fresh copy of the exact document to be published,
    before preparation and again before the commit witness.
    The supplied expected stamp comes from CatalogSnapshot.catalog_stamp.
    A cancellation after that durable commit never authorizes rollback.
    """
    if not callable(validate):
        raise TypeError('catalog publication requires a semantic validator')
    root = locks._safe(Path(resource_root).absolute())
    assert_catalog_ready(root)
    generation, _, _ = parse_catalog(value)
    raw = _encoded(value)
    if len(raw) > LIMIT:
        _reject('too-large')
    target = files._target(root, 'catalog.json')
    old = _stamp(target) if target.exists() else None
    if old != expected_catalog_stamp:
        _reject('changed-reinspect-required')
    if old is not None:
        previous = files._json(_read(target)[0])
        parse_catalog(previous)
        if previous['generation'] == generation:
            _reject('generation-reused')
    files._check_cancelled(cancelled)
    validate(files._json(raw))
    mode = locks.preflight_resource_publication(root, ['catalog.json', PENDING_CATALOG])
    directory = files._target(root, 'transactions/' + str(uuid.uuid4()))
    directory.mkdir(parents=True, exist_ok=False)
    retained = _write_bound(directory / 'new-catalog.json', raw)
    new = _write_bound(directory / 'new-catalog.publishing', raw)
    journal = dict(schema=SCHEMA, transactionId=directory.name,
                   rootIdentity=publication._identity(root.stat()), publicationMode=mode,
                   old=old, new=new, retained=retained)
    journal_stamp = _write_bound(directory / 'journal.json', _encoded(journal))
    pointer = dict(schema=SCHEMA, transactionId=directory.name, journal=journal_stamp)
    _write_bound(directory / 'pending-pointer.json', _encoded(pointer))

    def stage(name):
        if on_stage is not None:
            on_stage(name)

    try:
        files._check_cancelled(cancelled)
        if (_stamp(target) if target.exists() else None) != old:
            _reject('changed-reinspect-required')
        publication._publish_metadata(directory / 'pending-pointer.json', files._target(root, PENDING_CATALOG),
                                       mode, expected_data=_encoded(pointer))
        stage('pending')
        if old is not None:
            publication._move_owned(target, directory / 'old-catalog.json', old, old['identity'],
                                     linkless=mode == 'move-v1')
        stage('previous-retained')
        files._check_cancelled(cancelled)
        if mode == 'move-v1':
            publication._move_owned(directory / 'new-catalog.publishing', target,
                                     journal['new'], journal['new']['identity'], linkless=True)
        else:
            files._no_links(target)
            files._no_links(directory / 'new-catalog.publishing')
            if not _same(directory / 'new-catalog.publishing', journal['new']):
                _reject('staged-catalog-changed')
            os.link(directory / 'new-catalog.publishing', target)
        publication._directory_sync(root)
        stage('catalog-published')
        files._check_cancelled(cancelled)
        validate(files._json(raw))
        loaded_directory, durable, committed = _load(root)
        if loaded_directory != directory or committed:
            _reject('pending-changed')
        _preflight(root, directory, durable, True)
        files._check_cancelled(cancelled)
        commit = _encoded(dict(schema=SCHEMA, transactionId=directory.name,
                               journal=pointer['journal'], outcome='committed'))
        _write_bound(directory / 'commit-pointer.json', commit)
        stage('commit-prepared')
        publication._publish_metadata(directory / 'commit-pointer.json', directory / 'committed.json',
                                       mode, expected_data=commit)
        stage('committed')
        # Reload the durable proof before clearing pending, even after success.
        loaded_directory, durable, committed = _load(root)
        if loaded_directory != directory or not committed:
            _reject('commit-outcome-uncertain')
        _preflight(root, directory, durable, True)
        _clear_pending(root, directory)
    except Exception:
        if files._target(root, PENDING_CATALOG).exists():
            loaded_directory, _, committed = _load(root)
            if loaded_directory != directory:
                _reject('pending-changed')
            if committed:
                _reject('commit-outcome-uncertain')
            recover_catalog_locked(root)
        raise
    return dict(status='catalog-activated', generation=generation, transactionId=directory.name)
