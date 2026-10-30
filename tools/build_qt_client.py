"""Freeze the opt-in Qt TEST product, verify it, then produce a private Setup.

No install, executable launch, portable ZIP, network call or release operation.
--check-environment and --plan-only are read-only. A build needs a clean exact
source commit plus an explicit hashed inventory of an authorized resource root.
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.metadata
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import subprocess
import sys
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[1]
TOOLS = ROOT / "tools"
sys.path.insert(0, str(TOOLS))
from package_profiles import LIGHT_PROFILE, partition_resources
from packaged_resource_contract import check_package
from verify_admin_manifest import verify_administrator_manifest, verify_manifest_xml

PRODUCT = "XingtuQtClientTest"
QML_MODULES = ("QtQml", "QtQuick", "QtQuick/Controls", "QtQuick/Controls/Basic",
               "QtQuick/Dialogs", "QtQuick/Dialogs/quickimpl", "QtQuick/Layouts")
REQUIRED_RESOURCES = (
    "difficult-native-manifest.json", "references/nightmare-map-pack.json",
    "references/nightmare-author-route-catalog.json",
    "references/nightmare-canonical-coordinate-pack.json",
    "references/nightmare-shared-structure-index.json",
    "references/nightmare-structure-version-bindings.json",
    "references/difficult-shared-structure-index.json",
)
QT_LICENSE_ROOT = ROOT / "experiments/qt_quick/qt-licenses"
RUNTIME_DISTRIBUTIONS = ("PySide6-Essentials", "shiboken6", "numpy", "opencv-python-headless", "Pillow", "psutil")
# Runtime libraries from LGPL-capable QtBase/QtDeclarative/QtSvg. Unexpected
# modules stop the build for review rather than inheriting the app's license.
QT_LGPL_MODULES = set("""Concurrent Core DBus Gui Network OpenGL OpenGLWidgets PrintSupport
Qml QmlCore QmlLocalStorage QmlMeta QmlModels QmlNetwork QmlWorkerScript QmlXmlListModel
Quick QuickControls2 QuickControls2Basic QuickControls2BasicStyleImpl QuickControls2Impl
QuickControls2FluentWinUI3StyleImpl QuickControls2Fusion QuickControls2FusionStyleImpl
QuickControls2Imagine QuickControls2ImagineStyleImpl QuickControls2Material QuickControls2MaterialStyleImpl
QuickControls2Universal QuickControls2UniversalStyleImpl QuickControls2WindowsStyleImpl
QuickDialogs2 QuickDialogs2QuickImpl QuickDialogs2Utils QuickEffects QuickLayouts QuickParticles
QuickShapes QuickTemplates2 QuickTest QuickVectorImage QuickVectorImageGenerator QuickVectorImageHelpers
QuickWidgets Sql Svg SvgWidgets Test Widgets Xml LabsAnimation LabsFolderListModel LabsPlatform
LabsQmlModels LabsSettings LabsSharedImage LabsStyleKit LabsStyleKitImpl LabsSynchronizer LabsWavefrontMesh""".split())


def sha256(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def notice_inputs():
    """Validate vendored, exact upstream texts; the build itself never downloads."""
    index = _read(QT_LICENSE_ROOT / "UPSTREAM-SOURCES.json")
    if index.get("schema") != "xingtu-qt-license-sources-v1" or index.get("tag") != "v6.11.1":
        raise ValueError("Qt notice sources must match the pinned v6.11.1 runtime")
    for row in index["files"]:
        path = _local_file(QT_LICENSE_ROOT, row["path"])
        if path.stat().st_size != row["bytes"] or sha256(path) != row["sha256"]:
            raise ValueError(f"Qt license text changed from recorded upstream bytes: {row['path']}")
    for name in ("upstream/qtbase/LICENSES/LGPL-3.0-only.txt", "upstream/qtbase/LICENSES/GPL-3.0-only.txt",
                 "NOTICE.txt", "SOURCE-LINKS.txt"):
        if not (QT_LICENSE_ROOT / name).is_file():
            raise ValueError(f"Required Qt license/notice file missing: {name}")
    return dict(upstreamFiles=len(index["files"]), sourceIndexSha256=sha256(QT_LICENSE_ROOT / "UPSTREAM-SOURCES.json"),
                selectedQtLicense="LGPL-3.0-only", commercialLicenseAccepted=False)


def source_support_assets(resources):
    """Source-owned donation images remain available even in a minimal input."""
    rows = {}
    for name in ("assets/support/wechat.png", "assets/support/alipay.png"):
        path = ROOT / name
        if name not in resources and path.is_file():
            rows[name] = dict(bytes=path.stat().st_size, sha256=sha256(path))
    return rows


def prune_unneeded_qt(stage):
    """Remove unused GPL-only families and optional, unreviewed software GL.

    Main QML uses Quick/Basic/Dialogs/Layouts, not these optional imports. No
    installed library is changed: only this build's uninstalled staging files.
    """
    internal = stage / "_internal"
    qml_prefixes = ("pyside6/qml/qtquick/timeline/", "pyside6/qml/qtquick/virtualkeyboard/",
                    "pyside6/qml/qtquick/pdf/", "pyside6/qml/qtquick3d/", "pyside6/qml/qtquick3dphysics/",
                    "pyside6/qml/qtcharts/", "pyside6/qml/qtgraphs/", "pyside6/qml/qtdatavisualization/",
                    "pyside6/qml/qt/labs/lottieqt/")
    binary = re.compile(r"qt6(?:Lottie|QuickTimeline|VirtualKeyboard|QmlCompiler|Charts|Graphs|DataVisualization|"
                        r"CanvasPainter|HttpServer|Mqtt|Coap|Grpc|Quick3D|Pdf|WebEngine).*\.dll", re.I)
    removed = []
    for path in internal.rglob("*"):
        if not path.is_file():
            continue
        relative = path.relative_to(internal).as_posix()
        name = path.name.lower()
        if (relative.lower().startswith(qml_prefixes) or binary.fullmatch(path.name)
                or any(family in name for family in ("lottie", "virtualkeyboard", "quick3d", "quicktimeline", "qmlcompiler"))
                or name in {"qpdf.dll", "opengl32sw.dll"}):
            removed.append(dict(path=relative, sha256=sha256(path), bytes=path.stat().st_size))
            path.unlink()
    # Check both normal and delay imports without loading any binary. This
    # catches an accidental dependency on a removed optional library.
    import pefile
    binaries = list(internal.rglob("*.dll")) + list(internal.rglob("*.pyd"))
    names = {path.name.lower() for path in binaries}
    modules = set()
    for path in binaries:
        match = re.fullmatch(r"Qt6(.+)\.dll", path.name, re.I)
        if match:
            modules.add(match[1])
            if match[1] not in QT_LGPL_MODULES:
                raise ValueError(f"Qt module is outside the reviewed LGPL-capable runtime set: {path.name}")
        with pefile.PE(str(path), fast_load=True) as pe:
            pe.parse_data_directories(directories=[pefile.DIRECTORY_ENTRY["IMAGE_DIRECTORY_ENTRY_IMPORT"],
                                                   pefile.DIRECTORY_ENTRY["IMAGE_DIRECTORY_ENTRY_DELAY_IMPORT"]])
            entries = [*getattr(pe, "DIRECTORY_ENTRY_IMPORT", []), *getattr(pe, "DIRECTORY_ENTRY_DELAY_IMPORT", [])]
            for entry in entries:
                dependency = entry.dll.decode("ascii").lower()
                if dependency.startswith("qt6") and dependency.endswith(".dll") and dependency not in names:
                    raise ValueError(f"Missing retained Qt dependency: {path.name} -> {dependency}")
    return dict(removed=removed, retainedQtModules=sorted(modules),
                softwareOpenGLIncluded=False, renderingScope="Qt default Windows D3D backend; optional Mesa software OpenGL omitted")


def collect_notices(stage, qt_closure):
    """Copy exact upstream/wheel license files plus actual binary inventory."""
    target = stage / "_internal/qt-notices"
    target.mkdir(parents=True, exist_ok=True)
    shutil.copytree(QT_LICENSE_ROOT, target / "licenses", dirs_exist_ok=True)
    rows = []
    pattern = re.compile(r"^(?:licen[cs]e|copying|copyright|notice)(?:[._-].*)?$", re.I)
    for name in RUNTIME_DISTRIBUTIONS:
        distribution = importlib.metadata.distribution(name)
        for relative in distribution.files or ():
            relative = Path(relative)
            if not pattern.match(relative.name) or "commercial" in relative.name.lower():
                continue
            path = Path(distribution.locate_file(relative))
            if not path.is_file() or path.stat().st_size > 2 * 1024 * 1024:
                continue
            if ".." in relative.parts or relative.is_absolute():
                raise ValueError(f"License path outside distribution: {name}/{relative}")
            destination = target / "dependencies" / name / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(path, destination)
            rows.append(dict(distribution=name, version=distribution.version,
                             path=destination.relative_to(target).as_posix(), bytes=path.stat().st_size, sha256=sha256(path)))
    python_license = Path(sys.base_prefix) / "LICENSE.txt"
    if not python_license.is_file():
        raise ValueError("Python runtime LICENSE.txt is missing")
    shutil.copy2(python_license, target / "Python-LICENSE.txt")
    rows.append(dict(distribution="Python", version=sys.version.split()[0], path="Python-LICENSE.txt",
                     bytes=python_license.stat().st_size, sha256=sha256(python_license)))
    artifacts = []
    for path in sorted((stage / "_internal").rglob("*")):
        if path.is_file() and path.suffix.lower() in {".dll", ".pyd"}:
            artifacts.append(dict(path=path.relative_to(stage).as_posix(), bytes=path.stat().st_size, sha256=sha256(path)))
    report = dict(schema="xingtu-qt-distributed-notices-v1", licenseSelection="Qt/PySide LGPL v3 dynamic library route",
                  commercialLicenseAccepted=False, dependencyNotices=rows, qtClosure=qt_closure,
                  nativeArtifacts=artifacts, correspondingSourceObligation="Review exact wheel/source build correspondence and source-delivery terms before wider distribution; links are not a legal-clearance assertion")
    (target / "NOTICE-INVENTORY.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    return dict(path="_internal/qt-notices", dependencyNoticeFiles=len(rows), nativeArtifacts=len(artifacts),
                inventorySha256=sha256(target / "NOTICE-INVENTORY.json"), **notice_inputs())


def _read(path):
    return json.loads(Path(path).read_text(encoding="utf-8-sig"))


def _git(*args):
    result = subprocess.run(["git", "-C", str(ROOT), *args], check=True,
                            capture_output=True, text=True, encoding="utf-8")
    return result.stdout.strip()


def source_identity(commit):
    if not re.fullmatch(r"[0-9a-fA-F]{40}", commit):
        raise ValueError("--source-commit must be the full 40-character commit SHA")
    head = _git("rev-parse", "HEAD")
    if head.lower() != commit.lower():
        raise ValueError("Source checkout HEAD does not match --source-commit")
    if _git("status", "--porcelain", "--untracked-files=all"):
        raise ValueError("Source checkout must be clean, including untracked source files")
    if Path(_git("rev-parse", "--show-toplevel")).resolve() != ROOT:
        raise ValueError("Build recipe is not at the source checkout root")
    return dict(commit=head, tree=_git("rev-parse", "HEAD^{tree}"), clean=True,
                remoteVerification="not-performed-by-this-offline-build")


def _requirements(path, seen=None):
    seen = set() if seen is None else seen
    path = path.resolve()
    if path in seen:
        return {}
    seen.add(path)
    result = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.split("#", 1)[0].strip()
        if not line:
            continue
        if line.startswith("-r "):
            result.update(_requirements(path.parent / line[3:].strip(), seen))
        else:
            name, version = line.split("==", 1)
            if name in result and result[name] != version:
                raise ValueError(f"Conflicting build pins for {name}")
            result[name] = version
    return result


def environment(iscc):
    packages, errors = {}, []
    for name, expected in _requirements(TOOLS / "requirements-qt-build.txt").items():
        try:
            actual = importlib.metadata.version(name)
        except importlib.metadata.PackageNotFoundError:
            actual = None
        packages[name] = dict(expected=expected, actual=actual)
        if actual != expected:
            errors.append(f"{name}: expected {expected}, found {actual or 'missing'}")
    if sys.platform != "win32" or sys.maxsize <= 2**32 or sys.version_info[:2] != (3, 13):
        errors.append("Use the isolated Windows x64 Python 3.13 interpreter")
    compiler = Path(iscc).resolve() if iscc else None
    if not compiler or not compiler.is_file() or compiler.name.lower() != "iscc.exe":
        errors.append("Pass an existing Inno Setup 6 compiler explicitly with --iscc")
    qml_root = None
    try:
        from PySide6.QtCore import QLibraryInfo
        qml_root = Path(QLibraryInfo.path(QLibraryInfo.QmlImportsPath))
        for name in QML_MODULES:
            if not (qml_root / name / "qmldir").is_file():
                errors.append(f"Missing source QML module: {name}")
        plugins = Path(QLibraryInfo.path(QLibraryInfo.PluginsPath))
        if not (plugins / "platforms/qwindows.dll").is_file():
            errors.append("Missing Qt Windows platform plugin")
    except ImportError as error:
        errors.append(f"Qt dependency unavailable: {error}")
    return dict(success=not errors, python=sys.executable, packages=packages,
                compiler=str(compiler) if compiler else None,
                compilerSha256=sha256(compiler) if compiler and compiler.is_file() else None,
                qmlRoot=str(qml_root) if qml_root else None, errors=errors)


def _local_file(root, name):
    if not isinstance(name, str):
        raise ValueError("Resource inventory paths must be strings")
    posix = PurePosixPath(name)
    if ("\\" in name or ":" in name or posix.is_absolute()
            or ".." in posix.parts or name in ("", ".") or posix.as_posix() != name):
        raise ValueError(f"Invalid resource inventory path: {name!r}")
    path = root / name
    def linked(candidate):
        return candidate.is_symlink() or (hasattr(candidate, "is_junction") and candidate.is_junction())
    if linked(path) or any(linked(parent) for parent in path.parents if parent != root.parent):
        raise ValueError(f"Resource links are not allowed: {name}")
    path = path.resolve()
    if not path.is_relative_to(root) or not path.is_file():
        raise ValueError(f"Resource missing or outside explicit root: {name}")
    return path


def validate_resources(root, inventory):
    root, inventory = Path(root).resolve(), Path(inventory).resolve()
    if not root.is_dir() or root == ROOT or root == ROOT / "work":
        raise ValueError("Pass the exact isolated resource root, not the source/work directory")
    declaration = _read(inventory)
    if declaration.get("schema") != "xingtu-qt-resource-inventory-v1" or declaration.get("profileId") != LIGHT_PROFILE:
        raise ValueError("Resource inventory must declare xingtu-qt-resource-inventory-v1 / structure-editor-light")
    if not isinstance(declaration.get("resourceVersion"), str) or not declaration["resourceVersion"].strip():
        raise ValueError("Resource inventory must name the exact resourceVersion")
    rows = declaration.get("files")
    if not isinstance(rows, dict) or not rows:
        raise ValueError("Resource inventory requires a nonempty path -> {bytes, sha256} files object")
    for name, row in rows.items():
        path = _local_file(root, name)
        if name not in ("version.json", "difficult-native-manifest.json") and PurePosixPath(name).parts[0] not in {
                "assets", "references", "recognition"}:
            raise ValueError(f"Unexpected runtime-resource category: {name}")
        if path.suffix.lower() not in {".json", ".gz", ".npz", ".png", ".webp", ".jpg", ".jpeg", ".txt", ".md", ".ico", ".svg"}:
            raise ValueError(f"Executable, user state or unsupported resource type: {name}")
        if (not isinstance(row, dict) or type(row.get("bytes")) is not int
                or not re.fullmatch(r"[0-9a-fA-F]{64}", str(row.get("sha256", "")))
                or path.stat().st_size != row["bytes"] or sha256(path) != row["sha256"].lower()):
            raise ValueError(f"Resource size/hash does not match inventory: {name}")
    actual = {path.relative_to(root).as_posix() for path in root.rglob("*") if path.is_file()}
    if actual != set(rows):
        raise ValueError("Inventory must cover the exact resource root; undeclared/missing files exist")
    retained, omitted = partition_resources(rows)
    # The source version describes this client; the input's resourceVersion is
    # retained independently in the profile/record. Never ship an old EXE version.
    replaced = {"version.json": retained.pop("version.json")} if "version.json" in retained else {}
    missing = set(REQUIRED_RESOURCES) - set(retained)
    if missing:
        raise ValueError(f"Required two-mode base metadata missing: {sorted(missing)}")
    contract = check_package(root)
    if not contract["success"]:
        raise ValueError(f"Packaged runtime path contract rejected: {contract['errors']}")
    return declaration, retained, omitted, replaced, contract


def verify_frozen(stage, resources, qml_names):
    internal = stage / "_internal"
    executable = stage / (PRODUCT + ".exe")
    manifests = verify_administrator_manifest(executable)
    for name, row in resources.items():
        path = internal / name
        if not path.is_file() or path.stat().st_size != row["bytes"] or sha256(path) != row["sha256"].lower():
            raise ValueError(f"Frozen resource mismatch: {name}")
    for name in qml_names:
        if not (internal / name).is_file():
            raise ValueError(f"Client QML omitted: {name}")
    for name in QML_MODULES:
        directory = internal / "PySide6/qml" / name
        qmldir = directory / "qmldir"
        if not qmldir.is_file():
            raise ValueError(f"Frozen QML module missing: {name}")
        for line in qmldir.read_text(encoding="utf-8").splitlines():
            match = re.match(r"^(?:optional\s+)?plugin\s+(\w+)(?:\s+(.+))?$", line.strip())
            if match and not (directory / ((match[2] or ".").strip()) / (match[1] + ".dll")).is_file():
                raise ValueError(f"Frozen QML plugin missing: {name}/{match[1]}")
    if not (internal / "PySide6/plugins/platforms/qwindows.dll").is_file():
        raise ValueError("Frozen Windows platform plugin missing")
    if not (internal / "app/map_identity_engine.py").is_file():
        raise ValueError("Shared app module source closure missing")
    prohibited = []
    for path in stage.rglob("*"):
        name = path.name.lower()
        if name in {"_tkinter.pyd", "_tcl_data", "_tk_data", "tkinter"} or re.fullmatch(r"(?:tcl|tk)\d+.*\.dll", name):
            prohibited.append(path.relative_to(stage).as_posix())
    if prohibited:
        raise ValueError(f"Unexpected Tk runtime in Qt package: {prohibited}")
    return dict(administratorManifests=manifests, qmlModules=list(QML_MODULES),
                resourceFiles=len(resources), clientQmlFiles=len(qml_names), tkRuntimeAbsent=True)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check-environment", action="store_true")
    parser.add_argument("--plan-only", action="store_true", help="Validate and print inputs without writing/building")
    parser.add_argument("--source-commit")
    parser.add_argument("--resource-root", type=Path, help="Exact isolated assets/references root, not its parent")
    parser.add_argument("--resource-inventory", type=Path)
    parser.add_argument("--iscc", type=Path)
    parser.add_argument("--output-dir", type=Path, help="NEW directory within this checkout's work/ or dist/")
    args = parser.parse_args(argv)
    tools = environment(args.iscc)
    if args.check_environment:
        print(json.dumps(tools, indent=2))
        return 0 if tools["success"] else 1
    if not all((args.source_commit, args.resource_root, args.resource_inventory, args.iscc)):
        parser.error("--source-commit, --resource-root, --resource-inventory and --iscc are required")
    if not tools["success"]:
        raise ValueError("Build environment is not pinned: " + "; ".join(tools["errors"]))
    source = source_identity(args.source_commit)
    qt = ROOT / "experiments/qt_quick"
    if not (qt / "client.py").is_file() or not (qt / "Client.qml").is_file():
        raise ValueError("Integrated Qt entrypoint is not present")
    declaration, resources, omitted, replaced, contract = validate_resources(args.resource_root, args.resource_inventory)
    notices = notice_inputs()
    support = source_support_assets(resources)
    resources = dict(resources, **support)
    output = (args.output_dir or ROOT / "work/qt-setup" / source["commit"][:12]).resolve()
    if (not any(output.is_relative_to(ROOT / folder) for folder in ("work", "dist"))
            or output in (ROOT / "work", ROOT / "dist") or output.exists()):
        raise ValueError("Output must be a NEW subdirectory beneath this checkout's work/ or dist/")
    if args.resource_root.resolve().is_relative_to(output) or output.is_relative_to(args.resource_root.resolve()):
        raise ValueError("Resource input and build output must not contain one another")
    version = _read(ROOT / "version.json")
    display = str(version["displayVersion"]) + "-qt-test"
    numeric = str(version["numericVersion"])
    if not re.fullmatch(r"[0-9A-Za-z.+_-]+", display) or not re.fullmatch(r"\d+\.\d+\.\d+\.\d+", numeric):
        raise ValueError("Version metadata is not safe/valid for Setup")
    plan = dict(schema="xingtu-qt-setup-build-v1", product=PRODUCT, source=source,
                sourceRoot=str(ROOT), version=version, profileId=LIGHT_PROFILE,
                resourceRoot=str(args.resource_root.resolve()), resourceVersion=declaration["resourceVersion"],
                resourceInventorySha256=sha256(args.resource_inventory), resourceFiles=len(resources),
                omittedAuthorFiles=len(omitted), replacedResourceMetadata=list(replaced),
                resourcePathContract=contract, output=str(output), tools=tools,
                noticeInputs=notices, sourceSupportAssets=support,
                action="plan-only" if args.plan_only else "build-setup-only",
                buildCompleted=False, installPerformed=False, executableLaunched=False, published=False)
    if args.plan_only:
        print(json.dumps(plan, indent=2))
        return 0
    output.mkdir(parents=True)
    resource_stage = output / "resources"
    for name, row in resources.items():
        source_path = ROOT / name if name in support else _local_file(args.resource_root.resolve(), name)
        destination = resource_stage / name
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source_path, destination)
        if sha256(destination) != row["sha256"].lower():
            raise ValueError(f"Resource changed while staging: {name}")
    profile = dict(profileId=LIGHT_PROFILE, product=PRODUCT, sourceCommit=source["commit"],
                   resourceVersion=declaration["resourceVersion"], authorDisplayPackagesIncluded=[],
                   authorDisplayFilesIncluded=0, omittedAuthorDisplayFiles=len(omitted),
                   omittedAuthorDisplayBytes=sum(row["bytes"] for row in omitted.values()),
                   publicDownloadConfigured=False, localMapImportPreserved=True)
    profile_path = output / "installation-profile.json"
    profile_path.write_text(json.dumps(profile, indent=2), encoding="utf-8")
    # PyInstaller requires an unprefixed `assembly` DOM tag. ElementTree's
    # default ns0 prefix is valid XML but rejected by its manifest parser.
    ET.register_namespace("", "urn:schemas-microsoft-com:asm.v1")
    manifest = ET.parse(TOOLS / "identityv-assistant.manifest")
    manifest.getroot().find("{urn:schemas-microsoft-com:asm.v1}assemblyIdentity").set("name", "Xingtu.QtClientTest")
    manifest.getroot().find("{urn:schemas-microsoft-com:asm.v1}description").text = "Xingtu Qt Test Client"
    manifest_path = output / "qt-client.manifest"
    manifest.write(manifest_path, encoding="utf-8", xml_declaration=True)
    verify_manifest_xml(manifest_path.read_bytes())
    inputs = dict(sourceRoot=str(ROOT), product=PRODUCT, resourceStage=str(resource_stage),
                  resources=resources, profilePath=str(profile_path), manifestPath=str(manifest_path))
    input_path = output / "freeze-inputs.json"
    input_path.write_text(json.dumps(inputs, indent=2), encoding="utf-8")
    plan["resources"], plan["omittedAuthorResources"] = resources, omitted
    record_path = output / "build-record.json"
    record_path.write_text(json.dumps(plan, indent=2), encoding="utf-8")
    environment_vars = dict(os.environ, XINGTU_QT_BUILD_INPUTS=str(input_path))
    subprocess.run([sys.executable, "-m", "PyInstaller", "--noconfirm", "--clean",
                    "--distpath", str(output / "freeze"), "--workpath", str(output / "pyinstaller"),
                    str(TOOLS / "qt-client.spec")], cwd=ROOT, env=environment_vars, check=True)
    stage = output / "freeze" / PRODUCT
    plan["qtClosure"] = prune_unneeded_qt(stage)
    plan["notices"] = collect_notices(stage, plan["qtClosure"])
    plan["frozenVerification"] = verify_frozen(stage, resources, [p.name for p in qt.glob("*.qml")])
    # Root metadata is consumed by first-run sandbox seeding; source version is
    # authoritative. No user work/config, screenshots or import receipts copied.
    shutil.copy2(ROOT / "version.json", stage / "version.json")
    shutil.copy2(profile_path, stage / "installation-profile.json")
    if source_identity(args.source_commit) != source:
        raise ValueError("Source changed during freeze; do not create a Setup")
    setup_name = f"XingtuQtTest-Setup-{display}-{source['commit'][:12]}"
    subprocess.run([str(args.iscc.resolve()), f"/DStageDir={stage}", f"/DSetupOutputDir={output / 'setup'}",
                    f"/DSetupName={setup_name}", f"/DDisplayVersion={display}", f"/DNumericVersion={numeric}",
                    str(TOOLS / "qt-client.iss")], cwd=ROOT, check=True)
    setup = output / "setup" / (setup_name + ".exe")
    if not setup.is_file():
        raise ValueError("Inno Setup completed without the expected Setup artifact")
    plan["installer"] = dict(fileName=setup.name, bytes=setup.stat().st_size, sha256=sha256(setup))
    plan["recipes"] = {p.name: sha256(p) for p in (Path(__file__), TOOLS / "qt-client.spec", TOOLS / "qt-client.iss")}
    plan["source"] = source_identity(args.source_commit)
    plan["buildCompleted"] = True
    record_path.write_text(json.dumps(plan, indent=2), encoding="utf-8")
    print(json.dumps(dict(setup=str(setup), **plan["installer"], record=str(record_path)), indent=2))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (ValueError, OSError, subprocess.CalledProcessError) as error:
        print(f"Qt Setup build stopped: {error}", file=sys.stderr)
        raise SystemExit(1)
