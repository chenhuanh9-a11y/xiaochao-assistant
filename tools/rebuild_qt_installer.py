"""Recompile only Setup when committed installer-only changes leave runtime identical.

Copies the original exact production projection to a new owned output, never
launches Setup or an application, and never copies runtime work/user settings.
"""
from pathlib import Path
import argparse
import copy
import json
import shutil
import subprocess
import sys

import build_qt_client as build

ROOT = Path(__file__).resolve().parents[1]
CHINESE_SHA256 = "e0b0b350e2245f3c5e65586dfe43d574f6e7f06f2261149aba284954b3fc9a8d"
INSTALLER_ONLY_FILES = {
    "tools/qt-client.iss", "tools/build_qt_client.py",
    "tools/rebuild_qt_installer.py", "tools/INSTALLER_LANGUAGE.md",
    "tools/qt-game-watcher-lifecycle.iss", "tools/qt-uninstall-data.iss",
    "tools/qt-resource-reset.iss",
}


def validate_managed_runtime_reuse(stage, prior, source):
    """Bind candidate-only packaging to an already built matching runtime.

    A mutable side profile alone is not evidence. Require the completed build's
    exact manifest and EXE hashes, release report, and compiled-source identity;
    compare runtime source, not the last installer-wrapper commit.
    """
    stage = Path(stage)
    projection = prior.get("fileProjection", {})
    required = build.RESOURCE_INSTALLATION_POLICY["existingBasePlane"]
    if (projection.get("policy", {}).get("existingBasePlane") != required
            or projection.get("managedBaseManifest") != "_internal/managed-base-resources/manifest.json"
            or not projection.get("managedBaseManifestSha256")
            or not prior.get("releaseReport", {}).get("executableSha256")):
        raise ValueError("Managed base deployment requires a full freeze; legacy runtime reuse is unsupported")
    compiled = (prior.get("compiledPayloadSourceCommit")
                or prior.get("reusedCompiledPayload", {}).get("sourceCommit") or prior["source"]["commit"])
    changed = set(build._git("diff", "--name-only", compiled, source["commit"]).splitlines())
    if changed - INSTALLER_ONLY_FILES:
        raise ValueError("Compiled runtime changed; perform a full freeze")
    manifest = build._local_file(stage, projection["managedBaseManifest"])
    if build.sha256(manifest) != projection["managedBaseManifestSha256"]:
        raise ValueError("Managed candidate manifest changed; perform a full freeze")
    sys.path.insert(0, str(ROOT / "app"))
    from managed_base_resources import _manifest
    if _manifest(manifest)["bundleId"] != projection.get("managedBaseBundleId"):
        raise ValueError("Managed candidate identity changed; perform a full freeze")
    executable = build._local_file(stage, prior["product"] + ".exe")
    report_path = build._local_file(stage, prior["releaseReport"]["file"])
    report = build._read(report_path)
    if (build.sha256(executable) != prior["releaseReport"]["executableSha256"]
            or build.sha256(report_path) != prior["releaseReport"]["sha256"]
            or report.get("executableSha256") != prior["releaseReport"]["executableSha256"]
            or (report.get("compiledPayloadSourceCommit") or report.get("sourceCommit")) != compiled):
        raise ValueError("Compiled payload identity changed; perform a full freeze")
    for name in ("installation-profile.json", "_internal/installation-profile.json"):
        profile = build._read(build._local_file(stage, name))
        if (profile.get("resourceInstallationPolicy", {}).get("existingBasePlane") != required
                or profile.get("sourceCommit") != compiled):
            raise ValueError("Managed runtime profile identity changed; perform a full freeze")
    # asInvoker only describes elevation, not the helper's compiled behavior.
    # Reuse the complete helper inventories recorded by the original freeze.
    for kind in ("recovery", "import", "reset"):
        helper = prior.get(kind + "Helper")
        executable = "star-map-" + kind + "-helper.exe"
        runtime = "_star_map_" + kind + "_runtime/"
        inventory = helper.get("files") if isinstance(helper, dict) else None
        if (not isinstance(inventory, dict) or not inventory or helper.get("included") is not True
                or helper.get("executable") != executable or helper.get("runtimeDirectory") != runtime[:-1]
                or executable not in inventory
                or not any(isinstance(name, str) and name.startswith(runtime) for name in inventory)):
            raise ValueError("Helper inventory missing or invalid; perform a full freeze")
        for name, row in inventory.items():
            if (not isinstance(name, str) or (name != executable and not name.startswith(runtime))
                    or not isinstance(row, dict) or type(row.get("bytes")) is not int
                    or not isinstance(row.get("sha256"), str)):
                raise ValueError("Helper inventory missing or invalid; perform a full freeze")
            try:
                path = build._local_file(stage, name)
                matches = path.stat().st_size == row["bytes"] and build.sha256(path) == row["sha256"]
            except (OSError, ValueError) as error:
                raise ValueError("Helper payload missing or changed; perform a full freeze") from error
            if not matches:
                raise ValueError("Helper payload missing or changed; perform a full freeze")
    watcher = prior.get('gameWatcher')
    executable = 'XingtuGameWatcher.exe'
    inventory = watcher.get('files') if isinstance(watcher, dict) else None
    if (not isinstance(inventory, dict) or set(inventory) != {executable}
            or watcher.get('included') is not True or watcher.get('executable') != executable
            or executable not in projection.get('families', {}).get('program', [])):
        raise ValueError('Game watcher inventory/projection missing; perform a full freeze')
    row = inventory[executable]
    try:
        path = build._local_file(stage, executable)
        matches = (isinstance(row, dict) and type(row.get('bytes')) is int
                   and isinstance(row.get('sha256'), str) and path.stat().st_size == row['bytes']
                   and build.sha256(path) == row['sha256'])
    except (OSError, ValueError) as error:
        raise ValueError('Game watcher missing or changed; perform a full freeze') from error
    if not matches:
        raise ValueError('Game watcher missing or changed; perform a full freeze')
    return compiled


def refresh_release_identity(stage, record, compiled_source_commit):
    """Rebind report labels while preserving the actual reused EXE provenance."""
    stage = Path(stage)
    record.update(compiledPayloadSourceCommit=compiled_source_commit,
        buildFingerprint=compiled_source_commit[:8].upper(),
        installerSourceCommit=record['source']['commit'],
        installerBuildFingerprint=record['source']['commit'][:8].upper())
    rebound = {}
    for name in ('installation-profile.json', '_internal/installation-profile.json'):
        path = stage / name
        previous = build.sha256(path)
        profile = json.loads(path.read_text(encoding='utf-8'))
        profile.update(sourceCommit=compiled_source_commit, buildFingerprint=record['buildFingerprint'],
            compiledPayloadSourceCommit=compiled_source_commit,
            installerSourceCommit=record['source']['commit'], installerInstance=record['installerInstance'])
        path.write_text(json.dumps(profile, indent=2), encoding='utf-8')
        rebound[name] = dict(previousSha256=previous, sha256=build.sha256(path))
    fingerprint = stage / '_internal/references/build-fingerprint.txt'
    previous = build.sha256(fingerprint)
    fingerprint.write_text(record['buildFingerprint'] + '\n', encoding='ascii')
    rebound['_internal/references/build-fingerprint.txt'] = dict(previousSha256=previous, sha256=build.sha256(fingerprint))
    record['reusedCompiledPayload']['reboundIdentityFiles'] = rebound
    record['resources']['references/build-fingerprint.txt'] = dict(bytes=fingerprint.stat().st_size,
        sha256=build.sha256(fingerprint))
    record['releaseReport'] = build.stage_release_report(stage, product=record['product'],
        source=dict(record['source'], commit=compiled_source_commit), version=record['version'],
        resource_version=record['resourceVersion'],
        resource_inventory_sha256=record['resourceInventorySha256'],
        compiled_source_commit=compiled_source_commit, installer_source_commit=record['source']['commit'])
    ownership = Path(stage) / '_installer-owned-files.txt'
    names = ownership.read_text(encoding='utf-8-sig').splitlines()
    if 'release-report.json' not in names:
        names.append('release-report.json')
    ownership.write_text('\n'.join(names) + '\n', encoding='utf-8-sig')
    record['programOwnership'].update(files=len(names), sha256=build.sha256(ownership))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-commit", required=True)
    parser.add_argument("--from-record", type=Path, required=True)
    parser.add_argument("--iscc", type=Path, required=True)
    args = parser.parse_args()
    source = build.source_identity(args.source_commit)
    tools = build.environment(args.iscc)
    if not tools["success"]:
        raise ValueError("Build tool pins do not match")
    prior_path = args.from_record.resolve()
    if not prior_path.is_relative_to(ROOT / "work"):
        raise ValueError("Reuse only an owned build record under this checkout/work")
    prior = json.loads(prior_path.read_text(encoding="utf-8"))
    if (not prior.get("buildCompleted") or prior["installerInstance"]["id"] != "production"
            or prior["product"] != build.INSTANCES["production"]["product"]
            or prior["installerInstance"]["appId"] != build.INSTANCES["production"]["appId"]
            or not prior["source"]["clean"]):
        raise ValueError("A completed clean production build is required")
    changed = set(build._git("diff", "--name-only", prior["source"]["commit"], source["commit"]).splitlines())
    if changed - INSTALLER_ONLY_FILES:
        raise ValueError(f"Runtime changed; perform a full freeze: {sorted(changed - INSTALLER_ONLY_FILES)}")
    language = args.iscc.resolve().parent / "Languages/ChineseSimplified.isl"
    if not language.is_file() or build.sha256(language) != CHINESE_SHA256:
        raise ValueError("The verified official Simplified Chinese language file is unavailable/changed")
    output = ROOT / "work/qt-setup" / (source["commit"][:12] + "-production-zh")
    if output.exists():
        raise ValueError("Installer output must be new")
    old_stage = prior_path.parent / "freeze" / prior["product"]
    runtime_commit = validate_managed_runtime_reuse(old_stage, prior, source)
    projection = prior["fileProjection"]["families"]
    names = [name for group in projection.values() for name in group]
    if (len(names) != len(set(names))
            or any(name.casefold().startswith("work/") for name in names)):
        raise ValueError("Invalid prior exact file projection")
    stage = output / "freeze" / prior["product"]
    stage.mkdir(parents=True)
    reused = {}
    for name in names:
        original = build._local_file(old_stage, name)
        target = stage / name
        target.parent.mkdir(parents=True, exist_ok=True)
        before = build.sha256(original)
        shutil.copy2(original, target)
        if build.sha256(target) != before:
            raise ValueError(f"Reused payload changed while copying: {name}")
        reused[name] = dict(bytes=target.stat().st_size, sha256=before)
    record = copy.deepcopy(prior)
    record.update(source=source, sourceRoot=str(ROOT), output=str(output), tools=tools,
        action="installer-only", buildCompleted=False, executableLaunched=False,
        installPerformed=False, published=False,
        reusedCompiledPayload=dict(sourceCommit=runtime_commit,
            priorBuildSourceCommit=prior['source']['commit'], priorBuildSourceTree=prior["source"]["tree"],
            priorRecordSha256=build.sha256(prior_path),
            runtimeSourceIdentical=True, pythonFreezeRepeated=False, files=reused),
        installerLanguage=dict(name="chinesesimp", fileSha256=CHINESE_SHA256,
            fixedSimplifiedChinese=True, wizardLaunched=False))
    record["installerInstance"].update(build.INSTANCES["production"])
    refresh_release_identity(stage, record, runtime_commit)
    author_paths = sorted(record.get("bundledAuthorDefaults", {}).get("files", {}))
    record["frozenVerification"] = build.verify_frozen(stage, record["resources"],
        [p.name for p in (ROOT / "experiments/qt_quick").glob("*.qml")], record["product"],
        bundled_author_paths=author_paths)
    for kind in ("recovery", "import", "reset"):
        build.verify_invoker_manifest(stage / ("star-map-" + kind + "-helper.exe"))
    build.verify_invoker_manifest(stage / 'XingtuGameWatcher.exe')
    include, record["fileProjection"] = build.write_production_files_include(stage, record["resources"], output,
        resource_version=record["resourceVersion"], bundled_author_paths=author_paths, source=source)
    reset_defines = build.resource_reset_installer_defines(stage, record, output, source)
    record_path = output / "build-record.json"
    record_path.write_text(json.dumps(record, indent=2), encoding="utf-8")
    instance = record["installerInstance"]
    display = str(record["version"]["displayVersion"]) + instance["versionSuffix"]
    setup_name = build.setup_basename(instance, display, source["commit"])
    subprocess.run([str(args.iscc.resolve()), f"/DStageDir={stage}",
        f"/DSetupOutputDir={output / 'setup'}", f"/DSetupName={setup_name}",
        f"/DDisplayVersion={display}", f"/DNumericVersion={record['version']['numericVersion']}",
        "/DInstallerAppId={{" + instance["appId"] + "}",
        f"/DInstallerAppName={instance['appName']}", f"/DInstallerFolder={instance['installFolder']}",
        f"/DInstallerGroup={instance['startMenuGroup']}", f"/DInstallerExeName={record['product']}.exe",
        f"/DProductionFilesInclude={include}", "/DProductionInstaller",
        *reset_defines,
        str(ROOT / "tools/qt-client.iss")], cwd=ROOT, check=True)
    if build.source_identity(args.source_commit) != source:
        raise ValueError("Source changed during installer compilation")
    setup = output / "setup" / (setup_name + ".exe")
    record.update(buildCompleted=True, installer=dict(fileName=setup.name,
        bytes=setup.stat().st_size, sha256=build.sha256(setup)),
        recipes={name: build.sha256(ROOT / name) for name in INSTALLER_ONLY_FILES})
    record_path.write_text(json.dumps(record, indent=2), encoding="utf-8")
    print(json.dumps(dict(setup=str(setup), **record["installer"])))


if __name__ == "__main__":
    main()
