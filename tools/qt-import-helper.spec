# A DIFFERENT mapped EXE; never dispatch import inside the Qt main executable.
from pathlib import Path
import json
import os
import sys

root = Path(SPECPATH).resolve().parent
sys.path.insert(0, str(root / "tools"))
from build_qt_client import verify_invoker_manifest

inputs = json.loads(Path(os.environ["XINGTU_QT_BUILD_INPUTS"]).read_text(encoding="utf-8"))
if (Path(inputs["sourceRoot"]).resolve() != root
        or inputs["product"] != "星图"):
    raise ValueError("Import helper requires this exact production build input")

a = Analysis([str(root / "app/star_map_import_helper.py")],
    pathex=[str(root / "app")], binaries=[], datas=[(inputs["profilePath"], ".")], hiddenimports=[],
    hookspath=[], hooksconfig={}, runtime_hooks=[],
    excludes=["tkinter", "_tkinter", "PySide6", "PyQt5", "PyQt6", "PySide2",
              "identityv_daily_assistant", "cv2"], noarchive=False)
# Author import validates decoded PNG/JPEG bytes, NPZ references and affine
# matrices. Keep those business dependencies, while GUI/game engines stay out.
required = {"PIL.Image", "PIL.PngImagePlugin", "PIL.JpegImagePlugin", "numpy", "numpy.linalg"}
if not required.issubset({entry[0] for entry in a.pure}):
    raise ValueError("Import helper is missing required image/array validators")
pyz = PYZ(a.pure)
exe = EXE(pyz, a.scripts, [], exclude_binaries=True, name="star-map-import-helper",
    debug=False, bootloader_ignore_signals=False, strip=False, upx=False,
    uac_admin=False, uac_uiaccess=False, console=False,
    contents_directory="_star_map_import_runtime")
# Read RT_MANIFEST #1 before COLLECT; no execution or elevation here.
verify_invoker_manifest(exe.name)
coll = COLLECT(exe, a.binaries, a.datas, strip=False, upx=False,
               name="star-map-import-helper")
