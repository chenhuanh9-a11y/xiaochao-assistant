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


def explored_interior_mask(query, observation_bounds=None, *, margin_px=7, anchor=None):
    """Deeply-interior confirmed-walkable pixels, safely away from walls and fog borders.

    Fog can obscure a real room into darkness, so dark pixels are never negative
    evidence. Conversely, fog cannot turn solid wall/void into an already lit
    walkable room interior. Eroding ``query`` inward by ``margin_px`` excludes
    wall strokes, door seams, crop borders and fog fringes.
    """
    if query is None:
        return None
    binary = (np.asarray(query) > 0).astype(np.uint8)
    h, w = binary.shape[:2]
    if h < 16 or w < 16 or not np.any(binary):
        return np.zeros_like(binary, dtype=np.uint8)
    work = binary.copy()
    if observation_bounds is not None and len(observation_bounds) == 4:
        x0, y0, x1, y1 = (int(v) for v in observation_bounds)
        # Treat the 3px crop edge band as unknown, never confirmed interior.
        pad = 3
        work[:max(0, y0 + pad), :] = 0
        work[min(h, max(0, y1 - pad)):, :] = 0
        work[:, :max(0, x0 + pad)] = 0
        work[:, min(w, max(0, x1 - pad)):] = 0
    k = max(3, int(margin_px) * 2 + 1)
    kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (k, k))
    interior = cv2.erode(work, kernel)
    if anchor is not None and isinstance(anchor, dict) and anchor.get('center') is not None:
        cx, cy = (int(round(float(v))) for v in anchor['center'][:2])
        interior[max(0, cy - 20):min(h, cy + 21), max(0, cx - 20):min(w, cx + 21)] = 0
    return interior


def sample_interior_points(interior, limit=300, *, priority_mask=None, priority_limit=180):
    """Regularly sample (x, y) points on a 2D grid from the confirmed walkable interior."""
    if interior is None or not np.any(interior):
        return np.empty((0, 2), dtype=np.float64)
    chosen = []

    def _grid_sample(mask, cap):
        area = int(np.count_nonzero(mask))
        if area == 0:
            return None
        step = max(3, int(np.ceil(np.sqrt(area / max(1, cap)))))
        sub = mask[::step, ::step]
        ys, xs = np.nonzero(sub)
        if not len(xs):
            ys, xs = np.nonzero(mask)
            if len(xs) > cap:
                idx = np.linspace(0, len(xs) - 1, cap, dtype=int)
                xs, ys = xs[idx], ys[idx]
            return np.column_stack((xs, ys)).astype(np.float64)
        xs = xs * step
        ys = ys * step
        if len(xs) > cap:
            idx = np.linspace(0, len(xs) - 1, cap, dtype=int)
            xs, ys = xs[idx], ys[idx]
        return np.column_stack((xs, ys)).astype(np.float64)

    if priority_mask is not None and priority_mask.shape == interior.shape:
        prioritized = (interior > 0) & (priority_mask > 0)
        pts = _grid_sample(prioritized, priority_limit)
        if pts is not None and len(pts):
            chosen.append(pts)
    pts = _grid_sample(interior > 0, limit)
    if pts is not None and len(pts):
        chosen.append(pts)
    if not chosen:
        return np.empty((0, 2), dtype=np.float64)
    combined = np.vstack(chosen)
    _, uniq = np.unique(np.rint(combined).astype(int), axis=0, return_index=True)
    return combined[np.sort(uniq)]


def has_interior_contradiction(bad_points, *, min_points=8, min_span_px=14):
    """Return True when interior violation points form a coherent 2D spatial cluster."""
    if bad_points is None or len(bad_points) < min_points:
        return False
    spread = np.ptp(bad_points, axis=0)
    if float(np.max(spread)) < min_span_px:
        return False
    pts = np.rint(bad_points).astype(int)
    x0, y0 = pts.min(axis=0)
    x1, y1 = pts.max(axis=0)
    pad = 12
    local = np.zeros((int(y1 - y0) + pad * 2 + 1, int(x1 - x0) + pad * 2 + 1), dtype=np.uint8)
    local[pts[:, 1] - y0 + pad, pts[:, 0] - x0 + pad] = 1
    # 21x21 dilation bridges 2D grid interior samples up to ~20px apart while
    # keeping widely scattered noise disconnected.
    bridged = cv2.dilate(local, np.ones((21, 21), np.uint8))
    count, labels = cv2.connectedComponents(bridged, connectivity=8)
    for cid in range(1, count):
        ys, xs = np.nonzero((labels == cid) & (local > 0))
        if len(xs) >= min_points and max(int(xs.max() - xs.min()), int(ys.max() - ys.min())) >= min_span_px:
            return True
    return False
