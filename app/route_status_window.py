"""Presentation-only route status window; all product facts belong to the caller."""

from __future__ import annotations

import ctypes
import os
import tkinter as tk
from ctypes import wintypes
from typing import Callable


class _NonActivatingWindow:
    """Map Tk's client and wrapper HWNDs without asking Windows for focus."""

    def __init__(self, window: tk.Toplevel) -> None:
        self.window = window
        self.user32 = ctypes.WinDLL("user32", use_last_error=True)
        self.user32.GetAncestor.argtypes = [wintypes.HWND, wintypes.UINT]
        self.user32.GetAncestor.restype = wintypes.HWND
        self.user32.GetWindowLongW.argtypes = [wintypes.HWND, ctypes.c_int]
        self.user32.GetWindowLongW.restype = wintypes.LONG
        self.user32.SetWindowLongW.argtypes = [wintypes.HWND, ctypes.c_int, wintypes.LONG]
        self.user32.SetWindowLongW.restype = wintypes.LONG
        self.user32.ShowWindow.argtypes = [wintypes.HWND, ctypes.c_int]
        self.user32.ShowWindow.restype = wintypes.BOOL
        self.user32.SetWindowPos.argtypes = [
            wintypes.HWND, wintypes.HWND, ctypes.c_int, ctypes.c_int,
            ctypes.c_int, ctypes.c_int, wintypes.UINT,
        ]
        self.user32.SetWindowPos.restype = wintypes.BOOL
        self.user32.IsWindowVisible.argtypes = [wintypes.HWND]
        self.user32.IsWindowVisible.restype = wintypes.BOOL

    def handles(self) -> tuple[int, int]:
        self.window.update_idletasks()
        child = self.window.winfo_id()
        wrapper = self.user32.GetAncestor(child, 2)  # GA_ROOT
        if not wrapper:
            raise ctypes.WinError(ctypes.get_last_error())
        return child, int(wrapper)

    def prepare(self) -> None:
        _, wrapper = self.handles()
        # Match the existing assistant's WS_EX_NOACTIVATE / WS_EX_TOOLWINDOW.
        style = self.user32.GetWindowLongW(wrapper, -20)  # GWL_EXSTYLE
        ctypes.set_last_error(0)
        previous = self.user32.SetWindowLongW(wrapper, -20, style | 0x08000080)
        if not previous and ctypes.get_last_error():
            raise ctypes.WinError(ctypes.get_last_error())

    def show(self) -> bool:
        self.prepare()
        child, wrapper = self.handles()
        # A withdrawn Tk toplevel has an unmapped client as well as a wrapper.
        # Native mapping also updates Tk's wm state, without wm deiconify/lift.
        self.user32.ShowWindow(child, 4)  # SW_SHOWNOACTIVATE
        self.user32.ShowWindow(wrapper, 4)
        if not self.user32.SetWindowPos(
            wrapper, wintypes.HWND(-1), 0, 0, 0, 0,
            0x0001 | 0x0002 | 0x0010 | 0x0020 | 0x0040,
            # NOSIZE | NOMOVE | NOACTIVATE | FRAMECHANGED | SHOWWINDOW
        ):
            raise ctypes.WinError(ctypes.get_last_error())
        self.window.update_idletasks()
        return bool(self.user32.IsWindowVisible(wrapper))


class RouteStatusWindow:
    """Render caller-owned snapshots on the Tk thread.

    ``update`` returns current visibility. Only severity ``error`` with an explicit
    nonempty episode_id may show automatically. Dismissal suppresses that episode
    until an explicit ``update(..., automatic=False)``. ``hide`` only hides; it does
    not dismiss, acknowledge, retry, or change the caller's lifecycle state.

    Snapshot fields: episode_id, severity, title, message, phase (retained only,
    never translated into product facts), optional action_label/retry_enabled.
    Optional anchor_rect/work_area are (left, top, right, bottom) in the caller's
    Tk window coordinate space; negative monitor coordinates are preserved.
    Callbacks receive episode_id. The optional logger takes ``event, **fields``.
    Construction leaves the window withdrawn. There are no timers or global binds.
    """

    def __init__(
        self, root: tk.Misc, on_dismiss: Callable[[str], None] | None = None,
        on_retry: Callable[[str], None] | None = None, log: Callable | None = None,
    ) -> None:
        self._on_dismiss = on_dismiss
        self._on_retry = on_retry
        self._log = log
        self._episode_id = ""
        self._dismissed: set[str] = set()
        self._snapshot: dict = {}
        self._destroyed = False
        self._shown = False
        self._positioned = False
        self._drag_anchor: tuple[int, int, int, int] | None = None
        self.window = tk.Toplevel(root)
        self.window.withdraw()
        self.window.title("小抄助手 · 路线状态")
        self.window.overrideredirect(True)
        self.window.configure(bg="#0f1b29")
        self.window.protocol("WM_DELETE_WINDOW", self._dismiss)
        self.window.bind("<Escape>", lambda _event: self._dismiss())
        self.window.bind("<Destroy>", self._on_destroy)
        self.window.minsize(300, 120)

        shell = tk.Frame(
            self.window, bg="#0f1b29", highlightthickness=1,
            highlightbackground="#526477",
        )
        shell.pack(fill="both", expand=True)
        header = tk.Frame(shell, bg="#0b1622")
        header.pack(fill="x")
        self.title_label = tk.Label(
            header, text="路线状态", bg="#0b1622", fg="#f8fafc",
            font=("Microsoft YaHei UI", 10, "bold"), anchor="w",
            justify="left", wraplength=260, padx=12, pady=10,
        )
        self.title_label.pack(side="left", fill="x", expand=True)
        self.close_button = tk.Button(
            header, text="关闭", command=self._dismiss,
            bg="#0b1622", fg="#b5c5d4", activebackground="#26384a",
            activeforeground="#ffffff", relief="flat", bd=0,
            font=("Microsoft YaHei UI", 9), padx=10, pady=7,
            cursor="hand2", takefocus=True,
        )
        self.close_button.pack(side="right", padx=(0, 5))
        for drag_surface in (header, self.title_label):
            drag_surface.bind("<ButtonPress-1>", self._begin_drag)
            drag_surface.bind("<B1-Motion>", self._drag)
            drag_surface.bind("<ButtonRelease-1>", self._end_drag)

        self.message_label = tk.Label(
            shell, text="", bg="#0f1b29", fg="#dbe7f1",
            font=("Microsoft YaHei UI", 10), anchor="nw", justify="left",
            wraplength=348, padx=14, pady=12,
        )
        self.message_label.pack(fill="x")
        self.actions = tk.Frame(shell, bg="#0f1b29")
        self.actions.pack(fill="x", padx=14, pady=(0, 12))
        self.retry_button = tk.Button(
            self.actions, text="重新尝试", command=self._retry,
            bg="#167c58", fg="#ffffff", activebackground="#126947",
            activeforeground="#ffffff", disabledforeground="#b1bfcc",
            relief="flat", bd=0, font=("Microsoft YaHei UI", 9),
            padx=12, pady=7, cursor="hand2", takefocus=True,
        )
        # The caller may supply a monitor work area on first presentation. Never
        # clamp negative desktop coordinates to the primary monitor.
        root.update_idletasks()
        self._default_anchor = (
            root.winfo_x(), root.winfo_y(),
            root.winfo_x() + root.winfo_width(), root.winfo_y() + root.winfo_height(),
        )
        self.window.geometry("380x160")
        self._native = _NonActivatingWindow(self.window) if os.name == "nt" else None
        if self._native:
            self._native.prepare()
        self._emit("route-status-window-created")

    @property
    def visible(self) -> bool:
        if self._destroyed or not self._shown:
            return False
        try:
            return bool(self.window.winfo_viewable())
        except tk.TclError:
            return False

    @property
    def episode_id(self) -> str:
        return self._episode_id

    def update(self, snapshot: dict, *, automatic: bool = True) -> bool:
        if self._destroyed:
            return False
        self._snapshot = dict(snapshot)
        self._episode_id = str(snapshot.get("episode_id") or "")
        severity = str(snapshot.get("severity") or "normal").lower()
        self.title_label.configure(
            text=str(snapshot.get("title") or "路线状态"),
            fg={"error": "#ffd7a3", "warning": "#ffd7a3",
                "normal": "#5ee0a3", "success": "#5ee0a3"}.get(severity, "#9fc3e5"),
        )
        self.message_label.configure(text=str(snapshot.get("message") or ""))
        action_label = str(snapshot.get("action_label") or "重新尝试")
        retry_enabled = bool(snapshot.get("retry_enabled", False)) and self._on_retry is not None
        if self._on_retry and (snapshot.get("action_label") or retry_enabled):
            self.retry_button.configure(text=action_label, state="normal" if retry_enabled else "disabled")
            self.retry_button.pack(side="left")
        else:
            self.retry_button.pack_forget()
        self.window.update_idletasks()
        # Keep the user's position; grow vertically for wrapped caller copy.
        previous_height = self.window.winfo_height()
        height = max(120, self.window.winfo_reqheight())
        self.window.geometry(f"380x{height}")
        area = self._rect(snapshot.get("work_area"))
        if self._shown and height != previous_height and area:
            x, y = self.window.winfo_x(), self.window.winfo_y()
            left, top, right, bottom = area
            if x < left or x + 380 > right or y < top or y + height > bottom:
                self._place(snapshot, height)
        should_show = not automatic or (
            severity == "error" and bool(self._episode_id)
            and self._episode_id not in self._dismissed
        )
        if not self._shown and should_show:
            try:
                self._place(snapshot, height)
                if self._native:
                    self._shown = self._native.show()
                else:
                    self.window.deiconify()
                    self._shown = True
            except (tk.TclError, OSError) as exc:
                self.window.withdraw()
                self._shown = False
                self._emit("route-status-window-show-failed", error=str(exc))
            if self._shown:
                self._emit("route-status-window-shown", automatic=bool(automatic))
        self._emit("route-status-window-updated", severity=severity, automatic=bool(automatic))
        return self.visible

    @staticmethod
    def _rect(value) -> tuple[int, int, int, int] | None:
        try:
            left, top, right, bottom = (int(part) for part in value)
            if right > left and bottom > top:
                return left, top, right, bottom
        except (TypeError, ValueError, OverflowError):
            pass
        return None

    def _place(self, snapshot: dict, height: int) -> None:
        area = self._rect(snapshot.get("work_area"))
        if self._positioned:
            x, y = self.window.winfo_x(), self.window.winfo_y()
        else:
            anchor = self._rect(snapshot.get("anchor_rect")) or self._default_anchor
            x, y = anchor[2] + 12, anchor[1]
            if area and x + 380 > area[2]:
                x = anchor[0] - 380 - 12
        if area:
            left, top, right, bottom = area
            x = max(left, min(x, right - 380))
            y = max(top, min(y, bottom - height))
        # Keep a literal '+' before a negative coordinate: '+-888' is absolute
        # desktop x=-888; '-888' alone is a distance from the screen's right edge.
        self.window.geometry(f"380x{height}+{x}+{y}")
        self._positioned = True

    def hide(self, reason: str = "caller") -> None:
        if self._destroyed or not self._shown:
            return
        self.window.withdraw()
        self._shown = False
        self._emit("route-status-window-hidden", reason=reason)

    def destroy(self) -> None:
        if self._destroyed:
            return
        self.window.destroy()

    def _on_destroy(self, event) -> None:
        if event.widget is self.window:
            self._destroyed = True
            self._shown = False
            self._emit("route-status-window-destroyed")

    def _dismiss(self) -> None:
        if self._destroyed:
            return
        if self._episode_id:
            self._dismissed.add(self._episode_id)
        self.hide("dismiss")
        if self._on_dismiss:
            self._on_dismiss(self._episode_id)

    def _retry(self) -> None:
        if self._on_retry and self.retry_button.cget("state") == "normal":
            self._on_retry(self._episode_id)

    def _begin_drag(self, event) -> None:
        self._drag_anchor = (event.x_root, event.y_root, self.window.winfo_x(), self.window.winfo_y())

    def _drag(self, event) -> None:
        if self._drag_anchor:
            x, y, origin_x, origin_y = self._drag_anchor
            target_x, target_y = origin_x + event.x_root - x, origin_y + event.y_root - y
            self.window.geometry(f"+{target_x}+{target_y}")

    def _end_drag(self, _event) -> None:
        self._drag_anchor = None

    def _emit(self, event: str, **fields) -> None:
        if self._log:
            self._log(event, episode_id=self._episode_id, **fields)
