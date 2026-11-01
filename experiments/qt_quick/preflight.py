"""Read-only launch checks; never creates a window, installs or changes settings."""
from pathlib import Path
import argparse
import importlib.metadata
import json
import sys

ROOT = Path(__file__).resolve().parents[2]

def check(replay=None):
    if sys.prefix == sys.base_prefix or not Path(sys.prefix).resolve().is_relative_to(ROOT):
        raise ValueError("请使用此实验克隆中的专用虚拟环境")
    for name, version in (("PySide6-Essentials", "6.11.1"), ("shiboken6", "6.11.1"),
                          ("numpy", "2.2.6"), ("Pillow", "12.3.0"), ("psutil", "7.2.2")):
        if importlib.metadata.version(name) != version:
            raise ValueError(f"依赖版本不匹配：{name}，需要 {version}")
    from PySide6.QtCore import qVersion
    sandbox = ROOT / "work/qt-lab-installation"
    if not (sandbox / ".qt-lab-owner").is_file() or not (sandbox / "_internal/version.json").is_file():
        raise ValueError("尚未准备完整的隔离资源副本，请先执行 seed_sandbox.py")
    if replay:
        import backend  # only pure adapters; does not instantiate Backend or QGuiApplication
        from replay_adapter import load_render_replay
        load_render_replay(Path(replay), ROOT / "work")
    return dict(ready=True, python=sys.executable, qt=qVersion(),
                entrypoint=str(Path(__file__).with_name("main.py")), replay=str(replay) if replay else None)

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--replay", type=Path)
    args = parser.parse_args()
    try:
        print(json.dumps(check(args.replay), ensure_ascii=True))
    except Exception as error:
        print(f"Qt lab cannot start: {error}")
        raise SystemExit(1)
