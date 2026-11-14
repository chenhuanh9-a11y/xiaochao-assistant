"""Route-package identities shared by the Qt editor and Android community API."""
from dataclasses import dataclass


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

    @property
    def schema_version(self):
        return 3 if self.id == "legacy-nightmare" else 4

    @property
    def season(self):
        return "s1" if self.scenario == "phosphorescence" else "s0"

    @property
    def runtime_mode(self):
        return "difficult" if self.mode == "hard" else "nightmare"


SCOPES = {
    s.id: s for s in (
        RouteScope("legacy-nightmare", "lady-of-misfortune", "nightmare", "灾厄之女 · 噩梦",
                   ("B1", "1", "2"), ("负一楼", "一楼", "二楼"),
                   "nightmare-route-editor/catalog.json", "nightmare-route-editor/", 27),
        RouteScope("classic-hard", "lady-of-misfortune", "hard", "灾厄之女 · 困难",
                   ("1", "2"), ("一楼", "二楼"), "classic-route-editor/catalog.json"),
        RouteScope("season-s1-hard", "phosphorescence", "hard", "磷光症 · 困难",
                   ("-1", "-2", "-3"), ("B1", "B2", "B3"), "season-route-editor/catalog.json", max_maps=14),
        RouteScope("season-s1-nightmare", "phosphorescence", "nightmare", "磷光症 · 噩梦",
                   ("-1", "-2", "-3"), ("B1", "B2", "B3"), "season-route-editor/catalog.json", max_maps=15),
    )
}


def scope_for(scenario="lady-of-misfortune", mode="nightmare"):
    for scope in SCOPES.values():
        if (scope.scenario, scope.mode) == (scenario, mode):
            return scope
    raise ValueError("路线场景或难度无效")
