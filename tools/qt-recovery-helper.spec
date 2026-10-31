# A DIFFERENT mapped EXE; never dispatch recovery inside the Qt main executable.
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
    raise ValueError("Recovery helper requires this exact production build input")

a = Analysis([str(root / "app/star_map_recovery_helper.py")],
    pathex=[str(root / "app")], binaries=[], datas=[(inputs["profilePath"], ".")], hiddenimports=[],
    hookspath=[], hooksconfig={}, runtime_hooks=[],
    excludes=["tkinter", "_tkinter", "PySide6", "PyQt5", "PyQt6", "PySide2",
              "identityv_daily_assistant", "numpy", "cv2", "PIL"], noarchive=False)
pyz = PYZ(a.pure)
exe = EXE(pyz, a.scripts, [], exclude_binaries=True, name="star-map-recovery-helper",
    debug=False, bootloader_ignore_signals=False, strip=False, upx=False,
    uac_admin=False, uac_uiaccess=False, console=False,
    contents_directory="_star_map_recovery_runtime")
# Read RT_MANIFEST #1 before COLLECT; no execution or elevation here.
verify_invoker_manifest(exe.name)
coll = COLLECT(exe, a.binaries, a.datas, strip=False, upx=False,
               name="star-map-recovery-helper")
