"""手动选图: grouping, picker paging/keys, hotkey migration and the commit seam."""
from pathlib import Path
from types import SimpleNamespace
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'app'))
from manual_map_picker_core import (PickerState, normalize_selection_mode, option_title,
                               picker_groups, split_map_name)


def item(map_id, name, direction="unknown"):
    return SimpleNamespace(id=map_id, name=name, direction=direction, layers={})


NIGHTMARE = [item("a", "右中 / 中十"), item("b", "左中 / 红房间 / 雷同图"), item("c", "左中右 / 右S"),
             item("d", "噩梦 104026"), item("e", "左右 / 右┛"), item("f", "右中 / 中一"),
             item("g", "左中右 / 右┛"), item("h", "左中右 / 右└┐"), item("i", "左中右 / 右 I / 中└┐"),
             item("j", "左中右 / 右S / 雷同图")]


class GroupingTests(unittest.TestCase):
    def test_names_split_into_group_and_route(self):
        self.assertEqual(split_map_name("左中右 / 右S / 雷同图"), ("左中右", "右S / 雷同图"))
        self.assertEqual(split_map_name("噩梦 104026"), ("", "噩梦 104026"))
        self.assertEqual(option_title(NIGHTMARE[2], "左中右"), "右S")
        self.assertEqual(option_title(NIGHTMARE[3], "其他"), "噩梦 104026")

    def test_nightmare_groups_follow_the_old_picker_order_with_other_last(self):
        groups = picker_groups(NIGHTMARE, "nightmare")
        self.assertEqual([label for label, _items in groups], ["左中右", "左中", "左右", "右中", "其他"])
        self.assertEqual([m.id for m in dict(groups)["左中右"]], ["c", "g", "h", "i", "j"])

    def test_difficult_maps_without_a_name_group_use_their_direction(self):
        maps = [item("n1", "北-红门", "上"), item("s1", "南-L门", "下"), item("n2", "北-T门", "上")]
        groups = picker_groups(maps, "difficult")
        self.assertEqual([(label, [m.id for m in items]) for label, items in groups],
                         [("上", ["n1", "n2"]), ("下", ["s1"])])

    def test_selection_mode_defaults_to_automatic(self):
        self.assertEqual(normalize_selection_mode("manual"), "manual")
        self.assertEqual(normalize_selection_mode(None), "auto")
        self.assertEqual(normalize_selection_mode("Manual"), "auto")


class PickerStateTests(unittest.TestCase):
    def test_group_then_map_with_pages_and_back(self):
        state = PickerState(picker_groups(NIGHTMARE, "nightmare"))
        self.assertEqual(state.stage, "groups")
        self.assertIsNone(state.choose(4))  # Only 4 options per page; F5 does not exist.
        self.assertEqual(state.turn(1), True)  # Five groups -> two pages.
        self.assertEqual(state.choose(0), ("group", "其他"))
        self.assertTrue(state.back())
        self.assertEqual((state.stage, state.page), ("groups", 1))
        state.turn(-1)
        self.assertEqual(state.choose(0), ("group", "左中右"))
        self.assertEqual(state.page_count(), 2)
        self.assertEqual([m.id for m in state.options()], ["c", "g", "h", "i"])
        self.assertTrue(state.turn(1))
        self.assertFalse(state.turn(1))
        self.assertEqual(state.choose(0), ("map", NIGHTMARE[-1]))

    def test_single_group_starts_on_maps_and_cannot_go_back(self):
        state = PickerState(picker_groups([item("x", "地图 X"), item("y", "地图 Y")], "difficult"))
        self.assertEqual(state.stage, "maps")
        self.assertFalse(state.back())
        self.assertEqual(state.choose(1)[1].id, "y")


if __name__ == "__main__":
    unittest.main()
