"""Automated Qt entry failures persist diagnostics and exit without a dialog."""
from pathlib import Path
from contextlib import contextmanager
import ctypes
import io
import importlib.util
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

import packaged_product_identity as identity
import qt_startup_diagnostics as diagnostics

SPEC = importlib.util.spec_from_file_location(
    "qt_client_entry_diagnostic_exit",
    Path(__file__).parents[1] / "tools/qt_client_entry.py",
)
entry = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(entry)


class DiagnosticExitTests(unittest.TestCase):
    def _run_failure(self, argv):
        temp = tempfile.TemporaryDirectory(prefix="qt-diagnostic-exit-")
        self.addCleanup(temp.cleanup)
        work = Path(temp.name) / "work"

        def fail():
            raise ValueError("startup fixture failure")

        with patch.object(sys, "argv", ["star-map", *argv]), \
                patch.object(sys, "frozen", True, create=True), \
                patch.object(entry, "show_startup_splash"), \
                patch.object(entry, "close_startup_splash"), \
                patch.object(identity, "baked_identity", return_value={}), \
                patch.dict(sys.modules, {"client": SimpleNamespace(main=fail)}), \
                patch.object(sys, "stderr", io.StringIO()), \
                patch.object(diagnostics, "_candidate_directories", return_value=[work]), \
                patch.object(diagnostics, "_show_native_error") as show:
            result = entry.run()

        report = work / diagnostics.LOG_NAME
        self.assertEqual(result, 1)
        self.assertTrue(report.is_file(), "entry failure should persist the startup report")
        contents = report.read_text("utf-8")
        self.assertIn("exception=ValueError", contents)
        self.assertIn("stage=run-client", contents)
        return show

    def test_automated_diagnostics_persist_failure_without_native_dialog(self):
        invocations = (
            ["--self-check"],
            ["--probe-output", "probe.json"],
            ["--probe-output=probe.json"],
        )
        for argv in invocations:
            with self.subTest(argv=argv):
                show = self._run_failure(argv)
                show.assert_not_called()

    def test_interactive_startup_failure_keeps_native_dialog(self):
        for argv in ([], ["--"]):
            with self.subTest(argv=argv):
                show = self._run_failure(argv)
                show.assert_called_once()


@unittest.skipUnless(sys.platform == "win32", "Exercises the Windows startup identity/dialog boundary")
class MainDiagnosticFailureTests(unittest.TestCase):
    """Real main/entry/reporter; instance and pending-resource boundaries replaced."""
    def setUp(self):
        import client
        import client_single_instance as instance
        import client_resource_startup as resources
        import client_resource_startup_recovery as recovery
        self.client, self.instance = client, instance
        self.resources, self.recovery = resources, recovery
        temporary = tempfile.TemporaryDirectory(prefix="qt-main-failure-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.guard = Mock()
        self.guard.start.return_value = True
        self.enterContext(patch.object(identity, "baked_identity", return_value="qt-production"))
        self.enterContext(patch.object(entry, "show_startup_splash"))
        self.enterContext(patch.object(entry, "close_startup_splash"))
        self.enterContext(patch.object(client, "QGuiApplication", return_value=Mock()))
        self.enterContext(patch.object(client, "PRODUCTION", True))
        self.enterContext(patch.object(client, "owned_sandbox", return_value=self.root))
        self.enterContext(patch.object(instance, "instance_namespace", return_value="fixture"))
        self.enterContext(patch.object(instance, "SingleInstanceGuard", return_value=self.guard))
        self.enterContext(patch.object(ctypes.windll.shell32, "SetCurrentProcessExplicitAppUserModelID", return_value=0))
        self.box = self.enterContext(patch.object(ctypes.windll.user32, "MessageBoxW"))
        self.show = self.enterContext(patch.object(diagnostics, "_show_native_error"))
        self.enterContext(patch.object(diagnostics, "_candidate_directories", return_value=[self.root / "diagnostics"]))
        self.enterContext(patch.object(sys, "stderr", None))
        self.primary = self.enterContext(patch.object(client, "_run_primary_client", return_value=0))

    def invoke(self, probe=True):
        args = ["star-map", "--probe-output", str(self.root / "probe")] if probe else ["star-map"]
        with patch.object(sys, "argv", args):
            return entry.run()

    def test_probe_single_instance_failure_reports_and_returns_without_dialog(self):
        self.guard.start.side_effect = self.instance.SingleInstanceError("fixture unavailable")
        self.assertEqual(self.invoke(), 2)
        report = self.root / "diagnostics" / diagnostics.LOG_NAME
        self.assertTrue(report.is_file())
        self.assertIn("stage=instance-check", report.read_text("utf-8"))
        self.box.assert_not_called()
        self.show.assert_not_called()
        self.guard.close.assert_called_once()

    def test_interactive_single_instance_failure_keeps_original_notice(self):
        self.guard.start.side_effect = self.instance.SingleInstanceError("fixture unavailable")
        self.assertEqual(self.invoke(probe=False), 2)
        self.box.assert_called_once()
        self.guard.close.assert_called_once()

    def pending_resources(self):
        error = self.recovery.StartupRecoveryError("upgrade-recovery-required")
        @contextmanager
        def pending(root, *, on_failure, **kwargs):
            result = on_failure(error)
            if result is None:
                raise error
            yield result
        self.enterContext(patch.object(self.resources, "production_resources", pending))
        return self.enterContext(patch.object(self.recovery, "offer_startup_recovery",
            return_value=self.recovery.StartupRecoveryResult("cancelled")))

    def test_probe_pending_resources_reports_nonzero_without_recovery_prompt(self):
        recovery = self.pending_resources()
        self.assertEqual(self.invoke(), 1)
        recovery.assert_not_called()
        report = self.root / "diagnostics" / diagnostics.LOG_NAME
        self.assertTrue(report.is_file())
        self.assertIn("stage=run-client", report.read_text("utf-8"))
        self.show.assert_not_called()
        self.primary.assert_not_called()
        self.guard.close.assert_called_once()

    def test_interactive_pending_resources_keeps_cancelled_recovery_exit(self):
        recovery = self.pending_resources()
        self.assertEqual(self.invoke(probe=False), 0)
        recovery.assert_called_once()
        self.primary.assert_not_called()
        self.guard.close.assert_called_once()


if __name__ == "__main__":
    unittest.main()
