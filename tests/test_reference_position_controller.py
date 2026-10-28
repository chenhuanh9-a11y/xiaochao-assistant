"""Position lifecycle using real controller/PNG/clamp/settings, native UI IO only replaced."""
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch
import json
import queue
import sys
import tempfile
import threading
import unittest
import cv2
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'app'))
import identityv_daily_assistant as app
from native_ui_layout import MonitorContext, RectPx


class SurfaceIO:
    def __init__(self):
        self.hwnd = 2**40+17; self.visible = False; self.mailbox = None; self.position = None
        self._pointer_failed = False
    def set_pointer_mailbox(self, mailbox): self.mailbox = mailbox
    def hide(self): self.visible = False
    def close(self): self.set_pointer_mailbox(None); self.hide(); self.hwnd = None; self.closed = True
    def show_bgra(self, pixels, position, **kwargs):
        self.pixels = pixels.copy(); self.position = position
        self.visible = bool(self.visibility_guard()); return self.visible
    def move_by(self, dx, dy):
        if not self.visible: return False
        self.position = (self.position[0]+dx, self.position[1]+dy); return True


class PositionControllerTests(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory(); self.addCleanup(folder.cleanup)
        self.folder = Path(folder.name)
        png = self.folder/'route.png'
        pixels = np.zeros((160, 240, 4), np.uint8); pixels[20:140, 20:220] = (70, 160, 230, 255)
        cv2.imencode('.png', pixels)[1].tofile(str(png))
        self.a = a = app.DailyAssistant.__new__(app.DailyAssistant)
        a.closed = False; a.game_foreground = False; a.game_mode = 'nightmare'; a.game_hwnd = 88
        a.gate = SimpleNamespace(selected_id=None, confirmed_id=None)
        item = SimpleNamespace(id='m1', layers={'1':png}, name='Fixture map')
        a.pack = SimpleNamespace(maps=[item], by_id={'m1':item}, default_entry_floor=1)
        a.current_floor = '1'; a.current_bounds = (0, 0, 1920, 1080)
        a.route_profile_id = a.preferred_route_profile_id = 'wang-qingxin'
        a.author_route_catalog = None
        a.sheet_position = None; a.sheet_scale = 1.; a.sheet_opacity = .7
        a.sheet_move_mode = False; a.sheet_move_generation = 0; a._reference_pointer_mailbox = None
        a.reference_surface = None; a.sheet_window = None; a.active_map_cycle_id = None
        a.surface_visibility_generation = 0; a.map_is_open = False; a.floor_required = False
        a.events = queue.Queue(); a.urgent_events = queue.Queue(); a.root = Mock()
        a.ui_monitors = [MonitorContext('main', RectPx(0,0,1920,1080), RectPx(0,0,1920,1040),96)]
        a._rect_for_window = lambda window:RectPx(30,30,100,100)
        a._reference_editor_focus_allowed = lambda:True  # Native foreground IO only.
        a._protect_display_window = Mock()
        a._shared_structure_mode_enabled = lambda:False
        a._display_pixels_for_source = lambda path,role:app.imread_unicode(path,cv2.IMREAD_UNCHANGED)
        a._reference_sheet_may_show = lambda:False  # No identity/pose is authorized by this preview.
        a._reference_sheet_is_visible = lambda:False
        a._log = Mock()
        a.settings_path = self.folder/'settings.json'; a.settings = {}
        a.hotkey_name = 'G'; a.action_hotkeys = {key:('F9',120) for key in app.ACTION_ORDER}
        a.compact_position = None; a.interface_opacity = .8; a.experimental_auto_overlay = True
        a.zhan10_coffin_variant_id = 'variant-01'; a.onboarding_completed = True
        a.round_end_hint_shown = False; a.active_confirmation = None
        for target in ('tkinter.Tk','tkinter.Toplevel'):
            guard = patch(target,side_effect=AssertionError('No GUI')); guard.start(); self.addCleanup(guard.stop)
        factory = patch.object(app,'LayeredOverlay',SurfaceIO); factory.start(); self.addCleanup(factory.stop)

    def send(self, *samples):
        box = self.a._reference_pointer_mailbox
        for phase,x,y in samples: box.post(phase,x,y)
        while not self.a.urgent_events.empty():
            self.a._consume_reference_pointer_events(self.a.urgent_events.get())

    def test_settings_foreground_can_edit_and_only_finish_commits_real_settings(self):
        a = self.a
        self.assertTrue(a.reference_sheet_snapshot()['canAdjust'])
        self.assertTrue(a.move_sheet()['success'])
        start = a.reference_sheet_snapshot()['position']
        self.send(('down',start[0]+4,start[1]+6),('up',start[0]-36,start[1]+46))
        first = a.reference_sheet_snapshot()['position']
        self.assertTrue(a.sheet_move_mode)
        self.assertIsNone(a.sheet_position)
        self.assertFalse(a.settings_path.exists())
        self.send(('down',first[0]+2,first[1]+3),('up',first[0]-18,first[1]+33))
        expected = a.reference_sheet_snapshot()['position']
        self.assertTrue(a.finish_reference_sheet_move()['persisted'])
        self.assertEqual(a.sheet_position, expected)
        self.assertEqual(json.loads(a.settings_path.read_text('utf8'))['sheetPosition'], list(expected))
        self.assertFalse(a.sheet_move_mode)
        self.assertFalse(a.reference_editor_surface.visible)
        self.assertIsNone(a.reference_editor_surface.mailbox)
        # A new edit consumes the real saved field and remains recoverable.
        self.assertTrue(a.move_sheet()['success'])
        self.assertEqual(a.reference_sheet_snapshot()['position'], expected)
        a.root.after.assert_not_called()

    def test_first_edit_uses_normal_right_top_default_on_game_monitor(self):
        a = self.a
        a.ui_monitors.append(MonitorContext('left',RectPx(-1600,0,0,1000),RectPx(-1600,0,0,960),144))
        a._rect_for_window = lambda _:RectPx(-1500,10,-1450,60)
        self.assertTrue(a.move_sheet()['success'])
        width = a._reference_position_edit.size[0]
        self.assertEqual(a.reference_sheet_snapshot()['position'], (1920-width-20,35))

    def test_valid_normal_surface_origin_is_not_reanchored(self):
        a = self.a; a._reference_pixels = a._reference_editor_pixels()
        a._sheet_visible_reference = a._reference_sheet_context()[1:]
        a._reference_origin = (210,180)
        self.assertTrue(a.move_sheet()['success'])
        self.assertEqual(a.reference_sheet_snapshot()['position'], (210,180))

    def test_removed_monitor_saved_position_is_visible_when_editing_again(self):
        a = self.a
        a.ui_monitors.append(MonitorContext('left',RectPx(-1600,0,0,1000),RectPx(-1600,0,0,960),144))
        a.sheet_position = (-1400,100)
        a.move_sheet(); self.assertEqual(a.reference_sheet_snapshot()['position'],(-1400,100))
        a.finish_reference_sheet_move()
        a.ui_monitors = [MonitorContext('new',RectPx(0,0,1280,800),RectPx(0,0,1280,760),192)]
        a.current_bounds = None
        self.assertTrue(a.move_sheet()['success'])
        x,y = a.reference_sheet_snapshot()['position']; w,h = a._reference_position_edit.size
        self.assertGreaterEqual(x,5); self.assertGreaterEqual(y,5)
        self.assertLessEqual(x+w,1275); self.assertLessEqual(y+h,755)

    def test_hide_discards_pending_samples_and_cannot_revive_edit(self):
        a = self.a; a.move_sheet(); box = a._reference_pointer_mailbox
        box.post('down',30,30); marker = a.urgent_events.get()
        a.hide_sheet(); self.assertFalse(box.post('up',900,900))
        a._consume_reference_pointer_events(marker)
        self.assertFalse(a.sheet_move_mode); self.assertIsNone(a.sheet_position)
        self.assertFalse(a.reference_editor_surface.visible)

    def test_stale_context_and_external_focus_cancel_without_saving(self):
        a = self.a
        for invalidate in (lambda:setattr(a,'current_floor','2'),lambda:setattr(a,'_reference_editor_focus_allowed',lambda:False)):
            self.assertTrue(a.move_sheet()['success'])
            invalidate(); a._check_reference_position_editor()
            self.assertFalse(a.sheet_move_mode); self.assertIsNone(a.sheet_position)
            a.current_floor = '1'; a._reference_editor_focus_allowed = lambda:True

    def test_capture_invalidation_cancels_editor_and_rejects_old_marker(self):
        a = self.a; a.move_sheet(); old = a._reference_pointer_mailbox
        old.post('down',10,10); marker = a.urgent_events.get()
        for name in ('_invalidate_alignment_work','_invalidate_round_group_edge_correction','_clear_mouse_tracking'):
            setattr(a,name,Mock())
        a.capture_generation = 0
        a._invalidate_capture_work()
        a._consume_reference_pointer_events(marker)
        self.assertFalse(a.sheet_move_mode); self.assertIsNone(a.sheet_position)

    def test_cancel_native_event_and_failed_save_do_not_leave_input_capture(self):
        a = self.a; a.move_sheet(); self.send(('cancel',0,0))
        self.assertFalse(a.sheet_move_mode); self.assertIsNone(a.reference_editor_surface.mailbox)
        a.move_sheet()
        with patch.object(app,'atomic_write_text',side_effect=OSError('read-only fixture')):
            result = a.finish_reference_sheet_move()
        self.assertFalse(result['persisted']); self.assertFalse(a.sheet_move_mode)
        self.assertIsNone(a.reference_editor_surface.mailbox)

    def test_real_end_round_cancels_pending_editor_without_committing_draft(self):
        a = self.a; a.move_sheet(); old = a._reference_pointer_mailbox
        old.post('down',10,10); marker = a.urgent_events.get()
        a.round_work_active = True; a._round_identity_lock_context = lambda *args:None
        a.floor_var = SimpleNamespace(set=Mock())
        for name in ('_ui_action_begin','_resolve_route_issue','_alignment_v2_reset_round',
                '_invalidate_round_group_edge_correction','_clear_round_layer_group','_reset_locked_fit_retry',
                '_update_floor_buttons','_hide_round_recovery','_refresh_candidates','set_expanded','_set_status'):
            setattr(a,name,Mock())
        a.hide_route = lambda **kwargs:a.hide_sheet()  # Only unrelated route presentation IO.
        self.assertTrue(a.end_round(notify=False)['success'])
        a._consume_reference_pointer_events(marker)
        self.assertFalse(a.sheet_move_mode)
        self.assertIsNone(a.sheet_position)
        self.assertIsNone(json.loads(a.settings_path.read_text('utf8'))['sheetPosition'])
        self.assertFalse(old.post('up',90,90))

    def test_real_close_disposes_editor_on_owner_and_late_queue_cannot_show(self):
        a = self.a; a.move_sheet(); surface = a.reference_editor_surface
        box = a._reference_pointer_mailbox; box.post('down',10,10); marker = a.urgent_events.get()
        a.stop_event = threading.Event(); a.root.tk.call.return_value = ()
        a._display_capture_lock = threading.RLock(); a._display_capture_active = False
        a._alignment_v2_is_owner_thread = lambda:True
        for name in ('_cancel_drag_boundary_prompt_schedule','_cancel_drag_boundary_open_schedule',
                '_destroy_drag_boundary_prompt','_service_display_capture'):
            setattr(a,name,Mock())
        a.hide_route = lambda **kwargs:a.hide_sheet()
        a.close(); a._consume_reference_pointer_events(marker)
        self.assertTrue(a.closed); self.assertTrue(surface.closed)
        self.assertIsNone(surface.mailbox); self.assertIsNone(a.reference_editor_surface)
        a.root.destroy.assert_called_once()

    def test_shell_unmap_cancel_during_entry_cannot_reopen_preview(self):
        a = self.a
        a.sheet_window = SimpleNamespace(winfo_exists=lambda:True,withdraw=lambda:a._set_sheet_move_mode(False))
        result = a.move_sheet()
        self.assertFalse(result['success']); self.assertFalse(a.sheet_move_mode)
        self.assertFalse(a.reference_editor_surface.visible)
        self.assertIsNone(a._reference_pointer_mailbox)

    def test_finish_drains_pending_down_up_before_persisting_final_position(self):
        a = self.a; self.assertTrue(a.move_sheet()['success'])
        start = a.reference_sheet_snapshot()['position']
        box = a._reference_pointer_mailbox
        box.post('down',start[0]+4,start[1]+6)
        box.post('up',start[0]-36,start[1]+46)
        # The explicit finish command may run before the ordinary event poll.
        result = a.finish_reference_sheet_move()
        expected = (start[0]-40,start[1]+40)
        self.assertTrue(result['persisted'])
        self.assertEqual(a.sheet_position,expected)
        self.assertEqual(json.loads(a.settings_path.read_text('utf8'))['sheetPosition'],list(expected))
        while not a.urgent_events.empty():a._consume_reference_pointer_events(a.urgent_events.get())
        self.assertFalse(a.sheet_move_mode)
        self.assertEqual(a.sheet_position,expected)

    def test_finish_drains_pending_cancel_and_preserves_previously_saved_position(self):
        a = self.a; a.sheet_position = (400,200); self.assertTrue(a._save_settings())
        saved = a.settings_path.read_bytes()
        self.assertTrue(a.move_sheet()['success'])
        self.send(('down',404,206),('up',364,246))
        self.assertNotEqual(a.reference_sheet_snapshot()['position'],a.sheet_position)
        a._reference_pointer_mailbox.post('cancel')
        result = a.finish_reference_sheet_move()
        self.assertFalse(result['success'])
        self.assertFalse(result['persisted'])
        self.assertEqual(result['reason'],'reference-move-cancelled')
        self.assertEqual(a.sheet_position,(400,200))
        self.assertEqual(a.settings_path.read_bytes(),saved)
        self.assertFalse(a.sheet_move_mode)


if __name__ == '__main__': unittest.main()
