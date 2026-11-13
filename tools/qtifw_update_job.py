"""Launch and observe one native update helper without waiting for its work."""
from __future__ import annotations

import ctypes
from ctypes import wintypes
import os
from pathlib import Path
import subprocess
import sys


def process_state(pid, *, handle=None):
    """Use the exact Windows process creation time, never an executable name."""
    if os.name != "nt":
        raise ValueError("native-update-process-query-requires-windows")
    api = ctypes.WinDLL("kernel32", use_last_error=True)
    api.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    api.OpenProcess.restype = wintypes.HANDLE
    api.GetProcessTimes.argtypes = [wintypes.HANDLE, *([ctypes.POINTER(wintypes.FILETIME)] * 4)]
    api.GetProcessTimes.restype = wintypes.BOOL
    api.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
    api.WaitForSingleObject.restype = wintypes.DWORD
    api.CloseHandle.argtypes = [wintypes.HANDLE]
    api.CloseHandle.restype = wintypes.BOOL
    owned = handle is None
    if owned:
        handle = api.OpenProcess(0x1000 | 0x100000, False, int(pid))
        if not handle:
            error = ctypes.get_last_error()
            if error == 87:  # The process has already exited.
                return dict(running=False, created="")
            raise OSError(error, "native-update-process-query-failed")
    try:
        times = [wintypes.FILETIME() for _ in range(4)]
        if not api.GetProcessTimes(handle, *(ctypes.byref(value) for value in times)):
            raise ctypes.WinError(ctypes.get_last_error())
        waited = api.WaitForSingleObject(handle, 0)
        if waited not in (0, 258):
            raise ctypes.WinError(ctypes.get_last_error())
        # FILETIME exceeds JavaScript's exact integer range; keep it a string.
        created = str((times[0].dwHighDateTime << 32) | times[0].dwLowDateTime)
        return dict(running=waited == 258, created=created)
    finally:
        if owned:
            api.CloseHandle(handle)


def context(policy, target, result_file, *, pid=None, handle=None):
    value = dict(instance=policy["instance"], target=str(Path(target).resolve()),
                 resultFile=str(Path(result_file).resolve()))
    if pid is not None:
        value.update(pid=pid, created=process_state(pid, handle=handle)["created"])
    return value


def start(policy, target, result_file, *, setup_source, interactive=False):
    from resource_transaction import _safe
    result_file = _safe(Path(result_file).resolve())
    support = Path(sys.executable).parent if getattr(sys, "frozen", False) else Path(__file__).parent
    if result_file.parent != support.resolve() or result_file.exists():
        raise ValueError("native-update-result-location-unavailable")
    command = [sys.executable]
    if not getattr(sys, "frozen", False):
        command += ["-B", str(Path(__file__).with_name("qtifw_trial_helper.py"))]
    command += ["bootstrap-run", str(target), "--result-file", str(result_file),
                "--setup-source", str(setup_source)]
    if interactive:
        command.append("--interactive")
    # The child retains the existing resource leases, native maintenance/UNDO
    # database, recovery record and final verification in qtifw_upgrade.run().
    from qtifw_upgrade import _native_start
    try:
        child = _native_start(command, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                              stderr=subprocess.DEVNULL, creationflags=subprocess.CREATE_NO_WINDOW)
    except OSError as error:
        return dict(started=False, reason="native-update-launch-failed:" + str(error))
    job = context(policy, target, result_file, pid=child.pid, handle=int(child._handle))
    return dict(started=True, **job)


def status(pid, created):
    state = process_state(pid)
    return dict(running=state["running"] and state["created"] == str(created))


def publish(policy, target, result_file, outcome):
    from qtifw_upgrade import _write
    # The reader sees no file until the full verified result is published.
    _write(Path(result_file), dict(outcome, job=context(policy, target, result_file)))
