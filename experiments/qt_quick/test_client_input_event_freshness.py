"""Real source/backend queued edge contract; no hook registration or native input."""
import os
os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch
from PySide6.QtGui import QGuiApplication
from client_input import NativeShortcutBackend
from client_native_keys import PassThroughKeyboardSource
from test_client_input import Native

APP=QGuiApplication.instance() or QGuiApplication([])


class InputFreshnessTests(unittest.TestCase):
    def setUp(self):
        self.native=Native()
        self.source=PassThroughKeyboardSource()
        self.source.start=Mock(return_value=True)
        self.source.stop=Mock(return_value=True)
        self.source._thread=SimpleNamespace(is_alive=lambda: True)
        self.source.installed=True
        self.keys=NativeShortcutBackend(self.native,source=self.source)
        self.events=[]
        self.keys.actionTriggered.connect(self.events.append)
        self.addCleanup(self.keys.shutdown)

    def configure(self,action,chord):
        self.assertTrue(self.keys.configure({action:chord}))
        with patch('time.monotonic', return_value=90.):
            self.keys.enableManagement(); self.keys.bindWindow(42); self.keys.enable()

    def emit(self,vk,down,created=97.,foreground=42):
        # Native source creates the original edge; GUI delivery occurs later.
        with patch('time.monotonic',return_value=created):
            event=self.source._transition_event(vk,0,0,down,foreground)
        self.source.eventReceived.emit(event)
        with patch('time.monotonic',return_value=100.):
            APP.processEvents()

    def test_delayed_mouse_and_keyboard_downs_cannot_fire_game_or_management(self):
        for action,chord,vk,foreground in (
                ('map','MOUSE2',0x02,42), ('map','G',ord('G'),42),
                ('toggle-panel','MOUSE4',0x05,77), ('toggle-panel','F24',0x87,77)):
            with self.subTest(action=action,chord=chord):
                self.configure(action,chord)
                self.emit(vk,True,foreground=foreground)
                self.assertEqual(self.events,[])
                self.emit(vk,False,foreground=foreground)
                self.assertNotIn(vk,self.source.held)
                self.assertNotIn(vk,self.keys._event_down)
                self.emit(vk,True,created=99.99,foreground=foreground)
                self.assertEqual(self.events,[action])
                self.emit(vk,False,foreground=foreground)
                self.events.clear()

    def test_missing_future_and_nonfinite_down_timestamps_fail_closed(self):
        self.configure('toggle-panel','MOUSE4')
        for created in (None,100.01,float('nan'),float('inf'),float('-inf'),True,'99',10**400):
            with self.subTest(created=repr(created)):
                event=self.source._transition_event(0x05,0,0,True,77)
                if created is None:
                    event.pop('createdAt')
                else:
                    event['createdAt']=created
                self.source.eventReceived.emit(event)
                with patch('time.monotonic',return_value=100.):
                    APP.processEvents()
                self.assertEqual(self.events,[])
                self.emit(0x05,False)  # Invalid age never prevents release cleanup.
                self.assertNotIn(0x05,self.keys._event_down)

    def test_fresh_mouse_management_keeps_assistant_and_background_scope(self):
        self.configure('toggle-sheet-visibility','MOUSE5')
        self.native.foreground=77; self.native.process=os.getpid()
        self.emit(0x06,True,created=98.,foreground=77)  # Existing inclusive 2s budget.
        self.assertEqual(self.events,['toggle-sheet-visibility'])
        self.emit(0x06,False,foreground=77)
        self.native.process=9001
        self.emit(0x06,True,created=99.,foreground=77)
        self.assertEqual(self.events,['toggle-sheet-visibility']*2)

    def test_assistant_panel_toggle_has_one_native_mouse_owner_and_qml_keyboard_owner(self):
        self.native.foreground=77; self.native.process=os.getpid()
        for chord,vk in (('MOUSE2',0x02),('MOUSE3',0x04),('MOUSE4',0x05),('MOUSE5',0x06),('F9',0x78)):
            with self.subTest(chord=chord):
                self.configure('toggle-panel',chord)
                self.emit(vk,True,created=99.,foreground=77)
                self.emit(vk,True,created=99.,foreground=77)  # Held repeat cannot toggle twice.
                self.emit(vk,False,foreground=77)
                self.assertEqual(self.events,[] if chord=='F9' else ['toggle-panel'])
                self.events.clear()


if __name__=='__main__':unittest.main()
