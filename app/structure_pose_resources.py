"""Static structure inputs owned by one synchronous pose solve.

Region attempts reuse decoded inputs. Before offering or returning their result,
the owning solve rechecks the actual providers and prepared resources. Nothing
survives into the next frame or grants pose, identity or display authority.
"""
from pathlib import Path

import numpy as np


def _same_array(first, second):
    return first is second or (first is not None and second is not None
                               and np.array_equal(first, second))


def _same_reference(first, second):
    if first is second:
        return True
    if first is None or second is None:
        return False
    for name in ('path', 'resource_root'):
        left, right = getattr(first, name, None), getattr(second, name, None)
        if ((left is None) != (right is None)
                or left is not None and Path(left).resolve() != Path(right).resolve()):
            return False
    return (getattr(first, 'metadata', None) == getattr(second, 'metadata', None)
            and getattr(first, 'native_wall_segments', None) == getattr(second, 'native_wall_segments', None)
            and all(_same_array(getattr(first, name, None), getattr(second, name, None))
                    for name in ('clean_mask', 'validation_mask', 'known_boundary', 'unknown_mask')))


class StructureFitResources:
    """One fit's resource snapshot, including its ordinary optional misses."""
    def __init__(self, engine):
        self.engine = engine
        self.source = getattr(engine, 'qt_fast_structure_source', None)
        self.references = {}
        self.preparations = {}
        self._assert_current()

    def _assert_current(self):
        check = getattr(self.engine, 'qt_assert_resource_current', None)
        if check is not None:
            check()
        if getattr(self.engine, 'qt_fast_structure_source', None) is not self.source:
            raise ValueError('structure-fit-resource-source-changed')

    def reference(self, profile, map_id, floor):
        key = (profile, str(map_id), str(floor))
        if key not in self.references:
            self.references[key] = self.engine.qt_shared_provider(profile, map_id, floor)
        return self.references[key]

    def prepared(self, reference, *, cancelled=None):
        key = id(reference)
        if key not in self.preparations:
            acquire = getattr(self.source, 'acquire_prepared_use', None)
            use = None
            if callable(acquire):
                use = acquire(reference, cancelled=cancelled)
                if use.reference is not reference or use.source is not self.source:
                    raise ValueError('structure-fit-preparation-source-changed')
                value = use.prepared
            else:
                value = self._load_prepared(reference, cancelled=cancelled)
            self.preparations[key] = (reference, value, use)
        return self.preparations[key][1]

    def _load_prepared(self, reference, *, cancelled=None):
        from fast_structure_alignment import load_prepared
        return (load_prepared(reference) if self.source is None else
                load_prepared(reference, source=self.source, cancelled=cancelled))

    def check_current(self):
        self._assert_current()
        reader = getattr(self.source, 'reader', None)
        for key, reference in self.references.items():
            current = self.engine.qt_shared_provider(*key)
            if not _same_reference(reference, current):
                raise ValueError('structure-fit-reference-changed')
            # Prepared-source reads below also validate their typed reference.
            # Other lanes still need that actual payload check: the provider
            # itself may legitimately return a decoded cache entry.
            if reference is not None and id(reference) not in self.preparations and reader is not None:
                reader(reference.path)
        for reference, prepared, use in self.preparations.values():
            if use is not None:
                use.check_current()
                continue
            current = self._load_prepared(reference)
            if prepared is not None and (current is None or current.shape != prepared.shape
                    or current.pitch != prepared.pitch
                    or any(not _same_array(getattr(current, name), getattr(prepared, name))
                           for name in ('distance', 'chains', 'rays'))):
                raise ValueError('structure-fit-preparation-changed')
        self._assert_current()
