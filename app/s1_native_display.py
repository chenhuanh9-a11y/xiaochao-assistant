"""S1 rendered pixels bound to the exact unprojected typed reference.

Display only: this loader never captures, ranks, fits, or authorizes a pose.
The exact typed-product image hash or an inventoried producer registration,
not a matching filename/size, binds the bitmap to native reference coordinates.
"""
from collections import OrderedDict
from copy import deepcopy
from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
import struct
import threading

import cv2
import numpy as np


@dataclass(frozen=True)
class NativeDisplayResult:
    layer: dict | None
    reason: str = ""


REGISTRATION = "references/native-display-bindings.json"
PRODUCER_CONTRACT = "room-render-native-plus-doorframe-playerstart-v1"


class S1NativeDisplay:
    def __init__(self, context, *, capacity=6, max_bytes=96 * 1024 * 1024):
        self.context = context
        self.capacity = max(1, min(int(capacity), 6))
        self.max_bytes = max(1, min(int(max_bytes), 96 * 1024 * 1024))
        self._cache = OrderedDict()
        self._lock = threading.RLock()
        self._inventory = {row["path"]: dict(row) for row in context.manifest["files"]}
        self._registrations = None

    @staticmethod
    def _copy_layer(layer):
        return dict(layer, resourceIdentity=deepcopy(layer["resourceIdentity"]))

    def resolve(self, map_id, floor, reference):
        try:
            return self._resolve(str(map_id), str(floor), reference)
        except (OSError, ValueError, TypeError, KeyError, AttributeError, struct.error, cv2.error):
            # Never reuse a cached bitmap when its current binding/file fails.
            return NativeDisplayResult(None, "s1-native-display-invalid-binding-or-image")

    def _registered_overlay(self, map_id, floor, reference_path, binding, version,
                            canvas, renderer_digest, relative, display_digest):
        """An installed overlay product must declare its original image bridge.

        The final display may add door frames/PlayerStart on the same canvas.
        Its hash must never overwrite the typed sensor's room-render hash.
        """
        row = self._inventory.get(REGISTRATION)
        if row is None:
            raise ValueError("native overlay registration unavailable")
        path = self.context.local(REGISTRATION)
        if (path not in self.context._declared_files or type(row.get("bytes")) is not int
                or not 0 < row["bytes"] <= 2 * 1024 * 1024):
            raise ValueError("invalid native overlay registration inventory")
        with path.open("rb") as stream:
            data = stream.read(row["bytes"] + 1)
        digest = hashlib.sha256(data).hexdigest()
        if len(data) != row["bytes"] or digest != row["sha256"]:
            raise ValueError("native overlay registration changed")
        with self._lock:
            saved = self._registrations
        if saved is None or saved[0] != digest:
            document = json.loads(data)
            if (document.get("schema") != "xingtu-s1-native-display-bindings-v1"
                    or document.get("season") != "s1" or document.get("mode") != self.context.mode
                    or document.get("sourceVersion") != self.context.manifest["sourceVersion"]
                    or document.get("producerContract") != PRODUCER_CONTRACT
                    or document.get("coordinateChanges") != []):
                raise ValueError("native overlay registration scope mismatch")
            entries = {}
            for record in document["entries"]:
                key = (record["binding"]["mapId"], record["binding"]["floor"])
                if key in entries:
                    raise ValueError("duplicate native display binding")
                entries[key] = record
            saved = (digest, entries)
            with self._lock:
                self._registrations = saved
        record = saved[1].get((map_id, floor))
        ref_relative = reference_path.relative_to(self.context.root).as_posix()
        reference_row = self._inventory.get(ref_relative, {})
        if (record is None or record.get("binding") != binding or record.get("canvas") != canvas
                or record.get("referenceResource") != ref_relative
                or record.get("referenceSha256") != reference_row.get("sha256")
                or not reference_row.get("sha256")
                or record.get("rendererImageSha256") != renderer_digest
                or record.get("displayResource") != relative or record.get("displaySha256") != display_digest
                or any(record.get(name) != value for name, value in version.items())
                or record.get("matrixDisplayToReference") != np.eye(3).tolist()):
            raise ValueError("native overlay image/reference binding mismatch")
        return digest

    def _resolve(self, map_id, floor, reference):
        from s1_native_resources import MODE_LAYOUTS, S1_FLOORS
        ctx = self.context
        if map_id not in MODE_LAYOUTS[ctx.mode] or floor not in S1_FLOORS:
            raise ValueError("display context mismatch")
        binding = dict(profile="shared-structure", mapId=map_id, floor=floor, variant="variant-01")
        key = ("shared-structure", map_id, "variant-01", floor)
        entry = ctx.resources.entries.get(key)
        if entry is None or reference is None:
            raise ValueError("typed reference unavailable")
        reference_path = ctx.local(entry["resourcePath"])
        if reference_path != Path(reference.path).resolve() or reference_path not in ctx._declared_files:
            raise ValueError("typed reference path mismatch")
        meta = reference.metadata
        if "calibration" in meta:
            raise ValueError("calibrated references require a separate display bridge")
        version = {name: entry.get(name) for name in ("sourceVersion", "baseSnapshot", "structureVersionKey")}
        if (meta.get("sourceKind") != "game-rendered-shared-structure"
                or meta.get("binding") != binding
                or not all(isinstance(v, str) and v for v in version.values())
                or any(meta.get(name) != value for name, value in version.items())
                or version["sourceVersion"] != ctx.manifest["sourceVersion"]):
            raise ValueError("typed reference version mismatch")
        shape = tuple(reference.shape)
        if (len(shape) != 2 or any(type(n) is not int or n <= 0 for n in shape)
                or shape[0] * shape[1] * 4 > self.max_bytes):
            return NativeDisplayResult(None, "s1-native-display-byte-limit")
        height, width = shape
        canvas = meta.get("canvas", {})
        if (canvas.get("coordinateSpace") != f"game-native-pixels:{map_id}:{floor}"
                or canvas.get("layout") != map_id or canvas.get("floor") != floor
                or canvas.get("axes") != "x-right,y-down"
                or canvas.get("shapeHW") != [height, width]
                or canvas.get("width") != width or canvas.get("height") != height
                or canvas.get("worldFloor") != 5 + int(floor)):
            raise ValueError("typed native canvas mismatch")
        geometry = meta.get("sourceGeometry", {})
        digest = geometry.get("rendererImageSha256")
        if (geometry.get("projectionApplied") is not False
                or geometry.get("nativeShapeHW") != [height, width]
                or geometry.get("gameFloor") != int(floor)
                or geometry.get("sourceHeight") != canvas["worldFloor"]
                or not isinstance(digest, str) or len(digest) != 64
                or any(c not in "0123456789abcdef" for c in digest)):
            raise ValueError("unprojected renderer binding unavailable")
        declaration = ctx.adapters.maps[map_id]["floors"][floor]
        if (declaration.get("coordinateSpace") != canvas["coordinateSpace"]
                or declaration.get("coordinateWidth") != width or declaration.get("coordinateHeight") != height
                or declaration.get("sourceLayout") != map_id
                or declaration.get("worldFloor") != canvas["worldFloor"]
                or declaration.get("referenceResource") != entry["resourcePath"]
                or any(declaration.get(name) != value for name, value in version.items())):
            raise ValueError("coordinate declaration mismatch")
        canonical = np.asarray(declaration.get("matrixCanonicalToLegacyRecognition"), dtype=np.float64)
        shared = ctx.adapters.matrix_profile_to_canonical("shared-structure", map_id, floor)
        if (canonical.shape != (3, 3) or not np.array_equal(canonical, np.eye(3))
                or shared is None or not np.array_equal(np.asarray(shared), np.eye(3))):
            raise ValueError("unexpected native reference projection")
        relative = f"assets/game-native/{map_id}/{floor}.png"
        row = self._inventory.get(relative)
        if row is None:
            return NativeDisplayResult(None, "s1-native-display-not-installed")
        path = ctx.local(relative)
        display_digest = row.get("sha256")
        registration_digest = ""
        if display_digest != digest:
            registration_digest = self._registered_overlay(map_id, floor, reference_path, binding, version,
                canvas, digest, relative, display_digest)
        if (path not in ctx._declared_files
                or type(row.get("bytes")) is not int or not 0 < row["bytes"] <= 32 * 1024 * 1024):
            raise ValueError("renderer image inventory mismatch")
        # Hash and decode the same bounded bytes. Revalidation also precedes
        # cache hits, so a same-path replacement cannot retain stale pixels.
        with path.open("rb") as stream:
            data = stream.read(row["bytes"] + 1)
        if (len(data) != row["bytes"] or hashlib.sha256(data).hexdigest() != display_digest
                or data[:8] != b"\x89PNG\r\n\x1a\n" or data[12:16] != b"IHDR"
                or struct.unpack(">II", data[16:24]) != (width, height)):
            raise ValueError("renderer bytes or dimensions mismatch")
        if registration_digest and geometry.get("alphaRule") != "alpha>0":
            raise ValueError("registered overlay native alpha rule mismatch")
        footprint_digest = hashlib.sha256(reference.clean_mask.tobytes()).hexdigest() if registration_digest else ""
        cache_key = (map_id, floor, display_digest, registration_digest, footprint_digest, str(reference_path),
                     json.dumps(version, sort_keys=True), json.dumps(canvas, sort_keys=True))
        with self._lock:
            cached = self._cache.get(cache_key)
            if cached is not None:
                self._cache.move_to_end(cache_key)
                return NativeDisplayResult(self._copy_layer(cached))
        pixels = cv2.imdecode(np.frombuffer(data, np.uint8), cv2.IMREAD_UNCHANGED)
        if (pixels is None or pixels.dtype != np.uint8 or pixels.ndim != 3
                or pixels.shape[:2] != shape or pixels.shape[2] not in (3, 4)):
            raise ValueError("invalid rendered pixels")
        if pixels.shape[2] == 3:
            pixels = cv2.cvtColor(pixels, cv2.COLOR_BGR2BGRA)
        if not np.any(pixels[:, :, 3]):
            raise ValueError("empty rendered pixels")
        if registration_digest:
            # Corroborate the registered no-coordinate-change producer contract.
            # This is not sufficient to authorize an unregistered RGB bitmap.
            alpha = pixels[:, :, 3] > 0
            if (geometry.get("alphaRule") != "alpha>0"
                    or not np.array_equal(alpha, reference.clean_mask != 0)):
                raise ValueError("registered overlay native footprint mismatch")
        pixels = np.ascontiguousarray(pixels)
        pixels.setflags(write=False)
        bridge = np.eye(3)
        bridge.setflags(write=False)
        layer = dict(pixels=pixels, matrixDisplayToReference=bridge,
            resourceIdentity=dict(path=str(path), sha256=display_digest, binding=binding,
                rendererImageSha256=digest, displayRegistrationSha256=registration_digest, **version),
            referencePath=str(path), displayKind="game-native", displayLabel="S1 原生渲染地图",
            routeProfileId="shared-structure")
        with self._lock:
            self._cache[cache_key] = layer
            self._cache.move_to_end(cache_key)
            while (len(self._cache) > self.capacity
                    or sum(item["pixels"].nbytes for item in self._cache.values()) > self.max_bytes):
                self._cache.popitem(last=False)
        return NativeDisplayResult(self._copy_layer(layer))
