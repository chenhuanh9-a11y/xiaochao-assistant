from __future__ import annotations

import hashlib
import json
import os
import sys
from collections import OrderedDict
from dataclasses import dataclass
from pathlib import Path


SOURCE_ROOT = Path(__file__).resolve().parents[1]
RESOURCE_ROOT = Path(getattr(sys, "_MEIPASS", SOURCE_ROOT))
USER_ROOT = Path(sys.executable).resolve().parent if getattr(sys, "frozen", False) else SOURCE_ROOT
DEFAULT_CATALOG = RESOURCE_ROOT / "references" / "nightmare-author-route-catalog.json"


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


@dataclass(frozen=True)
class AuthorRouteVariant:
    profile_id: str
    map_id: str
    id: str
    display_name: str
    layers: dict[str, Path]
    identity_status: str
    display_policy: str


@dataclass(frozen=True)
class AuthorRouteProfile:
    id: str
    display_name: str
    author_name: str
    route_family_name: str
    runtime_enabled: bool
    settings_selectable: bool
    aligned_overlay_eligible: bool
    maps: dict[str, tuple[AuthorRouteVariant, ...]]


class AuthorRouteCatalog:
    """Read-only alternate route catalog; inactive profiles fail closed."""

    _PROFILE_ASSET_CACHE_LIMIT = 64

    def __init__(self, catalog_path: Path = DEFAULT_CATALOG, asset_base: Path | None = None) -> None:
        self.catalog_path = Path(catalog_path)
        raw = json.loads(self.catalog_path.read_text(encoding="utf-8"))
        if int(raw.get("schemaVersion", 0)) != 1:
            raise ValueError("unsupported author route catalog")
        self.status = str(raw.get("status", "unknown"))
        self.default_profile_id = str(raw.get("defaultProfileId", "wang-qingxin"))
        configured_asset_root = os.environ.get("IDENTITYV_AUTHOR_ROUTE_ASSET_ROOT")
        if asset_base is not None:
            root = Path(asset_base)
        elif configured_asset_root:
            root = Path(configured_asset_root).resolve()
        else:
            root = USER_ROOT
        self.profiles: dict[str, AuthorRouteProfile] = {}
        for profile_raw in raw.get("profiles", []):
            profile_id = str(profile_raw["id"])
            maps: dict[str, tuple[AuthorRouteVariant, ...]] = {}
            for map_id, variants_raw in profile_raw.get("maps", {}).items():
                variants = []
                for variant_raw in variants_raw:
                    resource_base = variant_raw.get("resourceBase", "asset-root")
                    if resource_base not in {"asset-root", "resource-root"}:
                        raise ValueError(f"unsupported author resource base: {resource_base}")
                    variant_root = self.catalog_path.parent.parent if resource_base == "resource-root" else root
                    variants.append(AuthorRouteVariant(
                        profile_id=profile_id,
                        map_id=map_id,
                        id=str(variant_raw["id"]),
                        display_name=str(variant_raw["displayName"]),
                        layers={
                            floor: variant_root / Path(relative)
                            for floor, relative in variant_raw["layers"].items()
                        },
                        identity_status=str(variant_raw["identityStatus"]),
                        display_policy=str(variant_raw["displayPolicy"]),
                    ))
                maps[map_id] = tuple(variants)
            profile = AuthorRouteProfile(
                id=profile_id,
                display_name=str(profile_raw["displayName"]),
                author_name=str(profile_raw["authorName"]),
                route_family_name=str(profile_raw["routeFamilyName"]),
                runtime_enabled=bool(profile_raw.get("runtimeEnabled", False)),
                settings_selectable=bool(profile_raw.get("settingsSelectable", False)),
                aligned_overlay_eligible=bool(profile_raw.get("alignedOverlayEligible", False)),
                maps=maps,
            )
            self.profiles[profile.id] = profile
        self._raw = raw

    def invalidate_asset_availability(self) -> None:
        """Recheck hints after an explicit resource refresh; no display authority."""
        self._profile_asset_cache_owner = id(self)
        self._profile_asset_cache = OrderedDict()

    def profile_has_assets(self, profile_id: str) -> bool:
        """Snapshot availability hint, never permission to display a route.

        A new catalog or replacement profile object rechecks the filesystem.
        Profiles and their maps/layers are read-only within a published snapshot;
        import/removal publishes a fresh snapshot. route_path still checks the
        selected file in real time. Do not use this memo to authorize rendering.
        """
        # Optional display composition shallow-copies catalogs before replacing
        # profiles. A copy must not inherit the source snapshot's availability.
        if getattr(self, '_profile_asset_cache_owner', None) != id(self):
            self.invalidate_asset_availability()
        profile = self.profiles.get(profile_id)
        if profile is None:
            self._profile_asset_cache.pop(profile_id, None)
            return False
        cached = self._profile_asset_cache.get(profile_id)
        if cached is not None and cached[0] is profile:
            self._profile_asset_cache.move_to_end(profile_id)
            return cached[1]
        available = any(path.is_file() for variants in profile.maps.values()
                        for variant in variants for path in variant.layers.values())
        self._profile_asset_cache[profile_id] = (profile, available)
        self._profile_asset_cache.move_to_end(profile_id)
        while len(self._profile_asset_cache) > self._PROFILE_ASSET_CACHE_LIMIT:
            self._profile_asset_cache.popitem(last=False)
        return available

    def profile_choices(self, *, include_inactive: bool = False) -> list[AuthorRouteProfile]:
        rows = list(self.profiles.values())
        return rows if include_inactive else [row for row in rows if row.runtime_enabled]

    def settings_choices(self) -> list[AuthorRouteProfile]:
        """Return profiles explicitly approved for settings-only sheet display."""
        return [row for row in self.profiles.values() if row.settings_selectable]

    def variants(
        self,
        profile_id: str,
        map_id: str,
        *,
        include_inactive: bool = False,
    ) -> tuple[AuthorRouteVariant, ...]:
        profile = self.profiles.get(profile_id)
        if profile is None or (not include_inactive and not profile.runtime_enabled):
            return ()
        return profile.maps.get(map_id, ())

    def route_path(
        self,
        profile_id: str,
        map_id: str,
        floor: str,
        variant_id: str | None = None,
        *,
        include_inactive: bool = False,
    ) -> Path | None:
        if floor not in {"1", "2", "B1"}:
            return None
        variants = self.variants(profile_id, map_id, include_inactive=include_inactive)
        if not variants:
            return None
        variant = next((row for row in variants if row.id == variant_id), None) if variant_id else variants[0]
        if variant is None:
            return None
        path = variant.layers.get(floor)
        return path if path is not None and path.is_file() else None

    def validate(self, *, verify_hashes: bool = False) -> dict:
        missing = []
        metadata_missing = []
        bad_hashes = []
        layer_count = 0
        raw_profiles = {row["id"]: row for row in self._raw.get("profiles", [])}
        for profile in self.profiles.values():
            raw_profile = raw_profiles[profile.id]
            for map_id, variants in profile.maps.items():
                raw_variants = {row["id"]: row for row in raw_profile["maps"][map_id]}
                for variant in variants:
                    for floor, path in variant.layers.items():
                        layer_count += 1
                        if not path.is_file():
                            missing.append({
                                "profileId": profile.id,
                                "mapId": map_id,
                                "variantId": variant.id,
                                "floor": floor,
                                "path": str(path),
                            })
                            continue
                        if verify_hashes:
                            metadata = (
                                raw_variants.get(variant.id, {}).get("layerMeta", {})
                                .get(floor)
                            )
                            if not isinstance(metadata, dict) or not metadata.get("sha256"):
                                metadata_missing.append({
                                    "profileId": profile.id,
                                    "mapId": map_id,
                                    "variantId": variant.id,
                                    "floor": floor,
                                    "path": str(path),
                                    "reason": "layer-metadata-missing",
                                })
                                continue
                            expected = str(metadata["sha256"])
                            actual = _sha256(path)
                            if actual != expected:
                                bad_hashes.append({
                                    "profileId": profile.id,
                                    "mapId": map_id,
                                    "variantId": variant.id,
                                    "floor": floor,
                                    "expected": expected,
                                    "actual": actual,
                                })
        return {
            "success": not missing and not metadata_missing and not bad_hashes,
            "status": self.status,
            "profileCount": len(self.profiles),
            "runtimeEnabledProfileCount": sum(row.runtime_enabled for row in self.profiles.values()),
            "mapCoveragePerProfile": {key: len(value.maps) for key, value in self.profiles.items()},
            "variantCountPerProfile": {
                key: sum(len(rows) for rows in value.maps.values())
                for key, value in self.profiles.items()
            },
            "layerCount": layer_count,
            "missing": missing,
            "metadataMissing": metadata_missing,
            "badHashes": bad_hashes,
        }
