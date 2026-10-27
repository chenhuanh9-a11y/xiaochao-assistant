"""Welcome view only. The runtime retains the two-step/save/dismiss lifecycle."""
from __future__ import annotations

import tkinter as tk
from desktop_main_ui import BG, SURFACE, CARD, LINE, TEXT, MUTED, GOLD, FONT, _button, HotkeyEditorView
from desktop_theme import ERROR, PRIMARY
from desktop_rounded import RoundedFrame


def _label(parent, text="", *, size=10, color=TEXT, bold=False, **kwargs):
    return tk.Label(parent, text=text, bg=parent.cget("bg"), fg=color,
                    font=(FONT, size, "bold" if bold else "normal"), **kwargs)


class ControlTrialView(tk.Frame):
    """Observe the controller's real visibility trial; never count clicks."""

    def __init__(self, parent, assistant):
        super().__init__(parent, bg=parent.cget("bg"))
        self.assistant = assistant
        self.visible_label = _label(self, "控件状态未知", size=10, bold=True, anchor="w")
        self.visible_label.pack(fill="x")
        self.shown_label = _label(self, "显示一次：未完成", size=10, anchor="w")
        self.shown_label.pack(fill="x", pady=(6, 0))
        self.hidden_label = _label(self, "隐藏一次：未完成", size=10, anchor="w")
        self.hidden_label.pack(fill="x", pady=(4, 0))
        actions = tk.Frame(self, bg=self.cget("bg"))
        actions.pack(fill="x", pady=(10, 6))
        self.show_button = _button(actions, "用鼠标显示", lambda: self._try(True))
        self.show_button.pack(side="left")
        self.hide_button = _button(actions, "用鼠标隐藏", lambda: self._try(False))
        self.hide_button.pack(side="left", padx=8)
        self.feedback = _label(self, size=9, color=GOLD, anchor="w", justify="left", wraplength=460)
        self.feedback.pack(fill="x")
        self.bind("<Map>", lambda event: self.render() if event.widget is self else None, add="+")
        self.render()

    def _try(self, visible):
        callback = getattr(self.assistant, "set_control_visible", None)
        result = None
        if callable(callback):
            result = callback(visible, source="mouse")
        self.render()
        if isinstance(result, dict) and result.get("message"):
            self.feedback.configure(text=str(result["message"]))

    def render(self, snapshot=None):
        getter = getattr(self.assistant, "onboarding_trial_snapshot", None)
        snapshot = snapshot if snapshot is not None else getter() if callable(getter) else {}
        visible = snapshot.get("panelVisible")
        self.visible_label.configure(text="控件已显示" if visible is True else "控件已隐藏" if visible is False else "控件状态未知")
        for label, flag, source, title in (
            (self.shown_label, "shown", "shownSource", "显示一次"),
            (self.hidden_label, "hidden", "hiddenSource", "隐藏一次"),
        ):
            origin = {"key": "快捷键", "keyboard": "快捷键", "mouse": "鼠标兜底"}.get(snapshot.get(source), "来源未知")
            label.configure(text=f"{title}：已完成（{origin}）" if snapshot.get(flag) is True else f"{title}：未完成")
        self.show_button.configure(state="normal" if snapshot.get("canShow") is True else "disabled")
        self.hide_button.configure(state="normal" if snapshot.get("canHide") is True else "disabled")
        self.feedback.configure(text=snapshot.get("message") or "用快捷键显示一次，再隐藏一次。录制期间不计入试用。")


def build_onboarding(assistant):
    """Build the existing widget references, without owning settings or stages."""
    assistant.onboarding_accent_color = PRIMARY
    window = tk.Toplevel(assistant.root)
    assistant.onboarding_window = window
    window.withdraw()
    window.title("准备快捷键 · 星图")
    window.resizable(True, True)
    window.attributes("-topmost", True)
    window.configure(bg=BG)
    window.protocol("WM_DELETE_WINDOW", assistant._dismiss_onboarding)
    window.bind("<Escape>", assistant._dismiss_onboarding)
    content = assistant._make_scrollable_surface(window, bg=BG)
    shell = tk.Frame(content, bg=SURFACE)
    shell.pack(fill="both", expand=True, padx=12, pady=12)
    hero = tk.Frame(shell, bg=SURFACE)
    hero.pack(fill="x", padx=26, pady=(22, 10))
    _label(hero, "准备快捷键", size=18, bold=True, anchor="w").pack(fill="x")
    _label(hero, "先核对地图键，再试用游戏控件显隐。完成后进入主页面。",
           size=9, color=MUTED, anchor="w", justify="left", wraplength=490).pack(fill="x", pady=(7, 0))

    key = RoundedFrame(shell, bg=CARD, highlightthickness=1, highlightbackground=LINE)
    assistant.onboarding_map_card = key
    assistant.onboarding_hotkey_card = key
    _label(key, "游戏地图键", size=12, bold=True, anchor="w").pack(fill="x", padx=16, pady=(14, 6))
    _label(key, "与游戏地图键保持一致；点击键位可更改。",
           size=9, color=MUTED, anchor="w", justify="left", wraplength=450).pack(fill="x", padx=16, pady=(0, 12))
    editor = HotkeyEditorView(key, assistant, owner=window, targets=("map",),
                              confirm_inline=False, allow_clear=False)
    editor.pack(fill="x", padx=16, pady=(0, 14))
    assistant.onboarding_hotkey_editor = editor
    assistant.onboarding_map_editor = editor
    assistant.onboarding_hotkey_entry = editor.entries["map"]
    assistant.onboarding_hotkey_capture_button = editor.edit_buttons["map"]

    panel = RoundedFrame(shell, bg=CARD, highlightthickness=1, highlightbackground=LINE)
    assistant.onboarding_panel_card = panel
    _label(panel, "试用游戏控件", size=12, bold=True, anchor="w").pack(fill="x", padx=16, pady=(14, 6))
    _label(panel, "用当前快捷键显示一次，再隐藏一次。",
           size=9, color=MUTED, anchor="w", justify="left", wraplength=450).pack(fill="x", padx=16, pady=(0, 12))
    panel_editor = HotkeyEditorView(panel, assistant, owner=window, targets=("toggle-panel",),
                                    confirm_inline=False, allow_clear=False)
    panel_editor.pack(fill="x", padx=16, pady=(0, 12))
    assistant.onboarding_panel_editor = panel_editor
    trial = ControlTrialView(panel, assistant)
    trial.pack(fill="x", padx=16, pady=(0, 14))
    assistant.onboarding_trial_view = trial
    actions = tk.Frame(shell, bg=SURFACE)
    actions.pack(fill="x", padx=26, pady=(10, 18))
    assistant.onboarding_action_button = _button(actions, "下一步", assistant._advance_onboarding, primary=True)
    assistant.onboarding_action_button.pack(side="right")
    assistant.onboarding_back_button = _button(actions, "上一步", lambda: assistant._set_onboarding_step(1))
    _button(actions, "稍后继续", assistant._dismiss_onboarding).pack(side="left")
    assistant.onboarding_error_var = tk.StringVar(assistant.root)
    _label(shell, textvariable=assistant.onboarding_error_var, size=9, color=ERROR,
           anchor="w", justify="left", wraplength=490).pack(fill="x", padx=26, pady=(0, 12))
    assistant._set_onboarding_step(1)
