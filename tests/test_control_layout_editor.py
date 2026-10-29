"""Real Tk editor/widgets, isolated controller/config, no user windows or game input."""
from pathlib import Path
import sys
import tkinter as tk
import unittest
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'app'))
from control_layout import copy_layout, preset_layout
from control_layout_editor import ControlLayoutEditor
from native_ui_layout import MonitorContext, RectPx, UiDpiContext
from identityv_daily_assistant import DailyAssistant


class LayoutOwner:
    def __init__(self):
        self.layout = preset_layout()
        self.applied = []
        self.allow_save = True
        self.snapshot = dict(mode='nightmare', controlStatusText='等待打开地图',
            controlFloorText='楼层待确认', userVisible=True, referenceEnabled=True,
            sheetOpacityPercent=40, referenceOpacityPercent=70,
            bindings={'toggle-panel': {'text': 'Ctrl+F7'}})

    def control_layout_snapshot(self): return copy_layout(self.layout)
    def _route_presentation_snapshot(self): return dict(self.snapshot)
    def _ui_context_for_window(self, _window):
        return UiDpiContext(MonitorContext('test', RectPx(0, 0, 1200, 900), RectPx(0, 0, 1200, 860), 96), 96)
    _ui_context_for_window_snapshot = _ui_context_for_window
    def apply_control_layout(self, value):
        self.applied.append(copy_layout(value))
        if self.allow_save:
            self.layout = copy_layout(value)
        return dict(success=self.allow_save, message='布局保存失败' if not self.allow_save else '已保存')


class LayoutEditorTests(unittest.TestCase):
    def setUp(self):
        self.root = tk.Tk()
        self.root.withdraw()
        self.owner = LayoutOwner()
        self.editors = []
        self.guard = patch.object(ControlLayoutEditor, 'deiconify')
        self.guard.start()

    def tearDown(self):
        for editor in self.editors:
            if editor.winfo_exists(): editor.destroy()
        self.root.update_idletasks()
        self.root.destroy()
        self.guard.stop()

    def editor(self):
        editor = ControlLayoutEditor(self.root, self.owner)
        self.editors.append(editor)
        self.root.update_idletasks()
        return editor

    def test_cancel_discards_draft_restore_and_order_changes_without_runtime_commands(self):
        editor = self.editor()
        editor.set_preset('common')
        editor.move('round', -1)
        editor._display['referencePosition'].set(True)
        editor._change()
        self.assertNotEqual(editor.draft, self.owner.layout)
        self.assertEqual(self.owner.applied, [])
        self.assertEqual(editor.preview._commands, {})
        editor.cancel_button.invoke()
        self.assertFalse(editor.winfo_exists())
        self.assertEqual(self.owner.layout, preset_layout())
        self.assertEqual(self.owner.snapshot['sheetOpacityPercent'], 40)
        self.assertEqual(self.owner.snapshot['referenceOpacityPercent'], 70)

    def test_apply_only_layout_save_failure_keeps_editor_and_draft(self):
        editor = self.editor()
        editor.set_preset('common')
        self.owner.allow_save = False
        editor.apply_button.invoke()
        self.assertTrue(editor.winfo_exists())
        self.assertIn('保存失败', editor.feedback.cget('text'))
        self.assertEqual(self.owner.layout, preset_layout())
        self.owner.allow_save = True
        editor.apply_button.invoke()
        self.assertFalse(editor.winfo_exists())
        self.assertEqual(self.owner.layout, preset_layout('common'))
        self.assertEqual(len(self.owner.applied), 2)

    def test_binding_changes_and_active_edit_are_visible_in_readonly_preview(self):
        editor = self.editor()
        snapshot = dict(self.owner.snapshot, referenceAdjusting=True)
        snapshot['bindings'] = {'toggle-panel': {'text': 'Alt+Shift+F8'}}
        editor.refresh_snapshot(snapshot)
        self.root.update_idletasks()
        self.assertIn('Alt+Shift+F8', editor.binding_label.cget('text'))
        self.assertTrue(editor.preview.edit_bar.winfo_manager())
        self.assertEqual(str(editor.preview.collapse_button.cget('state')), 'disabled')
        self.assertEqual(self.owner.applied, [])

    def test_narrow_layout_navigation_keeps_apply_cancel_and_fixed_recovery(self):
        editor = self.editor()
        # Map only our transparent, borderless test window outside the workspace;
        # withdrawn Tk windows retain their previous actual client dimensions.
        editor.attributes('-alpha', 0)
        editor.overrideredirect(True)
        editor.geometry('500x480+30000+30000')
        editor.tk.call('wm', 'deiconify', str(editor))
        self.root.update()
        self.root.update_idletasks()
        editor._reflow()
        self.assertTrue(editor._compact)
        self.assertEqual(editor.options.winfo_manager(), 'grid')
        self.assertEqual(editor.preview_host.winfo_manager(), '')
        editor.preview_button.invoke()
        self.assertEqual(editor.preview_host.winfo_manager(), 'grid')
        self.assertEqual(editor.options.winfo_manager(), '')
        for button in (editor.apply_button, editor.cancel_button, editor.restore_button):
            self.assertEqual(button.winfo_manager(), 'pack')
        editor.preview_button.invoke()
        self.assertEqual(editor.options.winfo_manager(), 'grid')

    def test_actual_controller_entry_reuses_editor_and_applies_through_same_writer(self):
        assistant = DailyAssistant.__new__(DailyAssistant)
        assistant.root = self.root
        assistant.closed = False
        assistant.settings = {}
        assistant.control_layout = preset_layout()
        assistant.sheet_opacity, assistant.reference_opacity = .4, .7
        assistant._route_presentation_snapshot = self.owner._route_presentation_snapshot
        assistant._ui_context_for_window = self.owner._ui_context_for_window
        assistant._ui_context_for_window_snapshot = self.owner._ui_context_for_window_snapshot
        saved = []
        assistant._save_settings = lambda: saved.append(assistant.control_layout_snapshot()) or True
        editor = assistant.open_control_layout_settings(owner=self.root)
        self.editors.append(editor)
        self.assertIs(assistant.open_control_layout_settings(owner=self.root), editor)
        editor.set_preset('common')
        editor.apply_button.invoke()
        self.assertEqual(saved, [preset_layout('common')])
        self.assertEqual(assistant.settings['gameControlLayout'], preset_layout('common'))
        self.assertEqual((assistant.sheet_opacity, assistant.reference_opacity), (.4, .7))
        self.assertIsNone(assistant._control_layout_editor)

    def test_hotkey_writer_refresh_reaches_control_view_without_recreating_it(self):
        editor = self.editor()
        assistant = DailyAssistant.__new__(DailyAssistant)
        assistant.game_control_view = editor.preview
        assistant.control_layout = preset_layout()
        assistant.pack = SimpleNamespace(by_id={})
        assistant.active_mode_var = tk.StringVar(self.root, value='nightmare')
        assistant._round_identity_lock_context = lambda: {}
        snapshot = dict(self.owner.snapshot)
        assistant._route_presentation_snapshot = lambda: dict(snapshot)
        snapshot['bindings'] = {'toggle-sheet-visibility': {'text': 'Alt+F6'}}
        assistant._refresh_hotkey_editor_views()
        self.assertEqual(editor.preview._visibility_key.cget('text'), 'Alt+F6')
        snapshot['bindings'] = {}
        assistant._refresh_hotkey_editor_views()
        self.assertFalse(editor.preview._visibility_key.winfo_manager())
        self.assertIs(assistant.game_control_view, editor.preview)


if __name__ == '__main__': unittest.main()
