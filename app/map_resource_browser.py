"""Image-first Tk map library, isolated from recognition and player settings."""
from __future__ import annotations

from collections import OrderedDict
from concurrent.futures import CancelledError, ThreadPoolExecutor
from pathlib import Path
import queue
import time
import tkinter as tk
from tkinter import ttk, filedialog

from PIL import Image, ImageOps, ImageTk, UnidentifiedImageError

from desktop_display import WindowDisplay
from desktop_rounded import RoundedButton, RoundedEntry, RoundedCombobox, RoundedFrame, set_rounded_scale
from desktop_theme import (BG, CARD, LINE, TEXT, MUTED, PRIMARY as ACCENT,
                           GOLD as WARNING, FONT, IMAGE_BG, INPUT_BG, BUTTON_BG,
                           BUTTON_HOVER, BUTTON_TEXT, PANEL_RADIUS_DIP, SHADOW_DIP)
from map_resource_model import (MapResourceModel, STRUCTURE_SOURCE, choose_resource,
                                floor_label, resource_status, resource_inventory)
from map_resource_overview import structure_previews, load_structure_preview, author_resource_version



def _label(parent, text="", size=10, color=TEXT, bold=False, **kwargs):
    return tk.Label(parent, text=text, bg=parent.cget("bg"), fg=color,
                    font=(FONT, size, "bold" if bold else "normal"), **kwargs)


def _button(parent, text, command):
    return RoundedButton(parent, text=text, command=command, bg=BUTTON_BG, fg=BUTTON_TEXT,
                     activebackground=BUTTON_HOVER, activeforeground=BUTTON_TEXT,
                     relief="flat", bd=0, padx=12, pady=7, cursor="hand2",
                     highlightcolor=ACCENT, highlightthickness=0,
                     highlightbackground=LINE, font=(FONT, 9, "bold"))


def load_preview(path: Path, size: tuple[int, int]) -> Image.Image:
    """Fit the whole source canvas. Source files and resource bindings stay untouched."""
    with Image.open(path) as source:
        source.draft("RGB", size)
        image = ImageOps.exif_transpose(source).convert("RGBA")
        image.thumbnail(size, Image.Resampling.LANCZOS)
    background = Image.new("RGBA", image.size, IMAGE_BG)
    background.alpha_composite(image)
    return background.convert("RGB")


class MapResourceBrowser(tk.Frame):
    def __init__(self, parent, assistant):
        super().__init__(parent, bg=BG)
        self.assistant = assistant
        self.model = None
        self._context = None
        self._generation = 0
        self._closed = False
        self._active = False
        self._executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="map-preview")
        self._results = queue.Queue()
        self._cache = OrderedDict()
        self._pending = {}
        self._preview_futures = {}
        self._preview_waiting = OrderedDict()
        self._cards = []
        self._jobs = set()
        # The runtime cancels interpreter-wide timers through the Tk root on
        # shutdown. Register there too, so Tcl command ownership stays aligned
        # with after_cancel before child widgets are destroyed.
        self._scheduler = self._root()
        self._detail = None
        self._preview_window = None
        self._scale = 1.0
        self._search_job = None
        self._resize_job = None
        self._controls_layout = None
        self._overview = True
        self._overview_rendered = None
        self._resource_mode_signature = None
        self._resource_mode_switching = False
        self._wheel_tag = f"MapResourceWheel-{id(self)}"
        self._wheel_binding = self._root().bind_class(self._wheel_tag, "<MouseWheel>", self._route_wheel)
        self._build()
        self.bind("<Configure>", lambda event: self._layout_controls() if event.widget is self else None, add="+")
        self.bind("<Destroy>", self._destroy, add="+")
        self.bind("<Unmap>", lambda event: self.suspend() if event.widget is self else None, add="+")
        self._schedule(50, self._drain)

    def _schedule(self, delay, callback):
        holder = []
        def run():
            self._jobs.discard(holder[0])
            if not self._closed:
                callback()
        holder.append(self._scheduler.after(delay, run))
        self._jobs.add(holder[0])
        return holder[0]

    def _cancel(self, job):
        if job is not None:
            self._scheduler.after_cancel(job)
            self._jobs.discard(job)

    def _apply_display(self):
        self._bind_scroll_tree(self.content)
        display = getattr(self.winfo_toplevel(), "_desktop_display", None)
        if display:
            display.apply_widgets(self)
        self._controls_layout = None
        self._layout_controls()

    def set_display_scale(self, scale):
        if scale == self._scale:
            return
        self._scale = scale
        self._controls_layout = None
        self._layout_controls()
        if self._overview and self.model is not None:
            if self._active:
                self._render_overview()
            return
        self._columns = 0
        if self._cards:
            self._layout_cards()

    def _build(self):
        top = tk.Frame(self, bg=BG)
        self.heading = top
        top._display_manual_geometry = True
        top.pack(fill="x", padx=SHADOW_DIP, pady=(20, 10))
        _label(top, "地图资源", size=22, bold=True).pack(side="left")
        self.mode_label = _label(top, color=ACCENT, bold=True)
        self.mode_label.pack(side="right")
        self.import_button = _button(top, "导入资源包", self._choose_map_package)
        self.import_button.pack(side="right", padx=(8, 0))
        self.overview_button = _button(top, "资源概览", self._show_overview)
        self.online_bar = tk.Frame(self, bg=BG)
        self.online_bar.pack(fill="x", padx=20, pady=(0, 10))
        self.online_toggle = _button(self.online_bar, "下载地图", self._toggle_online_packages)
        self.online_toggle.pack(side="left")
        mode_controls = tk.Frame(self.online_bar, bg=BG)
        mode_controls.pack(side="right", padx=(10, 0))
        _label(mode_controls, "游戏模式", size=9, color=MUTED).pack(side="left", padx=(0, 6))
        self.resource_mode_buttons = {}
        for mode, title in (("nightmare", "噩梦"), ("difficult", "困难")):
            button = _button(mode_controls, title, lambda value=mode: self._select_resource_mode(value))
            button.pack(side="left", padx=1)
            self.resource_mode_buttons[mode] = button
        self.mode_feedback = _label(self, size=9, color=WARNING, anchor="w", justify="left", wraplength=460)
        self.mode_feedback.bind("<Configure>", lambda event: self.mode_feedback.configure(
            wraplength=max(100, event.width - 4)), add="+")
        route_controls = tk.Frame(self, bg=BG)
        route_controls.pack(fill='x', padx=20, pady=(0, 8))
        _label(route_controls, '实际使用路线', size=9, color=MUTED).pack(side='left', padx=(0, 8))
        self.runtime_route_var = tk.StringVar(self)
        self.runtime_route_box = RoundedCombobox(route_controls, textvariable=self.runtime_route_var,
                                                state='readonly', width=20)
        self.runtime_route_box.pack(side='left', fill='x', expand=True)
        self.runtime_route_box.bind('<<ComboboxSelected>>', self._select_runtime_route)
        self.runtime_route_feedback = _label(self, size=9, color=MUTED, anchor='w', justify='left', wraplength=500)
        self.runtime_route_feedback.pack(fill='x', padx=20, pady=(0, 8))
        self.runtime_route_feedback.bind('<Configure>', lambda event: self.runtime_route_feedback.configure(
            wraplength=max(100, event.width - 4)), add='+')
        self._runtime_route_keys = ()
        self._runtime_route_signature = None
        self.online_bar_status = _label(self.online_bar, size=9, color=MUTED, anchor="w",
                                        justify="left", wraplength=300)
        self.online_bar_status.pack(side="left", fill="x", expand=True, padx=(10, 0))
        self.online_surface = tk.Frame(self, bg=BG)
        self.online_message = _label(self.online_surface, color=MUTED, anchor="w", justify="left", wraplength=460)
        self.online_message.pack(fill="x")
        online_actions = tk.Frame(self.online_surface, bg=BG)
        online_actions.pack(fill="x", pady=(6, 8))
        self.online_check = _button(online_actions, "检查下载列表", self._check_online_packages)
        self.online_check.pack(side="left")
        self.online_cancel = _button(online_actions, "取消", self._cancel_online_package)
        self.online_cancel.pack(side="left", padx=(8, 0))
        self.online_progress = ttk.Progressbar(self.online_surface, mode="determinate", maximum=100, value=0)
        self.online_progress_text = _label(self.online_surface, size=9, color=MUTED, anchor="w")
        self.online_list = tk.Frame(self.online_surface, bg=BG)
        self.online_list.pack(fill="x")
        self.online_feedback = _label(self.online_surface, color=WARNING, anchor="w", justify="left", wraplength=460)
        self._online_open = False
        self._online_signature = None
        self._online_message_text = ""
        self._online_rows = {}
        self.online_surface.bind("<Configure>", self._fit_online_text, add="+")
        self.subtitle = _label(self, "看图辨认地图，点开查看完整楼层与路线。", color=MUTED, size=9)
        self.subtitle._display_manual_geometry = True
        self.subtitle.pack(anchor="w", padx=SHADOW_DIP, pady=(0, 12))
        self.import_surface = tk.Frame(self, bg=BG)
        self.import_message = _label(self.import_surface, color=MUTED, anchor="w", justify="left", wraplength=460)
        self.import_message.pack(fill="x")
        self.import_title = _label(self.import_surface, bold=True, anchor="w", justify="left", wraplength=460)
        self.import_summary = _label(self.import_surface, color=MUTED, anchor="w", justify="left", wraplength=460)
        self.import_actions = tk.Frame(self.import_surface, bg=BG)
        self.import_confirm_button = _button(self.import_actions, "退出并导入", self._confirm_map_package)
        self.import_confirm_button.pack(side="left")
        self.import_cancel_button = _button(self.import_actions, "取消", self._cancel_map_package)
        self.import_cancel_button.pack(side="left", padx=(8, 0))
        self.import_feedback = _label(self.import_surface, color=WARNING, anchor="w", justify="left", wraplength=460)
        self._import_signature = None
        self._import_message_text = ""
        self.import_surface.bind("<Configure>", self._fit_import_text, add="+")
        self.filter_host = tk.Frame(self, bg=BG)
        self.filter_host.pack(fill="x", padx=SHADOW_DIP)
        controls = tk.Frame(self.filter_host, bg=BG)
        self.source_controls = controls
        controls._display_manual_geometry = True
        controls.pack(fill="x")
        _label(controls, "浏览来源", size=9, color=MUTED).pack(side="left", padx=(0, 8))
        self.source_var = tk.StringVar(self)
        self.source_box = RoundedCombobox(controls, textvariable=self.source_var, state="readonly", width=14)
        self.source_box.pack(side="left", fill="x", expand=True)
        self.source_box.bind("<<ComboboxSelected>>", self._source_changed)
        row = tk.Frame(self.filter_host, bg=BG)
        self.browse_controls = row
        row._display_manual_geometry = True
        row.pack(fill="x", pady=(10, 5))
        self.search_var = tk.StringVar(self)
        self.search_entry = RoundedEntry(row, textvariable=self.search_var, bg=INPUT_BG, fg=TEXT,
                                     insertbackground=TEXT, relief="flat", bd=6,
                                     font=(FONT, 10), width=8, highlightbackground=LINE, highlightcolor=ACCENT)
        self.search_entry.pack(side="left", fill="x", expand=True)
        self.search_entry.bind("<KeyRelease>", self._search_changed)
        self.search_entry.bind("<Escape>", self._clear_search)
        _label(row, "名称搜索", size=9, color=MUTED).pack(side="left", padx=8)
        self.floor_var = tk.StringVar(self, "全部楼层")
        self.floor_box = RoundedCombobox(row, textvariable=self.floor_var, state="readonly", width=12)
        self.floor_box.pack(side="right")
        self.floor_box.bind("<<ComboboxSelected>>", lambda _e: self._render_gallery())
        self.summary = _label(self, size=9, color=MUTED, anchor="w", justify="left")
        self.summary._display_manual_geometry = True
        self.summary.pack(fill="x", padx=SHADOW_DIP, pady=(5, 10))
        self.content = tk.Frame(self, bg=BG)
        self.content._desktop_scroll_owner = self
        self.content.pack(fill="both", expand=True)
        footer = tk.Frame(self, bg=BG)
        self.footer = footer
        footer._display_manual_geometry = True
        footer.pack(fill="x", padx=SHADOW_DIP, pady=(8, 12))
        _label(footer, "“实际使用路线”影响定点地图和游戏贴合；“浏览来源”只切换预览。", size=8, color=MUTED,
               anchor="w", wraplength=500).pack(fill="x")

    def _layout_controls(self):
        short = self.winfo_height() / self._scale < 600
        spacious = max(self.winfo_width(), self.master.winfo_width()) / self._scale >= 1200
        if self._active and self._overview and self.model is not None and self._overview_rendered != self._overview_signature():
            # Resize a settled overview once, rather than decoding its NPZs on
            # every Configure. Re-entry at the same size keeps the live page.
            self._cancel(self._resize_job)
            self._resize_job = self._schedule(80, self._render_overview)
        signature = short, spacious, self._scale, self._detail is not None
        if signature == self._controls_layout:
            return
        self._controls_layout = signature
        px = lambda value: round(value * self._scale)
        self.heading.pack_configure(padx=px(SHADOW_DIP), pady=(px(10 if short else 20), px(8 if short else 10)))
        self.subtitle.pack_forget()
        self.footer.pack_forget()
        # Overview removes this sibling. Restore the anchor before placing the
        # gallery subtitle relative to it, including the first tall-window entry.
        if self._overview:
            self.filter_host.pack_forget()
        elif not self.filter_host.winfo_manager():
            self.filter_host.pack(fill="x", padx=px(SHADOW_DIP), before=self.content)
        if not short and not self._overview:
            self.subtitle.pack(anchor="w", padx=px(SHADOW_DIP), pady=(0, px(12)), before=self.filter_host)
            self.footer.pack(fill="x", padx=px(SHADOW_DIP), pady=(px(8), px(12)))
        self.source_controls.pack_configure(side="left" if short else "top", fill="x", expand=short)
        self.browse_controls.pack_forget()
        self.summary.pack_forget()
        if self._detail is None and not self._overview:
            self.browse_controls.pack(side="left" if short else "top", fill="x", expand=True,
                                      padx=(px(12) if short else 0, 0),
                                      pady=(0 if short else px(10), 0 if short else px(5)))
            self.summary.pack(fill="x", padx=px(SHADOW_DIP), pady=(px(5), px(10)), before=self.content)
            full = getattr(self, "_summary_full", "")
            text = full.split("\n")[0] if short else full
            if short and self.model and self.profile_id == STRUCTURE_SOURCE:
                text += " · 源图标签不是游戏楼层"
            self.summary.configure(text=text)

    def invalidate_resource_context(self):
        self._generation += 1
        self._cancel_preview_work()
        self._cache.clear()
        self._close_preview()
        self._context = None
        self._overview_rendered = None
        self._runtime_route_signature = None

    def refresh(self):
        self._active = True
        self.sync_map_package_import()
        self.sync_online_packages()
        pack = self.assistant.pack
        catalog = getattr(self.assistant, "author_route_catalog", None)
        context = (id(pack), id(catalog))
        if context != self._context:
            self._context = context
            self.model = MapResourceModel(pack, catalog, self.assistant._mode_route_options())
            self._profile_keys = tuple(self.model.profiles)
            self.source_box.configure(values=tuple(self.model.profiles.values()))
            preferred = getattr(self.assistant, "preferred_route_profile_id", self.model.default_profile)
            self.source_box.current(self._profile_keys.index(preferred)
                                    if preferred in self._profile_keys else 0)
            self.search_var.set("")
            self.mode_label.configure(text=f"当前模式：{self.model.mode_label}")
            self._source_changed()
        else:
            if not self._overview:
                self._render_gallery()
            elif self._overview_rendered != self._overview_signature():
                self._render_overview()
            else:
                self._layout_controls()
        self.sync_game_mode()

    def sync_route_selection(self):
        getter = getattr(self.assistant, 'route_selection_snapshot', None)
        snapshot = getter() if callable(getter) else {}
        options = tuple(snapshot.get('options') or ())
        signature = (snapshot.get('mode'), options, snapshot.get('selected'),
                     snapshot.get('effective'), snapshot.get('message'), snapshot.get('fallback'))
        if signature == getattr(self, '_runtime_route_signature', None):
            return
        self._runtime_route_signature = signature
        self._runtime_route_keys = tuple(key for key, _label in options)
        self.runtime_route_box.configure(values=tuple(label for _key, label in options),
            state='readonly' if options and callable(getattr(self.assistant, 'select_route_profile', None)) else 'disabled')
        selected = snapshot.get('selected')
        if selected in self._runtime_route_keys:
            self.runtime_route_box.current(self._runtime_route_keys.index(selected))
        else:
            self.runtime_route_var.set('暂无可用路线，请先导入资源包')
        self.runtime_route_feedback.configure(text=snapshot.get('message') or '路线选择暂不可用',
            fg=WARNING if snapshot.get('fallback') else MUTED)

    def _select_runtime_route(self, _event=None):
        index = self.runtime_route_box.current()
        if not 0 <= index < len(self._runtime_route_keys):
            return
        setter = getattr(self.assistant, 'select_route_profile', None)
        if not callable(setter):
            return
        receipt = setter(self._runtime_route_keys[index]) or {}
        self.sync_route_selection()
        if not receipt.get('success'):
            self.runtime_route_feedback.configure(text=receipt.get('message') or '路线切换未完成', fg=WARNING)

    def sync_game_mode(self, snapshot=None):
        if self._closed:
            return
        self.sync_route_selection()
        if snapshot is None:
            getter = getattr(self.assistant, "_route_presentation_snapshot", None)
            snapshot = getter() if callable(getter) else {}
        mode = snapshot.get("mode", getattr(self.assistant, "game_mode", None))
        enabled = (snapshot.get("modeChangeEnabled") is True and not self._resource_mode_switching
                   and callable(getattr(self.assistant, "set_game_mode", None)))
        signature = mode, enabled
        if signature != self._resource_mode_signature:
            self._resource_mode_signature = signature
            self.mode_label.configure(text=f"当前模式：{ {'nightmare': '噩梦', 'difficult': '困难'}.get(mode, '未读取')}")
            for value, button in self.resource_mode_buttons.items():
                selected = mode == value
                button.configure(bg=ACCENT if selected else BUTTON_BG, fg="white" if selected else BUTTON_TEXT,
                                 activebackground=ACCENT if selected else BUTTON_HOVER,
                                 state="normal" if enabled else "disabled")
        context = (id(self.assistant.pack), id(getattr(self.assistant, "author_route_catalog", None)))
        if self._active and not self._resource_mode_switching and context != self._context:
            self.refresh()

    def _select_resource_mode(self, mode):
        if self._closed or self._resource_mode_switching:
            return
        callback = getattr(self.assistant, "set_game_mode", None)
        if not callable(callback):
            self.sync_game_mode()
            return
        self._resource_mode_switching = True
        self.sync_game_mode()
        try:
            result = callback(mode)
        finally:
            self._resource_mode_switching = False
            if not self._closed:
                self.sync_game_mode()
        if self._closed:
            return
        message = str(result.get("message") or "") if isinstance(result, dict) and result.get("success") is False else ""
        self.mode_feedback.configure(text=message)
        if message:
            anchor = next(widget for widget in (self.online_surface, self.subtitle, self.import_surface,
                                                self.filter_host, self.content) if widget.winfo_manager())
            self.mode_feedback.pack(fill="x", padx=20, pady=(0, 8), before=anchor)
        else:
            self.mode_feedback.pack_forget()

    def _map_package_snapshot(self):
        getter = getattr(self.assistant, "map_package_import_snapshot", None)
        return getter() if callable(getter) else {}

    def _fit_import_text(self, event):
        if self._closed:
            return
        for label in (self.import_message, self.import_title, self.import_summary, self.import_feedback):
            label.configure(wraplength=max(100, event.width - 4))

    def sync_map_package_import(self):
        if self._closed:
            return
        state = self._map_package_snapshot()
        stage = state.get("stage", "idle")
        busy = state.get("busy") is True or stage == "checking"
        helper = state.get("helperAvailable") is True
        live = state.get("liveAvailable") is True
        ready = state.get("ready") if stage == "ready" and isinstance(state.get("ready"), dict) else None
        title = str(ready.get("title") or "") if ready is not None else ""
        summary = str(ready.get("summary") or "") if ready is not None else ""
        inspect = callable(getattr(self.assistant, "inspect_map_package", None))
        snapshot = callable(getattr(self.assistant, "map_package_import_snapshot", None))
        confirm = callable(getattr(self.assistant, "confirm_map_package_import", None))
        cancel = callable(getattr(self.assistant, "cancel_map_package_import", None))
        can_import = ready is not None and not busy and (helper or live) and state.get("canImport") is True and confirm
        message = str(state.get("message") or "")
        if not snapshot or not inspect or not (helper or live):
            unavailable = "当前版本尚不支持导入资源包。"
            message = f"{message}\n{unavailable}" if message and unavailable not in message else message or unavailable
        elif not message and busy:
            message = "正在检查资源包，请稍候。"
        elif not message and ready is not None:
            message = "资源包已检查。点击“退出并导入”后，星图将正常退出并安装资源。"
        elif not message and stage == "error":
            message = "资源包检查失败，请重新选择。"
        package = str(state.get("packagePath") or "")
        signature = stage, busy, helper, live, title, summary, message, package, inspect, snapshot, can_import, cancel
        if signature == self._import_signature:
            return
        self._import_signature = signature
        self._import_message_text = message
        self.import_button.configure(state="normal" if inspect and snapshot and not busy else "disabled")
        self.import_message.configure(text=message)
        self.import_title.configure(text=title or (Path(package).name if package else ""))
        self.import_summary.configure(text=summary)
        self.import_confirm_button.configure(state="normal" if can_import else "disabled",
            text="导入并刷新" if live else "退出并导入")
        self.import_cancel_button.configure(state="normal" if cancel and stage != "idle" else "disabled")
        self.import_feedback.configure(text="")
        self.import_feedback.pack_forget()
        if title or package:
            self.import_title.pack(fill="x", pady=(6, 0))
        else:
            self.import_title.pack_forget()
        if ready is not None:
            self.import_summary.pack(fill="x", pady=(4, 0))
        else:
            self.import_summary.pack_forget()
        if stage != "idle":
            self.import_actions.pack(anchor="w", pady=(8, 0))
        else:
            self.import_actions.pack_forget()
        if message or stage != "idle":
            anchor = self.filter_host if self.filter_host.winfo_manager() else self.content
            self.import_surface.pack(fill="x", padx=SHADOW_DIP, pady=(0, 12), before=anchor)
        else:
            self.import_surface.pack_forget()

    def _map_package_action(self, name, *args, **kwargs):
        if self._closed:
            return None
        callback = getattr(self.assistant, name, None)
        result = callback(*args, **kwargs) if callable(callback) else None
        if self._closed:
            return result
        self.sync_map_package_import()
        message = str(result.get("message") or "") if isinstance(result, dict) else ""
        if message and message != self._import_message_text:
            self.import_feedback.configure(text=message)
            self.import_feedback.pack(fill="x", pady=(6, 0))
        return result

    def _choose_map_package(self):
        if self._closed:
            return
        state = self._map_package_snapshot()
        if state.get("busy") is True or state.get("stage") == "checking":
            return
        if (not callable(getattr(self.assistant, "inspect_map_package", None))
                or not callable(getattr(self.assistant, "map_package_import_snapshot", None))):
            self.sync_map_package_import()
            return
        path = filedialog.askopenfilename(parent=self.winfo_toplevel(), title="选择地图资源包",
            filetypes=(("地图资源包", "*.zip"), ("所有文件", "*.*")))
        if path and not self._closed:
            self._map_package_action("inspect_map_package", path)

    def _confirm_map_package(self):
        if self._closed:
            return
        state = self._map_package_snapshot()
        if (state.get("stage") != "ready" or state.get("busy") is True
                or not isinstance(state.get("ready"), dict)
                or not (state.get("helperAvailable") is True or state.get("liveAvailable") is True)
                or state.get("canImport") is not True):
            self.sync_map_package_import()
            return
        self._map_package_action("confirm_map_package_import", owner=self.winfo_toplevel())

    def _cancel_map_package(self):
        return self._map_package_action("cancel_map_package_import", reason="user-cancelled")

    def _online_snapshot(self):
        getter = getattr(self.assistant, "online_map_packages_snapshot", None)
        return getter() if callable(getter) else {}

    @staticmethod
    def _online_size(value):
        if not isinstance(value, int) or isinstance(value, bool) or value < 0:
            return ""
        if value >= 1_000_000:
            return f"{value / 1_000_000:.1f} MB"
        if value >= 1000:
            return f"{value / 1000:.0f} KB"
        return f"{value} B"

    def _fit_online_text(self, event):
        if self._closed:
            return
        for label in (self.online_message, self.online_feedback):
            label.configure(wraplength=max(100, event.width - 4))
        for row in self._online_rows.values():
            row["title"].configure(wraplength=max(100, event.width - 110))
            row["summary"].configure(wraplength=max(100, event.width - 110))

    def sync_online_packages(self):
        if self._closed:
            return
        state = self._online_snapshot()
        stage = state.get("stage", "idle")
        busy = state.get("busy") is True
        getter = callable(getattr(self.assistant, "online_map_packages_snapshot", None))
        check = getter and callable(getattr(self.assistant, "check_online_map_packages", None)) and state.get("canCheck") is True and not busy
        download = getter and callable(getattr(self.assistant, "download_online_map_package", None)) and state.get("canDownload") is True and not busy
        cancel = getter and callable(getattr(self.assistant, "cancel_online_map_package", None)) and state.get("canCancel") is True
        packages = tuple((item["packageId"], str(item.get("title") or item["packageId"]),
                          str(item.get("summary") or ""), self._online_size(item.get("bytes")))
                         for item in state.get("packages", ()) if isinstance(item, dict)
                         and isinstance(item.get("packageId"), str) and item["packageId"])
        selected = state.get("selectedPackageId")
        progress = state.get("progress") or {}
        received, total = progress.get("receivedBytes"), progress.get("totalBytes")
        received_text, total_text = self._online_size(received), self._online_size(total)
        message = str(state.get("message") or "")
        if not getter:
            message = "当前版本尚不支持地图下载。"
        elif not message:
            message = ("正在读取下载列表。" if stage == "checking-index" else
                       "正在接收地图资源。" if stage == "downloading" else
                       "下载未完成，请手动重试。" if stage == "error" else
                       "请选择要下载的地图包。" if packages else
                       "尚未配置地图下载地址。" if not check else "点击检查下载列表。")
        signature = stage, busy, message, packages, selected, received, total, check, download, cancel, self._online_open
        if signature == self._online_signature:
            return
        self._online_signature = signature
        self._online_message_text = message
        self.online_toggle.configure(text="收起下载" if self._online_open else "下载地图")
        self.online_check.configure(state="normal" if check else "disabled")
        self.online_cancel.configure(state="normal" if cancel else "disabled")
        self.online_message.configure(text=message)
        self.online_bar_status.configure(text=message)
        self.online_feedback.configure(text="")
        self.online_feedback.pack_forget()
        ids = {item[0] for item in packages}
        for package_id in tuple(self._online_rows):
            if package_id not in ids:
                self._online_rows.pop(package_id)["frame"].destroy()
        for package_id, title, summary, size in packages:
            row = self._online_rows.get(package_id)
            if row is None:
                frame = tk.Frame(self.online_list, bg=BG)
                button = _button(frame, "下载", lambda value=package_id: self._download_online_package(value))
                button.pack(side="right", padx=(10, 0))
                text = tk.Frame(frame, bg=BG)
                text.pack(side="left", fill="x", expand=True)
                heading = _label(text, bold=True, anchor="w", justify="left", wraplength=350)
                heading.pack(fill="x")
                detail = _label(text, size=9, color=MUTED, anchor="w", justify="left", wraplength=350)
                detail.pack(fill="x", pady=(3, 0))
                row = self._online_rows[package_id] = dict(frame=frame, title=heading, summary=detail,
                                                          button=button, signature=None)
            row_signature = title, summary, size, download, stage == "error" and selected == package_id
            if row["signature"] != row_signature:
                row["signature"] = row_signature
                row["title"].configure(text=title)
                row["summary"].configure(text=" · ".join(value for value in (summary, size) if value))
                row["button"].configure(text="重试" if row_signature[-1] else "下载",
                                        state="normal" if download else "disabled")
            row["frame"].pack(fill="x", pady=(0, 10))
        show_progress = selected and stage in {"downloading", "error"} and bool(received_text)
        if show_progress:
            text = f"已接收 {received_text}" + (f" / {total_text}" if total_text else "")
            self.online_progress_text.configure(text=text)
            self.online_progress_text.pack(fill="x", pady=(0, 8), before=self.online_list)
        else:
            self.online_progress_text.pack_forget()
        if show_progress and total_text and total > 0:
            self.online_progress.configure(value=min(100, received / total * 100))
            self.online_progress.pack(fill="x", pady=(0, 4), before=self.online_progress_text)
        else:
            self.online_progress.pack_forget()
        if self._online_open:
            self.online_bar_status.pack_forget()
            anchor = next(widget for widget in (self.subtitle, self.import_surface, self.filter_host, self.content)
                          if widget.winfo_manager())
            self.online_surface.pack(fill="x", padx=20, pady=(0, 12), before=anchor)
        else:
            self.online_bar_status.pack(side="left", fill="x", expand=True, padx=(10, 0))
            self.online_surface.pack_forget()

    def _online_action(self, name, *args, **kwargs):
        if self._closed:
            return None
        callback = getattr(self.assistant, name, None)
        result = callback(*args, **kwargs) if callable(callback) else None
        if self._closed:
            return result
        self.sync_online_packages()
        self.sync_map_package_import()
        message = str(result.get("message") or "") if isinstance(result, dict) else ""
        if message and message != self._online_message_text:
            self.online_feedback.configure(text=message)
            self.online_feedback.pack(fill="x", pady=(6, 0))
        return result

    def _toggle_online_packages(self):
        if self._closed:
            return
        self._online_open = not self._online_open
        self.sync_online_packages()

    def _check_online_packages(self):
        if self._closed:
            return
        state = self._online_snapshot()
        if state.get("canCheck") is not True or state.get("busy") is True:
            self.sync_online_packages()
            return
        self._online_action("check_online_map_packages")

    def _download_online_package(self, package_id):
        if self._closed:
            return
        state = self._online_snapshot()
        if (state.get("canDownload") is not True or state.get("busy") is True
                or not any(item.get("packageId") == package_id for item in state.get("packages", ()))):
            self.sync_online_packages()
            return
        self._online_action("download_online_map_package", package_id)

    def _cancel_online_package(self):
        if self._closed:
            return
        if self._online_snapshot().get("canCancel") is not True:
            self.sync_online_packages()
            return
        self._online_action("cancel_online_map_package", reason="user-cancelled")

    def suspend(self):
        self._active = False
        self._cancel(self._resize_job)
        self._resize_job = None
        self._close_preview()

    @property
    def profile_id(self):
        index = self.source_box.current()
        return self._profile_keys[index] if index >= 0 else self._profile_keys[0]

    def _source_changed(self, _event=None):
        structure = self.profile_id == STRUCTURE_SOURCE
        floors = sorted({floor for item in self.model.maps(self.profile_id) for floor in item.floors})
        self._floor_choices = {"全部源图" if structure else "全部楼层": None}
        self._floor_choices.update({floor_label(key, source=structure): key for key in floors})
        self.floor_box.configure(values=tuple(self._floor_choices))
        self.floor_box.current(0)
        self._render_overview() if self._overview else self._render_gallery()

    def _show_overview(self):
        self._overview = True
        self._render_overview()

    def _open_gallery(self):
        self._overview = False
        self.overview_button.pack(side="right", padx=12)
        self._render_gallery()

    def _overview_signature(self):
        return (self._context, self.profile_id, self._scale,
                max(self.winfo_width(), self.master.winfo_width()))

    def _render_overview(self):
        width = max(self.winfo_width(), self.master.winfo_width()) / self._scale
        spacious = width >= 1200
        inset, gap, image_height = (32, 28, 220) if spacious else (28, 20, 150)
        self._close_preview()
        self._clear_content()
        self._overview_rendered = self._overview_signature()
        self._detail = None
        self._controls_layout = None
        self._layout_controls()
        self.overview_button.pack_forget()
        self.browse_controls.pack_forget()
        self.summary.pack_forget()
        self.canvas = tk.Canvas(self.content, bg=BG, highlightthickness=0)
        scroll = tk.Scrollbar(self.content, orient="vertical", command=self.canvas.yview)
        self.canvas.configure(yscrollcommand=scroll.set)
        scroll.pack(side="right", fill="y")
        self.canvas.pack(side="left", fill="both", expand=True)
        page = tk.Frame(self.canvas, bg=BG)
        item = self.canvas.create_window((0, 0), window=page, anchor="nw")
        page.bind("<Configure>", lambda _e: self.canvas.configure(scrollregion=self.canvas.bbox("all")))
        self.canvas.bind("<Configure>", lambda event: self.canvas.itemconfigure(item, width=event.width))
        self.canvas.bind("<MouseWheel>", self._wheel)
        section = RoundedFrame(page, bg=CARD, padx=inset, pady=inset, radius=PANEL_RADIUS_DIP,
                               highlightthickness=1, highlightbackground=LINE, shadow=SHADOW_DIP)
        section.pack(fill="x", pady=(4, 2))
        _label(section, "结构底图", size=14, bold=True).pack(anchor="w")
        _label(section, f"{self.model.mode_label} · 当前安装资源", size=9, color=MUTED).pack(anchor="w", pady=(6, 14))
        tiles = tk.Frame(section, bg=CARD)
        tiles.pack(fill="x")
        assets = structure_previews(self.assistant.pack)
        for column, asset in enumerate(assets):
            tiles.columnconfigure(column, weight=1, uniform="structure")
            tile = tk.Frame(tiles, bg=CARD)
            tile.grid(row=0, column=column, sticky="nsew", padx=(0 if column == 0 else gap, 0))
            image = tk.Button(tile, text="结构图不可用", bg=IMAGE_BG, fg=MUTED, relief="flat", bd=0,
                              command=lambda selected=asset: self._open_structure(selected), cursor="hand2")
            image.pack(fill="both", expand=True)
            try:
                image_width = max(80, round((max(400, width)-2*inset-2*gap)/3))
                image._map_photo = ImageTk.PhotoImage(load_structure_preview(asset, (round(image_width*self._scale), round(image_height*self._scale))), master=self)
                image.configure(image=image._map_photo, text="", width=1, height=round(image_height*self._scale))
            except (OSError, ValueError, KeyError):
                image.configure(state="disabled")
            _label(tile, asset.name, size=9, bold=True, anchor="w", wraplength=120).pack(fill="x", pady=(8, 0))
            _label(tile, floor_label(asset.floor), size=9, color=MUTED, anchor="w").pack(fill="x", pady=(3, 0))
        if not assets:
            _label(tiles, "当前资源未提供可浏览的结构图。", size=10, color=MUTED).pack(anchor="w", pady=20)
        author = RoundedFrame(page, bg=CARD, padx=inset, pady=inset, radius=PANEL_RADIUS_DIP,
                              highlightthickness=1, highlightbackground=LINE, shadow=SHADOW_DIP)
        author.pack(fill="x", pady=(0, 16))
        _label(author, "作者路线", size=14, bold=True).pack(anchor="w", pady=(0, 14))
        inventory = resource_inventory(self.model.maps(self.profile_id))
        available, total = inventory["availableLayerImages"], inventory["layerImageCount"]
        view_button = _button(author, "查看路线", self._open_gallery)
        view_button.configure(bg=ACCENT, fg="white")
        view_button.configure(state="normal" if available else "disabled")
        view_button.pack(side="right", padx=(12, 0))
        name = self.model.profiles[self.profile_id] if available or total else "作者路线未安装"
        cover = tk.Label(author, bg=IMAGE_BG, text="未安装", fg=MUTED, font=(FONT, 9), width=12, height=5)
        cover.pack(side="left", padx=(0, gap))
        cover_path = next((Path(path) for item in self.model.maps(self.profile_id) for variant in item.variants
                           for path in variant.layers.values() if Path(path).is_file()), None)
        if cover_path is not None:
            try:
                cover._map_photo = ImageTk.PhotoImage(load_preview(cover_path, (round(112*self._scale), round(88*self._scale))), master=self)
                cover.configure(image=cover._map_photo, text="", width=round(112*self._scale), height=round(88*self._scale))
            except (OSError, ValueError):
                cover.configure(text="预览不可用")
        info = tk.Frame(author, bg=CARD)
        info.pack(side="left", fill="x", expand=True)
        if len(self._profile_keys) > 1:
            source = RoundedCombobox(info, textvariable=self.source_var, values=tuple(self.model.profiles.values()), state="readonly")
            source.pack(fill="x")
            source.bind("<<ComboboxSelected>>", self._source_changed)
        else:
            _label(info, name, size=16, bold=True, anchor="w").pack(fill="x")
        catalog = getattr(self.assistant, "author_route_catalog", None)
        _label(info, author_resource_version(self.assistant.pack, catalog), size=10, color=MUTED, anchor="w", wraplength=500).pack(fill="x", pady=(6, 0))
        status = "本地已装" if available and available == total else "部分可用" if available else "未安装作者路线 · 结构底图可独立浏览"
        _label(info, f"{status}\n{self._inventory_text(inventory)}", size=9, color=MUTED, anchor="w",
               justify="left").pack(fill="x", pady=(6, 0))
        self._apply_display()

    @staticmethod
    def _inventory_text(inventory):
        return f"{inventory['mapCount']} 张地图 · {inventory['availableLayerImages']}/{inventory['layerImageCount']} 张楼层图片可用"

    def _open_structure(self, asset):
        self._close_preview()
        window = tk.Toplevel(self)
        window.withdraw()
        self._preview_window = window
        window.title(f"结构底图 · {asset.name} · {floor_label(asset.floor)}")
        window.configure(bg=BG)
        window.transient(self.winfo_toplevel())
        image = tk.Label(window, bg=IMAGE_BG)
        image.pack(fill="both", expand=True, padx=18, pady=18)
        try:
            image._map_photo = ImageTk.PhotoImage(load_structure_preview(asset, (820, 580)), master=window)
            image.configure(image=image._map_photo)
        except (OSError, ValueError, KeyError):
            image.configure(text="结构图读取失败，请返回检查本地资源。")
        _label(window, f"{asset.name} · {floor_label(asset.floor)} · 结构底图", size=10).pack(pady=(0, 12))
        window.protocol("WM_DELETE_WINDOW", self._close_preview)
        window.bind("<Escape>", lambda _e: self._close_preview())
        WindowDisplay(window, self.assistant, preferred=(900, 700), minimum=(500, 400)).start(self.winfo_toplevel())
        window.deiconify()

    def _search_changed(self, _event=None):
        self._cancel(self._search_job)
        self._search_job = self._schedule(150, self._render_gallery)

    def _clear_search(self, _event=None):
        self.search_var.set("")
        self._render_gallery()
        return "break"

    def _clear_content(self):
        self._cancel(self._resize_job)
        self._resize_job = None
        self._cancel(self._search_job)
        self._search_job = None
        self._overview_rendered = None
        self._generation += 1
        self._cards = []
        self._cancel_preview_work()
        for child in self.content.winfo_children():
            child.destroy()

    def _render_gallery(self):
        self._search_job = None
        self._detail = None
        self._close_preview()
        self._clear_content()
        rows = self.model.maps(self.profile_id)
        term = self.search_var.get().strip().casefold()
        floor = self._floor_choices.get(self.floor_var.get())
        matching = [item for item in rows if term in item.name.casefold()
                    and (floor is None or floor in item.floors)]
        inventory = resource_inventory(matching, floor)
        qualifier = "源图标签不是游戏楼层" if self.profile_id == STRUCTURE_SOURCE else "选择缩略图查看完整路线与楼层"
        self._summary_full = f"{self._inventory_text(inventory)}\n{qualifier}"
        self._controls_layout = None
        self._layout_controls()
        if not matching:
            empty = tk.Frame(self.content, bg=BG)
            empty.pack(fill="both", expand=True, padx=SHADOW_DIP, pady=35)
            _label(empty, "没有符合条件的地图", size=13, bold=True).pack(anchor="w")
            _label(empty, "试试清空名称搜索，或切换来源与楼层。", color=MUTED, size=9).pack(anchor="w", pady=8)
            _button(empty, "清空搜索", self._clear_search).pack(anchor="w")
            self._apply_display()
            return
        self.canvas = tk.Canvas(self.content, bg=BG, highlightthickness=0)
        scroll = tk.Scrollbar(self.content, orient="vertical", command=self._scroll_to)
        self.canvas.configure(yscrollcommand=scroll.set)
        scroll.pack(side="right", fill="y", padx=(0, 5))
        self.canvas.pack(side="left", fill="both", expand=True, padx=(SHADOW_DIP - 4, 0))
        self.grid_frame = tk.Frame(self.canvas, bg=BG)
        self._grid_window = self.canvas.create_window((0, 0), window=self.grid_frame, anchor="nw")
        self.grid_frame.bind("<Configure>", lambda _e: self.canvas.configure(scrollregion=self.canvas.bbox("all")))
        self.canvas.bind("<Configure>", self._gallery_resize)
        self.canvas.bind("<MouseWheel>", self._wheel)
        self.canvas.bind("<Next>", lambda _e: self._scroll_to("scroll", 1, "pages"))
        self.canvas.bind("<Prior>", lambda _e: self._scroll_to("scroll", -1, "pages"))
        self.canvas.configure(takefocus=True)
        for item in matching:
            self._make_card(item, floor)
        self._apply_display()
        self._columns = 0
        self._schedule(1, self._layout_cards)

    def _make_card(self, item, floor):
        card = RoundedFrame(self.grid_frame, bg=CARD, highlightthickness=1, highlightbackground=LINE, radius=14)
        card._display_manual_geometry = True  # Height/grid handled by _layout_cards.
        px = lambda value: value  # DIP, scaled once by the owning window.
        thumb = tk.Button(card, bg=IMAGE_BG, activebackground=IMAGE_BG, fg=MUTED,
                          text="等待加载…", font=(FONT, 10), relief="flat", bd=0,
                          cursor="hand2", highlightthickness=1, highlightbackground=LINE,
                          highlightcolor=ACCENT, command=lambda: self._show_detail(item))
        thumb.place(x=px(10), y=px(55), relwidth=1, width=-px(20), height=px(164))
        title = _label(card, item.name, size=11, bold=True, anchor="w", justify="left")
        title.place(x=px(12), y=px(8), relwidth=1, width=-px(24), height=px(42))
        floors = " / ".join(floor_label(key, source=item.structure) for key in item.floors)
        floor_text = _label(card, floors or "无可用楼层绑定", size=9, color=MUTED, anchor="w")
        floor_text.place(x=px(12), y=px(226), relwidth=1, width=-px(24), height=px(22))
        status = resource_status(item, floor)
        status_label = _label(card, status, size=8, color=MUTED if status == "图片已收录" else WARNING,
                              anchor="w", justify="left", wraplength=240)
        status_label.place(x=px(12), y=px(250), relwidth=1, width=-px(24), height=px(32))
        thumb._status_widget = status_label
        thumb._success_status = "可预览 · 已收录" if status == "图片已收录" else status
        thumb._error_status = "图片损坏或无法读取"
        binding = choose_resource(item, floor, self.model.default_floor)
        for widget in (thumb, title, floor_text, status_label):
            widget._display_manual_geometry = True
        for widget in (title, status_label):
            widget.bind("<Configure>", lambda event: event.widget.configure(wraplength=max(80, event.width)))
        row = dict(frame=card, thumb=thumb, title=title, floor=floor_text, status=status_label, item=item, binding=binding, loaded=False,
                   requested_size=None)
        self._cards.append(row)
        if not binding:
            thumb.configure(text="未收录此来源图片\n点击查看资源详情")
            row["loaded"] = True
        for widget in (card, thumb, title, *card.winfo_children()):
            widget.bind("<MouseWheel>", self._wheel)
        title.bind("<Button-1>", lambda _e: self._show_detail(item))
        # Hover outlines the whole card in the accent; pointer checks ignore child crossings.
        def hover(on):
            card.configure(highlightbackground=ACCENT if on else LINE)
        def leave(_event):
            widget = card.winfo_containing(*card.winfo_pointerxy())
            if widget is None or not str(widget).startswith(str(card)):
                hover(False)
        for widget in (card, thumb, title, floor_text, status_label):
            widget.bind("<Enter>", lambda _e: hover(True), add="+")
            widget.bind("<Leave>", leave, add="+")
        thumb.bind("<Return>", lambda _e: thumb.invoke())
        thumb.bind("<FocusIn>", lambda _e: self._reveal_card(card))

    def _reveal_card(self, card):
        top = self.canvas.canvasy(0)
        bottom = top + self.canvas.winfo_height()
        y = card.winfo_y()
        if y < top or y + card.winfo_height() > bottom:
            total = max(1, self.grid_frame.winfo_height())
            self._scroll_to("moveto", max(0, y - 5) / total)

    def _gallery_resize(self, event):
        self.canvas.itemconfigure(self._grid_window, width=event.width)
        self._cancel(self._resize_job)
        self._resize_job = self._schedule(60, self._layout_cards)

    def _layout_cards(self):
        self._resize_job = None
        if self._detail is not None or not self._cards:
            return
        width = max(1, self.canvas.winfo_width())
        if width < 50:
            return
        columns = max(1, min(4, width // round(300 * self._scale)))
        compact = self.canvas.winfo_height() < round(300 * self._scale)
        height = (max(round(180 * self._scale), self.canvas.winfo_height() - round(10 * self._scale))
                  if compact else round(290 * self._scale))
        layout = columns, compact, width, height, self._scale
        if layout != getattr(self, "_card_layout", None) or columns != self._columns:
            self._card_layout = layout
            px = lambda value: round(value * self._scale)
            for column in range(4):
                self.grid_frame.columnconfigure(column, weight=1 if column < columns else 0,
                                                uniform="maps" if column < columns else "")
            for index, row in enumerate(self._cards):
                row["frame"].grid(row=index // columns, column=index % columns,
                                     sticky="ew", padx=round(4 * self._scale), pady=round(5 * self._scale))
                row["frame"].configure(height=height)
                if compact:
                    image_width = min(px(220), round(width / columns * .46))
                    row["thumb"].place_configure(x=px(10), y=px(10), relwidth=0,
                                                  width=image_width, height=height-px(20))
                    for key, y, h in (("title", 12, 42), ("floor", 65, 22), ("status", 95, 58)):
                        row[key].place_configure(x=image_width+px(24), y=px(y), relwidth=1,
                                                  width=-image_width-px(36), height=px(h))
                else:
                    for key, x, y, h in (("thumb", 10, 55, 164), ("title", 12, 8, 42),
                                         ("floor", 12, 226, 22), ("status", 12, 250, 32)):
                        row[key].place_configure(x=px(x), y=px(y), relwidth=1, width=-px(x*2), height=px(h))
            self._columns = columns
        self.grid_frame.update_idletasks()
        self._schedule(1, self._load_visible)

    def _scroll_to(self, *args):
        self.canvas.yview(*args)
        self._load_visible()

    def _bind_scroll_tree(self, widget):
        tags = tuple(value for value in widget.bindtags() if value != self._wheel_tag)
        widget.bindtags((tags[0], self._wheel_tag, *tags[1:]))
        for child in widget.winfo_children():
            self._bind_scroll_tree(child)

    def _route_wheel(self, event):
        if self._detail is not None or event.widget.winfo_class() in {
                "Scale", "TScale", "TCombobox", "Text", "Listbox", "Treeview"}:
            return None
        canvas = getattr(self, "canvas", None)
        if canvas is None or not canvas.winfo_exists() or not event.delta:
            return None
        return self._wheel(event)

    def _wheel(self, event):
        self._scroll_to("scroll", -max(1, abs(event.delta) // 120) if event.delta > 0
                        else max(1, abs(event.delta) // 120), "units")
        return "break"

    def _load_visible(self):
        if not self._active or self._detail is not None:
            return
        top = self.canvas.canvasy(0)
        bottom = top + self.canvas.winfo_height()
        for row in self._cards:
            frame = row["frame"]
            size = (max(64, row["thumb"].winfo_width() - round(8 * self._scale)),
                    max(48, row["thumb"].winfo_height() - round(32 * self._scale)))
            if (row["binding"] and (not row["loaded"] or row["requested_size"] != size)
                    and frame.winfo_y() < bottom + frame.winfo_height()
                    and frame.winfo_y() + frame.winfo_height() > top - frame.winfo_height()):
                row["loaded"] = True
                row["requested_size"] = size
                _variant, floor, path = row["binding"]
                self._request(path, size, row["thumb"],
                              f"{floor_label(floor, source=row['item'].structure)} · 点击看大图")

    def _request(self, path, size, widget, caption=""):
        generation = self._generation
        try:
            stamp = path.stat().st_mtime_ns
        except OSError:
            widget._image_request = None
            widget.configure(image="", text="图片文件缺失\n请检查内置资源是否完整")
            return
        key = (str(path), stamp, size)
        widget._image_request = (generation, key)
        if key in self._cache:
            self._cache.move_to_end(key)
            self._apply_image(widget, self._cache[key], caption)
            return
        widget.configure(image="", text="正在加载图片…")
        # One latest request per widget; at most two submitted decodes.
        self._preview_waiting[widget] = (generation, key, path, size, caption)
        self._preview_waiting.move_to_end(widget)
        while len(self._preview_waiting) > 64:
            old_widget, _ = self._preview_waiting.popitem(last=False)
            old_widget._image_request = None
            for row in self._cards:
                if row.get("thumb") is old_widget:
                    row["loaded"] = False
        self._pump_preview_work()

    def _cancel_preview_work(self):
        self._pending.clear()
        self._preview_waiting.clear()
        for future in self._preview_futures.values():
            future.cancel()

    def _pump_preview_work(self):
        while self._preview_waiting and len(self._preview_futures) < 2:
            widget, request = self._preview_waiting.popitem(last=False)
            generation, key, path, size, caption = request
            if (generation != self._generation or not widget.winfo_exists()
                    or widget._image_request != (generation, key)):
                continue
            token = object()
            self._pending[token] = (generation, key, widget, caption)
            future = self._executor.submit(load_preview, path, size)
            self._preview_futures[token] = future
            def done(result, token=token):
                try:
                    self._results.put((token, result.result(), None))
                except CancelledError:
                    self._results.put((token, None, None))
                except Exception as error:
                    self._results.put((token, None, str(error)))
            future.add_done_callback(done)

    def _apply_image(self, widget, pixels, caption):
        photo = ImageTk.PhotoImage(pixels, master=self)
        widget.configure(image=photo, text=caption, compound="bottom")
        widget._map_photo = photo
        status = getattr(widget, "_status_widget", None)
        if status is not None:
            status.configure(text=widget._success_status,
                             fg=(ACCENT if "可预览" in widget._success_status else
                                 WARNING if any(word in widget._success_status for word in ("缺失", "未核对")) else MUTED))

    def _drain(self):
        deadline = time.perf_counter() + .006
        # Limit both elapsed time and number of Tk image uploads per turn.
        for _ in range(2):
            if time.perf_counter() >= deadline:
                break
            try:
                token, pixels, error = self._results.get_nowait()
            except queue.Empty:
                break
            self._preview_futures.pop(token, None)
            request = self._pending.pop(token, None)
            if request is None:
                continue
            generation, key, widget, caption = request
            if (generation != self._generation or not widget.winfo_exists()
                    or getattr(widget, "_image_request", None) != (generation, key)):
                continue
            if pixels is not None:
                self._cache[key] = pixels
                while (len(self._cache) > 48 or
                       sum(im.width * im.height * len(im.getbands())
                           for im in self._cache.values()) > 32 * 1024 * 1024):
                    self._cache.popitem(last=False)
                self._apply_image(widget, pixels, caption)
            elif error:
                widget.configure(image="", text="图片无法读取\n请检查资源文件或重新下载")
                status = getattr(widget, "_status_widget", None)
                if status is not None:
                    status.configure(text=widget._error_status, fg=WARNING)
        self._pump_preview_work()
        self._schedule(10 if self._preview_futures else 50, self._drain)

    def _show_detail(self, item):
        self._clear_content()
        self._detail = item
        self._controls_layout = None
        self._layout_controls()
        self.browse_controls.pack_forget()
        self.summary.pack_forget()
        toolbar = tk.Frame(self.content, bg=BG)
        toolbar.pack(fill="x", padx=SHADOW_DIP, pady=(0, 8))
        _button(toolbar, "返回地图列表", self._render_gallery).pack(side="left")
        _label(toolbar, item.name, size=12, bold=True).pack(side="left", padx=12)
        row = tk.Frame(self.content, bg=BG)
        row.pack(fill="x", padx=SHADOW_DIP, pady=(0, 8))
        self.variant_box = RoundedCombobox(row, state="readonly", values=[v.name for v in item.variants], width=18)
        self.variant_box.pack(side="left", fill="x", expand=True)
        self.variant_box.bind("<<ComboboxSelected>>", lambda _e: self._detail_variant())
        self.detail_floor_box = RoundedCombobox(row, state="readonly", width=15)
        self.detail_floor_box.pack(side="left", padx=(8, 0))
        self.detail_floor_box.bind("<<ComboboxSelected>>", lambda _e: self._detail_preview())
        self.detail_image = tk.Button(self.content, text="暂无可用楼层", bg=IMAGE_BG, fg=MUTED,
                                      activebackground=IMAGE_BG, activeforeground=TEXT,
                                      relief="flat", bd=0, font=(FONT, 10), cursor="hand2",
                                      command=self._open_preview)
        self.detail_image.pack(fill="both", expand=True, padx=SHADOW_DIP)
        self.detail_image.bind("<Configure>", self._detail_resized)
        self.detail_image.bind("<Return>", lambda _event: self._open_preview())
        self.detail_status = _label(self.content, size=9, color=MUTED, anchor="w", justify="left", wraplength=530)
        self.detail_image._status_widget = self.detail_status
        self.detail_status.pack(fill="x", padx=SHADOW_DIP, pady=(8, 5))
        self._details_open = False
        self.details_toggle = _button(self.content, "资源详情", self._toggle_details)
        self.details_toggle.pack(anchor="w", padx=SHADOW_DIP, pady=(0, 6))
        self.details_text = tk.Text(self.content, height=3, bg=INPUT_BG, fg=MUTED, relief="flat",
                                    wrap="char", font=(FONT, 8))
        if item.variants:
            self.variant_box.current(0)
        else:
            self.variant_box.set("该作者暂无此地图")
        self._apply_display()
        self._detail_variant()
        self.variant_box.focus_set()

    def _detail_variant(self):
        item = self._detail
        index = self.variant_box.current()
        self._variant = item.variants[index] if index >= 0 and item.variants else None
        self._detail_floors = tuple(self._variant.layers) if self._variant else ()
        self.detail_floor_box.configure(values=[floor_label(key, source=item.structure) for key in self._detail_floors])
        if self._detail_floors:
            filtered = self._floor_choices.get(self.floor_var.get())
            preferred = filtered or self.model.default_floor
            self.detail_floor_box.current(self._detail_floors.index(preferred)
                                          if preferred in self._detail_floors else 0)
        else:
            self.detail_floor_box.set("无可用楼层")
        self._detail_preview()

    def _detail_resized(self, _event):
        self._cancel(self._resize_job)
        self._resize_job = self._schedule(120, self._detail_preview)

    def _detail_preview(self):
        self._cancel(self._resize_job)
        self.detail_image.update_idletasks()
        self._cancel(self._resize_job)
        self._resize_job = None
        self._close_preview()
        self._generation += 1
        self._cancel_preview_work()
        floor_index = self.detail_floor_box.current()
        self._detail_path = None
        if self._variant is not None and floor_index >= 0:
            floor = self._detail_floors[floor_index]
            self._detail_path = Path(self._variant.layers[floor])
            purpose = "结构参考；源图标签不是游戏楼层，尚未绑定作者路线。" if self._detail.structure else "作者路线预览；实际显示仍由当前游戏识别结果决定。"
            self.detail_image._success_status = f"{floor_label(floor, source=self._detail.structure)} · {resource_status(self._detail, floor)}\n{purpose}"
            self.detail_image._error_status = f"{floor_label(floor, source=self._detail.structure)} · 图片损坏或无法读取\n{purpose}"
            self.detail_status.configure(text=self.detail_image._success_status, fg=MUTED)
            size = (max(120, self.detail_image.winfo_width() - 20),
                    max(80, self.detail_image.winfo_height() - 35))
            self._request(self._detail_path, size, self.detail_image, "点击放大查看")
        else:
            self.detail_image.configure(image="", text="此来源未收录这张地图的楼层图片")
            self.detail_status.configure(text="可切换上方浏览来源，或返回查看其他地图。")
        self._update_details()

    def _update_details(self):
        text = f"内部标识：{self._detail.id}\n素材版本：{self._variant.id if self._variant else '无'}\n文件：{self._detail_path or '无绑定'}"
        self.details_text.configure(state="normal")
        self.details_text.delete("1.0", "end")
        self.details_text.insert("1.0", text)
        self.details_text.configure(state="disabled")

    def _toggle_details(self):
        self._details_open = not self._details_open
        if self._details_open:
            self.details_text.pack(fill="x", padx=SHADOW_DIP, pady=(0, 8))
        else:
            self.details_text.pack_forget()
        self.details_toggle.configure(text="收起资源详情" if self._details_open else "资源详情")

    def _open_preview(self):
        if self._detail_path is None or not self._detail_path.is_file():
            return
        self._close_preview()
        window = tk.Toplevel(self)
        window.withdraw()
        self._preview_window = window
        window.title(f"{self._detail.name} · {self.detail_floor_box.get()}")
        window.configure(bg=BG)
        window.transient(self.winfo_toplevel())
        def close_on_escape(_event):
            self._close_preview()
            return "break"
        window.bind("<Escape>", close_on_escape)
        window.protocol("WM_DELETE_WINDOW", self._close_preview)
        tools = tk.Frame(window, bg=BG)
        tools.pack(fill="x", padx=12, pady=8)
        _label(tools, "完整图像 · 滚轮或 + / − 缩放，0 适应窗口", size=9, color=MUTED).pack(anchor="w", pady=(0, 6))
        actions = tk.Frame(tools, bg=BG)
        actions.pack(fill="x")
        smaller = _button(actions, "缩小", lambda: change_zoom(1 / 1.2))
        smaller.pack(side="left")
        larger = _button(actions, "放大", lambda: change_zoom(1.2))
        larger.pack(side="left", padx=6)
        fit = _button(actions, "适应窗口", lambda: change_zoom(reset=True))
        fit.pack(side="left")
        zoom_label = _label(actions, "100%", size=9, color=MUTED)
        zoom_label.pack(side="left", padx=8)
        _button(actions, "关闭预览", self._close_preview).pack(side="right")
        host = tk.Frame(window, bg=IMAGE_BG)
        host.pack(fill="both", expand=True)
        canvas = tk.Canvas(host, bg=IMAGE_BG, highlightthickness=0)
        sx = tk.Scrollbar(host, orient="horizontal", command=canvas.xview)
        sy = tk.Scrollbar(host, orient="vertical", command=canvas.yview)
        canvas.configure(xscrollcommand=sx.set, yscrollcommand=sy.set)
        sx.pack(side="bottom", fill="x")
        sy.pack(side="right", fill="y")
        canvas.pack(side="left", fill="both", expand=True)
        image_label = tk.Label(canvas, bg=IMAGE_BG, fg=MUTED, text="正在加载图片…", font=(FONT, 10))
        image_id = canvas.create_window((0, 0), window=image_label, anchor="nw")
        image_label.bind("<Configure>", lambda _e: canvas.configure(scrollregion=canvas.bbox("all")))
        zoom = [1.0]
        render_job = [None]
        path = self._detail_path
        def render():
            if self._preview_window is not window or not window.winfo_exists():
                return
            width, height = max(100, canvas.winfo_width()), max(100, canvas.winfo_height())
            self._request(path, (round((width - 16) * zoom[0]), round((height - 16) * zoom[0])), image_label)
            canvas.coords(image_id, 8, 8)
        def resize(_e=None):
            self._cancel(render_job[0])
            render_job[0] = self._schedule(100, render)
        def change_zoom(factor=1.0, *, reset=False):
            zoom[0] = 1.0 if reset else min(4.0, max(0.5, zoom[0] * factor))
            zoom_label.configure(text=f"{zoom[0]:.0%}")
            smaller.configure(state="disabled" if zoom[0] <= .5 else "normal")
            larger.configure(state="disabled" if zoom[0] >= 4 else "normal")
            resize()
            return "break"
        def wheel(event):
            return change_zoom(1.2 if event.delta > 0 else 1 / 1.2)
        canvas.bind("<Configure>", resize)
        canvas.bind("<MouseWheel>", wheel)
        image_label.bind("<MouseWheel>", wheel)
        for key in ("<plus>", "<equal>", "<KP_Add>"):
            window.bind(key, lambda _event: change_zoom(1.2))
        for key in ("<minus>", "<KP_Subtract>"):
            window.bind(key, lambda _event: change_zoom(1 / 1.2))
        window.bind("<Key-0>", lambda _event: change_zoom(reset=True))
        self._preview_controls = (smaller, larger, fit, zoom_label)
        def display_changed(scale):
            set_rounded_scale(window, scale)
            resize()
        display = WindowDisplay(window, self.assistant, preferred=(900, 700),
                                minimum=(400, 300), on_scale=display_changed)
        display.start(self.winfo_toplevel())
        window.deiconify()

    def _close_preview(self):
        if self._preview_window is not None:
            window = self._preview_window
            self._preview_window = None
            window.destroy()
            if self._active and self._detail is not None and self.winfo_viewable():
                self.detail_image.focus_set()

    def _destroy(self, event):
        if event.widget is not self:
            return
        self._closed = True
        self._root().unbind_class(self._wheel_tag, "<MouseWheel>")
        self._root().deletecommand(self._wheel_binding)
        for job in tuple(self._jobs):
            self._scheduler.after_cancel(job)
        self._jobs.clear()
        self._cancel_preview_work()
        self._executor.shutdown(wait=False, cancel_futures=True)
