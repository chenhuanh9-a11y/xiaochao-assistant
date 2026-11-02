"""手动选图: grouping, picker paging/keys, hotkey migration and the commit seam."""
from pathlib import Path
from types import SimpleNamespace
import sys
import tkinter as tk
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'app'))
from manual_map_picker import (ManualMapPicker, PickerState, normalize_selection_mode, option_title,
                               picker_groups, split_map_name)
from manual_map_selection import ManualMapSelectionMixin
try:  # The assistant module needs Windows APIs; the pure picker tests do not.
    from identityv_daily_assistant import (ACTION_HOTKEYS, ACTION_ORDER, parse_action_hotkeys,
                                           recover_action_hotkeys)
except (ImportError, AttributeError) as error:
    ASSISTANT_IMPORT_ERROR = error
    ACTION_HOTKEYS = {"manual-map-picker": ("Alt+1", 0x31)}
else:
    ASSISTANT_IMPORT_ERROR = None


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


class PickerViewTests(unittest.TestCase):
    def setUp(self):
        try:
            self.root = tk.Tk()
        except tk.TclError as error:
            self.skipTest(f"Tk unavailable: {error}")
        self.root.withdraw()
        self.chosen, self.closed, self.requests = [], [], []
        self.picker = ManualMapPicker(self.root, on_choose=self.chosen.append,
                                      on_close=lambda: self.closed.append(True),
                                      on_need_thumbnails=self.requests.append)

    def tearDown(self):
        self.root.destroy()

    def test_keys_walk_groups_maps_and_request_only_visible_thumbnails(self):
        self.picker.present(picker_groups(NIGHTMARE, "nightmare"), "nightmare")
        self.assertEqual(self.picker.title_label.cget("text"), "手动选图 · 噩梦")
        self.picker.command("F1")
        self.assertEqual(self.requests, [["c", "g", "h", "i"]])
        self.picker.command("PageDown")
        self.assertEqual(self.requests[-1], ["j"])
        self.picker.command("F2")  # Only one map on the second page.
        self.assertEqual(self.chosen, [])
        self.picker.command("F1")
        self.assertEqual([m.id for m in self.chosen], ["j"])
        self.picker.command("Backspace")
        self.assertEqual(self.picker.state.stage, "groups")
        self.picker.command("Escape")
        self.assertEqual(self.closed, [True])

    def test_busy_picker_ignores_choices(self):
        self.picker.present(picker_groups(NIGHTMARE[:1], "nightmare"), "nightmare")
        self.picker.set_busy(True)
        self.picker.command("F1")
        self.assertEqual(self.chosen, [])


@unittest.skipIf(ASSISTANT_IMPORT_ERROR is not None, f"assistant module unavailable: {ASSISTANT_IMPORT_ERROR}")
class HotkeyMigrationTests(unittest.TestCase):
    NINE = "F5,F6,F7,F8,F9,F10,F11,,"

    def test_saved_nine_actions_gain_the_picker_default(self):
        parsed = parse_action_hotkeys(self.NINE, 0x47, "G")
        self.assertEqual(list(parsed), list(ACTION_ORDER))
        self.assertEqual(parsed["manual-map-picker"], ACTION_HOTKEYS["manual-map-picker"])
        self.assertEqual(parsed["toggle-panel"], ("F5", 0x74))

    def test_migration_never_reassigns_a_key_already_in_use(self):
        parsed = parse_action_hotkeys("F5,F6,F7,F8,F9,F10,F11,Alt+1,", 0x47, "G")
        self.assertEqual(parsed["start-round"][0], "Alt+1")
        self.assertEqual(parsed["manual-map-picker"], ("", 0))

    def test_full_list_round_trips_and_recovery_accepts_nine(self):
        text = ",".join(name for name, _vk in ACTION_HOTKEYS.values())
        self.assertEqual(parse_action_hotkeys(text, 0x47, "G")["manual-map-picker"][0], "Alt+1")
        recovered = recover_action_hotkeys(self.NINE.split(","), ACTION_HOTKEYS, "G")
        self.assertEqual(recovered["toggle-panel"], ("F5", 0x74))
        self.assertEqual(recovered["manual-map-picker"], ("", 0))


class FakeGate:
    def __init__(self):
        self.selected_id = self.confirmed_id = None

    def select(self, map_id):
        self.selected_id = map_id


class FakeAssistant(ManualMapSelectionMixin):
    def __init__(self, mode="manual", lock=None):
        self.map_selection_mode = mode
        self.lock = lock
        self.pack = SimpleNamespace(by_id={m.id: m for m in NIGHTMARE}, maps=NIGHTMARE,
                                    floors=("1", "2", "B1"), default_entry_floor=2)
        self._manual_picker_pack = self.pack
        self.gate = FakeGate()
        self.current_floor, self.manual_floor, self.floor_required = "2", None, False
        self.action_hotkeys = dict(ACTION_HOTKEYS)
        self.logs, self.statuses, self.saved, self.confirmed = [], [], 0, []

    def _round_identity_lock_context(self):
        return self.lock

    def _log(self, name, **fields):
        self.logs.append((name, fields))

    def _set_status(self, text, color):
        self.statuses.append(text)

    def _save_settings(self):
        self.saved += 1

    def confirm_route(self):
        self.confirmed.append((self.gate.selected_id, self.current_floor, self.floor_required))
        self.gate.confirmed_id = self.gate.selected_id


class CommitSeamTests(unittest.TestCase):
    def test_identity_ranking_is_skipped_only_while_manual_mode_is_unlocked(self):
        self.assertTrue(FakeAssistant()._manual_identity_skipped())
        self.assertFalse(FakeAssistant(lock={"mapId": "a"})._manual_identity_skipped())
        self.assertFalse(FakeAssistant(mode="auto")._manual_identity_skipped())
        self.assertFalse(FakeAssistant()._manual_identity_skipped(True))

    def test_pick_confirms_through_the_shared_route_commit(self):
        assistant = FakeAssistant()
        self.assertTrue(assistant._apply_manual_map_selection("c"))
        self.assertEqual(assistant.confirmed, [("c", "2", False)])
        self.assertEqual(assistant.logs[-1][1]["floorSeeded"], False)

    def test_unknown_floor_is_seeded_from_the_pack_entry_floor(self):
        assistant = FakeAssistant()
        assistant.floor_required = True
        assistant._apply_manual_map_selection("a")
        self.assertEqual(assistant.confirmed, [("a", "2", False)])
        assistant.manual_floor, assistant.current_floor = "B1", "unknown"
        assistant._apply_manual_map_selection("b")
        self.assertEqual(assistant.confirmed[-1], ("b", "B1", False))

    def test_stale_pack_or_unknown_map_is_never_committed(self):
        assistant = FakeAssistant()
        assistant._manual_picker_pack = object()
        self.assertFalse(assistant._apply_manual_map_selection("a"))
        self.assertFalse(FakeAssistant()._apply_manual_map_selection("missing"))
        self.assertEqual(assistant.confirmed, [])

    def test_mode_switch_is_saved_once_and_hint_names_the_hotkey(self):
        assistant = FakeAssistant(mode="auto")
        self.assertTrue(assistant.set_map_selection_mode("manual")["success"])
        assistant.set_map_selection_mode("manual")
        self.assertEqual(assistant.saved, 1)
        self.assertEqual(assistant.statuses, ["手动选图 · 按 Alt+1 选择本局地图"])
        self.assertFalse(assistant.set_map_selection_mode("other")["success"])


if __name__ == "__main__":
    unittest.main()
