"""Confirmed package deletion with temporary pins of the original file objects.

Only receipt/journal-owned objects are eligible. Unknown and changed files stay
in place. A durable committed journal is the irreversible commit: before it,
failure/process interruption restores the full selected set with original file
identities; afterwards recovery finishes guarded cleanup. No package copy
survives a successful return. Cleanup failures retain the pending gate.
Callers must also hold the application's idle resource lease for live mutation.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re
import stat
import uuid

import star_map_import as files
import map_package_upgrade as upgrade
from author_package_management import PACKAGE_NAMES
from managed_base_resources import managed_base_inventory
from resource_transaction import resource_read_lock, resource_write_lock, assert_resources_ready

STATE = "work/map-packages/removals"
PENDING = "work/map-packages/pending-removal.json"
SCHEMA = "map-package-removal-v1"
MAX_ENTRIES = 20000
MAX_BYTES = 16 * 1024 ** 3


def _reject(code):
    raise files.ImportRejected(code)


def _validate_relative(relative):
    if (not isinstance(relative, str) or not relative or len(relative) > 1024
            or relative.startswith("/") or any(c in relative for c in "\\:\x00\r\n")
            or any(part in {"", ".", ".."} or part.endswith((" ", ".")) for part in relative.split("/"))):
        _reject("removal-unsafe-path")


def _target(root, relative):
    _validate_relative(relative)
    target = root.joinpath(*relative.split("/"))
    files._no_links(target)
    return target


def _encoded(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _stamp(path):
    files._no_links(path)
    before = path.stat()
    if not stat.S_ISREG(before.st_mode) or before.st_size > MAX_BYTES:
        _reject("removal-not-regular-or-too-large")
    digest = files._sha(path)
    after = path.stat()
    if (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns, before.st_ctime_ns) != (
            after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns, after.st_ctime_ns):
        _reject("removal-object-changed-during-inspection")
    return dict(bytes=before.st_size, sha256=digest, identity=upgrade._identity(before))


def _children(root, relative):
    directory = _target(root, relative)
    if not directory.exists():
        return []
    children = sorted(directory.iterdir(), key=lambda path: path.name)
    if len(children) > 1024:
        _reject("removal-history-limit")
    return children


def _receipt(path, package):
    value = upgrade._read_json(path)
    return files._receipt_inventory(value, package)


def _unknown_history(directory, known):
    pending, result, count = [directory], [], 0
    while pending:
        parent = pending.pop()
        for path in parent.iterdir():
            count += 1
            if count > MAX_ENTRIES:
                _reject("removal-history-limit")
            files._no_links(path)
            if path.is_dir():
                pending.append(path)
            elif path not in known:
                result.append(path)
    return result


def _valid_target(package, relative):
    receipt = f"work/map-packages/{package}.json"
    if relative == receipt or (files._allowed(package, relative) and files._live_author_path(package, relative)):
        return True
    if re.fullmatch(r"work/map-packages/downloads/" + re.escape(package) + r"-[0-9a-f]{64}\.zip", relative):
        return True
    parts = relative.split("/")
    if relative.startswith("work/map-package-removals/") and len(parts) >= 4:
        if _legacy_owner(parts[2]) != package:
            return False
        logical = "/".join(parts[3:])
        return logical == "installed-receipt.json" or (
            files._allowed(package, logical) and files._live_author_path(package, logical))
    if relative.startswith("work/map-packages/upgrades/") and len(parts) >= 5:
        try:
            upgrade._transaction_id(parts[3])
        except files.ImportRejected:
            return False
        tail = "/".join(parts[4:])
        if tail in {"journal.json", "pending-pointer.json"}:
            return True
        logical = "/".join(parts[5:])
        return parts[4] in {"backup", "new"} and (logical == receipt or files._allowed(package, logical))
    if len(parts) >= 4 and re.fullmatch(r"work/map-packages/staging-[A-Za-z0-9_-]+", "/".join(parts[:3])):
        logical = "/".join(parts[3:])
        return logical in {"addition-journal.json", "receipt.json", upgrade.ADDITIONS_OUTCOME} or files._allowed(package, logical)
    return False


def _legacy_owner(name):
    # IDs can be prefixes of other IDs (suju / suju-speed). Bind the exact
    # longest registered prefix before consulting the user's selected set.
    return next((package for package in sorted(files.PACKAGE_IDS, key=len, reverse=True)
                 if re.fullmatch(re.escape(package) + r"-[A-Za-z0-9_-]{1,100}", name)), None)


def _plan_locked(root, package_id=None, cancelled=None):
    assert_resources_ready(root)
    if package_id is not None and package_id not in files.PACKAGE_IDS:
        _reject("unsupported-author-package")
    selected = set(files.PACKAGE_IDS) if package_id is None else {package_id}
    protected = set(managed_base_inventory(root))
    active, histories, witnesses, archives = {}, [], {}, {}

    def remember_archive(package, path):
        value = upgrade._read_json(path)
        digest = upgrade._digest(value.get("archiveSha256"))
        archives.setdefault(package, set()).add(digest)
    for package in sorted(files.PACKAGE_IDS):
        path = _target(root, f"work/map-packages/{package}.json")
        if not path.exists():
            continue
        inventory, dependencies = _receipt(path, package)
        active[package] = inventory
        remember_archive(package, path)
        witnesses[path.relative_to(root).as_posix()] = _stamp(path)
        protected.update(dependencies)
        if package not in selected:
            protected.update(inventory)
    # Old UI removals wrote this exact receipt and only the owned display set.
    for directory in _children(root, "work/map-package-removals"):
        package = _legacy_owner(directory.name)
        if package not in selected:
            continue
        files._no_links(directory)
        receipt = _target(directory, "installed-receipt.json")
        if not receipt.exists():
            continue  # An unindexed directory grants no deletion authority.
        inventory, _ = _receipt(receipt, package)
        remember_archive(package, receipt)
        sources = [(row, _target(directory, row["path"])) for row in inventory.values()
                   if files._live_author_path(package, row["path"])]
        histories.append((package, directory, sources, [receipt]))
        witnesses[receipt.relative_to(root).as_posix()] = _stamp(receipt)
    for directory in _children(root, "work/map-packages/upgrades"):
        files._no_links(directory)
        source = _target(directory, "journal.json")
        if not source.is_file():
            continue
        header = upgrade._read_json(source)
        if header.get("packageId") not in selected:
            continue
        directory, journal = upgrade._load_journal(root, directory.name)
        if journal["status"] in upgrade.RETIRED_STATES:
            # Retired summaries prove no payload ownership. Verify that claim
            # before allowing deletion of only the bounded metadata files.
            rows, directories = upgrade._retired_plan(directory, journal)
            if directories:
                _reject("removal-retired-upgrade-changed")
            histories.append((journal["packageId"], directory, [], [row[0] for row in rows]))
            witnesses[source.relative_to(root).as_posix()] = _stamp(source)
            continue
        if journal["status"] not in {"committed", "rolled-back"}:
            _reject("removal-upgrade-recovery-required")
        package = journal["packageId"]
        sources = []
        for entry in journal["entries"]:
            for side, row in (("backup", entry["old"]), ("new", entry["new"])):
                if row is not None:
                    sources.append((row, _target(directory, side + "/" + row["path"])))
                    if row["path"] == f"work/map-packages/{package}.json" and _target(directory, side + "/" + row["path"]).exists():
                        remember_archive(package, _target(directory, side + "/" + row["path"]))
        metadata = [source]
        pointer = _target(directory, "pending-pointer.json")
        if pointer.exists():
            if upgrade._read_json(pointer) != dict(schema=upgrade.POINTER_SCHEMA, transactionId=directory.name):
                _reject("removal-upgrade-pointer-changed")
            metadata.append(pointer)
        histories.append((package, directory, sources, metadata))
        witnesses[source.relative_to(root).as_posix()] = _stamp(source)
    for directory in _children(root, "work/map-packages"):
        if not re.fullmatch(r"staging-[A-Za-z0-9_-]+", directory.name):
            continue
        files._no_links(directory)
        source = _target(directory, "addition-journal.json")
        if not source.is_file():
            continue
        journal = upgrade._read_json(source)
        package = journal.get("packageId")
        if package not in selected:
            continue
        required = {"schema", "packageId", "transactionId", "stage", "rootIdentity", "files", "entries", "oldReceiptSha256"}
        if (set(journal) not in (required, required | {"stagedIdentities", "newReceipt"})
                or journal["schema"] != upgrade.ADDITIONS_SCHEMA
                or journal["stage"] != directory.relative_to(root).as_posix()
                or journal["rootIdentity"] != upgrade._identity(root.stat())):
            _reject("removal-invalid-additions-history")
        upgrade._transaction_id(journal["transactionId"])
        inventory = upgrade._rows(journal["files"], package)
        if not isinstance(journal["entries"], list) or not 1 <= len(journal["entries"]) <= len(inventory):
            _reject("removal-invalid-additions-history")
        seen = set()
        for entry in journal["entries"]:
            if not isinstance(entry, dict) or set(entry) != {"new", "newIdentity"}:
                _reject("removal-invalid-additions-history")
            row = entry["new"]
            if not isinstance(row, dict) or not isinstance(row.get("path"), str):
                _reject("removal-invalid-additions-history")
            key = row["path"].casefold()
            if key in seen or inventory.get(key) != row:
                _reject("removal-invalid-additions-history")
            seen.add(key)
            upgrade._valid_identity(entry["newIdentity"])
            target = _target(directory, row["path"])
            if target.exists() and not upgrade._matches(target, row, entry["newIdentity"]):
                _reject("removal-additions-history-changed")
        if journal["oldReceiptSha256"] is not None:
            upgrade._digest(journal["oldReceiptSha256"])
        sources = [(row, _target(directory, row["path"])) for row in inventory.values()]
        if "stagedIdentities" in journal:
            identities = journal["stagedIdentities"]
            if not isinstance(identities, dict) or set(identities) != {r["path"] for r in inventory.values()}:
                _reject("removal-invalid-additions-history")
            for row, target in sources:
                upgrade._valid_identity(identities[row["path"]])
                if target.exists() and not upgrade._matches(target, row, identities[row["path"]]):
                    _reject("removal-additions-history-changed")
            receipt = journal["newReceipt"]
            if (not isinstance(receipt, dict) or set(receipt) != {"path", "bytes", "sha256"}
                    or receipt["path"] != "receipt.json" or type(receipt["bytes"]) is not int
                    or not 0 <= receipt["bytes"] <= upgrade.MAX_JOURNAL_BYTES):
                _reject("removal-invalid-additions-history")
            upgrade._digest(receipt["sha256"])
            sources.append((receipt, _target(directory, "receipt.json")))
            if upgrade._matches(_target(directory, "receipt.json"), receipt):
                remember_archive(package, _target(directory, "receipt.json"))
        metadata = [source]
        outcome = upgrade._addition_outcome(directory, source, journal)
        if outcome is not None:
            terminal = _target(directory, upgrade.ADDITIONS_OUTCOME)
            metadata.append(terminal)
            witnesses[terminal.relative_to(root).as_posix()] = _stamp(terminal)
        histories.append((package, directory, sources, metadata))
        witnesses[source.relative_to(root).as_posix()] = _stamp(source)
    entries, preserved, packages = {}, [], set()

    def add(package, path, expected=None):
        files._check_cancelled(cancelled)
        relative = path.relative_to(root).as_posix()
        if not _valid_target(package, relative):
            _reject("removal-target-not-owned")
        files._no_links(path)
        if not path.exists():
            return True
        actual = _stamp(path)
        if expected is not None and (actual["bytes"] != expected["bytes"] or actual["sha256"] != expected["sha256"]):
            preserved.append(dict(path=relative, reason="modified-or-unverified"))
            return False
        entries[relative] = dict(path=relative, packageId=package, **actual)
        packages.add(package)
        return True

    dirty_cache = set()
    for package, digests in archives.items():
        if package not in selected:
            continue
        for digest in sorted(digests):
            path = _target(root, f"work/map-packages/downloads/{package}-{digest}.zip")
            if path.exists() and not add(package, path, dict(bytes=path.stat().st_size, sha256=digest)):
                dirty_cache.add(package)

    for package, inventory in active.items():
        if package not in selected:
            continue
        clean = True
        for key, row in inventory.items():
            if key in protected or not files._live_author_path(package, row["path"]):
                preserved.append(dict(path=row["path"], reason="official-core-or-shared"))
                continue
            clean = add(package, _target(root, row["path"]), row) and clean
        if clean and package not in dirty_cache:
            add(package, _target(root, f"work/map-packages/{package}.json"))
    for package, directory, sources, metadata in histories:
        clean = True
        for row, path in sources:
            clean = add(package, path, row) and clean
        if clean and package not in dirty_cache:
            for path in metadata:
                add(package, path)
        for path in _unknown_history(directory, {path for _row, path in sources} | set(metadata)):
            preserved.append(dict(path=path.relative_to(root).as_posix(), reason="unknown-history-file"))
    if len(entries) > MAX_ENTRIES or sum(row["bytes"] for row in entries.values()) > MAX_BYTES:
        _reject("removal-plan-too-large")
    registered = (set(active) & selected) | {package for package, _directory, _sources, _metadata in histories}
    result = dict(packageId=package_id, packageIds=sorted(packages), registeredPackageIds=sorted(registered),
        entries=sorted(entries.values(), key=lambda row: row["path"]), witnesses=witnesses,
        preserved=sorted(preserved, key=lambda row: row["path"]), fileCount=len(entries),
        bytes=sum(row["bytes"] for row in entries.values()))
    result["planSha256"] = hashlib.sha256(_encoded(result)).hexdigest()
    return result


def inspect_package_removal(install_root, package_id=None, *, cancelled=None):
    with resource_read_lock(install_root, wait=True, cancelled=lambda: files._check_cancelled(cancelled)) as root:
        return _plan_locked(Path(root), package_id, cancelled)


def managed_packages_snapshot(install_root, *, cancelled=None):
    with resource_read_lock(install_root, wait=True, cancelled=lambda: files._check_cancelled(cancelled)) as root:
        plan = _plan_locked(Path(root), cancelled=cancelled)
        result = []
        for package in plan["registeredPackageIds"]:
            rows = [row for row in plan["entries"] if row["packageId"] == package]
            active = _target(Path(root), f"work/map-packages/{package}.json").exists()
            inventory = _receipt(_target(Path(root), f"work/map-packages/{package}.json"), package)[0] if active else {}
            modified = any(row["reason"] == "modified-or-unverified" and row["path"].casefold() in inventory for row in plan["preserved"])
            result.append(dict(packageId=package, title=PACKAGE_NAMES.get(package, package),
                status="incomplete" if modified else "installed" if active else "managed-remnants", canRemove=bool(rows),
                fileCount=len(rows), bytes=sum(row["bytes"] for row in rows),
                mode="both" if package in ("s1-jiaye-handnotes", "s1-aichicu-routes") else ("difficult" if package.startswith("difficult-") else "nightmare"),
                **({"season": "s1"} if package in ("s1-jiaye-handnotes", "s1-aichicu-routes") else {})))
        return result


def _prune_empty(root, paths):
    # Empty directories only; stop at installation root and keep unknown files.
    parents = set()
    for relative in paths:
        parent = _target(root, relative).parent
        while parent != root and parent.is_relative_to(root):
            parents.add(parent)
            parent = parent.parent
    for path in sorted(parents, key=lambda p: len(p.parts), reverse=True):
        files._no_links(path)
        try:
            path.rmdir()
        except (FileNotFoundError, OSError):
            pass


def _check_other_pending(root):
    for name in ("pending-upgrade.json", "pending-base-repair.json", "pending-additions.json"):
        if _target(root, "work/map-packages/" + name).exists():
            _reject("removal-other-transaction-pending")


def _load_pending(root):
    pointer = upgrade._read_json(_target(root, PENDING))
    if set(pointer) != {"schema", "transactionId"} or pointer["schema"] != SCHEMA:
        _reject("removal-invalid-pending-pointer")
    transaction = upgrade._transaction_id(pointer["transactionId"])
    directory = _target(root, STATE + "/" + transaction)
    journal = upgrade._read_json(_target(directory, "journal.json"))
    if (set(journal) != {"schema", "transactionId", "rootIdentity", "packageIds", "entries", "status"}
            or journal["schema"] != SCHEMA or journal["transactionId"] != transaction
            or journal["rootIdentity"] != upgrade._identity(root.stat())
            or journal["status"] not in {"preparing", "prepared", "committed", "rolled-back"}
            or not isinstance(journal["packageIds"], list) or not journal["packageIds"]
            or any(not isinstance(p, str) or p not in files.PACKAGE_IDS for p in journal["packageIds"])
            or not isinstance(journal["entries"], list) or not 1 <= len(journal["entries"]) <= MAX_ENTRIES):
        _reject("removal-invalid-journal")
    seen = set()
    for row in journal["entries"]:
        if (not isinstance(row, dict) or set(row) != {"path", "packageId", "bytes", "sha256", "identity"}
                or row["packageId"] not in journal["packageIds"]
                or not isinstance(row["path"], str) or row["path"].casefold() in seen
                or not _valid_target(row["packageId"], row["path"])
                or type(row["bytes"]) is not int or not 0 <= row["bytes"] <= MAX_BYTES):
            _reject("removal-invalid-journal-entry")
        # Terminal recovery owns only its retained pins and metadata. Validate
        # the old logical path without consulting the user's current object or
        # ancestor links, which may legitimately have changed after completion.
        if journal["status"] in {"committed", "rolled-back"}:
            _validate_relative(row["path"])
        else:
            _target(root, row["path"])
        seen.add(row["path"].casefold())
        upgrade._digest(row["sha256"])
        upgrade._valid_identity(row["identity"])
    if sum(row["bytes"] for row in journal["entries"]) > MAX_BYTES:
        _reject("removal-plan-too-large")
    return directory, journal


def inspect_pending_removal_locked(root):
    root = Path(root)
    _check_other_pending(root)
    directory, journal = _load_pending(root)
    stamps = {PENDING: _stamp(_target(root, PENDING)), "journal": _stamp(directory / "journal.json")}
    for number, row in enumerate(journal["entries"]):
        if journal["status"] not in {"committed", "rolled-back"}:
            target = _target(root, row["path"])
            stamps[row["path"]] = _stamp(target) if target.exists() else None
        retained = _target(directory, "objects/" + str(number))
        stamps["object:" + str(number)] = _stamp(retained) if retained.exists() else None
    return dict(transactionId=journal["transactionId"],
        packageId=journal["packageIds"][0] if len(journal["packageIds"]) == 1 else "all-managed",
        status="removal-pending", pending=True, fileCount=len(journal["entries"]), blockedPaths=[],
        planSha256=hashlib.sha256(_encoded(stamps)).hexdigest())


def _cleanup_objects(root, directory, journal):
    # Validate the complete retained set before deleting any temporary link.
    for number, row in enumerate(journal["entries"]):
        retained = _target(directory, "objects/" + str(number))
        if retained.exists() and not upgrade._matches(retained, row, row["identity"]):
            _reject("removal-retained-object-changed")
    for number, row in enumerate(journal["entries"]):
        retained = _target(directory, "objects/" + str(number))
        if retained.exists() and not files._rollback_published(retained, tuple(row["identity"]), row["sha256"]):
            _reject("removal-temporary-cleanup-failed")
        upgrade._directory_sync(retained.parent)
    # An unknown extra object is not silently discarded or called complete.
    objects = _target(directory, "objects")
    if objects.exists() and any(objects.iterdir()):
        _reject("removal-unknown-temporary-object")
    _retire(root, directory, journal)


def _retire(root, directory, journal):
    pointer = _target(root, PENDING)
    expected_pointer = dict(schema=SCHEMA, transactionId=journal["transactionId"])
    if upgrade._read_json(pointer) != expected_pointer:
        _reject("removal-pending-pointer-changed")
    # Every payload pin has gone before the pending gate is cleared.
    identity = upgrade._identity(pointer.stat())
    digest = hashlib.sha256(_encoded(expected_pointer)).hexdigest()
    if not files._rollback_published(pointer, tuple(identity), digest):
        _reject("removal-pending-pointer-cleanup-failed")
    source = _target(directory, "journal.json")
    # The gate is already cleared and all payload pins gone. Only redundant
    # text/empty directories may remain if their best-effort retirement fails.
    try:
        upgrade._directory_sync(pointer.parent)
        identity = upgrade._identity(source.stat())
        digest = hashlib.sha256(upgrade._encoded(journal)).hexdigest()
        files._rollback_published(source, tuple(identity), digest)
        # Never prune active-resource parents after a durable outcome: a user
        # may have recreated even empty directories there in the meantime.
        private_history = [row["path"] for row in journal["entries"] if row["path"].startswith((
            "work/map-packages/upgrades/", "work/map-packages/staging-", "work/map-package-removals/"))]
        _prune_empty(root, private_history + [source.relative_to(root).as_posix(),
                           (directory / "objects/0").relative_to(root).as_posix()])
    except (OSError, files.ImportRejected):
        pass


def _restore(root, directory, journal):
    for number, row in enumerate(journal["entries"]):
        target = _target(root, row["path"])
        retained = _target(directory, "objects/" + str(number))
        if target.exists():
            if not upgrade._matches(target, row, row["identity"]):
                _reject("removal-rollback-conflict; retained-objects-and-pending-marker")
        elif not upgrade._matches(retained, row, row["identity"]):
            _reject("removal-rollback-source-missing-or-changed")
    for number, row in enumerate(journal["entries"]):
        target = _target(root, row["path"])
        if target.exists():
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        files._no_links(target.parent)
        os.link(_target(directory, "objects/" + str(number)), target)
        if not upgrade._matches(target, row, row["identity"]):
            _reject("removal-rollback-conflict; retained-objects-and-pending-marker")
        upgrade._directory_sync(target.parent)
    journal["status"] = "rolled-back"
    upgrade._write_journal(directory, journal)
    _cleanup_objects(root, directory, journal)


def recover_pending_removal_locked(root):
    root = Path(root)
    _check_other_pending(root)
    directory, journal = _load_pending(root)
    if journal["status"] in {"committed", "rolled-back"}:
        # Both durable outcomes are terminal. A rolled-back journal proves
        # restoration already finished; later user deletions/replacements must
        # never be resurrected or revalidated against that obsolete snapshot.
        _cleanup_objects(root, directory, journal)
        status = "removal-" + journal["status"]
    else:
        _restore(root, directory, journal)
        status = "removal-rolled-back"
    return dict(status=status, removalTransactionId=journal["transactionId"], restartRequired=True)


def remove_packages(install_root, package_id=None, *, application_idle=False, expected_plan_sha256=None, cancelled=None):
    if application_idle is not True or not expected_plan_sha256:
        _reject("removal-confirmation-and-idle-required")
    with resource_write_lock(install_root, wait=True, cancelled=lambda: files._check_cancelled(cancelled)) as root:
        root = Path(root)
        plan = _plan_locked(root, package_id, cancelled)
        if plan["planSha256"] != expected_plan_sha256:
            _reject("removal-plan-changed-reinspect-required")
        unverified = sum(row["reason"] != "official-core-or-shared" for row in plan["preserved"])
        if not plan["entries"]:
            return dict(status="preserved-items" if unverified else "already-removed", packageIds=[], removedFiles=0,
                preserved=plan["preserved"], unverifiedPreservedCount=unverified, temporaryCopiesRemaining=0, restartRequired=False)
        from resource_transaction import preflight_hardlinks
        preflight_hardlinks(root, [row["path"] for row in plan["entries"]])
        transaction = str(uuid.uuid4())
        directory = _target(root, STATE + "/" + transaction)
        directory.mkdir(parents=True, exist_ok=False)
        objects = _target(directory, "objects")
        objects.mkdir()
        journal = dict(schema=SCHEMA, transactionId=transaction, rootIdentity=upgrade._identity(root.stat()),
            packageIds=plan["packageIds"], entries=plan["entries"], status="preparing")
        upgrade._write_journal(directory, journal)
        upgrade._write_new(_target(root, PENDING), _encoded(dict(schema=SCHEMA, transactionId=transaction)))
        try:
            for number, row in enumerate(plan["entries"]):
                files._check_cancelled(cancelled)
                source = _target(root, row["path"])
                if not upgrade._matches(source, row, row["identity"]):
                    _reject("removal-confirmed-file-changed")
                retained = _target(objects, str(number))
                os.link(source, retained)
                if not upgrade._matches(retained, row, row["identity"]):
                    _reject("removal-retained-object-changed")
            upgrade._directory_sync(objects)
            journal["status"] = "prepared"
            upgrade._write_journal(directory, journal)
            for row in plan["entries"]:
                files._check_cancelled(cancelled)
                target = _target(root, row["path"])
                if not files._rollback_published(target, tuple(row["identity"]), row["sha256"]):
                    _reject("removal-confirmed-file-changed-or-unwritable")
                upgrade._directory_sync(target.parent)
            files._check_cancelled(cancelled)
            committed = dict(journal, status="committed")
            upgrade._write_journal(directory, committed)
        except Exception:
            # A failed atomic journal write can have succeeded before fsync failed.
            durable = upgrade._read_json(_target(directory, "journal.json"))
            if durable.get("status") == "committed":
                _reject("removal-committed-cleanup-required")
            _restore(root, directory, journal)
            raise
        try:
            _cleanup_objects(root, directory, committed)
        except Exception as error:
            raise files.ImportRejected("removal-committed-cleanup-required") from error
        return dict(status="packages-partially-removed" if unverified else "packages-removed", packageIds=plan["packageIds"],
            removedFiles=len(plan["entries"]), preserved=plan["preserved"], unverifiedPreservedCount=unverified,
            temporaryCopiesRemaining=0, restartRequired=False)
