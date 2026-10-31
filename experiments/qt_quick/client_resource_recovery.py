"""Qt compatibility import for the shared closed-application recovery plan."""
from map_package_recovery_plan import execute_locked, inspect_locked, upgrade
import star_map_import as importer
from package_lifecycle import assert_no_pending


def history_locked(root):
    assert_no_pending(root)
    pending = upgrade._pending_id(root)
    parent = importer._target(root, upgrade.STATE + "/upgrades")
    result = []
    if parent.exists():
        directories = sorted(parent.iterdir(), key=lambda p: p.name)
        if len(directories) > 128:
            raise importer.ImportRejected("too-many-upgrade-journals; inspect-with-closed-tool")
        for directory in directories:
            importer._no_links(directory)
            if not directory.is_dir() or not (directory / "journal.json").exists():
                continue
            plan = inspect_locked(root, upgrade._transaction_id(directory.name))
            if plan["pending"] or plan["status"] != "rolled-back":
                result.append(plan)
    if pending and not any(row["pending"] for row in result):
        raise importer.ImportRejected("pending-upgrade-journal-missing")
    return result
