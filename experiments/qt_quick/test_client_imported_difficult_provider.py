"""S0D default import/candidate flow; synthetic capture and labelled fit stubs."""
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch

import numpy as np
from PySide6.QtCore import QCoreApplication, QUrl

from client_imported_providers import SUPPORTED_CAPABILITIES, build_imported_engine_factories
from client_imported_resources import ImportedResourceService
from client_live_runtime import LiveRuntime, _AlignmentHost
from client_paths import RuntimePaths
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
        self.assertFalse(self.service.pendingWrites)
        deadline = time.monotonic()+90
        while not self.service.pendingWrites and self.service.busy and time.monotonic() < deadline:
            self.app.processEvents(); time.sleep(.005)
        self.assertTrue(self.service.pendingWrites, self.service.message)
        self.assertEqual(self.session.acquireResourceLease(), '')
        self.wait()
        self.assertFalse(self.errors, self.errors)

    def test_default_candidate_round_scans_both_floors_without_formal_rank_permission(self):
        self.activate()
        engine = self.factories.get_engine('difficult')
        state = self.runtime.snapshot()
        self.assertTrue(state['candidateStructureAvailable'], state.get('liveUnavailableReason'))
        self.assertFalse(state['liveEngineAvailable'])
        self.assertEqual(state['candidateSupportedFloors'], ['1', '2'])
        self.assertIn('start_round', state['availableCommands'])
        self.assertTrue(self.runtime.dispatch('start_round')['success'])
        reports = []
        scan = engine.qt_structure_scan
        def record_scan(*args, **kwargs):
            report = scan(*args, **kwargs)
            reports.append(report)
            return report
        for floor in ('1', '2'):
            with self.subTest(floor=floor), \
                 patch.object(self.runtime, '_observe', return_value=(
                     dict(detected=True, mapOpen=True), floor, dict(selectedFloor=int(floor), mapOpen=True))), \
                 patch.object(_AlignmentHost, 'fit', return_value=dict(success=False, reason='fixture-no-pose')) as fit, \
                 patch.object(engine, 'qt_structure_scan', side_effect=record_scan), \
                 patch.object(engine, 'rank', side_effect=AssertionError('formal rank entered')):
                result = self.runtime.dispatch('capture_now')
                self.assertEqual(result['reason'], 'identity-not-uniquely-verified', result)
                evidence = self.runtime.snapshot()['structureEvidence']
                checked = set(reports[-1]['checkedMapIds'])
                unscanned = set(evidence['currentUnscannedMapIds'])
                self.assertTrue(checked)
                self.assertEqual({call.args[1] for call in fit.call_args_list}, checked)
                self.assertEqual(checked | unscanned, set(source_fixture.IDS))
                self.assertFalse(checked & unscanned)
                self.assertEqual(set(evidence['possibleMapIds']), set(source_fixture.IDS))
                self.assertEqual(evidence['excludedMapIds'], [])
                self.assertEqual(evidence['currentStatus'], 'budget-unresolved' if unscanned else 'complete')
                self.assertFalse(self.runtime.snapshot()['identityLocked'])
                self.assertFalse(self.runtime.snapshot()['displayAuthorized'])
        with self.assertRaisesRegex(ValueError, 'native-base-rank-unavailable'):
            engine.rank(self.capture.frame, floor='1')

    def test_explicit_candidate_confirmation_recaptures_and_runs_real_solver(self):
        self.activate()
        self.factories.get_engine('difficult')
        self.assertTrue(self.runtime.dispatch('start_round')['success'])
        observe = (dict(detected=True, mapOpen=True), '2', dict(selectedFloor=2, mapOpen=True))
        proposal = dict(success=True, coordinateSpace='shared-structure-to-client',
            matrixSourceToClient=np.eye(3), selectedRouteCurrentFrameResidual=dict(
                verified=True, poseVerifiedAgainstSharedStructure=True, withinThreePixels=.95))
        with patch.object(self.runtime, '_observe', return_value=observe):
            # Only candidate fits are supplied. Explicit confirmation below
            # recaptures the black fixture and runs the real shared solver.
            with patch.object(_AlignmentHost, 'fit', return_value=proposal):
                self.runtime.dispatch('capture_now')
            state = self.runtime.snapshot()
            self.assertFalse(state['identityLocked'])
            self.assertFalse(state['displayAuthorized'])

            row = state['candidateRows'][0]
            selected = self.runtime.dispatch('select_candidate', dict(index=0,
                **{key: row[key] for key in ('mapId', 'structureFrameId',
                                            'structureGeneration', 'structureRoundId')}))
            self.assertTrue(selected['success'], selected)
            before = self.capture.count
            with patch.object(_AlignmentHost, 'fit', autospec=True, side_effect=_AlignmentHost.fit) as fit:
                result = self.runtime.dispatch('confirm_route', dict(mapId=row['mapId']))
            self.assertGreater(self.capture.count, before)
            self.assertTrue(fit.called, result)
            state = self.runtime.snapshot()
            self.assertEqual(state['mapId'], row['mapId'])
            self.assertEqual(state['identitySource'], 'manual')
            self.assertFalse(state['displayAuthorized'])

    def test_stream_eligibility_is_read_only_and_scanner_rejects_replaced_manifest(self):
        self.activate()
        engine = self.factories.get_engine('difficult')
        context = engine.qt_native_context
        manifest = context.root.parent.parent/'manifest.json'
        with patch.object(context, 'assert_current', wraps=context.assert_current) as check:
            for _ in range(20):
                self.assertTrue(self.runtime._observation_stream_enabled(engine))
            check.assert_not_called()
        replacement = manifest.with_suffix('.replacement')
        replacement.write_bytes(manifest.read_bytes())
        replacement.replace(manifest)
        with patch.object(_AlignmentHost, 'fit', side_effect=AssertionError('stale fit')) as fit:
            with self.assertRaises(ValueError):
                engine.qt_structure_scan(self.capture.frame, floor='1')
            fit.assert_not_called()

    def test_reference_lookup_owns_context_checks_and_rejects_mid_load_controls_change(self):
        self.activate()
        engine = self.factories.get_engine('difficult')
        context, provider = engine.qt_native_context, engine.qt_shared_provider
        for mid, expected_kind in ((source_fixture.IDS[0], 'game-rendered-shared-structure'),
                                   (source_fixture.LEGACY, 'legacy-shared-binary')):
            with self.subTest(map=mid), \
                 patch.object(context, 'assert_current', wraps=context.assert_current) as current:
                reference = provider('shared-structure', mid, '1')
                self.assertIsNotNone(reference)
                self.assertEqual(reference.metadata['sourceKind'], expected_kind)
                self.assertEqual(current.call_count, 2)  # Provider entry and return.
            with patch.object(context, 'assert_current', wraps=context.assert_current) as current:
                self.assertEqual(provider.binding_metadata('shared-structure', mid, '1')['path'], reference.path)
                self.assertEqual(current.call_count, 2)
        manifest = engine.qt_controls_profile._view.root.parent.parent/'manifest.json'
        load = provider.resources.load
        def change_controls(*args, **kwargs):
            reference = load(*args, **kwargs)
            self.assertIsNotNone(reference)
            replacement = manifest.with_suffix('.replacement')
            replacement.write_bytes(manifest.read_bytes())
            replacement.replace(manifest)
            return reference
        with patch.object(provider.resources, 'load', side_effect=change_controls):
            with self.assertRaises(ValueError):
                provider('shared-structure', source_fixture.IDS[1], '1')
        self.assertFalse(any(key[1] == source_fixture.IDS[1] for key in provider._cache))
        with self.assertRaises(ValueError):
            provider('shared-structure', source_fixture.IDS[0], '1')

    def test_service_and_default_factory_preserve_complete_data_without_rank_or_learning(self):
        row = self.service.packageRows[0]
        self.assertEqual(row['activatableContexts'], ['s0:difficult'])
        option = next(r for r in self.service.activationOptions if r['context'] == 's0:difficult')
        self.assertIn('地图需手动确认', option['notice'])
        model = self.service.packageModel
        self.activate()
        self.assertIs(self.service.packageModel, model)
        self.assertEqual(self.service.packageRows[0]['selectedContexts'], ['s0:difficult'])
        self.assertEqual(self.changes, [True])
        self.assertIn('地图需手动确认', self.service.message)
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
            self.assertEqual(display['displayLabel'], '历史结构草稿' if mid == source_fixture.LEGACY else '结构底图')
            self.assertFalse(display['fallback'])
            self.assertEqual(display['authorStatus'], 'none')
            self.assertEqual(display['fallbackReason'], '')
            self.assertEqual(display['pixels'].shape, shape)
            np.testing.assert_array_equal(display['matrixDisplayToReference'], np.eye(3))
        with self.assertRaisesRegex(ValueError, 'native-base-rank-unavailable'):
            engine.rank(np.zeros((8,8,3),np.uint8), floor='1')
        self.assertIs(self.runtime._get_engine('difficult', require_live=True), engine)
        self.assertIs(self.runtime._get_engine('difficult', require_live=False), engine)
        self.assertTrue(self.runtime.dispatch('start_round')['success'])
        self.assertEqual(self.capture.enable_count, 0)  # Prepared; no map input yet.
        self.assertEqual(self.capture.count, 0)

    def test_repeat_activation_keeps_generation_and_wrong_context_cannot_change_selection(self):
        self.activate()
        catalog = self.resolver.root/'catalog.json'
        original = catalog.read_bytes()
        self.activate()
        self.assertEqual(catalog.read_bytes(), original)
        self.assertEqual(self.changes, [True])
        self.assertIn('地图需手动确认', self.service.message)
        self.assertFalse(self.service.activatePackage(self.key, 's0', 'nightmare'))
        self.assertEqual(catalog.read_bytes(), original)


if __name__ == '__main__':
    unittest.main()
