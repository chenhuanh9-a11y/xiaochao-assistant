"""Bounded export of existing failure evidence; never captures or runs recognition."""
from datetime import datetime
import hashlib
import json
from pathlib import Path, PurePosixPath
import re


def log_window(payload):
    times = []
    for line in payload.splitlines():
        try:
            row = json.loads(line)
            # Qt uses timestampUTC; legacy Tk events use time. A malformed
            # preferred field must not hide an otherwise valid timestamp.
            for key in ('timestampUTC', 'time'):
                try:
                    value = row.get(key)
                    if value:
                        times.append(datetime.fromisoformat(value).timestamp())
                        break
                except (ValueError, TypeError, OverflowError, OSError):
                    continue
        except (ValueError, TypeError, AttributeError):
            continue
    return (min(times), max(times)) if times else None


def _tail(path, limit):
    with path.open('rb') as stream:
        size = stream.seek(0, 2)
        stream.seek(max(0, size - limit))
        data = stream.read(limit)
    if size > limit:
        data = data.partition(b'\n')[2]
    return data, size > limit


def _image(root, relative, limit, expected=None):
    name = PurePosixPath(str(relative).replace('\\', '/'))
    if (name.is_absolute() or '..' in name.parts or not name.parts
            or name.parts[0] != 'failures' or name.suffix.lower() not in {'.png', '.jpg'}):
        return None, 'invalid-sample-path'
    try:
        base = root.resolve()
        path = (base / name).resolve()
        if not path.is_relative_to(base) or ':' in str(name):
            return None, 'sample-outside-memory-root'
        if path.stat().st_size > limit:
            return None, 'image-byte-limit'
        with path.open('rb') as stream:
            data = stream.read(limit + 1)
        if len(data) > limit:
            return None, 'image-byte-limit'
    except FileNotFoundError:
        return None, 'sample-file-missing'
    except (OSError, RuntimeError) as error:
        return None, type(error).__name__
    if not (data.startswith(b'\x89PNG\r\n\x1a\n') or data.startswith(b'\xff\xd8\xff')):
        return None, 'invalid-image-header'
    if expected and hashlib.sha256(data).hexdigest().lower() != str(expected).lower():
        return None, 'sample-sha256-mismatch'
    return data, 'included'


def export_failure_evidence(archive, roots, log_windows, redact, *, build_fingerprint,
                            max_samples=12, max_bytes=24 * 1024 * 1024,
                            journal_bytes=8 * 1024 * 1024):
    """Copy recent journal records and their exact saved frames, with omissions explicit."""
    report = dict(requested=bool(roots), cases=[], journals=[], imageCount=0,
                  imageBytes=0, matchingCases=0, omittedBySampleLimit=0,
                  limits=dict(samples=max_samples, imageBytes=max_bytes,
                              journalTailBytes=journal_bytes))
    candidates = []
    seen_roots = set()
    for root in roots:
        root = Path(root).resolve()
        if root in seen_roots:
            continue
        seen_roots.add(root)
        journal = dict(rootIndex=len(seen_roots), invalidLines=0)
        report['journals'].append(journal)
        try:
            data, truncated = _tail(root / 'alignment-failures.jsonl', journal_bytes)
            journal.update(status='read', truncatedToRecentLines=truncated)
        except OSError as error:
            journal['status'] = 'journal-missing' if isinstance(error, FileNotFoundError) else type(error).__name__
            continue
        for line in data.splitlines():
            try:
                row = json.loads(line)
                if not isinstance(row, dict):
                    raise ValueError('not-object')
                diagnostic = row.get('diagnostic') or {}
                if not isinstance(diagnostic, dict):
                    raise ValueError('invalid-diagnostic')
                if row.get('event') != 'alignment-failure-sample-written':
                    continue
                stamp = datetime.fromisoformat(row['recordedAt']).timestamp()
                captured = datetime.fromisoformat(row.get('capturedAt') or row['recordedAt']).timestamp()
                event_path = str(diagnostic.get('eventPath') or '')
                source_log = event_path.replace('\\', '/').rsplit('/', 1)[-1]
                window = log_windows.get(source_log)
                # ProblemsService rotates this exact Qt session to one named
                # backup. Only included logs from that same session qualify.
                if (event_path and (not window or not window[0] <= captured <= window[1])
                        and re.fullmatch(r'events-qt-\d{8}T\d{12}Z-\d+\.jsonl', source_log)):
                    previous = source_log.removesuffix('.jsonl') + '-previous.jsonl'
                    previous_window = log_windows.get(previous)
                    if previous_window and previous_window[0] <= captured <= previous_window[1]:
                        source_log, window = previous, previous_window
                if not window:
                    # An explicit session must not be reassigned to an overlapping log.
                    if event_path:
                        continue
                    if diagnostic.get('buildFingerprint') != build_fingerprint:
                        continue
                    source_log = next((name for name, span in log_windows.items()
                                       if span and span[0] <= captured <= span[1]), '')
                    window = log_windows.get(source_log)
                if not window or not window[0] <= captured <= window[1]:
                    continue
                candidates.append((stamp, root, row, source_log))
            except (ValueError, TypeError, KeyError, OverflowError):
                journal['invalidLines'] += 1
    candidates.sort(key=lambda item: item[0], reverse=True)
    report['matchingCases'] = len(candidates)
    report['omittedBySampleLimit'] = max(0, len(candidates) - max_samples)
    for index, (_, root, row, source_log) in enumerate(candidates[:max_samples], 1):
        prefix = f'evidence/{index:03d}'
        case = dict(logFile=source_log, cycleId=row.get('cycleId'), floor=row.get('floor'),
                    mapId=row.get('mapId'), recordedAt=row.get('recordedAt'), files=[])
        images = [('frame', row.get('samplePath'), row.get('sampleSha256'))]
        observations = row.get('observationPaths') or {}
        if isinstance(observations, dict):
            images.extend((str(name), path, None) for name, path in list(observations.items())[:8])
        for number, (role, relative, expected) in enumerate(images):
            remaining = max_bytes - report['imageBytes']
            if remaining <= 0:
                pixels, status = None, 'export-image-budget-exhausted'
            else:
                pixels, status = _image(root, relative, min(12 * 1024 * 1024, remaining), expected)
            item = dict(role=role, status=status)
            if pixels is not None:
                extension = '.png' if pixels.startswith(b'\x89PNG') else '.jpg'
                target = f'{prefix}/image-{number}{extension}'
                archive.writestr(target, pixels)
                item.update(path=target, bytes=len(pixels))
                report['imageCount'] += 1
                report['imageBytes'] += len(pixels)
            case['files'].append(item)
        metadata_path = f'{prefix}/failure.json'
        # Keep the writer's full measured evidence; redact local paths, never reinterpret it.
        metadata = dict(row, exportedLogFile=source_log, exportedImages=case['files'])
        archive.writestr(metadata_path, redact(json.dumps(metadata, ensure_ascii=False).encode('utf-8')))
        case['metadataPath'] = metadata_path
        report['cases'].append(case)
    report['status'] = ('not-requested' if not roots else 'included' if report['imageCount']
                        else 'no-matching-saved-frame')
    return report
