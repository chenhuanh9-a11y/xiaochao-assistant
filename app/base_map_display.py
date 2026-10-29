"""Read-only normal map display, registered to the shared structure canvas.

The caller decides whether an author is missing and whether a custom gallery is
selected. This provider never changes those preferences or grants a pose.
"""
from __future__ import annotations

from collections import OrderedDict
from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
import threading

import cv2
import numpy as np
from PIL import Image

from custom_maps import CustomMapLibrary, mask_sha256, render_game_layer


@dataclass(frozen=True)
class BaseMapDisplayResult:
    layer: dict | None
    reason: str = ""


def _stamp(path):
    stat = Path(path).stat()
    return stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns


def _sha(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


class BaseMapDisplay:
    """Bounded per-resource cache; replace/clear on resource-context invalidation.

    Returns native-canvas pixels with an identity *display* bridge because the
    authoritative image-to-native matrix was already applied by render_game_layer.
    Recognition always retains its separate clean-mask/pose inputs.
    """

    def __init__(self, capacity=6, max_bytes=96 * 1024 * 1024):
        self.capacity = max(1, min(int(capacity), 6))
        self.max_bytes = max(1, min(int(max_bytes), 96 * 1024 * 1024))
        self._cache = OrderedDict()
        self._lock = threading.RLock()
        self._generation = 0

    def clear(self):
        with self._lock:
            self._generation += 1
            self._cache.clear()

    def peek(self, library, map_id, floor, reference):
        """UI-only, in-memory lookup. Owner must clear on resource invalidation.

        Does not stat/hash/decode or wait behind cold preparation. A miss requires
        worker preparation; callers retain safe structural fallback meanwhile.
        """
        key = (id(library), str(map_id), str(floor), id(reference))
        with self._lock:
            entry = self._cache.get(key)
            if entry is not None and entry[0] is library and entry[1] is reference:
                return BaseMapDisplayResult(dict(entry[3]))
        return BaseMapDisplayResult(None, "base-map-not-prepared")

    def resolve(self, library, map_id, floor, reference):
        """Worker-only preparation/revalidation; never call from the Tk thread."""
        key = (id(library), str(map_id), str(floor), id(reference))
        with self._lock:
            started_entry = self._cache.get(key)
            started_generation = self._generation
        try:
            if library is None or not library.available or not library.has(map_id):
                result = BaseMapDisplayResult(None, "base-map-unavailable")
            else:
                result = self._resolve(library, str(map_id), str(floor), reference)
        except KeyError:
            result = BaseMapDisplayResult(None, "base-map-floor-unavailable")
        except FileNotFoundError:
            result = BaseMapDisplayResult(None, "base-map-file-missing")
        except (OSError, ValueError, TypeError, AttributeError, cv2.error):
            result = BaseMapDisplayResult(None, "base-map-invalid-binding-or-image")
        if result.layer is None:
            # A failed revalidation revokes its old pixels, but may not erase a
            # newer worker's successful publication or a new resource generation.
            with self._lock:
                if (started_entry is not None and self._generation == started_generation
                        and self._cache.get(key) is started_entry):
                    self._cache.pop(key, None)
        return result

    def _resolve(self, library, map_id, floor, reference):
        with self._lock:
            generation = self._generation
        # This is the same schema owner used by CustomMapLibrary.floor/validate_reference.
        row = library._maps[map_id]["floors"][floor]
        row_key = json.dumps(row, sort_keys=True, separators=(",", ":"))
        asset = library.floor(map_id, floor)
        matrix = np.asarray(asset.to_native, np.float64)
        if (matrix.shape != (3, 3) or not np.isfinite(matrix).all()
                or not np.allclose(matrix[2], (0, 0, 1), rtol=0, atol=1e-12)
                or abs(np.linalg.det(matrix[:2, :2])) < 1e-12):
            raise ValueError("invalid image-to-native affine")
        mask = np.asarray(reference.clean_mask)
        if mask.nbytes + mask.size * 4 > self.max_bytes:
            return BaseMapDisplayResult(None, 'base-map-cache-byte-limit')
        if mask.ndim != 2 or tuple(mask.shape) != tuple(asset.native_shape):
            raise ValueError("native canvas mismatch")
        if tuple(reference.shape) != tuple(asset.native_shape):
            raise ValueError("reference shape mismatch")
        # Mutable masks must be re-hashed: identity alone cannot prove their content.
        mutable_digest = mask_sha256(mask) if mask.flags.writeable else None
        icon = library.root / "exit-icon.png"
        files = (library.root / "manifest.json", asset.image_path, asset.topology_path)
        stamps = tuple(_stamp(path) for path in files) + ((_stamp(icon) if icon.exists() else None),)
        key = (id(library), map_id, floor, id(reference))
        fingerprint = (library.version, id(mask), mutable_digest, row_key, stamps)
        with self._lock:
            entry = self._cache.get(key)
            if (entry is not None and entry[0] is library and entry[1] is reference
                    and entry[2] == fingerprint and generation == self._generation):
                self._cache.move_to_end(key)
                return BaseMapDisplayResult(dict(entry[3]))
        # Fresh decoder avoids the library's previously verified/image caches
        # retaining pixels after an import, deletion or same-path replacement.
        fresh = CustomMapLibrary(library.root)
        if (not fresh.available or fresh.version != library.version
                or fresh._maps.get(map_id, {}).get("floors", {}).get(floor) != row):
            raise ValueError("resource manifest changed; refresh context first")
        fresh.validate_reference(map_id, floor, mask)
        if _sha(asset.image_path) != row["imageSha256"]:
            raise ValueError("display image hash mismatch")
        with Image.open(asset.image_path) as image:
            if image.size != tuple(asset.image_size):
                raise ValueError("declared image size mismatch")
            image.load()
        pixels = render_game_layer(fresh, map_id, floor, [])
        after = tuple(_stamp(path) for path in files) + ((_stamp(icon) if icon.exists() else None),)
        if after != stamps:
            raise ValueError("resource changed while rendering")
        if (pixels.dtype != np.uint8 or pixels.shape != (*mask.shape, 4)
                or not np.any(pixels[:, :, 3])):
            raise ValueError("empty or malformed display")
        pixels.setflags(write=False)
        bridge = np.eye(3)
        bridge.setflags(write=False)
        layer = dict(routeSource=pixels, sourcePath=Path(reference.path),
            displayAssetPath=asset.image_path, matrixDisplayToReference=bridge,
            displayKind="base-map", displayLabel="基础地图（暂无作者路线）",
            displayResourceKey=(str(library.root.resolve()), library.version, map_id, floor,
                                row["imageSha256"], row["maskSha256"], row_key))
        with self._lock:
            if generation != self._generation:
                return BaseMapDisplayResult(None, "base-map-stale-preparation")
            self._cache[key] = (library, reference, fingerprint, layer)
            while (len(self._cache) > self.capacity or sum(
                    entry[1].clean_mask.nbytes + entry[3]['routeSource'].nbytes
                    for entry in self._cache.values()) > self.max_bytes):
                self._cache.popitem(last=False)
            return BaseMapDisplayResult(dict(layer))
