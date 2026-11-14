"""Use the real client readers to qualify a producer's temporary package.

This never activates a catalog. Source-equivalent control vectors and derived
arrays are verified here before publication, rather than recomputed as a
hardware-dependent requirement for enabling a package on the user's machine.
"""
from contextlib import ExitStack
from pathlib import Path
import sys
from uuid import uuid4

import numpy as np


QUICK_ROOT = str(Path(__file__).resolve().parents[1] / 'experiments' / 'qt_quick')
if QUICK_ROOT not in sys.path:
    sys.path.insert(0, QUICK_ROOT)


def assert_source_equal(name, expected, actual, *, max_report=5):
    """Explain an exact comparison without changing the resource's dtype."""
    expected, actual = np.asarray(expected), np.asarray(actual)
    if expected.shape != actual.shape:
        raise ValueError(f'{name}: shape {expected.shape} != {actual.shape}')
    if expected.dtype != actual.dtype:
        raise ValueError(f'{name}: dtype {expected.dtype} != {actual.dtype}')
    if np.array_equal(expected, actual):
        return
    different = expected != actual
    rows = different.reshape((expected.shape[0], -1)) if expected.ndim else different.reshape(1, 1)
    parts = [f'{name}: differingElements={int(different.sum())}/{different.size}',
             f'affectedRows={int(rows.any(axis=1).sum())}/{len(rows)}']
    ulps = None
    if expected.dtype in (np.dtype('float32'), np.dtype('float64')):
        unsigned = np.dtype(f'uint{expected.dtype.itemsize * 8}')
        sign = np.array(1 << (expected.dtype.itemsize * 8 - 1), dtype=unsigned)

        def ordered(values):
            bits = values.view(unsigned)
            return np.where(bits & sign, ~bits, bits ^ sign)

        left, right = ordered(expected), ordered(actual)
        ulps = np.maximum(left, right) - np.minimum(left, right)
        parts.append(f'maxULP={int(ulps.max())}')
    if np.issubdtype(expected.dtype, np.number):
        with np.errstate(over='ignore', invalid='ignore'):
            absolute = np.abs(expected.astype(np.float64) - actual.astype(np.float64))
        parts.append(f'maxAbsDiff={float(absolute.max()):.17g}')
    for flat in np.flatnonzero(different)[:max_report]:
        index = tuple(int(value) for value in np.unravel_index(flat, expected.shape))
        position = '[' + ','.join(map(str, index)) + ']'
        detail = f'{position}: expected={expected[index]!r}, actual={actual[index]!r}'
        if ulps is not None:
            detail += f', ULP={int(ulps[index])}'
        parts.append(detail)
    raise ValueError('; '.join(parts))


def _prepared_difference(bundle, provider):
    from fast_structure_alignment import prepare
    source = bundle.fast_structure_source
    for profile, map_id, variant, floor in source.reference_keys:
        reference = provider(profile, map_id, floor)
        prepared = source.read(reference, strict=True)
        if prepared is None:
            continue
        actual = prepare(reference.clean_mask)
        key = (profile, map_id, variant, floor)
        path = 'data/base/' + source.entries[key].path
        for field in ('distance', 'chains', 'rays', 'pitch', 'shape'):
            assert_source_equal(path + ':' + field, getattr(actual, field), getattr(prepared, field))
    return ''


def validate_original_package(resource_root, component_refs, *, season, mode):
    """Read the saved bytes through the client's selection and resource chain."""
    from client_imported_providers import SUPPORTED_CAPABILITIES, load_native_resources
    from imported_resource_catalog import SCHEMA
    from imported_resource_selection import inspect_selection

    refs = {ref.component_id: ref for ref in component_refs}

    def wire(ref):
        return dict(packageId=ref.package_id,
            resourceVersion=ref.resource_version, componentId=ref.component_id,
            manifestSha256=ref.manifest_sha256)

    document = dict(schema=SCHEMA, generation=str(uuid4()), displays=[], contexts={
        f'{season}:{mode}': dict(recognitionBase=wire(refs['base']),
            controls=wire(refs['controls']), reviewedGroups=None)})
    inspected = inspect_selection(resource_root, document, supported_capabilities=SUPPORTED_CAPABILITIES)
    base_view, controls_view = (inspected.views[refs[name]] for name in ('base', 'controls'))
    bundle, provider = None, None
    with ExitStack() as stack:
        for view in inspected.views.values():
            stack.enter_context(view.read_scope())
        try:
            bundle, controls, native, provider, check_current = load_native_resources(
                base_view, controls_view, season=season, mode=mode)
            # load_native_resources has already qualified the control bank by
            # identity/labels/row cosine; do not compare the same bank twice.
            bundle.fast_structure_source.validate_all(provider, compare_derived=True)
            check_current()
        except ValueError as error:
            detail = ''
            try:
                if bundle is not None and str(error).startswith('original-qt-base-fast-derived-mismatch:'):
                    detail = _prepared_difference(bundle, provider)
            except (OSError, ValueError, TypeError) as diagnostic:
                detail = str(diagnostic)
            suffix = ': ' + detail if detail else ''
            raise ValueError(f'original-base-export-semantic-invalid:{season}/{mode}: {error}{suffix}') from error
    return dict(typedLayers=len(bundle.versions), preparedLayers=len(bundle.fast_structure_source.entries))
