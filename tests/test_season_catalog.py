"""Catalog configuration and synthetic startup contracts; no game or package IO."""
import copy
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / 'app'), str(ROOT / 'experiments/qt_quick')]
from season_catalog import SEASON_DEFINITIONS
from season_context import validate_catalog, season_options


class SeasonCatalogTests(unittest.TestCase):
    def test_confirmed_names_are_display_only_and_are_copied(self):
        rows = season_options()
        self.assertEqual([r['label'] for r in rows], ['S0 · 厄运之女', 'S1 · 磷光症'])
        self.assertTrue(rows[0]['capabilities']['nightmare']['registered'])
        self.assertTrue(rows[1]['capabilities']['nightmare']['registered'])
        self.assertEqual(rows[1]['capabilities']['nightmare']['status'], 'resource-validation-required')
        rows[0]['name'] = 'mutated'
        rows[0]['capabilities']['nightmare']['registered'] = False
        self.assertEqual(season_options()[0]['name'], '厄运之女')
        self.assertEqual([row['id'] for row in rows], ['s0', 's1'])
        self.assertTrue(season_options()[0]['capabilities']['nightmare']['registered'])

    def test_invalid_catalog_is_rejected_instead_of_defaulting_to_s0(self):
        base = copy.deepcopy(SEASON_DEFINITIONS)
        invalid = [[], [base[0], base[0]], [base[1]]]
        for field, value in [('id', '../s2'), ('id', 's01'), ('number', True), ('number', 4),
                             ('name', 'bad\nname'), ('name', None), ('available', True)]:
            row = dict(base[1]); row[field] = value
            invalid.append([base[0], row])
        row = dict(base[1], resources={'nightmare':'legacy-s0','difficult':'unregistered'})
        invalid.append([base[0], row])
        for definitions in invalid:
            with self.subTest(definitions=definitions), self.assertRaises(ValueError):
                validate_catalog(definitions)

    def test_added_s2_configuration_is_accepted_end_to_end_without_resource_fallback(self):
        # A fresh process imports a temporary edited data module before any consumer.
        # This models adding an entry to the bundled configuration, not live patching
        # a service whitelist or selecting an alternate resource root.
        with tempfile.TemporaryDirectory() as folder:
            definitions = list(copy.deepcopy(SEASON_DEFINITIONS)) + [dict(id='s2', number=2,
                name='Fixture / Future', resources={'nightmare':'unregistered','difficult':'unregistered'})]
            Path(folder, 'season_catalog.py').write_text('SEASON_DEFINITIONS = ' + repr(definitions), encoding='utf-8')
            script = r'''
import os, sys, tempfile, time
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch
from PySide6.QtGui import QGuiApplication
from season_context import SEASONS, CONTEXTS, normalize_season, season_options
from client_settings import SettingsService
from client_paths import RESOURCE_ROOT, RuntimePaths
from client_coordinator import ClientCoordinator
from client_live_runtime import LiveRuntime
from client_session import SessionService
from client_providers import EngineFactories, ProviderUnavailable
from client_engine import EngineAdapter
from client_custom_display import CustomGalleryDisplay
from backend import Backend
from map_resource_context import load_browse_context
app = QGuiApplication([])
assert normalize_season('s2') == 's2' and len(CONTEXTS) == 7
assert season_options()[-1]['label'] == 'S2 · Fixture / Future'
assert not season_options()[-1]['capabilities']['nightmare']['registered']
try: normalize_season('s9')
except ValueError: pass
else: raise AssertionError('unknown season accepted')
(RESOURCE_ROOT/'work').mkdir(exist_ok=True)
with tempfile.TemporaryDirectory(dir=RESOURCE_ROOT/'work') as folder:
    root = Path(folder); (root/'.qt-lab-owner').write_text('qt-quick-isolated-v1')
    settings = SettingsService(root)
    assert settings.seasonOptions[-1]['id'] == 's2'
    fake = SimpleNamespace(settings=settings, resources=SimpleNamespace(busy=False),
        _settings_context=ClientCoordinator._settings_context, _game_context_locked=lambda:False,
        _clear_reference=Mock(), _revoke_runtime=Mock())
    ClientCoordinator.requestGameContext(fake, 's2', 'nightmare')
    deadline = time.monotonic()+5
    while settings.busy and time.monotonic()<deadline: app.processEvents(); time.sleep(.002)
    assert settings.persistedValues['gameSeason'] == 's2'
    reloaded = SettingsService(root)
    assert reloaded.persistedValues['gameSeason'] == 's2'
    assert ClientCoordinator._settings_context(reloaded.persistedValues) == ('nightmare','','s2')
    paths = RuntimePaths(root,False)
    assert paths.memory_root('nightmare','s2') == root/'work/seasons/s2/nightmare-memory'
    with patch.object(Backend,'refresh'):
        backend = Backend(root); backend.setContext('s2','difficult')
        assert (backend.season,backend.mode) == ('s2','difficult')
        backend.shutdown()
    registry = EngineFactories(root/'_internal')
    with patch.object(registry,'_build',side_effect=AssertionError('S0 build')):
        for mode in ('nightmare','difficult'):
            try: registry.get_engine(mode,'s2')
            except ProviderUnavailable as error: assert 's2' in str(error)
            else: raise AssertionError('S2 engine available')
            assert not registry.capabilities('s2')[mode]['available']
            context = load_browse_context(root, mode, season='s2')
            assert context.season == 's2' and context.pack is None and 'S2' in context.error
    factory, capture = Mock(side_effect=AssertionError('S0 factory')), Mock()
    runtime = LiveRuntime(engine_factories={'nightmare':factory},capture_provider=capture)
    session = SessionService(runtime)
    assert session.reconfigure('nightmare','','s2')
    deadline=time.monotonic()+5
    while session.busy and time.monotonic()<deadline: app.processEvents(); time.sleep(.002)
    assert session.appliedSeason == runtime.snapshot()['season'] == 's2'
    receipt=runtime.dispatch('start_round')
    assert not receipt['success'] and 's2' in receipt['reason']
    factory.assert_not_called(); capture.capture.assert_not_called()
    offline=EngineAdapter(engine_factories={'nightmare':factory})
    offline.dispatch('set_context',{'season':'s2'})
    assert not offline.snapshot()['savedFrameEngineAvailable']
    assert 's2' in offline.dispatch('rank_saved_frame',{'path':'/must-not-read'})['reason']
    display=CustomGalleryDisplay(Mock())
    assert 's2' in display.eligibility('old','nightmare','s2')['reason']
    assert 'S2' in display.eligibility('old','nightmare','s2')['message']
    session.shutdown(); settings.shutdown(); reloaded.shutdown()
print('S2 configured startup passed')
'''
            env = dict(os.environ, QT_QPA_PLATFORM='offscreen', PYTHONPATH=os.pathsep.join(
                [folder, str(ROOT/'app'), str(ROOT/'experiments/qt_quick')]))
            run = subprocess.run([sys.executable, '-c', script], env=env, cwd=ROOT,
                                 text=True, capture_output=True, timeout=30)
            self.assertEqual(run.returncode, 0, run.stdout + run.stderr)
            self.assertIn('S2 configured startup passed', run.stdout)
            # Frozen import uses the same imported data module, no new external JSON path.
            frozen = subprocess.run([sys.executable, '-c',
                "import sys; sys.frozen=True; sys._MEIPASS='/missing-fixture'; "
                "from season_context import season_options; assert season_options()[-1]['id']=='s2'"],
                env=env, cwd=ROOT, text=True, capture_output=True, timeout=10)
            self.assertEqual(frozen.returncode, 0, frozen.stderr)


if __name__ == '__main__': unittest.main()
