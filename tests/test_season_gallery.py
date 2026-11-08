"""Season boundaries with temporary declarations and Qt workers, never live game IO."""
import os
import sys
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / 'app'), str(ROOT / 'experiments/qt_quick')]
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
from map_resource_context import load_browse_context

try:
    from PySide6.QtGui import QGuiApplication
    from PySide6.QtQuick import QQuickWindow
    from backend import Backend
    from client_info import ClientInfoService
    from client_reference_presentation import ReferencePreviewProvider
    from client_resources import ResourceService
    QT = True
except ImportError:
    QT = False


class BrowseSeasons(unittest.TestCase):
    def test_s1_never_reads_s0_metadata_in_either_difficulty(self):
        with tempfile.TemporaryDirectory() as folder, patch.object(Path, 'read_text', side_effect=AssertionError('S0 read')):
            for mode in ('nightmare', 'difficult'):
                context = load_browse_context(Path(folder), mode, season='s1')
                self.assertEqual(context.season, 's1')
                self.assertIn('资源待补齐', context.error)
                self.assertIsNone(context.pack)
                self.assertIsNone(context.catalog)
                self.assertIsNone(context.model)
                self.assertEqual(context.profiles, ())
                self.assertEqual(dict(context.path_stamps), {})

    def test_unknown_season_is_not_legacy_default(self):
        with tempfile.TemporaryDirectory() as folder:
            self.assertIn('unknown browse season', load_browse_context(Path(folder), 'nightmare', season='s9').error)


@unittest.skipUnless(QT, 'PySide6 required')
class GallerySeasons(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QGuiApplication.instance() or QGuiApplication([])

    def wait(self, predicate):
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            self.app.processEvents()
            if predicate():
                return
            time.sleep(.002)
        self.fail('worker timeout')

    def backend(self, root):
        paths = SimpleNamespace(installation_root=root, resource_root=root)
        with patch('backend.runtime_paths', return_value=paths), patch.object(Backend, 'refresh'):
            backend = Backend(root)
        self.addCleanup(backend.shutdown)
        return backend

    def test_browse_switch_clears_preview_and_rejects_late_s0_result(self):
        with tempfile.TemporaryDirectory() as folder:
            backend = self.backend(Path(folder))
            jobs = []
            backend._submit = lambda fn, callback: jobs.append((fn, callback))
            backend.refresh()
            backend._profile = 'old-author'
            backend._selected = 's0/nightmare/old-author/shared-map/v1/1'
            backend._selection = {'imageUrl': 'old-s0'}
            backend.setSeason('s1')
            self.assertEqual(backend.selectedImage, '')
            self.assertEqual(backend.profiles, [])
            self.assertTrue(backend.viewKey.startswith('s1/nightmare/'))
            jobs[0][1](SimpleNamespace(error='', profiles=()), None)
            self.assertEqual(backend.stale_results, 1)
            self.assertEqual(backend.selectedImage, '')
            jobs[1][1](load_browse_context(Path(folder), 'nightmare', season='s1'), None)
            self.assertIn('资源待补齐', backend.message)
            self.assertEqual(backend.model.rows, [])
            self.assertEqual(backend.preview_model.rows, [])
            backend.setSeason('s0')
            self.assertEqual(backend.profileId, 'old-author')

    def test_combined_context_switch_has_one_generation_and_no_phantom_view(self):
        with tempfile.TemporaryDirectory() as folder:
            backend = self.backend(Path(folder))
            backend._submit = Mock()
            backend._profile = 'author'
            backend._selected = 's0/nightmare/author/map/v1/1'
            original_view = backend.viewKey
            other_view = 's0/difficult/other//'
            backend._mode_profiles[('s0', 'difficult')] = 'other'
            backend._selections[other_view] = 'retained-difficult-selection'
            left = []
            backend.viewChanging.connect(left.append)
            backend.setContext('s1', 'difficult')
            self.assertEqual(left, [original_view])
            self.assertEqual(backend._generation, 1)
            self.assertEqual(backend._submit.call_count, 1)
            self.assertEqual(backend._selections[other_view], 'retained-difficult-selection')
            backend.setContext('s0', 'nightmare')
            self.assertEqual(backend.selectedKey, 's0/nightmare/author/map/v1/1')
            self.assertEqual(backend._selections[other_view], 'retained-difficult-selection')
            backend.setContext('s0', 'nightmare')
            self.assertEqual(backend._generation, 2)

    def test_same_map_ids_have_distinct_season_row_and_view_keys(self):
        from map_resource_model import ResourceMap, ResourceVariant
        with tempfile.TemporaryDirectory() as folder:
            backend = self.backend(Path(folder))
            path = Path(folder) / 'fixture.png'
            profile = SimpleNamespace(profile_id='author', maps=(ResourceMap('same-map', 'Map', (ResourceVariant('v1', 'Route', {'1':path}),)),))
            backend.context = SimpleNamespace(profiles=(profile,), path_stamps={path:(1,1)})
            backend._profile = 'author'
            backend._apply_rows()
            s0key, s0view = backend.model.rows[0]['key'], backend.viewKey
            # Synthetic same-ID fixture checks the identity contract, not S1 availability.
            backend._season = 's1'
            backend._apply_rows()
            self.assertNotEqual(s0key, backend.model.rows[0]['key'])
            self.assertNotEqual(s0view, backend.viewKey)

    def test_s1_saved_startup_applied_and_desired_context_agree(self):
        from client_coordinator import ClientCoordinator
        from client_live_runtime import LiveRuntime
        from client_session import SessionService
        values = dict(gameSeason='s1', gameMode='difficult', routeProfileId='old-s0-author',
                      modeRouteProfiles={'difficult':'old-s0-author'}, contextRouteProfiles={})
        mode, profile, season = ClientCoordinator._settings_context(values)
        runtime = LiveRuntime(engine_factories={})
        runtime.configure_settings(values)
        runtime.dispatch('set_context', dict(season=season, mode=mode, profileId=profile))
        session = SessionService(runtime)
        try:
            self.assertEqual((mode, profile, season), ('difficult', '', 's1'))
            self.assertEqual((session.appliedMode, session.appliedProfile, session.appliedSeason), (mode, profile, season))
            self.assertEqual((runtime.snapshot()['mode'], runtime.snapshot()['profileId'], runtime.snapshot()['season']), (mode, profile, season))
        finally:
            session.shutdown()

    def test_s1_home_preview_never_falls_back_to_structure(self):
        with tempfile.TemporaryDirectory() as folder, patch('map_resource_overview.structure_previews', side_effect=AssertionError('S0 fallback')):
            for mode in ('nightmare', 'difficult'):
                self.assertEqual(ClientInfoService._home_rows(Path(folder), mode, 'shared-structure', 's1'), [])

    def test_s1_position_preview_does_not_construct_legacy_engine(self):
        factory = Mock(side_effect=AssertionError('S0 factory'))
        service = ReferencePreviewProvider({'nightmare': factory}, display_loader=Mock())
        try:
            service.request('nightmare', 'author', season='s1', context=('s1','nightmare'))
            self.wait(lambda: not service.busy)
            self.assertIn('资源待补齐', service.snapshot['message'])
            factory.assert_not_called()
        finally:
            service.close()
            service.pool.waitForDone()

    def test_s1_import_rejected_before_any_archive_read(self):
        with tempfile.TemporaryDirectory() as folder:
            backend = self.backend(Path(folder))
            backend._season = 's1'
            with patch('backend.inspect_package', side_effect=AssertionError('archive read')):
                backend.importArchive(str(Path(folder) / 's0.zip'))
            self.assertIn('资源待补齐', backend.message)
            self.assertFalse(backend.importing)

if QT:
    from copy import deepcopy
    from PySide6.QtCore import Property, QObject, Signal, Slot
    from test_client_control_actions import Settings, Coordinator
    class CommittedSettings(QObject):
        changed = Signal()
        seasonOptions = Property("QVariantList", lambda self: __import__("season_context").season_options(), constant=True)
        def __init__(self):
            super().__init__()
            self.data = dict(interfaceOpacity=1.0, sheetVisibilityEnabled=True, referenceSheetVisible=True,
                hotkey='M', actionHotkeys=['F9','F6','F7','F8','F4','F12','F11','F10','F10'],
                gameControlLayout=dict(order=['mode'], enabled=['mode'], display={}))
        values = Property('QVariantMap', lambda s: deepcopy(s.data), notify=changed)
        persistedValues = Property('QVariantMap', lambda s: deepcopy(s.data), notify=changed)
        busy = Property(bool, lambda s: False, constant=True)
    class ContextCoordinator(Coordinator):
        @Slot(str, str)
        def requestGameContext(self, season, mode):
            self.requests.append((season, mode))
    class Services(QObject):
        changed = Signal()
        startupAnnouncement = Signal('QVariantMap')
        busy = Property(bool, lambda s: False, constant=True)
        currentMode = Property(str, lambda s: 'nightmare', constant=True)
        currentSeason = Property(str, lambda s: 's0', constant=True)
        appliedSeason = Property(str, lambda s: 's0', constant=True)
        appliedMode = Property(str, lambda s: 'nightmare', constant=True)
        snapshot = Property('QVariantMap', lambda s: dict(commands={'start_round':{'available':False}}, roundActive=False, roundPreparing=False), constant=True)
        homePreviews = Property('QVariantList', lambda s: [], constant=True)
        gameWindows = Property('QVariantList', lambda s: [], constant=True)
        version = Property(str, lambda s: 'fixture', constant=True)
        # Match the real info service's pre-fetch announcement contract.  This
        # fixture exercises season layout; it does not fetch an update manifest.
        release = Property('QVariantMap', lambda s: {}, constant=True)
        updatePhase = Property(str, lambda s: 'idle', constant=True)
        hasUnread = Property(bool, lambda s: False, constant=True)
        checking = Property(bool, lambda s: False, constant=True)
        active = Property(bool, lambda s: False, constant=True)
        inviteVisible = Property(bool, lambda s: False, constant=True)
        boundWindow = Property(str, lambda s: '', constant=True)
        inputEnabled = Property(bool, lambda s: False, constant=True)
        runtimeStatus = Property(str, lambda s: '资源待补齐', constant=True)
        message = Property(str, lambda s: '', constant=True)
        @Slot(str, str)
        def requestGameContext(self, season, mode):
            self.requests.append((season, mode))
    HomeCommittedSettings = CommittedSettings

@unittest.skipUnless(QT, 'PySide6 required')
class SeasonSelectorLayout(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QGuiApplication.instance() or QGuiApplication([])

    def test_controls_season_dropdown_and_difficulties_fit_real_layout(self):
        from copy import deepcopy
        from PySide6.QtCore import Property, QObject, QUrl, QPointF, QPoint, Qt, Slot
        from PySide6.QtQml import QQmlApplicationEngine
        from PySide6.QtTest import QTest
        from test_client_control_stability import State
        from test_client_control_actions import Settings, Coordinator
        settings, state = CommittedSettings(), State()
        settings.data['gameControlLayout']['enabled'] = ['mode']
        state.data.update(roundActive=False, roundPreparing=False, mapOpen=False, groupId='', identityPhase='IDLE')
        coordinator = ContextCoordinator(settings)
        coordinator.requests = []
        engine = QQmlApplicationEngine()
        warnings = []
        engine.warnings.connect(lambda rows: warnings.extend(map(str, rows)))
        windows = QObject()
        for name, obj in dict(settingsService=settings, session=state, clientCoordinator=coordinator, windows=windows).items():
            engine.rootContext().setContextProperty(name, obj)
        engine.load(QUrl.fromLocalFile(str(ROOT/'experiments/qt_quick/ClientControls.qml')))
        self.assertTrue(engine.rootObjects(), warnings)
        window = engine.rootObjects()[0]
        try:
            window.show()
            QTest.qWait(30)
            def walk(item):
                yield item
                for child in item.childItems():
                    yield from walk(child)
            items = {item.objectName(): item for item in walk(window.contentItem())}
            names = ['controlGameContextSeason', 'controlModeDifficult', 'controlModeNightmare']
            buttons = [items[name] for name in names]
            for button in buttons:
                self.assertTrue(button.isVisible())
                self.assertTrue(button.isEnabled())
                top = button.mapToScene(QPointF(0, 0))
                bottom = button.mapToScene(QPointF(button.width(), button.height()))
                self.assertGreaterEqual(top.x(), 0)
                self.assertGreaterEqual(top.y(), 0)
                self.assertLessEqual(bottom.x(), window.width())
                self.assertLessEqual(bottom.y(), window.height())
            button = items['controlModeDifficult']
            point = button.mapToScene(QPointF(button.width()/2, button.height()/2))
            QTest.mouseClick(window, Qt.LeftButton, Qt.NoModifier, QPoint(round(point.x()), round(point.y())))
            self.assertEqual(coordinator.requests, [('s0','difficult')])
            picker = items['controlGameContextSeason']
            self.assertEqual(picker.property('count'), 2)
            def click_inside_window(item):
                point = item.mapToScene(QPointF(item.width()/2, item.height()/2))
                self.assertGreaterEqual(point.x(), 0)
                self.assertGreaterEqual(point.y(), 0)
                self.assertLess(point.x(), window.width())
                self.assertLess(point.y(), window.height())
                QTest.mouseClick(window, Qt.LeftButton, Qt.NoModifier, QPoint(round(point.x()), round(point.y())))
                QTest.qWait(20)
            def popup_visible():
                from PySide6.QtQml import QQmlExpression
                expression = QQmlExpression(engine.rootContext(), picker, "popup.visible")
                result = expression.evaluate()
                self.assertFalse(expression.hasError())
                return result[0] if isinstance(result, tuple) else result
            # Use this production WindowDoesNotAcceptFocus tool window, not
            # a normal-window surrogate of the shared selector.
            self.assertTrue(window.flags() & Qt.WindowDoesNotAcceptFocus)
            click_inside_window(picker)
            self.assertTrue(popup_visible())
            option = next(item for item in walk(window.contentItem())
                          if item.objectName() == 'controlGameContextSeasonOption_s1')
            click_inside_window(option)
            self.assertEqual(coordinator.requests[-1], ('s1','nightmare'))
            self.assertFalse(popup_visible())
            # The stub owner has not acknowledged the request, so S0 remains.
            self.assertEqual(picker.property('displayText'), 'S0 · 厄运之女')
            state.data['roundActive'] = True
            state.changed.emit()
            self.app.processEvents()
            count = len(coordinator.requests)
            self.assertFalse(picker.isEnabled())
            click_inside_window(picker)
            self.assertFalse(popup_visible())
            self.assertEqual(len(coordinator.requests), count)
            self.assertFalse(window.grabWindow().isNull())
            window.grabWindow().save('/tmp/season-controls-offscreen.png')
            self.assertFalse(warnings, '\n'.join(warnings))
        finally:
            window.close()
            from PySide6.QtCore import QCoreApplication, QEvent
            engine.deleteLater()
            QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
            self.app.processEvents()

    def test_shell_route_options_binding_excludes_s1_custom_and_busy_rows(self):
        from PySide6.QtCore import QUrl, QCoreApplication, QEvent
        from PySide6.QtQml import QQmlEngine, QQmlComponent
        source = (ROOT/'experiments/qt_quick/Client.qml').read_text(encoding='utf-8')
        start = source.index('    readonly property var routeOptions:')
        end = source.index('    Connections {', start)
        # Evaluate the production derived binding in Qt with only its two inputs.
        # This is not a full-shell or game test.
        binding = source[start:end]
        qml = ('import QtQuick\nQtObject { '
            'property QtObject routeChoices: QtObject { property string season: "s0"; property string mode: "nightmare"; '
            'property bool busy: false; property var profiles: [] } '
            'property QtObject settingsService: QtObject { property var values: ({gameSeason:"s0",gameMode:"nightmare"}) } '
            'property QtObject customGalleryService: QtObject { property var snapshot: ({available:true,galleries:[{id:"mine",name:"Mine"}]}) } '
            + binding + '}')
        engine = QQmlEngine()
        component = QQmlComponent(engine)
        component.setData(qml.encode(), QUrl())
        item = component.create()
        self.assertIsNotNone(item, [str(e) for e in component.errors()])
        try:
            self.assertEqual(item.property('routeOptions').toVariant(), [{'id':'custom:mine','name':'Mine'}])
            choices = item.property('routeChoices')
            choices.setProperty('season', 's1')
            self.assertEqual(item.property('routeOptions').toVariant(), [])
            settings = item.property('settingsService')
            settings.setProperty('values', {'gameSeason':'s1', 'gameMode':'nightmare'})
            self.assertEqual(item.property('routeOptions').toVariant(), [])
            choices.setProperty('season', 's0')
            settings.setProperty('values', {'gameSeason':'s0', 'gameMode':'nightmare'})
            self.assertEqual(item.property('routeOptions').toVariant(), [{'id':'custom:mine','name':'Mine'}])
            choices.setProperty('busy', True)
            self.assertEqual(item.property('routeOptions').toVariant(), [])
            choices.setProperty('busy', False)
            choices.setProperty('mode', 'difficult')
            self.assertEqual(item.property('routeOptions').toVariant(), [])
        finally:
            item.deleteLater()
            engine.deleteLater()
            QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)

    def test_home_season_selector_renders_at_narrow_and_wide_widths(self):
        from PySide6.QtCore import Property, QObject, Signal, Slot, QUrl, QPointF, QPoint, Qt
        from PySide6.QtQml import QQmlEngine, QQmlComponent
        from PySide6.QtQuick import QQuickWindow
        from PySide6.QtTest import QTest
        from test_client_control_actions import Settings
        service, settings = Services(), HomeCommittedSettings()
        service.requests = []
        engine = QQmlEngine()
        warnings = []
        engine.warnings.connect(lambda rows: warnings.extend(map(str, rows)))
        for name in ('session','resourceService','infoService','clientCoordinator','tutorialService','onboardingService'):
            engine.rootContext().setContextProperty(name, service)
        engine.rootContext().setContextProperty('settingsService', settings)
        component = QQmlComponent(engine, QUrl.fromLocalFile(str(ROOT/'experiments/qt_quick/ClientHome.qml')))
        page = component.create()
        self.assertIsNotNone(page, [str(e) for e in component.errors()])
        window = QQuickWindow()
        page.setParentItem(window.contentItem())
        try:
            window.show()
            selector = page.findChild(QObject, 'homeGameContextSeason')
            self.assertIsNotNone(selector)
            self.assertEqual(selector.property('count'), 2)
            for width in (540, 1100):
                window.resize(width, 900)
                page.setWidth(width)
                page.setHeight(900)
                QTest.qWait(30)
                self.assertTrue(selector.isVisible())
                point = selector.mapToScene(QPointF())
                self.assertGreaterEqual(point.x(), 0)
                self.assertLessEqual(point.x()+selector.width(), width)
                self.assertLessEqual(point.y()+selector.height(), window.height())
                self.assertFalse(window.grabWindow().isNull())
                window.grabWindow().save(f'/tmp/season-home-{width}-offscreen.png')
            selector.forceActiveFocus(Qt.OtherFocusReason)
            QTest.keyClick(window, Qt.Key_Down)
            self.app.processEvents()
            self.assertEqual(service.requests[-1], ('s1', 'nightmare'))
            self.assertFalse(warnings, '\n'.join(warnings))
        finally:
            window.close()
            page.setParentItem(None)
            page.deleteLater()
            window.deleteLater()
            from PySide6.QtCore import QCoreApplication, QEvent
            engine.deleteLater()
            QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
            self.app.processEvents()
