"""Worker-owned, read-only resource declarations for browsing either mode.

Preview availability is not installation ownership or runtime selection authority.
No runtime factory, environment override, solver, memory store or Tk is involved.
"""
from __future__ import annotations

from concurrent.futures import CancelledError
from dataclasses import dataclass
import json
from pathlib import Path
import stat
from types import MappingProxyType, SimpleNamespace
from typing import Mapping

from author_route_catalog import AuthorRouteCatalog, AuthorRouteProfile, AuthorRouteVariant
from map_resource_model import MapResourceModel, ResourceMap
from star_map_import import AUTHOR_PACKAGES


@dataclass(frozen=True)
class BrowseMap:
    id: str
    name: str
    layers: Mapping[str, Path]


@dataclass(frozen=True)
class BrowsePack:
    """Manifest presentation data only; not a runtime pack or identity authority."""
    mode: str
    resource_root: Path
    source_root: Path
    manifest_path: Path
    pack_id: str
    display_name: str
    default_entry_floor: str
    floors: tuple[str, ...]
    maps: tuple[BrowseMap, ...]
    by_id: Mapping[str, BrowseMap]
    route_profiles: Mapping[str, str]
    layer_role: str
    display_context: object | None = None
    tilemap_root: Path | None = None
    tilemap_entries: Mapping = None
    source_candidates: object | None = None


@dataclass(frozen=True)
class ProfileSummary:
    profile_id: str
    name: str
    package_id: str | None
    map_count: int
    available_maps: int
    missing_maps: int
    missing_floors: int
    layer_count: int
    available_layers: int
    first_preview: tuple[str, str, Path] | None
    maps: tuple[ResourceMap, ...]
    error: str = ""


@dataclass(frozen=True)
class BrowseContext:
    mode: str
    root: Path
    pack: object | None
    catalog: object | None
    model: MapResourceModel | None
    profiles: tuple[ProfileSummary, ...]
    path_stamps: Mapping[Path, tuple[int, int] | None]
    error: str = ""
    warnings: tuple[str, ...] = ()


def load_browse_context(resource_root: Path, mode: str, *, cancelled=lambda: False) -> BrowseContext:
    """Read installed declarations and stat preview files, on the caller's worker.

    Missing mode/bad metadata returns an explicit error context; cancellation raises
    CancelledError. A usable mode with no author catalog remains browsable. All
    paths are anchored to this exact resource root, including legacy source paths.
    """
    root = Path(resource_root).resolve()
    stamps = {}
    warnings = []

    def check():
        if cancelled():
            raise CancelledError()

    def local(relative, base=None):
        check()
        path = ((base or root) / str(relative)).resolve()
        if not path.is_relative_to(root):
            raise ValueError("resource path escapes browse root")
        return path

    def read(relative):
        check()
        data = json.loads(local(relative).read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            raise ValueError("resource metadata must be an object")
        return data

    def stamp(path):
        check()
        path = local(path)
        if path not in stamps:
            try:
                info = path.stat()
                stamps[path] = (info.st_mtime_ns, info.st_size) if stat.S_ISREG(info.st_mode) else None
            except FileNotFoundError:
                stamps[path] = None
        return stamps[path]

    def catalog_at(relative):
        path = local(relative)
        raw = read(path)
        profiles = raw.get("profiles", [])
        if not isinstance(profiles, list):
            raise ValueError("author profiles must be a list")
        for profile in profiles:
            check()
            if not isinstance(profile, dict) or not isinstance(profile.get("maps", {}), dict):
                raise ValueError("author profile maps must be an object")
            for variants in profile.get("maps", {}).values():
                if not isinstance(variants, list):
                    raise ValueError("author map variants must be a list")
                for variant in variants:
                    check()
                    if not isinstance(variant, dict) or not isinstance(variant.get("layers"), dict):
                        raise ValueError("author variant layers must be an object")
        # AuthorRouteCatalog's resource-root base is catalog.parent.parent;
        # validate the final path, including sidecar catalogs, before exposure.
        catalog = AuthorRouteCatalog(path, asset_base=root)
        if len(catalog.profiles) != len(raw.get("profiles", [])):
            raise ValueError("duplicate author profile")
        for profile in catalog.profiles.values():
            check()
            for variants in profile.maps.values():
                for variant in variants:
                    for path in variant.layers.values():
                        local(path)
        return catalog

    def empty_catalog():
        return SimpleNamespace(profiles={}, default_profile_id="", _raw={})

    try:
        check()
        if mode not in {"nightmare", "difficult"}:
            raise ValueError("unknown browse mode")
        # Build presentation fields explicitly. Runtime constructors may
        # consult environment/development paths or validate
        # recognition dependencies, neither of which belongs to browsing.
        pack = SimpleNamespace()
        if mode == "nightmare":
            raw = read("references/nightmare-map-pack.json")
            manifest = local("references/nightmare-map-pack.json")
            source_root = local("assets/nightmare-maps")
            catalog_path = local("references/nightmare-author-route-catalog.json")
            pack.layer_role = "author-display"
        elif local("difficult-native-manifest.json").is_file():
            declaration = read("difficult-native-manifest.json")
            interfaces = declaration["runtimeInterfaces"]
            manifest = local(interfaces.get("identityPack") or interfaces["sourceManifest"])
            raw = read(manifest)
            if interfaces.get("identityPack") and raw.get("schemaVersion") != 1:
                raise ValueError("unsupported native difficult pack")
            source_root = root
            catalog_path = local(interfaces["authorCatalog"])
            index = read(interfaces["typedIndex"])
            if index.get("schemaVersion") != 1 or index.get("resourceKind") != "shared-structure":
                raise ValueError("unsupported shared structure index")
            entries = {}
            for entry in index.get("entries", []):
                check()
                key = (str(entry["profile"]), str(entry["mapId"]),
                       str(entry.get("variant", "variant-01")), str(entry["floor"]))
                if key in entries:
                    raise ValueError("duplicate structure binding")
                local(entry["resourcePath"])
                entries[key] = entry
            legacy = interfaces.get("legacyClean")
            legacy_bindings = {}
            if legacy:
                legacy_root = local(legacy["resourceRoot"])
                for entry in read(legacy["index"]).get("entries", []):
                    check()
                    key = (str(entry["profileId"]), str(entry["mapId"]),
                           str(entry.get("variantId", "variant-01")), str(entry["floor"]))
                    legacy_bindings[key] = dict(entry, resourcePath=str(local(entry["resourcePath"], legacy_root)))
            def native_local(relative):
                path = (root / str(relative)).resolve()
                if not path.is_relative_to(root):
                    raise ValueError("resource path escapes browse root")
                return path
            pack.display_context = SimpleNamespace(resources=SimpleNamespace(entries=entries),
                legacy_bindings=legacy_bindings, local=native_local)
            pack.layer_role = "structure-reference"
        else:
            manifest = local("references/difficult-runtime.json")
            raw = read(manifest)
            source_root = local("assets/difficult-maps")
            catalog_path = None
            pack.tilemap_root = local("references/difficult-tilemaps")
            tile_index = local("references/difficult-tilemaps/index.json")
            pack.tilemap_entries = {}
            if tile_index.is_file():
                for entry in read(tile_index).get("entries", []):
                    key = (str(entry["mapId"]), str(entry["floor"]))
                    if key in pack.tilemap_entries:
                        raise ValueError("duplicate difficult tilemap binding")
                    for field in ("resourcePath", "routeResource"):
                        if entry.get(field):
                            local(entry[field], pack.tilemap_root)
                    pack.tilemap_entries[key] = entry
            pack.layer_role = "author-display"
        if raw.get("mode", mode if mode == "nightmare" else None) != mode:
            raise ValueError("browse manifest mode mismatch")
        pack.mode = mode
        pack.manifest_path, pack.resource_root, pack.source_root = manifest, root, source_root
        pack.pack_id = str(raw["packId"])
        pack.display_name = str(raw.get("displayName") or mode)
        pack.default_entry_floor = str(raw.get("defaultEntryFloor", "2" if mode == "nightmare" else "1"))
        pack.floors = tuple(map(str, raw.get("floors", ("1", "2", "B1") if mode == "nightmare" else ("1", "2"))))
        pack.maps, pack.route_profiles = [], {}
        pack.identity_profiles = {}
        pack.source_candidates = None
        expected_by_map = {}
        for row in raw["maps"]:
            check()
            base = root
            if mode == "nightmare":
                resource_base = row.get("resourceBase", "source-root")
                if resource_base not in {"source-root", "resource-root"}:
                    raise ValueError("unsupported nightmare resource base")
                base = root if resource_base == "resource-root" else source_root
            elif not hasattr(pack, "display_context"):
                base = source_root
            layers = {}
            for floor, relative in row["layers"].items():
                if hasattr(pack, "tilemap_entries"):
                    entry = pack.tilemap_entries.get((row["id"], str(floor)))
                    path = local(entry["routeResource"], pack.tilemap_root) if entry else local(relative, base)
                else:
                    path = local(relative, base) if relative is not None else None
                # Native declarations can explicitly mark a layer unavailable.
                # There is no fake path fallback; absent layers remain absent.
                if path is not None:
                    layers[str(floor)] = path
            pack.maps.append(BrowseMap(str(row["id"]), str(row["name"]), MappingProxyType(layers)))
            pack.route_profiles[str(row["id"])] = str(row.get("routeProfileId", "wang-qingxin" if mode == "nightmare" else "zhan10-speed"))
            expected_by_map[str(row["id"])] = set(map(str, row["layers"]))
        pack.by_id = {item.id: item for item in pack.maps}
        if not pack.maps or len(pack.by_id) != len(pack.maps):
            raise ValueError("browse map inventory is empty or duplicated")
        pack = BrowsePack(mode, root, source_root, manifest, pack.pack_id, pack.display_name,
            pack.default_entry_floor, pack.floors, tuple(pack.maps), MappingProxyType(pack.by_id),
            MappingProxyType(pack.route_profiles), pack.layer_role,
            getattr(pack, "display_context", None), getattr(pack, "tilemap_root", None),
            MappingProxyType(getattr(pack, "tilemap_entries", {})))
        catalog = empty_catalog()
        if catalog_path is not None:
            if catalog_path.is_file():
                catalog = catalog_at(catalog_path)
            else:
                warnings.append("author-catalog-missing")
        else:
            variants = {item.id: (AuthorRouteVariant("zhan10-speed", item.id, "variant-01",
                "速刷路线", dict(item.layers), "resource-card", "current-frame-required"),) for item in pack.maps}
            catalog = SimpleNamespace(default_profile_id="zhan10-speed", _raw={"profiles": []},
                profiles={"zhan10-speed": AuthorRouteProfile("zhan10-speed", "展十 · 困难速刷路线",
                    "展十", "困难速刷", True, True, True, variants)})
        profile_packages = {profile: package for package, profile in AUTHOR_PACKAGES.items()}
        profile_packages.update({"suju": "nightmare-suju", "zhan10-speed": "difficult-zhan10"})
        # Optional catalogs declare preview paths, not permission to render them
        # in the running game. Keep partial catalogs visible against all pack maps.
        profile_errors = {}
        if mode == "nightmare":
            for package, profile in AUTHOR_PACKAGES.items():
                check()
                sidecar = local(f"references/author-packs/{profile}/catalog.json")
                if not sidecar.is_file():
                    continue
                try:
                    optional = catalog_at(sidecar)
                    if set(optional.profiles) != {profile}:
                        raise ValueError("optional catalog profile mismatch")
                    if not set(optional.profiles[profile].maps).issubset(pack.by_id):
                        raise ValueError("optional catalog has unknown map")
                    catalog.profiles[profile] = optional.profiles[profile]
                except (OSError, ValueError, KeyError, TypeError) as error:
                    profile_errors[profile] = f"invalid-author-metadata: {type(error).__name__}"
                    warnings.append(f"{profile}: {profile_errors[profile]}")
                    # Never expose a stale base entry as the broken sidecar.
                    catalog.profiles.pop(profile, None)
        choices = tuple((key, "爱吃醋" if key == "nightingale" else profile.display_name)
                        for key, profile in catalog.profiles.items())
        model = MapResourceModel(pack, catalog, choices)
        for profile_id in profile_errors:
            model.profiles.pop(profile_id, None)
        summaries = []
        for profile_id, name in model.profiles.items():
            check()
            maps = model.maps(profile_id)
            available_maps = available_layers = layer_count = missing_floors = 0
            first = None
            for item in maps:
                check()
                present_floors = set()
                expected_floors = expected_by_map[item.id]
                for variant in item.variants:
                    for floor, path in variant.layers.items():
                        layer_count += 1
                        if stamp(path) is not None:
                            available_layers += 1
                            present_floors.add(floor)
                            if first is None:
                                first = (item.id, floor, Path(path))
                available_maps += bool(present_floors)
                missing_floors += len(expected_floors - present_floors)
            summaries.append(ProfileSummary(profile_id, name, profile_packages.get(profile_id),
                len(maps), available_maps, len(maps) - available_maps, missing_floors,
                layer_count, available_layers, first, maps))
        for profile, error in profile_errors.items():
            summaries.append(ProfileSummary(profile, profile, profile_packages.get(profile),
                len(pack.maps), 0, len(pack.maps), sum(map(len, expected_by_map.values())),
                0, 0, None, (), error))
        # Covers/structure previews and unselected floors share worker stamps.
        for item in pack.maps:
            for path in item.layers.values():
                stamp(path)
        if mode == "nightmare" and local("references/nightmare-structure-display.json").is_file():
            for entry in read("references/nightmare-structure-display.json").get("entries", []):
                stamp(local(entry["referenceResource"]))
        if getattr(pack, "display_context", None) is not None:
            for entry in pack.display_context.resources.entries.values():
                stamp(local(entry["resourcePath"]))
            for entry in pack.display_context.legacy_bindings.values():
                stamp(local(entry["resourcePath"]))
        from map_resource_overview import structure_previews
        for asset in structure_previews(pack, limit=len(pack.maps)):
            stamp(asset.path)
        check()
        return BrowseContext(mode, root, pack, catalog, model, tuple(summaries),
            MappingProxyType(stamps), warnings=tuple(warnings))
    except CancelledError:
        raise
    except (OSError, ValueError, KeyError, TypeError, AttributeError) as error:
        return BrowseContext(mode, root, None, None, None, (), MappingProxyType({}),
            error=f"browse-context-unavailable: {type(error).__name__}: {error}", warnings=tuple(warnings))
