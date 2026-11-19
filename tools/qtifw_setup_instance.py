"""Retain a product-wide Windows instance handle in the native QtIFW parent."""
import ctypes
from ctypes import wintypes as W
import hashlib
import os
from pathlib import Path
import struct
import time


def hold(policy, executable):
    from installer_resource_lease import _kernel
    from game_startup_win32 import Windows
    from qtifw_update_job import process_state

    api = _kernel()
    windows = Windows()
    pid = os.getppid()
    parent = api.OpenProcess(0x1000 | 0x100000 | 0x40, False, pid)
    if not parent:
        raise ctypes.WinError(ctypes.get_last_error())
    mapping = view = None
    try:
        image, created = windows.process(pid)
        if (Path(image).resolve() != Path(executable).resolve()
                or not process_state(pid, handle=parent)["running"]):
            raise ValueError("setup-instance-parent-mismatch")
        cache_name = policy.get("localCacheDir")
        if cache_name:
            # QtIFW acquires its cache before loading Controller. Let that
            # exact live owner win product arbitration as well: two concurrent
            # copies must not choose opposite winners for these two resources.
            owner = _cache_owner(api, Path(os.environ["LOCALAPPDATA"]) / "cache" / cache_name / "cache.lock")
            if owner and owner != pid:
                state = process_state(owner)
                if state["running"]:
                    return dict(primary=False, activated=_activate(windows, owner))
        # Production copies/renames/versions share one namespace. Isolated
        # fixtures deliberately cannot take ownership of a real Setup.
        product = "xingtu-production" if policy.get("portable") else policy["instance"]
        identity = str(windows._user_sid(pid)) + ":" + str(windows.session) + ":" + product
        name = "Local\\XingtuSetup-" + hashlib.sha256(identity.encode()).hexdigest()[:32]
        api.CreateFileMappingW.argtypes = [W.HANDLE, W.LPVOID, W.DWORD, W.DWORD, W.DWORD, W.LPCWSTR]
        api.CreateFileMappingW.restype = W.HANDLE
        api.MapViewOfFile.argtypes = [W.HANDLE, W.DWORD, W.DWORD, W.DWORD, ctypes.c_size_t]
        api.MapViewOfFile.restype = W.LPVOID
        api.UnmapViewOfFile.argtypes = [W.LPCVOID]
        api.UnmapViewOfFile.restype = W.BOOL
        ctypes.set_last_error(0)
        mapping = api.CreateFileMappingW(ctypes.c_void_p(-1), None, 4, 0, 16, name)
        existed = ctypes.get_last_error() == 183
        if not mapping:
            raise ctypes.WinError(ctypes.get_last_error())
        view = api.MapViewOfFile(mapping, 0xF001F, 0, 0, 16)
        if not view:
            raise ctypes.WinError(ctypes.get_last_error())
        if existed:
            first_pid, first_created = struct.unpack("<QQ", ctypes.string_at(view, 16))
            if first_pid == pid and first_created == created:
                return dict(primary=True)
            activated = False
            try:
                state = process_state(first_pid)
                if state["running"] and state["created"] == str(first_created):
                    activated = _activate(windows, first_pid)
            except OSError:
                pass
            return dict(primary=False, activated=activated)
        ctypes.memmove(view, struct.pack("<QQ", pid, created), 16)
        remote = W.HANDLE()
        if not api.DuplicateHandle(api.GetCurrentProcess(), mapping, parent,
                                   ctypes.byref(remote), 0, False, 2):
            raise ctypes.WinError(ctypes.get_last_error())
        return dict(primary=True)
    finally:
        if view:
            api.UnmapViewOfFile(view)
        if mapping:
            api.CloseHandle(mapping)
        api.CloseHandle(parent)


def _cache_owner(api, path):
    """Read QtIFW's locked ASCII PID through a read-only mapped file view.

    Windows byte-range locks block ReadFile, but deliberately do not apply to
    mapped views. Nothing here writes, unlocks or removes QtIFW's cache file.
    """
    api.CreateFileMappingW.argtypes = [W.HANDLE, W.LPVOID, W.DWORD, W.DWORD, W.DWORD, W.LPCWSTR]
    api.CreateFileMappingW.restype = W.HANDLE
    api.MapViewOfFile.argtypes = [W.HANDLE, W.DWORD, W.DWORD, W.DWORD, ctypes.c_size_t]
    api.MapViewOfFile.restype = W.LPVOID
    api.UnmapViewOfFile.argtypes = [W.LPCVOID]
    api.GetFileSizeEx.argtypes = [W.HANDLE, ctypes.POINTER(ctypes.c_longlong)]
    api.GetFileSizeEx.restype = W.BOOL
    file = api.CreateFileW(str(path), 0x80000000, 7, None, 3, 0, None)
    if file in (None, ctypes.c_void_p(-1).value):
        error = ctypes.get_last_error()
        if error in (2, 3):
            return None
        raise ctypes.WinError(error)
    mapping = view = None
    try:
        size = ctypes.c_longlong()
        if not api.GetFileSizeEx(file, ctypes.byref(size)):
            raise ctypes.WinError(ctypes.get_last_error())
        deadline = time.monotonic() + 2
        while size.value == 0 and time.monotonic() < deadline:
            time.sleep(.02)  # The winning SDK may still be flushing its PID.
            if not api.GetFileSizeEx(file, ctypes.byref(size)):
                raise ctypes.WinError(ctypes.get_last_error())
        if not 0 < size.value <= 16:
            raise ValueError("setup-cache-owner-invalid")
        mapping = api.CreateFileMappingW(file, None, 2, 0, 0, None)
        if not mapping:
            raise ctypes.WinError(ctypes.get_last_error())
        view = api.MapViewOfFile(mapping, 4, 0, 0, size.value)
        if not view:
            raise ctypes.WinError(ctypes.get_last_error())
        value = ctypes.string_at(view, size.value).strip()
        if not value.isdigit() or int(value) <= 0:
            raise ValueError("setup-cache-owner-invalid")
        return int(value)
    finally:
        if view:
            api.UnmapViewOfFile(view)
        if mapping:
            api.CloseHandle(mapping)
        api.CloseHandle(file)


def _activate(windows, pid):
    user = windows.user
    user.ShowWindow.argtypes = [W.HWND, ctypes.c_int]
    user.SetForegroundWindow.argtypes = [W.HWND]
    found = []

    @windows.callback_type
    def visit(hwnd, unused):
        owner = W.DWORD()
        user.GetWindowThreadProcessId(hwnd, ctypes.byref(owner))
        if owner.value == pid and user.IsWindowVisible(hwnd):
            user.ShowWindow(hwnd, 9)  # restore a minimized installer
            found.append(bool(user.SetForegroundWindow(hwnd)))
        return True

    user.EnumWindows(visit, 0)
    return any(found)
