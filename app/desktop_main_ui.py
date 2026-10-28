"""Desktop home for the assistant; the orb remains the in-game shortcut.

This module deliberately does not import the game runtime.  The runtime owner
calls ``attach_desktop_main_ui`` after building the existing Tk controls.
"""

from __future__ import annotations

import math
import random
import tkinter as tk
from tkinter import ttk
from pathlib import Path
import sys
import time
import webbrowser
from PIL import Image, ImageDraw, ImageOps, ImageTk

from desktop_display import WindowDisplay
from star_brand import BrandImages
from game_control_view import display_summary, identity_summary
from desktop_rounded import RoundedButton, RoundedFrame, RoundedEntry, RoundedCombobox, set_rounded_scale
try:
    from desktop_lucide import LucideIcons as DesktopIcons
except ImportError:
    DesktopIcons = None
from desktop_theme import (BG, SURFACE, CARD, TINT, LINE, TEXT, MUTED, SUBTLE, GREEN, GOLD, FONT,
                           PRIMARY, PRIMARY_HOVER, ON_PRIMARY, BUTTON_BG, BUTTON_HOVER,
                           BUTTON_TEXT, INPUT_BG, IMAGE_BG, WARNING_BG, SUCCESS_BG, ERROR, ERROR_BG,
                           SCROLL_THUMB, SCROLL_HOVER, NIGHT, NIGHT_TEXT, NIGHT_MUTED, NIGHT_ACCENT,
                           NIGHT_HOVER, STAR_GOLD, SIDEBAR, SIDEBAR_HOVER, SIDEBAR_SELECTED,
                           SIDEBAR_TEXT, SIDEBAR_MUTED, SIDEBAR_LINE, SIDEBAR_SUCCESS, PANEL_RADIUS_DIP,
                           SHADOW_DIP,
                           status_color)
from chest_auto_tap import TAP_KEYS, TRIGGER_KEYS
from custom_map_editor import CustomMapEditor
from map_resource_browser import MapResourceBrowser
from map_resource_model import MapResourceModel, choose_resource, floor_label, resource_inventory
from map_resource_overview import structure_previews, load_structure_preview
from update_service import UpdateService


ACTION_NAMES = {"map": "游戏地图键", "toggle-panel": "呼出/收起游戏控件", "candidate-prev": "上一个候选",
                "candidate-next": "下一个候选", "confirm-round": "确认当前地图",
                "cycle-floor": "切换楼层", "end-round": "结束本局",
                "toggle-sheet-visibility": "显示/隐藏小抄", "start-round": "开始本局",
                "toggle-round": "开始/结束本局"}


def _button(parent: tk.Misc, text: str, command, *, primary=False) -> RoundedButton:
    return RoundedButton(
        parent, text=text, command=command,
        bg=PRIMARY if primary else BUTTON_BG, fg=ON_PRIMARY if primary else BUTTON_TEXT,
        activebackground=PRIMARY_HOVER if primary else BUTTON_HOVER,
        activeforeground=ON_PRIMARY if primary else BUTTON_TEXT, relief="flat", bd=0,
        disabledforeground=SUBTLE,
        highlightthickness=0, highlightbackground=LINE, highlightcolor=PRIMARY,
        font=(FONT, 10, "bold"), padx=16, pady=9, cursor="hand2",
    )


def _notes_body(notes: str) -> str:
    """Release notes without the heading line and share links (buttons carry those)."""
    lines = [line.rstrip() for line in str(notes or "").splitlines()]
    if lines and lines[0].strip().rstrip("：:") == "更新公告":
        lines = lines[1:]
    lines = [line for line in lines if "网盘链接" not in line and "http" not in line]
    return "\n".join(lines).strip() or "本次更新暂无公告。"


def _rgb(color: str) -> tuple[int, int, int]:
    color = color.lstrip("#")
    return tuple(int(color[index:index + 2], 16) for index in (0, 2, 4))


def _mix(a: str, b: str, amount: float) -> tuple[int, int, int]:
    return tuple(round(x + (y - x) * amount) for x, y in zip(_rgb(a), _rgb(b)))


def _star(draw, cx, cy, outer, fill, *, inner=.3):
    """Four-point sparkle, the Xingtu mark."""
    points = []
    for index in range(8):
        angle = math.pi / 4 * index - math.pi / 2
        radius = outer if index % 2 == 0 else outer * inner
        points.append((cx + math.cos(angle) * radius, cy + math.sin(angle) * radius))
    draw.polygon(points, fill=fill)


def _brand_mark(size: int) -> Image.Image:
    """Fallback app mark (folded map, route, star) when brand artwork is not installed."""
    factor = 4
    extent = max(1, size) * factor
    gradient = Image.new("RGB", (1, extent))
    for y in range(extent):
        gradient.putpixel((0, y), _mix("#2f6cf0", "#173fae", y / max(1, extent - 1)))
    image = Image.new("RGBA", (extent, extent), (0, 0, 0, 0))
    mask = Image.new("L", (extent, extent), 0)
    ImageDraw.Draw(mask).rounded_rectangle((0, 0, extent - 1, extent - 1), radius=round(extent * .26), fill=255)
    image.paste(gradient.resize((extent, extent)), mask=mask)
    draw = ImageDraw.Draw(image)
    line = "#bfe8ff"
    width = max(1, round(extent * .035))
    left, right, top, bottom = extent * .2, extent * .8, extent * .27, extent * .78
    third = (right - left) / 3
    folds = [(left, top + extent * .03), (left + third, top), (left + third * 2, top + extent * .03), (right, top)]
    draw.line(folds + [(right, bottom - extent * .03), (left + third * 2, bottom), (left + third, bottom - extent * .03),
                       (left, bottom), folds[0]], fill=line, width=width, joint="curve")
    for x, y in folds[1:3]:
        draw.line([(x, y), (x, y + bottom - top - extent * .03)], fill=_mix("#2f6cf0", "#bfe8ff", .45), width=width)
    route = [(extent * .27, extent * .63), (extent * .42, extent * .5), (extent * .55, extent * .6), (extent * .7, extent * .43)]
    draw.line(route, fill="#ffffff", width=round(width * 1.2), joint="curve")
    for x, y in route:
        r = extent * .045
        draw.ellipse((x - r, y - r, x + r, y + r), fill="#ffffff")
    _star(draw, extent * .76, extent * .25, extent * .11, STAR_GOLD, inner=.32)
    return image.resize((max(1, size), max(1, size)), Image.Resampling.LANCZOS)


def _sidebar_art(width: int, height: int) -> Image.Image:
    """Faint constellation that fades into the sidebar above the status card."""
    factor = 3
    w, h = max(1, width) * factor, max(1, height) * factor
    image = Image.new("RGB", (w, h), SIDEBAR)
    draw = ImageDraw.Draw(image)
    noise = random.Random(5)
    for _ in range(22):
        x, y = noise.uniform(0, w), noise.uniform(h * .1, h)
        r = noise.choice((1, 1, 1.4)) * factor * .6
        fade = .15 + .45 * (y / h)
        draw.ellipse((x - r, y - r, x + r, y + r), fill=_mix(SIDEBAR, "#ffffff", noise.uniform(.4, 1) * fade))
    points = [(w * .18, h * .78), (w * .36, h * .56), (w * .58, h * .66), (w * .78, h * .4)]
    draw.line(points, fill=_mix(SIDEBAR, NIGHT_ACCENT, .28), width=factor, joint="curve")
    for x, y in points[:-1]:
        r = factor * 2.2
        draw.ellipse((x - r, y - r, x + r, y + r), fill=_mix(SIDEBAR, NIGHT_ACCENT, .5))
    _star(draw, *points[-1], h * .07, _mix(SIDEBAR, STAR_GOLD, .85), inner=.3)
    return image.resize((max(1, width), max(1, height)), Image.Resampling.LANCZOS)


def _hero_art(width: int, height: int) -> Image.Image:
    """Star-chart decoration for the night hero: rings, a route and its stars."""
    factor = 3
    w, h = max(1, width) * factor, max(1, height) * factor
    image = Image.new("RGB", (w, h), NIGHT)
    draw = ImageDraw.Draw(image)
    cx, cy = w * .62, h * .62
    for ratio, amount in ((.34, .5), (.58, .36), (.84, .24), (1.1, .14)):
        radius = h * ratio
        draw.ellipse((cx - radius, cy - radius, cx + radius, cy + radius),
                     outline=_mix(NIGHT, NIGHT_ACCENT, amount * .55), width=factor)
    noise = random.Random(17)
    for _ in range(26):
        x, y = noise.uniform(0, w), noise.uniform(0, h)
        r = noise.choice((1, 1, 1.5, 2)) * factor * .6
        draw.ellipse((x - r, y - r, x + r, y + r), fill=_mix(NIGHT, "#ffffff", noise.uniform(.25, .7)))
    route = [(.16, .74), (.34, .52), (.52, .6), (.7, .34), (.86, .2)]
    points = [(x * w, y * h) for x, y in route]
    draw.line(points, fill=_mix(NIGHT, NIGHT_ACCENT, .7), width=round(factor * 1.6), joint="curve")
    for x, y in points[:-1]:
        r = factor * 3.2
        draw.ellipse((x - r, y - r, x + r, y + r), fill=NIGHT, outline=NIGHT_ACCENT, width=round(factor * 1.4))
    _star(draw, *points[-1], h * .12, STAR_GOLD, inner=.3)
    return image.resize((max(1, width), max(1, height)), Image.Resampling.LANCZOS)


def _rounded_image(image: Image.Image, radius: int, background: str) -> Image.Image:
    """Clip an image to rounded corners over a solid card background."""
    factor = 3
    mask = Image.new("L", (image.width * factor, image.height * factor), 0)
    ImageDraw.Draw(mask).rounded_rectangle((0, 0, mask.width - 1, mask.height - 1),
                                           radius=radius * factor, fill=255)
    base = Image.new("RGB", image.size, background)
    base.paste(image.convert("RGB"), mask=mask.resize(image.size, Image.Resampling.LANCZOS))
    return base


def _keyboard_mask(size: int = 128) -> Image.Image:
    """Outline keyboard glyph in the same weight as the bundled icon masks."""
    mask = Image.new("L", (size, size), 0)
    draw = ImageDraw.Draw(mask)
    stroke = round(size * .07)
    draw.rounded_rectangle((size * .08, size * .24, size * .92, size * .76), radius=size * .1,
                           outline=255, width=stroke)
    for row, y in enumerate((.39, .53)):
        for column in range(5 if row == 0 else 4):
            x = .22 + column * .14 + (.07 if row else 0)
            r = size * .035
            draw.ellipse((size * x - r, size * y - r, size * x + r, size * y + r), fill=255)
    draw.line((size * .32, size * .65, size * .68, size * .65), fill=255, width=stroke)
    return mask


def _route_mask(size: int = 128) -> Image.Image:
    """Route glyph: two waypoints joined by a bent path."""
    mask = Image.new("L", (size, size), 0)
    draw = ImageDraw.Draw(mask)
    stroke = round(size * .08)
    draw.line([(size * .26, size * .74), (size * .26, size * .5), (size * .74, size * .5),
               (size * .74, size * .26)], fill=255, width=stroke, joint="curve")
    for x, y in ((.26, .74), (.74, .26)):
        r = size * .12
        draw.ellipse((size * x - r, size * y - r, size * x + r, size * y + r), outline=255, width=stroke)
    return mask


_GLYPHS = {"keyboard": _keyboard_mask, "route": _route_mask}


def _icon_tile(icons, name: str, size: int, *, fill=BUTTON_BG, color=PRIMARY, background=SURFACE) -> Image.Image:
    """A soft rounded square with a tinted icon, used for list rows."""
    tile = _rounded_image(Image.new("RGB", (size, size), fill), round(size * .3), background)
    mask = getattr(icons, "_masks", {}).get(name)
    if mask is None and name in _GLYPHS:
        mask = _GLYPHS[name]()
    if mask is not None:
        glyph = round(size * .5)
        offset = (size - glyph) // 2
        tile.paste(color, (offset, offset, offset + glyph, offset + glyph),
                   mask.resize((glyph, glyph), Image.Resampling.LANCZOS))
    return tile


def _placeholder(icons, width: int, height: int, radius: int) -> Image.Image:
    image = Image.new("RGB", (width, height), IMAGE_BG)
    mask = getattr(icons, "_masks", {}).get("map")
    if mask is not None:
        glyph = max(8, round(min(width, height) * .26))
        x, y = (width - glyph) // 2, (height - glyph) // 2
        image.paste(SCROLL_THUMB, (x, y, x + glyph, y + glyph), mask.resize((glyph, glyph), Image.Resampling.LANCZOS))
    return _rounded_image(image, radius, SURFACE)


class ReadonlyKeycap(RoundedButton):
    """A non-editable key value with a legacy get() seam and native copy menu."""

    def __init__(self, parent, *, get_value, **kwargs):
        self._get_value = get_value
        super().__init__(parent, **kwargs)

    def get(self):
        return self._get_value()


class HotkeyEditorView(tk.Frame):
    """Present committed keys; the controller alone owns capture and publishing."""

    def __init__(self, parent, assistant, *, owner, targets,
                 confirm_inline=True, allow_clear=True, advanced_targets=()):
        super().__init__(parent, bg=parent.cget("bg"))
        self.assistant, self.owner = assistant, owner
        self.confirm_inline, self.allow_clear = confirm_inline, allow_clear
        self.entries, self.edit_buttons = {}, {}
        self._labels, self._values, self._rows = {}, {}, {}
        self._raw_bindings = {}
        self._advanced_targets = tuple(advanced_targets)
        self._advanced_open = False
        self._advanced_button = None
        self._copy_menu = tk.Menu(self, tearoff=False)
        for target in targets:
            if target in self._advanced_targets and self._advanced_button is None:
                self._advanced_button = _button(self, "更多快捷键  ›", self._toggle_advanced)
                self._advanced_button.pack(fill="x", pady=(8, 12))
            row = tk.Frame(self, bg=self.cget("bg"), height=56)
            if target not in self._advanced_targets:
                row.pack(fill="x", pady=(0, 8))
            row.pack_propagate(False)
            label = tk.Label(row, text=ACTION_NAMES.get(target, target),
                             bg=self.cget("bg"), fg=TEXT, font=(FONT, 10), anchor="w")
            label.pack(side="left", fill="x", expand=True, padx=(0, 18))
            value = tk.StringVar(self, "")
            button = ReadonlyKeycap(
                row, get_value=lambda key=target: self._raw_bindings.get(key, ""),
                text="未读取", command=lambda key=target: self._begin_capture(key),
                bg=INPUT_BG, fg=BUTTON_TEXT, activebackground=BUTTON_HOVER,
                activeforeground=BUTTON_TEXT, highlightbackground=LINE,
                highlightcolor=PRIMARY, highlightthickness=1,
                font=(FONT, 10, "bold"), width=12, padx=12, pady=7)
            button.pack(side="right", pady=8)
            button.bind("<Button-3>", lambda event, key=target: self._copy_popup(event, key))
            button.bind("<Control-c>", lambda _event, key=target: self._copy_current(key))
            button.bind("<Control-Insert>", lambda _event, key=target: self._copy_current(key))
            self.entries[target] = self.edit_buttons[target] = button
            self._labels[target], self._values[target], self._rows[target] = label, value, row

        self.draft = tk.Frame(self, bg=self.cget("bg"))
        self.draft_title = tk.Label(self.draft, bg=self.cget("bg"), fg=TEXT,
                                   font=(FONT, 10, "bold"), anchor="w", justify="left")
        self.draft_title.pack(fill="x")
        self.draft_title.bind("<Configure>", lambda event: event.widget.configure(wraplength=max(120, event.width)))
        self._actions = tk.Frame(self.draft, bg=self.cget("bg"))
        self._actions.pack(fill="x", pady=(6, 4))
        self.record_button = _button(self._actions, "重录", lambda: self._invoke("start_hotkey_recording"))
        self.confirm_button = _button(self._actions, "确认", lambda: self._invoke("confirm_hotkey_edit"), primary=True)
        self.cancel_button = _button(self._actions, "取消", lambda: self._invoke("cancel_hotkey_edit"))
        self.clear_button = _button(self._actions, "清除", lambda: self._invoke("clear_hotkey_candidate"))
        self.feedback = tk.Label(self, bg=self.cget("bg"), fg=GOLD, font=(FONT, 9),
                                 anchor="w", justify="left", wraplength=480)
        self.bind("<Map>", lambda event: self.render() if event.widget is self else None, add="+")
        self.render()

    def _toggle_advanced(self):
        self._advanced_open = not self._advanced_open
        if not self._advanced_open:
            snapshot = self.assistant.hotkey_editor_snapshot()
            if snapshot.get("editing") and snapshot.get("target") in self._advanced_targets:
                self._invoke("cancel_hotkey_edit", reason="advanced-hidden")
        self._layout_advanced()

    def _layout_advanced(self):
        if self._advanced_button is None:
            return
        self._advanced_button.configure(text="收起更多快捷键  ‹" if self._advanced_open else "更多快捷键  ›")
        previous = self._advanced_button
        for target in self._advanced_targets:
            row = self._rows[target]
            row.pack_forget()
            if self._advanced_open:
                row.pack(fill="x", pady=(0, 8), after=previous)
                previous = row

    def _copy_current(self, target):
        text = self._raw_bindings.get(target, "")
        if text and text not in {"未绑定", "未设置", "未读取", "尚未读取"}:
            self.clipboard_clear()
            self.clipboard_append(text)
        return "break"

    def _copy_popup(self, event, target):
        self._copy_menu.delete(0, "end")
        self._copy_menu.add_command(label="复制当前快捷键",
                                    command=lambda: self._copy_current(target))
        try:
            self._copy_menu.tk_popup(event.x_root, event.y_root)
        finally:
            self._copy_menu.grab_release()
        return "break"

    def _begin_capture(self, target):
        getter = getattr(self.assistant, "hotkey_editor_snapshot", None)
        snapshot = getter() if callable(getter) else {}
        if snapshot.get("editing"):
            if snapshot.get("target") != target:
                return None
            self._invoke("cancel_hotkey_edit", "restart-recording")
        result = self._invoke("begin_hotkey_edit", target, owner=self.owner)
        if isinstance(result, dict) and result.get("success") is True:
            result = self._invoke("start_hotkey_recording")
            if isinstance(result, dict) and result.get("success") is True:
                # Leave Button keyboard activation before capturing Space/Enter.
                self.owner.focus_set()
        return result

    def _invoke(self, name, *args, **kwargs):
        callback = getattr(self.assistant, name, None)
        result = callback(*args, **kwargs) if callable(callback) else None
        self.render()
        if isinstance(result, dict) and result.get("success") is False and result.get("message"):
            self.feedback.configure(text=str(result["message"]),
                                    fg=status_color({"severity": "error"}))
            self.feedback.pack(fill="x", pady=(4, 0))
        return result

    def render(self, snapshot=None):
        getter = getattr(self.assistant, "hotkey_editor_snapshot", None)
        snapshot = snapshot if snapshot is not None else getter() if callable(getter) else {}
        bindings = snapshot.get("bindings") or {}
        editing = snapshot.get("editing") is True and snapshot.get("target") in self._rows
        phase, target = snapshot.get("phase"), snapshot.get("target")
        if editing and target in self._advanced_targets and not self._advanced_open:
            self._advanced_open = True
            self._layout_advanced()
        for key, button in self.entries.items():
            binding = bindings.get(key) or {}
            raw = str(binding.get("text") or "未绑定") if key in bindings else "尚未读取"
            self._raw_bindings[key] = raw
            text = {"未绑定": "未设置", "尚未读取": "未读取"}.get(raw, raw)
            if self._values[key].get() != text:
                self._values[key].set(text)
            active = editing and target == key
            shown = "请按新键" if active and phase == "recording" else text
            button.configure(text=shown,
                             state="normal" if active or binding.get("canEdit") is True else "disabled")
            self._labels[key].configure(text=binding.get("label") or ACTION_NAMES.get(key, key))
        if editing:
            self.draft.pack(fill="x", pady=(0, 12), after=self._rows[target])
            current = snapshot.get("currentLabel") or "未设置"
            candidate = "请按新键…" if phase == "recording" else snapshot.get("candidateLabel") or "未设置"
            suffix = "" if phase == "recording" else "（确认后生效）" if self.confirm_inline else "（尚未保存）"
            self.draft_title.configure(text=f"{current} → {candidate}{suffix}")
            for button in (self.record_button, self.confirm_button, self.cancel_button, self.clear_button):
                button.pack_forget()
            if snapshot.get("canRecord") is True:
                self.record_button.pack(side="left", padx=(0, 8))
            if self.confirm_inline:
                self.confirm_button.pack(side="left", padx=(0, 8))
            self.cancel_button.pack(side="left", padx=(0, 8))
            if self.allow_clear and snapshot.get("canClear") is True:
                self.clear_button.pack(side="left")
        else:
            self.draft.pack_forget()
        self.confirm_button.configure(state="normal" if editing and snapshot.get("canConfirm") is True else "disabled")
        self.record_button.configure(state="normal" if editing and snapshot.get("canRecord") is True else "disabled")
        self.clear_button.configure(state="normal" if editing and snapshot.get("canClear") is True else "disabled")
        self.cancel_button.configure(state="normal" if editing else "disabled")
        error = str(snapshot.get("error") or "")
        if error:
            self.feedback.configure(text=error, fg=status_color({"severity": "error"}))
            self.feedback.pack(fill="x", pady=(4, 0))
        else:
            self.feedback.pack_forget()


class DesktopMainUI:
    """A normal desktop window, not an always-on-top game overlay."""

    PAGES = ("home", "preferences", "maps", "custom", "help")
    HELP_IMAGE_FILES = {"quick-start": "home.png", "shortcuts-visibility": "settings.png",
                        "resources-opacity": "resources.png", "check-feedback": "help.png"}

    def __init__(self, assistant) -> None:
        self.assistant = assistant
        self.root = assistant.root
        self.window = tk.Toplevel(self.root)
        self.window.withdraw()
        self.window.title(f"星图 {self._display_version_text()}")
        self.window.configure(bg=BG)
        self._scale = 1.0  # Widget construction metrics are DIP, applied locally.
        self.window.protocol("WM_DELETE_WINDOW", self._request_main_close)
        self.window.bind("<Escape>", lambda _event: self.hide())
        self._page = "home"
        self._nav_buttons: dict[str, tk.Button] = {}
        self._page_frames: dict[str, tk.Frame] = {}
        self._scroll_pages = {}
        self._closed = False
        self._refresh_job = None
        self._shell_layout = None
        self._close_choice_window = None
        self._icons = DesktopIcons(self.root) if DesktopIcons is not None else None
        if self._icons is not None:
            self._icons._masks.setdefault("route", _route_mask())
        self._brand_images = BrandImages(self.window)
        self._window_brand_icons = self._brand_images.window_icons(self.window)
        self._action_feedback = tk.StringVar(self.root)
        self._mode_choice = tk.StringVar(self.root, assistant.game_mode_var.get())
        self._home_preview_context = None
        self._home_layout = None
        self.display = WindowDisplay(self.window, assistant, preferred=(1000, 720),
                                     minimum=(650, 470), on_scale=self._display_changed)
        self._update_dialog = None
        self.updates = UpdateService(self._display_version_text(), self._update_state_path())
        self._build()
        self.display.start(self.root)
        self.root.after(2500, lambda: None if self._closed else self._check_updates())
        self.window.bind("<Destroy>", self._destroy, add="+")
        self.window.bind("<Unmap>", self._unmap, add="+")
        self._refresh_job = self.root.after(0, self._refresh_view)

    def _px(self, value):
        return round(value * self._scale)

    def _display_changed(self, scale):
        self._close_help_images()
        self._scale = scale
        set_rounded_scale(self.window, scale)
        self._apply_icons()
        self.map_browser.set_display_scale(scale)
        if hasattr(self, "preferences_surface"):
            self._preferences_layout = None
            self._layout_preferences()
        self._home_preview_context = None
        self._layout_shell()

    def _apply_icons(self):
        compact = getattr(self, "_compact", False)
        for page, button in self._nav_buttons.items():
            name = {"home": "home", "preferences": "settings", "maps": "map", "custom": "route", "help": "help"}[page]
            selected = page == self._page
            photo = self._icons.get(name, self._px(20), SIDEBAR_TEXT if selected else SIDEBAR_MUTED) if self._icons else None
            button._desktop_icon = photo
            # The narrow rail keeps labels below their icons at regular sizes.
            label = self._nav_labels[page]
            text = "" if compact and photo else label
            if compact and not photo and len(text) > 4:
                text = text[:3] + "\n" + text[3:]
            button.configure(image=photo or "", text=text,
                             compound="image" if compact and photo else "top", anchor="center")
        if hasattr(self, "exit_button"):
            photo = self._icons.get("exit", self._px(18), SIDEBAR_MUTED) if self._icons else None
            self.exit_button._desktop_icon = photo
            self.exit_button.configure(image=photo or "", text="" if compact and photo else "退出",
                                       compound="image" if compact and photo else "top")
        if hasattr(self, "brand_icon"):
            size = self._px(40 if not compact else 36)
            photo = self._brand_images.get("round", size)
            if photo is None:
                photo = ImageTk.PhotoImage(_brand_mark(size), master=self.window)
            self.brand_icon._desktop_icon = photo
            self.brand_icon.configure(image=photo)
        if getattr(self, "_step_badges", None):
            size = self._px(22)
            circle = Image.new("L", (size * 4, size * 4), 0)
            ImageDraw.Draw(circle).ellipse((0, 0, size * 4 - 1, size * 4 - 1), fill=255)
            image = Image.new("RGB", (size, size), SURFACE)
            image.paste(BUTTON_BG, mask=circle.resize((size, size), Image.Resampling.LANCZOS))
            self._step_badge_photo = ImageTk.PhotoImage(image, master=self.window)
            for badge in self._step_badges:
                badge.configure(image=self._step_badge_photo)
        for tile in getattr(self, "_icon_tiles", ()):
            tile._desktop_icon = ImageTk.PhotoImage(_icon_tile(self._icons, tile._xingtu_icon, self._px(36)),
                                                    master=self.window)
            tile.configure(image=tile._desktop_icon)

    def _layout_shell(self):
        width = self.window.winfo_width()
        height = self.window.winfo_height()
        if self._shell_layout is not None and (width <= 1 or height <= 1):
            return
        # Short windows need the existing icon-only mode to keep every page reachable.
        compact = width / self._scale < 800 or height / self._scale < 660
        spacious = width / self._scale >= 1200
        signature = compact, spacious, self._scale
        if signature == self._shell_layout:
            return
        self._shell_layout = signature
        self._compact = compact
        self.header.configure(height=self._px(60))
        self.workspace.pack_configure(padx=self._px(48 if spacious else 28))
        rail_width = 80 if compact else 96
        self.rail.configure(width=self._px(rail_width))
        self._update_ui_signature = None  # Re-place the sidebar badge for the new rail.
        if compact:
            self.rail_update_badge.pack_forget()
            self.brand_text.pack_forget()
            self.brand.pack_configure(padx=self._px(20), pady=(self._px(24), self._px(24)))
            self.rail_version.pack_forget()
            self.rail_status.pack_forget()
            self.rail_art.pack_forget()
            self.exit_button.pack_configure(side="bottom", fill="x")
        else:
            self.brand_text.pack(side="top", padx=0, pady=(self._px(8), 0))
            self.brand.pack_configure(padx=self._px(8), pady=(self._px(24), self._px(20)))
            self.exit_button.pack_configure(side="bottom", fill="x")
            self.rail_version.pack(side="top", padx=0, pady=(0, self._px(4)), before=self.exit_button)
            self.rail_status.pack(side="bottom", fill="x", padx=self._px(8), pady=(0, self._px(8)),
                                  before=self.rail_art if self.rail_art.winfo_manager() else None)
            self.header_caption.configure(wraplength=self._px(rail_width - 32))
            self.home_setup_summary.configure(wraplength=self._px(rail_width - 32))
            self._rail_art_photo = ImageTk.PhotoImage(
                _sidebar_art(self._px(rail_width), self._px(72)), master=self.window)
            self.rail_art.configure(image=self._rail_art_photo)
            self.rail_art.pack(side="bottom", fill="x")
        self.rail_footer.pack_configure(padx=self._px(8), pady=(0, self._px(14)))
        for button in self._nav_buttons.values():
            button.pack_configure(padx=self._px(8), pady=self._px(2))
            button.configure(padx=self._px(4), pady=self._px(8))
        self._nav_mark.configure(width=self._px(3), height=self._px(16))
        self.root.after_idle(lambda: self._place_nav_mark(animate=False))
        self._apply_icons()
        self._layout_home()

    def _label(self, parent, text="", *, size=10, color=TEXT, bold=False, **kw):
        label = tk.Label(
            parent, text=text, bg=parent.cget("bg"), fg=color,
            font=(FONT, size, "bold" if bold else "normal"), **kw,
        )
        if kw.get("wraplength"):
            label.bind("<Configure>", lambda event: label.configure(wraplength=max(100, event.width - 2)))
        return label

    def _scroll_page(self, name: str) -> tk.Frame:
        outer = tk.Frame(self.body, bg=SURFACE)
        canvas = tk.Canvas(outer, bg=SURFACE, highlightthickness=0, bd=0)
        scroll = tk.Scrollbar(
            outer, orient="vertical", command=canvas.yview,
            bg=SCROLL_THUMB, activebackground=SCROLL_HOVER,
            troughcolor=SURFACE, relief="flat", bd=0,
            highlightthickness=0, width=11,
        )
        canvas.configure(yscrollcommand=scroll.set)
        canvas.pack(side="left", fill="both", expand=True)
        content = tk.Frame(canvas, bg=SURFACE)
        item = canvas.create_window((0, 0), window=content, anchor="nw")
        def fit_content(event=None):
            if event is not None and event.widget is canvas:
                canvas.itemconfigure(item, width=max(1, event.width))
            if name == "home":
                # Fill the client viewport while retaining scrolling at small
                # sizes; requested content height does not include surplus.
                height = max(canvas.winfo_height(), content.winfo_reqheight())
                if round(float(canvas.itemcget(item, "height"))) != height:
                    canvas.itemconfigure(item, height=height)
            canvas.configure(scrollregion=canvas.bbox("all"))
            # Show the bar only when the page actually overflows.
            overflow = content.winfo_reqheight() > canvas.winfo_height() + 1
            if overflow and not scroll.winfo_manager():
                scroll.pack(side="right", fill="y", before=canvas)
            elif not overflow and scroll.winfo_manager() and canvas.winfo_height() > 1:
                scroll.pack_forget()
                canvas.yview_moveto(0)
        content.bind("<Configure>", fit_content)
        canvas.bind("<Configure>", fit_content)
        if name == "home":
            self._fit_home_canvas = fit_content
        canvas.bind("<MouseWheel>", lambda e: canvas.yview_scroll(
            -1 if e.delta > 0 else 1, "units"))
        self._page_frames[name] = outer
        self._scroll_pages[name] = (canvas, content)
        return content

    def _finish_scroll_page(self, name):
        canvas, content = self._scroll_pages[name]
        tag = f"DesktopPage-{id(self)}-{name}"
        def wheel(event):
            if event.widget.winfo_class() in {"Scale", "TCombobox", "Text"}:
                return None
            # Tk does not bubble events through parents. Respect a nested
            # scroll surface instead of consuming its wheel before its handler.
            node = event.widget
            while node is not None and node is not content:
                if getattr(node, "_desktop_scroll_owner", None) is not None:
                    return None
                node = getattr(node, "master", None)
            canvas.yview_scroll(-1 if event.delta > 0 else 1, "units")
            return "break"
        binding = self.root.bind_class(tag, "<MouseWheel>", wheel)
        def reveal(event):
            if event.widget is not self.root.focus_get() or not canvas.winfo_viewable():
                return
            widget = event.widget
            y = widget.winfo_rooty() - content.winfo_rooty()
            top = canvas.canvasy(0)
            height = canvas.winfo_height()
            if y < top:
                canvas.yview_moveto(max(0, y - 12) / max(1, content.winfo_height()))
            elif y + widget.winfo_height() > top + height:
                canvas.yview_moveto(max(0, y + widget.winfo_height() - height + 12)
                                   / max(1, content.winfo_height()))
        def bind_children(widget):
            tags = tuple(value for value in widget.bindtags() if value != tag)
            widget.bindtags((tags[0], tag, *tags[1:]))
            widget.bind("<FocusIn>", reveal, add="+")
            for child in widget.winfo_children():
                bind_children(child)
        bind_children(content)
        self._scroll_pages[name] = (canvas, content, tag, binding)

    def _section(self, parent, title, detail=None):
        section = tk.Frame(parent, bg=SURFACE)
        section.pack(fill="x", pady=(0, self._px(20)))
        self._label(section, title, size=12, bold=True).pack(anchor="w", pady=(0, 6))
        if detail:
            label = self._label(section, detail, size=9, color=MUTED, justify="left", anchor="w", wraplength=500)
            label.pack(fill="x", pady=(0, 10))
        tk.Frame(section, height=1, bg=LINE).pack(fill="x", pady=(0, 12))
        return section

    def _field(self, parent, title):
        row = tk.Frame(parent, bg=SURFACE)
        row.pack(fill="x", pady=(0, 10))
        self._label(row, title, size=9, color=MUTED, anchor="w", width=13).pack(side="left", padx=(0, 10))
        return row

    def _clickable(self, row, command):
        """Whole-row click target; hover tints the title and chevron instead of the fill."""
        labels = []

        def collect(widget):
            if isinstance(widget, tk.Label):
                labels.append((widget, widget.cget("fg")))
            for child in widget.winfo_children():
                collect(child)
        collect(row)
        title = next((label for label, _fg in labels
                      if "bold" in str(label.cget("font")) and label.cget("text") not in {"›", "⌄"}), None)

        def paint(hover):
            for label, color in labels:
                if label.cget("text") in {"›", "⌄"}:
                    label.configure(fg=PRIMARY if hover else color)
                elif label is title:
                    label.configure(fg=BUTTON_TEXT if hover else color)

        def leave(_event):
            widget = row.winfo_containing(*row.winfo_pointerxy())
            if widget is None or not str(widget).startswith(str(row)):
                paint(False)

        def bind(widget):
            widget.configure(cursor="hand2")
            widget.bind("<Button-1>", lambda _e: command(), add="+")
            widget.bind("<Enter>", lambda _e: paint(True), add="+")
            widget.bind("<Leave>", leave, add="+")
            for child in widget.winfo_children():
                bind(child)
        bind(row)

    def _card(self, parent, *, padx=22, pady=18, bg=SURFACE, line=True) -> RoundedFrame:
        return RoundedFrame(parent, bg=bg, radius=PANEL_RADIUS_DIP, padx=padx, pady=pady,
                            highlightthickness=1 if line else 0, highlightbackground=LINE,
                            shadow=SHADOW_DIP)

    def _ghost(self, parent, text, command, *, color=BUTTON_TEXT, size=10):
        bg = parent.cget("bg")
        return RoundedButton(parent, text=text, command=command, bg=bg, fg=color,
                             activebackground=TINT if bg == SURFACE else BUTTON_BG, activeforeground=color,
                             disabledbackground=bg, highlightthickness=0, font=(FONT, size, "bold"),
                             padx=8, pady=4, cursor="hand2")

    def _build(self) -> None:
        container = tk.Frame(self.window, bg=BG)
        container.pack(fill="both", expand=True)
        rail = tk.Frame(container, bg=SIDEBAR, width=96)
        self.rail = rail
        rail._display_manual_geometry = True
        rail.pack(side="left", fill="y")
        rail.pack_propagate(False)
        brand = tk.Frame(rail, bg=SIDEBAR)
        self.brand = brand
        brand._display_manual_geometry = True
        brand.pack(fill="x", padx=8, pady=(24, 20))
        self.brand_icon = tk.Label(brand, bg=SIDEBAR, bd=0)
        self.brand_icon.pack(side="top")
        self.brand_text = tk.Frame(brand, bg=SIDEBAR)
        self.brand_text.pack(side="top", pady=(8, 0))
        self._label(self.brand_text, "星图", size=14, bold=True, color=SIDEBAR_TEXT).pack(anchor="center")
        self._label(self.brand_text, "地图与路线助手", size=8, color=SIDEBAR_MUTED).pack(anchor="center")
        self._nav_labels = {}
        for key, label in (("home", "主页面"), ("maps", "地图资源"), ("custom", "自定义地图"),
                           ("preferences", "设置"), ("help", "帮助")):
            button = RoundedButton(
                rail, text=label, command=lambda page=key: self.show_page(page),
                bg=SIDEBAR, fg=SIDEBAR_MUTED, activebackground=SIDEBAR_HOVER,
                activeforeground=SIDEBAR_TEXT, highlightthickness=0, highlightbackground=SIDEBAR,
                highlightcolor=NIGHT_ACCENT, font=(FONT, 10, "bold"), padx=4, pady=8,
                anchor="center", cursor="hand2")
            button.pack(fill="x", padx=8, pady=2)
            button._display_manual_geometry = True
            self._nav_buttons[key] = button
            self._nav_labels[key] = label
        # One star-gold bar marks the current page and glides between items.
        self._nav_mark = tk.Frame(rail, bg=STAR_GOLD, width=3, height=16)
        self._nav_mark._display_manual_geometry = True
        self._nav_mark_y = None
        self._nav_mark_job = None
        self._page_motion_job = None

        footer = tk.Frame(rail, bg=SIDEBAR)
        self.rail_footer = footer
        footer.pack(side="bottom", fill="x", padx=8, pady=(0, 14))
        self.rail_version = self._label(footer, f"版本 {self._display_version_text()}", size=8,
                                        color=SIDEBAR_MUTED, anchor="center")
        self.rail_version.pack(side="top", pady=(0, 4))
        self.rail_update_badge = tk.Label(footer, text="新版本", bg=STAR_GOLD, fg=NIGHT, bd=0,
                                          font=(FONT, 7, "bold"), padx=5, pady=1, cursor="hand2")
        for widget in (self.rail_version, self.rail_update_badge):
            widget.configure(cursor="hand2")
            widget.bind("<Button-1>", lambda _e: self._open_update_card(), add="+")
        self.exit_button = RoundedButton(
            footer, text="退出", command=lambda: self.assistant.request_close(owner=self.window),
            bg=SIDEBAR, fg=SIDEBAR_MUTED, activebackground=SIDEBAR_HOVER, activeforeground=SIDEBAR_TEXT,
            highlightthickness=0, highlightbackground=SIDEBAR, highlightcolor=NIGHT_ACCENT,
            font=(FONT, 9, "bold"), padx=10, pady=6, anchor="center", cursor="hand2")
        self.exit_button._display_manual_geometry = True
        self.exit_button.pack(side="bottom", fill="x")

        # Live game status stays visible on every page, not only on home.
        status = RoundedFrame(rail, bg=SIDEBAR_HOVER, radius=12, padx=8, pady=8, highlightthickness=0)
        self.rail_status = status
        status._display_manual_geometry = True
        status.pack(side="bottom", fill="x", padx=8, pady=(0, 8))
        self.header_caption = tk.Label(status, text="○  等待连接游戏", bg=SIDEBAR_HOVER, fg=SIDEBAR_MUTED,
                                       font=(FONT, 9, "bold"), anchor="center", justify="center", bd=0, wraplength=64)
        self.header_caption.pack(fill="x")
        self.home_setup_summary = tk.Label(status, bg=SIDEBAR_HOVER, fg=SIDEBAR_MUTED, font=(FONT, 8),
                                           anchor="center", justify="center", bd=0, wraplength=64)
        self.home_setup_summary.pack(fill="x", pady=(4, 0))
        self.rail_art = tk.Label(rail, bg=SIDEBAR, bd=0)
        self.rail_art.pack(side="bottom", fill="x")
        self.workspace = tk.Frame(container, bg=BG)
        self.workspace.pack(side="left", fill="both", expand=True, padx=28, pady=(14, 14))
        header = tk.Frame(self.workspace, bg=BG, height=60)
        self.header = header
        header._display_manual_geometry = True
        header.pack(fill="x")
        header.pack_propagate(False)
        self.header_title = self._label(header, "主页面", size=17, bold=True)
        self.header_title.pack(side="left", padx=(SHADOW_DIP, 0))
        self.header_greeting = self._label(header, "", size=9, color=SUBTLE, anchor="w")
        self.header_greeting.pack(side="left", padx=(12, 0), pady=(4, 0))
        self.drag_handles = (header, self.header_title, self.header_greeting, self.brand, self.brand_icon)
        self.hide_button = self._ghost(header, "最小化到任务栏", self.hide, color=MUTED)
        self.hide_button.configure(activebackground=LINE)
        self.hide_button.pack(side="right", padx=(0, SHADOW_DIP))
        self.body = tk.Frame(self.workspace, bg=BG)
        self.body._display_manual_geometry = True  # _rise_page owns its top padding.
        self.body.pack(fill="both", expand=True)
        self._build_home()
        self._build_preferences()
        self._build_maps()
        self._build_help()
        self.custom_editor = CustomMapEditor(self.body, self)
        self._page_frames["custom"] = self.custom_editor
        self.show_page("home")
        self.window.bind("<Configure>", lambda event: self._layout_shell()
                         if event.widget is self.window else None, add="+")

    def _heading(self, parent, title: str, subtitle: str, *, inset=SHADOW_DIP) -> None:
        # One explicit passive title row; its top/right whitespace is draggable.
        # Register exact widgets only, never all descendants or page content.
        row = tk.Frame(parent, bg=parent.cget("bg"))
        row.pack(fill="x", padx=inset)
        label = self._label(row, title, size=22, bold=True)
        label.pack(side="left", pady=(18, 3 if subtitle else 18))
        self.drag_handles += (row, label)
        if subtitle:
            self._label(parent, subtitle, size=9, color=MUTED, justify="left", anchor="w", wraplength=500).pack(
                fill="x", padx=inset, pady=(0, 16))

    def _build_home(self) -> None:
        content = self._scroll_page("home")
        content.configure(bg=BG)
        self._scroll_pages["home"][0].configure(bg=BG)
        self.home_grid = tk.Frame(content, bg=BG)
        self.home_grid.pack(fill="both", expand=True, pady=(0, 8))
        self.home_main = tk.Frame(self.home_grid, bg=BG)
        self.home_side = tk.Frame(self.home_grid, bg=BG)

        # Update reminder banner; shown by _sync_update_ui only while a newer version waits.
        self.home_update = RoundedFrame(self.home_main, bg=BUTTON_BG, radius=14, padx=16, pady=10, highlightthickness=0)
        self._label(self.home_update, "✦", size=10, bold=True, color=STAR_GOLD).pack(side="left")
        self.home_update_text = self._label(self.home_update, size=10, bold=True, color=BUTTON_TEXT, anchor="w")
        self.home_update_text.pack(side="left", padx=(8, 0))
        self._label(self.home_update, "查看更新公告  ›", size=9, bold=True, color=BUTTON_TEXT).pack(side="right")
        self._clickable(self.home_update, self.show_update_dialog)

        # Night hero: the one place that carries the brand mood.
        self.hero = self._card(self.home_main, padx=28, pady=24, bg=NIGHT, line=False)
        self.hero.pack(fill="x", pady=(0, 2))
        self.hero.columnconfigure(0, weight=1)
        hero_text = tk.Frame(self.hero, bg=NIGHT)
        hero_text.grid(row=0, column=0, sticky="nsew")
        self.hero_art = tk.Label(self.hero, bg=NIGHT, bd=0)
        self.hero_art.grid(row=0, column=1, sticky="ne", padx=(12, 0))
        kicker = tk.Frame(hero_text, bg=NIGHT)
        kicker.pack(fill="x")
        self._label(kicker, "✦", size=9, bold=True, color=STAR_GOLD).pack(side="left")
        self._label(kicker, "YOUR ROUTE, YOUR WAY", size=8, bold=True, color=NIGHT_ACCENT).pack(
            side="left", padx=(6, 0))
        self.hero_title = self._label(hero_text, "地图与路线，\n随手查看。", size=24, bold=True,
                                      color=NIGHT_TEXT, anchor="w", justify="left")
        self.hero_title.pack(fill="x", pady=(10, 6))
        self._label(hero_text, "打开游戏控件，回到游戏按地图键查看小抄。", size=10,
                    color=NIGHT_MUTED, anchor="w", justify="left", wraplength=380).pack(fill="x")
        # Actions span the art column so buttons never compete with the decoration.
        hero_footer = tk.Frame(self.hero, bg=NIGHT)
        hero_footer.grid(row=1, column=0, columnspan=2, sticky="ew")
        hero_actions = tk.Frame(hero_footer, bg=NIGHT)
        hero_actions.pack(fill="x", pady=(20, 0))
        self.panel_button = RoundedButton(
            hero_actions, text="打开游戏控件", command=self._open_control, bg=SURFACE, fg=NIGHT,
            activebackground=BUTTON_BG, activeforeground=NIGHT, highlightthickness=0,
            highlightbackground=SURFACE, highlightcolor=NIGHT_ACCENT, font=(FONT, 11, "bold"),
            padx=20, pady=11, cursor="hand2")
        if self._icons:
            self.panel_button._desktop_icon = self._icons.get("panel", self._px(18), NIGHT)
            self.panel_button.configure(image=self.panel_button._desktop_icon, compound="left")
        self.panel_button.pack(side="left")
        self.return_game_button = RoundedButton(
            hero_actions, text="切回游戏", command=self._return_to_game, bg=NIGHT, fg=NIGHT_TEXT,
            activebackground=NIGHT_HOVER, activeforeground=NIGHT_TEXT, disabledbackground=NIGHT,
            disabledforeground="#6f7fa6", highlightthickness=1, highlightbackground="#3a4f86",
            highlightcolor=NIGHT_ACCENT, font=(FONT, 10, "bold"), padx=16, pady=10, cursor="hand2")
        self.return_game_button.pack(side="left", padx=(10, 0))
        self.home_feedback = self._label(hero_footer, textvariable=self._action_feedback, size=9, color=STAR_GOLD,
                                         anchor="w", justify="left", wraplength=400)

        # This round: live state, the round action and recovery hints.
        self.home_state = self._card(self.home_main)
        self.home_state.pack(fill="x", pady=(0, 2))
        state_head = tk.Frame(self.home_state, bg=SURFACE)
        state_head.pack(fill="x")
        self._label(state_head, "本局", size=13, bold=True).pack(side="left")
        self.home_details_button = self._ghost(state_head, "状态说明", self._toggle_home_details, color=MUTED, size=9)
        self.home_details_button.pack(side="right")
        # Three equal stat tiles; spacer columns keep the gaps out of the uniform widths.
        context = tk.Frame(self.home_state, bg=SURFACE)
        self.home_context = context
        context.pack(fill="x", pady=(14, 0))
        values = []
        for index, title in enumerate(("状态", "地图", "楼层")):
            context.columnconfigure(index * 2, weight=1, uniform="stat")
            if index:
                context.columnconfigure(index * 2 - 1, minsize=10)
            tile = RoundedFrame(context, bg=INPUT_BG, radius=12, padx=14, pady=10, highlightthickness=0)
            tile.grid(row=0, column=index * 2, sticky="nsew")
            self._label(tile, title, size=8, color=SUBTLE, anchor="w").pack(fill="x")
            value = tk.Frame(tile, bg=INPUT_BG)
            value.pack(fill="x", pady=(3, 0))
            values.append(value)
        self.home_dot = tk.Label(values[0], text="●", bg=INPUT_BG, fg=MUTED, font=(FONT, 9), bd=0)
        self.home_dot.pack(side="left", padx=(0, 5))
        self.home_session = self._label(values[0], size=11, bold=True, anchor="w")
        self.home_session.pack(side="left")
        self.home_round = self._label(values[1], size=11, bold=True, anchor="w")
        self.home_round.pack(side="left")
        self.home_display = self._label(values[2], size=11, bold=True, anchor="w")
        self.home_display.pack(side="left")
        self.home_actions = tk.Frame(self.home_state, bg=SURFACE)
        self.home_actions.pack(fill="x", pady=(16, 0))
        self.start_round_button = _button(self.home_actions, "开始本局", self._start_round, primary=True)
        self.end_round_button = _button(self.home_actions, "结束本局", self._end_round)
        self.visibility_button = _button(self.home_actions, "小抄显隐", self._toggle_visibility)
        self.visibility_button.pack(side="left", padx=(8, 0))
        self.recognition_button = _button(self.home_actions, "查看识别与路线", self._open_recognition)
        self.recognition_button.pack(side="left", padx=(8, 0))
        self._home_details_open = False
        self.home_details = tk.Frame(self.home_state, bg=SURFACE)
        self.home_status = self._label(self.home_details, "等待运行状态", size=10, color=MUTED, anchor="w", wraplength=500)
        self.home_status.pack(fill="x")
        self.home_message = self._label(self.home_state, size=10, color=MUTED, anchor="w", justify="left", wraplength=500)
        self.recognition_hint = self._label(self.home_state, size=9, color=GOLD, anchor="w", justify="left", wraplength=500)

        # Resources: a quick look at what the current route will show.
        self.home_resources = self._card(self.home_main)
        self.home_resources.pack(fill="x", pady=(0, 2))
        resource_head = tk.Frame(self.home_resources, bg=SURFACE)
        resource_head.pack(fill="x", pady=(0, 12))
        self._label(resource_head, "地图资源", size=13, bold=True).pack(side="left")
        all_maps = self._ghost(resource_head, "查看全部  ›", lambda: self.show_page("maps"), size=9)
        all_maps.pack(side="right")
        self.home_resource_note = self._label(resource_head, size=9, color=SUBTLE, anchor="e", wraplength=260)
        self.home_resource_note.pack(side="right", fill="x", expand=True, padx=10)
        self.home_gallery = gallery = tk.Frame(self.home_resources, bg=SURFACE)
        gallery.pack(fill="x")
        self.home_empty = tk.Frame(self.home_resources, bg=SURFACE)
        empty_tile = tk.Label(self.home_empty, bg=SURFACE, bd=0)
        empty_tile._xingtu_icon = "map"
        empty_tile.pack(side="left", padx=(0, 14))
        # Pack the action before the text so narrow cards wrap the text, not the button.
        self._ghost(self.home_empty, "去地图资源  ›", lambda: self.show_page("maps"), size=9).pack(side="right")
        empty_words = tk.Frame(self.home_empty, bg=SURFACE)
        empty_words.pack(side="left", fill="x", expand=True)
        self._label(empty_words, "还没有可预览的地图图片", size=10, bold=True, anchor="w").pack(fill="x")
        self._label(empty_words, "安装作者路线或结构资源后，这里会显示地图预览。", size=9, color=SUBTLE,
                    anchor="w", justify="left", wraplength=200).pack(fill="x", pady=(3, 0))
        self._icon_tiles = [empty_tile]
        self.home_thumbnails = []
        for index in range(3):
            # Fixed-size tiles: sizes come from _refresh_home_resources, gaps from padx.
            tile = tk.Frame(gallery, bg=SURFACE)
            tile.grid(row=0, column=index, sticky="nw", padx=(0 if index == 0 else 12, 0))
            image = tk.Label(tile, bg=SURFACE, bd=0, padx=0, pady=0, highlightthickness=0, anchor="w")
            image.pack(fill="x")
            caption = self._label(tile, "资源待读取", size=9, color=MUTED, anchor="w", justify="left", wraplength=120)
            caption.pack(fill="x", pady=(8, 0))
            self.home_thumbnails.append((image, caption))

        # Side column: shortcuts and help.
        self.home_keys = self._card(self.home_side)
        self.home_keys.pack(fill="x", pady=(0, 2))
        self._label(self.home_keys, "快捷键", size=13, bold=True).pack(anchor="w")
        self._label(self.home_keys, "在游戏里随时可用", size=9, color=SUBTLE).pack(anchor="w", pady=(2, 12))
        self.home_keycaps = {}
        for target, title in (("map", "游戏地图"), ("toggle-panel", "游戏控件"),
                              ("toggle-round", "开始 / 结束"), ("toggle-sheet-visibility", "小抄显隐")):
            row = tk.Frame(self.home_keys, bg=SURFACE)
            row.pack(fill="x", pady=(0, 8))
            self._label(row, title, size=10, color=MUTED).pack(side="left")
            key = RoundedButton(row, text="未读取", command=lambda: self.show_page("preferences"),
                                bg=INPUT_BG, fg=TEXT, activebackground=BUTTON_BG, activeforeground=BUTTON_TEXT,
                                highlightthickness=1, highlightbackground=LINE, font=(FONT, 10, "bold"),
                                padx=10, pady=4, radius=8, takefocus=0, cursor="hand2")
            key.pack(side="right")
            self.home_keycaps[target] = key
        self._ghost(self.home_keys, "修改快捷键  ›", lambda: self.show_page("preferences"), size=9).pack(
            anchor="w", pady=(4, 0))

        self.home_support = self._card(self.home_side, pady=8)
        self.home_support.pack(fill="x", pady=(0, 2))
        for index, (title, note, icon) in enumerate((("使用帮助", "地图键、显影与常见问题", "help"),
                                                   ("排查与反馈", "查看运行信息，导出排查文件", "note"))):
            if index:
                tk.Frame(self.home_support, bg=LINE, height=1).pack(fill="x")
            row = tk.Frame(self.home_support, bg=SURFACE, cursor="hand2")
            row.pack(fill="x", pady=12)
            tile = tk.Label(row, bg=SURFACE, bd=0)
            tile._xingtu_icon = icon
            tile.pack(side="left", padx=(0, 12))
            words = tk.Frame(row, bg=SURFACE)
            words.pack(side="left", fill="x", expand=True)
            self._label(words, title, size=10, bold=True, anchor="w").pack(fill="x")
            self._label(words, note, size=9, color=SUBTLE, anchor="w").pack(fill="x", pady=(2, 0))
            self._label(row, "›", size=14, color=SUBTLE).pack(side="right")
            self._clickable(row, lambda: self.show_page("help"))
            self._icon_tiles.append(tile)
        self.home_grid.bind("<Configure>", lambda _e: self._layout_home(), add="+")
        self._finish_scroll_page("home")

    def _layout_home(self):
        if not hasattr(self, "home_grid"):
            return
        width = self.home_grid.winfo_width() / self._scale
        wide, spacious = width >= 720, width >= 1200
        side = max(250, round(width * .261)) if wide else 0
        gap = round(width * .034) if wide else 0
        main = width - side - gap if wide else width
        # The hero art follows the main column, so bucket its width into the signature.
        signature = wide, spacious, self._scale, side, gap, round(main / 40)
        if signature == self._home_layout:
            return
        self._home_layout = signature
        self.home_grid.columnconfigure(0, weight=1)
        self.home_grid.columnconfigure(1, weight=0, minsize=self._px(side))
        for frame in (self.home_main, self.home_side):
            frame.grid_forget()
        if wide:
            self.home_main.grid(row=0, column=0, sticky="new", padx=(0, self._px(gap)))
            self.home_side.grid(row=0, column=1, sticky="new")
        else:
            self.home_main.grid(row=0, column=0, sticky="ew")
            self.home_side.grid(row=1, column=0, sticky="ew")
        self.hero_title.configure(font=(FONT, -self._px(34 if spacious else 30 if main >= 560 else 26), "bold"))
        art = (round(min(280, max(170, main * .36))), 0) if main >= 480 else None
        if art:
            art = art[0], round(art[0] * .6)
            self._hero_art_photo = ImageTk.PhotoImage(_hero_art(self._px(art[0]), self._px(art[1])), master=self.window)
            self.hero_art.configure(image=self._hero_art_photo)
            self.hero_art.grid()
        else:
            self.hero_art.grid_remove()
        self._home_preview_context = None

    def _refresh_home_resources(self):
        pack = self.assistant.pack
        catalog = getattr(self.assistant, "author_route_catalog", None)
        profile = getattr(self.assistant, "route_profile_id", None)
        # Three equal tiles across the card; snap to 8 DIP so resizing doesn't thrash.
        # Measured from the card (sized by the grid), not the gallery, whose
        # request includes the previous tiles.
        gallery = self.home_resources.winfo_width() / self._scale - 2 * (SHADOW_DIP + 22 + 1)
        tile = max(88, min(300, int((gallery - 24) / 3) // 4 * 4)) if gallery > 0 else 150
        preview_size = (self._px(tile), self._px(round(tile * .62)))
        radius = self._px(12)
        context = id(pack), id(catalog), profile, self._scale, tile
        if context == self._home_preview_context:
            return
        self._home_preview_context = context
        model = MapResourceModel(pack, catalog, self.assistant._mode_route_options())
        profile = profile or model.default_profile
        self.home_resource_note.configure(text=f"{model.mode_label} · {model.profiles.get(profile, '当前来源')}")
        items = model.maps(profile)
        inventory = resource_inventory(items)
        rows = []
        for item in items:
            chosen = choose_resource(item, None, model.default_floor)
            if chosen is None or not chosen[2].is_file():
                # Home is a browsing preview. Try another registered image and
                # keep its actual floor label; runtime/gallery selection is unchanged.
                floors = tuple(dict.fromkeys((model.default_floor, *item.floors)))
                chosen = next(((variant, floor, Path(variant.layers[floor]))
                               for floor in floors for variant in item.variants
                               if floor in variant.layers and Path(variant.layers[floor]).is_file()), None)
            if chosen is not None and chosen[2].is_file():
                rows.append((item, chosen))
            if len(rows) == 3:
                break
        structure = () if rows else structure_previews(pack)
        if rows or structure:
            self.home_empty.pack_forget()
            self.home_gallery.pack(fill="x")
        else:
            self.home_gallery.pack_forget()
            self.home_empty.pack(fill="x", pady=(2, 4))
        if structure:
            reason = "作者路线未安装" if inventory["availableLayerImages"] == 0 else "当前作者预览不可用"
            self.home_resource_note.configure(text=f"{model.mode_label} · 结构底图 · {reason}")
        elif inventory["availableLayerImages"] == 0:
            self.home_resource_note.configure(text=f"{model.mode_label} · 作者路线未安装")
        elif inventory["availableLayerImages"] < inventory["layerImageCount"]:
            self.home_resource_note.configure(text=f"{model.mode_label} · {model.profiles.get(profile, '当前来源')} · 部分图片缺失")
        empty = _placeholder(self._icons, *preview_size, radius)

        def show(label, image):
            label._home_photo = ImageTk.PhotoImage(image, master=self.window)
            label.configure(image=label._home_photo, text="")

        def framed(image):
            # Center the picture on a soft tile so every thumbnail shares one shape.
            tile_image = Image.new("RGB", preview_size, IMAGE_BG)
            fitted = ImageOps.contain(image.convert("RGBA"), preview_size, Image.Resampling.LANCZOS)
            tile_image.paste(fitted, ((preview_size[0] - fitted.width) // 2,
                                      (preview_size[1] - fitted.height) // 2), fitted)
            return _rounded_image(tile_image, radius, SURFACE)

        for index, (label, caption) in enumerate(self.home_thumbnails):
            show(label, empty)
            caption.configure(text="暂未收录", fg=SUBTLE, wraplength=max(80, preview_size[0]))
            if structure and index < len(structure):
                asset = structure[index]
                caption.configure(text=f"{asset.name}\n{floor_label(asset.floor)} · 结构图", fg=MUTED)
                try:
                    show(label, framed(load_structure_preview(asset, preview_size)))
                except (OSError, ValueError, KeyError):
                    caption.configure(text=f"{asset.name}\n结构图不可用")
                continue
            if index >= len(rows):
                continue
            item, chosen = rows[index]
            caption.configure(text=item.name, fg=TEXT)
            if chosen is None:
                continue
            _variant, floor, path = chosen
            caption.configure(text=f"{item.name}\n{floor_label(floor, source=item.structure)}")
            try:
                with Image.open(path) as original:
                    show(label, framed(original))
            except (OSError, ValueError):
                caption.configure(text=f"{item.name}\n图片不可用")

    def _open_control(self):
        callback = getattr(self.assistant, "set_control_visible", None)
        result = callback(True, source="mouse") if callable(callback) else None
        if isinstance(result, dict) and result.get("success") is False:
            self._action_feedback.set(str(result.get("message") or "暂时无法打开游戏控件。"))
        self._sync_view()

    def _card_heading(self, parent, title, subtitle, icon, *, chevron=False):
        """Icon tile, title and subtitle; an optional chevron marks a collapsible card."""
        row = tk.Frame(parent, bg=parent.cget("bg"))
        tile = tk.Label(row, bg=row.cget("bg"), bd=0)
        tile._xingtu_icon = icon
        tile.pack(side="left", padx=(0, 12))
        self._icon_tiles.append(tile)
        marker = None
        if chevron:
            marker = self._label(row, "›", size=15, color=SUBTLE)
            marker.pack(side="right", padx=(8, 0))
        words = tk.Frame(row, bg=row.cget("bg"))
        words.pack(side="left", fill="x", expand=True)
        self._label(words, title, size=12, bold=True, anchor="w").pack(fill="x")
        if subtitle:
            self._label(words, subtitle, size=9, color=SUBTLE, anchor="w", justify="left",
                        wraplength=360).pack(fill="x", pady=(2, 0))
        return row, marker

    def _segmented(self, parent, variable, options, command):
        """Single-choice track; the chosen segment is a raised white block."""
        track = RoundedFrame(parent, bg=IMAGE_BG, radius=12, padx=3, pady=3, highlightthickness=0)
        buttons = {}

        def choose(value):
            if variable.get() != value:
                variable.set(value)
                command()

        def paint(*_args):
            current = variable.get()
            for value, button in buttons.items():
                selected = value == current
                button.configure(bg=SURFACE if selected else IMAGE_BG,
                                 activebackground=SURFACE if selected else LINE,
                                 fg=BUTTON_TEXT if selected else MUTED,
                                 activeforeground=BUTTON_TEXT if selected else TEXT)

        for value in options:
            button = RoundedButton(track, text=value, command=lambda value=value: choose(value),
                                   bg=IMAGE_BG, fg=MUTED, highlightthickness=0, radius=9,
                                   font=(FONT, 10, "bold"), padx=18, pady=6, cursor="hand2")
            button.pack(side="left")
            buttons[value] = button
        self._preference_traces.append((variable, variable.trace_add("write", paint)))
        paint()
        track.buttons = buttons
        return track

    def _setting_row(self, parent, title, note=None):
        row = tk.Frame(parent, bg=parent.cget("bg"))
        row.pack(fill="x", pady=(0, 14))
        words = tk.Frame(row, bg=row.cget("bg"))
        words.pack(side="left", fill="x", expand=True)
        self._label(words, title, size=10, anchor="w").pack(fill="x")
        if note:
            self._label(words, note, size=8, color=SUBTLE, anchor="w", justify="left",
                        wraplength=260).pack(fill="x", pady=(2, 0))
        return row

    def _build_preferences(self) -> None:
        self._preference_traces = []
        self._preferences_layout = None
        content = self._scroll_page("preferences")
        content.configure(bg=BG)
        self._scroll_pages["preferences"][0].configure(bg=BG)
        self._heading(content, "设置", "快捷键、小抄显示，以及游戏模式与作者路线。")
        self.preferences_surface = tk.Frame(content, bg=BG)
        self.preferences_surface.pack(fill="x")
        self.preferences_keys = self._card(self.preferences_surface, padx=24, pady=22)
        self.preferences_display = self._card(self.preferences_surface, padx=24, pady=22)
        heading, _ = self._card_heading(self.preferences_keys, "快捷键", "点击键帽录入新组合，核对后确认。", "keyboard")
        heading.pack(fill="x", pady=(0, 16))
        heading, _ = self._card_heading(self.preferences_display, "小抄显示", "显影、参考小抄、透明度与大小。", "eye")
        heading.pack(fill="x", pady=(0, 16))
        common = ("map", "toggle-panel", "toggle-round", "toggle-sheet-visibility")
        advanced = tuple(target for target in ACTION_NAMES if target not in common)
        self.hotkey_editor = HotkeyEditorView(self.preferences_keys, self.assistant, owner=self.window,
                                             targets=common + advanced, advanced_targets=advanced)
        self.hotkey_editor.pack(fill="x")
        self.hotkey_entry = self.hotkey_editor.entries["map"]
        self.capture_button = self.hotkey_editor.edit_buttons["map"]
        self.shortcut_entries = {name: entry for name, entry in self.hotkey_editor.entries.items() if name != "map"}
        self.save_map_key_button = self.hotkey_editor.confirm_button
        self.save_shortcuts_button = self.hotkey_editor.confirm_button

        display = self.preferences_display
        visibility = self._setting_row(display, "总显影", "同时控制参考小抄和贴合显示。")
        self.preferences_visibility_button = _button(visibility, "未知", self._toggle_visibility)
        self.preferences_visibility_button.configure(padx=18)
        self.preferences_visibility_button.pack(side="right")
        tk.Frame(display, bg=LINE, height=1).pack(fill="x", pady=(0, 14))
        reference = tk.Frame(display, bg=SURFACE)
        reference.pack(fill="x", pady=(0, 14))
        reference_heading = tk.Frame(reference, bg=SURFACE)
        reference_heading.pack(fill="x")
        self._label(reference_heading, "参考小抄", size=10).pack(side="left")
        self.reference_status = self._label(reference_heading, size=9, color=SUBTLE)
        self.reference_status.pack(side="right")
        reference_actions = tk.Frame(reference, bg=SURFACE)
        reference_actions.pack(fill="x", pady=(8, 0))
        self.reference_visibility_button = _button(reference_actions, "显示", self._toggle_reference_visibility)
        self.reference_visibility_button.pack(side="left")
        self.reference_adjust_button = _button(reference_actions, "调整位置", self._adjust_reference_position)
        self.reference_adjust_button.pack(side="left", padx=(8, 0))
        self._label(reference, "从首页切回游戏后，在悬浮面板点“调整小抄”；拖动后点“完成调整”。", size=8,
                    color=SUBTLE, anchor="w", justify="left", wraplength=400).pack(fill="x", pady=(8, 0))
        self.reference_feedback = self._label(reference, size=9, color=GOLD, anchor="w", justify="left", wraplength=400)
        self._reference_controls_signature = None
        tk.Frame(display, bg=LINE, height=1).pack(fill="x", pady=(0, 14))
        self.display_scales = {}
        for title, var, low, high, callback in (
            ("不透明度", self.assistant.opacity_var, 0, 100, self.assistant.change_sheet_opacity),
            ("大小", self.assistant.sheet_scale_var, 50, 150, self.assistant.change_sheet_scale),
        ):
            block = tk.Frame(display, bg=SURFACE)
            block.pack(fill="x", pady=(0, 12))
            row = tk.Frame(block, bg=SURFACE)
            row.pack(fill="x", pady=(0, 6))
            self._label(row, "小抄大小" if title == "大小" else title, size=10).pack(side="left")
            value = RoundedButton(row, text="", bg=BUTTON_BG, fg=BUTTON_TEXT, activebackground=BUTTON_BG,
                                  activeforeground=BUTTON_TEXT, highlightthickness=0, radius=9,
                                  font=(FONT, 9, "bold"), padx=8, pady=2, takefocus=0, cursor="arrow")
            value.pack(side="right")
            def update_value(*_args, target=value, variable=var):
                target.configure(text=f"{round(float(variable.get()))}%")
            self._preference_traces.append((var, var.trace_add("write", update_value)))
            update_value()
            # Flat slider: thin track, solid blue thumb; native keyboard handling kept.
            scale = tk.Scale(block, from_=low, to=high, variable=var, command=callback,
                             orient="horizontal", showvalue=False, resolution=1, width=8, sliderlength=18,
                             sliderrelief="flat", bg=PRIMARY, fg=TEXT, troughcolor=LINE,
                             activebackground=PRIMARY_HOVER, highlightthickness=0, bd=0, cursor="hand2")
            scale.pack(fill="x")
            self.display_scales[title] = scale
            scale.bind("<ButtonRelease-1>", lambda _event: self.assistant._save_settings())
            scale.bind("<KeyRelease>", lambda event: self.assistant._save_settings()
                       if event.keysym in {"Left", "Right", "Up", "Down", "Home", "End", "Prior", "Next"} else None)
        self._label(display, "不透明度 0% 完全透明，100% 完全不透明。", size=8, color=SUBTLE,
                    anchor="w").pack(fill="x")

        self._build_chest_auto_tap(content)

        # Collapsible "more": game mode, route, controls and the floating settings.
        self.preferences_more = self._card(content, padx=24, pady=18)
        self.preferences_more.pack(fill="x", pady=(0, 16))
        header, self._preferences_chevron = self._card_heading(
            self.preferences_more, "游戏与路线 · 更多设置", "游戏难度、作者路线、悬浮球与悬浮设置。", "map", chevron=True)
        header.pack(fill="x")
        self._clickable(header, self._toggle_preferences_extra)
        self.preferences_extra = tk.Frame(self.preferences_more, bg=SURFACE)
        inner = self.preferences_extra
        tk.Frame(inner, bg=LINE, height=1).pack(fill="x", pady=(16, 16))
        row = self._setting_row(inner, "游戏难度")
        self.mode_switch = self._segmented(row, self._mode_choice, ("噩梦", "困难"), self._change_mode)
        self.mode_switch.pack(side="right")
        row = self._setting_row(inner, "作者路线")
        self.route_box = RoundedCombobox(
            row, textvariable=self.assistant.route_profile_var,
            state="readonly", width=22)
        self.route_box.pack(side="right")
        self.route_box.bind("<<ComboboxSelected>>", self._change_route)
        self._refresh_route_choices()
        self.variant_row = self._setting_row(inner, "全棺路线版本")
        variant_var = getattr(self.assistant, "coffin_variant_var", tk.StringVar(self.root))
        existing = getattr(self.assistant, "coffin_variant_box", None)
        self.variant_box = RoundedCombobox(self.variant_row, textvariable=variant_var,
            values=tuple(existing.cget("values")) if existing is not None else (), state="readonly", width=22)
        self.variant_box.pack(side="right")
        self.variant_box.bind("<<ComboboxSelected>>", self._change_variant)
        self.variant_row.pack_forget()
        self._variant_anchor = row
        self._label(inner, textvariable=self._action_feedback, size=9, color=GOLD,
                    anchor="w", justify="left", wraplength=500).pack(fill="x")
        tk.Frame(inner, bg=LINE, height=1).pack(fill="x", pady=(6, 16))
        row = self._setting_row(inner, "显示悬浮球", "关闭后仍可用快捷键呼出游戏控件。")
        self.orb_visibility_check = tk.Checkbutton(
            row, text="", variable=self.assistant.orb_visibility_var,
            command=lambda: self.assistant.set_orb_visible(self.assistant.orb_visibility_var.get()),
            bg=SURFACE, fg=TEXT, activebackground=SURFACE, activeforeground=TEXT,
            selectcolor=INPUT_BG, disabledforeground=MUTED, anchor="e",
            highlightcolor=PRIMARY, highlightthickness=0, bd=0,
            font=(FONT, 10), cursor="hand2")
        self.orb_visibility_check.pack(side="right")
        row = self._setting_row(inner, "游戏控件", "在游戏旁打开快捷面板。")
        self.control_window_button = _button(row, "打开游戏控件", self._open_control)
        self.control_window_button.pack(side="right")
        row = self._setting_row(inner, "悬浮设置", "窗口位置、透明度等高级选项。")
        _button(row, "打开悬浮设置", self._open_advanced).pack(side="right")
        self.preferences_surface.bind("<Configure>", lambda _e: self._layout_preferences(), add="+")
        self._finish_scroll_page("preferences")

    def _build_chest_auto_tap(self, content):
        card = self._card(content, padx=24, pady=22)
        card.pack(fill="x", pady=(0, 2))
        heading, _ = self._card_heading(card, "开箱连点", "在游戏里按住触发键，自动连发开箱按键；松开立即停止。", "play")
        heading.pack(fill="x", pady=(0, 16))
        state = self.assistant.chest_auto_tap_snapshot()
        self._chest_enabled = tk.StringVar(self.root, "开启" if state.get("enabled") else "关闭")
        self._chest_hold = tk.StringVar(self.root, state.get("holdKey", ""))
        self._chest_tap = tk.StringVar(self.root, state.get("tapKey", ""))
        self._chest_interval = tk.StringVar(self.root, str(state.get("intervalMs", "")))
        row = self._setting_row(card, "开箱连点")
        self.chest_switch = self._segmented(row, self._chest_enabled, ("关闭", "开启"), self._save_chest_auto_tap)
        self.chest_switch.pack(side="right")
        tk.Frame(card, bg=LINE, height=1).pack(fill="x", pady=(0, 14))
        keys = tk.Frame(card, bg=SURFACE)
        keys.pack(fill="x")
        self.chest_boxes = {}
        for column, (title, variable, values, editable) in enumerate((
                ("按住触发", self._chest_hold, TRIGGER_KEYS, False),
                ("连发按键", self._chest_tap, TAP_KEYS, False),
                ("间隔（毫秒）", self._chest_interval, ("50", "70", "90", "120", "150", "200", "300", "500"), True))):
            keys.columnconfigure(column, weight=1, uniform="chest")
            cell = tk.Frame(keys, bg=SURFACE)
            cell.grid(row=0, column=column, sticky="ew", padx=(0 if column == 0 else 10, 0))
            self._label(cell, title, size=9, color=SUBTLE, anchor="w").pack(fill="x", pady=(0, 6))
            box = RoundedCombobox(cell, textvariable=variable, values=values,
                                  state="normal" if editable else "readonly", width=8)
            box.pack(fill="x")
            box.bind("<<ComboboxSelected>>", lambda _e: self._save_chest_auto_tap())
            if editable:
                box.bind("<Return>", lambda _e: self._save_chest_auto_tap())
                box.bind("<FocusOut>", lambda _e: self._save_chest_auto_tap(quiet=True))
            self.chest_boxes[title] = box
        self.chest_status = self._label(card, "", size=9, color=MUTED, anchor="w", justify="left", wraplength=500)
        self.chest_status.pack(fill="x", pady=(14, 0))
        self.chest_feedback = self._label(card, "", size=9, color=GOLD, anchor="w", justify="left", wraplength=500)
        self._label(card, "只在游戏窗口位于前台时发送按键。触发键不能与星图快捷键相同。", size=8, color=SUBTLE,
                    anchor="w", justify="left", wraplength=500).pack(fill="x", pady=(6, 0))
        self._chest_signature = None

    def _save_chest_auto_tap(self, *, quiet=False):
        current = self.assistant.chest_auto_tap_snapshot()
        wanted = dict(enabled=self._chest_enabled.get() == "开启", holdKey=self._chest_hold.get(),
                      tapKey=self._chest_tap.get(), intervalMs=self._chest_interval.get().strip())
        if quiet and str(wanted["intervalMs"]) == str(current.get("intervalMs")):
            return
        result = self.assistant.update_chest_auto_tap(**wanted)
        if not result.get("success"):
            # Disabling can succeed in memory even when saving fails.
            current = self.assistant.chest_auto_tap_snapshot()
            self._chest_enabled.set("开启" if current.get("enabled") else "关闭")
            self._chest_hold.set(current.get("holdKey", ""))
            self._chest_tap.set(current.get("tapKey", ""))
            self._chest_interval.set(str(current.get("intervalMs", "")))
        self.chest_feedback.configure(text=result.get("message") or "", fg=GREEN if result.get("success") else ERROR)
        if not self.chest_feedback.winfo_manager():
            self.chest_feedback.pack(fill="x", pady=(4, 0), after=self.chest_status)
        self._chest_signature = None
        self._sync_chest_auto_tap()

    def _sync_chest_auto_tap(self):
        state = self.assistant.chest_auto_tap_snapshot()
        signature = (state.get("enabled"), state.get("status"), state.get("holding"), state.get("error"))
        if signature == self._chest_signature:
            return
        self._chest_signature = signature
        self._chest_enabled.set("开启" if state.get("enabled") else "关闭")
        if not state.get("available"):
            text, color = "开箱连点只能在 Windows 上使用。", MUTED
        elif state.get("error"):
            text, color = state.get("status") or state["error"], ERROR
        elif not state.get("enabled"):
            text, color = f"未开启 · 开启后在游戏里按住 {state.get('holdKey')} 连发 {state.get('tapKey')}", MUTED
        else:
            text = state.get("status") or "等待按住触发键"
            color = ERROR if state.get("error") else GREEN if state.get("holding") else PRIMARY
        self.chest_status.configure(text=f"●  {text}", fg=color)

    def _layout_preferences(self):
        width = self.preferences_surface.winfo_width() / self._scale
        wide = width >= 700
        signature = wide, self._scale
        if self._preferences_layout == signature:
            return
        self._preferences_layout = signature
        for frame in (self.preferences_keys, self.preferences_display):
            frame.grid_forget()
        self.preferences_surface.columnconfigure(0, weight=1, uniform="settings")
        self.preferences_surface.columnconfigure(1, weight=1 if wide else 0, uniform="settings" if wide else "")
        self.preferences_keys.grid(row=0, column=0, sticky="nsew", padx=(0, self._px(4) if wide else 0),
                                   pady=(0, self._px(2)))
        self.preferences_display.grid(row=0 if wide else 1, column=1 if wide else 0,
                                      sticky="nsew", pady=(0, self._px(2)))

    def _reveal(self, page, widget):
        """After an expand, scroll just enough to show the opened card."""
        def run():
            canvas, content = self._scroll_pages[page][:2]
            self._fit_page(page)
            total = max(1, content.winfo_height())
            top = widget.winfo_rooty() - content.winfo_rooty()
            bottom = top + widget.winfo_height()
            view_top, view_height = canvas.canvasy(0), canvas.winfo_height()
            if bottom > view_top + view_height:
                canvas.yview_moveto(max(0, min(top, bottom - view_height + 12)) / total)
        self.root.after(40, run)

    def _fit_page(self, page):
        canvas, content = self._scroll_pages[page][:2]
        content.update_idletasks()
        canvas.configure(scrollregion=canvas.bbox("all"))

    def _toggle_preferences_extra(self):
        opened = not self.preferences_extra.winfo_manager()
        if opened:
            self.preferences_extra.pack(fill="x")
            self._reveal("preferences", self.preferences_more)
        else:
            self.preferences_extra.pack_forget()
        self._preferences_chevron.configure(text="⌄" if opened else "›")

    def _build_help(self):
        content = self._scroll_page("help")
        content.configure(bg=BG)
        self._scroll_pages["help"][0].configure(bg=BG)
        self._heading(content, "排查与帮助", "先看当前状态，再按需要展开步骤或导出排查信息。")
        self._build_update_card(content)
        state = self._card(content, padx=24, pady=20)
        state.pack(fill="x", pady=(0, 2))
        heading, _ = self._card_heading(state, "当前运行信息", "", "note")
        heading.pack(fill="x", pady=(0, 14))
        module = sys.modules.get(type(self.assistant).__module__)
        version = self._display_version_text()
        runtime_version = getattr(module, "APP_VERSION", "版本未读取")
        fingerprint = getattr(module, "BUILD_FINGERPRINT", "未读取")
        facts = tk.Frame(state, bg=SURFACE)
        facts.pack(fill="x")
        variable = getattr(self.assistant, "display_environment_var", tk.StringVar(self.root, "等待游戏环境信息"))
        for row_index, (title, kwargs) in enumerate((("版本", dict(text=f"星图 {version}")),
                                                     ("显示环境", dict(textvariable=variable)),
                                                     ("运行状态", dict()))):
            self._label(facts, title, size=9, color=SUBTLE, anchor="w").grid(
                row=row_index, column=0, sticky="nw", padx=(0, 18), pady=(0, 8))
            value = self._label(facts, size=10, anchor="w", justify="left", wraplength=480, **kwargs)
            value.grid(row=row_index, column=1, sticky="ew", pady=(0, 8))
            if row_index == 2:
                self.help_status = value
        facts.columnconfigure(1, weight=1)
        topics = (
            ("quick-start", "快速开始", "先核对模式与资源，再看本局状态；地图和楼层默认自动识别。", "play", (
                ("点一次“开始本局”。显示“已准备”时，切回游戏并打开地图，等待实际开始状态。", ("map", "toggle-round"), None),
                ("不继续时点“取消准备”，或使用当前“开始/结束本局”绑定。准备不等于已开始，取消准备不等于结束一局。", ("toggle-round",), None),
                ("楼层待确认时，先完整打开游戏地图等待自动确认；仍未确认时查看当前状态提示，再记录问题反馈。", ("map",), None),
                ("“结束本局”停止本局定位与贴图；设置、日志和作者图保留。", ("toggle-round",), None),
            )),
            ("shortcuts-visibility", "快捷键与显隐", "以当前键帽为准；控件显隐、收成球和小抄显隐各有作用。", "keyboard", (
                ("在设置点击键帽录入新组合；核对候选后点“确认”。冲突就重录，取消保留原绑定。", (), "preferences"),
                ("使用当前“呼出/收起控件”绑定，显示或隐藏整个游戏控件。", ("toggle-panel",), None),
                ("“收成球”仅折叠控件，球上的“展开”恢复面板；这与整个控件隐藏不同。录入地图键时收成球，会取消本次录入，已保存的地图键不变。", (), None),
                ("使用当前“显示/隐藏小抄”绑定改变贴图显隐；隐藏后识别、锁图和后台贴合继续。", ("toggle-sheet-visibility",), None),
                ("参考小抄可在设置中单独显示或隐藏；总显影仍同时控制参考和贴合显示。切回游戏后，在悬浮面板点“调整小抄”，拖动后点“完成调整”恢复鼠标穿透。", (), None),
            )),
            ("resources-opacity", "资源与透明度", "先看当前模式的资源状态；不透明度越高，小抄越实。", "map", (
                ("噩梦作者路线未安装时，结构底图仍可独立浏览；资源未就绪时先查看当前模式的缺失提示。", (), "maps"),
                ("“不透明度”0%为完全透明，100%为完全不透明；看不见时同时检查小抄显影是否开启。", ("toggle-sheet-visibility",), None),
            )),
            ("check-feedback", "检查与反馈", "先看状态，再提供发生步骤、版本和排查信息。", "help", (
                ("记录发生时间、模式、地图或楼层、具体动作、当前绑定及等待时间；附帮助页的版本与构建指纹。", (), None),
                ("界面可操作时使用“导出排查信息”；导出后先查看文件内容，再分享给反馈接收方。", (), "export"),
            )),
        )
        self._help_topics = {}
        self._step_badges = []
        self._help_binding_labels = []
        self._help_binding_texts = {}
        self._help_images = {}
        for topic, title, summary, icon, steps in topics:
            card = self._card(content, padx=24, pady=18)
            card.pack(fill="x", pady=(0, 2))
            header, chevron = self._card_heading(card, title, summary, icon, chevron=True)
            header.pack(fill="x")
            self._clickable(header, lambda topic=topic: self._toggle_help_topic(topic))
            details = tk.Frame(card, bg=SURFACE)
            self._help_topics[topic] = chevron, details
            tk.Frame(details, bg=LINE, height=1).pack(fill="x", pady=(16, 14))
            for index, (text, targets, action) in enumerate(steps, 1):
                row = tk.Frame(details, bg=SURFACE)
                row.pack(fill="x", pady=(0, 12))
                # Number over a soft circle; the circle image is rendered per scale in _apply_icons.
                badge = tk.Label(row, text=str(index), bg=SURFACE, fg=BUTTON_TEXT, bd=0,
                                 font=(FONT, 9, "bold"), compound="center")
                badge.pack(side="left", anchor="n", padx=(0, 12))
                self._step_badges.append(badge)
                body = tk.Frame(row, bg=SURFACE)
                body.pack(side="left", fill="x", expand=True)
                self._label(body, text, size=10, anchor="w", justify="left", wraplength=520).pack(fill="x")
                if topic == "check-feedback" and index == 1:
                    self._label(body, f"运行版本：{runtime_version}\n构建：{fingerprint}", size=9, color=SUBTLE,
                                anchor="w", justify="left", wraplength=520).pack(fill="x", pady=(4, 0))
                if targets:
                    label = self._label(body, size=9, color=SUBTLE, anchor="w", justify="left", wraplength=520)
                    label.pack(fill="x", pady=(4, 0))
                    self._help_binding_labels.append((label, targets))
                if action in {"preferences", "maps"}:
                    text = "前往设置  ›" if action == "preferences" else "查看地图资源  ›"
                    self._ghost(body, text, lambda page=action: self.show_page(page), size=9).pack(anchor="w", pady=(4, 0))
                elif action == "export":
                    self.export_button = _button(body, "导出排查信息", self._export_diagnostics)
                    self.export_button.pack(anchor="w", pady=(8, 0))
                    self.export_feedback = self._label(body, size=9, color=SUBTLE, anchor="w", justify="left", wraplength=510)
                    self.export_feedback.pack(fill="x", pady=(6, 0))
            image_toggle = self._ghost(details, "查看操作图", lambda topic=topic: self._toggle_help_image(topic), size=9)
            image_toggle.pack(anchor="w", padx=(30, 0))
            holder = tk.Frame(details, bg=SURFACE)
            image_label = self._label(holder, anchor="center", justify="center")
            image_label.pack(fill="x")
            self._help_images[topic] = dict(toggle=image_toggle, holder=holder, label=image_label,
                opened=False, source=None, photo=None, job=None, generation=0, size=None)
            holder.bind("<Configure>", lambda event, topic=topic: self._queue_help_image(topic), add="+")
        tk.Frame(content, bg=BG, height=14).pack(fill="x")
        self._finish_scroll_page("help")

    def _help_image_path(self, topic):
        module = sys.modules.get(type(self.assistant).__module__)
        resource_root = Path(getattr(module, "RESOURCE_ROOT", Path(__file__).resolve().parents[1]))
        return resource_root / "assets" / "help" / self.HELP_IMAGE_FILES[topic]

    def _toggle_help_image(self, topic):
        entry = self._help_images[topic]
        if entry["opened"]:
            self._close_help_image(topic)
            return
        entry["opened"] = True
        entry["toggle"].configure(text="收起操作图")
        entry["holder"].pack(fill="x", pady=(0, 12))
        self._queue_help_image(topic)

    def _queue_help_image(self, topic):
        entry = self._help_images[topic]
        if self._closed or not entry["opened"]:
            return
        if entry["job"] is not None:
            self.root.after_cancel(entry["job"])
        entry["generation"] += 1
        generation = entry["generation"]
        def render():
            if self._closed or not entry["opened"] or generation != entry["generation"]:
                return
            entry["job"] = None
            self._render_help_image(topic)
        entry["job"] = self.root.after(80, render)

    def _render_help_image(self, topic):
        entry = self._help_images[topic]
        if self._closed or not entry["opened"]:
            return
        width = entry["holder"].winfo_width()
        if width <= 1:
            return
        box = min(max(1, width - self._px(8)), self._px(880)), max(1, self._px(520))
        if entry["size"] == box:
            return
        try:
            if entry["source"] is None:
                with Image.open(self._help_image_path(topic)) as source:
                    entry["source"] = source.convert("RGBA")
            with ImageOps.contain(entry["source"], box, Image.Resampling.LANCZOS) as preview:
                photo = ImageTk.PhotoImage(preview, master=self.window)
            entry["label"].configure(image=photo, text="")
            entry["photo"] = photo
            entry["size"] = box
        except (OSError, ValueError, tk.TclError):
            entry["label"].configure(image="", text="操作图暂不可用，仍可按上方步骤操作。")

    def _close_help_image(self, topic):
        entry = self._help_images[topic]
        entry["opened"] = False
        entry["generation"] += 1
        if entry["job"] is not None:
            try:
                self.root.after_cancel(entry["job"])
            except tk.TclError:
                pass
            entry["job"] = None
        try:
            entry["holder"].pack_forget()
            entry["label"].configure(image="", text="")
            entry["toggle"].configure(text="查看操作图")
        except tk.TclError:
            pass
        entry["photo"] = None
        entry["size"] = None
        if entry["source"] is not None:
            entry["source"].close()
            entry["source"] = None

    def _close_help_images(self):
        for topic in getattr(self, "_help_images", {}):
            self._close_help_image(topic)

    def _display_version_text(self):
        getter = getattr(self.assistant, "display_version_label", None)
        if callable(getter):
            return str(getter() or "版本未读取")
        module = sys.modules.get(type(self.assistant).__module__)
        return str(getattr(module, "APP_VERSION", "版本未读取"))

    def _sync_reference_controls(self):
        getter = getattr(self.assistant, "reference_sheet_snapshot", None)
        state = getter() if callable(getter) else {}
        enabled = state.get("enabled")
        visible = state.get("visible") is True
        adjusting = state.get("adjusting") is True
        can_adjust = state.get("canAdjust") is True
        toggle = callable(getattr(self.assistant, "toggle_reference_sheet_visibility", None))
        adjust = callable(getattr(self.assistant, "finish_reference_sheet_move" if adjusting else "move_sheet", None))
        signature = enabled, visible, adjusting, can_adjust, toggle, adjust
        if signature == self._reference_controls_signature:
            return
        self._reference_controls_signature = signature
        known = isinstance(enabled, bool)
        self.reference_visibility_button.configure(text="隐藏" if enabled is True else "显示",
            state="normal" if known and toggle else "disabled")
        self.reference_adjust_button.configure(text="完成" if adjusting else "调整位置",
            state="normal" if known and adjust and (adjusting or can_adjust) else "disabled")
        text = ("状态未读取" if not known else "调整中" if visible and adjusting else "显示中" if visible
                else "已允许，当前隐藏" if enabled else "已隐藏")
        self.reference_status.configure(text=text)

    def _reference_action(self, method):
        callback = getattr(self.assistant, method, None)
        result = callback() if callable(callback) else dict(success=False, message="参考小抄暂不可用")
        message = str(result.get("message") or "") if isinstance(result, dict) else ""
        self.reference_feedback.configure(text=message)
        if message:
            self.reference_feedback.pack(fill="x", pady=(4, 0))
        else:
            self.reference_feedback.pack_forget()
        self._sync_reference_controls()

    def _toggle_reference_visibility(self):
        self._reference_action("toggle_reference_sheet_visibility")

    def _adjust_reference_position(self):
        getter = getattr(self.assistant, "reference_sheet_snapshot", None)
        state = getter() if callable(getter) else {}
        self._reference_action("finish_reference_sheet_move" if state.get("adjusting") is True else "move_sheet")

    def _toggle_help_topic(self, topic):
        chevron, details = self._help_topics[topic]
        if details.winfo_manager():
            self._close_help_image(topic)
            details.pack_forget()
            chevron.configure(text="›")
        else:
            details.pack(fill="x")
            chevron.configure(text="⌄")
            self._reveal("help", details.master)

    def _sync_help_bindings(self, values):
        changed = {target for target, text in values.items() if self._help_binding_texts.get(target) != text}
        if not changed:
            return
        self._help_binding_texts = dict(values)
        for label, targets in self._help_binding_labels:
            if changed.intersection(targets):
                text = "；".join(f"{ACTION_NAMES[target]} {values.get(target, '未设置')}" for target in targets)
                label.configure(text=f"当前绑定：{text}")

    def _build_maps(self) -> None:
        self.map_browser = MapResourceBrowser(self.body, self.assistant)
        self._page_frames["maps"] = self.map_browser
        # This existing row owns page-top blank space; its Button remains excluded.
        heading = self.map_browser.heading
        passive_labels = tuple(child for child in heading.winfo_children()
                               if isinstance(child, tk.Label))
        self.drag_handles += (heading, *passive_labels)

    def _refresh_route_choices(self) -> None:
        choices = [label for _key, label in self.assistant._mode_route_options()]
        self.route_box.configure(values=choices)

    def _change_mode(self, event=None) -> None:
        requested = self._mode_choice.get()
        callback = getattr(self.assistant, "set_game_mode", None)
        result = callback("difficult" if requested == "困难" else "nightmare") if callable(callback) else None
        self._refresh_route_choices()
        self._refresh_maps()
        actual = self.assistant.game_mode_var.get()
        self._mode_choice.set(actual)
        self._action_feedback.set(str(result.get("message") or "模式未切换。")
                                  if isinstance(result, dict) and result.get("success") is False else "")
        self._sync_view()

    def _change_route(self, event=None):
        self.assistant._apply_route_profile_setting(event)
        self._sync_view()

    def _change_variant(self, event=None):
        callback = getattr(self.assistant, "_apply_coffin_variant_setting", None)
        if callback:
            callback(event)
        self._sync_view()

    def _save_map_key(self):
        self.hotkey_editor._invoke("confirm_hotkey_edit")
        self._sync_view()

    def _capture_map_key(self):
        self.hotkey_editor._invoke("begin_hotkey_edit", "map", owner=self.window)
        self._sync_view()

    def _cancel_map_key(self):
        callback = getattr(self.assistant, "cancel_hotkey_edit", None)
        if not getattr(self.assistant, "closed", False) and callable(callback):
            callback(reason="view-hidden")

    def _unmap(self, event):
        # Owner-level focus policy and edit generation stay in the controller.
        if event.widget is self.window and not self._closed:
            self._close_help_images()
            self._cancel_map_key()

    def _save_shortcuts(self):
        self.hotkey_editor._invoke("confirm_hotkey_edit")

    def _start_round(self):
        getter = getattr(self.assistant, "_route_presentation_snapshot", None)
        snapshot = getter() if callable(getter) else {}
        action = "toggle_round" if snapshot.get("roundPreparing") is True else "start_round"
        callback = getattr(self.assistant, action, None)
        if callable(callback):
            callback()
        self._sync_view()

    def _toggle_visibility(self):
        self.assistant.toggle_sheet_visibility()
        self._sync_view()

    def _toggle_home_details(self):
        self._home_details_open = not self._home_details_open
        self._sync_view()

    def _end_round(self):
        callback = getattr(self.assistant, "end_round", None)
        if callback:
            callback()
        self._sync_view()

    def _return_to_game(self):
        callback = getattr(self.assistant, "focus_game_from_desktop", None)
        if callback:
            result = callback()
            if isinstance(result, dict):
                self._action_feedback.set(str(result.get("message") or ""))
                if result.get("success") is True:
                    self.hide()
            elif result is False:
                self._action_feedback.set("未能切回游戏，请从任务栏选择第五人格。")

    def _request_main_close(self):
        callback = getattr(self.assistant, "request_desktop_close", None)
        if callable(callback):
            callback()

    def show_close_choice(self, on_choice):
        """Present a choice; only the controller executes background or exit."""
        existing = self._close_choice_window
        if existing is not None and existing.winfo_exists():
            existing.deiconify()
            existing.lift()
            return existing
        dialog = tk.Toplevel(self.window)
        self._close_choice_window = dialog
        dialog.title("关闭主页面")
        dialog.transient(self.window)
        dialog.configure(bg=SURFACE)
        body = tk.Frame(dialog, bg=SURFACE)
        body.pack(fill="both", expand=True, padx=20, pady=18)
        self._label(body, "接下来怎么做？", size=14, bold=True, anchor="w").pack(fill="x")
        self._label(body, "最小化到任务栏会保留本局。退出会停止星图。",
                    size=10, anchor="w", justify="left", wraplength=380).pack(fill="x", pady=(10, 18))
        actions = tk.Frame(body, bg=SURFACE)
        actions.pack(fill="x")
        def choose(choice):
            self._close_choice_window = None
            dialog.destroy()
            on_choice(choice)
        _button(actions, "最小化到任务栏", lambda: choose("background")).pack(side="left")
        _button(actions, "退出星图", lambda: choose("exit")).pack(side="left", padx=8)
        cancel = _button(actions, "取消", lambda: choose("cancel"))
        cancel.pack(side="right")
        dialog.protocol("WM_DELETE_WINDOW", lambda: choose("cancel"))
        dialog.bind("<Escape>", lambda _event: choose("cancel"))
        display = WindowDisplay(dialog, self.assistant, preferred=(460, 210), minimum=(410, 180))
        display.start(self.window)
        cancel.focus_set()
        return dialog

    def _export_diagnostics(self):
        callback = getattr(self.assistant, "export_diagnostic_logs", None)
        if callback:
            callback()
        self._sync_view()

    def _sync_view(self):
        getter = getattr(self.assistant, "_route_presentation_snapshot", None)
        snapshot = getter() if callable(getter) else {}
        self.map_browser.sync_game_mode(snapshot)
        if self._page == "custom":
            self.custom_editor.refresh()
        elif self._page == "preferences":
            self._sync_chest_auto_tap()
        self.hide_button.configure(text="最小化到任务栏")
        control_visible = snapshot.get("controlVisible")
        self.panel_button.configure(text="  查看游戏控件" if control_visible is True else "  打开游戏控件")
        connected = bool(getattr(self.assistant, "game_hwnd", None))
        self.header_caption.configure(text="●  已连接游戏" if connected else "○  等待连接游戏",
                                      fg=SIDEBAR_SUCCESS if connected else SIDEBAR_MUTED)
        foreground = snapshot.get("foreground") is True
        phase = snapshot.get("phase")
        title = snapshot.get("title") or "等待运行状态"
        message = snapshot.get("message") or "尚未收到当前运行状态。"
        color = status_color(snapshot)
        self.home_status.configure(text=snapshot.get("controlStatusText") or title, fg=color)
        self.home_message.configure(text=message)
        mode = self.assistant.active_mode_var.get()
        route = self.assistant.route_profile_var.get()
        self.home_setup_summary.configure(text=f"{mode.removeprefix('当前模式：')} · {route}")
        today = time.localtime()
        self.header_greeting.configure(text=f"{today.tm_mon}月{today.tm_mday}日 · 星期{'一二三四五六日'[today.tm_wday]}")
        self._mode_choice.set(self.assistant.game_mode_var.get())
        key_getter = getattr(self.assistant, "hotkey_editor_snapshot", None)
        key_snapshot = key_getter() if callable(key_getter) else {}
        key_texts = {}
        for target, label in self.home_keycaps.items():
            key_texts[target] = str((key_snapshot.get("bindings", {}).get(target) or {}).get("text") or "未设置")
            label.configure(text=key_texts[target])
        self._sync_help_bindings(key_texts)
        self._sync_reference_controls()
        self.map_browser.sync_map_package_import()
        self.map_browser.sync_online_packages()
        self.home_session.configure(text=snapshot.get("roundStateText") or "本局状态未知")
        self.home_dot.configure(fg=color)
        map_id = snapshot.get("mapId")
        item = getattr(self.assistant.pack, "by_id", {}).get(map_id)
        locked = snapshot.get("locked") is True
        self.home_round.configure(text=(getattr(item, "name", "") if locked else "待确认"),
                                  fg=TEXT if locked else MUTED)
        floor = snapshot.get("controlFloorText")
        self.home_display.configure(text=floor or "待核对", fg=MUTED if not floor or "待" in floor else TEXT)
        redundant_status = self.home_status.cget("text") in {
            self.home_session.cget("text"), self.home_round.cget("text"), self.home_display.cget("text")}
        if redundant_status:
            self.home_status.pack_forget()
        elif not self.home_status.winfo_manager():
            self.home_status.pack(fill="x")
        visible = snapshot.get("userVisible")
        self.visibility_button.configure(
            text="关闭显影" if visible is True else "允许显影" if visible is False else "显影状态未知",
            state="normal" if (visible is True or visible is False) and callable(getattr(self.assistant, "toggle_sheet_visibility", None)) else "disabled",
            bg=WARNING_BG if visible is False else BUTTON_BG,
            fg=GOLD if visible is False else BUTTON_TEXT)
        self.preferences_visibility_button.configure(
            text="开启" if visible is True else "关闭" if visible is False else "未知",
            state="normal" if isinstance(visible, bool) else "disabled",
            bg=PRIMARY if visible is True else BUTTON_BG,
            fg="white" if visible is True else BUTTON_TEXT)
        active = snapshot.get("roundActive") is True
        action_button = self.end_round_button if active else self.start_round_button
        (self.start_round_button if active else self.end_round_button).pack_forget()
        if not action_button.winfo_manager():
            action_button.pack(side="left", before=self.visibility_button)
        action_button.configure(text=snapshot.get("roundActionText") or ("结束本局" if active else "开始本局"),
                                state="normal" if snapshot.get("roundEndEnabled" if active else "roundStartEnabled") is True
                                and callable(getattr(self.assistant, "end_round" if active else "start_round", None)) else "disabled")
        focus_callback = getattr(self.assistant, "focus_game_from_desktop", None)
        can_return = callable(focus_callback) and bool(getattr(self.assistant, "game_hwnd", None))
        recovery = phase in {"needs-user", "failed"}
        pending = snapshot.get("pending") is True
        self.recognition_button.configure(
            text="查看恢复步骤" if recovery else "查看识别与路线",
            state="normal" if callable(getattr(self.assistant, "_open_recognition_details", None)) and not pending else "disabled")
        next_action = ("先切回游戏，再从游戏控件查看。" if not foreground and can_return else
                       "请先打开第五人格。" if not can_return else
                       "正在处理，请稍候。" if pending else "")
        self.recognition_hint.configure(text=next_action)
        show_message = recovery or pending or self._home_details_open
        # Keep a live anchor in every state. Empty Labels/Frames still request
        # height in Tk, so they must not reserve a silent line in the hero.
        previous = self.home_actions
        for widget, shown in ((self.home_details, show_message and not redundant_status),
                              (self.home_message, show_message),
                              (self.recognition_hint, bool(next_action))):
            if shown:
                widget.pack(fill="x", pady=(10 if previous is self.home_actions else 4, 0), after=previous)
                previous = widget
            else:
                widget.pack_forget()
        if self._action_feedback.get():
            self.home_feedback.pack(fill="x", pady=(10, 0))
        else:
            self.home_feedback.pack_forget()
        self.return_game_button.configure(state="normal" if can_return else "disabled")
        self._refresh_home_resources()
        preferred = getattr(self.assistant, "preferred_route_profile_id", None)
        if preferred == "zhan10-coffin" and hasattr(self.assistant, "coffin_variant_var"):
            if not self.variant_row.winfo_manager():
                self.variant_row.pack(fill="x", pady=(0, 14), after=self._variant_anchor)
        else:
            self.variant_row.pack_forget()
        self._refresh_route_choices()
        self.hotkey_editor.render()
        exporting = bool(getattr(self.assistant, "diagnostic_export_in_progress", False))
        self.export_button.configure(text="正在导出…" if exporting else "导出排查信息",
            state="disabled" if exporting or not callable(getattr(self.assistant, "export_diagnostic_logs", None)) else "normal")
        self.export_feedback.configure(text="导出操作正在后台进行。" if exporting else "完成后会显示文件位置。")
        self.help_status.configure(text=snapshot.get("message") or self.assistant.quick_connection_var.get(), fg=color)
        # A child can change its requested height without changing the fixed
        # Canvas item's allocation. Reconcile through the existing refresh.
        self._fit_home_canvas()

    def _refresh_view(self):
        self._refresh_job = None
        if self._closed:
            return
        self._poll_updates()
        if self.window.winfo_viewable():
            self.display.refresh()
            self._layout_shell()
            self._sync_view()
        self._refresh_job = self.root.after(350, self._refresh_view)

    def _destroy(self, event):
        if event.widget is not self.window:
            return
        self._closed = True
        self._brand_images.close()
        self._window_brand_icons = ()
        self.brand_icon._desktop_icon = None
        self._close_help_images()
        for variable, token in getattr(self, "_preference_traces", ()):
            variable.trace_remove("write", token)
        self._preference_traces = []
        if self._refresh_job is not None:
            self.root.after_cancel(self._refresh_job)
            self._refresh_job = None
        if self._update_dialog is not None and self._update_dialog.winfo_exists():
            self._update_dialog.destroy()
        for name in ("_nav_mark_job", "_page_motion_job"):
            job = getattr(self, name, None)
            if job is not None:
                self.root.after_cancel(job)
                setattr(self, name, None)
        for page in self._scroll_pages.values():
            if len(page) == 4:
                self.root.unbind_class(page[2], "<MouseWheel>")
                self.root.deletecommand(page[3])

    def _refresh_maps(self) -> None:
        self.map_browser.refresh()

    def _open_recognition(self) -> None:
        if not bool(getattr(self.assistant, "game_foreground", False)):
            self._action_feedback.set("恢复步骤：先返回已连接的游戏，再打开控件窗的人工确认或恢复入口。切回游戏后，当前确认与错误仍需处理。")
            self.show_page("home")
            return
        self.hide()
        self.assistant._open_recognition_details()

    def _open_advanced(self) -> None:
        self.hide()
        self.assistant._show_assistant_shell("settings")

    def show_page(self, page: str) -> None:
        if page not in self.PAGES:
            raise ValueError(page)
        if self._page == "help" and page != "help":
            self._close_help_images()
        if self._page == "preferences" and page != "preferences":
            self._cancel_map_key()
        for name, frame in self._page_frames.items():
            frame.pack_forget()
            selected = name == page
            self._nav_buttons[name].configure(
                bg=SIDEBAR_SELECTED if selected else SIDEBAR,
                fg=SIDEBAR_TEXT if selected else SIDEBAR_MUTED,
                activebackground=SIDEBAR_SELECTED if selected else SIDEBAR_HOVER)

        self._page_frames[page].pack(fill="both", expand=True)
        changed = page != self._page
        self._page = page
        self._place_nav_mark(animate=changed)
        if changed:
            self._rise_page()
        if page == "home":
            self.header.pack(fill="x", before=self.body)
        else:
            self.header.pack_forget()
        self.header_title.configure(text={"home": "主页面", "maps": "地图资源", "custom": "自定义地图",
                                          "preferences": "设置", "help": "帮助"}[page])
        self._apply_icons()
        if page == "maps":
            self._refresh_maps()
        elif page == "custom":
            self.custom_editor.refresh()
        elif page == "preferences":
            self._refresh_route_choices()

    # Updates -----------------------------------------------------------------
    def _update_state_path(self) -> Path:
        settings = getattr(self.assistant, "settings_path", None)
        if isinstance(settings, (str, Path)):
            return Path(settings).parent / "update-state.json"
        return Path.home() / ".xingtu" / "update-state.json"

    def _build_update_card(self, content):
        card = self._card(content, padx=24, pady=20)
        card.pack(fill="x", pady=(0, 2))
        self.update_card = card
        heading, _ = self._card_heading(card, "版本与更新", "新版本会在这里提醒，下载在浏览器中进行。", "note")
        heading.pack(fill="x", pady=(0, 14))
        facts = tk.Frame(card, bg=SURFACE)
        facts.pack(fill="x")
        facts.columnconfigure(1, weight=1)
        self._label(facts, "当前版本", size=9, color=SUBTLE, anchor="w").grid(row=0, column=0, sticky="w", padx=(0, 18), pady=(0, 8))
        self._label(facts, f"v{self._display_version_text()}", size=10, anchor="w").grid(row=0, column=1, sticky="w", pady=(0, 8))
        self._label(facts, "最新版本", size=9, color=SUBTLE, anchor="w").grid(row=1, column=0, sticky="w", padx=(0, 18), pady=(0, 8))
        self.update_status = self._label(facts, "尚未检查", size=10, anchor="w", justify="left", wraplength=460)
        self.update_status.grid(row=1, column=1, sticky="ew", pady=(0, 8))
        actions = tk.Frame(card, bg=SURFACE)
        actions.pack(fill="x", pady=(6, 0))
        self.update_check_button = _button(actions, "检查更新", lambda: self._check_updates(manual=True))
        self.update_check_button.pack(side="left")
        self.update_quark_button = _button(actions, "夸克网盘下载", lambda: self._open_update_page("quark"), primary=True)
        self.update_uc_button = _button(actions, "UC 网盘下载", lambda: self._open_update_page("uc"))
        self.update_notes_title = self._label(card, "更新公告", size=10, bold=True, anchor="w")
        self.update_notes = self._label(card, size=9, color=MUTED, anchor="w", justify="left", wraplength=560)
        self.update_history_toggle = self._ghost(card, "往期更新  ›", self._toggle_update_history, size=9)
        self.update_history = tk.Frame(card, bg=SURFACE)
        self._update_history_signature = None
        self._update_ui_signature = None

    def _check_updates(self, *, manual=False):
        if self.updates.check(manual=manual):
            self._sync_update_ui()

    def _open_update_page(self, provider):
        release = self.updates.latest
        url = release.pages.get(provider) if release is not None else None
        if url:
            self.updates.mark_notes_seen()
            webbrowser.open(url)
            self._sync_update_ui()

    def _toggle_update_history(self):
        opened = not self.update_history.winfo_manager()
        if opened:
            self.update_history.pack(fill="x", pady=(8, 0))
            self._reveal("help", self.update_card)
        else:
            self.update_history.pack_forget()
        self.update_history_toggle.configure(text="收起往期更新  ‹" if opened else "往期更新  ›")

    def _render_update_history(self):
        releases = self.updates.history[:10]
        signature = tuple(release.version for release in releases)
        if signature == self._update_history_signature:
            return
        self._update_history_signature = signature
        for child in self.update_history.winfo_children():
            child.destroy()
        for index, release in enumerate(releases):
            if index:
                tk.Frame(self.update_history, bg=LINE, height=1).pack(fill="x", pady=10)
            head = tk.Frame(self.update_history, bg=SURFACE)
            head.pack(fill="x")
            self._label(head, f"v{release.version}", size=10, bold=True).pack(side="left")
            self._label(head, release.published_date, size=8, color=SUBTLE).pack(side="left", padx=(8, 0))
            self._label(self.update_history, _notes_body(release.notes), size=9, color=MUTED, anchor="w",
                        justify="left", wraplength=540).pack(fill="x", pady=(4, 0))

    def _sync_update_ui(self):
        service = self.updates
        release = service.latest
        available = service.phase == "available" and release is not None
        signature = (service.phase, service.message, release and release.version,
                     service.skipped_version, service.has_unread, len(service.history))
        if signature == self._update_ui_signature:
            return
        self._update_ui_signature = signature
        # Sidebar: version line plus a gold "新版本" pill while the notes are unread.
        if available and service.has_unread and self.rail_version.winfo_manager():
            self.rail_update_badge.pack(side="top", pady=(0, self._px(4)), after=self.rail_version)
        else:
            self.rail_update_badge.pack_forget()
        # Home banner: a quiet reminder that stays until the version is skipped.
        if available and release.version != service.skipped_version:
            self.home_update_text.configure(text=f"发现新版本 v{release.version}"
                                                 + (f" · {release.published_date}" if release.published_date else ""))
            if not self.home_update.winfo_manager():
                self.home_update.pack(fill="x", pady=(0, 2), before=self.hero)
        else:
            self.home_update.pack_forget()
        # Help card.
        color = (BUTTON_TEXT if available else GREEN if service.phase == "latest"
                 else ERROR if service.phase == "error" else MUTED)
        status = service.message or "尚未检查"
        if release is not None and service.phase in {"available", "latest"} and release.published_date:
            status += f"\n发布于 {release.published_date}"
        self.update_status.configure(text=status, fg=color)
        checking = service.phase == "checking"
        self.update_check_button.configure(text="正在检查…" if checking else "检查更新",
                                           state="disabled" if checking else "normal")
        for button, provider in ((self.update_quark_button, "quark"), (self.update_uc_button, "uc")):
            if available and release.pages.get(provider):
                if not button.winfo_manager():
                    button.pack(side="left", padx=(8, 0))
            else:
                button.pack_forget()
        if release is not None:
            self.update_notes.configure(text=_notes_body(release.notes))
            self.update_notes_title.pack(fill="x", pady=(16, 4))
            self.update_notes.pack(fill="x")
            self.update_history_toggle.pack(anchor="w", pady=(10, 0))
            self._render_update_history()
        self._fit_page("help")

    def _poll_updates(self):
        if self.updates.poll() is not None:
            self._sync_update_ui()
            if self.updates.should_prompt() and self.window.winfo_viewable():
                self.show_update_dialog()

    def show_update_dialog(self):
        """Update reminder: notes, the two official download pages, later or skip."""
        release = self.updates.latest
        if release is None:
            return None
        existing = self._update_dialog
        if existing is not None and existing.winfo_exists():
            existing.deiconify()
            existing.lift()
            return existing
        self.updates.mark_prompted()
        self.updates.mark_notes_seen()
        dialog = tk.Toplevel(self.window)
        self._update_dialog = dialog
        dialog.title("发现新版本")
        dialog.transient(self.window)
        dialog.configure(bg=SURFACE)
        body = tk.Frame(dialog, bg=SURFACE)
        body.pack(fill="both", expand=True, padx=24, pady=20)
        head = tk.Frame(body, bg=SURFACE)
        head.pack(fill="x")
        mark = tk.Label(head, bg=SURFACE, bd=0)
        mark._desktop_icon = (self._brand_images.get("round", self._px(44))
                              or ImageTk.PhotoImage(_brand_mark(self._px(44)), master=dialog))
        mark.configure(image=mark._desktop_icon)
        mark.pack(side="left", padx=(0, 14))
        words = tk.Frame(head, bg=SURFACE)
        words.pack(side="left", fill="x", expand=True)
        kicker = tk.Frame(words, bg=SURFACE)
        kicker.pack(fill="x")
        self._label(kicker, "✦", size=9, bold=True, color=STAR_GOLD).pack(side="left")
        self._label(kicker, "NEW VERSION", size=8, bold=True, color=PRIMARY).pack(side="left", padx=(6, 0))
        self._label(words, f"发现新版本 v{release.version}", size=16, bold=True, anchor="w").pack(fill="x", pady=(2, 0))
        meta = " · ".join(part for part in (f"当前 v{self._display_version_text()}", release.published_date and f"发布于 {release.published_date}",
                                            release.size and f"约 {release.size / 1024 / 1024:.0f} MB") if part)
        self._label(words, meta, size=9, color=SUBTLE, anchor="w").pack(fill="x", pady=(2, 0))
        notes_card = RoundedFrame(body, bg=INPUT_BG, radius=12, padx=14, pady=12, highlightthickness=0)
        notes_card.pack(fill="both", expand=True, pady=(16, 16))
        self._label(notes_card, "更新公告", size=9, bold=True, anchor="w").pack(fill="x", pady=(0, 6))
        text = tk.Text(notes_card, bg=INPUT_BG, fg=TEXT, relief="flat", bd=0, highlightthickness=0,
                       wrap="char", font=(FONT, 10), height=9, cursor="arrow", padx=0, pady=0,
                       spacing1=2, spacing3=4)
        text.insert("1.0", _notes_body(release.notes))
        text.configure(state="disabled")
        text.pack(fill="both", expand=True)
        actions = tk.Frame(body, bg=SURFACE)
        actions.pack(fill="x")

        def close():
            self._update_dialog = None
            dialog.destroy()
            self._sync_update_ui()

        def skip():
            self.updates.skip_latest()
            close()

        def download(provider):
            self._open_update_page(provider)
            close()

        if release.pages.get("quark"):
            _button(actions, "夸克网盘下载", lambda: download("quark"), primary=True).pack(side="left")
        if release.pages.get("uc"):
            _button(actions, "UC 网盘下载", lambda: download("uc")).pack(side="left", padx=(8, 0))
        later = self._ghost(actions, "稍后提醒", close, color=MUTED)
        later.pack(side="right")
        self._ghost(actions, "跳过此版本", skip, color=SUBTLE).pack(side="right", padx=(0, 4))
        dialog.protocol("WM_DELETE_WINDOW", close)
        dialog.bind("<Escape>", lambda _event: close())
        display = WindowDisplay(dialog, self.assistant, preferred=(520, 440), minimum=(440, 360))
        display.start(self.window)
        later.focus_set()
        self._sync_update_ui()
        return dialog

    def _open_update_card(self):
        self.show_page("help")
        self.root.after(60, lambda: self._scroll_pages["help"][0].yview_moveto(0))

    def _place_nav_mark(self, *, animate=True):
        """Ease the gold marker to the current nav item; snaps when not yet mapped."""
        if self._closed or not self.window.winfo_exists():
            return
        button = self._nav_buttons[self._page]
        if self._nav_mark_job is not None:
            self.root.after_cancel(self._nav_mark_job)
            self._nav_mark_job = None
        if button.winfo_height() <= 1:
            self._nav_mark_job = self.root.after(60, lambda: self._place_nav_mark(animate=False))
            return
        x = button.winfo_x() + self._px(5)
        target = button.winfo_y() + button.winfo_height() // 2
        start = self._nav_mark_y if animate and self._nav_mark_y is not None else target

        def step(index, frames=8):
            progress = 1 - (1 - index / frames) ** 3
            y = round(start + (target - start) * progress)
            self._nav_mark.place(in_=self.rail, x=x, y=y, anchor="w")
            self._nav_mark.lift()
            self._nav_mark_y = y
            self._nav_mark_job = self.root.after(16, step, index + 1) if index < frames else None
        step(0 if start != target else 8)

    def _rise_page(self):
        """A short upward settle when switching pages; layout ends exactly where it began."""
        if self._page_motion_job is not None:
            self.root.after_cancel(self._page_motion_job)
            self._page_motion_job = None
        if not self.window.winfo_viewable():
            self.body.pack_configure(pady=0)
            return

        def step(offsets):
            self.body.pack_configure(pady=(self._px(offsets[0]), 0))
            self._page_motion_job = self.root.after(16, step, offsets[1:]) if len(offsets) > 1 else None
        step((14, 9, 5, 2, 1, 0))

    def show(self) -> None:
        if not self.window.winfo_exists():
            return
        self.window.deiconify()
        self.window.lift()
        self.window.focus_force()

    def restore(self) -> None:
        if not self.window.winfo_exists():
            return
        self.window.deiconify()
        self.window.state('normal')
        self.window.lift()
        self.window.focus_force()

    def minimize(self) -> None:
        if self.window.winfo_exists():
            self._cancel_map_key()
            self.window.deiconify()
            self.window.iconify()

    def maximize(self) -> None:
        if self.window.winfo_exists():
            self.window.deiconify()
            self.window.state('zoomed')
            self.window.lift()

    def hide(self) -> None:
        if self._closed or getattr(self.assistant, "closed", False) or not self.window.winfo_exists():
            return
        self._cancel_map_key()
        self._close_help_images()
        # The controller directly withdraws/iconifies the existing window;
        # it must not call this view's hide again.
        self.assistant._hide_desktop_main_ui()


def attach_desktop_main_ui(assistant) -> DesktopMainUI:
    """One-call integration seam after ``DailyAssistant._build_ui``.

    Right-click is additive: left click still opens the existing quick panel.
    The menu controls the desktop window; Close calls the production
    confirmation/cleanup chain, never ``root.destroy`` directly.
    """
    ui = DesktopMainUI(assistant)
    menu = tk.Menu(assistant.orb_canvas, tearoff=False)
    menu.add_command(label="当前模式：", state="disabled")
    menu.add_separator()
    menu.add_command(label="还原", command=ui.restore)
    menu.add_command(label="最小化", command=ui.minimize)
    menu.add_command(label="最大化", command=ui.maximize)
    menu.add_separator()
    menu.add_command(label="关闭星图…", command=assistant.request_close)

    def popup(event) -> None:
        try:
            menu.tk_popup(event.x_root, event.y_root)
        finally:
            menu.grab_release()

    assistant.orb_canvas.bind("<Button-3>", popup, add="+")
    ui.orb_menu = menu
    assistant.desktop_main_ui = ui
    return ui
