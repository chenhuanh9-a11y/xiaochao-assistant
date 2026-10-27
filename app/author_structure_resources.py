"""Optional, fail-closed loader for dual-channel author structure products."""

from __future__ import annotations

import json
import os
import sys
import zipfile
from dataclasses import dataclass
from pathlib import Path

import numpy as np


SOURCE_ROOT = Path(__file__).resolve().parents[1]
RESOURCE_ROOT = Path(getattr(sys, "_MEIPASS", SOURCE_ROOT))
DEFAULT_INDEX = RESOURCE_ROOT / "references" / "nightmare-author-structure-index.json"
DEFAULT_RESOURCE_ROOT = RESOURCE_ROOT / "outputs" / "噩梦结构预处理-v1"
SUPPORTED_SCHEMA_VERSION = 2
MAX_CACHE_ENTRIES = 4


@dataclass(frozen=True)
class AuthorStructureProduct:
    clean_mask: np.ndarray | None
    clean_edge_5px: np.ndarray | None
    clean_distance: np.ndarray | None
    validation_mask: np.ndarray
    entry: dict
    known_boundary: np.ndarray | None = None
    unknown_mask: np.ndarray | None = None
    native_wall_segments: list[dict] | None = None

    @property
    def mask(self) -> np.ndarray:
        """Compatibility alias for the final validation channel."""
        return self.validation_mask


class AuthorStructureResourceStore:
    """Read precomputed products without changing the online fallback path."""

    def __init__(
        self,
        index_path: Path | None = None,
        resource_root: Path | None = None,
    ) -> None:
        configured_index = os.environ.get("IDENTITYV_AUTHOR_STRUCTURE_INDEX")
        configured_root = os.environ.get("IDENTITYV_AUTHOR_STRUCTURE_ROOT")
        self.index_path = Path(index_path or configured_index or DEFAULT_INDEX)
        self.resource_root = Path(resource_root or configured_root or DEFAULT_RESOURCE_ROOT)
        self.entries: dict[tuple[str, str, str, str], dict] = {}
        self.error: str | None = None
        self._cache: dict[tuple[str, str, str, str], AuthorStructureProduct] = {}
        self._load_index()

    @staticmethod
    def _key(profile_id: str, map_id: str, floor: str, variant_id: str | None) -> tuple[str, str, str, str]:
        return (str(profile_id), str(map_id), str(variant_id or "variant-01"), str(floor))

    def _load_index(self) -> None:
        try:
            payload = json.loads(self.index_path.read_text(encoding="utf-8"))
            if int(payload.get("schemaVersion", 0)) != SUPPORTED_SCHEMA_VERSION:
                raise ValueError("unsupported author structure resource schema")
            if payload.get("validationMaskContract") != "author_route_structure_mask":
                raise ValueError("author structure resource contract mismatch")
            for entry in payload.get("entries", []):
                key = self._key(
                    entry["profileId"], entry["mapId"],
                    entry["floor"], entry.get("variantId"),
                )
                self.entries[key] = dict(entry)
        except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError) as error:
            self.error = str(error)
            self.entries = {}

    def _path_for(self, entry: dict) -> Path:
        return self.resource_root / Path(str(entry["resourcePath"]))

    def load(
        self,
        profile_id: str,
        map_id: str,
        floor: str,
        *,
        variant_id: str | None = None,
        route_shape: tuple[int, int] | None = None,
    ) -> AuthorStructureProduct | None:
        key = self._key(profile_id, map_id, floor, variant_id)
        cached = self._cache.get(key)
        if cached is not None:
            if route_shape is None or cached.validation_mask.shape == tuple(route_shape):
                self._cache.pop(key, None)
                self._cache[key] = cached
                return cached
            return None
        entry = self.entries.get(key)
        if entry is None:
            return None
        try:
            if route_shape is not None and tuple(entry.get("sourceShape", ())) != tuple(route_shape):
                return None
            with np.load(self._path_for(entry), allow_pickle=False) as payload:
                if "validation_mask" not in payload.files:
                    return None
                validation_mask = np.asarray(payload["validation_mask"])
                clean_fields = {
                    "clean_mask", "clean_edge_5px", "clean_distance",
                }
                present_clean = clean_fields.intersection(payload.files)
                if present_clean and present_clean != clean_fields:
                    return None
                # Keep edge/distance in the archive for offline proof, but do
                # not inflate those large arrays on a runtime route load.
                clean_mask = (
                    np.asarray(payload["clean_mask"])
                    if present_clean else None
                )
                known_boundary = np.asarray(payload['known_boundary']) if 'known_boundary' in payload.files else None
                unknown_mask = np.asarray(payload['unknown_mask']) if 'unknown_mask' in payload.files else None
                # Optional static index stored with its source masks. Old or
                # malformed indexes retain the existing on-demand extraction.
                native_walls = None
                if 'native_wall_segments' in payload.files:
                    try:
                        native_walls = decode_native_wall_segments(payload['native_wall_segments'], validation_mask.shape)
                    except (ValueError, TypeError, zipfile.BadZipFile):
                        pass
                clean_edge = None
                clean_distance = None
            shape = validation_mask.shape
            if (
                validation_mask.ndim != 2
                or validation_mask.dtype != np.uint8
                or validation_mask.max(initial=0) > 1
            ):
                return None
            if clean_mask is not None and (
                clean_mask.shape != shape
                or clean_mask.dtype != np.uint8
                or clean_mask.max(initial=0) > 1
            ):
                return None
            if (known_boundary is None) != (unknown_mask is None):
                return None
            if any(a is not None and (a.shape != shape or a.dtype != np.uint8 or a.max(initial=0)>1)
                   for a in (known_boundary,unknown_mask)):
                return None
            product = AuthorStructureProduct(
                np.ascontiguousarray(clean_mask) if clean_mask is not None else None,
                np.ascontiguousarray(clean_edge) if clean_edge is not None else None,
                np.ascontiguousarray(clean_distance) if clean_distance is not None else None,
                np.ascontiguousarray(validation_mask),
                dict(entry),
                known_boundary,unknown_mask,native_walls,
            )
        except (OSError, KeyError, ValueError, TypeError, zipfile.BadZipFile):
            return None
        self._cache[key] = product
        while len(self._cache) > MAX_CACHE_ENTRIES:
            self._cache.pop(next(iter(self._cache)), None)
        return product

    def load_for_route(
        self,
        route: np.ndarray | None,
        profile_id: str,
        map_id: str | None,
        floor: str | None,
        *,
        variant_id: str | None = None,
    ) -> AuthorStructureProduct | None:
        if not isinstance(route, np.ndarray) or route.ndim < 2 or map_id is None or floor is None:
            return None
        return self.load(
            profile_id, map_id, floor,
            variant_id=variant_id,
            route_shape=tuple(route.shape[:2]),
        )

    def load_shared_reference(
        self,
        profile_id: str,
        map_id: str,
        floor: str,
        *,
        variant_id: str | None = None,
        expected_binding: dict | None = None,
    ):
        """Load an explicit game-rendered binary reference, without a route.

        This opt-in channel is separate from ``load`` so old author products
        cannot silently become shared geometry.  Index entries must name a
        ``sharedResourcePath`` (or ``sharedMaskResourcePath``); absent keys
        remain a hard failure.
        """
        from shared_structure_reference import load_shared_structure

        entry = self.entries.get(self._key(profile_id, map_id, floor, variant_id))
        if entry is None:
            return None
        relative = entry.get("sharedResourcePath", entry.get("sharedMaskResourcePath"))
        if not isinstance(relative, str) or not relative or Path(relative).is_absolute() or ".." in Path(relative).parts:
            return None
        path = (self.resource_root / Path(relative)).resolve()
        try:
            if not path.is_relative_to(self.resource_root.resolve()):
                return None
        except ValueError:
            return None
        binding = expected_binding or dict(
            profile=str(profile_id), mapId=str(map_id), floor=str(floor),
            variant=str(variant_id or "variant-01"),
        )
        return load_shared_structure(path, expected_binding=binding)


def decode_native_wall_segments(rows, shape):
    """Columns: axis, sign, normal, lo, hi, optional contour edge (-1)."""
    rows = np.asarray(rows)
    if rows.ndim != 2 or rows.shape[1] != 6 or not len(rows) or not np.issubdtype(rows.dtype, np.number):
        return None
    if not np.isfinite(rows).all() or not np.isin(rows[:, 0], [0, 1]).all() or not np.isin(rows[:, 1], [-1, 1]).all():
        return None
    if (rows[:, 3] > rows[:, 4]).any() or (rows[:, 2:5] < 0).any():
        return None
    if (rows[:, 5] < -1).any() or (rows[:, 5] != np.floor(rows[:, 5])).any():
        return None
    result = []
    for axis, sign, normal, lo, hi, edge in rows:
        axis = int(axis)
        if normal >= shape[1-axis] or hi >= shape[axis]:
            return None
        line = dict(axis=axis, sign=int(sign), normal=float(normal), lo=float(lo), hi=float(hi))
        if edge >= 0:
            line['edgeId'] = int(edge)
        result.append(line)
    return result
