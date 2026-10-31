"""Two bounded controls UI cases; no game capture, native keys, or data writes."""
from copy import deepcopy
import os
from pathlib import Path
import unittest

os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
os.environ.setdefault('QT_QUICK_BACKEND','software')
from PySide6.QtCore import QObject, Property, Signal, Slot, QUrl, QCoreApplication, QEvent, QPoint, QPointF, Qt
from PySide6.QtTest import QTest
from PySide6.QtGui import QGuiApplication, QFontDatabase
from PySide6.QtQml import QQmlApplicationEngine
import shiboken6
from test_client_control_actions import Settings, Coordinator

APP = QGuiApplication.instance() or QGuiApplication([])
if Path('C:/Windows/Fonts/msyh.ttc').is_file():
    QFontDatabase.addApplicationFont('C:/Windows/Fonts/msyh.ttc')


class StableSettings(Settings):
    def __init__(self):
        super().__init__()
        self.data['gameControlLayout']['enabled'] = ['mode']
        self._busy = False
    values = Property('QVariantMap', lambda self: deepcopy(self.data), notify=Settings.changed)
    busy = Property(bool, lambda self: self._busy, notify=Settings.changed)


class State(QObject):
    changed = Signal()
    def __init__(self):
        super().__init__()
        self._busy = False
        self._mode = self._applied = 'nightmare'
        self.calls = []
        self.data = dict(roundActive=True, roundPreparing=False, mapOpen=True,
            mapId='', provisionalMapId='nightmare-08', observedFloor='1',
            identityPhase='GROUP_LOCKED', groupLocked=True, groupId='similar-group',
            displayAuthorized=False, resourceLocked=False, contextPending=False,
            pendingAction='', commands={'set_game_mode':{'available':True}})
    snapshot = Property('QVariantMap', lambda self: deepcopy(self.data), notify=changed)
    busy = Property(bool, lambda self: self._busy, notify=changed)
    currentMode = Property(str, lambda self: self._mode, notify=changed)
    appliedMode = Property(str, lambda self: self._applied, notify=changed)
    @Slot(str, 'QVariantMap')
    def dispatchWithPayload(self, action, payload):
        self.calls.append((action, dict(payload)))
    def pulse(self, busy, frame):
        self._busy = busy
        self.data.update(pendingAction='refresh_map' if busy else '', frameId='frame-'+str(frame))
        self.changed.emit()
        APP.processEvents()


class BoundCoordinator(Coordinator):
    boundWindow = Property(str, lambda self: 'fixture-game', constant=True)


class ControlStabilityTests(unittest.TestCase):
    def setUp(self):
        self.settings, self.session = StableSettings(), State()
        self.coordinator = BoundCoordinator(self.settings)
        self.engine = QQmlApplicationEngine()
        self.warnings = []
        self.windows = QObject()
        self.engine.warnings.connect(lambda rows:self.warnings.extend(str(r) for r in rows))
        for name,value in dict(settingsService=self.settings,session=self.session,
                clientCoordinator=self.coordinator,windows=self.windows).items():
            self.engine.rootContext().setContextProperty(name,value)
        self.engine.load(QUrl.fromLocalFile(str(Path(__file__).with_name('ClientControls.qml'))))
        self.assertTrue(self.engine.rootObjects(),self.warnings)
        self.window = self.engine.rootObjects()[0]
        APP.processEvents()
        self.addCleanup(self.cleanup)

    def cleanup(self):
        self.window.deleteLater()
        self.engine.deleteLater()
        QCoreApplication.sendPostedEvents(None,QEvent.DeferredDelete)
        APP.processEvents()

    def item(self,name):
        def walk(item):
            yield item
            for child in item.childItems(): yield from walk(child)
        return next(item for item in walk(self.window.contentItem()) if item.objectName()==name)

    def test_active_group_refresh_keeps_mode_instance_selection_disabled_and_header_truthful(self):
        mode = self.item('controlModeNightmare')
        pointer = shiboken6.getCppPointer(mode)[0]
        color = mode.property('background').property('color')
        destroyed=[]
        mode.destroyed.connect(lambda:destroyed.append(True))
        enabled=[]
        mode.enabledChanged.connect(lambda:enabled.append(mode.isEnabled()))
        for frame in range(30):
            self.session.pulse(True,frame)
            self.session.pulse(False,frame)
            current = self.item('controlModeNightmare')
            self.assertEqual(shiboken6.getCppPointer(current)[0],pointer)
            self.assertTrue(current.property('selected'))
            self.assertTrue(current.property('primary'))
            self.assertFalse(current.isEnabled())
            self.assertEqual(current.property('background').property('color'),color)
        for _ in range(3):
            self.settings.changed.emit();APP.processEvents()
        self.settings.data['interfaceOpacity']=.9
        self.settings.changed.emit();APP.processEvents()
        self.assertEqual(shiboken6.getCppPointer(self.item('controlModeNightmare'))[0],pointer)
        self.assertEqual(destroyed,[])
        self.assertEqual(enabled,[])
        self.assertIn('相似组已锁定',self.item('controlStatusText').property('text'))
        self.assertIn('暂用 nightmare-08',self.item('controlMapFloorText').property('text'))
        self.assertNotIn('地图已核对',self.item('controlStatusText').property('text'))
        self.session.data.update(identityPhase='MEMBER_VERIFIED',mapId='nightmare-08',provisionalMapId='',displayAuthorized=True)
        self.session.changed.emit();APP.processEvents()
        self.assertIn('地图已核对',self.item('controlStatusText').property('text'))
        self.assertNotIn('暂用',self.item('controlMapFloorText').property('text'))
        self.assertFalse(self.warnings,'\n'.join(self.warnings))

    def test_manual_member_header_only_follows_committed_phase_not_pending_target(self):
        self.session.data.update(groupMemberSelectionPending=True, pendingGroupMemberId='nightmare-09',
            currentGroupMemberId='nightmare-08', manualGroupMemberHeld=False)
        self.session.changed.emit();APP.processEvents()
        self.assertNotIn('本局已手动选择',self.item('controlStatusText').property('text'))
        self.assertIn('暂用 nightmare-08',self.item('controlMapFloorText').property('text'))
        self.assertNotIn('nightmare-09',self.item('controlMapFloorText').property('text'))
        self.session.data.update(identityPhase='MANUAL_MEMBER_CONFIRMED', manualGroupMemberHeld=True,
            manualGroupMemberId='nightmare-09',currentGroupMemberId='nightmare-09',
            groupMemberRows=[dict(mapId='nightmare-09',name='地图乙')],groupMemberSelectionPending=False)
        self.session.changed.emit();APP.processEvents()
        self.assertIn('本局已手动选择',self.item('controlStatusText').property('text'))
        self.assertIn('位置待核对',self.item('controlStatusText').property('text'))
        self.assertIn('地图乙',self.item('controlMapFloorText').property('text'))
        self.assertNotIn('暂用',self.item('controlMapFloorText').property('text'))
        self.assertNotIn('地图已核对',self.item('controlStatusText').property('text'))
        self.session.data['displayAuthorized']=True
        self.settings.data['sheetVisibilityEnabled']=False
        self.settings.changed.emit();self.session.changed.emit();APP.processEvents()
        self.assertIn('贴合已隐藏',self.item('controlStatusText').property('text'))
        self.session.data.update(identityPhase='GROUP_LOCKED',manualGroupMemberHeld=False,
            manualGroupMemberId='',provisionalMapId='nightmare-09')
        self.session.changed.emit();APP.processEvents()
        self.assertNotIn('本局已手动选择',self.item('controlStatusText').property('text'))
        self.assertIn('暂用 nightmare-09',self.item('controlMapFloorText').property('text'))
        self.assertFalse(self.warnings,'\n'.join(self.warnings))

    def test_prepared_round_is_waiting_and_cancelable_in_existing_controls_context(self):
        self.settings.data['gameControlLayout']['enabled']=['mode','round']
        self.settings.changed.emit()
        self.session.data.update(roundActive=False,roundPreparing=True,identityPhase='UNLOCKED',
            groupLocked=False,mapId='',provisionalMapId='',observedFloor='',pendingAction='')
        self.session.data['commands']['toggle_round']={'available':True}
        self.session.changed.emit();APP.processEvents()
        self.assertIn('已准备',self.item('controlStatusText').property('text'))
        self.assertNotIn('正在准备',self.item('controlStatusText').property('text'))
        self.assertEqual(self.item('controlRoundAction').property('text'),'取消准备')
        self.assertTrue(self.item('controlRoundAction').isEnabled())
        self.session._busy=True;self.session.data['pendingAction']='start_round'
        self.session.changed.emit();APP.processEvents()
        self.assertIn('正在准备',self.item('controlStatusText').property('text'))
        self.assertFalse(self.item('controlRoundAction').isEnabled())
        self.assertFalse(self.warnings,'\n'.join(self.warnings))

    def test_idle_passive_refresh_is_stable_but_preparation_resource_and_pending_context_block_mode(self):
        self.session.data.update(roundActive=False,mapOpen=False,identityPhase='UNLOCKED',groupLocked=False,provisionalMapId='',observedFloor='')
        self.session.changed.emit();APP.processEvents()
        mode = self.item('controlModeNightmare')
        pointer = shiboken6.getCppPointer(mode)[0]
        color = mode.property('background').property('color')
        for frame in range(30):
            for busy in (True,False):
                self.session.pulse(busy,frame)
                self.assertEqual(shiboken6.getCppPointer(self.item('controlModeNightmare'))[0],pointer)
                self.assertTrue(mode.isEnabled())
                self.assertTrue(mode.property('selected'))
                self.assertEqual(mode.property('background').property('color'),color)
        self.assertIn('游戏已连接',self.item('controlStatusText').property('text'))
        for field in ('roundActive','roundPreparing','resourceLocked','contextPending'):
            self.session.data[field]=True;self.session.changed.emit();APP.processEvents()
            self.assertFalse(mode.isEnabled(),field)
            self.session.data[field]=False
        self.settings._busy=True;self.settings.changed.emit();APP.processEvents()
        self.assertFalse(mode.isEnabled())
        self.settings._busy=False;self.settings.changed.emit()
        self.session._mode='difficult';self.session.data['contextPending']=True
        self.session.changed.emit();APP.processEvents()
        self.assertTrue(mode.property('selected'))
        self.assertFalse(self.item('controlModeDifficult').property('selected'))
        self.assertIn('正在切换',self.item('controlStatusText').property('text'))
        self.assertFalse(self.warnings,'\n'.join(self.warnings))

    def group(self):
        self.session.data.update(groupMemberIds=['nightmare-08','nightmare-09','nightmare-08'],
            groupMemberRows=[dict(mapId='nightmare-08',name='地图甲'),dict(mapId='nightmare-09',name='地图乙')],
            currentGroupMemberId='nightmare-08',groupGeneration=8,cycleId=4,floor='1')
        self.session.data['commands']['select_group_member']={'available':True}
        self.session.changed.emit();APP.processEvents()

    def click(self, name):
        item = self.item(name)
        point = item.mapToScene(QPointF(item.width()/2,item.height()/2))
        QTest.mouseClick(self.window,Qt.LeftButton,Qt.NoModifier,QPoint(round(point.x()),round(point.y())))
        APP.processEvents()

    def test_default_group_picker_real_click_pending_failure_ack_and_blocked_reasons(self):
        self.group()
        self.window.show();QTest.qWait(20)
        self.assertEqual(self.window.property('activeModules').toVariant(),['mode'])
        self.assertTrue(self.item('controlGroupMemberSection').isVisible())
        self.click('controlGroupMember_nightmare-09')
        self.assertEqual(self.session.calls,[('select_group_member',dict(mapId='nightmare-09',
            groupId='similar-group',groupGeneration=8,cycleId=4,floor='1'))])
        self.assertTrue(self.item('controlGroupMember_nightmare-08').property('selected'))
        self.assertFalse(self.item('controlGroupMember_nightmare-09').property('selected'))
        self.session.data.update(groupMemberSelectionPending=True,pendingGroupMemberId='nightmare-09')
        self.session.changed.emit();APP.processEvents()
        self.assertFalse(self.item('controlGroupMember_nightmare-09').isEnabled())
        self.assertIn('当前仍为地图甲',self.item('controlGroupMemberReason').property('text'))
        self.click('controlGroupMember_nightmare-09')
        self.assertEqual(len(self.session.calls),1)
        self.session.data.update(groupMemberSelectionPending=False,groupMemberSelectionReason='切换未生效，请打开地图重试')
        self.session.changed.emit();APP.processEvents()
        self.assertTrue(self.item('controlGroupMember_nightmare-08').property('selected'))
        self.assertIn('切换未生效',self.item('controlGroupMemberReason').property('text'))
        self.session.data.update(currentGroupMemberId='nightmare-09',manualGroupMemberHeld=True,
            identityPhase='MANUAL_MEMBER_CONFIRMED',groupMemberSelectionReason='')
        self.session.changed.emit();APP.processEvents()
        self.assertTrue(self.item('controlGroupMember_nightmare-09').property('selected'))
        self.assertFalse(self.item('controlGroupMember_nightmare-09').isEnabled())
        self.assertIn('本局已选：地图乙',self.item('controlGroupMemberCurrent').property('text'))
        for changes, reason in [(dict(resourceLocked=True),'正在更新资源'),
            (dict(resourceLocked=False,contextPending=True),'正在切换环境'),
            (dict(contextPending=False,pendingAction='select_group_member'),'当前操作尚未完成')]:
            self.session.data.update(changes)
            self.session._busy=changes.get('pendingAction') is not None
            self.session.changed.emit();APP.processEvents()
            self.assertFalse(self.item('controlGroupMember_nightmare-08').isEnabled())
            self.assertIn(reason,self.item('controlGroupMemberReason').property('text'))
        self.assertFalse(self.warnings,'\n'.join(self.warnings))

    def test_group_picker_instances_focus_and_narrow_scroll_survive_frame_updates(self):
        self.group()
        self.window.show();QTest.qWait(20)
        button=self.item('controlGroupMember_nightmare-09')
        pointer=shiboken6.getCppPointer(button)[0]
        button.forceActiveFocus()
        for frame in range(20):
            self.session.pulse(True,frame)
            self.assertEqual(shiboken6.getCppPointer(self.item('controlGroupMember_nightmare-09'))[0],pointer)
            self.assertTrue(button.isEnabled())
            self.assertTrue(button.hasActiveFocus())
        self.session._busy=False
        self.session.data['groupMemberRows'][1]['name']='很长的相似地图名称用于检查窄控件中的省略与滚动边界' * 3
        self.session.changed.emit();APP.processEvents()
        self.window.setProperty('expandedHeightLimit',240)
        self.window.setWidth(240);QTest.qWait(20)
        scroll=self.item('controlContentScroll')
        button=self.item('controlGroupMember_nightmare-09')
        self.assertEqual(self.window.height(),240)
        self.assertGreater(scroll.property('contentHeight'),scroll.height())
        self.assertLessEqual(button.width(),scroll.width())
        self.assertLessEqual(button.mapToScene(QPointF(button.width(),0)).x(),self.window.width())
        self.assertTrue(button.property('contentItem').property('truncated'))
        self.window.setProperty('collapsed',True);APP.processEvents()
        self.assertEqual((self.window.width(),self.window.height()),(64,64))
        self.assertFalse(self.item('controlGroupMemberSection').isVisible())
        self.assertTrue(self.item('controlCollapsedOrb').isVisible())
        self.assertFalse(self.warnings,'\n'.join(self.warnings))

    def test_no_group_or_single_member_has_no_picker(self):
        self.window.show();APP.processEvents()
        self.assertFalse(self.item('controlGroupMemberSection').isVisible())
        self.group()
        self.session.data['identityPhase']='MEMBER_VERIFIED'
        self.session.changed.emit();APP.processEvents()
        self.assertEqual(self.item('controlGroupMemberCurrent').property('text'),'当前地图：地图甲')
        self.session.data.update(groupMemberIds=['nightmare-08'])
        self.session.changed.emit();APP.processEvents()
        self.assertFalse(self.item('controlGroupMemberSection').isVisible())
        self.session.data.update(groupMemberIds=['nightmare-08','nightmare-09'],groupId='')
        self.session.changed.emit();APP.processEvents()
        self.assertFalse(self.item('controlGroupMemberSection').isVisible())
        self.assertFalse(self.warnings,'\n'.join(self.warnings))


if __name__=='__main__':unittest.main()
