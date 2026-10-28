"""Runtime opacity consumers with timer/native I/O substitutes; no HWND/game."""
import unittest
from types import SimpleNamespace
import numpy as np
from test_fixed_map_position_ui import load_methods, WidgetIO
from test_independent_opacity_controls import Timers, Var

NAMES = {'change_sheet_opacity', '_flush_sheet_opacity_update',
         'change_reference_opacity', '_flush_reference_opacity_update',
         '_commit_game_control_opacity'}
ns = load_methods('identityv_daily_assistant.py', 'DailyAssistant', NAMES)
ns['np'] = np
Owner = ns['DailyAssistant']

class RuntimeOpacityTests(unittest.TestCase):
    def make(self):
        h = Owner()
        timers = Timers()
        timers.after_cancel = timers.cancel
        h.root = timers
        h.closed = False
        h.sheet_opacity = h.reference_opacity = .55
        h.opacity_var, h.reference_opacity_var = Var(55), Var(55)
        h.opacity_label = WidgetIO()
        h._sync_game_control_view = lambda: None
        h._protect_display_window = lambda *args: None
        h._log = lambda *args, **kw: None
        h.current_bounds = (0, 0, 40, 40)
        h.drag_current_overlay = np.ones((40,40,4), np.uint8)
        h.alignment_succeeded = True
        h.sheet_move_mode = False
        h.sheet_visibility_enabled = h.reference_sheet_visibility_enabled = True
        h._reference_pixels = np.ones((20,20,4), np.uint8)
        h._reference_origin = (4, 5)
        h._sheet_visible_reference = ('16', '1', 'author')
        h._reference_sheet_context = lambda: ('nightmare', '16', '1', 'author')
        h._reference_sheet_may_show = lambda: True
        h._reference_sheet_is_visible = lambda: True
        h.overlay_calls, h.reference_calls, h.saved = [], [], []
        h._show_aligned_overlay = lambda pixels, position: h.overlay_calls.append((pixels, position, h.sheet_opacity))
        def present(pixels, position, opacity):
            h.reference_calls.append((pixels, position, opacity))
            return True
        h.reference_surface = SimpleNamespace(hwnd=1, show_bgra=present)
        h._save_settings = lambda: h.saved.append((h.sheet_opacity, h.reference_opacity,
            len(h.overlay_calls), len(h.reference_calls))) or True
        return h

    def test_independent_zero_to_full_uses_current_pixels_without_recognition(self):
        h = self.make()
        for alpha in ('0', '100'):
            h.change_sheet_opacity(alpha)
            h._flush_sheet_opacity_update()
        self.assertEqual([x[2] for x in h.overlay_calls], [0,1])
        self.assertEqual(h.reference_calls, [])
        for alpha in ('0', '100'):
            h.change_reference_opacity(alpha)
            h._flush_reference_opacity_update()
        self.assertEqual([x[2] for x in h.reference_calls], [0,1])
        self.assertTrue(all(x[0] is h._reference_pixels for x in h.reference_calls))
        self.assertTrue(h.sheet_visibility_enabled and h.reference_sheet_visibility_enabled)
        self.assertFalse(h.sheet_move_mode)
        self.assertEqual(h.saved, [])

    def test_actual_authorized_compositor_reuses_retained_surface_at_zero_and_full(self):
        from test_native_switch_atomic import NativeSwitchCompositorTests, fixture
        h, core, grant, expected, source, target = NativeSwitchCompositorTests.setup_chain(self, True, True)
        result = h._run_alignment_v2_for_current_frame_unlocked(fixture.IDS[1], '1', '1', expected)
        self.assertTrue(result['success'])
        h.root = SimpleNamespace(after_cancel=lambda *_: None)
        calls = []
        original = target.show_retained
        def show(packet, position, opacity):
            calls.append(opacity)
            return original(packet, position, opacity=opacity)
        target.show_retained = show
        authorization = core.display_authorization
        for alpha in (0., 1.):
            h.sheet_opacity = alpha
            h._sheet_opacity_after_id = 1
            h._flush_sheet_opacity_update()
        self.assertEqual(calls, [0., 1.])
        self.assertIs(h.aligned_overlay, target)
        self.assertIs(core.display_authorization, authorization)
        self.assertTrue(target.visible)

    def test_release_reads_both_variables_flushes_once_before_save(self):
        h = self.make()
        h.change_sheet_opacity('20')
        h.change_reference_opacity('30')
        h.opacity_var.set(70)
        h.reference_opacity_var.set(0)
        self.assertTrue(h._commit_game_control_opacity())
        self.assertEqual(h.saved, [(.7,0,1,1)])
        self.assertEqual(h.root.pending, {})

    def test_late_reference_timer_never_revives_changed_floor_or_hidden_context(self):
        for invalid in ('floor', 'hidden', 'closed'):
            with self.subTest(invalid=invalid):
                h = self.make()
                h.change_reference_opacity('90')
                if invalid == 'floor': h._reference_sheet_context = lambda: ('nightmare','16','2','author')
                if invalid == 'hidden': h._reference_sheet_is_visible = lambda: False
                if invalid == 'closed': h.closed = True
                h._flush_reference_opacity_update()
                self.assertEqual(h.reference_calls, [])
                self.assertEqual(h.overlay_calls, [])
                self.assertEqual(h.reference_opacity, .9)

    def test_editor_preview_minimum_does_not_change_zero_preference(self):
        h = self.make()
        h.sheet_move_mode = True
        h._reference_editor_is_current = lambda: True
        h._reference_position_edit = SimpleNamespace(position=(9,8))
        h._reference_editor_presented_pixels = h._reference_pixels
        h.reference_editor_surface = h.reference_surface
        h.change_reference_opacity('0')
        h._flush_reference_opacity_update()
        self.assertEqual(h.reference_calls[-1][2], .4)
        self.assertEqual(h.reference_opacity, 0)
        self.assertTrue(h.sheet_move_mode)
        # Finishing/cancelling editor returns normal rendering to the saved zero.
        h.sheet_move_mode = False
        h.change_reference_opacity('1')
        h.change_reference_opacity('0')
        h._flush_reference_opacity_update()
        self.assertEqual(h.reference_calls[-1][2], 0)
        self.assertEqual(h.reference_opacity_var.get(), 0)

    def test_invalid_editor_and_failed_alignment_are_not_revived(self):
        h = self.make()
        h.sheet_move_mode = True
        h._reference_editor_is_current = lambda: False
        h.alignment_succeeded = False
        h.change_reference_opacity('80')
        h.change_sheet_opacity('80')
        h._flush_reference_opacity_update()
        h._flush_sheet_opacity_update()
        self.assertEqual(h.reference_calls + h.overlay_calls, [])

if __name__ == '__main__': unittest.main()
