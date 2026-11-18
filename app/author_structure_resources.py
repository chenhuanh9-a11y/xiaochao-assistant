"""Optional, fail-closed loader for dual-channel author structure products."""

from __future__ import annotations

import json
import os
import sys
import zipfile
import zlib
from dataclasses import dataclass
from io import BytesIO
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
        *,
        read_resource=None,
        check_current=None,
    ) -> None:
        if read_resource is not None and (index_path is None or resource_root is None):
            raise ValueError("external author resources require explicit index_path and resource_root")
        configured_index = os.environ.get("IDENTITYV_AUTHOR_STRUCTURE_INDEX")
        configured_root = os.environ.get("IDENTITYV_AUTHOR_STRUCTURE_ROOT")
        if read_resource is None:
            self.index_path = Path(index_path or configured_index or DEFAULT_INDEX)
            self.resource_root = Path(resource_root or configured_root or DEFAULT_RESOURCE_ROOT)
        else:
            self.index_path = Path(index_path)
            self.resource_root = Path(resource_root)
        self.read_resource = read_resource
        self.check_current = check_current
        self.entries: dict[tuple[str, str, str, str], dict] = {}
        self.error: str | None = None
        self._cache: dict[tuple[str, str, str, str], AuthorStructureProduct] = {}
        self._assert_current()
        try:
            self._load_index()
        finally:
            self._assert_current()

    def _assert_current(self) -> None:
        if self.check_current is None:
            return
        try:
            self.check_current()
        except Exception:
            # A stale view must never leave a prior successful product usable.
            self._cache.clear()
            raise

    @staticmethod
    def _key(profile_id: str, map_id: str, floor: str, variant_id: str | None) -> tuple[str, str, str, str]:
        return (str(profile_id), str(map_id), str(variant_id or "variant-01"), str(floor))

    def _load_index(self) -> None:
        try:
            if self.read_resource is None:
                payload = json.loads(self.index_path.read_text(encoding="utf-8"))
            else:
                from imported_resource_reader import decode_resource_json
                payload = decode_resource_json(self.read_resource(self.index_path))
            if int(payload.get("schemaVersion", 0)) != SUPPORTED_SCHEMA_VERSION:
                raise ValueError("unsupported author structure resource schema")
            if payload.get("validationMaskContract") != "author_route_structure_mask":
                raise ValueError("author structure resource contract mismatch")
            for entry in payload.get("entries", []):
                key = self._key(
                    entry["profileId"], entry["mapId"],
                    entry["floor"], entry.get("variantId"),
                )
                if self.read_resource is not None and key in self.entries:
                    raise ValueError("duplicate author structure resource entry")
                self.entries[key] = dict(entry)
        except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError) as error:
            self.error = str(error)
            self.entries = {}

    def _path_for(self, entry: dict) -> Path:
        raw_relative = entry["resourcePath"]
        relative = Path(str(raw_relative))
        if self.read_resource is None:
            return self.resource_root / relative
        if (not isinstance(raw_relative, str) or not raw_relative
                or relative.is_absolute() or ".." in relative.parts):
            raise ValueError("invalid author structure resource path")
        # Keep the lexical path so the component reader can reject links
        # instead of receiving a resolved target with the link erased.
        return self.resource_root / relative

    def load(
        self,
        profile_id: str,
        map_id: str,
        floor: str,
        *,
        variant_id: str | None = None,
        route_shape: tuple[int, int] | None = None,
    ) -> AuthorStructureProduct | None:
        self._assert_current()
        try:
            return self._load_current(
                profile_id, map_id, floor,
                variant_id=variant_id, route_shape=route_shape,
            )
        finally:
            self._assert_current()

    def _load_current(
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
            path = self._path_for(entry)
            if self.read_resource is None:
                source = path
            else:
                from imported_structure_declarations import validate_legacy_structure_declarations
                raw = self.read_resource(path)
                validate_legacy_structure_declarations(raw, expected_shape=route_shape)
                source = BytesIO(raw)
            with np.load(source, allow_pickle=False) as payload:
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
        except zlib.error:
            # Imported/package bytes can pass bounded-header inspection while
            # failing later when NumPy inflates a member. Keep trusted legacy
            # path behavior unchanged; stale-view errors are checked outside
            # this decoder block and continue to propagate.
            if self.read_resource is None:
                raise
            return None
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
        self._assert_current()
        try:
            if not isinstance(route, np.ndarray) or route.ndim < 2 or map_id is None or floor is None:
                return None
            return self.load(
                profile_id, map_id, floor,
                variant_id=variant_id,
                route_shape=tuple(route.shape[:2]),
            )
        finally:
            self._assert_current()

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
        self._assert_current()
        try:
            return self._load_shared_reference(
                profile_id, map_id, floor,
                variant_id=variant_id, expected_binding=expected_binding,
            )
        finally:
            self._assert_current()

    def _load_shared_reference(
        self,
        profile_id: str,
        map_id: str,
        floor: str,
        *,
        variant_id: str | None = None,
        expected_binding: dict | None = None,
    ):
        from shared_structure_reference import load_shared_structure

        entry = self.entries.get(self._key(profile_id, map_id, floor, variant_id))
        if entry is None:
            return None
        relative = entry.get("sharedResourcePath", entry.get("sharedMaskResourcePath"))
        if not isinstance(relative, str) or not relative or Path(relative).is_absolute() or ".." in Path(relative).parts:
            return None
        candidate = self.resource_root / Path(relative)
        path = candidate if self.read_resource is not None else candidate.resolve()
        try:
            if not path.is_relative_to(self.resource_root if self.read_resource is not None
                                       else self.resource_root.resolve()):
                return None
        except ValueError:
            return None
        binding = expected_binding or dict(
            profile=str(profile_id), mapId=str(map_id), floor=str(floor),
            variant=str(variant_id or "variant-01"),
        )
        kwargs = {}
        if self.read_resource is not None:
            kwargs.update(resource_root=self.resource_root, read_resource=self.read_resource)
        try:
            return load_shared_structure(path, expected_binding=binding, **kwargs)
        except (zlib.error, zipfile.BadZipFile):
            if self.read_resource is None:
                raise
            return None


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
