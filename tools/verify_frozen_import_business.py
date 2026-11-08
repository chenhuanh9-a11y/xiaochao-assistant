"""Synthetic author ZIP through a REAL frozen helper and real parent/lease gates.

No Setup, actual client UI, game resources, registry writes, or user installation.
The original helper spec is never rewritten. Old-red mode records a real failed
valid import; its successful fault reproduction is NOT business validation.
"""
from __future__ import annotations
import argparse
import ctypes
from ctypes import wintypes
import hashlib
import importlib.metadata
import json
import marshal
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time
from types import SimpleNamespace
import uuid
import zipfile

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / 'tools'), str(ROOT / 'app'), str(ROOT / 'tests')]
import build_qt_client as build
import verify_installer_compile_only as compile_probe

PACKAGES = compile_probe.HELPER_PACKAGES
ACTIVE = None
# Observed original spec, not an estimate or Setup-size comparison.
OLD_IMPORT_PAYLOAD = dict(sourceCommit='34ea53ea002ffd07c4bbcbf514503448e078b828',
    artifactSha256='3597efb04b70f18c4d2c770508157ae06bcea0bc0e030be756ec86f4aa8bbee9',
    fileCount=66, rawBytes=21326255)


def snapshot(root):
    return {p.relative_to(root).as_posix(): dict(bytes=p.stat().st_size, sha256=build.sha256(p))
            for p in sorted(root.rglob('*')) if p.is_file()}


def invalid_image_archive(source, destination):
    """Corrupt only synthetic image bytes, updating all hashes to reach decoding.

    Keep the valid source ZIP too. This is not an integrity-corruption test: the
    invalid compressed image must pass archive inventory and fail PIL decoding.
    """
    from test_generic_author_package import encoded, record
    import generic_author_package as generic
    with zipfile.ZipFile(source) as archive:
        entries = {name: archive.read(name) for name in archive.namelist()}
    metadata = json.loads(entries['map-package.json'])
    binding_path = generic.bindings_path(metadata['packageId'])
    document = json.loads(entries['data/' + binding_path])
    image_path = document['layers'][0]['displayPath']
    bad_image = b'CI synthetic invalid image, deliberately not PNG bytes.\n'
    entries['data/' + image_path] = bad_image
    document['layers'][0].update(displayBytes=len(bad_image),
                                displaySha256=hashlib.sha256(bad_image).hexdigest())
    entries['data/' + binding_path] = encoded(document)
    metadata['files'] = [record(row['path'], entries['data/' + row['path']]) for row in metadata['files']]
    entries['map-package.json'] = encoded(metadata)
    with zipfile.ZipFile(destination, 'x', compression=zipfile.ZIP_DEFLATED) as archive:
        for name, value in entries.items():
            archive.writestr(name, value)


def prepare_fixture(root, *, invalid_image=False):
    # Use existing production-contract producers without setUp/tearDown or any
    # TemporaryDirectory cleanup. Every failed object remains in this job root.
    from test_generic_author_package import make_base, GenericPackageTests, record
    import generic_author_base as base
    make_base(root)
    preserved = []
    for relative, data in (
            ('_internal/ci-unknown-preserved.txt', b'SYNTHETIC unknown resource object; retain bytes.\n'),
            ('work/ci-user-preserved.txt', b'SYNTHETIC personal-data sentinel; not actual user data.\n')):
        target = root / relative; target.parent.mkdir(parents=True, exist_ok=True)
        with target.open('xb') as stream: stream.write(data)
        preserved.append(record(relative, data))
    holder = SimpleNamespace(root=root, profile='author-ci-synthetic', sequence=0)
    holder.contract = base.resolve_layer(root, 's0', 'nightmare', 'map', 'B1')
    holder.deps = [record(p, (root / p).read_bytes()) for p in holder.contract['dependencies']]
    archive = GenericPackageTests.package(holder)
    destination = root / 'fixture-author.zip'
    if invalid_image:
        original = root / 'fixture-valid-author.zip'
        shutil.move(str(archive), original)
        invalid_image_archive(original, destination)
    else:
        shutil.move(str(archive), destination)
    with zipfile.ZipFile(destination) as package:
        metadata = json.loads(package.read('map-package.json'))
    return dict(archiveSha256=build.sha256(destination), dependencies=holder.deps,
                case='invalid-image' if invalid_image else 'valid-image',
                initialResourceFiles=snapshot(root / '_internal'), preservedFiles=preserved,
                payload=metadata['files'], packageId=holder.profile,
                pixels=('invalid PNG bytes with retained valid synthetic RGBA 9x8 ZIP and ones NPZ 8x9'
                        if invalid_image else 'constant synthetic RGBA 9x8 and ones NPZ 8x9') + ', not game input',
                schemaSourceKind='fixture protocol label only, no game provenance')


def validate_business(root, fixture, receipt, helper_exit, expectation):
    if expectation not in {'missing-image-dependencies', 'success', 'invalid-image-rejected'}:
        raise ValueError('Unknown business expectation')
    if receipt.get('requestId') != fixture['requestId'] or receipt.get('archiveSha256') != fixture['archiveSha256']:
        raise ValueError('Wrong request/ZIP receipt')
    if receipt.get('parentExit') != 'exited-matching-process-handle' or not receipt.get('completedAtUTC'):
        raise ValueError('Actual parent identity and normal exit were not proved')
    for row in fixture['preservedFiles']:
        path = root / row['path']
        if not path.is_file() or path.stat().st_size != row['bytes'] or build.sha256(path) != row['sha256']:
            raise ValueError('Synthetic unknown/user object changed')
    for row in fixture['dependencies']:
        path = root / row['path']
        if not path.is_file() or path.stat().st_size != row['bytes'] or build.sha256(path) != row['sha256']:
            raise ValueError('Synthetic base dependency changed')
    author_receipt = root / 'work/map-packages' / (fixture['packageId'] + '.json')
    if expectation in {'missing-image-dependencies', 'invalid-image-rejected'}:
        if helper_exit != 2 or receipt.get('success') is not False:
            raise ValueError('Expected failure was not a real failed import')
        if expectation == 'missing-image-dependencies':
            if receipt.get('errorCode') != 'ModuleNotFoundError' or "'PIL'" not in receipt.get('reason', ''):
                raise ValueError('Not the expected missing-PIL old-red; retain actual outcome')
        elif (receipt.get('errorCode') != 'author-v2-invalid-image' or
                receipt.get('reason') != 'author-v2-invalid-image'):
            raise ValueError('Invalid-image case failed for a different reason')
        if (author_receipt.exists() or any((root / row['path']).exists() for row in fixture['payload'])
                or snapshot(root / '_internal') != fixture['initialResourceFiles']):
            raise ValueError('Failed import published author files or changed resource bytes')
        state = root / 'work/map-packages'
        unexpected = [p for p in state.rglob('*') if p.is_file() and
                      p.relative_to(state).as_posix() not in {'import.lock', 'last-import-result.json'}]
        if unexpected:
            raise ValueError('Failed validation left a transaction/publication object')
        if expectation == 'missing-image-dependencies':
            return dict(outcome='old-red-confirmed', businessValidated=False,
                        validImportExpectedExitCode=0, actualHelperExitCode=helper_exit,
                        dynamicMissingDependency='PIL', numpyMissingEvidence='original-spec-exclusion-only')
        return dict(outcome='invalid-image-rejected-without-publication', businessValidated=False,
                    rejectionValidated=True, resourceBytesUnchanged=True, actualHelperExitCode=helper_exit)
    if helper_exit != 0 or receipt.get('success') is not True:
        raise ValueError('Valid synthetic import did not succeed')
    installed = json.loads(author_receipt.read_text('utf-8'))
    for field, value in [('packageId', fixture['packageId']), ('archiveSha256', fixture['archiveSha256']),
                         ('requiresFiles', fixture['dependencies'])]:
        if installed.get(field) != value:
            raise ValueError('Installed author receipt mismatch: ' + field)
    for row in fixture['payload']:
        path = root / row['path']
        if not path.is_file() or path.stat().st_size != row['bytes'] or build.sha256(path) != row['sha256']:
            raise ValueError('Installed synthetic author bytes differ')
    return dict(outcome='synthetic-business-passed', businessValidated=True,
                validImportExpectedExitCode=0, actualHelperExitCode=helper_exit)


def freeze_parent(work, profile, env):
    # A source-owned minimal onedir test process, never the actual main client.
    # Exclude image/GUI dependencies from THIS parent: it only launches/waits for
    # an observer handshake and must not supply modules to the separate helper.
    command = [sys.executable, '-m', 'PyInstaller', '--noconfirm', '--clean', '--onedir', '--console',
        '--name', '星图', '--contents-directory', '_synthetic_parent_runtime',
        '--distpath', str(work / 'parent-freeze'), '--workpath', str(work / 'parent-pyinstaller'),
        '--specpath', str(work / 'parent-spec'), '--paths', str(ROOT / 'app'),
        '--add-data', str(profile) + os.pathsep + '.',
        *sum((['--exclude-module', name] for name in ['numpy', 'PIL', 'cv2', 'tkinter', '_tkinter', 'PySide6', 'PySide2', 'PyQt5', 'PyQt6']), []),
        str(ROOT / 'tests/fixtures/synthetic_import_parent.py')]
    subprocess.run(command, cwd=ROOT, env=env, check=True, timeout=300)
    parent = work / 'parent-freeze/星图'
    build.verify_invoker_manifest(parent / '星图.exe')
    return parent


def publish_ack(path, value):
    if path.exists():
        raise ValueError('Observer ACK already exists')
    pending = path.with_suffix(path.suffix + '.tmp')
    with pending.open('x', encoding='utf-8') as stream:
        json.dump(value, stream); stream.flush(); os.fsync(stream.fileno())
    os.replace(pending, path)


def observe_real_handoff(root, work, env, fixture, progress):
    from star_map_import_helper import _kernel, _identity, _creation_time
    api = _kernel()
    handle = None
    with (work / 'parent-output.log').open('wb') as output:
        parent = subprocess.Popen([str(root / '星图.exe')], cwd=root, env=env, stdout=output, stderr=subprocess.STDOUT)
        progress.update(phase='parent-started', parentPid=parent.pid, forcedCleanup=False)
        try:
            deadline = time.monotonic() + 20
            handoff_path = root / 'handoff.json'
            while not handoff_path.exists():
                if parent.poll() is not None or time.monotonic() >= deadline:
                    raise ValueError('Synthetic parent exited or timed out before handoff')
                time.sleep(.05)
            handoff = json.loads(handoff_path.read_text('utf-8'))
            if handoff['probeId'] != fixture['probeId'] or handoff['parent']['pid'] != parent.pid:
                raise ValueError('Handoff is not this actual parent')
            progress.update(phase='handoff-observed', handoff=handoff)
            child = handoff['child']
            handle = api.OpenProcess(0x100000 | 0x1000, False, child['pid'])
            if not handle or _identity(api, handle, child['pid']) != child:
                raise ValueError('Could not retain exact live helper process')
            if os.path.normcase(os.path.realpath(child['executable'])) != os.path.normcase(str(root / 'star-map-import-helper.exe')):
                raise ValueError('Observed process is not this separate frozen helper')
            progress.update(phase='child-handle-retained', realChildHandleRetained=True)
            publish_ack(root / 'observer-ready.json', dict(probeId=fixture['probeId'], child=child))
            parent_code = parent.wait(timeout=20)
            progress.update(phase='parent-exited', parentExitCode=parent_code)
            if parent_code != 0:
                raise ValueError('Synthetic parent did not exit normally with zero status')
            if api.WaitForSingleObject(handle, 60000) != 0:
                raise TimeoutError('Real helper did not terminate within its bounded operation')
            if _creation_time(api, handle) != child['creationTime']:
                raise ValueError('Retained child identity changed')
            code = wintypes.DWORD()
            if not api.GetExitCodeProcess(handle, ctypes.byref(code)):
                raise ctypes.WinError(ctypes.get_last_error())
            progress.update(phase='helper-exited', helperExitCode=code.value)
            return progress
        finally:
            if handle:
                api.CloseHandle(handle)
            # No name-based kill or fake completion. Timeout means failed case;
            # disposable-runner teardown owns any remaining process cleanup.


def preserve_data_evidence(root, evidence):
    # Save all synthetic data/transaction objects, not partial software packages.
    for name in ('_internal', 'work'):
        source = root / name
        if source.exists():
            shutil.copytree(source, evidence / name)
    for name in ('fixture-author.zip', 'fixture-valid-author.zip', 'synthetic-probe.json', 'handoff.json', 'handoff.tmp', 'observer-ready.json', 'observer-ready.json.tmp'):
        source = root / name
        if source.exists():
            shutil.copy2(source, evidence / name)


def main(argv=None):
    global ACTIVE
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--work', type=Path, required=True)
    parser.add_argument('--source-commit', required=True)
    parser.add_argument('--expect', choices=('missing-image-dependencies', 'success', 'invalid-image-rejected'), required=True)
    args = parser.parse_args(argv)
    if os.name != 'nt' or os.environ.get('GITHUB_ACTIONS') != 'true' or os.environ.get('RUNNER_ENVIRONMENT') != 'github-hosted':
        raise ValueError('Hosted Windows only')
    source = build.source_identity(args.source_commit)
    work, temp = args.work.resolve(), Path(os.environ['RUNNER_TEMP']).resolve()
    if work.exists() or work == temp or not work.is_relative_to(temp) or work.is_relative_to(ROOT):
        raise ValueError('New isolated child of RUNNER_TEMP outside repository required')
    if sys.version_info[:3] != (3, 13, 3) or sys.maxsize <= 2**32:
        raise ValueError('Pinned Windows x64 Python 3.13.3 required')
    pins = build._requirements(ROOT / 'tools/requirements-qt-build.txt')
    versions = {p: importlib.metadata.version(p) for p in PACKAGES}
    if any(versions[p] != pins[p] for p in PACKAGES):
        raise ValueError('Build/data producer dependency pin mismatch')
    registration = compile_probe.registration_snapshot()
    work.mkdir(); root = work / 'synthetic-install'; root.mkdir()
    evidence = work / 'evidence'; evidence.mkdir()
    report = dict(schema='xingtu-frozen-helper-synthetic-business-v1', status='running', source=source,
        expectedMode=args.expect, packages=versions, syntheticParent=True, setupStarted=False,
        actualClientValidated=False, actualGameResourcesUsed=False, businessValidated=False,
        sourceSpecSha256=build.sha256(ROOT / 'tools/qt-import-helper.spec'))
    ACTIVE = (evidence / 'business-record.json', report)
    try:
        fixture = prepare_fixture(root, invalid_image=args.expect == 'invalid-image-rejected')
        fixture.update(probeId=uuid.uuid4().hex, requestId=uuid.uuid4().hex)
        compile_probe.write_json(root / 'synthetic-probe.json', fixture)
        report['fixture'] = fixture
        profile = work / 'installation-profile.json'
        compile_probe.write_json(profile, dict(product='星图', sourceCommit=source['commit'],
            installerInstance=dict(id='production', **build.INSTANCES['production']),
            syntheticHelperBusinessProbe=True, probeId=fixture['probeId']))
        inputs = work / 'freeze-inputs.json'
        compile_probe.write_json(inputs, dict(sourceRoot=str(ROOT), product='星图', profilePath=str(profile)))
        env = {k: v for k, v in os.environ.items() if k not in {'PYTHONPATH', 'PYTHONHOME'}}
        env['XINGTU_QT_BUILD_INPUTS'] = str(inputs)
        report['phase'] = 'freeze-original-helper'; compile_probe.write_json(*ACTIVE)
        helper = build.assemble_recovery_helper(work, root, env, kind='import')
        report['helper'] = helper
        files = helper['files']
        current_payload = dict(fileCount=len(files), rawBytes=sum(row['bytes'] for row in files.values()))
        report['helperPayloadComparison'] = dict(old=OLD_IMPORT_PAYLOAD, current=current_payload,
            fileCountDelta=current_payload['fileCount'] - OLD_IMPORT_PAYLOAD['fileCount'],
            rawBytesDelta=current_payload['rawBytes'] - OLD_IMPORT_PAYLOAD['rawBytes'],
            scope='import helper uncompressed files only; not Setup size or installed upgrade cost')
        helper['frozenSource'] = compile_probe.verify_archive(root / helper['executable'], 'import')
        from PyInstaller.archive.readers import CArchiveReader
        archive = CArchiveReader(str(root / helper['executable']))
        pyz = archive.open_embedded_archive(next(n for n, row in archive.toc.items() if row[-1] == 'z'))
        report['imageModulesFrozen'] = {p: p in pyz.toc for p in ('PIL', 'numpy')}
        report['phase'] = 'freeze-synthetic-parent'; compile_probe.write_json(*ACTIVE)
        parent = freeze_parent(work, profile, env)
        for path in parent.iterdir():
            if path.is_dir(): shutil.copytree(path, root / path.name)
            else: shutil.copy2(path, root / path.name)
        report['frozenProgramFiles'] = snapshot(parent)
        parent_archive = CArchiveReader(str(root / '星图.exe'))
        report['syntheticParentSourceSha256'] = compile_probe.verify_code(
            marshal.loads(parent_archive.extract('synthetic_import_parent')),
            ROOT / 'tests/fixtures/synthetic_import_parent.py')
        if build.sha256(root / '_synthetic_parent_runtime/installation-profile.json') != build.sha256(profile):
            raise ValueError('Synthetic parent baked profile differs')
        report['phase'] = 'real-parent-bound-import'; helper.update(launchAttempted=True, launched=None)
        compile_probe.write_json(*ACTIVE)
        report['processes'] = {}
        observe_real_handoff(root, work, env, fixture, report['processes'])
        helper['launched'] = True
        receipt = json.loads((root / 'work/map-packages/last-import-result.json').read_text('utf-8'))
        report['actualReceipt'] = receipt
        report.update(validate_business(root, fixture, receipt, report['processes']['helperExitCode'], args.expect))
        helper['nativeParentWaitTested'] = True
        from resource_transaction import resource_runtime_lease
        with resource_runtime_lease(root): pass
        report['leaseReacquiredAfterHelperExit'] = True
        if compile_probe.registration_snapshot() != registration:
            raise ValueError('Registration changed')
        if build.source_identity(args.source_commit) != source:
            raise ValueError('Source changed')
        report.update(status='passed', phase='completed', productionRegistrationUnchanged=True)
    except Exception as error:
        report.update(status='failed', failure=dict(type=type(error).__name__, message=str(error)), ownedChildTeardownVerified=False)
        raise
    finally:
        try:
            report['remainingObjectInventory'] = snapshot(root)
            preserve_data_evidence(root, evidence)
        except Exception as capture_error:
            report.update(status='failed', evidenceCaptureError=dict(type=type(capture_error).__name__, message=str(capture_error)))
            compile_probe.write_json(*ACTIVE)
            raise
        compile_probe.write_json(*ACTIVE)
    print(json.dumps({k: report[k] for k in ('schema', 'status', 'outcome', 'businessValidated', 'setupStarted', 'actualClientValidated')}, indent=2))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
