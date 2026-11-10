"""Test-only delay around unmodified production replies, no fake preview/loader."""
import json
import os
from pathlib import Path
import subprocess
import sys
import time


def trace(event):
    if os.environ.get("HYBRID_DELAYED_WORKER_TRACE") == "1":
        print(f"delayed-worker pid={os.getpid()} {event}", file=sys.stderr, flush=True)


def main():
    script = Path(__file__).resolve().parents[2] / "hybrid/python/worker.py"
    child = subprocess.Popen([sys.executable, "-B", "-u", str(script), *sys.argv[1:]],
                             stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                             stderr=sys.stderr, text=True, encoding="utf-8")
    trace(f"child-start pid={child.pid}")
    try:
        for line in sys.stdin:
            operation = json.loads(line).get("operation")
            trace(f"stdin operation={operation}")
            marker = os.environ.get("HYBRID_DELAYED_WORKER_MARKER")
            marker_operation = os.environ.get("HYBRID_DELAYED_WORKER_MARKER_OPERATION")
            if marker and operation == marker_operation:
                Path(marker).write_text(operation, encoding="utf-8")
            child.stdin.write(line)
            child.stdin.flush()
            response = child.stdout.readline()
            if not response:
                trace(f"child-eof operation={operation}")
                return child.wait()
            trace(f"child-response operation={operation} bytes={len(response)}")
            if operation == "preview":
                trace("preview-delay-start")
                time.sleep(0.25)
                trace("preview-delay-end")
            trace(f"stdout-write-start operation={operation} bytes={len(response)}")
            sys.stdout.write(response)
            trace(f"stdout-write-end operation={operation}")
            sys.stdout.flush()
            trace(f"stdout-flush-end operation={operation}")
            trace(f"stdout-forwarded operation={operation}")
        trace("stdin-eof")
    finally:
        trace("child-stdin-close")
        child.stdin.close()
        trace("child-stdout-close")
        child.stdout.close()
        trace("child-wait-start")
        child.wait(timeout=5)
        trace(f"child-exit code={child.returncode}")
    return child.returncode


if __name__ == "__main__":
    raise SystemExit(main())
