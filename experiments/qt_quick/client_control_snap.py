"""Only the controls window: available-screen DIP placement and release snapping.

QScreen.availableGeometry excludes reserved desktop/taskbar space. Qt window
coordinates are already logical pixels; never multiply these by devicePixelRatio.
No native handles, focus changes, input hooks or other-window positioning.
"""
from __future__ import annotations

import math
from PySide6.QtCore import QObject, QPoint, QRectF, QTimer, Slot
from PySide6.QtGui import QGuiApplication

MARGIN = 16
SNAP_DISTANCE = 12


def valid_position(value):
    return (isinstance(value, (tuple, list)) and len(value) == 2
            and all(type(number) in (int, float) and math.isfinite(number) for number in value))


def choose_area(areas, position, size):
    """Choose one actual monitor, including negative origins and virtual gaps."""
    if not areas:
        raise ValueError("No available control screen")
    areas = [QRectF(area) for area in areas]
    if not valid_position(position):
        return areas[0]
    rect = QRectF(position[0], position[1], size[0], size[1])
    def score(area):
        overlap = area.intersected(rect)
        distance = max(area.left() - rect.center().x(), 0, rect.center().x() - area.right()) ** 2
        distance += max(area.top() - rect.center().y(), 0, rect.center().y() - area.bottom()) ** 2
        return (overlap.width() * overlap.height(), -distance)
    return max(areas, key=score)


def clamp_position(position, size, area, margin=0):
    area = QRectF(area)
    # If a small work area cannot fit both margins, keep the whole possible
    # surface inside it rather than pushing its far edge into the taskbar.
    inset_x = min(margin, max(0, (area.width() - size[0]) / 2))
    inset_y = min(margin, max(0, (area.height() - size[1]) / 2))
    left, top = area.left() + inset_x, area.top() + inset_y
    right = max(left, area.right() - size[0] - inset_x)
    bottom = max(top, area.bottom() - size[1] - inset_y)
    return (round(min(max(position[0], left), right)), round(min(max(position[1], top), bottom)))


def release_position(position, size, area, threshold=SNAP_DISTANCE):
    area = QRectF(area)
    position = clamp_position(position, size, area)
    horizontal = {"left": area.left() + MARGIN, "right": area.right() - size[0] - MARGIN}
    vertical = {"top": area.top() + MARGIN, "bottom": area.bottom() - size[1] - MARGIN}
    x_anchor = min(horizontal, key=lambda key: abs(horizontal[key] - position[0]))
    y_anchor = min(vertical, key=lambda key: abs(vertical[key] - position[1]))
    x_edge_distance = min(abs(position[0] - area.left()), abs(area.right() - size[0] - position[0]))
    y_edge_distance = min(abs(position[1] - area.top()), abs(area.bottom() - size[1] - position[1]))
    x_anchor = x_anchor if min(abs(horizontal[x_anchor] - position[0]), x_edge_distance) <= threshold else None
    y_anchor = y_anchor if min(abs(vertical[y_anchor] - position[1]), y_edge_distance) <= threshold else None
    point = (horizontal[x_anchor] if x_anchor else position[0], vertical[y_anchor] if y_anchor else position[1])
    return clamp_position(point, size, area), (x_anchor, y_anchor)


class ControlSnap(QObject):
    def __init__(self, parent=None, *, areas=None):
        super().__init__(parent)
        self._window = None
        self._closed = False
        self._areas = areas
        self._anchors = (None, None)
        self._dragging = False
        self._placing = False
        self._resize = QTimer(self)
        self._resize.setSingleShot(True)
        self._resize.timeout.connect(self._reflow)

    def _screens(self):
        if self._areas is not None:
            return [QRectF(area) for area in self._areas()]
        screens = QGuiApplication.screens()
        preferred = self._window.screen() if self._window is not None else QGuiApplication.primaryScreen()
        screens = sorted(screens, key=lambda screen: screen is not preferred)
        return [QRectF(screen.availableGeometry()) for screen in screens]

    def bindWindow(self, window, saved_position=None):
        if self._window is not None:
            raise ValueError("A control snap controller binds exactly one window")
        if window.objectName() != "clientControlsWindow":
            raise ValueError("Control placement cannot move another surface")
        self._window = window
        window.setProperty("snapController", self)
        for signal in (window.widthChanged, window.heightChanged, window.screenChanged):
            signal.connect(self._queue_reflow)
        window.visibleChanged.connect(self._queue_reflow)
        if self._areas is None:
            for screen in QGuiApplication.screens():
                screen.availableGeometryChanged.connect(self._queue_reflow)
            QGuiApplication.instance().screenAdded.connect(self._queue_reflow)
            QGuiApplication.instance().screenRemoved.connect(self._queue_reflow)
        size = (window.width(), window.height())
        area = choose_area(self._screens(), saved_position, size)
        if valid_position(saved_position):
            point = clamp_position(saved_position, size, area)
            # Preserve valid stored positions exactly, without a surprise snap.
            self._anchors = tuple("left" if axis == 0 and abs(point[0] - (area.left() + MARGIN)) <= 1
                else "right" if axis == 0 and abs(point[0] - (area.right() - size[0] - MARGIN)) <= 1
                else "top" if axis == 1 and abs(point[1] - (area.top() + MARGIN)) <= 1
                else "bottom" if axis == 1 and abs(point[1] - (area.bottom() - size[1] - MARGIN)) <= 1
                else None for axis in (0, 1))
        else:
            point = clamp_position((area.right() - size[0] - MARGIN, area.top() + MARGIN), size, area)
            self._anchors = ("right", "top")
        self._place(point)
        self._queue_reflow()

    def _place(self, point):
        self._placing = True
        try:
            self._window.setPosition(QPoint(*point))
        finally:
            self._placing = False

    def _queue_reflow(self, *_args):
        if not self._closed and not self._placing and not self._dragging:
            self._resize.start(0)

    def _reflow(self):
        if self._closed or self._window is None or self._dragging:
            return
        window = self._window
        size = (window.width(), window.height())
        point = (window.x(), window.y())
        area = choose_area(self._screens(), point, size)
        # QML caps expansion using the same available area, excluding taskbar.
        limit = max(64, round(area.height() - 2 * MARGIN))
        if window.property("expandedHeightLimit") != limit:
            window.setProperty("expandedHeightLimit", limit)
        x_anchor, y_anchor = self._anchors
        x = area.right() - size[0] - MARGIN if x_anchor == "right" else area.left() + MARGIN if x_anchor == "left" else point[0]
        y = area.bottom() - size[1] - MARGIN if y_anchor == "bottom" else area.top() + MARGIN if y_anchor == "top" else point[1]
        self._place(clamp_position((x, y), size, area))

    @Slot()
    def beginDrag(self):
        self._dragging = True
        self._resize.stop()
        self._anchors = (None, None)

    @Slot()
    def releaseDrag(self):
        if self._window is None:
            return
        self._dragging = False
        window = self._window
        size = (window.width(), window.height())
        area = choose_area(self._screens(), (window.x(), window.y()), size)
        point, self._anchors = release_position((window.x(), window.y()), size, area)
        self._place(point)

    def shutdown(self):
        self._closed = True
        self._resize.stop()
