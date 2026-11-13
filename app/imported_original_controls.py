"""Qualify external control data for the unchanged original pixel observers."""
import numpy as np


def require_original_controls(controls, *, season, mode):
    if controls is None:
        return
    controls.assert_current()
    context = controls.for_context(season, mode)
    from floor_contract import floor_contract
    original = floor_contract(mode, season=season)
    if context is None or context.contract != original:
        raise ValueError('original-controls-floor-context-mismatch')
    if season == 's0':
        from floor_control_region import VERTICAL_SEARCH_AREA, AUTO_VERTICAL_SEARCH_AREA
        from s0_floor_templates import _bank, TEMPLATE_FAMILY
        vectors, labels = _bank()
        valid = (tuple(controls.fixed_search_region) == tuple(VERTICAL_SEARCH_AREA)
            and tuple(controls.expanded_search_region) == tuple(AUTO_VERTICAL_SEARCH_AREA)
            and controls.templates.family == TEMPLATE_FAMILY
            and np.array_equal(controls.templates.vectors, vectors)
            and np.array_equal(controls.templates.labels, labels))
    else:
        from imported_s1_controls_profile import _bounds
        from s1_floor_observation import SEARCH_BOUNDS, EXPANDED_SEARCH_BOUNDS, ORDER
        from s1_map_ui_geometry import SEARCH, SIZES, MIN_CORRELATION
        valid = (context.visual_order == tuple(ORDER)
            and np.allclose(_bounds(controls.fixed_search_region), SEARCH_BOUNDS, rtol=0, atol=1e-12)
            and np.allclose(_bounds(controls.expanded_search_region), EXPANDED_SEARCH_BOUNDS, rtol=0, atol=1e-12)
            and tuple(controls.map_ui.sizes) == tuple(SIZES)
            and dict(controls.map_ui.minimum_correlation) == MIN_CORRELATION
            and all(np.allclose(controls.map_ui.search[key], value, rtol=0, atol=1e-12)
                    for key,value in SEARCH.items()))
    if not valid:
        raise ValueError('original-controls-parameters-not-source-equivalent')
    controls.assert_current()
