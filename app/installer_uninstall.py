"""Fail-closed, full per-install cleanup before Inno's native uninstall.

The helper is copied with its baked installation profile to a temporary
runtime. It validates that profile against the installation and native Inno
registration, preflights every app-root entry, then removes only named product
namespaces and the current production user's imported-resource root. User data
inside those owned roots is intentionally cleared even when its bytes changed.
"""
from __future__ import annotations

import argparse
import configparser
from contextlib import ExitStack
import ctypes
from ctypes import wintypes as wt
import json
import ntpath
import os
from pathlib import Path
import re
import stat
import sys
import time

from packaged_product_identity import PRODUCTION_APP_ID, PRODUCTION_PRODUCT, _ISOLATED


_UNINSTALL_KEY = r"Software\Microsoft\Windows\CurrentVersion\Uninstall"
_RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
_EXIT_KEY = r"Software\Xingtu\GameStartup"
_APP_EXE = "星图.exe"
_OWNERSHIP = "_installer-owned-files.txt"
_MAX_PROFILE = 128 * 1024
_MAX_OWNERSHIP = 4 * 1024 * 1024
_MAX_RESULT_TEXT = 256 * 1024
_ATTR_REPARSE = 0x400
_ATTR_DIRECTORY = 0x10
_GENERIC_READ = 0x80000000
_GENERIC_WRITE = 0x40000000
_DELETE = 0x00010000
_READ_CONTROL = 0x00020000
_FILE_READ_ATTRIBUTES = 0x80
_FILE_SHARE_READ = 0x1
_FILE_SHARE_WRITE = 0x2
_OPEN_EXISTING = 3
_FILE_FLAG_OPEN_REPARSE_POINT = 0x00200000
_FILE_FLAG_BACKUP_SEMANTICS = 0x02000000
_FILE_DISPOSITION_INFO = 4
_INVALID_HANDLE_VALUE = ctypes.c_void_p(-1).value
_KNOWN_PRIVATE_FOLDERS = {
    "client": "XingtuQtClientTest",
    "layout-preview": "XingtuQtLayoutPreview",
    "s0-s1-test": "XingtuS0S1PrivateTest",
    "s1-floor-test": "XingtuS1FloorPrivateTest",
    "s1-auto-test": "XingtuS1AutoPrivateTest",
    "s1-playtest": "XingtuS1Playtest",
    "feature-test": "XingtuFeatureTest",
}
_KNOWN_PRIVATE_EXES = {key: "XingtuQtClientTest.exe" for key in _KNOWN_PRIVATE_FOLDERS}
_PRODUCTION_FOLDER = "XiaochaoAssistant"
_APP_METADATA = frozenset({
    "installation-profile.json", "version.json", "release-report.json",
    _OWNERSHIP, "_uninstall-helper-files.txt", "star-map-uninstall-helper.exe",
    ".xingtu-uninstall-failed.txt", "ui-settings.json", "custom-galleries.json",
    "qt-update-state.json", "qt-tutorial-state.json",
})
_RETRY_FILES = frozenset({
    'installation-profile.json', _OWNERSHIP, '_uninstall-helper-files.txt',
    'star-map-uninstall-helper.exe',
})
_QTIFW_MAINTENANCE_FILES = frozenset({
    "maintenancetool.exe", "maintenancetool.dat", "maintenancetool.ini",
    "components.xml", "updates.xml", "installscript.qs", "network.xml",
})
_PENDING_MARKERS = frozenset({
    "work/pending-installer-upgrade.json",
    "work/map-packages/pending-resource-reset.json",
})


class UninstallError(ValueError):
    def __init__(self, code: str, *, remaining=()):
        self.code = code
        self.remaining = tuple(dict.fromkeys(str(x) for x in remaining))
        super().__init__(code)


class _FileInfo(ctypes.Structure):
    _fields_ = [
        ("attributes", wt.DWORD), ("created", wt.FILETIME),
        ("accessed", wt.FILETIME), ("written", wt.FILETIME),
        ("volume", wt.DWORD), ("size_high", wt.DWORD), ("size_low", wt.DWORD),
        ("links", wt.DWORD), ("index_high", wt.DWORD), ("index_low", wt.DWORD),
    ]


class _DispositionInfo(ctypes.Structure):
    _fields_ = [("delete_file", ctypes.c_ubyte)]


def _kernel():
    if os.name != "nt":
        raise UninstallError("windows-required")
    api = ctypes.WinDLL("kernel32", use_last_error=True)
    declarations = {
        "CreateFileW": ([wt.LPCWSTR, wt.DWORD, wt.DWORD, wt.LPVOID,
                         wt.DWORD, wt.DWORD, wt.HANDLE], wt.HANDLE),
        "GetFileInformationByHandle": ([wt.HANDLE, ctypes.POINTER(_FileInfo)], wt.BOOL),
        "GetFinalPathNameByHandleW": ([wt.HANDLE, wt.LPWSTR, wt.DWORD, wt.DWORD], wt.DWORD),
        "SetFileInformationByHandle": ([wt.HANDLE, ctypes.c_int, wt.LPVOID, wt.DWORD], wt.BOOL),
        "ReadFile": ([wt.HANDLE, wt.LPVOID, wt.DWORD, ctypes.POINTER(wt.DWORD), wt.LPVOID], wt.BOOL),
        "CloseHandle": ([wt.HANDLE], wt.BOOL),
    }
    for name, (arguments, result) in declarations.items():
        function = getattr(api, name)
        function.argtypes, function.restype = arguments, result
    return api


def _win_path(path):
    value = os.path.abspath(os.fspath(path))
    if value.startswith("\\\\?\\") or value.startswith("\\\\.\\"):
        raise UninstallError("device-path-refused")
    if value.startswith("\\\\") or not re.match(r"^[A-Za-z]:\\", value):
        raise UninstallError("local-drive-path-required")
    return value


def _path_key(path):
    value = ntpath.normpath(_win_path(path)).rstrip("\\")
    if len(value) == 2:
        value += "\\"
    return value.casefold()


def _relative(value):
    if (not isinstance(value, str) or not value or len(value) > 1024
            or "\x00" in value or ":" in value):
        raise UninstallError("ownership-path-invalid")
    value = value.replace("\\", "/")
    parts = value.split("/")
    if any(not part or part in (".", "..") or part[-1:] in (" ", ".")
           or any(ord(c) < 32 or c in '*?"<>|' for c in part)
           or re.fullmatch(r"(?:con|prn|aux|nul|com[1-9]|lpt[1-9])(?:\..*)?", part, re.I)
           for part in parts):
        raise UninstallError("ownership-path-invalid")
    return "/".join(parts)


def _read_json(path, maximum):
    try:
        raw = Path(path).read_bytes()
    except OSError as error:
        raise UninstallError("identity-file-unavailable") from error
    if not 0 < len(raw) <= maximum:
        raise UninstallError("identity-file-size-invalid")
    try:
        value = json.loads(raw.decode("utf-8-sig"))
    except (UnicodeError, ValueError) as error:
        raise UninstallError("identity-file-invalid") from error
    if not isinstance(value, dict):
        raise UninstallError("identity-file-invalid")
    return value


def _known_identities():
    rows = {"production": dict(product=PRODUCTION_PRODUCT, appId=PRODUCTION_APP_ID,
                               folder=_PRODUCTION_FOLDER, executable=_APP_EXE,
                               hive="HKCU", production=True)}
    for name, app_id in _ISOLATED.items():
        rows[name] = dict(product="XingtuQtClientTest", appId=app_id,
                          folder=_KNOWN_PRIVATE_FOLDERS.get(name, ""),
                          executable=_KNOWN_PRIVATE_EXES.get(name, ""),
                          hive="HKLM", production=False)
    return rows


_IDENTITIES = _known_identities()


def _profile_identity(profile):
    try:
        product = profile["product"]
        instance = profile["installerInstance"]
        name, app_id = instance["id"], instance["appId"]
        declared_product = instance["product"]
    except (KeyError, TypeError):
        raise UninstallError("profile-identity-invalid")
    expected = _IDENTITIES.get(name)
    if (expected is None or product != expected["product"] or declared_product != product
            or app_id != expected["appId"] or not expected["folder"] or not expected["executable"]):
        raise UninstallError("profile-identity-not-allowlisted")
    return name, expected


def _baked_profile():
    # The standalone frozen helper is copied beside this file in its temporary
    # runtime; its sibling profile is the build-baked identity authority.
    return _read_json(Path(__file__).with_name("installation-profile.json"), _MAX_PROFILE)


class _RegistryProvider:
    """Small native registry surface; tests may replace this provider only."""

    def __init__(self, owner_sid):
        self.owner_sid = owner_sid

    def registrations(self):
        import winreg
        hives = (("HKCU", winreg.HKEY_CURRENT_USER), ("HKLM", winreg.HKEY_LOCAL_MACHINE))
        views = (winreg.KEY_WOW64_64KEY, winreg.KEY_WOW64_32KEY)
        identities = _IDENTITIES
        rows = []
        for hive_name, hive in hives:
            for view in views:
                for name, identity in identities.items():
                    key_path = _UNINSTALL_KEY + "\\{" + identity["appId"] + "}_is1"
                    try:
                        key = winreg.OpenKey(hive, key_path, 0, winreg.KEY_READ | view)
                    except FileNotFoundError:
                        continue
                    except OSError as error:
                        raise UninstallError("native-registration-unavailable") from error
                    with key:
                        try:
                            location, location_kind = winreg.QueryValueEx(key, "InstallLocation")
                            uninstall, uninstall_kind = winreg.QueryValueEx(key, "UninstallString")
                        except OSError as error:
                            raise UninstallError("native-registration-incomplete") from error
                        if (location_kind != winreg.REG_SZ or uninstall_kind != winreg.REG_SZ
                                or not isinstance(location, str) or not isinstance(uninstall, str)):
                            raise UninstallError("native-registration-invalid")
                        rows.append(dict(identity=name, app_id=identity["appId"], hive=hive_name,
                                         view=64 if view == winreg.KEY_WOW64_64KEY else 32,
                                         install_location=location, uninstall_string=uninstall,
                                         sid=self.owner_sid if hive_name == "HKCU" else None))
        return rows

    def run_value(self, name):
        import winreg
        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, _RUN_KEY, 0,
                                winreg.KEY_QUERY_VALUE | winreg.KEY_WOW64_64KEY) as key:
                value, kind = winreg.QueryValueEx(key, name)
            return kind, value
        except FileNotFoundError:
            return None
        except OSError as error:
            raise UninstallError("game-startup-run-unavailable") from error

    def exit_values(self, prefix):
        import winreg
        try:
            key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, _EXIT_KEY, 0,
                                 winreg.KEY_QUERY_VALUE | winreg.KEY_WOW64_64KEY)
        except FileNotFoundError:
            return {}
        except OSError as error:
            raise UninstallError("game-startup-exit-unavailable") from error
        result = {}
        with key:
            index = 0
            while True:
                try:
                    name, value, kind = winreg.EnumValue(key, index)
                except OSError as error:
                    if getattr(error, "winerror", None) == 259:
                        break
                    raise UninstallError("game-startup-exit-unavailable") from error
                index += 1
                if name.startswith(prefix):
                    result[name] = (kind, value)
        return result

    def delete_run_if_same(self, name, expected):
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, _RUN_KEY, 0,
                            winreg.KEY_QUERY_VALUE | winreg.KEY_SET_VALUE | winreg.KEY_WOW64_64KEY) as key:
            try:
                value, kind = winreg.QueryValueEx(key, name)
                actual = kind, value
            except FileNotFoundError:
                return False
            if actual != expected:
                raise UninstallError("game-startup-run-changed", remaining=("HKCU\\" + _RUN_KEY + "\\" + name,))
            winreg.DeleteValue(key, name)
        return True

    def delete_exit_if_same(self, name, expected):
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, _EXIT_KEY, 0,
                            winreg.KEY_QUERY_VALUE | winreg.KEY_SET_VALUE | winreg.KEY_WOW64_64KEY) as key:
            try:
                value, kind = winreg.QueryValueEx(key, name)
                actual = kind, value
            except FileNotFoundError:
                return False
            if actual != expected:
                raise UninstallError("game-startup-exit-changed", remaining=("HKCU\\" + _EXIT_KEY + "\\" + name,))
            winreg.DeleteValue(key, name)
        return True


def _current_user_sid():
    if os.name != "nt":
        raise UninstallError("windows-required")
    advapi = ctypes.WinDLL("advapi32", use_last_error=True)
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    advapi.OpenProcessToken.argtypes = [wt.HANDLE, wt.DWORD, ctypes.POINTER(wt.HANDLE)]
    advapi.OpenProcessToken.restype = wt.BOOL
    advapi.GetTokenInformation.argtypes = [wt.HANDLE, ctypes.c_int, wt.LPVOID, wt.DWORD,
                                           ctypes.POINTER(wt.DWORD)]
    advapi.GetTokenInformation.restype = wt.BOOL
    advapi.ConvertSidToStringSidW.argtypes = [wt.LPVOID, ctypes.POINTER(wt.LPWSTR)]
    advapi.ConvertSidToStringSidW.restype = wt.BOOL
    kernel.GetCurrentProcess.argtypes = []
    kernel.GetCurrentProcess.restype = wt.HANDLE
    kernel.LocalFree.argtypes = [wt.HLOCAL]
    kernel.LocalFree.restype = wt.HLOCAL
    kernel.CloseHandle.argtypes = [wt.HANDLE]
    kernel.CloseHandle.restype = wt.BOOL
    token = wt.HANDLE()
    if not advapi.OpenProcessToken(kernel.GetCurrentProcess(), 0x0008, ctypes.byref(token)):
        raise UninstallError("owner-sid-unavailable")
    try:
        needed = wt.DWORD()
        advapi.GetTokenInformation(token, 1, None, 0, ctypes.byref(needed))
        if not needed.value or needed.value > 65536:
            raise UninstallError("owner-sid-unavailable")
        buffer = ctypes.create_string_buffer(needed.value)
        if not advapi.GetTokenInformation(token, 1, buffer, needed, ctypes.byref(needed)):
            raise UninstallError("owner-sid-unavailable")
        sid_ptr = ctypes.cast(buffer, ctypes.POINTER(ctypes.c_void_p))[0]
        text = wt.LPWSTR()
        if not advapi.ConvertSidToStringSidW(sid_ptr, ctypes.byref(text)):
            raise UninstallError("owner-sid-unavailable")
        try:
            return text.value
        finally:
            kernel.LocalFree(text)
    finally:
        kernel.CloseHandle(token)


def _owner_sid(handle):
    advapi = ctypes.WinDLL("advapi32", use_last_error=True)
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    advapi.GetSecurityInfo.argtypes = [wt.HANDLE, ctypes.c_int, wt.DWORD,
        ctypes.POINTER(wt.LPVOID), ctypes.POINTER(wt.LPVOID), ctypes.POINTER(wt.LPVOID),
        ctypes.POINTER(wt.LPVOID), ctypes.POINTER(wt.LPVOID)]
    advapi.GetSecurityInfo.restype = wt.DWORD
    advapi.ConvertSidToStringSidW.argtypes = [wt.LPVOID, ctypes.POINTER(wt.LPWSTR)]
    advapi.ConvertSidToStringSidW.restype = wt.BOOL
    kernel.LocalFree.argtypes = [wt.HLOCAL]
    kernel.LocalFree.restype = wt.HLOCAL
    owner, descriptor = wt.LPVOID(), wt.LPVOID()
    status = advapi.GetSecurityInfo(handle, 1, 1, ctypes.byref(owner), None, None,
                                    None, ctypes.byref(descriptor))
    if status != 0 or not owner:
        raise UninstallError("filesystem-owner-unavailable")
    text = wt.LPWSTR()
    try:
        if not advapi.ConvertSidToStringSidW(owner, ctypes.byref(text)):
            raise UninstallError("filesystem-owner-unavailable")
        return text.value
    finally:
        if text:
            kernel.LocalFree(text)
        if descriptor:
            kernel.LocalFree(descriptor)


def _handle_info(api, handle):
    info = _FileInfo()
    if not api.GetFileInformationByHandle(handle, ctypes.byref(info)):
        raise UninstallError("file-identity-unavailable")
    if info.attributes & _ATTR_REPARSE:
        raise UninstallError("reparse-point-refused")
    return info


def _file_id(info):
    return (int(info.volume), int(info.index_high), int(info.index_low))


def _size(info):
    return (int(info.size_high) << 32) | int(info.size_low)


def _open_handle(api, path, *, directory, delete=False, read=False, write=False, read_control=False,
                 share=None, creation=_OPEN_EXISTING):
    access = (_FILE_READ_ATTRIBUTES | (_DELETE if delete else 0)
              | (_GENERIC_READ if read else 0) | (_GENERIC_WRITE if write else 0)
              | (_READ_CONTROL if read_control else 0))
    if share is None:
        share = _FILE_SHARE_READ | _FILE_SHARE_WRITE
    flags = _FILE_FLAG_OPEN_REPARSE_POINT | (_FILE_FLAG_BACKUP_SEMANTICS if directory else 0)
    handle = api.CreateFileW(_win_path(path), access, share, None, creation, flags, None)
    if handle in (None, _INVALID_HANDLE_VALUE):
        error = ctypes.get_last_error()
        raise UninstallError("file-open-failed-" + str(error), remaining=(path,))
    try:
        info = _handle_info(api, handle)
    except Exception:
        api.CloseHandle(handle)
        raise
    if bool(info.attributes & _ATTR_DIRECTORY) != bool(directory):
        api.CloseHandle(handle)
        raise UninstallError("path-kind-changed", remaining=(path,))
    if not directory and int(info.links) != 1:
        api.CloseHandle(handle)
        raise UninstallError("hard-link-refused", remaining=(path,))
    return handle, info


def _handle_path(api, handle):
    buffer = ctypes.create_unicode_buffer(32768)
    count = api.GetFinalPathNameByHandleW(handle, buffer, len(buffer), 0)
    if not 0 < count < len(buffer):
        raise UninstallError("physical-path-unavailable")
    value = buffer.value
    if value.startswith("\\\\?\\"):
        value = value[4:]
    return ntpath.normpath(value).casefold()


def _safe_ancestors(path):
    """Reject linked/reparse ancestors before any destructive operation."""
    path = Path(_win_path(path))
    chain = [path, *path.parents]
    for item in chain:
        if str(item).endswith(":\\"):
            continue
        try:
            info = item.lstat()
        except FileNotFoundError:
            continue
        except OSError as error:
            raise UninstallError("path-ancestor-unavailable", remaining=(item.name,)) from error
        if (stat.S_ISLNK(info.st_mode)
                or getattr(info, "st_file_attributes", 0) & _ATTR_REPARSE):
            raise UninstallError("reparse-ancestor-refused", remaining=(item.name,))


def _pin_directory(api, path, held_handles):
    """Hold the target and its entry parent against rename/reparse replacement."""
    path = Path(path)
    _safe_ancestors(path)
    parent_handle, _ = _open_handle(api, path.parent, directory=True)
    if _handle_path(api, parent_handle) != _path_key(path.parent):
        api.CloseHandle(parent_handle)
        raise UninstallError("directory-parent-physical-path-mismatch")
    held_handles.append(parent_handle)
    target_handle, _ = _open_handle(api, path, directory=True, read_control=True)
    if _handle_path(api, target_handle) != _path_key(path):
        api.CloseHandle(target_handle)
        raise UninstallError("directory-physical-path-mismatch")
    held_handles.append(target_handle)
    return target_handle


def _read_secure(api, path, maximum, observations=None):
    path = Path(path)
    _safe_ancestors(path)
    # Deny writers while interpreting ownership/identity, but allow the later
    # plan's DELETE handle. Bind this object to that plan before releasing it.
    handle, info = _open_handle(api, path, directory=False, read=True,
                              share=_FILE_SHARE_READ | 4)
    try:
        if _handle_path(api, handle) != _path_key(path):
            raise UninstallError("identity-file-physical-path-mismatch", remaining=(path.name,))
        if _size(info) > maximum:
            raise UninstallError("identity-file-size-invalid", remaining=(path.name,))
        chunks, total = [], 0
        while True:
            buffer = ctypes.create_string_buffer(65536)
            count = wt.DWORD()
            if not api.ReadFile(handle, buffer, len(buffer), ctypes.byref(count), None):
                raise UninstallError("identity-file-read-failed", remaining=(path.name,))
            if count.value == 0:
                break
            total += count.value
            if total > maximum:
                raise UninstallError("identity-file-size-invalid", remaining=(path.name,))
            chunks.append(buffer.raw[:count.value])
        if not total:
            raise UninstallError("identity-file-empty", remaining=(path.name,))
        raw = b"".join(chunks)
        if observations is not None:
            observations.append((handle, path, _file_id(info)))
            handle = None
        return raw
    finally:
        if handle:
            api.CloseHandle(handle)


def _read_json_bytes(raw, maximum, code="identity-file-invalid"):
    if not 0 < len(raw) <= maximum:
        raise UninstallError("identity-file-size-invalid")
    try:
        value = json.loads(raw.decode("utf-8-sig"))
    except (UnicodeError, ValueError) as error:
        raise UninstallError(code) from error
    if not isinstance(value, dict):
        raise UninstallError(code)
    return value


def _read_ownership(raw):
    if not 0 < len(raw) <= _MAX_OWNERSHIP:
        raise UninstallError("ownership-file-size-invalid")
    try:
        lines = raw.decode("utf-8-sig").splitlines()
    except UnicodeError as error:
        raise UninstallError("ownership-file-encoding-invalid") from error
    if not lines or len(lines) > 50000:
        raise UninstallError("ownership-file-count-invalid")
    result, folded = set(), set()
    for line in lines:
        if not line:
            continue
        value = _relative(line)
        key = value.casefold()
        if key in folded:
            raise UninstallError("ownership-file-duplicate")
        folded.add(key)
        result.add(value)
    return result


def _check_install_root(root):
    root_text = _win_path(root)
    canonical = ntpath.normpath(root_text)
    if canonical != root_text.rstrip("\\") or len(canonical) <= 3:
        raise UninstallError("install-root-noncanonical-or-broad")
    forbidden = {"windows", "users", "programdata", "program files", "program files (x86)",
                 "programs", "appdata", "local", "roaming", "desktop", "documents"}
    if ntpath.basename(canonical).casefold() in forbidden:
        raise UninstallError("install-root-broad")
    for env_name in ("USERPROFILE", "LOCALAPPDATA", "APPDATA", "PROGRAMDATA", "WINDIR",
                     "ProgramFiles", "ProgramFiles(x86)"):
        value = os.environ.get(env_name)
        if value:
            key = _path_key(value)
            root_key = _path_key(canonical)
            if root_key == key or key.startswith(root_key + "\\"):
                raise UninstallError("install-root-contains-shared-or-user-directory")
    return Path(canonical)


def _check_registry(root, identity_name, expected, registry, current_sid):
    try:
        rows = list(registry.registrations())
    except UninstallError:
        raise
    except Exception as error:
        raise UninstallError("native-registration-unavailable") from error
    # Inno may expose one logical HKCU/HKLM key through both registry views.
    # Collapse only rows whose ownership-bearing values are byte-for-byte equal.
    unique_rows, seen_rows = [], set()
    for row in rows:
        try:
            signature = (str(row["identity"]).casefold(), str(row["app_id"]).casefold(),
                         str(row["hive"]).upper(), row.get("sid"), _path_key(row["install_location"]),
                         str(row["uninstall_string"]).casefold())
        except (KeyError, TypeError):
            raise UninstallError("native-registration-invalid")
        if signature not in seen_rows:
            seen_rows.add(signature)
            unique_rows.append(row)
    rows = unique_rows
    root_key = _path_key(root)
    current = []
    production_rows = []
    for row in rows:
        try:
            name = row["identity"]
            entry = _IDENTITIES[name]
            location = row["install_location"]
            hive = row["hive"]
        except (KeyError, TypeError):
            raise UninstallError("native-registration-invalid")
        location_key = _path_key(location)
        if entry["production"]:
            production_rows.append(row)
        if name == identity_name and row.get("app_id", "").casefold() == expected["appId"].casefold():
            if hive != expected["hive"]:
                raise UninstallError("native-registration-hive-mismatch")
            if expected["hive"] == "HKCU" and row.get("sid") != current_sid:
                raise UninstallError("native-registration-sid-mismatch")
            if location_key != root_key:
                raise UninstallError("native-registration-root-mismatch")
            current.append(row)
            continue
        if (location_key == root_key
                or location_key.startswith(root_key.rstrip("\\") + "\\")
                or root_key.startswith(location_key.rstrip("\\") + "\\")):
            raise UninstallError("other-installation-overlaps-root")
    if len(current) != 1:
        raise UninstallError("native-registration-not-unique")
    row = current[0]
    uninstall = row.get("uninstall_string")
    if not isinstance(uninstall, str):
        raise UninstallError("native-uninstall-registration-invalid")
    match = re.fullmatch(r'\s*(?:"([^"]+\\unins[0-9]+\.exe)"|([^"\s]+\\unins[0-9]+\.exe))(?:\s+.*)?\s*',
                         uninstall, re.I)
    uninstall_file = (match.group(1) or match.group(2)) if match else None
    if not uninstall_file or _path_key(ntpath.dirname(uninstall_file)) != root_key:
        raise UninstallError("native-uninstall-registration-mismatch")
    uninstall_path = Path(uninstall_file)
    if not uninstall_path.is_file() or not re.fullmatch(r"unins[0-9]+\.exe", uninstall_path.name, re.I):
        raise UninstallError("native-uninstaller-missing")
    if expected["production"]:
        if len(production_rows) != 1 or production_rows[0] is not row:
            raise UninstallError("production-resource-root-shared")
    return row, uninstall_path, rows


def _native_file_name(name):
    return bool(re.fullmatch(r"unins[0-9]+\.(?:exe|dat|msg)", name, re.I))


def _is_maintenance_file(name):
    return name.casefold() in _QTIFW_MAINTENANCE_FILES


class _TreePlan:
    """Complete native-handle inventory; deletes only handles opened here."""

    def __init__(self, api, root, *, mode, owned=(), preserve_lock=False, checkpoint=None):
        self.api = api
        self.root = Path(root)
        self.mode = mode
        self.owned = {item.casefold() for item in owned}
        self.retry_program = _RETRY_FILES | {
            item for item in self.owned if item.startswith('_star_map_uninstall_runtime/')}
        if mode == 'app' and '_star_map_uninstall_runtime/installation-profile.json' not in self.retry_program:
            raise UninstallError('uninstall-runtime-ownership-missing',
                                 remaining=('_installer-owned-files.txt',))
        self.preserve_lock = preserve_lock
        self.nodes = []
        self.handles = []
        self.root_identity = None
        self.unowned = []
        self.checkpoint = checkpoint or (lambda: None)

    def _app_classification(self, relative, is_dir):
        folded = relative.casefold()
        # A failed data clear must leave a runnable, verifiable retry entry.
        # These exact program files are removed by native Inno only after all
        # non-native data cleanup succeeds.
        if folded in self.retry_program or is_dir and any(
                item.startswith(folded + '/') for item in self.retry_program):
            return 'preserve-program'
        if folded in _PENDING_MARKERS:
            raise UninstallError("installer-recovery-required", remaining=(relative,))
        if folded == "work" or folded.startswith("work/"):
            if self.preserve_lock and (folded == "work" or folded == "work/map-packages"):
                return "preserve-dir" if is_dir else "delete"
            if folded == "work/map-packages/import.lock":
                return "preserve-lock"
            return "delete"
        if folded == ".xingtu-uninstall-retained" or folded.startswith(".xingtu-uninstall-retained/"):
            return "delete"
        if folded == "_internal" or folded.startswith("_internal/"):
            return "delete"
        if folded == "assets/custom-maps" or folded.startswith("assets/custom-maps/"):
            return "delete"
        if folded == "assets" and is_dir:
            return "delete"
        parts = relative.split("/")
        if re.match(r"_star_map_[A-Za-z0-9_-]+_runtime(?:/|$)", folded, re.I):
            return "delete"
        if len(parts) == 1 and _native_file_name(parts[0]):
            return "preserve-native"
        if len(parts) == 1 and (parts[0].casefold() in _APP_METADATA or _is_maintenance_file(parts[0])):
            return "delete"
        if len(parts) == 1 and parts[0].casefold() == self.exe_name.casefold():
            return "delete"
        if folded in self.owned:
            return "delete"
        if is_dir and any(item.startswith(folded + "/") for item in self.owned):
            return "delete"
        return None

    def _external_classification(self, relative, is_dir):
        if self.preserve_lock:
            folded = relative.casefold()
            if folded in {"work", "work/map-packages"} and is_dir:
                return "preserve-dir"
            if folded == "work/map-packages/import.lock":
                return "preserve-lock"
        return "delete"

    def _classify(self, relative, is_dir):
        if relative.casefold() in _PENDING_MARKERS:
            raise UninstallError("installer-recovery-required", remaining=(relative,))
        if self.mode == "external":
            return self._external_classification(relative, is_dir)
        if self.mode == "full":
            return "delete"
        return self._app_classification(relative, is_dir)

    def scan(self, *, exe_name=None):
        self.exe_name = exe_name or _APP_EXE
        _safe_ancestors(self.root)
        parent = self.root.parent
        parent_handle, _parent_info = _open_handle(self.api, parent, directory=True, delete=False)
        if _handle_path(self.api, parent_handle) != _path_key(parent):
            self.api.CloseHandle(parent_handle)
            raise UninstallError("install-parent-physical-path-mismatch")
        self.handles.append(parent_handle)
        root_action = "delete" if self.mode in {"full", "recovery"} else "preserve-dir"
        root_handle, root_info = _open_handle(self.api, self.root, directory=True,
                                              delete=self.mode in {"external", "full", "recovery"},
                                              read_control=self.mode in {"app", "external", "full", "recovery"})
        self.handles.append(root_handle)
        self.root_identity = _file_id(root_info)
        expected = _path_key(self.root)
        if _handle_path(self.api, root_handle) != expected:
            raise UninstallError("install-root-physical-path-mismatch")
        self.nodes.append(dict(path=self.root, relative="", directory=True, action=root_action,
                               handle=root_handle, identity=_file_id(root_info)))
        self._walk(self.root, "")
        return self

    def _walk(self, directory_path, relative_directory):
        self.checkpoint()
        try:
            with os.scandir(directory_path) as iterator:
                entries = sorted(list(iterator), key=lambda entry: entry.name.casefold())
        except OSError as error:
            raise UninstallError("directory-enumeration-failed", remaining=(relative_directory or ".",)) from error
        folded_names = [entry.name.casefold() for entry in entries]
        if len(set(folded_names)) != len(folded_names):
            raise UninstallError("case-colliding-directory-entry", remaining=(relative_directory or ".",))
        for entry in entries:
            self.checkpoint()
            name = entry.name
            if (not name or name in (".", "..") or "/" in name or "\\" in name or ":" in name
                    or name[-1:] in (" ", ".") or any(ord(c) < 32 for c in name)):
                raise UninstallError("unsafe-directory-entry", remaining=(relative_directory + "/" + name,))
            rel = (relative_directory + "/" + name).lstrip("/")
            path = Path(directory_path) / name
            try:
                is_dir = entry.is_dir(follow_symlinks=False)
            except OSError as error:
                raise UninstallError("directory-entry-kind-unavailable", remaining=(rel,)) from error
            action = self._classify(rel, is_dir)
            if action is None:
                self.unowned.append(rel)
                raise UninstallError("unknown-install-root-entry", remaining=(rel,))
            if action == "preserve-lock":
                # The parent Inno process owns this same file object with share=0.
                try:
                    info = path.lstat()
                except OSError as error:
                    raise UninstallError("app-resource-lock-unavailable", remaining=(rel,)) from error
                if (not stat.S_ISREG(info.st_mode) or info.st_nlink != 1
                        or getattr(info, "st_file_attributes", 0) & _ATTR_REPARSE):
                    raise UninstallError("app-resource-lock-invalid", remaining=(rel,))
                self.nodes.append(dict(path=path, relative=rel, directory=False,
                                       action=action, handle=None, identity=None))
                continue
            if action == "preserve-native":
                try:
                    info = path.lstat()
                except OSError as error:
                    raise UninstallError("native-uninstall-file-unavailable", remaining=(rel,)) from error
                if (not stat.S_ISREG(info.st_mode) or info.st_nlink != 1
                        or getattr(info, "st_file_attributes", 0) & _ATTR_REPARSE):
                    raise UninstallError("native-uninstall-file-invalid", remaining=(rel,))
                self.nodes.append(dict(path=path, relative=rel, directory=False,
                                       action=action, handle=None, identity=None))
                continue
            delete_access = action == "delete" or (self.mode == "external" and action == "preserve-dir")
            # A DELETE handle alone can open a running Windows image and fail
            # only later, after unrelated data has already been removed. A
            # retained WRITE handle rejects a mapped main image now and also
            # prevents it from starting between this preflight and deletion.
            main_image = self.mode == 'app' and not is_dir and rel.casefold() in {
                self.exe_name.casefold(), '第五人格小抄助手.exe'}
            recovery_file = self.mode == 'recovery' and not is_dir
            recovery_lock = recovery_file and rel == 'work/map-packages/import.lock'
            handle, info = _open_handle(self.api, path, directory=is_dir,
                                        delete=delete_access,
                                        write=main_image or recovery_lock or recovery_file and path.suffix.casefold() in {'.exe', '.dll', '.pyd'},
                                        read=recovery_file,
                                        read_control=self.mode == 'recovery',
                                        share=0 if recovery_lock else _FILE_SHARE_READ if recovery_file else None)
            self.handles.append(handle)
            if _handle_path(self.api, handle) != _path_key(path):
                raise UninstallError("entry-physical-path-mismatch", remaining=(rel,))
            node = dict(path=path, relative=rel, directory=is_dir, action=action,
                        handle=handle, identity=_file_id(info))
            self.nodes.append(node)
            if is_dir:
                self._walk(path, rel)

    def assert_unchanged(self):
        self.checkpoint()
        for node in self.nodes:
            self.checkpoint()
            if node["handle"] is None:
                continue
            info = _handle_info(self.api, node["handle"])
            if not node['directory'] and int(info.links) != 1:
                raise UninstallError('hard-link-refused', remaining=(node['relative'],))
            if _file_id(info) != node["identity"]:
                raise UninstallError("preflight-object-changed", remaining=(node["relative"] or ".",))
            if _handle_path(self.api, node["handle"]) != _path_key(node["path"]):
                raise UninstallError("preflight-path-changed", remaining=(node["relative"] or ".",))
        for directory in (node for node in self.nodes if node["directory"] and node.get("handle")):
            self.checkpoint()
            prefix = directory["relative"]
            expected = set()
            for child in self.nodes:
                if child is directory or child.get("deleted"):
                    continue
                relative = child["relative"]
                if prefix:
                    if not relative.startswith(prefix + "/"):
                        continue
                    tail = relative[len(prefix) + 1:]
                else:
                    tail = relative
                if tail and "/" not in tail:
                    expected.add(tail.casefold())
            try:
                with os.scandir(directory["path"]) as iterator:
                    actual = {entry.name.casefold() for entry in iterator}
            except OSError as error:
                raise UninstallError("directory-enumeration-failed",
                                     remaining=(prefix or ".",)) from error
            if len(actual) != len(expected) or actual != expected:
                changed = sorted(actual.symmetric_difference(expected))
                raise UninstallError("directory-contents-changed",
                                     remaining=tuple(([prefix or "."] + changed)))

    def delete_files(self, mutation_started):
        self.assert_unchanged()
        for node in self.nodes:
            self.checkpoint()
            if node["action"] != "delete" or node["directory"]:
                continue
            if mutation_started is not None:
                mutation_started()
            _mark_delete(self.api, node["handle"], node["relative"])
            self.api.CloseHandle(node["handle"])
            self.handles.remove(node["handle"])
            node["handle"] = None
            node["deleted"] = True

    def delete_directories(self, mutation_started, *, include_root=False, include_preserved=False):
        self.assert_unchanged()
        dirs = [node for node in self.nodes if node["directory"] and
                (node["action"] == "delete" or
                 include_preserved and node["action"] == "preserve-dir")]
        if include_root:
            dirs += [node for node in self.nodes if node["directory"] and not node["relative"]
                     and node not in dirs]
        dirs.sort(key=lambda node: node["relative"].count("/") + bool(node["relative"]), reverse=True)
        for node in dirs:
            self.checkpoint()
            if node.get("deleted"):
                continue
            if mutation_started is not None:
                mutation_started()
            _mark_delete(self.api, node["handle"], self.mode + ":" + (node["relative"] or "."))
            self.api.CloseHandle(node["handle"])
            self.handles.remove(node["handle"])
            node["handle"] = None
            node["deleted"] = True

    def close(self):
        for handle in reversed(list(dict.fromkeys(self.handles))):
            if handle:
                try:
                    self.api.CloseHandle(handle)
                except Exception:
                    pass
        for node in self.nodes:
            node["handle"] = None
        self.handles.clear()

    def mark_external_lock_deleted(self):
        for node in self.nodes:
            if node["action"] == "preserve-lock" and node["relative"].casefold() == "work/map-packages/import.lock":
                node["deleted"] = True

    def remaining_paths(self):
        return [node["relative"] or "." for node in self.nodes
                if (node.get("action") == "delete" or self.mode == "external"
                    and node.get("action") == "preserve-dir") and not node.get("deleted")]


def _mark_delete(api, handle, relative):
    if not handle:
        raise UninstallError("delete-handle-unavailable", remaining=(relative,))
    current = _handle_info(api, handle)
    if not current.attributes & _ATTR_DIRECTORY and int(current.links) != 1:
        raise UninstallError('hard-link-refused', remaining=(relative,))
    info = _DispositionInfo(True)
    if not api.SetFileInformationByHandle(handle, _FILE_DISPOSITION_INFO,
                                          ctypes.byref(info), ctypes.sizeof(info)):
        raise UninstallError("handle-delete-failed-" + str(ctypes.get_last_error()), remaining=(relative,))


class _ExternalLease:
    """Exclusive external-resource import lock with a checked identity handoff."""

    def __init__(self, api, path):
        self.api, self.path, self.handle, self.identity = api, Path(path), None, None
        _safe_ancestors(self.path)
        self.handle, info = _open_handle(api, self.path, directory=False, delete=False,
                                       read=True, write=True, share=0)
        self.identity = _file_id(info)
        if _handle_path(self.api, self.handle) != _path_key(self.path):
            self.close()
            raise UninstallError("external-resource-lock-path-mismatch")
        self._lock_byte()

    def _lock_byte(self):
        import msvcrt
        fd = None
        try:
            fd = msvcrt.open_osfhandle(int(self.handle), os.O_RDWR | os.O_BINARY)
            self.handle = None
            msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
        except OSError as error:
            if fd is not None:
                os.close(fd)
                self.handle = None
            elif self.handle:
                self.api.CloseHandle(self.handle)
                self.handle = None
            raise UninstallError("external-resource-lock-busy") from error
        self.fd = fd

    def close_for_delete(self):
        if getattr(self, "fd", None) is None:
            raise UninstallError("external-resource-lock-unavailable")
        try:
            os.close(self.fd)
        finally:
            self.fd = None
        # Reopen the exact lock path with DELETE access and compare the original
        # file ID before marking it. A replacement or new user wins as an error.
        handle, info = _open_handle(self.api, self.path, directory=False, delete=True,
                                    read=True, share=0)
        if (_file_id(info) != self.identity
                or _handle_path(self.api, handle) != _path_key(self.path)):
            self.api.CloseHandle(handle)
            raise UninstallError("external-resource-lock-replaced", remaining=("work/map-packages/import.lock",))
        self.handle = handle

    def delete(self):
        if not self.handle:
            raise UninstallError("external-resource-lock-unavailable")
        _mark_delete(self.api, self.handle, "work/map-packages/import.lock")
        self.api.CloseHandle(self.handle)
        self.handle = None

    def close(self):
        if getattr(self, "fd", None) is not None:
            try:
                os.close(self.fd)
            except OSError:
                pass
            self.fd = None
        if self.handle:
            self.api.CloseHandle(self.handle)
            self.handle = None


def _external_lock_path(resource_root):
    return Path(resource_root) / "work" / "map-packages" / "import.lock"


def _validate_result_directory(path, root=None):
    value = Path(_win_path(path))
    _safe_ancestors(value)
    if not value.is_dir():
        raise UninstallError("result-directory-missing")
    if value == Path(value.anchor) or root is not None and _path_key(value) == _path_key(root):
        raise UninstallError("result-directory-invalid")
    if root is not None and (value.is_relative_to(root) or root.is_relative_to(value)):
        raise UninstallError("result-directory-not-outside-install")
    try:
        with os.scandir(value) as entries:
            if next(entries, None) is not None:
                raise UninstallError("result-directory-not-empty")
    except OSError as error:
        raise UninstallError("result-directory-unavailable") from error
    return value


def _install_profile_matches(root, expected_name, expected, observations=None):
    profile_path = root / "installation-profile.json"
    api = _kernel()
    profile = _read_json_bytes(_read_secure(api, profile_path, _MAX_PROFILE, observations), _MAX_PROFILE)
    name, actual = _profile_identity(profile)
    if name != expected_name or actual["appId"].casefold() != expected["appId"].casefold():
        raise UninstallError("installed-profile-identity-mismatch")
    internal = root / "_internal" / "installation-profile.json"
    if internal.exists():
        copied = _read_json_bytes(_read_secure(api, internal, _MAX_PROFILE, observations), _MAX_PROFILE)
        copied_name, copied_expected = _profile_identity(copied)
        if copied_name != expected_name or copied_expected["appId"].casefold() != expected["appId"].casefold():
            raise UninstallError("internal-profile-identity-mismatch")


def _require_owner(plan, sid, reason):
    if not plan or not plan.nodes:
        return
    if _owner_sid(plan.nodes[0]["handle"]) != sid:
        raise UninstallError(reason)


def _load_ownership(root, api, observations=None, expected_digest=None):
    raw = _read_secure(api, root / _OWNERSHIP, _MAX_OWNERSHIP, observations)
    if expected_digest is not None:
        import hashlib
        if not re.fullmatch(r'[0-9a-f]{64}', expected_digest) or hashlib.sha256(raw).hexdigest() != expected_digest:
            raise UninstallError('ownership-compiled-inventory-mismatch')
    return _read_ownership(raw)


def _validate_run_values(registry, root):
    from game_startup import run_command, run_name
    value_name, expected_command = run_name(root), run_command(root)
    current = registry.run_value(value_name)
    if current is not None and current != (1, expected_command):  # REG_SZ is 1.
        raise UninstallError("game-startup-run-mismatch")
    prefix = value_name + "-"
    values = registry.exit_values(prefix)
    for name, item in values.items():
        kind, value = item
        suffix = name[len(prefix):]
        if not suffix.isdecimal() or kind != 1 or not isinstance(value, str) or len(value) > 32768:
            raise UninstallError("game-startup-exit-value-invalid")
        try:
            data = json.loads(value)
        except ValueError as error:
            raise UninstallError("game-startup-exit-value-invalid") from error
        if (not isinstance(data, list) or len(data) > 256
                or any(not isinstance(entry, str) or len(entry) > 80 for entry in data)):
            raise UninstallError("game-startup-exit-value-invalid")
    return (value_name, current, values)


def _remove_run_values(registry, run_state, mutation_started):
    value_name, run_value, exit_values = run_state
    if run_value is not None:
        mutation_started()
        try:
            registry.delete_run_if_same(value_name, run_value)
        except Exception as error:
            raise UninstallError('game-startup-run-cleanup-failed',
                                 remaining=('HKCU\\' + _RUN_KEY + '\\' + value_name,)) from error
    for name, value in sorted(exit_values.items()):
        mutation_started()
        try:
            registry.delete_exit_if_same(name, value)
        except Exception as error:
            raise UninstallError('game-startup-exit-cleanup-failed',
                                 remaining=('HKCU\\' + _EXIT_KEY + '\\' + name,)) from error


def _failure_message(error):
    code = error.code if isinstance(error, UninstallError) else "unexpected-error"
    if code == "cleanup-time-limit":
        return "清理超过本次等待时间，已停止。请关闭本安装的星图和资源任务后重新卸载。"
    if code.startswith(("file-open", "handle-delete", "directory-enumeration")):
        return "部分文件被占用或当前账号无权清除。请退出本安装的星图，核对剩余项后重试；不会自动修改权限。"
    if code.startswith('recovery-'):
        if code == 'recovery-data-already-cleared-use-setup':
            return '资料已经清除，不会重新认领新出现的资料。请关闭当前卸载窗口，再打开卸载时的同一份安装包，按提示继续清理。'
        if code == 'recovery-data-not-cleared-use-original-uninstaller':
            return '资料清理还未完成，原生卸载尚未获准开始。请从原来的卸载入口重试；入口损坏时保留现场并联系星图维护者。'
        return '本次卸载的恢复登记、文件归属或占用状态未能核实，已停止。请保留剩余项；关闭本安装的资源任务后重开对应安装包继续清理，仍失败时联系星图维护者。'
    if "recovery" in code or "pending" in code:
        return "上次升级尚未完成，请先运行对应安装包完成恢复，再重新卸载。"
    if any(word in code for word in ("profile", "ownership", "identity-file", "uninstall-runtime")):
        return "安装文件不完整或无法核实归属。请使用对应安装包修复，再重新卸载；不要手动删除卸载程序。"
    if any(word in code for word in ("changed", "mismatch", "shared", "unknown", "link", "reparse")):
        return "资料归属或占用状态无法安全确认，已停止。请核对剩余项；其他实例和外部原件不会自动清理。"
    return "清理未完成，已停止。请保留剩余项和卸载入口，关闭相关任务后重试；仍失败时使用对应安装包修复。"


def _write_receipt(result_dir, *, status, mutation_started, error=None, remaining=()):
    receipt = configparser.ConfigParser(interpolation=None)
    receipt.optionxform = str
    receipt["result"] = {"status": status,
                         "mutationStarted": "True" if mutation_started else "False"}
    if error:
        receipt["result"]["errorCode"] = error.code if isinstance(error, UninstallError) else type(error).__name__
        receipt["result"]["message"] = _failure_message(error)
    if error:
        message = str(error) or type(error).__name__
        if isinstance(error, UninstallError) and error.remaining:
            message += "\n" + "\n".join(error.remaining)
        (result_dir / "error.txt").write_text(message[:_MAX_RESULT_TEXT], encoding="utf-8-sig")
    remaining_lines = sorted({str(item) for item in remaining if str(item)})
    (result_dir / "remaining.txt").write_text("\n".join(remaining_lines), encoding="utf-8-sig")
    temporary = result_dir / ".result.ini.tmp"
    with temporary.open("w", encoding="utf-16", newline="") as stream:
        receipt.write(stream)
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary, result_dir / "result.ini")


def _args(argv):
    parser = argparse.ArgumentParser(prog="star-map-uninstall-helper")
    parser.add_argument("--install-root", required=True)
    parser.add_argument("--result-directory", required=True)
    parser.add_argument("--parent-pid", required=True, type=int)
    parser.add_argument("--parent-created", required=True)
    parser.add_argument("--parent-executable", required=True)
    parser.add_argument("--parent-sha256", required=True)
    parser.add_argument("--lock-handle", required=True, type=int)
    parser.add_argument("--timeout-seconds", type=int, default=120, choices=range(1, 601),
                        metavar="1..600")
    parser.add_argument('--recover', action='store_true')
    parser.add_argument('--recovery-lease-root')
    parser.add_argument('--helper-inventory-sha256')
    parser.add_argument('--program-ownership-sha256')
    parser.add_argument('--shortcut', action='append', default=[])
    parser.add_argument('--shortcut-group', action='append', default=[])
    args = parser.parse_args(argv)
    if getattr(sys, 'frozen', False) and not args.helper_inventory_sha256:
        parser.error('the frozen helper requires its compiled helper inventory')
    if args.helper_inventory_sha256 and not args.recover and not args.program_ownership_sha256:
        parser.error('native data cleanup requires its compiled program ownership digest')
    if args.recover and not args.recovery_lease_root:
        parser.error('--recover requires --recovery-lease-root')
    return args


def run(args, *, registry_provider=None, owner_sid_provider=None, profile_provider=None,
        recovery_store=None):
    """Run cleanup; only narrow identity providers are replaceable for fixtures."""
    mutation = False
    root = Path(_win_path(args.install_root))
    result_dir = Path(_win_path(args.result_directory))
    try:
        result_dir = _validate_result_directory(result_dir, root)
    except Exception:
        return 2
    app_plan = external_plan = startup_plan = None
    external_lease = None
    pinned_handles = []
    metadata_observations = []
    remaining = []
    deadline = time.monotonic() + getattr(args, "timeout_seconds", 120)

    def checkpoint():
        if time.monotonic() >= deadline:
            raise UninstallError("cleanup-time-limit")

    def began_mutation():
        nonlocal mutation
        checkpoint()
        mutation = True

    try:
        checkpoint()
        if os.name != "nt":
            raise UninstallError("windows-required")
        _safe_ancestors(root)
        baked = profile_provider() if profile_provider else _baked_profile()
        baked_name, expected = _profile_identity(baked)
        root = _check_install_root(root)
        sid = owner_sid_provider() if owner_sid_provider else _current_user_sid()
        registry = registry_provider or _RegistryProvider(sid)
        registration, uninstall_path, registered_rows = _check_registry(
            root, baked_name, expected, registry, sid)
        if _path_key(uninstall_path.parent) != _path_key(root):
            raise UninstallError("native-uninstaller-outside-root")

        # Pin the exact root before reading mutable profile/ownership metadata.
        api = _kernel()
        _pin_directory(api, root, pinned_handles)
        ownership = _load_ownership(root, api, metadata_observations,
                                    getattr(args, 'program_ownership_sha256', None))
        _install_profile_matches(root, baked_name, expected, metadata_observations)
        run_state = None
        resource_root = None
        external_exists = False
        external_lock_missing = False
        startup_root = None
        startup_exists = False
        if expected["production"]:
            from game_startup import install_key, state_directory
            key = install_key(root)
            run_state = _validate_run_values(registry, root)
            for other in registry.registrations():
                try:
                    other_root = Path(other["install_location"])
                    other_key = install_key(other_root)
                except (KeyError, OSError, ValueError):
                    raise UninstallError("native-registration-invalid")
                if other_key == key and _path_key(other_root) != _path_key(root):
                    raise UninstallError("game-startup-root-shared")
            local = os.environ.get("LOCALAPPDATA")
            if not local or not Path(local).is_absolute():
                raise UninstallError("local-app-data-unavailable")
            local_root = Path(_win_path(local))
            _safe_ancestors(local_root)
            if not local_root.is_dir():
                raise UninstallError("local-app-data-unavailable")
            local_handle = _pin_directory(api, local_root, pinned_handles)
            if _owner_sid(local_handle) != sid:
                raise UninstallError("local-app-data-owner-mismatch")
            resource_root = local_root / "Xingtu" / "resources"
            startup_root = state_directory(root)
            _safe_ancestors(resource_root)
            _safe_ancestors(startup_root)
            external_exists = resource_root.exists()
            startup_exists = startup_root.exists()
            if external_exists:
                if len([r for r in registered_rows
                        if _IDENTITIES.get(r.get("identity"), {}).get("production")]) != 1:
                    raise UninstallError("production-resource-root-shared")
                ext_lock_path = _external_lock_path(resource_root)
                if ext_lock_path.exists():
                    # Open with share=0 and take the same byte-range lock used
                    # by resource_transaction while the preflight/deletion runs.
                    external_lease = _ExternalLease(api, ext_lock_path)
                else:
                    external_lock_missing = True
            elif resource_root.is_symlink():
                raise UninstallError("external-resource-root-linked")

        app_plan = _TreePlan(api, root, mode="app", owned=ownership, preserve_lock=True,
                             checkpoint=checkpoint)
        app_plan.scan(exe_name=expected["executable"])
        if expected["production"]:
            _require_owner(app_plan, sid, "install-root-owner-mismatch")
        if external_exists:
            external_plan = _TreePlan(api, resource_root, mode="external", preserve_lock=True,
                                     checkpoint=checkpoint)
            external_plan.scan()
            _require_owner(external_plan, sid, "external-resource-owner-mismatch")
            if external_lock_missing:
                # A previous attempt can stop after removing its lock but
                # before removing the empty lock ancestors. This permits only
                # that empty scaffold, never missing-lock data or unknown items.
                if any(not node['directory'] or node['relative'].casefold() not in {
                        '', 'work', 'work/map-packages'} for node in external_plan.nodes):
                    raise UninstallError("external-resource-lock-missing",
                                         remaining=(str(_external_lock_path(resource_root)),))
        if startup_exists:
            if not startup_root.is_dir():
                raise UninstallError("game-startup-state-not-directory")
            startup_plan = _TreePlan(api, startup_root, mode="full", checkpoint=checkpoint)
            startup_plan.scan()
            _require_owner(startup_plan, sid, "game-startup-owner-mismatch")
        app_plan.assert_unchanged()
        if external_plan:
            external_plan.assert_unchanged()
        if startup_plan:
            startup_plan.assert_unchanged()
        planned = {_path_key(node['path']): node for node in app_plan.nodes}
        for handle, path, identity in metadata_observations:
            node = planned.get(_path_key(path))
            if (node is None or node.get('identity') != identity
                    or _file_id(_handle_info(api, handle)) != identity
                    or _handle_path(api, handle) != _path_key(path)):
                raise UninstallError("installation-metadata-changed", remaining=(str(path),))
        for handle, _path, _identity in metadata_observations:
            api.CloseHandle(handle)
        metadata_observations.clear()

        # Keep the app's parent lock until Inno's pre-native data finalization.
        # The external lock is owned by this helper and is removed here.
        recovery_raw = None
        if getattr(args, 'helper_inventory_sha256', None):
            from installer_uninstall_recovery import RecoveryStore, prepare
            recovery_store = recovery_store or RecoveryStore()
            recovery_raw = prepare(args, identity=baked_name, sid=sid, registry=registry,
                                   store=recovery_store, app_plan=app_plan,
                                   data_roots=(resource_root, startup_root), native_executable=uninstall_path)
        app_plan.delete_files(began_mutation)
        app_plan.delete_directories(began_mutation)
        if external_plan:
            external_plan.delete_files(began_mutation)
            external_plan.delete_directories(began_mutation)
            if not external_lock_missing:
                external_lease.close_for_delete()
                began_mutation()
                external_lease.delete()
                external_lease = None
                external_plan.mark_external_lock_deleted()
            # Now remove the external lock ancestors and root through the same
            # directory handles opened during preflight.
            external_plan.delete_directories(began_mutation, include_root=True, include_preserved=True)
        if startup_plan:
            startup_plan.delete_files(began_mutation)
            startup_plan.delete_directories(began_mutation, include_root=True)
        if run_state:
            _remove_run_values(registry, run_state, began_mutation)
        if recovery_raw is not None:
            from installer_uninstall_recovery import ready
            ready(recovery_store, baked_name, recovery_raw)
        _write_receipt(result_dir, status="cleared", mutation_started=mutation)
        return 0
    except Exception as error:
        if isinstance(error, UninstallError):
            remaining.extend(error.remaining)
        if app_plan:
            remaining.extend(app_plan.remaining_paths())
        if external_plan:
            remaining.extend("%LOCALAPPDATA%/Xingtu/resources/" + p
                              for p in external_plan.remaining_paths())
        if startup_plan:
            remaining.extend("%LOCALAPPDATA%/Xingtu/game-startup/" + p
                              for p in startup_plan.remaining_paths())
        if not remaining:
            remaining.append("cleanup-not-confirmed")
        try:
            _write_receipt(result_dir, status="error", mutation_started=mutation,
                           error=error, remaining=remaining)
        except Exception:
            pass
        return 2
    finally:
        if external_lease:
            external_lease.close()
        if external_plan:
            external_plan.close()
        if startup_plan:
            startup_plan.close()
        if app_plan:
            app_plan.close()
        for handle, _path, _identity in metadata_observations:
            api.CloseHandle(handle)
        for handle in reversed(pinned_handles):
            try:
                api.CloseHandle(handle)
            except Exception:
                pass


def main(argv=None, *, registry_provider=None, owner_sid_provider=None, profile_provider=None,
         lease_provider=None, recovery_store=None, maintenance_provider=None):
    """CLI entry. The optional providers are fixture-only narrow seams."""
    try:
        args = _args(argv)
    except SystemExit as error:
        return int(error.code)
    if lease_provider is None:
        from installer_resource_lease import borrowed_installer_lease
        lease_provider = borrowed_installer_lease
    try:
        lease_root = args.recovery_lease_root if args.recover else args.install_root
        with ExitStack() as stack:
            if args.helper_inventory_sha256 or args.recover:
                from installer_uninstall_recovery import hold_maintenance
                profile = profile_provider() if profile_provider else _baked_profile()
                stack.enter_context((maintenance_provider or hold_maintenance)(profile))
            stack.enter_context(lease_provider(lease_root, parent_pid=args.parent_pid,
                                parent_created=args.parent_created,
                                parent_executable=args.parent_executable,
                                parent_sha256=args.parent_sha256,
                                lock_handle=args.lock_handle))
            if args.recover:
                from installer_uninstall_recovery import recover
                return recover(args, registry_provider=registry_provider,
                               owner_sid_provider=owner_sid_provider, profile_provider=profile_provider,
                               store=recovery_store)
            return run(args, registry_provider=registry_provider,
                       owner_sid_provider=owner_sid_provider, profile_provider=profile_provider,
                       recovery_store=recovery_store)
    except Exception as error:
        result_path = Path(args.result_directory)
        try:
            result_path = _validate_result_directory(result_path, Path(args.install_root))
            _write_receipt(result_path, status="error", mutation_started=False,
                           error=error if isinstance(error, UninstallError) else UninstallError("parent-lease-invalid"),
                           remaining=("cleanup-not-started",))
        except Exception:
            pass
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
