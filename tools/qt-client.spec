# Allowlisted Qt clients. Invoked only by build_qt_client.py.
from pathlib import Path
import json
import os
import sys
from PyInstaller.utils.hooks import copy_metadata

root = Path(SPECPATH).resolve().parent
sys.path.insert(0, str(root / "tools"))
from runtime_exclusions import PYTHON_MODULE_EXCLUSIONS, filter_binaries
from verify_admin_manifest import verify_administrator_manifest

inputs = json.loads(Path(os.environ["XINGTU_QT_BUILD_INPUTS"]).read_text(encoding="utf-8"))
product = inputs["product"]
if (Path(inputs["sourceRoot"]).resolve() != root
        or product not in {"XingtuQtClientTest", "星图"}):
    raise ValueError("Build input identity does not match this source checkout")
qt_root = root / "experiments" / "qt_quick"
resource_stage = Path(inputs["resourceStage"])
datas = [(str(resource_stage / name), str(Path(name).parent)) for name in inputs["resources"]]
for path in qt_root.glob("*.qml"):
    datas.append((str(path), "."))
for path in (qt_root / "icons").glob("*.png"):
    datas.append((str(path), "icons"))
# Helpers use __file__.parent and APP_ROOT in both source and frozen clients.
for path in qt_root.glob("*.py"):
    if path.name != "client.py":
        datas.append((str(path), "."))
for name in ("version.json", "THIRD_PARTY_NOTICES.md", "THIRD_PARTY_LUCIDE_LICENSE.txt",
             "THIRD_PARTY_FLUENT_LICENSE.txt"):
    datas.append((str(root / name), "."))
datas.append((str(qt_root / "THIRD_PARTY.md"), "qt-notices"))
datas.append((str(qt_root / "help-topics.json"), "."))
for path in (qt_root / "help-images").glob("*.png"):
    datas.append((str(path), "help-images"))
datas.append((str(root / "app" / "custom_default_gallery.json"), "."))
datas.append((str(Path(inputs["profilePath"])), "."))
for distribution in ("PySide6-Essentials", "shiboken6", "numpy", "opencv-python-headless", "Pillow", "psutil"):
    datas += copy_metadata(distribution)

a = Analysis(
    [str(root / "tools" / "qt_client_entry.py")],
    pathex=[str(qt_root), str(root / "app")], binaries=[], datas=datas,
    hiddenimports=["client", "PySide6.QtCore", "PySide6.QtGui", "PySide6.QtQml",
                   "PySide6.QtQuick", "PySide6.QtQuickControls2", "numpy", "cv2",
                   "PIL.PngImagePlugin", "PIL.JpegImagePlugin", "PIL.WebPImagePlugin",
                   "client_providers", "map_identity_engine", "alignment_core_v2",
                   "fast_structure_alignment", "shared_structure_context"],
    hookspath=[], hooksconfig={}, runtime_hooks=[],
    excludes=[*PYTHON_MODULE_EXCLUSIONS, "tkinter", "_tkinter", "PIL.ImageTk",
              "turtle", "PyQt5", "PyQt6", "PySide2", "identityv_daily_assistant"],
    noarchive=False,
)
# QtQml's standard PyInstaller hook collects installed QML plugin dependencies,
# including Quick, Controls/Basic, Dialogs/quickimpl, Layouts and platform deps.
# Do not prune QML modules before the assembled-tree closure gate has passed.
a.binaries, removed_optional_codecs = filter_binaries(a.binaries)
print("Excluded optional codecs:", [row[0] for row in removed_optional_codecs])
for module, source, kind in list(a.pure):
    if not source:
        continue
    path = Path(source).resolve()
    if path.parent == root / "app" and path.suffix == ".py":
        a.datas += [("app/" + path.name, str(path), "DATA")]
if any(row[0] == "identityv_daily_assistant" for row in a.pure):
    raise ValueError("Tk production entrypoint must not enter the Qt client")
pyz = PYZ(a.pure)
exe = EXE(pyz, a.scripts, [], exclude_binaries=True, name=product,
          debug=False, bootloader_ignore_signals=False, strip=False, upx=False,
          uac_admin=True, console=False,
          manifest=inputs["manifestPath"], version=inputs["versionInfoPath"],
          icon=inputs["iconPath"])
# This reads RT_MANIFEST #1 from the actual PE; it never launches the EXE.
verify_administrator_manifest(exe.name)
coll = COLLECT(exe, a.binaries, a.datas, strip=False, upx=False, name=product)
