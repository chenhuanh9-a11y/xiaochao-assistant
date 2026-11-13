"""Candidate-independent current-pixel structural measurements."""
import cv2
import numpy as np

from single_map_structure import current_part


def observed_wall_fragment_edges(frame, context):
    """Observed pixels supported by continuous contour fragments at any angle.

    Reuse the wall solver's twelve-sample minimum, without requiring a complete
    corner or an axis-aligned wall. Short icon strokes and broken fog edges are
    not enough for a negative structural measurement. This is independent of
    every map, pose and residual; it is not a general icon/fog classifier.
    Continuity counts uniform arc samples; raster rounding may repeat a pixel.
    """
    observed = current_part(frame, context, 'observed')
    nodes = current_part(frame, context, 'nodes')
    support = np.zeros_like(observed, dtype=np.uint8)
    for node in nodes:
        start, end = node['point'], nodes[node['next']]['point']
        delta = end-start
        length = np.linalg.norm(delta)
        if length < 12:
            continue
        points = np.rint(start+np.linspace(0, 1, int(np.ceil(length))+1)[:, None]*delta).astype(int)
        indexes = np.flatnonzero(observed[points[:, 1], points[:, 0]])
        for run in np.split(indexes, np.flatnonzero(np.diff(indexes) > 1)+1):
            if len(run) >= 12:
                measured = points[run]
                support[measured[:, 1], measured[:, 0]] = 1
    # The existing observed gradient is a three-pixel band. Retain its
    # measured neighbours, without turning unobserved pixels into evidence.
    return (cv2.dilate(support, np.ones((3, 3), np.uint8)) > 0) & observed
