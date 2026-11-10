"""Test-only proxy that delays real runtime responses and injects stale terminals."""
import json
import os
import subprocess
import sys
import threading
import time


worker = os.environ["HYBRID_TEST_REAL_WORKER"]
child = subprocess.Popen([sys.executable, "-B", "-u", worker, *sys.argv[1:]],
                         stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=sys.stderr, bufsize=0)
ready = child.stdout.readline()
sys.stdout.buffer.write(ready)
sys.stdout.buffer.flush()
delay_ms = int(os.environ.get("HYBRID_TEST_DELAY_RESULT_MS", "0"))
inject_stale = os.environ.get("HYBRID_TEST_INJECT_STALE") == "1"
injected = False


def pump():
    global injected
    for raw in child.stdout:
        try:
            message = json.loads(raw)
        except (UnicodeDecodeError, json.JSONDecodeError):
            message = {}
        if inject_stale and not injected and message.get("event") == "result":
            injected = True
            for foreign_session, old_id in (("00000000-0000-0000-0000-000000000000", message.get("id")),
                                            (message.get("session"), message.get("id", 0) - 1)):
                stale = dict(event="result", session=foreign_session, id=old_id, ok=True, result={"stale": True})
                sys.stdout.buffer.write(json.dumps(stale, separators=(",", ":")).encode() + b"\n")
                sys.stdout.buffer.flush()
        if message.get("event") == "result" and delay_ms:
            time.sleep(delay_ms / 1000)
        sys.stdout.buffer.write(raw)
        sys.stdout.buffer.flush()


reader = threading.Thread(target=pump, name="runtime-output", daemon=True)
reader.start()
for raw in sys.stdin.buffer:
    child.stdin.write(raw)
    child.stdin.flush()
child.stdin.close()
child.wait()
reader.join(timeout=10)
raise SystemExit(child.returncode)
