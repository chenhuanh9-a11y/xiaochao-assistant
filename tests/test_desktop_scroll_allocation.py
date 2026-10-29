"""Real hidden Tk allocation contract for production scroll-page callbacks."""
import ast
import os
from pathlib import Path
from types import SimpleNamespace
import tkinter as tk
import unittest
from unittest.mock import patch


SOURCE = Path(os.environ.get('XINGTU_TEST_DESKTOP_SOURCE',
                             Path(__file__).parents[1] / 'app/desktop_main_ui.py'))


def subject(root):
    tree = ast.parse(SOURCE.read_text(encoding='utf-8'))
    owner = next(node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == 'DesktopMainUI')
    method = next(node for node in owner.body if isinstance(node, ast.FunctionDef) and node.name == '_scroll_page')
    namespace = dict(tk=tk, SURFACE='white', SCROLL_THUMB='gray', SCROLL_HOVER='black')
    exec(compile(ast.Module(body=[method], type_ignores=[]), str(SOURCE), 'exec'), namespace)
    class UI:
        _scroll_page = namespace['_scroll_page']
    ui = UI()
    ui.body = tk.Frame(root)
    ui.body.pack(fill='both', expand=True)
    ui._page_frames, ui._scroll_pages = {}, {}
    return ui


class ScrollAllocationTests(unittest.TestCase):
    def setUp(self):
        try:
            self.root = tk.Tk()
        except tk.TclError as error:
            self.skipTest(f'Real Tk unavailable: {error}')
        self.root.withdraw()
        self.addCleanup(self.root.destroy)
        self.window = tk.Toplevel(self.root)
        self.window.withdraw()
        self.window.geometry('640x480')
        self.root.update_idletasks()
        self.ui = subject(self.window)
        callbacks = []
        original = tk.Misc._register
        def register(widget, func, *args, **kwargs):
            if getattr(func, '__name__', '') == 'fit_content':
                callbacks.append(func)
            return original(widget, func, *args, **kwargs)
        with patch.object(tk.Misc, '_register', register):
            self.content = self.ui._scroll_page('help')
        self.fit_content = callbacks[0]  # Exact callback registered on both real widgets.
        self.canvas = self.ui._scroll_pages['help'][0]
        self.item = self.canvas.find_all()[0]

    def test_hidden_content_event_constrains_initial_one_pixel_viewport(self):
        self.assertEqual(self.canvas.winfo_width(), 1)
        self.fit_content(SimpleNamespace(widget=self.content, width=505, height=1610))
        self.assertEqual(float(self.canvas.itemcget(self.item, 'width')), 1)

    def test_same_content_event_does_not_reconfigure_canvas_item(self):
        self.fit_content(SimpleNamespace(widget=self.content, width=505, height=1610))
        with patch.object(self.canvas, 'itemconfigure', wraps=self.canvas.itemconfigure) as configure:
            for _ in range(10):
                self.fit_content(SimpleNamespace(widget=self.content, width=505, height=1610))
        configure.assert_not_called()

    def test_page_allocation_recovers_after_hidden_dpi_and_reentry(self):
        label = tk.Label(self.content, text='A wrapped help sentence. ' * 20, wraplength=500)
        label.pack(fill='x')
        label.bind('<Configure>', lambda event: label.configure(wraplength=max(100, event.width-2)))
        self.fit_content(SimpleNamespace(widget=self.content, width=505, height=1610))
        self.ui._page_frames['help'].pack(fill='both', expand=True)
        self.root.update_idletasks()
        width = self.canvas.winfo_width()
        self.assertGreater(width, 100)
        self.assertEqual(float(self.canvas.itemcget(self.item, 'width')), width)
        bounds = self.canvas.bbox(self.item)
        self.assertEqual(bounds[2] - bounds[0], width)
        self.ui._page_frames['help'].pack_forget()
        label.configure(font=('Arial', -20))
        self.root.update_idletasks()
        self.ui._page_frames['help'].pack(fill='both', expand=True)
        self.root.update_idletasks()
        self.assertEqual(float(self.canvas.itemcget(self.item, 'width')), self.canvas.winfo_width())
        self.assertGreater(float(self.canvas.itemcget(self.item, 'width')), 100)


if __name__ == '__main__':
    unittest.main()
