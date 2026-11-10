"""Native protocol-2 grid service parity against the production Python _grid."""

from __future__ import annotations

import json
import queue
import shutil
import subprocess
import sys
import tempfile
import threading
import unittest
from pathlib import Path

import numpy as np


ROOT = Path(__file__).resolve().parents[1]
APP = ROOT / "app"
sys.path.insert(0, str(APP))
import fast_structure_alignment as alignment  # noqa: E402


DOTNET = Path(r"E:\codex project\2026-11-09\full-csharp-validation\dotnet-sdk-10.0.403\dotnet.exe")
PROJECT = ROOT / "hybrid" / "StarMap.Grid" / "StarMap.Grid.csproj"
PROGRAM = ROOT / "hybrid" / "StarMap.Grid" / "bin" / "Release" / "net10.0" / "StarMap.Grid.exe"


class GridServiceProcess:
    def __init__(self):
        self.process = subprocess.Popen(
            [str(PROGRAM)], cwd=ROOT, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=subprocess.PIPE, text=True, encoding="utf-8", bufsize=1,
        )
        self.lines: queue.Queue[str | None] = queue.Queue()
        self.stderr: list[str] = []
        self.reader = threading.Thread(target=self._read_stdout, daemon=True)
        self.error_reader = threading.Thread(target=self._read_stderr, daemon=True)
        self.reader.start()
        self.error_reader.start()
        ready = self.read()
        if ready != {"ready": True, "protocol": 2, "pid": self.process.pid}:
            raise AssertionError(f"unexpected ready message: {ready!r}")

    def _read_stdout(self):
        assert self.process.stdout is not None
        for line in self.process.stdout:
            self.lines.put(line.rstrip("\r\n"))
        self.lines.put(None)

    def _read_stderr(self):
        assert self.process.stderr is not None
        self.stderr.extend(self.process.stderr.readlines())

    def read(self, timeout: float = 20.0):
        try:
            line = self.lines.get(timeout=timeout)
        except queue.Empty as error:
            raise AssertionError(f"timed out waiting for service output; stderr={''.join(self.stderr)}") from error
        if line is None:
            raise AssertionError(f"service closed stdout; stderr={''.join(self.stderr)}")
        try:
            return json.loads(line)
        except json.JSONDecodeError as error:
            raise AssertionError(f"invalid JSONL from service: {line!r}") from error

    def send(self, value):
        assert self.process.stdin is not None
        self.process.stdin.write(json.dumps(value, separators=(",", ":"), allow_nan=False) + "\n")
        self.process.stdin.flush()

    def until(self, predicate, timeout: float = 30.0):
        # Keep the test bounded even if the service stops emitting progress.
        import time

        deadline = time.monotonic() + timeout
        seen = []
        while time.monotonic() < deadline:
            event = self.read(max(0.01, deadline - time.monotonic()))
            seen.append(event)
            if predicate(event):
                return event, seen
        raise AssertionError(f"timed out waiting for matching event; seen={seen!r}; stderr={''.join(self.stderr)}")

    def stop(self):
        if self.process.poll() is None:
            try:
                self.send({"op": "stop"})
                assert self.process.stdin is not None
                self.process.stdin.close()
            except (BrokenPipeError, OSError):
                pass
            code = self.process.wait(timeout=20)
            if code != 0:
                raise AssertionError(f"grid service exited {code}; stderr={''.join(self.stderr)}")
        self.reader.join(timeout=5)
        self.error_reader.join(timeout=5)
        for stream in (self.process.stdin, self.process.stdout, self.process.stderr):
            if stream is not None and not stream.closed:
                stream.close()


class HybridGridServiceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not DOTNET.is_file() and shutil.which("dotnet") is None:
            raise unittest.SkipTest(".NET SDK 10 is unavailable")
        dotnet = str(DOTNET) if DOTNET.is_file() else shutil.which("dotnet")
        result = subprocess.run(
            [dotnet, "build", str(PROJECT), "-c", "Release", "--nologo", "-p:UseSharedCompilation=false"],
            cwd=ROOT, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=120,
        )
        if result.returncode:
            raise AssertionError(f"grid service build failed:\n{result.stdout}\n{result.stderr}")
        if not PROGRAM.is_file():
            raise AssertionError(f"Release apphost was not produced: {PROGRAM}")

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="starmap-grid-")
        self.root = Path(self.temp.name)
        self.service = GridServiceProcess()

    def tearDown(self):
        self.service.stop()
        self.temp.cleanup()

    def write_field(self, name: str, field: np.ndarray) -> Path:
        path = self.root / f"{name}.u16"
        np.asarray(field, dtype="<u2").tofile(path)
        return path

    @staticmethod
    def input_for(points, scale: float, shape, prior=None, *, cancelled=False):
        height, width = shape
        return {
            "width": int(width),
            "height": int(height),
            "scale": float(scale),
            "points": np.asarray(points, dtype=np.float32).tolist(),
            "prior": None if prior is None else np.asarray(prior, dtype=np.float64).tolist(),
            "cancelled": bool(cancelled),
        }

    def run_search(self, session: str, request_id: int, field: np.ndarray, points, scale: float, prior=None):
        path = self.write_field(f"{session}-{request_id}", field)
        payload = self.input_for(points, scale, field.shape, prior)
        python_rows = alignment._grid(
            np.asarray(points, dtype=np.float32),
            alignment.PreparedStructure(field, np.empty((0, 3, 2), np.float32),
                                        np.empty((0, 2, 2), np.float32), 0.0, field.shape),
            scale, prior, None,
        )
        self.service.send({
            "op": "search", "session": session, "id": request_id, "timeoutMs": 30000,
            "referencePath": str(path), "input": payload,
        })
        terminal, seen = self.service.until(
            lambda row: row.get("event") == "result" and row.get("session") == session and row.get("id") == request_id
        )
        self.assertIn("started", [event.get("event") for event in seen])
        self.assertTrue(terminal.get("ok"), terminal)
        expected = [[float(score), float(candidate_scale), [float(x), float(y)]]
                    for score, candidate_scale, (x, y) in python_rows]
        self.assertEqual(terminal["rows"], expected)
        return terminal, payload

    def test_global_local_float32_boundary_and_large_score_paths_match_python(self):
        height, width = 84, 104
        yy, xx = np.indices((height, width), dtype=np.int32)
        field = ((xx * 29 + yy * 73 + (xx * yy) % 31) % 96).astype(np.uint16)
        rng = np.random.default_rng(29110)
        points = rng.uniform([-8, -4], [width + 9, height + 7], (72, 2)).astype(np.float32)
        scale = float(np.nextafter(np.float32(.5), np.float32(1.0)))
        self.run_search("global-boundary", 1, field, points, scale)

        local_scale = float(np.nextafter(np.float32(.75), np.float32(1.0)))
        prior = np.array([[local_scale, 0, 47.25], [0, local_scale, 38.5], [0, 0, 1]], np.float64)
        offsets = np.arange(-32.0, 32.1, 4.0)
        selected_tx, selected_ty = prior[0, 2] + offsets, prior[1, 2] + offsets
        boundary_points = np.array([
            [np.float32(selected_tx[5] + (17.5 * np.float32(local_scale))), np.float32(selected_ty[4] + 21 * local_scale)],
            [np.nextafter(np.float32(selected_tx[6] + (31.5 * np.float32(local_scale))), np.float32(-np.inf)), 42.0],
            [np.nextafter(np.float32(selected_tx[7] + (43.5 * np.float32(local_scale))), np.float32(np.inf)), 17.0],
        ], dtype=np.float32)
        self.run_search("local-boundary", 2, field, boundary_points, local_scale, prior)

        height, width = 520, 520
        yy, xx = np.indices((height, width), dtype=np.int32)
        large_field = ((xx * 17 + yy * 31 + (xx * yy) % 67) % 180).astype(np.uint16)
        large_points = rng.uniform([0, 0], [width - 1, height - 1], (96, 2)).astype(np.float32)
        large_scale = float(np.nextafter(np.float32(1.0), np.float32(np.inf)))
        reference_points = large_points / large_scale
        stride = max(3.0, 5.0 / large_scale)
        axes = [np.arange(-np.quantile(reference_points, .5, axis=0)[i],
                          large_field.shape[1 if i == 0 else 0]
                          - np.quantile(reference_points, .5, axis=0)[i] + stride, stride)
                for i in range(2)]
        self.assertGreater(len(axes[0]) * len(axes[1]), 4096)
        large_result, _ = self.run_search("global-large", 3, large_field, large_points, large_scale)
        self.assertIn("allPoseScoresMs", large_result["stages"])

    def test_even_float32_median_matches_numpy_quantile_candidate_rows(self):
        # This seed exposed a one-ULP median change that shifted otherwise tied
        # global candidates. Keep the original exact-coordinate counterexample.
        rng = np.random.default_rng(432)
        for _ in range(24):
            points = np.concatenate([
                rng.integers(0, 100, (48, 2)),
                rng.integers(1000, 1100, (48, 2)),
            ]).astype(np.float32)
            scale = float(rng.integers(5, 150) / rng.integers(15, 180))
            field = rng.integers(0, 96, (100, 140), dtype=np.uint16)
        original, _ = self.run_search("median-coordinate", 4, field, points, scale)
        self.assertEqual(original["rows"][0], [0.0, scale, [549.4999688992834, 549.0]])

        # This sample contains 96 unique integer image points and produces a
        # real score/order change, matching the production coarse point domain.
        rng = np.random.default_rng(432)
        for _ in range(65):
            pa = rng.choice(100, 48, replace=False)
            pb = rng.choice(100, 48, replace=False)
            points = np.concatenate([
                np.column_stack((pa % 10, pa // 10)),
                np.column_stack((pb % 10 + 60, pb // 10 + 60)),
            ]).astype(np.float32)
            scale = float(rng.integers(5, 150) / rng.integers(15, 180))
            field = rng.integers(0, 96, (200, 240), dtype=np.uint16)
        self.assertEqual(len(np.unique(points, axis=0)), 96)
        unique, _ = self.run_search("median-score", 5, field, points, scale)
        self.assertEqual(unique["rows"][0], [
            0.4479166567325592, scale, [-25.50000310348252, -20.500002974170748]
        ])

    def test_no_solution_and_deadline_are_explicit(self):
        # The production global proposal budget deliberately returns no rows
        # when all poses would exceed 350,000; this is not manufactured data.
        shape = (3000, 3000)
        field = np.zeros(shape, np.uint16)
        points = np.array([[1498.0, 1501.0], [1502.0, 1497.0]], np.float32)
        path = self.write_field("no-solution", field)
        request = self.input_for(points, 6.0, shape)
        expected = alignment._grid(points, alignment.PreparedStructure(
            field, np.empty((0, 3, 2), np.float32), np.empty((0, 2, 2), np.float32), 0.0, shape), 6.0, None, None)
        self.assertEqual(expected, [])
        self.service.send({"op": "search", "session": "no-solution", "id": 1, "timeoutMs": 30000,
                           "referencePath": str(path), "input": request})
        terminal, _ = self.service.until(lambda row: row.get("event") == "result" and row.get("id") == 1)
        self.assertTrue(terminal["ok"], terminal)
        self.assertEqual(terminal["rows"], [])

        small = np.zeros((20, 20), np.uint16)
        small_path = self.write_field("deadline", small)
        timeout_request = self.input_for(np.array([[5.0, 5.0]], np.float32), 1.0, small.shape)
        self.service.send({"op": "search", "session": "deadline", "id": 2, "timeoutMs": 0,
                           "referencePath": str(small_path), "input": timeout_request})
        timed_out, _ = self.service.until(lambda row: row.get("event") == "result" and row.get("id") == 2)
        self.assertFalse(timed_out["ok"])
        self.assertEqual(timed_out["reason"], "timeout")

        pre_cancel_path = self.write_field("pre-cancelled", small)
        pre_cancelled = self.input_for(np.array([[5.0, 5.0]], np.float32), 1.0, small.shape, cancelled=True)
        self.service.send({"op": "search", "session": "pre-cancelled", "id": 3, "timeoutMs": 30000,
                           "referencePath": str(pre_cancel_path), "input": pre_cancelled})
        cancelled, _ = self.service.until(lambda row: row.get("event") == "result" and row.get("id") == 3)
        self.assertFalse(cancelled["ok"])
        self.assertEqual(cancelled["reason"], "cancelled")

    def test_busy_exact_cancellation_and_service_recovery(self):
        height, width = 1200, 1200
        yy, xx = np.indices((height, width), dtype=np.int32)
        field = ((xx * 17 + yy * 31 + (xx * yy) % 67) % 200).astype(np.uint16)
        rng = np.random.default_rng(1120)
        points = rng.uniform([0, 0], [width - 1, height - 1], (600, 2)).astype(np.float32)
        path = self.write_field("cancel", field)
        request = self.input_for(points, 1.0, field.shape)
        self.service.send({"op": "search", "session": "cancel-owner", "id": 41, "timeoutMs": 30000,
                           "referencePath": str(path), "input": request})
        self.service.until(lambda row: row.get("event") == "started" and row.get("id") == 41)
        self.service.send({"op": "search", "session": "busy", "id": 42, "timeoutMs": 30000,
                           "referencePath": str(path), "input": request})
        busy, _ = self.service.until(lambda row: row.get("event") == "result" and row.get("id") == 42)
        self.assertFalse(busy["ok"])
        self.assertEqual(busy["reason"], "busy")

        self.service.send({"op": "cancel", "session": "wrong-session", "id": 41})
        self.service.send({"op": "cancel", "session": "cancel-owner", "id": 40})
        self.service.until(lambda row: row.get("event") == "progress" and row.get("id") == 41)
        self.service.send({"op": "cancel", "session": "cancel-owner", "id": 41})
        cancelled, _ = self.service.until(lambda row: row.get("event") == "result" and row.get("id") == 41)
        self.assertFalse(cancelled["ok"])
        self.assertEqual(cancelled["reason"], "cancelled")

        recovery_field = np.zeros((28, 32), np.uint16)
        recovery_points = np.array([[8.0, 8.0], [12.0, 14.0]], np.float32)
        recovered, _ = self.run_search("recovered", 43, recovery_field, recovery_points, 1.0)
        self.assertTrue(recovered["ok"])

    def test_eof_cancels_and_waits_for_worker_terminal(self):
        field = np.zeros((640, 640), np.uint16)
        points = np.array([[220.0, 180.0], [300.0, 360.0]], np.float32)
        path = self.write_field("eof", field)
        self.service.send({"op": "search", "session": "eof", "id": 7, "timeoutMs": 30000,
                           "referencePath": str(path), "input": self.input_for(points, 1.0, field.shape)})
        self.service.until(lambda row: row.get("event") == "started" and row.get("id") == 7)
        assert self.service.process.stdin is not None
        self.service.process.stdin.close()
        terminal, _ = self.service.until(lambda row: row.get("event") == "result" and row.get("id") == 7)
        self.assertFalse(terminal["ok"])
        self.assertEqual(terminal["reason"], "cancelled")
        self.assertEqual(self.service.process.wait(timeout=20), 0)


if __name__ == "__main__":
    unittest.main()
