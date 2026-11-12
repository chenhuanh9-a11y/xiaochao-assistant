"""Actual persistent C# transport and Python proposal consumer; no game/native IO."""
from pathlib import Path
import sys
import tempfile
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / part) for part in ("app", "experiments/qt_quick")]

import cv2
import numpy as np
import fast_structure_alignment as fast
from csharp_grid_backend import GridBackend, GridFailure
from single_map_structure import current_geometry, contour_graph


EXE = ROOT / "hybrid/StarMap.Grid/bin/Release/net10.0/StarMap.Grid.exe"


def ambiguous_scene():
    """A generated repeated-room image naturally needs grid and stays ambiguous."""
    source = np.zeros((300, 300), np.uint8)
    source[25:275, 25:275] = 1
    for y in range(45, 255, 40):
        for x in range(45, 255, 40):
            source[y:y+20, x:x+20] = 0
    source[85:110, 85:105] = 1
    source[155:180, 205:245] = 0
    # Generator transform only. It is never supplied to propose/grid/refine.
    projected = cv2.warpAffine(source, np.array([[.6, 0, 180], [0, .6, 120]]),
                               (640, 480), flags=cv2.INTER_NEAREST)
    image = (25 + projected * 105).astype(np.uint8)
    blurred = cv2.GaussianBlur(image, (0, 0), 1.2)
    regions = np.zeros_like(image)
    for node in contour_graph(source):
        x, y = np.rint(node["point"] * .6 + [180, 120]).astype(int)
        cv2.circle(regions, (int(x), int(y)), 4, 1, -1)
    image = np.where(regions > 0, blurred, image).astype(np.uint8)
    return source, cv2.cvtColor(image, cv2.COLOR_GRAY2BGR)


class HybridGridBackendTests(unittest.TestCase):
    def setUp(self):
        self.assertTrue(EXE.is_file(), "Build hybrid/StarMap.Grid in Release before this test.")
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.backend = GridBackend(EXE, self.temp.name)
        self.addCleanup(self.backend.close)

    def test_lazy_persistent_candidates_match_current_python_exactly(self):
        self.assertIsNone(self.backend.process_id)
        rng = np.random.default_rng(145)
        prepared = SimpleNamespace(distance=rng.integers(0, 140, (92, 137), dtype=np.uint16), shape=(92, 137))
        points = rng.uniform(-20, 160, (70, 2)).astype(np.float32)
        with self.backend.request("exact", 1, 10):
            for scale, prior in ((.37, None), (1., np.eye(3)), (.1, None)):
                expected = fast._grid(points, prepared, scale, prior, None)
                actual = self.backend(points, prepared, scale, prior, None)
                self.assertEqual(len(actual), len(expected))
                for left, right in zip(expected, actual):
                    self.assertEqual(left[:2], right[:2])
                    np.testing.assert_array_equal(left[2], right[2])
                process_id = self.backend.process_id
                self.assertIsNotNone(process_id)
            self.assertEqual(len(self.backend.calls), 3)
            self.assertEqual(self.backend.process_id, process_id)
        self.backend.close()
        self.assertIsNone(self.backend.process_id)

    def test_csharp_candidates_are_actually_refined_and_remain_rejected(self):
        source, frame = ambiguous_scene()
        prepared = fast.prepare(source)
        context = current_geometry(frame, observation_bounds=(0, 0, 640, 480))
        expected = fast.propose(frame, None, prepared, context=context)
        refined = []
        original = fast.refine

        def observe_refine(*args, **kwargs):
            value = original(*args, **kwargs)
            refined.append(value)
            return value

        with self.backend.request("consumer", 2, 10), fast.using_grid_backend(self.backend), patch.object(fast, "refine", observe_refine):
            actual = fast.propose(frame, None, prepared, context=context)
        self.assertEqual(len(self.backend.calls), 1)
        self.assertGreater(len(refined), 0)
        self.assertEqual(actual[0], [])
        self.assertEqual(actual[2]["reason"], "fast-ambiguous")
        for key in ("reason", "points", "coarsePoints", "scaleLocked", "candidateCount", "scores"):
            self.assertEqual(actual[2][key], expected[2][key])
        self.assertIsNone(fast._grid_backend.get())

    def test_scope_is_local_and_resets_after_failure_without_fallback(self):
        source, frame = ambiguous_scene()
        prepared = fast.prepare(source)
        context = current_geometry(frame, observation_bounds=(0, 0, 640, 480))
        seen = []

        def fail(*args):
            raise GridFailure("injected-backend-failure")

        def other_worker():
            seen.append(fast._grid_backend.get())

        with fast.using_grid_backend(fail):
            thread = threading.Thread(target=other_worker)
            thread.start(); thread.join()
            with self.assertRaisesRegex(GridFailure, "injected-backend-failure"):
                fast.propose(frame, None, prepared, context=context)
        self.assertEqual(seen, [None])
        self.assertIsNone(fast._grid_backend.get())

    def test_cancelled_request_starts_no_child_and_next_request_recovers(self):
        prepared = SimpleNamespace(distance=np.zeros((8, 8), np.uint16), shape=(8, 8))
        points = np.ones((40, 2), np.float32)
        with self.assertRaises(InterruptedError), self.backend.request("cancel", 3, 1, lambda: True):
            self.backend(points, prepared, 1., None, None)
        self.assertIsNone(self.backend.process_id)
        with self.backend.request("next", 4, 5):
            self.assertEqual(len(self.backend(points, prepared, 1., None, None)),
                             len(fast._grid(points, prepared, 1., None, None)))

    def test_idle_child_crash_is_restarted_only_by_next_request(self):
        prepared = SimpleNamespace(distance=np.zeros((8, 8), np.uint16), shape=(8, 8))
        points = np.ones((40, 2), np.float32)
        with self.backend.request("warm", 5, 5):
            self.backend(points, prepared, 1., None, None)
        process = self.backend._process
        process.kill(); process.wait(timeout=5)
        # Startup can recognize an already-exited idle child and explicitly
        # restart on the next request. No Python search is substituted.
        with self.backend.request("restart", 6, 5):
            self.backend(points, prepared, 1., None, None)
        self.assertNotEqual(self.backend.process_id, process.pid)

    def test_cancel_after_real_first_score_rejects_result_then_recovers(self):
        prepared = SimpleNamespace(distance=np.zeros((2048, 2048), np.uint16), shape=(2048, 2048))
        points = np.random.default_rng(19).uniform(0, 1000, (96, 2)).astype(np.float32)

        def first_score_seen():
            return any(event.get("phase") == "allPoseScores" for event in self.backend.events)

        with self.assertRaises(InterruptedError), self.backend.request("cancel-running", 7, 10, first_score_seen):
            self.backend(points, prepared, 1., None, None)
        self.assertTrue(first_score_seen())
        self.assertIsNone(self.backend.process_id)
        with self.backend.request("after-cancel", 8, 5):
            small = SimpleNamespace(distance=np.zeros((8, 8), np.uint16), shape=(8, 8))
            self.backend(points, small, 1., None, None)

    def test_transmitted_search_crash_fails_without_python_fallback(self):
        prepared = SimpleNamespace(distance=np.zeros((2048, 2048), np.uint16), shape=(2048, 2048))
        points = np.random.default_rng(20).uniform(0, 1000, (96, 2)).astype(np.float32)
        killed = []

        def kill_own_child_after_score():
            if not killed and any(e.get("phase") == "allPoseScores" for e in self.backend.events):
                killed.append(self.backend.process_id)
                self.backend._process.kill()
            return False

        with self.assertRaisesRegex(GridFailure, "grid-process-exited"), self.backend.request("crash-running", 9, 10, kill_own_child_after_score):
            self.backend(points, prepared, 1., None, None)
        self.assertEqual(len(killed), 1)
        self.assertIsNone(self.backend.process_id)

    def test_python_caller_cannot_swallow_service_failure_and_commit_success(self):
        prepared = SimpleNamespace(distance=np.zeros((8, 8), np.uint16), shape=(8, 8))
        with self.assertRaises(GridFailure), self.backend.request("failed-candidate", 10, 5):
            try:
                self.backend(np.empty((0, 2), np.float32), prepared, 1., None, None)
            except GridFailure:
                pass  # Mirrors a candidate loop that records an error and keeps running.
        self.assertEqual(len(self.backend.calls), 1)
        self.assertIsNone(self.backend.process_id)


if __name__ == "__main__":
    unittest.main()
