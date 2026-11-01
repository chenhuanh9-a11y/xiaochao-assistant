"""Windowed entry: record startup failures without blocking on an error dialog."""
from pathlib import Path
import sys
import traceback


def run():
    try:
        from client import main
        return main()
    except Exception:
        message = traceback.format_exc()
        try:
            work = Path(sys.executable).resolve().parent / "work"
            work.mkdir(parents=True, exist_ok=True)
            (work / "qt-client-startup-error.txt").write_text(message, encoding="utf-8")
        except OSError:
            pass
        if sys.stderr is not None:
            sys.stderr.write(message)
        return 1


if __name__ == "__main__":
    raise SystemExit(run())
