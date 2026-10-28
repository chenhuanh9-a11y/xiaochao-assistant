"""Headless gallery identity regression checks; no Tk interpreter or images."""
from pathlib import Path
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, call, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "app"))
from custom_map_editor import CustomMapEditor


class GalleryIdentityTests(unittest.TestCase):
    def test_same_label_gallery_rows_bind_their_own_stable_ids(self):
        callbacks = []

        def widget(*_args, **_kwargs):
            result = Mock()
            result.cget.return_value = "surface"
            result.bind.side_effect = lambda _event, callback: callbacks.append(callback)
            return result

        editor = SimpleNamespace(gallery_list=Mock(), gallery_id="second", _in_use="first",
                                 _select_gallery=Mock())
        editor.gallery_list.winfo_children.return_value = []
        galleries = [("default", "Same label", True), ("first", "Same label", False),
                     ("second", "Same label", False)]
        with patch("custom_map_editor.tk.Frame", side_effect=widget), \
                patch("custom_map_editor.tk.Label", side_effect=widget), \
                patch("tkinter.Tk", side_effect=AssertionError("GUI creation is forbidden")):
            CustomMapEditor._render_gallery_list(editor, galleries)
        for callback in callbacks:
            callback(None)
        self.assertEqual(editor._select_gallery.call_args_list,
                         [call(gallery) for gallery in ("default", "first", "second") for _ in range(3)])

    def test_select_and_use_keep_exact_id_for_duplicate_labels(self):
        editor = SimpleNamespace(gallery_id="first", _in_use="first", _signature=object(), refresh=Mock(),
                                 assistant=SimpleNamespace(use_custom_gallery=Mock(
                                     return_value=dict(success=True, message="ok"))),
                                 _note=Mock(), ui=SimpleNamespace(_refresh_route_choices=Mock()))
        CustomMapEditor._select_gallery(editor, "second")
        self.assertEqual(editor.gallery_id, "second")
        self.assertIsNone(editor._signature)
        editor.refresh.assert_called_once_with()
        CustomMapEditor._toggle_use(editor)
        editor.assistant.use_custom_gallery.assert_called_once_with("second")


if __name__ == "__main__":
    unittest.main(verbosity=2)
