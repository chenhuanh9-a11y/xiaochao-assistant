"""Shared season identity; difficulty remains the legacy ``mode`` value.

S0 owns the established resource contracts. S1 is deliberately unregistered
until independently validated season-specific resources are supplied. This
registry is an availability declaration, never permission to borrow S0 assets.
"""
from dataclasses import dataclass
from types import MappingProxyType

SEASONS = ("s0", "s1")
MODES = ("nightmare", "difficult")


def normalize_season(value="s0"):
    if value not in SEASONS:
        raise ValueError(f"Unknown game season: {value}")
    return value


def context_key(mode, season="s0"):
    return SeasonContext(season=season, mode=mode).key


@dataclass(frozen=True)
class SeasonContext:
    season: str = "s0"
    mode: str = "nightmare"

    def __post_init__(self):
        normalize_season(self.season)
        if self.mode not in MODES:
            raise ValueError(f"Unknown game mode: {self.mode}")

    @property
    def key(self):
        return f"{self.season}:{self.mode}"

    @property
    def label(self):
        return f"{self.season.upper()} {'噩梦' if self.mode == 'nightmare' else '困难'}"

    @property
    def registered(self):
        return self.season == "s0"


CONTEXTS = MappingProxyType({context_key(mode, season): SeasonContext(season, mode)
                             for season in SEASONS for mode in MODES})


def selected_profile(values, mode=None, season=None, default=""):
    """Read migrated preferences without lending legacy authors to a new season."""
    mode = values.get("gameMode", "nightmare") if mode is None else mode
    season = values.get("gameSeason", "s0") if season is None else season
    key = context_key(mode, season)
    profile = values.get("contextRouteProfiles", {}).get(key)
    if profile:
        return profile
    if season == "s0":
        return values.get("modeRouteProfiles", {}).get(mode) or values.get("routeProfileId", default)
    return default
