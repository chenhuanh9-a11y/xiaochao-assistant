"""Fresh link/reparse checks without querying unused Windows file identity."""
import os
import stat


_get_attributes = None
if os.name == 'nt':
    import ctypes
    from ctypes import wintypes

    _kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    _get_attributes = _kernel.GetFileAttributesW
    _get_attributes.argtypes = [wintypes.LPCWSTR]
    _get_attributes.restype = wintypes.DWORD


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
