"""One source/frozen path contract; the Qt test product owns a separate work tree."""
from pathlib import Path
import sys

FROZEN = bool(getattr(sys, "frozen", False))
RESOURCE_ROOT = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parents[2])).resolve()
WORK_ROOT = ((Path(sys.executable).resolve().parent / "work") if FROZEN else RESOURCE_ROOT / "work").resolve()
DEFAULT_SANDBOX = WORK_ROOT / ("qt-client-runtime" if FROZEN else "qt-lab-installation")
APP_ROOT = RESOURCE_ROOT / "app"


def owned_sandbox(path):
    path = Path(path).resolve()
    marker = path / ".qt-lab-owner"
    if (not path.is_relative_to(WORK_ROOT) or not marker.is_file() or marker.is_symlink()
            or marker.read_text(encoding="utf-8").strip() != "qt-quick-isolated-v1"):
        raise ValueError("Qt client data must use its own marked work directory")
    return path
