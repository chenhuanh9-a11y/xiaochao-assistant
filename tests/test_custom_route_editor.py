"""Offline method tests: no Tk interpreter, windows, game or input events."""
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "app"))
from custom_route_editor import CustomRouteEditor  # noqa: E402
from custom_routes import CustomRouteStore, FLOOR_LABELS, FloorRoute, Walkable  # noqa: E402
from desktop_theme import ERROR  # noqa: E402


class CustomRouteEditorTests(unittest.TestCase):
    def setUp(self):
        # Fail loudly if any test accidentally starts a GUI or creates Tk images.
        for target in ("tkinter.Tk", "tkinter.Toplevel", "custom_route_editor.ImageTk.PhotoImage"):
            guard = patch(target, side_effect=AssertionError("GUI creation is forbidden"))
            guard.start()
            self.addCleanup(guard.stop)

    def test_floor_only_snapshot_change_updates_current_floor_and_jump(self):
        snapshot = dict(available=True, maps=(("a", "Map A"),), currentMapId="a",
                        currentFloor="1", useInGame=False)
        editor = SimpleNamespace(
            _snapshot=lambda: snapshot, _store=lambda: SimpleNamespace(revision=0),
            _signature=None, _maps=snapshot["maps"], map_id="a", floor="1",
            in_game_var=Mock(), floor_var=Mock(), current_button=Mock(), library_note=Mock(),
            _refresh_rows=Mock(), _schedule_render=Mock(), _select=Mock(),
        )
        CustomRouteEditor.refresh(editor)
        snapshot["currentFloor"] = "2"
        CustomRouteEditor.refresh(editor)
        self.assertEqual(editor._current, ("a", "2"))
        CustomRouteEditor._jump_to_current(editor)
        self.assertEqual(editor.floor, "2")
        editor.floor_var.set.assert_called_with(FLOOR_LABELS["2"])
        editor._select.assert_called_once_with("a")
        self.assertEqual(editor._schedule_render.call_count, 2)
        CustomRouteEditor.refresh(editor)
        self.assertEqual(editor._schedule_render.call_count, 2, "unchanged snapshot still skips work")

    def test_tools_before_first_point_and_clear_keep_floor_preferences(self):
        with tempfile.TemporaryDirectory() as folder:
            store = CustomRouteStore(Path(folder) / "routes.json")
            editor = SimpleNamespace(
                map_id="a", floor="1", _history={}, _store=lambda: store,
                _route=lambda: store.route("a", "1"), _feedback=Mock(),
                _refresh_rows=Mock(), _schedule_render=Mock(), _paint_tools=Mock(),
            )
            editor._commit = lambda route, **kwargs: CustomRouteEditor._commit(editor, route, **kwargs)
            CustomRouteEditor._set_color(editor, "#12a37f")
            CustomRouteEditor._toggle_snap(editor)
            route = editor._route()
            self.assertEqual(route, FloorRoute(color="#12a37f", snap=False))
            self.assertEqual(CustomRouteStore(store.path).route("a", "1"), route)
            editor._commit(FloorRoute(((15, 15),), route.color, route.snap))
            CustomRouteEditor._clear(editor)
            self.assertEqual(editor._route(), route)
            self.assertEqual(store.count(), 0)
            CustomRouteEditor._undo(editor)
            self.assertEqual(editor._route(), FloorRoute(((15, 15),), route.color, route.snap))

    def _render_editor(self, *, snap=True, drag=None):
        mask = np.zeros((100, 100), np.uint8)
        mask[10:40, 10:40] = mask[60:90, 60:90] = 1
        canvas = Mock()
        canvas.winfo_width.return_value = canvas.winfo_height.return_value = 240
        editor = SimpleNamespace(
            _render_job=None, canvas=canvas, _paint_tools=Mock(), status=Mock(),
            _message=(None, False), map_id="a", floor="1", _drag=drag,
            _route=lambda: FloorRoute(((25, 25), (75, 75)), snap=snap),
            _floor_data=lambda *_args: (mask, Walkable(mask)),
            _picture_key=("a", "1", (216, 216)), _picture=object(),
            ui=SimpleNamespace(_px=lambda value: value), _sync_hint=Mock(),
        )
        editor._paint_status = lambda: CustomRouteEditor._paint_status(editor)
        return editor

    def test_disconnected_snapped_render_keeps_markers_and_reports_failure(self):
        editor = self._render_editor()
        CustomRouteEditor._render(editor)
        editor.canvas.create_line.assert_not_called()
        self.assertEqual(editor.canvas.create_oval.call_count, 2)
        self.assertIn("无法", editor.status.configure.call_args.kwargs["text"])
        self.assertEqual(editor.status.configure.call_args.kwargs["fg"], ERROR)

    def test_disconnected_snapped_drag_preview_does_not_fall_back_to_straight_line(self):
        editor = self._render_editor(drag=dict(index=1, point=(75, 75)))
        CustomRouteEditor._render(editor)
        editor.canvas.create_line.assert_not_called()
        self.assertIn("无法", editor._path_error)

    def test_manual_drag_preview_draws_the_explicit_straight_segment(self):
        editor = self._render_editor(snap=False, drag=dict(index=1, point=(75, 75)))
        CustomRouteEditor._render(editor)
        self.assertEqual(editor.canvas.create_line.call_count, 2)  # Outline and colour.
        self.assertIsNone(editor._path_error)


if __name__ == "__main__":
    unittest.main(verbosity=2)
