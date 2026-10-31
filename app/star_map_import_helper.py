"""Separate helper process: bind parent handle, wait for normal exit, import, receipt.

Freeze this file as a DIFFERENT executable, star-map-import-helper.exe. Never
dispatch it inside the main application's mapped executable. No GUI/restart/kill.
"""
from __future__ import annotations

import argparse
import ctypes
from ctypes import wintypes
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import sys
import tempfile
import subprocess
import time

from star_map_import import ImportRejected, _check_closed, _no_links, _target, install_package
from packaged_product_identity import expected_application_executable


def _kernel():
    if os.name != "nt":
        raise ImportRejected("windows-helper-required")
    api = ctypes.WinDLL("kernel32", use_last_error=True)
    api.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    api.OpenProcess.restype = wintypes.HANDLE
    api.CloseHandle.argtypes = [wintypes.HANDLE]
    api.QueryFullProcessImageNameW.argtypes = [wintypes.HANDLE, wintypes.DWORD, wintypes.LPWSTR,
                                               ctypes.POINTER(wintypes.DWORD)]
    api.GetProcessTimes.argtypes = [wintypes.HANDLE, *([ctypes.POINTER(wintypes.FILETIME)] * 4)]
    api.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
    api.GetProcessId.argtypes = [wintypes.HANDLE]
    api.GetProcessId.restype = wintypes.DWORD
    api.GetExitCodeProcess.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
    api.SetHandleInformation.argtypes = [wintypes.HANDLE, wintypes.DWORD, wintypes.DWORD]
    return api


def _creation_time(api, handle):
    times = [wintypes.FILETIME() for _ in range(4)]
    if not api.GetProcessTimes(handle, *(ctypes.byref(value) for value in times)):
        raise ctypes.WinError(ctypes.get_last_error())
    return str((times[0].dwHighDateTime << 32) | times[0].dwLowDateTime)


def _identity(api, handle, pid):
    created = _creation_time(api, handle)
    text = ctypes.create_unicode_buffer(32768)
    length = wintypes.DWORD(len(text))
    if not api.QueryFullProcessImageNameW(handle, 0, text, ctypes.byref(length)):
        raise ctypes.WinError(ctypes.get_last_error())
    return {"pid": pid, "creationTime": created, "executable": text.value}


def application_process_identity(pid=None):
    """Main captures this while alive; creationTime is Windows FILETIME decimal text."""
    pid = os.getpid() if pid is None else pid
    api = _kernel()
    handle = api.OpenProcess(0x1000, False, pid)  # QUERY_LIMITED_INFORMATION only
    if not handle:
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        return _identity(api, handle, pid)
    finally:
        api.CloseHandle(handle)


def wait_for_application_exit(pid, creation_time, executable, timeout_seconds, *, parent_handle=None,
                              parent_executable=None, cancel_path=None):
    if type(pid) is not int or pid <= 0 or not re.fullmatch(r"[0-9]{1,20}", str(creation_time)):
        raise ImportRejected("invalid-parent-identity")
    if not 1 <= timeout_seconds <= 300:
        raise ImportRejected("invalid-wait-deadline")
    api = _kernel()
    # This one handle binds identity and wait to the SAME process object, even
    # if its numeric PID is reused after the original process terminates.
    handle = parent_handle or api.OpenProcess(0x100000 | 0x1000, False, pid)
    if not handle:
        raise ImportRejected("parent-handle-unavailable; cannot prove normal exit")
    try:
        if api.GetProcessId(handle) != pid:
            raise ImportRejected("parent-handle-pid-mismatch")
        if _creation_time(api, handle) != str(creation_time):
            raise ImportRejected("parent-creation-time-mismatch")
        # QueryFullProcessImageNameW can fail with ERROR_GEN_FAILURE after exit.
        # For an inherited handle, launch_import_helper already captured and
        # checked the image while alive; PID and creation time are still queried
        # from that retained real handle here, not from a potentially reused PID.
        actual_executable = (parent_executable if parent_handle else _identity(api, handle, pid)["executable"])
        if not actual_executable or os.path.normcase(os.path.realpath(actual_executable)) != os.path.normcase(os.path.realpath(executable)):
            raise ImportRejected("parent-executable-mismatch")
        deadline = time.monotonic() + timeout_seconds
        while True:
            if cancel_path is not None:
                _no_links(cancel_path)
                if cancel_path.exists():
                    raise ImportRejected("import-request-cancelled")
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise ImportRejected("parent-exit-timeout; close was cancelled or did not finish")
            waited = api.WaitForSingleObject(handle, min(250, max(1, int(remaining * 1000))))
            if waited == 0:
                break
            if waited != 258:
                raise ctypes.WinError(ctypes.get_last_error())
        exit_code = wintypes.DWORD()
        if not api.GetExitCodeProcess(handle, ctypes.byref(exit_code)):
            raise ctypes.WinError(ctypes.get_last_error())
        if exit_code.value != 0:
            raise ImportRejected(f"parent-exit-not-normal: {exit_code.value}")
        return "exited-matching-process-handle"
    finally:
        api.CloseHandle(handle)


def receipt_path(install_root):
    return _target(Path(os.path.abspath(install_root)), "work/map-packages/last-import-result.json")


def _cancel_path(root, request_id):
    if not re.fullmatch(r"[A-Za-z0-9-]{1,64}", request_id):
        raise ImportRejected("invalid-request-id")
    return _target(Path(os.path.abspath(root)), f"work/map-packages/cancel-{request_id}")


def cancel_helper_request(install_root, request_id):
    """Main calls only if its normal close is cancelled after helper launch."""
    path = _cancel_path(install_root, request_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.touch(exist_ok=True)


def launch_import_helper(helper_path, archive, install_root, archive_sha256, request_id, *, wait_seconds=120):
    """Main calls after confirmation; start a hidden DIFFERENT EXE before close.

    The inherited real parent handle survives a fast parent exit. Main should
    retain the returned Popen for failure reporting, then execute its old close.
    No shell, no shutdown/restart here. Missing helper raises before closing.
    """
    if not re.fullmatch(r"[0-9a-f]{64}", archive_sha256):
        raise ImportRejected("invalid-helper-request")
    return _launch_parent_bound_helper(helper_path, install_root, request_id,
        ["--archive", os.path.abspath(archive), "--archive-sha256", archive_sha256],
        wait_seconds=wait_seconds)


def _launch_parent_bound_helper(helper_path, install_root, request_id, request_args, *, wait_seconds=120):
    """Shared strict launch: different EXE, retained parent object, no shell/close."""
    helper = Path(helper_path).resolve()
    root = Path(os.path.abspath(install_root))
    _no_links(helper)
    _no_links(root)
    if not helper.is_file() or helper.suffix.lower() != ".exe":
        raise ImportRejected("separate-helper-not-installed")
    if os.path.normcase(str(helper)) == os.path.normcase(os.path.realpath(sys.executable)):
        raise ImportRejected("helper-must-use-separate-executable")
    if not 1 <= wait_seconds <= 300:
        raise ImportRejected("invalid-helper-request")
    if _cancel_path(root, request_id).exists():
        raise ImportRejected("request-id-already-cancelled; choose a fresh id")
    api = _kernel()
    handle = api.OpenProcess(0x100000 | 0x1000, False, os.getpid())
    if not handle:
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        identity = _identity(api, handle, os.getpid())
        expected_exe = _target(root, expected_application_executable())
        if os.path.normcase(os.path.realpath(identity["executable"])) != os.path.normcase(str(expected_exe)):
            raise ImportRejected("launcher-is-not-installed-application")
        if not api.SetHandleInformation(handle, 1, 1):
            raise ctypes.WinError(ctypes.get_last_error())
        startup = subprocess.STARTUPINFO()
        startup.lpAttributeList = {"handle_list": [int(handle)]}
        argv = [str(helper), *request_args, "--install-root", str(root),
                "--parent-pid", str(identity["pid"]), "--parent-created", identity["creationTime"],
                "--parent-executable", identity["executable"],
                "--parent-handle", str(int(handle)),
                "--request-id", request_id, "--wait-seconds", str(wait_seconds)]
        return subprocess.Popen(argv, startupinfo=startup, close_fds=True,
                                creationflags=subprocess.CREATE_NO_WINDOW)
    finally:
        api.CloseHandle(handle)


def _write_receipt(root, result, *, path=None):
    path = receipt_path(root) if path is None else Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    _no_links(path.parent)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent,
                                         prefix="helper-result-", suffix=".json", delete=False) as handle:
            temporary = Path(handle.name)
            json.dump(result, handle, ensure_ascii=False, indent=2)
        _no_links(path)
        os.replace(temporary, path)
    finally:
        if temporary is not None and temporary.exists():
            temporary.unlink()


def run_helper(*, archive, install_root, parent_pid, parent_created, archive_sha256,
               request_id, wait_seconds=120, parent_handle=None, parent_executable=None):
    """Returns (exitCode, receipt); no caller boolean can bypass process verification."""
    if not re.fullmatch(r"[0-9a-f]{64}", archive_sha256):
        raise ImportRejected("invalid-archive-sha256")
    if not re.fullmatch(r"[A-Za-z0-9-]{1,64}", request_id):
        raise ImportRejected("invalid-request-id")
    root = Path(os.path.abspath(install_root))
    _no_links(root)
    expected_exe = _target(root, expected_application_executable())
    result = {"requestId": request_id, "archiveSha256": archive_sha256,
              "success": False, "restartRequired": False,
              "completedAtUTC": None, "scope": "offline-resource-import; not game/player acceptance"}
    try:
        if os.path.normcase(os.path.realpath(sys.executable)) == os.path.normcase(str(expected_exe)):
            raise ImportRejected("helper-must-use-separate-executable")
        result["parentExit"] = wait_for_application_exit(parent_pid, parent_created, expected_exe, wait_seconds,
            parent_handle=parent_handle, parent_executable=parent_executable,
            cancel_path=_cancel_path(root, request_id))
        if _cancel_path(root, request_id).exists():
            raise ImportRejected("import-request-cancelled")
        _check_closed(root)  # Also rejects a second running instance of this installed EXE.
        imported = install_package(archive, root, application_stopped=True,
                                   expected_archive_sha256=archive_sha256)
        result.update({k: v for k, v in imported.items() if k != "files"})
        result["success"] = True
        code = 0
    except Exception as error:
        result["errorCode"] = type(error).__name__
        result["reason"] = str(error)
        code = 2
    result["completedAtUTC"] = datetime.now(timezone.utc).isoformat()
    # If this write fails, main must show 'result unavailable', never success.
    _write_receipt(root, result)
    return code, result


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--archive", required=True)
    parser.add_argument("--install-root", required=True)
    parser.add_argument("--parent-pid", type=int, required=True)
    parser.add_argument("--parent-created", required=True)
    parser.add_argument("--parent-handle", type=int)
    parser.add_argument("--parent-executable")
    parser.add_argument("--archive-sha256", required=True)
    parser.add_argument("--request-id", required=True)
    parser.add_argument("--wait-seconds", type=int, default=120)
    args = parser.parse_args()
    try:
        code, receipt = run_helper(**vars(args))
        print(json.dumps(receipt, ensure_ascii=False))
        raise SystemExit(code)
    except (ImportRejected, OSError) as error:
        print(json.dumps({"success": False, "reason": str(error), "receiptWritten": False}, ensure_ascii=False))
        raise SystemExit(2)
