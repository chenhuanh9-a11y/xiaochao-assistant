"""Fake affinity/windows and synthetic pixels only, without desktop capture."""
import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import threading
import time
import unittest

import numpy as np
from PySide6.QtGui import QGuiApplication

from client_capture_exclusion import QtCaptureExclusion, CaptureExclusionError
from client_capture import ClientCaptureProvider
from test_client_input import Native as CaptureNative, Backend as CaptureBackend

APP = QGuiApplication.instance() or QGuiApplication([])


class Window:
    def __init__(self, hwnd):
        self.hwnd = hwnd
        self.owner = threading.get_ident()

    def winId(self):
        if threading.get_ident() != self.owner:
            raise AssertionError("Worker touched a window")
        return self.hwnd


class Native:
    def __init__(self):
        self.values = {101: 1, 102: 0, 103: 0}
        self.calls = []
        self.owner = threading.get_ident()
        self.fail_set = None
        self.foreign = set()

    def record(self, *call):
        if threading.get_ident() != self.owner:
            raise AssertionError("Worker touched affinity")
        self.calls.append(call)

    def is_window(self, hwnd):
        self.record("is-window", hwnd)
        return hwnd in self.values

    def process_id(self, hwnd):
        self.record("pid", hwnd)
        return os.getpid() + 1 if hwnd in self.foreign else os.getpid()

    def get(self, hwnd):
        self.record("get", hwnd)
        return self.values[hwnd]

    def set(self, hwnd, value):
        self.record("set", hwnd, value)
        if (hwnd, value) == self.fail_set:
            raise OSError("fixture affinity failure")
        self.values[hwnd] = value

    def flush(self):
        self.record("flush")


class CaptureExclusionTests(unittest.TestCase):
    def test_constructing_on_a_worker_is_rejected_before_any_window_or_affinity_io(self):
        native, errors = Native(), []
        def create():
            try:
                QtCaptureExclusion(windows=lambda: self.fail("Worker enumerated windows"), native=native)
            except Exception as error:
                errors.append(error)
        worker = threading.Thread(target=create)
        worker.start(); worker.join(1.)
        self.assertFalse(worker.is_alive())
        self.assertEqual(len(errors), 1)
        self.assertIsInstance(errors[0], CaptureExclusionError)
        self.assertIn("Qt GUI window owner", str(errors[0]))
        self.assertEqual(native.calls, [])

    def make(self, capture):
        self.native = Native()
        self.windows = [Window(101), Window(102)]
        helper = QtCaptureExclusion(windows=lambda: self.windows, native=self.native,
                                   capture=capture, timeout=.2)
        self.addCleanup(helper.shutdown)
        return helper

    def run_worker(self, helper, *, on_owner=None, process_events=True, target=None):
        result = {}
        def work():
            try:
                result["value"] = target() if target is not None else helper.full_capture(42)
            except Exception as error:
                result["error"] = error
        thread = threading.Thread(target=work)
        thread.start()
        deadline = time.monotonic() + 2.
        while thread.is_alive() and time.monotonic() < deadline:
            if process_events:
                APP.processEvents()
            if on_owner is not None:
                on_owner(helper)
            thread.join(.001)
        self.assertFalse(thread.is_alive(), "Fixture thread did not terminate")
        if process_events:
            APP.processEvents()
        return result

    def test_prepare_restore_run_only_on_ui_and_original_capture_only_on_worker(self):
        owner = threading.get_ident()
        def capture(hwnd):
            self.assertNotEqual(threading.get_ident(), owner)
            self.assertEqual(hwnd, 42)
            self.assertEqual([self.native.values[h] for h in (101, 102)], [0x11, 0x11])
            return np.zeros((20, 30, 3), np.uint8), (10, 20, 40, 40)
        helper = self.make(capture)
        self.assertEqual(self.native.calls, [])
        result = self.run_worker(helper)
        self.assertNotIn("error", result)
        self.assertEqual(self.native.values, {101: 1, 102: 0, 103: 0})
        sets = [r for r in self.native.calls if r[0] == "set"]
        self.assertEqual(sets, [("set", 101, 0x11), ("set", 102, 0x11),
                                ("set", 102, 0), ("set", 101, 1)])
        self.assertFalse(helper.pendingWrites)

    def test_capture_failure_restores_before_original_error_returns(self):
        def capture(hwnd):
            raise ValueError("original capture failed")
        helper = self.make(capture)
        result = self.run_worker(helper)
        self.assertIsInstance(result["error"], ValueError)
        self.assertEqual(str(result["error"]), "original capture failed")
        self.assertEqual(self.native.values[101], 1)
        self.assertEqual(self.native.values[102], 0)

    def test_client_provider_uses_owner_lane_after_dc_failure_and_retains_full_client_metadata(self):
        helper = self.make(lambda hwnd: (np.zeros((80, 100, 3), np.uint8), (10, 20, 110, 100)))
        backend = CaptureBackend()
        def dc_failure(hwnd, region):
            raise OSError("fixture client DC failed")
        backend.capture = dc_failure
        provider = ClientCaptureProvider(CaptureNative(), backend, full_capture=helper.full_capture)
        self.addCleanup(provider.shutdown)
        provider.bindFullClient(42)
        self.assertTrue(provider.enable())
        result = self.run_worker(helper, target=provider.capture)
        self.assertNotIn("error", result)
        frame = result["value"]
        self.assertEqual(frame.context, backend.current)
        self.assertEqual(frame.frame_bgr.shape, (80, 100, 3))
        self.assertEqual(frame.acquisition, "full-frame-fallback-crop")
        self.assertEqual(self.native.values[101], 1)

    def test_partial_prepare_failure_never_captures_and_restores_prior_windows(self):
        captures = []
        helper = self.make(lambda hwnd: captures.append(hwnd))
        self.native.fail_set = (102, 0x11)
        result = self.run_worker(helper)
        self.assertIsInstance(result["error"], OSError)
        self.assertEqual(captures, [])
        self.assertEqual(self.native.values[101], 1)

    def test_restore_failure_discards_result_and_reports_precise_boundary(self):
        helper = self.make(lambda hwnd: ("synthetic pixels", (0, 0, 30, 20)))
        self.native.fail_set = (101, 1)
        result = self.run_worker(helper)
        self.assertIsInstance(result["error"], CaptureExclusionError)
        self.assertIn("could not be restored", str(result["error"]))
        self.assertNotIn("value", result)

    def test_new_window_is_enrolled_on_owner_before_capture_can_complete(self):
        waiting, finish = threading.Event(), threading.Event()
        def capture(hwnd):
            waiting.set()
            if not finish.wait(1.):
                raise AssertionError("Owner did not enroll the new window")
            self.assertEqual(self.native.values[103], 0x11)
            return "synthetic", (0, 0, 30, 20)
        helper = self.make(capture)
        def enroll(helper):
            if waiting.is_set() and not finish.is_set():
                window = Window(103)
                self.windows.append(window)
                helper.protectWindow(window)
                finish.set()
        result = self.run_worker(helper, on_owner=enroll)
        self.assertNotIn("error", result)
        self.assertEqual(self.native.values[103], 0)

    def test_cancel_during_native_capture_drains_restoration_but_discards_pixels(self):
        waiting, finish = threading.Event(), threading.Event()
        def capture(hwnd):
            waiting.set(); finish.wait(1.)
            return "synthetic", (0, 0, 30, 20)
        helper = self.make(capture)
        def cancel(helper):
            if waiting.is_set() and not finish.is_set():
                self.assertTrue(helper.pendingWrites)
                helper.cancel()
                finish.set()
        result = self.run_worker(helper, on_owner=cancel)
        self.assertIsInstance(result["error"], CaptureExclusionError)
        self.assertFalse(helper.pendingWrites)
        self.assertEqual(self.native.values[101], 1)

    def test_foreign_window_and_owner_thread_capture_are_rejected(self):
        helper = self.make(lambda hwnd: self.fail("Captured without exclusion"))
        with self.assertRaisesRegex(CaptureExclusionError, "capture worker"):
            helper.full_capture(42)
        self.native.foreign.add(102)
        result = self.run_worker(helper)
        self.assertIsInstance(result["error"], CaptureExclusionError)
        self.assertEqual(self.native.values[101], 1)

    def test_owner_timeout_never_captures_and_late_owner_events_restore_safely(self):
        captures = []
        helper = self.make(lambda hwnd: captures.append(hwnd))
        result = self.run_worker(helper, process_events=False)
        self.assertIsInstance(result["error"], CaptureExclusionError)
        self.assertEqual(captures, [])
        self.assertTrue(helper.pendingWrites)
        APP.processEvents()
        self.assertFalse(helper.pendingWrites)
        self.assertEqual(self.native.calls, [])


if __name__ == "__main__":
    unittest.main()
