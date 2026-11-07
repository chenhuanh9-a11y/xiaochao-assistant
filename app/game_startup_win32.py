"""Small read-only Win32 adapter, imported only by the ordinary-user watcher."""
import ctypes
from ctypes import wintypes as W
import ntpath
import os
from pathlib import Path

from game_startup import MAIN_NAME, WATCHER_NAME, title_allowed


class ProcessEntry(ctypes.Structure):
    _fields_ = [("dwSize", W.DWORD), ("cntUsage", W.DWORD), ("th32ProcessID", W.DWORD),
                ("th32DefaultHeapID", ctypes.c_size_t), ("th32ModuleID", W.DWORD),
                ("cntThreads", W.DWORD), ("th32ParentProcessID", W.DWORD),
                ("pcPriClassBase", W.LONG), ("dwFlags", W.DWORD), ("szExeFile", W.WCHAR * 260)]


class Windows:
    def __init__(self):
        self.kernel = k = ctypes.WinDLL("kernel32", use_last_error=True)
        self.user = u = ctypes.WinDLL("user32", use_last_error=True)
        self.shell = s = ctypes.WinDLL("shell32", use_last_error=True)
        k.OpenProcess.argtypes = [W.DWORD, W.BOOL, W.DWORD]
        k.OpenProcess.restype = W.HANDLE
        k.CloseHandle.argtypes = [W.HANDLE]
        k.ProcessIdToSessionId.argtypes = [W.DWORD, ctypes.POINTER(W.DWORD)]
        k.ProcessIdToSessionId.restype = W.BOOL
        k.QueryFullProcessImageNameW.argtypes = [W.HANDLE, W.DWORD, W.LPWSTR, ctypes.POINTER(W.DWORD)]
        k.QueryFullProcessImageNameW.restype = W.BOOL
        k.GetProcessTimes.argtypes = [W.HANDLE] + [ctypes.POINTER(W.FILETIME)] * 4
        k.GetProcessTimes.restype = W.BOOL
        k.CreateToolhelp32Snapshot.argtypes = [W.DWORD, W.DWORD]
        k.CreateToolhelp32Snapshot.restype = W.HANDLE
        for name in ("Process32FirstW", "Process32NextW"):
            getattr(k, name).argtypes = [W.HANDLE, ctypes.POINTER(ProcessEntry)]
            getattr(k, name).restype = W.BOOL
        k.CreateMutexW.argtypes = [ctypes.c_void_p, W.BOOL, W.LPCWSTR]
        k.CreateMutexW.restype = W.HANDLE
        u.IsWindowVisible.argtypes = [W.HWND]
        u.IsWindowVisible.restype = W.BOOL
        u.GetWindowTextW.argtypes = [W.HWND, W.LPWSTR, ctypes.c_int]
        u.GetWindowTextW.restype = ctypes.c_int
        u.GetWindowThreadProcessId.argtypes = [W.HWND, ctypes.POINTER(W.DWORD)]
        self.callback_type = ctypes.WINFUNCTYPE(W.BOOL, W.HWND, W.LPARAM)
        u.EnumWindows.argtypes = [self.callback_type, W.LPARAM]
        u.EnumWindows.restype = W.BOOL
        s.ShellExecuteW.argtypes = [W.HWND, W.LPCWSTR, W.LPCWSTR, W.LPCWSTR, W.LPCWSTR, ctypes.c_int]
        s.ShellExecuteW.restype = ctypes.c_ssize_t
        s.IsUserAnAdmin.restype = W.BOOL
        self.session = self.session_for(os.getpid())

    def session_for(self, pid):
        session = W.DWORD()
        if not self.kernel.ProcessIdToSessionId(pid, ctypes.byref(session)):
            raise ctypes.WinError(ctypes.get_last_error())
        return session.value

    def process(self, pid):
        handle = self.kernel.OpenProcess(0x1000, False, pid)
        if not handle:
            raise ctypes.WinError(ctypes.get_last_error())
        try:
            image = ctypes.create_unicode_buffer(32768)
            size = W.DWORD(len(image))
            times = [W.FILETIME() for _ in range(4)]
            if not self.kernel.QueryFullProcessImageNameW(handle, 0, image, ctypes.byref(size)):
                raise ctypes.WinError(ctypes.get_last_error())
            if not self.kernel.GetProcessTimes(handle, *(ctypes.byref(t) for t in times)):
                raise ctypes.WinError(ctypes.get_last_error())
            return image.value, (times[0].dwHighDateTime << 32) | times[0].dwLowDateTime
        finally:
            self.kernel.CloseHandle(handle)

    def games(self):
        games = set()
        @self.callback_type
        def callback(hwnd, unused):
            try:
                if not self.user.IsWindowVisible(hwnd):
                    return True
                title = ctypes.create_unicode_buffer(1025)
                self.user.GetWindowTextW(hwnd, title, len(title))
                if not title_allowed(title.value):
                    return True
                pid = W.DWORD()
                self.user.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
                if self.session_for(pid.value) != self.session:
                    return True
                image, created = self.process(pid.value)
                if ntpath.basename(image).casefold() == "dwrg.exe":
                    games.add((pid.value, created))
            except OSError:
                pass  # Unreadable is not a game identity.
            return True
        if not self.user.EnumWindows(callback, 0):
            raise ctypes.WinError(ctypes.get_last_error())
        return sorted(games)

    def main_state(self, directory):
        expected = ntpath.normcase(str(Path(directory) / MAIN_NAME))
        snapshot = self.kernel.CreateToolhelp32Snapshot(2, 0)
        if snapshot == ctypes.c_void_p(-1).value:
            return "unknown"
        unknown = False
        try:
            entry = ProcessEntry()
            entry.dwSize = ctypes.sizeof(entry)
            more = self.kernel.Process32FirstW(snapshot, ctypes.byref(entry))
            if not more:
                return "unknown"
            while more:
                if entry.szExeFile.casefold() == MAIN_NAME.casefold():
                    try:
                        if self.session_for(entry.th32ProcessID) == self.session:
                            image, _ = self.process(entry.th32ProcessID)
                            if ntpath.normcase(image) == expected:
                                return "present"
                    except OSError:
                        unknown = True
                more = self.kernel.Process32NextW(snapshot, ctypes.byref(entry))
            if ctypes.get_last_error() != 18:  # ERROR_NO_MORE_FILES
                return "unknown"
            return "unknown" if unknown else "absent"
        finally:
            self.kernel.CloseHandle(snapshot)

    def launch(self, directory):
        # Supported Shell/UAC path. No task, token manipulation or elevation bypass.
        result = self.shell.ShellExecuteW(None, "runas", str(Path(directory) / MAIN_NAME),
                                         None, str(directory), 1)
        return "requested" if result > 32 else "cancelled-or-failed"

    def watcher_state(self, directory):
        """Check exact mapped helper paths, including other Windows sessions.

        Never kill processes. Other users are not modified; an unreadable or
        still-running helper blocks replacement. Exclude this control command's
        own PyInstaller parent bootloader as well as its Python child.
        """
        expected = ntpath.normcase(str(Path(directory) / WATCHER_NAME))
        snapshot = self.kernel.CreateToolhelp32Snapshot(2, 0)
        if snapshot == ctypes.c_void_p(-1).value:
            return "unknown"
        rows = []
        try:
            entry = ProcessEntry()
            entry.dwSize = ctypes.sizeof(entry)
            more = self.kernel.Process32FirstW(snapshot, ctypes.byref(entry))
            if not more:
                return "unknown"
            while more:
                rows.append((int(entry.th32ProcessID), int(entry.th32ParentProcessID), entry.szExeFile))
                more = self.kernel.Process32NextW(snapshot, ctypes.byref(entry))
            if ctypes.get_last_error() != 18:
                return "unknown"
        finally:
            self.kernel.CloseHandle(snapshot)
        return helper_process_state(rows, expected, os.getpid(), self.process)

    def acquire(self, name):
        ctypes.set_last_error(0)
        handle = self.kernel.CreateMutexW(None, False, "Local\\" + name)
        if not handle:
            raise ctypes.WinError(ctypes.get_last_error())
        if ctypes.get_last_error() == 183:
            self.kernel.CloseHandle(handle)
            return None
        return handle

    def interactive_user_matches(self):
        """Do not confuse credential-prompt administrator HKCU with the desktop user."""
        self.user.GetShellWindow.restype = W.HWND
        hwnd = self.user.GetShellWindow()
        if not hwnd:
            return False
        pid = W.DWORD()
        self.user.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        return self._user_sid(pid.value) == self._user_sid(os.getpid())

    def _user_sid(self, pid):
        advapi = ctypes.WinDLL("advapi32", use_last_error=True)
        advapi.OpenProcessToken.argtypes = [W.HANDLE, W.DWORD, ctypes.POINTER(W.HANDLE)]
        advapi.OpenProcessToken.restype = W.BOOL
        advapi.GetTokenInformation.argtypes = [W.HANDLE, ctypes.c_int, ctypes.c_void_p,
                                               W.DWORD, ctypes.POINTER(W.DWORD)]
        advapi.GetTokenInformation.restype = W.BOOL
        advapi.ConvertSidToStringSidW.argtypes = [ctypes.c_void_p, ctypes.POINTER(W.LPWSTR)]
        advapi.ConvertSidToStringSidW.restype = W.BOOL
        self.kernel.LocalFree.argtypes = [ctypes.c_void_p]
        self.kernel.LocalFree.restype = ctypes.c_void_p
        process = self.kernel.OpenProcess(0x1000, False, pid)
        if not process:
            raise ctypes.WinError(ctypes.get_last_error())
        token = W.HANDLE()
        try:
            if not advapi.OpenProcessToken(process, 8, ctypes.byref(token)):
                raise ctypes.WinError(ctypes.get_last_error())
            needed = W.DWORD()
            advapi.GetTokenInformation(token, 1, None, 0, ctypes.byref(needed))
            if not needed.value or needed.value > 65536:
                raise OSError("Token user unavailable")
            buffer = ctypes.create_string_buffer(needed.value)
            if not advapi.GetTokenInformation(token, 1, buffer, needed, ctypes.byref(needed)):
                raise ctypes.WinError(ctypes.get_last_error())
            sid = ctypes.cast(buffer, ctypes.POINTER(ctypes.c_void_p))[0]
            text = W.LPWSTR()
            if not advapi.ConvertSidToStringSidW(sid, ctypes.byref(text)):
                raise ctypes.WinError(ctypes.get_last_error())
            try:
                return text.value
            finally:
                self.kernel.LocalFree(text)
        finally:
            if token:
                self.kernel.CloseHandle(token)
            self.kernel.CloseHandle(process)

    def episode_alive(self, key):
        try:
            pid, created = (int(value) for value in key.split(":"))
            _, current_created = self.process(pid)
            return current_created == created
        except OSError as error:
            # ERROR_INVALID_PARAMETER is documented for a nonexistent PID;
            # access denied and all other failures retain the reservation.
            return False if getattr(error, "winerror", None) == 87 else None
        except (ValueError, TypeError):
            return None


def helper_process_state(rows, expected, current_pid, inspect):
    """Pure enumeration policy, independently tested with fake process metadata."""
    excluded = {current_pid}
    parent = next((parent for pid, parent, _ in rows if pid == current_pid), None)
    if parent is not None and any(pid == parent and name.casefold() == WATCHER_NAME.casefold()
                                  for pid, _, name in rows):
        try:
            if ntpath.normcase(inspect(parent)[0]) == expected:
                excluded.add(parent)
        except OSError:
            return "unknown"
    unknown = False
    for pid, _, name in rows:
        if pid in excluded or name.casefold() != WATCHER_NAME.casefold():
            continue
        try:
            if ntpath.normcase(inspect(pid)[0]) == expected:
                return "present"
        except OSError as error:
            if getattr(error, "winerror", None) != 87:
                unknown = True
    return "unknown" if unknown else "absent"
