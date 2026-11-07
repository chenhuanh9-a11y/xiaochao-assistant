# Minimal independent onefile watcher. Never inherit the Qt admin manifest.
from pathlib import Path
import json
import os
import sys

root = Path(SPECPATH).resolve().parent
sys.path.insert(0, str(root / "tools"))
from build_qt_client import verify_invoker_manifest
inputs = json.loads(Path(os.environ["XINGTU_QT_BUILD_INPUTS"]).read_text(encoding="utf-8"))
if Path(inputs["sourceRoot"]).resolve() != root or inputs["product"] != "星图":
    raise ValueError("Game watcher requires this exact production build input")
a = Analysis([str(root / "app/game_startup_watcher.py")],
    pathex=[str(root / "app")], binaries=[], datas=[], hiddenimports=[],
    hookspath=[], hooksconfig={}, runtime_hooks=[],
    excludes=["tkinter", "_tkinter", "PySide6", "PyQt5", "PyQt6", "PySide2",
              "identityv_daily_assistant", "numpy", "cv2", "PIL", "psutil"], noarchive=False)
pyz = PYZ(a.pure)
exe = EXE(pyz, a.scripts, a.binaries, a.datas, [], name="XingtuGameWatcher",
    debug=False, bootloader_ignore_signals=False, strip=False, upx=False,
    uac_admin=False, uac_uiaccess=False, console=False)
verify_invoker_manifest(exe.name)
