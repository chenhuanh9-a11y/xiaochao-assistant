"""Persistent, receipt-owned map upgrades. Call only under the import lock.

Core upgrades require a closed application. Display-only upgrades may run under
the caller's idle resource lease. Windows moves each old object using the same
READ|DELETE, share-READ handle that verifies its identity and contents. POSIX
uses an exclusive backup link followed by a guarded unlink; it cannot exclude
hostile concurrent pathname/in-place writes as Windows does. Journal/file data
is fsynced, with directory fsync where supported. This supports process-loss
recovery, but does not promise atomicity across a power failure or faulty disk.
Only unresolved transactions retain payloads. Durable committed transactions
can only finish cleanup; they can never restore an earlier installed version.
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
STATES = {"preparing", "prepared", "committed", "rolling-back", "rollback-incomplete", "rolled-back"}
TERMINAL = {"committed", "rolled-back"}
RETIRED_SCHEMA = "map-package-upgrade-retired-v1"
RETIRED_STATES = {"committed-retired", "rolled-back-retired"}
MAX_RETIRED = 16
MAX_HISTORY = 4096


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
    # Explicit rollback also creates a pointer, independently of installation.
    adapter.preflight_hardlinks(root, [PENDING])
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
    if journal.get("schema") == RETIRED_SCHEMA:
        if (set(journal) != {"schema", "transactionId", "packageId", "rootIdentity", "status"}
                or journal["transactionId"] != transaction_id
                or not adapter.known_package_id(journal["packageId"])
                or journal["status"] not in RETIRED_STATES
                or _valid_identity(journal["rootIdentity"]) != _identity(root.stat())
                or len(_encoded(journal)) > 1024):
            _reject("invalid-upgrade-retired-journal")
        return directory, journal
    legacy_keys = {"schema", "transactionId", "packageId", "rootIdentity", "status", "files",
                   "entries", "oldReceipt", "newReceipt", "blockedPaths"}
    extended = set(journal) == legacy_keys | {"receiptData", "stage", "stagedIdentities", "cachedArchives"}
    if ((set(journal) != legacy_keys and not extended)
            or journal["schema"] != SCHEMA or journal["transactionId"] != transaction_id
            or not isinstance(journal["packageId"], str) or not adapter.known_package_id(journal["packageId"])
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
        may_be_missing = extended and journal["status"] in TERMINAL | {"preparing"}
        if (retained.exists() or not may_be_missing) and not _matches(retained, row, entry["newIdentity"]):
            _reject("upgrade-retained-new-object-changed")
    if receipt_path.casefold() not in found:
        _reject("upgrade-receipt-entry-missing")
    blocked = journal["blockedPaths"]
    if (not isinstance(blocked, list) or len(blocked) > len(entries)
            or any(not isinstance(path, str) or path.casefold() not in found for path in blocked)):
        _reject("invalid-upgrade-blocked-paths")
    # Old ownership is proven by the old receipt, never the new ZIP inventory.
    if extended:
        receipts = journal["receiptData"]
        if not isinstance(receipts, dict) or set(receipts) != {"old", "new"}:
            _reject("invalid-upgrade-receipt-proof")
        decoded = {}
        for side in ("old", "new"):
            data = receipts[side]
            row = journal[side + "Receipt"]
            if (not isinstance(data, str) or len(data.encode("utf-8")) != row["bytes"]
                    or hashlib.sha256(data.encode("utf-8")).hexdigest() != row["sha256"]):
                _reject("invalid-upgrade-receipt-proof")
            decoded[side] = adapter._json(data.encode("utf-8"))
        previous, new_receipt = decoded["old"], decoded["new"]
    else:
        old_receipt = adapter._target(directory, "backup/" + receipt_path)
        if not old_receipt.exists():
            old_receipt = adapter._target(root, receipt_path)
        if not _matches(old_receipt, journal["oldReceipt"]):
            _reject("upgrade-old-receipt-unavailable-or-changed")
        previous = _read_json(old_receipt)
        new_receipt = _read_json(adapter._target(directory, "new/" + receipt_path))
    if previous.get("packageId") != package_id:
        _reject("upgrade-old-receipt-package-mismatch")
    old_inventory = _rows(previous.get("files"), package_id)
    for entry in entries:
        old = entry["old"]
        if old is not None and old["path"] != receipt_path and old_inventory.get(old["path"].casefold()) != old:
            _reject("upgrade-replacement-not-owned-by-old-receipt")
    if (new_receipt.get("packageId") != package_id
            or new_receipt.get("upgradeTransactionId") != transaction_id
            or _rows(new_receipt.get("files"), package_id) != inventory):
        _reject("upgrade-new-receipt-mismatch")
    if extended:
        stage, identities = journal["stage"], journal["stagedIdentities"]
        if stage is None:
            if identities != {}:
                _reject("invalid-upgrade-stage")
        elif (not isinstance(stage, str) or not re.fullmatch(r"work/map-packages/staging-[a-zA-Z0-9_-]+", stage)
                or not isinstance(identities, dict) or set(identities) != {row["path"] for row in inventory.values()}):
            _reject("invalid-upgrade-stage")
        else:
            for identity in identities.values():
                _valid_identity(identity)
        allowed_cache = {f"{STATE}/downloads/{package_id}-{_digest(receipt.get('archiveSha256'))}.zip"
                         for receipt in (previous, new_receipt)}
        cached = journal["cachedArchives"]
        if not isinstance(cached, list) or len(cached) > 2:
            _reject("invalid-upgrade-cache-inventory")
        seen_cache = set()
        for row in cached:
            if (not isinstance(row, dict) or set(row) != {"path", "bytes", "sha256", "identity"}
                    or row["path"] not in allowed_cache or row["path"] in seen_cache
                    or type(row["bytes"]) is not int or not 0 <= row["bytes"] <= adapter.MAX_ARCHIVE_BYTES
                    or row["path"] != f"{STATE}/downloads/{package_id}-{_digest(row['sha256'])}.zip"):
                _reject("invalid-upgrade-cache-inventory")
            seen_cache.add(row["path"])
            _valid_identity(row["identity"])
    return directory, journal


def _cached_archives(root, package_id, receipts):
    """Only exact receipt-bound names in the private download cache are owned."""
    adapter = _adapter()
    result = []
    for digest in sorted({_digest(receipt.get("archiveSha256")) for receipt in receipts}):
        relative = f"{STATE}/downloads/{package_id}-{digest}.zip"
        path = adapter._target(root, relative)
        if not path.exists():
            continue
        row = dict(path=relative, bytes=path.stat().st_size, sha256=digest)
        if not 0 <= row["bytes"] <= adapter.MAX_ARCHIVE_BYTES:
            _reject("upgrade-cache-object-changed")
        row["identity"] = _capture(path, row)
        result.append(row)
    return result


def _embed_legacy_proof(root, directory, journal):
    if "receiptData" in journal:
        return journal
    adapter = _adapter()
    relative = journal["oldReceipt"]["path"]
    old = adapter._target(directory, "backup/" + relative)
    if not old.exists():
        old = adapter._target(root, relative)
    old_data = _read_bytes(old)
    new_data = _read_bytes(adapter._target(directory, "new/" + relative))
    journal = dict(journal, receiptData={"old": old_data.decode("utf-8"), "new": new_data.decode("utf-8")},
                   stage=None, stagedIdentities={},
                   cachedArchives=_cached_archives(root, journal["packageId"],
                       (adapter._json(old_data), adapter._json(new_data))))
    _write_journal(directory, journal)
    return journal


def _metadata_row(path, expected):
    data = _read_bytes(path)
    if _adapter()._json(data) != expected:
        _reject("upgrade-cleanup-metadata-changed")
    row = dict(bytes=len(data), sha256=hashlib.sha256(data).hexdigest(), path=path.name)
    return row, _capture(path, row)


def _private_tree_plan(base, expected):
    """Preflight every existing object. Missing owned objects permit restart."""
    adapter = _adapter()
    adapter._no_links(base)
    if not base.exists():
        return [], []
    if not base.is_dir():
        _reject("upgrade-cleanup-private-directory-changed")
    allowed_dirs = {base}
    for name in expected:
        allowed_dirs.update(parent for parent in (base / name).parents if parent.is_relative_to(base))
    files, directories, todo = [], [], [base]
    visited = 0
    while todo:
        parent = todo.pop()
        for path in parent.iterdir():
            visited += 1
            if visited > 100000:
                _reject("upgrade-cleanup-inventory-limit")
            adapter._no_links(path)
            if path.is_dir():
                if path not in allowed_dirs:
                    _reject("upgrade-cleanup-unknown-object")
                directories.append(path)
                todo.append(path)
            else:
                item = expected.get(path.relative_to(base).as_posix())
                if item is None or not _matches(path, item[0], item[1]):
                    _reject("upgrade-cleanup-object-changed")
                files.append((path, item[0], item[1]))
    return files, sorted(directories, key=lambda path: len(path.parts), reverse=True)


def _history_directories(root):
    parent = _adapter()._target(root, STATE + "/upgrades")
    if not parent.exists():
        return []
    result = []
    for visited, path in enumerate(parent.iterdir()):
        if visited >= MAX_HISTORY:
            _reject("upgrade-history-limit; retirement-not-complete")
        _adapter()._no_links(path)
        if not path.is_dir():
            _reject("upgrade-history-unknown-object")
        _transaction_id(path.name)
        # Empty directories left after a final metadata unlink are harmless.
        if not (path / "journal.json").exists() and not any(path.iterdir()):
            continue
        result.append(path)
    return sorted(result, key=lambda path: path.name)


def _retired_plan(directory, journal):
    expected = {"journal.json": _metadata_row(directory / "journal.json", journal)}
    pointer = directory / "pending-pointer.json"
    if pointer.exists():
        expected["pending-pointer.json"] = _metadata_row(pointer,
            dict(schema=POINTER_SCHEMA, transactionId=journal["transactionId"]))
    return _private_tree_plan(directory, expected)


def _prune_retired(root, current):
    retired = []
    for directory in _history_directories(root):
        header = _read_json(directory / "journal.json")
        if header.get("schema") == RETIRED_SCHEMA:
            _, journal = _load_journal(root, directory.name)
            files, directories = _retired_plan(directory, journal)
            if directories:
                _reject("upgrade-retired-directory-changed")
            retired.append((directory, files))
    # Keep the current pending transaction, then at most fifteen other summaries.
    retired.sort(key=lambda item: (item[0] == current, item[0].stat().st_mtime_ns), reverse=True)
    for directory, files in retired[MAX_RETIRED:]:
        for path, row, identity in sorted(files, key=lambda item: item[0].name == "journal.json"):
            if not _adapter()._rollback_published(path, tuple(identity), row["sha256"]):
                _reject("upgrade-retired-summary-cleanup-failed")
        directory.rmdir()
    _directory_sync(current.parent)


def _finish_retirement(root, directory, journal):
    _retired_plan(directory, journal)  # No payload or unknown objects may remain.
    _prune_retired(root, directory)
    try:
        _clear_pointer(root, journal["transactionId"], directory)
    except Exception:
        # An unlink may have succeeded before its durability check failed.
        # Re-arm the compact cleanup-only witness instead of reporting an
        # untracked failure or ever restoring installed resources.
        if _pending_id(root) is None:
            _pointer(root, journal["transactionId"], directory)
        raise
    return dict(status="upgrade-committed" if journal["status"] == "committed-retired" else "rolled-back",
                packageId=journal["packageId"], upgradeTransactionId=journal["transactionId"],
                temporaryCopiesRemaining=0, cleanupOnly=True, restartRequired=True)


def _cleanup_upgrade(root, directory, journal):
    """Terminal-only cleanup never reads or writes the installed payload paths."""
    adapter = _adapter()
    if journal["status"] in RETIRED_STATES:
        return _finish_retirement(root, directory, journal)
    if journal["status"] not in TERMINAL:
        _reject("upgrade-cleanup-not-terminal")
    journal = _embed_legacy_proof(root, directory, journal)
    expected = {"journal.json": _metadata_row(directory / "journal.json", journal),
        "pending-pointer.json": _metadata_row(directory / "pending-pointer.json",
            dict(schema=POINTER_SCHEMA, transactionId=journal["transactionId"]))}
    for entry in journal["entries"]:
        for side in ("new", "old"):
            row = entry[side]
            if row is not None:
                expected[("new/" if side == "new" else "backup/") + row["path"]] = (row, entry[side + "Identity"])
    paths, directories = _private_tree_plan(directory, expected)
    stage = adapter._target(root, journal["stage"]) if journal["stage"] else None
    if stage is not None:
        stage_expected = {row["path"]: (row, journal["stagedIdentities"][row["path"]]) for row in journal["files"]}
        receipt_entry = next(entry for entry in journal["entries"] if entry["new"] == journal["newReceipt"])
        stage_expected["upgrade-receipt.json"] = (journal["newReceipt"], receipt_entry["newIdentity"])
        stage_files, stage_dirs = _private_tree_plan(stage, stage_expected)
        paths.extend(stage_files)
        directories.extend(stage_dirs)
    for row in journal["cachedArchives"]:
        path = adapter._target(root, row["path"])
        if path.exists():
            if not _matches(path, row, row["identity"]):
                _reject("upgrade-cleanup-cache-changed")
            paths.append((path, row, row["identity"]))
    metadata = {directory / "journal.json", directory / "pending-pointer.json"}
    for path, row, identity in paths:
        if path not in metadata:
            if not adapter._rollback_published(path, tuple(identity), row["sha256"]):
                _reject("upgrade-cleanup-owned-file-unavailable")
            _directory_sync(path.parent)
    for path in directories:
        path.rmdir()  # Never recurse into an unknown/newly introduced object.
    if stage is not None and stage.exists():
        stage.rmdir()
        _directory_sync(stage.parent)
    # A compact durable terminal witness survives a crash before pointer removal.
    retired = dict(schema=RETIRED_SCHEMA, transactionId=journal["transactionId"],
                   packageId=journal["packageId"], rootIdentity=journal["rootIdentity"],
                   status=journal["status"] + "-retired")
    _metadata_row(directory / "journal.json", journal)
    _write_journal(directory, retired)
    return _finish_retirement(root, directory, retired)


def _retire_or_reject(root, directory, journal):
    try:
        return _cleanup_upgrade(root, directory, journal)
    except Exception as error:
        raise _adapter().ImportRejected("upgrade-cleanup-incomplete") from error


def retire_completed_upgrades(root):
    """Caller holds writer lock. Migrate A066 history before any runtime reader."""
    from resource_transaction import assert_resources_ready
    root = Path(os.path.abspath(root))
    assert_resources_ready(root)  # Never automatically resolve an active transaction.
    completed = 0
    for directory in _history_directories(root):
        if not directory.exists():  # Earlier retirement may have pruned a compact summary.
            continue
        directory, journal = _load_journal(root, directory.name)
        if journal["status"] in RETIRED_STATES:
            _retired_plan(directory, journal)
            continue
        if journal["status"] not in TERMINAL:
            _reject("upgrade-recovery-required")
        journal = _embed_legacy_proof(root, directory, journal)
        _pointer(root, journal["transactionId"], directory)
        _retire_or_reject(root, directory, journal)
        completed += 1
    return dict(status="completed-upgrades-retired", retiredTransactions=completed,
                temporaryCopiesRemaining=0)


def _receipt_committed(root, journal):
    entry = next(entry for entry in journal["entries"] if entry["new"] == journal["newReceipt"])
    return _matches(_adapter()._target(root, entry["new"]["path"]), entry["new"], entry["newIdentity"])


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
    return dict(_retire_or_reject(root, directory, journal), cleanupOnly=False)


def commit_upgrade(root: Path, result: dict, stage: Path, *, live=False, cancelled=None) -> dict:
    """Commit same-pack replacements under the caller's writer/idle-or-closed lease."""
    adapter = _adapter()
    if live and not adapter.supports_live_author_import(result):
        _reject("live-import-requires-restart")
    adapter._check_cancelled(cancelled)
    root, stage = Path(os.path.abspath(root)), Path(os.path.abspath(stage))
    adapter._no_links(root)
    adapter._no_links(stage)
    if _pending_id(root) is not None:
        _reject("pending-upgrade-recovery-required")
    package_id = result.get("packageId")
    if not isinstance(package_id, str) or not adapter.known_package_id(package_id):
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
        adapter._check_cancelled(cancelled)
        target = adapter._target(root, row["path"])
        if key in missing:
            if target.exists():
                _reject("upgrade-missing-target-now-exists")
        else:
            identities[key] = _capture(target, replacements.get(key, row))
    adapter._check_cancelled(cancelled)
    transaction_id = str(uuid.uuid4())
    directory = adapter._target(root, f"{STATE}/upgrades/{transaction_id}")
    output = dict(result, upgradeTransactionId=transaction_id, added=len(missing),
                  replaced=len(replacements), status="upgraded-restart-required")
    if live:
        output.update(restartRequired=False, liveImport=True, status="author-upgraded-live",
            authorFilesAdded=len(missing), authorFilesReplaced=len(replacements),
            authorFilesAlreadyPresent=sum(adapter._live_author_path(package_id, row['path'])
                for row in inventory.values()) - len(missing) - len(replacements))
    old_receipt_row = _receipt_row(package_id, previous_data)
    old_receipt_identity = _capture(receipt, old_receipt_row)
    new_receipt_data = _encoded(output)
    new_receipt_row = _receipt_row(package_id, new_receipt_data)
    entries = []
    # Prepare identities from the existing private stage before creating pins.
    for key in sorted(set(replacements) | set(missing)):
        row = inventory[key]
        source = adapter._target(stage, row["path"])
        if not _matches(source, row):
            _reject("upgrade-staged-data-changed")
        entries.append({"new": row, "old": replacements.get(key),
                        "newIdentity": _capture(source, row), "oldIdentity": identities.get(key)})
    new_receipt = adapter._target(stage, "upgrade-receipt.json")
    _write_new(new_receipt, new_receipt_data)
    entries.append({"new": new_receipt_row, "old": old_receipt_row,
                    "newIdentity": _capture(new_receipt, new_receipt_row),
                    "oldIdentity": old_receipt_identity})
    journal = {"schema": SCHEMA, "transactionId": transaction_id, "packageId": package_id,
               "rootIdentity": _identity(root.stat()), "status": "rolled-back", "files": list(inventory.values()),
               "entries": entries, "oldReceipt": old_receipt_row, "newReceipt": new_receipt_row,
               "blockedPaths": [], "receiptData": {"old": previous_data.decode("utf-8"),
                                                    "new": new_receipt_data.decode("utf-8")},
               "stage": stage.relative_to(root).as_posix(),
               "stagedIdentities": {row["path"]: _capture(adapter._target(stage, row["path"]), row)
                                    for row in inventory.values()},
               "cachedArchives": _cached_archives(root, package_id, (previous, output))}
    directory.mkdir(parents=True, exist_ok=False)
    adapter._no_links(directory)
    # No active writes or payload pins yet. A crash before pointer publication
    # is a completed no-op whose private stage may be safely retired at startup.
    _write_journal(directory, journal)
    _pointer(root, transaction_id, directory)
    try:
        journal["status"] = "preparing"
        _write_journal(directory, journal)
        for entry in entries:
            adapter._check_cancelled(cancelled)
            row = entry["new"]
            source = new_receipt if row == new_receipt_row else adapter._target(stage, row["path"])
            retained = adapter._target(directory, "new/" + row["path"])
            retained.parent.mkdir(parents=True, exist_ok=True)
            adapter._no_links(retained.parent)
            if not _matches(source, row, entry["newIdentity"]):
                _reject("upgrade-staged-data-changed")
            os.link(source, retained)
            with retained.open("r+b") as handle:
                os.fsync(handle.fileno())
            _directory_sync(retained.parent)
        journal["status"] = "prepared"
        _write_journal(directory, journal)
        for entry in entries:
            # Receipt publication is the commit point. Before it, cancellation
            # restores the entire old inventory/receipt using the same journal.
            adapter._check_cancelled(cancelled)
            row = entry["new"]
            target = adapter._target(root, row["path"])
            if row["path"] == new_receipt_row["path"]:
                for current in inventory.values():
                    adapter._check_cancelled(cancelled)
                    if not _matches(adapter._target(root, current["path"]), current):
                        _reject("upgrade-data-changed-before-receipt-publication")
            adapter._check_cancelled(cancelled)
            if entry["old"] is not None:
                backup = adapter._target(directory, "backup/" + row["path"])
                _move_owned(target, backup, entry["old"], entry["oldIdentity"])
            target.parent.mkdir(parents=True, exist_ok=True)
            adapter._no_links(target.parent)
            retained = adapter._target(directory, "new/" + row["path"])
            if not _matches(retained, row, entry["newIdentity"]):
                _reject("upgrade-retained-new-object-changed")
            adapter._check_cancelled(cancelled)
            os.link(retained, target)
            if not _matches(target, row, entry["newIdentity"]):
                _reject("upgrade-published-object-changed")
            _directory_sync(target.parent)
        journal["status"] = "committed"
        _write_journal(directory, journal)
    except Exception as error:
        # A published final receipt or durable committed journal is irreversible.
        # Failure after either witness leaves only cleanup for the next helper.
        durable = _read_json(directory / "journal.json")
        if durable["status"] == "committed" or _receipt_committed(root, journal):
            if durable["status"] != "committed":
                journal["status"] = "committed"
                _write_journal(directory, journal)
            raise adapter.ImportRejected("upgrade-cleanup-incomplete") from error
        try:
            if durable["status"] == "preparing":
                journal["status"] = "rolled-back"
                _write_journal(directory, journal)
                _retire_or_reject(root, directory, journal)
            else:
                _rollback(root, directory, journal)
        except Exception as rollback_error:
            raise adapter.ImportRejected(f"upgrade-failed-and-rollback-incomplete; transaction={transaction_id}; "
                                         f"{rollback_error}") from error
        raise
    _retire_or_reject(root, directory, journal)
    return dict(output, temporaryCopiesRemaining=0)


def recover_pending_upgrade(root: Path) -> dict:
    """Explicit process-loss recovery; caller owns import lock and app closure."""
    root = Path(os.path.abspath(root))
    transaction_id = _pending_id(root)
    if transaction_id is None:
        return {"status": "no-pending-upgrade"}
    directory, journal = _load_journal(root, transaction_id)
    if journal["status"] in TERMINAL | RETIRED_STATES:
        return _retire_or_reject(root, directory, journal)
    journal = _embed_legacy_proof(root, directory, journal)
    if _receipt_committed(root, journal):
        journal["status"] = "committed"
        _write_journal(directory, journal)
        return _retire_or_reject(root, directory, journal)
    if journal["status"] == "preparing":
        journal["status"] = "rolled-back"
        _write_journal(directory, journal)
        return dict(_retire_or_reject(root, directory, journal), cleanupOnly=False)
    return _rollback(root, directory, journal)


def rollback_upgrade(root: Path, transaction_id: str) -> dict:
    """Compatibility entry: completed upgrades are no longer a rollback library."""
    root = Path(os.path.abspath(root))
    transaction_id = _transaction_id(transaction_id)
    pending = _pending_id(root)
    if pending is not None and pending != transaction_id:
        _reject("another-pending-upgrade-recovery-required")
    directory, journal = _load_journal(root, transaction_id)
    if journal["status"] in TERMINAL | RETIRED_STATES or _receipt_committed(root, journal):
        _reject("upgrade-transaction-no-longer-rollbackable")
    if pending is None:
        _pointer(root, transaction_id, directory)
    return recover_pending_upgrade(root)


def retire_upgrade(root, transaction_id):
    """Explicit plan-bound cleanup of an A066 completed transaction."""
    pending = _pending_id(root)
    if pending is not None and pending != transaction_id:
        _reject("another-pending-upgrade-recovery-required")
    directory, journal = _load_journal(root, transaction_id)
    if journal["status"] not in TERMINAL | RETIRED_STATES and _receipt_committed(root, journal):
        journal = _embed_legacy_proof(root, directory, journal)
        journal["status"] = "committed"
        _write_journal(directory, journal)
    if journal["status"] not in TERMINAL | RETIRED_STATES:
        _reject("upgrade-cleanup-not-terminal")
    if journal["status"] not in RETIRED_STATES:
        journal = _embed_legacy_proof(root, directory, journal)
    if pending is None:
        _pointer(root, transaction_id, directory)
    return _retire_or_reject(root, directory, journal)


ADDITIONS_PENDING = STATE + '/pending-additions.json'
ADDITIONS_SCHEMA = 'map-package-additions-v1'
ADDITIONS_OUTCOME = 'addition-outcome.json'
ADDITIONS_OUTCOME_SCHEMA = 'map-package-additions-outcome-v1'


def prepare_additions(root, result, stage, *, live=False):
    """Persist witnesses before the first additive publication (including crashes)."""
    adapter = _adapter()
    entries = []
    for row in result['missingFiles']:
        if adapter._target(root, row['path']).exists():
            _reject('target-changed-during-import')
        source = adapter._target(stage, row['path'])
        entries.append({'new': row, 'newIdentity': _capture(source, row)})
    if not entries:
        return False
    transaction_id = str(uuid.uuid4())
    result['additionTransactionId'] = transaction_id
    receipt = adapter._target(root, f"{STATE}/{result['packageId']}.json")
    if receipt.exists():
        adapter._owned_inventory(root, result['packageId'])
    expected_result = dict(result)
    adapter._set_addition_outcome(expected_result, len(entries), live=live)
    receipt_data = _encoded(expected_result)
    journal = dict(schema=ADDITIONS_SCHEMA, packageId=result['packageId'], transactionId=transaction_id,
        stage=stage.relative_to(root).as_posix(), rootIdentity=_identity(root.stat()),
        files=result['files'], entries=entries,
        stagedIdentities={row['path']: _capture(adapter._target(stage, row['path']), row) for row in result['files']},
        newReceipt=dict(path='receipt.json', bytes=len(receipt_data), sha256=hashlib.sha256(receipt_data).hexdigest()),
        oldReceiptSha256=adapter._sha(receipt) if receipt.exists() else None)
    retained = adapter._target(stage, 'addition-journal.json')
    _write_new(retained, _encoded(journal))
    os.link(retained, adapter._target(root, ADDITIONS_PENDING))
    _directory_sync(retained.parent)
    _directory_sync(adapter._target(root, STATE))
    return True


def _addition_outcome(stage, retained, journal):
    """An immutable terminal witness survives partial private-payload cleanup."""
    adapter = _adapter()
    path = adapter._target(stage, ADDITIONS_OUTCOME)
    if not path.exists():
        return None
    value = _read_json(path)
    if (set(value) != {'schema', 'transactionId', 'journalSha256', 'status'}
            or value['schema'] != ADDITIONS_OUTCOME_SCHEMA
            or value['transactionId'] != journal['transactionId']
            or value['journalSha256'] != adapter._sha(retained)
            or value['status'] not in {'committed', 'rolled-back'}):
        _reject('additions-outcome-witness-changed')
    return value


def _addition_journal(root):
    adapter = _adapter()
    path = adapter._target(root, ADDITIONS_PENDING)
    if not path.exists():
        return None
    journal = _read_json(path)
    if (set(journal) != {'schema', 'packageId', 'transactionId', 'stage', 'rootIdentity',
                         'files', 'entries', 'oldReceiptSha256', 'stagedIdentities', 'newReceipt'}
            or journal['schema'] != ADDITIONS_SCHEMA
            or not isinstance(journal['packageId'], str) or not adapter.known_package_id(journal['packageId'])
            or _valid_identity(journal['rootIdentity']) != _identity(root.stat())):
        _reject('invalid-additions-journal')
    _transaction_id(journal['transactionId'])
    stage_name = journal['stage']
    if not isinstance(stage_name, str) or not re.fullmatch(r'work/map-packages/staging-[a-zA-Z0-9_-]+', stage_name):
        _reject('invalid-additions-stage')
    stage = adapter._target(root, stage_name)
    retained = adapter._target(stage, 'addition-journal.json')
    if _read_json(retained) != journal or _identity(retained.stat()) != _identity(path.stat()):
        _reject('additions-journal-witness-changed')
    outcome = _addition_outcome(stage, retained, journal)
    inventory = _rows(journal['files'], journal['packageId'])
    identities = journal['stagedIdentities']
    if not isinstance(identities, dict) or set(identities) != {row['path'] for row in inventory.values()}:
        _reject('invalid-additions-staged-identities')
    for row in inventory.values():
        _valid_identity(identities[row['path']])
        staged = adapter._target(stage, row['path'])
        if (outcome is None or staged.exists()) and not _matches(staged, row, identities[row['path']]):
            _reject('additions-retained-object-changed')
    receipt_row = journal['newReceipt']
    if (not isinstance(receipt_row, dict) or set(receipt_row) != {'path', 'bytes', 'sha256'}
            or receipt_row['path'] != 'receipt.json' or type(receipt_row['bytes']) is not int
            or not 0 <= receipt_row['bytes'] <= MAX_JOURNAL_BYTES):
        _reject('invalid-additions-receipt-record')
    _digest(receipt_row['sha256'])
    entries = journal['entries']
    if not isinstance(entries, list) or not 1 <= len(entries) <= len(inventory):
        _reject('invalid-additions-entry-count')
    found = set()
    for entry in entries:
        if not isinstance(entry, dict) or set(entry) != {'new', 'newIdentity'}:
            _reject('invalid-additions-entry')
        rows = _rows([entry['new']], journal['packageId'])
        key, row = next(iter(rows.items()))
        if key in found or inventory.get(key) != row:
            _reject('invalid-additions-entry')
        found.add(key)
        _valid_identity(entry['newIdentity'])
        staged = adapter._target(stage, row['path'])
        if (entry['newIdentity'] != identities[row['path']]
                or (outcome is None or staged.exists()) and not _matches(staged, row, entry['newIdentity'])):
            _reject('additions-retained-object-changed')
    if journal['oldReceiptSha256'] is not None:
        _digest(journal['oldReceiptSha256'])
    return stage, retained, journal


def _addition_cleanup_plan(stage, retained, journal):
    """Validate the whole private stage before deleting any of its objects."""
    adapter = _adapter()
    expected = {row['path']: (row, journal['stagedIdentities'][row['path']]) for row in journal['files']}
    expected['receipt.json'] = (journal['newReceipt'], None)
    outcome = _addition_outcome(stage, retained, journal)
    if outcome is not None:
        path = adapter._target(stage, ADDITIONS_OUTCOME)
        expected[ADDITIONS_OUTCOME] = (dict(path=ADDITIONS_OUTCOME, bytes=path.stat().st_size,
            sha256=adapter._sha(path)), _identity(path.stat()))
    expected['addition-journal.json'] = (dict(path='addition-journal.json', bytes=retained.stat().st_size,
                                            sha256=adapter._sha(retained)), _identity(retained.stat()))
    allowed_dirs = {stage}
    for name in expected:
        parent = stage / name
        allowed_dirs.update(p for p in parent.parents if p.is_relative_to(stage))
    files, directories = [], []
    for path in stage.rglob('*'):
        adapter._no_links(path)
        if path.is_dir():
            if path not in allowed_dirs:
                _reject('additions-stage-cleanup-conflict')
            directories.append(path)
        else:
            name = path.relative_to(stage).as_posix()
            item = expected.get(name)
            if item is None or not _matches(path, item[0], item[1]):
                _reject('additions-stage-cleanup-conflict')
            files.append((path, tuple(_identity(path.stat())), item[0]['sha256']))
    return files, sorted(directories, key=lambda p: len(p.parts), reverse=True)


def recover_pending_additions(root):
    """Caller owns writer/closed lease; preserve unknown replacements on recovery."""
    adapter = _adapter()
    loaded = _addition_journal(root)
    if loaded is None:
        return {'status': 'no-pending-additions'}
    stage, retained, journal = loaded
    outcome = _addition_outcome(stage, retained, journal)
    if outcome is None:
        receipt = adapter._target(root, f"{STATE}/{journal['packageId']}.json")
        current = _read_json(receipt) if receipt.exists() else None
        committed = (current is not None and current.get('additionTransactionId') == journal['transactionId']
                     and current.get('packageId') == journal['packageId']
                     and current.get('files') == journal['files'])
        if committed:
            if any(not _matches(adapter._target(root, row['path']), row) for row in journal['files']):
                _reject('additions-committed-data-changed')
        else:
            if (adapter._sha(receipt) if receipt.exists() else None) != journal['oldReceiptSha256']:
                _reject('additions-receipt-changed')
            # Inspect the entire set first; leave all objects untouched on conflicts.
            for entry in journal['entries']:
                path = adapter._target(root, entry['new']['path'])
                if path.exists() and not _matches(path, entry['new'], entry['newIdentity']):
                    _reject('additions-rollback-conflict')
            for entry in reversed(journal['entries']):
                row = entry['new']
                path = adapter._target(root, row['path'])
                if path.exists() and not adapter._rollback_published(path, tuple(entry['newIdentity']), row['sha256']):
                    _reject('additions-rollback-incomplete')
        # Publication/rollback is now complete. Persist this before deleting
        # any witnesses, so a retry never replays business mutations or requires
        # already cleaned staging files/current user objects to remain unchanged.
        outcome = dict(schema=ADDITIONS_OUTCOME_SCHEMA, transactionId=journal['transactionId'],
            journalSha256=adapter._sha(retained), status='committed' if committed else 'rolled-back')
        _write_new(adapter._target(stage, ADDITIONS_OUTCOME), _encoded(outcome))
    committed = outcome['status'] == 'committed'
    files, directories = _addition_cleanup_plan(stage, retained, journal)
    # Keep both linked journal witnesses and terminal outcome until all staged
    # payload/receipt copies and their subdirectories have been cleaned.
    outcome_path = adapter._target(stage, ADDITIONS_OUTCOME)
    metadata = {retained, outcome_path}
    incomplete = False
    for path, identity, digest in files:
        if path in metadata:
            continue
        if not adapter._rollback_published(path, identity, digest):
            incomplete = True
        _directory_sync(path.parent)
    for directory in directories:
        try:
            directory.rmdir()  # Never recursively remove a newly introduced object.
        except OSError:
            incomplete = True
    if incomplete:
        raise adapter.ImportRejected('additions-stage-cleanup-incomplete', details={'stage': journal['stage']})
    # Revalidate the complete stage: a concurrent unknown object cannot become
    # an untracked successful cleanup merely because it appeared after planning.
    remaining, remaining_directories = _addition_cleanup_plan(stage, retained, journal)
    if remaining_directories or {row[0] for row in remaining} != metadata:
        _reject('additions-stage-cleanup-incomplete')
    pending = adapter._target(root, ADDITIONS_PENDING)
    if not adapter._rollback_published(pending, tuple(_identity(retained.stat())), adapter._sha(retained)):
        _reject('additions-pointer-could-not-be-cleared')
    # No payload copies remain when the gate is cleared. Only redundant terminal
    # text/empty directories may remain after a best-effort metadata retirement.
    try:
        _directory_sync(pending.parent)
        by_path = {path: (identity, digest) for path, identity, digest in remaining}
        for path in (outcome_path, retained):
            if not adapter._rollback_published(path, *by_path[path]):
                break
        else:
            stage.rmdir()
    except (OSError, adapter.ImportRejected):
        pass
    return {'status': 'additions-committed' if committed else 'additions-rolled-back',
            'packageId': journal['packageId'], 'additionTransactionId': journal['transactionId'],
            'restartRequired': True}
