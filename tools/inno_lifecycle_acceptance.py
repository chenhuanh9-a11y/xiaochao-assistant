"""Preflight-only lifecycle acceptance planner.

Specifications are untrusted data. Native lifecycle execution stays disabled
until a separately authenticated disposable Windows runner is implemented.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path, PureWindowsPath
import platform

SCENARIOS = {"fresh-install", "qtifw-to-inno", "inno-upgrade", "reinstall", "full-clear",
             "cancel", "interrupted-retry", "occupied-file", "no-admin", "incomplete-previous",
             "helper-hang", "helper-crash"}
REQUIRED_ASSERTIONS = {"installed", "settings", "custom-content", "resource-evidence",
                       "shortcut", "startup", "user-registration", "machine-registration",
                       "old-maintenance-entry", "no-prepared-only"}
PACKAGES = ("setup", "historicalQtIfw", "historicalInno")
OBSERVATIONS = {"path", "content-sha256", "json-value"}


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _path_errors(value: object, label: str, roots: list[Path], *, allow_root: bool = True) -> tuple[Path | None, list[str]]:
    errors: list[str] = []
    if not isinstance(value, str) or not value:
        return None, [f"{label} must be a non-empty absolute path"]
    # Reject Windows traversal and network/device paths even when planning on Linux.
    win = PureWindowsPath(value)
    if ".." in Path(value).parts or ".." in win.parts or value.startswith(("\\\\", "//")) or win.drive.startswith("\\"):
        return None, [f"{label} contains traversal or a network/device path"]
    path = Path(value)
    if not path.is_absolute():
        return None, [f"{label} must be absolute"]
    try:
        absolute = path.absolute()
        # Reject any symlink/reparse-like component before resolving it.
        cursor = Path(absolute.anchor)
        for part in absolute.parts[1:]:
            cursor = cursor / part
            if cursor.is_symlink():
                return None, [f"{label} traverses a symbolic link"]
        resolved = absolute.resolve(strict=False)
    except (OSError, RuntimeError):
        return None, [f"{label} cannot be safely resolved"]
    if not allow_root and any(resolved == root for root in roots):
        errors.append(f"{label} must be below an allowed root")
    if roots and not any(resolved == root or root in resolved.parents for root in roots):
        errors.append(f"{label} is outside allowedRoots")
    return resolved, errors


def validate(spec: dict, execute: bool = False) -> list[str]:
    errors: list[str] = []
    if not isinstance(spec, dict):
        return ["spec must be an object"]
    if execute:
        errors.append("native execution is disabled: no authenticated disposable Windows runner is configured")
    raw_roots = spec.get("allowedRoots")
    if not isinstance(raw_roots, list) or not raw_roots:
        errors.append("allowedRoots must explicitly name disposable test roots")
        raw_roots = []
    roots: list[Path] = []
    for value in raw_roots:
        root, found = _path_errors(value, "allowedRoots entry", [])
        errors.extend(found)
        if root is not None:
            roots.append(root)
    packages = spec.get("packages")
    if not isinstance(packages, dict):
        errors.append("packages must be an object")
        packages = {}
    for label in PACKAGES:
        item = packages.get(label)
        if not isinstance(item, dict):
            errors.append(f"missing package declaration: {label}")
            continue
        path, found = _path_errors(item.get("path"), f"package {label} path", roots)
        errors.extend(found)
        if path is not None:
            try:
                if not path.is_file():
                    errors.append(f"package unavailable: {label}")
                elif not isinstance(item.get("sha256"), str) or len(item["sha256"]) != 64 or item["sha256"].lower() != sha256(path):
                    errors.append(f"package hash mismatch: {label}")
            except OSError:
                errors.append(f"package unreadable: {label}")
        if not isinstance(item.get("version"), str) or not item["version"] or not isinstance(item.get("sourceCommit"), str) or not item["sourceCommit"]:
            errors.append(f"package provenance incomplete: {label}")
    cases = spec.get("scenarios")
    if not isinstance(cases, list) or not cases:
        errors.append("no lifecycle scenarios configured")
        cases = []
    for index, case in enumerate(cases):
        where = f"scenario {index}"
        if not isinstance(case, dict):
            errors.append(f"{where} must be an object")
            continue
        name = case.get("name")
        if name not in SCENARIOS:
            errors.append(f"unknown scenario: {name}")
        root, found = _path_errors(case.get("root"), f"{where} root", roots, allow_root=False)
        errors.extend(found)
        if name == "qtifw-to-inno" and not packages.get("historicalQtIfw"):
            errors.append("real historical QtIFW package required")
        if name == "inno-upgrade" and not packages.get("historicalInno"):
            errors.append("historical Inno package required")
        steps = case.get("steps", [])
        if not isinstance(steps, list):
            errors.append(f"{where} steps must be a list")
            continue
        for step_index, step in enumerate(steps):
            step_label = f"{where} step {step_index}"
            if not isinstance(step, dict):
                errors.append(f"{step_label} must be an object")
                continue
            # There are deliberately no executable command fields in the accepted schema.
            if any(key in step for key in ("command", "executable", "script", "interpreter", "args", "arguments")):
                errors.append(f"{step_label} requests a command; only named trusted adapters may execute")
            adapter = step.get("action")
            if adapter is not None:
                errors.append(f"{step_label} uses unsupported action adapter: {adapter}")
            for check in step.get("observe", []):
                errors.extend(_validate_observation(check, roots, root, step_label))
        assertions = case.get("assertions", [])
        if not isinstance(assertions, list):
            errors.append(f"{where} assertions must be a list")
        else:
            for check in assertions:
                errors.extend(_validate_observation(check, roots, root, where))
        for evidence in case.get("resultEvidence", []):
            if not isinstance(evidence, dict):
                errors.append(f"{where} resultEvidence entries must be objects")
    return errors


def _validate_observation(check: object, roots: list[Path], scenario_root: Path | None, label: str) -> list[str]:
    errors: list[str] = []
    if not isinstance(check, dict):
        return [f"{label} observation must be an object"]
    kind = check.get("kind")
    if kind == "registry":
        return [f"{label} registry observations are disabled until product identity is independently verified"]
    if kind not in OBSERVATIONS:
        return [f"{label} has unsupported observation kind: {kind}"]
    path, found = _path_errors(check.get("path"), f"{label} observation path", roots, allow_root=False)
    errors.extend(found)
    if scenario_root is not None and path is not None and path != scenario_root and scenario_root not in path.parents:
        errors.append(f"{label} observation path is outside its scenario root")
    if kind == "json-value" and (not isinstance(check.get("field"), str) or not check["field"]):
        errors.append(f"{label} JSON observation needs a field selector")
    return errors


def run(spec: dict, execute: bool, adapter: str | None = None) -> dict:
    errors = validate(spec, execute)
    report = {"schema": "xingtu.inno-lifecycle-report.v2", "executionRequested": execute,
              "executionEnabled": False, "status": "blocked", "platform": platform.platform(),
              "inputErrors": errors, "scenarios": []}
    if not errors and not execute:
        for case in spec["scenarios"]:
            report["scenarios"].append({"name": case["name"], "status": "preflight-only",
                                        "execution": "disabled; no trusted isolated runner configured"})
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("spec", type=Path)
    parser.add_argument("--execute", action="store_true", help="refused until an authenticated disposable Windows runner exists")
    parser.add_argument("--report", type=Path, required=True)
    args = parser.parse_args()
    spec = json.loads(args.spec.read_text(encoding="utf-8"))
    # Validate report destination before creating anything. A spec cannot authorize an
    # arbitrary report location: it must be inside one of its declared roots.
    errors = validate(spec, args.execute)
    raw_roots = spec.get("allowedRoots", []) if isinstance(spec, dict) else []
    roots = []
    for value in raw_roots:
        root, _ = _path_errors(value, "allowedRoots entry", [])
        if root is not None:
            roots.append(root)
    report_path, report_errors = _path_errors(str(args.report.absolute()), "report path", roots, allow_root=False)
    errors.extend(report_errors)
    if errors:
        report = {"schema": "xingtu.inno-lifecycle-report.v2", "executionRequested": args.execute,
                  "executionEnabled": False, "status": "blocked", "platform": platform.platform(),
                  "inputErrors": errors, "scenarios": []}
    else:
        report = run(spec, args.execute)
    if report_path is None or report_errors:
        print("blocked: report destination failed boundary validation")
        return 2
    try:
        report_path.parent.mkdir(parents=True, exist_ok=True)
        # Re-check after directory creation to catch a newly introduced symlink.
        checked, found = _path_errors(str(report_path), "report path", roots, allow_root=False)
        if found or checked != report_path:
            print("blocked: report destination changed during validation")
            return 2
        report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    except OSError:
        print("blocked: report could not be written safely")
        return 2
    print(f"{report['status']}: {report_path}")
    return 0 if report["status"] == "passed" else 2


if __name__ == "__main__":
    raise SystemExit(main())
