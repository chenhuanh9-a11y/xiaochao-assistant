"""Guarded Windows lifecycle acceptance runner for disposable test machines.

Without --execute this only validates inputs and emits a plan. Each scenario
uses explicit command adapters; observations are performed independently after
each command. A prepared legacy copy can never satisfy an installed assertion.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import platform
import subprocess
import sys
import time

SCENARIOS = {"fresh-install", "qtifw-to-inno", "inno-upgrade", "reinstall", "full-clear",
             "cancel", "interrupted-retry", "occupied-file", "no-admin", "incomplete-previous",
             "helper-hang", "helper-crash"}
REQUIRED_ASSERTIONS = {"installed", "settings", "custom-content", "resource-evidence",
                       "shortcut", "startup", "user-registration", "machine-registration",
                       "old-maintenance-entry", "no-prepared-only"}


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def validate(spec: dict, execute: bool = False) -> list[str]:
    errors = []
    if not isinstance(spec.get("allowedRoots"), list) or not spec["allowedRoots"]:
        errors.append("allowedRoots must explicitly name disposable test roots")
    if execute and (platform.system() != "Windows" or spec.get("disposableWindows") is not True or
                    spec.get("productIdentityVerified") is not True):
        errors.append("execution requires disposable Windows and verified product identity")
    packages = spec.get("packages", {})
    for label in ("setup", "historicalQtIfw", "historicalInno"):
        item = packages.get(label)
        if label in {"setup", "historicalQtIfw", "historicalInno"} and not item:
            errors.append(f"missing package declaration: {label}")
            continue
        path = Path(item.get("path", ""))
        if not path.is_file():
            errors.append(f"package unavailable: {label}")
        elif item.get("sha256", "").lower() != sha256(path):
            errors.append(f"package hash mismatch: {label}")
        if not item.get("version") or not item.get("sourceCommit"):
            errors.append(f"package provenance incomplete: {label}")
    cases = spec.get("scenarios", [])
    if not cases:
        errors.append("no lifecycle scenarios configured")
    for case in cases:
        if case.get("name") not in SCENARIOS:
            errors.append(f"unknown scenario: {case.get('name')}")
        if not case.get("root"):
            errors.append(f"scenario has no isolated root: {case.get('name')}")
        if case.get("name") == "qtifw-to-inno" and not packages.get("historicalQtIfw"):
            errors.append("real historical QtIFW package required")
        if case.get("name") == "inno-upgrade" and not packages.get("historicalInno"):
            errors.append("historical Inno package required")
    return errors


def observe(checks: list[dict]) -> list[dict]:
    rows = []
    for check in checks:
        kind = check["kind"]
        if kind == "path":
            path = Path(check["path"])
            try:
                state = "present" if path.exists() else "absent"
                detail = {"path": str(path), "kind": "path"}
            except PermissionError:
                state, detail = "access-denied", {"path": str(path), "kind": "path"}
        elif kind == "content-sha256":
            path = Path(check["path"])
            try:
                state = sha256(path) if path.is_file() else "absent"
                detail = {"path": str(path), "kind": kind}
            except PermissionError:
                state, detail = "access-denied", {"path": str(path), "kind": kind}
        elif kind == "json-value":
            path = Path(check["path"])
            try:
                value = json.loads(path.read_text(encoding="utf-8"))
                for part in check["field"].split("."):
                    value = value[part]
                state, detail = value, {"path": str(path), "kind": kind, "field": check["field"]}
            except PermissionError:
                state, detail = "access-denied", {"path": str(path), "kind": kind}
            except (OSError, ValueError, KeyError, TypeError):
                state, detail = "not-checked", {"path": str(path), "kind": kind}
        elif kind == "registry":
            if os.name != "nt":
                state, detail = "not-checked", {"kind": "registry", "reason": "Windows only"}
            else:
                proc = subprocess.run(["reg.exe", "query", check["key"], "/v", check["value"]],
                                      capture_output=True, text=True, timeout=15)
                state = "present" if proc.returncode == 0 else "absent" if proc.returncode == 1 else "not-checked"
                detail = {"kind": "registry", "key": check["key"], "value": check["value"],
                          "exitCode": proc.returncode, "output": proc.stdout[-1000:]}
        else:
            state, detail = "not-checked", {"kind": kind}
        expected = check.get("expected")
        rows.append({"label": check["label"], "state": state, "expected": expected,
                     "passed": expected is not None and state == expected, **detail})
    return rows


def run(spec: dict, execute: bool, adapter: str | None = None) -> dict:
    errors = validate(spec, execute)
    report = {"schema": "xingtu.inno-lifecycle-report.v1", "evidenceIsSimulated": False,
              "executionRequested": execute, "platform": platform.platform(),
              "packages": spec.get("packages", {}), "scenarios": [], "inputErrors": errors}
    if errors:
        report["status"] = "blocked"
        return report
    for case in spec["scenarios"]:
        row = {"name": case["name"], "root": case["root"], "steps": [], "observations": [],
               "status": "skipped" if not execute else "failed"}
        if execute:
            root = Path(case["root"]).resolve()
            if not any(root.is_relative_to(Path(p).resolve()) for p in spec["allowedRoots"]):
                row.update(status="blocked", error="scenario root outside allowedRoots")
                report["scenarios"].append(row)
                continue
            for step in case.get("steps", []):
                command = [str(x).replace("{root}", str(root)) for x in step["command"]]
                started = time.monotonic()
                try:
                    proc = subprocess.run(command, cwd=str(root), capture_output=True, text=True,
                                          timeout=min(int(step.get("timeoutSeconds", 300)), 1800), shell=False)
                    result = {"label": step["label"], "command": command, "exitCode": proc.returncode,
                              "durationSeconds": round(time.monotonic() - started, 3),
                              "stdout": proc.stdout[-2000:], "stderr": proc.stderr[-2000:]}
                    row["steps"].append(result)
                    row["observations"].extend(observe(step.get("observe", [])))
                    if proc.returncode != step.get("expectedExitCode", 0):
                        row.update(status="failed", error="unexpected process exit")
                        break
                except subprocess.TimeoutExpired:
                    row["steps"].append({"label": step["label"], "timedOut": True,
                                         "durationSeconds": round(time.monotonic() - started, 3)})
                    row.update(status="failed", error="process timed out; no forced termination or cleanup claimed")
                    break
            else:
                checks = case.get("assertions", [])
                assertion_rows = observe(checks)
                row["assertions"] = assertion_rows
                names = {x.get("label") for x in checks}
                missing = REQUIRED_ASSERTIONS - names
                failing = [x["label"] for x in assertion_rows if not x["passed"]]
                observed_bad = [x["label"] for x in row["observations"] if x["state"] in {"access-denied", "not-checked"}]
                prepared_only = any(x.get("status") == "prepared-native-unverified" or
                                    x.get("nativeInstalled") is False for x in case.get("resultEvidence", []))
                row.update(status="passed" if not (missing or failing or observed_bad or prepared_only) else "failed",
                           missingAssertions=sorted(missing), failedAssertions=failing,
                           unverifiedObservations=observed_bad,
                           reason="prepared legacy copy is not native installation" if prepared_only else None)
        report["scenarios"].append(row)
    report["status"] = "passed" if execute and all(x["status"] == "passed" for x in report["scenarios"]) else "blocked" if not execute else "failed"
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("spec", type=Path)
    parser.add_argument("--execute", action="store_true", help="run only in a declared disposable Windows environment")
    parser.add_argument("--report", type=Path, required=True)
    args = parser.parse_args()
    spec = json.loads(args.spec.read_text(encoding="utf-8"))
    report = run(spec, args.execute)
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"{report['status']}: {args.report}")
    return 0 if report["status"] == "passed" else 2


if __name__ == "__main__":
    raise SystemExit(main())
