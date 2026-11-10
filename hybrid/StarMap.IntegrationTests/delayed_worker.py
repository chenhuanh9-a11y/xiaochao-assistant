"""Test-only delay around unmodified production replies, no fake preview/loader."""
import json
from pathlib import Path
import subprocess
import sys
import time


def main():
    script = Path(__file__).resolve().parents[2] / "hybrid/python/worker.py"
    child = subprocess.Popen([sys.executable, "-B", "-u", str(script), *sys.argv[1:]],
                             stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                             stderr=sys.stderr, text=True, encoding="utf-8")
    try:
        for line in sys.stdin:
            child.stdin.write(line)
            child.stdin.flush()
            response = child.stdout.readline()
            if not response:
                return child.wait()
            if json.loads(line).get("operation") == "preview":
                time.sleep(0.25)
            sys.stdout.write(response)
            sys.stdout.flush()
    finally:
        child.stdin.close()
        child.stdout.close()
        child.wait(timeout=5)
    return child.returncode


if __name__ == "__main__":
    raise SystemExit(main())
