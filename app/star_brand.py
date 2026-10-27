"""Original Xingtu artwork, fitted only for the existing Tk image slots."""
from pathlib import Path
import sys
import tkinter as tk
from PIL import Image, ImageOps, ImageTk

RESOURCE_ROOT = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parents[1]))


class BrandImages:
    FILES = {"round": "xingtu-round.png", "square": "xingtu-square.png"}

    def __init__(self, master, *, resource_root=None):
        self.master = master
        self.resource_root = Path(resource_root) if resource_root is not None else RESOURCE_ROOT
        self._sources = {}
        self._photos = {}
        self._closed = False

    def get(self, shape, size):
        if self._closed:
            return None
        size = max(1, int(size))
        previous = self._photos.get(shape)
        if previous is not None and previous[0] == size:
            return previous[1]
        if shape not in self._sources:
            try:
                with Image.open(self.resource_root / "assets" / "brand" / self.FILES[shape]) as source:
                    self._sources[shape] = source.convert("RGBA")
            except (OSError, ValueError):
                self._sources[shape] = None
        source = self._sources[shape]
        if source is None:
            return None
        try:
            with ImageOps.contain(source, (size, size), Image.Resampling.LANCZOS) as preview:
                photo = ImageTk.PhotoImage(preview, master=self.master)
        except (OSError, ValueError, tk.TclError):
            return None
        # Keep one current display size per shape. Window icons are retained
        # explicitly by their owning view, not by an unbounded DPI cache.
        self._photos[shape] = size, photo
        return photo

    def window_icons(self, window):
        photos = tuple(photo for size in (16, 24, 32, 48, 64, 128, 256)
                       if (photo := self.get("square", size)) is not None)
        if photos:
            try:
                window.iconphoto(False, *photos)
            except tk.TclError:
                return ()
        return photos

    def close(self):
        self._closed = True
        self._photos.clear()
        for source in self._sources.values():
            if source is not None:
                source.close()
        self._sources.clear()
