"""Verify native-monitor/Qt-screen correspondence before placing an overlay.

Windows Qt can expose EDID-friendly names instead of GDI DISPLAY identifiers.
On Windows, Qt preserves screen origins but scales sizes into logical pixels.
Only a unique geometry match (or a geometry-verified device ID) is accepted.
"""
from dataclasses import dataclass
import math


class ScreenGeometryError(ValueError):
    def __init__(self, message, diagnostics):
        super().__init__(message)
        self.diagnostics = diagnostics


@dataclass(frozen=True)
class ClientScreenProjection:
    screen: object
    origin_x: float
    origin_y: float
    device_scale: float
    diagnostics: dict


def project_client_geometry(origin, size, monitor_name, monitor_rect, screens):
    """No native IO, scaling of pose matrices, primary-screen fallback or mutation."""
    values = (*origin, *size, *monitor_rect)
    if (len(origin) != 2 or len(size) != 2 or len(monitor_rect) != 4
            or any(isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v) for v in values)):
        raise ScreenGeometryError("游戏窗口坐标信息不完整", {})
    x, y = origin
    width, height = size
    left, top, right, bottom = monitor_rect
    diag = dict(nativeDevice=monitor_name, nativeRect=list(monitor_rect),
                clientOrigin=list(origin), clientSize=list(size), qtScreens=[])
    if (width <= 0 or height <= 0 or right <= left or bottom <= top
            or x >= right or y >= bottom or x + width <= left or y + height <= top):
        raise ScreenGeometryError("游戏客户区与目标显示器位置不一致", diag)
    candidates = []
    for screen in screens:
        try:
            geometry = screen.geometry()
            qx, qy, qw, qh = geometry.x(), geometry.y(), geometry.width(), geometry.height()
            dpr, name = float(screen.devicePixelRatio()), str(screen.name())
            row = dict(name=name, logicalRect=[qx, qy, qw, qh], deviceScale=dpr)
            diag["qtScreens"].append(row)
            if not all(math.isfinite(v) for v in (qx, qy, qw, qh, dpr)) or min(qw, qh, dpr) <= 0:
                continue
            # Logical integer rounding can move a reconstructed size slightly;
            # keep the allowance bounded to two physical pixels, never a ratio.
            tolerance = min(2., max(1., dpr / 2. + 1e-6))
            if (abs(qx - left) <= 1. and abs(qy - top) <= 1.
                    and abs(qw * dpr - (right - left)) <= tolerance
                    and abs(qh * dpr - (bottom - top)) <= tolerance):
                candidates.append((screen, row))
        except (RuntimeError, TypeError, ValueError, AttributeError):
            continue  # A disconnected screen cannot authorize placement.
    named = [row for row in candidates if row[1]["name"].casefold() == str(monitor_name).casefold()]
    selected = named[0] if len(named) == 1 else candidates[0] if len(candidates) == 1 else None
    if selected is None:
        raise ScreenGeometryError("无法唯一核对 Qt 与游戏显示器坐标", diag)
    screen, row = selected
    dpr = row["deviceScale"]
    diag.update(selectedQtScreen=row["name"], match="device-and-geometry" if selected in named else "unique-physical-geometry")
    return ClientScreenProjection(screen, row["logicalRect"][0] + (x - left) / dpr,
                                  row["logicalRect"][1] + (y - top) / dpr, dpr, diag)
