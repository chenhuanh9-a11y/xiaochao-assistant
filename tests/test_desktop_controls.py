"""Real isolated Tk canvases; no product, game, settings or system input."""
from pathlib import Path
import sys
import tkinter as tk
import unittest

sys.path.insert(0, str(Path(__file__).parents[1] / 'app'))
from desktop_controls import PillSlider, ToggleSwitch


class DesktopControlsTests(unittest.TestCase):
    def setUp(self):
        self.root = tk.Tk()
        self.root.withdraw()
        self.host = tk.Toplevel(self.root)
        self.host.overrideredirect(True)
        self.host.geometry('600x250+24000+24000')
        self.host.attributes('-disabled', True)
        self.addCleanup(self.root.destroy)

    def invoke(self, widget, pattern, key='space'):
        script = widget.bind(pattern)
        substitutions = {'%W': str(widget), '%K': key, '%k': '0', '%N': '0', '%b': '1',
            '%s': '0', '%t': '0', '%x': '10', '%y': '10', '%X': '0', '%Y': '0',
            '%A': '{}', '%E': '0', '%D': '0', '%f': '0', '%h': '0', '%w': '0', '%T': '2', '%#': '0'}
        for token, value in substitutions.items():
            script = script.replace(token, value)
        result = int(self.root.tk.call('catch', script, 'controlResult'))
        self.assertIn(result, (0, 3), self.root.tk.getvar('controlResult'))

    def test_slider_focus_ring_and_thumb_fit_at_three_dpi_sizes(self):
        slider = PillSlider(self.host)
        slider.pack(fill='x')
        for scale in (1, 1.5, 2, 1):
            slider.configure(height=round(22*scale))
            self.root.update()
            for value in (0, 50, 100):
                slider.set(value)
                slider._set_hover(True)
                self.root.update_idletasks()
                left, top, right, bottom = slider.bbox('all')
                self.assertGreaterEqual(left, 0)
                self.assertGreaterEqual(top, 0)
                self.assertLessEqual(right, slider.winfo_width())
                self.assertLessEqual(bottom, slider.winfo_height())

    def test_switch_variable_keyboard_disable_and_trace_cleanup(self):
        variable = tk.BooleanVar(self.root, False)
        calls = []
        control = ToggleSwitch(self.host, variable=variable, command=calls.append)
        control.pack()
        self.root.update()
        self.invoke(control, '<KeyPress-space>')
        self.assertTrue(variable.get())
        self.assertEqual(calls, [True])
        variable.set(False)
        self.assertFalse(control.get())
        self.assertEqual(calls, [True])
        control.configure(state='disabled')
        for pattern in ('<KeyPress-space>', '<KeyPress-Return>', '<ButtonRelease-1>'):
            self.invoke(control, pattern)
        self.assertFalse(variable.get())
        self.assertEqual(calls, [True])
        self.assertEqual(control.cget('cursor'), 'arrow')
        self.assertFalse(int(control.cget('takefocus')))
        control.destroy()
        self.assertEqual(variable.trace_info(), [])
        variable.set(True)

    def test_initial_disabled_slider_and_switch_skip_tab_focus(self):
        for cls in (PillSlider, ToggleSwitch):
            control = cls(self.host, state='disabled')
            self.assertEqual(control.cget('cursor'), 'arrow')
            self.assertFalse(int(control.cget('takefocus')))
            control.configure(state='normal')
            self.assertTrue(int(control.cget('takefocus')))


if __name__ == '__main__':
    unittest.main()
