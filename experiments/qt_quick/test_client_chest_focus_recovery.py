"""Qt/core focus transitions with a real worker and mocked native input only."""
import time
import threading
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from client_chest_auto_tap import ChestAutoTapService
import chest_auto_tap as core
import test_client_chest_auto_tap as fixtures


class ChestFocusRecoveryTests(unittest.TestCase):
    def setUp(self):
        self.f = fixtures.ChestTests();self.f.setUp()
        self.addCleanup(self.f.doCleanups)
        self.f.adapter.shutdown()
        self.f.focus=99
        self.held=False;self.events=[]
        self.block_release=False
        self.releasing=threading.Event();self.release=threading.Event()
        self.patches=[patch.object(core,'_user32',None),
            patch.object(core,'key_down',lambda vk:self.held),
            patch.object(core,'foreground_window',lambda:self.f.focus),
            patch.object(core,'same_top_window',lambda a,b:bool(b) and a==b),
            patch.object(core,'_send_key',self.send)]
        for item in self.patches:item.start()
        def factory(*args,**kwargs):
            service=core.ChestAutoTap(*args,**kwargs);service.available=True;return service
        self.adapter=ChestAutoTapService(SimpleNamespace(settings_path=Path(self.f.temp.name)/'settings.json'),
            self.f.owner,self.f.input,self.f.settings,service_factory=factory,native=self.f.native)
        self.service=self.adapter._service

    def tearDown(self):
        self.held=False;self.release.set()
        self.adapter.shutdown();self.service._disable()
        self.wait(lambda:not self.service.snapshot()['pendingReleaseVks'])
        if self.service._thread is not None:
            self.service._thread.join(1)
            self.assertFalse(self.service._thread.is_alive(), "fixture worker must exit before restoring native input")
        for item in reversed(self.patches):item.stop()

    def wait(self,predicate):
        end=time.monotonic()+2
        while not predicate() and time.monotonic()<end:time.sleep(.002)
        self.assertTrue(predicate(),'fixture worker timeout')

    def send(self,vk,up):
        self.events.append((vk,up,self.f.focus))
        if up and self.block_release:
            self.releasing.set();self.release.wait(2)
        return 0

    def enable_in_assistant_then_game(self):
        result=self.adapter.update(True,'F12','F','90');self.adapter._timer.stop()
        self.assertTrue(result['success'])
        self.assertTrue(self.adapter.snapshot['enabled'])
        self.assertFalse(self.adapter.snapshot['running'])
        self.f.focus=42;self.adapter.pollOnce()
        self.assertTrue(self.adapter.snapshot['running'])
        self.wait(lambda:'等待' in self.service.snapshot()['status'])

    def test_release_fault_remains_disabled_after_retry_succeeds_and_focus_returns(self):
        attempts=[]
        def send(vk,up):
            result=self.send(vk,up)
            if up:
                attempts.append(vk)
                return 5 if len(attempts)==1 else result
            return result
        with patch.object(core,'_send_key',send):
            self.enable_in_assistant_then_game()
            self.held=True;self.wait(lambda:any(not up for _,up,_ in self.events))
            self.f.focus=99;self.adapter.pollOnce()
            self.wait(lambda:len(attempts)>=2 and not self.service.snapshot()['pendingReleaseVks'])
            self.f.focus=42;self.adapter.pollOnce()
            self.assertFalse(self.adapter.snapshot['enabled'])
            self.assertFalse(self.adapter.snapshot['running'])
            self.assertFalse(self.service.snapshot()['enabled'])
            self.assertIn('释放按键',self.adapter.snapshot['message'])
            self.assertEqual(sum(not up for _,up,_ in self.events),1)

    def test_actual_qml_switch_preserves_intent_across_focus_pause(self):
        from PySide6.QtCore import QObject, QUrl, Qt
        from PySide6.QtQuick import QQuickView
        from PySide6.QtTest import QTest
        view=QQuickView();view.setResizeMode(QQuickView.SizeRootObjectToView)
        view.rootContext().setContextProperty('chestAutoTapService',self.adapter)
        view.setSource(QUrl.fromLocalFile(str(Path(__file__).with_name('ClientChestAutoTap.qml'))))
        self.assertFalse(view.errors())
        view.resize(600,350);view.show();fixtures.APP.processEvents()
        switch=view.rootObject().findChild(QObject,'chestAutoTapEnabled')
        position=switch.mapToScene(switch.boundingRect().center()).toPoint()
        try:
            QTest.mouseClick(view,Qt.LeftButton,Qt.NoModifier,position)
            fixtures.APP.processEvents();self.adapter._timer.stop()
            self.assertTrue(switch.property('checked'))
            self.assertFalse(self.adapter.snapshot['running'])
            self.f.focus=42;self.adapter.pollOnce()
            self.wait(lambda:self.service.snapshot().get('triggerReady'))
            self.held=True;self.wait(lambda:any(not up for _,up,_ in self.events))
            self.f.focus=99;self.adapter.pollOnce();fixtures.APP.processEvents()
            self.wait(lambda:not self.service.snapshot()['pendingReleaseVks'])
            self.assertTrue(switch.property('checked'))
            self.assertFalse(self.adapter.snapshot['running'])
            count=sum(not up for _,up,_ in self.events)
            self.f.focus=42;self.adapter.pollOnce();fixtures.APP.processEvents()
            time.sleep(.12)
            self.assertTrue(switch.property('checked'))
            self.assertEqual(sum(not up for _,up,_ in self.events),count)
            self.held=False;self.wait(lambda:self.service.snapshot()['triggerReady'])
            self.held=True;self.wait(lambda:sum(not up for _,up,_ in self.events)>count)
            self.f.focus=99
            QTest.mouseClick(view,Qt.LeftButton,Qt.NoModifier,position);fixtures.APP.processEvents()
            self.assertFalse(switch.property('checked'))
            self.assertFalse(self.adapter.snapshot['enabled'])
        finally:
            view.close();view.deleteLater();fixtures.APP.processEvents()

    def test_binding_loss_rebind_and_conflict_still_require_explicit_enable(self):
        self.enable_in_assistant_then_game()
        self.held=True;self.wait(lambda:any(not up for _,up,_ in self.events))
        self.f.owner.boundWindow='';self.adapter.pollOnce()
        self.wait(lambda:not self.service.snapshot()['pendingReleaseVks'])
        self.assertFalse(self.adapter.snapshot['enabled'])
        self.f.owner.boundWindow='42';self.adapter.pollOnce()
        self.assertFalse(self.adapter.snapshot['running'])
        count=sum(not up for _,up,_ in self.events)
        self.assertTrue(self.adapter.update(True,'F12','E','90')['success']);self.adapter._timer.stop()
        time.sleep(.03)
        self.assertEqual(sum(not up for _,up,_ in self.events),count)
        self.held=False;self.wait(lambda:self.service.snapshot()['triggerReady'])
        self.held=True;self.wait(lambda:any(vk==ord('E') and not up for vk,up,_ in self.events))
        self.f.input.snapshot['bindings']['confirm-round']='F12';self.f.input.changed.emit()
        self.wait(lambda:not self.service.snapshot()['pendingReleaseVks'])
        self.assertFalse(self.adapter.snapshot['enabled'])
        self.assertIn('确认本局',self.adapter.snapshot['message'])
        self.f.input.snapshot['bindings']['confirm-round']='';self.f.input.changed.emit()
        self.assertFalse(self.adapter.snapshot['enabled'])
        self.assertFalse(self.adapter.snapshot['running'])

    def test_same_binding_temporarily_unavailable_resumes_only_after_new_press(self):
        self.enable_in_assistant_then_game()
        self.held=True;self.wait(lambda:any(not up for _,up,_ in self.events))
        self.f.input.snapshot['enabled']=False;self.f.input.changed.emit()
        self.wait(lambda:not self.service.snapshot()['pendingReleaseVks'])
        self.assertTrue(self.adapter.snapshot['enabled'])
        self.f.input.snapshot['enabled']=True;self.f.input.changed.emit()
        count=sum(not up for _,up,_ in self.events);time.sleep(.03)
        self.assertEqual(sum(not up for _,up,_ in self.events),count)
        self.held=False;self.wait(lambda:self.service.snapshot()['triggerReady'])
        self.held=True;self.wait(lambda:sum(not up for _,up,_ in self.events)>count)

    def test_fast_focus_return_waits_for_normal_release_without_revoking_enable(self):
        self.enable_in_assistant_then_game()
        self.block_release=True;self.held=True
        self.wait(lambda:any(not up for _,up,_ in self.events))
        self.f.focus=99;self.adapter.pollOnce()
        self.assertTrue(self.releasing.wait(1))
        self.assertFalse(self.adapter.snapshot['running'])
        self.f.focus=42;self.adapter.pollOnce()
        self.assertTrue(self.adapter.snapshot['enabled'])
        self.assertFalse(self.adapter.snapshot['running'])
        self.assertEqual(sum(not up for _,up,_ in self.events),1)
        self.release.set();self.wait(lambda:not self.service.snapshot()['pendingReleaseVks'])
        self.adapter.pollOnce()
        self.assertTrue(self.adapter.snapshot['running'])

    def test_focus_resume_does_not_resave_unchanged_configuration(self):
        self.enable_in_assistant_then_game()
        self.f.focus=99;self.adapter.pollOnce()
        with patch.object(self.service,'_save',side_effect=OSError('fixture readonly')) as save:
            self.f.focus=42;self.adapter.pollOnce()
            self.assertTrue(self.adapter.snapshot['enabled'])
            self.assertTrue(self.adapter.snapshot['running'])
            save.assert_not_called()

    def test_return_with_old_held_trigger_waits_for_release_and_new_press(self):
        self.enable_in_assistant_then_game()
        self.held=True;self.wait(lambda:any(not up for _,up,_ in self.events))
        self.f.focus=99;self.adapter.pollOnce()
        self.wait(lambda:not self.service.snapshot()['pendingReleaseVks'])
        count=sum(not up for _,up,_ in self.events)
        self.f.focus=42;self.adapter.pollOnce()
        time.sleep(.12)
        self.assertEqual(sum(not up for _,up,_ in self.events),count)
        self.held=False;time.sleep(.02)
        self.held=True;self.wait(lambda:sum(not up for _,up,_ in self.events)>count)
        self.assertTrue(all(foreground==42 for _,up,foreground in self.events if not up))


if __name__=='__main__':unittest.main()
