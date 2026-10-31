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
}


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
        buildFingerprint=source["commit"][:8].upper(),
        reusedCompiledPayload=dict(sourceCommit=prior["source"]["commit"],
            sourceTree=prior["source"]["tree"], priorRecordSha256=build.sha256(prior_path),
            runtimeSourceIdentical=True, pythonFreezeRepeated=False, files=reused),
        installerLanguage=dict(name="chinesesimp", fileSha256=CHINESE_SHA256,
            fixedSimplifiedChinese=True, wizardLaunched=False))
    record["installerInstance"].update(build.INSTANCES["production"])
    rebound = {}
    for name in ("installation-profile.json", "_internal/installation-profile.json"):
        path = stage / name
        profile = json.loads(path.read_text(encoding="utf-8"))
        profile.update(sourceCommit=source["commit"], buildFingerprint=record["buildFingerprint"],
                       installerInstance=record["installerInstance"])
        path.write_text(json.dumps(profile, indent=2), encoding="utf-8")
        rebound[name] = dict(previousSha256=reused[name]["sha256"], sha256=build.sha256(path))
    fingerprint = stage / "_internal/references/build-fingerprint.txt"
    fingerprint.write_text(record["buildFingerprint"] + "\n", encoding="ascii")
    rebound["_internal/references/build-fingerprint.txt"] = dict(
        previousSha256=reused["_internal/references/build-fingerprint.txt"]["sha256"],
        sha256=build.sha256(fingerprint))
    record["reusedCompiledPayload"]["reboundIdentityFiles"] = rebound
    record["resources"]["references/build-fingerprint.txt"] = dict(
        bytes=fingerprint.stat().st_size, sha256=build.sha256(fingerprint))
    record["frozenVerification"] = build.verify_frozen(stage, record["resources"],
        [p.name for p in (ROOT / "experiments/qt_quick").glob("*.qml")], record["product"])
    for kind in ("recovery", "import"):
        build.verify_invoker_manifest(stage / ("star-map-" + kind + "-helper.exe"))
    include, record["fileProjection"] = build.write_production_files_include(stage, record["resources"], output)
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
