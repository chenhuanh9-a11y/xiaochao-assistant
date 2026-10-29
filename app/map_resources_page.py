"""Author-first resource page. Browsing is separate from runtime selection.

The established browser owns safe transfer actions and bounded preview decoding;
this composition changes their presentation, not import/download authorization.
"""
from __future__ import annotations

from collections import OrderedDict
from concurrent.futures import CancelledError, ThreadPoolExecutor
from pathlib import Path
import threading
import tkinter as tk

from desktop_rounded import RoundedCombobox, RoundedFrame
from desktop_theme import BG, CARD, LINE, TEXT, MUTED, PRIMARY, BUTTON_BG
from map_resource_browser import MapResourceBrowser, _label, _button
from map_resource_context import load_browse_context
from map_resource_model import choose_resource, floor_label


class MapResourcesPage(MapResourceBrowser):
    def __init__(self, parent, assistant):
        self.browse_mode = getattr(assistant, 'game_mode', 'nightmare')
        self._browse_context = None
        self._browse_epoch = 0
        self._browse_serial = 0
        self._browse_pending = None
        self._browse_work = None
        self._browse_cache = OrderedDict()
        self._metadata_executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix='resource-metadata')
        self._selected_package = None
        self._tab = 'installed'
        self._detail_open = False
        self._library_signature = None
        self._row_data = {}
        self._card_widgets = {}
        self._card_records = {}
        self._card_context = None
        self._runtime_identity = None
        self._route_snapshot = {}
        self._action_feedback = ''
        self._management_context = None
        self._controls_layout = None
        super().__init__(parent, assistant)

    def _build(self):
        self.heading = tk.Frame(self, bg=BG)
        self.heading.pack(fill='x', padx=20, pady=(18, 10))
        _label(self.heading, '地图资源', size=22, bold=True).pack(side='left')
        self.import_button = _button(self.heading, '导入本地图包', self._choose_map_package)
        self.import_button.pack(side='right')
        toolbar = tk.Frame(self, bg=BG)
        toolbar.pack(fill='x', padx=20, pady=(0, 8))
        self.resource_mode_buttons = {}
        for mode, title in (('nightmare', '噩梦'), ('difficult', '困难')):
            button = _button(toolbar, title, lambda value=mode: self._select_resource_mode(value))
            button.pack(side='left', padx=(0, 6))
            self.resource_mode_buttons[mode] = button
        self.mode_label = _label(toolbar, size=9, color=MUTED)
        self.mode_label.pack(side='left', padx=8)
        tabs = tk.Frame(self, bg=BG)
        tabs.pack(fill='x', padx=20, pady=(0, 8))
        self.tab_buttons = {}
        for key, title in (('discover', '发现'), ('installed', '已安装')):
            button = _button(tabs, title, lambda value=key: self._select_tab(value))
            button.pack(side='left', padx=(0, 8))
            self.tab_buttons[key] = button
        self.catalog_refresh_button = _button(tabs, '刷新本机', self._refresh_catalog)
        self.catalog_refresh_button.pack(side='right')
        self.task_cancel = _button(tabs, '取消', self._cancel_current_task)
        self.task_status = _label(self, size=9, color=MUTED, anchor='w', justify='left', wraplength=640)
        self.task_status.pack(fill='x', padx=20, pady=(0, 6))
        self.content = tk.Frame(self, bg=BG)
        self.content.pack(fill='both', expand=True, padx=20)
        self.content.columnconfigure(0, weight=5)
        self.content.columnconfigure(1, weight=4)
        self.content.rowconfigure(0, weight=1)
        self.list_host = tk.Frame(self.content, bg=BG)
        self.list_host.grid(row=0, column=0, sticky='nsew', padx=(0, 12))
        self.list_canvas = tk.Canvas(self.list_host, bg=BG, highlightthickness=0, width=220, height=1)
        self.list_scroll = tk.Scrollbar(self.list_host, command=self.list_canvas.yview)
        self.list_canvas.configure(yscrollcommand=self.list_scroll.set)
        self.list_scroll.pack(side='right', fill='y')
        self.list_canvas.pack(side='left', fill='both', expand=True)
        self.cards_host = tk.Frame(self.list_canvas, bg=BG)
        self.empty_host = tk.Frame(self.cards_host, bg=BG)
        self.empty_title = _label(self.empty_host, size=13, bold=True, anchor='w', justify='left', wraplength=280)
        self.empty_title.pack(fill='x', pady=(18, 10))
        _label(self.empty_host, '可以查看已安装图包，或导入本地图包。', color=MUTED,
               wraplength=280, justify='left').pack(fill='x', pady=(0, 10))
        _button(self.empty_host, '导入本地图包', self._choose_map_package).pack(anchor='w')
        self.empty_installed = _button(self.empty_host, '查看已安装', lambda: self._select_tab('installed'))
        self.empty_advanced = _button(self.empty_host, '高级：下载服务状态', self._show_source_status)
        self._list_window = self.list_canvas.create_window(0, 0, window=self.cards_host, anchor='nw')
        self.cards_host.bind('<Configure>', lambda _e: self.list_canvas.configure(scrollregion=self.list_canvas.bbox('all')))
        self.list_canvas.bind('<Configure>', self._list_width)
        self.detail_host = tk.Frame(self.content, bg=BG)
        self.detail_host.grid(row=0, column=1, sticky='nsew')
        self.detail_canvas = tk.Canvas(self.detail_host, bg=BG, highlightthickness=0, width=300, height=1)
        self.detail_scroll = tk.Scrollbar(self.detail_host, command=self.detail_canvas.yview)
        self.detail_canvas.configure(yscrollcommand=self.detail_scroll.set)
        self.detail_scroll.pack(side='right', fill='y')
        self.detail_canvas.pack(side='left', fill='both', expand=True)
        self.detail_body = tk.Frame(self.detail_canvas, bg=CARD)
        self._detail_window = self.detail_canvas.create_window(0, 0, window=self.detail_body, anchor='nw')
        self.detail_body.bind('<Configure>', lambda _e: self.detail_canvas.configure(scrollregion=self.detail_canvas.bbox('all')))
        self.detail_canvas.bind('<Configure>', self._detail_width)
        self.back_button = _button(self.detail_body, '返回列表', self._back_to_list)
        self.detail_title = _label(self.detail_body, '选择一个图包', size=14, bold=True, anchor='w')
        self.detail_title.pack(fill='x', padx=16, pady=(14, 8))
        self.detail_image = _label(self.detail_body, '导入后可预览作者地图', color=MUTED, anchor='center', justify='center')
        self.detail_image.configure(height=10, bg='#edf1f7')
        self.detail_image.pack(fill='x', padx=16, pady=(0, 8))
        selectors = tk.Frame(self.detail_body, bg=CARD)
        selectors.pack(fill='x', padx=16, pady=(0, 8))
        self.map_var, self.floor_var = tk.StringVar(self), tk.StringVar(self)
        self.map_box = RoundedCombobox(selectors, textvariable=self.map_var, state='readonly', width=15)
        self.floor_box = RoundedCombobox(selectors, textvariable=self.floor_var, state='readonly', width=8)
        self.map_box.pack(side='left', fill='x', expand=True)
        self.floor_box.pack(side='left', padx=(6, 0))
        self.map_box.bind('<<ComboboxSelected>>', self._map_changed)
        self.floor_box.bind('<<ComboboxSelected>>', lambda _e: self._request_detail())
        self.coverage_label = _label(self.detail_body, color=MUTED, size=9, anchor='w', justify='left', wraplength=400)
        self.coverage_label.pack(fill='x', padx=16, pady=(0, 6))
        self.version_label = _label(self.detail_body, color=MUTED, size=9, anchor='w', wraplength=400)
        self.version_label.pack(fill='x', padx=16, pady=(0, 8))
        self.primary_button = _button(self.detail_body, '使用此作者', self._primary_action)
        self.primary_button.pack(fill='x', padx=16, pady=(0, 6))
        actions = tk.Frame(self.detail_body, bg=CARD)
        actions.pack(fill='x', padx=16, pady=(0, 8))
        self.update_button = _button(actions, '更新图包', self._download_selected)
        self.update_button.pack(side='left')
        self.manage_button = _button(actions, '管理 / 修复', self._manage_selected)
        self.manage_button.pack(side='left', padx=(6, 0))
        self.selection_note = _label(self.detail_body, size=9, color=MUTED, anchor='w', justify='left', wraplength=400)
        self.selection_note.pack(fill='x', padx=16, pady=(0, 8))
        self.operation_host = tk.Frame(self.detail_body, bg=CARD)
        self.operation_host.pack(fill='x', padx=16, pady=(0, 10))
        # Existing action surfaces and callbacks remain the only mutation route.
        self._build_operation_surfaces()
        self.managed_cancel_button = _button(self.managed_surface, '取消', self._cancel_managed_confirmation)
        self.filter_host = self.subtitle = self.content  # Transfer placement anchor (same parent).
        self.online_bar_status = self.task_status
        self.online_feedback.configure(bg=CARD)
        self._runtime_route_signature = None
        self._selected_detail_signature = None
        self.footer = tk.Frame(self, bg=BG)
        self.footer.pack(fill='x', padx=20, pady=(8, 12))
        self.actual_selection = _label(self.footer, color=TEXT, size=9, anchor='w')
        self.actual_selection.pack(fill='x')
        self._bind_local_wheel(self.cards_host, self.list_canvas)
        self._bind_local_wheel(self.detail_body, self.detail_canvas)

    def _bind_local_wheel(self, widget, canvas):
        if (not isinstance(widget, (tk.Scale, RoundedCombobox))
                and getattr(widget, '_map_page_wheel_canvas', None) is not canvas):
            widget.bind('<MouseWheel>', lambda event: canvas.yview_scroll(-int(event.delta / 120), 'units') or 'break')
            widget._map_page_wheel_canvas = canvas
        for child in widget.winfo_children():
            self._bind_local_wheel(child, canvas)

    def _detail_width(self, event):
        self.detail_canvas.itemconfigure(self._detail_window, width=event.width)
        for label in (self.coverage_label, self.version_label, self.selection_note):
            label.configure(wraplength=max(100, event.width - round(32*self._scale)))
        self._cancel(self._resize_job)
        self._resize_job = self._schedule(100, self._request_detail)

    def _list_width(self, event):
        self.list_canvas.itemconfigure(self._list_window, width=event.width)
        self._layout_author_cards()

    def _layout_author_cards(self):
        columns = 2 if self.list_canvas.winfo_width() / max(.5, self._scale) >= 420 else 1
        for column in (0, 1):
            self.cards_host.columnconfigure(column, weight=1 if column < columns else 0, uniform='authors')
        for index, frame in enumerate(getattr(self, '_author_frames', ())):
            frame.grid(row=index // columns, column=index % columns, sticky='new',
                       padx=(0, 8 if columns == 2 and index % 2 == 0 else 0), pady=(0, 10))

    def _layout_controls(self):
        if not hasattr(self, 'detail_host'):
            return
        compact = self.winfo_width() / max(.5, self._scale) < 720
        layout = compact, self._detail_open
        if layout == self._controls_layout:
            return
        self._controls_layout = layout
        if compact:
            if self._detail_open:
                self.list_host.grid_remove()
                self.detail_host.grid(row=0, column=0, columnspan=2, sticky='nsew')
                self.back_button.pack(fill='x', padx=16, pady=(8, 0), before=self.detail_title)
            else:
                self.detail_host.grid_remove()
                self.list_host.grid(row=0, column=0, columnspan=2, sticky='nsew', padx=0)
        else:
            self.back_button.pack_forget()
            self.list_host.grid(row=0, column=0, columnspan=1, sticky='nsew', padx=(0, 12))
            self.detail_host.grid(row=0, column=1, columnspan=1, sticky='nsew')

    def set_display_scale(self, scale):
        if scale == self._scale:
            return
        self._scale = scale
        self._cards_need_metrics = True
        self._library_signature = None
        self._layout_controls()
        if self._active:
            self._render_library()

    def _resource_root(self):
        root = (getattr(self.assistant, 'nightmare_resource_root', None)
                or getattr(getattr(self.assistant, 'pack', None), 'resource_root', None))
        if root is None:
            raise ValueError('无法定位安装资源目录')
        return Path(root)

    def refresh(self):
        self._active = True
        identity = (id(getattr(self.assistant, 'pack', None)), id(getattr(self.assistant, 'author_route_catalog', None)))
        if self._runtime_identity is not None and identity != self._runtime_identity:
            self.invalidate_resource_context()
        self._runtime_identity = identity
        self.sync_map_package_import()
        self.sync_online_packages()
        self.sync_managed_packages()
        self.sync_route_selection()
        if self._browse_context is None:
            self._queue_context()
        self._render_library()

    def _queue_context(self):
        if self._closed:
            return
        try:
            root = self._resource_root()
        except ValueError as error:
            self.task_status.configure(text=str(error))
            return
        key = (str(root), self.browse_mode, self._browse_epoch)
        work = self._browse_work
        if ((work is not None and work[1] == key and not work[2].is_set() and self._browse_pending is None)
                or (self._browse_pending is not None and self._browse_pending[1] == key)):
            return
        self._browse_serial += 1
        if self._browse_work is not None:
            self._browse_work[2].set()
        if key in self._browse_cache:
            self._browse_pending = None
            self._browse_context = self._browse_cache[key]
            self.model = self._browse_context.model
            self._library_signature = None
            return
        self._browse_pending = (self._browse_serial, key, root)
        self._start_context()

    def _start_context(self):
        if self._closed or not self._active or self._browse_work is not None or self._browse_pending is None:
            return
        serial, key, root = self._browse_pending
        self._browse_pending = None
        cancel = threading.Event()
        future = self._metadata_executor.submit(load_browse_context, root, key[1], cancelled=cancel.is_set)
        self._browse_work = serial, key, cancel, future

    def _poll_context(self):
        work = self._browse_work
        if work is not None and work[3].done():
            self._browse_work = None
            serial, key, cancel, future = work
            try:
                context = future.result()
            except CancelledError:
                context = None
            except Exception as error:
                context = None
                if serial == self._browse_serial and self._active:
                    self.task_status.configure(text=f'资源读取失败：{error}')
            if (context is not None and not cancel.is_set() and serial == self._browse_serial
                    and self._active and not self._closed):
                self._browse_cache[key] = context
                while len(self._browse_cache) > 2:
                    self._browse_cache.popitem(last=False)
                self._browse_context, self.model = context, context.model
                self._library_signature = None
                self._render_library()
            self._start_context()

    def _select_resource_mode(self, mode):
        if mode not in {'nightmare', 'difficult'} or self._closed or mode == self.browse_mode:
            return
        self._cancel_managed_confirmation()
        self.browse_mode = mode
        self._selected_package = None
        self._browse_context = self.model = None
        self._generation += 1
        self._cancel_preview_work()
        self._queue_context()
        self._library_signature = None
        self._render_library()

    def _select_tab(self, tab):
        if tab != self._tab:
            self._cancel_managed_confirmation()
        self._tab = tab
        self._detail_open = False
        self._library_signature = None
        self._render_library()

    def _refresh_catalog(self):
        if self._tab == 'discover':
            self._check_online_packages()
        else:
            self.invalidate_resource_context()
            getter = getattr(self.assistant, 'managed_author_packages_snapshot', None)
            if callable(getter): getter(refresh=True)

    def _package_rows(self):
        summaries = {row.package_id: row for row in getattr(self._browse_context, 'profiles', ()) if row.package_id}
        online_state = self._online_snapshot()
        online = {row['packageId']: row for row in online_state.get('packages', ()) if isinstance(row, dict) and row.get('packageId')}
        state = self.assistant.managed_author_packages_snapshot() if callable(getattr(self.assistant, 'managed_author_packages_snapshot', None)) else {}
        managed = {row['packageId']: row for row in state.get('packages', ()) if isinstance(row, dict) and row.get('packageId')}
        keys = set(online) if self._tab == 'discover' else set(summaries) | set(managed)
        result = {}
        for key in sorted(keys):
            if not key.startswith(self.browse_mode + '-'):
                continue
            profile, remote, local = summaries.get(key), online.get(key), managed.get(key, {})
            has_images = bool(profile and profile.available_layers)
            installed = has_images or local.get('status') not in {None, '', 'missing', 'not-installed'}
            if self._tab == 'installed' and not installed:
                continue
            title = (profile.name if profile else local.get('title') or (remote or {}).get('title') or key)
            status = ((remote or {}).get('installation') or {}).get('status') or local.get('status')
            if online_state.get('selectedPackageId') == key:
                if online_state.get('busy'): status = 'downloading'
                elif online_state.get('stage') == 'error': status = 'download-error'
            result[key] = dict(key=key, title=title, profile=profile, remote=remote, local=local,
                               installed=installed, status=status)
        return result

    def _render_library(self):
        if self._closed or not self._active:
            return
        rows = self._package_rows()
        signature = (self.browse_mode, self._tab, id(self._browse_context), self._scale,
            tuple((key, row['title'], row['status'], row['installed'], str((row['remote'] or {}).get('revision'))) for key, row in rows.items()))
        self._row_data = rows
        if self._selected_package not in rows:
            self._selected_package = next(iter(rows), None)
        if signature != self._library_signature:
            self._library_signature = signature
            if self._card_context != id(self._browse_context):
                self._card_context = id(self._browse_context)
                for record in self._card_records.values(): record['frame'].destroy()
                self._card_records.clear()
                self._card_widgets.clear()
            for record in self._card_records.values(): record['frame'].grid_remove()
            self._author_frames = []
            self._author_cards = {}
            if not rows:
                message = ('在线图包暂不可用' if self._tab == 'discover' else
                           '正在读取本机资源…' if self._browse_work else '尚未导入作者图包')
                if self._browse_context is not None and self._browse_context.error:
                    message = self._browse_context.error
                self.empty_title.configure(text=message)
                self.empty_host.grid(row=0, column=0, columnspan=2, sticky='ew')
                if self._tab == 'discover':
                    self.empty_installed.pack(anchor='w', pady=8)
                    self.empty_advanced.pack(anchor='w', pady=8)
                else:
                    self.empty_installed.pack_forget(); self.empty_advanced.pack_forget()
            else:
                self.empty_host.grid_remove()
            for key, row in rows.items():
                record = self._card_records.get(key)
                if record is None:
                    record = self._make_author_card(key)
                    self._card_records[key] = record
                card, image = record['frame'], record['image']
                self._author_frames.append(card)
                self._author_cards[key] = card
                card.configure(highlightbackground=PRIMARY if key == self._selected_package else LINE)
                record['title'].configure(text=row['title'])
                record['status'].configure(text=self._status_text(row))
                profile = row['profile']
                coverage = f'本地覆盖 {profile.available_maps}/{profile.map_count} 张' if profile and not profile.error else '覆盖待资源检查'
                record['coverage'].configure(text=coverage)
                self._card_widgets[key] = image
                if profile and profile.first_preview:
                    self._request(profile.first_preview[2], (round(250*self._scale), round(150*self._scale)), image)
            self._layout_author_cards()
            display = getattr(self.winfo_toplevel(), '_desktop_display', None)
            if display and getattr(self, '_cards_need_metrics', False):
                display.apply_widgets(self.cards_host)
                self._cards_need_metrics = False
        for mode, button in self.resource_mode_buttons.items():
            button.configure(bg=PRIMARY if mode == self.browse_mode else BUTTON_BG,
                             fg='white' if mode == self.browse_mode else TEXT)
        for tab, button in self.tab_buttons.items():
            button.configure(bg=PRIMARY if tab == self._tab else BUTTON_BG,
                             fg='white' if tab == self._tab else TEXT)
        count = len(self._browse_context.pack.maps) if self._browse_context and self._browse_context.pack else None
        self.mode_label.configure(text='仅浏览' + (f' · {count} 张' if count is not None else ''))
        self._show_selected()
        self._layout_controls()

    def _make_author_card(self, key):
        frame = RoundedFrame(self.cards_host, bg=CARD, padx=12, pady=12,
                             highlightbackground=LINE, highlightthickness=1)
        image = _label(frame, '安装后可预览作者图', color=MUTED, anchor='center', justify='center')
        image.configure(bg='#edf1f7', height=6); image.pack(fill='x')
        image.bind('<Button-1>', lambda _event: self._select_package(key))
        title = _label(frame, bold=True, anchor='w', wraplength=260)
        title.pack(fill='x', pady=(8, 4))
        status = _label(frame, color=MUTED, size=9, anchor='w');status.pack(fill='x')
        coverage = _label(frame, color=MUTED, size=9, anchor='w');coverage.pack(fill='x', pady=(4, 6))
        _button(frame, '查看图包', lambda: self._select_package(key)).pack(anchor='w')
        self._cards_need_metrics = True
        self._bind_local_wheel(frame, self.list_canvas)
        return dict(frame=frame, image=image, title=title, status=status, coverage=coverage)

    @staticmethod
    def _status_text(row):
        return {'installed': '已安装', 'update-available': '已安装 · 可更新', 'incomplete': '需要修复',
                'modified': '本地有修改', 'unregistered': '本地已有资源', 'unmanaged': '旧资源待核实',
                'unverified': '待检查', 'error': '检查失败', 'downloading': '下载中',
                'download-error': '下载未完成 · 可重试'}.get(
                    row['status'], '已在本机' if row['installed'] else '未安装')

    def _select_package(self, key):
        if key not in self._row_data: return
        if key != self._selected_package:
            self._cancel_managed_confirmation()
        self._selected_package = key
        self._managed_signature = None
        for value, card in getattr(self, '_author_cards', {}).items():
            card.configure(highlightbackground=PRIMARY if value == key else LINE)
        self._detail_open = True
        self._show_selected()
        self._layout_controls()

    def _back_to_list(self):
        self._detail_open = False
        self._layout_controls()

    def _show_selected(self):
        row = self._row_data.get(self._selected_package)
        if row is None:
            self._selected_detail_signature = None
            self._map_items = self._map_ids = self._floor_keys = ()
            self.detail_title.configure(text='选择一个图包')
            self.detail_image.configure(image='', text='作者地图将在这里预览', height=10)
            self.detail_image._image_request = self.detail_image._displayed_image_request = None
            self.detail_image._map_photo = None
            self.map_box.configure(values=(), state='disabled'); self.map_var.set('暂无地图预览')
            self.floor_box.configure(values=(), state='disabled'); self.floor_var.set('无楼层')
            self.coverage_label.configure(text=''); self.version_label.configure(text='')
            self.primary_button.configure(state='disabled')
            self.update_button.configure(state='disabled'); self.manage_button.configure(state='disabled')
            return
        signature = (self._selected_package, id(self._browse_context), self._scale,
                     row['status'], str(row['remote']))
        if signature == self._selected_detail_signature:
            self._sync_selected_actions()
            return
        self._selected_detail_signature = signature
        self.detail_title.configure(text=f"{row['title']} · 预览")
        profile = row['profile']
        items = tuple(profile.maps) if profile else ()
        old = getattr(self, '_map_ids', ())
        selected_map = old[self.map_box.current()] if 0 <= self.map_box.current() < len(old) else None
        self._map_items = items
        self._map_ids = tuple(item.id for item in items)
        self.map_box.configure(values=tuple(item.name for item in items), state='readonly' if items else 'disabled')
        if items:
            self.map_box.current(self._map_ids.index(selected_map) if selected_map in self._map_ids else 0)
        else:
            self.map_var.set('暂无地图预览')
        self._map_changed()
        if profile and not profile.error:
            coverage = f'本地覆盖 {profile.available_maps}/{profile.map_count} 张'
            partial = 0
            for item in items:
                present = {floor for variant in item.variants for floor, path in variant.layers.items()
                           if self._browse_context.path_stamps.get(Path(path)) is not None}
                if present:
                    partial += len(set(self._browse_context.pack.by_id[item.id].layers) - present)
            if profile.missing_maps: coverage += f' · 缺 {profile.missing_maps} 张地图'
            if partial: coverage += f'\n已覆盖地图仍缺 {partial} 个楼层'
        else:
            coverage = profile.error if profile else '图包尚未安装，覆盖信息以检查结果为准'
        remote = row['remote'] or {}
        if row['status'] == 'update-available': coverage += '\n更新包覆盖信息待下载检查'
        self.coverage_label.configure(text=coverage)
        version = remote.get('revision')
        size = self._online_size(remote.get('bytes'))
        self.version_label.configure(text=' · '.join(filter(None, (f'版本 {version}' if version is not None else '', size))))
        self._sync_selected_actions()

    def _map_changed(self, _event=None):
        items = getattr(self, '_map_items', ())
        index = self.map_box.current()
        item = items[index] if 0 <= index < len(items) else None
        floors = tuple(self._browse_context.pack.by_id[item.id].layers) if item and self._browse_context else ()
        old = self.floor_var.get()
        self._floor_keys = floors
        labels = tuple(floor_label(value) for value in floors)
        self.floor_box.configure(values=labels, state='readonly' if floors else 'disabled')
        if labels: self.floor_box.current(labels.index(old) if old in labels else 0)
        else: self.floor_var.set('无楼层')
        self._request_detail()

    def _request_detail(self):
        if self._closed: return
        items = getattr(self, '_map_items', ())
        index, floor_index = self.map_box.current(), self.floor_box.current()
        item = items[index] if 0 <= index < len(items) else None
        floors = getattr(self, '_floor_keys', ())
        floor = floors[floor_index] if 0 <= floor_index < len(floors) else None
        binding = choose_resource(item, floor, floor) if item and floor else None
        if not binding:
            self.detail_image._image_request = self.detail_image._displayed_image_request = None
            self.detail_image._map_photo = None
            self.detail_image.configure(image='', text='此作者暂无该地图 / 楼层图片', height=10)
            return
        width = max(160, min(round(720*self._scale), self.detail_canvas.winfo_width()-round(32*self._scale)))
        # Leave room for floor/coverage/actions; a large preview must not force
        # a minimum canvas height that clips the fixed selection footer.
        height = min(round(width*.68), max(round(110*self._scale), round(self.detail_canvas.winfo_height()*.48)))
        self._request(binding[2], (width, height), self.detail_image)

    def _preview_stamp(self, path):
        context = self._browse_context
        stamp = context.path_stamps.get(Path(path)) if context is not None else None
        if stamp is None: raise OSError('preview file unavailable')
        return stamp

    def _apply_image(self, widget, pixels, caption):
        # Tk interprets Label.height as pixels once an image is set.
        widget.configure(height=0)
        super()._apply_image(widget, pixels, caption)

    def _sync_selected_actions(self):
        row = self._row_data.get(self._selected_package)
        snapshot = self._route_snapshot
        options = dict(snapshot.get('options') or ())
        selected = snapshot.get('selected')
        self.actual_selection.configure(text=f"当前选择（{'困难' if snapshot.get('mode') == 'difficult' else '噩梦'}）：{options.get(selected, '暂无作者选择')}")
        if not row: return
        profile = row['profile']
        usable = bool(row['installed'] and profile and profile.profile_id in options and snapshot.get('mode') == self.browse_mode
                      and not self._map_package_snapshot().get('busy'))
        active = usable and selected == profile.profile_id
        online = self._online_snapshot()
        downloadable = bool(row['remote'] and online.get('canDownload') and not online.get('busy'))
        self.primary_button.configure(text=('已选择此作者' if active else f"使用{row['title']}" if row['installed'] else '下载图包'),
            state='normal' if (usable and not active) or (not row['installed'] and downloadable) else 'disabled')
        self.update_button.configure(text='重试下载' if row['status'] == 'download-error' else
            '修复 / 重新下载' if row['status'] in {'incomplete', 'modified', 'unverified'} else '更新图包',
            state='normal' if downloadable and row['status'] != 'installed' else 'disabled')
        self.manage_button.configure(state='normal' if row['installed'] else 'disabled')
        message = '预览、下载和导入不会自动更换作者。'
        if row['installed'] and snapshot.get('mode') != self.browse_mode:
            message = '当前游戏模式不同；请在游戏控件切换模式后再使用此作者。'
        elif row['installed'] and not usable:
            message = '此作者当前不可使用；请检查或修复资源。'
        self.selection_note.configure(text=message)

    def _primary_action(self):
        row = self._row_data.get(self._selected_package)
        if not row: return
        if not row['installed']:
            self._download_selected(); return
        profile = row['profile']
        snapshot = self._route_snapshot
        if not profile or snapshot.get('mode') != self.browse_mode or profile.profile_id not in dict(snapshot.get('options') or ()):
            self._sync_selected_actions(); return
        result = self.assistant.select_route_profile(profile.profile_id) or {}
        self._action_feedback = str(result.get('message') or '作者切换未完成') if result.get('success') is False else ''
        if self._action_feedback: self.task_status.configure(text=self._action_feedback)
        self.selection_note.configure(text=result.get('message') or '')
        self.sync_route_selection()

    def _download_selected(self):
        if self._selected_package:
            self._download_online_package(self._selected_package)

    def _choose_map_package(self):
        self._detail_open = True
        self._layout_controls()
        return super()._choose_map_package()

    def _manage_selected(self):
        if self._selected_package:
            self._management_context = self._current_management_context()
            self._managed_open = True
            self._detail_open = True
            self._layout_controls()
            self._inspect_managed_removal(self._selected_package)

    def _cancel_managed_confirmation(self):
        self._management_context = None
        self._managed_open = False
        self._managed_ready = None
        self._managed_signature = None
        self.sync_managed_packages()

    def _confirm_managed_removal(self):
        ready = self._managed_ready
        state = self.assistant.managed_author_packages_snapshot()
        if (self._managed_open and self._management_context == self._current_management_context()
                and ready and ready.get('packageId') == self._selected_package and not state.get('busy')):
            super()._confirm_managed_removal()

    def _current_management_context(self):
        return self.browse_mode, self._selected_package, self._browse_epoch

    def _show_source_status(self):
        self._action_feedback = self._online_snapshot().get('message') or '下载服务由发行配置提供；本地导入不依赖在线服务。'
        self.task_status.configure(text=self._action_feedback)

    def _online_action(self, name, *args, **kwargs):
        self._action_feedback = ''
        result = super()._online_action(name, *args, **kwargs)
        if isinstance(result, dict) and result.get('success') is False:
            self._action_feedback = str(result.get('message') or '操作未完成，请重试')
            self.task_status.configure(text=self._action_feedback)
        return result

    def _map_package_action(self, name, *args, **kwargs):
        self._action_feedback = ''
        result = super()._map_package_action(name, *args, **kwargs)
        if isinstance(result, dict) and result.get('success') is False:
            self._action_feedback = str(result.get('message') or '操作未完成，请重试')
            self.task_status.configure(text=self._action_feedback)
        return result

    def _cancel_current_task(self):
        getter = getattr(self.assistant, 'managed_author_packages_snapshot', None)
        managed = getter() if callable(getter) else {}
        if (managed.get('busy') and managed.get('stage') in {'checking', 'removing'}):
            self._cancel_map_package()
        elif self._map_package_snapshot().get('stage') in {'checking', 'ready', 'installing', 'cancelling'}:
            self._cancel_map_package()
        elif self._online_snapshot().get('canCancel'):
            self._cancel_online_package()

    def sync_route_selection(self):
        if hasattr(self, 'actual_selection'):
            getter = getattr(self.assistant, 'route_selection_snapshot', None)
            self._route_snapshot = getter() if callable(getter) else {}
            self._sync_selected_actions()

    def sync_game_mode(self, snapshot=None):
        self.sync_route_selection()

    def _put_operations(self):
        for surface in (self.import_surface, self.managed_surface, self.online_surface):
            if surface.winfo_manager():
                surface.pack(in_=self.operation_host, fill='x', padx=0, pady=(0, 8))

    def sync_map_package_import(self):
        super().sync_map_package_import()
        self._put_operations()

    def sync_managed_packages(self):
        super().sync_managed_packages()
        if (self._management_context != self._current_management_context()
                or (self._managed_ready and self._managed_ready.get('packageId') != self._selected_package)):
            self._managed_ready = None
            self.managed_confirm.pack_forget()
        if self._managed_open and self._managed_ready:
            state = self.assistant.managed_author_packages_snapshot()
            self.managed_cancel_button.configure(state='disabled' if state.get('busy') else 'normal')
            self.managed_cancel_button.pack(anchor='w', pady=6)
        else:
            self.managed_cancel_button.pack_forget()
            if not self._managed_open: self._managed_ready = None
        if self._selected_package:
            for key, row in self._managed_rows.items():
                if key != self._selected_package: row['frame'].pack_forget()
        self._put_operations()

    def sync_online_packages(self):
        if self._closed or not hasattr(self, 'task_status'): return
        state = self._online_snapshot()
        self.catalog_refresh_button.configure(text='刷新本机' if self._tab == 'installed' else '刷新列表',
            state='normal' if (self._browse_work is None if self._tab == 'installed' else
                               state.get('canCheck') and not state.get('busy')) else 'disabled')
        busy = bool(state.get('busy'))
        received, total = (state.get('progress') or {}).get('receivedBytes'), (state.get('progress') or {}).get('totalBytes')
        message = str(state.get('message') or '') if busy or state.get('stage') == 'error' else self._action_feedback
        if busy and received:
            message += f" · {self._online_size(received)}" + (f" / {self._online_size(total)}" if total else '')
        self.task_status.configure(text=message)
        if message:
            if not self.task_status.winfo_manager():
                self.task_status.pack(fill='x', padx=20, pady=(0, 6), before=self.content)
        else:
            self.task_status.pack_forget()
        if state.get('canCancel') or self._map_package_snapshot().get('stage') in {'checking', 'ready', 'installing', 'cancelling'}:
            self.task_cancel.pack(side='right', padx=6)
        else:
            self.task_cancel.pack_forget()
        self._online_message_text = message
        self.online_feedback.configure(text='')
        getter = getattr(self.assistant, 'managed_author_packages_snapshot', None)
        managed = getter() if callable(getter) else {}
        if managed.get('busy') and managed.get('stage') in {'checking', 'removing'}:
            self.task_status.configure(text=str(managed.get('message') or '正在处理本机资源…'))
            if not self.task_status.winfo_manager():
                self.task_status.pack(fill='x', padx=20, pady=(0, 6), before=self.content)
            self.task_cancel.pack(side='right', padx=6)

    def _drain(self):
        self._poll_context()
        if self._active and not self._closed:
            self.sync_online_packages()
            self.sync_map_package_import()
            self._render_library()
        super()._drain()

    def invalidate_resource_context(self):
        self._cancel_managed_confirmation()
        super().invalidate_resource_context()
        self._browse_epoch += 1
        self._browse_context = None
        self._browse_cache.clear()
        self._library_signature = None
        if self._browse_work is not None: self._browse_work[2].set()
        if self._active: self._queue_context()

    def suspend(self):
        self._cancel_managed_confirmation()
        super().suspend()
        self._browse_serial += 1
        self._browse_pending = None
        if self._browse_work is not None: self._browse_work[2].set()

    def _resume(self):
        if not self._closed:
            self.refresh()

    def _destroy(self, event):
        if event.widget is self:
            if self._browse_work is not None: self._browse_work[2].set()
            self._browse_pending = None
            self._metadata_executor.shutdown(wait=False, cancel_futures=True)
            self._browse_cache.clear()
            self._browse_context = None
            self._card_widgets.clear()
            self._card_records.clear()
            self._author_frames = []
            self._author_cards = {}
            self._cache.clear()
        super()._destroy(event)
