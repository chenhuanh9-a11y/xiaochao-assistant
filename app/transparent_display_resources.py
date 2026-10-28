"""Display-only transparent artwork; never an identity/validation image loader.

Callers explicitly select a delivered manifest. A producer's ``generated`` row
is not a declaration that its still-in-progress directory has been accepted.
The current producer preserves the original canvas; other transforms are
reported as unsupported rather than guessed from the visible pixel bounds.
"""

from __future__ import annotations

from collections import OrderedDict
import json
import threading
from pathlib import Path, PurePosixPath

import cv2
import numpy as np
from PIL import Image


def _path_token(value: str | Path) -> str:
    return str(PurePosixPath(str(value).replace('\\', '/'))).rstrip('/').casefold()


def _relative_path(value: str) -> Path:
    path = PurePosixPath(str(value).replace('\\', '/'))
    if path.is_absolute() or ':' in str(path) or '..' in path.parts:
        raise ValueError(f'display resource path must stay relative: {value}')
    return Path(*path.parts)


class TransparentDisplayStore:
    """Resolve exact display roles, with a small cache separate from originals."""

    def __init__(self, manifest_path: Path, *, source_root: Path,
                 resource_root: Path | None = None, cache_entries: int = 2,
                 source_roots: tuple[Path, ...] = ()):
        self.manifest_path = Path(manifest_path)
        self.source_root = Path(source_root).resolve()
        self.source_roots = tuple(dict.fromkeys((self.source_root, *(Path(root).resolve() for root in source_roots))))
        self.resource_root = Path(resource_root or self.manifest_path.parent).resolve()
        self.cache_entries = max(1, int(cache_entries))
        payload = json.loads(self.manifest_path.read_text(encoding='utf-8-sig'))
        self.producer_source_root = _path_token(payload.get('sourceRoot', ''))
        self._by_source: dict[str, list[dict]] = {}
        self._by_key: dict[tuple, list[dict]] = {}
        self._cache: OrderedDict[str, np.ndarray] = OrderedDict()
        self._cache_lock = threading.RLock()
        for row in payload['records']:
            source = _path_token(_relative_path(row['sourceRelative']))
            # Keep lists: aliases or producer duplicates must not overwrite a
            # different author/role bound to the same original file.
            self._by_source.setdefault(source, []).append(row)
            for role in row['displayRoles']:
                key = self._key(row['mode'], row['profileId'], row['mapId'],
                                row['floor'], row.get('variantId'), role)
                self._by_key.setdefault(key, []).append(row)

    @staticmethod
    def _key(mode, profile_id, map_id, floor, variant_id, role) -> tuple:
        return (str(mode), str(profile_id), str(map_id), str(floor),
                str(variant_id or 'variant-01'), str(role))

    @staticmethod
    def _one(rows: list[dict], description: str) -> dict:
        if not rows:
            raise KeyError(f'transparent display resource unavailable: {description}')
        # Duplicate inventory entries for one physical output are harmless.
        # Different outputs/geometry for the requested binding are ambiguous.
        signatures = {
            (_path_token(row['sourceRelative']), _path_token(row['outputRelativePath']),
             row['stats']['width'], row['stats']['height'],
             json.dumps(row['sourceToOutput'], separators=(',', ':')),
             json.dumps(row.get('outputToOriginal'), separators=(',', ':')),
             row.get('status')) for row in rows
        }
        if len(signatures) != 1:
            raise ValueError(f'ambiguous transparent display resource: {description}')
        return dict(rows[0])

    def entry(self, mode: str, profile_id: str, map_id: str, floor: str,
              role: str, *, variant_id: str | None = None) -> dict:
        key = self._key(mode, profile_id, map_id, floor, variant_id, role)
        return self._one(self._by_key.get(key, []), repr(key))

    def entry_for_source(self, source: Path, role: str) -> dict:
        token = _path_token(source)
        for root in (*(_path_token(path) for path in self.source_roots), self.producer_source_root):
            if root and token.startswith(root + '/'):
                token = token[len(root) + 1:]
                break
        rows = [row for row in self._by_source.get(token, [])
                if role in row['displayRoles']]
        return self._one(rows, f'{source} ({role})')

    def load(self, mode: str, profile_id: str, map_id: str, floor: str,
             role: str, *, variant_id: str | None = None) -> np.ndarray:
        return self._load(self.entry(mode, profile_id, map_id, floor, role,
                                     variant_id=variant_id))

    def load_for_source(self, source: Path, role: str) -> np.ndarray:
        original = Path(source)
        token = _path_token(original)
        known = original.is_absolute() and any(token.startswith(_path_token(root) + '/') for root in self.source_roots)
        return self._load(self.entry_for_source(source, role), original_path=original if known else None)

    def source_path(self, entry: dict) -> Path:
        """Resolve an original header within explicit runtime roots only."""
        relative = _relative_path(entry['sourceRelative'])
        candidates = [root / relative for root in self.source_roots if (root / relative).is_file()]
        if len(candidates) > 1:
            raise ValueError(f"ambiguous original header across runtime roots: {entry['sourceRelative']}")
        if candidates:
            return candidates[0]
        raise FileNotFoundError(f"transparent display original header unavailable: {entry['sourceRelative']}")

    def _load(self, row: dict, *, original_path: Path | None = None) -> np.ndarray:
        if row.get('status') != 'generated':
            raise ValueError(f"transparent display resource not generated: {row['sourceRelative']}")
        for field in ('sourceToOutput', 'outputToOriginal'):
            matrix = np.asarray(row[field], dtype=np.float64)
            if (matrix.shape != (3, 3) or not np.isfinite(matrix).all()
                    or not np.allclose(matrix, np.eye(3), rtol=0, atol=1e-9)):
                raise ValueError(f'unsupported transparent display canvas transform: {field}')
        relative = _relative_path(row['outputRelativePath'])
        path = (self.resource_root / relative).resolve()
        if not path.is_relative_to(self.resource_root):
            raise ValueError(f'display resource leaves its root: {relative}')
        expected = (int(row['stats']['width']), int(row['stats']['height']))
        with Image.open(original_path or self.source_path(row)) as original:
            original_size = original.size  # Header only; no original-pixel/cache replacement.
        if expected != original_size:
            raise ValueError(f'transparent display image does not preserve original canvas: {path}')
        key = str(path)
        with self._cache_lock:
            pixels = self._cache.get(key)
        if pixels is None:
            pixels = cv2.imdecode(np.fromfile(path, dtype=np.uint8), cv2.IMREAD_UNCHANGED)
        if pixels is None or pixels.dtype != np.uint8 or pixels.ndim != 3 or pixels.shape[2] != 4:
            raise ValueError(f'transparent display image must be uint8 BGRA: {path}')
        with self._cache_lock:
            # Another reader may have filled/evicted this path during IO.
            # Validate the actual winner for this binding before every return.
            pixels = self._cache.get(key, pixels)
            if (pixels.shape[1], pixels.shape[0]) != expected:
                raise ValueError(f'transparent display image does not preserve original canvas: {path}')
            pixels.setflags(write=False)
            self._cache[key] = pixels
            self._cache.move_to_end(key)
            while len(self._cache) > self.cache_entries:
                self._cache.popitem(last=False)
            return pixels


def unpremultiply_display_canvas(
    warped: np.ndarray, source_shape: tuple, matrix: np.ndarray,
) -> np.ndarray:
    """Convert only a conservative support region of a full, unchanged warp.

    Moving the warp's origin changes OpenCV interpolation rounding. Keep that
    warp intact and reduce only the independent per-pixel alpha conversion.
    Other transforms retain the original full-canvas path.
    """
    m = np.asarray(matrix, np.float64)
    if (m.shape != (3, 3) or not np.isfinite(m).all()
            or not np.array_equal(m[2], [0, 0, 1])
            or m[0, 1] != 0 or m[1, 0] != 0 or m[0, 0] <= 0 or m[1, 1] <= 0):
        return unpremultiply_display_bgra(warped)
    height, width = warped.shape[:2]
    sh, sw = source_shape[:2]
    # Bilinear sampling can reach one source pixel beyond the canvas. Include
    # that support, plus two destination pixels for boundary quantization.
    with np.errstate(over='ignore', invalid='ignore'):
        limits = np.array([m[0, 2] - m[0, 0], m[1, 2] - m[1, 1],
                           m[0, 0] * sw + m[0, 2], m[1, 1] * sh + m[1, 2]])
    if not np.isfinite(limits).all():
        return unpremultiply_display_bgra(warped)
    x0 = max(0, min(width, int(np.floor(limits[0])) - 2))
    y0 = max(0, min(height, int(np.floor(limits[1])) - 2))
    x1 = max(0, min(width, int(np.ceil(limits[2])) + 2))
    y1 = max(0, min(height, int(np.ceil(limits[3])) + 2))
    # A small trim does not repay the additional output copy. Empty or tiny
    # regions also retain the existing conversion behavior.
    if min(x1 - x0, y1 - y0) < 4 or (x1 - x0) * (y1 - y0) * 4 >= width * height * 3:
        return unpremultiply_display_bgra(warped)
    output = np.zeros(warped.shape, np.uint8)
    output[y0:y1, x0:x1] = unpremultiply_display_bgra(warped[y0:y1, x0:x1])
    return output


def unpremultiply_display_bgra(warped: np.ndarray) -> np.ndarray:
    """Return the same straight-alpha bytes using contiguous color planes.

    ``warped`` is float32 BGR premultiplied by alpha in [0, 1]. Quantized-zero
    alpha pixels are zero in the existing display contract, so their colors
    need no division. Keep the float alpha divisor and NumPy rounding for the
    remaining pixels; this must not change interpolation or client coordinates.
    """
    alpha = np.clip(cv2.extractChannel(warped, 3), 0.0, 1.0)
    alpha_bytes = np.rint(alpha * 255.0).astype(np.uint8)
    output = np.zeros(warped.shape, dtype=np.uint8)
    x, y, width, height = cv2.boundingRect(alpha_bytes)
    if width == 0 or height == 0:
        return output
    # Work across all three channels while a small strip is still hot, instead
    # of repeatedly walking full-frame float buffers. Pixel arithmetic and the
    # quantized-alpha visibility rule stay identical.
    block_rows = max(1, 32768 // width)
    for top in range(y, y + height, block_rows):
        region = np.s_[top:min(top + block_rows, y + height), x:x + width]
        divisor = np.ascontiguousarray(alpha[region])
        positive = divisor > 0
        visible = cv2.compare(alpha_bytes[region], 0, cv2.CMP_GT)
        for channel in range(3):
            premultiplied = cv2.extractChannel(warped[region], channel)
            color = np.divide(premultiplied, divisor,
                              out=np.zeros_like(premultiplied), where=positive)
            np.rint(color, out=color)
            np.clip(color, 0, 255, out=color)
            output[region][:, :, channel] = cv2.bitwise_and(
                color.astype(np.uint8), visible)
        output[region][:, :, 3] = alpha_bytes[region]
    return output


def fit_display_bgra(pixels: np.ndarray, max_size: tuple[int, int]) -> np.ndarray:
    """Fit a straight-alpha BGRA canvas without dark transparent-edge fringes.

    Output remains straight alpha; the native compositor applies the player's
    global opacity and performs its required premultiplication exactly once.
    No cropping or source-coordinate transformation happens here.
    """
    if pixels.dtype != np.uint8 or pixels.ndim != 3 or pixels.shape[2] != 4:
        raise ValueError('display image must be uint8 BGRA')
    height, width = pixels.shape[:2]
    maximum_width, maximum_height = map(int, max_size)
    if min(height, width, maximum_width, maximum_height) <= 0:
        raise ValueError('display canvas and fit bounds must be positive')
    scale = min(1.0, maximum_width / width, maximum_height / height)
    size = (max(1, round(width * scale)), max(1, round(height * scale)))
    if size == (width, height):
        return pixels
    alpha = pixels[:, :, 3:4].astype(np.float32) / 255.0
    premultiplied = np.concatenate((pixels[:, :, :3].astype(np.float32) * alpha, alpha), axis=2)
    resized = cv2.resize(premultiplied, size, interpolation=cv2.INTER_AREA)
    resized_alpha = np.clip(resized[:, :, 3:4], 0.0, 1.0)
    rgb = np.divide(resized[:, :, :3], resized_alpha,
                    out=np.zeros_like(resized[:, :, :3]), where=resized_alpha > 0)
    output = np.concatenate((np.clip(np.rint(rgb), 0, 255),
                             np.rint(resized_alpha * 255.0)), axis=2).astype(np.uint8)
    output[output[:, :, 3] == 0] = 0
    return np.ascontiguousarray(output)
