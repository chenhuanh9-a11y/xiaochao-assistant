"""Local Tk desktop sizing; never changes interpreter scaling or game pixels."""
from __future__ import annotations

import ctypes
from ctypes import wintypes
import tkinter as tk
from tkinter import font as tkfont

from native_ui_layout import (MonitorContext, RectPx, UiDpiContext,
                              clamp_rect_to_work_area, format_tk_geometry)


def window_context(assistant, window, *, sync_layout=True):
    getter = getattr(assistant, "_ui_context_for_window_snapshot", None) if not sync_layout else None
    getter = getter or getattr(assistant, "_ui_context_for_window", None)
    if callable(getter):
        return getter(window)
    try:
        area = RectPx(*assistant._monitor_work_area())
    except (AttributeError, tk.TclError):
        area = RectPx(0, 0, window.winfo_screenwidth(), window.winfo_screenheight())
    dpi = max(96, round(window.winfo_fpixels("1i")))
    return UiDpiContext(MonitorContext("desktop-fallback", area, area, dpi), dpi)


class WindowDisplay:
    """Own one Toplevel's fonts, DIP spacing and work-area bounds.

    Base widget metrics are captured once, before applying this window's scale.
    Negative pixel fonts make the window independent of the orb's global Tk
    scaling. Timers belong to the true Tk root, matching runtime shutdown.
    """

    def __init__(self, window, assistant, *, preferred, minimum, on_scale=None):
        self.window = window
        self.assistant = assistant
        self.root = window._root()
        self.preferred = preferred
        self.minimum = minimum
        self.on_scale = on_scale
        self.scale = 1.0
        self.context = None
        self._signature = None
        self._widgets = {}
        self._job = None
        self._updating = False
        self._closed = False
        window._desktop_display = self
        window.bind("<Configure>", self._configured, add="+")
        window.bind("<Map>", self._configured, add="+")
        window.bind("<Destroy>", self._destroy, add="+")

    def _configured(self, event):
        if event.widget is self.window and not self._closed and not self._updating:
            if self._job is not None:
                self.root.after_cancel(self._job)
            self._job = self.root.after(80, self.refresh)

    def _frame(self):
        """Native outer bounds include caption/borders, unlike Tk client size."""
        getter = getattr(self.assistant, "_native_window_handle", None)
        if callable(getter):
            rect = wintypes.RECT()
            if ctypes.windll.user32.GetWindowRect(getter(self.window), ctypes.byref(rect)):
                return RectPx(rect.left, rect.top, rect.right, rect.bottom)
        return RectPx(self.window.winfo_x(), self.window.winfo_y(),
                      self.window.winfo_x() + self.window.winfo_width(),
                      self.window.winfo_y() + self.window.winfo_height())

    def start(self, reference=None):
        context = window_context(self.assistant, reference or self.window)
        self.scale = context.scale
        area = context.monitor.work_area_px
        width, height = (round(value * self.scale) for value in self.preferred)
        width, height = min(width, area.width), min(height, area.height)
        self.window.geometry(format_tk_geometry(RectPx(
            area.left + (area.width - width) // 2,
            area.top + (area.height - height) // 2,
            area.left + (area.width + width) // 2,
            area.top + (area.height + height) // 2)))
        self.refresh()

    def apply_widgets(self, parent=None):
        if self._closed:
            return
        def lengths(value):
            parts = value if isinstance(value, (tuple, list)) else self.root.tk.splitlist(str(value))
            return tuple(round(float(part) * self.scale)
                         for part in parts)
        def visit(widget):
            if isinstance(widget, tk.Toplevel) and widget is not self.window:
                return
            if widget not in self._widgets:
                config, pack, grid, place = {}, {}, {}, {}
                options = widget.keys()
                if "font" in options:
                    actual = tkfont.Font(root=self.root, font=widget.cget("font")).actual()
                    size = actual.pop("size")
                    # Positive sizes are points; negative sizes already pixels.
                    config["font"] = (actual, size * 96 / 72 if size > 0 else -size)
                for key in ("padx", "pady", "borderwidth", "highlightthickness"):
                    if key in options:
                        config[key] = widget.cget(key)
                if not getattr(widget, "_display_manual_geometry", False):
                    if isinstance(widget, (tk.Frame, tk.Canvas, tk.Scrollbar)):
                        for key in ("width", "height"):
                            if key in options:
                                config[key] = widget.cget(key)
                    manager = widget.winfo_manager()
                    if manager in ("pack", "grid"):
                        info = widget.pack_info() if manager == "pack" else widget.grid_info()
                        target = pack if manager == "pack" else grid
                        target.update((key, info[key]) for key in ("padx", "pady", "ipadx", "ipady") if key in info)
                    elif manager == "place":
                        info = widget.place_info()
                        place.update((key, info[key]) for key in ("x", "y", "width", "height") if info.get(key) != "")
                self._widgets[widget] = config, pack, grid, place
            config, pack, grid, place = self._widgets[widget]
            values = {key: lengths(value)[0] for key, value in config.items() if key != "font"}
            if "font" in config:
                actual, pixels = config["font"]
                font_options = dict(actual, size=-max(1, round(pixels * self.scale)))
                font = getattr(widget, "_desktop_font", None)
                try:
                    reusable = isinstance(font, tkfont.Font) and font.configure() == font_options
                except tk.TclError:
                    if str(font) in tkfont.names(root=self.root):
                        raise
                    reusable = False  # An externally deleted named font.
                if not reusable:
                    font = tkfont.Font(root=self.root, **font_options)
                values["font"] = font
            if values:
                try:
                    widget.configure(**values)
                except Exception:
                    # Some widget wrappers can apply the native font before
                    # raising. Keep its owner only if it was actually consumed;
                    # otherwise the previous owner survives the failed update.
                    if ("font" in values and widget.winfo_exists()
                        and str(widget.cget("font")) == str(values["font"])):
                        widget._desktop_font = values["font"]
                    raise
                if "font" in values:
                    widget._desktop_font = values["font"]
            if pack and widget.winfo_manager() == "pack":
                widget.pack_configure(**{key: lengths(value) for key, value in pack.items()})
            if grid and widget.winfo_manager() == "grid":
                widget.grid_configure(**{key: lengths(value) for key, value in grid.items()})
            if place and widget.winfo_manager() == "place":
                widget.place_configure(**{key: lengths(value)[0] for key, value in place.items()})
            for child in widget.winfo_children():
                visit(child)
        # Remove dead dynamic cards/details; do not retain their PhotoImages.
        self._widgets = {widget: values for widget, values in self._widgets.items() if widget.winfo_exists()}
        visit(parent or self.window)

    def refresh(self):
        if self._closed or self._updating:
            return
        if self._job is not None:
            self.root.after_cancel(self._job)
            self._job = None
        # Before the first map, Windows can already reflect a requested rect
        # while Tk's client size/position still describe the old layout. Do not
        # combine those measurements and overwrite the pending geometry.
        if self.window.state() == "withdrawn":
            return
        self._updating = True
        try:
            context = window_context(self.assistant, self.window, sync_layout=False)
            signature = context.monitor.monitor_id, context.dpi, context.monitor.work_area_px
            if signature == self._signature:
                return
            self.context = context
            self._signature = signature
            self.scale = context.scale
            self.apply_widgets()
            if self.on_scale:
                self.on_scale(self.scale)
            self.window.update_idletasks()
            area = context.monitor.work_area_px
            outer = self._frame()
            border_w = max(0, outer.width - self.window.winfo_width())
            border_h = max(0, outer.height - self.window.winfo_height())
            margin = min(round(8 * self.scale), area.width // 8, area.height // 8)
            available_w = max(1, area.width - border_w - 2 * margin)
            available_h = max(1, area.height - border_h - 2 * margin)
            self.window.minsize(min(round(self.minimum[0] * self.scale), available_w),
                                min(round(self.minimum[1] * self.scale), available_h))
            if self.window.state() in {"normal", "withdrawn"}:
                width = min(self.window.winfo_width(), available_w)
                height = min(self.window.winfo_height(), available_h)
                target = RectPx(outer.left, outer.top, outer.left + width + border_w,
                                outer.top + height + border_h)
                fit = clamp_rect_to_work_area(target, area, margin_px=margin)
                x = fit.left + self.window.winfo_x() - outer.left
                y = fit.top + self.window.winfo_y() - outer.top
                geometry = format_tk_geometry(RectPx(x, y, x + width, y + height))
                if self.window.geometry() != geometry:
                    self.window.geometry(geometry)
        finally:
            self._updating = False

    def _destroy(self, event):
        if event.widget is self.window:
            self._closed = True
            self._widgets.clear()
            if self._job is not None:
                self.root.after_cancel(self._job)
                self._job = None
