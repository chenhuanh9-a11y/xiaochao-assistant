from __future__ import annotations

import argparse
import ctypes
import json
import os
import math
import sys
import threading
import time
from collections import deque
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "work" / "vendor"))

import cv2
import numpy as np
from PIL import ImageGrab

from client_space_geometry import (
    CLIENT_COMPONENT_POLICIES,
    centered_safe_viewport,
    normalized_rect_to_client,
)
from side_door_recognition import SideDoorRecognizer


ASSETS = Path(os.environ.get("IDENTITYV_LEGACY_ASSETS_ROOT", str(ROOT / "assets" / "legacy")))
LIVE = ROOT / "work" / "live"
LIVE_OUTPUTS = ROOT / "outputs" / "实时MVP验证"
ROUTE_ROOT = ROOT / "outputs" / "透明路线层" / "速刷版12.9"

user32 = ctypes.windll.user32
gdi32 = ctypes.windll.gdi32


class RECT(ctypes.Structure):
    _fields_ = [("left", ctypes.c_long), ("top", ctypes.c_long), ("right", ctypes.c_long), ("bottom", ctypes.c_long)]


class POINT(ctypes.Structure):
    _fields_ = [("x", ctypes.c_long), ("y", ctypes.c_long)]


class SIZE(ctypes.Structure):
    _fields_ = [("cx", ctypes.c_long), ("cy", ctypes.c_long)]


class BLENDFUNCTION(ctypes.Structure):
    _fields_ = [("BlendOp", ctypes.c_ubyte), ("BlendFlags", ctypes.c_ubyte), ("SourceConstantAlpha", ctypes.c_ubyte), ("AlphaFormat", ctypes.c_ubyte)]


class BITMAPINFOHEADER(ctypes.Structure):
    _fields_ = [
        ("biSize", ctypes.c_uint32), ("biWidth", ctypes.c_long), ("biHeight", ctypes.c_long),
        ("biPlanes", ctypes.c_ushort), ("biBitCount", ctypes.c_ushort), ("biCompression", ctypes.c_uint32),
        ("biSizeImage", ctypes.c_uint32), ("biXPelsPerMeter", ctypes.c_long), ("biYPelsPerMeter", ctypes.c_long),
        ("biClrUsed", ctypes.c_uint32), ("biClrImportant", ctypes.c_uint32),
    ]


class RGBQUAD(ctypes.Structure):
    _fields_ = [("rgbBlue", ctypes.c_ubyte), ("rgbGreen", ctypes.c_ubyte), ("rgbRed", ctypes.c_ubyte), ("rgbReserved", ctypes.c_ubyte)]


class BITMAPINFO(ctypes.Structure):
    _fields_ = [("bmiHeader", BITMAPINFOHEADER), ("bmiColors", RGBQUAD * 1)]


# Explicit pointer-sized signatures are required on 64-bit Windows. They are
# shared by the fast screen capture and the native layered overlay.
user32.GetDC.argtypes = [ctypes.c_void_p]
user32.GetDC.restype = ctypes.c_void_p
user32.ReleaseDC.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
user32.ReleaseDC.restype = ctypes.c_int
gdi32.CreateCompatibleDC.argtypes = [ctypes.c_void_p]
gdi32.CreateCompatibleDC.restype = ctypes.c_void_p
gdi32.CreateDIBSection.argtypes = [
    ctypes.c_void_p, ctypes.POINTER(BITMAPINFO), ctypes.c_uint,
    ctypes.POINTER(ctypes.c_void_p), ctypes.c_void_p, ctypes.c_uint,
]
gdi32.CreateDIBSection.restype = ctypes.c_void_p
gdi32.SelectObject.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
gdi32.SelectObject.restype = ctypes.c_void_p
gdi32.BitBlt.argtypes = [
    ctypes.c_void_p, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
    ctypes.c_void_p, ctypes.c_int, ctypes.c_int, ctypes.c_uint,
]
gdi32.BitBlt.restype = ctypes.c_bool
gdi32.DeleteObject.argtypes = [ctypes.c_void_p]
gdi32.DeleteObject.restype = ctypes.c_bool
gdi32.DeleteDC.argtypes = [ctypes.c_void_p]
gdi32.DeleteDC.restype = ctypes.c_bool


WNDPROC = ctypes.WINFUNCTYPE(ctypes.c_ssize_t, ctypes.c_void_p, ctypes.c_uint, ctypes.c_size_t, ctypes.c_ssize_t)
user32.DefWindowProcW.argtypes = [ctypes.c_void_p, ctypes.c_uint, ctypes.c_size_t, ctypes.c_ssize_t]
user32.DefWindowProcW.restype = ctypes.c_ssize_t


class WNDCLASSEXW(ctypes.Structure):
    _fields_ = [
        ("cbSize", ctypes.c_uint), ("style", ctypes.c_uint), ("lpfnWndProc", WNDPROC),
        ("cbClsExtra", ctypes.c_int), ("cbWndExtra", ctypes.c_int), ("hInstance", ctypes.c_void_p),
        ("hIcon", ctypes.c_void_p), ("hCursor", ctypes.c_void_p), ("hbrBackground", ctypes.c_void_p),
        ("lpszMenuName", ctypes.c_wchar_p), ("lpszClassName", ctypes.c_wchar_p), ("hIconSm", ctypes.c_void_p),
    ]


class MSG(ctypes.Structure):
    _fields_ = [("hwnd", ctypes.c_void_p), ("message", ctypes.c_uint), ("wParam", ctypes.c_void_p), ("lParam", ctypes.c_void_p), ("time", ctypes.c_uint), ("pt", POINT)]


# Layered-window APIs also carry pointer-sized HWND/HDC/HINSTANCE values.  A
# missing signature makes ctypes assume 32-bit ``int`` and intermittently raises
# OverflowError on 64-bit Windows when a valid handle is above 0x7fffffff.
kernel32 = ctypes.windll.kernel32
kernel32.GetModuleHandleW.argtypes = [ctypes.c_wchar_p]
kernel32.GetModuleHandleW.restype = ctypes.c_void_p
kernel32.OpenProcess.argtypes = [ctypes.c_uint, ctypes.c_bool, ctypes.c_uint]
kernel32.OpenProcess.restype = ctypes.c_void_p
kernel32.QueryFullProcessImageNameW.argtypes = [
    ctypes.c_void_p, ctypes.c_uint, ctypes.c_wchar_p, ctypes.POINTER(ctypes.c_ulong),
]
kernel32.QueryFullProcessImageNameW.restype = ctypes.c_bool
kernel32.CloseHandle.argtypes = [ctypes.c_void_p]
kernel32.CloseHandle.restype = ctypes.c_bool
user32.RegisterClassExW.argtypes = [ctypes.POINTER(WNDCLASSEXW)]
user32.RegisterClassExW.restype = ctypes.c_ushort
user32.CreateWindowExW.argtypes = [
    ctypes.c_uint, ctypes.c_wchar_p, ctypes.c_wchar_p, ctypes.c_uint,
    ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
    ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p,
]
user32.CreateWindowExW.restype = ctypes.c_void_p
user32.ShowWindow.argtypes = [ctypes.c_void_p, ctypes.c_int]
user32.ShowWindow.restype = ctypes.c_bool
user32.PostMessageW.argtypes = [ctypes.c_void_p, ctypes.c_uint, ctypes.c_void_p, ctypes.c_void_p]
user32.PostMessageW.restype = ctypes.c_bool
user32.UpdateLayeredWindow.argtypes = [
    ctypes.c_void_p, ctypes.c_void_p, ctypes.POINTER(POINT), ctypes.POINTER(SIZE),
    ctypes.c_void_p, ctypes.POINTER(POINT), ctypes.c_uint,
    ctypes.POINTER(BLENDFUNCTION), ctypes.c_uint,
]
user32.UpdateLayeredWindow.restype = ctypes.c_bool
user32.SetWindowPos.argtypes = [
    ctypes.c_void_p, ctypes.c_void_p,
    ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_uint,
]
user32.SetWindowPos.restype = ctypes.c_bool
user32.DestroyWindow.argtypes = [ctypes.c_void_p]
user32.DestroyWindow.restype = ctypes.c_bool
user32.UnregisterClassW.argtypes = [ctypes.c_wchar_p, ctypes.c_void_p]
user32.UnregisterClassW.restype = ctypes.c_bool
user32.PeekMessageW.argtypes = [
    ctypes.POINTER(MSG), ctypes.c_void_p, ctypes.c_uint, ctypes.c_uint, ctypes.c_uint,
]
user32.PeekMessageW.restype = ctypes.c_bool
user32.TranslateMessage.argtypes = [ctypes.POINTER(MSG)]
user32.TranslateMessage.restype = ctypes.c_bool
user32.DispatchMessageW.argtypes = [ctypes.POINTER(MSG)]
user32.DispatchMessageW.restype = ctypes.c_ssize_t


def now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="milliseconds")


def enhance(image: np.ndarray) -> np.ndarray:
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY) if image.ndim == 3 else image
    return cv2.createCLAHE(2.0, (8, 8)).apply(gray)


def imread_unicode(path: Path, flags: int = cv2.IMREAD_UNCHANGED) -> np.ndarray | None:
    try:
        data = np.fromfile(path, dtype=np.uint8)
    except OSError:
        return None
    return cv2.imdecode(data, flags) if data.size else None


def imwrite_unicode(path: Path, image: np.ndarray) -> bool:
    path.parent.mkdir(parents=True, exist_ok=True)
    ok, encoded = cv2.imencode(path.suffix or ".png", image)
    if not ok:
        return False
    encoded.tofile(path)
    return True


def window_text(hwnd: int) -> str:
    n = user32.GetWindowTextLengthW(hwnd)
    buf = ctypes.create_unicode_buffer(n + 1)
    user32.GetWindowTextW(hwnd, buf, n + 1)
    return buf.value


def process_image_path(pid: int) -> str | None:
    """Return a process image path using read-only, limited query access."""
    process_query_limited_information = 0x1000
    handle = kernel32.OpenProcess(process_query_limited_information, False, int(pid))
    if not handle:
        return None
    try:
        capacity = 32768
        size = ctypes.c_ulong(capacity)
        buffer = ctypes.create_unicode_buffer(capacity)
        if not kernel32.QueryFullProcessImageNameW(
            handle, 0, buffer, ctypes.byref(size),
        ):
            return None
        return buffer.value
    finally:
        kernel32.CloseHandle(handle)


def window_class_name(hwnd: int) -> str:
    """Return a top-level window class without reading window contents."""
    buffer = ctypes.create_unicode_buffer(512)
    if not user32.GetClassNameW(ctypes.c_void_p(int(hwnd)), buffer, len(buffer)):
        return ""
    return buffer.value


def executable_version_strings(image_path: str | None) -> dict[str, str]:
    """Read privacy-safe PE version strings; never return the executable path."""
    path = str(image_path or "")
    if not path:
        return {}
    try:
        version = ctypes.windll.version
        version.GetFileVersionInfoSizeW.argtypes = [
            ctypes.c_wchar_p, ctypes.POINTER(ctypes.c_ulong),
        ]
        version.GetFileVersionInfoSizeW.restype = ctypes.c_ulong
        version.GetFileVersionInfoW.argtypes = [
            ctypes.c_wchar_p, ctypes.c_ulong, ctypes.c_ulong, ctypes.c_void_p,
        ]
        version.GetFileVersionInfoW.restype = ctypes.c_bool
        version.VerQueryValueW.argtypes = [
            ctypes.c_void_p, ctypes.c_wchar_p,
            ctypes.POINTER(ctypes.c_void_p), ctypes.POINTER(ctypes.c_uint),
        ]
        version.VerQueryValueW.restype = ctypes.c_bool
        ignored = ctypes.c_ulong(0)
        size = int(version.GetFileVersionInfoSizeW(path, ctypes.byref(ignored)))
        if size <= 0:
            return {}
        buffer = ctypes.create_string_buffer(size)
        if not version.GetFileVersionInfoW(path, 0, size, buffer):
            return {}

        def query(block: str) -> str:
            pointer = ctypes.c_void_p()
            length = ctypes.c_uint(0)
            if not version.VerQueryValueW(
                buffer, block, ctypes.byref(pointer), ctypes.byref(length),
            ) or not pointer.value or length.value <= 1:
                return ""
            return ctypes.wstring_at(pointer.value, length.value).rstrip("\0")

        pointer = ctypes.c_void_p()
        length = ctypes.c_uint(0)
        translations: list[tuple[int, int]] = []
        if version.VerQueryValueW(
            buffer, r"\VarFileInfo\Translation",
            ctypes.byref(pointer), ctypes.byref(length),
        ) and pointer.value and length.value >= 4:
            raw = ctypes.string_at(pointer.value, length.value)
            for offset in range(0, len(raw) - 3, 4):
                translations.append((
                    int.from_bytes(raw[offset:offset + 2], "little"),
                    int.from_bytes(raw[offset + 2:offset + 4], "little"),
                ))
        for fallback in ((0x0804, 0x04B0), (0x0409, 0x04B0), (0x0409, 0x04E4)):
            if fallback not in translations:
                translations.append(fallback)

        fields = {
            "companyName": "CompanyName",
            "fileDescription": "FileDescription",
            "fileVersion": "FileVersion",
            "productName": "ProductName",
            "productVersion": "ProductVersion",
        }
        result: dict[str, str] = {}
        for language, codepage in translations:
            prefix = f"\\StringFileInfo\\{language:04X}{codepage:04X}\\"
            for output_name, resource_name in fields.items():
                if output_name in result:
                    continue
                value = query(prefix + resource_name).strip()
                if value:
                    result[output_name] = value[:256]
        return result
    except (AttributeError, OSError, TypeError, ValueError):
        return {}


def identityv_title_is_candidate(title: str) -> bool:
    normalized = str(title or "").strip()
    folded = normalized.casefold()
    if normalized == "第五人格":
        return True
    for prefix in ("identity v", "identityv"):
        if folded == prefix:
            return True
        if folded.startswith(prefix) and folded[len(prefix)] in " -–—|:·[()":
            return True
    return False


def identityv_window_identity_is_allowed(title: str, image_path: str | None) -> bool:
    """Fail closed unless both the game title and executable identify Identity V."""
    executable = Path(str(image_path or "")).name.casefold()
    return bool(identityv_title_is_candidate(title) and executable == "dwrg.exe")


def collect_identityv_window_environment() -> dict:
    """Collect text-only binding evidence for Chinese or international clients.

    Only title-matched Identity V windows or the already allowed ``dwrg.exe``
    process are included. Full executable paths, screenshots, command lines,
    and unrelated desktop windows are deliberately excluded.
    """
    candidates: list[dict] = []
    foreground = int(user32.GetForegroundWindow() or 0)
    callback_type = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)

    @callback_type
    def callback(hwnd, _):
        if not user32.IsWindowVisible(hwnd):
            return True
        handle = int(hwnd)
        title = window_text(handle)
        pid = ctypes.c_ulong()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        image_path = process_image_path(int(pid.value))
        executable_name = Path(str(image_path or "")).name
        if not (
            identityv_title_is_candidate(title)
            or executable_name.casefold() == "dwrg.exe"
        ):
            return True

        client = RECT()
        outer = RECT()
        client_size = None
        outer_size = None
        if user32.GetClientRect(hwnd, ctypes.byref(client)):
            client_size = [
                max(0, int(client.right - client.left)),
                max(0, int(client.bottom - client.top)),
            ]
        if user32.GetWindowRect(hwnd, ctypes.byref(outer)):
            outer_size = [
                max(0, int(outer.right - outer.left)),
                max(0, int(outer.bottom - outer.top)),
            ]
        dpi = None
        try:
            dpi = int(user32.GetDpiForWindow(hwnd)) or None
        except (AttributeError, OSError, TypeError, ValueError):
            pass
        candidates.append({
            "title": str(title)[:256],
            "windowClass": window_class_name(handle)[:256],
            "processId": int(pid.value),
            "executableName": executable_name[:256],
            "executablePathIncluded": False,
            "versionInfo": executable_version_strings(image_path),
            "clientSize": client_size,
            "outerWindowSize": outer_size,
            "dpi": dpi,
            "foreground": handle == foreground,
            "acceptedByCurrentBinding": identityv_window_identity_is_allowed(
                title, image_path,
            ),
        })
        return True

    try:
        user32.EnumWindows(callback, 0)
        error = None
    except (AttributeError, OSError, TypeError, ValueError) as exception:
        error = f"{type(exception).__name__}: {exception}"
    result = {
        "schema": "identityv-window-environment-v1",
        "candidateCount": len(candidates),
        "candidates": candidates,
        "currentBinding": {
            "titlePolicy": "exact-cn-or-international-prefix-with-separator",
            "executableAllowlist": ["dwrg.exe"],
            "failClosed": True,
        },
        "privacy": (
            "Only matching game-window text metadata and client dimensions are included; "
            "full paths, screenshots, command lines, and unrelated windows are excluded."
        ),
    }
    if error:
        result["collectionError"] = error
    return result


def find_game_window() -> tuple[int, int, str]:
    matches: list[tuple[int, int, str]] = []
    callback_type = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)

    @callback_type
    def callback(hwnd, _):
        if not user32.IsWindowVisible(hwnd):
            return True
        title = window_text(hwnd)
        pid = ctypes.c_ulong()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        if identityv_window_identity_is_allowed(
            title, process_image_path(int(pid.value)),
        ):
            matches.append((int(hwnd), int(pid.value), title))
        return True

    user32.EnumWindows(callback, 0)
    if not matches:
        raise RuntimeError("未找到第五人格可见顶层窗口")
    return matches[0]


def client_rect(hwnd: int) -> tuple[int, int, int, int]:
    rect = RECT()
    if not user32.GetClientRect(hwnd, ctypes.byref(rect)):
        raise ctypes.WinError()
    point = POINT(0, 0)
    if not user32.ClientToScreen(hwnd, ctypes.byref(point)):
        raise ctypes.WinError()
    return point.x, point.y, point.x + rect.right, point.y + rect.bottom


def capture_client_gdi(hwnd: int) -> tuple[np.ndarray, tuple[int, int, int, int]]:
    if user32.IsIconic(hwnd):
        raise RuntimeError("游戏窗口已最小化；只读MVP不会主动恢复窗口")
    bounds = client_rect(hwnd)
    x0, y0, x1, y1 = bounds
    width, height = x1 - x0, y1 - y0
    screen_dc = user32.GetDC(None)
    memory_dc = gdi32.CreateCompatibleDC(screen_dc)
    bitmap = None
    old = None
    try:
        info = BITMAPINFO()
        info.bmiHeader.biSize = ctypes.sizeof(BITMAPINFOHEADER)
        info.bmiHeader.biWidth = width
        info.bmiHeader.biHeight = -height
        info.bmiHeader.biPlanes = 1
        info.bmiHeader.biBitCount = 32
        info.bmiHeader.biCompression = 0
        bits = ctypes.c_void_p()
        bitmap = gdi32.CreateDIBSection(
            screen_dc, ctypes.byref(info), 0, ctypes.byref(bits), None, 0
        )
        if not bitmap or not bits.value:
            raise ctypes.WinError()
        old = gdi32.SelectObject(memory_dc, bitmap)
        if not gdi32.BitBlt(
            memory_dc, 0, 0, width, height, screen_dc, x0, y0,
            0x00CC0020 | 0x40000000,
        ):
            raise ctypes.WinError()
        buffer_type = ctypes.c_ubyte * (width * height * 4)
        bgra = np.ctypeslib.as_array(buffer_type.from_address(bits.value)).reshape(height, width, 4)
        return np.ascontiguousarray(bgra[:, :, :3]), bounds
    finally:
        if old:
            gdi32.SelectObject(memory_dc, old)
        if bitmap:
            gdi32.DeleteObject(bitmap)
        if memory_dc:
            gdi32.DeleteDC(memory_dc)
        if screen_dc:
            user32.ReleaseDC(None, screen_dc)


def capture_client_window_dc(hwnd: int) -> tuple[np.ndarray, tuple[int, int, int, int]]:
    """Capture the game client DC without compositing unrelated top windows."""
    if user32.IsIconic(hwnd):
        raise RuntimeError("游戏窗口已最小化；只读MVP不会主动恢复窗口")
    bounds = client_rect(hwnd)
    width, height = bounds[2] - bounds[0], bounds[3] - bounds[1]
    source_dc = user32.GetDC(hwnd)
    memory_dc = gdi32.CreateCompatibleDC(source_dc)
    bitmap = None
    old = None
    try:
        info = BITMAPINFO()
        info.bmiHeader.biSize = ctypes.sizeof(BITMAPINFOHEADER)
        info.bmiHeader.biWidth = width
        info.bmiHeader.biHeight = -height
        info.bmiHeader.biPlanes = 1
        info.bmiHeader.biBitCount = 32
        info.bmiHeader.biCompression = 0
        bits = ctypes.c_void_p()
        bitmap = gdi32.CreateDIBSection(
            source_dc, ctypes.byref(info), 0, ctypes.byref(bits), None, 0,
        )
        if not bitmap or not bits.value:
            raise ctypes.WinError()
        old = gdi32.SelectObject(memory_dc, bitmap)
        if not gdi32.BitBlt(
            memory_dc, 0, 0, width, height, source_dc, 0, 0,
            0x00CC0020,
        ):
            raise ctypes.WinError()
        buffer_type = ctypes.c_ubyte * (width * height * 4)
        bgra = np.ctypeslib.as_array(
            buffer_type.from_address(bits.value),
        ).reshape(height, width, 4)
        frame = np.ascontiguousarray(bgra[:, :, :3])
        if float(frame.mean()) < 1.0 or float(frame.std()) < 1.0:
            raise RuntimeError("game-client-dc-returned-blank-frame")
        return frame, bounds
    finally:
        if old:
            gdi32.SelectObject(memory_dc, old)
        if bitmap:
            gdi32.DeleteObject(bitmap)
        if memory_dc:
            gdi32.DeleteDC(memory_dc)
        if source_dc:
            user32.ReleaseDC(hwnd, source_dc)


def capture_client(hwnd: int) -> tuple[np.ndarray, tuple[int, int, int, int]]:
    try:
        # The screen DC also sees our topmost floating sheet and can create a
        # perfect but meaningless self-match. Prefer the game client itself.
        return capture_client_window_dc(hwnd)
    except Exception:
        try:
            return capture_client_gdi(hwnd)
        except Exception:
            # Compatibility fallback for unusual compositor/driver combinations.
            bounds = client_rect(hwnd)
            rgb = np.asarray(ImageGrab.grab(bbox=bounds, all_screens=True))
            return cv2.cvtColor(rgb, cv2.COLOR_RGB2BGR), bounds


MAP_REGIONS = {
    "floorSwitch": (0.77, 0.04, 0.14, 0.17, 0.025),
    "closeButton": (0.90, 0.04, 0.095, 0.24, 0.025),
    "zoomControl": (0.91, 0.27, 0.085, 0.52, 0.018),
    "bottomControls": (0.78, 0.77, 0.21, 0.22, 0.025),
}
BACKDROP = (0.25, 0.14, 0.63, 0.66, 0.45)
MAP_INTERFACE_MIN_WIDTH = 1000
MAP_INTERFACE_MIN_ASPECT = 1.25
MAP_INTERFACE_WIDE_ASPECT = 1.45
MAP_INTERFACE_NARROW_BACKDROP = 0.55
FLOOR_CONTROL_SAFE_ASPECT = 16.0 / 9.0
FLOOR_BUTTON_REGIONS = {
    -1: (0.727, 0.018, 0.035, 0.057),
    1: (0.766, 0.018, 0.035, 0.057),
    2: (0.805, 0.018, 0.035, 0.057),
}


def floor_control_vertical_geometry(width: int, height: int) -> dict:
    """Map the wide HUD's floor-control band into a narrow safe viewport.

    When a client is narrower than 16:9, the game keeps the floor controls
    inside a vertically centered 16:9 safe viewport.  Their x positions still
    scale with the full client, but their y positions must include that safe
    viewport's top inset.  This covers 16:10, 4:3, and intermediate ratios
    without hard-coding individual resolutions.
    """
    w, h = max(1, int(width)), max(1, int(height))
    viewport = centered_safe_viewport((w, h))
    # Keep normalized HUD geometry exact; integer rounding belongs only at the
    # final pixel crop in ``normalized_rect_to_client``.
    safe_height = float(
        w * 9.0 / 16.0
        if viewport.mode == "centered-safe-height" else h
    )
    safe_top = float(
        (h - safe_height) / 2.0
        if viewport.mode == "centered-safe-height" else 0
    )
    if safe_top <= 0.5:
        return {
            "layoutFamily": "wide",
            "safeTop": 0.0,
            "safeHeight": 1.0,
            "searchTop": 0.015,
            "searchBottom": 0.090,
            "regionTop": 0.018,
            "regionHeight": 0.057,
        }
    def safe_y(value: float) -> float:
        return float((safe_top + safe_height * value) / h)

    return {
        "layoutFamily": "centered-16x9-safe-area",
        "safeTop": float(safe_top / h),
        "safeHeight": float(safe_height / h),
        "searchTop": safe_y(0.015),
        "searchBottom": safe_y(0.090),
        "regionTop": safe_y(0.018),
        "regionHeight": float(safe_height * 0.057 / h),
    }


def _legacy_floor_control_vertical_geometry() -> dict:
    return {
        "layoutFamily": "cropped-wide-hud",
        "safeTop": 0.0,
        "safeHeight": 1.0,
        "searchTop": 0.015,
        "searchBottom": 0.090,
        "regionTop": 0.018,
        "regionHeight": 0.057,
    }


def _floor_button_layout_in_geometry(
    frame: np.ndarray, vertical: dict,
) -> tuple[list[float], dict] | None:
    """Anchor the triplet to a filled button, not three strong text columns."""
    if frame is None or frame.ndim != 3:
        return None
    h, w = frame.shape[:2]
    if w < 1000 or h < 600:
        return None
    x0, x1 = int(w * 0.70), int(w * 0.995)
    y0 = int(h * vertical["searchTop"])
    y1 = min(h, int(h * (vertical["searchBottom"] + vertical["regionHeight"])))
    gray = cv2.cvtColor(frame[y0:y1, x0:x1], cv2.COLOR_BGR2GRAY)
    if gray.size == 0:
        return None
    unit = vertical["safeHeight"] * h
    kw, kh = max(3, round(w * 0.020) | 1), max(3, round(unit * 0.030) | 1)
    # Dense texture survives cursor cuts; sparse text strokes do not fill a
    # button-sized window. A component of this density field anchors one tile.
    density = cv2.boxFilter((gray > 60).astype(np.float32), -1, (kw, kh), normalize=True)
    _, _, stats, _ = cv2.connectedComponentsWithStats((density > 0.70).astype(np.uint8), 8)
    best = None
    for bx, by, bw, bh, _area in stats[1:]:
        tile_width, tile_height = bw + kw * 0.5, bh + kh * 0.5
        if not (w * 0.020 <= tile_width <= w * 0.060
                and unit * 0.022 <= tile_height <= unit * 0.085
                and 0.8 <= tile_width / tile_height <= 2.2):
            continue
        top, bottom = max(0, int(by - kh * 0.25)), min(gray.shape[0], int(by + bh + kh * 0.25))
        measured = dict(vertical, regionTop=max(0.0, (y0 + top - 0.022 * unit) / h))
        measure_top = int(h * measured["regionTop"])
        measure_bottom = math.ceil(h * (measured["regionTop"] + measured["regionHeight"]))
        # Label peaks must come from the same rows that will be scored. Text
        # below a tile cannot complete an otherwise occluded floor triplet.
        strip = cv2.cvtColor(frame[measure_top:measure_bottom, x0:x1], cv2.COLOR_BGR2GRAY).astype(np.float32)
        baseline = np.percentile(strip, 20, axis=0)
        signal = np.maximum(strip - baseline[None, :], 0.0).mean(axis=0)
        smooth = cv2.GaussianBlur(signal[None, :], (max(5, round(w * 0.003) | 1), 1), 0).ravel()
        local = np.flatnonzero((smooth[1:-1] >= smooth[:-2]) & (smooth[1:-1] > smooth[2:])) + 1
        anchor = int(bx + bw / 2)
        peaks = []
        for index in sorted(local.tolist(), key=lambda value: float(smooth[value]), reverse=True):
            if smooth[index] < 7.0:
                break
            if bx - kw * 0.25 <= index < bx + bw + kw * 0.25:
                continue
            if all(abs(index - existing) >= max(12, round(w * 0.020)) for existing in peaks):
                peaks.append(index)
            if len(peaks) >= 12:
                break
        peaks.append(anchor)
        peaks.sort()
        for ai in range(len(peaks) - 2):
            for bi in range(ai + 1, len(peaks) - 1):
                for ci in range(bi + 1, len(peaks)):
                    first, second, third = peaks[ai], peaks[bi], peaks[ci]
                    if anchor not in (first, second, third):
                        continue
                    left, right = second - first, third - second
                    if not (w * 0.020 <= min(left, right)
                            and max(left, right) <= min(w * 0.080, tile_width * 1.20)):
                        continue
                    uneven = abs(left - right) / max(left, right)
                    if uneven > 0.28:
                        continue
                    score = float(smooth[first] + smooth[second] + smooth[third]) - 35.0 * uneven
                    if best is None or score > best[0]:
                        best = (score, [float((x0 + index) / w) for index in (first, second, third)], measured)
    return (best[1], best[2]) if best is not None else None


def _locate_floor_button_centers_in_geometry(
    frame: np.ndarray, vertical: dict,
) -> list[float] | None:
    found = _floor_button_layout_in_geometry(frame, vertical)
    return found[0] if found is not None else None


def _locate_floor_button_layout(
    frame: np.ndarray,
) -> tuple[list[float] | None, dict]:
    if frame is None or frame.ndim != 3:
        return None, _legacy_floor_control_vertical_geometry()
    h, w = frame.shape[:2]
    primary = floor_control_vertical_geometry(w, h)
    found = _floor_button_layout_in_geometry(frame, primary)
    if found is not None:
        return found
    if primary["layoutFamily"] == "centered-16x9-safe-area":
        # Accepted confirmation-memory frames may be right-edge crops of a
        # wide client. Their pixel aspect looks narrow, but the top safe inset
        # has already been removed. Only allow the proven legacy band when the
        # complete triplet is visibly shifted to that crop's far-right edge.
        cropped = _legacy_floor_control_vertical_geometry()
        cropped_layout = _floor_button_layout_in_geometry(frame, cropped)
        cropped_centers = cropped_layout[0] if cropped_layout is not None else None
        if (
            cropped_centers is not None
            and cropped_centers[0] >= 0.82
            and cropped_centers[-1] >= 0.90
        ):
            return cropped_layout
    return None, primary


def locate_floor_button_centers(frame: np.ndarray) -> list[float] | None:
    centers, _vertical = _locate_floor_button_layout(frame)
    return centers


def region_pixels(
    frame: np.ndarray,
    region: tuple[float, ...],
    stride: int = 2,
    *,
    policy: str = "full-client",
) -> np.ndarray:
    h, w = frame.shape[:2]
    x, y, rw, rh = region[:4]
    step = max(1, int(stride))
    x0, y0, x1, y1 = normalized_rect_to_client(
        (w, h), (x, y, rw, rh), policy=policy,
    )
    return frame[y0:y1:step, x0:x1:step]


def neutral_ratio(frame: np.ndarray, region: tuple[float, ...], tinted: bool = False) -> float:
    pixels = region_pixels(
        frame, region, policy=CLIENT_COMPONENT_POLICIES["ui-roi"],
    ).astype(np.int16)
    if not pixels.size:
        return 0.0
    maximum = pixels.max(axis=2)
    minimum = pixels.min(axis=2)
    mean = pixels.mean(axis=2)
    if tinted:
        mask = (mean >= 52) & (mean <= 230) & ((maximum - minimum) <= 90)
    else:
        mask = (mean >= 90) & (mean <= 230) & ((maximum - minimum) <= 55)
    return float(mask.mean())


def backdrop_ratio(frame: np.ndarray) -> float:
    # OpenCV uses BGR; the original detector's condition is blue >= red-4 and blue >= green-8.
    # The backdrop spans most of the screen; stride 4 still samples roughly
    # 94k pixels at 2560x1417 and avoids spending live latency on redundant
    # neighboring pixels.
    p = region_pixels(frame, BACKDROP, stride=4).astype(np.int16)
    if not p.size:
        return 0.0
    blue, green, red = p[:, :, 0], p[:, :, 1], p[:, :, 2]
    mean = p.mean(axis=2)
    mask = (mean >= 14) & (mean <= 88) & (blue >= red - 4) & (blue >= green - 8)
    return float(mask.mean())


def backdrop_texture(frame: np.ndarray) -> dict:
    """Separate the flat map canvas from dark/tinted 3-D gameplay frames."""
    pixels = region_pixels(frame, BACKDROP, stride=4)
    if not pixels.size:
        return {"darkRatio": 0.0, "grayStd": 255.0}
    gray = cv2.cvtColor(pixels, cv2.COLOR_BGR2GRAY)
    return {
        "darkRatio": float((gray < 75).mean()),
        "grayStd": float(gray.std()),
    }


def map_interface_shape(width: int, height: int) -> dict:
    """Classify the client layout without treating supported 4:3 as non-map."""
    w, h = int(width), int(height)
    aspect = float(w / max(h, 1))
    if w < MAP_INTERFACE_MIN_WIDTH:
        return {
            "shapeAccepted": False,
            "reason": "capture-shape-width",
            "frameWidth": w,
            "frameHeight": h,
            "aspectRatio": round(aspect, 4),
            "layoutFamily": "unsupported-small",
        }
    if aspect < MAP_INTERFACE_MIN_ASPECT:
        return {
            "shapeAccepted": False,
            "reason": "capture-shape-aspect",
            "frameWidth": w,
            "frameHeight": h,
            "aspectRatio": round(aspect, 4),
            "layoutFamily": "unsupported-narrow",
        }
    return {
        "shapeAccepted": True,
        "reason": "shape-accepted",
        "frameWidth": w,
        "frameHeight": h,
        "aspectRatio": round(aspect, 4),
        "layoutFamily": (
            "wide" if aspect >= MAP_INTERFACE_WIDE_ASPECT else "narrow-4x3"
        ),
    }


def detect_map_interface(frame: np.ndarray) -> dict:
    h, w = frame.shape[:2]
    shape = map_interface_shape(w, h)
    if not shape["shapeAccepted"]:
        return {
            **shape,
            "detected": False,
            "matchedAnchors": 0,
            "neutralMatchedAnchors": 0,
            "tintedMatchedAnchors": 0,
            "ratios": {},
            "tintedRatios": {},
            "backdropRatio": 0.0,
            "backdropTexture": {"darkRatio": 0.0, "grayStd": 255.0},
            "flatMapCanvas": False,
            "detectionMode": "shape-rejected",
        }
    ratios = {name: neutral_ratio(frame, region) for name, region in MAP_REGIONS.items()}
    neutral_count = sum(ratios[name] >= MAP_REGIONS[name][4] for name in MAP_REGIONS)
    backdrop = backdrop_ratio(frame)
    texture = backdrop_texture(frame)
    flat_map_canvas = bool(texture["darkRatio"] >= 0.84 and texture["grayStd"] <= 22.5)
    strong = backdrop >= 0.80
    tinted_ratios: dict[str, float] = {}
    tinted_count = 0
    if neutral_count < 3 and strong:
        tinted_ratios = {name: neutral_ratio(frame, region, True) for name, region in MAP_REGIONS.items()}
        tinted_count = sum(tinted_ratios[name] >= MAP_REGIONS[name][4] for name in MAP_REGIONS)
    damage = strong and tinted_count >= 3
    matched = max(neutral_count, tinted_count if damage else 0)
    required_backdrop = (
        MAP_INTERFACE_NARROW_BACKDROP
        if shape["layoutFamily"] == "narrow-4x3" else BACKDROP[4]
    )
    detected = bool(
        flat_map_canvas and backdrop >= required_backdrop
        and (neutral_count >= 3 or damage)
    )
    if detected:
        reason = "map-interface-detected"
    elif not flat_map_canvas:
        reason = "map-canvas-texture-mismatch"
    elif backdrop < required_backdrop:
        reason = "map-backdrop-insufficient"
    elif matched < 3:
        reason = "map-anchors-insufficient"
    else:
        reason = "map-evidence-insufficient"
    return {
        **shape,
        "reason": reason,
        "detected": detected,
        "matchedAnchors": int(matched),
        "neutralMatchedAnchors": int(neutral_count),
        "tintedMatchedAnchors": int(tinted_count),
        "ratios": ratios,
        "tintedRatios": tinted_ratios,
        "backdropRatio": backdrop,
        "backdropTexture": texture,
        "flatMapCanvas": flat_map_canvas,
        "detectionMode": "damage-tint" if damage else "neutral",
        "requiredBackdropRatio": required_backdrop,
        "clientCoordinatePolicies": dict(CLIENT_COMPONENT_POLICIES),
    }


def detect_selected_floor(frame: np.ndarray) -> dict:
    """Read the textured highlight behind the B1/1/2 floor buttons without OCR."""
    h, w = frame.shape[:2]
    centers, vertical = _locate_floor_button_layout(frame)
    if centers is not None:
        layout = "localized-triplet"
    else:
        # Missing labels cannot turn a fixed crop of text/cursor into a floor.
        return {
            "selectedFloor": None,
            "scores": {"-1": 0.0, "1": 0.0, "2": 0.0},
            "margin": 0.0,
            "confidence": 0.0,
            "reason": ("safe-area-floor-triplet-not-localized"
                       if vertical["layoutFamily"] == "centered-16x9-safe-area"
                       else "floor-triplet-not-localized"),
            "layout": "unlocalized",
            "centers": None,
            "layoutFamily": vertical["layoutFamily"],
            "safeTop": vertical["safeTop"],
            "safeHeight": vertical["safeHeight"],
        }
    scores: dict[int, float] = {}
    regions = FLOOR_BUTTON_REGIONS
    if centers is not None:
        regions = {
            floor: (
                center - 0.0175,
                vertical["regionTop"],
                0.035,
                vertical["regionHeight"],
            )
            for floor, center in zip((-1, 1, 2), centers)
        }
    for floor, (x, y, rw, rh) in regions.items():
        region = frame[int(h * y):math.ceil(h * (y + rh)), int(w * x):math.ceil(w * (x + rw))]
        if not region.size:
            scores[floor] = 0.0
            continue
        gray = cv2.cvtColor(region, cv2.COLOR_BGR2GRAY)
        # Clip sparse bright glyphs, signatures and cursor strokes; measure
        # the filled highlight background rather than its brightest outliers.
        scores[floor] = float(np.minimum(gray, np.percentile(gray, 75)).std())
    ordered = sorted(scores, key=scores.get, reverse=True)
    top, second = ordered[0], ordered[1]
    margin = scores[top] - scores[second]
    selected = top if scores[top] >= 25.0 and margin >= 8.0 else None
    return {
        "selectedFloor": selected,
        "scores": {str(key): value for key, value in scores.items()},
        "margin": margin,
        "confidence": min(1.0, max(0.0, margin / 25.0)) if selected else 0.0,
        "reason": "highlight-detected" if selected else "floor-controls-unavailable-or-ambiguous",
        "layout": layout,
        "centers": centers,
        "layoutFamily": vertical["layoutFamily"],
        "safeTop": vertical["safeTop"],
        "safeHeight": vertical["safeHeight"],
    }


@dataclass
class Reference:
    map_id: str
    map_name: str
    floor: int
    image: np.ndarray
    keypoints: list
    descriptors: np.ndarray | None


class ReferenceIndex:
    def __init__(self) -> None:
        manifest = json.loads((ASSETS / "maps" / "manifest.json").read_text(encoding="utf-8"))
        self.meta = {m["id"]: m for m in manifest["maps"] if not m.get("manualOnly")}
        self.sift = cv2.SIFT_create(nfeatures=4500, contrastThreshold=0.009, edgeThreshold=14)
        self.references: list[Reference] = []
        for map_id, meta in self.meta.items():
            full = cv2.imread(str(ASSETS / "maps" / f"{map_id}.webp"), cv2.IMREAD_COLOR)
            if full is None:
                continue
            for floor in (1, 2):
                image = self._floor_image(full, meta, floor)
                keypoints, descriptors = self.sift.detectAndCompute(enhance(image), None)
                self.references.append(Reference(map_id, meta["name"], floor, image, keypoints, descriptors))
        self.entrance_recognizer = SideDoorRecognizer(
            ASSETS / "maps" / "vision-index.json",
            ROOT / "outputs" / "人类道路分类索引.json",
            ROOT / "references" / "入口局部模板索引.webp",
        )

    @staticmethod
    def _floor_image(full: np.ndarray, meta: dict, floor: int) -> np.ndarray:
        h, _ = full.shape[:2]
        explicit = meta.get("floorBounds", {}).get(str(floor))
        if explicit:
            x, y = int(explicit["left"]), int(explicit["top"])
            part = full[y:y + int(explicit["height"]), x:x + int(explicit["width"])]
        elif meta.get("floorLayout") == "single-map":
            part = full
        else:
            part = full[:h // 2] if floor == 1 else full[h // 2:]
        return cv2.resize(part, (900, 750), interpolation=cv2.INTER_AREA)


def ratio_matches(a: np.ndarray | None, b: np.ndarray | None, ratio: float = 0.80) -> list:
    if a is None or b is None or len(a) < 2 or len(b) < 2:
        return []
    pairs = cv2.BFMatcher(cv2.NORM_L2).knnMatch(a, b, k=2)
    return [m for m, n in pairs if m.distance < ratio * n.distance]


def reprojection_errors(src: np.ndarray, dst: np.ndarray, matrix: np.ndarray) -> np.ndarray:
    projected = cv2.perspectiveTransform(src.reshape(-1, 1, 2).astype(np.float32), matrix).reshape(-1, 2)
    return np.linalg.norm(projected - dst, axis=1)


def matrix_sanity(matrix: np.ndarray, frame_shape: tuple[int, int], reference_shape: tuple[int, int]) -> dict:
    fh, fw = frame_shape
    rh, rw = reference_shape
    if matrix is None or matrix.shape != (3, 3) or not np.isfinite(matrix).all():
        return {"sane": False, "reason": "matrix-nonfinite"}
    linear = matrix[:2, :2]
    singular = np.linalg.svd(linear, compute_uv=False)
    if singular[-1] <= 1e-5 or singular[0] / singular[-1] > 2.2:
        return {"sane": False, "reason": "matrix-degenerate", "singularValues": singular.tolist()}
    corners = np.float32([[0, 0], [rw - 1, 0], [rw - 1, rh - 1], [0, rh - 1]]).reshape(-1, 1, 2)
    projected = cv2.perspectiveTransform(corners, matrix).reshape(-1, 2)
    if not np.isfinite(projected).all() or not cv2.isContourConvex(projected.astype(np.float32)):
        return {"sane": False, "reason": "projected-quad-invalid"}
    area = abs(float(cv2.contourArea(projected.astype(np.float32))))
    coverage = area / max(1.0, fw * fh)
    x0, y0 = projected.min(axis=0)
    x1, y1 = projected.max(axis=0)
    ix = max(0.0, min(float(fw), float(x1)) - max(0.0, float(x0)))
    iy = max(0.0, min(float(fh), float(y1)) - max(0.0, float(y0)))
    overlap = (ix * iy) / max(1.0, (x1 - x0) * (y1 - y0))
    sane = 0.005 <= coverage <= 0.65 and overlap >= 0.35 and 0.12 <= singular[-1] <= 8.0
    return {
        "sane": bool(sane),
        "reason": "ok" if sane else "coverage-or-scale",
        "coverage": coverage,
        "screenOverlap": overlap,
        "singularValues": singular.tolist(),
        "projectedCorners": projected.tolist(),
    }


class Recognizer:
    ROI = (0.28, 0.18, 0.58, 0.62)

    def __init__(self, index: ReferenceIndex) -> None:
        self.index = index

    def recognize(self, frame: np.ndarray) -> dict:
        entrance_evidence = self.index.entrance_recognizer.recognize(frame)
        h, w = frame.shape[:2]
        rx, ry, rw, rh = self.ROI
        x0, y0 = int(w * rx), int(h * ry)
        x1, y1 = int(w * (rx + rw)), int(h * (ry + rh))
        roi = frame[y0:y1, x0:x1]
        keypoints, descriptors = self.index.sift.detectAndCompute(enhance(roi), None)
        candidates = []
        for reference in self.index.references:
            good = ratio_matches(reference.descriptors, descriptors, 0.80)
            if len(good) < 6:
                continue
            src = np.float32([reference.keypoints[m.queryIdx].pt for m in good])
            dst = np.float32([keypoints[m.trainIdx].pt for m in good])
            models = []
            partial, partial_mask = cv2.estimateAffinePartial2D(src, dst, method=cv2.RANSAC, ransacReprojThreshold=4.0)
            if partial is not None and partial_mask is not None:
                models.append(("similarity", np.vstack([partial, [0, 0, 1]]), partial_mask.ravel().astype(bool)))
            affine, affine_mask = cv2.estimateAffine2D(src, dst, method=cv2.RANSAC, ransacReprojThreshold=4.0)
            if affine is not None and affine_mask is not None:
                models.append(("affine", np.vstack([affine, [0, 0, 1]]), affine_mask.ravel().astype(bool)))
            homography, homography_mask = cv2.findHomography(src, dst, cv2.RANSAC, 4.0)
            if homography is not None and homography_mask is not None:
                models.append(("homography", homography, homography_mask.ravel().astype(bool)))
            viable = []
            offset = np.array([[1, 0, x0], [0, 1, y0], [0, 0, 1]], dtype=np.float64)
            for model_name, local_matrix, keep in models:
                inliers = int(keep.sum())
                if inliers < 4:
                    continue
                errors = reprojection_errors(src[keep], dst[keep], local_matrix)
                screen_matrix = offset @ local_matrix
                sanity = matrix_sanity(screen_matrix, (h, w), reference.image.shape[:2])
                ratio = inliers / len(good)
                median = float(np.median(errors))
                p95 = float(np.percentile(errors, 95))
                complexity_penalty = {"similarity": 0.0, "affine": 1.0, "homography": 2.5}[model_name]
                score = inliers + 12.0 * ratio + 0.08 * len(good) - 0.8 * median - complexity_penalty
                viable.append({
                    "model": model_name, "matrix": screen_matrix, "inliers": inliers,
                    "inlierRatio": ratio, "goodMatches": len(good), "medianError": median,
                    "p95Error": p95, "sanity": sanity, "score": score,
                })
            if not viable:
                continue
            sane_models = [m for m in viable if m["sanity"]["sane"]]
            chosen = max(sane_models or viable, key=lambda m: m["score"])
            candidates.append({"reference": reference, **chosen})
        candidates.sort(key=lambda c: c["score"], reverse=True)
        if not candidates:
            if entrance_evidence.get("success"):
                return {
                    "success": False, "reason": "map-retrieved-awaiting-registration",
                    "mapId": entrance_evidence.get("mapId"),
                    "floor": entrance_evidence.get("floor"),
                    "confidence": entrance_evidence.get("confidence", 0.0),
                    "retrievalEvidence": entrance_evidence,
                    "roi": [x0, y0, x1 - x0, y1 - y0], "screenshotKeypoints": len(keypoints),
                }
            return {
                "success": False, "reason": "no-feature-model",
                "retrievalEvidence": entrance_evidence,
                "roi": [x0, y0, x1 - x0, y1 - y0], "screenshotKeypoints": len(keypoints),
            }
        top = candidates[0]
        second = candidates[1] if len(candidates) > 1 else None
        margin = top["score"] - (second["score"] if second else 0.0)
        confidence = (
            0.30 * min(top["inliers"] / 35.0, 1.0)
            + 0.25 * min(top["inlierRatio"] / 0.55, 1.0)
            + 0.20 * math.exp(-top["medianError"] / 4.0)
            + 0.15 * min(max(margin, 0.0) / 12.0, 1.0)
            + 0.10 * min(top["sanity"].get("coverage", 0.0) / 0.18, 1.0)
        )
        success = bool(
            top["sanity"]["sane"] and top["inliers"] >= 10 and top["inlierRatio"] >= 0.18
            and top["medianError"] <= 5.0 and top["p95Error"] <= 11.0 and confidence >= 0.58
        )
        ref = top.pop("reference")
        matrix = top.pop("matrix")
        public = {
            "success": success, "reason": "accepted" if success else "low-confidence-or-degenerate",
            "mapId": ref.map_id, "mapName": ref.map_name, "floor": ref.floor,
            "confidence": confidence, "margin": margin, "roi": [x0, y0, x1 - x0, y1 - y0],
            **top,
            "second": None if second is None else {
                "mapId": second["reference"].map_id, "floor": second["reference"].floor,
                "score": second["score"], "inliers": second["inliers"], "inlierRatio": second["inlierRatio"],
            },
            "matrixReferenceToClient": matrix.tolist(),
            "retrievalEvidence": entrance_evidence,
        }
        if entrance_evidence.get("success"):
            if entrance_evidence.get("mapId") != ref.map_id:
                public["success"] = False
                public["reason"] = "retrieval-evidence-disagreement"
            else:
                public["crossEvidenceAgreement"] = True
                public["confidence"] = min(0.99, confidence * 0.75 + float(entrance_evidence.get("confidence", 0.0)) * 0.25)
        public["_matrix"] = matrix
        public["_reference"] = ref
        return public


def prepare_layered_bgra(
    image: np.ndarray,
    opacity: float,
) -> tuple[np.ndarray | None, tuple[int, int, int, int] | None]:
    """Crop and premultiply a BGRA layer without full-frame coordinate arrays.

    ``np.where(alpha > 0)`` materializes two potentially million-element index
    arrays on a 2560x1440 route layer.  OpenCV can obtain the same non-zero
    bounding rectangle directly, and its channel multiply avoids the three
    large float32 temporaries previously created for every reopen/drag update.
    """
    if image is None or image.ndim != 3 or image.shape[2] != 4:
        return None, None
    alpha = image[:, :, 3]
    x, y, width, height = cv2.boundingRect(alpha)
    if width <= 0 or height <= 0:
        return None, None
    pad = 2
    x0 = max(0, int(x) - pad)
    y0 = max(0, int(y) - pad)
    x1 = min(image.shape[1], int(x + width) + pad)
    y1 = min(image.shape[0], int(y + height) + pad)
    crop = np.ascontiguousarray(image[y0:y1, x0:x1].copy())
    original_alpha = crop[:, :, 3].copy()
    effective_opacity = float(np.clip(opacity, 0.0, 1.0))
    # UpdateLayeredWindow expects premultiplied BGRA.  Multiplying each channel
    # in OpenCV is materially cheaper than promoting the whole crop to float32.
    for channel in range(3):
        crop[:, :, channel] = cv2.multiply(
            crop[:, :, channel], original_alpha,
            scale=effective_opacity / 255.0,
            dtype=cv2.CV_8U,
        )
    crop[:, :, 3] = cv2.convertScaleAbs(
        original_alpha, alpha=effective_opacity, beta=0.0,
    )
    return crop, (x0, y0, x1, y1)


def layered_surface_payload_is_unchanged(
    previous: np.ndarray | None,
    current: np.ndarray | None,
    previous_position: tuple[int, int] | None,
    position: tuple[int, int],
    previous_opacity: float | None,
    opacity: float,
    visible: bool,
) -> bool:
    """Return whether a visible native surface can be reused verbatim."""
    return bool(
        visible
        and isinstance(previous, np.ndarray)
        and isinstance(current, np.ndarray)
        and previous_position == tuple(position)
        and previous_opacity == float(opacity)
        and previous.shape == current.shape
        and np.array_equal(previous, current)
    )


class LayeredOverlay:
    WS_EX_LAYERED = 0x00080000
    WS_EX_TRANSPARENT = 0x00000020
    WS_EX_NOACTIVATE = 0x08000000
    WS_EX_TOOLWINDOW = 0x00000080
    WS_POPUP = 0x80000000
    ULW_ALPHA = 0x00000002
    AC_SRC_OVER = 0x00
    AC_SRC_ALPHA = 0x01
    SW_HIDE = 0
    SW_SHOWNOACTIVATE = 4
    WM_APP_HIDE = 0x8000 + 0x31
    HWND_TOPMOST = -1
    SWP_NOSIZE = 0x0001
    SWP_NOMOVE = 0x0002
    SWP_NOACTIVATE = 0x0010
    SWP_SHOWWINDOW = 0x0040
    DIB_RGB_COLORS = 0

    def __init__(self) -> None:
        self._owner_thread_id = threading.get_ident()
        self._diagnostic_lock = threading.Lock()
        self._diagnostic_events = deque(maxlen=128)
        self._wndproc = WNDPROC(self._wndproc_callback)
        self.class_name = f"IdentityVReadOnlyOverlay_{ctypes.windll.kernel32.GetCurrentProcessId()}_{uuid4().hex}"
        self._instance = ctypes.windll.kernel32.GetModuleHandleW(None)
        self._class_registered = False
        self.hwnd = None
        self.screen_position: tuple[int, int] | None = None
        self.window_size: tuple[int, int] | None = None
        self.client_offset: tuple[int, int] | None = None
        self.visible = False
        self.visibility_guard = None
        self._visibility_lock = threading.RLock()
        self._last_presented_bgra: np.ndarray | None = None
        self._last_presented_position: tuple[int, int] | None = None
        self._last_presented_opacity: float | None = None
        self.present_count = 0
        # Exclude this desktop-only overlay from our own screen capture where Windows supports it.
        # The overlay is hidden before recognition captures. Do not opt this large
        # layered window out of capture: some recorders turn its whole rectangle black.
        self.capture_excluded = False

        # Each class owns this instance's bound callback for its full native lifetime.
        wc = WNDCLASSEXW()
        wc.cbSize = ctypes.sizeof(WNDCLASSEXW)
        wc.lpfnWndProc = self._wndproc
        wc.hInstance = self._instance
        wc.lpszClassName = self.class_name
        if not user32.RegisterClassExW(ctypes.byref(wc)):
            raise ctypes.WinError()
        self._class_registered = True
        try:
            ex_style = self.WS_EX_LAYERED | self.WS_EX_TRANSPARENT | self.WS_EX_NOACTIVATE | self.WS_EX_TOOLWINDOW
            self.hwnd = user32.CreateWindowExW(ex_style, self.class_name, "", self.WS_POPUP, 0, 0, 1, 1, None, None, self._instance, None)
            if not self.hwnd:
                raise ctypes.WinError()
        except BaseException:
            self.close()
            raise

    def _record_diagnostic(self, event: str, **values) -> None:
        payload = {
            "event": str(event),
            "monotonicMs": round(time.monotonic() * 1000.0, 3),
            "threadId": int(threading.get_ident()),
            **values,
        }
        lock = getattr(self, "_diagnostic_lock", None)
        events = getattr(self, "_diagnostic_events", None)
        if lock is None or events is None:
            return
        with lock:
            events.append(payload)

    def drain_diagnostics(self) -> list[dict]:
        lock = getattr(self, "_diagnostic_lock", None)
        events = getattr(self, "_diagnostic_events", None)
        if lock is None or events is None:
            return []
        with lock:
            drained = list(events)
            events.clear()
        return drained

    @contextmanager
    def _visibility_guard(self, operation: str):
        started = time.perf_counter()
        self._visibility_lock.acquire()
        wait_ms = (time.perf_counter() - started) * 1000.0
        if wait_ms >= 2.0:
            self._record_diagnostic(
                "overlay-visibility-lock-wait",
                operation=str(operation),
                waitMs=round(wait_ms, 3),
            )
        try:
            yield
        finally:
            self._visibility_lock.release()

    def _record_native_call(
        self, operation: str, started: float, result: bool,
    ) -> None:
        elapsed_ms = (time.perf_counter() - started) * 1000.0
        if elapsed_ms >= 5.0:
            self._record_diagnostic(
                "overlay-native-call-slow",
                operation=str(operation),
                elapsedMs=round(elapsed_ms, 3),
                result=bool(result),
            )

    def _wndproc_callback(self, hwnd, msg, wp, lp):
        if int(msg) == int(self.WM_APP_HIDE):
            self._record_diagnostic(
                "overlay-hide-request-consumed",
                hwnd=int(hwnd),
            )
            self.hide()
            return 0
        return user32.DefWindowProcW(hwnd, msg, wp, lp)

    def hide(self) -> None:
        with self._visibility_guard("hide"):
            started = time.perf_counter()
            result = bool(user32.ShowWindow(self.hwnd, self.SW_HIDE))
            self._record_native_call("ShowWindow(SW_HIDE)", started, result)
            self.visible = False

    def request_hide(self) -> bool:
        """Request a hide without waiting on the owner thread's visibility lock.

        The global input listener uses this path at the physical close edge.
        It marks the surface logically hidden immediately and posts a private
        window message for the thread that owns the HWND. The owner already
        pumps its native queue from the UI loop, so the actual ShowWindow call
        remains serialized with restore/move/show_bgra without making the
        listener wait behind a potentially stuck native call.
        """
        if threading.get_ident() == int(getattr(self, "_owner_thread_id", 0)):
            self.hide()
            return True
        self.visible = False
        hwnd = getattr(self, "hwnd", None)
        if not hwnd:
            self._record_diagnostic(
                "overlay-hide-request-failed", reason="missing-hwnd",
            )
            return False
        try:
            posted = bool(user32.PostMessageW(
                hwnd, self.WM_APP_HIDE, None, None,
            ))
        except Exception as error:
            self._record_diagnostic(
                "overlay-hide-request-failed",
                reason=f"{type(error).__name__}: {error}",
            )
            return False
        self._record_diagnostic(
            "overlay-hide-requested",
            posted=posted,
            ownerThreadId=int(getattr(self, "_owner_thread_id", 0)),
        )
        return posted

    def _visibility_allowed(self) -> bool:
        guard = getattr(self, "visibility_guard", None)
        if not callable(guard):
            return True
        try:
            return bool(guard())
        except Exception:
            return False

    def restore(self, client_origin: tuple[int, int]) -> bool:
        """Show the already composited bitmap without rebuilding its DIB.

        ``ShowWindow(SW_HIDE)`` does not discard a layered window's surface.
        Reopening the same verified map layer can therefore be a compositor
        move/show operation instead of another alpha crop, DIB allocation and
        ``UpdateLayeredWindow`` call.
        """
        if (
            not self.hwnd
            or self.window_size is None
            or self.client_offset is None
        ):
            return False
        x = int(client_origin[0] + self.client_offset[0])
        y = int(client_origin[1] + self.client_offset[1])
        with self._visibility_guard("restore"):
            if not self._visibility_allowed():
                self.hide()
                return False
            started = time.perf_counter()
            ok = bool(user32.SetWindowPos(
                self.hwnd, self.HWND_TOPMOST, x, y, 0, 0,
                self.SWP_NOSIZE | self.SWP_NOACTIVATE | self.SWP_SHOWWINDOW,
            ))
            self._record_native_call("SetWindowPos(restore)", started, ok)
            if ok:
                if not self._visibility_allowed():
                    self.hide()
                    return False
                started = time.perf_counter()
                shown = bool(user32.ShowWindow(self.hwnd, self.SW_SHOWNOACTIVATE))
                self._record_native_call("ShowWindow(SW_SHOWNOACTIVATE)", started, shown)
                self.screen_position = (x, y)
                self._last_presented_position = (x, y)
                self.visible = True
            return ok

    def move_by(self, dx: float, dy: float) -> bool:
        """Move the already composited layer without rebuilding its bitmap.

        The game remains the mouse target because this window is permanently
        click-through.  During an in-game map drag this cheap compositor move
        mirrors the cursor; the expensive source warp is deferred until the
        drag has finished.
        """
        if (
            not self.hwnd or not self.visible
            or self.screen_position is None
            or not np.isfinite([dx, dy]).all()
        ):
            return False
        x = int(round(self.screen_position[0] + float(dx)))
        y = int(round(self.screen_position[1] + float(dy)))
        with self._visibility_guard("move_by"):
            if not self._visibility_allowed():
                self.hide()
                return False
            started = time.perf_counter()
            ok = bool(user32.SetWindowPos(
                self.hwnd, self.HWND_TOPMOST, x, y, 0, 0,
                self.SWP_NOSIZE | self.SWP_NOACTIVATE | self.SWP_SHOWWINDOW,
            ))
            self._record_native_call("SetWindowPos(move_by)", started, ok)
            if ok:
                if not self._visibility_allowed():
                    self.hide()
                    return False
                self.screen_position = (x, y)
                self._last_presented_position = (x, y)
                self.visible = True
            return ok

    def show_bgra(self, image: np.ndarray, client_origin: tuple[int, int], opacity: float = 0.88) -> bool:
        crop, crop_bounds = prepare_layered_bgra(image, opacity)
        if crop is None or crop_bounds is None:
            self.hide()
            return False
        x0, y0, _x1, _y1 = crop_bounds
        height, width = crop.shape[:2]
        effective_opacity = float(np.clip(opacity, 0.0, 1.0))
        destination_position = (
            int(client_origin[0] + x0), int(client_origin[1] + y0),
        )
        with self._visibility_guard("show_bgra-cache-check"):
            if not self._visibility_allowed():
                self.hide()
                return False
            if layered_surface_payload_is_unchanged(
                self._last_presented_bgra,
                crop,
                self._last_presented_position,
                destination_position,
                self._last_presented_opacity,
                effective_opacity,
                self.visible,
            ):
                # The compositor already owns this exact visible bitmap.
                # Reassert bookkeeping only; do not allocate a DIB or call
                # UpdateLayeredWindow again.
                self.screen_position = destination_position
                self.window_size = (int(width), int(height))
                self.client_offset = (int(x0), int(y0))
                return True

        screen_dc = user32.GetDC(None)
        memory_dc = gdi32.CreateCompatibleDC(screen_dc)
        info = BITMAPINFO()
        info.bmiHeader.biSize = ctypes.sizeof(BITMAPINFOHEADER)
        info.bmiHeader.biWidth = width
        info.bmiHeader.biHeight = -height
        info.bmiHeader.biPlanes = 1
        info.bmiHeader.biBitCount = 32
        info.bmiHeader.biCompression = 0
        bits = ctypes.c_void_p()
        bitmap = gdi32.CreateDIBSection(screen_dc, ctypes.byref(info), self.DIB_RGB_COLORS, ctypes.byref(bits), None, 0)
        if not bitmap:
            user32.ReleaseDC(None, screen_dc)
            gdi32.DeleteDC(memory_dc)
            return False
        old = gdi32.SelectObject(memory_dc, bitmap)
        ctypes.memmove(bits, crop.ctypes.data, crop.nbytes)
        destination = POINT(client_origin[0] + x0, client_origin[1] + y0)
        size = SIZE(width, height)
        source = POINT(0, 0)
        blend = BLENDFUNCTION(self.AC_SRC_OVER, 0, 255, self.AC_SRC_ALPHA)
        started = time.perf_counter()
        ok = bool(user32.UpdateLayeredWindow(self.hwnd, screen_dc, ctypes.byref(destination), ctypes.byref(size), memory_dc, ctypes.byref(source), 0, ctypes.byref(blend), self.ULW_ALPHA))
        self._record_native_call("UpdateLayeredWindow", started, ok)
        gdi32.SelectObject(memory_dc, old)
        gdi32.DeleteObject(bitmap)
        gdi32.DeleteDC(memory_dc)
        user32.ReleaseDC(None, screen_dc)
        if ok:
            with self._visibility_guard("show_bgra-commit"):
                if not self._visibility_allowed():
                    self.hide()
                    return False
                self.screen_position = destination_position
                self.window_size = (int(width), int(height))
                self.client_offset = (int(x0), int(y0))
                self._last_presented_bgra = crop
                self._last_presented_position = destination_position
                self._last_presented_opacity = effective_opacity
                self.present_count += 1
                self.visible = True
                if not self._visibility_allowed():
                    self.hide()
                    return False
                started = time.perf_counter()
                moved = bool(user32.SetWindowPos(self.hwnd, self.HWND_TOPMOST, 0, 0, 0, 0, self.SWP_NOMOVE | self.SWP_NOSIZE | self.SWP_NOACTIVATE | self.SWP_SHOWWINDOW))
                self._record_native_call("SetWindowPos(show_bgra)", started, moved)
                # A close edge may arrive while the native call is running.
                # Never re-show a surface that was just suppressed/hidden.
                if not self._visibility_allowed():
                    self.hide()
                    return False
                started = time.perf_counter()
                shown = bool(user32.ShowWindow(self.hwnd, self.SW_SHOWNOACTIVATE)
                )
                self._record_native_call("ShowWindow(SW_SHOWNOACTIVATE)", started, shown)
                if not self._visibility_allowed():
                    self.hide()
                    return False
        return ok

    def pump(self) -> None:
        msg = MSG()
        while user32.PeekMessageW(ctypes.byref(msg), None, 0, 0, 1):
            user32.TranslateMessage(ctypes.byref(msg))
            user32.DispatchMessageW(ctypes.byref(msg))

    def close(self) -> None:
        with self._visibility_guard("close"):
            if self.hwnd:
                self.hide()
                if not user32.DestroyWindow(self.hwnd):
                    raise ctypes.WinError()
                self.hwnd = None
            self._last_presented_bgra = None
            self._last_presented_position = None
            self._last_presented_opacity = None
            if self._class_registered:
                if not user32.UnregisterClassW(self.class_name, self._instance):
                    raise ctypes.WinError()
                self._class_registered = False


def json_safe(value):
    if isinstance(value, np.generic):
        return value.item()
    if isinstance(value, np.ndarray):
        return value.tolist()
    if isinstance(value, dict):
        return {k: json_safe(v) for k, v in value.items() if not str(k).startswith("_")}
    if isinstance(value, (list, tuple)):
        return [json_safe(v) for v in value]
    return value


def serializable(result: dict) -> dict:
    return json_safe(result)


def render_result(frame: np.ndarray, result: dict, show_candidate: bool = False) -> tuple[np.ndarray | None, np.ndarray]:
    preview = frame.copy()
    if not result.get("mapId"):
        return None, preview
    h, w = frame.shape[:2]
    matrix = result.get("_matrix")
    route_path = ROUTE_ROOT / result["mapId"] / f"floor-{result['floor']}.png"
    route = imread_unicode(route_path, cv2.IMREAD_UNCHANGED)
    if route is None or route.shape[2] != 4 or matrix is None:
        return None, preview
    warped = cv2.warpPerspective(route, matrix, (w, h), flags=cv2.INTER_LINEAR, borderMode=cv2.BORDER_CONSTANT)
    accepted = bool(result.get("success"))
    if accepted or show_candidate:
        alpha = warped[:, :, 3:4].astype(np.float32) / 255.0 * 0.90
        preview = np.clip(preview.astype(np.float32) * (1 - alpha) + warped[:, :, :3].astype(np.float32) * alpha, 0, 255).astype(np.uint8)
    label = f"{result.get('mapId')} F{result.get('floor')} {result.get('model')} conf={result.get('confidence', 0):.3f} {'SHOW' if accepted else 'HIDDEN'}"
    cv2.rectangle(preview, (20, 20), (min(w - 20, 940), 72), (20, 20, 20), -1)
    cv2.putText(preview, label, (34, 57), cv2.FONT_HERSHEY_SIMPLEX, 0.82, (80, 230, 80) if accepted else (40, 90, 240), 2, cv2.LINE_AA)
    return warped if accepted else None, preview


def append_event(path: Path, payload: dict) -> None:
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(payload, ensure_ascii=False) + "\n")


def run(args: argparse.Namespace) -> None:
    LIVE.mkdir(parents=True, exist_ok=True)
    LIVE_OUTPUTS.mkdir(parents=True, exist_ok=True)
    hwnd, pid, title = find_game_window()
    frame, bounds = capture_client(hwnd)
    index = ReferenceIndex()
    recognizer = Recognizer(index)
    overlay = None if args.no_overlay else LayeredOverlay()
    session = datetime.now().strftime("%Y%m%d-%H%M%S")
    events_path = LIVE_OUTPUTS / f"live-events-{session}.jsonl"
    status_path = LIVE / "latest_status.json"
    event = {
        "time": now_iso(), "event": "session-start", "hwnd": hwnd, "pid": pid, "title": title,
        "clientBounds": bounds, "captureSize": [frame.shape[1], frame.shape[0]],
        "overlayEnabled": overlay is not None, "overlayCaptureExcluded": getattr(overlay, "capture_excluded", False),
        "safety": "read-only capture; no SendInput; no process memory; no injection; no render hook",
    }
    append_event(events_path, event)
    print(json.dumps(event, ensure_ascii=False), flush=True)

    raw_open_count = 0
    raw_closed_count = 0
    stable_open = False
    stable_candidate: tuple[str, int] | None = None
    candidate_count = 0
    accepted: dict | None = None
    last_reason = None
    last_recognition = 0.0
    start = time.monotonic()
    frame_index = 0
    try:
        while args.duration <= 0 or time.monotonic() - start < args.duration:
            loop_start = time.monotonic()
            frame, bounds = capture_client(hwnd)
            frame_index += 1
            evidence = detect_map_interface(frame)
            raw_open = evidence["detected"]
            raw_open_count = raw_open_count + 1 if raw_open else 0
            raw_closed_count = raw_closed_count + 1 if not raw_open else 0
            if not stable_open and raw_open_count >= 3:
                stable_open = True
                accepted = None
                stable_candidate = None
                candidate_count = 0
            if stable_open and raw_closed_count >= 2:
                stable_open = False
                accepted = None
                stable_candidate = None
                candidate_count = 0
                if overlay:
                    overlay.hide()

            result: dict = {"success": False, "reason": "map-closed"}
            if stable_open and time.monotonic() - last_recognition >= args.recognition_interval:
                last_recognition = time.monotonic()
                result = recognizer.recognize(frame)
                key = (result.get("mapId"), result.get("floor")) if result.get("mapId") else None
                if result.get("success") and key == stable_candidate:
                    candidate_count += 1
                elif result.get("success"):
                    stable_candidate = key
                    candidate_count = 1
                else:
                    stable_candidate = None
                    candidate_count = 0
                if candidate_count >= 2:
                    accepted = result
                elif not result.get("success"):
                    accepted = None

            active = accepted if stable_open and accepted else result
            warped, preview = render_result(frame, active)
            visible = bool(stable_open and accepted and warped is not None)
            if overlay:
                if visible:
                    overlay.show_bgra(warped, (bounds[0], bounds[1]))
                else:
                    overlay.hide()
                overlay.pump()

            reason = "route-visible" if visible else (
                "map-open-recognition-pending" if stable_open and active.get("reason") == "map-closed" else active.get("reason", "map-closed")
            )
            status = {
                "time": now_iso(), "session": session, "frame": frame_index, "hwnd": hwnd, "pid": pid,
                "clientBounds": bounds, "rawMapOpen": raw_open, "stableMapOpen": stable_open,
                "routeVisible": visible, "reason": reason, "mapEvidence": evidence,
                "recognition": serializable(active),
            }
            status_path.write_text(json.dumps(status, ensure_ascii=False, indent=2), encoding="utf-8")
            if reason != last_reason:
                append_event(events_path, {"event": "state-change", **status})
                print(json.dumps({"time": status["time"], "state": reason, "mapOpen": stable_open, "recognition": serializable(active)}, ensure_ascii=False), flush=True)
                imwrite_unicode(LIVE_OUTPUTS / f"{session}-{frame_index:05d}-{reason}.png", preview)
                last_reason = reason

            if args.once:
                break
            remaining = 1.0 / max(args.fps, 0.2) - (time.monotonic() - loop_start)
            if remaining > 0:
                time.sleep(remaining)
    finally:
        if overlay:
            overlay.close()
        append_event(events_path, {"time": now_iso(), "event": "session-stop", "frames": frame_index})
        print(json.dumps({"event": "session-stop", "frames": frame_index, "events": str(events_path)}, ensure_ascii=False), flush=True)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="第五人格只读实时地图覆盖MVP")
    parser.add_argument("--duration", type=float, default=600.0, help="运行秒数；0表示持续运行")
    parser.add_argument("--fps", type=float, default=4.0)
    parser.add_argument("--recognition-interval", type=float, default=0.8)
    parser.add_argument("--no-overlay", action="store_true", help="只监测，不显示桌面悬浮层")
    parser.add_argument("--once", action="store_true", help="只处理一帧")
    return parser.parse_args()


if __name__ == "__main__":
    run(parse_args())
