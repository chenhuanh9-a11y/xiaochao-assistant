"""Opt-in timing/privacy/exit boundaries, controlled runtime and owned Qt queue."""
import json
import os
import subprocess
import sys
from pathlib import Path
import tempfile
import threading
import time
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

import client_lifecycle_trace as tracing
import test_client_material_handoff as handoff
from test_client_coordinator import CoordinatorTests
from test_client_session import until


class LifecycleTraceTests(unittest.TestCase):
    producer = handoff.MaterialHandoffTests.producer

    def trace(self):
        temp = tempfile.TemporaryDirectory(prefix="xingtu-trace-owned-")
        self.addCleanup(temp.cleanup)
        service = tracing.LifecycleTrace(SimpleNamespace(work_root=Path(temp.name)))
        def stop():
            service.shutdown()
            service._thread.join(2)  # Test cleanup only; product never joins it.
            self.assertFalse(service._thread.is_alive())
        self.addCleanup(stop)
        return service

    def test_default_off_creates_no_service_and_observation_does_no_diagnostic_timing(self):
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop(tracing.ENVIRONMENT_FLAG, None)
            with patch.object(tracing, "LifecycleTrace", side_effect=AssertionError("disabled service constructed")):
                for _ in range(1000):
                    self.assertIsNone(tracing.create_lifecycle_trace(SimpleNamespace()))
        fixture, session = self.producer("nightmare")
        self.assertIsNone(fixture.runtime._diagnostics)
        with patch("client_live_runtime.time.monotonic", side_effect=AssertionError("disabled diagnostic clock")):
            fixture.runtime._observe(fixture.engine, fixture.capture.frame)

    def test_enabled_real_queue_and_presenter_record_source_observation_and_delivery(self):
        trace = self.trace()
        fixture, session = self.producer("nightmare", display=True)
        runtime = fixture.runtime
        runtime._diagnostics = session._diagnostics = trace
        CoordinatorTests.setUpClass()
        view = CoordinatorTests(); view.setUp(); self.addCleanup(view.tearDown)
        coordinator = view.coordinator
        view.session.changed.disconnect(coordinator._session_changed)
        view.session.actionRequested.disconnect(coordinator._action_requested)
        coordinator.runtime, coordinator.session, coordinator._diagnostics = runtime, session, trace
        coordinator._applied_context = ("nightmare", runtime.snapshot()["profileId"])
        coordinator._convert_client_geometry = lambda row: row.update(deviceScale=1.)
        view.images.publish = Mock(side_effect=lambda token, *a, **kw: "image://live/" + token)
        session.changed.connect(coordinator._session_changed)
        session.actionRequested.connect(coordinator._action_requested)
        runtime.set_event_callback(coordinator.runtimeEvent.emit)
        session.dispatch("capture_now")
        until(lambda: not session._jobs)
        self.assertTrue(coordinator.alignedData)
        pressed = time.monotonic()
        view.input.mapInputRequested.emit(dict(sequence=1, openIntent=False,
            createdAt=pressed, hwnd=123, source="escape"))
        until(lambda: not session._jobs)
        self.assertFalse(coordinator.alignedData)
        until(lambda: not trace.pendingWrites, timeout=3)
        rows = [json.loads(line) for line in trace.log_path.read_text(encoding="utf-8").splitlines()]
        stages = {row["stage"] for row in rows}
        self.assertTrue({"enqueue", "worker-start", "worker-end", "result-consumed", "observation",
                         "runtime-event-received", "presentation-publish", "close-input"} <= stages, stages)
        close = next(row for row in rows if row["stage"] == "close-input")
        self.assertEqual((close["source"], close["inputAt"]), ("escape", pressed))
        observation = next(row for row in rows if row["stage"] == "observation")
        self.assertIs(observation["mapDetected"], True)
        self.assertGreaterEqual(observation["floorMs"], 0)
        delivered = next(row for row in rows if row["stage"] == "runtime-event-received")
        self.assertGreaterEqual(delivered["deliveryMs"], 0)
        self.assertTrue(any(row.get("accepted") is True for row in rows if row["stage"] == "result-consumed"))

    def test_bounded_queue_private_fields_and_blocked_write_do_not_hold_shutdown(self):
        entered, release = threading.Event(), threading.Event()
        batches = []
        def blocked(path, rows):
            batches.append(rows); entered.set(); release.wait(3)
        with patch.object(tracing, "_write_batch", side_effect=blocked):
            trace = self.trace()
            for _ in range(150):
                trace.record("observation", reason="ValueError:secret_identifier", source="C:/private/path", frameId="1:2:abc",
                             rawKeys="SECRET", pixels=[1, 2], mapDetected=True, elapsedMs=float("nan"))
            self.assertTrue(entered.wait(2))
            self.assertLessEqual(len(trace._queue) + trace._inflight, 64)
            self.assertGreater(trace._dropped, 0)
            CoordinatorTests.setUpClass()
            view = CoordinatorTests(); view.setUp(); self.addCleanup(view.tearDown)
            view.coordinator.close_writers = (trace,)
            view.coordinator.capture.disable = Mock()
            view.session.stop = Mock()
            idle_pool = SimpleNamespace(activeThreadCount=lambda: 0)
            view.session._pool = view.resources.pool = view.settings.pool = idle_pool
            view.resources.canCancel = False
            started = time.monotonic()
            view.coordinator.requestClose()
            self.assertLess(time.monotonic()-started, .1)
            self.assertTrue(view.coordinator.closeReady)
            self.assertFalse(trace.pendingWrites)
            self.assertTrue(trace._thread.daemon)
            self.assertTrue(trace._thread.is_alive())  # Stuck IO is not joined.
            release.set(); trace._thread.join(2)
        encoded = json.dumps(batches)
        self.assertNotIn("SECRET", encoded)
        self.assertNotIn("C:/private", encoded)
        self.assertNotIn("secret_identifier", encoded)
        self.assertNotIn("pixels", encoded)
        self.assertNotIn("elapsedMs", encoded)

    def test_existing_writer_rotation_has_fixed_total_bound(self):
        with tempfile.TemporaryDirectory(prefix="xingtu-trace-rotation-") as directory:
            path = Path(directory) / "lifecycle-detail.jsonl"
            with patch.object(tracing, "LOG_MAX_BYTES", 1024):
                tracing._write_batch(path, [dict(event="qt-lifecycle-detail", stage="runtime-result", reason="x"*96)]*50)
            files = sorted(Path(directory).glob("*.jsonl"))
            self.assertEqual(len(files), 2)
            self.assertTrue(all(file.stat().st_size < 1024 for file in files))

    def test_interpreter_exits_with_diagnostic_stream_write_blocked(self):
        code = r'''
import sys, threading
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
import client_lifecycle_trace as tracing
entered = threading.Event()
original = Path.open
class Blocked:
    def __init__(self, stream): self.stream = stream
    def tell(self): return self.stream.tell()
    def write(self, data):
        entered.set()
        threading.Event().wait(30)
    def close(self): self.stream.close()
def opened(path, *args, **kwargs):
    stream = original(path, *args, **kwargs)
    return Blocked(stream) if path.name == 'lifecycle-detail.jsonl' else stream
with patch.object(Path, 'open', opened):
    trace = tracing.LifecycleTrace(SimpleNamespace(work_root=Path(sys.argv[1])))
    trace.record('observation', mapDetected=True)
    assert entered.wait(2)
    trace.shutdown()
    assert trace._thread.is_alive() and not trace.pendingWrites
    print('blocked-stream-exit-ready', flush=True)
'''
        with tempfile.TemporaryDirectory(prefix="xingtu-trace-exit-") as directory:
            result = subprocess.run([sys.executable, "-X", "utf8", "-c", code, directory],
                capture_output=True, text=True, encoding="utf-8", timeout=5)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("blocked-stream-exit-ready", result.stdout)


if __name__ == "__main__":
    unittest.main()
