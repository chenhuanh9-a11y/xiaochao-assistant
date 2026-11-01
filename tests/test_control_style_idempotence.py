"""Real Tk font/style regression; no game, visible UI, focus or timing benchmark.

The root and offscreen host stay withdrawn throughout. The observer counts actual
ttk style setters while forwarding queries and setters to the real interpreter.
"""
from contextlib import contextmanager
from copy import deepcopy
from pathlib import Path
import sys
import tkinter as tk
from tkinter import ttk
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'app'))
from desktop_rounded import RoundedButton
from desktop_theme import BUTTON_TEXT, PRIMARY
from game_control_view import GameControlView


class ControlStyleIdempotenceTests(unittest.TestCase):
    def setUp(self):
        try:
            self.root = tk.Tk()
        except tk.TclError as error:
            self.skipTest(f'Real Tk runtime unavailable: {error}')
        self.root.withdraw()
        self.addCleanup(self.root.destroy)
        self.host = tk.Toplevel(self.root)
        self.host.withdraw()
        self.host.overrideredirect(True)
        self.host.geometry('352x250+20000+20000')
        self.snapshot = dict(userVisible=True, referenceEnabled=True,
            overlayVisible=False, referenceVisible=False, referenceCanAdjust=True,
            referenceAdjusting=False, roundStartEnabled=True, roundActive=False,
            mode='difficult', modeChangeEnabled=True, controlFloorText='F2 verified',
            candidateName='Fixture map', mapOpen=True, locked=False, pending=False)
        commands = {name: (lambda *args: None) for name in (
            'start_round', 'end_round', 'toggle_round', 'collapse_control', 'hide_control',
            'toggle_sheet_visibility', 'set_reference_sheet_visible', 'move_sheet',
            'finish_reference_sheet_move', 'cancel_reference_sheet_move', '_open_desktop_main_ui',
            'open_control_layout_settings', 'capture_now', 'previous_candidate', 'next_candidate',
            'confirm_route', '_start_map_reselection', '_open_recognition_details', 'set_game_mode')}
        commands['get_control_snapshot'] = lambda: deepcopy(self.snapshot)
        self.view = GameControlView(self.host, commands=commands)
        self.view.pack(fill='both', expand=True)
        self.view.render(self.snapshot, map_name='Fixture map')
        self.root.update_idletasks()

    @contextmanager
    def style_sets(self):
        writes = []
        original = ttk.Style.configure

        def observed(style, name, query_opt=None, **kwargs):
            if kwargs:
                writes.append((str(name), dict(kwargs)))
            return original(style, name, query_opt, **kwargs)

        with patch.object(ttk.Style, 'configure', observed):
            yield writes

    def button_metrics(self):
        return [(widget, attributes['font'])
                for widget, attributes, _manager, _geometry in self.view._scale_metrics
                if isinstance(widget, RoundedButton) and 'font' in attributes]

    def font_parts(self, button):
        name = str(button.cget('style'))
        value = button._skins.style.configure(name, 'font')
        return tuple(map(str, self.root.tk.splitlist(value)))

    def assert_pixel_fonts(self):
        for button, base in self.button_metrics():
            expected = (str(base[0]), str(-max(1, round(base[1]*self.view._scale))),
                        *map(str, base[2:]))
            self.assertEqual(self.font_parts(button), expected, str(button))
            self.assertLess(int(self.font_parts(button)[1]), 0)

    def appearance(self, button):
        style = button._skins.style
        name = str(button.cget('style'))
        return dict(style=name, text=button.cget('text'), state=str(button.cget('state')),
                    fg=style.lookup(name, 'foreground'),
                    disabled=style.lookup(name, 'foreground', ('disabled',)),
                    active=style.lookup(name, 'foreground', ('active',)),
                    foregroundMap=style.map(name, 'foreground'),
                    layout=style.layout(name),
                    props={key: button.cget(key) for key in
                           ('bg', 'fg', 'activebackground', 'activeforeground',
                            'disabledbackground', 'disabledforeground')})

    def test_same_snapshot_render_does_not_set_any_style(self):
        before = {str(button): self.appearance(button) for button, _ in self.button_metrics()}
        with self.style_sets() as writes:
            self.view.render(self.snapshot, map_name='Fixture map')
            self.view.render(deepcopy(self.snapshot), map_name='Fixture map')
            self.root.update_idletasks()
        self.assertEqual(writes, [], 'Unchanged render must not invalidate global ttk styles')
        self.assert_pixel_fonts()
        self.assertEqual({str(button): self.appearance(button)
                          for button, _ in self.button_metrics()}, before)
        self.assertEqual(self.host.state(), 'withdrawn')

    def test_external_font_change_is_repaired_without_changing_button_appearance(self):
        button = self.view.collapse_button
        style = button._skins.style
        name = str(button.cget('style'))
        appearance = self.appearance(button)
        style.configure(name, font=('Arial', -7, 'normal', 'italic'))
        with self.style_sets() as writes:
            self.view.render(self.snapshot, map_name='Fixture map')
        self.assertTrue(any(key == name and 'font' in options for key, options in writes))
        self.assert_pixel_fonts()
        self.assertEqual(self.appearance(button), appearance)
        with self.style_sets() as repeated:
            self.view.render(self.snapshot, map_name='Fixture map')
        self.assertEqual(repeated, [])

    def test_shared_preview_style_repair_survives_preview_destruction(self):
        self.view.set_display_scale(1.5)
        self.view.render(self.snapshot, map_name='Fixture map')
        preview = GameControlView(self.host, commands={})
        preview.set_display_scale(1.5)
        preview.render(self.snapshot, map_name='Fixture map')
        self.addCleanup(lambda: preview.destroy() if preview.winfo_exists() else None)
        live_button = self.view.collapse_button
        preview_button = preview.collapse_button
        name = str(live_button.cget('style'))
        self.assertEqual(str(preview_button.cget('style')), name)
        preview_button._skins.style.configure(name, font=('Arial', -8, 'normal', 'roman'))
        self.view.render(self.snapshot, map_name='Fixture map')
        self.assert_pixel_fonts()
        before = self.font_parts(live_button)
        preview.destroy()
        self.root.update_idletasks()
        self.assertEqual(self.font_parts(live_button), before)
        with self.style_sets() as writes:
            self.view.render(self.snapshot, map_name='Fixture map')
        self.assertEqual(writes, [])

    def test_dpi_transitions_keep_negative_pixel_fonts_and_steady_render_is_idempotent(self):
        for scale in (1.0, 1.25, 1.5, 2.0, 1.0):
            with self.subTest(scale=scale):
                self.view.set_display_scale(scale)
                self.view.render(self.snapshot, map_name='Fixture map')
                self.assert_pixel_fonts()
                with self.style_sets() as writes:
                    self.view.render(self.snapshot, map_name='Fixture map')
                self.assertEqual(writes, [])

    def test_selected_disabled_and_new_color_styles_keep_their_visual_contract(self):
        self.view.set_display_scale(1.5)
        selected = dict(self.snapshot, mode='nightmare', modeChangeEnabled=False)
        self.view.render(selected, map_name='Fixture map')
        self.assertEqual(self.view.mode_buttons['nightmare'].cget('bg'), PRIMARY)
        self.assertEqual(self.view.mode_buttons['nightmare'].cget('fg'), 'white')
        self.assertEqual(self.view.mode_buttons['difficult'].cget('fg'), BUTTON_TEXT)
        for button in self.view.mode_buttons.values():
            self.assertEqual(str(button.cget('state')), 'disabled')
            self.assertEqual(button._skins.style.lookup(str(button.cget('style')),
                             'foreground', ('disabled',)), button.cget('disabledforeground'))
        # A genuinely new style must still receive the physical font while its
        # own image element, colors, disabled map, text and state are retained.
        button = self.view.collapse_button
        old_name = str(button.cget('style'))
        button.configure(bg='#13579b', fg='#fefdfc', activebackground='#2468ac')
        self.assertNotEqual(str(button.cget('style')), old_name)
        before = self.appearance(button)
        self.view.render(selected, map_name='Fixture map')
        self.assert_pixel_fonts()
        self.assertEqual(self.appearance(button), before)
        appearances = {str(item): self.appearance(item) for item, _ in self.button_metrics()}
        with self.style_sets() as writes:
            self.view.render(selected, map_name='Fixture map')
        self.assertEqual(writes, [])
        self.assertEqual({str(item): self.appearance(item)
                          for item, _ in self.button_metrics()}, appearances)


if __name__ == '__main__':
    unittest.main()
