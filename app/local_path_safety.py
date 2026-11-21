"""Shared low-level path link checks, directory sync, and Windows handle ops."""
import os
import stat


_kernel = None
_real_windll = None
_get_attributes = None
_INVALID_HANDLE_VALUE = None

if os.name == 'nt':
    import ctypes
    from ctypes import wintypes
    import msvcrt

    _real_windll = ctypes.WinDLL
    _kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    _get_attributes = _kernel.GetFileAttributesW
    _get_attributes.argtypes = [wintypes.LPCWSTR]
    _get_attributes.restype = wintypes.DWORD

    _kernel.CreateFileW.argtypes = [
        wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, ctypes.c_void_p,
        wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE,
    ]
    _kernel.CreateFileW.restype = wintypes.HANDLE
    _kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    _kernel.CloseHandle.restype = wintypes.BOOL
    _kernel.SetFileInformationByHandle.argtypes = [
        wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD,
    ]
    _kernel.SetFileInformationByHandle.restype = wintypes.BOOL
    _INVALID_HANDLE_VALUE = ctypes.c_void_p(-1).value

    class _FileRenameInfo(ctypes.Structure):
        _fields_ = [
            ('ReplaceIfExists', ctypes.c_ubyte),
            ('RootDirectory', wintypes.HANDLE),
            ('FileNameLength', wintypes.DWORD),
            ('FileName', wintypes.WCHAR * 1),
        ]


_cached_windll = None
_cached_kernel = None


def _kernel32():
    global _cached_windll, _cached_kernel
    if ctypes.WinDLL is _real_windll:
        return _kernel
    if ctypes.WinDLL is _cached_windll and _cached_kernel is not None:
        return _cached_kernel
    api = ctypes.WinDLL('kernel32', use_last_error=True)
    api.CreateFileW.argtypes = [
        wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, ctypes.c_void_p,
        wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE,
    ]
    api.CreateFileW.restype = wintypes.HANDLE
    api.CloseHandle.argtypes = [wintypes.HANDLE]
    api.CloseHandle.restype = wintypes.BOOL
    api.SetFileInformationByHandle.argtypes = [
        wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD,
    ]
    api.SetFileInformationByHandle.restype = wintypes.BOOL
    _cached_windll, _cached_kernel = ctypes.WinDLL, api
    return api


def is_link_or_reparse(path):
    """Query this lexical component anew; callers still check every ancestor.

    GetFileAttributesW returns the link's own attributes. Failed queries use
    the original lstat path, preserving UNC-root/long-path/error behavior.
    Never pass NUL to LPCWSTR, which would silently truncate the pathname.
    https://learn.microsoft.com/windows/win32/api/fileapi/nf-fileapi-getfileattributesw
    """
    name = os.fspath(path)
    if _get_attributes is not None and '\x00' not in name:
        attributes = _get_attributes(name)
        if attributes != 0xFFFFFFFF:
            return bool(attributes & 0x400)
    info = path.lstat()
    return stat.S_ISLNK(info.st_mode) or bool(getattr(info, 'st_file_attributes', 0) & 0x400)


def reject_links(path, *, on_link, reject_hardlink=None):
    """Reject symlinks/reparse points along ``path`` and every lexical ancestor."""
    for item in (path, *path.parents):
        try:
            linked = is_link_or_reparse(item)
        except FileNotFoundError:
            continue
        if linked:
            on_link(item)
        if reject_hardlink is not None and item == path:
            try:
                info = item.lstat()
            except FileNotFoundError:
                continue
            if stat.S_ISREG(info.st_mode) and getattr(info, 'st_nlink', 1) > 1:
                reject_hardlink(item)
    return path


def directory_sync(path):
    if os.name == 'nt':
        return  # Python cannot portably flush a Windows directory handle.
    fd = os.open(path, os.O_RDONLY | getattr(os, 'O_DIRECTORY', 0))
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def win32_open_handle_fd(path, *, access=0x80010000, share=1, creation=3,
                         flags=0x00200000, osf_flags=None):
    """Open a Windows file/directory handle and wrap it in an owning CRT fd."""
    if osf_flags is None:
        osf_flags = os.O_RDONLY | os.O_BINARY
    api = _kernel32()
    handle = api.CreateFileW(os.fspath(path), access, share, None, creation, flags, None)
    if handle in (None, _INVALID_HANDLE_VALUE):
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        return msvcrt.open_osfhandle(int(handle), osf_flags)
    except BaseException:
        api.CloseHandle(handle)
        raise


def win32_set_delete_disposition(fd):
    """Mark the exact open Windows handle for deletion via FileDispositionInfo."""
    disposition = ctypes.c_ubyte(1)
    return bool(_kernel32().SetFileInformationByHandle(
        wintypes.HANDLE(msvcrt.get_osfhandle(fd)),
        4, ctypes.byref(disposition), ctypes.sizeof(disposition),
    ))


def win32_rename_handle(fd, destination, *, replace_if_exists=False):
    """Rename the exact open Windows handle via FileRenameInfo."""
    name = os.fspath(destination).encode('utf-16-le')
    size = max(ctypes.sizeof(_FileRenameInfo), _FileRenameInfo.FileName.offset + len(name) + 2)
    buffer = ctypes.create_string_buffer(size)
    info = _FileRenameInfo.from_buffer(buffer)
    info.ReplaceIfExists = 1 if replace_if_exists else 0
    info.RootDirectory = None
    info.FileNameLength = len(name)
    ctypes.memmove(ctypes.addressof(buffer) + _FileRenameInfo.FileName.offset, name, len(name))
    return bool(_kernel32().SetFileInformationByHandle(
        wintypes.HANDLE(msvcrt.get_osfhandle(fd)), 3, buffer, size,
    ))


def unique_json_pairs(on_duplicate):
    """Return an ``object_pairs_hook`` that rejects duplicate JSON object keys."""
    def unique(items):
        result = {}
        for key, value in items:
            if key in result:
                on_duplicate(key)
            result[key] = value
        return result
    return unique
