"""Route-package identities shared by the Qt editor and Android community API.

Built-in S0/S1 scopes are always present; future seasons (S2/S3+) imported via
structure packages are dynamically registered into ``SCOPES`` so Custom Routes,
Route Plaza, and live gameplay display work seamlessly for any season.
"""
from dataclasses import dataclass
import threading


@dataclass(frozen=True)
class RouteScope:
    id: str
    scenario: str
    mode: str
    label: str
    floors: tuple[str, ...]
    floor_labels: tuple[str, ...]
    catalog: str
    asset_prefix: str = ""
    max_maps: int = 30
    season_id: str = ""

    @property
    def schema_version(self):
        return 3 if self.id == "legacy-nightmare" else 4

    @property
    def season(self):
        if self.season_id:
            return self.season_id
        return "s1" if self.scenario == "phosphorescence" else "s0"

    @property
    def runtime_mode(self):
        return "difficult" if self.mode == "hard" else "nightmare"


_LOCK = threading.RLock()

SCOPES = {
    s.id: s for s in (
        RouteScope("legacy-nightmare", "lady-of-misfortune", "nightmare", "灾厄之女 · 噩梦",
                   ("B1", "1", "2"), ("负一楼", "一楼", "二楼"),
                   "nightmare-route-editor/catalog.json", "nightmare-route-editor/", 27, season_id="s0"),
        RouteScope("classic-hard", "lady-of-misfortune", "hard", "灾厄之女 · 困难",
                   ("1", "2"), ("一楼", "二楼"), "classic-route-editor/catalog.json", season_id="s0"),
        RouteScope("season-s1-hard", "phosphorescence", "hard", "磷光症 · 困难",
                   ("-1", "-2", "-3"), ("B1", "B2", "B3"), "season-route-editor/catalog.json", max_maps=14, season_id="s1"),
        RouteScope("season-s1-nightmare", "phosphorescence", "nightmare", "磷光症 · 噩梦",
                   ("-1", "-2", "-3"), ("B1", "B2", "B3"), "season-route-editor/catalog.json", max_maps=15, season_id="s1"),
    )
}

_SEASON_TITLES = {
    "s0": "灾厄之女",
    "s1": "磷光症",
}


def _default_floor_label(fid: str) -> str:
    s = str(fid)
    if s == "B1":
        return "负一楼"
    if s.startswith("-") and s[1:].isdigit():
        return f"B{s[1:]}"
    if s.isdigit():
        return {"1": "一楼", "2": "二楼", "3": "三楼"}.get(s, f"{s}楼")
    return s


def register_scope(scope: RouteScope, *, season_title: str = "") -> RouteScope:
    with _LOCK:
        SCOPES[scope.id] = scope
        if season_title:
            _SEASON_TITLES[scope.season] = season_title
        return scope


def ensure_season_scope(
    season_id: str,
    runtime_mode: str,
    *,
    season_title: str = "",
    floors: tuple[str, ...] = ("-1", "-2", "-3"),
    scenario: str = "",
) -> RouteScope:
    """Ensure a RouteScope exists for any imported season + mode (including S2/S3+)."""
    sid = str(season_id or "").strip().lower()
    rmode = str(runtime_mode or "nightmare").strip().lower()
    plaza_mode = "hard" if rmode in ("difficult", "hard") else "nightmare"
    with _LOCK:
        for existing in SCOPES.values():
            if existing.season == sid and existing.runtime_mode == ("difficult" if plaza_mode == "hard" else "nightmare"):
                return existing
        title = season_title or _SEASON_TITLES.get(sid) or sid.upper()
        mode_label = "困难" if plaza_mode == "hard" else "噩梦"
        fls = tuple(str(f) for f in (floors or ("-1", "-2", "-3")))
        fl_labels = tuple(_default_floor_label(f) for f in fls)
        scope_id = f"season-{sid}-{plaza_mode}"
        scope = RouteScope(
            id=scope_id,
            scenario=scenario or f"season-{sid}",
            mode=plaza_mode,
            label=f"{title} · {mode_label}",
            floors=fls,
            floor_labels=fl_labels,
            catalog=f"season-{sid}-route-editor/catalog.json",
            max_maps=30,
            season_id=sid,
        )
        SCOPES[scope_id] = scope
        _SEASON_TITLES[sid] = title
        return scope


def sync_dynamic_scopes_from_seasons() -> None:
    """Register RouteScopes for any dynamically discovered seasons in season_context."""
    try:
        from season_context import all_season_entries
    except Exception:
        return
    for entry in all_season_entries():
        sid = str(entry.get("id") or "").strip().lower()
        if not sid or sid in ("s0", "s1"):
            continue
        title = str(entry.get("title") or sid.upper())
        for rmode in entry.get("modes") or ("difficult", "nightmare"):
            ensure_season_scope(sid, rmode, season_title=title)


def season_options() -> list[dict[str, str]]:
    """Dynamic season filter options for Route Plaza."""
    sync_dynamic_scopes_from_seasons()
    seen = []
    with _LOCK:
        for scope in SCOPES.values():
            if scope.season not in seen:
                seen.append(scope.season)
        rows = [{"id": "", "name": "全部赛季"}]
        for sid in seen:
            rows.append({"id": sid, "name": _SEASON_TITLES.get(sid, sid.upper())})
        return rows


def scope_for(scenario="lady-of-misfortune", mode="nightmare"):
    sync_dynamic_scopes_from_seasons()
    with _LOCK:
        for scope in SCOPES.values():
            if (scope.scenario, scope.mode) == (scenario, mode):
                return scope
    raise ValueError("路线场景或难度无效")
