"""Optional codec and unused Qt payload exclusions shared by Windows recipes.

Image capture and PNG/JPEG/WebP still require cv2/Pillow themselves. Never
exclude those packages or a shared C runtime to remove one optional codec.
QML imports Controls.Basic, Dialogs, Layouts and the folder-list model used by
file dialogs. Other Quick styles, their bindings, and non-zh_CN translations
are display payload, not the installer migration logic.
"""
from __future__ import annotations

import re
from pathlib import PurePosixPath

PYTHON_MODULE_EXCLUSIONS = ("PIL.AvifImagePlugin", "PIL._avif")
# Python modules the frozen client imports. Qt6Core is the ICU/link root even
# when a packaging probe stages that DLL alone.
RUNTIME_SEED_NAMES = frozenset("""
qtcore.pyd qtgui.pyd qtqml.pyd qtquick.pyd qtquickcontrols2.pyd qtnetwork.pyd qttest.pyd
qt6core.dll
""".split())


def excluded_binary(destination: str) -> bool:
    name = str(destination).replace("\\", "/").rsplit("/", 1)[-1].casefold()
    return bool(re.fullmatch(r"opencv_videoio_ffmpeg\d+(?:_64)?\.dll", name)
                or (name.startswith("_avif.") and name.endswith(".pyd")))


def filter_binaries(entries):
    """Return retained and excluded TOC entries, preserving their full tuples."""
    retained, removed = [], []
    for entry in entries:
        (removed if excluded_binary(entry[0]) else retained).append(entry)
    return retained, removed


_QML_PREFIXES = (
    "pyside6/qml/qtquick/effects/",
    "pyside6/qml/qtquick/localstorage/",
    "pyside6/qml/qtquick/nativestyle/",
    "pyside6/qml/qtquick/particles/",
    "pyside6/qml/qtquick/pdf/",
    "pyside6/qml/qtquick/scene2d/",
    "pyside6/qml/qtquick/scene3d/",
    "pyside6/qml/qtquick/shapes/designhelpers/",
    "pyside6/qml/qtquick/timeline/",
    "pyside6/qml/qtquick/tooling/",
    "pyside6/qml/qtquick/vectorimage/",
    "pyside6/qml/qtquick/virtualkeyboard/",
    "pyside6/qml/qtquick3d/",
    "pyside6/qml/qtquick3dphysics/",
    "pyside6/qml/qtcharts/",
    "pyside6/qml/qtgraphs/",
    "pyside6/qml/qtdatavisualization/",
    "pyside6/qml/qtnetwork/",
    "pyside6/qml/qtqml/statemachine/",
    "pyside6/qml/qtqml/xmllistmodel/",
)
_PLUGIN_DIRS = (
    "pyside6/plugins/designer/",
    "pyside6/plugins/qmllint/",
    "pyside6/plugins/qmltooling/",
    "pyside6/plugins/sqldrivers/",
)
_PLUGIN_FILES = frozenset("""
qdirect2d.dll qminimal.dll qoffscreen.dll
qicns.dll qpdf.dll qsvg.dll qtga.dll qtiff.dll qwbmp.dll qsvgicon.dll
qopensslbackend.dll qlottievectorimage.dll qtvirtualkeyboardplugin.dll
qmodernwindowsstyle.dll
""".split())
_CONTROLS = "pyside6/qml/qtquick/controls/"
_LABS = "pyside6/qml/qt/labs/"
_FOLDER_MODEL = "pyside6/qml/qt/labs/folderlistmodel/"
_DLL_FAMILY = re.compile(
    r"qt6(?:Lottie|QuickTimeline|VirtualKeyboard|QmlCompiler|Charts|Graphs|DataVisualization|"
    r"CanvasPainter|HttpServer|Mqtt|Coap|Grpc|Quick3D|Pdf|WebEngine).*\.dll",
    re.I)
_QT_BINARY = re.compile(r"Qt6.+\.dll|Qt.+\.pyd", re.I)


def _relative(path: str) -> str:
    return str(path).replace("\\", "/").casefold()


def unused_staged_relative(relative: str) -> str | None:
    """Reason a staged ``_internal`` file is unused, or None when it must stay.

    The path is relative to ``_internal``. Brand icons, map resources, helper
    runtimes outside ``_internal``, and the C# grid are not matched here.
    """
    rel = _relative(relative)
    name = rel.rsplit("/", 1)[-1]
    if rel.startswith(_QML_PREFIXES) or rel.startswith(_PLUGIN_DIRS):
        return "unused-qt-payload"
    if rel.startswith(_CONTROLS):
        top = rel[len(_CONTROLS):].split("/", 1)[0]
        if "/" in rel[len(_CONTROLS):] and top not in {"basic", "impl"}:
            return "unused-qt-style"
    if rel.startswith(_LABS) and not rel.startswith(_FOLDER_MODEL):
        return "unused-qt-labs"
    if "/plugins/" in rel and name in _PLUGIN_FILES:
        return "unused-qt-plugin"
    if rel.startswith("pyside6/translations/") and name.endswith(".qm") and not name.endswith("_zh_cn.qm"):
        return "unused-qt-translation"
    if rel.startswith("cv2/data/") and name.endswith(".xml"):
        return "unused-opencv-cascade"
    if name.endswith(".pdb") or (rel.startswith("pyside6/") and name.endswith(".pyi")):
        return "unused-debug-or-stub"
    if _DLL_FAMILY.fullmatch(name) or any(token in name for token in (
            "lottie", "virtualkeyboard", "quick3d", "quicktimeline", "qmlcompiler")):
        return "unused-qt-payload"
    if name in {"qpdf.dll", "opengl32sw.dll"}:
        return "unused-qt-payload"
    return None


def unreferenced_qt_binaries(relative_paths, imports_of) -> list[str]:
    """Qt bindings no remaining seeded binary imports.

    ``imports_of(relative)`` returns imported DLL names for one staged binary.
    Plugins and QML binaries stay seeds because Qt loads them by directory, not
    by a link import. Non-PySide6 binaries are seeds too, so numpy, OpenCV,
    Python and the grid are never selected.
    """
    files = [str(path).replace("\\", "/") for path in relative_paths]
    by_name: dict[str, list[str]] = {}
    for rel in files:
        by_name.setdefault(PurePosixPath(rel).name.casefold(), []).append(rel)
    seeds = []
    for rel in files:
        low = rel.casefold()
        name = PurePosixPath(rel).name.casefold()
        if (name in RUNTIME_SEED_NAMES or low.startswith("pyside6/plugins/")
                or (low.startswith("pyside6/qml/") and name.endswith(".dll"))
                or (not low.startswith("pyside6/") and name.endswith((".dll", ".pyd")))):
            seeds.append(rel)
    seen: set[str] = set()
    stack = list(seeds)
    while stack:
        rel = stack.pop()
        if rel in seen:
            continue
        seen.add(rel)
        if not PurePosixPath(rel).name.casefold().endswith((".dll", ".pyd")):
            continue
        for dep in imports_of(rel):
            for cand in by_name.get(str(dep).casefold(), []):
                if cand not in seen:
                    stack.append(cand)
    extra = []
    for rel in files:
        if rel in seen or not rel.casefold().startswith("pyside6/") or rel.count("/") != 1:
            continue
        if _QT_BINARY.fullmatch(PurePosixPath(rel).name):
            extra.append(rel)
    return extra
