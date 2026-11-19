"""Bounded native display materials; resource validation stays with the provider."""
from collections import OrderedDict
from copy import deepcopy
from dataclasses import dataclass, field
import weakref

import numpy as np

from shared_structure_reference import SharedStructureReference


def _immutable(array):
    """A bytes backing cannot be made writable by a downstream numpy consumer."""
    return np.frombuffer(array.tobytes(order='C'), dtype=array.dtype).reshape(array.shape)


_MASKS = ('clean_mask', 'validation_mask', 'known_boundary', 'unknown_mask')


def _array_state(reference):
    return tuple(None if (array := getattr(reference, name)) is None else
        (id(array), array.shape, array.dtype.str, array.strides, array.flags.writeable)
        for name in _MASKS)


@dataclass(frozen=True)
class _NativeDisplayReference(SharedStructureReference):
    _display_owner: object = field(default=None, init=False, repr=False, compare=False)
    _display_arrays: tuple = field(default=(), init=False, repr=False, compare=False)


def freeze_native_reference(reference, owner):
    """Called only after the provider's original real native decode/validation."""
    result = _NativeDisplayReference(**{
        name: None if (array := getattr(reference, name)) is None else _immutable(array)
        for name in _MASKS}, metadata=reference.metadata, path=reference.path,
        native_wall_segments=reference.native_wall_segments, resource_root=reference.resource_root)
    object.__setattr__(result, '_display_owner', weakref.ref(owner))
    object.__setattr__(result, '_display_arrays', _array_state(result))
    return result


def owns_native_arrays(reference, owner):
    return (type(reference) is _NativeDisplayReference
        and reference._display_owner is not None and reference._display_owner() is owner
        and reference._display_arrays == _array_state(reference))


class NativeStructureDisplayMaterials:
    """An LRU of two immutable color buffers, never a source-authorization cache."""
    def __init__(self, *, max_entries=3, max_bytes=192 * 1024 * 1024):
        self.max_entries, self.max_bytes = max_entries, max_bytes
        self.entries = OrderedDict()
        self.retained_bytes = 0

    def _remove(self, key):
        self.retained_bytes -= self.entries.pop(key)[-1]

    def discard(self, reference):
        key = id(reference)
        if key in self.entries:
            self._remove(key)

    def layer(self, reference, owner, scope, variant, raster):
        if not owns_native_arrays(reference, owner):
            self.discard(reference)
            return raster(reference)
        key = id(reference)
        metadata = deepcopy(reference.metadata)
        binding = (scope, str(reference.path), str(variant), metadata)
        entry = self.entries.get(key)
        if entry is not None and (entry[0]() is not reference or entry[1] != binding):
            self._remove(key)
            entry = None
        if entry is None:
            layer = raster(reference)
            bgra = _immutable(layer['pixels'])
            rgba = _immutable(np.ascontiguousarray(bgra[..., [2, 1, 0, 3]]))
            layer = dict(layer, pixels=bgra, rgbaPixels=rgba,
                matrixDisplayToReference=_immutable(layer['matrixDisplayToReference']))
            retained = bgra.nbytes + rgba.nbytes + layer['matrixDisplayToReference'].nbytes
            entry = (weakref.ref(reference), binding, layer, retained)
            if self.max_entries > 0 and retained <= self.max_bytes:
                self.entries[key] = entry
                self.retained_bytes += retained
        else:
            self.entries.move_to_end(key)
        while self.entries and (len(self.entries) > self.max_entries or self.retained_bytes > self.max_bytes):
            self._remove(next(iter(self.entries)))
        # Small mutable metadata is detached; only genuinely readonly buffers
        # are shared with consumers. No reference object escapes in the layer.
        return {name: value if name in ('pixels', 'rgbaPixels', 'matrixDisplayToReference') else deepcopy(value)
                for name, value in entry[2].items()}
