"""Qualify original observers by parameters and portable row-wise similarity."""
import numpy as np

_VECTOR_SIMILARITY_FLOOR = 1.0 - 1e-6


class OriginalControlsError(ValueError):
    def __init__(self, code, **details):
        self.code, self.details = code, details
        suffix = (f":row={details['worstRowIndex']},cos={details['worstCos']:.9f},label={details['label']}"
                  if 'worstCos' in details else '')
        super().__init__(code + suffix)


def _immutable_bytes_array(value):
    if type(value) is not np.ndarray:
        return False
    owner = value
    while type(owner) is np.ndarray:
        if owner.flags.writeable:
            return False
        owner = owner.base
    return isinstance(owner, bytes)


def require_original_controls(controls, *, season, mode):
    if controls is None:
        return
    controls.assert_current()
    context = controls.for_context(season, mode)
    from floor_contract import floor_contract
    original = floor_contract(mode, season=season)
    if context is None or context.contract != original:
        raise OriginalControlsError('original-controls-floor-context-mismatch')
    if season == 's0':
        result = _require_s0_source_equivalent(controls)
    else:
        result = _require_s1_source_equivalent(controls, context)
    controls.assert_current()
    return result


def _require_s0_source_equivalent(controls):
    from floor_control_region import VERTICAL_SEARCH_AREA, AUTO_VERTICAL_SEARCH_AREA
    from s0_floor_templates import _bank, BANK_ID, GENERATOR_VERSION, TEMPLATE_FAMILY

    # Legacy v1 packs have no identity declaration. They still undergo the same
    # label, shape, numeric and content proof against the shipped static asset.
    if getattr(controls, 'bank_id', None) not in (None, BANK_ID):
        raise OriginalControlsError('original-controls-bank-version-mismatch', bankId=controls.bank_id)
    if getattr(controls, 'generator_version', None) not in (None, GENERATOR_VERSION):
        raise OriginalControlsError('original-controls-generator-version-mismatch', bankId=BANK_ID)
    if tuple(controls.fixed_search_region) != tuple(VERTICAL_SEARCH_AREA):
        raise OriginalControlsError('original-controls-fixed-region-mismatch')
    if tuple(controls.expanded_search_region) != tuple(AUTO_VERTICAL_SEARCH_AREA):
        raise OriginalControlsError('original-controls-expanded-region-mismatch')
    if controls.templates.family != TEMPLATE_FAMILY:
        raise OriginalControlsError('original-controls-template-family-mismatch')
    vectors, labels = _bank()  # Fixed program data; never renders a font.
    if not np.array_equal(controls.templates.labels, labels):
        raise OriginalControlsError('original-controls-template-labels-mismatch')
    supplied = controls.templates.vectors
    immutable = _immutable_bytes_array(supplied)
    stamp = (supplied.shape, supplied.dtype.str, supplied.strides) if immutable else None
    cached = getattr(controls, '_original_vector_qualification', None)
    if (immutable and cached is not None and cached[0] is supplied
            and cached[1] is vectors and cached[2] == stamp):
        return dict(cached[3])
    candidate = np.asarray(controls.templates.vectors, dtype=np.float64)
    reference = np.asarray(vectors, dtype=np.float64)
    details = dict(shape=list(candidate.shape), bankId=BANK_ID, generatorVersion=GENERATOR_VERSION)
    if candidate.shape != reference.shape:
        raise OriginalControlsError('original-controls-template-vector-shape-mismatch', **details)
    norms = np.linalg.norm(candidate, axis=1) * np.linalg.norm(reference, axis=1)
    if not np.all(np.isfinite(norms)) or np.any(norms <= 0.0):
        raise OriginalControlsError('original-controls-template-vector-degenerate', **details)
    similarity = np.einsum('ij,ij->i', candidate, reference, optimize=False) / norms
    worst = int(np.argmin(similarity))
    details.update(worstRowIndex=worst, worstCos=float(similarity[worst]),
                   maxAbsDiff=float(np.max(np.abs(candidate - reference))), label=int(labels[worst]))
    if not np.isfinite(similarity[worst]) or similarity[worst] < _VECTOR_SIMILARITY_FLOOR:
        raise OriginalControlsError('original-controls-template-vector-mismatch', **details)
    if immutable:
        # PackageView's decoded buffers are backed by immutable bytes. Reuse
        # only this object's content proof; currentness/parameters/labels above
        # are still checked before every original pixel observation.
        controls._original_vector_qualification = (supplied, vectors, stamp, dict(details))
    return details


def _require_s1_source_equivalent(controls, context):
    from imported_s1_controls_profile import _bounds
    from s1_floor_observation import SEARCH_BOUNDS, EXPANDED_SEARCH_BOUNDS, ORDER
    from s1_map_ui_geometry import SEARCH, SIZES, MIN_CORRELATION

    if context.visual_order != tuple(ORDER):
        raise OriginalControlsError('original-controls-visual-order-mismatch')
    if not np.allclose(_bounds(controls.fixed_search_region), SEARCH_BOUNDS, rtol=0, atol=1e-12):
        raise OriginalControlsError('original-controls-fixed-region-mismatch')
    if not np.allclose(_bounds(controls.expanded_search_region), EXPANDED_SEARCH_BOUNDS, rtol=0, atol=1e-12):
        raise OriginalControlsError('original-controls-expanded-region-mismatch')
    if tuple(controls.map_ui.sizes) != tuple(SIZES):
        raise OriginalControlsError('original-controls-map-ui-sizes-mismatch')
    if dict(controls.map_ui.minimum_correlation) != MIN_CORRELATION:
        raise OriginalControlsError('original-controls-map-ui-correlation-mismatch')
    for key, value in SEARCH.items():
        if not np.allclose(controls.map_ui.search[key], value, rtol=0, atol=1e-12):
            raise OriginalControlsError('original-controls-map-ui-search-mismatch')
