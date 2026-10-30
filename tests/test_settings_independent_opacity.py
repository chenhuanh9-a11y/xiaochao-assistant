"""Real withdrawn Tk settings card/Scale bindings; no user app, HWND or game.

Controller callbacks and disk writes are recording substitutes. Existing runtime
opacity tests separately cover the actual display consumers.
"""
from pathlib import Path
import sys
import tkinter as tk
from types import SimpleNamespace
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'app'))
from desktop_main_ui import DesktopMainUI
from game_control_view import GameControlView
from opacity_controls import OpacityUpdates


class SettingsIndependentOpacityTests(unittest.TestCase):
    def setUp(self):
        try:
            self.root = tk.Tk()
        except tk.TclError as error:
            self.skipTest(f'Tk/Tcl unavailable: {error}')
        self.root.withdraw()
        self.root.geometry('560x600+0+0')
        self.addCleanup(self.root.destroy)
        self.previews, self.saved = [], []
        self.state = dict(overlay=20, reference=75, visible=False, reference_visible=True,
                          editing=True, scale=121)
        assistant = SimpleNamespace(opacity_var=tk.IntVar(self.root, 20),
            reference_opacity_var=tk.IntVar(self.root, 75), sheet_scale_var=tk.IntVar(self.root, 121),
            sheet_opacity=.20, reference_opacity=.75)
        def change(surface, variable, value):
            percent = round(float(value))
            variable.set(percent)
            self.state[surface] = percent
            setattr(assistant, 'sheet_opacity' if surface == 'overlay' else 'reference_opacity', percent / 100)
            assistant.display_opacity_updates.external(surface, percent)
            self.previews.append((surface, percent))
        assistant.change_sheet_opacity = lambda value: change('overlay', assistant.opacity_var, value)
        assistant.change_reference_opacity = lambda value: change('reference', assistant.reference_opacity_var, value)
        assistant.change_sheet_scale = lambda value: self.state.update(scale=round(float(value)))
        assistant._save_settings = lambda: self.saved.append(dict(self.state))
        self.game_view = GameControlView(self.root, opacity_var=assistant.opacity_var,
            reference_opacity_var=assistant.reference_opacity_var,
            commands={'change_sheet_opacity': assistant.change_sheet_opacity,
                      'change_reference_opacity': assistant.change_reference_opacity,
                      '_save_settings': assistant._save_settings})
        assistant.display_opacity_updates = self.game_view._opacity_updates
        self.ui = DesktopMainUI.__new__(DesktopMainUI)
        self.ui.root = self.ui.window = self.root
        self.ui.assistant = assistant
        self.ui._preference_traces = []
        # This is the production card and the exact builder _build_preferences
        # calls, with real widgets and the controller's canonical Tk variables.
        self.card = self.ui._card(self.root, padx=24, pady=22)
        self.card.pack(fill='both', expand=True)
        self.ui._build_display_sliders(self.card)
        self.addCleanup(self.cleanup_controls)
        self.root.update_idletasks()

    def cleanup_controls(self):
        self.ui._display_opacity_updates.commit()
        for variable, token in self.ui._preference_traces:
            variable.trace_remove('write', token)
        self.ui._preference_traces.clear()
        self.game_view.destroy()

    def scale(self, surface):
        return self.ui.display_scales['贴合小抄不透明度' if surface == 'overlay' else '定点地图不透明度']

    def move(self, surface, value):
        scale = self.scale(surface)
        # Invoke the real widget's value binding and registered Tcl command.
        # Tk may defer its own Scale command until idle on withdrawn windows.
        scale.set(value)
        self.root.update_idletasks()
        self.root.tk.call(scale.cget('command'), str(value))

    def release(self, surface, *, key=None):
        scale = self.scale(surface)
        # Execute the widget's installed binding with Tk's normal substitutions;
        # do not call the save coordinator directly in the test.
        pattern = '<KeyRelease>' if key else '<ButtonRelease-1>'
        script = scale.bind(pattern)
        values = {'%W': str(scale), '%K': key or '??', '%k': '0', '%N': '0',
                  '%b': '1', '%s': '0', '%t': '0', '%x': '0', '%y': '0',
                  '%X': '0', '%Y': '0', '%A': '{}', '%E': '0', '%D': '0',
                  '%f': '0', '%h': '0', '%w': '0', '%T': '5', '%#': '0'}
        for token, value in values.items():
            script = script.replace(token, value)
        self.root.tk.eval(script)

    def pump(self, milliseconds):
        self.root.after(milliseconds, self.root.quit)
        self.root.mainloop()

    def test_two_named_sliders_share_real_vars_and_external_value_labels(self):
        self.assertEqual(set(self.ui.display_scales),
                         {'贴合小抄不透明度', '定点地图不透明度', '大小'})
        self.assertIs(self.ui._display_opacity_updates, self.game_view._opacity_updates)
        for surface, var in (('overlay', self.ui.assistant.opacity_var),
                             ('reference', self.ui.assistant.reference_opacity_var)):
            scale = self.scale(surface)
            self.assertEqual(str(scale.cget('variable')), str(var))
            self.assertEqual((float(scale.cget('from')), float(scale.cget('to'))), (0, 100))
        self.ui.assistant.opacity_var.set(0)
        self.ui.assistant.reference_opacity_var.set(100)
        self.assertEqual(self.ui.display_scale_values['贴合小抄不透明度'].cget('text'), '0%')
        self.assertEqual(self.ui.display_scale_values['定点地图不透明度'].cget('text'), '100%')
        self.assertEqual(self.ui.display_scale_values['大小'].cget('text'), '121%')
        self.assertEqual(self.saved, [])

    def test_overlay_and_reference_commands_commit_independently_without_visibility_changes(self):
        self.move('overlay', 0)
        self.assertEqual(self.ui.assistant.reference_opacity_var.get(), 75)
        self.assertEqual(self.saved, [])
        self.release('overlay')
        self.assertEqual(self.previews, [('overlay', 0)])
        self.assertEqual((self.saved[-1]['overlay'], self.saved[-1]['reference']), (0, 75))
        self.move('reference', 100)
        self.assertEqual(self.ui.assistant.opacity_var.get(), 0)
        self.release('reference', key='Right')
        self.assertEqual(self.previews[-1], ('reference', 100))
        self.assertEqual((self.saved[-1]['overlay'], self.saved[-1]['reference']), (0, 100))
        self.assertEqual((self.state['visible'], self.state['reference_visible'], self.state['editing']),
                         (False, True, True))

    def test_drag_bursts_coalesce_and_release_does_not_duplicate_debounced_save(self):
        for value in range(101):
            self.move('overlay', value)
            self.move('reference', 100-value)
        self.assertEqual(self.saved, [])
        self.assertLessEqual(len(self.ui._display_opacity_updates.timers), 2)
        self.release('reference')
        self.assertEqual(len(self.saved), 1)
        self.assertEqual((self.saved[0]['overlay'], self.saved[0]['reference']), (100, 0))
        self.pump(330)
        self.assertEqual(len(self.saved), 1)

    def test_keyboard_without_release_still_saves_latest_values_once(self):
        self.move('reference', 0)
        self.move('reference', 100)
        self.assertEqual(self.saved, [])
        self.pump(330)
        self.assertEqual(len(self.saved), 1)
        self.assertEqual((self.saved[0]['overlay'], self.saved[0]['reference']), (20, 100))

    def test_other_view_change_does_not_leave_a_stale_slider_baseline(self):
        self.ui.assistant.change_reference_opacity(40)
        self.previews.clear()
        self.move('reference', 75)  # The card's original baseline, now a new change.
        self.release('reference')
        self.assertEqual(self.previews, [('reference', 75)])
        self.assertEqual(self.saved[-1]['reference'], 75)

    def test_two_views_pending_changes_share_latest_values_without_replay(self):
        self.game_view._opacity_changed('overlay', 80)
        self.game_view._opacity_changed('reference', 10)
        self.move('overlay', 40)
        self.move('reference', 90)
        self.release('overlay')
        self.pump(330)
        self.assertEqual((self.state['overlay'], self.state['reference']), (40, 90))
        self.assertEqual(len(self.saved), 1)
        self.assertNotIn(('overlay', 80), self.previews)
        self.assertNotIn(('reference', 10), self.previews)

    def test_direct_controller_write_cancels_only_older_surface_pending(self):
        self.move('overlay', 80)
        self.move('reference', 90)
        self.ui.assistant.change_sheet_opacity(35)
        self.pump(330)
        self.assertEqual((self.state['overlay'], self.state['reference']), (35, 90))
        self.assertNotIn(('overlay', 80), self.previews)
        self.assertEqual((self.saved[-1]['overlay'], self.saved[-1]['reference']), (35, 90))

    def test_hide_before_preview_tick_commits_and_keeps_shared_queue_open(self):
        self.ui._closed = False
        self.ui._cancel_map_key = lambda: None
        self.ui._close_help_images = lambda: None
        self.ui.assistant._hide_desktop_main_ui = lambda: None
        self.move('overlay', 62)
        self.ui.hide()
        self.assertEqual((self.state['overlay'], self.saved[-1]['overlay']), (62, 62))
        self.assertFalse(self.game_view._opacity_updates.closed)

    def test_unmap_before_debounce_commits_without_closing_shared_queue(self):
        self.move('reference', 63)
        self.pump(20)
        self.assertEqual(self.saved, [])
        script = self.root.bind('<Unmap>')
        # An Unmap on the settings window is the production hidden-page hook.
        # event_generate works for Unmap even though this fixture stays withdrawn.
        self.assertTrue(script)
        self.root.event_generate('<Unmap>')
        self.assertEqual(self.saved[-1]['reference'], 63)
        self.assertFalse(self.game_view._opacity_updates.closed)

    def test_settings_destroy_handler_before_preview_tick_commits_without_closing_owner(self):
        self.ui._close_home_previews = self.ui._close_help_images = lambda: None
        self.ui._support_window = self.ui._refresh_job = self.ui._update_dialog = None
        self.ui._brand_images = SimpleNamespace(close=lambda: None)
        self.ui.brand_icon = SimpleNamespace(_desktop_icon=None)
        self.ui._cancel_view_animation = lambda name: None
        self.ui._scroll_pages = {}
        self.move('reference', 64)
        self.ui._destroy(SimpleNamespace(widget=self.root))
        self.assertEqual((self.state['reference'], self.saved[-1]['reference']), (64, 64))
        self.assertFalse(self.game_view._opacity_updates.closed)
        self.assertEqual(self.game_view._opacity_updates.pending, {})

    def test_game_view_actual_destroy_commits_last_value_then_closes_owner(self):
        self.move('overlay', 61)
        self.game_view.destroy()
        self.assertEqual((self.state['overlay'], self.saved[-1]['overlay']), (61, 61))
        self.assertTrue(self.game_view._opacity_updates.closed)


class ExternalOpacityReconciliationTests(unittest.TestCase):
    def make(self):
        timers = {}
        def schedule(delay, callback):
            token = object()
            timers[token] = (delay, callback)
            return token
        def cancel(token):
            timers.pop(token, None)
        values, saves = {}, []
        updates = None
        def preview(surface, value):
            values[surface] = value
            updates.external(surface, value)
        updates = OpacityUpdates(schedule, cancel,
            {surface: lambda value, key=surface: preview(key, value)
             for surface in ('overlay', 'reference')}, lambda: saves.append(dict(values)))
        return updates, timers, values, saves

    def test_own_flush_preserves_dirty_until_commit_and_external_keeps_other_lane(self):
        updates, timers, values, saves = self.make()
        updates.change('overlay', 80)
        updates.change('reference', 90)
        updates.external('overlay', 35)
        self.assertEqual(updates.dirty, {'reference'})
        self.assertNotIn('overlay', updates.pending)
        updates.flush('reference')
        self.assertEqual(updates.dirty, {'reference'})
        self.assertIsNone(updates._flushing_surface)
        updates.commit()
        self.assertEqual(saves, [{'reference': 90}])
        self.assertEqual(timers, {})

    def test_preview_exception_always_clears_reentrancy_marker(self):
        updates, timers, values, saves = self.make()
        def fail(value):
            raise ValueError('preview failed')
        updates.previews['overlay'] = fail
        updates.change('overlay', 80)
        with self.assertRaisesRegex(ValueError, 'preview failed'):
            updates.flush('overlay')
        self.assertIsNone(updates._flushing_surface)
        updates.external('overlay', 35)
        self.assertFalse(updates.dirty)
        self.assertEqual(timers, {})


if __name__ == '__main__':
    unittest.main()
