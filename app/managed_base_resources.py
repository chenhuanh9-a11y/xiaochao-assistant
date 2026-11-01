"""Manifest-owned base repair, before resource consumers and under the shared lease.

Setup installs candidates only. A complete preflight precedes any active-file
change. Durable journals retain old/new objects for guarded rollback after an
exception or process loss; this is not a power-loss atomic filesystem claim.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re
import uuid

import map_package_upgrade as transaction
import star_map_import as files

SCHEMA = "xingtu-managed-base-v1"
CANDIDATE = "_internal/managed-base-resources"
STATE = "work/map-packages/managed-base-state.json"
PENDING = "work/map-packages/pending-base-repair.json"
JOURNAL_SCHEMA = "xingtu-base-repair-v1"


class ManagedBaseResourceError(ValueError):
    def __init__(self, reason, issues=()):
        self.reason, self.issues = reason, list(issues)
        self.code = reason.split(";", 1)[0].split(":", 1)[0]
        self.details = {"issues": [{key: row[key] for key in ("path", "reason", "expected", "actual") if key in row}
                                   for row in self.issues[:12]], "issueCount": len(self.issues)}
        detail = "; ".join(f"{row.get('reason')}: {row.get('path')}" for row in self.issues)
        super().__init__(reason + (": " + detail if detail else ""))


def _base_path(path):
    path = files._relative(path)
    return (path == "_internal/difficult-native-manifest.json" or
            path.startswith(("_internal/assets/", "_internal/references/", "_internal/recognition/"))) and not (
                path.startswith("_internal/assets/custom-maps/") or
                path == "_internal/references/build-fingerprint.txt")


def _rows(values):
    # The base bundle can exceed an individual author's 2,000-file limit.
    if not isinstance(values, list) or not 0 < len(values) <= 20000:
        raise ManagedBaseResourceError("invalid-base-inventory")
    rows = {}
    for value in values:
        row = next(iter(files._rows([value]).values()))
        key = row["path"].casefold()
        if key in rows or not _base_path(row["path"]):
            raise ManagedBaseResourceError("invalid-base-path", [dict(path=row["path"], reason="not-base-or-duplicate")])
        rows[key] = row
    return rows


def _encoded(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode("utf-8")


def manifest_for_rows(rows, resource_version=""):
    rows = sorted(_rows(rows).values(), key=lambda row: row["path"])
    return dict(schema=SCHEMA, resourceVersion=resource_version,
                bundleId=hashlib.sha256(_encoded(rows)).hexdigest(), files=rows)


def _manifest(path):
    value = transaction._read_json(path)
    if value.get("schema") != SCHEMA:
        raise ManagedBaseResourceError("invalid-base-manifest-schema")
    expected = manifest_for_rows(value.get("files"), value.get("resourceVersion", ""))
    if value.get("bundleId") != expected["bundleId"]:
        raise ManagedBaseResourceError("invalid-base-manifest-bundle-id")
    return expected


def managed_base_inventory(install_root):
    """Protected paths from current and candidate base manifests; call under lease.

    Preserve both generations while repair may be blocked. Invalid state is an
    error, never an empty ownership claim for a removal operation.
    """
    root = Path(os.path.abspath(install_root))
    protected = {}
    for name in (STATE, CANDIDATE + "/manifest.json"):
        path = files._target(root, name)
        if path.exists():
            for row in _manifest(path)["files"]:
                protected.setdefault(row["path"].casefold(), []).append(row)
    return protected


def _actual(path):
    files._no_links(path)
    if not path.exists():
        return dict(status="missing")
    if not path.is_file():
        return dict(status="not-file")
    return dict(status="hashed", bytes=path.stat().st_size, sha256=files._sha(path))


def _same(actual, expected):
    return actual.get("bytes") == expected["bytes"] and actual.get("sha256") == expected["sha256"]


def _issue(row, actual, reason, **extra):
    return dict(path=row["path"], expected=dict(bytes=row["bytes"], sha256=row["sha256"]),
                actual=actual, reason=reason, **extra)


def _package_constraints(root):
    owned, dependencies, unrecorded = {}, [], []
    directory = files._target(root, "work/map-packages")
    if not directory.exists():
        return owned, dependencies, unrecorded
    # This directory also carries handoff/result metadata. These exact writer
    # namespaces are not ownership receipts; ignore their contents here, leave
    # them intact, and let their own consumers validate them. Unknown JSON is
    # still rejected rather than silently classed as an installed author.
    reserved = {Path(STATE).name, Path(PENDING).name, "pending-upgrade.json",
                "pending-additions.json", "pending-removal.json", "qt-import-pending.json",
                "qt-recovery-pending.json", "last-import-result.json", "last-recovery-result.json",
                "qt-import-notice-seen.json", "main-import-request.json"}
    for path in sorted(directory.glob("*.json")):
        if (path.name in reserved or re.fullmatch(r"recovery-request-[A-Za-z0-9-]{1,64}\.json", path.name)
                or re.fullmatch(r"helper-result-[a-z0-9_]{8}\.json", path.name)):
            continue
        package = path.stem
        if package not in files.PACKAGE_IDS:
            raise ManagedBaseResourceError("unrecognized-installed-package-receipt", [dict(path="work/map-packages/" + path.name, reason="unknown-package")])
        inventory, _ = files._owned_inventory(root, package)
        receipt = transaction._read_json(path)
        for key, row in inventory.items():
            owned.setdefault(key, []).append((package, row))
        if "requiresFiles" not in receipt:
            unrecorded.append(package)
        else:
            deps = receipt["requiresFiles"]
            if deps:
                dependencies.extend((package, row) for row in files._rows(deps, dependencies=True).values())
    return owned, dependencies, unrecorded


def plan_managed_base_resources(install_root):
    """Read-only complete preflight. Call under a resource lease for a stable view."""
    root = Path(os.path.abspath(install_root))
    candidate = files._target(root, CANDIDATE + "/manifest.json")
    if not candidate.exists():
        for name in ("installation-profile.json", "_internal/installation-profile.json"):
            profile = files._target(root, name)
            if profile.is_file():
                policy = transaction._read_json(profile).get("resourceInstallationPolicy")
                if isinstance(policy, dict) and policy.get("existingBasePlane") == "manifest-owned-transaction-before-runtime":
                    raise ManagedBaseResourceError("base-candidate-manifest-missing", [dict(path=CANDIDATE + "/manifest.json", reason="missing")])
        return dict(status="not-managed", files=[], changes=[], issues=[])
    manifest = _manifest(candidate)
    inventory = _rows(manifest["files"])
    state = files._target(root, STATE)
    previous = _rows(_manifest(state)["files"]) if state.exists() else {}
    owned, dependencies, unrecorded = _package_constraints(root)
    issues, changes = [], []
    for key, row in inventory.items():
        source = files._target(root, CANDIDATE + "/payload/" + row["path"])
        if not transaction._matches(source, row):
            issues.append(_issue(row, _actual(source), "candidate-integrity-mismatch"))
        actual = _actual(files._target(root, row["path"]))
        if _same(actual, row):
            continue
        for package, author_row in owned.get(key, []):
            if author_row != row:
                issues.append(_issue(row, actual, "installed-package-owned-conflict", packageId=package))
        old = previous.get(key)
        if actual["status"] == "missing":
            changes.append(dict(new=row, old=None))
        elif old is not None and _same(actual, old):
            if unrecorded:
                issues.append(_issue(row, actual, "installed-package-dependencies-unrecorded", packages=unrecorded))
            changes.append(dict(new=row, old=old))
        else:
            issues.append(_issue(row, actual, "base-ownership-unknown"))
    # Check every receipt constraint against the complete prospective set, even
    # for paths not directly changed by this repair.
    for package, row in dependencies:
        desired = inventory.get(row["path"].casefold())
        actual = desired or _actual(files._target(root, row["path"]))
        if not _same(actual, row):
            issues.append(_issue(row, actual, "installed-package-dependency-conflict", packageId=package))
    return dict(status="blocked" if issues else "repair-needed" if changes else "healthy",
                manifest=manifest, files=manifest["files"], changes=changes, issues=issues)


def _write_journal(directory, journal):
    transaction._write_journal(directory, journal)


def _clear_pending(root, transaction_id):
    pointer = files._target(root, PENDING)
    if transaction._read_json(pointer) != dict(schema=JOURNAL_SCHEMA, transactionId=transaction_id):
        raise ManagedBaseResourceError("base-pending-pointer-changed")
    pointer.unlink()
    transaction._directory_sync(pointer.parent)


def _rollback(root, directory, journal):
    blocked = []
    # Preflight the entire restoration; changed unknown files remain untouched.
    for entry in journal["entries"]:
        new, old = entry["new"], entry["old"]
        path = files._target(root, new["path"])
        if old is not None and transaction._matches(path, old, entry["oldIdentity"]):
            continue
        if old is not None and not transaction._matches(files._target(directory, "backup/" + new["path"]), old, entry["oldIdentity"]):
            blocked.append(new["path"])
        if path.exists() and not transaction._matches(path, new, entry["newIdentity"]):
            blocked.append(new["path"])
    if not blocked:
        for entry in reversed(journal["entries"]):
            new, old = entry["new"], entry["old"]
            path = files._target(root, new["path"])
            if old is not None and transaction._matches(path, old, entry["oldIdentity"]):
                continue
            try:
                if path.exists() and not files._rollback_published(path, tuple(entry["newIdentity"]), new["sha256"]):
                    blocked.append(new["path"])
                    continue
                if old is not None:
                    path.parent.mkdir(parents=True, exist_ok=True)
                    files._no_links(path.parent)
                    os.link(files._target(directory, "backup/" + new["path"]), path)
                transaction._directory_sync(path.parent)
            except OSError:
                blocked.append(new["path"])
    journal.update(status="rollback-incomplete" if blocked else "rolled-back", blockedPaths=blocked)
    _write_journal(directory, journal)
    if blocked:
        raise ManagedBaseResourceError("base-rollback-incomplete; retained-backups-and-pending-marker",
                                       [dict(path=path, reason="changed-or-unwritable") for path in blocked])
    _clear_pending(root, journal["transactionId"])


def _load_pending(root):
    pointer = transaction._read_json(files._target(root, PENDING))
    transaction_id = pointer.get("transactionId")
    if (pointer.get("schema") != JOURNAL_SCHEMA or not isinstance(transaction_id, str)
            or not re.fullmatch(r"[0-9a-f]{8}-(?:[0-9a-f]{4}-){3}[0-9a-f]{12}", transaction_id)):
        raise ManagedBaseResourceError("invalid-base-pending-pointer")
    directory = files._target(root, "work/map-packages/base-repairs/" + transaction_id)
    journal = transaction._read_json(files._target(directory, "journal.json"))
    if (journal.get("schema") != JOURNAL_SCHEMA or journal.get("transactionId") != transaction_id
            or journal.get("rootIdentity") != transaction._identity(root.stat())
            or journal.get("status") not in {"prepared", "committed", "rolled-back", "rollback-incomplete"}):
        raise ManagedBaseResourceError("invalid-base-repair-journal")
    expected = _rows(journal.get("files"))
    seen = set()
    entries = journal.get("entries")
    if not isinstance(entries, list) or not entries or len(entries) > len(expected) + 1:
        raise ManagedBaseResourceError("invalid-base-repair-entries")
    for entry in entries:
        row = next(iter(files._rows([entry.get("new")]).values()))
        key = row["path"].casefold()
        if key in seen or (row["path"] != STATE and expected.get(key) != row):
            raise ManagedBaseResourceError("invalid-base-repair-target")
        seen.add(key)
        transaction._valid_identity(entry.get("newIdentity"))
        if not transaction._matches(files._target(directory, "new/" + row["path"]), row, entry["newIdentity"]):
            raise ManagedBaseResourceError("base-repair-retained-object-changed")
        old = entry.get("old")
        if old is not None:
            files._rows([old])
            transaction._valid_identity(entry.get("oldIdentity"))
            if old["path"] != row["path"]:
                raise ManagedBaseResourceError("invalid-base-repair-old-target")
    if entries[-1]["new"]["path"] != STATE:
        raise ManagedBaseResourceError("invalid-base-repair-state-entry")
    return directory, journal


def _recover(root):
    if not files._target(root, PENDING).exists():
        return dict(status="no-pending-base-repair")
    directory, journal = _load_pending(root)
    if journal["status"] == "committed":
        for row in journal["files"]:
            if not transaction._matches(files._target(root, row["path"]), row):
                raise ManagedBaseResourceError("committed-base-file-changed")
        state = journal["entries"][-1]
        if not transaction._matches(files._target(root, STATE), state["new"], state["newIdentity"]):
            raise ManagedBaseResourceError("committed-base-state-changed")
        _clear_pending(root, journal["transactionId"])
        return dict(status="committed")
    _rollback(root, directory, journal)
    return dict(status="rolled-back")


def recover_managed_base_resources(install_root):
    """Recover only verified objects; never overwrite an unknown post-crash edit."""
    from resource_transaction import resource_write_lock
    with resource_write_lock(install_root) as root:
        return _recover(Path(root))


def repair_managed_base_resources(install_root):
    """Repair once before creating providers/caches, while the runtime owns its lease."""
    from resource_transaction import resource_write_lock, preflight_hardlinks
    with resource_write_lock(install_root) as root:
        root = Path(root)
        if files._target(root, PENDING).exists():
            _recover(root)
        for name, code in (("pending-upgrade.json", "author-upgrade-recovery-required"),
                           ("pending-additions.json", "additions-recovery-required"),
                           ("pending-removal.json", "removal-recovery-required")):
            if files._target(root, "work/map-packages/" + name).exists():
                raise ManagedBaseResourceError(code)
        plan = plan_managed_base_resources(root)
        if plan["status"] == "not-managed":
            return plan
        if plan["issues"]:
            raise ManagedBaseResourceError("base-repair-preflight-blocked", plan["issues"])
        state = files._target(root, STATE)
        encoded = _encoded(plan["manifest"])
        if not plan["changes"] and state.exists() and state.read_bytes() == encoded:
            return dict(status="healthy", bundleId=plan["manifest"]["bundleId"], checked=len(plan["files"]), changed=0)
        state_row = dict(path=STATE, bytes=len(encoded), sha256=hashlib.sha256(encoded).hexdigest())
        old_state = dict(path=STATE, bytes=state.stat().st_size, sha256=files._sha(state)) if state.exists() else None
        changes = [*plan["changes"], dict(new=state_row, old=old_state)]
        preflight_hardlinks(root, [entry["new"]["path"] for entry in changes])
        transaction_id = str(uuid.uuid4())
        directory = files._target(root, "work/map-packages/base-repairs/" + transaction_id)
        directory.mkdir(parents=True, exist_ok=False)
        entries = []
        for change in changes:
            row, old = change["new"], change["old"]
            target = files._target(root, row["path"])
            retained = files._target(directory, "new/" + row["path"])
            data = encoded if row["path"] == STATE else files._target(root, CANDIDATE + "/payload/" + row["path"]).read_bytes()
            transaction._write_new(retained, data)
            entries.append(dict(new=row, old=old, newIdentity=transaction._capture(retained, row),
                                oldIdentity=transaction._capture(target, old) if old else None))
            if old is None and target.exists():
                raise ManagedBaseResourceError("base-target-appeared-before-commit")
        journal = dict(schema=JOURNAL_SCHEMA, transactionId=transaction_id, rootIdentity=transaction._identity(root.stat()),
                       status="prepared", files=plan["files"], entries=entries, blockedPaths=[])
        _write_journal(directory, journal)
        transaction._write_new(files._target(root, PENDING), _encoded(dict(schema=JOURNAL_SCHEMA, transactionId=transaction_id)))
        try:
            for entry in entries:
                row = entry["new"]
                target = files._target(root, row["path"])
                if row["path"] == STATE:
                    for current in plan["files"]:
                        if not transaction._matches(files._target(root, current["path"]), current):
                            raise ManagedBaseResourceError("base-verification-before-state-failed")
                if entry["old"]:
                    transaction._move_owned(target, files._target(directory, "backup/" + row["path"]),
                                            entry["old"], entry["oldIdentity"])
                target.parent.mkdir(parents=True, exist_ok=True)
                files._no_links(target.parent)
                os.link(files._target(directory, "new/" + row["path"]), target)
                if not transaction._matches(target, row, entry["newIdentity"]):
                    raise ManagedBaseResourceError("base-published-object-changed")
                transaction._directory_sync(target.parent)
            journal["status"] = "committed"
            _write_journal(directory, journal)
            _clear_pending(root, transaction_id)
        except Exception as error:
            try:
                _rollback(root, directory, journal)
            except Exception as rollback_error:
                raise ManagedBaseResourceError(f"base-repair-failed-and-rollback-incomplete: {rollback_error}") from error
            raise
        return dict(status="repaired" if plan["changes"] else "healthy", bundleId=plan["manifest"]["bundleId"],
                    checked=len(plan["files"]), changed=len(plan["changes"]), transactionId=transaction_id)
