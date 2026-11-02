"""Qt-side pure logic for 手动选图 (manual map selection).

The grouping/naming/paging functions mirror ``app/manual_map_picker.py`` from
PR #70. They deliberately have no tkinter or Qt imports so the Qt client and
its offline tests can share the same rules while the Tk implementation still
lives on its own branch. Keep this module free of application imports until
#70 lands, then reconcile the two copies instead of growing a third one.
"""
from __future__ import annotations

from dataclasses import dataclass
import math

SELECTION_MODES = ("auto", "manual")
SELECTION_MODE_LABELS = {"auto": "自动识别", "manual": "手动选图"}
NIGHTMARE_GROUP_ORDER = ("左中右", "左中", "左右", "右中")
OTHER_GROUP = "其他"
OPTION_KEYS = ("F1", "F2", "F3", "F4")
PAGE_SIZE = len(OPTION_KEYS)
MODE_TITLES = {"nightmare": "噩梦", "difficult": "困难"}
MANUAL_PICKER_ACTION = "manual-map-picker"
# Pending verification against the other developer's global-hotkey fix.
MANUAL_MAP_PICKER_DEFAULT_HOTKEY = "Alt+1"
# Only routed while the picker is visible; Escape keeps its existing map-close
# edge as well, matching the Tk picker.
PICKER_KEY_VKS = {"F1": 0x70, "F2": 0x71, "F3": 0x72, "F4": 0x73,
                  "PageUp": 0x21, "PageDown": 0x22, "Backspace": 0x08,
                  "Escape": 0x1B}
PICKER_KEY_NAMES = tuple(PICKER_KEY_VKS)


def normalize_selection_mode(value) -> str:
    return value if value in SELECTION_MODES else "auto"


def split_map_name(name: str) -> tuple[str, str]:
    """'左中右 / 右S / 雷同图' -> ('左中右', '右S / 雷同图'); no group -> ('', name)."""
    parts = [part.strip() for part in str(name or "").split("/")]
    if len(parts) >= 2 and all(parts):
        return parts[0], " / ".join(parts[1:])
    return "", str(name or "").strip()


def map_group(item) -> str:
    group, _rest = split_map_name(item.name)
    if group:
        return group
    direction = str(getattr(item, "direction", "") or "").strip()
    return direction if direction and direction not in {"unknown", "全部"} else OTHER_GROUP


def picker_groups(maps, mode: str = "nightmare") -> list[tuple[str, list]]:
    """Groups in the familiar order; unknown groups by first appearance, 其他 last."""
    grouped: dict[str, list] = {}
    for item in maps:
        grouped.setdefault(map_group(item), []).append(item)
    preferred = NIGHTMARE_GROUP_ORDER if mode == "nightmare" else ()
    order = [label for label in preferred if label in grouped]
    order += [label for label in grouped if label not in order and label != OTHER_GROUP]
    order += [OTHER_GROUP] if OTHER_GROUP in grouped else []
    return [(label, grouped[label]) for label in order]


def option_title(item, group: str) -> str:
    prefix, rest = split_map_name(item.name)
    return rest if prefix and prefix == group else str(item.name)


@dataclass
class PickerState:
    """Two-stage page state; the Qt view and the tests share it."""
    groups: list
    group: int | None = None
    page: int = 0
    group_page: int = 0

    def __post_init__(self):
        if len(self.groups) == 1:
            self.group = 0  # A single group needs no first step.

    @property
    def stage(self) -> str:
        return "groups" if self.group is None else "maps"

    @property
    def single_group(self) -> bool:
        return len(self.groups) == 1

    def items(self) -> list:
        return list(self.groups) if self.group is None else list(self.groups[self.group][1])

    def page_count(self) -> int:
        return max(1, math.ceil(len(self.items()) / PAGE_SIZE))

    def options(self) -> list:
        start = self.page * PAGE_SIZE
        return self.items()[start:start + PAGE_SIZE]

    def choose(self, index: int):
        """('group', label) after entering a group, ('map', item) for a map, else None."""
        options = self.options()
        if not 0 <= index < len(options):
            return None
        if self.group is None:
            self.group, self.group_page, self.page = self.page * PAGE_SIZE + index, self.page, 0
            return "group", options[index][0]
        return "map", options[index]

    def back(self) -> bool:
        if self.group is None or self.single_group:
            return False
        self.group, self.page = None, self.group_page
        return True

    def turn(self, delta: int) -> bool:
        page = max(0, min(self.page_count() - 1, self.page + int(delta)))
        changed, self.page = page != self.page, page
        return changed
