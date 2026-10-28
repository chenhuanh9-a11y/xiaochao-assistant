"""Optional codec exclusions shared by Windows PyInstaller recipes.

Image capture and PNG/JPEG/WebP still require cv2/Pillow themselves. Never
exclude those packages or a shared C runtime to remove one optional codec.
"""
from __future__ import annotations

import re

PYTHON_MODULE_EXCLUSIONS = ("PIL.AvifImagePlugin", "PIL._avif")


def excluded_binary(destination: str) -> bool:
    name = str(destination).replace("\\", "/").rsplit("/", 1)[-1].casefold()
    return bool(re.fullmatch(r"opencv_videoio_ffmpeg\d+(?:_64)?\.dll", name)
                or (name.startswith("_avif.") and name.endswith(".pyd")))


def filter_binaries(entries):
    """Return retained and excluded TOC entries, preserving their full tuples."""
    retained, removed = [], []
    for entry in entries:
        (removed if excluded_binary(entry[0]) else retained).append(entry)
    return retained, removed
