"""Pure Windows UI sizing and virtual-desktop layout primitives.

Logical UI measurements enter this module as device-independent pixels (DIP).
``RectPx`` values are always physical pixels in the Windows virtual-desktop
coordinate space, whose origin may leave monitors at negative X or Y.

This coordinate space is deliberately separate from the game client/capture
space.  Game display contexts, screenshots, route matrices, and alignment
coordinates must not be passed through these helpers or scaled with UI DPI.
"""

from __future__ import annotations

from dataclasses import dataclass
from math import ceil, floor, isfinite
from typing import Literal, Mapping, Sequence


BASE_DPI = 96
WINDOW_ANCHOR_SCHEMA = "native-window-anchor-v1"

WindowEdge = Literal["left", "right", "top", "bottom"]
SurfaceVariant = Literal["regular", "compact"]


SURFACE_SIZE_DIP: Mapping[str, tuple[float, float]] = {
    "orb": (64, 64),
    "quick": (336, 280),
    "quick-round": (336, 326),
    "recognition": (440, 540),
    "settings": (620, 540),
    "onboarding": (650, 690),
    "drag-boundary-prompt": (540, 330),
    "exit-confirmation": (440, 220),
    "status-pill": (260, 36),
}


@dataclass(frozen=True, slots=True)
class RectPx:
    """A non-empty rectangle in Windows virtual-desktop physical pixels."""

    left: int
    top: int
    right: int
    bottom: int

    def __post_init__(self) -> None:
        if any(
            not isinstance(value, int) or isinstance(value, bool)
            for value in (self.left, self.top, self.right, self.bottom)
        ):
            raise TypeError("RectPx coordinates must be integers")
        if self.right <= self.left or self.bottom <= self.top:
            raise ValueError("RectPx must have positive width and height")

    @property
    def width(self) -> int:
        return self.right - self.left

    @property
    def height(self) -> int:
        return self.bottom - self.top

    @property
    def center(self) -> tuple[float, float]:
        return (
            self.left + self.width / 2.0,
            self.top + self.height / 2.0,
        )

    def contains(self, other: RectPx) -> bool:
        return (
            self.left <= other.left
            and self.top <= other.top
            and self.right >= other.right
            and self.bottom >= other.bottom
        )

    def intersection_area(self, other: RectPx) -> int:
        width = max(0, min(self.right, other.right) - max(self.left, other.left))
        height = max(0, min(self.bottom, other.bottom) - max(self.top, other.top))
        return width * height

    def as_tuple(self) -> tuple[int, int, int, int]:
        return self.left, self.top, self.right, self.bottom


@dataclass(frozen=True, slots=True)
class MonitorContext:
    """Stable monitor identity plus current bounds, work area, and UI DPI."""

    monitor_id: str
    bounds_px: RectPx
    work_area_px: RectPx
    dpi: int = BASE_DPI
    is_primary: bool = False

    def __post_init__(self) -> None:
        if not self.monitor_id:
            raise ValueError("monitor_id must not be empty")
        if (
            not isinstance(self.dpi, int)
            or isinstance(self.dpi, bool)
            or self.dpi <= 0
        ):
            raise ValueError("monitor DPI must be positive")
        if not self.bounds_px.contains(self.work_area_px):
            raise ValueError("monitor work area must be inside monitor bounds")


@dataclass(frozen=True, slots=True)
class UiDpiContext:
    """DPI for one assistant window, never for game capture coordinates."""

    monitor: MonitorContext
    dpi: int

    def __post_init__(self) -> None:
        if (
            not isinstance(self.dpi, int)
            or isinstance(self.dpi, bool)
            or self.dpi <= 0
        ):
            raise ValueError("window DPI must be positive")

    @classmethod
    def from_monitor(cls, monitor: MonitorContext) -> UiDpiContext:
        return cls(monitor=monitor, dpi=monitor.dpi)

    @property
    def scale(self) -> float:
        return self.dpi / BASE_DPI


@dataclass(frozen=True, slots=True)
class FontTokensPx:
    caption: int
    body: int
    section: int
    title: int
    display: int


@dataclass(frozen=True, slots=True)
class SpacingTokensPx:
    xs: int
    sm: int
    md: int
    lg: int
    xl: int


@dataclass(frozen=True, slots=True)
class ControlTokensPx:
    compact_min_height: int
    default_min_height: int
    hit_target_min: int


@dataclass(frozen=True, slots=True)
class RadiusTokensPx:
    sm: int
    md: int
    lg: int


@dataclass(frozen=True, slots=True)
class StrokeTokensPx:
    hairline: int
    strong: int
    focus: int


@dataclass(frozen=True, slots=True)
class UiTokensPx:
    """Semantic native-UI tokens resolved for one window DPI."""

    dpi: int
    font: FontTokensPx
    spacing: SpacingTokensPx
    control: ControlTokensPx
    radius: RadiusTokensPx
    stroke: StrokeTokensPx


@dataclass(frozen=True, slots=True)
class WindowAnchor:
    """Portable placement state independent of raw desktop coordinates."""

    monitor_id: str
    edge: WindowEdge
    ratio: float
    saved_work_area_px: RectPx
    saved_dpi: int

    def __post_init__(self) -> None:
        if not self.monitor_id:
            raise ValueError("monitor_id must not be empty")
        if self.edge not in {"left", "right", "top", "bottom"}:
            raise ValueError("unsupported anchor edge")
        if not isfinite(self.ratio) or not 0.0 <= self.ratio <= 1.0:
            raise ValueError("anchor ratio must be between zero and one")
        if (
            not isinstance(self.saved_dpi, int)
            or isinstance(self.saved_dpi, bool)
            or self.saved_dpi <= 0
        ):
            raise ValueError("saved DPI must be positive")

    def to_dict(self) -> dict[str, object]:
        return {
            "schema": WINDOW_ANCHOR_SCHEMA,
            "monitorId": self.monitor_id,
            "edge": self.edge,
            "ratio": self.ratio,
            "savedWorkRectPx": list(self.saved_work_area_px.as_tuple()),
            "savedDpi": self.saved_dpi,
        }

    @classmethod
    def from_dict(cls, raw: Mapping[str, object]) -> WindowAnchor | None:
        """Parse saved placement fail-closed; invalid state falls back to default."""

        try:
            if raw.get("schema") != WINDOW_ANCHOR_SCHEMA:
                return None
            rect = raw.get("savedWorkRectPx")
            monitor_id = raw.get("monitorId")
            edge = raw.get("edge")
            ratio = raw.get("ratio")
            saved_dpi = raw.get("savedDpi")
            if (
                not isinstance(rect, (list, tuple))
                or len(rect) != 4
                or any(
                    not isinstance(value, int) or isinstance(value, bool)
                    for value in rect
                )
                or not isinstance(monitor_id, str)
                or not monitor_id
                or edge not in {"left", "right", "top", "bottom"}
                or not isinstance(ratio, (int, float))
                or isinstance(ratio, bool)
                or not isinstance(saved_dpi, int)
                or isinstance(saved_dpi, bool)
            ):
                return None
            return cls(
                monitor_id=monitor_id,
                edge=edge,  # type: ignore[arg-type]
                ratio=float(ratio),
                saved_work_area_px=RectPx(*rect),
                saved_dpi=saved_dpi,
            )
        except (KeyError, TypeError, ValueError):
            return None


@dataclass(frozen=True, slots=True)
class SurfaceLayout:
    """One fitted assistant surface and its responsive overflow decisions."""

    surface_id: str
    ui_context: UiDpiContext
    rect_px: RectPx
    available_rect_px: RectPx
    requested_size_px: tuple[int, int]
    variant: SurfaceVariant
    needs_vertical_scroll: bool

    def __post_init__(self) -> None:
        if not self.surface_id:
            raise ValueError("surface_id must not be empty")
        if not self.available_rect_px.contains(self.rect_px):
            raise ValueError("surface must be inside its available work area")
        if self.requested_size_px[0] <= 0 or self.requested_size_px[1] <= 0:
            raise ValueError("requested surface size must be positive")

    @property
    def needs_width_reflow(self) -> bool:
        return self.rect_px.width < self.requested_size_px[0]

    @property
    def tk_geometry(self) -> str:
        return format_tk_geometry(self.rect_px)


def dip_to_px(value: float, dpi: int = BASE_DPI) -> int:
    """Convert DIP to pixels using symmetric round-half-away-from-zero."""

    if not isinstance(dpi, int) or isinstance(dpi, bool) or dpi <= 0:
        raise ValueError("DPI must be positive")
    if not isfinite(float(value)):
        raise ValueError("DIP value must be finite")
    scaled = float(value) * dpi / BASE_DPI
    return floor(scaled + 0.5) if scaled >= 0 else ceil(scaled - 0.5)


def ui_tokens(context: UiDpiContext) -> UiTokensPx:
    """Resolve the incumbent native visual hierarchy at the current UI DPI."""

    def px(value: float) -> int:
        return dip_to_px(value, context.dpi)

    return UiTokensPx(
        dpi=context.dpi,
        font=FontTokensPx(
            caption=px(8),
            body=px(9),
            section=px(10),
            title=px(14),
            display=px(16),
        ),
        spacing=SpacingTokensPx(
            xs=px(4),
            sm=px(8),
            md=px(12),
            lg=px(16),
            xl=px(24),
        ),
        control=ControlTokensPx(
            compact_min_height=px(28),
            default_min_height=px(32),
            hit_target_min=px(40),
        ),
        radius=RadiusTokensPx(sm=px(6), md=px(12), lg=px(20)),
        stroke=StrokeTokensPx(
            hairline=max(1, px(1)),
            strong=max(1, px(2)),
            focus=max(1, px(2)),
        ),
    )


def format_tk_geometry(rect: RectPx) -> str:
    """Format absolute virtual-desktop coordinates for Windows Tk.

    Tk normally interprets ``-120`` as an offset from the right/bottom edge.
    Prefixing every absolute coordinate with ``+`` intentionally yields forms
    such as ``+-120`` for a monitor left of or above the primary display.
    """

    return f"{rect.width}x{rect.height}+{rect.left}+{rect.top}"


def _available_work_area(work_area: RectPx, margin_px: int) -> RectPx:
    if margin_px < 0:
        raise ValueError("margin must not be negative")
    horizontal = min(margin_px, (work_area.width - 1) // 2)
    vertical = min(margin_px, (work_area.height - 1) // 2)
    return RectPx(
        work_area.left + horizontal,
        work_area.top + vertical,
        work_area.right - horizontal,
        work_area.bottom - vertical,
    )


def clamp_rect_to_work_area(
    rect: RectPx,
    work_area: RectPx,
    *,
    margin_px: int = 0,
) -> RectPx:
    """Keep a surface fully visible, shrinking only dimensions that cannot fit."""

    available = _available_work_area(work_area, margin_px)
    width = min(rect.width, available.width)
    height = min(rect.height, available.height)
    left = min(max(rect.left, available.left), available.right - width)
    top = min(max(rect.top, available.top), available.bottom - height)
    return RectPx(left, top, left + width, top + height)


def select_monitor_for_rect(
    rect: RectPx,
    monitors: Sequence[MonitorContext],
) -> MonitorContext:
    """Select by overlap, then proximity, with primary/deterministic tie breaks."""

    if not monitors:
        raise ValueError("at least one monitor is required")
    rect_x, rect_y = rect.center

    def score(item: tuple[int, MonitorContext]) -> tuple[int, float, int, int]:
        index, monitor = item
        monitor_x, monitor_y = monitor.bounds_px.center
        distance_squared = (rect_x - monitor_x) ** 2 + (rect_y - monitor_y) ** 2
        return (
            rect.intersection_area(monitor.bounds_px),
            -distance_squared,
            int(monitor.is_primary),
            -index,
        )

    return max(enumerate(monitors), key=score)[1]


def monitor_topology_signature(
    monitors: Sequence[MonitorContext],
) -> tuple[tuple[object, ...], ...]:
    """Return a deterministic signature for every physical monitor.

    Identity, full bounds, work area, DPI and primary ownership all matter.
    In particular, changing only the taskbar work area or removing a monitor
    must invalidate prior placement/capture authority.
    """

    return tuple(sorted(
        (
            monitor.monitor_id,
            *monitor.bounds_px.as_tuple(),
            *monitor.work_area_px.as_tuple(),
            monitor.dpi,
            bool(monitor.is_primary),
        )
        for monitor in monitors
    ))


def adjacent_surface_layout(
    surface_id: str,
    desired_size_dip: tuple[float, float],
    context: UiDpiContext,
    anchor_rect_px: RectPx,
    *,
    preferred_edge: WindowEdge | None = None,
    gap_dip: float = 4,
    margin_dip: float = 8,
) -> SurfaceLayout:
    """Fit a surface beside an anchor while keeping it fully reachable."""

    requested_width = dip_to_px(desired_size_dip[0], context.dpi)
    requested_height = dip_to_px(desired_size_dip[1], context.dpi)
    gap = dip_to_px(gap_dip, context.dpi)
    work = context.monitor.work_area_px
    prefer_left = preferred_edge == "right"
    right_origin = (anchor_rect_px.right + gap, anchor_rect_px.top)
    left_origin = (anchor_rect_px.left - requested_width - gap, anchor_rect_px.top)
    origins = (left_origin, right_origin) if prefer_left else (right_origin, left_origin)
    for origin in origins:
        candidate = RectPx(
            origin[0], origin[1],
            origin[0] + requested_width,
            origin[1] + requested_height,
        )
        if work.contains(candidate):
            return layout_surface(
                surface_id, desired_size_dip, context,
                origin_px=origin, margin_dip=margin_dip,
            )
    return layout_surface(
        surface_id, desired_size_dip, context,
        origin_px=origins[0], margin_dip=margin_dip,
    )


def layout_surface_catalog(
    context: UiDpiContext,
    *,
    surface_sizes: Mapping[str, tuple[float, float]] = SURFACE_SIZE_DIP,
) -> dict[str, SurfaceLayout]:
    """Resolve every assistant surface for deterministic geometry smoke."""

    return {
        surface_id: layout_surface(surface_id, size, context)
        for surface_id, size in surface_sizes.items()
    }


def _surface_layout(
    surface_id: str,
    context: UiDpiContext,
    requested_size_px: tuple[int, int],
    available: RectPx,
    rect: RectPx,
) -> SurfaceLayout:
    needs_width_reflow = rect.width < requested_size_px[0]
    return SurfaceLayout(
        surface_id=surface_id,
        ui_context=context,
        rect_px=rect,
        available_rect_px=available,
        requested_size_px=requested_size_px,
        variant="compact" if needs_width_reflow else "regular",
        needs_vertical_scroll=rect.height < requested_size_px[1],
    )


def layout_surface(
    surface_id: str,
    desired_size_dip: tuple[float, float],
    context: UiDpiContext,
    *,
    origin_px: tuple[int, int] | None = None,
    margin_dip: float = 8,
) -> SurfaceLayout:
    """Fit a surface by available space; DPI scales tokens, not the whole screen."""

    requested = (
        dip_to_px(desired_size_dip[0], context.dpi),
        dip_to_px(desired_size_dip[1], context.dpi),
    )
    if requested[0] <= 0 or requested[1] <= 0:
        raise ValueError("desired surface size must be positive")
    margin_px = dip_to_px(margin_dip, context.dpi)
    available = _available_work_area(context.monitor.work_area_px, margin_px)
    if origin_px is None:
        left = available.left + (available.width - min(requested[0], available.width)) // 2
        top = available.top + (available.height - min(requested[1], available.height)) // 2
    else:
        left, top = int(origin_px[0]), int(origin_px[1])
    desired = RectPx(left, top, left + requested[0], top + requested[1])
    fitted = clamp_rect_to_work_area(
        desired,
        context.monitor.work_area_px,
        margin_px=margin_px,
    )
    return _surface_layout(surface_id, context, requested, available, fitted)


def capture_window_anchor(
    rect: RectPx,
    context: UiDpiContext,
    *,
    margin_dip: float = 8,
) -> WindowAnchor:
    """Capture the nearest work-area edge plus its normalized edge position."""

    margin_px = dip_to_px(margin_dip, context.dpi)
    available = _available_work_area(context.monitor.work_area_px, margin_px)
    fitted = clamp_rect_to_work_area(
        rect,
        context.monitor.work_area_px,
        margin_px=margin_px,
    )
    distances = {
        "left": fitted.left - available.left,
        "right": available.right - fitted.right,
        "top": fitted.top - available.top,
        "bottom": available.bottom - fitted.bottom,
    }
    edge = min(distances, key=distances.get)
    if edge in {"left", "right"}:
        span = available.height - fitted.height
        offset = fitted.top - available.top
    else:
        span = available.width - fitted.width
        offset = fitted.left - available.left
    ratio = offset / span if span > 0 else 0.5
    return WindowAnchor(
        monitor_id=context.monitor.monitor_id,
        edge=edge,  # type: ignore[arg-type]
        ratio=max(0.0, min(float(ratio), 1.0)),
        saved_work_area_px=context.monitor.work_area_px,
        saved_dpi=context.dpi,
    )


def _monitor_for_anchor(
    anchor: WindowAnchor,
    monitors: Sequence[MonitorContext],
) -> MonitorContext:
    for monitor in monitors:
        if monitor.monitor_id == anchor.monitor_id:
            return monitor
    if not monitors:
        raise ValueError("at least one monitor is required")
    if any(
        anchor.saved_work_area_px.intersection_area(monitor.bounds_px) > 0
        for monitor in monitors
    ):
        return select_monitor_for_rect(anchor.saved_work_area_px, monitors)
    for monitor in monitors:
        if monitor.is_primary:
            return monitor
    return select_monitor_for_rect(anchor.saved_work_area_px, monitors)


def restore_window_anchor(
    anchor: WindowAnchor,
    desired_size_dip: tuple[float, float],
    monitors: Sequence[MonitorContext],
    *,
    surface_id: str = "window",
    margin_dip: float = 8,
) -> SurfaceLayout:
    """Restore on the saved monitor or safely fall back after topology changes."""

    monitor = _monitor_for_anchor(anchor, monitors)
    context = UiDpiContext.from_monitor(monitor)
    requested = (
        dip_to_px(desired_size_dip[0], context.dpi),
        dip_to_px(desired_size_dip[1], context.dpi),
    )
    if requested[0] <= 0 or requested[1] <= 0:
        raise ValueError("desired surface size must be positive")
    margin_px = dip_to_px(margin_dip, context.dpi)
    available = _available_work_area(monitor.work_area_px, margin_px)
    width = min(requested[0], available.width)
    height = min(requested[1], available.height)
    ratio = max(0.0, min(anchor.ratio, 1.0))

    if anchor.edge in {"left", "right"}:
        top = available.top + round((available.height - height) * ratio)
        left = available.left if anchor.edge == "left" else available.right - width
    else:
        left = available.left + round((available.width - width) * ratio)
        top = available.top if anchor.edge == "top" else available.bottom - height
    fitted = RectPx(left, top, left + width, top + height)
    return _surface_layout(surface_id, context, requested, available, fitted)


__all__ = [
    "BASE_DPI",
    "ControlTokensPx",
    "FontTokensPx",
    "MonitorContext",
    "RadiusTokensPx",
    "RectPx",
    "SpacingTokensPx",
    "StrokeTokensPx",
    "SurfaceLayout",
    "SURFACE_SIZE_DIP",
    "UiDpiContext",
    "UiTokensPx",
    "WindowAnchor",
    "adjacent_surface_layout",
    "capture_window_anchor",
    "clamp_rect_to_work_area",
    "dip_to_px",
    "format_tk_geometry",
    "layout_surface",
    "layout_surface_catalog",
    "monitor_topology_signature",
    "restore_window_anchor",
    "select_monitor_for_rect",
    "ui_tokens",
]
