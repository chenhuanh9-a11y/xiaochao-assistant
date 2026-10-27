"""One transient presentation; the controller owns event meaning and native host."""
from __future__ import annotations

import tkinter as tk

from desktop_theme import SURFACE, LINE, TEXT, FONT, GREEN, ERROR


class ToastPresenter(tk.Frame):
    """Consume a notification snapshot without creating a window or taking focus.

    Native host positioning, NOACTIVATE and mouse transparency belong to the
    caller's on_show/on_hide/on_alpha callbacks. No business state is inferred.
    """

    def __init__(self, parent, *, on_show, on_hide, on_alpha, scale=1.0):
        super().__init__(parent, bg=SURFACE, highlightbackground=LINE,
                         highlightthickness=1, bd=0)
        self._on_show, self._on_hide, self._on_alpha = on_show, on_hide, on_alpha
        self._jobs = set()
        self._serial = 0
        self._last_token = None
        self._visible = False
        self._closed = False
        self._kind = "success"
        self.icon = tk.Canvas(self, bg=SURFACE, highlightthickness=0, bd=0)
        self.icon.pack(side="left", padx=(0, 10))
        self._circle = self.icon.create_oval(0, 0, 1, 1, fill=GREEN, outline="")
        self._mark = self.icon.create_line(0, 0, 1, 1, fill="white", width=2)
        self.message = tk.Label(self, bg=SURFACE, fg=TEXT, anchor="w", justify="left")
        self.message.pack(side="left", fill="x", expand=True)
        self.set_display_scale(scale)
        self.bind("<Destroy>", self._destroyed, add="+")

    def set_display_scale(self, scale):
        self._scale = max(.5, float(scale))
        px = lambda value: max(1, round(value * self._scale))
        self.configure(padx=px(14), pady=px(10), highlightthickness=px(1))
        self.icon.configure(width=px(20), height=px(20))
        self.icon.pack_configure(padx=(0, px(10)))
        self.message.configure(font=(FONT, max(8, round(10 * self._scale))),
                               wraplength=px(400))
        self._paint_icon()

    def _paint_icon(self):
        scale = self._scale
        self.icon.coords(self._circle, *(round(v * scale) for v in (1, 1, 19, 19)))
        self.icon.itemconfigure(self._circle, fill=ERROR if self._kind == "error" else GREEN)
        coords = (10, 5, 10, 11, 10, 14) if self._kind == "error" else (5, 10, 9, 14, 15, 6)
        self.icon.coords(self._mark, *(round(v * scale) for v in coords))
        self.icon.itemconfigure(self._mark, width=max(1, round(2 * scale)))

    def _cancel_jobs(self):
        for handle in tuple(self._jobs):
            try:
                self.after_cancel(handle)
            except tk.TclError:
                pass  # The owning Tcl interpreter may already be closing.
        self._jobs.clear()

    def _later(self, delay, callback):
        serial = self._serial
        handle = None

        def deliver():
            self._jobs.discard(handle)
            if not self._closed and serial == self._serial:
                callback()

        handle = self.after(max(0, int(delay)), deliver)
        self._jobs.add(handle)

    def render(self, snapshot):
        if self._closed:
            return
        if snapshot.get("visible") is not True:
            self.clear()
            return
        token = snapshot.get("token")
        text = str(snapshot.get("message") or "").strip()
        if token is None or not text or token == self._last_token:
            return
        self._cancel_jobs()
        self._serial += 1
        self._last_token = token
        self._kind = snapshot.get("kind", "success")
        self.message.configure(text=text)
        self._paint_icon()
        self._on_alpha(1.0)
        self._on_show()
        self._visible = True
        self._later(snapshot.get("durationMs", 3200), lambda: self._fade(1))

    def _fade(self, step):
        if step >= 8:
            if self._visible:
                self._on_hide()
            self._visible = False
            return
        self._on_alpha(1.0 - step / 8)
        self._later(40, lambda: self._fade(step + 1))

    def clear(self):
        if self._closed:
            return
        self._serial += 1
        self._cancel_jobs()
        if self._visible:
            self._on_hide()
        self._visible = False

    def close(self):
        if not self._closed:
            self.clear()
            self._closed = True

    def _destroyed(self, event):
        if event.widget is self:
            self._serial += 1
            self._cancel_jobs()
            self._closed = True
            self._visible = False

    def destroy(self):
        self.close()
        super().destroy()
