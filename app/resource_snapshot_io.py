"""Bounded reads for immutable resource snapshot files.

The caller supplies the namespace-specific link check and rejection mapping so
existing readers can reuse the same handle/path identity checks unchanged.
"""
from __future__ import annotations

import hashlib
import os
import stat


def read_stable_file(path, max_bytes, *, no_links, reject, identity=None):
    """Read one regular file with bounded I/O and matching handle/path identity.

    ``reject`` receives ``invalid-metadata-file`` or ``metadata-changed``. I/O
    errors from opening or reading the path remain visible to the caller.
    """
    no_links(path)
    flags = (os.O_RDONLY | getattr(os, 'O_NONBLOCK', 0)
             | getattr(os, 'O_NOFOLLOW', 0) | getattr(os, 'O_BINARY', 0))
    descriptor = os.open(path, flags)
    with os.fdopen(descriptor, 'rb') as handle:
        before = os.fstat(handle.fileno())
        if not stat.S_ISREG(before.st_mode) or before.st_size > max_bytes:
            reject('invalid-metadata-file')
        raw = handle.read(max_bytes + 1)
        after = os.fstat(handle.fileno())

    signature = lambda info: (info.st_dev, info.st_ino, info.st_size,
                              info.st_mtime_ns, info.st_ctime_ns)
    no_links(path)
    # On CPython 3.13/Windows, fstat().st_ctime is ChangeTime while pathname
    # stat().st_ctime is birthtime. Reopen and compare using the same API.
    current_descriptor = os.open(path, flags)
    try:
        current = os.fstat(current_descriptor)
    finally:
        os.close(current_descriptor)
    no_links(path)
    if (len(raw) > max_bytes or len(raw) != after.st_size
            or not stat.S_ISREG(current.st_mode)
            or signature(before) != signature(after)
            or signature(current) != signature(after)):
        reject('metadata-changed')

    if identity is None:
        identity_value = signature(after)
    else:
        identity_value = identity(after)
    row = dict(path=path.name, bytes=len(raw),
               sha256=hashlib.sha256(raw).hexdigest(), identity=identity_value)
    return raw, row
