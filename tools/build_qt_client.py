"""Freeze an allowlisted Qt product, verify it, then produce a private Setup.

No install, executable launch, portable ZIP, network call or release operation.
--check-environment and --plan-only are read-only. A build needs a clean exact
source commit plus an explicit hashed inventory of an authorized resource root.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import importlib.metadata
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import subprocess
import sys
from xml.dom import minidom

ROOT = Path(__file__).resolve().parents[1]
TOOLS = ROOT / "tools"
sys.path.insert(0, str(TOOLS))
sys.path.insert(0, str(ROOT / "app"))
from managed_base_resources import optional_author_sidecar
from package_profiles import LIGHT_PROFILE, partition_resources
from packaged_resource_contract import check_package
from packaged_product_identity import PRIVATE_S1_INSTANCES
from verify_admin_manifest import verify_administrator_manifest, verify_manifest_xml

PRODUCT = "XingtuQtClientTest"
INSTANCES = {
    "client": dict(appId="AF19AFD9-3016-44C9-9D20-6F216C5D8C51", appName="Xingtu Qt Test Client",
                   installFolder="XingtuQtClientTest", startMenuGroup="Xingtu Qt Test Client",
                   setupPrefix="XingtuQtTest-Setup", versionSuffix="-qt-test", product=PRODUCT),
    "layout-preview": dict(appId="D4B8E562-EA62-4A43-AF1D-043A5646971E", appName="Xingtu Qt Layout Preview",
                           installFolder="XingtuQtLayoutPreview", startMenuGroup="Xingtu Qt Layout Preview",
                           setupPrefix="XingtuQtLayout-Setup", versionSuffix="-qt-layout-test", product=PRODUCT),
    "s0-s1-test": dict(appId="907F912D-58FD-4F84-A7A4-0C816AA70E52", appName="Xingtu S0 S1 Private Test",
                       installFolder="XingtuS0S1PrivateTest", startMenuGroup="Xingtu S0 S1 Private Test",
                       setupPrefix="XingtuS0S1Private-Setup", versionSuffix="-s0-s1-private", product=PRODUCT),
    "s1-floor-test": dict(appId="3C271F5D-71FF-4EA8-9569-B8DAC30F3773", appName="Xingtu S1 Floor Private Test",
                          installFolder="XingtuS1FloorPrivateTest", startMenuGroup="Xingtu S1 Floor Private Test",
                          setupPrefix="XingtuS1FloorPrivate-Setup", versionSuffix="-s1-floor-private", product=PRODUCT),
    "s1-auto-test": dict(appId="ED53B6E8-E9A0-4F21-AF75-7C8A62BE7347", appName="Xingtu S1 Auto Private Test",
                         installFolder="XingtuS1AutoPrivateTest", startMenuGroup="Xingtu S1 Auto Private Test",
                         setupPrefix="XingtuS1AutoPrivate-Setup", versionSuffix="-s1-auto-private", product=PRODUCT),
    "s1-playtest": dict(appId="D9FE6D76-C31A-47AF-9281-B4614C245E92", appName="Xingtu S1 Playtest",
                        installFolder="XingtuS1Playtest", startMenuGroup="Xingtu S1 Playtest",
                        setupPrefix="XingtuS1Playtest-Setup", versionSuffix="-s1-playtest", product=PRODUCT),
    "production": dict(appId="D04394A0-DACE-4FF9-874D-3DCB43EE53D1", appName="星图",
                       installFolder="XiaochaoAssistant", startMenuGroup="星图",
                       setupPrefix="星图", versionSuffix="", product="星图"),
}
def setup_basename(instance, display, source_commit):
    if instance["appId"] == INSTANCES["production"]["appId"]:
        return f"{instance['setupPrefix']}-{display}-Setup"
    return f"{instance['setupPrefix']}-{display}-{source_commit[:12]}"


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
BRAND_ICON = "assets/brand/xingtu.ico"
BRAND_ICON_SHA256 = "7aad9827252a62999ff214c4cb0cf08dd8f64473f584dd82ca2b4a2fee353b6b"
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


def qt_manifest(product=PRODUCT):
    # Preserve scoped default namespaces and unprefixed DOM tags. PyInstaller
    # locates security elements by tagName; ElementTree's nsN prefixes create
    # duplicate requestedExecutionLevel nodes during its manifest adjustment.
    with minidom.parse(str(TOOLS / "identityv-assistant.manifest")) as document:
        document.getElementsByTagName("assemblyIdentity")[0].setAttribute(
            "name", "Xingtu.Assistant" if product != PRODUCT else "Xingtu.QtClientTest")
        description = document.getElementsByTagName("description")[0]
        for child in list(description.childNodes):
            description.removeChild(child)
        description.appendChild(document.createTextNode("星图" if product != PRODUCT else "Xingtu Qt Test Client"))
        data = document.toxml(encoding="utf-8")
    verify_manifest_xml(data)
    from PyInstaller.utils.win32.winmanifest import create_application_manifest
    verify_manifest_xml(create_application_manifest(data, uac_admin=True, uac_uiaccess=False))
    return data


def version_info_text(product, numeric):
    """PyInstaller PE metadata; the display version remains separate in Setup."""
    if product not in {PRODUCT, INSTANCES["production"]["product"]}:
        raise ValueError("Unknown Qt product")
    version = tuple(int(part) for part in numeric.split("."))
    if len(version) != 4 or any(not 0 <= part <= 65535 for part in version):
        raise ValueError("Invalid PE numeric version")
    name = "星图" if product != PRODUCT else "Xingtu Qt Test Client"
    fields = {"FileDescription": name, "ProductName": name,
              "InternalName": product, "OriginalFilename": product + ".exe",
              "FileVersion": numeric, "ProductVersion": numeric}
    strings = ",\n".join(f"StringStruct({key!r}, {value!r})" for key, value in fields.items())
    return ("VSVersionInfo(ffi=FixedFileInfo(filevers=" + repr(version) + ", prodvers=" + repr(version)
            + ", mask=0x3f, flags=0, OS=0x40004, fileType=1, subtype=0, date=(0, 0)), "
            + "kids=[StringFileInfo([StringTable('080404B0', [" + strings
            + "])]), VarFileInfo([VarStruct('Translation', [2052, 1200])])])\n")


def verify_invoker_manifest(executable):
    """Read the helper PE without loading it; it must inherit, never elevate."""
    import pefile
    count = 0
    with pefile.PE(str(executable)) as pe:
        resources = getattr(pe, "DIRECTORY_ENTRY_RESOURCE", None)
        for resource in resources.entries if resources else ():
            if resource.id != 24:
                continue
            for entry in resource.directory.entries:
                if entry.id != 1:
                    continue
                for language in entry.directory.entries:
                    data = language.data.struct
                    with minidom.parseString(pe.get_data(data.OffsetToData, data.Size)) as document:
                        levels = document.getElementsByTagNameNS("urn:schemas-microsoft-com:asm.v3", "requestedExecutionLevel")
                        if (len(levels) != 1 or levels[0].getAttribute("level") != "asInvoker"
                                or levels[0].getAttribute("uiAccess") != "false"):
                            raise ValueError("Recovery helper must use asInvoker/uiAccess=false")
                    count += 1
    if not count:
        raise ValueError("Recovery helper has no process manifest (RT_MANIFEST #1)")
    return count


def assemble_recovery_helper(output, stage, environment_vars, *, kind="recovery"):
    """One separate helper freeze in the same build chain; no EXE launch."""
    if kind not in ("recovery", "import"):
        raise ValueError("Unknown resource helper")
    product = "star-map-" + kind + "-helper"
    subprocess.run([sys.executable, "-m", "PyInstaller", "--noconfirm", "--clean",
                    "--distpath", str(output / (kind + "-helper-freeze")),
                    "--workpath", str(output / (kind + "-helper-pyinstaller")),
                    str(TOOLS / ("qt-" + kind + "-helper.spec"))], cwd=ROOT, env=environment_vars, check=True)
    helper_stage = output / (kind + "-helper-freeze") / product
    executable = helper_stage / (product + ".exe")
    manifests = verify_invoker_manifest(executable)
    runtime = helper_stage / ("_star_map_" + kind + "_runtime")
    if not runtime.is_dir() or {p.name for p in helper_stage.iterdir()} != {executable.name, runtime.name}:
        raise ValueError("Recovery helper must have a separate exact runtime directory")
    inputs = _read(Path(environment_vars["XINGTU_QT_BUILD_INPUTS"]))
    if sha256(runtime / "installation-profile.json") != sha256(Path(inputs["profilePath"])):
        raise ValueError("Helper baked profile differs from its parent build")
    files = {}
    for path in helper_stage.rglob("*"):
        if path.is_symlink() or (hasattr(path, "is_junction") and path.is_junction()):
            raise ValueError("Recovery helper output must not contain links")
        if path.is_file():
            relative = path.relative_to(helper_stage).as_posix()
            files[relative] = dict(bytes=path.stat().st_size, sha256=sha256(path))
    for name in (executable.name, runtime.name):
        if (stage / name).exists():
            raise ValueError("Recovery helper would replace an existing staged path")
    shutil.copy2(executable, stage / executable.name)
    shutil.copytree(runtime, stage / runtime.name)
    for name, row in files.items():
        path = stage / name
        if path.stat().st_size != row["bytes"] or sha256(path) != row["sha256"]:
            raise ValueError(f"Recovery helper changed during staging: {name}")
    verify_invoker_manifest(stage / executable.name)
    return dict(included=True, product=product, executable=executable.name,
                runtimeDirectory=runtime.name, invokerManifests=manifests, files=files,
                launched=False, nativeParentWaitTested=False)


def assemble_game_watcher(output, stage, environment_vars):
    """Freeze the production-only opt-in watcher; never launch or register it."""
    inputs = _read(Path(environment_vars['XINGTU_QT_BUILD_INPUTS']))
    if Path(inputs['sourceRoot']).resolve() != ROOT or inputs.get('product') != '星图':
        raise ValueError('Game watcher requires this exact production build input')
    directory = Path(output) / 'game-watcher-freeze'
    subprocess.run([sys.executable, '-m', 'PyInstaller', '--noconfirm', '--clean',
                    '--distpath', str(directory), '--workpath', str(Path(output) / 'game-watcher-pyinstaller'),
                    str(TOOLS / 'qt-game-watcher.spec')], cwd=ROOT, env=environment_vars, check=True)
    executable = directory / 'XingtuGameWatcher.exe'
    for path in (directory, executable):
        if path.is_symlink() or (hasattr(path, 'is_junction') and path.is_junction()):
            raise ValueError('Game watcher output must not contain links')
    if not executable.is_file() or set(directory.iterdir()) != {executable}:
        raise ValueError('Game watcher must be one exact standalone executable')
    manifests = verify_invoker_manifest(executable)
    row = dict(bytes=executable.stat().st_size, sha256=sha256(executable))
    destination = Path(stage) / executable.name
    if destination.exists() or destination.is_symlink():
        raise ValueError('Game watcher would replace an existing staged path')
    shutil.copy2(executable, destination)
    if destination.stat().st_size != row['bytes'] or sha256(destination) != row['sha256']:
        raise ValueError('Game watcher changed during staging')
    verify_invoker_manifest(destination)
    return dict(included=True, executable=executable.name, files={executable.name: row},
                invokerManifests=manifests, launched=False, startupRegistrationChanged=False)


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
    windows_icu = use_windows_icu(internal)
    removed.extend(windows_icu["removed"])
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
                windowsIcu=windows_icu,
                softwareOpenGLIncluded=False, renderingScope="Qt default Windows D3D backend; optional Mesa software OpenGL omitted")


def use_windows_icu(internal):
    """Keep Qt's Windows ICU ABI; reject unrelated PATH-resolved ICU builds."""
    import pefile
    icu_files = [path for path in internal.rglob("*.dll") if re.fullmatch(r"icu.*\.dll", path.name, re.I)]
    required = set()
    for path in [*internal.rglob("*.dll"), *internal.rglob("*.pyd")]:
        if path in icu_files:
            continue
        with pefile.PE(str(path), fast_load=True) as pe:
            pe.parse_data_directories(directories=[pefile.DIRECTORY_ENTRY["IMAGE_DIRECTORY_ENTRY_IMPORT"],
                                                   pefile.DIRECTORY_ENTRY["IMAGE_DIRECTORY_ENTRY_DELAY_IMPORT"]])
            for entry in [*getattr(pe, "DIRECTORY_ENTRY_IMPORT", []), *getattr(pe, "DIRECTORY_ENTRY_DELAY_IMPORT", [])]:
                name = entry.dll.decode("ascii").lower()
                if not name.startswith("icu"):
                    continue
                if name != "icuuc.dll" or any(item.name is None for item in entry.imports):
                    raise ValueError(f"Unreviewed ICU dependency: {path.name} -> {name}")
                required.update(item.name for item in entry.imports)
    system = Path(os.environ["SystemRoot"]) / "System32/icuuc.dll"
    with pefile.PE(str(system)) as pe:
        exported = {item.name for item in pe.DIRECTORY_ENTRY_EXPORT.symbols}
    if not required or not required <= exported:
        raise ValueError("Windows ICU does not supply the exact Qt ICU imports")
    removed = []
    for path in icu_files:
        removed.append(dict(path=path.relative_to(internal).as_posix(), bytes=path.stat().st_size,
                            sha256=sha256(path), reason="Qt uses Windows native unversioned ICU ABI"))
        path.unlink()
    return dict(provider="Windows System32/icuuc.dll", requiredSymbols=sorted(name.decode("ascii") for name in required),
                systemLibraryBundled=False, removed=removed)


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


# This exact source-owned file is the only fingerprint exclusion. It contains
# declarative release data, never executable code; all builder code and source
# version bytes remain included. Exclusion avoids a self-referential hash.
S1_PRODUCTION_LOCK = "tools/s1-production-release-lock.json"
S0_RESOURCE_CONTEXTS = ("s0:nightmare", "s0:difficult")
S1_RESOURCE_CONTEXTS = ("s1:difficult", "s1:nightmare")
S1_HISTORICAL_MODES = ("difficulty2", "difficult", "nightmare")


def s1_inventory_modes(resources):
    """Projection of an explicit inventory; never directory discovery."""
    modes = {PurePosixPath(name).parts[2] for name in resources
             if name.startswith("seasons/s1/") and len(PurePosixPath(name).parts) > 3}
    if modes - set(S1_HISTORICAL_MODES):
        raise ValueError("Unknown S1 resource inventory mode")
    return tuple(mode for mode in S1_HISTORICAL_MODES if mode in modes)


def source_payload_fingerprint(source):
    """Read exact clean tracked bytes and modes, excluding only the release lock."""
    if (not isinstance(source, dict) or source.get("clean") is not True
            or source_identity(source.get("commit", "")) != source):
        raise ValueError("Production S1 requires the exact clean source identity")
    rows, names = [], set()
    for record in _git("ls-files", "--stage", "-z").split("\0"):
        if not record:
            continue
        metadata, name = record.split("\t", 1)
        mode, _, stage = metadata.split()
        if mode not in {"100644", "100755"} or stage != "0" or name in names:
            raise ValueError("Production S1 source must contain only unique tracked regular files")
        names.add(name)
        path = _local_file(ROOT, name)
        if name != S1_PRODUCTION_LOCK:
            rows.append(dict(path=name, mode=mode, bytes=path.stat().st_size, sha256=sha256(path)))
    if not {S1_PRODUCTION_LOCK, "version.json", "tools/build_qt_client.py"} <= names:
        raise ValueError("Production S1 source lock, builder and version must be tracked")
    if source_identity(source["commit"]) != source:
        raise ValueError("Source changed while checking the production S1 release")
    return hashlib.sha256(json.dumps(sorted(rows, key=lambda row: row["path"]),
        sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode("utf-8")).hexdigest()


def _s0_closure_digest(rows):
    return hashlib.sha256(json.dumps(rows, sort_keys=True, ensure_ascii=False,
                                    separators=(",", ":")).encode("utf-8")).hexdigest()


def production_s0_predecessor_closure(resources, release, *, bundled_packages=()):
    """Preserve reviewed published S0 bytes in the *actual* candidate plane.

    Historical provenance must be verified on Windows before source-lock review.
    A hash-shaped claim is not proof of publication. Never derive predecessors
    from the new composite input, installed mutable bytes or incoming metadata.
    """
    from managed_base_resources import SCHEMA, manifest_for_rows
    expected, predecessors = release.get("s0BaseFiles"), release.get("s0Predecessors")
    if not isinstance(expected, dict) or not expected or not isinstance(predecessors, list) or not predecessors:
        raise ValueError("Production S1 requires reviewed complete published S0 predecessor closure")
    selected, _ = partition_resources(resources, bundled_packages=bundled_packages)
    author_paths = selected_bundled_author_paths(selected, bundled_packages, "production")
    selected.update(source_support_assets(selected))
    current = {"_internal/" + name: row for name, row in selected.items()
               if name not in author_paths and not name.casefold().startswith("seasons/")
               and resource_file_family("_internal/" + name) == "base"}
    for name, row in expected.items():
        if (not isinstance(name, str) or not name.startswith("_internal/")
                or name.casefold().startswith("_internal/seasons/")
                or not isinstance(row, dict) or set(row) != {"bytes", "sha256"}
                or type(row["bytes"]) is not int or row["bytes"] < 0
                or type(row["sha256"]) is not str or not re.fullmatch(r"[a-f0-9]{64}", row["sha256"])):
            raise ValueError("Invalid exact current S0 candidate inventory")
    if current != expected:
        raise ValueError("Current S0 candidate plane differs from reviewed exact closure")
    evidence, seen = [], set()
    for predecessor in predecessors:
        if (not isinstance(predecessor, dict) or set(predecessor) != {
                "sourceCommit", "setupSha256", "manifestSha256", "manifestUtf8"}
                or type(predecessor["sourceCommit"]) is not str
                or not re.fullmatch(r"[a-f0-9]{40}", predecessor["sourceCommit"])
                or any(type(predecessor[field]) is not str or not re.fullmatch(r"[a-f0-9]{64}", predecessor[field])
                       for field in ("setupSha256", "manifestSha256"))
                or type(predecessor["manifestUtf8"]) is not str):
            raise ValueError("Invalid published S0 predecessor evidence")
        raw = predecessor["manifestUtf8"].encode("utf-8")
        if (not raw or len(raw) > 16 * 1024 * 1024
                or hashlib.sha256(raw).hexdigest() != predecessor["manifestSha256"]
                or predecessor["setupSha256"] in seen):
            raise ValueError("Invalid exact published S0 manifest bytes or duplicate predecessor")
        seen.add(predecessor["setupSha256"])
        manifest = json.loads(raw.decode("utf-8-sig"))
        if (not isinstance(manifest, dict) or set(manifest) != {"schema", "resourceVersion", "bundleId", "files"}
                or manifest["schema"] != SCHEMA or type(manifest["resourceVersion"]) is not str
                or not manifest["resourceVersion"].strip()):
            raise ValueError("Invalid published S0 managed manifest")
        canonical = manifest_for_rows(manifest["files"], manifest["resourceVersion"])
        if manifest["bundleId"] != canonical["bundleId"]:
            raise ValueError("Published S0 manifest bundle identity mismatch")
        rows = canonical["files"]
        if any(row["path"].casefold().startswith("_internal/seasons/") for row in rows):
            raise ValueError("S0 predecessor cannot be the S1 composite inventory")
        # Preserve the existing narrowly registered legacy optional-author
        # relinquishment. It must not turn into resurrection or base ownership.
        owned = [row for row in rows if not optional_author_sidecar(row["path"])]
        if not owned:
            raise ValueError("Published S0 predecessor must contain base ownership")
        for row in owned:
            if current.get(row["path"]) != {key: row[key] for key in ("bytes", "sha256")}:
                raise ValueError("Unreviewed S0 omission, relocation or byte change: " + row["path"])
        evidence.append(dict(sourceCommit=predecessor["sourceCommit"], setupSha256=predecessor["setupSha256"],
            manifestSha256=predecessor["manifestSha256"], resourceVersion=manifest["resourceVersion"],
            retainedBaseFiles=len(owned), excludedLegacyOptionalAuthorFiles=len(rows) - len(owned)))
    return dict(currentBaseFiles=len(current), candidateInventorySha256=_s0_closure_digest(current),
                publishedPredecessors=evidence,
                policy="exact-published-S0-bytes-preserved; no-new-retirement-authority")


def production_s1_release(resources, resource_version="", *, source=None, bundled_packages=()):
    """Current build eligibility, separate from historical managed-base recovery.

    No caller/profile/environment field can enable this path. Both source-owned
    policies, exact clean source/version and complete S1 byte inventory must
    agree. Historical managed inventories alone never authorize new builds.
    """
    if bundled_packages:
        raise ValueError("Production Setup no longer bundles optional author packages; use map-package downloads")
    if any(name.casefold().startswith("seasons/s1/difficulty2/") for name in resources):
        raise ValueError("Production Setup cannot include retired S1 difficulty2 resources")
    lock_path = _local_file(ROOT, S1_PRODUCTION_LOCK)
    lock = _read(lock_path)
    if (not isinstance(lock, dict) or set(lock) != {"schema", "enabled", "release"}
            or lock["schema"] != "xingtu-s1-production-release-lock-v1"
            or type(lock["enabled"]) is not bool):
        raise ValueError("Invalid source-owned production S1 release lock")
    selected = {name: row for name, row in resources.items() if name.casefold().startswith("seasons/")}
    release = lock["release"]
    if lock["enabled"] is not True:
        if release is not None:
            raise ValueError("Inactive production S1 release lock must be empty")
        if selected:
            raise ValueError("Production resource projection does not support season resources: production S1 disabled; only the exact S1 native roots in an allowlisted S1 private Setup are enabled")
        return None
    if (not isinstance(release, dict) or set(release) != {
            "resourceVersion", "sourcePayloadSha256", "versionSha256", "files", "s0BaseFiles", "s0Predecessors"}
            or type(release["resourceVersion"]) is not str or not release["resourceVersion"].strip()
            or any(type(release[field]) is not str or not re.fullmatch(r"[a-f0-9]{64}", release[field])
                   for field in ("sourcePayloadSha256", "versionSha256"))
            or not isinstance(release["files"], dict) or not release["files"]):
        raise ValueError("Invalid active production S1 release lock")
    if not selected and resource_version != release["resourceVersion"]:
        return None  # Existing S0 products do not inherit S1 build requirements.
    expected = release["files"]
    if not selected or resource_version != release["resourceVersion"] or selected != expected:
        raise ValueError("Production S1 inventory/version differs from the current source-owned release")
    if set(s1_inventory_modes(expected)) != {"difficult", "nightmare"}:
        raise ValueError("Production S1 release must contain exactly the supported runtime modes")
    rows = []
    for name, row in expected.items():
        if (not name.startswith("seasons/s1/") or not isinstance(row, dict)
                or set(row) != {"bytes", "sha256"} or type(row["bytes"]) is not int
                or row["bytes"] < 0 or type(row["sha256"]) is not str
                or not re.fullmatch(r"[a-f0-9]{64}", row["sha256"])):
            raise ValueError("Invalid exact production S1 release inventory")
        rows.append(dict(path="_internal/" + name, **row))
    # Independently checks normalized scope, reviewed mode manifests and the
    # complete inventory against source-reviewed historical/runtime locks.
    from managed_base_resources import manifest_for_rows
    manifest_for_rows(rows, resource_version)
    if (source_payload_fingerprint(source) != release["sourcePayloadSha256"]
            or sha256(_local_file(ROOT, "version.json")) != release["versionSha256"]):
        raise ValueError("Production S1 source/version differs from the current source-owned release")
    s0_closure = production_s0_predecessor_closure(resources, release, bundled_packages=bundled_packages)
    return dict(schema=lock["schema"], lockSha256=sha256(lock_path),
        sourcePayloadSha256=release["sourcePayloadSha256"], versionSha256=release["versionSha256"],
        resourceVersion=resource_version, resourceFiles=len(selected),
        sourceCommit=source["commit"], s0Closure=s0_closure)


def included_resource_contexts(instance, resources, resource_version="", *, source=None, bundled_packages=()):
    """Declarations follow verified release identity; they are not authority."""
    include_s1 = instance in PRIVATE_S1_INSTANCES
    if instance == "production":
        include_s1 = production_s1_release(resources, resource_version, source=source, bundled_packages=bundled_packages) is not None
    contexts = tuple("s1:" + mode for mode in s1_inventory_modes(resources)) if include_s1 else ()
    return list(S0_RESOURCE_CONTEXTS + contexts)


def validate_resources(root, inventory, *, bundled_packages=(), instance="client", source=None):
    if instance not in INSTANCES:
        raise ValueError("Unknown installer instance")
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
    production_release = (production_s1_release(rows, declaration["resourceVersion"], source=source, bundled_packages=bundled_packages)
                          if instance == "production" else None)
    for name, row in rows.items():
        path = _local_file(root, name)
        is_s1 = name.startswith("seasons/s1/") and len(PurePosixPath(name).parts) > 3 and PurePosixPath(name).parts[2] in {"difficulty2", "difficult", "nightmare"}
        if name.startswith("seasons/") and (not is_s1 or not (instance in PRIVATE_S1_INSTANCES or production_release)):
            raise ValueError("Season resources support only the exact S1 native roots in an allowlisted S1 private Setup: " + name)
        if not is_s1 and name not in ("version.json", "difficult-native-manifest.json") and PurePosixPath(name).parts[0] not in {
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
    retained, omitted = partition_resources(rows, bundled_packages=bundled_packages)
    # The source version describes this client; the input's resourceVersion is
    # retained independently in the profile/record. Never ship an old EXE version.
    replaced = {"version.json": retained.pop("version.json")} if "version.json" in retained else {}
    missing = set(REQUIRED_RESOURCES) - set(retained)
    if missing:
        raise ValueError(f"Required two-mode base metadata missing: {sorted(missing)}")
    contract = check_package(root)
    if not contract["success"]:
        raise ValueError(f"Packaged runtime path contract rejected: {contract['errors']}")
    if instance in PRIVATE_S1_INSTANCES or production_release:
        sys.path.insert(0, str(ROOT / "experiments/qt_quick"))
        from s1_private_resources import validated_s1_resources
        contract["s1"] = validated_s1_resources(root, required=True, modes=s1_inventory_modes(rows))
        if production_release:
            if contract["s1"]["files"] != {name: row for name, row in rows.items() if name.startswith("seasons/")}:
                raise ValueError("Native S1 validation differs from the production release inventory")
            contract["productionS1Release"] = production_release
    return declaration, retained, omitted, replaced, contract


def verify_frozen(stage, resources, qml_names, product=PRODUCT, *, bundled_author_paths=()):
    internal = stage / "_internal"
    executable = stage / (product + ".exe")
    manifests = verify_administrator_manifest(executable)
    for name, row in resources.items():
        path = internal / ("qt-author-base/" + name if name in bundled_author_paths else name)
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


# Product identity travels with program updates, even inside the resource tree.
_PROGRAM_IDENTITY = {"_internal/version.json", "_internal/installation-profile.json",
                     "_internal/references/build-fingerprint.txt"}
RESOURCE_INSTALLATION_POLICY = dict(existingBasePlane="manifest-owned-transaction-before-runtime",
    missingBasePlane="repair-complete-manifest", partialExistingPlane="repair-missing-verify-entire-set",
    editorBase="seed-whole-family-only-when-both-outer-and-internal-absent",
    resourceVersionMeaning="candidate-baseline; actual health is verified before resource consumers",
    unknownOwnership="block-with-path-and-expected-actual-hashes",
    installedAuthorPackages="preserve-receipt-files-and-dependency-contracts")


def resource_file_family(relative):
    """Strict families used by the generated production [Files] projection."""
    relative = relative.casefold()
    if relative in _PROGRAM_IDENTITY:
        return "program"
    if relative.startswith("_internal/assets/custom-maps/"):
        return "editor"
    if optional_author_sidecar(relative):
        return "optional-author"
    if (relative.startswith(("_internal/assets/", "_internal/references/", "_internal/recognition/", "_internal/seasons/"))
            or relative == "_internal/difficult-native-manifest.json"):
        return "base"
    return "program"


BUNDLED_AUTHOR_PREFIXES = ("assets/zhan10-native/", "assets/zhan10-legacy-clean/")
BUNDLED_AUTHOR_ROOT = "_internal/qt-author-base/"


def selected_bundled_author_paths(resources, packages, instance):
    """Explicit build input, not permission inferred from resources on disk."""
    if packages and instance == "production":
        raise ValueError("Production Setup no longer bundles optional author packages; use map-package downloads")
    if packages and instance not in {"client"} | PRIVATE_S1_INSTANCES:
        raise ValueError("Bundled author defaults require an isolated client or allowlisted S1 private Setup")
    if set(packages) - {"difficult-zhan10"}:
        raise ValueError("Unsupported bundled author package")
    names = sorted(name for name in resources if name.startswith(BUNDLED_AUTHOR_PREFIXES))
    if bool(packages) != bool(names):
        raise ValueError("Selected bundled author package must have explicit display assets")
    return names


def verify_bundled_author_defaults(stage, resources, bundled_author_paths):
    """Bind the relocated program files and manifest to the exact input lock."""
    stage = Path(stage).resolve()
    names = list(bundled_author_paths)
    if len(names) != len(set(names)):
        raise ValueError("Duplicate bundled author resource path")
    rows = {}
    for name in names:
        if (not isinstance(name, str) or not name.startswith(BUNDLED_AUTHOR_PREFIXES)
                or "\\" in name or ":" in name or ".." in PurePosixPath(name).parts
                or PurePosixPath(name).as_posix() != name or name not in resources):
            raise ValueError("Invalid bundled author resource mapping")
        row = resources[name]
        if (not isinstance(row, dict) or set(row) != {"bytes", "sha256"} or type(row["bytes"]) is not int or row["bytes"] < 0
                or not isinstance(row["sha256"], str) or not re.fullmatch(r"[0-9a-fA-F]{64}", row["sha256"])):
            raise ValueError("Bundled author resource requires exact hash and byte count")
        path = _local_file(stage, BUNDLED_AUTHOR_ROOT + name)
        if path.stat().st_size != row["bytes"] or sha256(path) != row["sha256"].lower():
            raise ValueError("Bundled author resource differs from verified inventory: " + name)
        rows[name] = row
    root = stage / BUNDLED_AUTHOR_ROOT
    actual = {path.relative_to(stage).as_posix() for path in root.rglob("*") if path.is_file()}
    expected = {BUNDLED_AUTHOR_ROOT + name for name in names}
    if names:
        expected.add(BUNDLED_AUTHOR_ROOT + "inventory.json")
    if actual != expected:
        raise ValueError("Bundled author projection differs from exact inventory")
    if not names:
        return None
    manifest = _local_file(stage, BUNDLED_AUTHOR_ROOT + "inventory.json")
    expected_manifest = dict(schema="xingtu-bundled-author-v1", packageId="difficult-zhan10", files=rows)
    if _read(manifest) != expected_manifest:
        raise ValueError("Bundled author manifest differs from verified inventory")
    return dict(manifest=BUNDLED_AUTHOR_ROOT + "inventory.json", manifestSha256=sha256(manifest),
                mappings={name: BUNDLED_AUTHOR_ROOT + name for name in sorted(names)},
                policy="program-owned-read-only-default; existing-user-object-wins; no-import-receipt")


def production_file_projection(stage, resources, *, bundled_author_paths=(), resource_version="", source=None):
    """Read a clean assembled stage; every resource belongs to exactly one plane."""
    production_release = production_s1_release(resources, resource_version, source=source,
        bundled_packages=("difficult-zhan10",) if bundled_author_paths else ())
    stage = Path(stage).resolve()
    families = dict(program=[], base=[], editor=[], **{"optional-author": []})
    seen = set()
    for path in sorted(stage.rglob("*")):
        if path.is_symlink() or (hasattr(path, "is_junction") and path.is_junction()):
            raise ValueError("Production stage must not contain links")
        if not path.is_file():
            continue
        name = path.relative_to(stage).as_posix()
        if name.casefold().startswith("_internal/seasons/"):
            if not production_release:
                raise ValueError("Production resource projection does not support season resources")
            relative = name[len("_internal/"):]
            row = resources.get(relative)
            if not row or path.stat().st_size != row["bytes"] or sha256(path) != row["sha256"]:
                raise ValueError("Staged S1 bytes differ from the exact production release inventory")
        if any(char in name for char in ('"', ';', '\r', '\n')) or name.lower().startswith("work/"):
            raise ValueError(f"Invalid staged program/resource path: {name}")
        if name.casefold() in seen:
            raise ValueError("Case-colliding staged files")
        seen.add(name.casefold())
        families[resource_file_family(name)].append(name)
    verify_bundled_author_defaults(stage, resources, bundled_author_paths)
    expected = {"_internal/" + name for name in resources
                if name not in bundled_author_paths and resource_file_family("_internal/" + name) != "program"}
    actual = set(families["base"]) | set(families["editor"]) | set(families["optional-author"])
    if actual != expected:
        raise ValueError(f"Resource projection differs from exact inventory: missing={sorted(expected-actual)}, extra={sorted(actual-expected)}")
    if production_release:
        # Input partitioning is not proof of the assembled candidate plane.
        # Bind every actual staged S0 base byte, including unexpected author
        # additions, partial relocation and source-owned support omissions.
        staged_s0 = {name: dict(bytes=(stage / name).stat().st_size, sha256=sha256(stage / name))
                     for name in families["base"] if not name.casefold().startswith("_internal/seasons/")}
        if _s0_closure_digest(staged_s0) != production_release["s0Closure"]["candidateInventorySha256"]:
            raise ValueError("Staged S0 candidate plane differs from reviewed exact closure")
        # Re-run the native provider on the actual staged bytes, not input-root
        # metadata or a serialized prior success report.
        sys.path.insert(0, str(ROOT / "experiments/qt_quick"))
        from s1_private_resources import validated_s1_resources
        native = validated_s1_resources(stage / "_internal", required=True, modes=s1_inventory_modes(resources))
        if native["files"] != {name: row for name, row in resources.items() if name.startswith("seasons/")}:
            raise ValueError("Staged native S1 contract differs from the production release inventory")
    return families


def stage_production_fingerprint(resource_stage, resources, source_commit):
    """Replace only program identity; never label Qt with a baseline Tk hash."""
    name = "references/build-fingerprint.txt"
    previous = resources.get(name, {}).get("sha256")
    value = source_commit[:8].upper()
    data = (value + "\n").encode("ascii")
    path = Path(resource_stage) / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    resources[name] = dict(bytes=len(data), sha256=hashlib.sha256(data).hexdigest())
    return dict(buildFingerprint=value, fingerprintScheme="source-commit-prefix",
                replacedBaselineFingerprintSha256=previous)


def stage_editor_baseline(stage, resources):
    """Program-owned fallback; never repairs/replaces a player's legacy family."""
    prefix = "assets/custom-maps/"
    copied = []
    for name in resources:
        if not name.startswith(prefix):
            continue
        source = Path(stage) / "_internal" / name
        row = resources[name]
        if source.stat().st_size != row["bytes"] or sha256(source) != row["sha256"].lower():
            raise ValueError(f"Editor baseline differs from source inventory: {name}")
        destination = Path(stage) / "_internal/qt-editor-base" / name[len(prefix):]
        if destination.exists():
            raise ValueError("Editor baseline destination must be fresh")
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, destination)
        if sha256(source) != sha256(destination):
            raise ValueError("Editor baseline changed while staging")
        copied.append(dict(source=name, destination=destination.relative_to(stage).as_posix(),
                           bytes=destination.stat().st_size, sha256=sha256(destination)))
    return dict(policy="program-owned-fallback; preserve-existing-legacy-families", files=copied)


def write_production_files_include(stage, resources, output, *, resource_version="", bundled_author_paths=(), source=None):
    families = production_file_projection(stage, resources, bundled_author_paths=bundled_author_paths,
                                          resource_version=resource_version, source=source)
    author_defaults = verify_bundled_author_defaults(stage, resources, bundled_author_paths)
    # Keep active data untouched during Setup. The startup repair verifies this
    # complete candidate manifest under the same OS lock as author transactions.
    sys.path.insert(0, str(ROOT / "app"))
    from managed_base_resources import CANDIDATE, manifest_for_rows
    rows = [dict(path=name, bytes=(Path(stage) / name).stat().st_size,
                 sha256=sha256(Path(stage) / name)) for name in families["base"]]
    for row in rows:
        declared = resources[row["path"][len("_internal/"):]]
        if ("bytes" in declared and "sha256" in declared
                and (row["bytes"] != declared["bytes"] or row["sha256"] != declared["sha256"].lower())):
            raise ValueError("Managed candidate differs from verified resource inventory: " + row["path"])
    # A family-only projection is useful to validate editor staging. Real full
    # builds already require the complete two-mode base in validate_resources.
    manifest = manifest_for_rows(rows, resource_version) if rows else None
    manifest_name = CANDIDATE + "/manifest.json" if manifest else None
    if manifest:
        manifest_path = Path(stage) / manifest_name
        manifest_path.parent.mkdir(parents=True, exist_ok=True)
        manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
        if manifest_name not in families["program"]:
            families["program"].append(manifest_name)
    lines = ["; Generated exact production projection; no directory wildcard copies."]
    installed_files = []
    for family, names in families.items():
        if family == "optional-author":
            # These belong to separately imported author displays. Shipping an
            # older copy as base or program data would overwrite the author or
            # resurrect it after the user removes its package.
            continue
        for name in names:
            source = name.replace("/", "\\")
            installed = CANDIDATE + "/payload/" + name if family == "base" else name
            installed_files.append(installed)
            parent = str(PurePosixPath(installed).parent)
            destination = "{app}" + ("\\" + parent.replace("/", "\\") if parent != "." else "")
            check = "; Check: InstallEditorResourceFamily" if family == "editor" else ""
            lines.append(f'Source: "{{#StageDir}}\\{source}"; DestDir: "{destination}"; Flags: ignoreversion{check}')
    include = Path(output) / "production-files.iss"
    include.write_text("\n".join(lines) + "\n", encoding="utf-8-sig")
    report = dict(schema="xingtu-qt-installer-projection-v1", families=families,
                  policy=RESOURCE_INSTALLATION_POLICY, installedFiles=installed_files,
                  bundledAuthorDefaults=author_defaults,
                  omittedOptionalAuthorFiles=families["optional-author"],
                  managedBaseManifest=manifest_name, managedBaseBundleId=manifest["bundleId"] if manifest else None,
                  managedBaseManifestSha256=sha256(Path(stage) / manifest_name) if manifest else None,
                  includeSha256=sha256(include))
    (Path(output) / "production-file-projection.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    return include, report


def stage_release_report(stage, *, product, source, version, resource_version, resource_inventory_sha256,
                         compiled_source_commit=None, installer_source_commit=None):
    """Generate program-owned identity from this completed freeze, never a resource baseline."""
    sys.path.insert(0, str(ROOT / 'app'))
    from package_identity import selected_file_identities
    stage = Path(stage)
    report = dict(schema='xingtu-qt-release-report-v1',
        sourceCommit=source['commit'], sourceDirty=not source['clean'],
        compiledPayloadSourceCommit=compiled_source_commit or source['commit'],
        installerSourceCommit=installer_source_commit or source['commit'],
        builtAt=datetime.now(timezone.utc).isoformat(),
        version=version['version'], displayVersion=version['displayVersion'],
        numericVersion=version['numericVersion'], buildFingerprint=source['commit'][:8].upper(),
        fingerprintScheme='source-commit-prefix', executableSha256=sha256(stage / (product + '.exe')),
        resourceVersion=resource_version, resourceInventorySha256=resource_inventory_sha256,
        resourceVersionMeaning='candidate-baseline; installed health verified separately before resource consumers',
        identityFiles={name: row for name, row in selected_file_identities(stage).items()
                       if row['status'] == 'hashed'})
    path = stage / 'release-report.json'
    path.write_text(json.dumps(report, indent=2), encoding='utf-8')
    return dict(file=path.name, sha256=sha256(path), executableSha256=report['executableSha256'])


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check-environment", action="store_true")
    parser.add_argument("--plan-only", action="store_true", help="Validate and print inputs without writing/building")
    parser.add_argument("--instance", choices=tuple(INSTANCES), default="client",
                        help="Allowlisted installer/runtime identity; production upgrades the original per-user 星图 installation")
    parser.add_argument("--source-commit")
    parser.add_argument("--resource-root", type=Path, help="Exact isolated assets/references root, not its parent")
    parser.add_argument("--resource-inventory", type=Path)
    parser.add_argument("--bundled-package", action="append", choices=("difficult-zhan10",), default=[],
                        help="Include this explicitly authorized read-only display default in an isolated client/private Setup only")
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
    declaration, resources, omitted, replaced, contract = validate_resources(args.resource_root, args.resource_inventory,
                                                                         bundled_packages=args.bundled_package,
                                                                         instance=args.instance, source=source)
    author_paths = selected_bundled_author_paths(resources, args.bundled_package, args.instance)
    if BRAND_ICON not in resources or resources[BRAND_ICON]["sha256"].lower() != BRAND_ICON_SHA256:
        raise ValueError("The existing supplied Xingtu ICO must be present in the locked resource inventory")
    notices = notice_inputs()
    support = source_support_assets(resources)
    resources = dict(resources, **support)
    instance = dict(id=args.instance, **INSTANCES[args.instance])
    product = instance["product"]
    instance.update(defaultDirName=("{localappdata}\\Programs\\XiaochaoAssistant" if args.instance == "production"
                                    else "{autopf}\\" + instance["installFolder"]),
                    usePreviousAppDir=True,
                    installerPrivilege="lowest" if args.instance == "production" else "admin",
                    runtimeDataProfile="production" if args.instance == "production" else "isolated")
    output_name = source["commit"][:12] + ("-" + args.instance if args.instance != "client" else "")
    output = (args.output_dir or ROOT / "work/qt-setup" / output_name).resolve()
    if (not any(output.is_relative_to(ROOT / folder) for folder in ("work", "dist"))
            or output in (ROOT / "work", ROOT / "dist") or output.exists()):
        raise ValueError("Output must be a NEW subdirectory beneath this checkout's work/ or dist/")
    if args.resource_root.resolve().is_relative_to(output) or output.is_relative_to(args.resource_root.resolve()):
        raise ValueError("Resource input and build output must not contain one another")
    version = _read(ROOT / "version.json")
    display = str(version["displayVersion"]) + instance["versionSuffix"]
    numeric = str(version["numericVersion"])
    if not re.fullmatch(r"[0-9A-Za-z.+_-]+", display) or not re.fullmatch(r"\d+\.\d+\.\d+\.\d+", numeric):
        raise ValueError("Version metadata is not safe/valid for Setup")
    plan = dict(schema="xingtu-qt-setup-build-v1", product=product, source=source,
                installerInstance=instance,
                resourceInstallationPolicy=RESOURCE_INSTALLATION_POLICY if args.instance == "production" else None,
                recoveryHelper=dict(included=args.instance == "production", product="star-map-recovery-helper"),
                importHelper=dict(included=args.instance == "production", product="star-map-import-helper"),
                gameWatcher=dict(included=args.instance == "production", executable="XingtuGameWatcher.exe"),
                sourceRoot=str(ROOT), version=version, profileId=LIGHT_PROFILE,
                resourceRoot=str(args.resource_root.resolve()), resourceVersion=declaration["resourceVersion"],
                resourceInventorySha256=sha256(args.resource_inventory), resourceFiles=len(resources),
                omittedAuthorFiles=len(omitted), replacedResourceMetadata=list(replaced),
                authorDisplayPackagesIncluded=args.bundled_package, authorDisplayFilesIncluded=len(author_paths),
                resourcePathContract=contract, output=str(output), tools=tools,
                noticeInputs=notices, sourceSupportAssets=support,
                productBrand=dict(icon=BRAND_ICON, iconSha256=BRAND_ICON_SHA256,
                                  artworkSource="existing supplied 3438365b/38490F4F brand resource; unchanged bytes",
                                  fileDescription=instance["appName"], productName=instance["appName"]),
                action="plan-only" if args.plan_only else "build-setup-only",
                buildCompleted=False, installPerformed=False, executableLaunched=False, published=False)
    if args.instance == "production":
        plan.update(buildFingerprint=source["commit"][:8].upper(), fingerprintScheme="source-commit-prefix",
                    replacedBaselineFingerprintSha256=resources.get("references/build-fingerprint.txt", {}).get("sha256"))
        if "references/build-fingerprint.txt" in resources:
            plan["replacedResourceMetadata"].append("references/build-fingerprint.txt")
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
    fingerprint = (stage_production_fingerprint(resource_stage, resources, source["commit"])
                   if args.instance == "production" else {})
    if fingerprint:
        plan.update(fingerprint)
        plan["resourceFiles"] = len(resources)
    profile = dict(profileId=LIGHT_PROFILE, product=product, sourceCommit=source["commit"],
                   installerInstance=instance,
                   resourceInstallationPolicy=RESOURCE_INSTALLATION_POLICY if args.instance == "production" else None,
                   recoveryHelper=dict(included=args.instance == "production", executable="star-map-recovery-helper.exe"),
                   importHelper=dict(included=args.instance == "production", executable="star-map-import-helper.exe"),
                   gameWatcher=dict(included=args.instance == "production", executable="XingtuGameWatcher.exe"),
                   resourceVersion=declaration["resourceVersion"], authorDisplayPackagesIncluded=args.bundled_package,
                   includedResourceContexts=included_resource_contexts(args.instance, resources,
                       declaration["resourceVersion"], source=source, bundled_packages=args.bundled_package),
                   authorDisplayFilesIncluded=len(author_paths), omittedAuthorDisplayFiles=len(omitted),
                   omittedAuthorDisplayBytes=sum(row["bytes"] for row in omitted.values()),
                   publicDownloadConfigured=False, localMapImportPreserved=True)
    profile.update(fingerprint)
    profile_path = output / "installation-profile.json"
    profile_path.write_text(json.dumps(profile, indent=2), encoding="utf-8")
    manifest_path = output / "qt-client.manifest"
    manifest_path.write_bytes(qt_manifest(product))
    version_path = output / "qt-client-version-info.txt"
    version_path.write_text(version_info_text(product, numeric), encoding="utf-8")
    inputs = dict(sourceRoot=str(ROOT), product=product, resourceStage=str(resource_stage),
                  resources=resources, profilePath=str(profile_path), manifestPath=str(manifest_path),
                   versionInfoPath=str(version_path), iconPath=str(resource_stage / BRAND_ICON),
                   bundledAuthorPaths=author_paths)
    input_path = output / "freeze-inputs.json"
    input_path.write_text(json.dumps(inputs, indent=2), encoding="utf-8")
    plan["resources"], plan["omittedAuthorResources"] = resources, omitted
    record_path = output / "build-record.json"
    record_path.write_text(json.dumps(plan, indent=2), encoding="utf-8")
    environment_vars = dict(os.environ, XINGTU_QT_BUILD_INPUTS=str(input_path))
    subprocess.run([sys.executable, "-m", "PyInstaller", "--noconfirm", "--clean",
                    "--distpath", str(output / "freeze"), "--workpath", str(output / "pyinstaller"),
                    str(TOOLS / "qt-client.spec")], cwd=ROOT, env=environment_vars, check=True)
    stage = output / "freeze" / product
    if args.instance == "production":
        plan["recoveryHelper"] = assemble_recovery_helper(output, stage, environment_vars)
        plan["importHelper"] = assemble_recovery_helper(output, stage, environment_vars, kind="import")
        plan['gameWatcher'] = assemble_game_watcher(output, stage, environment_vars)
    plan["qtClosure"] = prune_unneeded_qt(stage)
    plan["notices"] = collect_notices(stage, plan["qtClosure"])
    plan["frozenVerification"] = verify_frozen(stage, resources, [p.name for p in qt.glob("*.qml")], product,
                                             bundled_author_paths=author_paths)
    if author_paths:
        defaults = dict(schema="xingtu-bundled-author-v1", packageId="difficult-zhan10",
                        files={name:resources[name] for name in author_paths})
        (stage / "_internal/qt-author-base/inventory.json").write_text(json.dumps(defaults, indent=2), encoding="utf-8")
        plan["bundledAuthorDefaults"] = defaults
    plan["editorBaseline"] = stage_editor_baseline(stage, resources)
    # Root metadata is consumed by first-run sandbox seeding; source version is
    # authoritative. No user work/config, screenshots or import receipts copied.
    shutil.copy2(ROOT / "version.json", stage / "version.json")
    shutil.copy2(profile_path, stage / "installation-profile.json")
    if source_identity(args.source_commit) != source:
        raise ValueError("Source changed during freeze; do not create a Setup")
    plan['releaseReport'] = stage_release_report(stage, product=product, source=source,
        version=version, resource_version=declaration['resourceVersion'],
        resource_inventory_sha256=plan['resourceInventorySha256'])
    production_defines = []
    if args.instance == "production":
        # Relative program ownership drives the existing exact-file backup and
        # failed-upgrade rollback. No work/settings/imports are frozen or owned.
        ownership = stage / "_installer-owned-files.txt"
        ownership.write_text("", encoding="utf-8-sig")
        include, projection = write_production_files_include(stage, resources, output,
            resource_version=declaration["resourceVersion"], bundled_author_paths=author_paths, source=source)
        owned_files = sorted(name.replace("/", "\\") for name in projection["installedFiles"])
        if any(name.lower() == "work" or name.lower().startswith("work\\") for name in owned_files):
            raise ValueError("Production ownership must not contain user work/data")
        ownership.write_text("\n".join(owned_files) + "\n", encoding="utf-8-sig")
        plan["programOwnership"] = dict(files=len(owned_files), sha256=sha256(ownership),
                                        backup="{app}/work/installer-backups", userDataOwned=False)
        plan["fileProjection"] = projection
        production_defines = ["/DProductionInstaller", f"/DProductionFilesInclude={include}"]
    setup_name = setup_basename(instance, display, source["commit"])
    subprocess.run([str(args.iscc.resolve()), f"/DStageDir={stage}", f"/DSetupOutputDir={output / 'setup'}",
                    f"/DSetupName={setup_name}", f"/DDisplayVersion={display}", f"/DNumericVersion={numeric}",
                    "/DInstallerAppId={{" + instance["appId"] + "}",
                    f"/DInstallerAppName={instance['appName']}",
                    f"/DInstallerFolder={instance['installFolder']}",
                    f"/DInstallerGroup={instance['startMenuGroup']}",
                    f"/DInstallerExeName={product}.exe",
                    *production_defines,
                    str(TOOLS / "qt-client.iss")], cwd=ROOT, check=True)
    setup = output / "setup" / (setup_name + ".exe")
    if not setup.is_file():
        raise ValueError("Inno Setup completed without the expected Setup artifact")
    plan["installer"] = dict(fileName=setup.name, bytes=setup.stat().st_size, sha256=sha256(setup))
    recipes = [Path(__file__), TOOLS / "qt-client.spec", TOOLS / "qt-recovery-helper.spec",
               TOOLS / "qt-import-helper.spec", TOOLS / "qt-game-watcher.spec", TOOLS / "qt-client.iss"]
    if args.instance == 'production':
        recipes.extend((TOOLS / "qt-game-watcher-lifecycle.iss", TOOLS / "qt-uninstall-data.iss"))
    plan["recipes"] = {p.name: sha256(p) for p in recipes}
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
