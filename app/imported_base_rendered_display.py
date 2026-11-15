"""Display-only artwork owned by an original base; no identity or pose writer."""
from collections import OrderedDict
import threading

import cv2
import numpy as np

from imported_author_display import decode_base_rendered_display
from shared_structure_display import structure_display_identity
from s1_native_display import NativeDisplayResult


class ImportedBaseRenderedDisplay:
    def __init__(self, base, provider):
        self.base = base
        self.display = decode_base_rendered_display(base.reader.view, base, provider)
        self._cache, self._lock = OrderedDict(), threading.RLock()

    def resolve(self, map_id, floor, reference):
        package = self.display
        layer = package.layer(map_id, floor)
        if layer is None:
            return NativeDisplayResult(None, 'base-rendered-layer-not-installed')
        # Reuse the selected layer's real reference proof. No basename, map
        # name, canvas size or prior cached pixels can stand in for ownership.
        from author_display_validation import validate_display_reference
        if (reference.metadata.get('sourceKind') != 'game-rendered-shared-structure'
                or reference.metadata.get('binding', {}).get('mapId') != str(map_id)
                or reference.metadata.get('binding', {}).get('floor') != str(floor)):
            _reject('reference-context-mismatch')
        expected = dict(referenceResource=reference.path.resolve().relative_to(self.base.reader.view.root.resolve()).as_posix(),
            referenceShapeHW=list(reference.shape), coordinateSpace=reference.metadata['canvas']['coordinateSpace'],
            **{name:reference.metadata.get(name) for name in ('structureVersionKey','baseSnapshot','sourceVersion')})
        actual = dict(referenceResource=layer.reference.path, referenceShapeHW=list(layer.reference_shape_hw),
            coordinateSpace=layer.coordinate_space, structureVersionKey=layer.structure_version_key,
            baseSnapshot=layer.base_snapshot, sourceVersion=layer.source_version)
        validate_display_reference(actual, expected, reject=_reject)
        raw = package.image_bytes(map_id, floor)
        key = (package.component_ref, layer.image.path)
        with self._lock:
            pixels = self._cache.get(key)
        if pixels is None:
            pixels = cv2.imdecode(np.frombuffer(raw, np.uint8), cv2.IMREAD_UNCHANGED)
            if (pixels is None or pixels.dtype != np.uint8
                    or tuple(pixels.shape[:2]) != layer.display_shape_hw):
                _reject('decode-failed')
            if pixels.ndim == 2:
                pixels = cv2.cvtColor(pixels, cv2.COLOR_GRAY2BGRA)
            elif pixels.ndim == 3 and pixels.shape[2] == 3:
                pixels = cv2.cvtColor(pixels, cv2.COLOR_BGR2BGRA)
            if pixels.ndim != 3 or pixels.shape[2] != 4:
                _reject('channels-invalid')
            from native_structure_display_material import _immutable
            pixels = _immutable(pixels)
        package.image_bytes(map_id, floor)
        self.base.assert_current()
        with self._lock:
            self._cache[key] = pixels
            self._cache.move_to_end(key)
            while len(self._cache) > 3 or sum(p.nbytes for p in self._cache.values()) > 96 * 1024 * 1024:
                self._cache.popitem(last=False)
        return NativeDisplayResult(dict(pixels=pixels,
            matrixDisplayToReference=np.asarray(layer.matrix_display_to_reference, dtype=np.float64),
            resourceIdentity=dict(reference=structure_display_identity(reference),
                component=str(package.component_ref), image=layer.image.path),
            referencePath=str(package.image_path(map_id, floor)),
            displayKind='base-rendered', displayLabel='无路线渲染图', routeProfileId='shared-structure'))


def _reject(reason):
    raise ValueError('base-rendered-display-' + reason)
