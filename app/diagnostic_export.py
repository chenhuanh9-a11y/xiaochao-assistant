"""Bounded existing diagnostic ZIP export shared by Tk and Qt; no capture/UI imports."""
from __future__ import annotations
from datetime import datetime, timezone
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
    # Explicit watchdog reserve only; no arbitrary recursive file collection.
    watchdog=live_root / 'watchdog'
    if watchdog.is_dir() and not watchdog.is_symlink():
        for path in watchdog.glob('thread-stacks-*.jsonl'):
            if path.is_file() and not path.is_symlink():
                candidates['watchdog/'+path.name.casefold()]=path
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
            # Never invent a malformed JSONL suffix when a single row exceeds
            # the export budget. The manifest records this truncated empty tail.
            payload = b""
    return payload, True


def _redact_path_text(text: str) -> str:
    # Work on decoded strings, never JSON escape syntax. Preserve quoted frame
    # boundaries (including single quotes) and avoid treating https:/ as a drive.
    text = re.sub(r'''(?im)(["'])(?:[A-Z]:[\\/]|\\\\)[^\r\n]*?\1(?=,[ \t]*line[ \t]+\d+(?:,[ \t]*in[ \t]+[^/\\\r\n]*)?\r?$)''',
                  lambda match: match[1] + "<local-path>" + match[1], text)
    text = re.sub(r'(?i)"(?:[A-Z]:[\\/]|\\\\)[^"\r\n]*"', '"<local-path>"', text)
    # Apostrophes are legal path characters. Outside a recognized frame line,
    # choose the last quote conservatively instead of leaking a path suffix.
    text = re.sub(r"(?i)'(?:[A-Z]:[\\/]|\\\\)[^\r\n]*'", "'<local-path>'", text)
    text = re.sub(r'(?<![:/\w])/(?:home|Users|workspace|tmp|var|opt)/[^"\r\n]*', "<local-path>", text)
    return re.sub(r'(?i)(?:\b[A-Z]:[\\/]|\\\\)[^"\r\n]*', "<local-path>", text)


def _redact_json_paths(value):
    # Iterative traversal avoids adding Python recursion for nested metadata.
    result = [None]
    pending = [(result, 0, value)]
    while pending:
        parent, slot, item = pending.pop()
        if isinstance(item, dict):
            output = {}
            parent[slot] = output
            reserved = {key for key in item if _redact_path_text(key) == key}
            for key, child in item.items():
                base = name = _redact_path_text(key)
                serial = 2
                while name in output or (name != key and name in reserved):
                    name = base + "#" + str(serial)
                    serial += 1
                output[name] = None
                pending.append((output, name, child))
        elif isinstance(item, list):
            output = [None] * len(item)
            parent[slot] = output
            pending.extend((output, index, child) for index, child in enumerate(item))
        else:
            parent[slot] = _redact_path_text(item) if isinstance(item, str) else item
    return result[0]


def _redact_local_paths(payload: bytes) -> bytes:
    """Redact JSON values before serialization; handle JSONL and plain text too."""
    text = payload.decode("utf-8", errors="replace")

    def structured(value):
        return json.dumps(_redact_json_paths(value), ensure_ascii=False, separators=(",", ":"))

    try:
        redacted = structured(json.loads(text))
    except json.JSONDecodeError:
        rows = []
        # JSONL records end at LF, not at Unicode separators legal in strings.
        for line in text.split("\n"):
            try:
                value = json.loads(line)
            except json.JSONDecodeError:
                rows.append(_redact_path_text(line))
            else:
                # Only normalize a valid record; malformed/truncated text is
                # still text and must not invalidate adjacent JSONL records.
                ending = "\r" if line.endswith("\r") else ""
                rows.append(structured(value) + ending)
        redacted = "\n".join(rows)
    return redacted.encode("utf-8", errors="backslashreplace")


def _log_sessions(payload: bytes, log_name: str) -> list[dict]:
    """Describe only identities actually present in included records, never hashes."""
    groups = {}
    for line in payload.split(b'\n'):
        try:
            row = json.loads(line)
            if not isinstance(row, dict):
                continue
            session, pid, stream = row.get('sessionId'), row.get('pid'), row.get('logStream')
            if not isinstance(session, str) or not re.fullmatch(r'[0-9a-f]{32}', session) or type(pid) is not int or not isinstance(stream, str):
                continue
            key = session, pid, stream
            group = groups.setdefault(key, dict(sessionId=session, pid=pid, logStream=stream,
                installIds=[], logs=[log_name], firstAt=None, lastAt=None, firstSeq=None, lastSeq=None,
                recordCount=0, diagnosticLoss={}))
            group['recordCount'] += 1
            install = row.get('installId')
            if isinstance(install, str) and re.fullmatch(r'[0-9a-f]{32}', install) and install not in group['installIds']:
                group['installIds'].append(install)
            stamp = row.get('timestampUTC', row.get('time'))
            try:
                parsed = datetime.fromisoformat(stamp)
                if parsed.tzinfo is not None:
                    stamp = parsed.astimezone(timezone.utc).isoformat(timespec='milliseconds')
                    group['firstAt'] = min(group['firstAt'] or stamp, stamp)
                    group['lastAt'] = max(group['lastAt'] or stamp, stamp)
            except (ValueError, TypeError):
                pass
            seq = row.get('seq')
            if type(seq) is int:
                group['firstSeq'] = min(group['firstSeq'] or seq, seq)
                group['lastSeq'] = max(group['lastSeq'] or seq, seq)
            loss = row.get('diagnosticLoss')
            if isinstance(loss, dict) and type(loss.get('droppedTotal')) is int and loss['droppedTotal'] >= group['diagnosticLoss'].get('droppedTotal', 0):
                group['diagnosticLoss'] = loss
        except (ValueError, TypeError):
            continue
    return list(groups.values())


def _merge_sessions(sessions):
    merged = {}
    for row in sessions:
        key = row['sessionId'], row['pid'], row['logStream']
        if key not in merged:
            merged[key] = row
            continue
        previous = merged[key]
        previous['logs'] += row['logs']
        previous['installIds'] = list(dict.fromkeys(previous['installIds'] + row['installIds']))
        previous['recordCount'] += row['recordCount']
        for field, operation in (('firstAt', min), ('lastAt', max), ('firstSeq', min), ('lastSeq', max)):
            values = [value for value in (previous[field], row[field]) if value is not None]
            previous[field] = operation(values) if values else None
        if row['diagnosticLoss'].get('droppedTotal', 0) >= previous['diagnosticLoss'].get('droppedTotal', 0):
            previous['diagnosticLoss'] = row['diagnosticLoss']
    return list(merged.values())


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
    export_id: str | None = None,
    summary: dict | None = None,
    automatic_failure_only: bool = False,
    include_automatic_failure_evidence: bool = False,
    resource_root: Path | None = None,
) -> dict:
    """Export bounded logs and explicitly requested, already saved failure evidence."""
    from diagnostic_evidence import export_failure_evidence, log_window
    from uuid import uuid4
    export_id = export_id or uuid4().hex
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
    session_windows = {}
    sessions = []
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
                from diagnostic_privacy import sanitize_log_payload
                payload = sanitize_log_payload(payload)
                # Sanitization can expand legacy text: reapply the byte budget.
                cap = max(1, min(file_byte_limit, remaining))
                if len(payload) > cap:
                    kept, used = [], 0
                    for line in reversed(payload.split(b'\n')):
                        if not line: continue
                        if used + len(line) + 1 > cap: break
                        kept.append(line); used += len(line) + 1
                    payload = b'\n'.join(reversed(kept)) + (b'\n' if kept else b'')
                    truncated = True
                log_windows[path.name] = log_window(payload)
                archive.writestr(f"logs/{path.name}", payload)
                included_bytes += len(payload)
                included_sha256 = hashlib.sha256(payload).hexdigest().upper()
                observed_sessions = _log_sessions(payload, path.name)
                sessions.extend(observed_sessions)
                for scope in observed_sessions:
                    if scope['firstAt'] and scope['lastAt']:
                        session_windows[(scope['sessionId'],scope['pid'],path.name)]=(
                            datetime.fromisoformat(scope['firstAt']).timestamp(),
                            datetime.fromisoformat(scope['lastAt']).timestamp())
                included_logs.append({
                    "name": path.name,
                    "sourceBytes": path.stat().st_size,
                    "includedBytes": len(payload),
                    "truncatedToRecentLines": truncated,
                    "includedSha256": included_sha256,
                    "sessionId": observed_sessions[0]['sessionId'] if len(observed_sessions) == 1 else None,
                    "sessionIdentityStatus": 'recorded' if observed_sessions else 'legacy-not-recorded',
                    "logId": included_sha256[:16].lower(),
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
                automatic_only=automatic_failure_only,
                include_automatic=include_automatic_failure_evidence,
                session_windows=session_windows,
                build_fingerprint=build_fingerprint,
            )
            manifest = {
                "schema": "identityv-safe-diagnostic-export-v3" if failure_roots else "identityv-safe-diagnostic-export-v2",
                "exportedAt": moment.isoformat(timespec="seconds"),
                "exportId": export_id,
                "sessions": _merge_sessions(sessions),
                "sessionRangeScope": "included-records-only; rotated and truncated logs may be incomplete",
                "appVersion": app_version,
                "buildFingerprint": build_fingerprint,
                "runtimeProcessIntegrity": process_integrity or {"level": "Unknown", "rank": -1},
                "requestedExecutionLevel": "unavailable",
                "requestedExecutionLevelReason": "Executable manifest not inspected; runtime token is reported separately.",
                "textOnly": failure_evidence['imageCount'] == 0,
                "privacy": (
                    "Recent text logs, package version files and Identity V window metadata; "
                    "when requested, bounded saved failure game frames and measured evidence "
                    "from the included log sessions. The export operation takes no new screenshots; "
                    "requested images were saved earlier by separately enabled sampling. "
                    "Full local paths, full settings files and unrelated memory are excluded; "
                    "relevant shortcut binding state may be included."
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
                "runtimeContext": runtime_context or {
                    "collectionStatus": "not-provided",
                },
                "logs": included_logs,
                "versionFiles": version_files,
            }
            if summary is not None:
                from diagnostic_privacy import safe_record
                safe_summary = _redact_local_paths(json.dumps(safe_record(summary), ensure_ascii=False, indent=2).encode("utf-8"))
                if len(safe_summary) > 1024 * 1024:
                    raise ValueError("Diagnostic summary exceeds bounded metadata limit")
                archive.writestr("qt-client-summary.json", safe_summary)
            archive.writestr(
                "diagnostic-manifest.json",
                _redact_local_paths(json.dumps(manifest, ensure_ascii=False, indent=2).encode("utf-8")),
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
        "exportId": export_id,
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
