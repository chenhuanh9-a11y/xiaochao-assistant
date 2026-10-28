"""Own hidden native HWND only. No SendInput, cursor changes, Tk or product launch."""
from pathlib import Path
import ctypes
import queue
import sys
import threading
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'app'))
from identityv_live_mvp import LayeredOverlay, user32
from reference_position_editor import ReferencePointerMailbox


@unittest.skipUnless(sys.platform == 'win32', 'Native Windows test')
class NativePositionWindowTests(unittest.TestCase):
    def setUp(self):
        self.foreground = user32.GetForegroundWindow()
        self.surface = LayeredOverlay()  # Created hidden; never show or activate it.
        self.addCleanup(self.surface.close)
        self.target = queue.Queue()
        self.mailbox = ReferencePointerMailbox(21,self.surface.hwnd,self.target)
        self.surface.set_pointer_mailbox(self.mailbox)
        self.assertFalse(user32.IsWindowVisible(self.surface.hwnd))
        user32.SendMessageW.argtypes = [ctypes.c_void_p,ctypes.c_uint,ctypes.c_size_t,ctypes.c_ssize_t]
        user32.SendMessageW.restype = ctypes.c_ssize_t

    def send(self, message, x=4, y=7):
        user32.SendMessageW(self.surface.hwnd,message,0,((y & 0xffff)<<16)|(x & 0xffff))

    def tearDown(self):
        self.assertEqual(user32.GetForegroundWindow(),self.foreground,'Own hidden tests must not activate a window')

    def test_actual_wndproc_fast_down_up_keeps_both_and_releases_own_capture(self):
        self.send(0x0201)
        self.assertEqual(user32.GetCapture(),self.surface.hwnd)
        self.send(0x0202,14,17)
        self.assertFalse(user32.GetCapture())
        self.assertEqual([row[0] for row in self.mailbox.take()],['down','up'])
        self.assertEqual(self.target.qsize(),1)
        self.assertIs(self.surface._pointer_mailbox,self.mailbox)
        self.surface.set_pointer_mailbox(None)
        self.assertTrue(user32.GetWindowLongW(self.surface.hwnd,-20) & LayeredOverlay.WS_EX_TRANSPARENT)

    def test_actual_capture_changed_cancels_without_tk_or_activation(self):
        self.send(0x0201);self.send(0x0215)
        self.assertFalse(user32.GetCapture())
        self.assertEqual([row[0] for row in self.mailbox.take()],['down','cancel'])

    def test_actual_destroy_when_idle_reports_cancel_and_owner_can_close_class(self):
        hwnd = self.surface.hwnd
        self.assertTrue(user32.DestroyWindow(hwnd))
        self.assertIsNone(self.surface.hwnd)
        self.assertEqual(self.mailbox.take(),[('cancel',0,0)])
        self.assertEqual(self.target.get()['hwnd'],hwnd)

    def test_hwnd_apis_have_pointer_width_signatures(self):
        for name in ('GetCapture','SetCapture','GetForegroundWindow'):
            self.assertIs(getattr(user32,name).restype,ctypes.c_void_p)
        self.assertEqual(user32.SetCapture.argtypes,[ctypes.c_void_p])
        self.assertEqual(user32.ReleaseCapture.argtypes,[])


class NativePointerBoundaryTests(unittest.TestCase):
    def test_empty_unbind_keeps_non_editor_nonowner_close_compatible(self):
        surface = LayeredOverlay.__new__(LayeredOverlay)
        surface._pointer_mailbox = None; surface._owner_thread_id = -1
        with patch.object(user32,'GetCapture',side_effect=AssertionError('No new native call')):
            surface.set_pointer_mailbox(None)

    def test_pointer_callback_errors_do_not_escape_or_call_tk_logging(self):
        surface = LayeredOverlay.__new__(LayeredOverlay)
        surface._pointer_mailbox = object(); surface._pointer_dragging = True
        with patch.object(surface,'_reference_pointer_message',side_effect=OSError('native IO failure')),patch.object(user32,'GetCapture',return_value=0):
            self.assertEqual(surface._wndproc_callback(2**40+3,0x0201,0,0),0)
        self.assertTrue(surface._pointer_failed)


if __name__ == '__main__': unittest.main()
