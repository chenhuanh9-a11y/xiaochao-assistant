"""Production main scheduling with actual resource files/leases, no game IO.

Only QtClient (resource consumer), native single-instance guard, and the owned
application identity are replaced. The actual main, Qt event loop, build
projection, repair, file hashing and resource lease execute against Temp.
"""
from contextlib import nullcontext
from pathlib import Path
import os
import sys
import unittest
from unittest.mock import Mock, patch

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
from PySide6.QtCore import QTimer
from PySide6.QtGui import QGuiApplication

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'tests'))
import test_managed_base_resources as fixtures
import client
from resource_transaction import resource_runtime_lease, ResourceTransactionError


class ResourceStartupTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QGuiApplication.instance() or QGuiApplication([])

    def setUp(self):
        self.fixture = fixtures.ManagedBaseResourcesTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.fixture.project()
        self.root = self.fixture.target

    def run_main(self, constructor, *, real_recovery=False):
        guard = Mock()
        guard.start.return_value = True
        native_identity = (patch('ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID', return_value=0)
                           if os.name == 'nt' else nullcontext())
        recovery = (nullcontext() if real_recovery else
                    patch('client_resource_startup_recovery.offer_startup_recovery', return_value=None))
        with patch.object(sys, 'argv', ['client', '--sandbox', str(self.root)]), \
             patch.object(client, 'PRODUCTION', True), \
             patch.object(client, 'owned_sandbox', side_effect=lambda p: Path(p)), \
             patch.object(client, 'QGuiApplication', return_value=self.app), \
             patch.object(client, 'QtClient', side_effect=constructor), \
             recovery, \
             patch('client_single_instance.SingleInstanceGuard', return_value=guard), \
             native_identity:
            try:
                result = client.main()
            finally:
                guard.close.assert_called_once()
        return result

    def test_main_repairs_before_first_reader_and_keeps_lease_through_shutdown(self):
        observed = {}
        def consumer(app, root, **_):
            observed['ready'] = all((root / '_internal' / name).is_file()
                                    for name in self.fixture.resources)
            try:
                resource_runtime_lease(root).close()
                observed['leased'] = False
            except ResourceTransactionError:
                observed['leased'] = True
            instance = Mock()
            def drain():
                try:
                    resource_runtime_lease(root).close()
                    observed['shutdownLeased'] = False
                except ResourceTransactionError:
                    observed['shutdownLeased'] = True
            instance.shutdown.side_effect = drain
            QTimer.singleShot(0, app.quit)
            return instance
        self.assertEqual(self.run_main(consumer), 0)
        self.assertEqual(observed, dict(ready=True, leased=True, shutdownLeased=True))
        with resource_runtime_lease(self.root):
            pass

    def test_pending_author_transaction_prevents_reader_and_releases_lease(self):
        pending = self.root / 'work/map-packages/pending-additions.json'
        pending.parent.mkdir(parents=True)
        pending.write_text('{}', encoding='utf-8')
        called = []
        def consumer(app, *_args, **_kw):
            called.append(True)
            QTimer.singleShot(0, app.quit)
            return Mock()
        with self.assertRaisesRegex(ValueError, 'recovery-required'):
            self.run_main(consumer)
        self.assertEqual(called, [])
        self.assertTrue(pending.exists())
        with resource_runtime_lease(self.root):
            pass

    def test_installer_and_reset_markers_block_real_main_without_map_helper(self):
        cases = (
            ('work/pending-installer-upgrade.json', 'installer-upgrade-recovery-required'),
            ('work/map-packages/pending-resource-reset.json', 'resource-reset-recovery-required'),
        )
        for relative, code in cases:
            with self.subTest(code=code):
                pending = self.root / relative
                self.fixture.write(pending, b'{"status":"unverified"}')
                self.fixture.write(self.root / 'work/player-settings.json', b'keep settings')
                self.fixture.write(self.root / '_internal/references/catalog.json', b'keep resources')
                # Establish the persistent lock file before taking a byte snapshot.
                with resource_runtime_lease(self.root):
                    pass
                def snapshot():
                    return {p.relative_to(self.root).as_posix(): p.read_bytes()
                            for p in self.root.rglob('*') if p.is_file()}
                before = snapshot()
                consumer = Mock()
                with patch('client_resource_startup_recovery._inspect') as inspect, \
                     patch('client_resource_startup_recovery._confirm') as confirm, \
                     patch('client_resource_startup_recovery._launch') as launch, \
                     patch('client_resource_startup_recovery._persist_pending') as persist:
                    with self.assertRaisesRegex(ValueError, code) as caught:
                        self.run_main(consumer, real_recovery=True)
                self.assertEqual(caught.exception.code, code)
                consumer.assert_not_called()
                for boundary in (inspect, confirm, launch, persist):
                    boundary.assert_not_called()
                self.assertEqual(snapshot(), before)
                with resource_runtime_lease(self.root):
                    pass
                self.assertEqual(snapshot(), before)
                pending.unlink()

    def test_deferred_constructor_failure_is_persisted(self):
        error = ImportError('example module unavailable', name='owned_fixture_module')
        with patch('qt_startup_diagnostics.report_startup_failure') as report:
            self.assertEqual(self.run_main(Mock(side_effect=error)), 1)
        report.assert_called_once_with(error, stage='create-client')
        with resource_runtime_lease(self.root):
            pass

    def test_self_check_never_prepares_providers_during_pending_transaction(self):
        pending = self.root / 'work/map-packages/pending-additions.json'
        pending.parent.mkdir(parents=True)
        pending.write_text('{}', encoding='utf-8')
        registry = Mock()
        registry.capabilities.return_value = {'difficult': {'available': True}}
        with patch.object(sys, 'argv', ['client', '--sandbox', str(self.root), '--self-check', '--verify-resources']), \
             patch.object(client, 'PRODUCTION', True), patch.object(client, 'FROZEN', False), \
             patch.object(client, 'owned_sandbox', side_effect=lambda p: Path(p)), \
             patch('client_providers.build_engine_factories', return_value=registry):
            with self.assertRaisesRegex(ValueError, 'additions-recovery-required'):
                client.main()
        registry.prepare.assert_not_called()


if __name__ == '__main__':
    unittest.main()
