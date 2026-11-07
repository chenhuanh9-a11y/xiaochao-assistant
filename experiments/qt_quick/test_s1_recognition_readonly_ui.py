"""Production QML, real mouse/key events, synthetic state; no live-game claim."""
from copy import deepcopy
from pathlib import Path
import unittest

from PySide6.QtCore import QObject, Property, QCoreApplication, QEvent, QUrl, Signal, Slot
from PySide6.QtQml import QQmlApplicationEngine
from PySide6.QtTest import QTest

import test_client_recognition as ui
import test_client_control_actions as controls
from client_control_layout import preset_layout


class ControlsSession(QObject):
    changed = Signal()
    def __init__(self):
        super().__init__()
        self.state = dict(candidateRows=[], commands={})
        self.calls = []
    snapshot = Property('QVariantMap', lambda s: deepcopy(s.state), notify=changed)
    busy = Property(bool, lambda s: False, constant=True)
    currentMode = Property(str, lambda s: 'nightmare', constant=True)
    appliedMode = Property(str, lambda s: 'nightmare', constant=True)
    appliedSeason = Property(str, lambda s: s.state.get('season', 's0'), notify=changed)
    @Slot(str, 'QVariantMap')
    def dispatchWithPayload(self, action, payload):
        self.calls.append((action, dict(payload)))


class CompactControlsIsolationTests(unittest.TestCase):
    def test_s1_hides_group_picker_and_preserves_compact_layout_and_season_switcher(self):
        session, settings = ControlsSession(), controls.Settings()
        settings.data["gameControlLayout"] = preset_layout("compact")
        session.state.update(season='s0', roundActive=True, groupId='group-ab',
            groupMemberIds=['map-a', 'map-b'], identityPhase='GROUP_LOCKED',
            currentGroupMemberId='map-a')
        session.state['commands']['select_group_member'] = dict(available=True)
        coordinator, windows = controls.Coordinator(settings), QObject()
        engine = QQmlApplicationEngine()
        warnings = []
        engine.warnings.connect(lambda rows: warnings.extend(map(str, rows)))
        for name, value in dict(session=session, settingsService=settings,
                clientCoordinator=coordinator, windows=windows).items():
            engine.rootContext().setContextProperty(name, value)
        engine.load(QUrl.fromLocalFile(str(Path(__file__).with_name('ClientControls.qml'))))
        self.assertEqual(len(engine.rootObjects()), 1, warnings)
        window = engine.rootObjects()[0]
        window.show(); ui.APP.processEvents()
        def items(item):
            yield item
            for child in item.childItems():
                yield from items(child)
        def item(name):
            return next(i for i in items(window.contentItem()) if i.objectName() == name)
        try:
            self.assertTrue(item('controlGroupMemberSection').isVisible())
            selector = item('controlGameContext')
            for season in ('s1', 's0', 's1'):
                session.state['season'] = season
                session.changed.emit(); ui.APP.processEvents()
                self.assertEqual(item('controlGroupMemberSection').isVisible(), season == 's0')
                self.assertEqual(item('controlGroupMemberSection').isEnabled(), season == 's0')
                self.assertIs(item('controlGameContext'), selector)
                self.assertEqual(selector.property('season'), season)
                self.assertTrue(selector.isVisible())
                self.assertEqual(window.property('activeModules').toVariant(), ['display', 'mode', 'round'])
                self.assertFalse(any(i.objectName() == 'controlReselectMap' for i in items(window.contentItem())))
            # Explicit S0 selection exposes existing modules even when hidden in
            # the saved compact layout, without changing that preference.
            session.state['season'] = 's0'
            session.changed.emit(); ui.APP.processEvents()
            saved_layout = deepcopy(settings.data["gameControlLayout"])
            window.setProperty('mapSelectionRequested', True); ui.APP.processEvents()
            self.assertIn('manual_map', window.property('activeModules').toVariant())
            self.assertIn('recognition', window.property('activeModules').toVariant())
            session.state['season'] = 's1'
            session.changed.emit(); ui.APP.processEvents()
            self.assertEqual(window.property('activeModules').toVariant(), ['display', 'mode', 'round'])
            window.hide(); ui.APP.processEvents()
            self.assertFalse(window.property('mapSelectionRequested'))
            self.assertEqual(settings.data["gameControlLayout"], saved_layout)
            self.assertEqual(session.calls, [])
            self.assertEqual(warnings, [])
        finally:
            window.hide(); window.deleteLater(); engine.deleteLater()
            QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
            ui.APP.processEvents()


if __name__ == '__main__':
    unittest.main()
