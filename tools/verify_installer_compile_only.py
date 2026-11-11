"""Hosted Windows compile-only evidence, never a distributable product build.

Freeze the five ORIGINAL helper specs. Compile ORIGINAL production Inno with
/O- and explicitly synthetic main/base bytes. No production resource lock is
changed or declared satisfied. No Xingtu Setup is produced or executed.
"""
from __future__ import annotations
import argparse
import hashlib
import importlib.metadata
import json
import marshal
import os
from pathlib import Path
import subprocess
import sys
import types

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / 'tools'), str(ROOT / 'app')]
import build_qt_client as build

ISCC_SHA256 = '0a8757031b33777e4c9cbffee40f11a5062b36d25cbe144c1db73b6102b80ad7'
HELPER_PACKAGES = ('PyInstaller', 'pyinstaller-hooks-contrib', 'pefile', 'psutil', 'numpy', 'Pillow')
HELPER_MODULES = {
    'import': ('star_map_import', 'generic_author_package', 'packaged_product_identity', 'resource_transaction'),
    'recovery': ('star_map_import', 'star_map_import_helper', 'map_package_recovery_plan', 'map_package_upgrade', 'resource_transaction', 'packaged_product_identity'),
    'reset': ('resource_upgrade_reset', 'installer_resource_lease', 'installer_upgrade_coordinator', 'installer_upgrade_journal', 'installer_native_observation', 'managed_base_resources', 'map_package_upgrade', 'star_map_import', 'resource_transaction', 'installer_legacy_bridge', 'installer_legacy_handoff', 'installer_legacy_assets', 'legacy_migration'),
    'watcher': ('game_startup', 'game_startup_win32', 'packaged_product_identity'),
    'uninstall': ('installer_uninstall', 'installer_resource_lease', 'game_startup', 'packaged_product_identity'),
}
ENTRIES = {**{k: 'star_map_' + k + '_helper' for k in ('import', 'recovery', 'reset', 'uninstall')}, 'watcher': 'game_startup_watcher'}
_ACTIVE_REPORT = None
RECIPES = ('qt-client.iss', 'qt-game-watcher-lifecycle.iss', 'qt-resource-reset.iss', 'qt-legacy-migration.iss', 'qt-uninstall-data.iss', 'qt-installer-target.iss', 'qt-import-helper.spec', 'qt-recovery-helper.spec', 'qt-reset-helper.spec', 'qt-uninstall-helper.spec', 'qt-game-watcher.spec')


def cli_cases(kind):
    if kind not in ENTRIES:
        raise ValueError('Unknown helper')
    # Watcher with NO arguments is expressly forbidden: it can start listening.
    return ((('--help',), 2),) if kind == 'watcher' else ((('--help',), 0), ((), 2))


def normalized(code):
    if not isinstance(code, types.CodeType):
        raise ValueError('Frozen object is not code')
    return code.replace(co_filename='<verified-source>', co_consts=tuple(
        normalized(x) if isinstance(x, types.CodeType) else x for x in code.co_consts))


def verify_code(actual, source):
    expected = compile(source.read_bytes(), str(source), 'exec', dont_inherit=True, optimize=0)
    if normalized(actual) != normalized(expected):
        raise ValueError('Frozen code differs from source: ' + source.name)
    return build.sha256(source)


def verify_archive(executable, kind):
    from PyInstaller.archive.readers import CArchiveReader
    archive = CArchiveReader(str(executable))
    entry = ENTRIES[kind]
    if archive.toc.get(entry, (None,))[-1] != 's':
        raise ValueError('Missing actual frozen entry script: ' + entry)
    hashes = {entry: verify_code(marshal.loads(archive.extract(entry)), ROOT / 'app' / (entry + '.py'))}
    names = [name for name, row in archive.toc.items() if row[-1] == 'z']
    if len(names) != 1:
        raise ValueError('Expected one embedded PYZ')
    pyz = archive.open_embedded_archive(names[0])
    for name in HELPER_MODULES[kind]:
        hashes[name] = verify_code(pyz.extract(name), ROOT / 'app' / (name + '.py'))
    if any(name.split('.')[0] in {'PySide6', 'PyQt5', 'PyQt6', 'PySide2', 'tkinter', '_tkinter'} for name in pyz.toc):
        raise ValueError('GUI runtime unexpectedly bundled into helper')
    image_modules = {name: name in pyz.toc for name in ('PIL', 'numpy')}
    if kind == 'import' and not all(image_modules.values()):
        raise ValueError('Import helper missing required image/array dependencies')
    return dict(entry=entry, sourceModuleSha256=hashes, moduleCount=len(pyz.toc), sourceCodeMatched=True, imageModulesFrozen=image_modules)


def write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')


def make_synthetic_projection(stage, output, source, profile, helpers):
    """Exercise existing ownership/projection generators, not resource eligibility."""
    version = json.loads((ROOT / 'version.json').read_text('utf-8'))
    fixture = b'{"syntheticCompileOnly":true,"notGameResource":true}\n'
    relative = 'recognition/native/ci-compile-only.json'
    target = stage / '_internal' / relative
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(fixture)
    resources = {relative: dict(bytes=len(fixture), sha256=hashlib.sha256(fixture).hexdigest())}
    (stage / '星图.exe').write_bytes(b'NOT A PE. SYNTHETIC COMPILE FIXTURE. NEVER EXECUTE.\n')
    write_json(stage / 'version.json', version)
    write_json(stage / 'installation-profile.json', profile)
    (stage / '_installer-owned-files.txt').write_text('', encoding='utf-8-sig')
    record = dict(resetHelper=helpers['reset'], uninstallHelper=helpers['uninstall'])
    uninstall_defines = build.uninstall_helper_installer_defines(stage, record)
    record['releaseReport'] = build.stage_release_report(stage, product='星图', source=source,
        version=version, resource_version='ci-synthetic-not-release',
        resource_inventory_sha256=hashlib.sha256(json.dumps(resources, sort_keys=True).encode()).hexdigest())
    include, record['fileProjection'] = build.write_production_files_include(stage, resources, output,
        resource_version='ci-synthetic-not-release', source=source)
    build.write_program_ownership(stage, record)
    defines = build.resource_reset_installer_defines(stage, record, output, source)
    return include, defines + uninstall_defines, record


def compiler_arguments(iscc, stage, work, include, defines):
    version = json.loads((ROOT / 'version.json').read_text('utf-8'))
    instance = build.INSTANCES['production']
    return [str(iscc), '/O-', f'/DStageDir={stage}', f'/DSetupOutputDir={work / "setup-must-not-be-produced"}',
        '/DSetupName=COMPILE_ONLY_NOT_A_PRODUCT', f'/DDisplayVersion={version["displayVersion"]}',
        f'/DNumericVersion={version["numericVersion"]}', '/DInstallerAppId={{' + instance['appId'] + '}',
        f'/DInstallerAppName={instance["appName"]}', f'/DInstallerFolder={instance["installFolder"]}',
        f'/DInstallerGroup={instance["startMenuGroup"]}', '/DInstallerExeName=星图.exe',
        '/DProductionInstaller', f'/DProductionFilesInclude={include}', *defines, str(ROOT / 'tools/qt-client.iss')]


def run_logged(command, *, cwd, log, expected, timeout):
    """Persist each CLI/compiler outcome, including output received on timeout."""
    result = dict(expectedExitCode=expected, timeoutSeconds=timeout, status='running')
    try:
        completed = subprocess.run(command, cwd=cwd, timeout=timeout, capture_output=True)
    except subprocess.TimeoutExpired as error:
        log.write_bytes((error.stdout or b'') + b'\n' + (error.stderr or b''))
        result.update(status='timed-out', ownedChildTeardownVerified=False)
        write_json(log.with_suffix('.json'), result)
        raise
    log.write_bytes(completed.stdout + b'\n' + completed.stderr)
    result.update(exitCode=completed.returncode, status='passed' if completed.returncode == expected else 'failed')
    write_json(log.with_suffix('.json'), result)
    if completed.returncode != expected:
        raise ValueError(f'{log.name}: expected exit {expected}, got {completed.returncode}')
    return result


def registration_snapshot():
    """No values from a pre-existing product are read; refuse its presence."""
    import winreg
    product = r'Software\Microsoft\Windows\CurrentVersion\Uninstall\{D04394A0-DACE-4FF9-874D-3DCB43EE53D1}_is1'
    for hive in (winreg.HKEY_CURRENT_USER, winreg.HKEY_LOCAL_MACHINE):
        for view in (winreg.KEY_WOW64_32KEY, winreg.KEY_WOW64_64KEY):
            try:
                with winreg.OpenKey(hive, product, 0, winreg.KEY_READ | view):
                    raise ValueError('Existing production registration; not a disposable empty target')
            except FileNotFoundError:
                pass
    values = []
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r'Software\Microsoft\Windows\CurrentVersion\Run') as key:
            count = winreg.QueryInfoKey(key)[1]
            values = [winreg.EnumValue(key, i) for i in range(count)]
    except FileNotFoundError:
        pass
    return hashlib.sha256(repr(sorted(values)).encode('utf-8')).hexdigest()


def main(argv=None):
    global _ACTIVE_REPORT
    _ACTIVE_REPORT = None
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--iscc', type=Path, required=True)
    parser.add_argument('--work', type=Path, required=True)
    parser.add_argument('--source-commit', required=True)
    args = parser.parse_args(argv)
    if os.name != 'nt' or os.environ.get('GITHUB_ACTIONS') != 'true' or os.environ.get('RUNNER_ENVIRONMENT') != 'github-hosted':
        raise ValueError('Disposable hosted Windows CI only')
    source = build.source_identity(args.source_commit)
    work = args.work.resolve()
    temporary = Path(os.environ['RUNNER_TEMP']).resolve()
    if work.exists() or not work.is_relative_to(temporary) or work == temporary or work.is_relative_to(ROOT):
        raise ValueError('Work must be a NEW child of RUNNER_TEMP, outside source')
    iscc = args.iscc.resolve()
    if not iscc.is_relative_to(temporary) or build.sha256(iscc) != ISCC_SHA256:
        raise ValueError('Pinned isolated compiler required, not a PATH shim')
    if sys.version_info[:3] != (3, 13, 3) or sys.maxsize <= 2**32:
        raise ValueError('Use pinned Windows x64 Python 3.13.3')
    pins = build._requirements(ROOT / 'tools/requirements-qt-build.txt')
    packages = {name: importlib.metadata.version(name) for name in HELPER_PACKAGES}
    if any(packages[name] != pins[name] for name in HELPER_PACKAGES):
        raise ValueError('Helper build dependencies differ from source pins')
    before = registration_snapshot()
    recipes = {name: build.sha256(ROOT / 'tools' / name) for name in RECIPES}
    work.mkdir()
    stage = work / 'synthetic-stage'
    stage.mkdir()
    profile = dict(product='星图', sourceCommit=source['commit'],
        installerInstance=dict(id='production', **build.INSTANCES['production']),
        syntheticCompileOnly=True, resourceVersion='ci-synthetic-not-release')
    profile_path = work / 'installation-profile.json'
    write_json(profile_path, profile)
    inputs = work / 'freeze-inputs.json'
    write_json(inputs, dict(sourceRoot=str(ROOT), product='星图', profilePath=str(profile_path)))
    env = dict(os.environ, XINGTU_QT_BUILD_INPUTS=str(inputs))
    helpers = {}
    report = dict(schema='xingtu-production-inno-compile-only-v1', source=source,
        status='running', phase='freeze', helpers=helpers, setupStarted=False, setupProduced=False,
        fullProductFrozen=False, productionResourceLockValidated=False, upgradeE2eValidated=False)
    _ACTIVE_REPORT = (work / 'compile-only-record.json', report)
    def checkpoint(phase):
        report['phase'] = phase
        write_json(_ACTIVE_REPORT[0], report)
    for kind in ('import', 'recovery', 'reset', 'watcher', 'uninstall'):
        checkpoint('freeze-' + kind)
        helpers[kind] = (build.assemble_game_watcher(work, stage, env) if kind == 'watcher'
            else build.assemble_recovery_helper(work, stage, env, kind=kind))
    for kind, row in helpers.items():
        checkpoint('verify-' + kind)
        executable = stage / row['executable']
        row['frozenSource'] = verify_archive(executable, kind)
        row['finalManifestCount'] = build.verify_invoker_manifest(executable)
        row['cli'] = []
        for index, (arguments, expected) in enumerate(cli_cases(kind)):
            row.update(launched=None, launchAttempted=True, cliOnly=True, businessActionStarted=False)
            checkpoint('cli-' + kind + '-' + str(index))
            outcome = run_logged([str(executable), *arguments], cwd=work, timeout=40,
                log=work / f'helper-{kind}-{index}.log', expected=expected)
            row['launched'] = True
            row['cli'].append(dict(arguments=arguments, **outcome))
    checkpoint('synthetic-projection')
    include, defines, projection = make_synthetic_projection(stage, work, source, profile, helpers)
    command = compiler_arguments(iscc, stage, work, include, defines)
    checkpoint('original-inno-compile-only')
    run_logged(command, cwd=ROOT, timeout=180, log=work / 'inno-compile-only.log', expected=0)
    if list((work / 'setup-must-not-be-produced').glob('*.exe')):
        raise ValueError('/O- unexpectedly produced a Setup')
    if registration_snapshot() != before:
        raise ValueError('Run registration changed during compile-only probe')
    if build.source_identity(args.source_commit) != source or recipes != {name: build.sha256(ROOT / 'tools' / name) for name in RECIPES}:
        raise ValueError('Original source or recipes changed')
    report.update(status='passed', phase='completed', compilerSha256=ISCC_SHA256,
        packages=packages, recipeSha256=recipes, originalProductionInnoCompiled=True, helpers=helpers,
        resourceProjection=projection, syntheticStage=True, originalStageMainIsExecutable=False,
        setupProduced=False, setupStarted=False, productionResourceLockValidated=False,
        fullProductFrozen=False, interactionDriverValidated=False, upgradeE2eValidated=False,
        productionRegistrationAbsentBeforeAndAfter=True, startupRunUnchanged=True,
        limitations=['No maps or original Setup supplied', 'No resource business action or watcher startup exercised',
                     'CLI help does not validate lazy business dependencies; specs exclude numpy/PIL'])
    write_json(work / 'compile-only-record.json', report)
    print(json.dumps({key: report[key] for key in ('schema', 'source', 'originalProductionInnoCompiled', 'syntheticStage', 'setupProduced', 'setupStarted', 'productionResourceLockValidated', 'fullProductFrozen', 'upgradeE2eValidated')}, indent=2))
    return 0


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except Exception as error:
        if _ACTIVE_REPORT is not None:
            path, report = _ACTIVE_REPORT
            report.update(status='failed', failure=dict(type=type(error).__name__, message=str(error)))
            write_json(path, report)
        raise
