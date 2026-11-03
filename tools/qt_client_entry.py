"""Windowed entry: record startup failures without blocking on an error dialog."""
from pathlib import Path
import os
import sys
import traceback


def show_startup_failure(message):
    """Qt may not import at all; windowed startup still needs a visible receipt."""
    if os.name == "nt":
        import ctypes
        ctypes.windll.user32.MessageBoxW(None, message, "星图启动提示", 0x10)


def run():
    try:
        from client import main
        return main()
    except Exception:
        message = traceback.format_exc()
        record = None
        try:
            work = Path(sys.executable).resolve().parent / "work"
            work.mkdir(parents=True, exist_ok=True)
            record = work / "qt-client-startup-error.txt"
            record.write_text(message, encoding="utf-8")
        except OSError:
            record = None
        try:
            if sys.stderr is not None:
                sys.stderr.write(message)
        except Exception:
            pass
        try:
            show_startup_failure("星图启动失败。" + (
                f"\n\n详细信息已保存到：\n{record}" if record else "\n启动诊断记录未能保存。"))
        except Exception:
            pass
        return 1


if __name__ == "__main__":
    raise SystemExit(run())
