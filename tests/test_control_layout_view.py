"""Isolated real Tk geometry/render contracts; no product process or desktop input."""
from copy import deepcopy
from pathlib import Path
import sys
import tkinter as tk
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'app'))
from control_layout import preset_layout
from game_control_view import GameControlView
from desktop_display import WindowDisplay
from desktop_rounded import RoundedButton


class ControlLayoutViewTests(unittest.TestCase):
    def font_pixels(self, description):
        if str(description) in set(self.root.tk.call('font', 'names')):
            return int(self.root.tk.call('font', 'configure', description, '-size'))
        # Styles may store a font tuple instead of a name. Font(font=tuple)
        # normalizes it to points, so inspect its original pixel definition.
        return int(self.root.tk.splitlist(description)[1])

    def setUp(self):
        self.root = tk.Tk()
        self.root.withdraw()
        self.host = tk.Toplevel(self.root)
        self.host.overrideredirect(True)
        self.host.geometry('352x250+20000+20000')
        self.calls = []
        self.snapshot = dict(userVisible=True, referenceEnabled=True, overlayVisible=False,
            referenceVisible=False, referenceCanAdjust=True, referenceAdjusting=False,
            roundStartEnabled=True, mode='difficult', controlFloorText='F2 已核对')
        commands = {name: (lambda *args, key=name: self.calls.append((key, args))) for name in (
            'start_round', 'end_round', 'toggle_round', 'collapse_control', 'hide_control',
            'toggle_sheet_visibility', 'set_reference_sheet_visible', 'move_sheet',
            'finish_reference_sheet_move', 'cancel_reference_sheet_move', '_open_desktop_main_ui',
            'open_control_layout_settings', 'capture_now', 'previous_candidate', 'next_candidate',
            'confirm_route', '_start_map_reselection', '_open_recognition_details')}
        commands['get_control_snapshot'] = lambda: deepcopy(self.snapshot)
        self.view = GameControlView(self.host, commands=commands)
        self.view.pack(fill='both', expand=True)
        self.view.render(self.snapshot, map_name='测试地图')
        self.settle()

    def tearDown(self):
        self.root.destroy()

    def settle(self, height=None):
        if height is not None:
            self.view.set_available_height(height)
        self.root.update()
        width, height = self.view.expanded_size()
        self.host.geometry(f'{width}x{height}+20000+20000')
        self.root.update()

    def shown_modules(self):
        return [next(key for key, value in self.view.modules.items() if value is child)
                for child in self.view.module_host.pack_slaves()]

    def test_default_and_idempotent_detached_layout(self):
        self.assertEqual(self.shown_modules(), ['display', 'round'])
        identities = {key: str(widget) for key, widget in self.view.modules.items()}
        layout = preset_layout('common')
        self.view.apply_layout(layout)
        for _ in range(30):
            self.view.apply_layout(deepcopy(layout))
            self.view.render(self.snapshot, map_name='测试地图')
        layout['enabled'].clear()
        self.settle()
        self.assertEqual(self.shown_modules(), ['display', 'map', 'round'])
        self.assertEqual(identities, {key: str(widget) for key, widget in self.view.modules.items()})
        self.assertEqual(self.calls, [])

    def test_order_and_independent_opacity_options(self):
        layout = preset_layout()
        layout.update(order=['recognition', 'round', 'map', 'display'], enabled=['display', 'recognition'])
        layout['display']['referenceOpacity'] = True
        self.view.apply_layout(layout)
        self.settle()
        self.assertEqual(self.shown_modules(), ['recognition', 'display'])
        self.assertFalse(self.view.opacity_cells['overlay'].winfo_manager())
        self.assertEqual(self.view.opacity_cells['reference'].winfo_manager(), 'pack')

    def test_empty_layout_retains_header_and_more_menu_recovery(self):
        layout = preset_layout()
        layout['enabled'] = []
        self.view.apply_layout(layout)
        self.view.render(self.snapshot, bindings={'toggle-panel': {'text': 'Ctrl+Shift+F12'}})
        self.settle()
        self.assertEqual(self.shown_modules(), [])
        self.assertTrue(self.view.header.winfo_ismapped())
        self.assertTrue(self.view.more_button.winfo_ismapped())
        self.assertEqual(self.view.more_menu.entrycget(1, 'accelerator'), '')
        self.assertEqual(self.view.more_menu.entrycget(2, 'accelerator'), 'Ctrl+Shift+F12')

    def test_editing_forces_fixed_actions_and_refuses_snapshot_collapse(self):
        self.snapshot.update(referenceAdjusting=True, controlCollapsed=True)
        layout = preset_layout()
        layout['enabled'] = []
        self.view.apply_layout(layout)
        self.view.render(self.snapshot)
        self.settle(210)
        self.assertFalse(self.view._collapsed)
        self.assertEqual(str(self.view.collapse_button.cget('state')), 'disabled')
        for button in (self.view.finish_edit_button, self.view.cancel_edit_button):
            self.assertTrue(button.winfo_ismapped())
            bottom = button.winfo_rooty()-self.host.winfo_rooty()+button.winfo_height()
            self.assertLessEqual(bottom, self.host.winfo_height())
        self.view.finish_edit_button.invoke()
        self.view.cancel_edit_button.invoke()
        self.assertEqual([item[0] for item in self.calls], ['finish_reference_sheet_move', 'cancel_reference_sheet_move'])

    def test_runtime_rebinding_long_keys_use_separate_nonoverlapping_row(self):
        self.view.render(self.snapshot, bindings={'toggle-sheet-visibility': {'text': 'Ctrl+Alt+Shift+PageDown'}})
        self.settle()
        button, chip = self.view.quick_sheet_visibility_button, self.view._visibility_key
        self.assertGreaterEqual(chip.winfo_y(), button.winfo_y()+button.winfo_height())
        self.assertLessEqual(chip.winfo_width(), self.view.overlay_controls.winfo_width())
        self.view.render(self.snapshot, bindings={'toggle-sheet-visibility': {'text': 'F3'}})
        self.assertEqual(chip.cget('text'), 'F3')
        self.view.render(self.snapshot, bindings={})
        self.assertFalse(chip.winfo_manager())

    def test_toggles_report_intent_and_summary_reports_actual_visibility(self):
        self.assertIn('开', self.view.quick_sheet_visibility_button.cget('text'))
        self.assertNotIn('等待', self.view.quick_sheet_visibility_button.cget('text'))
        self.assertIn('暂不可见', self.view.summary_label.cget('text'))
        self.assertIn('测试地图', self.view.identity_label.cget('text'))
        self.assertIn('F2', self.view.identity_label.cget('text'))

    def test_repeated_render_and_geometry_leave_no_idle_loop(self):
        for _ in range(100):
            self.view.render(self.snapshot)
            self.view.expanded_size()
        self.settle()
        self.assertIsNone(self.view._geometry_pending)
        pending = self.root.tk.call('after', 'info')
        self.assertFalse(pending)

    def test_destroy_releases_owned_fonts_on_tk_thread_before_late_gc(self):
        fonts = list(self.view._pixel_fonts.values())
        names = {str(font) for font in fonts}
        self.assertTrue(names <= set(self.root.tk.call('font', 'names')))
        self.view.destroy()
        self.assertFalse(names & set(self.root.tk.call('font', 'names')))
        self.assertTrue(all(not font.delete_font for font in fonts))
        self.assertEqual(self.view._pixel_fonts, {})
        self.assertEqual(self.view._scale_metrics, [])

    def test_destroy_preview_does_not_leave_live_panel_style_pointing_at_deleted_font(self):
        preview = GameControlView(self.host, commands={})
        self.view.set_display_scale(1.5)
        preview.set_display_scale(1.5)
        self.view.render(self.snapshot)
        preview.render(self.snapshot)
        button = self.view.collapse_button
        style = button._skins.style
        style_name = str(button.cget('style'))
        before = str(style.lookup(style_name, 'font'))
        self.assertIn('-20', before)
        preview.destroy()
        after = str(style.lookup(style_name, 'font'))
        self.assertEqual(after, before)
        self.assertNotIn('font', after)  # No preview-owned named font in a shared style.

    def test_locked_and_pending_candidates_are_disabled(self):
        self.view.render(self.snapshot)
        self.assertEqual(self.view.candidate_label.cget('text'), '尚无候选')
        self.assertEqual(str(self.view.recognition_buttons['confirm_route'].cget('state')), 'disabled')
        self.view.render(dict(self.snapshot, candidateName='困难候选13'))
        self.assertIn('困难候选13', self.view.candidate_label.cget('text'))
        self.assertEqual(str(self.view.recognition_buttons['confirm_route'].cget('state')), 'normal')
        for change in ({'locked': True}, {'pending': True}, {'mapOpen': False}):
            self.view.render(dict(self.snapshot, **change))
            for command in ('previous_candidate', 'next_candidate', 'confirm_route'):
                self.assertEqual(str(self.view.recognition_buttons[command].cget('state')), 'disabled')

    def test_missing_commands_disable_actions(self):
        self.view._commands.pop('capture_now')
        self.view._refresh_menu()
        self.assertEqual(self.view.more_menu.entrycget(4, 'state'), 'disabled')
        isolated = GameControlView(self.host, commands={})
        try:
            self.assertEqual(str(isolated.recognition_buttons['capture_now'].cget('state')), 'disabled')
        finally:
            isolated.destroy()

    def test_all_modules_small_height_and_dpi_keep_fixed_regions(self):
        layout = preset_layout('common')
        layout['enabled'] = list(layout['order'])
        layout['display'] = dict.fromkeys(layout['display'], True)
        self.view.apply_layout(layout)
        self.snapshot['referenceAdjusting'] = True
        for scale in (1, 1.5, 2):
            self.view.set_display_scale(scale)
            self.view.render(self.snapshot)
            self.settle(round(220*scale))
            self.assertTrue(self.view.scrollbar.winfo_ismapped())
            for widget in (self.view.header, self.view.edit_bar):
                self.assertGreaterEqual(widget.winfo_y(), 0)
                self.assertLessEqual(widget.winfo_y()+widget.winfo_height(), self.host.winfo_height())
            self.assertEqual(self.view.expanded_size()[0], round(352*scale))

    def test_font_pixels_spacing_and_named_fonts_are_idempotent(self):
        initial_scaling = float(self.root.tk.call('tk', 'scaling'))
        tracked = (self.view.status_title, self.view.quick_sheet_visibility_button,
                   self.view.finish_edit_button, self.view._visibility_key)
        base_pixels = [abs(int(widget._control_pixel_font.cget('size'))) for widget in tracked]
        base_lines = [int(self.root.tk.call('font', 'metrics', widget.cget('font'), '-linespace'))
                      for widget in tracked]
        for scale in (1, 1.5, 2, 1):
            self.view.set_display_scale(scale)
            self.view.render(self.snapshot)
            self.settle(round(240*scale))
            for widget, pixels, lines in zip(tracked, base_pixels, base_lines):
                rendered_font = (widget._skins.style.lookup(str(widget.cget('style')), 'font')
                    if isinstance(widget, RoundedButton) else widget.cget('font'))
                self.assertEqual(self.font_pixels(rendered_font), -round(pixels*scale))
                actual_line = int(self.root.tk.call('font', 'metrics', rendered_font, '-linespace'))
                # Hinting adds fixed leading at small CJK sizes; the exact
                # negative pixel assertion above is the scaling authority.
                self.assertAlmostEqual(actual_line, lines*scale, delta=5)
            self.assertEqual(int(self.view.quick_sheet_visibility_button.cget('padx')), round(5*scale))
        names = set(self.root.tk.splitlist(self.root.tk.call('font', 'names')))
        for _ in range(30):
            self.view.set_display_scale(2)
        self.assertEqual(names, set(self.root.tk.splitlist(self.root.tk.call('font', 'names'))))
        self.assertEqual(initial_scaling, float(self.root.tk.call('tk', 'scaling')))

    def test_window_display_then_view_scaling_does_not_compound_fonts(self):
        # Exercise the editor host's real recursive scaler, followed by the
        # same on_scale contract used by the view's normal controller host.
        display = WindowDisplay.__new__(WindowDisplay)
        display.root, display.window = self.root, self.host
        display._closed, display._widgets = False, {}
        for scale in (1, 1.5, 2, 1.5, 1):
            display.scale = scale
            display.apply_widgets(self.view)
            self.view.set_display_scale(scale)
            self.view.render(self.snapshot)
            self.settle(round(260*scale))
            for widget in (self.view.status_title, self.view.quick_sheet_visibility_button,
                           self.view.finish_edit_button):
                rendered_font = (widget._skins.style.lookup(str(widget.cget('style')), 'font')
                    if isinstance(widget, RoundedButton) else widget.cget('font'))
                self.assertEqual(self.font_pixels(rendered_font), -round(13*scale))


if __name__ == '__main__':
    unittest.main()
