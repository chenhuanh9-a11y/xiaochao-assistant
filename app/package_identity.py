"""Bounded, read-only package identity. Call hashing only on an export worker."""
import hashlib
import json
from pathlib import Path
import re
import sys

# Fixed witnesses, never paths supplied by a report or a recursive user-data scan.
# The EXE digest covers its frozen PYZ; these Python files are source copies.
MODULE_FILES = (
    'client_info.py', 'client_input.py', 'client_native_keys.py', 'client_map_input.py',
    'client_coordinator.py', 'client_live_runtime.py', 'client_capture.py', 'client_space_geometry.py',
    'client_chest_auto_tap.py', 'client_window_binding.py', 'client_failure_samples.py',
    'app/map_close_control.py', 'app/diagnostic_export.py', 'app/diagnostic_evidence.py',
    'app/package_identity.py', 'app/map_identity_engine.py', 'app/fast_structure_alignment.py',
)
RESOURCE_DECLARATIONS = (
    'difficult-native-manifest.json', 'references/nightmare-map-pack.json',
    'references/nightmare-author-route-catalog.json', 'references/nightmare-canonical-coordinate-pack.json',
    'references/nightmare-shared-structure-index.json', 'references/nightmare-structure-version-bindings.json',
    'references/difficult-shared-structure-index.json',
)
INTERNAL_FILES = ('version.json', 'installation-profile.json', 'references/build-fingerprint.txt',
                  'Main.qml', 'ClientHelp.qml', *MODULE_FILES, *RESOURCE_DECLARATIONS)
FILE_LIMIT = 8 * 1024 * 1024
TOTAL_LIMIT = 32 * 1024 * 1024
EXE_LIMIT = 128 * 1024 * 1024


def file_identity(path, *, limit, root=None):
    """Hash bounded bytes and report absence, changes and limits explicitly."""
    path = Path(path)
    try:
        if root is not None:
            root = Path(root).resolve()
            if not path.resolve().is_relative_to(root):
                return dict(status='outside-package')
            for part in (path, *path.parents):
                if part == root:
                    break
                if part.is_symlink() or (hasattr(part, 'is_junction') and part.is_junction()):
                    return dict(status='linked-file')
        before = path.stat()
        if not path.is_file():
            return dict(status='not-file')
        if before.st_size > limit:
            return dict(status='byte-limit', bytes=before.st_size)
        digest, size = hashlib.sha256(), 0
        with path.open('rb') as stream:
            for chunk in iter(lambda: stream.read(min(1024 * 1024, limit - size + 1)), b''):
                size += len(chunk)
                if size > limit:
                    return dict(status='byte-limit', bytes=size)
                digest.update(chunk)
        after = path.stat()
        if (before.st_size, before.st_mtime_ns, before.st_ino) != (after.st_size, after.st_mtime_ns, after.st_ino):
            return dict(status='changed-during-read', bytes=size)
        return dict(status='hashed', bytes=size, sha256=digest.hexdigest())
    except FileNotFoundError:
        return dict(status='missing')
    except OSError as error:
        return dict(status=type(error).__name__)


def selected_file_identities(installation_root, resource_root=None):
    installation_root = Path(installation_root)
    resource_root = Path(resource_root) if resource_root is not None else installation_root / '_internal'
    rows, remaining = {}, TOTAL_LIMIT
    paths = [(name, installation_root / name, installation_root)
             for name in ('version.json', 'installation-profile.json', *RESOURCE_DECLARATIONS)]
    paths.extend(('_internal/' + name, resource_root / name, resource_root) for name in INTERNAL_FILES)
    for name, path, root in paths:
        row = file_identity(path, limit=min(FILE_LIMIT, remaining), root=root)
        if row['status'] in ('hashed', 'changed-during-read'):
            remaining -= row['bytes']
        rows[name] = row
    return rows


def collect_package_identity(installation_root, *, resource_root=None):
    """Export actual bytes independently from startup labels and stale reports."""
    root = Path(installation_root)
    report_path = root / 'release-report.json'
    report_identity = file_identity(report_path, limit=1024 * 1024, root=root)
    report = {}
    if report_identity['status'] == 'hashed':
        try:
            with report_path.open('rb') as stream:
                data = stream.read(1024 * 1024 + 1)
            if hashlib.sha256(data).hexdigest() != report_identity['sha256']:
                report_identity = dict(status='changed-during-read')
            else:
                value = json.loads(data)
                if isinstance(value, dict):
                    report = value
                else:
                    report_identity['status'] = 'invalid-json-object'
        except (OSError, ValueError):
            report_identity['status'] = 'invalid-or-unreadable'
    packaged = bool(getattr(sys, 'frozen', False))
    executable = (file_identity(Path(sys.executable), limit=EXE_LIMIT)
                  if packaged else dict(status='source-interpreter-not-package'))
    expected = report.get('executableSha256')
    valid_hash = lambda value: isinstance(value, str) and re.fullmatch(r'[0-9a-fA-F]{64}', value)
    verification = 'expected-hash-unavailable'
    if executable['status'] != 'hashed':
        verification = executable['status']
    elif valid_hash(expected):
        verification = 'match' if executable['sha256'] == expected.lower() else 'mismatch'
    files = selected_file_identities(root, resource_root)
    expected_files = report.get('identityFiles')
    expected_files = expected_files if isinstance(expected_files, dict) else {}
    for name, row in files.items():
        expected_row = expected_files.get(name)
        expected_sha = expected_row.get('sha256') if isinstance(expected_row, dict) else None
        row['expectedSha256'] = expected_sha.lower() if valid_hash(expected_sha) else None
        row['matchesReport'] = (row.get('sha256') == expected_sha.lower()
                                if row['status'] == 'hashed' and valid_hash(expected_sha) else None)
    return dict(schema='xingtu-package-identity-v1', collectionTime='export-worker',
        executable=executable, executableVerification=verification,
        releaseReport=report_identity, files=files,
        scope='EXE bytes (including frozen modules), selected source copies and resource declarations; not a full installation audit',
        resourceScope='Declarations only; bundled baseline may differ from intentionally preserved installed resources',
        limits=dict(executableBytes=EXE_LIMIT, fileBytes=FILE_LIMIT, selectedFileBytes=TOTAL_LIMIT))
