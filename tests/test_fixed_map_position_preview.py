"""Offline adapter callbacks and real controller persistence; native IO replaced."""
from pathlib import Path
import json
import os
import sys
import tempfile
import unittest
from unittest.mock import patch

import cv2
import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'tools'))
from preview_fixed_map_position import load_app, make_controller, PositionPreview


class RootIO:
    def update_idletasks(self): pass
    def winfo_x(self): return 60
    def winfo_y(self): return 80
    def winfo_width(self): return 490
    def winfo_height(self): return 230


class SurfaceIO:
    def __init__(self):
        self.hwnd, self.visible, self.mailbox, self._pointer_failed = 101, False, None, False
    def set_pointer_mailbox(self, value): self.mailbox = value
    def show_bgra(self, pixels, position, **kwargs):
        self.pixels, self.position = pixels.copy(), position
        self.visible = bool(self.visibility_guard())
        return self.visible
    def hide(self): self.visible = False
    def move_by(self, dx, dy):
        self.position = (self.position[0] + dx, self.position[1] + dy)
        return self.visible
    def close(self): self.set_pointer_mailbox(None); self.hide(); self.hwnd = None


class PreviewTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.state = Path(directory.name)
        supplied = os.environ.get('XINGTU_POSITION_PREVIEW_REFERENCE')
        self.image = Path(supplied) if supplied else self.state / 'reference.png'
        if not supplied:
            pixels = np.zeros((160, 240, 4), np.uint8)
            pixels[20:140, 20:220] = (70, 160, 230, 255)
            cv2.imencode('.png', pixels)[1].tofile(str(self.image))
        self.app = load_app(Path(os.environ.get('XINGTU_POSITION_PREVIEW_RESOURCES', ROOT)), self.state)
        from native_ui_layout import MonitorContext, RectPx
        self.monitors = (MonitorContext('primary', RectPx(0,0,1920,1080), RectPx(0,0,1920,1040),96),)
        for target, value in ((self.app, 'LayeredOverlay'),):
            item = patch.object(target, value, SurfaceIO); item.start(); self.addCleanup(item.stop)
        item = patch.object(self.app.ctypes.windll.user32, 'GetForegroundWindow', return_value=77)
        item.start(); self.addCleanup(item.stop)
        item = patch.object(self.app, 'window_process_id', return_value=os.getpid())
        item.start(); self.addCleanup(item.stop)
        for target in ('tkinter.Tk', 'tkinter.Toplevel', '_tkinter.create'):
            item = patch(target, side_effect=AssertionError('No GUI construction'))
            item.start(); self.addCleanup(item.stop)

    def controller(self):
        return make_controller(self.app, RootIO(), self.image, self.state, monitors=self.monitors)

    def test_actual_callbacks_mailbox_finish_and_reload(self):
        a = self.controller()
        preview = PositionPreview(self.app, a)
        self.addCleanup(preview.close)
        preview.poll()
        self.assertTrue(preview.surface.visible)
        commands = preview.callbacks()
        self.assertTrue(commands['edit']()['success'])
        x, y = a.reference_sheet_snapshot()['position']
        box = a._reference_pointer_mailbox
        for phase in ('down', 'move', 'up'):
            box.post(phase, x+5 if phase == 'down' else x-75, y+7 if phase == 'down' else y+47)
        preview.poll()
        expected = (x-80, y+40)
        self.assertEqual(a.reference_sheet_snapshot()['position'], expected)
        self.assertTrue(a.sheet_move_mode)
        self.assertFalse(a.settings_path.exists())
        self.assertTrue(commands['finish']()['persisted'])
        raw = json.loads(a.settings_path.read_text('utf-8'))
        self.assertEqual(raw['sheetPosition'], list(expected))
        self.assertFalse(raw['onboardingCompleted'])
        self.assertFalse(a.sheet_move_mode)
        self.assertIsNone(a.reference_editor_surface.mailbox)
        self.assertFalse(box.post('up', 900, 900))
        preview.poll()
        self.assertTrue(preview.surface.visible)
        self.assertEqual(preview.surface.position, expected)
        other = self.controller()
        self.assertEqual(other.sheet_position, expected)
        self.assertEqual(other._reference_editor_initial_position(preview.pixels.shape[1::-1]), expected)
        self.assertIsNone(a.gate.confirmed_id)
        self.assertFalse(a.game_foreground)
        self.assertFalse(hasattr(a, 'chest_auto_tap'))

    def test_external_focus_cancels_and_cannot_save_or_revive_stale_events(self):
        a = self.controller(); preview = PositionPreview(self.app, a)
        self.addCleanup(preview.close)
        self.assertTrue(preview.edit()['success'])
        box = a._reference_pointer_mailbox
        box.post('down', 60, 60)
        with patch.object(self.app, 'window_process_id', return_value=os.getpid()+1):
            preview.poll()
            self.assertFalse(a.sheet_move_mode)
            self.assertFalse(preview.surface.visible)
            self.assertFalse(preview.edit()['success'])
        self.assertFalse(box.post('up', 900, 900))
        self.assertFalse(a.settings_path.exists())


if __name__ == '__main__': unittest.main()
