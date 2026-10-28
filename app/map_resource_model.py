"""Read-only presentation of existing pack/catalog bindings. No identity inference."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


STRUCTURE_SOURCE = "__structure_reference__"


def floor_label(floor: str, *, source=False) -> str:
    if source:
        return f"源图 {floor}"
    return {"1": "1 楼", "2": "2 楼", "B1": "B1 地下室"}.get(str(floor), str(floor))


def ordered_floors(layers, *, source=False) -> tuple[str, ...]:
    keys = tuple(str(key) for key in layers)
    return tuple(sorted(keys, key=lambda key: (
        {"1": 0, "2": 1, "B1": 2}.get(key, 3), key)))


@dataclass(frozen=True)
class ResourceVariant:
    id: str
    name: str
    layers: dict[str, Path]


@dataclass(frozen=True)
class ResourceMap:
    id: str
    name: str
    variants: tuple[ResourceVariant, ...]
    structure: bool = False

    @property
    def floors(self) -> tuple[str, ...]:
        return ordered_floors({key: None for row in self.variants for key in row.layers})


class MapResourceModel:
    """Take a metadata snapshot; browsing never calls a runtime state writer."""

    def __init__(self, pack, catalog, route_options=()):
        self.pack = pack
        self.catalog = catalog
        self.mode = getattr(pack, "mode", "nightmare")
        self.mode_label = "困难" if self.mode == "difficult" else "噩梦"
        self.default_floor = str(getattr(pack, "default_entry_floor", "1"))
        self.profiles = dict(route_options)
        self.author_details = {}
        for key, profile in getattr(catalog, "profiles", {}).items():
            if profile.runtime_enabled or profile.settings_selectable:
                # Current route labels take precedence over legacy catalog names.
                display_name = '爱吃醋' if key == 'nightingale' else profile.display_name
                author_name = '爱吃醋' if key == 'nightingale' else profile.author_name
                self.profiles.setdefault(key, display_name)
                self.author_details[key] = f"{author_name} · {profile.route_family_name}"
        self.base_profiles = dict(getattr(pack, "route_profiles", {}))
        self.default_profile = getattr(catalog, "default_profile_id", "wang-qingxin")
        for item in pack.maps:
            key = self.base_profiles.get(item.id, self.default_profile)
            if key not in self.profiles:
                self.profiles[key] = getattr(pack, "display_name", "内置路线")
        self.source_store = getattr(pack, "source_candidates", None)
        if getattr(self.source_store, "by_source_group", {}):
            self.profiles[STRUCTURE_SOURCE] = "结构参考 · 未绑定素材"

    def maps(self, profile_id: str) -> tuple[ResourceMap, ...]:
        if profile_id == STRUCTURE_SOURCE:
            return self._structure_maps()
        profile = getattr(self.catalog, "profiles", {}).get(profile_id)
        result = []
        for item in self.pack.maps:
            if (getattr(self.pack, 'layer_role', 'author-display') == 'author-display'
                    and self.base_profiles.get(item.id, self.default_profile) == profile_id):
                variants = (ResourceVariant("pack", "内置路线", dict(item.layers)),)
            else:
                variants = tuple(ResourceVariant(row.id, row.display_name, dict(row.layers))
                                 for row in (profile.maps.get(item.id, ()) if profile else ()))
            result.append(ResourceMap(item.id, item.name, variants))
        return tuple(result)

    def _structure_maps(self) -> tuple[ResourceMap, ...]:
        result = []
        for group, rows in sorted(getattr(self.source_store, "by_source_group", {}).items()):
            variants = {}
            for row in rows:
                variant = str(row["variant"])
                variants.setdefault(variant, {})[str(row["sourceFloor"])] = (
                    self.source_store.resource_root / Path(row["resourcePath"]))
            folder = str(rows[0].get("sourceFolder") or group)
            result.append(ResourceMap(
                f"source:{group}", f"来源 {folder}",
                tuple(ResourceVariant(key, f"素材版本 {key}", layers)
                      for key, layers in variants.items()), True))
        return tuple(result)


def choose_resource(item: ResourceMap, floor: str | None, default_floor: str):
    """One exact variant/floor binding, never a different-floor fallback to a filter."""
    preferred = (floor,) if floor else tuple(dict.fromkeys((default_floor, *item.floors)))
    for key in preferred:
        for variant in item.variants:
            if key in variant.layers:
                return variant, key, Path(variant.layers[key])
    return None


def resource_status(item: ResourceMap, floor: str | None = None) -> str:
    layers = [Path(path) for row in item.variants for key, path in row.layers.items()
              if floor is None or key == floor]
    if not layers:
        return "该来源未收录此地图" if not item.variants else "暂无此楼层"
    available = sum(path.is_file() for path in layers)
    if not available:
        return "图片文件缺失"
    if available < len(layers):
        return "部分图片缺失"
    return "结构参考 · 游戏楼层未核对" if item.structure else "图片已收录"


def resource_inventory(items, floor=None):
    """Count map cards and their explicitly bound variant/floor images separately."""
    paths = [Path(path) for item in items for variant in item.variants
             for key, path in variant.layers.items() if floor is None or key == floor]
    return dict(mapCount=len(items), layerImageCount=len(paths),
                availableLayerImages=sum(path.is_file() for path in paths))
