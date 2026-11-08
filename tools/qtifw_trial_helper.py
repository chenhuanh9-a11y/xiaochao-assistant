"""Local QtIFW trial support. A baked policy confines every write to its test run.

The original program inventory supplies file ownership. Interrupted first-install
files are quarantined, never deleted or treated as a general rollback engine.
"""
from __future__ import annotations

import argparse
from contextlib import ExitStack
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
    scope = _safe(Path(policy["scope"]).absolute())
    token = _safe(scope / ".qtifw-trial-scope").read_text(encoding="ascii").strip()
    if token != policy["instance"]:
        raise ValueError("trial-scope-mismatch")
    target = Path(target).absolute()
    _safe(target)
    if ".." in target.parts or not target.is_relative_to(scope / "targets") or target == scope / "targets":
        raise ValueError("outside-trial-targets")
    return target


def bound_owner(policy, target):
    target = scoped(policy, target)
    owner = json.loads(_safe(target / OWNER).read_text(encoding="utf-8"))
    if owner.get("instance") != policy["instance"] or owner.get("payloadSource") != policy["payloadSource"]:
        raise ValueError("trial-owner-mismatch")
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


def prepare(policy, target):
    target = scoped(policy, target)
    for prefix in (target, target / "program"):
        if any(_safe(prefix / name).exists() for name in ("unins000.exe", "unins000.dat")):
            raise ValueError("legacy-inno-migration-required")
    entries = owned_program(policy, target)
    marker = _safe(target / OWNER)
    if marker.exists():
        _, owner = bound_owner(policy, target)
        if (target / "trial-maintenance.exe").exists():
            raise ValueError("use-existing-maintenance-tool")
        if owner["phase"] != "installing" and entries:
            raise ValueError("completed-trial-without-maintenance-requires-inspection")
    else:
        if entries:
            raise ValueError("unowned-program-collision")
        target.mkdir(parents=True, exist_ok=True)
        owner = dict(instance=policy["instance"], payloadSource=policy["payloadSource"], phase="installing")
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


def verify(policy, target):
    target, owner = bound_owner(policy, target)
    entries = owned_program(policy, target)
    if len(entries) != len(policy["files"]):
        raise ValueError("program-incomplete")
    for name, path, row in entries:
        if path.stat().st_size != row["bytes"] or hashlib.sha256(path.read_bytes()).hexdigest() != row["sha256"]:
            raise ValueError("program-byte-mismatch:" + name)
    owner["phase"] = "complete"
    write_owner(target, owner)
    return dict(verified=True, files=len(entries))


def data_files(policy, target):
    target, _ = bound_owner(policy, target)
    work = _safe(target / "program/work")
    result = []
    if work.exists():
        if not work.is_dir():
            raise ValueError("work-is-not-a-directory")
        for path in work.rglob("*"):
            _safe(path)
            info = path.stat()
            if stat.S_ISDIR(info.st_mode):
                continue
            if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
                raise ValueError("data-object-not-exclusive-file")
            if path.relative_to(work).as_posix() != "map-packages/import.lock":
                result.append(path)
    return target, result


def clear_data(policy, target, choice):
    if choice not in {"keep", "clear"}:
        raise ValueError("explicit-data-choice-required")
    target, _ = bound_owner(policy, target)
    if choice == "keep":
        return dict(dataChoice="keep", cleared=0)
    program = _safe(target / "program")
    program.mkdir(exist_ok=True)
    # Same cooperative lock as startup/import. Never clear a running app's work.
    with resource_write_lock(program):
        assert_resources_ready(program)
        _, paths = data_files(policy, target)
        if len(paths) > 50000:
            raise ValueError("trial-data-limit")
        # Reuse existing same-handle Win32 deletion primitives. Open every file
        # before marking any; sharing conflicts/links reject the whole preflight.
        from failure_sampling import _open_delete_image, _mark_delete_image
        with ExitStack() as stack:
            handles = []
            for path in paths:
                fd = _open_delete_image(path)
                stack.callback(os.close, fd)
                current = os.fstat(fd)
                previous = path.stat()
                if (current.st_nlink != 1 or not stat.S_ISREG(current.st_mode)
                        or (current.st_dev, current.st_ino) != (previous.st_dev, previous.st_ino)):
                    raise ValueError("trial-data-changed")
                handles.append(fd)
            for fd in handles:
                _mark_delete_image(fd)
    return dict(dataChoice="clear", cleared=len(paths), lockFilePreserved=True)


def receipt(policy, target, name, value):
    target = scoped(policy, target)
    destination = _safe(target / ("trial-" + name + ".json"))
    destination.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("prepare", "verify", "inspect-data", "select-data", "undo-data", "noop", "data", "pause", "fail"))
    parser.add_argument("target", type=Path)
    parser.add_argument("choice", nargs="?", default="keep")
    parser.add_argument("operation", nargs="?", default="none", choices=("none", "purge"))
    args = parser.parse_args()
    policy = json.loads(Path(__file__).with_name("qtifw-trial-policy.json").read_text(encoding="utf-8"))
    try:
        if args.action == "prepare":
            result = prepare(policy, args.target)
        elif args.action == "verify":
            target, _ = bound_owner(policy, args.target)
            if not _safe(target / "trial-maintenance.exe").is_file():
                raise ValueError("maintenance-not-created")
            result = verify(policy, args.target)
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
            result = clear_data(policy, args.target, choice)
            receipt(policy, args.target, "data", dict(success=True, **result))
        elif args.action == "inspect-data":
            target, paths = data_files(policy, args.target)
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
        if args.action in {"data", "undo-data"}:
            try:
                receipt(policy, args.target, "data", dict(success=False, status="incomplete", reason=str(error)))
            except (ValueError, OSError):
                pass  # Native log still records the original failure.
        print(json.dumps(dict(success=False, reason=str(error)), ensure_ascii=True))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
