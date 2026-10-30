"""Two bounded controls UI cases; no game capture, native keys, or data writes."""
from copy import deepcopy
import os
from pathlib import Path
import unittest

os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
os.environ.setdefault('QT_QUICK_BACKEND','software')
from PySide6.QtCore import QObject, Property, Signal, QUrl, QCoreApplication, QEvent
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
        self.data = dict(roundActive=True, roundPreparing=False, mapOpen=True,
            mapId='', provisionalMapId='nightmare-08', observedFloor='1',
            identityPhase='GROUP_LOCKED', groupLocked=True, groupId='similar-group',
            displayAuthorized=False, resourceLocked=False, contextPending=False,
            pendingAction='', commands={'set_game_mode':{'available':True}})
    snapshot = Property('QVariantMap', lambda self: deepcopy(self.data), notify=changed)
    busy = Property(bool, lambda self: self._busy, notify=changed)
    currentMode = Property(str, lambda self: self._mode, notify=changed)
    appliedMode = Property(str, lambda self: self._applied, notify=changed)
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


if __name__=='__main__':unittest.main()
