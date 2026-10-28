"""Actual control callbacks/rendering with widget/controller I/O substitutes.

No Tk construction, native windows, input injection or product startup.
Runtime preference persistence and native hiding belong to controller tests.
"""
import unittest

from test_fixed_map_position_ui import load_methods, WidgetIO


class VisibilityControllerIO:
    def __init__(self):
        self.state = dict(userVisible=True, referenceEnabled=True,
                          overlayVisible=True, referenceVisible=True,
                          referenceCanAdjust=True, referenceAdjusting=False)
        self.calls = []

    def snapshot(self):
        return dict(self.state)

    def toggle_overlay(self):
        self.calls.append("overlay")
        self.state["userVisible"] = not self.state["userVisible"]

    def set_reference(self, enabled):
        self.calls.append(("reference", enabled))
        self.state["referenceEnabled"] = enabled

    def move(self):
        self.calls.append("move")
        self.state["referenceAdjusting"] = True

    def finish(self):
        self.calls.append("finish")
        self.state["referenceAdjusting"] = False


class ControlVisibilityUITests(unittest.TestCase):
    def make_view(self):
        ns = load_methods("game_control_view.py", "GameControlView", {
            "_command", "_has", "_call", "_toggle_reference_visibility",
            "_sync_visibility_controls", "_adjust_reference", "_sync_reference_action",
        })
        ns.update(PRIMARY="selected", BUTTON_BG="idle", BUTTON_TEXT="text", BUTTON_HOVER="hover")
        view = ns["GameControlView"]()
        controller = VisibilityControllerIO()
        view._commands = dict(get_control_snapshot=controller.snapshot,
                              toggle_sheet_visibility=controller.toggle_overlay,
                              set_reference_sheet_visible=controller.set_reference,
                              move_sheet=controller.move, finish_reference_sheet_move=controller.finish)
        view.quick_sheet_visibility_button = WidgetIO()
        view.quick_reference_visibility_button = WidgetIO()
        view.reference_adjust_button = WidgetIO()
        view._reference_edit_icon = "position-icon"
        return view, controller

    def test_each_switch_uses_only_its_controller_command(self):
        view, ctl = self.make_view()
        view._call("toggle_sheet_visibility")
        self.assertFalse(ctl.state["userVisible"])
        self.assertTrue(ctl.state["referenceEnabled"])
        view._toggle_reference_visibility()
        self.assertFalse(ctl.state["referenceEnabled"])
        self.assertFalse(ctl.state["userVisible"])
        view._call("toggle_sheet_visibility")
        self.assertTrue(ctl.state["userVisible"])
        self.assertFalse(ctl.state["referenceEnabled"])
        self.assertEqual(ctl.calls, ["overlay", ("reference", False), "overlay"])

    def test_repeat_switch_reads_current_preference_not_previous_render(self):
        view, ctl = self.make_view()
        view._sync_visibility_controls(ctl.snapshot())
        for _ in range(20):
            view._toggle_reference_visibility()
        self.assertTrue(ctl.state["referenceEnabled"])
        self.assertTrue(ctl.state["userVisible"])
        self.assertEqual(ctl.calls, [("reference", bool(i % 2)) for i in range(20)])

    def test_unavailable_pixels_keep_enabled_preference_and_waiting_state(self):
        view, ctl = self.make_view()
        ctl.state.update(overlayVisible=False, referenceVisible=False)
        before = ctl.snapshot()
        view._sync_visibility_controls(ctl.snapshot())
        for button in (view.quick_sheet_visibility_button, view.quick_reference_visibility_button):
            self.assertIn("等待", button.values["text"])
            self.assertEqual(button.values["bg"], "selected")
            self.assertEqual(button.values["state"], "normal")
        self.assertEqual(ctl.snapshot(), before)
        self.assertEqual(ctl.calls, [])

    def test_preference_off_wins_over_stale_visible_snapshot(self):
        view, ctl = self.make_view()
        ctl.state.update(userVisible=False, referenceEnabled=False)
        view._sync_visibility_controls(ctl.snapshot())
        for button in (view.quick_sheet_visibility_button, view.quick_reference_visibility_button):
            self.assertIn("关", button.values["text"])
            self.assertEqual(button.values["bg"], "idle")

    def test_unknown_reference_protocol_cannot_toggle_an_unrelated_switch(self):
        view, ctl = self.make_view()
        ctl.state.pop("referenceEnabled")
        view._sync_visibility_controls(ctl.snapshot())
        self.assertEqual(view.quick_reference_visibility_button.values["state"], "disabled")
        view._toggle_reference_visibility()
        self.assertEqual(ctl.calls, [])
        self.assertTrue(ctl.state["userVisible"])

    def test_missing_setter_disables_only_reference_control(self):
        view, ctl = self.make_view()
        view._commands.pop("set_reference_sheet_visible")
        view._sync_visibility_controls(ctl.snapshot())
        self.assertEqual(view.quick_reference_visibility_button.values["state"], "disabled")
        self.assertEqual(view.quick_sheet_visibility_button.values["state"], "normal")

    def test_adjacent_edit_action_finishes_even_if_visibility_is_disabled(self):
        view, ctl = self.make_view()
        ctl.state.update(userVisible=False, referenceEnabled=False)
        view._sync_reference_action(ctl.snapshot())
        self.assertEqual(view.reference_adjust_button.values["image"], "position-icon")
        view._adjust_reference()
        ctl.state["referenceCanAdjust"] = False
        view._sync_reference_action(ctl.snapshot())
        self.assertEqual(view.reference_adjust_button.values["text"], "完成")
        self.assertEqual(view.reference_adjust_button.values["image"], "")
        self.assertEqual(view.reference_adjust_button.values["state"], "normal")
        view._adjust_reference()
        self.assertEqual(ctl.calls, ["move", "finish"])
        self.assertFalse(ctl.state["userVisible"])
        self.assertFalse(ctl.state["referenceEnabled"])


if __name__ == "__main__":
    unittest.main()
