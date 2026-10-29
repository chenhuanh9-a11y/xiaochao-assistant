"""Draft-only game-control layout editor; runtime commands stay disabled in preview."""
from __future__ import annotations

import tkinter as tk

from control_layout import (MODULE_LABELS, DISPLAY_OPTIONS, copy_layout,
                            layout_preset, preset_layout, validate_layout)
from desktop_display import WindowDisplay
from desktop_rounded import RoundedButton
from desktop_theme import BG, SURFACE, TEXT, MUTED, FONT, PRIMARY, LINE, BUTTON_BG, BUTTON_TEXT
from game_control_view import GameControlView


class ControlLayoutEditor(tk.Toplevel):
    def __init__(self, owner, controller):
        super().__init__(owner, bg=BG)
        self.withdraw()
        self.title('自定义游戏控件')
        self.controller = controller
        self.draft = copy_layout(controller.control_layout_snapshot())
        self._snapshot = controller._route_presentation_snapshot()
        self._compact = False
        self._show_preview = False
        self._rendering = False
        self.protocol('WM_DELETE_WINDOW', self.destroy)
        self.bind('<Escape>', lambda _event: self.destroy())
        self.bind('<Destroy>', self._closed, add='+')
        header = tk.Frame(self, bg=BG)
        header.pack(fill='x', padx=16, pady=(14, 8))
        self._label(header, '自定义游戏控件', bold=True).pack(side='left')
        self.preview_button = self._button(header, '预览', self.toggle_preview)
        self.preview_button.pack(side='right')
        self._label(self, '状态、收起和更多入口始终保留；这里只调整布局。', color=MUTED).pack(
            fill='x', padx=16, pady=(0, 8))
        presets = tk.Frame(self, bg=BG)
        presets.pack(fill='x', padx=16, pady=(0, 8))
        self._label(presets, '预设', color=MUTED).pack(side='left', padx=(0, 8))
        self.preset_buttons = {}
        for key, title in (('compact', '精简'), ('common', '常用')):
            button = self._button(presets, title, lambda name=key: self.set_preset(name))
            button.pack(side='left', padx=(0, 6))
            self.preset_buttons[key] = button

        footer = tk.Frame(self, bg=BG)
        footer.pack(side='bottom', fill='x', padx=16, pady=12)
        self.apply_button = self._button(footer, '应用', self.apply, primary=True)
        self.apply_button.pack(side='right')
        self.cancel_button = self._button(footer, '取消', self.destroy)
        self.cancel_button.pack(side='right', padx=8)
        self.restore_button = self._button(footer, '恢复默认', lambda: self.set_preset('compact'))
        self.restore_button.pack(side='left')
        self.feedback = self._label(self, '', color=MUTED)
        self.feedback.pack(side='bottom', fill='x', padx=16)
        self.body = tk.Frame(self, bg=BG)
        self.body.pack(fill='both', expand=True, padx=16)
        self.body.columnconfigure(0, weight=1)
        self.body.rowconfigure(0, weight=1)
        self.options = tk.Frame(self.body, bg=SURFACE)
        self.options.grid(row=0, column=0, sticky='nsew', padx=(0, 12))
        self.option_canvas = tk.Canvas(self.options, bg=SURFACE, highlightthickness=0, width=260)
        scrollbar = tk.Scrollbar(self.options, orient='vertical', command=self.option_canvas.yview)
        scrollbar.pack(side='right', fill='y')
        self.option_canvas.pack(side='left', fill='both', expand=True)
        self.option_canvas.configure(yscrollcommand=scrollbar.set)
        self.option_content = tk.Frame(self.option_canvas, bg=SURFACE)
        self._option_window = self.option_canvas.create_window(0, 0, anchor='nw', window=self.option_content)
        self.option_content.bind('<Configure>', self._content_changed)
        self.option_canvas.bind('<Configure>', lambda event: self.option_canvas.itemconfigure(
            self._option_window, width=event.width))
        self.option_canvas.bind('<MouseWheel>', self._wheel)

        self.preview_host = tk.Frame(self.body, bg=BG)
        self.preview_host.grid(row=0, column=1, sticky='n')
        self.preview = GameControlView(self.preview_host, commands={})
        self.preview.pack()
        self._label(self.preview_host, '布局预览 · 不会执行游戏操作', color=MUTED).pack(pady=8)
        self._enabled = {key: tk.BooleanVar(self, value=key in self.draft['enabled']) for key in MODULE_LABELS}
        self._display = {key: tk.BooleanVar(self, value=value) for key, value in self.draft['display'].items()}
        self.rows = {}
        self.up_buttons, self.down_buttons = {}, {}
        for key, title in MODULE_LABELS.items():
            row = tk.Frame(self.option_content, bg=SURFACE)
            self.rows[key] = row
            main = tk.Frame(row, bg=SURFACE)
            main.pack(fill='x', padx=10, pady=(8, 4))
            tk.Checkbutton(main, text=title, variable=self._enabled[key], command=self._change,
                font=(FONT, 10), bg=SURFACE, fg=TEXT, activebackground=SURFACE,
                selectcolor=SURFACE).pack(side='left')
            self.down_buttons[key] = self._button(main, '下移', lambda name=key: self.move(name, 1))
            self.down_buttons[key].pack(side='right')
            self.up_buttons[key] = self._button(main, '上移', lambda name=key: self.move(name, -1))
            self.up_buttons[key].pack(side='right', padx=4)
            if key == 'display':
                for option, label in DISPLAY_OPTIONS.items():
                    tk.Checkbutton(row, text=label, variable=self._display[option], command=self._change,
                        font=(FONT, 9), bg=SURFACE, fg=TEXT, activebackground=SURFACE,
                        selectcolor=SURFACE).pack(anchor='w', padx=26)
            tk.Frame(row, bg=LINE, height=1).pack(fill='x', padx=12, pady=(8, 0))
        self.binding_label = self._label(self.option_content, '', color=MUTED)
        self.binding_label.pack(fill='x', padx=12, pady=12)
        self._bind_option_wheel(self.option_content)
        self._sync_draft()
        self.display = WindowDisplay(self, controller, preferred=(800, 620), minimum=(420, 400),
                                     on_scale=self._scaled)
        self.bind('<Configure>', self._configured, add='+')
        self.deiconify()
        self.display.start(owner)

    def _label(self, parent, text, *, bold=False, color=TEXT):
        label = tk.Label(parent, text=text, bg=parent.cget('bg'), fg=color, anchor='w', justify='left',
                         font=(FONT, 10, 'bold' if bold else 'normal'), wraplength=400)
        def fit(event):
            if event.width > 1 and int(label.cget('wraplength')) != event.width:
                label.configure(wraplength=event.width)
        label.bind('<Configure>', fit)
        return label

    def _button(self, parent, text, callback, *, primary=False):
        return RoundedButton(parent, text=text, command=callback, font=(FONT, 9), padx=10, pady=7,
            bg=PRIMARY if primary else BUTTON_BG, fg='white' if primary else BUTTON_TEXT,
            activebackground=PRIMARY if primary else BUTTON_BG, highlightthickness=0)

    def _content_changed(self, _event=None):
        self.option_canvas.configure(scrollregion=self.option_canvas.bbox('all'))

    def _closed(self, event):
        if event.widget is self and getattr(self.controller, '_control_layout_editor', None) is self:
            self.controller._control_layout_editor = None

    def _wheel(self, event):
        self.option_canvas.yview_scroll(-int(event.delta / 120), 'units')
        return 'break'

    def _bind_option_wheel(self, widget):
        widget.bind('<MouseWheel>', self._wheel, add='+')
        for child in widget.winfo_children():
            self._bind_option_wheel(child)

    def _change(self):
        self.draft['enabled'] = [key for key in self.draft['order'] if self._enabled[key].get()]
        self.draft['display'] = {key: variable.get() for key, variable in self._display.items()}
        self._sync_draft()

    def move(self, key, step):
        index = self.draft['order'].index(key)
        target = index + step
        if 0 <= target < len(self.draft['order']):
            self.draft['order'][index], self.draft['order'][target] = self.draft['order'][target], key
            self._change()

    def set_preset(self, name):
        self.draft = preset_layout(name)
        for key, variable in self._enabled.items(): variable.set(key in self.draft['enabled'])
        for key, variable in self._display.items(): variable.set(self.draft['display'][key])
        self._sync_draft()

    def _sync_draft(self):
        self.draft = validate_layout(self.draft)
        for index, key in enumerate(self.draft['order']):
            self.rows[key].pack_forget()
            self.rows[key].pack(fill='x', before=self.binding_label)
            self.up_buttons[key].configure(state='normal' if index else 'disabled')
            self.down_buttons[key].configure(state='normal' if index < len(self.rows) - 1 else 'disabled')
        preset = layout_preset(self.draft)
        for key, button in self.preset_buttons.items():
            button.configure(text=('精简' if key == 'compact' else '常用') + (' · 已选' if key == preset else ''))
        self.preview.apply_layout(self.draft)
        self.refresh_snapshot(self._snapshot)

    def refresh_snapshot(self, snapshot):
        if self._rendering:
            return
        self._rendering = True
        try:
            self._snapshot = dict(snapshot)
            key = GameControlView._binding_text(snapshot.get('bindings', {}), 'toggle-panel')
            self.binding_label.configure(text=f'呼出/隐藏控件：{key or "未绑定"}\n在主页设置中修改快捷键。')
            # Preview only: never collapse it or execute current round commands.
            self.preview.render(dict(snapshot, controlCollapsed=False))
        finally:
            self._rendering = False

    def _scaled(self, scale):
        self.preview.set_display_scale(scale)
        self._reflow()

    def _configured(self, event):
        if event.widget is self and hasattr(self, 'display'):
            self._reflow()

    def _reflow(self):
        compact = self.winfo_width() < round(760 * self.display.scale)
        self._compact = compact
        if compact:
            self.body.columnconfigure(1, weight=0)
            if self._show_preview:
                self.options.grid_remove()
                self.preview_host.grid(row=0, column=0, sticky='n')
            else:
                self.preview_host.grid_remove()
                self.options.grid(row=0, column=0, sticky='nsew', padx=0)
            self.preview_button.configure(text='返回设置' if self._show_preview else '预览')
            self.preview_button.pack(side='right')
        else:
            self.options.grid(row=0, column=0, sticky='nsew', padx=(0, 12))
            self.preview_host.grid(row=0, column=1, sticky='n')
            self.preview_button.pack_forget()
        self.preview.set_available_height(max(1, self.body.winfo_height() - round(40 * self.display.scale)))

    def toggle_preview(self):
        self._show_preview = not self._show_preview
        self._reflow()

    def apply(self):
        result = self.controller.apply_control_layout(copy_layout(self.draft))
        if result.get('success'):
            self.destroy()
        else:
            self.feedback.configure(text=result.get('message') or '未能应用布局，请重试')
        return result
