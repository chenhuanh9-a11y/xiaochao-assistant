"""QtIFW support with explicit fresh-target ownership and frozen inventories.

The original program inventory supplies file ownership. Interrupted first-install
files are quarantined, never deleted or treated as a general rollback engine.
"""
from __future__ import annotations

import argparse
from contextlib import ExitStack, closing
import hashlib
import json
import os
from pathlib import Path
import stat
import sys
import time
import uuid

if not getattr(sys, "frozen", False):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "app"))
from resource_transaction import _safe, resource_write_lock, assert_resources_ready, rename_no_replace

OWNER = ".qtifw-trial-owner.json"


def write_owner(target, owner, *, new=False):
    marker = _safe(target / OWNER)
    temporary = _safe(target / (OWNER + "." + uuid.uuid4().hex + ".new"))
    with temporary.open("x", encoding="utf-8") as stream:
        json.dump(owner, stream)
        stream.flush()
        os.fsync(stream.fileno())
    if new:
        rename_no_replace(temporary, marker)
    else:
        temporary.replace(marker)


def scoped(policy, target):
    scope = None
    if not policy.get("portable"):
        scope = _safe(Path(policy["scope"]).absolute())
        token = _safe(scope / ".qtifw-trial-scope").read_text(encoding="ascii").strip()
        if token != policy["instance"]:
            raise ValueError("trial-scope-mismatch")
    supplied = Path(target)
    if not supplied.is_absolute():
        raise ValueError("absolute-install-directory-required")
    target = supplied.absolute()
    _safe(target)
    if ".." in target.parts or target == Path(target.anchor):
        raise ValueError("invalid-install-directory")
    if scope is not None:
        internal = target.is_relative_to(scope / "targets") and target != scope / "targets"
        if not internal and (not policy.get("allowNewTargets") or target.is_relative_to(scope)
                             or scope.is_relative_to(target)):
            raise ValueError("outside-trial-targets")
    if os.name == "nt" and (target.drive.startswith("\\\\") or
            os.path.isreserved(str(target)) or
            target.is_relative_to(Path(os.environ.get("SystemRoot", "C:/Windows")))):
        raise ValueError("system-or-network-directory-not-supported")
    return target


def bound_owner(policy, target):
    target = scoped(policy, target)
    owner = json.loads(_safe(target / OWNER).read_text(encoding="utf-8"))
    if owner.get("instance") != policy["instance"] or owner.get("payloadSource") != policy["payloadSource"]:
        raise ValueError("trial-owner-mismatch")
    if policy.get("allowNewTargets") and os.path.normcase(owner.get("target", "")) != os.path.normcase(str(target)):
        raise ValueError("trial-owner-target-mismatch")
    return target, owner


def owned_program(policy, target):
    """Validate all paths before any quarantine or native extraction starts."""
    result = []
    for name, row in policy["files"].items():
        relative = Path(name)
        if (not name or "\\" in name or ":" in name or relative.is_absolute()
                or any(p in {"..", "."} for p in relative.parts)
                or name.casefold().startswith("work/")):
            raise ValueError("invalid-program-inventory")
        path = _safe(target / "program" / relative)
        if path.exists():
            info = path.stat()
            if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
                raise ValueError("program-object-not-exclusive-file")
            result.append((name, path, row))
    return result


def check_target(policy, target):
    """Read-only admission shared by the directory page and pre-extract path."""
    target = scoped(policy, target)
    for parent in target.parents:
        if any(_safe(parent / name).exists() for name in (OWNER, "unins000.exe", "星图.exe")):
            raise ValueError("inside-existing-installation")
    for prefix in (target, target / "program"):
        if any(_safe(prefix / name).exists() for name in ("unins000.exe", "unins000.dat")):
            raise ValueError("legacy-inno-migration-required")
    marker = _safe(target / OWNER)
    entries = owned_program(policy, target) if marker.exists() or not policy.get("allowNewTargets") else []
    if marker.exists():
        _, owner = bound_owner(policy, target)
        if (target / "trial-maintenance.exe").exists():
            raise ValueError("use-existing-maintenance-tool")
        if owner["phase"] != "installing" and entries:
            raise ValueError("completed-trial-without-maintenance-requires-inspection")
    else:
        if entries:
            raise ValueError("unowned-program-collision")
        if policy.get("allowNewTargets") and target.exists() and (
                not target.is_dir() or any(target.iterdir())):
            raise ValueError("install-directory-not-empty")
    return dict(checked=True, target=str(target))


def prepare(policy, target):
    check_target(policy, target)
    target = scoped(policy, target)
    entries = owned_program(policy, target)
    marker = _safe(target / OWNER)
    if marker.exists():
        _, owner = bound_owner(policy, target)
    else:
        target.mkdir(parents=True, exist_ok=True)
        owner = dict(instance=policy["instance"], payloadSource=policy["payloadSource"],
                     phase="installing", target=str(target))
        write_owner(target, owner, new=True)
    if owner["phase"] != "installing":
        owner["phase"] = "installing"
        write_owner(target, owner)
    # All present program files were absent before this marked first attempt.
    # Preserve them (including torn/changed files) outside the new component.
    moved = []
    if entries:
        destination = _safe(target / "recovery-files" / uuid.uuid4().hex)
        for name, path, _ in entries:
            output = _safe(destination / name)
            output.parent.mkdir(parents=True, exist_ok=True)
            if output.exists():
                raise ValueError("recovery-destination-exists")
            path.rename(output)
            moved.append(name)
    return dict(prepared=True, quarantined=moved, target=str(target))


def verify_program(policy, target):
    target, _ = bound_owner(policy, target)
    entries = owned_program(policy, target)
    if len(entries) != len(policy["files"]):
        raise ValueError("program-incomplete")
    for name, path, row in entries:
        if path.stat().st_size != row["bytes"] or hashlib.sha256(path.read_bytes()).hexdigest() != row["sha256"]:
            raise ValueError("program-byte-mismatch:" + name)
    return dict(verified=True, files=len(entries))


def verify(policy, target):
    result = verify_program(policy, target)
    target, owner = bound_owner(policy, target)
    owner["phase"] = "complete"
    write_owner(target, owner)
    return result


# Exact application-written settings paths; never claim a whole work subtree.
# Map payloads are selected separately through the existing receipt planner.
SETTINGS_FILES = tuple("nightmare-memory/" + name for name in (
    "ui-settings.json", "custom-routes.json", "custom-galleries.json", "update-state.json",
    "tutorial-state.json", "chest-auto-tap.json"))


def data_files(policy, target):
    target, _ = bound_owner(policy, target)
    work = _safe(target / "program/work")
    result = []
    for name in SETTINGS_FILES:
        path = _safe(work / name)
        if not path.exists():
            continue
        info = path.stat()
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
            raise ValueError("data-object-not-exclusive-file")
        # Malformed/foreign data is retained for manual review.
        try:
            raw = path.read_bytes()
            if not isinstance(json.loads(raw), dict):
                continue
        except (ValueError, UnicodeError):
            continue
        result.append((path, dict(bytes=info.st_size, sha256=hashlib.sha256(raw).hexdigest(),
                                  identity=(info.st_dev, info.st_ino))))
    # Reuse authoritative package ownership, including changed/unknown retention.
    # Keep metadata as retry evidence; never remove receipts ahead of payloads.
    if (work / "map-packages").exists():
        from map_package_removal import _plan_locked
        plan = _plan_locked(target / "program")
        for row in plan["entries"]:
            if row["path"] not in plan["witnesses"] and row["path"] != "work/map-packages/" + row["packageId"] + ".json":
                path = _safe(target / "program" / row["path"])
                result.append((path, dict(bytes=row["bytes"], sha256=row["sha256"],
                                          identity=row["identity"])))
    return target, result


def clear_data(policy, target, choice, *, native_lease=False):
    if choice not in {"keep", "clear"}:
        raise ValueError("explicit-data-choice-required")
    target, _ = bound_owner(policy, target)
    if choice == "keep":
        return dict(dataChoice="keep", cleared=0)
    program = _safe(target / "program")
    program.mkdir(exist_ok=True)
    # Same cooperative lock as startup/import. Never clear a running app's work.
    if native_lease:
        from qtifw_trial_lease import verified_uninstaller_lease
        lease = closing(verified_uninstaller_lease(target))
    else:
        lease = resource_write_lock(program)
    with lease:
        assert_resources_ready(program)
        _, paths = data_files(policy, target)
        if len(paths) > 50000:
            raise ValueError("trial-data-limit")
        # Reuse existing same-handle Win32 deletion primitives. Open every file
        # before marking any; sharing conflicts/links reject the whole preflight.
        from failure_sampling import _open_delete_image, _mark_delete_image
        with ExitStack() as stack:
            handles = []
            for path, expected in paths:
                fd = _open_delete_image(path)
                stack.callback(os.close, fd)
                current = os.fstat(fd)
                previous = path.stat()
                digest = hashlib.sha256()
                while block := os.read(fd, 1024 * 1024):
                    digest.update(block)
                if (current.st_nlink != 1 or not stat.S_ISREG(current.st_mode)
                        or (current.st_dev, current.st_ino) != (previous.st_dev, previous.st_ino)
                        or (current.st_dev, current.st_ino) != tuple(expected["identity"])
                        or current.st_size != expected["bytes"] or digest.hexdigest() != expected["sha256"]):
                    raise ValueError("trial-data-changed")
                handles.append(fd)
            for fd in handles:
                _mark_delete_image(fd)
    return dict(dataChoice="clear", cleared=len(paths), lockFilePreserved=True)


def receipt(policy, target, name, value):
    target = scoped(policy, target)
    destination = _safe(target / ("trial-" + name + ".json"))
    destination.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
    if name == "data" and not policy.get("portable"):
        # Trial evidence also lives outside the target. Formal installs retain
        # the target and read the local receipt on the finish page.
        external = _safe(Path(policy["scope"]) / "last-uninstall.json")
        external.write_text(json.dumps(dict(value, target=str(target)), ensure_ascii=False, indent=2), encoding="utf-8")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("check-target", "prepare", "verify-program", "verify", "install-maps", "disable-watcher", "inspect-data", "hold-uninstall", "select-data", "undo-data", "noop", "data", "pause", "fail"))
    parser.add_argument("target", type=Path)
    parser.add_argument("choice", nargs="?", default="keep")
    parser.add_argument("operation", nargs="?", default="none", choices=("none", "purge"))
    args = parser.parse_args()
    policy = json.loads(Path(__file__).with_name("qtifw-trial-policy.json").read_text(encoding="utf-8"))
    try:
        if args.action == "check-target":
            # Browsing/validation must not create the target, marker or receipt.
            print(json.dumps(check_target(policy, args.target), ensure_ascii=True))
            return 0
        elif args.action == "prepare":
            result = prepare(policy, args.target)
        elif args.action == "verify-program":
            result = verify_program(policy, args.target)
        elif args.action == "verify":
            target, _ = bound_owner(policy, args.target)
            if not _safe(target / "trial-maintenance.exe").is_file():
                raise ValueError("maintenance-not-created")
            result = verify(policy, args.target)
        elif args.action == "install-maps":
            target, owner = bound_owner(policy, args.target)
            if owner["phase"] != "installing":
                raise ValueError("maps-require-new-installation")
            verify_program(policy, target)
            result = dict(installed=0, packages=[])
            if policy.get("mapPackages"):
                from qtifw_map_seed import install_bundled_maps
                result = install_bundled_maps(target, policy["mapPackages"])
        elif args.action == "select-data":
            bound_owner(policy, args.target)
            if args.choice not in {"keep", "clear"}:
                raise ValueError("explicit-data-choice-required")
            result = dict(choice=args.choice, operation=args.operation)
            if args.operation == "none":
                receipt(policy, args.target, "data", dict(success=False, status="not-requested"))
        elif args.action == "noop":
            bound_owner(policy, args.target)
            result = dict(armed=True)
        elif args.action == "disable-watcher":
            target, _ = bound_owner(policy, args.target)
            program = target / "program"
            from game_startup_watcher import control
            from game_startup_win32 import Windows
            code = control("--disable", program, Windows())
            if code != 0:
                raise ValueError("game-startup-disable-failed:" + str(code))
            result = dict(watcherDisabled=True)
        elif args.action in {"data", "undo-data"}:
            target, owner = bound_owner(policy, args.target)
            choice = args.choice
            if args.action == "undo-data":
                selected = json.loads(_safe(target / "trial-select-data.json").read_bytes())
                if selected.get("operation") != "purge":
                    print('{"skipped":"update-or-install-rollback"}')
                    return 0
                choice = selected["choice"]
            if owned_program(policy, target):
                raise ValueError("native-uninstall-not-complete")
            result = clear_data(policy, args.target, choice, native_lease=args.action == "undo-data")
            receipt(policy, args.target, "data", dict(success=True, **result))
        elif args.action == "hold-uninstall":
            target, _ = bound_owner(policy, args.target)
            if not policy.get("nativeUninstallLease"):
                raise ValueError("native-uninstall-lease-policy-required")
            from qtifw_trial_lease import hold_uninstaller_lease
            result = hold_uninstaller_lease(target)
        elif args.action == "inspect-data":
            target, paths = data_files(policy, args.target)
            if policy.get("allowNewTargets"):
                for path in target.rglob("*"):
                    _safe(path)
                    info = path.stat()
                    if not stat.S_ISDIR(info.st_mode) and (not stat.S_ISREG(info.st_mode) or info.st_nlink != 1):
                        raise ValueError("installation-object-not-exclusive-file")
            with resource_write_lock(target / "program"):
                assert_resources_ready(target / "program")
            result = dict(dataFiles=len(paths), scope="program/work only")
        else:
            target, _ = bound_owner(policy, args.target)
            if not policy.get("faultsAllowed"):
                raise ValueError("fault-injection-disabled")
            receipt(policy, target, "checkpoint", {"action": args.action})
            if args.action == "fail":
                return 47
            time.sleep(20)
            result = dict(pauseComplete=True)
        receipt(policy, args.target, args.action, dict(success=True, **result))
        print(json.dumps(result, ensure_ascii=True))
        return 0
    except (ValueError, OSError, KeyError) as error:
        if args.action in {"data", "undo-data", "verify", "verify-program"}:
            try:
                name = "data" if args.action in {"data", "undo-data"} else args.action
                receipt(policy, args.target, name, dict(success=False, status="incomplete", reason=str(error)))
            except (ValueError, OSError):
                pass  # Native log still records the original failure.
        print(json.dumps(dict(success=False, reason=str(error)), ensure_ascii=True))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
