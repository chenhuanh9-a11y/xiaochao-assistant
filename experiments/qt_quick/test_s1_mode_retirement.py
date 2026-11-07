"""Synthetic runtime and temporary settings only; no game or user installation."""
import json
import unittest
from unittest.mock import patch
from season_context import CONTEXTS, modes_for_season, known_modes_for_season, season_options
from client_engine import EngineAdapter
from client_providers import build_engine_factories, ProviderUnavailable
from client_live_runtime import LiveRuntime
from test_client_settings import SettingsTests
from test_client_live_runtime import FixtureCapture, fixture_engine

class RetirementTests(unittest.TestCase):
    def test_selector_catalog_and_history_are_separate(self):
        self.assertEqual(modes_for_season('s1'), ('nightmare', 'difficult'))
        self.assertIn('difficulty2', known_modes_for_season('s1'))
        self.assertFalse(CONTEXTS['s1:difficulty2'].registered)
        self.assertNotIn('difficulty2', season_options()[1]['capabilities'])

    def test_qml_hides_runtime_ordinary_but_retains_maintenance_browse(self):
        from pathlib import Path
        from PySide6.QtCore import QUrl
        from PySide6.QtGui import QGuiApplication
        from PySide6.QtQml import QQmlEngine, QQmlComponent
        app = QGuiApplication.instance() or QGuiApplication([])
        engine = QQmlEngine()
        component = QQmlComponent(engine, QUrl.fromLocalFile(str(Path(__file__).with_name('SeasonSelector.qml'))))
        self.assertFalse(component.isError(), component.errors())
        item = component.createWithInitialProperties(dict(seasons=season_options(), season='s1', mode='difficulty2'))
        self.assertIsNotNone(item)
        self.assertFalse(item.property('ordinaryAvailable'))
        requests = []
        item.contextRequested.connect(lambda *args: requests.append(args))
        item.requestContext('s1', 'difficulty2')
        self.assertFalse(requests)
        item.requestContext('s1', 'difficult')
        self.assertEqual(requests, [('s1', 'difficult')])
        item.setProperty('browseDifficulty2', True)
        self.assertTrue(item.property('ordinaryAvailable'))
        item.deleteLater()

    def test_provider_never_builds_retired_engine(self):
        registry = build_engine_factories('missing-synthetic-root')
        with patch.object(registry, '_build', side_effect=AssertionError('must not build')):
            report = registry.prepare_context('difficulty2', season='s1')['difficulty2']
            self.assertEqual(report['reason'], 'retired-mode')
            self.assertFalse(report['available'])
            with self.assertRaisesRegex(ProviderUnavailable, 'retired-mode'):
                registry.get_engine('difficulty2', 's1')

    def test_saved_legacy_context_cannot_start_or_capture_and_can_recover(self):
        capture = FixtureCapture()
        runtime = LiveRuntime(engine_factories={'nightmare': lambda: fixture_engine()},
                              capture_provider=capture, evidence_kind='synthetic')
        self.assertTrue(runtime.dispatch('set_context', dict(season='s1', mode='difficulty2'))['success'])
        state = runtime.snapshot()
        self.assertEqual((state['season'], state['mode']), ('s1', 'difficulty2'))
        self.assertNotIn('start_round', state['availableCommands'])
        for action in ('start_round', 'toggle_round', 'capture_now', 'rank_saved_frame', 'prepare_manual_alignment'):
            result = runtime.dispatch(action)
            self.assertFalse(result['success'], result)
            self.assertEqual(result['reason'], 'retired-mode')
        self.assertEqual(capture.enable_count, 0)
        self.assertFalse(runtime.snapshot()['roundActive'])
        self.assertTrue(runtime.dispatch('set_context', dict(season='s0', mode='nightmare'))['success'])
        self.assertIn('start_round', runtime.snapshot()['availableCommands'])

class PreferenceRetirementTests(SettingsTests):
    def test_new_settings_selection_cannot_enable_retired_mode(self):
        service = self.service()
        self.assertTrue(service.setValue('gameSeason', 's1'))
        self.assertFalse(service.setValue('gameMode', 'difficulty2'))
        self.assertEqual(service.values['gameMode'], 'nightmare')

    def test_retired_preference_is_not_rewritten_by_read_or_unrelated_save(self):
        original = dict(gameSeason='s1', gameMode='difficulty2', contextRouteProfiles={'s1:difficulty2': 'keep-author'})
        self.write(original)
        service = self.service()
        self.assertEqual(service.persistedValues['gameMode'], 'difficulty2')
        self.assertIn('已退役', service.message)
        self.assertEqual(json.loads(self.path.read_text()), original)
        service.setValue('sheetScale', 1.2)
        self.assertTrue(service.saveFields(['sheetScale']))
        self.wait(lambda: not service.busy)
        saved = json.loads(self.path.read_text())
        self.assertEqual(saved['gameMode'], 'difficulty2')
        self.assertEqual(saved['contextRouteProfiles']['s1:difficulty2'], 'keep-author')
