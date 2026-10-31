"""Physical game-map pointer policy extracted unchanged from Tk 3438365b.

These hit tests confer neither identity nor pose authority. Both frontends use
the same client-pixel bounds and the existing optional calibrated boundary.
"""
import numpy as np


def normalize_drag_boundary(value) -> tuple[float, float, float, float] | None:
    """Validate one resolution-independent client-area interaction boundary."""
    if not isinstance(value, (list, tuple)) or len(value) != 4:
        return None
    try:
        normalized = tuple(float(item) for item in value)
    except (TypeError, ValueError):
        return None
    if not np.isfinite(normalized).all():
        return None
    left, top, right, bottom = normalized
    if not (
        0.0 <= left < right <= 1.0
        and 0.0 <= top < bottom <= 1.0
        and right - left >= 0.15
        and bottom - top >= 0.15
    ):
        return None
    return left, top, right, bottom


def drag_boundary_client_rect(
    client_bounds,
    normalized_boundary,
) -> tuple[float, float, float, float] | None:
    """Project a normalized boundary into the current game client rectangle."""
    boundary = normalize_drag_boundary(normalized_boundary)
    if boundary is None or not client_bounds:
        return None
    left, top, right, bottom = (int(value) for value in client_bounds)
    width, height = right - left, bottom - top
    if width < 100 or height < 100:
        return None
    x1, y1, x2, y2 = boundary
    return (
        left + width * x1,
        top + height * y1,
        left + width * x2,
        top + height * y2,
    )


def cursor_can_drag_visible_map(
    cursor: tuple[int, int] | None,
    client_bounds,
    component: dict | None,
    normalized_boundary=None,
) -> bool:
    """Allow drag mirroring anywhere on the game map canvas, not just walls.

    A newly entered floor can be almost entirely fogged, and players commonly
    begin a drag in that empty area. Requiring the press to fall inside the
    currently explored component silently disabled the exact cross-floor drag
    case the layer group is meant to support. Fixed HUD/control strips remain
    excluded and a zero-motion click is discarded at mouse-up.
    """
    if cursor is None or not client_bounds:
        return False
    left, top, right, bottom = (int(value) for value in client_bounds)
    width, height = right - left, bottom - top
    if width < 100 or height < 100:
        return False
    calibrated_rect = drag_boundary_client_rect(
        client_bounds, normalized_boundary,
    )
    if calibrated_rect is not None:
        x1, y1, x2, y2 = calibrated_rect
        return bool(x1 <= cursor[0] < x2 and y1 <= cursor[1] < y2)
    client_x, client_y = cursor[0] - left, cursor[1] - top
    fixed_control = (
        client_y <= height * 0.13
        or client_x <= width * 0.08
        or client_x >= width * 0.94
        or client_y >= height * 0.94
    )
    inside_client = 0 <= client_x < width and 0 <= client_y < height
    return bool(inside_client and not fixed_control)


def cursor_is_map_close_control(
    cursor: tuple[int, int] | None,
    client_bounds,
) -> bool:
    """Recognize a physical click on the map's fixed top-right close area."""
    if cursor is None or not client_bounds:
        return False
    left, top, right, bottom = (int(value) for value in client_bounds)
    width, height = right - left, bottom - top
    if width < 100 or height < 100:
        return False
    client_x, client_y = cursor[0] - left, cursor[1] - top
    return bool(
        width * 0.90 <= client_x < width
        and 0 <= client_y <= height * 0.22
    )


def calibrated_drag_motion_policy(
    cursor: tuple[int, int] | None,
    client_bounds,
    normalized_boundary,
    previously_allowed: bool,
) -> tuple[bool, bool]:
    """Return (record_delta, current_allowed) for one held-mouse sample.

    With no calibration this deliberately returns the legacy behavior: every
    held movement is recorded after an eligible press. With calibration,
    leaving the rectangle pauses prediction and the first sample after re-entry
    only re-arms the origin, preventing a large catch-up jump.
    """
    boundary = normalize_drag_boundary(normalized_boundary)
    if boundary is None:
        return True, True
    current_allowed = cursor_can_drag_visible_map(
        cursor, client_bounds, None, boundary,
    )
    return bool(previously_allowed and current_allowed), current_allowed
