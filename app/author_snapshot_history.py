"""Verified author history and explicit rollback, never base/identity migration.

Only an installation-bound committed activation witness can nominate a retained
version. Listing does not grant content trust. Restore rechecks the complete
payload and current base, then uses the existing guarded activation transaction.
No files or historical versions are deleted by this module.
"""
from __future__ import annotations

import hashlib
import os
from pathlib import Path
import stat
import uuid

import author_resource_snapshots as snapshots
import generic_author_package as generic
import star_map_import as files
from resource_transaction import resource_read_lock, assert_resources_ready, preflight_hardlinks

MAX_HISTORY = 4096


def _reject(reason):
    raise files.ImportRejected('author-history-' + reason)


def _current(root, package):
    snapshots.activation_path(package)
    activation, digest = snapshots._read_activation(root, package)
    if activation is None:
        _reject('not-managed')
    if files._target(root, f'work/map-packages/{package}.json').exists():
        _reject('dual-active-records')
    stamp = snapshots._stamp(files._target(root, snapshots.activation_path(package)))
    if stamp['sha256'] != digest:
        _reject('current-changed')
    return activation, stamp


def _proof(root, package, transaction_id):
    transaction_id = snapshots._transaction_id(transaction_id)
    directory = files._target(root, snapshots.STORE + '/transactions/' + transaction_id)
    names = ('journal.json', 'pending-pointer.json', 'new-activation.json', 'committed.json', 'commit-pointer.json')
    observed = {name: snapshots._metadata(files._target(directory, name)) for name in names}
    journal = files._json(observed['journal.json'][0])
    journal = snapshots._validated_journal(root, directory, transaction_id, journal)
    if journal['packageId'] != package or journal['status'] != 'committed':
        _reject('not-committed-for-package')
    expected_pointer = snapshots._encoded(dict(schema=snapshots.JOURNAL_SCHEMA,
        transactionId=transaction_id, planSha256=snapshots._journal_digest(journal)))
    if observed['pending-pointer.json'][0] != expected_pointer:
        _reject('history-pointer-changed')
    entry = next(row for row in journal['changes'] if row['path'] == snapshots.activation_path(package))
    raw, pin_stamp = observed['new-activation.json']
    if any(pin_stamp[key] != entry['new'][key] for key in ('bytes', 'sha256', 'identity')):
        _reject('activation-witness-changed')
    activation = snapshots._validate_activation(files._json(raw), package)
    if activation['snapshotId'] is None:
        _reject('history-deactivation')
    proof = {(directory / name).relative_to(root).as_posix(): stamp for name, (_, stamp) in observed.items()}
    _verify_proof(root, proof)
    return activation, proof


def _verify_proof(root, proof):
    for relative, expected in proof.items():
        if snapshots._stamp(files._target(root, relative)) != expected:
            _reject('history-changed')


def _payload_read(path, expected, cancelled):
    """Bounded regular-file read, with handle observations in the same time domain."""
    files._check_cancelled(cancelled)
    files._no_links(path)
    flags = os.O_RDONLY | getattr(os, 'O_NONBLOCK', 0) | getattr(os, 'O_NOFOLLOW', 0) | getattr(os, 'O_BINARY', 0)
    descriptor = os.open(path, flags)
    with os.fdopen(descriptor, 'rb') as stream:
        before = os.fstat(stream.fileno())
        if not stat.S_ISREG(before.st_mode) or before.st_size != expected['bytes']:
            _reject('payload-changed')
        blocks, count = [], 0
        while block := stream.read(min(1024 * 1024, expected['bytes'] - count + 1)):
            files._check_cancelled(cancelled)
            count += len(block)
            if count > expected['bytes']:
                _reject('payload-changed')
            blocks.append(block)
        after = os.fstat(stream.fileno())
    files._no_links(path)
    descriptor = os.open(path, flags)
    try:
        current = os.fstat(descriptor)
    finally:
        os.close(descriptor)
    files._no_links(path)
    identity = lambda value: (value.st_dev, value.st_ino, value.st_size, value.st_mtime_ns, value.st_ctime_ns)
    data = b''.join(blocks)
    if (not stat.S_ISREG(current.st_mode) or identity(before) != identity(after) or identity(after) != identity(current)
            or len(data) != expected['bytes'] or hashlib.sha256(data).hexdigest() != expected['sha256']):
        _reject('payload-changed')
    return data, dict(bytes=expected['bytes'], sha256=expected['sha256'],
                      identity=snapshots.transaction._identity(after), observation=list(identity(after)))


class _HistoryView(snapshots.SnapshotPackage):
    """Validation-only view; it is never registered as a runtime provider."""
    def __init__(self, root, package, activation, proof, cancelled):
        self.root, self.package, self.activation = root, package, activation
        self.proof, self.cancelled, self.evidence = proof, cancelled, {}
        self.directory, self.manifest, self.owned, self.dependencies = snapshots._manifest(root, package, activation)
        manifest_path = files._target(self.directory, 'manifest.json')
        self.proof = dict(proof, **{manifest_path.relative_to(root).as_posix(): snapshots._stamp(manifest_path)})
        if self.proof[manifest_path.relative_to(root).as_posix()]['sha256'] != activation['manifestSha256']:
            _reject('manifest-changed')
        self.paths = {row['path'].casefold(): snapshots._stored_name(index, row)
                     for index, row in enumerate(sorted(self.owned.values(), key=lambda row: row['path']))}

    def assert_active(self):
        files._check_cancelled(self.cancelled)
        _verify_proof(self.root, self.proof)

    def verify_payload(self):
        # Revisit every byte observed earlier in this plan, then compare all
        # handle timestamps/identities once more. Metadata proof alone cannot
        # detect an image changed while a later dependency was being checked.
        for relative, expected in tuple(self.evidence.items()):
            _, observed = _payload_read(files._target(self.root, relative), expected, self.cancelled)
            if observed != expected:
                _reject('payload-changed')
        flags = os.O_RDONLY | getattr(os, 'O_NONBLOCK', 0) | getattr(os, 'O_NOFOLLOW', 0) | getattr(os, 'O_BINARY', 0)
        for relative, expected in self.evidence.items():
            files._check_cancelled(self.cancelled)
            path = files._target(self.root, relative)
            descriptor = os.open(path, flags)
            try:
                info = os.fstat(descriptor)
            finally:
                os.close(descriptor)
            if (not stat.S_ISREG(info.st_mode) or [info.st_dev, info.st_ino, info.st_size,
                    info.st_mtime_ns, info.st_ctime_ns] != expected['observation']):
                _reject('payload-changed')
        self.assert_active()

    def read(self, relative):
        expected = self.owned.get(relative.casefold()) or self.dependencies.get(relative.casefold())
        if expected is None:
            _reject('undeclared-payload')
        path = self.path(relative)
        data, evidence = _payload_read(path, expected, self.cancelled)
        key = path.relative_to(self.root).as_posix()
        if key in self.evidence and evidence != self.evidence[key]:
            _reject('payload-changed')
        self.evidence[key] = evidence
        self.assert_active()
        return data


def _candidate(root, package, transaction_id, cancelled=None):
    activation, proof = _proof(root, package, transaction_id)
    view = _HistoryView(root, package, activation, proof, cancelled)
    # Every retained author byte must remain original; runtime's per-layer
    # damage isolation must not turn a damaged history into a valid rollback.
    for row in view.owned.values():
        view.read(row['path'])
    from generic_author_base import browse_validation_request
    with browse_validation_request(cancelled or (lambda: False)):
        document = generic.validate_bindings(view.read(generic.bindings_path(package)), view.owned,
            view.dependencies, view.read, root=root, isolate_incompatible_layers=True)
    view.verify_payload()
    evidence = dict(snapshotId=activation['snapshotId'], manifestSha256=activation['manifestSha256'],
        sourceTransactionId=transaction_id, sourceProof=view.proof, payloadEvidence=view.evidence,
        layerStates=document['layerStates'])
    return view, document, evidence


def _list_locked(root, package, cancelled=None):
    assert_resources_ready(root)
    current, _ = _current(root, package)
    parent = files._target(root, snapshots.STORE + '/transactions')
    if not parent.exists():
        return []
    files._no_links(parent)
    children = list(parent.iterdir())
    if len(children) > MAX_HISTORY:
        _reject('history-limit')
    rows = {}
    for directory in sorted(children):
        files._check_cancelled(cancelled)
        try:
            snapshots._transaction_id(directory.name)
            activation, proof = _proof(root, package, directory.name)
            version, manifest, owned, _ = snapshots._manifest(root, package, activation)
            if activation['season'] != current['season']:
                _reject('season-mismatch')
            row = dict(packageId=package, snapshotId=activation['snapshotId'], sourceTransactionId=directory.name,
                archiveSha256=manifest['receipt']['archiveSha256'], contentId=manifest['contentId'],
                season=activation['season'], fileCount=len(owned), bytes=sum(item['bytes'] for item in owned.values()),
                current=activation['snapshotId'] == current['snapshotId'], status='retained-unchecked', canInspectRestore=True)
            _verify_proof(root, proof)
            key = activation['snapshotId']
            if key in rows and rows[key]['contentId'] != row['contentId']:
                _reject('version-conflict')
            rows.setdefault(key, row)
        except (OSError, ValueError, KeyError, TypeError):
            # An unverified directory or another package's transaction cannot
            # nominate a rollback version. It remains untouched.
            continue
    versions = files._target(root, snapshots.STORE + '/versions/' + package)
    if versions.exists():
        children = list(versions.iterdir())
        if len(children) > MAX_HISTORY:
            _reject('history-limit')
        for path in children:
            files._check_cancelled(cancelled)
            try:
                snapshots._id(path.name)
            except ValueError:
                continue
            if path.name not in rows:
                rows[path.name] = dict(packageId=package, snapshotId=path.name, sourceTransactionId='',
                    archiveSha256='', contentId='', season=current['season'], fileCount=0, bytes=0,
                    current=path.name == current['snapshotId'], status='unverified-history', canInspectRestore=False)
    for row in rows.values():
        row['canInspectRestore'] = row['canInspectRestore'] and not row['current']
        if row['current'] and row['status'] != 'unverified-history':
            row['status'] = 'current'
    return sorted(rows.values(), key=lambda row: (not row['current'], row['snapshotId']))


def list_versions(install_root, package, *, cancelled=None):
    with resource_read_lock(install_root, wait=True, cancelled=cancelled) as root:
        return _list_locked(Path(root), package, cancelled)


def _inspect_locked(root, package, transaction_id, cancelled=None):
    assert_resources_ready(root)
    current, current_stamp = _current(root, package)
    view, document, evidence = _candidate(root, package, transaction_id, cancelled)
    if view.activation['season'] != current['season']:
        _reject('season-mismatch')
    if current['snapshotId'] == view.activation['snapshotId']:
        _reject('already-current')
    if not document['layers']:
        _reject('no-compatible-layers')
    after, after_stamp = _current(root, package)
    if (after, after_stamp) != (current, current_stamp):
        _reject('current-changed')
    plan = dict(action='restore-author-version', packageId=package, title=document['displayName'],
        sourceTransactionId=transaction_id, snapshotId=view.activation['snapshotId'],
        archiveSha256=view.manifest['receipt']['archiveSha256'], season=current['season'],
        currentActivation=current, currentStamp=current_stamp, candidate=evidence,
        activeLayerCount=len(document['layers']), inactiveLayers=document['inactiveLayers'],
        fileCount=len(view.owned), bytes=sum(row['bytes'] for row in view.owned.values()),
        retainedPreviousVersions=True, restartRequired=False)
    plan['planSha256'] = snapshots._digest(plan)
    return plan


def inspect_restore(install_root, package, transaction_id, *, cancelled=None):
    with resource_read_lock(install_root, wait=True, cancelled=cancelled) as root:
        return _inspect_locked(Path(root), package, transaction_id, cancelled)


def restore_version(install_root, package, transaction_id, *, application_idle=False,
                    expected_plan_sha256=None, cancelled=None):
    if application_idle is not True:
        _reject('restore-requires-idle')
    if not expected_plan_sha256:
        _reject('restore-confirmation-required')
    with files._writer_locked_installation(install_root, require_closed=False) as (root, _):
        plan = _inspect_locked(root, package, transaction_id, cancelled)
        if plan['planSha256'] != expected_plan_sha256:
            _reject('plan-changed-reinspect-required')
        preflight_hardlinks(root, [snapshots.activation_path(package), snapshots.PENDING])
        def verify_candidate():
            _, _, current = _candidate(root, package, transaction_id, cancelled)
            if current != plan['candidate']:
                _reject('plan-changed-reinspect-required')
        value = dict(schema=snapshots.ACTIVATION_SCHEMA, packageId=package, season=plan['season'],
            snapshotId=plan['snapshotId'], manifestSha256=plan['candidate']['manifestSha256'], generation=uuid.uuid4().hex)
        txn = snapshots._activate_locked(root, package, value,
            expected_activation_sha=plan['currentStamp']['sha256'],
            expected_activation_identity=plan['currentStamp']['identity'],
            expected_legacy_sha=None, expected_legacy_identity=None, cancelled=cancelled,
            before_commit=verify_candidate)
        return dict(status='author-version-restored', packageId=package, snapshotId=plan['snapshotId'],
            snapshotTransactionId=txn, activeLayerCount=plan['activeLayerCount'],
            inactiveLayers=plan['inactiveLayers'], retainedPreviousVersions=True, restartRequired=False)
