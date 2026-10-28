# Windows development skeleton. Private maps remain an explicit external input.
from pathlib import Path
import sys

root = Path(SPECPATH).resolve().parent
sys.path.insert(0, str(root / "tools"))
from runtime_exclusions import PYTHON_MODULE_EXCLUSIONS, filter_binaries

datas = [(str(root / "version.json"), "."),
         (str(root / "app" / "custom_default_gallery.json"), ".")]
for name in ("THIRD_PARTY_LUCIDE_LICENSE.txt", "THIRD_PARTY_FLUENT_LICENSE.txt",
             "THIRD_PARTY_NOTICES.md"):
    datas.append((str(root / name), "."))
if (root / "assets" / "support").is_dir():
    datas.append((str(root / "assets" / "support"), "assets/support"))

a = Analysis(
    [str(root / "app" / "identityv_daily_assistant.py")],
    pathex=[str(root / "app")], binaries=[], datas=datas,
    hiddenimports=["PIL.PngImagePlugin", "PIL.JpegImagePlugin", "PIL.WebPImagePlugin"],
    hookspath=[], hooksconfig={}, runtime_hooks=[],
    excludes=list(PYTHON_MODULE_EXCLUSIONS), noarchive=False,
)
# FFmpeg is collected as a binary by the cv2 hook, so --exclude-module alone
# cannot remove it. Filter AFTER Analysis and BEFORE COLLECT.
a.binaries, removed_optional_codecs = filter_binaries(a.binaries)
print("Excluded optional codecs:", [row[0] for row in removed_optional_codecs])
pyz = PYZ(a.pure)
exe = EXE(pyz, a.scripts, [], exclude_binaries=True, name="xiaochao-assistant",
          debug=False, bootloader_ignore_signals=False, strip=False, upx=True,
          console=False, manifest=str(root / "tools" / "identityv-assistant.manifest"))
coll = COLLECT(exe, a.binaries, a.datas, strip=False, upx=True, name="xiaochao-assistant")
