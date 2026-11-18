"""Close only this admitted Xingtu installation's background processes."""
from __future__ import annotations

import ctypes
import ntpath
import os
import stat
import time
from ctypes import wintypes as wt
from pathlib import Path

from game_startup import MAIN_NAME, WATCHER_NAME


# This is the recognition worker emitted by build_qt_client.py's managed
# inventory at _internal/csharp-grid/StarMap.Grid.exe. The Python main process
# owns its normal shutdown protocol; this helper only waits for it to exit.
GRID_PATH = "_internal/csharp-grid/StarMap.Grid.exe"
PROCESS_IMAGES = {
    MAIN_NAME: (MAIN_NAME, "main"),
    WATCHER_NAME: (WATCHER_NAME, "watcher"),
    "StarMap.Grid.exe": (GRID_PATH, "recognition-helper"),
}

GRACEFUL_WAIT_SECONDS = 4.0
TERMINATE_WAIT_SECONDS = 2.0
WAIT_OBJECT_0 = 0
WAIT_TIMEOUT = 0x102
ERROR_NO_MORE_FILES = 18
ERROR_INVALID_PARAMETER = 87
PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
SYNCHRONIZE = 0x00100000
PROCESS_TERMINATE = 0x0001
TOKEN_QUERY = 0x0008
TOKEN_USER = 1
TOKEN_SESSION_ID = 12
WM_CLOSE = 0x0010
GW_OWNER = 4


def _path_key(path):
    return ntpath.normcase(ntpath.normpath(str(path)))


def _image_key(path):
    """Normalize DOS short-name spelling for exact image-path comparisons."""
    value = ntpath.normpath(str(path))
    if os.name == "nt":
        # QueryFullProcessImageNameW can preserve the 8.3 spelling used to
        # launch an image; QtIFW admission supplies the long spelling. Resolve
        # only DOS aliases here, leaving installation-directory link checks
        # on the lexical _path_key path.
        api = ctypes.WinDLL("kernel32", use_last_error=True).GetLongPathNameW
        api.argtypes = [wt.LPCWSTR, wt.LPWSTR, wt.DWORD]
        api.restype = wt.DWORD
        buffer = ctypes.create_unicode_buffer(32768)
        length = api(value, buffer, len(buffer))
        if 0 < length < len(buffer):
            value = buffer.value
        else:
            parent, leaf = ntpath.split(value)
            if parent and parent != value and leaf:
                value = ntpath.join(_image_key(parent), leaf)
    return _path_key(value)


def _exception_reason(prefix, error):
    detail = " ".join(str(error).split())
    if not detail:
        detail = type(error).__name__
    return prefix + ":" + detail[:320]


def _manifest_keys(policy):
    files = policy.get("files") if isinstance(policy, dict) else None
    if not isinstance(files, dict):
        raise ValueError("managed-file-inventory-unavailable")
    return {str(name).replace("\\", "/").casefold() for name in files}


def _admitted_process_images(policy, target):
    """Repeat native admission and expose only executable names in that inventory."""
    from qtifw_upgrade import _old_install, check, pending

    try:
        admission = check(policy, target)
    except Exception as error:
        return None, None, _exception_reason("target-admission-failed", error)
    if not isinstance(admission, dict) or admission.get("mode") != "update":
        mode = admission.get("mode") if isinstance(admission, dict) else None
        return None, None, "target-not-admitted" + (":" + str(mode) if mode else "")
    try:
        root = Path(admission["target"]).resolve(strict=True)
        program = root / "program"
        info = program.lstat()
        if (not stat.S_ISDIR(info.st_mode) or stat.S_ISLNK(info.st_mode)
                or getattr(info, "st_file_attributes", 0) & 0x400
                or _path_key(program.resolve(strict=True)) != _path_key(program)):
            raise ValueError("program-directory-not-ordinary")
        recovery = pending(policy, root)
        if recovery is None:
            previous, _, _ = _old_install(policy, root)
            managed = _manifest_keys(previous)
        else:
            previous = recovery.get("previousPolicy")
            managed = _manifest_keys(previous) if recovery.get("stage") in {"backing-up", "prepared"} \
                else _manifest_keys(previous) | _manifest_keys(policy)
        if not isinstance(previous, dict):
            raise ValueError("previous-install-inventory-unavailable")
        previous_files = previous.get("files")
        current_files = policy.get("files") if isinstance(policy, dict) else None
        if not isinstance(previous_files, dict) or not isinstance(current_files, dict):
            raise ValueError("managed-file-inventory-unavailable")
        images = {}
        for image_name, (relative, role) in PROCESS_IMAGES.items():
            if relative.casefold() not in managed:
                continue
            image_path = program.joinpath(*relative.split("/"))
            images[_image_key(image_path)] = dict(name=image_name, role=role, path=str(image_path))
        if _image_key(program / MAIN_NAME) not in images:
            raise ValueError("managed-main-image-unavailable")
        return root, images, None
    except Exception as error:
        return None, None, _exception_reason("target-admission-failed", error)


class _WindowsProcesses:
    """Win32 process operations that retain each validated process handle."""

    def __init__(self, force):
        if os.name != "nt":
            raise OSError("windows-only")
        from game_startup_win32 import ProcessEntry, Windows

        self.windows = Windows()
        self.kernel = self.windows.kernel
        self.user = self.windows.user
        self.ProcessEntry = ProcessEntry
        self.kernel.OpenProcess.argtypes = [wt.DWORD, wt.BOOL, wt.DWORD]
        self.kernel.OpenProcess.restype = wt.HANDLE
        self.kernel.WaitForSingleObject.argtypes = [wt.HANDLE, wt.DWORD]
        self.kernel.WaitForSingleObject.restype = wt.DWORD
        self.kernel.GetProcessTimes.argtypes = [wt.HANDLE] + [ctypes.POINTER(wt.FILETIME)] * 4
        self.kernel.GetProcessTimes.restype = wt.BOOL
        self.kernel.TerminateProcess.argtypes = [wt.HANDLE, wt.UINT]
        self.kernel.TerminateProcess.restype = wt.BOOL
        self.kernel.GetCurrentProcess.restype = wt.HANDLE
        self.user.GetWindow.argtypes = [wt.HWND, wt.UINT]
        self.user.GetWindow.restype = wt.HWND
        self.user.PostMessageW.argtypes = [wt.HWND, wt.UINT, ctypes.c_size_t, ctypes.c_ssize_t]
        self.user.PostMessageW.restype = wt.BOOL
        self.advapi = ctypes.WinDLL("advapi32", use_last_error=True)
        self.advapi.OpenProcessToken.argtypes = [wt.HANDLE, wt.DWORD, ctypes.POINTER(wt.HANDLE)]
        self.advapi.OpenProcessToken.restype = wt.BOOL
        self.advapi.GetTokenInformation.argtypes = [wt.HANDLE, ctypes.c_int, ctypes.c_void_p,
                                                    wt.DWORD, ctypes.POINTER(wt.DWORD)]
        self.advapi.GetTokenInformation.restype = wt.BOOL
        self.advapi.ConvertSidToStringSidW.argtypes = [ctypes.c_void_p, ctypes.POINTER(wt.LPWSTR)]
        self.advapi.ConvertSidToStringSidW.restype = wt.BOOL
        self.kernel.LocalFree.argtypes = [ctypes.c_void_p]
        self.kernel.LocalFree.restype = ctypes.c_void_p
        self.access = PROCESS_QUERY_LIMITED_INFORMATION | SYNCHRONIZE
        self.force = force
        self.current_sid, self.current_session = self._token_identity(self.kernel.GetCurrentProcess())

    def _token_identity(self, process):
        token = wt.HANDLE()
        if not self.advapi.OpenProcessToken(process, TOKEN_QUERY, ctypes.byref(token)):
            raise ctypes.WinError(ctypes.get_last_error())
        try:
            session = wt.DWORD()
            needed = wt.DWORD()
            if not self.advapi.GetTokenInformation(token, TOKEN_SESSION_ID, ctypes.byref(session),
                                                   ctypes.sizeof(session), ctypes.byref(needed)):
                raise ctypes.WinError(ctypes.get_last_error())
            needed = wt.DWORD()
            self.advapi.GetTokenInformation(token, TOKEN_USER, None, 0, ctypes.byref(needed))
            if not 0 < needed.value <= 65536:
                raise OSError("process-user-token-unavailable")
            data = ctypes.create_string_buffer(needed.value)
            if not self.advapi.GetTokenInformation(token, TOKEN_USER, data, needed,
                                                   ctypes.byref(needed)):
                raise ctypes.WinError(ctypes.get_last_error())
            sid = ctypes.cast(data, ctypes.POINTER(ctypes.c_void_p)).contents.value
            text = wt.LPWSTR()
            if not self.advapi.ConvertSidToStringSidW(sid, ctypes.byref(text)):
                raise ctypes.WinError(ctypes.get_last_error())
            try:
                return text.value, int(session.value)
            finally:
                self.kernel.LocalFree(text)
        finally:
            self.kernel.CloseHandle(token)

    def _image(self, process):
        image = ctypes.create_unicode_buffer(32768)
        size = wt.DWORD(len(image))
        if not self.kernel.QueryFullProcessImageNameW(process, 0, image, ctypes.byref(size)):
            raise ctypes.WinError(ctypes.get_last_error())
        return image.value

    def _created(self, process):
        times = [wt.FILETIME() for _ in range(4)]
        if not self.kernel.GetProcessTimes(process, *(ctypes.byref(value) for value in times)):
            raise ctypes.WinError(ctypes.get_last_error())
        return (times[0].dwHighDateTime << 32) | times[0].dwLowDateTime

    def _snapshot(self):
        snapshot = self.kernel.CreateToolhelp32Snapshot(2, 0)
        if snapshot == ctypes.c_void_p(-1).value:
            raise ctypes.WinError(ctypes.get_last_error())
        rows = []
        try:
            entry = self.ProcessEntry()
            entry.dwSize = ctypes.sizeof(entry)
            ctypes.set_last_error(0)
            more = self.kernel.Process32FirstW(snapshot, ctypes.byref(entry))
            if not more:
                error = ctypes.get_last_error()
                if error == ERROR_NO_MORE_FILES:
                    return rows
                raise ctypes.WinError(error)
            while more:
                rows.append((int(entry.th32ProcessID), str(entry.szExeFile)))
                ctypes.set_last_error(0)
                more = self.kernel.Process32NextW(snapshot, ctypes.byref(entry))
                if not more:
                    error = ctypes.get_last_error()
                    if error != ERROR_NO_MORE_FILES:
                        raise ctypes.WinError(error)
        finally:
            self.kernel.CloseHandle(snapshot)
        return rows

    def identify(self, expected_images):
        self._scan_handles = []
        try:
            return self._identify(expected_images)
        except BaseException:
            for row in self._scan_handles:
                self.close(row)
            raise

    def _identify(self, expected_images):
        """Return validated held handles and explicit unreadable/foreign candidates."""
        by_name = {ntpath.basename(path).casefold() for path in expected_images}
        records, issues = [], []
        for pid, image_name in self._snapshot():
            if image_name.casefold() not in by_name:
                continue
            handle = self.kernel.OpenProcess(self.access, False, pid)
            if not handle:
                error = ctypes.get_last_error()
                if error == ERROR_INVALID_PARAMETER:  # The snapshot entry exited.
                    continue
                issues.append(dict(pid=pid, executable=image_name, role="unverified",
                                   reason="process-identity-unreadable"))
                continue
            record = dict(pid=pid, executable=image_name, role="unverified", path=None,
                          handle=handle, terminate_handle=None, targetable=False,
                          close_sent=False, close_failed=False)
            self._scan_handles.append(record)
            try:
                if self.wait_state(handle) is False:
                    self.close(record)
                    continue
                actual_path = self._image(handle)
                expected = expected_images.get(_image_key(actual_path))
                if expected is None:
                    self.close(record)  # Same basename in another install is not ours.
                    continue
                created = self._created(handle)
                record.update(executable=expected["name"], role=expected["role"],
                              path=actual_path, created=created)
                sid, session = self._token_identity(handle)
                if sid != self.current_sid or session != self.current_session:
                    record["reason"] = "different-user-or-session"
                    records.append(record)
                    continue
                record["targetable"] = True
                if self.force:
                    terminate_handle = self.kernel.OpenProcess(
                        self.access | PROCESS_TERMINATE, False, pid)
                    if not terminate_handle:
                        if self.wait_state(handle) is False:
                            self.close(record)
                            continue
                        record["reason"] = "terminate-handle-unavailable"
                    else:
                        try:
                            same_process = (self._created(terminate_handle) == created
                                            and _image_key(self._image(terminate_handle)) == _image_key(actual_path)
                                            and self._token_identity(terminate_handle)
                                            == (self.current_sid, self.current_session))
                        except OSError:
                            same_process = False
                        if same_process:
                            record["terminate_handle"] = terminate_handle
                        else:
                            self.kernel.CloseHandle(terminate_handle)
                            record["reason"] = "terminate-handle-identity-mismatch"
                records.append(record)
            except OSError:
                if self.wait_state(handle) is False:
                    self.close(record)
                else:
                    record["reason"] = "process-identity-unreadable"
                    issues.append({key: record[key] for key in ("pid", "executable", "role", "reason")})
                    records.append(record)
        return records, issues

    def merge_rescan(self, records, issues, fresh_records, fresh_issues):
        """Retain late arrivals as blockers, without signaling or terminating them."""
        known_processes = {(row["pid"], row.get("created")) for row in records}
        known_issues = {(row.get("pid"), str(row.get("executable", "")).casefold())
                        for row in issues}
        late_found = False
        for row in fresh_records:
            key = (row["pid"], row.get("created"))
            if key in known_processes:
                self.close(row)
                continue
            row.update(targetable=False, late=True, reason="late-process-detected")
            records.append(row)
            known_processes.add(key)
            late_found = True
        for row in fresh_issues:
            key = (row.get("pid"), str(row.get("executable", "")).casefold())
            if key in known_issues:
                continue
            issues.append(dict(row, reason="late-process-identity-unreadable"))
            known_issues.add(key)
            late_found = True
        return late_found

    def wait_state(self, handle):
        result = self.kernel.WaitForSingleObject(handle, 0)
        if result == WAIT_OBJECT_0:
            return False
        if result == WAIT_TIMEOUT:
            return True
        return None

    def close(self, record):
        for key in ("terminate_handle", "handle"):
            handle = record.get(key)
            if handle:
                self.kernel.CloseHandle(handle)
                record[key] = None

    def send_main_close(self, records):
        main_pids = {row["pid"] for row in records
                     if row.get("targetable") and row["role"] == "main"}
        if not main_pids:
            return
        failures = set()

        @self.windows.callback_type
        def callback(hwnd, unused):
            pid = wt.DWORD()
            self.user.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
            if pid.value not in main_pids:
                return True
            # The main window may be hidden to the tray. Exact image path,
            # validated process handle, owning PID and an unowned top-level
            # window identify it without relying on visibility or title text.
            if self.user.GetWindow(hwnd, GW_OWNER):
                return True
            ctypes.set_last_error(0)
            if not self.user.PostMessageW(hwnd, WM_CLOSE, 0, 0):
                failures.add(pid.value)
            else:
                for row in records:
                    if row["pid"] == pid.value:
                        row["close_sent"] = True
            return True

        ctypes.set_last_error(0)
        if not self.user.EnumWindows(callback, 0):
            raise ctypes.WinError(ctypes.get_last_error())
        for row in records:
            if row.get("targetable") and row["role"] == "main" and row["pid"] in failures:
                row["close_failed"] = True

    def wait_for_exit(self, records, seconds):
        deadline = time.monotonic() + max(0.0, seconds)
        while True:
            alive = [row for row in records if row.get("targetable") and row.get("handle")
                     and self.wait_state(row["handle"]) is not False]
            if not alive or time.monotonic() >= deadline:
                return
            time.sleep(min(0.05, max(0.0, deadline - time.monotonic())))

    def terminate_validated(self, records):
        for row in records:
            if not row.get("targetable") or not row.get("handle"):
                continue
            if self.wait_state(row["handle"]) is False:
                continue
            terminate_handle = row.get("terminate_handle")
            if not terminate_handle:
                row["reason"] = row.get("reason") or "terminate-handle-unavailable"
            elif not self.kernel.TerminateProcess(terminate_handle, 1):
                row["reason"] = "terminate-failed"

    def remaining(self, records, issues):
        result = list(issues)
        for row in records:
            if row.get("late"):
                result.append(dict(pid=row["pid"], executable=row["executable"], role=row["role"],
                                   path=row.get("path"), reason="late-process-detected",
                                   running=self.wait_state(row["handle"]) is not False))
                continue
            if row.get("handle") and self.wait_state(row["handle"]) is False:
                continue
            if row.get("reason") == "process-identity-unreadable":
                # Already reported as an issue above.
                continue
            reason = row.get("reason")
            if row.get("targetable"):
                reason = reason or ("close-request-failed" if row.get("close_failed") else
                                    "graceful-close-timeout" if row["role"] == "main" else
                                    "awaiting-main-shutdown" if row["role"] == "recognition-helper" else
                                    "watcher-still-running")
            result.append(dict(pid=row["pid"], executable=row["executable"], role=row["role"],
                               path=row.get("path"), reason=reason or "process-state-unknown"))
        return result


def close_background(policy, target, force=False):
    """Close this admitted installation's app, watcher and recognition worker.

    force=True is reserved for the caller's explicit user-confirmation path.
    This function never searches or terminates by executable name alone.
    """
    if type(force) is not bool:
        return dict(success=False, remaining=[], reason="force-must-be-boolean", matchedCount=0)
    root, expected_images, admission_error = _admitted_process_images(policy, target)
    if admission_error:
        return dict(success=False, remaining=[], reason=admission_error, matchedCount=0)
    try:
        processes = _WindowsProcesses(force)
    except Exception as error:
        return dict(success=False, remaining=[], reason=_exception_reason(
            "windows-process-api-unavailable", error), matchedCount=0)

    records, issues = [], []
    rescan_problem = None
    late_found = False
    watcher_problem = None
    watcher_code = None
    watcher_was_enabled = None
    try:
        try:
            records, issues = processes.identify(expected_images)
        except Exception as error:
            return dict(success=False, remaining=[], reason=_exception_reason(
                "process-enumeration-failed", error), matchedCount=0)

        watcher_path = next((row["path"] for row in expected_images.values()
                             if row["role"] == "watcher"), None)
        watcher_candidate = any(
            row["role"] == "watcher"
            or (row["role"] == "unverified" and row["executable"].casefold() == WATCHER_NAME.casefold())
            for row in records)
        if watcher_path and (Path(watcher_path).is_file() or watcher_candidate):
            try:
                from game_startup_watcher import control
                program = root / "program"
                status_code = control("--status", program, processes.windows)
                if status_code not in (0, 10):
                    watcher_problem = "watcher-status-failed:" + str(status_code)
                else:
                    watcher_was_enabled = status_code == 10
                    watcher_code = control("--disable", program, processes.windows)
                    if watcher_code != 0:
                        watcher_problem = "watcher-disable-failed:" + str(watcher_code)
            except Exception as error:
                watcher_problem = _exception_reason("watcher-control-failed", error)
        try:
            processes.send_main_close(records)
        except Exception:
            for row in records:
                if row.get("targetable") and row["role"] == "main":
                    row["close_failed"] = True
        processes.wait_for_exit(records, GRACEFUL_WAIT_SECONDS)
        if force:
            processes.terminate_validated(records)
            processes.wait_for_exit(records, TERMINATE_WAIT_SECONDS)
        initial_remaining = processes.remaining(records, issues)
        if (force and watcher_code == 6
                and not any(ntpath.basename(str(row["executable"])).casefold() == WATCHER_NAME.casefold()
                            for row in initial_remaining)):
            # --disable removed the Run value but hit its bounded watcher wait;
            # explicit force has now stopped the exact held watcher handle.
            watcher_problem = None
        if watcher_was_enabled:
            # --disable also removes the opt-in Run value. Restore that
            # registration after the close attempt; this command does not
            # launch the watcher, and lets the native update record the user's
            # original opt-in in its recovery marker. Failed attempts retain it.
            try:
                from game_startup_watcher import control
                if control("--restore-after-upgrade", root / "program", processes.windows) != 0:
                    watcher_problem = watcher_problem or "watcher-registration-restore-failed"
            except Exception as error:
                watcher_problem = watcher_problem or _exception_reason(
                    "watcher-registration-restore-failed", error)
        try:
            fresh_records, fresh_issues = processes.identify(expected_images)
            late_found = processes.merge_rescan(records, issues, fresh_records, fresh_issues)
        except Exception as error:
            rescan_problem = _exception_reason("process-rescan-failed", error)
            # identify() closes any handles it acquired before failing.
        remaining = processes.remaining(records, issues)
        if watcher_problem:
            reason = watcher_problem
        elif rescan_problem:
            reason = rescan_problem
        elif issues:
            reason = "process-identity-incomplete"
        elif late_found:
            reason = "late-process-detected"
        elif remaining:
            reason = "processes-still-running"
        else:
            reason = ""
        matched = len({(row["pid"], row.get("created")) for row in records
                       if row.get("targetable") and not row.get("late")})
        return dict(success=not watcher_problem and not rescan_problem and not late_found and not remaining,
                    remaining=remaining, reason=reason, matchedCount=matched)
    finally:
        for row in records:
            processes.close(row)
