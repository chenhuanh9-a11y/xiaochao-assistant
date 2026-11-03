"""Shared season identity; difficulty remains the legacy ``mode`` value.

S0 owns the established resource contracts. S1 is deliberately unregistered
until independently validated season-specific resources are supplied. This
registry is an availability declaration, never permission to borrow S0 assets.
"""
from dataclasses import dataclass
from types import MappingProxyType
import re
from season_catalog import SEASON_DEFINITIONS

MODES = ("nightmare", "difficult")


def validate_catalog(definitions):
    """Validate display data without converting it into resource authority."""
    if not isinstance(definitions, (tuple, list)) or not definitions:
        raise ValueError("Season catalog must be a nonempty list")
    rows, seen = [], set()
    for row in definitions:
        if not isinstance(row, dict) or set(row) != {"id", "number", "name", "resources"}:
            raise ValueError("Invalid season catalog fields")
        season, number, name = row["id"], row["number"], row["name"]
        if (not isinstance(season, str) or not re.fullmatch(r"s(?:0|[1-9][0-9]*)", season)
                or type(number) is not int or number < 0 or season != f"s{number}" or season in seen):
            raise ValueError("Season IDs and numbers must be unique and match s<number>")
        if not isinstance(name, str) or len(name) > 80 or any(ord(c) < 32 for c in name):
            raise ValueError("Invalid season display name")
        resources = row["resources"]
        if (not isinstance(resources, dict) or set(resources) != set(MODES)
                or any(value not in ("legacy-s0", "unregistered") for value in resources.values())
                or (season != "s0" and any(value != "unregistered" for value in resources.values()))):
            raise ValueError("Season catalog cannot grant unregistered resource capability")
        seen.add(season)
        rows.append(MappingProxyType(dict(id=season, number=number, name=name.strip(),
            resources=MappingProxyType(dict(resources)))))
    if "s0" not in seen:
        raise ValueError("Season catalog must retain the legacy S0 identity")
    return tuple(rows)


CATALOG = validate_catalog(SEASON_DEFINITIONS)
SEASONS = tuple(row["id"] for row in CATALOG)


def season_label(season):
    normalize_season(season)
    row = next(row for row in CATALOG if row["id"] == season)
    return f"S{row['number']}" + (f" · {row['name']}" if row["name"] else "")


def season_options():
    return [dict(id=row["id"], number=row["number"], name=row["name"], label=season_label(row["id"]),
        capabilities={mode: dict(registered=row["id"] == "s0" and row["resources"][mode] == "legacy-s0",
            status="resource-validation-required" if row["id"] == "s0" and row["resources"][mode] == "legacy-s0" else "unregistered")
            for mode in MODES}) for row in CATALOG]


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
        return f"{season_label(self.season)} {'噩梦' if self.mode == 'nightmare' else '困难'}"

    @property
    def registered(self):
        return self.season == "s0" and next(row for row in CATALOG if row["id"] == self.season)["resources"][self.mode] == "legacy-s0"


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
