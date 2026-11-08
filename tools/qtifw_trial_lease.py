"""Keep a trial uninstaller's resource lease in the actual native parent.

The support helper is one-folder, so its direct parent is QtIFW. The native
Windows cleanup waits for that EXE to exit, which also releases this handle.
"""
import ctypes
from ctypes import wintypes as wt
import hashlib
import os
from pathlib import Path
import stat

from resource_transaction import _safe
from installer_resource_lease import _kernel, _FileInfo, _final, _parent


def inspect_installation_objects(target, lock_path):
    """Allow hard-link aliases only when every name stays in this installation."""
    aliases = {}
    for path in Path(target).rglob("*"):
        _safe(path)
        if path == lock_path:
            continue  # checked through the live exclusive native handle
        current = path.stat()
        if stat.S_ISDIR(current.st_mode):
            continue
        if not stat.S_ISREG(current.st_mode):
            raise ValueError("installation-object-not-regular-file")
        if current.st_nlink != 1:
            identity = (current.st_dev, current.st_ino)
            expected, count = aliases.get(identity, (current.st_nlink, 0))
            if expected != current.st_nlink:
                raise ValueError("installation-hardlinks-changed")
            aliases[identity] = (expected, count + 1)
    if any(expected != count for expected, count in aliases.values()):
        raise ValueError("installation-hardlink-outside-target")


def hold_uninstaller_lease(target):
    if os.name != "nt":
        raise ValueError("windows-uninstall-lease-required")
    program = _safe(Path(target) / "program")
    directory = _safe(program / "work/map-packages")
    lock_path = _safe(directory / "import.lock")
    expected_parent = _safe(Path(target) / "trial-maintenance.exe")
    parent_pid = os.getppid()
    api = _kernel()
    invalid = ctypes.c_void_p(-1).value
    parent = api.OpenProcess(0x100000 | 0x1000 | 0x40, False, parent_pid)
    local = root_handle = None
    remote = wt.HANDLE()
    try:
        if not parent:
            raise ValueError("uninstaller-parent-open-denied")
        times = [wt.FILETIME() for _ in range(4)]
        if not api.GetProcessTimes(parent, *(ctypes.byref(value) for value in times)):
            raise ValueError("uninstaller-parent-time-unavailable")
        created = str((times[0].dwHighDateTime << 32) | times[0].dwLowDateTime)
        digest = hashlib.sha256(expected_parent.read_bytes()).hexdigest()
        _parent(api, parent, parent_pid, created, expected_parent, digest)
        program.mkdir(exist_ok=True)
        directory.mkdir(parents=True, exist_ok=True)
        # share=0 conflicts with both runtime leases and another uninstaller.
        local = api.CreateFileW(str(lock_path), 0xC0000000, 0, None, 4, 0x80, None)
        if local in (None, invalid):
            raise ValueError("uninstall-resource-busy")
        info = _FileInfo()
        root_handle = api.CreateFileW(str(program), 0, 7, None, 3, 0x02000000, None)
        if (root_handle in (None, invalid) or api.GetFileType(local) != 1
                or not api.GetFileInformationByHandle(local, ctypes.byref(info))
                or info.attributes & (0x10 | 0x400) or info.links != 1
                or _final(api, local) != _final(api, root_handle) + "\\work\\map-packages\\import.lock"):
            raise ValueError("uninstall-lock-object-mismatch")
        # Enumerate only under the live exclusive lock. Pending journals are
        # data to remove, not a requirement to make a broken install runnable.
        inspect_installation_objects(target, lock_path)
        _parent(api, parent, parent_pid, created, expected_parent, digest)
        if os.getppid() != parent_pid:
            raise ValueError("uninstaller-parent-changed")
        if not api.DuplicateHandle(api.GetCurrentProcess(), local, parent, ctypes.byref(remote), 0, False, 2):
            raise ValueError("uninstall-lease-transfer-failed")
        return dict(leaseHeld=True, parentPid=parent_pid, release="native-parent-exit")
    finally:
        # Never close the parent's duplicate on Finished/cancel. Native process
        # exit is the lifetime boundary; its stock VBS then removes TargetDir.
        for handle in (local, root_handle, parent):
            if handle not in (None, invalid):
                api.CloseHandle(handle)
