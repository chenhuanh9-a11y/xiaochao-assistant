"""Explicit game-client coordinate policies used by capture and recognition.

All rectangles here are local to the captured game client.  Screen origins,
window DPI and assistant UI scaling never alter these mappings.
"""

from __future__ import annotations

from dataclasses import dataclass
from math import ceil, floor
from typing import Literal, Mapping


ComponentPolicy = Literal[
    "safe-viewport",
    "full-client",
    "coordinate-adapter",
    "captured-client",
]

CLIENT_COMPONENT_POLICIES: Mapping[str, ComponentPolicy] = {
    "ui-roi": "safe-viewport",
    "floor-buttons": "safe-viewport",
    "map-canvas": "full-client",
    "exit-icons": "coordinate-adapter",
    "current-frame-pose": "captured-client",
    "route-render": "captured-client",
}


@dataclass(frozen=True, slots=True)
class ClientViewport:
    left: int
    top: int
    right: int
    bottom: int
    mode: str

    @property
    def width(self) -> int:
        return self.right - self.left

    @property
    def height(self) -> int:
        return self.bottom - self.top

    def as_tuple(self) -> tuple[int, int, int, int]:
        return self.left, self.top, self.right, self.bottom


def centered_safe_viewport(
    client_size: tuple[int, int],
    *,
    target_aspect: float = 16.0 / 9.0,
) -> ClientViewport:
    """Return the centered 16:9 HUD viewport inside any client aspect."""

    width, height = (max(1, int(value)) for value in client_size)
    aspect = width / height
    if abs(aspect - target_aspect) <= 0.01:
        return ClientViewport(0, 0, width, height, "full-client")
    if aspect < target_aspect:
        safe_height = min(height, max(1, round(width / target_aspect)))
        top = (height - safe_height) // 2
        return ClientViewport(0, top, width, top + safe_height, "centered-safe-height")
    safe_width = min(width, max(1, round(height * target_aspect)))
    left = (width - safe_width) // 2
    return ClientViewport(left, 0, left + safe_width, height, "centered-safe-width")


def normalized_rect_to_client(
    client_size: tuple[int, int],
    rect: tuple[float, float, float, float],
    *,
    policy: ComponentPolicy = "full-client",
) -> tuple[int, int, int, int]:
    """Map a normalized rectangle through an explicit component policy."""

    width, height = (max(1, int(value)) for value in client_size)
    x, y, rect_width, rect_height = (float(value) for value in rect)
    if not (
        0.0 <= x <= 1.0
        and 0.0 <= y <= 1.0
        and 0.0 < rect_width <= 1.0
        and 0.0 < rect_height <= 1.0
        and x + rect_width <= 1.000001
        and y + rect_height <= 1.000001
    ):
        raise ValueError("normalized client rectangle is invalid")
    if policy == "safe-viewport":
        viewport = centered_safe_viewport((width, height))
    else:
        viewport = ClientViewport(0, 0, width, height, "full-client")
    left = viewport.left + floor(viewport.width * x)
    top = viewport.top + floor(viewport.height * y)
    right = viewport.left + ceil(viewport.width * (x + rect_width))
    bottom = viewport.top + ceil(viewport.height * (y + rect_height))
    return (
        max(0, min(left, width - 1)),
        max(0, min(top, height - 1)),
        max(1, min(right, width)),
        max(1, min(bottom, height)),
    )


def client_geometry_signature(
    *,
    origin: tuple[int, int],
    size: tuple[int, int],
    dpi: int,
    window_mode: str,
    monitor_id: str,
    work_area: tuple[int, int, int, int],
    topology_signature: tuple,
) -> tuple:
    """Bind a captured client to its full physical display environment."""

    return (
        int(origin[0]), int(origin[1]), int(size[0]), int(size[1]),
        int(dpi), str(window_mode), str(monitor_id),
        *(int(value) for value in work_area),
        tuple(topology_signature),
    )


__all__ = [
    "CLIENT_COMPONENT_POLICIES",
    "ClientViewport",
    "centered_safe_viewport",
    "client_geometry_signature",
    "normalized_rect_to_client",
]
