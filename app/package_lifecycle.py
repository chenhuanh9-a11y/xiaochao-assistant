"""One ownership inventory and temporary transaction for author packages.

Callers hold the existing resource lease. Mutations also hold the import OS lock.
Successful operations discard rollback bytes; interrupted operations can only be
resumed through this journal, never by the legacy upgrade recovery mechanism.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import uuid

from legacy_author_inventory import LEGACY_AUTHOR_INVENTORY

STATE = "work/map-packages/lifecycle"
SCHEMA = "package-lifecycle-v1"
LIMIT = 20000


def _api():
    import star_map_import
    return star_map_import


def _upgrade():
    import map_package_upgrade
    return map_package_upgrade


def scope(package):
    api = _api()
    if package not in api.PACKAGE_IDS:
        raise api.ImportRejected("unsupported-author-package")
    return dict(packageId=package, mode=package.split("-", 1)[0],
                profileId=api.AUTHOR_PACKAGES.get(package, {
                    "nightmare-suju": "suju", "difficult-zhan10": "zhan10-speed"}.get(package)))


def display_owned(package, row):
    return _api()._allowed(package, row["path"]) and _api()._live_author_path(package, row["path"])


def _encoded(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _read(path):
    _api()._no_links(path)
    if path.stat().st_size > 8 * 1024 * 1024:
        raise _api().ImportRejected("package-index-too-large")
    return _api()._json(path.read_bytes())


def _stamp(path):
    api = _api()
    api._no_links(path)
    before = path.stat()
    if not path.is_file():
        raise api.ImportRejected("non-file-author-path")
    digest = api._sha(path)
    after = path.stat()
    stamp = lambda s: [s.st_dev, s.st_ino, s.st_size, s.st_mtime_ns]
    if stamp(before) != stamp(after):
        raise api.ImportRejected("package-file-changed-during-inspection")
    return dict(bytes=after.st_size, sha256=digest, identity=[after.st_dev, after.st_ino])


def _children(root, relative):
    path = _api()._target(root, relative)
    if not path.exists():
        return []
    children = sorted(path.iterdir(), key=lambda p: p.name)
    if len(children) > LIMIT:
        raise _api().ImportRejected("package-index-too-large")
    for child in children:
        _api()._no_links(child)
    return children


def receipt_rows(value):
    api = _api()
    package = value.get("packageId")
    expected = scope(package)
    if any(key in value and value[key] != expected[key] for key in ("mode", "profileId")):
        raise api.ImportRejected("receipt-package-scope-mismatch")
    _upgrade()._digest(value.get("archiveSha256"))
    if value.get("revision") != 1:
        raise api.ImportRejected("invalid-installed-package-receipt")
    rows = api._rows(value.get("files"))
    if any(not api._allowed(package, r["path"]) for r in rows.values()):
        raise api.ImportRejected("invalid-installed-package-receipt")
    return package, rows


def _index_lines(path):
    _api()._no_links(path)
    raw = path.read_bytes()
    if len(raw) > 8 * 1024 * 1024:
        raise _api().ImportRejected("package-index-too-large")
    if raw.startswith((b"\xff\xfe", b"\xfe\xff")):
        return raw.decode("utf-16").splitlines()
    try:
        return raw.decode("utf-8-sig").splitlines()
    except UnicodeDecodeError:
        return raw.decode("mbcs" if os.name == "nt" else "cp1252").splitlines()


def inspect_cleanup(root, package=None, *, cancelled=None, pending_only=False):
    """Read-only plan: active receipts, pinned legacy data, indexed historical bytes."""
    api, upgrade = _api(), _upgrade()
    root = Path(os.path.abspath(root))
    api._no_links(root)
    selected = {package} if package is not None else set(api.PACKAGE_IDS)
    for p in selected:
        scope(p)
    files, witnesses, residuals, proofs, owners, active = {}, {}, {}, {}, {}, {}
    archives, histories = {}, []

    def rel(path):
        return path.relative_to(root).as_posix()

    def witness(path):
        witnesses[rel(path)] = _stamp(path)

    def residual(path, reason):
        residuals[rel(path)] = reason

    def add(path, p, logical, expected=None, kind="display"):
        api._check_cancelled(cancelled)
        if p not in selected or not path.exists():
            return
        if not path.is_file():
            residual(path, "not-regular")
            return
        current = _stamp(path)
        if expected and (current["bytes"], current["sha256"]) != (expected["bytes"], expected["sha256"]):
            residual(path, "modified-or-unverified")
            return
        files[rel(path)] = dict(path=rel(path), logicalPath=logical, packageId=p, kind=kind, **current)

    def record(value, expected_package=None):
        p, rows = receipt_rows(value)
        if expected_package is not None and p != expected_package:
            raise api.ImportRejected("receipt-package-mismatch")
        archives.setdefault(value["archiveSha256"], set()).add(p)
        for row in rows.values():
            if display_owned(p, row):
                proofs.setdefault(row["path"].casefold(), []).append((p, row))
        return p, rows

    for p in sorted(api.PACKAGE_IDS):
        path = api._target(root, f"work/map-packages/{p}.json")
        if path.exists():
            witness(path)
            try:
                actual, rows = record(_read(path), p)
                active[p] = rows
                for key in rows:
                    owners.setdefault(key, set()).add(p)
            except (ValueError, OSError) as error:
                residual(path, str(error))
    for p, release in LEGACY_AUTHOR_INVENTORY.items():
        record(dict(packageId=p, revision=1, archiveSha256=release["archiveSha256"],
                    files=[{k: row[k] for k in ("path", "bytes", "sha256")} for row in release["files"]]))

    for folder in _children(root, "work/map-package-removals"):
        receipt = api._target(folder, "installed-receipt.json")
        try:
            witness(receipt)
            value = _read(receipt)
            p, rows = receipt_rows(value)
            if not folder.name.startswith(p + "-"):
                raise api.ImportRejected("removal-receipt-package-mismatch")
            record(value, p)
            if p in selected:
                for row in rows.values():
                    if display_owned(p, row):
                        add(api._target(folder, row["path"]), p, row["path"], row, "history")
                # Keep the receipt if changed/unknown bytes still need provenance.
                histories.append((folder, p, receipt))
        except (ValueError, OSError) as error:
            residual(folder, str(error))

    for folder in _children(root, "work/map-packages/upgrades"):
        if not (folder / "journal.json").exists():
            residual(folder, "unindexed-upgrade-residue")
            continue  # Retired journals cannot revive packages; remaining core data is preserved.
        try:
            directory, journal = upgrade._load_journal(root, folder.name)
            p = journal["packageId"]
            witness(directory / "journal.json")
            receipt_name = f"work/map-packages/{p}.json"
            for side in ("backup", "new"):
                path = api._target(directory, side + "/" + receipt_name)
                if path.exists():
                    witness(path)
                    record(_read(path), p)
            if p not in selected:
                continue
            core = any(e["new"]["path"] != receipt_name and not display_owned(p, e["new"])
                       for e in journal["entries"])
            if core and journal["status"] not in {"committed", "rolled-back"}:
                raise api.ImportRejected("pending-core-upgrade-requires-recovery")
            for entry in journal["entries"]:
                logical = entry["new"]["path"]
                if logical != receipt_name and not display_owned(p, entry["new"]):
                    continue
                for side, row in (("new", entry["new"]), ("backup", entry["old"])):
                    if row is not None:
                        add(api._target(directory, side + "/" + logical), p, logical, row,
                            "metadata" if logical == receipt_name else "history")
            for name in ("journal.json", "pending-pointer.json"):
                add(directory / name, p, name, kind="metadata")
            pending = api._target(root, upgrade.PENDING)
            if pending.exists() and upgrade._pending_id(root) == folder.name:
                add(pending, p, upgrade.PENDING, kind="metadata")
        except (ValueError, OSError) as error:
            residual(folder, str(error))
            # A selected unresolved pending upgrade must not later resurrect removed data.
            if api._target(root, upgrade.PENDING).exists():
                raise api.ImportRejected("cleanup-pending-upgrade-unverified") from error
    if api._target(root, upgrade.PENDING).exists() and upgrade.PENDING not in files:
        raise api.ImportRejected("cleanup-pending-upgrade-unverified")

    for logical, candidates in sorted(proofs.items()):
        candidates = [(p, row) for p, row in candidates if p in selected]
        if not candidates:
            continue
        path = api._target(root, candidates[0][1]["path"])
        if not path.exists():
            continue
        if len(owners.get(logical, ())) > 1 or owners.get(logical, set()) - selected:
            residual(path, "shared-package-ownership")
            continue
        stamp = _stamp(path) if path.is_file() else None
        match = next(((p, row) for p, row in candidates if stamp and
                      (row["bytes"], row["sha256"]) == (stamp["bytes"], stamp["sha256"])), None)
        if match:
            add(path, match[0], match[1]["path"], match[1])
        else:
            residual(path, "modified-or-unverified")
    for p in selected & active.keys():
        path = api._target(root, f"work/map-packages/{p}.json")
        add(path, p, rel(path), kind="metadata")
    # Report unknown neighbours without claiming them. Bound traversal to author roots.
    for p in selected:
        for prefix in api.LIVE_AUTHOR_IMAGE_ROOTS[p]:
            folder = api._target(root, prefix.rstrip("/"))
            if folder.is_dir():
                for count, path in enumerate(folder.rglob("*")):
                    if count >= LIMIT:
                        raise api.ImportRejected("package-index-too-large")
                    api._no_links(path)
                    if path.is_file() and rel(path) not in files:
                        residual(path, residuals.get(rel(path), "unowned-neighbour"))
    for folder, p, receipt in histories:
        unknown = False
        for count, path in enumerate(folder.rglob("*")):
            if count >= LIMIT:
                raise api.ImportRejected("package-index-too-large")
            api._no_links(path)
            if path.is_file() and path != receipt and rel(path) not in files:
                unknown = True
                residual(path, "unverified-removal-residue")
        if not unknown:
            add(receipt, p, rel(receipt), kind="metadata")

    for folder in _children(root, "work/installer-backups"):
        index = next((folder / name for name in ("resources.txt", "existing.txt") if (folder / name).is_file()), None)
        if index is None:
            residual(folder, "unindexed-installer-backup")
            continue
        try:
            witness(index)
            for number, logical in enumerate(_index_lines(index)):
                if number >= LIMIT:
                    raise api.ImportRejected("package-index-too-large")
                logical = api._relative(logical.replace("\\", "/"))
                candidates = [(p, r) for p, r in proofs.get(logical.casefold(), ()) if p in selected]
                path = api._target(folder, f"files/{number}")
                if not candidates or not path.is_file():
                    continue
                stamp = _stamp(path)
                match = next(((p, r) for p, r in candidates if
                              (r["bytes"], r["sha256"]) == (stamp["bytes"], stamp["sha256"])), None)
                if match:
                    add(path, match[0], logical, match[1], "history")
                else:
                    residual(path, "modified-or-unverified-backup")
        except (ValueError, OSError) as error:
            residual(folder, str(error))
    for path in _children(root, "work/map-packages/downloads"):
        if path.is_file():
            packages = archives.get(_stamp(path)["sha256"], set())
            if packages and packages <= selected:
                add(path, sorted(packages)[0], rel(path), kind="cache")
            else:
                residual(path, "unverified-cache")
        else:
            residual(path, "unindexed-cache")

    transactions, pending_bytes, pending_count = [], 0, 0
    for directory in _children(root, STATE):
        journal = _load_transaction(root, directory)
        if not set(journal["packageIds"]) <= selected:
            raise api.ImportRejected("another-package-cleanup-pending")
        if journal["status"] == "empty":
            witnesses[rel(directory)] = {"emptyTransaction": _upgrade()._identity(directory.stat())}
        elif journal["status"] == "initializing":
            witness(directory / journal["initializationFile"])
        else:
            witness(directory / "journal.json")
        transactions.append(directory.name)
        for number, entry in enumerate(journal["entries"]):
            for side in ("old", "new"):
                path = directory / side / str(number)
                if path.exists():
                    witness(path)
                    pending_bytes += witnesses[rel(path)]["bytes"]
                    pending_count += 1
    if pending_only:
        files, residuals = {}, {}
    plan = dict(packageId=package, packageIds=sorted(selected), files=sorted(files.values(), key=lambda r: r["path"]),
                witnesses=witnesses, transactions=transactions, pendingOnly=pending_only,
                residuals=[dict(path=p, reason=r) for p, r in sorted(residuals.items())],
                fileCount=len(files) + pending_count, bytes=sum(r["bytes"] for r in files.values()) + pending_bytes,
                modifiedFiles=[p for p, r in residuals.items() if r == "modified-or-unverified"],
                missingFiles=[], preservedCoreFiles=sum(not display_owned(p, r) for p, rows in active.items() if p in selected for r in rows.values()),
                receiptSha256=witnesses.get(f"work/map-packages/{package}.json", {}).get("sha256", ""), restartRequired=False)
    plan["planSha256"] = hashlib.sha256(_encoded(plan)).hexdigest()
    return plan


def _valid_target(package, path):
    api = _api()
    api._relative(path)
    if display_owned(package, {"path": path}) or path == f"work/map-packages/{package}.json":
        return True
    if path == "work/map-packages/pending-upgrade.json":
        return True
    # Historical paths are still constrained; no settings/accounts/custom routes.
    parts = path.split("/")
    if path.startswith("work/installer-backups/"):
        return len(parts) == 5 and parts[3] == "files" and parts[4].isdigit()
    if path.startswith("work/map-package-removals/") and len(parts) >= 4:
        return parts[-1] == "installed-receipt.json" or display_owned(package, {"path": "/".join(parts[3:])})
    if path.startswith("work/map-packages/upgrades/") and len(parts) >= 5:
        _upgrade()._transaction_id(parts[3])
        rest = "/".join(parts[4:])
        return rest in {"journal.json", "pending-pointer.json"} or (parts[4] in {"backup", "new"} and
            (display_owned(package, {"path": "/".join(parts[5:])}) or "/".join(parts[5:]) == f"work/map-packages/{package}.json"))
    return path.startswith("work/map-packages/downloads/") and len(parts) == 4


def _load_transaction(root, directory):
    api = _api()
    _upgrade()._transaction_id(directory.name)
    source = directory / "journal.json"
    initializing = False
    if not source.exists():
        if _empty_transaction(directory):
            return dict(schema=SCHEMA, rootIdentity=_upgrade()._identity(root.stat()),
                        packageIds=[], status="empty", entries=[])
        # First journal publication can be interrupted after its complete temp
        # file is flushed. At this boundary no live file has been moved.
        children = list(directory.iterdir())
        for child in children:
            api._no_links(child)
        temporaries = [p for p in children if p.name.startswith("journal-") and p.name.endswith(".tmp")]
        if (len(temporaries) != 1 or {p.name for p in children} != {"old", "new", temporaries[0].name}
                or any(not (directory / side).is_dir() or any((directory / side).iterdir()) for side in ("old", "new"))):
            raise api.ImportRejected("unverified-lifecycle-initialization")
        source = temporaries[0]
        _upgrade()._transaction_id(source.name[len("journal-"):-len(".tmp")])
        initializing = True
    journal = _read(source)
    if (journal.get("schema") != SCHEMA or journal.get("rootIdentity") != _upgrade()._identity(root.stat())
            or journal.get("status") not in {"prepared", "committed"}
            or not isinstance(journal.get("entries"), list) or len(journal["entries"]) > LIMIT):
        raise api.ImportRejected("invalid-package-lifecycle-journal")
    for p in journal["packageIds"]:
        scope(p)
    paths = set()
    for entry in journal["entries"]:
        if entry["path"].casefold() in paths or entry["packageId"] not in journal["packageIds"] or not _valid_target(entry["packageId"], entry["path"]):
            raise api.ImportRejected("invalid-package-lifecycle-target")
        paths.add(entry["path"].casefold())
        for side in ("old", "new"):
            row = entry[side]
            if row is not None:
                _upgrade()._digest(row["sha256"])
                _upgrade()._valid_identity(row["identity"])
                if type(row["bytes"]) is not int or not 0 <= row["bytes"] <= api.MAX_TOTAL_BYTES:
                    raise api.ImportRejected("invalid-package-lifecycle-row")
    commit_receipt = journal.get("commitReceipt")
    if commit_receipt is not None:
        if (not journal["entries"] or journal["entries"][-1]["path"] != commit_receipt
                or journal["entries"][-1]["new"] is None
                or commit_receipt != f"work/map-packages/{journal['entries'][-1]['packageId']}.json"):
            raise api.ImportRejected("invalid-package-lifecycle-commit")
        if not initializing and journal["status"] == "prepared" and all(
                _upgrade()._matches(api._target(root, e["path"]), e["new"], e["new"]["identity"])
                if e["new"] else not api._target(root, e["path"]).exists() for e in journal["entries"]):
            journal = dict(journal, status="committed", inferredCommit=True)
    if initializing:
        if journal["status"] != "prepared":
            raise api.ImportRejected("unverified-lifecycle-initialization")
        for entry in journal["entries"]:
            live = api._target(root, entry["path"])
            old = entry["old"]
            if (old and not _upgrade()._matches(live, old, old["identity"])) or (old is None and live.exists()):
                raise api.ImportRejected("unverified-lifecycle-initialization")
        journal = dict(journal, status="initializing", initializationFile=source.name)
    return journal


def _empty_transaction(directory):
    """Only our exact empty initialization/finalization shape is recoverable without a journal."""
    api = _api()
    api._no_links(directory)
    for child in directory.iterdir():
        api._no_links(child)
        if child.name not in {"old", "new"} or not child.is_dir() or any(child.iterdir()):
            return False
    return True


def _discard_empty(directory):
    if not _empty_transaction(directory):
        raise _api().ImportRejected("unverified-lifecycle-initialization")
    for name in ("old", "new"):
        child = directory / name
        if child.exists():
            child.rmdir()
    directory.rmdir()


def _save_journal(directory, journal):
    """Clean an ordinary failed metadata write using the exact file we created."""
    api = _api()
    temporary = api._target(directory, "journal-" + str(uuid.uuid4()) + ".tmp")
    identity = None
    try:
        with temporary.open("xb") as stream:
            identity = _upgrade()._identity(os.fstat(stream.fileno()))
            stream.write(_encoded(journal))
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, api._target(directory, "journal.json"))
    except Exception:
        if identity is not None and temporary.exists():
            api._rollback_published(temporary, tuple(identity), api._sha(temporary))
        raise


def assert_no_pending(root):
    if _children(Path(root), STATE):
        raise _api().ImportRejected("package-cleanup-pending; retry-package-cleanup")


def _purge(directory, journal):
    api, upgrade = _api(), _upgrade()
    remaining = []
    for number, entry in enumerate(journal["entries"]):
        for side in ("old", "new"):
            row = entry[side]
            path = directory / side / str(number)
            if path.exists():
                try:
                    if row is None or not upgrade._matches(path, row, row["identity"]):
                        raise api.ImportRejected("lifecycle-retained-object-changed")
                    if not api._rollback_published(path, tuple(row["identity"]), row["sha256"]):
                        raise api.ImportRejected("lifecycle-object-not-removed")
                except (OSError, ValueError):
                    remaining.append(entry["path"])
    if not remaining:
        try:
            for side in ("old", "new"):
                if (directory / side).exists():
                    api._no_links(directory / side)
                    (directory / side).rmdir()
            api._no_links(directory / "journal.json")
            if _read(directory / "journal.json") != journal:
                raise api.ImportRejected("lifecycle-journal-changed")
            (directory / "journal.json").unlink()
            directory.rmdir()
        except (OSError, ValueError):
            remaining.append("transaction-metadata")
    return remaining


def _rollback(root, directory, journal):
    api, upgrade = _api(), _upgrade()
    for number, entry in reversed(list(enumerate(journal["entries"]))):
        target = api._target(root, entry["path"])
        old, new = entry["old"], entry["new"]
        saved = directory / "old" / str(number)
        if old and upgrade._matches(target, old, old["identity"]):
            continue
        if old and not upgrade._matches(saved, old, old["identity"]):
            raise api.ImportRejected("cleanup-rollback-incomplete")
        if target.exists():
            if new is None or not upgrade._matches(target, new, new["identity"]):
                raise api.ImportRejected("cleanup-rollback-conflict")
            if not api._rollback_published(target, tuple(new["identity"]), new["sha256"]):
                raise api.ImportRejected("cleanup-rollback-conflict")
        if old:
            target.parent.mkdir(parents=True, exist_ok=True)
            api._no_links(target.parent)
            os.link(saved, target)
    if _purge(directory, journal):
        raise api.ImportRejected("cleanup-rollback-complete-discard-pending")


def _transact(root, packages, changes, *, cancelled=None, verify=None, commit_receipt=None):
    """changes=(path, package, old stamp or None, new source or None). Receipt last."""
    api, upgrade = _api(), _upgrade()
    assert_no_pending(root)
    entries = []
    for path, package, old, source in changes:
        if not _valid_target(package, path):
            raise api.ImportRejected("invalid-package-lifecycle-target")
        new = None
        if source is not None:
            new = _stamp(source)
        entries.append(dict(path=path, packageId=package, old=old, new=new))
    directory = api._target(root, STATE + "/" + str(uuid.uuid4()))
    for side in ("old", "new"):
        (directory / side).mkdir(parents=True, exist_ok=True)
    journal = dict(schema=SCHEMA, rootIdentity=upgrade._identity(root.stat()),
                   packageIds=sorted(packages), status="prepared", entries=entries, commitReceipt=commit_receipt)
    try:
        _save_journal(directory, journal)
    except Exception:
        _discard_empty(directory)
        raise
    try:
        for number, change in enumerate(changes):
            source = change[3]
            if source is not None:
                row = entries[number]["new"]
                if not upgrade._matches(source, row, row["identity"]):
                    raise api.ImportRejected("package-staged-object-changed")
                os.link(source, directory / "new" / str(number))
        for number, entry in enumerate(entries):
            api._check_cancelled(cancelled)
            path = api._target(root, entry["path"])
            if verify is not None and entry["path"] == f"work/map-packages/{entry['packageId']}.json":
                verify()
            if entry["old"]:
                upgrade._move_owned(path, directory / "old" / str(number), entry["old"], entry["old"]["identity"])
            elif path.exists():
                raise api.ImportRejected("package-target-created-during-transaction")
            if entry["new"]:
                if not upgrade._matches(directory / "new" / str(number), entry["new"], entry["new"]["identity"]):
                    raise api.ImportRejected("package-staged-object-changed")
                path.parent.mkdir(parents=True, exist_ok=True)
                api._no_links(path.parent)
                os.link(directory / "new" / str(number), path)
        if commit_receipt is None:
            api._check_cancelled(cancelled)
        journal["status"] = "committed"
        _save_journal(directory, journal)
    except Exception:
        journal = _load_transaction(root, directory)
        if journal.get("status") == "committed":
            return ["transaction-metadata"]
        _rollback(root, directory, journal)
        raise
    return _purge(directory, journal)


def cleanup_live(root, package=None, *, application_idle=False, expected_plan_sha256=None, cancelled=None, pending_only=False):
    api = _api()
    if application_idle is not True or not expected_plan_sha256:
        raise api.ImportRejected("cleanup-requires-idle-and-confirmation")
    with api._writer_locked_installation(root, require_closed=False) as (root, _state):
        plan = inspect_cleanup(root, package, cancelled=cancelled, pending_only=pending_only)
        if plan["planSha256"] != expected_plan_sha256:
            raise api.ImportRejected("author-files-changed-reinspect-required")
        for transaction in plan["transactions"]:
            directory = api._target(root, STATE + "/" + transaction)
            journal = _load_transaction(root, directory)
            if journal["status"] == "initializing":
                source = api._target(directory, journal["initializationFile"])
                stamp = _stamp(source)
                if not api._rollback_published(source, tuple(stamp["identity"]), stamp["sha256"]):
                    raise api.ImportRejected("lifecycle-initialization-cleanup-failed")
                _discard_empty(directory)
                continue
            if journal["status"] == "empty":
                _discard_empty(directory)
                continue
            if journal.pop("inferredCommit", False):
                _save_journal(directory, journal)
            if journal["status"] == "prepared":
                _rollback(root, directory, journal)
                return dict(status="cleanup-restored-reinspect-required", changed=True, cleanupPending=False,
                            removedFiles=0, residuals=plan["residuals"], restartRequired=False)
            if _purge(directory, journal):
                return dict(status="cleanup-pending", changed=True, cleanupPending=True,
                            removedFiles=0, residuals=plan["residuals"], restartRequired=False)
        changes = [(r["path"], r["packageId"], {k: r[k] for k in ("bytes", "sha256", "identity")}, None)
                   for r in plan["files"]]
        remaining = _transact(root, plan["packageIds"], changes, cancelled=cancelled) if changes else []
        return dict(status="cleanup-pending" if remaining else "cleanup-finished" if pending_only else "author-removed-live", changed=bool(changes or plan["transactions"]),
                    cleanupPending=bool(remaining), packageId=package,
                    removedFiles=sum(r["kind"] == "display" for r in plan["files"]),
                    deletedFiles=len(changes), bytes=plan["bytes"], residuals=plan["residuals"],
                    preservedCoreFiles=plan["preservedCoreFiles"], restartRequired=False)


def replace_author_locked(root, result, stage, *, cancelled=None):
    """Same stable package, only unchanged receipt-owned display files; no durable backup."""
    api = _api()
    package = result["packageId"]
    receipt = api._target(root, f"work/map-packages/{package}.json")
    old = _stamp(receipt)
    if old["sha256"] != result["previousReceiptSha256"]:
        raise api.ImportRejected("upgrade-old-receipt-changed")
    changes = []
    replacements = api._rows(result["replaceFiles"]) if result["replaceFiles"] else {}
    missing = api._rows(result["missingFiles"]) if result["missingFiles"] else {}
    def capture_expected(row):
        target = api._target(root, row["path"])
        stamp = _stamp(target)
        if (stamp["bytes"], stamp["sha256"]) != (row["bytes"], row["sha256"]):
            raise api.ImportRejected("upgrade-owned-file-changed")
        return stamp
    for row in result.get("removeFiles", []):
        changes.append((row["path"], package, capture_expected(row), None))
    for row in result["files"]:
        key = row["path"].casefold()
        if key in replacements or key in missing:
            stamp = capture_expected(replacements[key]) if key in replacements else None
            if not _upgrade()._matches(api._target(stage, row["path"]), row):
                raise api.ImportRejected("upgrade-staged-data-changed")
            changes.append((row["path"], package, stamp, api._target(stage, row["path"])))
    output = dict(result, status="author-upgraded-live", liveImport=True, restartRequired=False,
                  replaced=len(replacements), added=len(missing), removed=len(result.get("removeFiles", [])),
                  authorFilesReplaced=len(replacements), authorFilesAdded=len(missing),
                  authorFilesAlreadyPresent=sum(display_owned(package, r) for r in result["files"]) - len(replacements) - len(missing))
    temporary = stage / "lifecycle-receipt.json"
    temporary.write_bytes(_encoded(output))
    changes.append((f"work/map-packages/{package}.json", package, old, temporary))

    def verify():
        for row in result["files"]:
            if not _upgrade()._matches(api._target(root, row["path"]), row):
                raise api.ImportRejected("upgrade-data-changed-before-receipt-publication")
    remaining = _transact(root, [package], changes, cancelled=cancelled, verify=verify,
                          commit_receipt=f"work/map-packages/{package}.json")
    output["cleanupPending"] = bool(remaining)
    return output
