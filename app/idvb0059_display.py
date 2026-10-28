"""Bounded, per-floor retained display rasters for the isolated 0059 trial.

Only presentation translation is rounded (at most 0.5 client pixel per axis).
The caller's alignment matrix is never changed. Perspective and excessive
canvases explicitly fall back to the existing renderer instead of reusing a
stale packet. Cache ownership belongs to the assistant's map/resource session.
"""
from __future__ import annotations

from collections import OrderedDict
from dataclasses import dataclass, replace
import threading
import time

import cv2
import numpy as np

from transparent_display_resources import unpremultiply_display_bgra


def client_clip(offset, bitmap_size, client_size):
    """Intersection with the game client, in local bitmap coordinates."""
    x, y = offset
    width, height = bitmap_size
    cw, ch = client_size
    x0, y0 = max(0, min(width, -x)), max(0, min(height, -y))
    x1, y1 = max(x0, min(width, cw - x)), max(y0, min(height, ch - y))
    return int(x0), int(y0), int(x1), int(y1)


@dataclass(frozen=True, eq=False)
class RetainedDisplayPacket:
    pixels: np.ndarray
    token: object
    client_offset: tuple[int, int]
    clip_bounds: tuple[int, int, int, int]
    client_size: tuple[int, int]
    identity_key: object

    def at_offset(self, offset):
        offset = tuple(int(value) for value in offset)
        height, width = self.pixels.shape[:2]
        return replace(self, client_offset=offset,
                       clip_bounds=client_clip(offset, (width, height), self.client_size))


@dataclass
class _Raster:
    owner: np.ndarray
    snapshot: np.ndarray
    linear_key: tuple
    pixels: np.ndarray
    anchor: tuple[int, int]
    token: object

    @property
    def nbytes(self):
        return self.owner.nbytes + self.snapshot.nbytes + self.pixels.nbytes


def _immutable_pixels(pixels):
    # A bytes owner prevents callers from undoing setflags(write=False).
    return np.frombuffer(pixels.tobytes(), np.uint8).reshape(pixels.shape)


class RetainedDisplayCache:
    def __init__(self, *, max_entries=8, max_bytes=96 * 1024 * 1024,
                 max_dimension=8192, max_work_bytes=256 * 1024 * 1024):
        self.max_entries = max(1, int(max_entries))
        self.max_bytes = max(1, int(max_bytes))
        self.max_dimension = max(1, int(max_dimension))
        self.max_work_bytes = max(1, int(max_work_bytes))
        self._entries = OrderedDict()
        self._lock = threading.RLock()
        self.bitmap_build_count = 0
        self.reuse_count = 0

    @property
    def retained_bytes(self):
        with self._lock:
            return sum(entry.nbytes for entry in self._entries.values())

    def clear(self):
        with self._lock:
            self._entries.clear()

    def invalidate(self, identity_key):
        with self._lock:
            self._entries.pop(identity_key, None)

    def build(self, display_source_uint8_BGRA, matrix3x3, frame_shape, identity_key):
        started = time.perf_counter()
        with self._lock:
            output, packet, diagnostics = self._build(
                display_source_uint8_BGRA, matrix3x3, frame_shape, identity_key)
            diagnostics.update(bitmapBuildCount=self.bitmap_build_count,
                               bitmapReuseCount=self.reuse_count,
                               retainedBytes=self.retained_bytes,
                               elapsedMs=round((time.perf_counter() - started) * 1000, 3))
            return output, packet, diagnostics

    def _build(self, source, matrix, frame_shape, identity_key):
        def fallback(reason):
            self.invalidate(identity_key)
            return None, None, dict(retained=False, reason=reason)

        hash(identity_key)
        if (not isinstance(source, np.ndarray) or source.dtype != np.uint8
                or source.ndim != 3 or source.shape[2] != 4
                or min(source.shape[:2]) <= 0):
            return fallback('unsupported-source')
        matrix = np.asarray(matrix, np.float64)
        if (matrix.shape != (3, 3) or not np.isfinite(matrix).all()
                or not np.array_equal(matrix[2], [0, 0, 1])):
            return fallback('unsupported-perspective')
        linear = matrix[:2, :2]
        if abs(float(np.linalg.det(linear))) < 1e-12:
            return fallback('singular-transform')
        ch, cw = (int(value) for value in frame_shape[:2])
        sh, sw = source.shape[:2]
        if min(cw, ch) <= 0 or max(cw, ch) > self.max_dimension:
            return fallback('client-dimensions')
        # Include the complete bilinear support before clipping, including
        # rotations/shears and fractional scales. Translation is independent.
        corners = np.array([[-1, -1], [sw, -1], [sw, sh], [-1, sh]], np.float64)
        with np.errstate(over='ignore', invalid='ignore'):
            corners = corners @ linear.T
        if not np.isfinite(corners).all():
            return fallback('transform-bounds')
        minimum = np.floor(corners.min(axis=0)) - 2
        maximum = np.ceil(corners.max(axis=0)) + 2
        extent = maximum - minimum
        if (min(extent) <= 0 or max(extent) > self.max_dimension
                or np.max(np.abs(minimum)) > 2 ** 30
                or np.max(np.abs(matrix[:2, 2])) > 2 ** 30):
            return fallback('bitmap-dimensions')
        width, height = (int(value) for value in extent)
        # Budget source ownership+snapshot, immutable raster and interpolation
        # scratch before allocating. Existing cached entries are separately LRU
        # bounded; the full client array remains caller-owned, never retained.
        estimated_retained = source.nbytes * 2 + width * height * 4
        estimated_work = source.nbytes * 6 + width * height * 28 + cw * ch * 4
        if estimated_retained > self.max_bytes or estimated_work > self.max_work_bytes:
            return fallback('bitmap-memory-budget')
        linear_key = tuple(linear.ravel())
        entry = self._entries.get(identity_key)
        reused = bool(entry is not None and entry.owner is source
                      and entry.linear_key == linear_key
                      and entry.snapshot.shape == source.shape
                      and np.array_equal(entry.snapshot, source))
        if not reused:
            # Source snapshots protect against in-place edits by the resource
            # owner, without introducing content hashes or trusting array id.
            snapshot = _immutable_pixels(source)
            premultiplied = snapshot.astype(np.float32)
            premultiplied[:, :, 3] /= 255.0
            premultiplied[:, :, :3] *= premultiplied[:, :, 3:4]
            local_matrix = np.eye(3, dtype=np.float64)
            local_matrix[:2, :2] = linear
            local_matrix[:2, 2] = -minimum
            warped = cv2.warpPerspective(
                premultiplied, local_matrix, (width, height), flags=cv2.INTER_LINEAR,
                borderMode=cv2.BORDER_CONSTANT, borderValue=(0, 0, 0, 0))
            pixels = unpremultiply_display_bgra(warped)
            x, y, ink_width, ink_height = cv2.boundingRect(pixels[:, :, 3])
            if not ink_width or not ink_height:
                return fallback('empty-bitmap')
            x0, y0 = max(0, x - 2), max(0, y - 2)
            x1, y1 = min(width, x + ink_width + 2), min(height, y + ink_height + 2)
            entry = _Raster(source, snapshot, linear_key,
                            _immutable_pixels(pixels[y0:y1, x0:x1]),
                            (int(minimum[0]) + x0, int(minimum[1]) + y0), object())
            self._entries[identity_key] = entry
            self.bitmap_build_count += 1
        else:
            self.reuse_count += 1
        self._entries.move_to_end(identity_key)
        while len(self._entries) > self.max_entries or self.retained_bytes > self.max_bytes:
            self._entries.popitem(last=False)
        translation = tuple(int(round(value)) for value in matrix[:2, 2])
        offset = tuple(entry.anchor[i] + translation[i] for i in range(2))
        ph, pw = entry.pixels.shape[:2]
        clip = client_clip(offset, (pw, ph), (cw, ch))
        packet = RetainedDisplayPacket(entry.pixels, entry.token, offset, clip, (cw, ch), identity_key)
        output = np.zeros((ch, cw, 4), np.uint8)
        x0, y0, x1, y1 = clip
        if x1 > x0 and y1 > y0:
            ox, oy = offset
            output[oy + y0:oy + y1, ox + x0:ox + x1] = entry.pixels[y0:y1, x0:x1]
        return output, packet, dict(
            retained=True, renderedPixelsReused=reused, localBitmapSize=(pw, ph),
            clientOffset=offset, clipBounds=clip,
            presentationRoundingError=tuple(float(translation[i] - matrix[i, 2]) for i in range(2)),
            reason='retained-local-bitmap')
