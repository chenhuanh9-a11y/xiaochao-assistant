"""Real Qt-blocked runtime process; missing resources are explicit capability gaps."""
import json
import os
from pathlib import Path
import queue
import subprocess
import sys
import tempfile
import threading
import unittest

from PIL import Image
from tests.test_hybrid_browse_worker import BLOCK_QT_AND_RUN

ROOT = Path(__file__).resolve().parents[1]
WORKER = ROOT / "hybrid/python/runtime_worker.py"


class RuntimeProcess:
    def __init__(self, root):
        for name in ("resources", "data", "frames"):
            (root / name).mkdir()
        self.root = root
        self.process = subprocess.Popen([sys.executable, "-B", "-u", "-c", BLOCK_QT_AND_RUN,
            str(WORKER), "--resource-root", str(root / "resources"), "--data-root", str(root / "data"),
            "--frame-root", str(root / "frames")], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=subprocess.PIPE, text=True, encoding="utf-8", errors="replace",
            env=dict(os.environ, PYTHONIOENCODING="utf-8"))
        self.messages = queue.Queue()
        self.errors = []

        def read():
            for line in self.process.stdout:
                self.messages.put(json.loads(line))
            self.messages.put(dict(event="transport-closed"))

        def errors():
            for chunk in self.process.stderr:
                self.errors.append(chunk)

        self.reader = threading.Thread(target=read, daemon=True)
        self.error_reader = threading.Thread(target=errors, daemon=True)
        self.reader.start(); self.error_reader.start()
        ready = self.messages.get(timeout=15)
        if ready.get("ready") is not True:
            self.process.wait(timeout=5)
            self.error_reader.join(timeout=2)
            self.cleanup()
            raise AssertionError((ready, "".join(self.errors)))
        self.ready = ready

    def send(self, value):
        self.process.stdin.write(json.dumps(value) + "\n")
        self.process.stdin.flush()

    def terminal(self, request_id):
        while True:
            message = self.messages.get(timeout=15)
            if message.get("event") == "result" and message.get("id") == request_id:
                return message

    def close(self):
        if self.process.stdin and not self.process.stdin.closed:
            self.process.stdin.close()
        self.process.wait(timeout=15)
        self.reader.join(timeout=2); self.error_reader.join(timeout=2)
        self.process.stdout.close(); self.process.stderr.close()
        return self.process.returncode

    def cleanup(self):
        if self.process.poll() is None:
            self.process.terminate()
            self.process.wait(timeout=5)
        for stream in (self.process.stdin, self.process.stdout, self.process.stderr):
            if not stream.closed:
                stream.close()


class HybridRuntimeWorkerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.worker = RuntimeProcess(self.root)
        self.addCleanup(self.worker.cleanup)

    def request(self, request_id, operation, **values):
        self.worker.send(dict(id=request_id, session="worker-test", operation=operation, **values))
        return self.worker.terminal(request_id)

    def test_actual_runtime_snapshot_missing_resources_frame_and_shutdown(self):
        self.assertEqual(self.worker.ready["evidenceKind"], "recorded")
        initial = self.request(1, "snapshot")
        self.assertTrue(initial["ok"], initial)
        self.assertFalse(initial["result"]["snapshot"]["liveAuthorization"])
        self.assertIsNone(initial["result"]["render"])
        self.assertEqual(initial["gridCalls"], [])
        for index, mode in enumerate(("nightmare", "difficult"), 2):
            report = self.request(index, "prepare", season="s0", mode=mode)
            self.assertTrue(report["ok"], report)
            self.assertFalse(report["result"]["capability"]["available"])
        frame = self.root / "frames/black.png"
        Image.new("RGB", (128, 96)).save(frame)
        selected = self.request(4, "set_frame", path=str(frame))
        self.assertTrue(selected["result"]["frame"]["success"], selected)
        started = self.request(5, "dispatch", action="start_round")
        self.assertFalse(started["result"]["receipt"]["success"], started)
        self.assertFalse(started["result"]["snapshot"]["liveAuthorization"])
        self.assertIsNone(started["result"]["render"])
        self.assertEqual(self.worker.close(), 0)

    def test_unknown_operation_fails_current_request_and_later_snapshot_recovers(self):
        failed = self.request(10, "not-an-operation")
        self.assertFalse(failed["ok"], failed)
        self.assertIsNone(failed["render"])
        self.assertFalse(failed["snapshot"]["displayAuthorized"])
        self.assertTrue(self.request(11, "snapshot")["ok"])
        self.assertEqual(self.worker.close(), 0)

    def test_foreign_cancel_has_no_authority_and_bad_json_recovers(self):
        self.worker.send(dict(id=99, session="foreign", operation="cancel"))
        ack = self.worker.messages.get(timeout=5)
        self.assertEqual(ack["event"], "cancel-ack")
        self.assertFalse(ack["matched"])
        self.worker.process.stdin.write("bad json\n")
        self.worker.process.stdin.flush()
        error = self.worker.messages.get(timeout=5)
        self.assertEqual(error["event"], "protocol-error")
        self.assertTrue(self.request(12, "snapshot")["ok"])
        self.assertEqual(self.worker.close(), 0)

    def test_grid_initialization_failure_exits_without_output_thread_leak(self):
        folder = self.root / "startup-failure"
        for name in ("resources", "frames", "data"):
            (folder / name).mkdir(parents=True, exist_ok=True)
        (folder / "data/grid-cache").write_text("ordinary file", encoding="utf-8")
        child = subprocess.run([sys.executable, "-B", str(WORKER),
            "--resource-root", str(folder / "resources"), "--frame-root", str(folder / "frames"),
            "--data-root", str(folder / "data")], input="", capture_output=True, text=True,
            encoding="utf-8", errors="replace", timeout=5)
        self.assertNotEqual(child.returncode, 0)
        self.assertIn("FileExistsError", child.stderr)
        self.assertNotIn('"ready":true', child.stdout)

    def test_output_pump_eof_cancels_its_actual_blocked_pipe_write(self):
        code = ("import sys; sys.path.insert(0,sys.argv[1]); "
                "from output_pipe import OutputPipe; "
                "pump=OutputPipe(sys.stdout.fileno()); "
                "pump.send(b'ready\\n'); pump.send(b'x'*(16*1024*1024)); "
                "sys.stdin.buffer.read(); pump.close()")
        child = subprocess.Popen([sys.executable, "-B", "-c", code, str(ROOT / "hybrid/python")],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        try:
            self.assertEqual(child.stdout.readline(), b"ready\n")
            # Stop reading the large actual os.write, then deliver client EOF.
            child.stdin.close()
            child.wait(timeout=5)
            self.assertEqual(child.returncode, 0, child.stderr.read().decode("utf-8", errors="replace"))
        finally:
            if child.poll() is None:
                child.kill(); child.wait(timeout=5)
            for stream in (child.stdin, child.stdout, child.stderr):
                stream.close()


if __name__ == "__main__":
    unittest.main()
