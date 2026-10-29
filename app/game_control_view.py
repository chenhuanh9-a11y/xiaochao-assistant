"""Game controls only; the caller owns facts, commands and window lifecycle.

Construct inside an existing parent, then call render with the runtime snapshot.
No window, polling, focus, key registration or settings ownership lives here.
"""
from __future__ import annotations

from collections.abc import Callable, Mapping
import tkinter as tk
from tkinter import font as tkfont

from desktop_rounded import RoundedButton
from desktop_lucide import LucideIcons
from star_brand import BrandImages
from opacity_controls import OpacityUpdates, opacity_percent
from control_layout import load_layout
from desktop_theme import (SURFACE, LINE, TEXT, MUTED, FONT, PRIMARY,
                           BUTTON_BG, BUTTON_HOVER, BUTTON_TEXT, GOLD,
                           WARNING_BG, status_color)


def _fact(snapshot, name, yes, no):
    value = snapshot.get(name)
    return yes if value is True else no if value is False else "未知"


def display_summary(snapshot):
    """Report supplied logical conditions, never claim native pixels appeared."""
    intent = _fact(snapshot, "userVisible", "开", "关")
    reference_intent = _fact(snapshot, "referenceEnabled", "开", "关")
    pose = _fact(snapshot, "verified", "已核对", "待核对")
    overlay = _fact(snapshot, "overlayVisible", "条件满足", "条件未满足")
    reference = _fact(snapshot, "referenceVisible", "条件满足", "条件未满足")
    return f"贴合开关：{intent} · 定点开关：{reference_intent} · 位置：{pose}\n贴合显示：{overlay} · 定点地图：{reference}"


def identity_summary(snapshot, map_name):
    locked = snapshot.get("locked")
    identity = "地图已确认" if locked is True else "地图待确认" if locked is False else "地图状态未知"
    floor = {"1": "1楼", "2": "2楼", "B1": "地下1层"}.get(str(snapshot.get("floor")), "未知")
    # current_floor may be a target/seed; its presence does not verify the floor.
    return f"{identity} · 目标楼层：{floor}"


class GameControlView(tk.Frame):
    """One host, two internal layouts. Controller facts decide every action."""

    OPACITY_WIDTH_DIP = 24
    OPACITY_SLIDER_LENGTH_DIP = 32
    EXPANDED_WIDTH_DIP = 352
    EXPANDED_HEIGHT_DIP = 250
    BALL_SIZE_DIP = 64
    BALL_TRANSPARENT_COLOR = "#010203"

    def __init__(self, parent, *, opacity_var=None, reference_opacity_var=None, commands: Mapping[str, Callable]):
        super().__init__(parent, bg=SURFACE)
        self.opacity_var = opacity_var if opacity_var is not None else tk.IntVar(self, value=55)
        self.reference_opacity_var = (reference_opacity_var if reference_opacity_var is not None
                                      else tk.IntVar(self, value=self.opacity_var.get()))
        self._commands = dict(commands)
        self._opacity_updates = OpacityUpdates(self.after, self.after_cancel,
            {surface: (lambda value, name=command: self._call(name, str(value)))
             for surface, command in (('overlay', 'change_sheet_opacity'),
                                      ('reference', 'change_reference_opacity')) if self._has(command)},
            lambda: self._call('_save_settings'))
        self._icons = LucideIcons(self._root())
        self._brand_images = BrandImages(self._root())
        self.bind("<Destroy>", self._destroy_brand, add="+")
        self._scale, self._collapsed, self._press_origin = 1.0, False, None
        self.expanded_frame = tk.Frame(self, bg=SURFACE)
        self.expanded_frame.configure(width=self.EXPANDED_WIDTH_DIP, height=self.EXPANDED_HEIGHT_DIP)
        self.expanded_frame.grid_propagate(False)
        self.expanded_frame.columnconfigure(0, weight=1, minsize=self.EXPANDED_WIDTH_DIP)
        self.expanded_frame.pack(fill="both", expand=True)
        self.expanded_frame.rowconfigure(1, weight=1)
        self.header = tk.Frame(self.expanded_frame, bg=SURFACE)
        self.header.grid(row=0, column=0, sticky="ew", padx=12, pady=(8, 4))
        self.header.columnconfigure(0, weight=1)
        self.status_title = self._label(self.header, "等待游戏", size=10, bold=True)
        self.status_title.grid(row=0, column=0, sticky="ew")
        tools = tk.Frame(self.header, bg=SURFACE)
        tools.grid(row=0, column=1, rowspan=2, sticky="ne", padx=(5, 0))
        self.collapse_button = self._button(tools, "收起", "collapse_control", padx=7, pady=6)
        self.collapse_button.pack(side="left", padx=(0, 4))
        self.more_button = self._button(tools, "⋯", "hide_control", padx=9, pady=6)
        self.more_button.configure(state="normal", command=self._show_more)
        self.more_button.pack(side="left")
        self.identity_label = self._label(self.header, "楼层待核对", color=MUTED)
        self.identity_label.grid(row=1, column=0, sticky="ew", pady=(3, 0))
        self.summary_label = self._label(self.header, "", color=MUTED, size=8)
        self.summary_label.grid(row=2, column=0, columnspan=2, sticky="ew", pady=(5, 0))
        # Old aliases stay available to the runtime; their actions live in More.
        self.quick_main_button = self._button(tools, "主页", "_open_desktop_main_ui")
        self.hide_button = self._button(tools, "隐藏", "hide_control")
        self._hide_key = self._keycap(self.hide_button)
        self.more_menu = tk.Menu(self, tearoff=False, bg=SURFACE, fg=TEXT,
                                 font=(FONT, -13),
                                 activebackground=BUTTON_BG, activeforeground=BUTTON_TEXT)
        self.scroll_host = tk.Frame(self.expanded_frame, bg=SURFACE)
        self.scroll_host.grid(row=1, column=0, sticky="nsew", padx=12)
        self.scroll_host.rowconfigure(0, weight=1)
        self.scroll_host.columnconfigure(0, weight=1)
        self.content_canvas = tk.Canvas(self.scroll_host, bg=SURFACE, bd=0, highlightthickness=0, height=1)
        self.content_canvas.grid(row=0, column=0, sticky="nsew")
        self.scrollbar = tk.Scrollbar(self.scroll_host, orient="vertical", command=self.content_canvas.yview)
        self.content_canvas.configure(yscrollcommand=self.scrollbar.set)
        self.module_host = tk.Frame(self.content_canvas, bg=SURFACE)
        self._content_window = self.content_canvas.create_window(0, 0, anchor="nw", window=self.module_host)
        self.module_host.bind("<Configure>", self._content_changed)
        self.content_canvas.bind("<Configure>", self._canvas_changed)
        self.content_canvas.bind("<MouseWheel>", self._scroll_content)
        self.modules = {key: tk.Frame(self.module_host, bg=SURFACE) for key in ('display', 'map', 'recognition', 'round')}
        self.controls = self.modules['display']
        self.visibility_controls = tk.Frame(self.controls, bg=SURFACE)
        self.visibility_controls.pack(fill="x", pady=(6, 0))
        self.visibility_controls.columnconfigure((0, 1), weight=1, uniform="visibility")
        self.overlay_controls = tk.Frame(self.visibility_controls, bg=SURFACE)
        self.overlay_controls.grid(row=0, column=0, sticky="new", padx=(0, 3))
        self.quick_sheet_visibility_button = self._button(self.overlay_controls, "贴合 未知", "toggle_sheet_visibility", padx=5)
        self.quick_sheet_visibility_button.pack(fill="x")
        self._visibility_key = self._keycap(self.quick_sheet_visibility_button)
        self.reference_controls = tk.Frame(self.visibility_controls, bg=SURFACE)
        self.reference_controls.grid(row=0, column=1, sticky="new", padx=(3, 0))
        self.quick_reference_visibility_button = self._button(self.reference_controls, "定点 未知", "set_reference_sheet_visible", padx=5)
        self.quick_reference_visibility_button.configure(command=self._toggle_reference_visibility)
        self.quick_reference_visibility_button.pack(fill="x")
        self.reference_adjust_button = self._button(self.reference_controls, "编辑位置", "move_sheet", padx=5, pady=5)
        self.reference_adjust_button.configure(command=self._adjust_reference)
        self.opacity_controls = tk.Frame(self.controls, bg=SURFACE)
        self.opacity_controls.pack(fill="x")
        self.opacity_scales, self.opacity_values, self.opacity_cells = {}, {}, {}
        for surface, variable, command, title in (
                ('overlay', self.opacity_var, 'change_sheet_opacity', '贴合不透明度'),
                ('reference', self.reference_opacity_var, 'change_reference_opacity', '定点不透明度')):
            cell = self.opacity_cells[surface] = tk.Frame(self.opacity_controls, bg=SURFACE)
            heading = tk.Frame(cell, bg=SURFACE)
            heading.pack(fill="x", pady=(6, 0))
            self._label(heading, title, color=MUTED).pack(side="left")
            self.opacity_values[surface] = self._label(heading, "", bold=True)
            self.opacity_values[surface].pack(side="right")
            self._opacity_updates.sync(surface, variable.get())
            slider = tk.Scale(cell, from_=0, to=100, orient="horizontal", showvalue=False,
                variable=variable, command=lambda value, key=surface: self._opacity_changed(key, value),
                state="normal" if self._has(command) else "disabled", bg=SURFACE, fg=TEXT,
                troughcolor=LINE, activebackground=PRIMARY, bd=0, highlightthickness=0,
                width=self.OPACITY_WIDTH_DIP, sliderlength=self.OPACITY_SLIDER_LENGTH_DIP, length=1, resolution=1)
            slider.pack(fill="x")
            slider.bind("<ButtonRelease-1>", lambda _event: self._opacity_updates.commit())
            slider.bind("<KeyRelease>", self._opacity_key_released)
            self.opacity_scales[surface] = slider
        self.quick_opacity_scale = self.opacity_scales['overlay']
        self.quick_opacity_value = self.opacity_values['overlay']
        mode_host = tk.Frame(self.modules['map'], bg=SURFACE)
        mode_host.pack(fill="x", pady=(8, 0))
        mode_host.columnconfigure((0, 1), weight=1, uniform="mode")
        self.mode_buttons = {}
        for column, (mode, label) in enumerate((("nightmare", "噩梦"), ("difficult", "困难"))):
            button = self._button(mode_host, label, "set_game_mode", padx=5, pady=6)
            button.configure(command=lambda value=mode: self._call("set_game_mode", value))
            button.grid(row=0, column=column, sticky="ew", padx=2)
            self.mode_buttons[mode] = button
        self.map_button = self._button(self.modules['map'], "选择地图", "_start_map_reselection")
        self.map_button.pack(fill="x", pady=(4, 0))
        self.recognition_buttons, self.recognition_keys = {}, {}
        self.candidate_label = self._label(self.modules['recognition'], "尚无候选", color=MUTED)
        self.candidate_label.pack(fill="x", pady=(6, 2))
        for command, title, binding in (('previous_candidate', '上一个候选', 'candidate-prev'),
                ('next_candidate', '下一个候选', 'candidate-next'), ('confirm_route', '确认地图', 'confirm-round'),
                ('capture_now', '重新识别', None)):
            host = tk.Frame(self.modules['recognition'], bg=SURFACE)
            host.pack(fill="x", pady=(4, 0))
            button = self._button(host, title, command)
            button.pack(fill="x")
            self.recognition_buttons[command] = button
            if binding:
                self.recognition_keys[binding] = self._keycap(button)
        self._round_host = self.modules['round']
        self.quick_start_round_button = self._button(self._round_host, "开始本局", "start_round", pady=9)
        self.quick_end_round_button = self._button(self._round_host, "结束本局", "end_round", pady=9)
        for button, active in ((self.quick_start_round_button, False), (self.quick_end_round_button, True)):
            button.configure(command=lambda value=active: self._round_action(value), bg=PRIMARY if not active else BUTTON_BG,
                             fg="white" if not active else BUTTON_TEXT, anchor="center")
        self._start_key = self._keycap(self.quick_start_round_button)
        self._end_key = self._keycap(self.quick_end_round_button)
        self.edit_bar = tk.Frame(self.expanded_frame, bg=SURFACE)
        self._label(self.edit_bar, "正在调整定点位置", color=PRIMARY).pack(fill="x", pady=(4, 3))
        edit_actions = tk.Frame(self.edit_bar, bg=SURFACE)
        edit_actions.pack(fill="x")
        edit_actions.columnconfigure((0, 1), weight=1, uniform="edit")
        self.finish_edit_button = self._button(edit_actions, "完成", "finish_reference_sheet_move")
        self.finish_edit_button.grid(row=0, column=0, sticky="ew", padx=(0, 3))
        self.cancel_edit_button = self._button(edit_actions, "取消", "cancel_reference_sheet_move")
        self.cancel_edit_button.grid(row=0, column=1, sticky="ew", padx=(3, 0))
        pending_widgets = [self.module_host]
        while pending_widgets:
            widget = pending_widgets.pop()
            pending_widgets.extend(widget.winfo_children())
            # Keep native horizontal scale wheel behavior; all other content
            # descendants scroll only this panel, with no root/global binding.
            if not isinstance(widget, tk.Scale):
                widget.bind("<MouseWheel>", self._scroll_content, add="+")
        self._layout = None
        self._available_height = None
        self._editing = False
        self._bindings = {}
        self._last_snapshot = {}
        self._geometry_pending = None
        self.preferred_height_px = self.EXPANDED_HEIGHT_DIP
        self.apply_layout(None)
        self.collapsed_frame = tk.Frame(self, bg=self.BALL_TRANSPARENT_COLOR)
        self.collapsed_canvas = tk.Canvas(
            self.collapsed_frame, width=64, height=64, bg=self.BALL_TRANSPARENT_COLOR,
            bd=0, highlightthickness=0, cursor="hand2")
        self.collapsed_canvas.pack()
        self._orb_ids = {
            "orb_body": self.collapsed_canvas.create_oval(.5, .5, 63.5, 63.5, fill="#eaf1ff", outline="#c0d0e9", width=1),
            "panel_icon": self.collapsed_canvas.create_image(32, 24),
            "panel_arrow": self.collapsed_canvas.create_line(29, 25, 32, 28, 35, 25, fill=PRIMARY, width=1.7, capstyle="round", joinstyle="round"),
            "orb_mode_text": self.collapsed_canvas.create_text(32, 46, text="展开", fill=PRIMARY),
        }
        self._orb_base_coords = {item: self.collapsed_canvas.coords(item) for item in self._orb_ids.values()}
        self.collapsed_canvas.bind("<ButtonPress-1>", self._ball_press, add="+")
        self.collapsed_canvas.bind("<ButtonRelease-1>", self._ball_release, add="+")
        self.set_display_scale(1.0)
        self.render({}, map_name="", mode_text="")

    def apply_layout(self, layout):
        """Apply detached preferences without changing any runtime/game state."""
        normalized = load_layout(layout)
        if normalized == self._layout:
            return
        self._layout = normalized
        for module in self.modules.values():
            module.pack_forget()
        for key in normalized['order']:
            if key in normalized['enabled']:
                self.modules[key].pack(fill="x", pady=(0, round(4*self._scale)))
        self.module_host.pack_propagate(bool(normalized['enabled']))
        if not normalized['enabled']:
            self.module_host.configure(height=1)
        for key, option in (('overlay', 'alignedOpacity'), ('reference', 'referenceOpacity')):
            self.opacity_cells[key].pack_forget()
            if normalized['display'][option]:
                self.opacity_cells[key].pack(fill="x")
        self.reference_adjust_button.pack_forget()
        if normalized['display']['referencePosition']:
            self.reference_adjust_button.pack(fill="x", pady=(round(4*self._scale), 0))
        self._apply_edit_bar()
        self.content_canvas.yview_moveto(0)
        self._queue_geometry()

    def _apply_edit_bar(self):
        if self._editing:
            self.reference_adjust_button.pack_forget()
            self.edit_bar.grid(row=2, column=0, sticky="ew", padx=round(12*self._scale),
                               pady=(round(4*self._scale), round(8*self._scale)))
        else:
            self.edit_bar.grid_remove()
            if self._layout and self._layout['display']['referencePosition']:
                self.reference_adjust_button.pack(fill="x", pady=(round(4*self._scale), 0))

    def set_available_height(self, physical_px):
        """Cap expanded height in physical pixels. Fixed chrome sets the minimum.

        The caller owns the native window geometry and must use expanded_size().
        Below minimum_height_px the fixed recovery/edit actions cannot fit.
        """
        physical_px = max(1, int(physical_px))
        if physical_px == self._available_height:
            return
        self._available_height = physical_px
        self._queue_geometry()

    def expanded_size(self):
        self.update_idletasks()
        self._measure_geometry()
        return round(self.EXPANDED_WIDTH_DIP*self._scale), self.preferred_height_px

    def _queue_geometry(self):
        if self._geometry_pending is None:
            self._geometry_pending = self.after_idle(self._measure_geometry)

    def _measure_geometry(self):
        self._geometry_pending = None
        if not self.winfo_exists():
            return
        width = round(self.EXPANDED_WIDTH_DIP*self._scale)
        self.summary_label.configure(wraplength=max(100, width-round(24*self._scale)))
        self.status_title.configure(wraplength=max(80, width-round(130*self._scale)))
        self.identity_label.configure(wraplength=max(80, width-round(130*self._scale)))
        self.candidate_label.configure(wraplength=max(100, width-round(50*self._scale)))
        fixed = self.header.winfo_reqheight()+round(12*self._scale)
        if self._editing:
            fixed += self.edit_bar.winfo_reqheight()+round(12*self._scale)
        self.minimum_height_px = fixed+1
        natural = fixed + self.module_host.winfo_reqheight()+round(8*self._scale)
        cap = self._available_height if self._available_height is not None else round(640*self._scale)
        self.preferred_height_px = min(natural, max(self.minimum_height_px, cap))
        self.expanded_frame.configure(width=width, height=self.preferred_height_px)
        self._update_scrollbar()

    def _canvas_changed(self, event):
        self.content_canvas.itemconfigure(self._content_window, width=event.width)
        self._update_scrollbar()

    def _content_changed(self, _event=None):
        self.content_canvas.configure(scrollregion=self.content_canvas.bbox("all"))
        self._queue_geometry()

    def _update_scrollbar(self):
        if self.module_host.winfo_reqheight() > self.content_canvas.winfo_height()+1:
            self.scrollbar.grid(row=0, column=1, sticky="ns")
        else:
            self.scrollbar.grid_remove()
            self.content_canvas.yview_moveto(0)

    def _scroll_content(self, event):
        if self.scrollbar.winfo_manager():
            self.content_canvas.yview_scroll(-int(event.delta/120), "units")
        return "break"

    def _refresh_menu(self):
        self.more_menu.delete(0, "end")
        key = self._binding_text(self._bindings, "toggle-panel")
        for title, command in (("主页", "_open_desktop_main_ui"), ("自定义控件", "open_control_layout_settings"),
                ("隐藏控件", "hide_control"), ("选择地图", "_start_map_reselection"),
                ("重新识别", "capture_now"), ("识别详情", "_open_recognition_details")):
            self.more_menu.add_command(label=title, command=self._command(command),
                accelerator=key if command == 'hide_control' else "",
                state="normal" if self._has(command) else "disabled")

    def _show_more(self):
        self._refresh_menu()
        try:
            self.more_menu.tk_popup(self.more_button.winfo_rootx(),
                                    self.more_button.winfo_rooty()+self.more_button.winfo_height())
        finally:
            self.more_menu.grab_release()

    def _command(self, name):
        callback = self._commands.get(name)
        if name == "hide_control" and not callable(callback):
            callback = self._commands.get("_collapse_assistant_shell")
        return callback if callable(callback) else None

    def _has(self, name):
        return self._command(name) is not None

    def _call(self, name, *args):
        callback = self._command(name)
        return callback(*args) if callback is not None else None

    def _round_action(self, active):
        if active:
            return self._call("end_round")
        getter = self._command("get_control_snapshot")
        snapshot = getter() if getter is not None else {}
        action = "toggle_round" if snapshot.get("roundPreparing") is True else "start_round"
        return self._call(action)

    def _adjust_reference(self):
        getter = self._command("get_control_snapshot")
        if getter is None:
            return None
        snapshot = getter()
        adjusting = snapshot.get("referenceAdjusting") is True
        if not adjusting and snapshot.get("referenceCanAdjust") is not True:
            return None
        return self._call("finish_reference_sheet_move" if adjusting else "move_sheet")

    def _sync_reference_action(self, snapshot):
        adjusting = snapshot.get("referenceAdjusting") is True
        action = "finish_reference_sheet_move" if adjusting else "move_sheet"
        enabled = (self._has("get_control_snapshot") and self._has(action)
                   and (adjusting or snapshot.get("referenceCanAdjust") is True))
        self.reference_adjust_button.configure(text="完成" if adjusting else "",
                                              image="" if adjusting else getattr(self, "_reference_edit_icon", ""),
                                              state="normal" if enabled else "disabled")

    def _toggle_reference_visibility(self):
        getter = self._command("get_control_snapshot")
        if getter is None:
            return None
        enabled = getter().get("referenceEnabled")
        if not isinstance(enabled, bool):
            return None
        return self._call("set_reference_sheet_visible", not enabled)

    def _sync_visibility_controls(self, snapshot):
        for button, label, intent_key, visible_key, command in (
            (self.quick_sheet_visibility_button, "贴合", "userVisible", "overlayVisible", "toggle_sheet_visibility"),
            (self.quick_reference_visibility_button, "定点", "referenceEnabled", "referenceVisible", "set_reference_sheet_visible"),
        ):
            intent = snapshot.get(intent_key)
            status = "开" if intent is True else "关" if intent is False else "未知"
            available = isinstance(intent, bool) and self._has(command)
            if command == "set_reference_sheet_visible":
                available = available and self._has("get_control_snapshot")
            button.configure(text=f"{label} {status}",
                             state="normal" if available else "disabled",
                             bg=PRIMARY if intent is True else BUTTON_BG,
                             fg="white" if intent is True else BUTTON_TEXT,
                             activebackground=PRIMARY if intent is True else BUTTON_HOVER,
                             activeforeground="white" if intent is True else BUTTON_TEXT)
            icons = getattr(self, "_icons", None)
            if icons is not None:
                name = "map" if command == "set_reference_sheet_visible" else "eye" if intent is True else "eye-off"
                button._lucide_image = icons.get(name, round(17*self._scale), "white" if intent is True else BUTTON_TEXT)
                button.configure(image=button._lucide_image, compound="left")

    def _opacity_key_released(self, event):
        if event.keysym in {"Left", "Right", "Up", "Down", "Prior", "Next", "Home", "End"}:
            self._opacity_updates.commit()

    def _opacity_changed(self, surface, value):
        try:
            percent = opacity_percent(value)
        except (TypeError, ValueError):
            return
        self.opacity_values[surface].configure(text=f"{percent}%")
        self._opacity_updates.change(surface, percent)

    def _sync_opacity_controls(self, snapshot):
        for surface, key, variable in (
                ('overlay', 'sheetOpacityPercent', self.opacity_var),
                ('reference', 'referenceOpacityPercent', self.reference_opacity_var)):
            try:
                percent = self._opacity_updates.sync(surface, snapshot.get(key, variable.get()))
            except (TypeError, ValueError, tk.TclError):
                continue
            if variable.get() != percent:
                variable.set(percent)
            self.opacity_values[surface].configure(text=f"{percent}%")

    def _button(self, parent, text, command, *, padx=12, pady=8):
        return RoundedButton(parent, text=text, command=self._command(command),
                             state="normal" if self._has(command) else "disabled",
                             bg=BUTTON_BG, fg=BUTTON_TEXT, activebackground=BUTTON_HOVER,
                             activeforeground=BUTTON_TEXT, highlightbackground=LINE,
                             highlightcolor=PRIMARY, highlightthickness=1,
                             font=(FONT, -13, "bold"), padx=padx, pady=pady, cursor="hand2")

    def _label(self, parent, text="", *, size=9, color=TEXT, bold=False):
        return tk.Label(parent, text=text, bg=SURFACE, fg=color, anchor="w",
                        font=(FONT, -round(size*96/72), "bold" if bold else "normal"))

    def _keycap(self, button):
        # A separate row cannot collide with native button text, even for chords.
        label = tk.Label(button.master, bg=SURFACE, fg=MUTED, font=(FONT, -11), anchor="center")
        label._key_button = button
        label.bind("<ButtonRelease-1>", lambda _event: button.invoke())
        return label

    @staticmethod
    def _binding_text(bindings, target):
        binding = (bindings or {}).get(target)
        text = str(binding.get("text") or "") if isinstance(binding, dict) else str(binding or "")
        return "" if text in {"未绑定", "未设置", "尚未读取", "未读取", "", "—"} else text

    def _paint_key(self, label, text):
        label.configure(text=text, wraplength=max(70, round(145*self._scale)))
        button = label._key_button
        if text and button.winfo_manager():
            label.pack(fill="x", after=button, pady=(round(self._scale), round(2*self._scale)))
        else:
            label.pack_forget()

    def _set_collapsed(self, collapsed):
        if collapsed == self._collapsed:
            return
        self._collapsed = collapsed
        if collapsed:
            self.expanded_frame.pack_forget()
            self.configure(bg=self.BALL_TRANSPARENT_COLOR)
            self.collapsed_frame.pack()
        else:
            self.collapsed_frame.pack_forget()
            self.configure(bg=SURFACE)
            self.expanded_frame.pack(fill="both", expand=True)

    def _ball_press(self, event):
        self._press_origin = (event.x_root, event.y_root)
        self.collapsed_canvas.itemconfigure(self._orb_ids["orb_body"], fill=BUTTON_HOVER)

    def _ball_release(self, event):
        origin, self._press_origin = self._press_origin, None
        self.collapsed_canvas.itemconfigure(self._orb_ids["orb_body"], fill="#eaf1ff")
        if origin and max(abs(event.x_root-origin[0]), abs(event.y_root-origin[1])) <= 3 * self._scale:
            self._call("expand_control")

    def _scale_typography_and_spacing(self):
        """Own physical font pixels without changing interpreter-wide scaling.

        Capture 96-DPI tuples once, then always derive from that baseline.
        Negative pixel fonts also let WindowDisplay produce the same final size
        when a host recursively scales this view; neither path compounds sizes.
        One reusable named font is allocated per distinct base tuple; changing
        DPI reconfigures that font instead of allocating one on each render.
        """
        if not hasattr(self, '_scale_metrics'):
            self._scale_metrics = []
            self._pixel_fonts = {}
            pending = [self]
            while pending:
                widget = pending.pop()
                pending.extend(widget.winfo_children())
                attributes, geometry, manager = {}, {}, widget.winfo_manager()
                for option in ('padx', 'pady'):
                    try:
                        attributes[option] = int(widget.cget(option))
                    except (tk.TclError, TypeError, ValueError):
                        pass
                try:
                    parts = tuple(self.tk.splitlist(widget.cget('font')))
                    size = int(parts[1])
                    # All visible text starts with negative px; tolerate a host
                    # supplying a point tuple before initial scale capture.
                    pixels = abs(size) if size < 0 else round(size*96/72)
                    attributes['font'] = (parts[0], pixels,
                        parts[2] if len(parts) > 2 else 'normal',
                        parts[3] if len(parts) > 3 else 'roman')
                except (tk.TclError, IndexError, TypeError, ValueError):
                    pass
                if manager in ('pack', 'grid'):
                    info = widget.pack_info() if manager == 'pack' else widget.grid_info()
                    for option in ('padx', 'pady', 'ipadx', 'ipady'):
                        if option in info:
                            raw = info[option]
                            parts = raw if isinstance(raw, (tuple, list)) else self.tk.splitlist(str(raw))
                            geometry[option] = tuple(int(value) for value in parts)
                self._scale_metrics.append((widget, attributes, manager, geometry))
        for widget, attributes, manager, geometry in self._scale_metrics:
            changes = {}
            for option, base in attributes.items():
                current = widget.cget(option)
                if option == 'font':
                    target = (base[0], -max(1, round(base[1]*self._scale)), *base[2:])
                    pixel_font = self._pixel_fonts.get(base)
                    if pixel_font is None:
                        pixel_font = self._pixel_fonts[base] = tkfont.Font(root=self._root(),
                            family=base[0], size=target[1], weight=base[2], slant=base[3])
                    elif int(pixel_font.cget('size')) != target[1]:
                        pixel_font.configure(size=target[1])
                    equal = (str(current) == str(pixel_font) or
                        tuple(map(str, self.tk.splitlist(current))) == tuple(map(str, target)))
                    widget._control_pixel_font = pixel_font
                    target = pixel_font
                else:
                    target = round(base*self._scale)
                    equal = int(current) == target
                if not equal:
                    changes[option] = target
            if changes:
                widget.configure(**changes)
            if manager and manager == widget.winfo_manager() and geometry:
                options = {key: tuple(round(value*self._scale) for value in values)
                           for key, values in geometry.items()}
                if manager == 'pack':
                    widget.pack_configure(**options)
                elif manager == 'grid':
                    widget.grid_configure(**options)
        self._apply_scaled_button_fonts()

    def _apply_scaled_button_fonts(self):
        # RoundedButton normalizes font tuples through point-based Tk metadata.
        # Styles are shared across views, including the read-only editor preview.
        # A tuple keeps their font independent of any one view's Font lifetime.
        # Rendering can select a different style for selected/disabled colors.
        for widget, attributes, _manager, _geometry in self._scale_metrics:
            if isinstance(widget, RoundedButton) and 'font' in attributes:
                base = attributes['font']
                pixels = (base[0], -max(1, round(base[1]*self._scale)), *base[2:])
                style = str(widget.cget('style'))
                current = widget._skins.style.configure(style, 'font')
                # ttk broadcasts style changes across the interpreter, even
                # when the font is identical. A control status refresh must
                # not invalidate every page's layout on each rendered button.
                if tuple(map(str, self.tk.splitlist(current))) != tuple(map(str, pixels)):
                    widget._skins.style.configure(style, font=pixels)

    def set_display_scale(self, scale):
        self._scale = max(.5, float(scale))
        self._scale_typography_and_spacing()
        self.expanded_frame.columnconfigure(0, minsize=0)
        self.collapsed_canvas.configure(width=round(64*self._scale), height=round(64*self._scale))
        for item, coords in self._orb_base_coords.items():
            self.collapsed_canvas.coords(item, *(round(value*self._scale) for value in coords))
        self._ball_photo = (self._brand_images.get("round", round(23*self._scale))
                            or self._icons.get("panel", round(23*self._scale), PRIMARY))
        self.collapsed_canvas.itemconfigure(self._orb_ids["panel_icon"], image=self._ball_photo)
        self.collapsed_canvas.itemconfigure(self._orb_ids["panel_arrow"], width=max(1, 1.7*self._scale))
        self.collapsed_canvas.itemconfigure(self._orb_ids["orb_body"], width=max(1, self._scale))
        self.collapsed_canvas.itemconfigure(self._orb_ids["orb_mode_text"], font=(FONT, -max(1, round(11*self._scale))))
        self._reference_edit_icon = self._icons.get("settings", round(15*self._scale), BUTTON_TEXT)
        self.reference_adjust_button.configure(image=self._reference_edit_icon, compound="left")
        for slider in self.opacity_scales.values():
            slider.configure(width=max(1, round(self.OPACITY_WIDTH_DIP*self._scale)),
                             sliderlength=max(1, round(self.OPACITY_SLIDER_LENGTH_DIP*self._scale)))
        self._queue_geometry()

    def _destroy_brand(self, event):
        if event.widget is self:
            if self._geometry_pending is not None:
                self.after_cancel(self._geometry_pending)
                self._geometry_pending = None
            try:
                self._opacity_updates.commit()
            finally:
                self._opacity_updates.close()
            self._brand_images.close()
            self._ball_photo = None
            # Destroy is delivered on the owning Tk thread. Child widgets can
            # retain Font references after their Tcl windows are gone; release
            # native fonts here so later Python GC never calls Tcl from a worker.
            for font in getattr(self, '_pixel_fonts', {}).values():
                if font.delete_font:
                    try:
                        self.tk.call('font', 'delete', font.name)
                    except tk.TclError:
                        pass
                    font.delete_font = False
            self._pixel_fonts = {}
            self._scale_metrics = []

    def render(self, snapshot, *, map_name="", mode_text="", bindings=None):
        self._last_snapshot = dict(snapshot)
        self._editing = snapshot.get("referenceAdjusting") is True
        self._set_collapsed(snapshot.get("controlCollapsed") is True and not self._editing)
        self._sync_reference_action(snapshot)
        self.status_title.configure(text=snapshot.get("controlStatusText") or snapshot.get("title") or "等待游戏",
                                    fg=status_color(snapshot))
        floor = snapshot.get("controlFloorText") or "楼层待核对"
        self.identity_label.configure(text=f"{map_name} · {floor}" if map_name else floor)
        self.summary_label.configure(text=" · ".join((
            "贴合已显示" if snapshot.get("overlayVisible") is True else "贴合暂不可见",
            "定点已显示" if snapshot.get("referenceVisible") is True else "定点暂不可见")))
        mode = snapshot.get("mode")
        for value, button in self.mode_buttons.items():
            selected = mode == value
            title = "噩梦" if value == "nightmare" else "困难"
            button.configure(text=f"{title} · 已选" if selected else title,
                             bg=PRIMARY if selected else BUTTON_BG, fg="white" if selected else BUTTON_TEXT,
                             state="normal" if self._has("set_game_mode") and snapshot.get("modeChangeEnabled") is not False else "disabled")
        self._sync_visibility_controls(snapshot)
        self._sync_opacity_controls(snapshot)
        self._bindings = dict(bindings if bindings is not None else snapshot.get("bindings") or {})
        candidate_name = str(snapshot.get('candidateName') or '').strip()
        self.candidate_label.configure(text=f"候选：{candidate_name}" if candidate_name else "尚无候选")
        self._paint_key(self._visibility_key, self._binding_text(self._bindings, "toggle-sheet-visibility"))
        active = snapshot.get("roundActive") is True
        button = self.quick_end_round_button if active else self.quick_start_round_button
        (self.quick_start_round_button if active else self.quick_end_round_button).pack_forget()
        if not button.winfo_manager():
            button.pack(fill="x", pady=(round(8*self._scale), 0))
        enabled = snapshot.get("roundEndEnabled" if active else "roundStartEnabled") is True
        action = "end_round" if active else "toggle_round" if snapshot.get("roundPreparing") is True else "start_round"
        button.configure(text=snapshot.get("roundActionText") or ("结束本局" if active else "开始本局"),
                         state="normal" if enabled and self._has(action) else "disabled")
        round_key = self._binding_text(self._bindings, "toggle-round") or self._binding_text(self._bindings, "end-round" if active else "start-round")
        self._paint_key(self._start_key, round_key)
        self._paint_key(self._end_key, round_key)
        for target, label in self.recognition_keys.items():
            self._paint_key(label, self._binding_text(self._bindings, target))
        candidate_available = (snapshot.get('candidateActionsEnabled') is not False
            and snapshot.get('pending') is not True and snapshot.get('locked') is not True
            and snapshot.get('mapOpen') is not False)
        for command, candidate_button in self.recognition_buttons.items():
            enabled = self._has(command)
            if command in ('previous_candidate', 'next_candidate', 'confirm_route'):
                enabled = enabled and candidate_available
            if command == 'confirm_route':
                enabled = enabled and bool(candidate_name) and snapshot.get('confirmRouteEnabled') is not False
            if command == 'capture_now':
                enabled = enabled and snapshot.get('captureEnabled') is not False and snapshot.get('pending') is not True
            candidate_button.configure(state='normal' if enabled else 'disabled')
        self.collapse_button.configure(state="normal" if not self._editing and self._has("collapse_control") else "disabled")
        self._apply_edit_bar()
        self._refresh_menu()
        self._apply_scaled_button_fonts()
        self._queue_geometry()

