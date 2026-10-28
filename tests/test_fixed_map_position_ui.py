"""Actual view methods with controller/widget I/O consumers; no Tk or product startup."""
import ast
from pathlib import Path
import types
import unittest


ROOT = Path(__file__).resolve().parents[1]


def load_methods(filename, class_name, names, functions=()):
    path = ROOT / "app" / filename
    tree = ast.parse(path.read_text(encoding="utf-8-sig"))
    cls = next(node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == class_name)
    body = [node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name in functions]
    body.append(ast.ClassDef(name=class_name, bases=[], keywords=[], decorator_list=[],
        body=[node for node in cls.body if isinstance(node, ast.FunctionDef) and node.name in names]))
    module = ast.fix_missing_locations(ast.Module(body=body, type_ignores=[]))
    namespace = {}
    exec(compile(module, str(path), "exec"), namespace)
    return namespace


class WidgetIO:
    def __init__(self):
        self.values = {}
        self.manager = ""
        self.writes = 0

    def configure(self, **values):
        self.values.update(values)
        self.writes += 1

    def pack(self, **_values):
        self.manager = "pack"

    def pack_forget(self):
        self.manager = ""


class ReferenceControllerIO:
    """Only the view API boundary is substituted; this is not a native-editor test."""
    def __init__(self):
        self.game_foreground = False
        self.state = dict(enabled=True, visible=False, adjusting=False, canAdjust=True,
                          position=None, editingPreview=False, message="可以编辑定点地图")
        self.calls = []
        self.persisted = True

    def reference_sheet_snapshot(self):
        return dict(self.state)

    def move_sheet(self):
        self.calls.append("move_sheet")
        if not self.state["canAdjust"]:
            return dict(success=False, reason="not-editable", message="当前不能编辑定点地图", adjusting=False)
        self.state.update(adjusting=True, editingPreview=True, message="定点地图正在编辑")
        return dict(success=True, reason="editing", message="移动后请点击完成", adjusting=True)

    def finish_reference_sheet_move(self):
        self.calls.append("finish_reference_sheet_move")
        self.state.update(adjusting=False, editingPreview=False, message="已结束定点地图编辑")
        return dict(success=self.persisted, persisted=self.persisted, reason="finished", adjusting=False,
                    message="定点地图位置已保存" if self.persisted else "本次位置已修改，保存失败")

    def toggle_reference_sheet_visibility(self):
        self.calls.append("toggle_reference_sheet_visibility")
        self.state.update(enabled=not self.state["enabled"], message="定点地图显示设置已更新")
        return dict(success=True, reason="visibility", message="定点地图显示设置已更新")


DESKTOP_NAMES = {"_sync_reference_controls", "_set_reference_feedback", "_reference_action",
                 "_toggle_reference_visibility", "_adjust_reference_position"}
GCV_NAMES = {"_command", "_has", "_call", "_adjust_reference", "_sync_reference_action"}


class FixedMapPositionUITests(unittest.TestCase):
    def desktop(self, controller=None):
        namespace = load_methods("desktop_main_ui.py", "DesktopMainUI", DESKTOP_NAMES)
        view = namespace["DesktopMainUI"]()
        view.assistant = controller or ReferenceControllerIO()
        view._reference_controls_signature = None
        for name in ("reference_visibility_button", "reference_adjust_button", "reference_status", "reference_feedback"):
            setattr(view, name, WidgetIO())
        return view

    def gcv(self, controller):
        namespace = load_methods("game_control_view.py", "GameControlView", GCV_NAMES, ("_fact", "display_summary"))
        view = namespace["GameControlView"]()
        view.reference_adjust_button = WidgetIO()
        def snapshot():
            return dict(referenceAdjusting=controller.state["adjusting"],
                        referenceCanAdjust=controller.state["canAdjust"], foreground=controller.game_foreground)
        view._commands = dict(get_control_snapshot=snapshot, move_sheet=controller.move_sheet,
                              finish_reference_sheet_move=controller.finish_reference_sheet_move)
        return view, snapshot

    def test_settings_entry_uses_controller_permission_even_out_of_game(self):
        for enabled in (True, False):
            with self.subTest(enabled=enabled):
                controller = ReferenceControllerIO()
                controller.state["enabled"] = enabled
                view = self.desktop(controller)
                view._sync_reference_controls()
                self.assertFalse(controller.game_foreground)
                self.assertEqual(view.reference_adjust_button.values, dict(text="编辑位置", state="normal"))
                view._adjust_reference_position()
                self.assertEqual(controller.calls, ["move_sheet"])
                self.assertEqual(view.reference_adjust_button.values["text"], "完成")
                self.assertEqual(view.reference_status.values["text"], "编辑中")

    def test_hidden_editing_state_stays_editing_and_can_be_finished(self):
        controller = ReferenceControllerIO()
        controller.state.update(adjusting=True, visible=False, canAdjust=False, editingPreview=True)
        view = self.desktop(controller)
        view._sync_reference_controls()
        self.assertEqual(view.reference_status.values["text"], "编辑中")
        self.assertEqual(view.reference_adjust_button.values, dict(text="完成", state="normal"))
        view._adjust_reference_position()
        self.assertEqual(controller.calls, ["finish_reference_sheet_move"])
        self.assertEqual(view.reference_adjust_button.values, dict(text="编辑位置", state="disabled"))

    def test_can_adjust_false_disables_entry_even_when_game_is_foreground(self):
        controller = ReferenceControllerIO()
        controller.game_foreground = True
        controller.state["canAdjust"] = False
        view = self.desktop(controller)
        view._sync_reference_controls()
        self.assertEqual(view.reference_adjust_button.values["state"], "disabled")
        view._adjust_reference_position()  # a stale callback still goes through the controller's rejection
        self.assertFalse(controller.state["adjusting"])
        self.assertEqual(view.reference_feedback.values["text"], "当前不能编辑定点地图")

    def test_handler_dispatches_from_fresh_editing_state(self):
        controller = ReferenceControllerIO()
        view = self.desktop(controller)
        view._sync_reference_controls()
        controller.state.update(adjusting=True, editingPreview=True)
        view._adjust_reference_position()
        self.assertEqual(controller.calls, ["finish_reference_sheet_move"])

    def test_snapshot_message_updates_without_changing_visibility(self):
        controller = ReferenceControllerIO()
        view = self.desktop(controller)
        view._sync_reference_controls()
        controller.state["message"] = "请先选择可用的定点地图"
        view._sync_reference_controls()
        self.assertEqual(view.reference_feedback.values["text"], controller.state["message"])
        writes = view.reference_feedback.writes
        view._sync_reference_controls()
        self.assertEqual(view.reference_feedback.writes, writes)

    def test_finish_receipt_is_not_overwritten_by_snapshot_or_unchanged_poll(self):
        controller = ReferenceControllerIO()
        controller.persisted = False
        controller.state.update(adjusting=True, editingPreview=True)
        view = self.desktop(controller)
        view._sync_reference_controls()
        view._adjust_reference_position()
        self.assertEqual(view.reference_feedback.values["text"], "本次位置已修改，保存失败")
        self.assertEqual(view.reference_adjust_button.values["text"], "编辑位置")
        view._sync_reference_controls()
        self.assertEqual(view.reference_feedback.values["text"], "本次位置已修改，保存失败")
        controller.state["message"] = "当前定点地图已隐藏"
        view._sync_reference_controls()
        self.assertEqual(view.reference_feedback.values["text"], "当前定点地图已隐藏")

    def test_missing_protocol_remains_unavailable_with_new_name(self):
        view = self.desktop(types.SimpleNamespace())
        view._sync_reference_controls()
        self.assertEqual(view.reference_adjust_button.values["state"], "disabled")
        view._adjust_reference_position()
        self.assertEqual(view.reference_feedback.values["text"], "定点地图暂不可用")

    def test_gcv_permission_and_explicit_finish_are_independent_of_foreground(self):
        controller = ReferenceControllerIO()
        view, snapshot = self.gcv(controller)
        view._sync_reference_action(snapshot())
        self.assertEqual(view.reference_adjust_button.values, dict(text="编辑位置", state="normal"))
        view._adjust_reference()
        controller.state["canAdjust"] = False
        view._sync_reference_action(snapshot())
        self.assertEqual(view.reference_adjust_button.values, dict(text="完成", state="normal"))
        view._adjust_reference()
        self.assertEqual(controller.calls, ["move_sheet", "finish_reference_sheet_move"])

    def test_gcv_stale_action_reads_current_permission_and_editing(self):
        controller = ReferenceControllerIO()
        view, snapshot = self.gcv(controller)
        view._sync_reference_action(snapshot())
        controller.state["canAdjust"] = False
        view._adjust_reference()
        self.assertEqual(controller.calls, [])
        controller.state.update(adjusting=True, editingPreview=True)
        view._adjust_reference()
        self.assertEqual(controller.calls, ["finish_reference_sheet_move"])

    def test_reference_visibility_keeps_existing_controller_protocol(self):
        view = self.desktop()
        view._toggle_reference_visibility()
        self.assertEqual(view.assistant.calls, ["toggle_reference_sheet_visibility"])
        self.assertEqual(view.reference_visibility_button.values["text"], "显示")

    def test_fixed_map_visible_labels_and_summary(self):
        for name in ("desktop_main_ui.py", "game_control_view.py"):
            source = (ROOT / "app" / name).read_text(encoding="utf-8-sig")
            for old in ("参考小抄", "调整小抄", "完成调整", "调整位置", "松手即可", "8秒", "8 秒"):
                self.assertNotIn(old, source)
            self.assertIn("编辑位置", source)
            compile(source, name, "exec")
        namespace = load_methods("game_control_view.py", "GameControlView", GCV_NAMES, ("_fact", "display_summary"))
        self.assertIn("定点地图：条件满足", namespace["display_summary"](dict(referenceVisible=True)))
        source = (ROOT / "app/desktop_main_ui.py").read_text(encoding="utf-8-sig")
        self.assertIn("移动后仍保持编辑状态", source)
        self.assertIn("点击“完成”恢复鼠标穿透", source)

if __name__ == "__main__":
    unittest.main()
