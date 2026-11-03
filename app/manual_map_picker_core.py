"""Toolkit-free rules for 手动选图 (manual map selection).

Shared by the Tk picker (``manual_map_picker``) and the Qt client
(``experiments/qt_quick/client_manual_map.py``): grouping, option titles and
the two-stage page state. No tkinter/Qt imports so either UI can use it.
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
# Polled only while the picker is visible; Escape is read by the map-close path.
PICKER_KEY_CODES = {"F1": 0x70, "F2": 0x71, "F3": 0x72, "F4": 0x73,
                    "PageUp": 0x21, "PageDown": 0x22, "Backspace": 0x08}
THUMBNAIL_SIZE = (200, 116)
MODE_TITLES = {"nightmare": "噩梦", "difficult": "困难"}


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
    """Two-stage page state; the view and the tests share it."""
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
