"""Existing custom art/routes as a display source, never identity or pose input."""
from __future__ import annotations

from copy import deepcopy
import hashlib
import json
import sys
import threading

import cv2
import numpy as np

from client_paths import APP_ROOT
if str(APP_ROOT) not in sys.path:
    sys.path.insert(0, str(APP_ROOT))
from custom_maps import (RouteError, gallery_of, profile_id as custom_profile_id,
                         mask_sha256, render_game_layer)
from shared_structure_reference import SharedStructureReference


def _route_hash(points):
    return hashlib.sha256(json.dumps(points, sort_keys=True, separators=(",", ":"),
                                    ensure_ascii=False).encode("utf-8")).hexdigest()


class CustomGalleryDisplay:
    def __init__(self, service):
        self.service = service
        self._lock = threading.RLock()
        self._layers = {}
        self._last_error = ""

    @property
    def last_error(self):
        with self._lock:
            return self._last_error

    def eligibility(self, gallery_id, mode):
        """Prepare a preference request; this does not apply it or mark it active."""
        if mode != "nightmare":
            return dict(allowed=False, message="切换到噩梦模式后才能使用自定义图集")
        try:
            state = self.service.display_snapshot(str(gallery_id))
            if state is None:
                return dict(allowed=False, message="图集不存在、正在准备或已被其他操作修改")
            if not state["library"].available:
                return dict(allowed=False, message=state["library"].error or "自定义地图资源未安装")
            return dict(allowed=True, galleryId=state["galleryId"],
                        profileId=custom_profile_id(state["galleryId"]), message="图集可用，等待应用设置")
        except (OSError, ValueError, TypeError, KeyError) as error:
            return dict(allowed=False, message=f"图集不可用：{error}")

    def use_request(self, gallery_id, mode, *, fallback_profile="wang-qingxin"):
        if not gallery_id:
            return dict(success=True, applied=False, galleryId="", profileId=fallback_profile,
                        message="正在恢复作者路线")
        result = self.eligibility(gallery_id, mode)
        return dict(result, success=result["allowed"], applied=False)

    def active_gallery(self, profile, mode):
        gallery_id = gallery_of(profile)
        return gallery_id if gallery_id and self.eligibility(gallery_id, mode)["allowed"] else ""

    def invalidate(self):
        with self._lock:
            self._layers.clear()

    def layer(self, profile, map_id, floor, reference, mode):
        """Return BGRA for the runtime's existing single BGRA→RGBA conversion."""
        gallery_id = gallery_of(profile)
        if gallery_id is None:
            return None
        try:
            if mode != "nightmare":
                raise ValueError("custom-gallery-nightmare-only")
            if not isinstance(reference, SharedStructureReference):
                raise ValueError("custom-gallery-typed-reference-required")
            binding = reference.metadata.get("binding")
            if (not isinstance(binding, dict) or str(binding.get("mapId")) != str(map_id)
                    or str(binding.get("floor")) != str(floor)):
                raise ValueError("custom-gallery-reference-binding-mismatch")
            state = self.service.display_snapshot(gallery_id, str(map_id))
            if state is None:
                raise ValueError("custom-gallery-unavailable-or-changed")
            library = state["library"]
            if not library.available or not library.has(map_id):
                raise ValueError(library.error or "custom-map-unavailable")
            # The production contract checks BOTH native shape and exact clean
            # mask digest before any custom image can become a display layer.
            library.validate_reference(map_id, str(floor), reference.clean_mask)
            manifest_hash = hashlib.sha256((library.root / "manifest.json").read_bytes()).hexdigest()
            route_hash = _route_hash(state["points"])
            reference_hash = mask_sha256(reference.clean_mask)
            identity = dict(kind="custom-gallery", galleryId=gallery_id, libraryVersion=library.version,
                libraryManifestSha256=manifest_hash, routeSha256=route_hash,
                mapId=str(map_id), floor=str(floor), referencePath=str(reference.path),
                referenceBinding=deepcopy(binding), referenceMaskSha256=reference_hash,
                structureVersionKey=reference.metadata.get("structureVersionKey"))
            key = json.dumps(identity, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
            with self._lock:
                cached = self._layers.get(key)
            if cached is None:
                pixels = render_game_layer(library, str(map_id), str(floor), state["points"])
                if (pixels.dtype != np.uint8 or pixels.ndim != 3 or pixels.shape[2] != 4
                        or tuple(pixels.shape[:2]) != tuple(reference.shape)):
                    raise ValueError("custom-gallery-native-canvas-mismatch")
                pixels = np.ascontiguousarray(pixels)
                pixels.setflags(write=False)
                identity["displayPixelsSha256"] = hashlib.sha256(pixels.tobytes()).hexdigest()
                cached = dict(pixels=pixels, matrixDisplayToReference=np.eye(3), resourceIdentity=identity,
                    displayKind="custom-gallery", displayLabel=state["galleryName"],
                    routeProfileId=custom_profile_id(gallery_id), pixelFormat="BGRA")
            # A gallery save can finish while image rendering is in progress.
            # Such output is discarded even before the root revokes its epoch.
            current = self.service.display_snapshot(gallery_id, str(map_id))
            if (current is None or current["library"] is not library
                    or _route_hash(current["points"]) != route_hash
                    or hashlib.sha256((library.root / "manifest.json").read_bytes()).hexdigest() != manifest_hash):
                raise ValueError("custom-gallery-changed-during-render")
            library.validate_reference(map_id, str(floor), reference.clean_mask)
            with self._lock:
                self._layers[key] = cached
                while len(self._layers) > 6:
                    self._layers.pop(next(iter(self._layers)))
                self._last_error = ""
            # Metadata is copied; immutable native pixels are shared with the
            # bounded display cache and never handed to a recognition kernel.
            return {name: value if name == "pixels" else deepcopy(value) for name, value in cached.items()}
        except (OSError, ValueError, TypeError, KeyError, cv2.error, RouteError) as error:
            with self._lock:
                self._last_error = str(error)
            return None  # Same explicit structural/author fallback as legacy.
