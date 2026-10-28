"""Production-method contracts with fake windows; no Tk GUI or game input."""
import ast
from pathlib import Path
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import Mock
import tkinter as tk
APP = Path(__file__).resolve().parents[1] / 'app'
sys.path.insert(0, str(APP))
from native_ui_layout import RectPx, MonitorContext, UiDpiContext, clamp_rect_to_work_area, format_tk_geometry

def extract(path, names, namespace):
    tree = ast.parse(path.read_text(encoding='utf-8-sig'))
    methods = [n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef) and n.name in names]
    wrapper = ast.ClassDef(name='Subject', bases=[], keywords=[], body=methods, decorator_list=[])
    exec(compile(ast.fix_missing_locations(ast.Module(body=[wrapper], type_ignores=[])), str(path), 'exec'), namespace)
    return namespace['Subject']

class SnapshotTests(unittest.TestCase):
    def setUp(self):
        self.area = RectPx(0,0,1920,1080)
        self.monitors = [MonitorContext('left', self.area, self.area, 96), MonitorContext('right', self.area, self.area, 144)]
        self.dpi = 96
        self.window = Mock()
        self.window.winfo_x.return_value = 10
        self.window.winfo_y.return_value = 10
        self.window.winfo_width.return_value = 800
        self.window.winfo_height.return_value = 600
        self.window.state.return_value = 'normal'
        self.window.geometry.return_value = ''
        ns = dict(tk=tk, RectPx=RectPx, UiDpiContext=UiDpiContext,
            select_monitor_for_rect=lambda r, ms: ms[int(r.left >= 1000)],
            window_dpi=lambda hwnd:self.dpi)
        cls = extract(APP/'identityv_daily_assistant.py', {'_rect_for_window', '_ui_context_for_window', '_ui_context_for_window_snapshot'}, ns)
        self.assistant = cls()
        self.assistant.ui_monitors = self.monitors
        self.assistant._native_window_handle = Mock(return_value=123)

    def test_thousand_queries_do_not_drain_idle_callbacks(self):
        for _ in range(1000):
            self.assertEqual(self.assistant._ui_context_for_window_snapshot(self.window).dpi,96)
        self.window.update_idletasks.assert_not_called()
        self.assistant._native_window_handle.assert_called_with(self.window, sync_layout=False)

    def test_snapshot_reads_new_monitor_and_dpi_without_waiting(self):
        self.assistant._ui_context_for_window_snapshot(self.window)
        self.window.winfo_x.return_value=1200
        self.dpi=144
        context=self.assistant._ui_context_for_window_snapshot(self.window)
        self.assertEqual(context.monitor.monitor_id,'right')
        self.assertEqual(context.dpi,144)
        self.window.update_idletasks.assert_not_called()

    def test_initial_layout_boundary_still_flushes_once(self):
        self.assistant._ui_context_for_window(self.window)
        self.window.update_idletasks.assert_called_once()

    def display(self):
        from desktop_display import window_context
        ns=dict(window_context=window_context, RectPx=RectPx,
            clamp_rect_to_work_area=clamp_rect_to_work_area, format_tk_geometry=format_tk_geometry)
        cls=extract(APP/'desktop_display.py', {'refresh'}, ns)
        obj=cls()
        obj._closed=obj._updating=False
        obj._job=None
        obj.window=self.window
        obj.assistant=self.assistant
        obj._signature=None
        obj.on_scale=Mock()
        obj.apply_widgets=Mock()
        obj._frame=lambda:RectPx(10,10,810,610)
        obj.minimum=(100,100)
        return obj

    def test_withdrawn_refresh_does_not_query_or_flush(self):
        obj=self.display()
        self.window.state.return_value='withdrawn'
        obj.refresh()
        self.assistant._native_window_handle.assert_not_called()
        self.window.update_idletasks.assert_not_called()

    def test_refresh_flushes_only_after_real_dpi_change(self):
        obj=self.display()
        order=[]
        obj.apply_widgets.side_effect=lambda:order.append('apply')
        self.window.update_idletasks.side_effect=lambda:order.append('flush')
        obj.refresh()
        self.assertEqual(order,['apply','flush'])
        for _ in range(1000):obj.refresh()
        self.assertEqual(order,['apply','flush'])
        self.dpi=144
        obj.refresh()
        self.assertEqual(order,['apply','flush','apply','flush'])
        self.assertEqual(obj.scale,1.5)

if __name__=='__main__':unittest.main()
