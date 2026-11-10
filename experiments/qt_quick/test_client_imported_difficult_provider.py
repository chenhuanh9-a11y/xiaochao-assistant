"""S0D import -> Session lease -> catalog -> data engine, with ranking closed."""
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch

import numpy as np
from PySide6.QtCore import QCoreApplication, QUrl

from client_imported_providers import SUPPORTED_CAPABILITIES, build_imported_engine_factories
from client_imported_resources import ImportedResourceService
from client_live_runtime import LiveRuntime
from client_paths import RuntimePaths
from client_providers import ProviderUnavailable
from client_route_preferences import prepare_display_layer
from client_session import SessionService
from imported_resource_catalog import ResourceResolver
from test_client_live_runtime import FixtureCapture
import test_export_s0_difficult_native_base_package as source_fixture


class ImportedDifficultProviderTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QCoreApplication.instance() or QCoreApplication([])
        temporary = tempfile.TemporaryDirectory(prefix='s0d-provider-source-')
        cls.addClassCleanup(temporary.cleanup)
        cls.source = source_fixture.S0DifficultSourceFixture(Path(temporary.name))
        cls.source.export()

    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix='s0d-provider-active-')
        self.addCleanup(temporary.cleanup)
        self.paths = RuntimePaths(Path(temporary.name).resolve(), False)
        self.resolver = ResourceResolver(self.paths.imported_resource_root, supported_capabilities=SUPPORTED_CAPABILITIES)
        self.factories = build_imported_engine_factories(self.resolver, self.paths)
        self.capture = FixtureCapture()
        self.runtime = LiveRuntime(engine_factories=self.factories, capture_provider=self.capture, evidence_kind='synthetic')
        self.runtime.dispatch('set_context', dict(season='s0', mode='difficult'))
        self.session = SessionService(self.runtime)
        self.service = ImportedResourceService(self.resolver, paths=self.paths)
        self.service.setLeaseProvider(self.session)
        self.errors, self.changes = [], []
        self.service.errorReported.connect(self.errors.append)
        self.service.resourcesChanged.connect(lambda:self.changes.append(True))
        self.addCleanup(self.close)
        self.service.importArchive(QUrl.fromLocalFile(str(self.source.output)).toString())
        self.wait()
        self.assertFalse(self.errors, self.errors)
        self.key = self.service.packageRows[0]['key']

    def close(self):
        self.service.shutdown(); self.service.pool.waitForDone()
        self.session.shutdown(); self.session._pool.waitForDone()
        self.runtime.shutdown(); self.app.processEvents()

    def wait(self):
        deadline = time.monotonic()+90
        while self.service.busy and time.monotonic() < deadline:
            self.app.processEvents(); time.sleep(.005)
        self.assertFalse(self.service.busy, self.service.message)

    def activate(self):
        self.assertTrue(self.service.activatePackage(self.key, 's0', 'difficult'))
        self.assertEqual(self.session.acquireResourceLease(), '')
        self.wait()
        self.assertFalse(self.errors, self.errors)

    def test_service_and_default_factory_preserve_complete_data_without_rank_or_learning(self):
        row = self.service.packageRows[0]
        self.assertEqual(row['activatableContexts'], ['s0:difficult'])
        option = next(r for r in self.service.activationOptions if r['context'] == 's0:difficult')
        self.assertIn('识别功能尚未开放', option['notice'])
        model = self.service.packageModel
        self.activate()
        self.assertIs(self.service.packageModel, model)
        self.assertEqual(self.service.packageRows[0]['selectedContexts'], ['s0:difficult'])
        self.assertEqual(self.changes, [True])
        self.assertIn('识别功能尚未开放', self.service.message)
        with patch('client_providers.EngineFactories._build', side_effect=AssertionError('legacy builder entered')):
            engine = self.factories.get_engine('difficult')
        for _ in range(2):
            self.assertIs(self.factories.get_engine('difficult'), engine)
            report = self.factories.capabilities()['difficult']
            self.assertTrue(report['dataReady'], report)
            self.assertFalse(report['rankReady'], report)
            self.assertFalse(report['available'], report)
            self.assertFalse(report['liveReady'], report)
        self.assertEqual(set(engine.pack.by_id), set(source_fixture.IDS))
        self.assertEqual(report['inventory']['typedLayers'], 58)
        self.assertEqual(report['inventory']['legacyLayers'], 2)
        self.assertEqual(report['inventory']['mainEntries'], 30)
        self.assertEqual(report['inventory']['sideEntries'], 60)
        self.assertFalse(report['inventory']['semanticSideEntries'])
        self.assertEqual(engine.side_entry_identity.passport_error, 'entry-passports-source-unavailable')
        self.assertEqual(len(engine.side_entry_identity.passports), 2)
        self.assertEqual(len(engine.side_entry_identity.entry_coverage), 2)
        self.assertFalse(engine.memory_store.sample_validator(np.zeros((8,8,3),np.uint8)))
        self.assertIsNone(engine.memory_store.portable_seed_path)
        self.assertFalse(engine.memory_store.root.exists())
        self.assertFalse(hasattr(engine, 'qt_s1_structure_scan'))
        self.assertFalse(hasattr(engine, 'qt_s1_structure_rank'))
        for mid, floor, shape in ((source_fixture.IDS[0], '1', (8,8,4)),
                                   (source_fixture.LEGACY, '2', (6,10,4))):
            display = prepare_display_layer(engine, mid, floor, 'shared-structure')
            self.assertEqual(display['displayKind'], 'shared-structure')
            self.assertEqual(display['pixels'].shape, shape)
            np.testing.assert_array_equal(display['matrixDisplayToReference'], np.eye(3))
        with self.assertRaisesRegex(ValueError, 'native-base-rank-unavailable'):
            engine.rank(np.zeros((8,8,3),np.uint8), floor='1')
        with self.assertRaisesRegex(ProviderUnavailable, 'native-base-rank-unavailable'):
            self.runtime._get_engine('difficult', require_live=True)
        self.assertIs(self.runtime._get_engine('difficult', require_live=False), engine)
        self.assertFalse(self.runtime.dispatch('start_round')['success'])
        self.assertEqual(self.capture.enable_count, 0)
        self.assertEqual(self.capture.count, 0)

    def test_repeat_activation_keeps_generation_and_wrong_context_cannot_change_selection(self):
        self.activate()
        catalog = self.resolver.root/'catalog.json'
        original = catalog.read_bytes()
        self.activate()
        self.assertEqual(catalog.read_bytes(), original)
        self.assertEqual(self.changes, [True])
        self.assertIn('识别功能尚未开放', self.service.message)
        self.assertFalse(self.service.activatePackage(self.key, 's0', 'nightmare'))
        self.assertEqual(catalog.read_bytes(), original)


if __name__ == '__main__':
    unittest.main()
