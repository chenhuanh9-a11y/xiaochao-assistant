"""Semantic floors and vertical control axes for declared season/mode contexts.

Logical floor identities do not define the visual S0 column order. S1 keeps
its own negative floor identities and separately validated runtime capabilities.
Unregistered contexts have no detector or resource authority.
"""
from dataclasses import dataclass
import threading
from types import MappingProxyType


@dataclass(frozen=True)
class FloorDefinition:
    observed_value: int
    floor_id: str


@dataclass(frozen=True)
class FloorContract:
    season: str
    mode: str
    floors: tuple[FloorDefinition, ...]
    control_axis: str

    @property
    def observed_values(self):
        return tuple(floor.observed_value for floor in self.floors)

    @property
    def floor_ids(self):
        return tuple(floor.floor_id for floor in self.floors)

    def floor_id_for(self, observed):
        # A slot index, numeric string or bool is not a detector reading.
        if type(observed) is not int:
            return None
        return next((floor.floor_id for floor in self.floors
                     if floor.observed_value == observed), None)

    def observed_value_for(self, floor_id):
        if not isinstance(floor_id, str):
            return None
        return next((floor.observed_value for floor in self.floors
                     if floor.floor_id == floor_id), None)


_BASEMENT = FloorDefinition(-1, "B1")
_FIRST = FloorDefinition(1, "1")
_SECOND = FloorDefinition(2, "2")
_CONTRACTS = MappingProxyType({
    ("s0", "nightmare"): FloorContract("s0", "nightmare", (_BASEMENT, _FIRST, _SECOND), "vertical"),
    ("s0", "difficult"): FloorContract("s0", "difficult", (_FIRST, _SECOND), "vertical"),
    **{("s1", mode): FloorContract("s1", mode,
        tuple(FloorDefinition(value, str(value)) for value in (-1, -2, -3, -4)), "vertical")
        for mode in ("difficulty2", "difficult", "nightmare")},
})
_DYNAMIC_LOCK = threading.RLock()
_DYNAMIC_CONTRACTS = {}


def register_floor_contract(season, mode, floors, *, control_axis="vertical"):
    """Register a package-declared floor contract for a dynamic season (e.g. s2+)."""
    from season_context import is_valid_season_id, MODES
    if not is_valid_season_id(season) or mode not in MODES:
        raise ValueError(f"Invalid floor contract context: {season}:{mode}")
    if control_axis not in ("vertical", "horizontal"):
        raise ValueError(f"Invalid control axis: {control_axis}")
    if (season, mode) in _CONTRACTS:
        return _CONTRACTS[(season, mode)]
    defs = []
    seen_values, seen_ids = set(), set()
    for row in floors:
        if isinstance(row, FloorDefinition):
            item = row
        elif isinstance(row, dict):
            val, fid = row.get("observedValue"), row.get("floorId")
            if type(val) is not int or not isinstance(fid, str) or not fid:
                raise ValueError("Invalid floor definition")
            item = FloorDefinition(val, fid)
        else:
            val, fid = row
            if type(val) is not int or not isinstance(fid, str) or not fid:
                raise ValueError("Invalid floor definition")
            item = FloorDefinition(val, fid)
        if item.observed_value in seen_values or item.floor_id in seen_ids:
            raise ValueError("Duplicate floor definition")
        seen_values.add(item.observed_value)
        seen_ids.add(item.floor_id)
        defs.append(item)
    if not 1 <= len(defs) <= 12:
        raise ValueError("Floor contract must contain 1..12 floors")
    contract = FloorContract(season, mode, tuple(defs), control_axis)
    with _DYNAMIC_LOCK:
        _DYNAMIC_CONTRACTS[(season, mode)] = contract
    return contract


def clear_dynamic_floor_contracts():
    with _DYNAMIC_LOCK:
        _DYNAMIC_CONTRACTS.clear()


def floor_display_label(floor_id):
    text = str(floor_id or "")
    if not text:
        return ""
    if text.startswith("-") and text[1:].isdigit():
        return f"B{abs(int(text))}"
    if text[0].upper() in ("B", "F"):
        return text.upper()
    if text.isdigit():
        return f"F{text}"
    return text


def floor_contract(mode, *, season="s0"):
    """Return a built-in or package-registered contract without cross-season fallback."""
    if not isinstance(mode, str) or not isinstance(season, str):
        return None
    builtin = _CONTRACTS.get((season, mode))
    if builtin is not None:
        return builtin
    with _DYNAMIC_LOCK:
        return _DYNAMIC_CONTRACTS.get((season, mode))


# Compatibility exports for existing S0 callers. Never use these for a new
# season: resolve its contract first. Keep the legacy dict/tuple value shapes.
S0_MODE_FLOORS = {mode: contract.observed_values
                  for (season, mode), contract in _CONTRACTS.items() if season == "s0"}
S0_FLOOR_NAMES = {floor.observed_value: floor.floor_id
                  for (season, mode), contract in _CONTRACTS.items() if season == "s0" for floor in contract.floors}
