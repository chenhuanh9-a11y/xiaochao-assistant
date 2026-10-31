"""Manual identity intent and four-card browsing, separate from similarity groups.

Route labels are presentation categories, never identity evidence. An accepted
plan proposes an identity only; the runtime still owns geometry and atomic
publication of identity, current-floor reference and aligned material.
"""
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

SELECTION_MODES = ("auto", "manual")
SELECTION_MODE_LABELS = {"auto": "自动识别", "manual": "手动选图"}
NIGHTMARE_GROUP_ORDER = ("左中右", "左中", "左右", "右中")
OTHER_GROUP = "其他"
PAGE_SIZE = 4


def normalize_selection_mode(value):
    return value if isinstance(value, str) and value in SELECTION_MODES else "auto"


def manual_identity_skipped(selection_mode, locked=False):
    """Skip only unlocked identity ranking; capture/floor/fit remain enabled."""
    return normalize_selection_mode(selection_mode) == "manual" and not locked


def _field(item, name, default=""):
    return item.get(name, default) if isinstance(item, Mapping) else getattr(item, name, default)


def split_map_name(name):
    parts = [part.strip() for part in str(name or "").split("/")]
    if len(parts) >= 2 and all(parts):
        return parts[0], " / ".join(parts[1:])
    return "", str(name or "").strip()


def map_group(item):
    prefix, _ = split_map_name(_field(item, "name"))
    direction = str(_field(item, "direction") or "").strip()
    return prefix or (direction if direction and direction not in {"unknown", "全部"} else OTHER_GROUP)


def picker_groups(maps, mode="nightmare"):
    """PR70 route-category order, using only the supplied map inventory."""
    grouped, seen = {}, set()
    for item in maps:
        map_id = str(_field(item, "id") or _field(item, "mapId") or "")
        if not map_id or map_id in seen:
            continue
        seen.add(map_id)
        grouped.setdefault(map_group(item), []).append(item)
    preferred = NIGHTMARE_GROUP_ORDER if mode == "nightmare" else ()
    order = [label for label in preferred if label in grouped]
    order += [label for label in grouped if label not in order and label != OTHER_GROUP]
    if OTHER_GROUP in grouped:
        order.append(OTHER_GROUP)
    return [(label, tuple(grouped[label])) for label in order]


def option_title(item, group):
    name = str(_field(item, "name") or _field(item, "id") or _field(item, "mapId"))
    prefix, rest = split_map_name(name)
    return rest if prefix == group else name


@dataclass
class PickerState:
    groups: list
    group: int | None = None
    page: int = 0
    group_page: int = 0

    def __post_init__(self):
        if len(self.groups) == 1:
            self.group = 0

    @property
    def stage(self):
        return "groups" if self.group is None else "maps"

    @property
    def single_group(self):
        return len(self.groups) == 1

    def items(self):
        return self.groups if self.group is None else self.groups[self.group][1]

    def page_count(self):
        return max(1, (len(self.items()) + PAGE_SIZE - 1) // PAGE_SIZE)

    def options(self):
        start = self.page * PAGE_SIZE
        return self.items()[start:start + PAGE_SIZE]

    def choose(self, index):
        if type(index) is not int or not 0 <= index < len(self.options()):
            return None
        option = self.options()[index]
        if self.group is None:
            self.group, self.group_page, self.page = self.page * PAGE_SIZE + index, self.page, 0
            return "group", option[0]
        return "map", option

    def back(self):
        if self.group is None or self.single_group:
            return False
        self.group, self.page = None, self.group_page
        return True

    def turn(self, delta):
        page = max(0, min(self.page_count() - 1, self.page + int(delta)))
        changed, self.page = page != self.page, page
        return changed


@dataclass(frozen=True)
class ManualSelectionScope:
    mode: str
    cycle_id: object
    floor: str
    generation: int
    resource_identity: str
    selection_mode: str

    def payload(self):
        return dict(mode=self.mode, cycleId=self.cycle_id, floor=self.floor,
                    manualSelectionGeneration=self.generation, resourceIdentity=self.resource_identity)


def manual_selection_scope(state):
    return ManualSelectionScope(str(state.get("mode") or ""), state.get("cycleId"),
        str(state.get("floor") or ""), state.get("manualSelectionGeneration", 0),
        str(state.get("resourceIdentity") or ""), normalize_selection_mode(state.get("mapSelectionMode")))


@dataclass(frozen=True)
class ManualSelectionPlan:
    accepted: bool
    changed: bool
    reason: str
    message: str
    map_id: str = ""
    floor: str = ""
    scope: ManualSelectionScope | None = None


def manual_selection_is_current(plan, state):
    """Necessary scope check; grants neither current-frame geometry nor pixels."""
    return bool(plan.accepted and plan.scope is not None and (state.get("roundActive") or state.get("roundPrepared"))
        and not any(state.get(key) for key in ("closed", "resourceLocked", "contextPending"))
        and not (state.get("roundPreparing") and not state.get("roundPrepared"))
        and plan.scope == manual_selection_scope(state))


def plan_manual_selection(state, payload, map_ids, *, floors=(), default_floor=""):
    """Validate a current-pack pick without writing state or granting a pose.

    ``map_ids`` must come from the runtime pack, not an automatic candidate cohort.
    A missing floor can be seeded from declared metadata; the shared automatic
    floor observation still owns the actual floor. An explicit pick is permitted
    in either mode, as in PR70; selecting a map does not change the mode preference.
    """
    def reject(reason, message):
        return ManualSelectionPlan(False, False, reason, message)

    scope = manual_selection_scope(state)
    if not (state.get("roundActive") or state.get("roundPrepared")) or scope.cycle_id is None:
        return reject("round-unavailable", "请先开始本局，再选择地图")
    if (any(state.get(key) for key in ("closed", "resourceLocked", "contextPending"))
            or state.get("roundPreparing") and not state.get("roundPrepared")):
        return reject("context-unavailable", "正在切换环境或更新资源，请稍后选择")
    if not scope.resource_identity or scope.mode not in {"nightmare", "difficult"}:
        return reject("catalog-unavailable", "当前模式的地图资源尚未就绪")
    if any(key not in payload or payload[key] != value for key, value in scope.payload().items()):
        return reject("choice-scope-changed", "本局、楼层或地图资源已变化，请重新选择")
    if state.get("manualSelectionPending"):
        return reject("choice-pending", "正在核对上次选择，请等待结果")
    target = str(payload.get("mapId") or "")
    if not target or target not in map_ids:
        return reject("map-unavailable", "所选地图不在当前资源中，请重新选择")
    supported_floors = tuple(map(str, floors))
    floor = scope.floor
    if state.get("floorRequired") or floor not in supported_floors:
        seed = str(state.get("manualFloor") or default_floor)
        floor = seed if seed in supported_floors else str(default_floor)
    if floor not in supported_floors:
        return reject("floor-unavailable", "所选地图尚无可用楼层，请等待楼层识别")
    if (target == state.get("manualSelectedMapId") and target == state.get("mapId")
            and state.get("desiredManualMapId", target) in ("", target)):
        return ManualSelectionPlan(True, False, "already-current", "当前已使用此地图", target, floor, scope)
    return ManualSelectionPlan(True, True, "prepare-manual-map", "已选择地图，正在核对当前楼层与位置",
                               target, floor, scope)
