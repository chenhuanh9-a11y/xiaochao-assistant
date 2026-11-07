"""Fake own-process counters and real Qt timer ownership, no desktop inspection."""
import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from types import SimpleNamespace
import unittest
from unittest.mock import patch
from PySide6.QtGui import QGuiApplication
from client_performance import PerformanceService, process_sample
from diagnostic_privacy import safe_record

APP = QGuiApplication.instance() or QGuiApplication([])


class PerformanceTests(unittest.TestCase):
    def test_bounded_detached_metadata_has_explicit_endpoint_limits(self):
        now = [0.]
        service = PerformanceService(sampler=lambda: dict(workingSetBytes=100, privateBytes=80,
            threadCount=3, handleCount=20, path="private", commandLine="secret"),
            clock=lambda: now[0], start_timer=False)
        for _ in range(150):
            now[0] += 10
            service.sample()
        result = service.snapshot
        self.assertEqual(result["sampleCount"], 150)
        self.assertEqual(len(result["samples"]), 120)
        self.assertEqual(result["maxima"]["privateBytes"], 80)
        self.assertFalse(result["gpuMeasured"])
        self.assertFalse(result["childProcessesMeasured"])
        self.assertNotIn("path", result["samples"][0])
        result["samples"][0]["workingSetBytes"] = 999
        self.assertEqual(service.snapshot["samples"][0]["workingSetBytes"], 100)
        self.assertEqual(safe_record(dict(performance=service.snapshot))["performance"], service.snapshot)
        service.shutdown()

    def test_failed_sample_is_unknown_not_zero_and_shutdown_stops_sampling(self):
        def fail(): raise OSError("private error")
        service = PerformanceService(sampler=fail)
        service.sample()
        self.assertEqual(service.snapshot["status"], "sample-unavailable")
        self.assertEqual(service.snapshot["samples"], [])
        service.shutdown()
        self.assertFalse(service._timer.isActive())
        service._sampler = lambda: dict(workingSetBytes=100)
        service.sample()
        self.assertEqual(service.snapshot["sampleCount"], 0)

    def test_non_windows_virtual_memory_is_not_reported_as_private_commit(self):
        import psutil
        process = unittest.mock.MagicMock()
        process.memory_info.return_value = SimpleNamespace(rss=123, vms=987)
        process.num_threads.return_value = 2
        del process.num_handles
        with patch.object(psutil, "Process", return_value=process):
            row = process_sample()
        self.assertEqual(row, dict(workingSetBytes=123, threadCount=2))
