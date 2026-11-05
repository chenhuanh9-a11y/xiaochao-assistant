"""Editor for the 框选 floor-recognition mode.

The player frames the range the floor-button row can slide through on the
latest open-map frame the runtime already captured; no extra capture runs. The
chosen rectangle only enters the settings draft and is committed by the
settings page's normal save. Reading the draft inside the frame gives live
feedback before saving.
"""
from __future__ import annotations

import threading

from PySide6.QtCore import QObject, Property, Signal, Slot
from PySide6.QtGui import QImage
from PySide6.QtQuick import QQuickImageProvider

from floor_control_region import (MODE_FLOORS, locate_floor_row, normalized_region, read_floor_in_region,
                                  region_for_client)

# Client part shown in the editor: legacy top row and new right-side column.
# Additional real aspect ratios still need independent acceptance.
VIEW = (.5, 0., .5, .65)
DEFAULT_RECT = (.70, .01, .24, .11)
FLOOR_LABELS = {-1: "B1 层", 1: "1 楼", 2: "2 楼"}
REASONS = {
    "highlight-not-found": "框内没有找到高亮的楼层按钮",
    "buttons-incomplete": "框内按钮不完整，请把整排按钮和它能滑到的位置都框进去",
    "too-many-buttons": "框内按钮比本模式多，请把框收紧到楼层按钮这一排",
    "region-too-small": "框太小",
    "region-invalid": "框选范围无效",
    "vertical-labels-unverified": "竖排楼层数字或负号不完整，请框住全部按钮",
    "layouts-ambiguous": "发现多组楼层控件，请缩小框选范围",
}


class FloorRegionImages(QQuickImageProvider):
    """Holds the one editor image; no disk encoding."""
    def __init__(self):
        super().__init__(QQuickImageProvider.Image)
        self._lock = threading.Lock()
        self._image = QImage()

    def publish(self, image: QImage) -> None:
        with self._lock:
            self._image = image

    def requestImage(self, identifier, size, requestedSize):
        with self._lock:
            image = self._image
        if size is not None:
            size.setWidth(image.width())
            size.setHeight(image.height())
        return image


def suggested_rect(mode, floor_read, size):
    """A starting range around the fixed reading's buttons, else a default."""
    read = floor_read or {}
    if read.get("controlAxis") == "vertical":
        # Vertical centers are 2D and in observedOrder, never legacy x slots.
        try:
            points = read["centerPoints"]
            x, y, w, h = read["tileBounds"]
            ys = [float(point[1]) for point in points]
            x0, x1 = max(0., x - .3 * w), min(1., x + 1.3 * w)
            y0, y1 = max(0., min(ys) - .7 * h), min(1., max(ys) + .7 * h)
            rect = normalized_region((x0, y0, x1 - x0, y1 - y0))
            if rect is not None:
                return rect
        except (KeyError, TypeError, ValueError):
            pass
        return DEFAULT_RECT
    centers = read.get("centers")
    floors = MODE_FLOORS.get(mode, ())
    if not isinstance(centers, (list, tuple)) or len(centers) != len(floors) or len(centers) < 2:
        return DEFAULT_RECT
    try:
        centers = sorted(float(c) for c in centers)
    except (TypeError, ValueError):
        return DEFAULT_RECT
    pitch = (centers[-1] - centers[0]) / (len(centers) - 1)
    if not pitch > 0:
        return DEFAULT_RECT
    # The selected button keeps its place while the row slides, so any button
    # may come to rest up to (count - 1) pitches either side of it.
    reach = (len(floors) - 1) * pitch + .7 * pitch
    vertical = (floor_read or {}).get("controlVertical") or floor_read or {}
    top = vertical.get("regionTop")
    height = vertical.get("regionHeight")
    if not isinstance(top, (int, float)) or not isinstance(height, (int, float)) or height <= 0:
        top, height = DEFAULT_RECT[1], DEFAULT_RECT[3]
    selected = floor_read.get("selectedFloor")
    anchor = centers[floors.index(selected)] if selected in floors else sum(centers) / len(centers)
    x0, x1 = max(0., anchor - reach), min(1., anchor + reach)
    y0, y1 = max(0., top - .3 * height), min(1., top + 1.3 * height)
    return normalized_region((x0, y0, x1 - x0, y1 - y0)) or DEFAULT_RECT


class FloorRegionService(QObject):
    changed = Signal()

    def __init__(self, runtime, settings, images: FloorRegionImages):
        super().__init__()
        self._runtime, self._settings, self._images = runtime, settings, images
        self._frame = self._mode = None
        self._image_url = ""
        self._serial = 0
        self._draft = []
        self._preview = ""
        self._message = ""
        # The summary follows the settings draft.
        settings.changed.connect(self.changed)

    def _applied_context(self):
        snapshot = getattr(self._runtime, "snapshot", None)
        if callable(snapshot):
            try:
                state = snapshot()
                if isinstance(state, dict):
                    return state.get("season", "s0"), state.get("mode")
            except Exception:
                return None, None
        # Compatibility for the old calibration-only owner/test protocol.
        return self._settings.values.get("gameSeason", "s0"), None

    def _s0_context_current(self):
        season, mode = self._applied_context()
        return season == "s0" and (mode is None or self._mode is None or mode == self._mode)

    def _reject_context(self):
        self._frame = self._mode = None
        self._image_url, self._draft, self._preview = "", [], ""
        self._images.publish(QImage())
        self._message = "当前赛季尚未验证竖排楼层校准；不能写入 S0 框选范围。"
        self.changed.emit()
        return False

    @Slot()
    def invalidateContext(self):
        if self._frame is not None and not self._s0_context_current():
            self._reject_context()

    summary = Property(str, lambda s: region_summary(s._settings.values), notify=changed)
    available = Property(bool, lambda s: s._frame is not None, notify=changed)
    imageUrl = Property(str, lambda s: s._image_url, notify=changed)
    viewRect = Property("QVariantList", lambda s: list(VIEW), notify=changed)
    draftRect = Property("QVariantList", lambda s: list(s._draft), notify=changed)
    preview = Property(str, lambda s: s._preview, notify=changed)
    message = Property(str, lambda s: s._message, notify=changed)
    frameMode = Property(str, lambda s: s._mode or "", notify=changed)
    clientSize = Property("QVariantList", lambda s: list(s._frame.shape[1::-1]) if s._frame is not None else [],
                          notify=changed)

    @Slot(result=bool)
    def open(self):
        """Load the latest open-map frame; never captures by itself."""
        if not self._s0_context_current():
            return self._reject_context()
        latest = self._runtime.latest_map_frame() if self._runtime is not None else None
        if latest is None:
            self._frame = self._mode = None
            self._image_url, self._draft, self._preview = "", [], ""
            self._message = "还没有可用的地图画面：先连接游戏，在游戏里打开一次地图，再回来框选。"
            self.changed.emit()
            return False
        frame, mode, floor_read = latest
        current_season, current_mode = self._applied_context()
        if current_season != "s0" or (current_mode is not None and mode != current_mode):
            return self._reject_context()
        self._frame, self._mode = frame, mode
        h, w = frame.shape[:2]
        x0, y0 = int(w * VIEW[0]), int(h * VIEW[1])
        x1, y1 = int(w * (VIEW[0] + VIEW[2])), int(h * (VIEW[1] + VIEW[3]))
        import cv2
        import numpy as np
        rgba = np.ascontiguousarray(cv2.cvtColor(frame[y0:y1, x0:x1, :3], cv2.COLOR_BGR2RGBA))
        self._images.publish(QImage(rgba.data, rgba.shape[1], rgba.shape[0], rgba.strides[0],
                                    QImage.Format_RGBA8888).copy())
        self._serial += 1
        self._image_url = f"image://floorregion/{self._serial}"
        saved = region_for_client(self._settings.values.get("floorControlRegion"), (w, h))
        centers = (floor_read or {}).get("centers")
        if not isinstance(centers, (list, tuple)) or len(centers) != len(MODE_FLOORS.get(mode, ())):
            # The fixed detector missed the row; start from the automatic search.
            located = locate_floor_row(frame, mode)
            if located["selectedFloor"] is not None:
                bounds = located["tileBounds"]
                floor_read = dict(located, regionTop=bounds[1], regionHeight=bounds[3])
        self._message = ""
        self._set_draft(saved or suggested_rect(mode, floor_read, (w, h)))
        return True

    def _set_draft(self, rect):
        self._draft = list(rect)
        read = read_floor_in_region(self._frame, rect, self._mode)
        floor = read["selectedFloor"]
        self._preview = (f"框内读到：{FLOOR_LABELS[floor]}" if floor in FLOOR_LABELS
                         else "框内没读到楼层：" + REASONS.get(read["reason"], read["reason"]))
        self.changed.emit()

    @Slot(float, float, float, float, result=bool)
    def setDraft(self, x, y, width, height):
        """Client-normalized rectangle from the editor; clamped to the frame."""
        if not self._s0_context_current():
            return self._reject_context()
        if self._frame is None:
            return False
        x0, y0 = max(0., min(1., x)), max(0., min(1., y))
        x1, y1 = max(0., min(1., x + width)), max(0., min(1., y + height))
        rect = normalized_region((min(x0, x1), min(y0, y1), abs(x1 - x0), abs(y1 - y0)))
        if rect is None:
            self._preview = "框太小，请拖出更大的范围"
            self.changed.emit()
            return False
        self._set_draft(rect)
        return True

    @Slot(result=bool)
    def useDraft(self):
        """Put the range into the settings draft and switch the draft to 框选."""
        if not self._s0_context_current():
            return self._reject_context()
        if self._frame is None or not self._draft:
            return False
        h, w = self._frame.shape[:2]
        value = dict(rect=list(self._draft), clientSize=[int(w), int(h)])
        if not (self._settings.setValue("floorControlRegion", value)
                and self._settings.setValue("floorControlMode", "region")):
            self._message = self._settings.message
            self.changed.emit()
            return False
        # The editor closes; release the frame and image, keep the message.
        self._frame = self._mode = None
        self._image_url, self._draft, self._preview = "", [], ""
        self._images.publish(QImage())
        self._message = f"已放入草稿（{w}×{h}）；点「保存更改」后生效。"
        self.changed.emit()
        return True

    @Slot()
    def close(self):
        self._frame = self._mode = None
        self._image_url, self._draft, self._preview, self._message = "", [], "", ""
        self._images.publish(QImage())
        self.changed.emit()


def region_summary(values) -> str:
    """One line for the settings page describing the saved range."""
    region = (values or {}).get("floorControlRegion")
    if not isinstance(region, dict) or normalized_region(region.get("rect")) is None:
        return "还没有框选范围；未框选时按自动方式识别。"
    size = region.get("clientSize") or ["?", "?"]
    return f"已框选（按 {size[0]}×{size[1]} 窗口框的；窗口比例变了会先按自动方式识别，需要重新框选）"
