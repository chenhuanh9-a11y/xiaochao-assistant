"""Production QML, real mouse/key events, synthetic state; no live-game claim."""
from copy import deepcopy
from pathlib import Path
import unittest

from PySide6.QtCore import QObject, Property, QCoreApplication, QEvent, QUrl, Signal, Slot
from PySide6.QtQml import QQmlApplicationEngine
from PySide6.QtTest import QTest

import test_client_recognition as ui
import test_client_control_actions as controls


class Coordinator(QObject):
    def __init__(self):
        super().__init__()
        self.recapture_calls = 0

    @Slot()
    def recaptureFromMain(self):
        self.recapture_calls += 1


class S1ReadonlyRecognitionTests(unittest.TestCase):
    item = ui.RecognitionTests.item
    click = ui.RecognitionTests.click

    def setUp(self):
        ui.RecognitionTests.setUp(self)
        self.coordinator = Coordinator()
        self.engine.rootContext().setContextProperty('clientCoordinator', self.coordinator)

    def tearDown(self):
        ui.RecognitionTests.tearDown(self)
        QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
        ui.APP.processEvents()

    def publish(self, **changes):
        self.session.state.update(changes)
        self.session.changed.emit()
        ui.APP.processEvents()
        QTest.qWait(20)

    def s1(self, **changes):
        state = dict(season='s1', roundActive=True, mapOpen=True,
            candidateStructureAvailable=True, candidateProvisionalDisplayEnabled=True,
            candidateDisplayOnly=True, identityPhase='CANDIDATE_DISPLAY',
            automaticFloorObservationAvailable=False, manualMapPickAvailable=True,
            manualAlignmentSession=False, availableFloors=['-1', '-2', '-3', '-4'],
            observedFloor='-2', floor='-2', targetFloor='-2', manualFloor='',
            provisionalMapId='map-a', displayAuthorized=True)
        state.update(changes)
        self.publish(**state)

    def test_s1_candidate_rows_are_readonly_without_manual_actions(self):
        self.s1()
        self.assertEqual(self.item('recognitionCandidateList').property('count'), 2)
        for name in ('recognitionPrevious', 'recognitionNext', 'recognitionConfirm', 'recognitionReselect'):
            self.assertFalse(self.item(name).isVisible(), name)
            self.assertFalse(self.item(name).isEnabled(), name)
        row = self.item('recognitionCandidate_1')
        self.assertTrue(row.isVisible())
        self.assertFalse(row.isEnabled())
        self.click('recognitionCandidate_1')
        row.forceActiveFocus()
        QTest.keyClick(self.window, ui.Qt.Key_Space)
        self.assertEqual(self.session.calls, [])
        names = [item.objectName() for item in self.page.findChildren(QObject)]
        self.assertNotIn('recognitionPrepareManualAlignment', names)
        self.assertIn('未锁定候选', self.item('recognitionIdentitySummary').property('text'))
        self.assertNotIn('地图已确认', self.item('recognitionIdentitySummary').property('text'))
        self.click('recognitionCapture')
        self.assertEqual(self.coordinator.recapture_calls, 1)
        self.assertFalse(self.warnings)

    def test_native_auto_floor_contract_does_not_expose_manual_only_fallback(self):
        self.s1()
        self.assertTrue(self.item('recognitionAutomaticFloors').isVisible())
        for name in ('recognitionFloor_auto', 'recognitionFloor_-1', 'recognitionFloor_-2',
                     'recognitionFloor_-3', 'recognitionFloor_-4', 'recognitionCycleFloor'):
            self.assertFalse(self.item(name).isVisible(), name)
        self.assertEqual(self.item('recognitionCurrentFloor').property('text'), '自动观察当前 B2')
        self.publish(observedFloor='', displayAuthorized=False)
        self.assertIn('当前楼层未知', self.item('recognitionCurrentFloor').property('text'))
        self.assertNotIn('当前显示', self.item('recognitionIdentitySummary').property('text'))
        self.publish(candidateStructureAvailable=False)
        self.assertFalse(self.item('recognitionFloorActions').isVisible())
        self.assertIn('暂不可用', self.item('recognitionFloorExplanation').property('text'))
        self.assertEqual(self.session.calls, [])
        self.assertFalse(self.warnings)

    def test_existing_manual_session_keeps_target_only_fallback_and_auto_reset(self):
        self.s1(candidateStructureAvailable=False, candidateProvisionalDisplayEnabled=False,
            manualAlignmentSession=True, observedFloor='', floorSource='unknown',
            displayAuthorized=False)
        self.assertTrue(self.item('recognitionFloor_-3').isVisible())
        # Candidate capability is not the observer used by a legacy manual session.
        self.publish(candidateStructureAvailable=True)
        self.assertTrue(self.item('recognitionFloor_-3').isVisible())
        self.click('recognitionFloor_-3')
        self.assertEqual(self.session.calls, [('set_floor', {'floor': '-3'})])
        self.assertIn('当前楼层未知', self.item('recognitionCurrentFloor').property('text'))
        self.assertIn('仅设置目标', self.item('recognitionFloorExplanation').property('text'))
        self.publish(manualFloor='-3', targetFloor='-3', floorSource='manual-unvalidated-fallback')
        self.assertEqual(self.item('recognitionCurrentFloor').property('text'), '当前楼层未知 · 人工回退 B3')
        self.publish(automaticFloorObservationAvailable=True)
        self.assertFalse(self.item('recognitionFloor_-3').isVisible())
        self.assertTrue(self.item('recognitionFloor_auto').isVisible())
        self.click('recognitionFloor_auto')
        self.assertEqual(self.session.calls[-1], ('set_floor', {'floor': 'auto'}))
        self.assertFalse(self.item('recognitionManualMapActions').isVisible())
        self.assertFalse(self.warnings)

    def test_repeat_season_switch_preserves_s0_controls_and_stable_page(self):
        original = deepcopy(self.session.state)
        original['season'] = 's0'
        page = self.page
        for _ in range(3):
            self.s1()
            self.assertFalse(self.item('recognitionConfirm').isVisible())
            self.session.state = deepcopy(original)
            self.session.changed.emit(); ui.APP.processEvents()
            self.assertIs(self.page, page)
            self.assertTrue(self.item('recognitionConfirm').isVisible())
            self.assertTrue(self.item('recognitionCandidate_1').isEnabled())
            self.click('recognitionCandidate_1')
            self.assertEqual(self.session.calls[-1], ('select_candidate', {'index': 1}))
            self.click('recognitionFloor_2')
            self.assertEqual(self.session.calls[-1], ('set_floor', {'floor': '2'}))
        self.assertFalse(self.warnings)

    def test_s1_group_fields_never_expose_manual_group_choices(self):
        group = dict(groupId='group-ab', groupMemberIds=['map-a', 'map-b'],
            groupGeneration=1, cycleId=1, currentGroupMemberId='map-a',
            identityPhase='GROUP_LOCKED')
        self.session.state['commands']['select_group_member'] = dict(available=True)
        self.publish(season='s0', roundActive=True, **group)
        self.assertTrue(self.item('groupMember_map-b').isVisible())
        self.s1(**group)
        self.assertFalse(self.page.property('groupVisible'))
        self.assertFalse(self.item('groupMember_map-b').isVisible())
        self.assertFalse(self.item('groupMember_map-b').isEnabled())
        self.assertTrue(self.item('recognitionCandidateList').isVisible())
        self.publish(season='s0')
        self.assertTrue(self.item('groupMember_map-b').isVisible())
        self.click('groupMember_map-b')
        self.assertEqual(self.session.calls[-1][0], 'select_group_member')
        self.assertFalse(self.warnings)


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
            self.assertEqual(session.calls, [])
            self.assertEqual(warnings, [])
        finally:
            window.hide(); window.deleteLater(); engine.deleteLater()
            QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
            ui.APP.processEvents()


if __name__ == '__main__':
    unittest.main()
