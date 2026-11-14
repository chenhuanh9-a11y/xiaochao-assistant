"""Offline QtIFW upgrade adapter; native components retain file ownership.

The native Setup invokes this adapter. The pending record prevents startup
after native update failure; old files are copied before QtIFW can undo them.
"""
from __future__ import annotations

from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
import shutil
import stat
import subprocess
import sys
import uuid
import xml.etree.ElementTree as ET

from resource_transaction import _safe, _assert_resource_transactions_ready, resource_write_lock, rename_no_replace
from resource_locations import installed_resource_root

PENDING = "work/pending-installer-upgrade.json"
PACKAGE = "org.xingtu.qtifw.realtrial"
DATA_PACKAGE = "org.xingtu.qtifw.trialdata"
PREVIOUS_VERSIONS = frozenset({"1.1.2", "1.1.3", "1.1.4", "1.1.5", "1.1.6", "1.1.7"})
BRAND_MIGRATIONS = frozenset({"_internal/assets/brand/xingtu.ico", "_internal/assets/brand/xingtu-round.png"})


def _installed_resource_root(target):
    """Use this installation's user-owned resources beside its executable."""
    root = _safe(installed_resource_root(Path(target) / "program"))
    if root.exists() and not root.is_dir():
        raise ValueError("upgrade-installed-resource-root-invalid")
    return root


def _installed_catalog_ready(root):
    # These are the independent catalog's own transaction markers. An admitted
    # native program marker never authorizes ignoring either resource writer.
    for name, code in (("pending-catalog.json", "resource-catalog-recovery-required"),
                       ("pending-removal.json", "resource-removal-recovery-required")):
        if _safe(root / "transactions" / name).exists():
            raise ValueError(code)


@contextmanager
def _installed_resource_lease(target):
    from resource_transaction import resource_read_lock
    root = _installed_resource_root(target)
    if root.exists():
        with resource_read_lock(root):
            _installed_catalog_ready(root)
            yield
    else:
        # Program-only Setup does not create, seed, own or delete this plane.
        yield


def _native_start(command, **kwargs):
    """Make Windows loader failures return to this CLI instead of waiting on UI.

    Only the thread creating this child
    temporarily adds SEM_FAILCRITICALERRORS; no process/system setting changes.
    """
    if os.name != "nt":
        return subprocess.Popen(command, **kwargs)
    import ctypes
    from ctypes import wintypes as wt
    api = ctypes.WinDLL("kernel32", use_last_error=True)
    api.GetThreadErrorMode.argtypes = []
    api.GetThreadErrorMode.restype = wt.DWORD
    api.SetThreadErrorMode.argtypes = [wt.DWORD, ctypes.POINTER(wt.DWORD)]
    api.SetThreadErrorMode.restype = wt.BOOL
    previous = api.GetThreadErrorMode()
    if not api.SetThreadErrorMode(previous | 1, None):
        raise ctypes.WinError(ctypes.get_last_error())
    child = None
    try:
        child = subprocess.Popen(command, **kwargs)
        return child
    finally:
        if not api.SetThreadErrorMode(previous, None):
            if child is not None:
                # The caller still owns its resource leases. Do not let it
                # publish a retryable failure while native work is alive.
                # Drain captured output as well, so pipe buffers cannot block
                # the owned child's natural exit.
                with child:
                    child.communicate()
            raise ValueError("native-launch-error-mode-restoration-failed")


def _read(path):
    path = _safe(Path(path))
    if not path.is_file() or path.stat().st_size > 16 * 1024 * 1024:
        raise ValueError("upgrade-record-unavailable:" + path.name)
    return json.loads(path.read_bytes())


def _write(path, data):
    path = _safe(Path(path))
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = _safe(path.with_name(path.name + "." + uuid.uuid4().hex + ".new"))
    with temporary.open("x", encoding="utf-8") as stream:
        json.dump(data, stream, ensure_ascii=False)
        stream.flush()
        os.fsync(stream.fileno())
    temporary.replace(path)


def _identity(target):
    info = target.stat()
    return [info.st_dev, info.st_ino]


def pending(policy, target):
    from qtifw_trial_helper import OWNER, scoped
    target = scoped(policy, target)
    path = _safe(target / "program" / PENDING)
    if not path.exists():
        return None
    record = _read(path)
    if (record.get("schema") != "xingtu-qtifw-upgrade-v1"
            or record.get("instance") != policy["instance"]
            or record.get("payloadSource") != policy["payloadSource"]
            or record.get("target") != str(target)
            or record.get("rootIdentity") != _identity(target)
            or record.get("stage") not in {"backing-up", "prepared", "native-in-progress", "native-complete"}):
        raise ValueError("upgrade-pending-context-mismatch")
    owner = _read(target / OWNER)
    current = (owner.get("instance"), owner.get("payloadSource"))
    previous = record["previousOwner"]
    if current not in {(previous["instance"], previous["payloadSource"]),
                       (policy["instance"], policy["payloadSource"])}:
        raise ValueError("upgrade-pending-owner-mismatch")
    backup = Path(record["backup"])
    if len(backup.parts) != 2 or backup.parts[0] != "recovery-files" or uuid.UUID(backup.parts[1]).hex != backup.parts[1]:
        raise ValueError("upgrade-backup-path-invalid")
    _safe(target / backup)
    return record


def accepts_previous_owner(policy, target, owner):
    if not policy.get("upgradeCapable"):
        return False
    record = pending(policy, target)
    return record is not None and owner == record["previousOwner"]


def _versions(target):
    path = _safe(target / "components.xml")
    if not path.is_file() or path.stat().st_size > 4 * 1024 * 1024:
        raise ValueError("qtifw-component-record-unavailable")
    tree = ET.parse(path)
    return {p.findtext("Name"): p.findtext("Version") for p in tree.findall("Package")}


def _matches(path, row):
    path = _safe(path)
    if not path.exists():
        return False
    info = path.stat()
    if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
        raise ValueError("program-object-not-exclusive-file")
    if info.st_size != row["bytes"]:
        return False
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest() == row["sha256"]


def _brand_adoptions(policy, target, old):
    """Recognize only active brand copies declared by the verified old bundle."""
    from qtifw_trial_helper import owned_program
    collisions = {name: path for name, path, _ in owned_program(policy, target) if name not in old["files"]}
    if not collisions:
        return {}
    if collisions.keys() - BRAND_MIGRATIONS:
        raise ValueError("unowned-upgrade-program-collision:" + sorted(collisions.keys() - BRAND_MIGRATIONS)[0])
    from managed_base_resources import CANDIDATE, _manifest
    manifest_name = CANDIDATE + "/manifest.json"
    manifest_path = target / "program" / manifest_name
    if manifest_name not in old["files"] or not _matches(manifest_path, old["files"][manifest_name]):
        raise ValueError("unowned-upgrade-program-collision:" + sorted(collisions)[0])
    rows = {row["path"]: row for row in _manifest(manifest_path)["files"]}
    adopted = {}
    for name, active in collisions.items():
        row = rows.get(name)
        expected = {key: row[key] for key in ("bytes", "sha256")} if row else None
        candidate = CANDIDATE + "/payload/" + name
        if (expected is None or old["files"].get(candidate) != expected
                or not _matches(target / "program" / candidate, expected) or not _matches(active, expected)):
            raise ValueError("unowned-upgrade-program-collision:" + name)
        adopted[name] = expected
    return adopted


def _retry_program(policy, target, record):
    """A native marker permits only absent, old, or exact new program bytes."""
    from qtifw_trial_helper import owned_program
    old = record["previousPolicy"]
    adopted = record.get("adoptedBrandFiles", {})
    # Native UNDO may already have removed the old candidate. The checked
    # backup retains its manifest/payload proof and the adopted active copies.
    if _brand_adoptions(policy, target / record["backup"], old) != adopted:
        raise ValueError("upgrade-brand-backup-context-mismatch")
    previous = dict(old["files"], **adopted)
    combined = dict(previous, **policy["files"])
    for name, path, _ in owned_program(dict(files=combined), target):
        if not any(_matches(path, row) for row in (previous.get(name), policy["files"].get(name)) if row is not None):
            raise ValueError("upgrade-intermediate-program-byte-mismatch:" + name)


def _old_install(policy, target, *, verify_files=True):
    from qtifw_trial_helper import OWNER, bound_owner, verify_program
    old = _read(target / "_installer-support/_internal/qtifw-trial-policy.json")
    if not (old.get("portable") is True and old.get("fullUninstall") is True
            and old.get("allowNewTargets") is True and isinstance(old.get("files"), dict)):
        raise ValueError("unsupported-qtifw-installation")
    _, owner = bound_owner(old, target)
    profile = _read(target / "program/_internal/installation-profile.json")
    report = _read(target / "program/release-report.json")
    if (owner.get("phase") != "complete" or profile.get("product") != "星图"
            or profile.get("installerInstance", {}).get("id") != "production"
            or profile["installerInstance"].get("appId") != "D04394A0-DACE-4FF9-874D-3DCB43EE53D1"
            or profile.get("sourceCommit") != old.get("payloadSource")
            or report.get("sourceCommit") != old.get("payloadSource")
            or report.get("displayVersion") not in PREVIOUS_VERSIONS | {policy["targetVersion"]["displayVersion"]}):
        raise ValueError("unsupported-upgrade-source")
    if report["displayVersion"] == policy["targetVersion"]["displayVersion"] and (old["payloadSource"] != policy["payloadSource"]
            or old["instance"] != policy["instance"]):
        raise ValueError("different-same-version-installation")
    maintenance = _safe(target / "trial-maintenance.exe")
    if not maintenance.is_file() or maintenance.stat().st_nlink != 1:
        raise ValueError("qtifw-maintenance-unavailable")
    versions = _versions(target)
    if not {PACKAGE, DATA_PACKAGE} <= versions.keys():
        raise ValueError("unsupported-qtifw-components")
    # Discovery can shortlist by verified owner/profile/component metadata.
    # Actual admission always repeats the complete byte/collision validation.
    if verify_files:
        verify_program(old, target)
    adopted = _brand_adoptions(policy, target, old) if verify_files else {}
    return old, _read(target / OWNER), adopted


def check(policy, target):
    from qtifw_trial_helper import OWNER, check_target, scoped
    if not policy.get("upgradeCapable"):
        raise ValueError("upgrade-not-included")
    target = scoped(policy, target)
    _installed_catalog_ready(_installed_resource_root(target))
    if not _safe(target / OWNER).exists():
        check_target(policy, target)
        return dict(mode="fresh", target=str(target))
    record = pending(policy, target)
    if record is None:
        owner = _read(target / OWNER)
        if (owner.get("instance") == policy["instance"] and owner.get("payloadSource") == policy["payloadSource"]
                and owner.get("phase") == "installing" and not _safe(target / "trial-maintenance.exe").exists()):
            # Preserve the original same-Setup first-install recovery: native
            # prepare quarantines its partial files before extracting again.
            check_target(policy, target)
            return dict(mode="fresh", target=str(target), recovery=True)
        _old_install(policy, target)
    elif record["stage"] in {"backing-up", "prepared"}:
        # No native work has started: retry must retain the first admission's
        # old-byte and new-only collision protections, including late arrivals.
        previous_policy, previous_owner, adopted = _old_install(policy, target)
        if (previous_policy != record["previousPolicy"] or previous_owner != record["previousOwner"]
                or adopted != record.get("adoptedBrandFiles", {})):
            raise ValueError("upgrade-pre-native-source-changed")
    else:
        _retry_program(policy, target, record)
    return dict(mode="update", target=str(target), recovery=record is not None)


def _copy_checked(source, destination, expected=None):
    source, destination = _safe(source), _safe(destination)
    if source.stat().st_nlink != 1 or not stat.S_ISREG(source.stat().st_mode):
        raise ValueError("upgrade-source-not-exclusive-file")
    data = source.read_bytes()
    if expected and (len(data) != expected["bytes"] or hashlib.sha256(data).hexdigest() != expected["sha256"]):
        raise ValueError("upgrade-backup-source-changed")
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.exists():
        if destination.read_bytes() != data:
            raise ValueError("upgrade-backup-conflict")
    else:
        temporary = _safe(destination.with_name(destination.name + "." + uuid.uuid4().hex + ".new"))
        with temporary.open("xb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        rename_no_replace(temporary, destination)
    if destination.read_bytes() != data:
        raise ValueError("upgrade-backup-verification-failed")


def _prepare(policy, target):
    from qtifw_trial_helper import OWNER
    record = pending(policy, target)
    if record is None:
        old, owner, adopted = _old_install(policy, target)
        from game_startup_watcher import control
        from game_startup_win32 import Windows
        state = control("--status", target / "program", Windows())
        if state not in {0, 10}:
            raise ValueError("upgrade-watcher-state-unavailable:" + str(state))
        record = dict(schema="xingtu-qtifw-upgrade-v1", instance=policy["instance"],
            payloadSource=policy["payloadSource"], target=str(target), rootIdentity=_identity(target),
            previousOwner=owner, previousPolicy=old, adoptedBrandFiles=adopted,
            backup="recovery-files/" + uuid.uuid4().hex,
            stage="backing-up", watcherWasEnabled=state == 10)
        _write(target / "program" / PENDING, record)
    if record["stage"] == "backing-up":
        backup = target / record["backup"]
        previous = dict(record["previousPolicy"]["files"], **record.get("adoptedBrandFiles", {}))
        for name, expected in previous.items():
            _copy_checked(target / "program" / name, backup / "program" / name, expected)
        for name in (OWNER, "components.xml", "trial-maintenance.exe", "trial-maintenance.dat", "trial-maintenance.ini"):
            source = _safe(target / name)
            if source.is_file():
                _copy_checked(source, backup / name)
        record["stage"] = "prepared"
        _write(target / "program" / PENDING, record)
    return record


def _native_complete(policy, target, *, maintenance_check=False):
    from qtifw_trial_helper import verify_program
    verify_program(policy, target)
    wanted = policy["targetVersion"]["version"]
    versions = _versions(target)
    if any(versions.get(name) != wanted for name in (PACKAGE, DATA_PACKAGE)):
        raise ValueError("qtifw-update-component-version-incomplete")
    for name in ("trial-maintenance.exe", "trial-maintenance.dat"):
        if not _safe(target / name).is_file():
            raise ValueError("qtifw-update-maintenance-incomplete")
    if maintenance_check:
        env = dict(os.environ, XINGTU_QTIFW_VERIFY_MAINTENANCE="1")
        env.pop("XINGTU_QTIFW_UPGRADE_SUPPORT", None)
        env.pop("XINGTU_QTIFW_UPGRADE_BASE", None)
        env.pop("XINGTU_QTIFW_UPGRADE_RCC", None)
        record = pending(policy, target)
        if record is None:
            raise ValueError("upgrade-verification-pending-required")
        cache = _safe(target / record["backup"] / ("verify-cache-" + uuid.uuid4().hex))
        with _native_start([str(target / "trial-maintenance.exe"), "--cache-path", str(cache), "--reject-messages", "list"],
                env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, encoding="utf-8", errors="replace",
                creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0) as process:
            try:
                output, errors = process.communicate(timeout=60)
            except subprocess.TimeoutExpired:
                process.kill()  # Only this owned verification child, never the installed application.
                process.communicate()
                raise ValueError("qtifw-persisted-controller-verification-timeout")
        expected = "XINGTU_MAINTENANCE_SOURCE_VERIFIED: " + policy["instance"]
        if process.returncode != 0 or expected not in output + errors:
            raise ValueError("qtifw-persisted-controller-verification-failed")


def _finish(policy, target, record):
    from qtifw_trial_helper import write_owner
    _native_complete(policy, target, maintenance_check=True)
    record["stage"] = "native-complete"
    _write(target / "program" / PENDING, record)
    write_owner(target, dict(instance=policy["instance"], payloadSource=policy["payloadSource"],
        phase="complete", target=str(target)))
    if record["watcherWasEnabled"]:
        from game_startup_watcher import control
        from game_startup_win32 import Windows
        if control("--restore-after-upgrade", target / "program", Windows()) != 0:
            raise ValueError("upgrade-watcher-restoration-incomplete")
    _native_complete(policy, target)
    _write(target / record["backup"] / "upgrade-result.json", dict(success=True,
        sourceCommit=policy["payloadSource"], previousSource=record["previousPolicy"]["payloadSource"],
        target=str(target), userData="retained-in-place", backupRetained=True))
    # This is the adapter's own record under its held resource write lease.
    if pending(policy, target) != record:
        raise ValueError("upgrade-pending-changed-before-completion")
    _safe(target / "program" / PENDING).unlink()
    return dict(success=True, mode="update", target=str(target), backup=record["backup"],
                programFiles=len(policy["files"]), userData="retained-in-place")


def materialize_repository(support, setup):
    """Read native component archives from this Setup using QtIFW's devtool.

    The local repository keeps QtIFW's generated archive checksums; it validates
    the copied archive itself. No old installation is passed to devtool.
    """
    support, setup = _safe(Path(support).resolve()), _safe(Path(setup).resolve())
    if not setup.is_file() or setup.stat().st_nlink != 1:
        raise ValueError("native-setup-source-unavailable")
    boot = _safe(support / "boot")
    layout = _read(boot / "archive-layout.json")
    if not isinstance(layout, list) or not layout:
        raise ValueError("native-repository-layout-unavailable")
    dump = boot / ("archive-dump-" + uuid.uuid4().hex)
    flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
    with (boot / (dump.name + ".log")).open("xb") as log:
        process = _native_start([str(boot / "devtool.exe"), "dump", str(setup), str(dump)],
                               stdout=log, stderr=subprocess.STDOUT, creationflags=flags)
        with process:
            if process.wait() != 0:
                raise ValueError("native-setup-archive-read-failed")
    for row in layout:
        package, archive, relative = row.get("package"), row.get("archive"), row.get("repository")
        if (package not in {PACKAGE, DATA_PACKAGE} or not isinstance(archive, str)
                or "/" in archive or "\\" in archive or not archive.endswith("program.7z")
                or relative != package + "/" + archive):
            raise ValueError("native-repository-layout-invalid")
        source = _safe(dump / "metadata" / package / archive)
        destination = _safe(boot / "repository" / relative)
        if not source.is_file():
            raise ValueError("native-setup-program-archive-unavailable")
        _copy_checked(source, destination)
    return dict(materialized=True, archives=len(layout))


def run(policy, target, support=None, *, setup_source=None, interactive=False):
    from qtifw_trial_helper import scoped
    target = scoped(policy, target)
    # Read-only discovery preserves fresh Setup's original admission path.
    # Update admission is repeated under both leases in _run below.
    if check(policy, target)["mode"] == "fresh":
        raise ValueError("native-fresh-setup-required")
    # Discover the optional resource root only after the program lease. This
    # prevents an absent -> created root from being skipped between leases,
    # and shares the program -> resources order with runtime and uninstall.
    with resource_write_lock(target / "program"), _installed_resource_lease(target):
        if setup_source is not None:
            if check(policy, target)["mode"] != "update":
                raise ValueError("native-fresh-setup-required")
            materialize_repository(support or Path(sys.executable).parent, setup_source)
        return _run(policy, target, support, interactive=interactive)


def _run(policy, target, support=None, *, interactive=False):
    """Run native work under the caller's program and installed-resource leases."""
    from qtifw_trial_helper import receipt, scoped
    plan = check(policy, target)
    target = scoped(policy, target)
    support = Path(support or Path(sys.executable).parent).resolve()
    boot = _safe(support / "boot")
    env = dict(os.environ)
    env.pop("XINGTU_QTIFW_UPGRADE_SUPPORT", None)
    env.pop("XINGTU_QTIFW_UPGRADE_BASE", None)
    env.pop("XINGTU_QTIFW_UPGRADE_RCC", None)
    env.pop("XINGTU_QTIFW_VERIFY_MAINTENANCE", None)
    env.setdefault("QT_QPA_PLATFORM", "windows")
    flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
    automatic = ["--reject-messages", "--auto-answer",
        "installationErrorWithRetry=Cancel,installationError=Ok", "--confirm-command"]
    if plan["mode"] == "fresh":
        # Fresh installation belongs to the running QtIFW Setup. This adapter
        # must never recurse into a second installer or replace its database.
        raise ValueError("native-fresh-setup-required")
    program = target / "program"
    # The adapter has already validated its own physical-root/source-bound
    # native marker. It never bypasses another resource transaction.
    _assert_resource_transactions_ready(program)
    record = _prepare(policy, target)
    if record["stage"] in {"native-in-progress", "native-complete"}:
        try:
            _native_complete(policy, target)
        except (ValueError, OSError):
            pass
        else:
            return _finish(policy, target, record)
    session = target / record["backup"] / ("native-" + uuid.uuid4().hex)
    session.mkdir()
    base = session / "installerbase.exe"
    replacement = session / "update.rcc"
    shutil.copy2(boot / "installerbase.exe", base)
    shutil.copy2(boot / "update.rcc", replacement)
    env["XINGTU_QTIFW_UPGRADE_SUPPORT"] = str(support)
    env["XINGTU_QTIFW_UPGRADE_BASE"] = str(base)
    env["XINGTU_QTIFW_UPGRADE_RCC"] = str(replacement)
    receipt(policy, target, "select-data", dict(success=True, choice="keep", operation="none"))
    versions = _versions(target)
    command = ["update", PACKAGE, DATA_PACKAGE] if {PACKAGE, DATA_PACKAGE} <= versions.keys() else ["install", PACKAGE]
    previous_stage = record["stage"]
    record["stage"] = "native-in-progress"
    _write(program / PENDING, record)
    with (session / "native-update.log").open("xb") as log:
        try:
            mode = ["--start-updater"] if interactive else [*automatic, *command]
            process = _native_start([str(target / "trial-maintenance.exe"), "--script",
                str(boot / "upgrade-controller.js"), "--set-temp-repository", (boot / "repository").as_uri(),
                "--cache-path", str(session / "cache"),
                *mode], env=env, stdout=log, stderr=subprocess.STDOUT, creationflags=flags)
        except OSError as error:
            # CreateProcess did not start: retain pre-native collision
            # checks on the next attempt rather than claiming native work.
            record["stage"] = previous_stage
            _write(program / PENDING, record)
            raise ValueError("native-update-failed:launch:" + str(error)) from error
        with process:
            code = process.wait()
    if code != 0:
        raise ValueError("native-update-failed:" + str(code))
    return _finish(policy, target, record)
