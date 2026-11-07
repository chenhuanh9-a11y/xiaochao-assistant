"""Receipt-bound snapshot candidates for the existing removal transaction.

This module only plans. It does not delete, claim unknown files, validate base
compatibility, or reconstruct missing activation authority from directory names.
The caller hashes every payload, confirms the plan and uses the common pinned
object rollback/commit/cleanup implementation.
"""
from pathlib import Path
import re

import author_resource_snapshots as snapshots
import generic_author_package as generic
import star_map_import as files


def valid_target(package, relative):
    if not generic.is_generic_profile(package):
        return False
    if relative == snapshots.activation_path(package):
        return True
    prefix = snapshots.STORE + '/versions/' + package + '/'
    if relative.startswith(prefix):
        return bool(re.fullmatch(r'[a-f0-9]{32}/(?:manifest\.json|files/[0-9]{4,5}\.(?:png|jpg|jpeg|webp|json))', relative[len(prefix):]))
    prefix = snapshots.STORE + '/transactions/'
    if relative.startswith(prefix):
        parts = relative[len(prefix):].split('/')
        if len(parts) == 2:
            try:
                snapshots._transaction_id(parts[0])
            except ValueError:
                return False
            return parts[1] in {'journal.json', 'pending-pointer.json', 'new-activation.json',
                               'committed.json', 'commit-pointer.json', 'old-0', 'old-1',
                               'pending-pointer.json.publication.json', 'commit-pointer.json.publication.json'}
    return False


def _transaction(root, directory, package):
    """A completed activation proves its nominated snapshot and metadata pins."""
    tx = snapshots._transaction_id(directory.name)
    journal_path = files._target(directory, 'journal.json')
    journal = snapshots._validated_journal(root, directory, tx, snapshots._read_json(journal_path))
    if journal['packageId'] != package or journal['status'] != 'committed':
        raise ValueError('snapshot-removal-uncommitted-history')
    pointer = files._target(directory, 'pending-pointer.json')
    if snapshots._metadata(pointer)[0] != snapshots._encoded(dict(
            schema=snapshots.JOURNAL_SCHEMA, transactionId=tx, planSha256=snapshots._journal_digest(journal))):
        raise ValueError('snapshot-removal-history-pointer-changed')
    metadata = [journal_path, pointer, files._target(directory, 'committed.json'),
                files._target(directory, 'commit-pointer.json')]
    pin = files._target(directory, 'new-activation.json')
    entry = next(row for row in journal['changes'] if row['path'] == snapshots.activation_path(package))
    history = journal.get('activationHistory', entry['new'])
    if not snapshots.transaction._matches(pin, history, history['identity']):
        raise ValueError('snapshot-removal-history-pin-changed')
    activation = snapshots._validate_activation(snapshots._read_json(pin), package)
    metadata.append(pin)
    if snapshots.transaction._publication_mode(journal) == 'move-v1':
        for retained in (pointer, directory / 'commit-pointer.json'):
            snapshots.transaction._publication_proof(retained)
            proof, source = snapshots.transaction._publication_paths(retained)
            if source.exists():
                raise ValueError('snapshot-removal-history-unpublished-marker')
            metadata.append(proof)
    legacy = []
    for index, row in enumerate(journal['changes']):
        if row['old'] is None:
            continue
        old = files._target(directory, 'old-' + str(index))
        if not snapshots.transaction._matches(old, row['old'], row['old']['identity']):
            raise ValueError('snapshot-removal-history-pin-changed')
        metadata.append(old)
        if row['path'] == f'work/map-packages/{package}.json':
            receipt = snapshots._read_json(old)
            owned, _ = files._receipt_inventory(receipt, package)
            legacy.extend(owned.values())
    return activation, metadata, legacy


def collect(root, package, *, cancelled=None):
    """Return explicit candidates and retained paths, never infer payload ownership."""
    import map_package_removal as removal
    root = Path(root)
    versions, sources, metadata, retained, archives = {}, [], [], [], set()
    active = files._target(root, snapshots.activation_path(package))
    if active.exists():
        activation, _ = snapshots._read_activation(root, package)
        metadata.append(active)
        if activation['snapshotId'] is not None:
            versions[activation['snapshotId']] = activation
    for directory in removal._children(root, snapshots.STORE + '/transactions'):
        files._check_cancelled(cancelled)
        header = files._target(directory, 'journal.json')
        if not header.is_file():
            continue
        try:
            if snapshots._read_json(header).get('packageId') != package:
                continue
            activation, recorded, legacy = _transaction(root, directory, package)
            if activation['snapshotId'] is not None:
                versions[activation['snapshotId']] = activation
            metadata.extend(recorded)
            sources.extend((row, files._target(root, row['path'])) for row in legacy
                           if files._live_author_path(package, row['path']))
            retained.extend(removal._unknown_history(directory, set(recorded)))
        except (OSError, ValueError, KeyError, TypeError):
            # An invalid historical transaction grants no payload authority.
            retained.append(directory)
    known = set()
    for identity, activation in versions.items():
        files._check_cancelled(cancelled)
        directory = files._target(root, snapshots._version_path(package, identity))
        try:
            directory, manifest, owned, _deps = snapshots._manifest(root, package, activation)
            manifest_path = files._target(directory, 'manifest.json')
            paths = [(row, files._target(directory, snapshots._stored_name(index, row)))
                     for index, row in enumerate(sorted(owned.values(), key=lambda row: row['path']))]
            sources.extend(paths)
            metadata.append(manifest_path)
            archives.add(manifest['receipt']['archiveSha256'])
            known.add(directory)
            retained.extend(removal._unknown_history(directory, {path for _, path in paths} | {manifest_path}))
        except (OSError, ValueError, KeyError, TypeError):
            retained.append(directory)
    for directory in removal._children(root, snapshots.STORE + '/versions/' + package):
        if directory not in known and directory not in retained:
            retained.append(directory)
    return dict(sources=sources, metadata=list(dict.fromkeys(metadata)), retained=retained,
                archives=archives)
