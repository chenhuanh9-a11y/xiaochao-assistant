"""Shared current-frame UI proof; no floor, identity or display authority.

Each exposure must supply all anchors and backdrop together. Shape evidence
always uses the raw, disjoint pixels and the existing X, +/-, and rail checks.
"""
from map_interface_observation import MAP_REGIONS, backdrop_ratio, neutral_ratio
from s1_map_ui_geometry import observe_s1_map_ui_geometry


ANCHORS = ('closeButton', 'zoomControl', 'bottomControls')


def observe_independent_map_ui(frame, observation, *, excluded_bounds, floor_search_right,
                                parameters=None, exposures=(1.,), tinted=False,
                                minimum_height=600, masked_backdrop=False):
    if observation.get('shapeAccepted') is not True:
        return dict(verified=False, reason='map-ui-client-shape-unverified')
    required = observation.get('requiredBackdropRatio')
    if required is None or not masked_backdrop and observation.get('backdropRatio', 0.) < required:
        return dict(verified=False, reason='map-ui-backdrop-unverified')
    if exposures == (1.,) and not tinted:
        # Removing pixels cannot increase these unexposed neutral ratios.
        ratios = observation.get('ratios', {})
        if any(ratios.get(name, 0.) < MAP_REGIONS[name][4] for name in ANCHORS):
            return dict(verified=False, reason='map-ui-anchors-unverified')
    for exposure in exposures:
        ratios = {name: neutral_ratio(frame, MAP_REGIONS[name], tinted=tinted,
            excluded_bounds=excluded_bounds, exposure=exposure) for name in ANCHORS}
        backdrop = (backdrop_ratio(frame, excluded_bounds=excluded_bounds, exposure=exposure)
                    if masked_backdrop else observation['backdropRatio'])
        if all(ratios[name] >= MAP_REGIONS[name][4] for name in ANCHORS) and backdrop >= required:
            break
    else:
        return dict(verified=False, reason='map-ui-anchors-unverified')
    geometry = observe_s1_map_ui_geometry(frame, floor_search_right=floor_search_right,
        minimum_height=minimum_height, parameters=parameters, excluded_bounds=excluded_bounds)
    return dict(verified=geometry.get('verified') is True, reason=geometry['reason'],
                anchorRatios=ratios, backdropRatio=backdrop, exposure=exposure, geometry=geometry)
