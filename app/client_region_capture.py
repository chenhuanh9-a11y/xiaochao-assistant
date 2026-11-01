"""Client-DC rectangle capture, separate from recognition and display state.

Coordinates are half-open client pixels under the caller's existing DPI
awareness. This module never changes process DPI awareness, discovers windows,
reads keys, infers a floor, or authorizes display. A crop is not a full frame.
"""
from __future__ import annotations

import ctypes
import operator
import os
from dataclasses import dataclass
from typing import Callable

import numpy as np

Rect = tuple[int, int, int, int]


@dataclass(frozen=True)
class ClientCaptureContext:
    full_client_size: tuple[int, int]
    client_screen_bounds: Rect
    dpi: int


@dataclass(frozen=True)
class RegionCapture:
    frame_bgr: np.ndarray
    roi_client_bounds: Rect
    requested_client_bounds: Rect
    context: ClientCaptureContext
    acquisition: str
    fallback_reason: str | None = None

    @property
    def origin_client(self) -> tuple[int, int]:
        return self.roi_client_bounds[:2]

    @property
    def roi_screen_bounds(self) -> Rect:
        x, y = self.context.client_screen_bounds[:2]
        a, b, c, d = self.roi_client_bounds
        return x + a, y + b, x + c, y + d


class CaptureContextChanged(RuntimeError):
    """Move/resize/DPI transition: discard, recompute the ROI, then request anew."""


def _rect(values) -> Rect:
    if len(values) != 4:
        raise ValueError('rectangle requires left, top, right, bottom')
    result = tuple(operator.index(value) for value in values)
    if result[2] <= result[0] or result[3] <= result[1]:
        raise ValueError('rectangle must have positive width and height')
    return result


def clip_client_rectangle(rect: Rect, size: tuple[int, int]) -> Rect:
    a, b, c, d = _rect(rect)
    width, height = (operator.index(value) for value in size)
    if width <= 0 or height <= 0:
        raise ValueError('client must have positive width and height')
    clipped = max(0, a), max(0, b), min(width, c), min(height, d)
    if clipped[2] <= clipped[0] or clipped[3] <= clipped[1]:
        raise ValueError('rectangle does not intersect the client')
    return clipped


# Module-level types avoid generating new ctypes pointer/Structure classes on
# every capture. DLL wrappers below are also constructed once per module.
class _RECT(ctypes.Structure):
    _fields_ = [('left', ctypes.c_long), ('top', ctypes.c_long),
                ('right', ctypes.c_long), ('bottom', ctypes.c_long)]


class _POINT(ctypes.Structure):
    _fields_ = [('x', ctypes.c_long), ('y', ctypes.c_long)]


class _BITMAPINFOHEADER(ctypes.Structure):
    _fields_ = [('biSize', ctypes.c_uint32), ('biWidth', ctypes.c_long),
                ('biHeight', ctypes.c_long), ('biPlanes', ctypes.c_ushort),
                ('biBitCount', ctypes.c_ushort), ('biCompression', ctypes.c_uint32),
                ('biSizeImage', ctypes.c_uint32), ('biXPelsPerMeter', ctypes.c_long),
                ('biYPelsPerMeter', ctypes.c_long), ('biClrUsed', ctypes.c_uint32),
                ('biClrImportant', ctypes.c_uint32)]


class _BITMAPINFO(ctypes.Structure):
    _fields_ = [('bmiHeader', _BITMAPINFOHEADER), ('bmiColors', ctypes.c_uint32 * 1)]


class _Win32RegionBackend:
    def __init__(self, user32=None, gdi32=None):
        self.user32 = user32 or ctypes.WinDLL('user32', use_last_error=True)
        self.gdi32 = gdi32 or ctypes.WinDLL('gdi32', use_last_error=True)
        pointer = ctypes.c_void_p
        signatures = (
            (self.user32, 'IsIconic', [pointer], ctypes.c_int),
            (self.user32, 'GetClientRect', [pointer, ctypes.POINTER(_RECT)], ctypes.c_int),
            (self.user32, 'ClientToScreen', [pointer, ctypes.POINTER(_POINT)], ctypes.c_int),
            (self.user32, 'GetDpiForWindow', [pointer], ctypes.c_uint),
            (self.user32, 'GetDC', [pointer], pointer),
            (self.user32, 'ReleaseDC', [pointer, pointer], ctypes.c_int),
            (self.gdi32, 'CreateCompatibleDC', [pointer], pointer),
            (self.gdi32, 'DeleteDC', [pointer], ctypes.c_int),
            (self.gdi32, 'CreateDIBSection', [pointer, ctypes.POINTER(_BITMAPINFO), ctypes.c_uint,
                                           ctypes.POINTER(pointer), pointer, ctypes.c_uint], pointer),
            (self.gdi32, 'SelectObject', [pointer, pointer], pointer),
            (self.gdi32, 'DeleteObject', [pointer], ctypes.c_int),
            (self.gdi32, 'BitBlt', [pointer, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
                                  pointer, ctypes.c_int, ctypes.c_int, ctypes.c_uint], ctypes.c_int),
            (self.gdi32, 'GdiFlush', [], ctypes.c_int),
        )
        for dll, name, arguments, result in signatures:
            function = getattr(dll, name)
            function.argtypes = arguments
            function.restype = result

    def context(self, hwnd: int) -> ClientCaptureContext:
        if self.user32.IsIconic(hwnd):
            raise OSError('game window is minimized; not restored by capture')
        rect = _RECT()
        point = _POINT(0, 0)
        if not self.user32.GetClientRect(hwnd, ctypes.byref(rect)):
            raise ctypes.WinError(ctypes.get_last_error())
        if not self.user32.ClientToScreen(hwnd, ctypes.byref(point)):
            raise ctypes.WinError(ctypes.get_last_error())
        width, height = rect.right - rect.left, rect.bottom - rect.top
        dpi = int(self.user32.GetDpiForWindow(hwnd))
        if width <= 0 or height <= 0 or dpi <= 0:
            raise OSError('client size or DPI unavailable')
        return ClientCaptureContext((width, height),
                                    (point.x, point.y, point.x + width, point.y + height), dpi)

    def capture(self, hwnd: int, roi: Rect) -> np.ndarray:
        x, y, right, bottom = roi
        width, height = right - x, bottom - y
        source = self.user32.GetDC(hwnd)
        if not source:
            raise ctypes.WinError(ctypes.get_last_error())
        memory = bitmap = old = None
        try:
            memory = self.gdi32.CreateCompatibleDC(source)
            if not memory:
                raise ctypes.WinError(ctypes.get_last_error())
            info = _BITMAPINFO()
            info.bmiHeader.biSize = ctypes.sizeof(_BITMAPINFOHEADER)
            info.bmiHeader.biWidth, info.bmiHeader.biHeight = width, -height
            info.bmiHeader.biPlanes, info.bmiHeader.biBitCount = 1, 32
            bits = ctypes.c_void_p()
            bitmap = self.gdi32.CreateDIBSection(memory, ctypes.byref(info), 0,
                                               ctypes.byref(bits), None, 0)
            if not bitmap or not bits.value:
                raise ctypes.WinError(ctypes.get_last_error())
            old = self.gdi32.SelectObject(memory, bitmap)
            if not old or old == ctypes.c_void_p(-1).value:
                old = None
                raise ctypes.WinError(ctypes.get_last_error())
            if not self.gdi32.BitBlt(memory, 0, 0, width, height, source, x, y, 0x00CC0020):
                raise ctypes.WinError(ctypes.get_last_error())
            # GDI may batch the copy. CPU access to DIB bits must follow its
            # completion on this capture thread, not merely BitBlt's return.
            if not self.gdi32.GdiFlush():
                raise OSError('GdiFlush failed before reading ROI pixels')
            storage = ctypes.c_ubyte * (width * height * 4)
            bgra = np.ctypeslib.as_array(storage.from_address(bits.value)).reshape(height, width, 4)
            # Own the pixels before deleting the DIB. All-black/small/uniform
            # ROIs are valid captures; readiness is the caller's decision.
            return np.array(bgra[:, :, :3], copy=True, order='C')
        finally:
            if old:
                self.gdi32.SelectObject(memory, old)
            if bitmap:
                self.gdi32.DeleteObject(bitmap)
            if memory:
                self.gdi32.DeleteDC(memory)
            self.user32.ReleaseDC(hwnd, source)


_NATIVE = _Win32RegionBackend() if os.name == 'nt' else None


def client_capture_context(hwnd: int, *, _backend=None) -> ClientCaptureContext:
    backend = _backend if _backend is not None else _NATIVE
    if backend is None:
        raise OSError('client rectangle capture requires Windows')
    return backend.context(hwnd)


def capture_client_region(
    hwnd: int,
    rect_client_px: Rect,
    *,
    full_capture: Callable[[int], tuple[np.ndarray, Rect]] | None = None,
    expected_context: ClientCaptureContext | None = None,
    reject_blank_full_client: bool = False,
    _backend=None,
) -> RegionCapture:
    """Capture one ROI; native errors may use the caller's existing fallback.

    ``full_capture`` must be the caller's ordinary full capture path, including
    its overlay exclusion. No direct screen fallback is invented here. A
    resize/move/DPI transition fails rather than silently recropping stale ROI
    coordinates. ``_backend`` is for offline protocol tests, never an oracle.
    """
    backend = _backend if _backend is not None else _NATIVE
    if backend is None:
        raise OSError('client rectangle capture requires Windows')
    requested = _rect(rect_client_px)
    context = backend.context(hwnd)
    if expected_context is not None and context != expected_context:
        raise CaptureContextChanged('client changed before ROI capture')
    roi = clip_client_rectangle(requested, context.full_client_size)
    try:
        pixels = backend.capture(hwnd, roi)
        if reject_blank_full_client and roi == (0, 0, *context.full_client_size):
            # Preserve the production full-client DC failure criterion. An
            # arbitrary uniform ROI remains legitimate and is never tested.
            from identityv_live_mvp import client_frame_is_blank
            if client_frame_is_blank(pixels):
                raise OSError('game-client-dc-returned-blank-frame')
    except OSError as error:
        if full_capture is None:
            raise
        if backend.context(hwnd) != context:
            raise CaptureContextChanged('client changed before fallback') from error
        frame, bounds = full_capture(hwnd)
        if backend.context(hwnd) != context or tuple(bounds) != context.client_screen_bounds:
            raise CaptureContextChanged('client changed during fallback') from error
        width, height = context.full_client_size
        if frame.dtype != np.uint8 or frame.shape != (height, width, 3):
            raise ValueError('fallback must return a full-client uint8 BGR frame')
        x, y, right, bottom = roi
        pixels = np.array(frame[y:bottom, x:right], copy=True, order='C')
        return RegionCapture(pixels, roi, requested, context,
                             'full-frame-fallback-crop', type(error).__name__ + ': ' + str(error))
    if backend.context(hwnd) != context:
        raise CaptureContextChanged('client changed during ROI capture')
    width, height = roi[2] - roi[0], roi[3] - roi[1]
    if pixels.dtype != np.uint8 or pixels.shape != (height, width, 3):
        raise ValueError('ROI backend must return uint8 BGR pixels at ROI dimensions')
    return RegionCapture(pixels, roi, requested, context, 'client-dc-roi')
