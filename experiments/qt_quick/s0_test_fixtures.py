"""Synthetic S0 observations shared by tests; no capture or native game input."""
from floor_control_region import VERTICAL_SEARCH_AREA


def synthetic_read(mode="nightmare", selected=1):
    order = [2, 1] if mode == "difficult" else [2, 1, -1]
    points = [[.85, .30 + i * .1] for i in range(len(order))]
    return dict(selectedFloor=selected, controlAxis="vertical", observedOrder=order,
        centerPoints=points, tileBounds=[.83, points[order.index(selected)][1] - .045, .04, .09],
        controlSearchRegion=list(VERTICAL_SEARCH_AREA),
        labelEvidence=[dict(floor=f, negative=f < 0, digitAspect=.2 if abs(f) == 1 else .5)
                       for f in order])
