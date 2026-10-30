"""Explicitly bound foreground client capture; no discovery or screen fallback."""
from __future__ import annotations

import operator
import sys
import threading

from client_paths import APP_ROOT
APP = APP_ROOT
if str(APP) not in sys.path:
    sys.path.insert(0, str(APP))
from session_state_machine import global_key_is_allowed
from client_input import NativeWindowAPI


class CaptureUnavailable(RuntimeError):
    pass


class ClientCaptureProvider:
    """Worker-safe provider returning the existing RegionCapture contract.

    bind() records a fixed half-open client ROI. bindFullClient() records only
    the full-client intent: each worker capture derives its rectangle from that
    transaction's backend context. Neither binding method takes a frame.
    """
    def __init__(self, native=None, backend=None, *, full_capture=None):
        # Same callback contract as client_region_capture: (hwnd) -> (BGR,
        # physical client bounds). The owner must exclude ALL its Qt surfaces
        # through its existing owner-thread lane and restore them before return
        # or exception. No callback means no screen fallback at all.
        if full_capture is not None and not callable(full_capture):
            raise TypeError("A display-excluding full capture callback is required")
        self._native = native if native is not None else NativeWindowAPI()
        self._backend = backend
        self._full_capture = full_capture
        self._lock = threading.RLock()
        self._hwnd, self._region = 0, None
        self._full_client = False
        self._expected_context = None
        self._enabled, self._closed = False, False
        self._generation = 0
        self._status, self._message = "disabled", "Client capture is disabled."

    @property
    def snapshot(self):
        with self._lock:
            return dict(enabled=self._enabled, status=self._status, message=self._message,
                        hwnd=str(self._hwnd), region=list(self._region or ()),
                        generation=self._generation, closed=self._closed,
                        captureIntent="full-client" if self._full_client else "roi")

    @property
    def generation(self):
        with self._lock:
            return self._generation

    def is_current(self, captured_context, generation=None):
        """Revalidate captured geometry without acquiring any pixels.

        ``generation`` is the provider generation sampled for acquisition;
        passing it rejects disable/re-enable and rebinding even if geometry is
        identical. Omitting it verifies the current binding for this call only.
        Native failures are unknown context and return False.
        """
        with self._lock:
            if (not self._enabled or self._closed or not self._hwnd
                    or (not self._full_client and self._region is None)):
                return False
            current_generation = self._generation
            if generation is not None and generation != current_generation:
                return False
            hwnd, region = self._hwnd, self._region
            full_client = self._full_client
            expected = self._expected_context
        try:
            if not self._native.is_window(hwnd):
                return False
            if not global_key_is_allowed(hwnd, self._native.foreground_window()):
                return False
            from client_region_capture import client_capture_context
            current = client_capture_context(hwnd, _backend=self._backend)
            if current != captured_context or (expected is not None and current != expected):
                return False
            width, height = current.full_client_size
            if not full_client and (region[0] < 0 or region[1] < 0 or region[2] > width or region[3] > height):
                return False
            # Foreground can change while the native geometry query runs.
            if (not self._native.is_window(hwnd)
                    or not global_key_is_allowed(hwnd, self._native.foreground_window())):
                return False
            with self._lock:
                return bool(self._enabled and not self._closed
                            and self._generation == current_generation
                            and self._hwnd == hwnd and self._region == region
                            and self._full_client == full_client)
        except Exception:
            return False

    def bind(self, hwnd, region, expected_context=None):
        with self._lock:
            if self._closed:
                raise CaptureUnavailable("Capture provider is shut down")
            self.disable()
            hwnd = operator.index(hwnd)
            rect = tuple(operator.index(value) for value in region)
            if hwnd <= 0 or len(rect) != 4 or rect[2] <= rect[0] or rect[3] <= rect[1]:
                raise ValueError("Explicit HWND and a positive client rectangle are required")
            if rect[0] < 0 or rect[1] < 0:
                raise ValueError("Client rectangle must begin inside the client")
            self._hwnd, self._region = hwnd, rect
            self._full_client = False
            self._expected_context = expected_context

    def bindFullClient(self, hwnd):
        """Bind a full-client capture intent; no UI/catalog geometry is retained."""
        with self._lock:
            if self._closed:
                raise CaptureUnavailable("Capture provider is shut down")
            self.disable()
            hwnd = operator.index(hwnd)
            if hwnd <= 0:
                raise ValueError("Explicit HWND is required")
            self._hwnd, self._region = hwnd, None
            self._full_client = True
            self._expected_context = None

    def enable(self):
        with self._lock:
            if self._closed:
                return False
            self._enabled = False
            self._generation += 1
            try:
                if (not self._hwnd or (not self._full_client and self._region is None)
                        or not self._native.is_window(self._hwnd)):
                    self._status, self._message = "unknown", "An explicit valid game HWND/client region is required."
                    return False
            except Exception as error:
                self._status, self._message = "failed", str(error)
                return False
            self._enabled, self._status = True, "ready"
            self._message = "Client capture enabled; no frame has been taken."
            return True

    def disable(self):
        with self._lock:
            self._enabled = False
            self._generation += 1
            self._status, self._message = "disabled", "Client capture is disabled."

    @staticmethod
    def _cancelled(cancelled):
        if cancelled is None:
            return False
        return bool(cancelled.is_set() if hasattr(cancelled, "is_set") else cancelled())

    def capture(self, cancelled=None):
        with self._lock:
            if not self._enabled or self._closed:
                raise CaptureUnavailable("Client capture requires explicit enable")
            hwnd, region, expected = self._hwnd, self._region, self._expected_context
            full_client = self._full_client
            generation = self._generation
        if self._cancelled(cancelled):
            raise CaptureUnavailable("Capture request cancelled")
        try:
            if not self._native.is_window(hwnd):
                raise CaptureUnavailable("Bound game window is unavailable; rebind explicitly")
            if not global_key_is_allowed(hwnd, self._native.foreground_window()):
                raise CaptureUnavailable("Bound game must own the foreground before capture")
            from client_region_capture import capture_client_region, client_capture_context
            context = client_capture_context(hwnd, _backend=self._backend)
            if full_client:
                # Rectangle and metadata come from this same worker/backend
                # context, not from catalog pixels sampled on another thread.
                width, height = context.full_client_size
                region = (0, 0, width, height)
            if region[2] > context.full_client_size[0] or region[3] > context.full_client_size[1]:
                raise CaptureUnavailable("Bound client rectangle exceeds the current client; rebind explicitly")
            frame = capture_client_region(hwnd, region, expected_context=expected or context,
                                          full_capture=self._full_capture, _backend=self._backend)
            with self._lock:
                if (generation != self._generation or not self._enabled or self._closed
                        or self._cancelled(cancelled)):
                    raise CaptureUnavailable("Captured frame was superseded or cancelled")
            if not global_key_is_allowed(hwnd, self._native.foreground_window()):
                raise CaptureUnavailable("Foreground changed during capture; frame discarded")
            with self._lock:
                if generation != self._generation or not self._enabled or self._closed:
                    raise CaptureUnavailable("Captured frame was superseded")
                self._status, self._message = "ready", "Client frame captured."
            return frame
        except Exception as error:
            with self._lock:
                if generation == self._generation and self._enabled:
                    self._status, self._message = "failed", str(error)
            raise

    def shutdown(self):
        with self._lock:
            self.disable()
            self._closed = True
            self._hwnd, self._region = 0, None
