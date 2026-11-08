"""Build an offline, local-only QtIFW trial from an existing verified Xingtu freeze.

No installation, app launch, registry write or production source relabeling.
The policy confines this trial Setup to a new, marked output/targets directory.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import uuid
import xml.etree.ElementTree as ET

import build_qt_client as build
from rebuild_qt_installer import validate_managed_runtime_reuse
from probe_qtifw_lifecycle import write_xml

ROOT = Path(__file__).resolve().parents[1]
PACKAGE = "org.xingtu.qtifw.realtrial"


def stage_payload(stage, record, output, *, payload_stage=None, archive_from=None, ifw_bin=None):
    """Use the original installed inventory; exclude every active data plane."""
    from managed_base_resources import CANDIDATE
    compiled = validate_managed_runtime_reuse(stage, record, record["source"])
    inventory_path = build.write_installer_program_inventory(stage, record, output, record["source"])
    inventory = json.loads(inventory_path.read_bytes())
    data = output / "packages" / PACKAGE / "data/program"
    if archive_from:
        prior = json.loads(build._local_file(archive_from, "qtifw-build-record.json").read_bytes())
        binary = build._local_file(archive_from, Path(prior["setup"]).name)
        if (prior["payloadSource"] != compiled or prior["programFiles"] != len(inventory["files"])
                or binary.stat().st_size != prior["bytes"] or build.sha256(binary) != prior["sha256"]):
            raise ValueError("Prior trial archive source changed")
        dump = output / "archive-dump"
        subprocess.run([str(ifw_bin / "devtool.exe"), "dump", str(binary), str(dump)], check=True)
        archives = list((dump / "metadata" / PACKAGE).glob("*program.7z"))
        if len(archives) != 1:
            raise ValueError("Expected one original program archive")
        data.parent.mkdir(parents=True)
        shutil.copy2(archives[0], data.parent / "program.7z")
        return compiled, inventory
    data.mkdir(parents=True)
    for name, row in inventory["files"].items():
        if name.startswith(CANDIDATE + "/payload/"):
            original = name[len(CANDIDATE + "/payload/"):]
        else:
            original = name
        source = build._local_file(payload_stage, name) if payload_stage else build._local_file(stage, original)
        if source.stat().st_size != row["bytes"] or build.sha256(source) != row["sha256"]:
            raise ValueError("Frozen file changed during QtIFW staging: " + name)
        target = data / name
        target.parent.mkdir(parents=True, exist_ok=True)
        if payload_stage:
            # Read-only build inputs only; native archives materialize regular
            # independent files. Never link an installed runtime/user directory.
            os.link(source, target)
        else:
            shutil.copy2(source, target)
        if build.sha256(target) != row["sha256"]:
            raise ValueError("QtIFW staged file differs: " + name)
    return compiled, inventory


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--build-record", type=Path, required=True)
    parser.add_argument("--stage", type=Path, required=True)
    parser.add_argument("--ifw-bin", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--payload-stage", type=Path, help="Reuse verified read-only staged build inputs on the same volume")
    parser.add_argument("--reuse-archive-from", type=Path, help="Reuse program archive from a hash-verified prior trial Setup; rebuild scripts/helper")
    args = parser.parse_args()
    if build._git("status", "--porcelain").strip():
        raise ValueError("Build from a clean committed checkout")
    output = args.output.absolute()
    if output.exists():
        raise ValueError("QtIFW trial output must be new")
    from resource_transaction import _safe
    _safe(output)
    record = json.loads(args.build_record.read_bytes())
    output.mkdir(parents=True)
    compiled, inventory = stage_payload(args.stage, record, output, payload_stage=args.payload_stage,
        archive_from=args.reuse_archive_from, ifw_bin=args.ifw_bin)
    instance = uuid.uuid4().hex
    (output / "targets").mkdir()
    (output / ".qtifw-trial-scope").write_text(instance + "\n", encoding="ascii")
    policy = dict(scope=str(output), instance=instance, payloadSource=compiled,
                  files=inventory["files"], faultsAllowed=True)
    policy_path = output / "qtifw-trial-policy.json"
    policy_path.write_text(json.dumps(policy, ensure_ascii=False), encoding="utf-8")
    # Small asInvoker helper, independent of the application's administrator EXE.
    subprocess.run([sys.executable, "-m", "PyInstaller", "--noconfirm", "--clean", "--onefile", "--console",
        "--name", "qtifw-trial-helper", "--paths", str(ROOT / "app"),
        "--exclude-module", "PySide6", "--exclude-module", "numpy", "--exclude-module", "cv2",
        "--distpath", str(output / "helper"), "--workpath", str(output / "helper-work"),
        "--specpath", str(output), "--add-data", str(policy_path) + ";.",
        str(ROOT / "tools/qtifw_trial_helper.py")], cwd=ROOT, check=True)
    helper = output / "helper/qtifw-trial-helper.exe"
    build.verify_invoker_manifest(helper)
    qrc = output / "trial.qrc"
    qrc.write_text('<RCC><qresource prefix="/qtifw-trial"><file alias="helper.exe">helper/qtifw-trial-helper.exe</file></qresource></RCC>', encoding="utf-8")
    config = output / "config"
    meta = output / "packages" / PACKAGE / "meta"
    meta.mkdir()
    config.mkdir()
    scripts = ROOT / "tools/qtifw_trial"
    controller = (scripts / "controller.js").read_text(encoding="utf-8")
    controller = controller.replace("__SCOPE__", json.dumps(output.as_posix())).replace("__INSTANCE__", json.dumps(instance))
    (config / "controller.js").write_text(controller, encoding="utf-8")
    shutil.copy2(scripts / "component.js", meta / "component.js")
    shutil.copy2(scripts / "data-page.ui", meta / "data-page.ui")
    write_xml(meta / "package.xml", "Package", dict(DisplayName="星图真实程序（隔离安装试用）",
        Description="原 142001C8 程序与辅助文件；只用于本机隔离验证。", Version="1.0.0", ReleaseDate="2026-11-08",
        Default="true", Script="component.js"))
    tree = ET.parse(meta / "package.xml")
    ET.SubElement(ET.SubElement(tree.getroot(), "UserInterfaces"), "UserInterface").text = "data-page.ui"
    tree.write(meta / "package.xml", encoding="utf-8", xml_declaration=True)
    write_xml(config / "config.xml", "Installer", dict(Name="Xingtu QtIFW Local Trial " + instance,
        Version="1.0.0", Title="星图安装器隔离试用（非正式版）", Publisher="Xingtu development trial",
        TargetDir=str(output / "targets/trial"), MaintenanceToolName="trial-maintenance",
        RemoveTargetDir="false", AllowNonAsciiCharacters="true", AllowSpaceInPath="true",
        AllowRepositoriesForOfflineInstaller="false", SaveDefaultRepositories="false", PersistentLocalCache="false",
        ControlScript="controller.js"))
    setup = output / "Xingtu-QtIFW-Local-Trial-Setup.exe"
    with (output / "binarycreator.log").open("wb") as log:
        subprocess.run([str(args.ifw_bin / "binarycreator.exe"), "--offline-only", "--compression", "3",
            "--resources", str(qrc), "-t", str(args.ifw_bin / "installerbase.exe"),
            "-c", str(config / "config.xml"), "-p", str(output / "packages"), str(setup)],
            stdout=log, stderr=subprocess.STDOUT, check=True)
    result = dict(installerSource=build._git("rev-parse", "HEAD"), payloadSource=compiled,
        programFiles=len(inventory["files"]), instance=instance, root=str(output), setup=str(setup),
        bytes=setup.stat().st_size, sha256=build.sha256(setup), installed=False, published=False,
        runtimeEntry="Use --self-check or isolated --probe-output only; production IPC identity is unchanged")
    (output / "qtifw-build-record.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
