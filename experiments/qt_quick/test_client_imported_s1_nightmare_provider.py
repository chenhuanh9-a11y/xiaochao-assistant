"""S1 nightmare uses its own pool through the shared imported activation path.

The source fixture has 15 registered maps with synthetic 8x8 structures. The
negative frame proves real scanner routing and competition, not game accuracy.
"""
from pathlib import Path
import tempfile
import time
import unittest

import numpy as np
from PySide6.QtCore import QCoreApplication, QUrl

from client_imported_providers import (
    SUPPORTED_CAPABILITIES, ImportedProviderUnavailable, build_imported_engine_factories, build_native_engine,
)
from client_imported_resources import ImportedResourceService
from client_live_runtime import LiveRuntime
from client_paths import RuntimePaths
from client_route_preferences import prepare_display_layer
from client_session import SessionService
from imported_resource_catalog import ResourceResolver
from imported_resource_packages import PackageView
from s1_native_resources import MODE_LAYOUTS
from test_client_live_runtime import FixtureCapture
import test_export_s1_native_base_package as source_fixture
import test_imported_s1_controls_profile as controls_fixture


class ImportedS1NightmareProviderTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QCoreApplication.instance() or QCoreApplication([])
        temporary = tempfile.TemporaryDirectory(prefix='s1-nightmare-provider-source-')
        cls.addClassCleanup(temporary.cleanup)
        cls.source = source_fixture.S1NativeBaseSourceFixture(
            Path(temporary.name).resolve(), mode='nightmare')
        cls.source.export()

    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix='s1-nightmare-provider-active-')
        self.addCleanup(temporary.cleanup)
        self.paths = RuntimePaths(Path(temporary.name).resolve(), False)
        self.resolver = ResourceResolver(self.paths.imported_resource_root,
                                         supported_capabilities=SUPPORTED_CAPABILITIES)
        self.factories = build_imported_engine_factories(self.resolver, self.paths)
        self.runtime = LiveRuntime(engine_factories=self.factories,
                                   capture_provider=FixtureCapture(), evidence_kind='synthetic')
        self.session = SessionService(self.runtime)
        self.service = ImportedResourceService(self.resolver, paths=self.paths)
        self.service.setLeaseProvider(self.session)
        self.errors, self.changes = [], []
        self.service.errorReported.connect(self.errors.append)
        self.service.resourcesChanged.connect(lambda: self.changes.append(True))
        self.addCleanup(self.close)
        self.service.importArchive(QUrl.fromLocalFile(str(self.source.output)).toString())
        self.wait()
        self.assertFalse(self.errors, self.errors)
        self.assertEqual(self.changes, [True])
        self.assertEqual(self.service.availableContexts, ['s1:nightmare'])
        self.key = self.service.packageRows[0]['key']

    def close(self):
        self.service.shutdown()
        self.service.pool.waitForDone()
        self.app.processEvents()
        self.session.shutdown()
        self.session._pool.waitForDone()
        self.runtime.shutdown()
        self.app.processEvents()

    def wait(self):
        deadline = time.monotonic() + 90
        while self.service.busy and time.monotonic() < deadline:
            self.app.processEvents()
            time.sleep(.005)
        self.assertFalse(self.service.busy, self.service.message)

    def test_service_activates_nightmare_pool_and_actual_scanner_without_live_authority(self):
        row = self.service.packageRows[0]
        self.assertEqual(row['activatableContexts'], ['s1:nightmare'])
        option = next(item for item in self.service.activationOptions if item['context'] == 's1:nightmare')
        self.assertEqual(option['label'], '启用 S1 噩梦数据')
        self.assertIn('不可用', option['notice'])
        model = self.service.packageModel
        self.assertTrue(self.service.activatePackage(self.key, 's1', 'nightmare'))
        self.assertFalse(self.service.pendingWrites)
        deadline = time.monotonic()+90
        while not self.service.pendingWrites and self.service.busy and time.monotonic() < deadline:
            self.app.processEvents(); time.sleep(.005)
        self.assertTrue(self.service.pendingWrites, self.service.message)
        self.assertEqual(self.session.acquireResourceLease(), '')
        self.wait()
        self.assertFalse(self.errors, self.errors)
        self.assertEqual(self.changes, [True])
        self.assertIs(self.service.packageModel, model)
        self.assertEqual(self.service.packageRows[0]['selectedContexts'], ['s1:nightmare'])
        self.assertIn('S1 噩梦', self.service.message)
        self.assertIn('自动楼层识别', self.service.message)
        self.assertIn('不可用', self.service.message)
        engine = self.factories.get_engine('nightmare', season='s1')
        report = self.factories.capabilities('s1')['nightmare']
        self.assertTrue(report['available'], report)
        self.assertFalse(report['liveReady'])
        self.assertFalse(report['candidateObservation']['candidateReady'])
        self.assertEqual(set(engine.pack.by_id), MODE_LAYOUTS['nightmare'])
        self.assertFalse(set(engine.pack.by_id) & MODE_LAYOUTS['difficult'])
        self.assertEqual(report['inventory']['typedLayers'], 60)
        self.assertEqual(report['inventory']['mainEntries'], 15)
        self.assertEqual(report['inventory']['sideEntries'], 60)
        self.assertFalse(report['inventory']['semanticSideEntries'])
        self.assertEqual(engine.qt_controls_profile.for_context('s1', 'nightmare').contract.floor_ids,
                         ('-1', '-2', '-3', '-4'))
        self.assertIsNone(engine.memory_store.portable_seed_path)
        self.assertFalse(engine.memory_store.sample_validator(np.zeros((8, 8, 3), np.uint8)))
        self.assertFalse(engine.memory_store.root.exists())
        display = prepare_display_layer(engine, self.source.ids[0], '-4', 'shared-structure')
        self.assertEqual(display['pixels'].shape, (8, 8, 4))
        self.assertEqual(display['displayKind'], 'shared-structure')
        engine.rank(np.zeros((1200, 1920, 3), np.uint8), floor='-4', budget_seconds=.05)
        scan = engine.last_s1_structure_scan
        self.assertEqual(set(scan['expectedMapIds']), MODE_LAYOUTS['nightmare'])
        self.assertEqual(set(scan['checkedMapIds']) | set(scan['unscannedMapIds']), MODE_LAYOUTS['nightmare'])
        self.assertEqual(scan['excludedMapIds'], [])
        self.assertFalse(scan['identityHardGatePassed'])
        self.assertFalse(scan['automaticSelectionAuthorized'])
        self.assertFalse(scan['liveAuthorization'])
        before = (self.resolver.root / 'catalog.json').read_bytes()
        self.assertTrue(self.service.activatePackage(self.key, 's1', 'nightmare'))
        self.wait()
        self.assertEqual((self.resolver.root / 'catalog.json').read_bytes(), before)
        self.assertEqual(self.changes, [True])
        self.assertIn('不可用', self.service.message)

    def test_other_context_or_controls_cannot_be_used_for_nightmare_base(self):
        before = (self.resolver.root / 'catalog.json').read_bytes()
        self.assertFalse(self.service.activatePackage(self.key, 's1', 'difficult'))
        self.assertFalse(self.service.activatePackage(self.key, 's0', 'nightmare'))
        # Observe saved component refs without selecting or changing their mode.
        from imported_resource_archive import inspect_archive
        refs = {ref.component_id: ref for ref in inspect_archive(self.source.output).component_refs}
        base = PackageView(self.resolver.root, refs['base'])
        controls = PackageView(self.resolver.root, refs['controls'])
        with self.assertRaisesRegex(ValueError, 'native-base-context-unsupported'):
            build_native_engine(base, controls, self.paths, season='s1', mode='difficult')
        other = controls_fixture.ProfilePackage(
            self.paths.installation_root / 'difficult-controls', controls_fixture.document(('difficult',)))
        with self.assertRaisesRegex(ImportedProviderUnavailable, 'controls-base-floors-mismatch'):
            build_native_engine(base, other.view, self.paths, season='s1', mode='nightmare')
        self.assertEqual((self.resolver.root / 'catalog.json').read_bytes(), before)
        self.assertEqual(self.changes, [True])


if __name__ == '__main__':
    unittest.main()
