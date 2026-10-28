"""Game controls only; the caller owns facts, commands and window lifecycle.

Construct inside an existing parent, then call render with the runtime snapshot.
No window, polling, focus, key registration or settings ownership lives here.
"""
from __future__ import annotations

from collections.abc import Callable, Mapping
import tkinter as tk

from desktop_rounded import RoundedButton
from desktop_lucide import LucideIcons
from star_brand import BrandImages
from opacity_controls import OpacityUpdates, opacity_percent
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
        header = tk.Frame(self.expanded_frame, bg=SURFACE)
        header.grid(row=0, column=0, sticky="ew", padx=12, pady=(8, 4))
        self.status_title = self._label(header, "等待游戏", size=10, bold=True)
        self.status_title.pack(side="left", fill="x", expand=True, padx=(0, 6))
        tools = tk.Frame(header, bg=SURFACE)
        tools.pack(side="right")
        self.quick_main_button = self._button(tools, "主页", "_open_desktop_main_ui", padx=7, pady=7)
        self.quick_main_button.pack(side="left", padx=(0, 4))
        self.collapse_button = self._button(tools, "收成球", "collapse_control", padx=7, pady=7)
        self.collapse_button.pack(side="left", padx=(0, 4))
        self.hide_button = self._button(tools, "隐藏", "hide_control", padx=7, pady=7)
        self.hide_button.configure(width=7, anchor="w")
        self.hide_button.pack(side="left")
        self._hide_key = self._keycap(self.hide_button)

        context = tk.Frame(self.expanded_frame, bg=SURFACE)
        context.grid(row=1, column=0, sticky="ew", padx=12, pady=(4, 0))
        modes = tk.Frame(context, bg=BUTTON_BG)
        modes.pack(side="left")
        self.mode_buttons = {}
        for mode, label in (("nightmare", "噩梦"), ("difficult", "困难")):
            button = self._button(modes, label, "set_game_mode", padx=10, pady=6)
            button.configure(command=lambda value=mode: self._call("set_game_mode", value))
            button.pack(side="left", padx=1)
            self.mode_buttons[mode] = button
        self.identity_label = self._label(context, "楼层待核对", color=MUTED)
        self.identity_label.pack(side="right")
        self.controls = tk.Frame(self.expanded_frame, bg=SURFACE)
        self.controls.grid(row=2, column=0, sticky="ew", padx=12, pady=(8, 0))
        self.visibility_controls = tk.Frame(self.controls, bg=SURFACE)
        self.visibility_controls.pack(fill="x")
        for column in (0, 1):
            self.visibility_controls.columnconfigure(column, weight=1, uniform="visibility")
        self.quick_sheet_visibility_button = self._button(
            self.visibility_controls, "贴合 · 未知", "toggle_sheet_visibility", padx=7, pady=8)
        self.quick_sheet_visibility_button.configure(anchor="w", width=0)
        self.quick_sheet_visibility_button.grid(row=0, column=0, sticky="ew", padx=(0, 3))
        self._visibility_key = self._keycap(self.quick_sheet_visibility_button)
        self.reference_controls = tk.Frame(self.visibility_controls, bg=SURFACE)
        self.reference_controls.grid(row=0, column=1, sticky="ew", padx=(3, 0))
        self.reference_controls.columnconfigure(0, weight=1)
        self.quick_reference_visibility_button = self._button(
            self.reference_controls, "定点 · 未知", "set_reference_sheet_visible", padx=5, pady=8)
        self.quick_reference_visibility_button.configure(command=self._toggle_reference_visibility, anchor="w", width=0)
        self.quick_reference_visibility_button.grid(row=0, column=0, sticky="ew")
        # 编辑位置复用现有 controller，入口紧邻定点地图开关。
        self.reference_adjust_button = self._button(
            self.reference_controls, "", "move_sheet", padx=5, pady=8)
        self.reference_adjust_button.configure(command=self._adjust_reference, width=0)
        self.reference_adjust_button.grid(row=0, column=1, sticky="ns", padx=(3, 0))
        self.opacity_controls = tk.Frame(self.controls, bg=SURFACE)
        self.opacity_controls.pack(fill="x", pady=(6, 0))
        self.opacity_scales, self.opacity_values = {}, {}
        for column, (surface, variable, command) in enumerate((
                ('overlay', self.opacity_var, 'change_sheet_opacity'),
                ('reference', self.reference_opacity_var, 'change_reference_opacity'))):
            self.opacity_controls.columnconfigure(column, weight=1, uniform="opacity")
            cell = tk.Frame(self.opacity_controls, bg=SURFACE)
            cell.grid(row=0, column=column, sticky="ew", padx=(0, 3) if column == 0 else (3, 0))
            heading = tk.Frame(cell, bg=SURFACE)
            heading.pack(fill="x")
            self._label(heading, "不透明度", color=MUTED).pack(side="left")
            value_label = self._label(heading, "—", bold=True)
            value_label.pack(side="right")
            self.opacity_values[surface] = value_label
            self._opacity_updates.sync(surface, variable.get())
            slider = tk.Scale(cell, from_=0, to=100, orient="horizontal", showvalue=False,
                variable=variable, command=lambda value, key=surface: self._opacity_changed(key, value),
                state="normal" if self._has(command) else "disabled", bg=SURFACE, fg=TEXT,
                troughcolor=LINE, activebackground=PRIMARY, bd=0, highlightthickness=0,
                width=self.OPACITY_WIDTH_DIP, sliderlength=self.OPACITY_SLIDER_LENGTH_DIP,
                length=1, resolution=1)
            slider.pack(fill="x")
            slider.bind("<ButtonRelease-1>", lambda _event: self._opacity_updates.commit())
            slider.bind("<KeyRelease>", self._opacity_key_released)
            self.opacity_scales[surface] = slider
        # Preserve existing runtime aliases for the overlay control.
        self.quick_opacity_scale = self.opacity_scales['overlay']
        self.quick_opacity_value = self.opacity_values['overlay']

        self._round_host = tk.Frame(self.expanded_frame, bg=SURFACE)
        self._round_host.grid(row=3, column=0, sticky="ew", padx=12, pady=(8, 10))
        self.quick_start_round_button = self._button(
            self._round_host, "开始本局", "start_round", pady=10)
        self.quick_end_round_button = self._button(
            self._round_host, "结束本局", "end_round", pady=10)
        for button, active in ((self.quick_start_round_button, False), (self.quick_end_round_button, True)):
            button.configure(command=lambda value=active: self._round_action(value),
                             bg=PRIMARY, fg="white", activebackground=PRIMARY,
                             activeforeground="white", anchor="w")
        self.quick_end_round_button.configure(bg=BUTTON_BG, fg=BUTTON_TEXT,
                                              activebackground=BUTTON_HOVER, activeforeground=BUTTON_TEXT)
        self._start_key = self._keycap(self.quick_start_round_button)
        self._end_key = self._keycap(self.quick_end_round_button)

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
            actual_visible = snapshot.get(visible_key) is True
            status = ("开" if actual_visible else "等待") if intent is True else "关" if intent is False else "未知"
            available = isinstance(intent, bool) and self._has(command)
            if command == "set_reference_sheet_visible":
                available = available and self._has("get_control_snapshot")
            button.configure(text=f"{label} · {status}",
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
                             font=(FONT, 10, "bold"), padx=padx, pady=pady, cursor="hand2")

    def _label(self, parent, text="", *, size=9, color=TEXT, bold=False):
        return tk.Label(parent, text=text, bg=SURFACE, fg=color, anchor="w",
                        font=(FONT, size, "bold" if bold else "normal"))

    def _keycap(self, button):
        label = tk.Label(button, bg=SURFACE, fg=MUTED, font=(FONT, 8),
                         bd=1, relief="solid", padx=4, pady=1)
        label._display_manual_geometry = True
        label.bind("<ButtonRelease-1>", lambda _event: button.invoke())
        return label

    @staticmethod
    def _binding_text(bindings, target):
        binding = (bindings or {}).get(target)
        text = str(binding.get("text") or "") if isinstance(binding, dict) else str(binding or "")
        return "" if text in {"未绑定", "未设置", "尚未读取", "未读取"} else text

    def _paint_key(self, label, text):
        label.configure(text=text)
        if text:
            label.place(relx=1, rely=.5, x=-round(9 * self._scale), anchor="e")
        else:
            label.place_forget()

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

    def set_display_scale(self, scale):
        self._scale = max(.5, float(scale))
        self.expanded_frame.configure(width=round(self.EXPANDED_WIDTH_DIP*self._scale),
                                      height=round(self.EXPANDED_HEIGHT_DIP*self._scale))
        self.expanded_frame.columnconfigure(0, minsize=round(self.EXPANDED_WIDTH_DIP * self._scale))
        self.collapsed_canvas.configure(width=round(64 * self._scale), height=round(64 * self._scale))
        for item, coords in self._orb_base_coords.items():
            self.collapsed_canvas.coords(item, *(round(value*self._scale) for value in coords))
        self._ball_photo = (self._brand_images.get("round", round(23*self._scale))
                            or self._icons.get("panel", round(23*self._scale), PRIMARY))
        self.collapsed_canvas.itemconfigure(self._orb_ids["panel_icon"], image=self._ball_photo)
        self.collapsed_canvas.itemconfigure(self._orb_ids["panel_arrow"], width=max(1, 1.7*self._scale))
        self.collapsed_canvas.itemconfigure(self._orb_ids["orb_body"], width=max(1, self._scale))
        self.collapsed_canvas.itemconfigure(self._orb_ids["orb_mode_text"],
                                           font=(FONT, -max(1, round(11*self._scale))))
        for button, name in ((self.quick_main_button, "home"), (self.collapse_button, "fold"), (self.hide_button, "eye-off")):
            button._lucide_image = self._icons.get(name, round(17*self._scale), BUTTON_TEXT)
            button.configure(image=button._lucide_image, compound="none")
        for button, name, color in ((self.quick_sheet_visibility_button, "eye", BUTTON_TEXT),
                                    (self.quick_reference_visibility_button, "map", BUTTON_TEXT),
                                    (self.quick_start_round_button, "play", "white"),
                                    (self.quick_end_round_button, "stop", BUTTON_TEXT)):
            button._lucide_image = self._icons.get(name, round(17*self._scale), color)
            button.configure(image=button._lucide_image, compound="left")
        self._reference_edit_icon = self._icons.get("settings", round(17*self._scale), BUTTON_TEXT)
        self.reference_adjust_button.configure(image=self._reference_edit_icon, compound="none")
        for slider in self.opacity_scales.values():
            slider.configure(width=max(1, round(self.OPACITY_WIDTH_DIP*self._scale)),
                             sliderlength=max(1, round(self.OPACITY_SLIDER_LENGTH_DIP*self._scale)))

    def _destroy_brand(self, event):
        if event.widget is self:
            self._opacity_updates.close()
            self._brand_images.close()
            self._ball_photo = None

    def render(self, snapshot, *, map_name="", mode_text="", bindings=None):
        self._set_collapsed(snapshot.get("controlCollapsed") is True)
        self._sync_reference_action(snapshot)
        self.status_title.configure(text=snapshot.get("controlStatusText") or snapshot.get("title") or "等待游戏",
                                    fg=status_color(snapshot))
        self.identity_label.configure(text=snapshot.get("controlFloorText") or "楼层待核对")
        mode = snapshot.get("mode")
        for value, button in self.mode_buttons.items():
            selected = mode == value
            title = "噩梦" if value == "nightmare" else "困难"
            button.configure(text=f"{title} · 已选" if selected else title,
                             bg=PRIMARY if selected else BUTTON_BG,
                             fg="white" if selected else BUTTON_TEXT,
                             state="normal" if self._has("set_game_mode") and snapshot.get("modeChangeEnabled") is not False else "disabled")
        self._sync_visibility_controls(snapshot)
        self._sync_opacity_controls(snapshot)
        keys = bindings if bindings is not None else snapshot.get("bindings") or {}
        hide_key = self._binding_text(keys, "toggle-panel")
        self.hide_button.configure(width=max(7, len(hide_key)+4))
        self._paint_key(self._hide_key, hide_key)
        self._paint_key(self._visibility_key, self._binding_text(keys, "toggle-sheet-visibility"))
        active = snapshot.get("roundActive") is True
        button = self.quick_end_round_button if active else self.quick_start_round_button
        (self.quick_start_round_button if active else self.quick_end_round_button).pack_forget()
        if not button.winfo_manager():
            button.pack(fill="x")
        enabled = snapshot.get("roundEndEnabled" if active else "roundStartEnabled") is True
        action = "end_round" if active else "toggle_round" if snapshot.get("roundPreparing") is True else "start_round"
        button.configure(text=snapshot.get("roundActionText") or ("结束本局" if active else "开始本局"),
                         state="normal" if enabled and self._has(action) else "disabled")
        round_key = self._binding_text(keys, "toggle-round") or self._binding_text(keys, "end-round" if active else "start-round")
        self._paint_key(self._start_key, round_key)
        self._paint_key(self._end_key, round_key)

