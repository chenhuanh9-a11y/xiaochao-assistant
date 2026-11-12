"""Independent test identity and real local services; no game or installed data."""
import io
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / p) for p in ('app', 'tools', 'tests', 'experiments/qt_quick')]
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
from PySide6.QtGui import QGuiApplication
import client_info
import client_settings
from client_paths import WORK_ROOT
import packaged_product_identity as identity
import qt_startup_diagnostics as diagnostics
import s1_candidate_purpose as purpose
from test_qt_startup_failures import StartupFixture
import test_s1_locked_identity as fixture
import test_s1_playtest_round_lock as shared
from update_service import parse_release


def grid_profile():
    return dict(product='XingtuQtClientTest', installerInstance=dict(
        id='grid-test', product='XingtuQtClientTest',
        appId='2D739B42-E6AC-4C37-9498-95DC9CC9AA43'))


class GridIdentityTests(StartupFixture):
    def test_baked_grid_identity_uses_marked_private_data_and_distinct_taskbar_identity(self):
        self.bake(grid_profile())
        paths = self.load_paths()
        self.assertEqual(identity.baked_identity(require_qt=True), 'qt-isolated')
        self.assertEqual(identity.qt_application_model_id(), 'Xingtu.GridTest')
        self.assertTrue(paths.GRID_TEST)
        self.assertFalse(paths.PRODUCTION)
        sandbox = self.install / 'work/qt-client-runtime'
        self.assertEqual(paths.DEFAULT_SANDBOX, sandbox)
        self.assertFalse(sandbox.exists())
        with self.assertRaisesRegex(ValueError, 'own marked work directory'):
            paths.runtime_paths()
        sandbox.mkdir(parents=True)
        (sandbox / '.qt-lab-owner').write_text('qt-quick-isolated-v1', encoding='utf-8')
        self.assertEqual(paths.runtime_paths().settings_path, sandbox / 'ui-settings.json')
        outside = self.root / 'formal-data'
        outside.mkdir()
        (outside / '.qt-lab-owner').write_text('qt-quick-isolated-v1', encoding='utf-8')
        with self.assertRaisesRegex(ValueError, 'own marked work directory'):
            paths.runtime_paths(outside)

    def test_appid_mismatch_cannot_select_grid_test(self):
        profile = grid_profile()
        profile['installerInstance']['appId'] = identity.PRODUCTION_APP_ID
        self.bake(profile)
        with self.assertRaises(identity.InstallationProfileError):
            self.load_paths()

    def test_grid_round_capability_has_no_implicit_private_diagnostic_permission(self):
        self.bake(grid_profile())
        value = purpose.packaged_native_candidate_purpose()
        self.assertTrue(purpose.native_candidate_purpose_is_valid(value))
        self.assertTrue(purpose.native_round_lock_purpose_is_valid(value))
        self.assertTrue(purpose.native_final_round_lock_purpose_is_valid(value))
        self.assertFalse(purpose.private_native_candidate_purpose_is_valid(value))
        self.assertIsNone(purpose.production_native_candidate_purpose())

    def test_failed_test_startup_cannot_replace_formal_fallback_diagnostic(self):
        self.bake(grid_profile())
        (self.install / 'work').write_text('owned obstruction', encoding='utf-8')
        local = self.root / 'local'
        formal = local / 'Xingtu/diagnostics' / diagnostics.LOG_NAME
        formal.parent.mkdir(parents=True)
        formal.write_bytes(b'formal diagnostic sentinel')
        with patch.dict(os.environ, LOCALAPPDATA=str(local)), patch.object(sys, 'stderr', io.StringIO()):
            result = diagnostics.report_startup_failure(ImportError('grid-test startup fixture'), show_dialog=False)
        self.assertEqual(result, local / 'Xingtu/qt-test/diagnostics' / diagnostics.LOG_NAME)
        self.assertTrue(result.is_file())
        self.assertEqual(formal.read_bytes(), b'formal diagnostic sentinel')
        self.assertIn('QT-STARTUP-', result.read_text(encoding='utf-8'))


class GridInfoSettingsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QGuiApplication.instance() or QGuiApplication([])

    def test_real_services_keep_sampling_off_and_never_fetch_or_open_formal_update(self):
        WORK_ROOT.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(prefix='grid-test-services-', dir=WORK_ROOT) as temporary:
            root = Path(temporary)
            (root / '.qt-lab-owner').write_text('qt-quick-isolated-v1', encoding='utf-8')
            raw = b'{"automaticFailureEvidence":true}'
            (root / 'ui-settings.json').write_bytes(raw)
            fetched, opened, sampling = [], [], []
            with patch.object(client_settings, 'GRID_TEST', True), patch.object(client_info, 'GRID_TEST', True):
                settings = client_settings.SettingsService(root)
                info = client_info.ClientInfoService(root, fetch=lambda url: fetched.append(url),
                    open_url=lambda url: opened.append(url) or True)
                try:
                    self.assertTrue(settings.reload())
                    self.assertFalse(settings.persistedValues['automaticFailureEvidence'])
                    runtime = SimpleNamespace(set_automatic_failure_sampling_enabled=sampling.append)
                    info.setProviders(settings=settings, coordinator=SimpleNamespace(runtime=runtime))
                    self.assertFalse(info.scheduleStartupCheck())
                    info.checkUpdates()
                    info.openAnnouncement()
                    cached = parse_release(dict(schemaVersion=1, platform='windows', version='9.1',
                        versionCode=91, releaseNotes='fixture', publishedAt='2026-11-10', size=100,
                        downloadPageUrl='https://pan.quark.cn/s/example', ucDownloadPageUrl='https://drive.uc.cn/s/example'))
                    info._service.latest = cached
                    info._announcements[cached.announcement_id] = cached
                    info.openDownloadPage('quark')
                    info.openAnnouncementDownload(cached.announcement_id, 'quark')
                    self.app.processEvents()
                    self.assertEqual(fetched, [])
                    self.assertEqual(opened, [])
                    self.assertIn('独立网格测试包', info.message)
                    self.assertIn('C# 网格独立测试', info.facts['version'])
                    self.assertFalse(info.setAutomaticFailureSampling(True))
                    self.assertFalse(info.automaticFailureSampling)
                    self.assertTrue(sampling)
                    self.assertFalse(any(sampling))
                    self.assertEqual((root / 'ui-settings.json').read_bytes(), raw)
                    self.assertFalse(info._startup_timer.isActive())
                finally:
                    info.shutdown()
                    settings.shutdown()
                    self.app.processEvents()


class GridTestRoundLockContracts(shared.PlaytestRoundLockContracts):
    def setUp(self):
        with tempfile.TemporaryDirectory() as temporary:
            Path(temporary, 'installation-profile.json').write_text(json.dumps(grid_profile()), encoding='utf-8')
            with patch.object(sys, 'frozen', True, create=True), patch.object(sys, '_MEIPASS', temporary, create=True):
                self.candidate_purpose = purpose.packaged_native_candidate_purpose()
        fixture.LockedIdentityContracts.setUp(self)


class DifficultGridTestRoundLockContracts(GridTestRoundLockContracts):
    def setUp(self):
        from dataclasses import replace
        super().setUp()
        self.registry.engine.pack.mode = 'difficult'
        self.registry.catalog = replace(self.registry.catalog, mode='difficult')
        self.start_patch(patch('s1_structure_selection.StructureCatalog.from_engine', return_value=self.registry.catalog))
        self.assertTrue(self.runtime.dispatch('set_context', dict(season='s1', mode='difficult'))['success'])
