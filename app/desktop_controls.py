"""Canvas controls that match the rounded card style: a pill slider and a switch.

Both keep the small part of the Tk widget API the app relies on (``variable``,
``command``, ``set``/``get``, ``state`` and caller ``<ButtonRelease-1>`` /
``<KeyRelease>`` bindings) and draw from their current pixel size, so the
window DPI owner can rescale them like any other Canvas. Caller bindings append
with ``add="+"`` so interaction cleanup runs before the caller commits a value.
"""
from __future__ import annotations

import tkinter as tk

from desktop_theme import LINE, PRIMARY, PRIMARY_HOVER, SUBTLE, SURFACE

TRACK_OFF = "#dfe5f0"
THUMB_RING = "#c9d6f6"
DISABLED_FILL = "#b7c1d3"
KEYS = {"Left": -1, "Down": -1, "Right": 1, "Up": 1, "Prior": 10, "Next": -10}


class PillSlider(tk.Canvas):
    """Horizontal slider: rounded track, filled progress and a round thumb."""

    def __init__(self, master, *, from_=0, to=100, variable=None, command=None, state="normal",
                 height=22, bg=None, resolution=1, cursor="hand2", **kwargs):
        bg = bg or master.cget("bg")
        super().__init__(master, height=height, bg=bg, highlightthickness=0, bd=0,
                         cursor=cursor if state != "disabled" else "arrow",
                         takefocus=state != "disabled", **kwargs)
        self._from, self._to = float(from_), float(to)
        self._resolution = max(1, int(resolution))
        self._variable = variable if variable is not None else tk.IntVar(self, value=int(from_))
        self._command = command
        # Like tk.Scale, cget("command") names a Tcl command taking the value.
        self._command_name = self.register(lambda value: command(value) if command else None)
        self._state = state
        self._hover = self._dragging = False
        self._trace = self._variable.trace_add("write", lambda *_args: self._draw())
        self.bind("<Configure>", lambda _event: self._draw(), add="+")
        self.bind("<ButtonPress-1>", self._press, add="+")
        self.bind("<B1-Motion>", self._drag, add="+")
        self.bind("<ButtonRelease-1>", self._release, add="+")
        self.bind("<Enter>", lambda _event: self._set_hover(True), add="+")
        self.bind("<Leave>", lambda _event: self._set_hover(False), add="+")
        self.bind("<KeyPress>", self._key, add="+")
        self.bind("<FocusIn>", lambda _event: self._draw(), add="+")
        self.bind("<FocusOut>", lambda _event: self._draw(), add="+")
        self.bind("<Destroy>", self._destroyed, add="+")

    # Scale-compatible API ---------------------------------------------------
    def get(self) -> int:
        try:
            return int(round(float(self._variable.get())))
        except (tk.TclError, ValueError):
            return int(self._from)

    def set(self, value) -> None:
        value = self._clamp(value)
        if value == self.get():
            return
        self._variable.set(value)
        if self._command is not None:
            self._command(str(value))

    def configure(self, cnf=None, **kwargs):
        if "state" in kwargs:
            self._state = kwargs.pop("state")
            if self._state == "disabled":
                self._dragging = False
            super().configure(cursor="hand2" if self._state != "disabled" else "arrow",
                              takefocus=self._state != "disabled")
            self._draw()
        for key in ("width", "sliderlength"):
            kwargs.pop(key, None)  # tk.Scale sizing; the canvas height carries the size.
        return super().configure(cnf, **kwargs)

    config = configure

    def cget(self, key):
        values = {"state": self._state, "variable": str(self._variable), "from": self._from,
                  "to": self._to, "command": self._command_name}
        return values[key] if key in values else super().cget(key)

    # Interaction -------------------------------------------------------------
    def _enabled(self) -> bool:
        return self._state != "disabled"

    def _clamp(self, value) -> int:
        value = round(float(value) / self._resolution) * self._resolution
        return int(max(min(self._from, self._to), min(max(self._from, self._to), value)))

    def _geometry(self):
        width, height = max(1, self.winfo_width()), max(1, self.winfo_height())
        # Reserve room for the focus ring and the pressed thumb at every DPI.
        radius = max(4, height // 2 - 4)
        return width, height, radius, radius + 4, width - radius - 4

    def _value_at(self, x: float) -> int:
        _width, _height, _radius, left, right = self._geometry()
        ratio = 0.0 if right <= left else (x - left) / (right - left)
        return self._clamp(self._from + max(0.0, min(1.0, ratio)) * (self._to - self._from))

    def _press(self, event):
        if not self._enabled():
            return
        self.focus_set()
        self._dragging = True
        self.set(self._value_at(event.x))
        self._draw()

    def _drag(self, event):
        if self._enabled() and self._dragging:
            self.set(self._value_at(event.x))

    def _release(self, _event):
        self._dragging = False
        self._draw()

    def _set_hover(self, hover: bool) -> None:
        self._hover = hover
        self._draw()

    def _key(self, event):
        if not self._enabled():
            return
        if event.keysym == "Home":
            self.set(self._from)
        elif event.keysym == "End":
            self.set(self._to)
        elif event.keysym in KEYS:
            self.set(self.get() + KEYS[event.keysym] * self._resolution)
        else:
            return
        return "break"

    def _destroyed(self, event):
        if event.widget is self:
            try:
                self._variable.trace_remove("write", self._trace)
            except (tk.TclError, ValueError):
                pass

    # Drawing -------------------------------------------------------------------
    def _draw(self) -> None:
        if not self.winfo_exists():
            return
        self.delete("all")
        width, height, radius, left, right = self._geometry()
        middle = height / 2
        track = max(4, round(height * 0.28))
        span = (self._to - self._from) or 1.0
        x = left + (right - left) * (self.get() - self._from) / span
        enabled = self._enabled()
        fill = (PRIMARY_HOVER if self._dragging else PRIMARY) if enabled else DISABLED_FILL
        self.create_line(left, middle, right, middle, width=track, fill=TRACK_OFF, capstyle="round")
        if x > left:
            self.create_line(left, middle, x, middle, width=track, fill=fill, capstyle="round")
        grow = 2 if enabled and (self._hover or self._dragging) else 0
        if enabled and (self._hover or self._dragging or self.focus_get() is self):
            ring = radius + 3
            self.create_oval(x - ring, middle - ring, x + ring, middle + ring, fill=THUMB_RING, outline="")
        knob = radius - 1 + grow
        self.create_oval(x - knob, middle - knob + 1, x + knob, middle + knob + 1, fill=LINE, outline="")
        self.create_oval(x - knob, middle - knob, x + knob, middle + knob, fill=SURFACE,
                         outline=fill, width=max(2, round(height * 0.1)))


class ToggleSwitch(tk.Canvas):
    """On/off pill switch. ``command`` runs on a user click with the new value."""

    def __init__(self, master, *, variable=None, command=None, state="normal",
                 width=42, height=24, bg=None, **kwargs):
        bg = bg or master.cget("bg")
        super().__init__(master, width=width, height=height, bg=bg, highlightthickness=0, bd=0,
                         cursor="hand2" if state != "disabled" else "arrow",
                         takefocus=state != "disabled", **kwargs)
        self._variable = variable
        self._value = bool(variable.get()) if variable is not None else False
        self._command = command
        self._state = state
        self._trace = (variable.trace_add("write", lambda *_args: self._sync_variable())
                       if variable is not None else None)
        self.bind("<Configure>", lambda _event: self._draw(), add="+")
        self.bind("<ButtonRelease-1>", self._click, add="+")
        self.bind("<KeyPress-space>", self._click, add="+")
        self.bind("<KeyPress-Return>", self._click, add="+")
        self.bind("<FocusIn>", lambda _event: self._draw(), add="+")
        self.bind("<FocusOut>", lambda _event: self._draw(), add="+")
        self.bind("<Destroy>", self._destroyed, add="+")

    def get(self) -> bool:
        return self._value

    def set(self, value) -> None:
        """Show a value decided elsewhere; never runs ``command``."""
        value = bool(value)
        if value != self._value:
            self._value = value
            self._draw()

    def configure(self, cnf=None, **kwargs):
        if "state" in kwargs:
            self._state = kwargs.pop("state")
            super().configure(cursor="hand2" if self._state != "disabled" else "arrow",
                              takefocus=self._state != "disabled")
            self._draw()
        return super().configure(cnf, **kwargs)

    config = configure

    def cget(self, key):
        return self._state if key == "state" else super().cget(key)

    def _sync_variable(self):
        try:
            self.set(self._variable.get())
        except tk.TclError:
            pass

    def _click(self, _event=None):
        if self._state == "disabled":
            return
        value = not self._value
        if self._variable is not None:
            self._variable.set(value)
        self.set(value)
        if self._command is not None:
            self._command(value)
        return "break"

    def _destroyed(self, event):
        if event.widget is self and self._trace is not None:
            try:
                self._variable.trace_remove("write", self._trace)
            except (tk.TclError, ValueError):
                pass

    def _draw(self) -> None:
        if not self.winfo_exists():
            return
        self.delete("all")
        width, height = max(1, self.winfo_width()), max(1, self.winfo_height())
        pad = max(2, round(height * 0.12))
        enabled = self._state != "disabled"
        on = self._value
        track = (PRIMARY if enabled else DISABLED_FILL) if on else (TRACK_OFF if enabled else LINE)
        radius = height / 2
        self.create_oval(0, 0, height, height, fill=track, outline="")
        self.create_oval(width - height, 0, width, height, fill=track, outline="")
        self.create_rectangle(radius, 0, width - radius, height, fill=track, outline="")
        knob = height - 2 * pad
        x = width - pad - knob if on else pad
        self.create_oval(x, pad + 1, x + knob, pad + knob + 1, fill=SUBTLE if not enabled else "#c7d0e0",
                         outline="")
        self.create_oval(x, pad, x + knob, pad + knob, fill=SURFACE, outline="")
