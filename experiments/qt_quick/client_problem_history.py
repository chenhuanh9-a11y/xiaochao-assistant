"""Bounded diagnostic history reader; call only from an export/background worker.

Counts represent observed retained records, not a lifetime total. Legacy logs did
not persist repeats, so absent counts cannot be reconstructed. No Qt dependency.
"""
from __future__ import annotations
from copy import deepcopy
import json
from itertools import islice
import os
from pathlib import Path

_FIELDS = ('title', 'summary', 'raw', 'action', 'phase', 'kind', 'mode', 'mapId', 'floor',
           'errorCode', 'errorDetails', 'exceptionType', 'exceptionOrigin', 'exceptionAction')
_MAX_COUNT = 2**53 - 1
SNAPSHOT_LIMIT = 96


def summarize_recent_problems(log_dir, current_recent=(), *, limit=32, max_files=8,
                              max_bytes=2*1024*1024, max_entries=256, max_records=4096, _coverage=None):
    """Merge retained originals/checkpoints/current snapshot without double count.

    Directory entries, files, bytes, parsed records and returned rows are capped.
    Oversize lines and partial tail lines are skipped. Symlinks are never read.
    Snapshot rows must be copied on their owner thread before calling this API.
    """
    coverage = _coverage if _coverage is not None else {}
    coverage.update(scope="bounded-retained-history", counts="observed-only",
                    completeHistory=False, filesRead=0, bytesRead=0, recordsExamined=0,
                    directoryLimitReached=False, fileLimitReached=False, byteLimitReached=False,
                    recordLimitReached=False, readErrors=0)
    snapshot = list(islice(current_recent, SNAPSHOT_LIMIT + 1))
    coverage['snapshotRecords'] = min(len(snapshot), SNAPSHOT_LIMIT)
    coverage['snapshotLimitReached'] = len(snapshot) > SNAPSHOT_LIMIT
    current_recent = snapshot[:SNAPSHOT_LIMIT]
    limit = min(32, max(0, int(limit)))
    if not limit:
        return []
    max_files = min(16, max(0, int(max_files)))
    remaining = min(4*1024*1024, max(0, int(max_bytes)))
    max_records = min(8192, max(0, int(max_records)))
    candidates = []
    root = Path(log_dir)
    try:
        if root.is_symlink() or root.resolve() != root.absolute():
            coverage['readErrors'] += 1
            return _merge((), current_recent, limit)
        with os.scandir(root) as entries:
            for index, entry in enumerate(entries):
                if index >= min(1024, max(0, int(max_entries))):
                    coverage["directoryLimitReached"] = True
                    break
                if (entry.name.startswith('events-') and entry.name.endswith('.jsonl')
                        and entry.is_file(follow_symlinks=False)):
                    candidates.append((entry.stat(follow_symlinks=False).st_mtime_ns, entry.name))
    except OSError:
        coverage["readErrors"] += 1
    coverage["fileLimitReached"] = len(candidates) > max_files
    rows = []
    for _, name in sorted(candidates, reverse=True)[:max_files]:
        if remaining <= 0 or coverage["recordsExamined"] >= max_records:
            break
        try:
            path = root / name
            if path.is_symlink() or path.resolve() != path.absolute() or path.stat().st_nlink > 1:
                coverage['readErrors'] += 1
                continue
            with path.open('rb') as stream:
                size = os.fstat(stream.fileno()).st_size
                start = max(0, size - remaining)
                stream.seek(start)
                data = stream.read(remaining)
                remaining -= len(data)
                coverage["filesRead"] += 1
                coverage["bytesRead"] += len(data)
                coverage["byteLimitReached"] |= bool(start)
            if start:
                data = data.partition(b'\n')[2]
            for line in reversed(data.splitlines()):
                if coverage["recordsExamined"] >= max_records:
                    coverage["recordLimitReached"] = True
                    break
                coverage["recordsExamined"] += 1
                # Keep total decoded data bounded even for malformed/huge lines.
                if len(line) > 24576:
                    continue
                try:
                    row = json.loads(line)
                except (ValueError, UnicodeError):
                    continue
                if isinstance(row, dict) and row.get('event') in (
                        'qt-runtime-problem', 'qt-runtime-problem-summary'):
                    rows.append(row)
        except OSError:
            coverage["readErrors"] += 1
            continue
    coverage["byteLimitReached"] |= remaining <= 0
    return _merge(rows, current_recent, limit)


def _merge(rows, current, limit):
    occurrences = {}
    # Current snapshot takes precedence for presentation when counts are equal.
    for row in list(rows) + list(islice(current, SNAPSHOT_LIMIT)):
        if not isinstance(row, dict) or row.get('kind') not in ('error', 'warning'):
            continue
        if not isinstance(row.get('timestampUTC'), str):
            continue
        group = row.get('problemGroupId')
        signature = ('run-group:' + group if isinstance(group, str) and len(group) == 32
                     else json.dumps([row.get(key, '') for key in _FIELDS], sort_keys=True, ensure_ascii=False))
        identity = row.get('occurrenceId') or json.dumps(
            [row.get('id'), row['timestampUTC'], signature], sort_keys=True)
        if not isinstance(identity, str):
            continue
        if isinstance(row.get('sessionId'),str) and len(row['sessionId'])==32 and type(row.get('pid')) is int:
            identity=(row['sessionId'],row['pid'],identity)
        count = row.get('count', 1)
        count = min(_MAX_COUNT, count) if type(count) is int and count > 0 else 1
        previous = occurrences.get(identity)
        if previous is None or count >= previous['count']:
            copied = {key: deepcopy(value) for key, value in row.items() if not key.startswith('_')}
            copied['count'] = count
            copied['_group'] = signature
            occurrences[identity] = copied
    groups = {}
    for row in occurrences.values():
        signature = row.pop('_group')
        previous = groups.get(signature)
        if previous is None:
            groups[signature] = row
            continue
        total = min(_MAX_COUNT, previous['count'] + row['count'])
        first = min(previous['timestampUTC'], row['timestampUTC'])
        if str(row.get('lastTimestampUTC', row['timestampUTC'])) > str(previous.get('lastTimestampUTC', previous['timestampUTC'])):
            groups[signature] = row
        groups[signature].update(count=total, timestampUTC=first)
    return sorted(groups.values(), key=lambda row: str(row.get('lastTimestampUTC', row['timestampUTC'])), reverse=True)[:limit]


def summarize_problem_history(log_dir, current_recent=(), **limits):
    """Export schema: recentProblems plus honest bounded-scan coverage metadata."""
    coverage = {}
    rows = summarize_recent_problems(log_dir, current_recent, _coverage=coverage, **limits)
    coverage['limits'] = dict(maxFiles=min(16, max(0, int(limits.get('max_files', 8)))),
        maxBytes=min(4*1024*1024, max(0, int(limits.get('max_bytes', 2*1024*1024)))),
        maxDirectoryEntries=min(1024, max(0, int(limits.get('max_entries', 256)))),
        maxRecords=min(8192, max(0, int(limits.get('max_records', 4096)))),
        maxProblems=min(32, max(0, int(limits.get('limit', 32)))),
        maxSnapshotRecords=SNAPSHOT_LIMIT)
    return dict(recentProblems=rows, coverage=coverage)
