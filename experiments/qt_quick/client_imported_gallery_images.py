"""Worker-only image provider over the currently published browse snapshot."""
from concurrent.futures import CancelledError
import json
import threading
from urllib.parse import quote, unquote

import numpy as np
from PySide6.QtCore import QSize, Qt
from PySide6.QtGui import QImage
from PySide6.QtQuick import QQuickImageProvider


class ImportedGalleryImages(QQuickImageProvider):
    MAX_EDGE = 8192  # Input is already bounded to sixteen million pixels.

    def __init__(self):
        # Qt may otherwise invoke providers synchronously for a consumer that
        # omitted Image.asynchronous. See Qt's QQuickImageProvider contract.
        super().__init__(QQuickImageProvider.Image, QQuickImageProvider.ForceAsynchronousImageLoading)
        self._lock = threading.RLock()
        self._assets, self._serial = None, 0
        self._inflight, self._failed = 0, False

    def publish(self, assets):
        """UI-owned publication; pixel reads remain on Qt's image worker."""
        with self._lock:
            # A refreshed reader can replace a cancelled reader while keeping
            # URLs and textures when actual resource/content identities match.
            if (self._assets is None or assets is None or self._assets.signature != assets.signature
                    or self._inflight or self._failed):
                self._serial += 1
            self._assets = assets
            if assets is not None:
                assets.retain_snapshot()
            self._failed = False

    def url(self, path):
        with self._lock:
            key = self._assets.key(path) if self._assets is not None else None
            return (f'image://importedgallery/{self._serial}/' + quote(json.dumps(key), safe='')
                    if key is not None else '')

    def clear(self):
        self.publish(None)

    def requestImage(self, identifier, size, requestedSize):
        image = QImage()
        with self._lock:
            assets, serial = self._assets, self._serial
            if assets is not None:
                self._inflight += 1
        try:
            requested_serial, encoded = identifier.split('/', 1)
            if assets is None or requested_serial != str(serial):
                return image
            key = json.loads(unquote(encoded))
            if (not isinstance(key, list) or len(key) != 3
                    or any(not isinstance(value, str) for value in key)):
                return image
            pixels = assets.pixels(tuple(key))
            if (pixels.dtype != np.uint8 or pixels.ndim != 3 or pixels.shape[2] != 4
                    or pixels.shape[0] * pixels.shape[1] > 16_000_000):
                return image
            # Wrap read-only BGRA, shrink before allocating the Qt-owned RGB
            # result. rgbSwapped makes an ownership copy independent of NumPy.
            view = QImage(pixels.data, pixels.shape[1], pixels.shape[0],
                          pixels.strides[0], QImage.Format_RGBA8888)
            width = min(requestedSize.width(), self.MAX_EDGE) if requestedSize.width() > 0 else self.MAX_EDGE
            height = min(requestedSize.height(), self.MAX_EDGE) if requestedSize.height() > 0 else self.MAX_EDGE
            if view.width() > width or view.height() > height:
                view = view.scaled(QSize(width, height), Qt.KeepAspectRatio, Qt.SmoothTransformation)
            image = view.rgbSwapped()
            assets.assert_current()
            with self._lock:
                if self._assets is not assets or self._serial != serial:
                    image = QImage()
        except (OSError, ValueError, RuntimeError, TypeError, CancelledError):
            image = QImage()
            with self._lock:
                if self._assets is assets:
                    self._failed = True
        finally:
            with self._lock:
                if assets is not None:
                    self._inflight -= 1
            if size is not None:
                size.setWidth(image.width())
                size.setHeight(image.height())
        return image
