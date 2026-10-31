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
    pending = upgrade._pending_id(root)
    if transaction_id is None:
        transaction_id = pending
    if transaction_id is None:
        raise importer.ImportRejected("no-pending-upgrade")
    directory, journal = upgrade._load_journal(root, transaction_id)
    paths = {upgrade.PENDING, (directory / "journal.json").relative_to(root).as_posix(),
             (directory / "pending-pointer.json").relative_to(root).as_posix()}
    for entry in journal["entries"]:
        relative = entry["new"]["path"]
        paths.add(relative)
        paths.add((directory / "new" / relative).relative_to(root).as_posix())
        paths.add((directory / "backup" / relative).relative_to(root).as_posix())
    fingerprints = {name: _stamp(importer._target(root, name)) for name in sorted(paths)}
    encoded = json.dumps(fingerprints, sort_keys=True, separators=(",", ":")).encode()
    return dict(transactionId=transaction_id, packageId=journal["packageId"],
        status=journal["status"], pending=transaction_id == pending,
        fileCount=len(journal["files"]), blockedPaths=list(journal["blockedPaths"]),
        planSha256=hashlib.sha256(encoded).hexdigest())


def history_locked(root):
    pending = upgrade._pending_id(root)
    parent = importer._target(root, upgrade.STATE + "/upgrades")
    if not parent.exists():
        if pending is not None:
            raise importer.ImportRejected("pending-upgrade-journal-missing")
        return []
    importer._no_links(parent)
    result = []
    directories = sorted(parent.iterdir(), key=lambda path: path.name)
    if len(directories) > 128:
        raise importer.ImportRejected("too-many-upgrade-journals; inspect-with-closed-tool")
    for directory in directories:
        importer._no_links(directory)
        if not directory.is_dir():
            continue
        plan = inspect_locked(root, upgrade._transaction_id(directory.name))
        if plan["pending"] or plan["status"] != "rolled-back":
            result.append(plan)
    if pending is not None and not any(plan["pending"] for plan in result):
        raise importer.ImportRejected("pending-upgrade-journal-missing")
    return result


def execute_locked(root, plan, action):
    """Recheck consent, then call the unchanged transaction (including conflicts)."""
    current = inspect_locked(root, plan["transactionId"])
    if current != plan:
        raise importer.ImportRejected("recovery-plan-changed-reinspect-required")
    if action == "recover" and current["pending"]:
        return upgrade.recover_pending_upgrade(root)
    if action == "rollback":
        return upgrade.rollback_upgrade(root, current["transactionId"])
    raise importer.ImportRejected("recovery-operation-no-longer-valid")
