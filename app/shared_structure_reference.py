"""Typed, route-free shared structure references.

This module deliberately knows nothing about author sheets.  A reference is a
binary canvas rendered from the game structure pipeline plus the metadata that
binds that canvas to one map/floor/variant.  Missing or ambiguous products are
not substituted with an author image or a zero canvas.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

import numpy as np


@dataclass(frozen=True)
class SharedStructureReference:
    clean_mask: np.ndarray
    validation_mask: np.ndarray
    metadata: dict
    path: Path
    known_boundary: np.ndarray | None = None
    unknown_mask: np.ndarray | None = None
    native_wall_segments: list[dict] | None = None
    resource_root: Path | None = None

    @property
    def mask(self) -> np.ndarray:
        """Compatibility alias for the typed validation channel."""
        return self.validation_mask

    @property
    def shape(self) -> tuple[int, int]:
        return tuple(int(v) for v in self.clean_mask.shape)

    @property
    def entry(self) -> dict:
        return self.metadata

    @property
    def reference_to_canvas_matrix(self) -> np.ndarray | None:
        """Explicit current-reference -> shared-canvas bridge, if supplied."""
        calibration = self.metadata.get("calibration")
        raw = calibration.get("matrixCurrentReferenceToNative") if isinstance(calibration, dict) else None
        try:
            matrix = np.asarray(raw, dtype=np.float64)
        except (TypeError, ValueError):
            return None
        if matrix.shape != (3, 3) or not np.isfinite(matrix).all() or not np.allclose(matrix[2], [0.0, 0.0, 1.0]):
            return None
        return matrix


class SharedStructureResourceStore:
    """Independent index for shared geometry products.

    It intentionally does not accept the author-route index contract.  This
    keeps game geometry products from being admitted by renaming an old route
    resource or by inheriting its validation mask.
    """

    def __init__(self, index_path: str | Path, resource_root: str | Path):
        self.index_path = Path(index_path)
        self.resource_root = Path(resource_root)
        self.entries: dict[tuple[str, str, str, str], dict] = {}
        self.error: str | None = None
        try:
            payload = json.loads(self.index_path.read_text(encoding="utf-8"))
            if payload.get("schemaVersion") != 1 or payload.get("resourceKind") != "shared-structure":
                raise ValueError("unsupported shared structure index")
            for entry in payload.get("entries", []):
                key = (str(entry["profile"]), str(entry["mapId"]),
                       str(entry.get("variant", "variant-01")), str(entry["floor"]))
                if key in self.entries:
                    raise ValueError("duplicate shared structure entry")
                self.entries[key] = dict(entry)
        except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError) as error:
            self.error = str(error)
            self.entries = {}

    def load(self, profile: str, map_id: str, floor: str, *, variant: str = "variant-01") -> SharedStructureReference | None:
        entry = self.entries.get((str(profile), str(map_id), str(variant), str(floor)))
        if entry is None:
            return None
        relative = entry.get("resourcePath")
        if not isinstance(relative, str) or not relative or Path(relative).is_absolute() or ".." in Path(relative).parts:
            return None
        path = (self.resource_root / Path(relative)).resolve()
        try:
            if not path.is_relative_to(self.resource_root.resolve()):
                return None
        except ValueError:
            return None
        binding = dict(profile=str(profile), mapId=str(map_id), floor=str(floor), variant=str(variant))
        return load_shared_structure(path, expected_binding=binding, resource_root=self.resource_root)


def _metadata_from_archive(archive) -> dict | None:
    if "metadata" not in archive.files:
        return None
    value = archive["metadata"]
    if value.ndim != 0:
        return None
    try:
        payload = json.loads(str(value.item()))
    except (TypeError, ValueError, json.JSONDecodeError):
        return None
    return payload if isinstance(payload, dict) else None


def _binary(value, shape: tuple[int, int] | None = None) -> np.ndarray | None:
    array = np.asarray(value)
    if array.ndim != 2 or array.dtype != np.uint8:
        return None
    values = np.unique(array)
    if not np.all(np.isin(values, (0, 1, 255))):
        return None
    result = (array > 0).astype(np.uint8)
    if shape is not None and tuple(result.shape) != tuple(shape):
        return None
    return np.ascontiguousarray(result)


def _field_name(metadata: dict, role: str, default: str | None = None) -> str | None:
    roles = metadata.get("roles")
    if not isinstance(roles, dict):
        return default
    value = roles.get(role, default)
    return value if isinstance(value, str) and value else None


def _decode_native_wall_segments(rows, shape: tuple[int, int]) -> list[dict] | None:
    """Decode the shared wall rows into the existing line-consumer shape."""
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
        if normal >= shape[1 - axis] or hi >= shape[axis]:
            return None
        line = dict(axis=axis, sign=int(sign), normal=float(normal), lo=float(lo), hi=float(hi))
        if edge >= 0:
            line["edgeId"] = int(edge)
        result.append(line)
    return result


def load_shared_structure(
    path: str | Path,
    *,
    expected_binding: dict | None = None,
    expected_shape: tuple[int, int] | None = None,
    resource_root: str | Path | None = None,
) -> SharedStructureReference | None:
    """Load one game-rendered binary structure product, fail-closed.

    The preferred product declares explicit role fields in metadata.roles:
    ``clean``, ``validation``, and optionally ``known``, ``unknown`` and
    ``nativeWalls``.  A compact single-mask product is accepted only when its
    metadata explicitly declares both clean and validation as that same field.
    """
    candidate = Path(path)
    if candidate.suffix.lower() != ".npz" or not candidate.is_file():
        return None
    try:
        with np.load(candidate, allow_pickle=False) as archive:
            metadata = _metadata_from_archive(archive)
            if metadata is None or metadata.get("sourceKind") not in {
                "game-rendered-shared-structure", "legacy-shared-binary",
            }:
                return None
            clean_name = _field_name(metadata, "clean")
            validation_name = _field_name(metadata, "validation")
            if clean_name is None or validation_name is None:
                compact = [name for name in ("shared_mask", "game_structure_mask") if name in archive.files]
                roles = metadata.get("roles")
                if len(compact) != 1 or not isinstance(roles, dict) or roles.get("clean") != compact[0] or roles.get("validation") != compact[0]:
                    return None
                clean_name = validation_name = compact[0]
            if clean_name not in archive.files or validation_name not in archive.files:
                return None
            clean_mask = _binary(archive[clean_name])
            validation_mask = _binary(archive[validation_name])
            if clean_mask is None or validation_mask is None or clean_mask.shape != validation_mask.shape:
                return None
            known_name = _field_name(metadata, "known")
            unknown_name = _field_name(metadata, "unknown")
            known = _binary(archive[known_name]) if known_name in archive.files else None if known_name is None else None
            unknown = _binary(archive[unknown_name]) if unknown_name in archive.files else None if unknown_name is None else None
            if (known_name is not None and known is None) or (unknown_name is not None and unknown is None):
                return None
            if (known is None) != (unknown is None) or any(x is not None and x.shape != clean_mask.shape for x in (known, unknown)):
                return None
            native = None
            native_name = _field_name(metadata, "nativeWalls")
            if native_name is not None:
                if native_name not in archive.files:
                    return None
                native = _decode_native_wall_segments(archive[native_name], clean_mask.shape)
                if native is None:
                    return None
    except (OSError, ValueError, TypeError, KeyError):
        return None
    if expected_shape is not None and tuple(clean_mask.shape) != tuple(expected_shape):
        return None
    if expected_binding is not None:
        binding = metadata.get("binding")
        if not isinstance(binding, dict) or binding != dict(expected_binding):
            return None
    return SharedStructureReference(clean_mask, validation_mask, dict(metadata), candidate, known, unknown,
                                    native, Path(resource_root).resolve() if resource_root is not None else None)


__all__ = ["SharedStructureReference", "SharedStructureResourceStore", "load_shared_structure"]
