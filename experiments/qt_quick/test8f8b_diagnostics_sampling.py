"""Help opt-in and export worker integration with synthetic providers only."""
import json
import os
from pathlib import Path
import sys
import tempfile
import threading
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch

ROOT = Path(os.environ.get('QT_IDENTITY_SOURCE_ROOT', Path(__file__).resolve().parents[2]))
sys.path[:0] = [str(ROOT / 'experiments/qt_quick'), str(ROOT / 'app')]
from PySide6.QtCore import QCoreApplication
import client_info


class DiagnosticsSamplingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QCoreApplication.instance() or QCoreApplication([])

    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        internal = self.root / '_internal'
        internal.mkdir()
        self.commit = '8f8b14bda334ccf7bba26d1eadd87f723e83a34a'
        (internal / 'installation-profile.json').write_text(json.dumps(dict(sourceCommit=self.commit)), 'utf-8')
        paths = SimpleNamespace(installation_root=self.root, resource_root=internal,
            work_root=self.root / 'work', update_state_path=self.root / 'update.json',
            memory_root=lambda mode: self.root / 'work' / (mode + '-memory'))
        with patch.object(client_info, 'runtime_paths', return_value=paths):
            self.service = client_info.ClientInfoService(self.root)
        self.addCleanup(self.service.shutdown)

    def test_existing_help_opt_in_configures_current_runtime_and_is_not_persisted(self):
        calls = []
        runtime = SimpleNamespace(
            configure_failure_sampling=lambda **kwargs: calls.append(('configure', kwargs)),
            set_failure_sampling_enabled=lambda value: calls.append(('enabled', value)))
        self.service.setProviders(coordinator=SimpleNamespace(runtime=runtime),
                                 problems=SimpleNamespace(log_path=self.root / 'events-qt.jsonl'))
        self.assertEqual(calls[0], ('configure', dict(event_path=str(self.root / 'events-qt.jsonl'),
            build_fingerprint='8F8B14BD', source_commit=self.commit)))
        self.assertEqual(calls[-1], ('enabled', False))
        self.service.setIncludeFailureEvidence(True)
        self.assertEqual(calls[-1], ('enabled', True))
        self.service.setIncludeFailureEvidence(False)
        self.assertEqual(calls[-1], ('enabled', False))
        self.assertFalse((self.root / 'update.json').exists())

    def test_shutdown_disables_sampling_and_missing_runtime_is_safe(self):
        self.service.setIncludeFailureEvidence(True)
        calls = []
        self.service.setProviders(coordinator=SimpleNamespace(runtime=SimpleNamespace(
            set_failure_sampling_enabled=calls.append)))
        self.assertEqual(calls[-1], True)
        self.service.shutdown()
        self.assertEqual(calls[-1], False)

    def test_hashing_runs_only_inside_existing_export_worker(self):
        import package_identity
        calls = []
        original = package_identity.collect_package_identity
        def collect(*args, **kwargs):
            calls.append(threading.get_ident())
            return original(*args, **kwargs)
        with patch.object(package_identity, 'collect_package_identity', side_effect=collect):
            self.assertEqual(calls, [])
            self.service._export_diagnostics(self.root / 'diagnostics.zip')
            deadline = time.monotonic() + 5
            while self.service.checking and time.monotonic() < deadline:
                self.app.processEvents()
                time.sleep(.002)
            self.assertFalse(self.service.checking)
            self.assertTrue(self.service.diagnosticsReceipt['success'])
        self.assertEqual(len(calls), 1)
        self.assertNotEqual(calls[0], threading.get_ident())


if __name__ == '__main__':
    unittest.main()
