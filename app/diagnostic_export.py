"""Bounded existing diagnostic ZIP export shared by Tk and Qt; no capture/UI imports."""
from __future__ import annotations
from datetime import datetime
import hashlib
import json
import os
from pathlib import Path
import re
import sys
import tempfile
import zipfile

DIAGNOSTIC_LOG_LIMIT = 8
DIAGNOSTIC_FILE_BYTE_LIMIT = 8 * 1024 * 1024
DIAGNOSTIC_TOTAL_BYTE_LIMIT = 24 * 1024 * 1024

def safe_diagnostic_log_files(live_root: Path, limit: int = DIAGNOSTIC_LOG_LIMIT) -> list[Path]:
    """Return only recent text event/error logs from the dedicated live folder."""
    if limit <= 0 or not live_root.is_dir():
        return []
    candidates: dict[str, Path] = {}
    for pattern in ("events-*.jsonl", "startup-error-*.log"):
        for path in live_root.glob(pattern):
            if path.is_file():
                candidates[path.name.casefold()] = path
    return sorted(
        candidates.values(),
        key=lambda path: (path.stat().st_mtime, path.name),
        reverse=True,
    )[:limit]


def _bounded_log_tail(path: Path, byte_limit: int) -> tuple[bytes, bool]:
    size = path.stat().st_size
    if size <= byte_limit:
        return path.read_bytes(), False
    with path.open("rb") as handle:
        handle.seek(max(0, size - byte_limit))
        handle.readline()  # Drop a partial JSON/text line at the truncation edge.
        payload = handle.read(byte_limit)
        if not payload:
            # One abnormally long line is still more useful than an empty log.
            handle.seek(max(0, size - byte_limit))
            payload = handle.read(byte_limit)
    return payload, True


def _redact_local_paths(payload: bytes) -> bytes:
    """Remove local Windows paths while preserving surrounding diagnostic text."""
    text = payload.decode("utf-8", errors="replace")
    redacted = re.sub(
        r'(?i)(?:[A-Z]:[\\/]|\\\\)[^"\r\n]*',
        "<local-path>",
        text,
    )
    return redacted.encode("utf-8")


def _event_log_session_id(path: Path) -> str | None:
    """Derive a stable, non-identifying session id from the startup event."""
    if not path.name.startswith("events-"):
        return None
    try:
        with path.open("r", encoding="utf-8", errors="replace") as handle:
            for _index, line in zip(range(32), handle):
                try:
                    row = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if row.get("event") != "assistant-started":
                    continue
                seed = "|".join(str(row.get(key, "")) for key in (
                    "time", "processId", "buildFingerprint",
                ))
                return hashlib.sha256(seed.encode("utf-8")).hexdigest()[:16]
    except OSError:
        return None
    return None


def diagnostic_package_metadata(app_root: Path, *, app_version: str, build_fingerprint: str) -> dict:
    """Compare runtime identity with the package report copied beside the EXE."""
    common = dict(runtimeVersion=app_version, runtimeBuildFingerprint=build_fingerprint,
        executionMode='packaged' if getattr(sys, 'frozen', False) else 'source',
        executableVerification='not-performed', consistencyScope='version-and-short-fingerprint-only',
        releaseReportSourceCommit=None, releaseReportBuiltAt=None,
        releaseReportExecutableSha256=None, releaseReportSourceDirty=None)
    report_path = app_root / "release-report.json"
    if not report_path.is_file():
        return {
            **common,
            "status": "release-report-missing",
            "releaseReportPresent": False,
            "runtimeVersion": app_version,
            "runtimeBuildFingerprint": build_fingerprint,
            "versionConsistent": None,
            "buildFingerprintConsistent": None,
        }
    try:
        if report_path.stat().st_size > 1024 * 1024:
            raise ValueError('release-report-too-large')
        report = json.loads(report_path.read_text(encoding="utf-8-sig"))
        if not isinstance(report, dict):
            raise ValueError('release-report-not-object')
    except (OSError, ValueError):
        return {
            **common,
            "status": "release-report-invalid",
            "releaseReportPresent": True,
            "runtimeVersion": app_version,
            "runtimeBuildFingerprint": build_fingerprint,
            "versionConsistent": None,
            "buildFingerprintConsistent": None,
        }
    report_version = str(report.get("version") or "")
    report_fingerprint = str(
        report.get("buildFingerprint") or ""
    ).upper()
    version_consistent = report_version == app_version
    fingerprint_consistent = report_fingerprint == build_fingerprint
    for source, target, length in (('sourceCommit', 'releaseReportSourceCommit', 40),
                                  ('executableSha256', 'releaseReportExecutableSha256', 64),
                                  ('sourcePatchSha256', 'releaseReportSourcePatchSha256', 64)):
        value = report.get(source)
        common[target] = value if isinstance(value, str) and re.fullmatch(r'[0-9a-fA-F]{'+str(length)+'}', value) else None
    built_at = report.get('builtAt')
    if isinstance(built_at, str):
        try:
            datetime.fromisoformat(built_at)
            common['releaseReportBuiltAt'] = built_at
        except ValueError:
            pass
    dirty = report.get('sourceDirty')
    common['releaseReportSourceDirty'] = dirty if isinstance(dirty, bool) else None
    dirty_files = report.get('sourceDirtyFiles')
    common['releaseReportSourceDirtyFiles'] = (list(dirty_files) if isinstance(dirty_files, list)
        and all(isinstance(path, str) for path in dirty_files) else None)
    complete = all(common[key] is not None for key in ('releaseReportSourceCommit',
        'releaseReportBuiltAt', 'releaseReportExecutableSha256', 'releaseReportSourceDirty'))
    return {
        **common,
        "status": (
            ("consistent" if complete else "release-report-incomplete")
            if version_consistent and fingerprint_consistent
            else "package-runtime-mismatch"
        ),
        "releaseReportPresent": True,
        "runtimeVersion": app_version,
        "runtimeBuildFingerprint": build_fingerprint,
        "releaseReportVersion": report_version or None,
        "releaseReportBuildFingerprint": report_fingerprint or None,
        "versionConsistent": version_consistent,
        "buildFingerprintConsistent": fingerprint_consistent,
    }


def export_safe_diagnostic_bundle(
    app_root: Path,
    live_root: Path,
    *,
    exported_at: datetime | None = None,
    log_limit: int = DIAGNOSTIC_LOG_LIMIT,
    file_byte_limit: int = DIAGNOSTIC_FILE_BYTE_LIMIT,
    total_byte_limit: int = DIAGNOSTIC_TOTAL_BYTE_LIMIT,
    window_environment: dict | None = None,
    runtime_context: dict | None = None,
    package_metadata: dict | None = None,
    failure_roots: tuple[Path, ...] = (),
    app_version: str,
    build_fingerprint: str,
    process_integrity: dict | None = None,
    destination: Path | None = None,
    summary: dict | None = None,
    resource_root: Path | None = None,
) -> dict:
    """Export bounded logs and explicitly requested, already saved failure evidence."""
    from diagnostic_evidence import export_failure_evidence, log_window
    from package_identity import collect_package_identity
    moment = exported_at or datetime.now().astimezone()
    selected_destination = destination is not None
    destination_root = Path(destination).parent if selected_destination else app_root / "outputs" / "诊断日志"
    destination_root.mkdir(parents=True, exist_ok=True)
    base_name = f"第五人格小抄助手-诊断日志-{moment:%Y%m%d-%H%M%S}"
    destination = Path(destination) if selected_destination else destination_root / f"{base_name}.zip"
    if selected_destination:
        if destination.suffix.lower() != ".zip":
            raise ValueError("Diagnostic destination must be a ZIP file")
        if destination.exists():
            raise FileExistsError(destination)
    else:
        suffix = 2
        while destination.exists():
            destination = destination_root / f"{base_name}-{suffix}.zip"
            suffix += 1

    logs = safe_diagnostic_log_files(live_root, limit=log_limit)
    included_logs: list[dict] = []
    log_windows = {}
    included_bytes = 0
    fd, temporary_name = tempfile.mkstemp(
        prefix=".diagnostic-export-", suffix=".tmp", dir=destination_root,
    )
    os.close(fd)
    temporary = Path(temporary_name)
    try:
        with zipfile.ZipFile(temporary, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            for path in logs:
                remaining = total_byte_limit - included_bytes
                if remaining <= 0:
                    break
                payload, truncated = _bounded_log_tail(
                    path, max(1, min(file_byte_limit, remaining)),
                )
                payload = _redact_local_paths(payload)
                log_windows[path.name] = log_window(payload)
                archive.writestr(f"logs/{path.name}", payload)
                included_bytes += len(payload)
                included_sha256 = hashlib.sha256(payload).hexdigest().upper()
                included_logs.append({
                    "name": path.name,
                    "sourceBytes": path.stat().st_size,
                    "includedBytes": len(payload),
                    "truncatedToRecentLines": truncated,
                    "includedSha256": included_sha256,
                    "sessionId": (
                        _event_log_session_id(path)
                        or (
                            included_sha256[:16].lower()
                            if path.name.startswith("events-") else None
                        )
                    ),
                })

            version_files: list[str] = []
            version_names = ('version.json', 'installation-profile.json', 'release-report.json') if (
                package_metadata or {}).get('qtSourceCommit') else ('VERSION.txt', 'release-report.json', 'SHA256SUMS.txt')
            for name in version_names:
                path = app_root / name
                if path.is_file() and path.stat().st_size <= 1024 * 1024:
                    archive.write(path, f"version/{name}")
                    version_files.append(name)

            package_metadata_source = 'startup-snapshot' if package_metadata is not None else 'export-time-report'
            package_metadata = package_metadata if package_metadata is not None else diagnostic_package_metadata(app_root, app_version=app_version, build_fingerprint=build_fingerprint)
            failure_evidence = export_failure_evidence(
                archive, failure_roots, log_windows, _redact_local_paths,
                build_fingerprint=build_fingerprint,
            )
            manifest = {
                "schema": "identityv-safe-diagnostic-export-v3" if failure_roots else "identityv-safe-diagnostic-export-v2",
                "exportedAt": moment.isoformat(timespec="seconds"),
                "appVersion": app_version,
                "buildFingerprint": build_fingerprint,
                "runtimeProcessIntegrity": process_integrity or {"level": "Unknown", "rank": -1},
                "requestedExecutionLevel": "requireAdministrator",
                "textOnly": failure_evidence['imageCount'] == 0,
                "privacy": (
                    "Recent text logs, package version files and Identity V window metadata; "
                    "when requested, bounded saved failure game frames and measured evidence "
                    "from the included log sessions. No new screenshots are captured. "
                    "Full local paths, confirmations, settings and unrelated memory are excluded."
                ),
                "failureEvidence": failure_evidence,
                "windowEnvironment": window_environment or {
                    "schema": "identityv-window-environment-v1",
                    "candidateCount": 0,
                    "candidates": [],
                    "collectionStatus": "not-requested",
                },
                "packageMetadata": package_metadata,
                "packageMetadataSource": package_metadata_source,
                "packageIdentity": collect_package_identity(app_root, resource_root=resource_root),
                "runtimeContext": runtime_context or {
                    "collectionStatus": "not-provided",
                },
                "logs": included_logs,
                "versionFiles": version_files,
            }
            if summary is not None:
                safe_summary = _redact_local_paths(json.dumps(summary, ensure_ascii=False, indent=2).encode("utf-8"))
                if len(safe_summary) > 1024 * 1024:
                    raise ValueError("Diagnostic summary exceeds bounded metadata limit")
                archive.writestr("qt-client-summary.json", safe_summary)
            archive.writestr(
                "diagnostic-manifest.json",
                json.dumps(manifest, ensure_ascii=False, indent=2).encode("utf-8"),
            )
        if selected_destination:
            # Publish without replacing a file created since the save dialog.
            os.link(temporary, destination)
        else:
            os.replace(temporary, destination)
    finally:
        if temporary.exists():
            temporary.unlink()
    return {
        "path": destination,
        "includedLogCount": len(included_logs),
        "windowCandidateCount": int(
            (window_environment or {}).get("candidateCount", 0),
        ),
        "truncatedLogCount": sum(
            1 for row in included_logs if row["truncatedToRecentLines"]
        ),
        "packageMetadataStatus": package_metadata["status"],
        "includedFailureImageCount": failure_evidence['imageCount'],
        "failureEvidenceStatus": failure_evidence['status'],
        "omittedFailureCaseCount": failure_evidence['omittedBySampleLimit'],
    }
