"""Optional native support window. No game runtime, network or payment actions."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
import tkinter as tk

from PIL import Image, ImageTk

from desktop_display import WindowDisplay
from desktop_rounded import RoundedButton, set_rounded_scale
from desktop_theme import (BG, SURFACE, TEXT, MUTED, PRIMARY, BUTTON_BG,
                           BUTTON_TEXT, ERROR, FONT)


ASSET_ROOT = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parents[1])) / "assets" / "support"
METHODS = {"wechat": ("微信", "wechat.png"), "alipay": ("支付宝", "alipay.png")}


def load_code(asset_root: Path, method: str) -> Image.Image:
    """Read only the two approved derivatives, without decoding payment data."""
    with Image.open(Path(asset_root) / METHODS[method][1]) as image:
        image.load()
        return image.copy()


def fit_code(image: Image.Image, size: int) -> Image.Image:
    """Keep the full quiet zone and hard pixel edges; never enlarge the source."""
    target = min(max(1, int(size)), image.width, image.height)
    return image.resize((target, target), Image.Resampling.NEAREST)


class SupportWindow:
    def __init__(self, parent, *, context_provider=None, asset_root=None, on_close=None):
        self.window = tk.Toplevel(parent)
        self.window.withdraw()
        self.window.title("支持星图")
        self.window.configure(bg=BG)
        if parent.winfo_viewable():
            self.window.transient(parent)
        self.root = self.window._root()
        self.asset_root = Path(asset_root) if asset_root is not None else ASSET_ROOT
        self._on_close = on_close
        self._closed = False
        self._method = "wechat"
        self._scale = 1.0
        self._render_job = None
        self._render_key = None
        self._wrap_width = None
        self._sources = {}
        self._photo = None
        self.display = WindowDisplay(self.window, context_provider or object(),
                                     preferred=(440, 620), minimum=(320, 450),
                                     on_scale=self._display_changed)
        self._build()
        self._bind_actions()
        self.display.start(parent)

    @property
    def closed(self):
        return self._closed

    def _build(self):
        body = tk.Frame(self.window, bg=BG)
        body.pack(fill="both", expand=True, padx=24, pady=24)
        body.columnconfigure(0, weight=1)
        body.rowconfigure(3, weight=1)
        tk.Label(body, text="支持星图", bg=BG, fg=TEXT,
                 font=(FONT, 20, "bold"), anchor="w").grid(row=0, column=0, sticky="ew")
        self.intro = tk.Label(body, text="如果星图帮到了你，可以自愿支持开发。", bg=BG, fg=MUTED,
                              font=(FONT, 10), anchor="w", justify="left", wraplength=360)
        self.intro.grid(row=1, column=0, sticky="ew", pady=(8, 18))
        choices = tk.Frame(body, bg=BG)
        choices.grid(row=2, column=0, sticky="ew", pady=(0, 16))
        self.buttons = {}
        for column, (method, (name, _filename)) in enumerate(METHODS.items()):
            choices.columnconfigure(column, weight=1)
            button = RoundedButton(choices, text=name, command=lambda value=method: self.select(value),
                                   bg=BUTTON_BG, fg=BUTTON_TEXT, highlightthickness=0,
                                   font=(FONT, 10, "bold"), padx=12, pady=9, cursor="hand2")
            button.grid(row=0, column=column, sticky="ew", padx=(0, 6) if column == 0 else (6, 0))
            self.buttons[method] = button
        self.image_host = tk.Frame(body, bg=SURFACE, width=360, height=340)
        self.image_host.grid(row=3, column=0, sticky="nsew")
        self.image_host.grid_propagate(False)
        self.image_label = tk.Label(self.image_host, bg=SURFACE, bd=0)
        self.image_label.place(relx=.5, rely=.5, anchor="center")
        self.status = tk.Label(body, bg=BG, fg=MUTED, font=(FONT, 9), height=2,
                               anchor="center", justify="center", wraplength=360)
        self.status.grid(row=4, column=0, sticky="ew", pady=(8, 0))
        self.retry_button = RoundedButton(body, text="重新加载", command=self.retry,
                                          bg=BUTTON_BG, fg=BUTTON_TEXT, highlightthickness=0,
                                          font=(FONT, 9), padx=12, pady=6, cursor="hand2")
        self.note = tk.Label(body, text="免费功能照常使用，随时可以关闭。", bg=BG, fg=MUTED,
                             font=(FONT, 9), wraplength=360, justify="center")
        self.note.grid(row=6, column=0, sticky="ew", pady=(8, 14))
        RoundedButton(body, text="关闭", command=self.close, bg=BUTTON_BG, fg=BUTTON_TEXT,
                      highlightthickness=0, font=(FONT, 10), padx=18, pady=8,
                      cursor="hand2").grid(row=7, column=0)
        self.image_host.bind("<Configure>", lambda _event: self._queue_render())
        self._paint_selection()

    def _bind_actions(self):
        self.window.protocol("WM_DELETE_WINDOW", self.close)
        self.window.bind("<Escape>", self._escape)
        self.window.bind("<Destroy>", self._destroy, add="+")
        self.window.bind("<Configure>", self._configured, add="+")

    def _escape(self, _event=None):
        self.close()
        return "break"

    def _display_changed(self, scale):
        self._scale = scale
        set_rounded_scale(self.window, scale)
        self._fit_copy()
        self._queue_render(force=True)

    def _configured(self, event):
        if event.widget is self.window and not self._closed:
            self._fit_copy()

    def _fit_copy(self):
        width = max(120, self.window.winfo_width() - round(48 * self._scale))
        if width != self._wrap_width:
            self._wrap_width = width
            for label in (self.intro, self.status, self.note):
                label.configure(wraplength=width)

    def show(self):
        if self._closed:
            return
        self.window.deiconify()
        self.window.lift()
        self.window.focus_set()
        self._queue_render()

    def _paint_selection(self):
        for method, button in self.buttons.items():
            selected = method == self._method
            button.configure(bg=PRIMARY if selected else BUTTON_BG,
                             fg="white" if selected else BUTTON_TEXT)

    def select(self, method):
        if self._closed or method not in METHODS or method == self._method:
            return
        self._method = method
        self._paint_selection()
        self._queue_render(force=True)

    def retry(self):
        self._queue_render(force=True)

    def _cancel_render(self):
        if self._render_job is not None:
            try:
                self.root.after_cancel(self._render_job)
            except tk.TclError:
                pass  # The owning interpreter may already be shutting down.
            self._render_job = None

    def _queue_render(self, *, force=False):
        if self._closed:
            return
        self._cancel_render()
        if force:
            self._render_key = None
            self.image_label.configure(image="")
            self._photo = None
            self.status.configure(text="正在加载二维码…", fg=MUTED)
            self.retry_button.grid_remove()
        # Loading and PhotoImage creation both stay on the Tk event thread.
        self._render_job = self.root.after_idle(self._render)

    def _render(self):
        self._render_job = None
        if self._closed:
            return
        available = min(self.image_host.winfo_width(), self.image_host.winfo_height()) - round(16 * self._scale)
        if available <= 1:
            return  # The first Configure event retries after actual allocation.
        key = self._method, available
        if key == self._render_key:
            return
        try:
            if self._method not in self._sources:
                self._sources[self._method] = load_code(self.asset_root, self._method)
            with fit_code(self._sources[self._method], available) as preview:
                photo = ImageTk.PhotoImage(preview, master=self.window)
            self.image_label.configure(image=photo)
        except (OSError, ValueError, tk.TclError):
            self.image_label.configure(image="")
            self._photo = None
            self._render_key = None
            self.status.configure(text="二维码暂时无法加载，请重试或直接关闭。", fg=ERROR)
            self.retry_button.grid(row=5, column=0, pady=(4, 0))
            return
        self._photo = photo
        self._render_key = key
        self.status.configure(text=f"使用{METHODS[self._method][0]}扫一扫", fg=MUTED)
        self.retry_button.grid_remove()

    def close(self):
        if not self._closed:
            self.window.destroy()

    def _destroy(self, event):
        if event.widget is not self.window or self._closed:
            return
        self._closed = True
        self._cancel_render()
        self._photo = None
        for source in self._sources.values():
            source.close()
        self._sources.clear()
        if self._on_close is not None:
            self._on_close()


def open_support_window(parent, current=None, *, context_provider=None, asset_root=None):
    """Reuse one window per UI owner; construction alone never makes it visible."""
    if current is None or current.closed:
        current = SupportWindow(parent, context_provider=context_provider, asset_root=asset_root)
    current.show()
    return current


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check-assets", action="store_true", help="Check images without opening a window.")
    args = parser.parse_args(argv)
    if args.check_assets:
        checks = {}
        for method in METHODS:
            try:
                with load_code(ASSET_ROOT, method) as image:
                    checks[method] = dict(readable=True, size=list(image.size))
            except (OSError, ValueError):
                checks[method] = dict(readable=False)
        print(json.dumps(checks))
        return 0 if all(item["readable"] for item in checks.values()) else 1
    root = tk.Tk()
    root.withdraw()
    panel = SupportWindow(root, on_close=root.destroy)
    panel.show()
    root.mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
