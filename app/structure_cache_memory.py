"""Array-storage accounting for the two static alignment preparation caches.

Not an RSS estimate: Python metadata, allocator overhead, temporary solver work,
other holders and GPU allocations are outside this deliberately narrow budget.
"""
from __future__ import annotations

import numpy as np


REFERENCE_ARRAY_FIELDS = ('clean_mask', 'validation_mask', 'known_boundary', 'unknown_mask')


def reference_arrays(reference):
    return tuple(getattr(reference, name, None) for name in REFERENCE_ARRAY_FIELDS)


def array_storage(array):
    """Identify the retained backing allocation, not a small view's pixels.

    Handles ndarray views, NumPy stride-trick wrappers, and Python buffer
    owners. Our cache products use NumPy-owned arrays; unknown external owners
    are charged for the visible array, not claimed to be fully measured.
    """
    owner = array
    visited = set()
    while id(owner) not in visited:
        visited.add(id(owner))
        if isinstance(owner, np.ndarray):
            base = owner.base
        elif isinstance(owner, memoryview):
            base = owner.obj
        elif type(owner).__module__.startswith('numpy.lib.'):
            base = getattr(owner, 'base', None)  # stride_tricks.DummyArray
        else:
            break
        if base is None:
            break
        owner = base
    if isinstance(owner, np.ndarray):
        size = owner.nbytes
    else:
        try:
            size = memoryview(owner).nbytes
        except TypeError:
            size = array.nbytes
    return id(owner), int(size)


def retained_array_bytes(arrays):
    """Deduplicate backing storage across reference and prepared aliases."""
    allocations = {}
    for array in arrays:
        if isinstance(array, np.ndarray):
            key, size = array_storage(array)
            allocations[key] = max(size, allocations.get(key, 0))
    return sum(allocations.values())


def cache_array_bytes(cache, arrays_for):
    return retained_array_bytes(array for entry in cache.values() for array in arrays_for(entry))


def trim_prepared_cache(cache, *, max_entries, max_bytes, arrays_for):
    """Call under the existing owner lock, only after a cold insertion.

    Keep the newest entry even if it alone exceeds the budget: dropping it
    would repeat expensive preparation on every map-open. Thus this is a soft
    retained-array budget with an explicit singleton exception, not a hard cap.
    In-flight callers keep their ordinary references when an LRU entry expires.
    """
    while len(cache) > max_entries:
        cache.popitem(last=False)
    while len(cache) > 1 and cache_array_bytes(cache, arrays_for) > max_bytes:
        cache.popitem(last=False)


def prepared_cache_snapshot(cache, *, max_entries, max_bytes, arrays_for):
    """Read under owner lock. Contains counts only, never paths or map data."""
    size = cache_array_bytes(cache, arrays_for)
    return dict(entries=len(cache), retainedArrayBytes=size, maxEntries=max_entries,
                budgetBytes=max_bytes, oversizedSingleton=len(cache) == 1 and size > max_bytes,
                accounting='unique-backing-arrays; excludes-metadata-temporaries-other-holders')
