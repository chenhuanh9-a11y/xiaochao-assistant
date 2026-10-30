"""Shared current-frame observation primitives; no UI or capture dependency."""
from __future__ import annotations

import cv2
import numpy as np
from nightmare_candidate import visible_map_component, map_color_mask


def map_reuse_evidence(
    frame: np.ndarray | None,
    *,
    maximum_width: int = 640,
    visible_override: tuple | None = None,
) -> dict:
    """Extract every reopen-reuse feature with one visible-map scan."""
    if frame is None:
        return {"component": None, "componentShape": None, "translationMask": None}
    visible = visible_override if visible_override is not None else visible_map_component(frame)
    if visible is None:
        return {"component": None, "componentShape": None, "translationMask": None}
    patch, bounds, component = visible
    geometry = {
        "bounds": tuple(bounds),
        "center": tuple(float(value) for value in component["center"]),
        "width": int(component["width"]),
        "height": int(component["height"]),
        "area": int(component["area"]),
    }
    live_mask = map_color_mask(patch, live=True).astype(np.uint8)
    mask = live_mask
    if int(mask.sum()) < 300:
        gray = cv2.cvtColor(patch, cv2.COLOR_BGR2GRAY)
        mask = ((gray >= 75) & (gray <= 220)).astype(np.uint8)
    shape = None
    if int(mask.sum()) >= 300:
        shape = cv2.resize(
            mask, (96, 96), interpolation=cv2.INTER_NEAREST,
        ).astype(np.uint8)
    translation_mask = None
    if int(live_mask.sum()) >= 300:
        height, width = frame.shape[:2]
        canvas = np.zeros((height, width), np.uint8)
        x0, y0, x1, y1 = (int(value) for value in bounds)
        insert_height = min(live_mask.shape[0], y1 - y0, height - y0)
        insert_width = min(live_mask.shape[1], x1 - x0, width - x0)
        if insert_height > 0 and insert_width > 0:
            canvas[y0:y0 + insert_height, x0:x0 + insert_width] = live_mask[
                :insert_height, :insert_width
            ]
            canvas = cv2.dilate(canvas, np.ones((3, 3), np.uint8))
            scale = min(1.0, float(maximum_width) / max(width, 1))
            if scale < 1.0:
                canvas = cv2.resize(
                    canvas,
                    (max(1, round(width * scale)), max(1, round(height * scale))),
                    interpolation=cv2.INTER_AREA,
                )
            translation_mask = ((canvas > 0.22).astype(np.float32), scale)
    return {
        "component": geometry,
        "componentShape": shape,
        "translationMask": translation_mask,
    }


def structure_current_map_evidence(frame: np.ndarray | None) -> dict | None:
    """Return existing pixels plus their actual, exclusive observation bounds.

    The consumer must treat the 3px crop-edge band as unknown, not a wall.
    Bounds are measured before any source/pose/residual is consulted. Keeping
    the original extraction avoids linking transient HUD glow into the map.
    """
    if frame is None:
        return None
    visible = visible_map_component(frame)
    if visible is None:
        return None
    current = map_reuse_evidence(frame, maximum_width=frame.shape[1],
        visible_override=visible)['translationMask']
    if current is None:
        return None
    return dict(query=(current[0] > .22).astype(np.uint8),
                observationBounds=tuple(visible[1]), visible=visible)


def map_translation_mask(
    frame: np.ndarray | None,
    *,
    maximum_width: int = 640,
) -> tuple[np.ndarray, float] | None:
    """Render only the visible map island on a small full-client canvas."""
    return map_reuse_evidence(
        frame, maximum_width=maximum_width,
    )["translationMask"]
