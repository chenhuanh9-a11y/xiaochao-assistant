"""Offscreen lifecycle: real Qt loop, fake product/services; no game or install."""
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace
import os
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
from PySide6.QtCore import QTimer
from PySide6.QtGui import QGuiApplication
import client


class ClientSplashTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QGuiApplication.instance() or QGuiApplication([])

    def test_success_closes_hint_before_event_loop_exit(self):
        with tempfile.TemporaryDirectory() as folder:
            args = SimpleNamespace(sandbox=Path(folder), probe_output=None)
            guard = Mock()
            instance = Mock()
            def construct(*a, **kw):
                QTimer.singleShot(0, self.app.quit)
                return instance
            with patch.object(client, 'PRODUCTION', False), \
                 patch.object(client, 'owned_sandbox', side_effect=lambda p: p), \
                 patch.object(client, 'QtClient', side_effect=construct), \
                 patch('qt_startup_splash.close_startup_splash') as close:
                self.assertEqual(client._run_primary_client(self.app, args, guard), 0)
            close.assert_called_once()
            instance.shutdown.assert_called_once()

    def test_constructor_failure_closes_before_reporting_and_drains_partial(self):
        with tempfile.TemporaryDirectory() as folder:
            args = SimpleNamespace(sandbox=Path(folder), probe_output=None)
            partial = Mock()
            events = []
            def fail(*a, **kw):
                kw['on_created'](partial)
                raise ValueError('fixture failure')
            with patch.object(client, 'PRODUCTION', False), \
                 patch.object(client, 'owned_sandbox', side_effect=lambda p: p), \
                 patch.object(client, 'QtClient', side_effect=fail), \
                 patch('qt_startup_splash.close_startup_splash', side_effect=lambda: events.append('close')), \
                 patch('qt_startup_diagnostics.report_startup_failure', side_effect=lambda *a, **k: events.append('report')):
                self.assertEqual(client._run_primary_client(self.app, args, Mock()), 1)
            self.assertEqual(events, ['close', 'report'])
            partial.shutdown.assert_called_once()

    def test_deferred_path_failure_closes_and_reports_instead_of_stalling(self):
        with tempfile.TemporaryDirectory() as folder:
            args = SimpleNamespace(sandbox=Path(folder), probe_output=None)
            events = []
            with patch.object(client, 'PRODUCTION', False), \
                 patch.object(client, 'owned_sandbox', side_effect=ValueError('invalid sandbox')), \
                 patch.object(client, 'QtClient') as constructor, \
                 patch('qt_startup_splash.close_startup_splash', side_effect=lambda: events.append('close')), \
                 patch('qt_startup_diagnostics.report_startup_failure', side_effect=lambda *a, **k: events.append('report')):
                self.assertEqual(client._run_primary_client(self.app, args, Mock()), 1)
            self.assertEqual(events, ['close', 'report'])
            constructor.assert_not_called()

    def test_secondary_and_recovery_close_before_other_ui(self):
        for primary in (False, True):
            events = []
            guard = Mock()
            guard.start.return_value = primary
            @contextmanager
            def resources(root, **kw):
                result = kw['on_failure'](ValueError('fixture recovery'))
                yield result
            with patch.object(sys, 'argv', ['client', '--sandbox', '.']), \
                 patch.object(client, 'QGuiApplication', return_value=self.app), \
                 patch('client_single_instance.SingleInstanceGuard', return_value=guard), \
                 patch.object(client, 'owned_sandbox', side_effect=lambda p: p), \
                 patch('client_resource_startup.production_resources', resources), \
                 patch('client_resource_startup_recovery.offer_startup_recovery', side_effect=lambda *a: (events.append('recovery') or SimpleNamespace(exit_code=3))), \
                 patch('qt_startup_splash.close_startup_splash', side_effect=lambda: events.append('close')), \
                 patch.object(client, '_run_primary_client') as run:
                self.assertEqual(client.main(), 3 if primary else 0)
            self.assertEqual(events, ['close', 'recovery'] if primary else ['close'])
            run.assert_not_called()
            guard.close.assert_called_once()


if __name__ == '__main__':
    unittest.main()
