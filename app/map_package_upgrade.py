"""Persistent, receipt-owned map upgrades. Call only under the import lock.

The application must be closed. Windows moves each old object using the same
READ|DELETE, share-READ handle that verifies its identity and contents. POSIX
uses an exclusive backup link followed by a guarded unlink; it cannot exclude
hostile concurrent pathname/in-place writes as Windows does. Journal/file data
is fsynced, with directory fsync where supported. This supports process-loss
recovery, but does not promise atomicity across a power failure or faulty disk.
Backups and new inode-pinning links are deliberately retained after success.
"""
from __future__ import annotations

import ctypes
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import uuid


SCHEMA = "map-package-owned-upgrade-v1"
POINTER_SCHEMA = "map-package-pending-upgrade-v1"
STATE = "work/map-packages"
PENDING = STATE + "/pending-upgrade.json"
MAX_JOURNAL_BYTES = 8 * 1024 * 1024
STATES = {"prepared", "committed", "rolling-back", "rollback-incomplete", "rolled-back"}


def _adapter():
    import star_map_import
    return star_map_import


def _reject(reason):
    raise _adapter().ImportRejected(reason)


def _transaction_id(value):
    if not isinstance(value, str):
        _reject("invalid-upgrade-transaction-id")
    try:
        parsed = uuid.UUID(value)
    except ValueError:
        _reject("invalid-upgrade-transaction-id")
    if str(parsed) != value or parsed.version != 4:
        _reject("invalid-upgrade-transaction-id")
    return value


def _digest(value):
    if not isinstance(value, str) or not re.fullmatch(r"[0-9a-f]{64}", value):
        _reject("invalid-upgrade-digest")
    return value


def _identity(info):
    return [info.st_dev, info.st_ino]


def _valid_identity(value):
    if (not isinstance(value, list) or len(value) != 2
            or any(type(number) is not int or not 0 <= number < 2 ** 256 for number in value)):
        _reject("invalid-upgrade-file-identity")
    return value


def _directory_sync(path):
    if os.name == "nt":
        return  # Python cannot portably flush a Windows directory handle.
    fd = os.open(path, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def _write_new(path, data):
    _adapter()._no_links(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    _adapter()._no_links(path.parent)
    with path.open("xb") as handle:
        handle.write(data)
        handle.flush()
        os.fsync(handle.fileno())
    _directory_sync(path.parent)


def _encoded(value):
    data = json.dumps(value, ensure_ascii=False, indent=2).encode("utf-8")
    if len(data) > MAX_JOURNAL_BYTES:
        _reject("upgrade-metadata-too-large")
    return data


def _write_journal(directory, journal):
    adapter = _adapter()
    path = adapter._target(directory, "journal.json")
    temporary = adapter._target(directory, f"journal-{uuid.uuid4()}.tmp")
    _write_new(temporary, _encoded(journal))
    os.replace(temporary, path)
    _directory_sync(directory)


def _read_bytes(path):
    adapter = _adapter()
    adapter._no_links(path)
    with path.open("rb") as handle:
        info = os.fstat(handle.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_size > MAX_JOURNAL_BYTES:
            _reject("invalid-upgrade-metadata-file")
        data = handle.read(MAX_JOURNAL_BYTES + 1)
    if len(data) > MAX_JOURNAL_BYTES:
        _reject("upgrade-metadata-too-large")
    return data


def _read_json(path):
    return _adapter()._json(_read_bytes(path))


def _matches(path, row, identity=None):
    adapter = _adapter()
    adapter._no_links(path)
    try:
        with path.open("rb") as handle:
            info = os.fstat(handle.fileno())
            if (not stat.S_ISREG(info.st_mode) or info.st_size != row["bytes"]
                    or (identity is not None and _identity(info) != identity)):
                return False
            return hashlib.file_digest(handle, "sha256").hexdigest() == row["sha256"]
    except FileNotFoundError:
        return False


def _capture(path, row):
    if not _matches(path, row):
        _reject(f"upgrade-owned-file-changed: {row['path']}")
    info = path.stat()
    if not _matches(path, row, _identity(info)):
        _reject(f"upgrade-owned-file-changed: {row['path']}")
    return _identity(info)


def _rows(values, package_id):
    adapter = _adapter()
    rows = {} if values == [] else adapter._rows(values)
    for row in rows.values():
        if not adapter._allowed(package_id, row["path"]):
            _reject("upgrade-path-not-in-data-allowlist")
    if sum(row["bytes"] for row in rows.values()) > adapter.MAX_TOTAL_BYTES:
        _reject("upgrade-expanded-size-limit")
    return rows


def _receipt_row(package_id, data):
    return {"path": f"{STATE}/{package_id}.json", "bytes": len(data),
            "sha256": hashlib.sha256(data).hexdigest()}


def _move_owned(source, destination, row, identity):
    """Move only the verified old object, exclusively, retaining no-overwrite."""
    adapter = _adapter()
    adapter._no_links(source)
    adapter._no_links(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    adapter._no_links(destination.parent)
    if os.name != "nt":
        if not _matches(source, row, identity):
            _reject("upgrade-owned-target-changed-before-backup")
        # link() never overwrites. The adapter's POSIX guarded unlink still has
        # its documented final path race; fail closed on every detectable race.
        os.link(source, destination)
        if not _matches(destination, row, identity):
            _reject("upgrade-backup-object-changed")
        if not adapter._rollback_published(source, tuple(identity), row["sha256"]):
            _reject("upgrade-owned-target-could-not-be-moved")
    else:
        from ctypes import wintypes
        import msvcrt
        api = ctypes.WinDLL("kernel32", use_last_error=True)
        api.CreateFileW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD,
            ctypes.c_void_p, wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE]
        api.CreateFileW.restype = wintypes.HANDLE
        api.CloseHandle.argtypes = [wintypes.HANDLE]
        api.SetFileInformationByHandle.argtypes = [wintypes.HANDLE, ctypes.c_int,
                                                   ctypes.c_void_p, wintypes.DWORD]
        api.SetFileInformationByHandle.restype = wintypes.BOOL
        native = api.CreateFileW(str(source), 0x80010000, 1, None, 3, 0x200000, None)
        if native == ctypes.c_void_p(-1).value:
            raise OSError(ctypes.get_last_error(), "upgrade-owned-target-not-exclusively-readable")
        try:
            fd = msvcrt.open_osfhandle(int(native), os.O_RDONLY | os.O_BINARY)
            native = None
            with os.fdopen(fd, "rb") as handle:
                info = os.fstat(handle.fileno())
                if (not stat.S_ISREG(info.st_mode) or info.st_size != row["bytes"]
                        or _identity(info) != identity
                        or getattr(info, "st_file_attributes", 0) & 0x400
                        or hashlib.file_digest(handle, "sha256").hexdigest() != row["sha256"]):
                    _reject("upgrade-owned-target-changed-before-backup")
                class RenameInfo(ctypes.Structure):
                    _fields_ = [("ReplaceIfExists", ctypes.c_ubyte),
                                ("RootDirectory", wintypes.HANDLE),
                                ("FileNameLength", wintypes.DWORD),
                                ("FileName", wintypes.WCHAR * 1)]
                name = str(destination.absolute()).encode("utf-16-le")
                size = max(ctypes.sizeof(RenameInfo), RenameInfo.FileName.offset + len(name) + 2)
                buffer = ctypes.create_string_buffer(size)
                rename = RenameInfo.from_buffer(buffer)
                rename.ReplaceIfExists = 0
                rename.RootDirectory = None
                rename.FileNameLength = len(name)
                ctypes.memmove(ctypes.addressof(buffer) + RenameInfo.FileName.offset, name, len(name))
                if not api.SetFileInformationByHandle(msvcrt.get_osfhandle(handle.fileno()),
                                                      3, buffer, size):
                    raise OSError(ctypes.get_last_error(), "upgrade-owned-target-rename-failed")
        finally:
            if native is not None:
                api.CloseHandle(native)
    _directory_sync(source.parent)
    _directory_sync(destination.parent)


def _pointer(root, transaction_id, directory):
    adapter = _adapter()
    value = {"schema": POINTER_SCHEMA, "transactionId": transaction_id}
    retained = adapter._target(directory, "pending-pointer.json")
    if not retained.exists():
        _write_new(retained, _encoded(value))
    elif _read_json(retained) != value:
        _reject("upgrade-pointer-source-changed")
    pending = adapter._target(root, PENDING)
    os.link(retained, pending)  # Exclusive: never replace another transaction.
    _directory_sync(pending.parent)


def _pending_id(root):
    path = _adapter()._target(root, PENDING)
    if not path.exists():
        return None
    value = _read_json(path)
    if set(value) != {"schema", "transactionId"} or value["schema"] != POINTER_SCHEMA:
        _reject("invalid-pending-upgrade-pointer")
    return _transaction_id(value["transactionId"])


def _clear_pointer(root, transaction_id, directory):
    adapter = _adapter()
    if _pending_id(root) != transaction_id:
        _reject("pending-upgrade-pointer-changed")
    retained = adapter._target(directory, "pending-pointer.json")
    expected = {"schema": POINTER_SCHEMA, "transactionId": transaction_id}
    if _read_json(retained) != expected:
        _reject("upgrade-pointer-source-changed")
    path = adapter._target(root, PENDING)
    if not adapter._rollback_published(path, tuple(_identity(retained.stat())), adapter._sha(retained)):
        _reject("pending-upgrade-pointer-could-not-be-cleared")
    _directory_sync(path.parent)


def _load_journal(root, transaction_id):
    adapter = _adapter()
    transaction_id = _transaction_id(transaction_id)
    directory = adapter._target(root, f"{STATE}/upgrades/{transaction_id}")
    journal = _read_json(adapter._target(directory, "journal.json"))
    if (set(journal) != {"schema", "transactionId", "packageId", "rootIdentity", "status", "files",
                        "entries", "oldReceipt", "newReceipt", "blockedPaths"}
            or journal["schema"] != SCHEMA or journal["transactionId"] != transaction_id
            or not isinstance(journal["packageId"], str) or journal["packageId"] not in adapter.PACKAGE_IDS
            or not isinstance(journal["status"], str) or journal["status"] not in STATES
            or _valid_identity(journal["rootIdentity"]) != _identity(root.stat())):
        _reject("invalid-upgrade-journal")
    package_id = journal["packageId"]
    inventory = _rows(journal["files"], package_id)
    if not inventory:
        _reject("empty-upgrade-inventory")
    receipt_path = f"{STATE}/{package_id}.json"
    for name in ("oldReceipt", "newReceipt"):
        row = journal[name]
        if (not isinstance(row, dict) or set(row) != {"path", "bytes", "sha256"}
                or row["path"] != receipt_path or type(row["bytes"]) is not int
                or not 0 <= row["bytes"] <= MAX_JOURNAL_BYTES):
            _reject("invalid-upgrade-receipt-record")
        _digest(row["sha256"])
    entries = journal["entries"]
    if not isinstance(entries, list) or not 1 <= len(entries) <= adapter.MAX_FILES + 1:
        _reject("invalid-upgrade-entry-count")
    found = set()
    for entry in entries:
        if (not isinstance(entry, dict) or set(entry) != {"new", "old", "newIdentity", "oldIdentity"}
                or not isinstance(entry["new"], dict)):
            _reject("invalid-upgrade-entry")
        row = entry["new"]
        path = row.get("path")
        if not isinstance(path, str) or path.casefold() in found:
            _reject("duplicate-upgrade-entry")
        found.add(path.casefold())
        _valid_identity(entry["newIdentity"])
        if path == receipt_path:
            if row != journal["newReceipt"] or entry["old"] != journal["oldReceipt"]:
                _reject("upgrade-receipt-entry-mismatch")
        elif inventory.get(path.casefold()) != row:
            _reject("upgrade-entry-not-in-inventory")
        if entry["old"] is not None:
            old = entry["old"]
            _valid_identity(entry["oldIdentity"])
            if path != receipt_path:
                if _rows([old], package_id).get(path.casefold()) != old:
                    _reject("upgrade-old-entry-path-mismatch")
        elif entry["oldIdentity"] is not None:
            _reject("upgrade-missing-entry-has-old-identity")
        retained = adapter._target(directory, "new/" + path)
        if not _matches(retained, row, entry["newIdentity"]):
            _reject("upgrade-retained-new-object-changed")
    if receipt_path.casefold() not in found:
        _reject("upgrade-receipt-entry-missing")
    blocked = journal["blockedPaths"]
    if (not isinstance(blocked, list) or len(blocked) > len(entries)
            or any(not isinstance(path, str) or path.casefold() not in found for path in blocked)):
        _reject("invalid-upgrade-blocked-paths")
    # Old ownership is proven by the old receipt, never the new ZIP inventory.
    old_receipt = adapter._target(directory, "backup/" + receipt_path)
    if not old_receipt.exists():
        old_receipt = adapter._target(root, receipt_path)
    if not _matches(old_receipt, journal["oldReceipt"]):
        _reject("upgrade-old-receipt-unavailable-or-changed")
    previous = _read_json(old_receipt)
    if previous.get("packageId") != package_id:
        _reject("upgrade-old-receipt-package-mismatch")
    old_inventory = _rows(previous.get("files"), package_id)
    for entry in entries:
        old = entry["old"]
        if old is not None and old["path"] != receipt_path and old_inventory.get(old["path"].casefold()) != old:
            _reject("upgrade-replacement-not-owned-by-old-receipt")
    new_receipt = _read_json(adapter._target(directory, "new/" + receipt_path))
    if (new_receipt.get("packageId") != package_id
            or new_receipt.get("upgradeTransactionId") != transaction_id
            or _rows(new_receipt.get("files"), package_id) != inventory):
        _reject("upgrade-new-receipt-mismatch")
    return directory, journal


def _rollback(root, directory, journal):
    adapter = _adapter()
    journal["status"] = "rolling-back"
    _write_journal(directory, journal)
    blocked = []
    # Diagnose the complete rollback before touching any resource or receipt.
    # A pre-existing user edit must not leave unrelated entries half-restored.
    for entry in journal["entries"]:
        new, old = entry["new"], entry["old"]
        try:
            path = adapter._target(root, new["path"])
            if old is not None and _matches(path, old, entry["oldIdentity"]):
                continue
            if old is not None:
                backup = adapter._target(directory, "backup/" + new["path"])
                if not _matches(backup, old, entry["oldIdentity"]):
                    blocked.append(new["path"])
                    continue
            if path.exists():
                retained = adapter._target(directory, "new/" + new["path"])
                if (not _matches(retained, new, entry["newIdentity"])
                        or not _matches(path, new, entry["newIdentity"])):
                    blocked.append(new["path"])
        except (OSError, adapter.ImportRejected):
            blocked.append(new["path"])
    if blocked:
        journal["blockedPaths"] = blocked
        journal["status"] = "rollback-incomplete"
        _write_journal(directory, journal)
        _reject("upgrade-rollback-incomplete; no-resource-changes; backups-and-pending-marker-retained: "
                + ", ".join(blocked))
    for entry in reversed(journal["entries"]):
        new, old = entry["new"], entry["old"]
        path = adapter._target(root, new["path"])
        backup = adapter._target(directory, "backup/" + new["path"])
        try:
            # A prior partial rollback may have already restored the old inode.
            if old is not None and _matches(path, old, entry["oldIdentity"]):
                continue
            # Never remove the new version unless restoration is possible.
            if old is not None and not _matches(backup, old, entry["oldIdentity"]):
                blocked.append(new["path"])
                continue
            if path.exists():
                retained = adapter._target(directory, "new/" + new["path"])
                if (not _matches(retained, new, entry["newIdentity"])
                        or not adapter._rollback_published(path, tuple(entry["newIdentity"]), new["sha256"])):
                    blocked.append(new["path"])
                    continue
                _directory_sync(path.parent)
            if old is not None:
                path.parent.mkdir(parents=True, exist_ok=True)
                adapter._no_links(path.parent)
                os.link(backup, path)  # Exclusive restoration; retain the backup.
                if not _matches(path, old, entry["oldIdentity"]):
                    blocked.append(new["path"])
                _directory_sync(path.parent)
        except (OSError, adapter.ImportRejected):
            blocked.append(new["path"])
    journal["blockedPaths"] = blocked
    journal["status"] = "rollback-incomplete" if blocked else "rolled-back"
    _write_journal(directory, journal)
    if blocked:
        _reject("upgrade-rollback-incomplete; backups-and-pending-marker-retained: " + ", ".join(blocked))
    _clear_pointer(root, journal["transactionId"], directory)
    return {"status": "rolled-back", "packageId": journal["packageId"],
            "upgradeTransactionId": journal["transactionId"], "restartRequired": True}


def commit_upgrade(root: Path, result: dict, stage: Path) -> dict:
    """Commit validated same-pack replacements; caller owns lock/app-closed check."""
    adapter = _adapter()
    root, stage = Path(os.path.abspath(root)), Path(os.path.abspath(stage))
    adapter._no_links(root)
    adapter._no_links(stage)
    if _pending_id(root) is not None:
        _reject("pending-upgrade-recovery-required")
    package_id = result.get("packageId")
    if not isinstance(package_id, str) or package_id not in adapter.PACKAGE_IDS:
        _reject("invalid-upgrade-package-id")
    inventory = _rows(result.get("files"), package_id)
    replacements = _rows(result.get("replaceFiles"), package_id)
    missing = _rows(result.get("missingFiles"), package_id)
    if (not inventory or not replacements or set(replacements) & set(missing)
            or not (set(replacements) | set(missing)) <= set(inventory)
            or any(inventory[key] != row for key, row in missing.items())):
        _reject("invalid-upgrade-change-inventory")
    _digest(result.get("archiveSha256"))
    previous_digest = _digest(result.get("previousReceiptSha256"))
    receipt = adapter._target(root, f"{STATE}/{package_id}.json")
    previous_data = _read_bytes(receipt)
    if len(previous_data) > MAX_JOURNAL_BYTES or hashlib.sha256(previous_data).hexdigest() != previous_digest:
        _reject("upgrade-old-receipt-changed")
    previous = adapter._json(previous_data)
    if previous.get("packageId") != package_id:
        _reject("upgrade-old-receipt-package-mismatch")
    old_inventory = _rows(previous.get("files"), package_id)
    if any(old_inventory.get(key) != row for key, row in replacements.items()):
        _reject("upgrade-replacement-not-owned-by-old-receipt")
    # Recheck the entire selected closure immediately before preparing mutation.
    identities = {}
    for key, row in inventory.items():
        target = adapter._target(root, row["path"])
        if key in missing:
            if target.exists():
                _reject("upgrade-missing-target-now-exists")
        else:
            identities[key] = _capture(target, replacements.get(key, row))
    transaction_id = str(uuid.uuid4())
    directory = adapter._target(root, f"{STATE}/upgrades/{transaction_id}")
    directory.mkdir(parents=True, exist_ok=False)
    adapter._no_links(directory)
    output = dict(result, upgradeTransactionId=transaction_id, added=len(missing),
                  replaced=len(replacements), status="upgraded-restart-required")
    old_receipt_row = _receipt_row(package_id, previous_data)
    old_receipt_identity = _capture(receipt, old_receipt_row)
    new_receipt_data = _encoded(output)
    new_receipt_row = _receipt_row(package_id, new_receipt_data)
    entries = []
    for key in sorted(set(replacements) | set(missing)):
        row = inventory[key]
        source = adapter._target(stage, row["path"])
        if not _matches(source, row):
            _reject("upgrade-staged-data-changed")
        retained = adapter._target(directory, "new/" + row["path"])
        retained.parent.mkdir(parents=True, exist_ok=True)
        adapter._no_links(retained.parent)
        os.link(source, retained)
        # Windows _commit/FlushFileBuffers requires a writable file handle.
        with retained.open("r+b") as handle:
            os.fsync(handle.fileno())
        entries.append({"new": row, "old": replacements.get(key),
                        "newIdentity": _capture(retained, row), "oldIdentity": identities.get(key)})
    new_receipt = adapter._target(directory, "new/" + new_receipt_row["path"])
    _write_new(new_receipt, new_receipt_data)
    entries.append({"new": new_receipt_row, "old": old_receipt_row,
                    "newIdentity": _capture(new_receipt, new_receipt_row),
                    "oldIdentity": old_receipt_identity})
    journal = {"schema": SCHEMA, "transactionId": transaction_id, "packageId": package_id,
               "rootIdentity": _identity(root.stat()), "status": "prepared", "files": list(inventory.values()),
               "entries": entries, "oldReceipt": old_receipt_row, "newReceipt": new_receipt_row,
               "blockedPaths": []}
    _write_journal(directory, journal)
    _pointer(root, transaction_id, directory)
    try:
        for entry in entries:
            row = entry["new"]
            target = adapter._target(root, row["path"])
            if row["path"] == new_receipt_row["path"]:
                for current in inventory.values():
                    if not _matches(adapter._target(root, current["path"]), current):
                        _reject("upgrade-data-changed-before-receipt-publication")
            if entry["old"] is not None:
                backup = adapter._target(directory, "backup/" + row["path"])
                _move_owned(target, backup, entry["old"], entry["oldIdentity"])
            target.parent.mkdir(parents=True, exist_ok=True)
            adapter._no_links(target.parent)
            retained = adapter._target(directory, "new/" + row["path"])
            if not _matches(retained, row, entry["newIdentity"]):
                _reject("upgrade-retained-new-object-changed")
            os.link(retained, target)
            if not _matches(target, row, entry["newIdentity"]):
                _reject("upgrade-published-object-changed")
            _directory_sync(target.parent)
        journal["status"] = "committed"
        _write_journal(directory, journal)
        _clear_pointer(root, transaction_id, directory)
        return output
    except Exception as error:
        try:
            _rollback(root, directory, journal)
        except Exception as rollback_error:
            raise adapter.ImportRejected(f"upgrade-failed-and-rollback-incomplete; transaction={transaction_id}; "
                                         f"{rollback_error}") from error
        raise


def recover_pending_upgrade(root: Path) -> dict:
    """Explicit process-loss recovery; caller owns import lock and app closure."""
    root = Path(os.path.abspath(root))
    transaction_id = _pending_id(root)
    if transaction_id is None:
        return {"status": "no-pending-upgrade"}
    directory, journal = _load_journal(root, transaction_id)
    return _rollback(root, directory, journal)


def rollback_upgrade(root: Path, transaction_id: str) -> dict:
    """Undo a completed transaction without deleting a changed/current object."""
    root = Path(os.path.abspath(root))
    transaction_id = _transaction_id(transaction_id)
    pending = _pending_id(root)
    if pending is not None and pending != transaction_id:
        _reject("another-pending-upgrade-recovery-required")
    directory, journal = _load_journal(root, transaction_id)
    if journal["status"] == "rolled-back" and pending is None:
        return {"status": "rolled-back", "packageId": journal["packageId"],
                "upgradeTransactionId": transaction_id, "restartRequired": True}
    if pending is None:
        _pointer(root, transaction_id, directory)
    return _rollback(root, directory, journal)
