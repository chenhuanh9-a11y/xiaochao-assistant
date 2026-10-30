"""Display-only reference placement and installed-resource editing previews.

Coordinate provenance is explicit. An unmarked historical list could have been
written by either shell, so it is clamped without guessing a DPI conversion.
Nothing here observes a game, selects an identity, or grants a pose.
"""
from dataclasses import dataclass
import copy
import math
import threading

from PySide6.QtCore import QObject, Property, QRunnable, QThreadPool, Qt, Signal, Slot

from client_screen_geometry import project_client_geometry, ScreenGeometryError


QT_LOGICAL = "qt-logical-v1"
LEGACY_PHYSICAL = "legacy-physical-v1"
UNKNOWN = "unknown"


def _numbers(value, count, name):
    if (not isinstance(value, (tuple, list)) or len(value) != count
            or any(isinstance(v, bool) or not isinstance(v, (int, float))
                   or not math.isfinite(v) for v in value)):
        raise ValueError(f"Invalid reference {name}")
    return tuple(float(v) for v in value)


def reference_position_record(position):
    """New saves carry their space; reading/placement never writes preferences."""
    return dict(space=QT_LOGICAL, position=list(_numbers(position, 2, "position")))


@dataclass(frozen=True)
class ReferencePlacement:
    screen: object
    x: float
    y: float
    width: float
    height: float
    diagnostics: dict

    @property
    def position(self):
        return (self.x, self.y)

    @property
    def size(self):
        return (self.width, self.height)


def _rect(rect):
    return tuple(float(v) for v in (rect.x(), rect.y(), rect.width(), rect.height()))


def _distance(point, rect):
    x, y = point
    left, top, width, height = rect
    return max(left - x, 0, x - left - width) ** 2 + max(top - y, 0, y - top - height) ** 2


def restore_reference_geometry(position, size, screens, *, position_space=UNKNOWN,
                               native_monitors=(), margin=5):
    """Fit a static window into a current QScreen work area, preserving aspect.

    ``native_monitors`` contains ``{device: str, rect: [l,t,r,b]}`` records.
    Only explicitly physical positions use the verified native/Qt projection;
    screen origins are never divided by DPR. An unavailable legacy mapping
    falls back to a visible logical clamp with a diagnostic, not a guessed
    conversion or an automatic preference rewrite.
    """
    width, height = _numbers(size, 2, "size")
    if min(width, height) <= 0:
        raise ValueError("Invalid reference size")
    if isinstance(position, dict):
        position_space = position.get("space", UNKNOWN)
        position = position.get("position")
    if position_space not in (QT_LOGICAL, LEGACY_PHYSICAL, UNKNOWN):
        raise ValueError("Unknown reference position space")
    if isinstance(margin, bool) or not isinstance(margin, (int, float)) or not math.isfinite(margin) or margin < 0:
        raise ValueError("Invalid reference margin")
    point = None if position is None else _numbers(position, 2, "position")
    rows = []
    for screen in screens:
        try:
            geometry, available = _rect(screen.geometry()), _rect(screen.availableGeometry())
            if all(math.isfinite(v) for v in (*geometry, *available)) and min(*geometry[2:], *available[2:]) > 0:
                rows.append((screen, geometry, available))
        except (RuntimeError, TypeError, ValueError, AttributeError):
            continue
    if not rows:
        raise ValueError("No current reference screen")
    diagnostics = dict(sourceSpace=position_space, conversion="none", inputPosition=list(point) if point else None)
    selected = None
    if point is not None and position_space == LEGACY_PHYSICAL:
        monitors = []
        for monitor in native_monitors:
            try:
                left, top, right, bottom = _numbers(monitor["rect"], 4, "native monitor")
                if right > left and bottom > top:
                    monitors.append((monitor, (left, top, right - left, bottom - top)))
            except (KeyError, TypeError, ValueError):
                continue
        try:
            if not monitors:
                raise ValueError("Legacy physical monitor mapping unavailable")
            monitor, rect = min(monitors, key=lambda row: _distance(point, row[1]))
            left, top, w, h = rect
            anchor = (max(left, min(point[0], left + w - 1)), max(top, min(point[1], top + h - 1)))
            projection = project_client_geometry(anchor, (1, 1), monitor.get("device", ""),
                                                  monitor["rect"], [row[0] for row in rows])
            selected = next(row for row in rows if row[0] is projection.screen)
            point = (projection.origin_x, projection.origin_y)
            diagnostics.update(conversion="verified-physical-to-logical", projection=projection.diagnostics)
        except (ScreenGeometryError, ValueError, StopIteration) as error:
            diagnostics.update(conversion="unverified-clamp-only", conversionError=str(error))
    elif point is not None and position_space == UNKNOWN:
        diagnostics["conversion"] = "ambiguous-clamp-only"
    if selected is None:
        selected = min(rows, key=lambda row: _distance(point, row[1])) if point is not None else rows[0]
    screen, _, (left, top, area_width, area_height) = selected
    inset = min(float(margin), max(0., (min(area_width, area_height) - 1) / 2))
    factor = min(1., (area_width - 2 * inset) / width, (area_height - 2 * inset) / height)
    width, height = width * factor, height * factor
    x, y = point if point is not None else (left + area_width - width - 20, top + 35)
    x = max(left + inset, min(x, left + area_width - inset - width))
    y = max(top + inset, min(y, top + area_height - inset - height))
    diagnostics.update(selectedQtScreen=str(screen.name()), outputSpace=QT_LOGICAL,
                       availableRect=[left, top, area_width, area_height], fittedScale=factor)
    return ReferencePlacement(screen, x, y, width, height, diagnostics)


class _PreviewWork(QRunnable):
    def __init__(self, run):
        super().__init__()
        self._run = run

    def run(self):
        self._run()


class ReferencePreviewProvider(QObject):
    """One installed display layer on a worker, with no capture/rank/pose calls.

    The owner must cancel on mode/library/context changes and drain ``pool``
    during normal shutdown. ``ready`` is delivered on the QObject owner thread;
    its RGBA pixels are published by the coordinator's existing image provider.
    The payload is explicitly display-only and cannot enter the game presenter.
    """
    changed = Signal()
    ready = Signal(object)
    failed = Signal(str)
    _completed = Signal(object)

    def __init__(self, factories, parent=None, *, display_loader=None):
        super().__init__(parent)
        self._factories = factories
        if display_loader is None:
            from client_route_preferences import prepare_display_layer
            display_loader = prepare_display_layer
        self._loader = display_loader
        self.pool = QThreadPool(self)
        self.pool.setMaxThreadCount(1)
        self._generation, self._closed = 0, False
        self._cancel = threading.Event()
        self._state = dict(busy=False, message="")
        self._completed.connect(self._finish, Qt.QueuedConnection)

    snapshot = Property("QVariantMap", lambda s: copy.deepcopy(s._state), notify=changed)
    busy = Property(bool, lambda s: s._state["busy"], notify=changed)

    def request(self, mode, profile_id, *, map_id="", floor="", context=None,
                variant_id="variant-01"):
        if self._closed:
            return None
        self.cancel()
        generation, cancelled = self._generation, self._cancel
        captured_context = copy.deepcopy(context)
        self._state = dict(busy=True, requestId=generation, context=captured_context,
                           message="正在读取位置预览")
        self.changed.emit()

        def work():
            try:
                if cancelled.is_set():
                    return
                # EngineFactories is lazy; invoking one mode avoids preparing
                # the unrelated mode and performs all resource IO on this worker.
                engine = self._factories[mode]()
                maps = engine.pack.maps
                if not maps:
                    raise ValueError("No installed reference maps")
                selected = map_id or maps[0].id
                item = engine.pack.by_id.get(selected)
                if item is None:
                    raise ValueError("Reference preview map is not installed")
                floors = tuple(str(x) for x in getattr(item, "layers", engine.identity_floors))
                selected_floor = str(floor) if str(floor) in floors else str(getattr(engine.pack, "default_entry_floor", "1"))
                if selected_floor not in floors:
                    selected_floor = next(iter(floors), "")
                if not selected_floor:
                    raise ValueError("No installed reference preview floor")
                if cancelled.is_set():
                    return
                layer = self._loader(engine, selected, selected_floor, profile_id, variant_id=variant_id)
                import numpy as np
                pixels = np.asarray(layer["pixels"])
                if (pixels.dtype != np.uint8 or pixels.ndim != 3 or pixels.shape[2] != 4
                        or not 0 < pixels.shape[0] * pixels.shape[1] <= 20_000_000):
                    raise ValueError("Invalid installed reference preview pixels")
                rgba = np.ascontiguousarray(pixels[:, :, [2, 1, 0, 3]])
                rgba.setflags(write=False)
                if not cancelled.is_set():
                    self._completed.emit(dict(requestId=generation, context=captured_context,
                        previewOnly=True, referenceAuthorized=False, liveAuthorization=False,
                        mode=mode, mapId=selected, floor=selected_floor, profileId=profile_id,
                        resourceIdentity=layer.get("resourceIdentity"), pixels=rgba,
                        title="位置预览（不参与识别）"))
            except Exception as error:
                if not cancelled.is_set():
                    self._completed.emit(dict(requestId=generation, error=f"{type(error).__name__}:{error}"[:512]))

        self.pool.start(_PreviewWork(work))
        return generation

    @Slot(object)
    def _finish(self, result):
        if self._closed or result["requestId"] != self._generation or self._cancel.is_set():
            return
        self._state = {key: value for key, value in result.items() if key != "pixels"}
        self._state.update(busy=False, message=result.get("error", "位置预览已准备"))
        self.changed.emit()
        if "error" in result:
            self.failed.emit(result["error"])
        else:
            self.ready.emit(result)

    @Slot()
    def cancel(self):
        self._cancel.set()
        self.pool.clear()  # Keep at most one queued replacement behind active IO.
        self._generation += 1
        self._cancel = threading.Event()
        self._state = dict(busy=False, message="")
        self.changed.emit()

    @Slot()
    def close(self):
        self.cancel()
        self._closed = True
