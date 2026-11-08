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


def _move_owned(source, destination, row, identity, *, linkless=False):
    """Move only the verified old object, exclusively, retaining no-overwrite."""
    adapter = _adapter()
    adapter._no_links(source)
    adapter._no_links(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    adapter._no_links(destination.parent)
    if os.name != "nt":
        if not _matches(source, row, identity):
            _reject("upgrade-owned-target-changed-before-backup")
        # The move-only protocol keeps this object's identity in a durable
        # journal instead of requiring a second hard link. POSIX still has its
        # documented final pathname race; verify the moved object afterward.
        if linkless:
            from resource_transaction import rename_no_replace
            rename_no_replace(source, destination)
        else:
            os.link(source, destination)
        if not _matches(destination, row, identity):
            _reject("upgrade-backup-object-changed")
        if not linkless and not adapter._rollback_published(source, tuple(identity), row["sha256"]):
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



PUBLICATION_WITNESS_SCHEMA = "resource-metadata-publication-v1"


def _publication_mode(journal):
    mode = journal.get("publicationMode", "hardlink-v1")
    if mode not in {"hardlink-v1", "move-v1"}:
        _reject("invalid-resource-publication-mode")
    return mode


def _publication_paths(retained):
    return (retained.with_name(retained.name + ".publication.json"),
            retained.with_name(retained.name + ".publishing"))


def _publication_proof(retained):
    """Durable identity proof for a metadata copy; payload ownership is separate."""
    proof, source = _publication_paths(retained)
    value = _read_json(proof)
    _validate_publication_proof(value)
    if not _matches(retained, value, value["retainedIdentity"]):
        _reject("metadata-publication-retained-changed")
    if source.exists() and not _matches(source, value, value["identity"]):
        _reject("metadata-publication-source-changed")
    return value


def _validate_publication_proof(value):
    if (not isinstance(value, dict) or set(value) != {"schema", "retainedIdentity", "identity", "bytes", "sha256"}
            or value["schema"] != PUBLICATION_WITNESS_SCHEMA
            or type(value["bytes"]) is not int or not 0 <= value["bytes"] <= MAX_JOURNAL_BYTES):
        _reject("invalid-metadata-publication-witness")
    _valid_identity(value["identity"])
    _valid_identity(value["retainedIdentity"])
    _digest(value["sha256"])
    return value


def _publish_metadata(retained, target, mode, *, expected_data=None, before_publish=None):
    """Resume only canonical control declarations; never infer payload ownership.

    Before the proof exists, an unpublished source is only a metadata encoding.
    Its complete bytes must equal this caller's canonical transaction statement.
    Once a complete proof exists, its recorded source identity is mandatory.
    """
    if mode == "hardlink-v1":
        os.link(retained, target)
        _directory_sync(target.parent)
        return
    if mode != "move-v1":
        _reject("invalid-resource-publication-mode")
    if not isinstance(expected_data, bytes) or _read_bytes(retained) != expected_data:
        _reject("metadata-publication-expected-bytes-changed")
    row = dict(bytes=len(expected_data), sha256=hashlib.sha256(expected_data).hexdigest())
    proof, source = _publication_paths(retained)
    temporary = proof.with_name(proof.name + ".tmp")

    def statement(value):
        _validate_publication_proof(value)
        if (value['bytes'] != row['bytes'] or value['sha256'] != row['sha256']
                or not _matches(retained, row, value['retainedIdentity'])):
            _reject("metadata-publication-retained-changed")
        return value

    if target.exists():
        if source.exists() or temporary.exists():
            _reject("metadata-publication-already-pending")
        _metadata_publication_identity(retained, target, mode)
        return

    # A completely persisted temporary proof is already an identity declaration,
    # even if its final rename was interrupted. Do not recapture its source.
    if temporary.exists():
        value = statement(_read_json(temporary))
        if not _matches(source, row, value['identity']):
            _reject("metadata-publication-source-changed")
        temporary_row, temporary_identity = _metadata_row(temporary, value)
        if proof.exists():
            previous = statement(_read_json(proof))
            previous_row, previous_identity = _metadata_row(proof, previous)
            if not _adapter()._rollback_published(proof, tuple(previous_identity), previous_row['sha256']):
                _reject("metadata-publication-witness-changed")
    elif proof.exists():
        value = statement(_publication_proof(retained))
        if source.exists():
            # Resume the original prepared marker using its original identity.
            if before_publish is not None:
                before_publish(value, _identity(proof.stat()))
            _move_owned(source, target, row, value['identity'], linkless=True)
            return
        # A terminal transaction may rearm after marker removal. Retire only
        # this verified old declaration before creating the next source, so an
        # interruption cannot leave a new source paired with the old identity.
        previous_row, previous_identity = _metadata_row(proof, value)
        if not _adapter()._rollback_published(proof, tuple(previous_identity), previous_row['sha256']):
            _reject("metadata-publication-witness-changed")
        _directory_sync(proof.parent)

    if not temporary.exists():
        if source.exists():
            # This is restricted to the exact complete canonical control JSON.
            # Partial, edited, unknown or payload bytes never gain authority here.
            if _read_bytes(source) != expected_data:
                _reject("metadata-publication-unrecorded-source-changed")
        else:
            _write_new(source, expected_data)
        value = dict(schema=PUBLICATION_WITNESS_SCHEMA, retainedIdentity=_capture(retained, row),
                     identity=_capture(source, row), **row)
        _write_new(temporary, _encoded(value))
        temporary_row, temporary_identity = _metadata_row(temporary, value)
    _move_owned(temporary, proof, temporary_row, temporary_identity, linkless=True)
    if not _matches(proof, temporary_row, temporary_identity):
        _reject("metadata-publication-witness-changed")
    _directory_sync(proof.parent)
    if before_publish is not None:
        before_publish(value, temporary_identity)
    _move_owned(source, target, row, value["identity"], linkless=True)


def _metadata_publication_identity(retained, target, mode):
    if mode == "hardlink-v1":
        identity = _identity(retained.stat())
        row = dict(bytes=retained.stat().st_size, sha256=_adapter()._sha(retained))
    else:
        row = _publication_proof(retained)
        identity = row["identity"]
    if not _matches(target, row, identity):
        _reject("metadata-publication-target-changed")
    return identity


def _publication_cleanup_records(retained, mode):
    if mode != "move-v1":
        return {}
    value = _publication_proof(retained)
    proof, source = _publication_paths(retained)
    return {proof.name: _metadata_row(proof, value), source.name: (value, value["identity"])}

def _pointer(root, transaction_id, directory):
    adapter = _adapter()
    # Explicit rollback also creates a pointer, independently of installation.
    journal = _read_json(adapter._target(directory, "journal.json"))
    mode = _publication_mode(journal)
    if mode == "hardlink-v1":
        adapter.preflight_hardlinks(root, [PENDING])
    value = {"schema": POINTER_SCHEMA, "transactionId": transaction_id}
    retained = adapter._target(directory, "pending-pointer.json")
    if not retained.exists():
        _write_new(retained, _encoded(value))
    elif _read_json(retained) != value:
        _reject("upgrade-pointer-source-changed")
    pending = adapter._target(root, PENDING)
    def persist_retired_witness(value, identity):
        if journal.get('schema') == RETIRED_SCHEMA:
            journal['publicationWitness'] = dict(value=value, identity=identity)
            _write_journal(directory, journal)
    _publish_metadata(retained, pending, mode, expected_data=_encoded(value), before_publish=persist_retired_witness)
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
    mode = _publication_mode(_read_json(directory / "journal.json"))
    identity = _metadata_publication_identity(retained, path, mode)
    if not adapter._rollback_published(path, tuple(identity), adapter._sha(retained)):
        _reject("pending-upgrade-pointer-could-not-be-cleared")
    _directory_sync(path.parent)


def _load_journal(root, transaction_id):
    adapter = _adapter()
    transaction_id = _transaction_id(transaction_id)
    directory = adapter._target(root, f"{STATE}/upgrades/{transaction_id}")
    journal = _read_json(adapter._target(directory, "journal.json"))
    if _pending_id(root) == transaction_id:
        _metadata_publication_identity(adapter._target(directory, "pending-pointer.json"),
            adapter._target(root, PENDING), _publication_mode(journal))
    if journal.get("schema") == RETIRED_SCHEMA:
        expected_keys = {"schema", "transactionId", "packageId", "rootIdentity", "status"}
        if "publicationMode" in journal:
            expected_keys.add("publicationMode")
        if _publication_mode(journal) == "move-v1":
            expected_keys.add("publicationWitness")
            witness = journal.get("publicationWitness")
            if not isinstance(witness, dict) or set(witness) != {"value", "identity"}:
                _reject("invalid-upgrade-retired-publication-witness")
            _validate_publication_proof(witness["value"])
            _valid_identity(witness["identity"])
        if (set(journal) != expected_keys
                or journal["transactionId"] != transaction_id
                or not adapter.known_package_id(journal["packageId"])
                or journal["status"] not in RETIRED_STATES
                or _valid_identity(journal["rootIdentity"]) != _identity(root.stat())
                or len(_encoded(journal)) > 1024):
            _reject("invalid-upgrade-retired-journal")
        _publication_mode(journal)
        return directory, journal
    legacy_keys = {"schema", "transactionId", "packageId", "rootIdentity", "status", "files",
                   "entries", "oldReceipt", "newReceipt", "blockedPaths"}
    mode = _publication_mode(journal)
    keys = set(journal) - {"publicationMode"}
    extended = keys == legacy_keys | {"receiptData", "stage", "stagedIdentities", "cachedArchives"}
    if ((keys != legacy_keys and not extended) or (mode == "move-v1" and not extended)
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
        may_be_missing = (mode == "move-v1" or extended and journal["status"] in TERMINAL | {"preparing"})
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
    if _publication_mode(journal) == "move-v1":
        witness = journal["publicationWitness"]
        value = _validate_publication_proof(witness["value"])
        data = _encoded(dict(schema=POINTER_SCHEMA, transactionId=journal["transactionId"]))
        if value['bytes'] != len(data) or value['sha256'] != hashlib.sha256(data).hexdigest():
            _reject("invalid-upgrade-retired-publication-witness")
        expected["pending-pointer.json"] = (value, value["retainedIdentity"])
        proof, source = _publication_paths(pointer)
        encoded = _encoded(value)
        expected[proof.name] = (dict(bytes=len(encoded), sha256=hashlib.sha256(encoded).hexdigest()), witness["identity"])
        expected[source.name] = (value, value["identity"])
    elif pointer.exists():
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
    expected.update(_publication_cleanup_records(directory / "pending-pointer.json", _publication_mode(journal)))
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
    metadata = {directory / "journal.json", directory / "pending-pointer.json",
                *_publication_paths(directory / "pending-pointer.json")}
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
    if "publicationMode" in journal:
        retired["publicationMode"] = journal["publicationMode"]
    if _publication_mode(journal) == "move-v1":
        pointer = directory / "pending-pointer.json"
        value = _publication_proof(pointer)
        proof, _ = _publication_paths(pointer)
        retired["publicationWitness"] = dict(value=value, identity=_identity(proof.stat()))
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
                if ((_publication_mode(journal) != "move-v1" and not _matches(retained, new, entry["newIdentity"]))
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
                if _publication_mode(journal) == "move-v1":
                    _move_owned(path, retained, new, entry["newIdentity"], linkless=True)
                elif (not _matches(retained, new, entry["newIdentity"])
                      or not adapter._rollback_published(path, tuple(entry["newIdentity"]), new["sha256"])):
                    blocked.append(new["path"])
                    continue
                _directory_sync(path.parent)
            if old is not None:
                path.parent.mkdir(parents=True, exist_ok=True)
                adapter._no_links(path.parent)
                if _publication_mode(journal) == "move-v1":
                    _move_owned(backup, path, old, entry["oldIdentity"], linkless=True)
                else:
                    os.link(backup, path)  # Legacy restoration retains its linked witness.
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


def commit_upgrade(root: Path, result: dict, stage: Path, *, live=False, cancelled=None, publication_mode=None) -> dict:
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
    if publication_mode is None:
        from resource_transaction import preflight_resource_publication
        publication_mode = preflight_resource_publication(root, [r["path"] for r in result["files"]] + [PENDING])
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
    final_identities = dict(identities)
    final_identities.update({entry['new']['path'].casefold(): entry['newIdentity'] for entry in entries})
    journal = {"schema": SCHEMA, "publicationMode": publication_mode, "transactionId": transaction_id, "packageId": package_id,
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
            if publication_mode == "move-v1":
                _move_owned(source, retained, row, entry["newIdentity"], linkless=True)
            else:
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
                    if not _matches(adapter._target(root, current["path"]), current,
                                    final_identities[current['path'].casefold()]):
                        _reject("upgrade-data-changed-before-receipt-publication")
            adapter._check_cancelled(cancelled)
            if entry["old"] is not None:
                backup = adapter._target(directory, "backup/" + row["path"])
                _move_owned(target, backup, entry["old"], entry["oldIdentity"], linkless=publication_mode == "move-v1")
            target.parent.mkdir(parents=True, exist_ok=True)
            adapter._no_links(target.parent)
            retained = adapter._target(directory, "new/" + row["path"])
            if not _matches(retained, row, entry["newIdentity"]):
                _reject("upgrade-retained-new-object-changed")
            adapter._check_cancelled(cancelled)
            if publication_mode == "move-v1":
                _move_owned(retained, target, row, entry["newIdentity"], linkless=True)
            else:
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
        # Rearming a compact move-v1 terminal journal persists a fresh marker
        # witness. Use that durable generation rather than our previous copy.
        directory, journal = _load_journal(root, transaction_id)
    return _retire_or_reject(root, directory, journal)


ADDITIONS_PENDING = STATE + '/pending-additions.json'
ADDITIONS_SCHEMA = 'map-package-additions-v1'
ADDITIONS_OUTCOME = 'addition-outcome.json'
ADDITIONS_OUTCOME_SCHEMA = 'map-package-additions-outcome-v1'


def prepare_additions(root, result, stage, *, live=False, publication_mode="hardlink-v1"):
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
    journal = dict(schema=ADDITIONS_SCHEMA, publicationMode=publication_mode, packageId=result['packageId'], transactionId=transaction_id,
        stage=stage.relative_to(root).as_posix(), rootIdentity=_identity(root.stat()),
        files=result['files'], entries=entries,
        stagedIdentities={row['path']: _capture(adapter._target(stage, row['path']), row) for row in result['files']},
        newReceipt=dict(path='receipt.json', bytes=len(receipt_data), sha256=hashlib.sha256(receipt_data).hexdigest()),
        oldReceiptSha256=adapter._sha(receipt) if receipt.exists() else None)
    if publication_mode == 'move-v1':
        staged_receipt = adapter._target(stage, 'receipt.json')
        _write_new(staged_receipt, receipt_data)
        journal['newReceiptIdentity'] = _capture(staged_receipt, journal['newReceipt'])
        journal['oldReceipt'] = None
        if receipt.exists():
            old = dict(path='old-receipt.json', bytes=receipt.stat().st_size, sha256=adapter._sha(receipt))
            journal['oldReceipt'] = dict(old, identity=_capture(receipt, old))
    retained = adapter._target(stage, 'addition-journal.json')
    _write_new(retained, _encoded(journal))
    _publish_metadata(retained, adapter._target(root, ADDITIONS_PENDING), publication_mode, expected_data=_encoded(journal))
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


def _addition_journal(root, *, retained=None, verify_files=True):
    adapter = _adapter()
    unpublished = retained is not None
    path = Path(retained) if unpublished else adapter._target(root, ADDITIONS_PENDING)
    if not path.exists():
        return None
    journal = _read_json(path)
    mode = _publication_mode(journal)
    expected_keys = {'schema', 'packageId', 'transactionId', 'stage', 'rootIdentity',
                     'files', 'entries', 'oldReceiptSha256', 'stagedIdentities', 'newReceipt'}
    if 'publicationMode' in journal:
        expected_keys.add('publicationMode')
    if mode == 'move-v1':
        expected_keys.update({'newReceiptIdentity', 'oldReceipt'})
    if (set(journal) != expected_keys
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
    if unpublished and path != retained:
        _reject('invalid-additions-stage')
    if _read_json(retained) != journal:
        _reject('additions-journal-witness-changed')
    if not unpublished:
        _metadata_publication_identity(retained, path, mode)
    outcome = _addition_outcome(stage, retained, journal)
    inventory = _rows(journal['files'], journal['packageId'])
    identities = journal['stagedIdentities']
    if not isinstance(identities, dict) or set(identities) != {row['path'] for row in inventory.values()}:
        _reject('invalid-additions-staged-identities')
    for row in inventory.values():
        _valid_identity(identities[row['path']])
        if verify_files:
            staged = adapter._target(stage, row['path'])
            if (staged.exists() or outcome is None and mode != 'move-v1') and not _matches(staged, row, identities[row['path']]):
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
        if entry['newIdentity'] != identities[row['path']]:
            _reject('additions-retained-object-changed')
        if verify_files:
            staged = adapter._target(stage, row['path'])
            if (staged.exists() or outcome is None and mode != 'move-v1') and not _matches(staged, row, entry['newIdentity']):
                _reject('additions-retained-object-changed')
    if journal['oldReceiptSha256'] is not None:
        _digest(journal['oldReceiptSha256'])
    if mode == 'move-v1':
        _valid_identity(journal['newReceiptIdentity'])
        old = journal['oldReceipt']
        if old is not None:
            if (not isinstance(old, dict) or set(old) != {'path', 'identity', 'bytes', 'sha256'}
                    or old['path'] != 'old-receipt.json' or type(old['bytes']) is not int
                    or not 0 <= old['bytes'] <= MAX_JOURNAL_BYTES
                    or old['sha256'] != journal['oldReceiptSha256']):
                _reject('invalid-additions-old-receipt')
            _valid_identity(old['identity'])
        elif journal['oldReceiptSha256'] is not None:
            _reject('invalid-additions-old-receipt')
    return stage, retained, journal




def _legacy_addition_declaration(root, stage, retained, journal):
    """Validate only the two pre-mode history shapes already read by removal.

    Those producers/compatibility records have no terminal-outcome requirement:
    the old producer cleared its pending link before private cleanup. Absence
    of the link does not distinguish a prepared record from retained history.
    This read-only compatibility check grants no new recovery or deletion power.
    """
    adapter = _adapter()
    required = {'schema', 'packageId', 'transactionId', 'stage', 'rootIdentity',
                'files', 'entries', 'oldReceiptSha256'}
    extended = required | {'stagedIdentities', 'newReceipt'}
    if (not isinstance(journal, dict) or set(journal) not in (required, extended)
            or journal['schema'] != ADDITIONS_SCHEMA
            or not isinstance(journal['packageId'], str) or not adapter.known_package_id(journal['packageId'])
            or _valid_identity(journal['rootIdentity']) != _identity(root.stat())
            or journal['stage'] != stage.relative_to(root).as_posix()
            or not re.fullmatch(r'work/map-packages/staging-[a-zA-Z0-9_-]+', journal['stage'])):
        _reject('invalid-legacy-additions-history')
    _transaction_id(journal['transactionId'])
    inventory = _rows(journal['files'], journal['packageId'])
    entries = journal['entries']
    if not isinstance(entries, list) or not 1 <= len(entries) <= len(inventory):
        _reject('invalid-legacy-additions-history')
    seen = set()
    for entry in entries:
        if not isinstance(entry, dict) or set(entry) != {'new', 'newIdentity'}:
            _reject('invalid-legacy-additions-history')
        rows = _rows([entry['new']], journal['packageId'])
        key, row = next(iter(rows.items()))
        if key in seen or inventory.get(key) != row:
            _reject('invalid-legacy-additions-history')
        seen.add(key)
        _valid_identity(entry['newIdentity'])
    if journal['oldReceiptSha256'] is not None:
        _digest(journal['oldReceiptSha256'])
    if set(journal) == extended:
        # The ten-field producer form has stronger complete-stage declarations;
        # use the shared parser without inspecting or adopting current payloads.
        _addition_journal(root, retained=retained, verify_files=False)
    else:
        _addition_outcome(stage, retained, journal)  # If present, it must be valid.

def prepared_additions(root):
    """Read only bounded staging control records, never image/payload bytes.

    A journal without its terminal outcome is unfinished even before the global
    marker exists. Discovery grants no deletion or live-resource authority.
    """
    adapter = _adapter()
    state = adapter._target(root, STATE)
    if not state.exists():
        return []
    pending = adapter._target(root, ADDITIONS_PENDING)
    active = _read_json(pending).get('transactionId') if pending.exists() else None
    result = []
    for count, stage in enumerate(state.iterdir()):
        if count >= MAX_HISTORY:
            _reject('additions-history-limit')
        if not re.fullmatch(r'staging-[a-zA-Z0-9_-]+', stage.name):
            continue
        adapter._no_links(stage)
        if not stage.is_dir():
            _reject('invalid-additions-stage')
        retained = adapter._target(stage, 'addition-journal.json')
        if not retained.exists():
            continue
        journal = _read_json(retained)
        if 'publicationMode' not in journal:
            _legacy_addition_declaration(root, stage, retained, journal)
            continue  # Preserve historical no-pointer semantics; never synthesize an outcome.
        loaded = _addition_journal(root, retained=retained, verify_files=False)
        if journal['transactionId'] == active:
            continue
        if _addition_outcome(stage, retained, journal) is None:
            result.append(loaded)
    return sorted(result, key=lambda item: item[2]['transactionId'])


def _preflight_prepared_addition(root, stage, retained, journal):
    """Prove the full original private stage before rearming its marker.

    All payloads must still be the pre-journaled objects and no selected live
    addition may exist. Unknown/partial controls or changed payloads stay put.
    """
    adapter = _adapter()
    mode = _publication_mode(journal)
    data = _encoded(journal)
    if _read_bytes(retained) != data or _addition_outcome(stage, retained, journal) is not None:
        _reject('additions-preparation-journal-changed')
    expected = {'addition-journal.json': _metadata_row(retained, journal)}
    for row in journal['files']:
        identity = journal['stagedIdentities'][row['path']]
        if not _matches(adapter._target(stage, row['path']), row, identity):
            _reject('additions-preparation-payload-changed')
        expected[row['path']] = (row, identity)
    for entry in journal['entries']:
        if adapter._target(root, entry['new']['path']).exists():
            _reject('additions-preparation-live-target-present; 当前已有图包文件，已保留当前安装和准备记录；请导出诊断信息核查，不要直接删除准备目录')
    receipt = adapter._target(root, f"{STATE}/{journal['packageId']}.json")
    if mode == 'move-v1':
        old = journal['oldReceipt']
        if ((old is None and receipt.exists()) or
                (old is not None and not _matches(receipt, old, old['identity']))):
            _reject('additions-preparation-receipt-changed')
    elif (adapter._sha(receipt) if receipt.exists() else None) != journal['oldReceiptSha256']:
        _reject('additions-preparation-receipt-changed')
    new_receipt = adapter._target(stage, 'receipt.json')
    if mode == 'move-v1' or new_receipt.exists():
        identity = journal.get('newReceiptIdentity')
        if not _matches(new_receipt, journal['newReceipt'], identity):
            _reject('additions-preparation-receipt-changed')
        expected['receipt.json'] = (journal['newReceipt'], identity)
    if mode == 'move-v1':
        proof, source = _publication_paths(retained)
        temporary = proof.with_name(proof.name + '.tmp')
        statement = None
        row = dict(bytes=len(data), sha256=hashlib.sha256(data).hexdigest())
        for control in (proof, temporary):
            if control.exists():
                value = _validate_publication_proof(_read_json(control))
                if (value['bytes'] != row['bytes'] or value['sha256'] != row['sha256']
                        or not _matches(retained, row, value['retainedIdentity'])):
                    _reject('additions-preparation-publication-changed')
                expected[control.name] = _metadata_row(control, value)
                statement = value  # A complete newer temporary proof takes priority.
        if source.exists():
            if statement is None:
                if _read_bytes(source) != data:
                    _reject('additions-preparation-publication-changed')
                identity = _capture(source, row)  # Exact unpublished control JSON only.
            else:
                identity = statement['identity']
                if not _matches(source, row, identity):
                    _reject('additions-preparation-publication-changed')
            expected[source.name] = (row, identity)
        elif statement is not None:
            _reject('additions-preparation-publication-source-missing')
    _private_tree_plan(stage, expected)  # Reject every unknown object before writes.

def _addition_cleanup_plan(stage, retained, journal):
    """Validate the whole private stage before deleting any of its objects."""
    adapter = _adapter()
    expected = {row['path']: (row, journal['stagedIdentities'][row['path']]) for row in journal['files']}
    expected['receipt.json'] = (journal['newReceipt'], journal.get('newReceiptIdentity'))
    if journal.get('oldReceipt') is not None:
        expected['old-receipt.json'] = (journal['oldReceipt'], journal['oldReceipt']['identity'])
    expected.update(_publication_cleanup_records(retained, _publication_mode(journal)))
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


def recover_pending_additions(root, transaction_id=None):
    """Caller owns writer/closed lease; preserve unknown replacements on recovery."""
    adapter = _adapter()
    loaded = _addition_journal(root)
    if loaded is None:
        candidates = prepared_additions(root)
        if transaction_id is not None:
            candidates = [item for item in candidates if item[2]['transactionId'] == transaction_id]
        if not candidates:
            return {'status': 'no-pending-additions'}
        stage, retained, journal = candidates[0]
        _preflight_prepared_addition(root, stage, retained, journal)
        _publish_metadata(retained, adapter._target(root, ADDITIONS_PENDING),
            _publication_mode(journal), expected_data=_encoded(journal))
        loaded = _addition_journal(root)
    if transaction_id is not None and loaded[2]['transactionId'] != transaction_id:
        _reject('another-pending-additions-recovery-required')
    stage, retained, journal = loaded
    outcome = _addition_outcome(stage, retained, journal)
    if outcome is None:
        receipt = adapter._target(root, f"{STATE}/{journal['packageId']}.json")
        current = _read_json(receipt) if receipt.exists() else None
        committed = (current is not None and current.get('additionTransactionId') == journal['transactionId']
                     and current.get('packageId') == journal['packageId']
                     and current.get('files') == journal['files'])
        if _publication_mode(journal) == 'move-v1':
            committed = committed and _matches(receipt, journal['newReceipt'], journal['newReceiptIdentity'])
        if committed:
            if any(not _matches(adapter._target(root, row['path']), row,
                    journal['stagedIdentities'][row['path']] if _publication_mode(journal) == 'move-v1'
                    and row in [entry['new'] for entry in journal['entries']] else None) for row in journal['files']):
                _reject('additions-committed-data-changed')
        else:
            if _publication_mode(journal) == 'move-v1':
                old = journal['oldReceipt']
                backup = adapter._target(stage, 'old-receipt.json')
                if receipt.exists():
                    if old is None or not _matches(receipt, old, old['identity']):
                        _reject('additions-receipt-changed')
                elif old is not None and not _matches(backup, old, old['identity']):
                    _reject('additions-receipt-changed')
                if backup.exists() and (old is None or not _matches(backup, old, old['identity'])):
                    _reject('additions-old-receipt-changed')
            elif (adapter._sha(receipt) if receipt.exists() else None) != journal['oldReceiptSha256']:
                _reject('additions-receipt-changed')
            # Inspect the entire set first; leave all objects untouched on conflicts.
            for entry in journal['entries']:
                path = adapter._target(root, entry['new']['path'])
                if path.exists() and not _matches(path, entry['new'], entry['newIdentity']):
                    _reject('additions-rollback-conflict')
            for entry in reversed(journal['entries']):
                row = entry['new']
                path = adapter._target(root, row['path'])
                if path.exists():
                    if _publication_mode(journal) == 'move-v1':
                        _move_owned(path, adapter._target(stage, row['path']), row, entry['newIdentity'], linkless=True)
                    elif not adapter._rollback_published(path, tuple(entry['newIdentity']), row['sha256']):
                        _reject('additions-rollback-incomplete')
            if _publication_mode(journal) == 'move-v1' and journal['oldReceipt'] is not None and not receipt.exists():
                old = journal['oldReceipt']
                _move_owned(adapter._target(stage, 'old-receipt.json'), receipt, old, old['identity'], linkless=True)
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
    if _publication_mode(journal) == 'move-v1':
        metadata.add(_publication_paths(retained)[0])
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
    identity = _metadata_publication_identity(retained, pending, _publication_mode(journal))
    if not adapter._rollback_published(pending, tuple(identity), adapter._sha(retained)):
        _reject('additions-pointer-could-not-be-cleared')
    # No payload copies remain when the gate is cleared. Only redundant terminal
    # text/empty directories may remain after a best-effort metadata retirement.
    try:
        _directory_sync(pending.parent)
        by_path = {path: (identity, digest) for path, identity, digest in remaining}
        if _publication_mode(journal) != 'move-v1':
            for path in sorted(metadata, key=lambda path: path == retained):
                if not adapter._rollback_published(path, *by_path[path]):
                    break
            else:
                stage.rmdir()
        # move-v1 keeps these three tiny terminal ownership records together.
        # They contain no payload copies. The receipt-owned removal transaction
        # can retire them with its own durable inventory; untracked partial
        # deletion here would destroy the surviving history's authority.
    except (OSError, adapter.ImportRejected):
        pass
    return {'status': 'additions-committed' if committed else 'additions-rolled-back',
            'packageId': journal['packageId'], 'additionTransactionId': journal['transactionId'],
            'restartRequired': True}
