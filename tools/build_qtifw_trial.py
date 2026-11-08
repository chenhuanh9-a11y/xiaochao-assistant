"""Build an offline QtIFW Setup from an existing verified Xingtu freeze.

No installation, app launch, registry write or production source relabeling.
Selected empty targets are claimed explicitly; support files stay in this run.
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
import zipfile

import build_qt_client as build
from rebuild_qt_installer import validate_managed_runtime_reuse
from probe_qtifw_lifecycle import write_xml

ROOT = Path(__file__).resolve().parents[1]
PACKAGE = "org.xingtu.qtifw.realtrial"
DATA_PACKAGE = "org.xingtu.qtifw.trialdata"


def check_archive_inventory(prior_policy_path, compiled, inventory):
    prior_policy = json.loads(prior_policy_path.read_bytes())
    if prior_policy.get("payloadSource") != compiled or prior_policy.get("files") != inventory["files"]:
        raise ValueError("Prior trial archive inventory differs from this freeze")


def map_inputs(root, report_path):
    """Use the accepted delivery's existing byte inventory, without repacking it."""
    report = json.loads(Path(report_path).read_bytes())
    packages, sources, seen = [], [], set()
    for row in report["packages"]:
        relative = Path(row["path"].replace("\\", "/"))
        archive = build._local_file(root, relative.as_posix())
        if archive.stat().st_size != row["outputBytes"] or build.sha256(archive) != row["sha256"]:
            raise ValueError("Accepted map archive changed: " + str(relative))
        with zipfile.ZipFile(archive) as zipped:
            manifest = json.loads(zipped.read("map-package.json"))
        package_id, season = manifest["packageId"], manifest.get("season", "s0")
        if package_id in seen or season not in {"s0", "s1"}:
            raise ValueError("Duplicate map identity or invalid season")
        seen.add(package_id)
        destination = "_installer-support/maps/" + relative.as_posix()
        packages.append(dict(path=destination, bytes=row["outputBytes"], sha256=row["sha256"],
                             packageId=package_id, season=season, versionedAuthor=True))
        sources.append((archive, destination))
    if not packages:
        raise ValueError("Accepted map delivery is empty")
    return packages, sources


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
        check_archive_inventory(build._local_file(archive_from, "qtifw-trial-policy.json"), compiled, inventory)
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
    parser.add_argument("--release", action="store_true", help="Portable distribution; no build-machine scope dependency")
    parser.add_argument("--map-pack-root", type=Path)
    parser.add_argument("--map-pack-report", type=Path, help="Accepted batch-report.json; verify ZIPs without modifying them")
    parser.add_argument("--payload-stage", type=Path, help="Reuse verified read-only staged build inputs on the same volume")
    parser.add_argument("--reuse-archive-from", type=Path, help="Reuse program archive from a hash-verified prior trial Setup; rebuild scripts/helper")
    args = parser.parse_args()
    if bool(args.map_pack_root) != bool(args.map_pack_report) or (args.map_pack_root and not args.release):
        parser.error("Map root and delivery report must be supplied together with --release")
    if build._git("status", "--porcelain").strip():
        raise ValueError("Build from a clean committed checkout")
    installer_source = build.source_identity(build._git("rev-parse", "HEAD"))
    output = args.output.absolute()
    if output.exists():
        raise ValueError("QtIFW trial output must be new")
    from resource_transaction import _safe
    _safe(output)
    record = json.loads(args.build_record.read_bytes())
    packages, map_sources = map_inputs(args.map_pack_root, args.map_pack_report) if args.map_pack_root else ([], [])
    output.mkdir(parents=True)
    compiled, inventory = stage_payload(args.stage, record, output, payload_stage=args.payload_stage,
        archive_from=args.reuse_archive_from, ifw_bin=args.ifw_bin)
    if args.release and compiled != installer_source["commit"]:
        raise ValueError("Release Setup and frozen program must use the same final source commit")
    instance = uuid.uuid4().hex
    (output / "targets").mkdir()
    (output / ".qtifw-trial-scope").write_text(instance + "\n", encoding="ascii")
    policy = dict(scope=str(output), instance=instance, payloadSource=compiled, allowNewTargets=True, fullUninstall=True,
                  files=inventory["files"], faultsAllowed=not args.release, portable=args.release, mapPackages=packages)
    if args.release:
        policy.pop("scope")
    policy_path = output / "qtifw-trial-policy.json"
    policy_path.write_text(json.dumps(policy, ensure_ascii=False), encoding="utf-8")
    # Small asInvoker helper, independent of the application's administrator EXE.
    exclusions = [] if packages else ["--exclude-module", "numpy", "--exclude-module", "cv2"]
    subprocess.run([sys.executable, "-m", "PyInstaller", "--noconfirm", "--clean", "--onedir", "--console",
        "--name", "qtifw-trial-helper", "--paths", str(ROOT / "app"),
        "--exclude-module", "PySide6", *exclusions,
        "--distpath", str(output / "helper"), "--workpath", str(output / "helper-work"),
        "--specpath", str(output), "--add-data", str(policy_path) + ";.",
        str(ROOT / "tools/qtifw_trial_helper.py")], cwd=ROOT, check=True)
    helper_root = output / "helper/qtifw-trial-helper"
    helper = helper_root / "qtifw-trial-helper.exe"
    build.verify_invoker_manifest(helper)
    if args.release:
        shutil.copytree(helper_root, output / "packages" / DATA_PACKAGE / "data/_installer-support")
        for archive, destination in map_sources:
            copied = output / "packages" / DATA_PACKAGE / "data" / destination
            copied.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(archive, copied)
    qrc = output / "trial.qrc"
    support_files = sorted(path.relative_to(helper_root).as_posix() for path in helper_root.rglob("*") if path.is_file())
    resource = ET.Element("RCC")
    entries = ET.SubElement(resource, "qresource", prefix="/qtifw-trial")
    for name in support_files:
        ET.SubElement(entries, "file", alias=name).text = (helper_root / name).relative_to(output).as_posix()
    ET.ElementTree(resource).write(qrc, encoding="utf-8", xml_declaration=True)
    config = output / "config"
    meta = output / "packages" / PACKAGE / "meta"
    meta.mkdir()
    config.mkdir()
    scripts = ROOT / "tools/qtifw_trial"
    controller = (scripts / "controller.js").read_text(encoding="utf-8")
    controller = controller.replace("__SCOPE__", json.dumps("" if args.release else output.as_posix())).replace("__INSTANCE__", json.dumps(instance))
    controller = controller.replace("__SUPPORT_FILES__", json.dumps(support_files))
    (config / "controller.js").write_text(controller, encoding="utf-8")
    shutil.copy2(scripts / "trial.qss", config / "trial.qss")
    shutil.copy2(scripts / "component.js", meta / "component.js")
    shutil.copy2(scripts / "install-location.ui", meta / "install-location.ui")
    if args.release:
        for path in (config / "controller.js", meta / "component.js", meta / "install-location.ui"):
            text = path.read_text(encoding="utf-8").replace("星图试用版", "星图").replace("星图（试用版）", "星图")
            text = text.replace("测试安装操作未完成", "安装操作未完成").replace("本测试目录", "安装目录")
            text = text.replace("安装器试用向导", "安装向导").replace("本测试安装", "星图")
            text = text.replace("本试用版", "星图")
            path.write_text(text, encoding="utf-8")
    write_xml(meta / "package.xml", "Package", dict(DisplayName="星图",
        Description="星图桌面程序。", Version="1.0.0", ReleaseDate="2026-11-08",
        Default="true", Script="component.js", Dependencies=DATA_PACKAGE))
    package_tree = ET.parse(meta / "package.xml")
    interfaces = ET.SubElement(package_tree.getroot(), "UserInterfaces")
    ET.SubElement(interfaces, "UserInterface").text = "install-location.ui"
    ET.indent(package_tree)
    package_tree.write(meta / "package.xml", encoding="utf-8", xml_declaration=True)
    data_meta = output / "packages" / DATA_PACKAGE / "meta"
    data_meta.mkdir(parents=True)
    shutil.copy2(scripts / "data-component.js", data_meta / "data-component.js")
    write_xml(data_meta / "package.xml", "Package", dict(DisplayName="卸载清理",
        Description="彻底卸载时清理安装目录。", Version="1.0.0", Virtual="true",
        ReleaseDate="2026-11-08", Script="data-component.js"))
    write_xml(config / "config.xml", "Installer", dict(Name="星图安装器隔离试用 " + instance,
        Version="1.0.0", Title="星图（试用版）", Publisher="星图开发试用",
        TargetDir=str(output / "targets/trial"), MaintenanceToolName="trial-maintenance",
        RemoveTargetDir="true", AllowNonAsciiCharacters="true", AllowSpaceInPath="true",
        AllowRepositoriesForOfflineInstaller="false", SaveDefaultRepositories="false", PersistentLocalCache="false",
        ControlScript="controller.js", StyleSheet="trial.qss", WizardStyle="Modern",
        WizardShowPageList="false", WizardDefaultWidth="680", WizardDefaultHeight="440"))
    # Include both IFW pages and Qt standard buttons. Explicit translations
    # also apply to the generated maintenance tool, independent of OS locale.
    tree = ET.parse(config / "config.xml")
    if args.release:
        for key, value in dict(Name="星图", Title="星图安装", Publisher="星图", Version=record["version"]["version"],
                               TargetDir="@HomeDir@/AppData/Local/Programs/星图").items():
            tree.getroot().find(key).text = value
    translations = ET.SubElement(tree.getroot(), "Translations")
    for name in ("ifw_zh_CN", "qt_zh_CN"):
        ET.SubElement(translations, "Translation").text = name
    ET.indent(tree)
    tree.write(config / "config.xml", encoding="utf-8", xml_declaration=True)
    setup = output / ("星图-" + record["version"]["version"] + "-Setup.exe" if args.release else "Xingtu-QtIFW-Local-Trial-Setup.exe")
    with (output / "binarycreator.log").open("wb") as log:
        subprocess.run([str(args.ifw_bin / "binarycreator.exe"), "--offline-only", "--compression", "3",
            "--resources", str(qrc), "-t", str(args.ifw_bin / "installerbase.exe"),
            "-c", str(config / "config.xml"), "-p", str(output / "packages"), str(setup)],
            stdout=log, stderr=subprocess.STDOUT, check=True)
    if build.source_identity(installer_source["commit"]) != installer_source:
        raise ValueError("Source changed during QtIFW build; preserve the candidate for inspection")
    result = dict(installerSource=installer_source["commit"], payloadSource=compiled,
        programFiles=len(inventory["files"]), instance=instance, root=str(output), setup=str(setup),
        bytes=setup.stat().st_size, sha256=build.sha256(setup), installed=False, published=False,
        portable=args.release,
        mapPackages=packages,
        runtimeEntry="Use --self-check or isolated --probe-output only; production IPC identity is unchanged")
    (output / "qtifw-build-record.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
