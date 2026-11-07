"""Read-only consent plans around the existing app-closed upgrade transaction."""
import hashlib
import json
import stat

import star_map_import as importer
import map_package_upgrade as upgrade


def _stamp(path):
    importer._no_links(path)
    if not path.exists():
        return None
    before = path.stat()
    if not stat.S_ISREG(before.st_mode):
        raise importer.ImportRejected("recovery-target-not-regular")
    digest = importer._sha(path)
    after = path.stat()
    identity = lambda info: (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns)
    if identity(before) != identity(after):
        raise importer.ImportRejected("recovery-target-changed-during-inspection")
    return dict(identity=list(identity(after)), sha256=digest)


def inspect_locked(root, transaction_id=None):
    """Caller holds the OS resource writer lock; no files are changed."""
    import author_resource_snapshots as snapshots
    if importer._target(root, snapshots.PENDING).exists():
        plan = snapshots.recovery_plan_locked(root)
        if transaction_id is None or transaction_id == plan['transactionId']:
            return plan
    if importer._target(root, 'work/map-packages/pending-removal.json').exists():
        from map_package_removal import inspect_pending_removal_locked
        removal = inspect_pending_removal_locked(root)
        if transaction_id is None or removal['transactionId'] == transaction_id:
            return removal
    additions = upgrade._addition_journal(root)
    if additions is not None and (transaction_id is None or additions[2]['transactionId'] == transaction_id):
        stage, retained, journal = additions
        outcome = upgrade._addition_outcome(stage, retained, journal)
        paths = {upgrade.ADDITIONS_PENDING, retained.relative_to(root).as_posix(),
                 (stage / upgrade.ADDITIONS_OUTCOME).relative_to(root).as_posix(),
                 (stage / 'receipt.json').relative_to(root).as_posix()}
        if outcome is None:
            paths.add(f"{upgrade.STATE}/{journal['packageId']}.json")
            paths.update(row['path'] for row in journal['files'])
        paths.update((stage / row['path']).relative_to(root).as_posix() for row in journal['files'])
        fingerprints = {name: _stamp(importer._target(root, name)) for name in sorted(paths)}
        encoded = json.dumps(fingerprints, sort_keys=True, separators=(',', ':')).encode()
        return dict(transactionId=journal['transactionId'], packageId=journal['packageId'],
                    status='additions-pending', pending=True, fileCount=len(journal['files']),
                    blockedPaths=[], planSha256=hashlib.sha256(encoded).hexdigest(),
                    **({'cleanupOnly': True} if outcome is not None else {}))
    pending = upgrade._pending_id(root)
    if transaction_id is None:
        transaction_id = pending
    if transaction_id is None:
        raise importer.ImportRejected("no-pending-upgrade")
    directory, journal = upgrade._load_journal(root, transaction_id)
    terminal = (journal["status"] in upgrade.TERMINAL | upgrade.RETIRED_STATES
                or upgrade._receipt_committed(root, journal))
    if journal["status"] in upgrade.RETIRED_STATES and transaction_id != pending:
        raise importer.ImportRejected("upgrade-transaction-retired")
    paths = {upgrade.PENDING, (directory / "journal.json").relative_to(root).as_posix(),
             (directory / "pending-pointer.json").relative_to(root).as_posix()}
    for entry in journal.get("entries", []):
        relative = entry["new"]["path"]
        if not terminal:
            paths.add(relative)
        paths.add((directory / "new" / relative).relative_to(root).as_posix())
        paths.add((directory / "backup" / relative).relative_to(root).as_posix())
    if journal.get("stage"):
        paths.add(journal["stage"] + "/upgrade-receipt.json")
        paths.update(journal["stage"] + "/" + row["path"] for row in journal["files"])
    paths.update(row["path"] for row in journal.get("cachedArchives", []))
    fingerprints = {name: _stamp(importer._target(root, name)) for name in sorted(paths)}
    encoded = json.dumps(fingerprints, sort_keys=True, separators=(",", ":")).encode()
    return dict(transactionId=transaction_id, packageId=journal["packageId"],
        status=("upgrade-cleanup-pending" if transaction_id == pending else "upgrade-cleanup-available")
               if terminal else journal["status"], pending=transaction_id == pending,
        fileCount=len(journal.get("files", [])), blockedPaths=list(journal.get("blockedPaths", [])),
        planSha256=hashlib.sha256(encoded).hexdigest())


def history_locked(root):
    pending = upgrade._pending_id(root)
    result = []
    import author_resource_snapshots as snapshots
    if importer._target(root, snapshots.PENDING).exists():
        result.append(snapshots.recovery_plan_locked(root))
    if importer._target(root, 'work/map-packages/pending-removal.json').exists():
        from map_package_removal import inspect_pending_removal_locked
        result.append(inspect_pending_removal_locked(root))
    additions = upgrade._addition_journal(root)
    if additions is not None:
        result.append(inspect_locked(root, additions[2]['transactionId']))
    parent = importer._target(root, upgrade.STATE + "/upgrades")
    if not parent.exists():
        if pending is not None:
            raise importer.ImportRejected("pending-upgrade-journal-missing")
        return result
    importer._no_links(parent)
    directories = upgrade._history_directories(root)
    for directory in directories:
        importer._no_links(directory)
        if not directory.is_dir():
            continue
        _, journal = upgrade._load_journal(root, directory.name)
        if journal["status"] in upgrade.RETIRED_STATES and directory.name != pending:
            upgrade._retired_plan(directory, journal)
            continue
        result.append(inspect_locked(root, upgrade._transaction_id(directory.name)))
    if pending is not None and not any(plan["pending"] for plan in result):
        raise importer.ImportRejected("pending-upgrade-journal-missing")
    return result


def execute_locked(root, plan, action):
    """Recheck consent before recovery or cleanup under the caller's writer lock."""
    current = inspect_locked(root, plan["transactionId"])
    if current != plan:
        raise importer.ImportRejected("recovery-plan-changed-reinspect-required")
    if current['status'] == 'author-snapshot-pending':
        if action != 'recover':
            raise importer.ImportRejected('recovery-operation-no-longer-valid')
        import author_resource_snapshots as snapshots
        return snapshots.recover_locked(root)
    if current['status'] == 'removal-pending':
        if action != 'recover':
            raise importer.ImportRejected('recovery-operation-no-longer-valid')
        from map_package_removal import recover_pending_removal_locked
        return recover_pending_removal_locked(root)
    if current['status'] == 'additions-pending':
        if action != 'recover':
            raise importer.ImportRejected('recovery-operation-no-longer-valid')
        return upgrade.recover_pending_additions(root)
    if current['status'] in {'upgrade-cleanup-pending', 'upgrade-cleanup-available'}:
        if action != 'recover':
            raise importer.ImportRejected('upgrade-transaction-no-longer-rollbackable')
        return upgrade.retire_upgrade(root, current['transactionId'])
    if action == "recover" and current["pending"]:
        return upgrade.recover_pending_upgrade(root)
    if action == "rollback":
        return upgrade.rollback_upgrade(root, current["transactionId"])
    raise importer.ImportRejected("recovery-operation-no-longer-valid")
