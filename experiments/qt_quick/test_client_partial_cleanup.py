"""Actual offscreen QtClient/pools and Temp lease; native IO is substituted."""
import os
import builtins
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import time
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from PySide6.QtCore import QRunnable, QTimer, QUrl
from PySide6.QtGui import QGuiApplication
from PySide6.QtQml import QQmlApplicationEngine
import client
import client_paths
import backend
from client_input import NativeShortcutBackend
from resource_transaction import resource_runtime_lease


class PartialClientCleanupTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QGuiApplication.instance() or QGuiApplication([])

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / "fixture"
        (self.root / "_internal").mkdir(parents=True)
        (self.root / ".qt-lab-owner").write_text("qt-quick-isolated-v1")
        self.enterContext(patch.object(client_paths, "WORK_ROOT", self.root.parent))
        self.enterContext(patch.object(client_paths, "PRODUCTION", False))
        self.enterContext(patch.object(client, "PRODUCTION", False))
        self.enterContext(patch("client_input.NativeShortcutBackend", side_effect=lambda: NativeShortcutBackend(native=SimpleNamespace(), source=None)))
        self.enterContext(patch("client_capture_exclusion.QtCaptureExclusion.protectWindow", return_value=True))
        self.enterContext(patch("client_coordinator.ClientCoordinator.startLiveServices"))
        self.instances = []
        instances = self.instances
        class TrackedClient(client.QtClient):
            def __new__(cls, *args, **kwargs):
                instance = super().__new__(cls)
                instances.append(instance)
                return instance
        self.enterContext(patch.object(client, "QtClient", TrackedClient))
        self.started, self.release, self.loaded = threading.Event(), threading.Event(), threading.Event()
        original = backend.load_browse_context
        def blocked(*args, **kwargs):
            self.started.set()
            if not self.release.wait(8):
                raise RuntimeError("fixture worker timeout")
            return original(*args, **kwargs)
        self.enterContext(patch.object(backend, "load_browse_context", side_effect=blocked))
        self.args = SimpleNamespace(sandbox=self.root, probe_output=self.root / "probe")
        self.guard = Mock()
        self.report = self.enterContext(patch("qt_startup_diagnostics.report_startup_failure"))

    def external_lease_probe(self):
        script = ("import sys;sys.path.insert(0,sys.argv[1]);"
                  "from resource_transaction import resource_runtime_lease,ResourceTransactionError\n"
                  "try:\n lease=resource_runtime_lease(sys.argv[2]);lease.close();print('acquired')\n"
                  "except ResourceTransactionError: print('busy')\n")
        result = subprocess.run([sys.executable, "-c", script, str(client_paths.APP_ROOT), str(self.root)],
                                capture_output=True, text=True, timeout=10)
        self.assertEqual(result.returncode, 0, result.stderr)
        return result.stdout.strip()

    def final_fixture_cleanup(self):
        # Only teardown after the asserted function has returned. Never counts
        # as evidence that the product drained its workers.
        self.release.set()
        for instance in self.instances:
            evidence = getattr(instance, "g_cycle_evidence", None)
            if evidence is not None:
                evidence.shutdown(wait=True)
            trace = getattr(instance, "lifecycle_trace", None)
            if trace is not None:
                trace.shutdown()
                trace._thread.join()
            for value in vars(instance).values():
                pool = getattr(value, "pool", getattr(value, "_pool", None))
                if hasattr(pool, "waitForDone"):
                    pool.waitForDone()
            for name in ("coordinator", "input", "session", "resources", "settings", "backend", "route_choices", "info", "online_packages", "custom_galleries", "windows"):
                value = getattr(instance, name, None)
                if value is not None:
                    value.shutdown()
        self.app.processEvents()

    def run_complete_client_exit(self, *, loop_error=False, shutdown_error=False, evidence_mode=None, normal_close=False):
        if evidence_mode is not None:
            self.args.g_cycle_evidence = True
        if evidence_mode == "busy":
            import g_cycle_evidence
            original_encode = g_cycle_evidence._encode
            def blocked_encode(*args):
                self.started.set()
                if not self.release.wait(8):
                    raise RuntimeError("fixture evidence writer timeout")
                return original_encode(*args)
            self.enterContext(patch.object(g_cycle_evidence, "_encode", side_effect=blocked_encode))
        surface = self.root / "fixture.qml"
        surface.write_text('import QtQuick\nWindow { objectName: "clientControlsWindow"; width: 120; height: 90; visible: false }')
        original_load = QQmlApplicationEngine.load
        self.enterContext(patch.object(QQmlApplicationEngine, "load", lambda engine, url:
            original_load(engine, QUrl.fromLocalFile(str(surface)))))
        observed = {}
        original_shutdown = None
        release = self.release
        class Work(QRunnable):
            def run(self):
                if not release.wait(8):
                    raise RuntimeError("fixture pool worker timed out")
        jobs = []
        def ready(instance, output):
            nonlocal original_shutdown
            self.assertIn(instance.performance, instance.coordinator.close_writers)
            if evidence_mode is not None:
                self.assertIn(instance.g_cycle_reporter, instance.coordinator.close_writers)
                self.assertNotIn(instance.problems, instance.coordinator.close_writers)
                recorder = instance.g_cycle_evidence
                recorder.shutdown = Mock(wraps=recorder.shutdown)
                self.assertTrue(recorder.snapshot()["running"])
                if evidence_mode == "busy":
                    reply = recorder.submit(None, dict(identity="fixture", session="fixture",
                        round=1, cycle=1, opening="none", attempt="none", frame="none",
                        outcome="missing", reason="fixture-no-input"))
                    self.assertEqual(reply["status"], "queued")
                    self.assertEqual(recorder.pendingWrites, 1)
            for pool in (instance.session._pool, instance.resources.pool):
                job = Work()
                jobs.append(job)
                pool.start(job)
            if shutdown_error:
                original_shutdown = instance.settings.shutdown
                instance.settings.shutdown = Mock(side_effect=RuntimeError("fixture settings shutdown failure"))
            instance.tutorial.dismissInvite()
            instance.tutorial.markDone('start')
            instance.tutorial.markDone('round')
            self.loaded.set()
            if not loop_error:
                QTimer.singleShot(0, instance.coordinator.requestClose if normal_close else self.app.quit)
        self.enterContext(patch("probe_client.run_client_probe", side_effect=ready))
        def observer():
            try:
                if not self.loaded.wait(5):
                    observed["error"] = "constructor never completed"
                    return
                time.sleep(0.05)
                observed["lease"] = self.external_lease_probe()
            except Exception as error:
                observed["error"] = str(error)
            finally:
                self.release.set()
        thread = threading.Thread(target=observer)
        thread.start()
        if loop_error:
            def broken_exec():
                self.app.processEvents()
                raise RuntimeError("fixture event loop failure")
            self.enterContext(patch.object(self.app, "exec", side_effect=broken_exec))
        try:
            with resource_runtime_lease(self.root):
                if shutdown_error:
                    from client_startup_cleanup import ClientCleanupError
                    with self.assertRaises(ClientCleanupError) as caught:
                        client._run_primary_client(self.app, self.args, self.guard)
                    self.assertIn("settings.shutdown", [name for name, error in caught.exception.failures])
                elif loop_error:
                    with self.assertRaisesRegex(RuntimeError, "event loop failure"):
                        client._run_primary_client(self.app, self.args, self.guard)
                else:
                    self.assertEqual(client._run_primary_client(self.app, self.args, self.guard), 0)
                instance = self.instances[0]
                self.assertFalse(instance.performance._timer.isActive())
                if evidence_mode is not None:
                    recorder = instance.g_cycle_evidence
                    recorder.shutdown.assert_any_call(wait=True)
                    self.assertEqual(recorder.pendingWrites, 0)
                    self.assertFalse(recorder.snapshot()["running"])
                    if evidence_mode == "busy":
                        self.assertEqual(recorder.snapshot()["counters"]["saved-missing-input"], 1)
                        text = instance.problems.log_path.read_text(encoding="utf-8")
                        self.assertIn('"g-cycle-evidence"', text)
                        self.assertIn('"completion"', text)
                        self.assertIn('"saved-missing-input"', text)
                        self.assertFalse(instance.g_cycle_reporter.pendingWrites)
                import json
                saved = json.loads(instance.tutorial.state_path.read_text(encoding='utf-8'))
                self.assertEqual(saved['done'], ['start', 'round'])
                self.assertTrue(saved['invited'])
                self.assertTrue(instance.tutorial._closed)
                self.assertFalse(instance.tutorial.pendingWrites)
                self.assertEqual(instance.session._pool.activeThreadCount(), 0)
                self.assertEqual(instance.resources.pool.activeThreadCount(), 0)
                self.assertEqual(instance.backend.pool.activeThreadCount(), 0)
            thread.join(10)
            self.assertNotIn("error", observed)
            self.assertEqual(observed["lease"], "busy")
            self.assertEqual(self.external_lease_probe(), "acquired")
            journal = self.instances[0].lifecycle.path.read_text(encoding="utf-8")
            if shutdown_error:
                self.assertNotIn("resource-workers-drained", journal)
                self.assertNotIn("shutdown-completed", journal)
            else:
                self.assertIn("resource-workers-drained", journal)
        finally:
            self.release.set()
            thread.join(10)
            if original_shutdown is not None:
                self.instances[0].settings.shutdown = original_shutdown
            self.final_fixture_cleanup()

    def test_direct_quit_drains_actual_session_and_resource_pools(self):
        self.run_complete_client_exit()

    def test_event_loop_exception_still_drains_actual_client_pools(self):
        self.run_complete_client_exit(loop_error=True)

    def test_shutdown_failure_continues_other_pool_drains_without_success_record(self):
        self.run_complete_client_exit(shutdown_error=True)

    def test_complete_client_exit_joins_idle_evidence_writer(self):
        self.run_complete_client_exit(evidence_mode="idle")

    def test_complete_client_exit_drains_and_joins_busy_evidence_writer(self):
        self.run_complete_client_exit(evidence_mode="busy")

    def test_normal_close_keeps_problems_open_until_evidence_completion(self):
        self.run_complete_client_exit(evidence_mode="busy", normal_close=True)

    def test_other_shutdown_failure_still_joins_evidence_writer(self):
        self.run_complete_client_exit(evidence_mode="busy", shutdown_error=True)

    def test_actual_constructor_import_failure_keeps_original_error_before_lifecycle_exists(self):
        original = builtins.__import__
        failure = ImportError("fixture early import", name="owned_fixture_dependency")
        def importing(name, *args, **kwargs):
            if name == "backend":
                raise failure
            return original(name, *args, **kwargs)
        with patch("builtins.__import__", side_effect=importing), resource_runtime_lease(self.root):
            self.assertEqual(client._run_primary_client(self.app, self.args, self.guard), 1)
        self.assertEqual(len(self.instances), 1)
        self.assertFalse(hasattr(self.instances[0], "lifecycle"))
        self.report.assert_called_once_with(failure, stage="create-client")

    def test_qml_failure_keeps_lease_until_actual_partial_backend_workers_finish(self):
        original_load = QQmlApplicationEngine.load
        def missing(engine, url):
            original_load(engine, QUrl.fromLocalFile(str(self.root / "absent.qml")))
            self.loaded.set()
        self.enterContext(patch.object(QQmlApplicationEngine, "load", missing))
        observed = {}
        def observer():
            try:
                if not self.loaded.wait(5) or not self.started.wait(5):
                    observed["error"] = "fixture did not reach QML and worker boundaries"
                    return
                time.sleep(0.05)
                observed["leaseWhileWorkerBlocked"] = self.external_lease_probe()
            except Exception as error:
                observed["error"] = str(error)
            finally:
                self.release.set()
        thread = threading.Thread(target=observer)
        thread.start()
        try:
            with patch("qt_startup_diagnostics.report_startup_failure") as report:
                with resource_runtime_lease(self.root):
                    result = client._run_primary_client(self.app, self.args, self.guard)
                    observed["activeAtReturn"] = sum(getattr(self.instances[0], name).pool.activeThreadCount()
                                                     for name in ("backend", "route_choices"))
                thread.join(10)
            self.assertFalse(thread.is_alive())
            self.assertNotIn("error", observed)
            self.assertEqual(result, 1)
            self.assertEqual(observed["leaseWhileWorkerBlocked"], "busy")
            self.assertEqual(observed["activeAtReturn"], 0)
            report.assert_called_once()
            self.assertEqual(self.external_lease_probe(), "acquired")
        finally:
            self.release.set()
            thread.join(10)
            self.final_fixture_cleanup()


if __name__ == "__main__":
    unittest.main()
