"""A closeable browsing preview, independent of the pinned game reference."""
from __future__ import annotations

from PySide6.QtCore import QObject, Property, Signal, Slot


class PreviewService(QObject):
    changed = Signal()

    def __init__(self, backend, windows, parent=None):
        super().__init__(parent)
        self.backend, self.windows = backend, windows
        self._closed = False
        self._message = ""
        self._data = self._selection()
        backend.changed.connect(self._selection_changed)
        windows.windowChanged.connect(self._window_changed)

    def _selection(self):
        return dict(imageUrl=str(self.backend.selectedImage or ""),
                    title=str(self.backend.selectedTitle or ""),
                    floor=str(self.backend.selectedFloor or ""))

    previewData = Property("QVariantMap", lambda self: dict(self._data), notify=changed)
    visible = Property(bool, lambda self: bool(self.windows.snapshot.get("preview", {}).get("visible")), notify=changed)
    message = Property(str, lambda self: self._message, notify=changed)

    @Slot()
    def _selection_changed(self):
        if self._closed:
            return
        data = self._selection()
        if data != self._data:
            self._data, self._message = data, ""
            self.changed.emit()

    @Slot(str)
    def _window_changed(self, name):
        if name == "preview" and not self._closed:
            self.changed.emit()

    @Slot(result=bool)
    def show(self):
        if self._closed:
            return False
        self._selection_changed()
        if not self._data["imageUrl"]:
            self._message = "请先选择可预览的地图图层。"
            self.changed.emit()
            return False
        self.windows.show("preview")
        return self.visible

    @Slot(result=bool)
    def close(self):
        window = self.windows._windows.get("preview")
        return bool(window.close()) if window is not None else False

    @Slot()
    def shutdown(self):
        if self._closed:
            return
        self._closed = True
        self.backend.changed.disconnect(self._selection_changed)
        self.windows.windowChanged.disconnect(self._window_changed)
        if "preview" in self.windows.snapshot:
            self.windows.hide("preview")
