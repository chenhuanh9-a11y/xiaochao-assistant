"""Semantic floors and vertical control axes for declared season/mode contexts.

Logical floor identities do not define the visual S0 column order.
Unregistered contexts have no detector or resource authority.
"""
from dataclasses import dataclass
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
})


def floor_contract(mode, *, season="s0"):
    """Return only a reviewed context, without fallback across seasons/modes."""
    if not isinstance(mode, str) or not isinstance(season, str):
        return None
    return _CONTRACTS.get((season, mode))


# Compatibility exports for existing S0 callers. Never use these for a new
# season: resolve its contract first. Keep the legacy dict/tuple value shapes.
S0_MODE_FLOORS = {mode: contract.observed_values
                  for (season, mode), contract in _CONTRACTS.items() if season == "s0"}
S0_FLOOR_NAMES = {floor.observed_value: floor.floor_id
                  for (season, mode), contract in _CONTRACTS.items() if season == "s0" for floor in contract.floors}
