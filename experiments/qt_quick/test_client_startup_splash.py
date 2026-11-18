"""Offscreen lifecycle: real Qt loop, fake product/services; no game or install."""
from contextlib import nullcontext
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

    def run_program_entry(self, root, *, primary=True, on_run=None):
        """Actual entry/main/program lease; no product UI or native guard/dialog."""
        import qt_client_entry as entry
        import client_resource_startup as startup
        events = []
        guard = Mock()
        guard.start.return_value = primary
        native_identity = (patch('ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID', return_value=0)
                           if os.name == 'nt' else nullcontext())
        def run_primary(app, args, guard):
            events.append('client')
            return on_run(app, args, guard) if on_run is not None else 0
        close = Mock(side_effect=lambda: events.append('close'))
        with patch.object(sys, 'argv', ['client', '--sandbox', str(root)]), \
             patch.object(client, 'PRODUCTION', True), \
             patch.object(client, 'QGuiApplication', return_value=self.app), \
             patch('client_single_instance.SingleInstanceGuard', return_value=guard), \
             patch.object(client, 'owned_sandbox', side_effect=lambda p: Path(p)), \
             patch.object(client, 'QtClient') as constructor, \
             patch.object(client, '_run_primary_client', side_effect=run_primary) as run, \
             patch.object(startup, 'program_runtime', wraps=startup.program_runtime) as runtime, \
             patch.object(startup, 'repair_managed_base_resources') as repair, \
             patch('client_resource_startup_recovery.offer_startup_recovery') as recovery, \
             patch('packaged_product_identity.baked_identity', return_value='qt-production'), \
             patch.object(entry, 'show_startup_splash', side_effect=lambda _: events.append('show')), \
             patch.object(entry, 'close_startup_splash', close), \
             patch('qt_startup_splash.close_startup_splash', close), \
             patch.object(entry, 'report_startup_failure', side_effect=lambda *a, **k: events.append('report')) as report, \
             native_identity:
            result = entry.run()
        guard.close.assert_called_once()
        constructor.assert_not_called()
        repair.assert_not_called()
        recovery.assert_not_called()
        return SimpleNamespace(result=result, events=events, run=run, runtime=runtime, report=report)

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

    def test_compatibility_report_does_not_close_splash_before_first_frame(self):
        with tempfile.TemporaryDirectory() as folder:
            report = dict(status='retirement-deferred', reasons=['retired-mode-still-required'])
            args = SimpleNamespace(sandbox=Path(folder), probe_output=None,
                                   resource_startup_report=report)
            instance = Mock()
            instance._closed, instance._main_frame_seen = False, False
            frame_ready = client.QtClient._main_frame_ready
            close_counts, reports_at_frame = [], []

            def observe_first_frame():
                try:
                    close_counts.append(close.call_count)
                    reports_at_frame.append(instance.resources.setStartupCompatibilityReport.call_args)
                    frame_ready(instance)
                    close_counts.append(close.call_count)
                finally:
                    self.app.quit()

            def construct(*a, **kw):
                QTimer.singleShot(0, observe_first_frame)
                return instance

            with patch.object(client, 'PRODUCTION', False), \
                 patch.object(client, 'owned_sandbox', side_effect=lambda p: p), \
                 patch.object(client, 'QtClient', side_effect=construct), \
                 patch('qt_startup_splash.close_startup_splash') as close:
                self.assertEqual(client._run_primary_client(self.app, args, Mock()), 0)
            self.assertEqual(close_counts, [0, 1])
            self.assertEqual(reports_at_frame[0].args, (report,))
            instance.resources.setStartupCompatibilityReport.assert_called_once_with(report)
            instance.shutdown.assert_called_once()

    def test_first_submitted_main_frame_closes_once_and_records_real_boundary(self):
        from types import SimpleNamespace
        instance = client.QtClient.__new__(client.QtClient)
        instance._closed, instance._main_frame_seen = False, False
        instance.performance = SimpleNamespace(sample=Mock())
        with patch('qt_startup_splash.close_startup_splash') as close, \
             patch.object(client, 'set_startup_stage') as stage:
            instance._main_frame_ready()
            instance._main_frame_ready()
        close.assert_called_once()
        stage.assert_called_once_with('main-frame-submitted')
        instance.performance.sample.assert_called_once()

    def test_repeated_qml_warnings_keep_bounded_serializable_list(self):
        instance = client.QtClient.__new__(client.QtClient)
        instance.warnings = []
        for _ in range(5):
            instance._record_qml_warnings(range(100))
        self.assertEqual(len(instance.warnings), 128)
        self.assertIsInstance(instance.warnings, list)
        self.assertEqual(instance.warnings[-1], '99')

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
        from resource_transaction import resource_runtime_lease
        cases = (
            ('work/pending-installer-upgrade.json', 'installer-upgrade-recovery-required'),
            ('work/map-packages/pending-resource-reset.json', 'resource-reset-recovery-required'),
        )
        for primary in (False, True):
            for relative, code in cases:
                with self.subTest(primary=primary, code=code), tempfile.TemporaryDirectory() as folder:
                    root = Path(folder).resolve()
                    pending = root / relative
                    pending.parent.mkdir(parents=True, exist_ok=True)
                    pending.write_bytes(b'{"fixture":"retained"}')
                    # Establish the real persistent lease file before comparing
                    # the untouched installation across a blocked startup.
                    with resource_runtime_lease(root):
                        pass
                    def snapshot():
                        return {p.relative_to(root).as_posix(): p.read_bytes()
                                for p in root.rglob('*') if p.is_file()}
                    before = snapshot()
                    observed = self.run_program_entry(root, primary=primary)
                    observed.run.assert_not_called()
                    self.assertEqual(observed.result, 1 if primary else 0)
                    self.assertEqual(observed.events, ['show', 'close', 'report', 'close'] if primary
                                     else ['show', 'close', 'close'])
                    if primary:
                        observed.runtime.assert_called_once()
                        observed.report.assert_called_once()
                        self.assertEqual(observed.report.call_args.args[0].code, code)
                        self.assertEqual(observed.report.call_args.kwargs, dict(stage='run-client'))
                    else:
                        observed.runtime.assert_not_called()
                        observed.report.assert_not_called()
                    self.assertEqual(snapshot(), before)
                    with resource_runtime_lease(root):
                        pass

    def test_legacy_journals_are_retained_and_reported_without_old_repair(self):
        from resource_transaction import resource_runtime_lease
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder).resolve()
            state = root / 'work/map-packages'
            state.mkdir(parents=True)
            names = ['pending-base-repair.json', 'pending-upgrade.json', 'pending-additions.json',
                     'pending-removal.json', 'pending-author-snapshot.json']
            for name in names:
                (state / name).write_bytes(b'{"fixture":"retained"}')
            before = {name: (state / name).read_bytes() for name in names}
            reports = []
            def consumer(app, args, guard):
                reports.append(dict(args.resource_startup_report))
                return 0
            observed = self.run_program_entry(root, on_run=consumer)
            self.assertEqual(observed.result, 0)
            self.assertEqual(observed.events, ['show', 'client', 'close'])
            observed.run.assert_called_once()
            observed.runtime.assert_called_once()
            observed.report.assert_not_called()
            self.assertEqual(reports, [dict(status='imported-resources', legacyPending=names)])
            self.assertEqual({name: (state / name).read_bytes() for name in names}, before)
            with resource_runtime_lease(root):
                pass


if __name__ == '__main__':
    unittest.main()
