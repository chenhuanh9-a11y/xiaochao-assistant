"""Manual map selection (选图方式：手动选图) without identity recognition.

The picker follows the earlier desktop quick picker: choose a group, then one
of four maps per page with F1–F4 (PageUp/PageDown to turn pages, Backspace to
go back, Esc to close). Choosing a map commits it exactly like confirming a
candidate, so fitting and the reference sheet then follow that identity.
"""
from __future__ import annotations

import tkinter as tk

from desktop_theme import (BUTTON_BG, BUTTON_TEXT, FONT, IMAGE_BG, LINE, MUTED,
                           SURFACE, TEXT, TINT)

# Toolkit-free rules live in manual_map_picker_core; re-exported for existing callers.
from manual_map_picker_core import (  # noqa: F401
    SELECTION_MODES,
    SELECTION_MODE_LABELS,
    NIGHTMARE_GROUP_ORDER,
    OTHER_GROUP,
    OPTION_KEYS,
    PAGE_SIZE,
    PICKER_KEY_CODES,
    THUMBNAIL_SIZE,
    MODE_TITLES,
    normalize_selection_mode,
    split_map_name,
    map_group,
    picker_groups,
    option_title,
    PickerState)


class ManualMapPicker(tk.Toplevel):
    """Borderless picker over the game; the owner makes it non-activating."""

    def __init__(self, master, *, on_choose, on_close, on_need_thumbnails, scale: float = 1.0):
        super().__init__(master, bg=LINE)
        self.withdraw()
        self.overrideredirect(True)
        self.attributes("-topmost", True)
        self._on_choose, self._on_close = on_choose, on_close
        self._on_need_thumbnails = on_need_thumbnails
        self._scale = max(0.75, float(scale or 1.0))
        self._photos: dict[str, object] = {}
        self.thumbnail_size = (self._px(THUMBNAIL_SIZE[0]), self._px(THUMBNAIL_SIZE[1]))
        # An image keeps empty cards at their final pixel size before decoding.
        self._placeholder = tk.PhotoImage(master=self, width=self.thumbnail_size[0],
                                          height=self.thumbnail_size[1])
        self._mode = "nightmare"
        self.state: PickerState | None = None
        self.busy = False
        shell = tk.Frame(self, bg=SURFACE, padx=self._px(16), pady=self._px(14))
        shell.pack(fill="both", expand=True, padx=1, pady=1)
        header = tk.Frame(shell, bg=SURFACE)
        header.pack(fill="x")
        self.title_label = self._text(header, "", size=15, bold=True)
        self.title_label.pack(side="left")
        close = self._text(header, "关闭  Esc", size=9, color=MUTED)
        close.configure(cursor="hand2")
        close.pack(side="right")
        close.bind("<Button-1>", lambda _event: self._on_close())
        self.description_label = self._text(shell, "", size=9, color=MUTED)
        self.description_label.pack(fill="x", pady=(self._px(2), self._px(10)))
        self.options_frame = tk.Frame(shell, bg=SURFACE)
        self.options_frame.pack(fill="both", expand=True)
        # Both stages share the measured map-card size, so the window never jumps.
        sample, _widgets = self._map_card(0, "测量", self._placeholder)
        sample.update_idletasks()
        card_width = sample.winfo_reqwidth() + 2 * self._px(4)
        card_height = sample.winfo_reqheight() + 2 * self._px(4)
        sample.destroy()
        for index in (0, 1):
            self.options_frame.columnconfigure(index, weight=1, uniform="option", minsize=card_width)
            self.options_frame.rowconfigure(index, uniform="row", minsize=card_height)
        footer = tk.Frame(shell, bg=SURFACE)
        footer.pack(fill="x", pady=(self._px(10), 0))
        self.previous_label = self._pager(footer, "‹ 上一页  PgUp", -1)
        self.previous_label.pack(side="left")
        self.next_label = self._pager(footer, "下一页  PgDn ›", 1)
        self.next_label.pack(side="right")
        self.page_label = self._text(footer, "", size=9, color=MUTED)
        self.page_label.pack(side="left", expand=True)
        self.help_label = self._text(shell, "", size=8, color=MUTED)
        self.help_label.pack(fill="x", pady=(self._px(6), 0))

    # Layout helpers ---------------------------------------------------------
    def _px(self, value: float) -> int:
        return max(1, round(value * self._scale))

    def _font(self, size: float, bold: bool = False):
        return (FONT, -self._px(size * 96 / 72), "bold" if bold else "normal")

    def _text(self, parent, text, *, size=10, color=TEXT, bold=False, bg=SURFACE):
        return tk.Label(parent, text=text, bg=bg, fg=color, anchor="w", justify="left",
                        font=self._font(size, bold))

    def _pager(self, parent, text, delta):
        label = self._text(parent, text, size=9, color=BUTTON_TEXT)
        label.configure(cursor="hand2")
        label.bind("<Button-1>", lambda _event: self.command("PageDown" if delta > 0 else "PageUp"))
        return label

    # Public API -------------------------------------------------------------
    def present(self, groups: list, mode: str) -> None:
        self._mode = mode
        self.state = PickerState(groups)
        self.busy = False
        self._photos = {}
        self.render()

    def set_thumbnail(self, map_id: str, photo) -> None:
        self._photos[map_id] = photo
        image = getattr(self, "_image_labels", {}).get(map_id)
        if image is not None and image.winfo_exists():
            image.configure(image=photo)

    def set_busy(self, busy: bool, message: str = "") -> None:
        self.busy = bool(busy)
        if message:
            self.help_label.configure(text=message)

    def command(self, key: str) -> None:
        state = self.state
        if state is None:
            return
        if key == "Escape":
            self._on_close()
        elif key == "Backspace":
            if not self.busy and state.back():
                self.render()
        elif key in {"PageUp", "PageDown"}:
            if not self.busy and state.turn(1 if key == "PageDown" else -1):
                self.render()
        elif key in OPTION_KEYS:
            self.choose(OPTION_KEYS.index(key))

    def choose(self, index: int) -> None:
        if self.busy or self.state is None:
            return
        picked = self.state.choose(index)
        if picked is None:
            return
        kind, value = picked
        if kind == "group":
            self.render()
        else:
            self._on_choose(value)

    # Rendering --------------------------------------------------------------
    def _card(self, index: int):
        card = tk.Frame(self.options_frame, bg=TINT, padx=self._px(8), pady=self._px(8),
                        highlightthickness=1, highlightbackground=LINE, cursor="hand2")
        top = tk.Frame(card, bg=TINT)
        top.pack(fill="x")
        key = tk.Label(top, text=OPTION_KEYS[index], bg=BUTTON_BG, fg=BUTTON_TEXT,
                       font=self._font(8, True), padx=self._px(5))
        key.pack(side="right")
        return card, top, key

    def _map_card(self, index: int, title_text: str, photo):
        card, top, key = self._card(index)
        title = self._text(top, title_text, size=10, bold=True, bg=TINT)
        title.pack(side="left", fill="x", expand=True)
        image = tk.Label(card, bg=IMAGE_BG, bd=0, image=photo)
        image.pack(fill="x", pady=(self._px(6), 0))
        return card, (card, top, title, key, image)

    def render(self) -> None:
        state = self.state
        if state is None:
            return
        maps_stage = state.stage == "maps"
        mode_title = MODE_TITLES.get(self._mode, "")
        group = state.groups[state.group][0] if maps_stage and state.groups else ""
        self.title_label.configure(text=f"手动选图 · {mode_title}" if mode_title else "手动选图")
        if not state.groups:
            description = "当前模式没有可选地图，请先检查地图资源。"
        elif maps_stage:
            prefix = "" if state.single_group else f"已选 {group} · "
            description = f"{prefix}选择本局地图，选好后按这张图贴合和显示"
        else:
            description = "先选路线组合，再选地图；不经过地图识别"
        self.description_label.configure(text=description)
        pages = state.page_count()
        self.page_label.configure(text=f"{state.page + 1} / {pages}" if pages > 1 else "")
        self.previous_label.configure(fg=BUTTON_TEXT if state.page > 0 else LINE)
        self.next_label.configure(fg=BUTTON_TEXT if state.page < pages - 1 else LINE)
        keys = " / ".join(OPTION_KEYS)
        back = "" if state.single_group or not maps_stage else "，Backspace 返回"
        self.help_label.configure(text=f"点击或按 {keys} 选择{'，PgUp/PgDn 翻页' if pages > 1 else ''}{back}，Esc 关闭")
        for child in self.options_frame.winfo_children():
            child.destroy()
        self._image_labels = {}
        options = state.options()
        for index, option in enumerate(options):
            if maps_stage:
                card, widgets = self._map_card(index, option_title(option, group),
                                               self._photos.get(option.id) or self._placeholder)
                self._image_labels[option.id] = widgets[-1]
            else:
                label, items = option
                card, top, key = self._card(index)
                title = self._text(top, label, size=13, bold=True, bg=TINT)
                title.pack(side="left", fill="x", expand=True)
                count = self._text(card, f"{len(items)} 张地图", size=9, color=MUTED, bg=TINT)
                count.pack(fill="x", pady=(self._px(4), 0))
                widgets = (card, top, title, key, count)
            card.grid(row=index // 2, column=index % 2, sticky="nsew",
                      padx=self._px(4), pady=self._px(4))
            for widget in widgets:
                widget.bind("<Button-1>", lambda _event, value=index: self.choose(value))
        if not options:
            self._text(self.options_frame, "没有可选地图", color=MUTED).grid(row=0, column=0, sticky="w")
        if maps_stage:
            missing = [item.id for item in options if item.id not in self._photos]
            if missing:
                self._on_need_thumbnails(missing)
