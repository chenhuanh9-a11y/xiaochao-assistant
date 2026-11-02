"""Custom map page: route galleries over the maintainer's own map art.

Two views share one page. The gallery view lists galleries (a read-only
default plus the player's own) and the 24 maps of the chosen gallery. The
editor view plans routes the old way: click a room to target it, click a
corridor to add a via point; legs follow the game road data and change floor
on verified stairs. Every change is saved as route data immediately.
"""
from __future__ import annotations

import json
import tkinter as tk
from tkinter import filedialog, simpledialog

from PIL import Image, ImageDraw, ImageTk

from custom_maps import (COLORS, DEFAULT_COLOR, FLOOR_LABELS, FLOORS, MAX_POINTS, RouteError, draw_route,
                         insertion_index, marker_groups)
from desktop_rounded import RoundedButton, RoundedFrame
from desktop_theme import (BG, BUTTON_BG, BUTTON_TEXT, ERROR, FONT, GREEN, IMAGE_BG, INPUT_BG, LINE, MUTED,
                           PRIMARY, SUBTLE, SURFACE, TEXT, TINT)

FLOOR_BY_LABEL = {label: floor for floor, label in FLOOR_LABELS.items()}
THUMB = 132


class CustomMapEditor(tk.Frame):
    def __init__(self, parent, ui):
        super().__init__(parent, bg=BG)
        self.ui = ui
        self.assistant = ui.assistant
        self.gallery_id = None
        self.map_id = None
        self.floor = "1"
        self.points: list[dict] = []
        self.segments: list[dict] = []
        self.plan_error = ""
        self.color = DEFAULT_COLOR
        self.pending_start = False
        self._past, self._future = [], []
        self._signature = None
        self._view = None  # (scale, ox, oy) display <- image pixels
        self._render_job = None
        self._thumbs = {}
        self._thumb_queue = []
        self._message = (None, False)
        self._in_use = None
        self._maps = ()
        self.floor_var = tk.StringVar(self, FLOOR_LABELS[self.floor])
        self._build()
        self.bind("<Destroy>", self._destroy, add="+")

    # Data ---------------------------------------------------------------------
    def _library(self):
        getter = getattr(self.assistant, "custom_map_library", None)
        return getter() if callable(getter) else None

    def _galleries(self):
        getter = getattr(self.assistant, "custom_galleries", None)
        return getter() if callable(getter) else None

    def _snapshot(self):
        getter = getattr(self.assistant, "custom_map_snapshot", None)
        return getter() if callable(getter) else dict(available=False, message="当前版本不支持自定义地图。")

    def _readonly(self):
        store = self._galleries()
        return store is None or store.readonly(self.gallery_id)

    # Construction ---------------------------------------------------------------
    def _build(self):
        ui = self.ui
        head = tk.Frame(self, bg=BG)
        head.pack(fill="x", padx=ui._px(24), pady=(ui._px(22), ui._px(12)))
        self.heading = head
        self.back_button = ui._ghost(head, "‹  返回图库", self._show_galleries, size=9)
        words = tk.Frame(head, bg=BG)
        words.pack(side="left", fill="x", expand=True)
        self.title = ui._label(words, "自定义地图", size=22, bold=True, anchor="w")
        self.title.pack(fill="x")
        self.subtitle = ui._label(words, "在你的地图上排好路线，进游戏后会自动贴合地图。", size=9, color=MUTED,
                                  anchor="w", justify="left", wraplength=520)
        self.subtitle.pack(fill="x", pady=(2, 0))
        self.notice = ui._label(self, "", size=9, color=ERROR, anchor="w", justify="left", wraplength=640)

        self.galleries_view = tk.Frame(self, bg=BG)
        self._build_galleries(self.galleries_view)
        self.editor_view = tk.Frame(self, bg=BG)
        self._build_editor(self.editor_view)
        self.galleries_view.pack(fill="both", expand=True, padx=ui._px(14), pady=(0, ui._px(14)))

    def _build_galleries(self, host):
        ui = self.ui
        host.columnconfigure(1, weight=1)
        host.rowconfigure(0, weight=1)
        side = ui._card(host, padx=12, pady=14)
        side.grid(row=0, column=0, sticky="ns", padx=(0, 4))
        ui._label(side, "图库", size=11, bold=True, anchor="w").pack(fill="x", padx=6)
        self.gallery_list = tk.Frame(side, bg=SURFACE, width=ui._px(180))
        self.gallery_list.pack(fill="x", pady=(8, 10))
        actions = tk.Frame(side, bg=SURFACE)
        actions.pack(fill="x")
        for row, items in enumerate(((("新建", self._new_gallery), ("复制", self._copy_gallery)),
                                     (("改名", self._rename_gallery), ("删除", self._delete_gallery)),
                                     (("导入", self._import_gallery), ("导出", self._export_gallery)))):
            line = tk.Frame(actions, bg=SURFACE)
            line.pack(fill="x", pady=2)
            for text, command in items:
                button = ui._ghost(line, text, command, size=9)
                button.pack(side="left", padx=(0, 4))
                setattr(self, f"_gallery_{command.__name__}", button)

        main = ui._card(host, padx=18, pady=14)
        main.grid(row=0, column=1, sticky="nsew", padx=(4, 0))
        top = tk.Frame(main, bg=SURFACE)
        top.pack(fill="x")
        self.gallery_title = ui._label(top, "", size=13, bold=True, anchor="w")
        self.gallery_title.pack(side="left")
        self.use_button = RoundedButton(top, text="在游戏中使用", command=self._toggle_use, bg=PRIMARY, fg="white",
                                        activebackground=PRIMARY, highlightthickness=0, font=(FONT, 9, "bold"),
                                        padx=14, pady=6, cursor="hand2")
        self.use_button.pack(side="right")
        self.gallery_note = ui._label(main, "", size=9, color=MUTED, anchor="w", justify="left", wraplength=560)
        self.gallery_note.pack(fill="x", pady=(4, 10))
        holder = tk.Frame(main, bg=SURFACE)
        holder.pack(fill="both", expand=True)
        self.grid_canvas = tk.Canvas(holder, bg=SURFACE, highlightthickness=0, bd=0)
        bar = tk.Scrollbar(holder, orient="vertical", command=self.grid_canvas.yview, width=10, relief="flat", bd=0)
        self.grid_canvas.configure(yscrollcommand=bar.set)
        bar.pack(side="right", fill="y")
        self.grid_canvas.pack(side="left", fill="both", expand=True)
        self.grid_canvas._desktop_scroll_owner = self
        self.grid_frame = tk.Frame(self.grid_canvas, bg=SURFACE)
        self._grid_item = self.grid_canvas.create_window((0, 0), window=self.grid_frame, anchor="nw")
        self.grid_frame.bind("<Configure>", lambda _e: self.grid_canvas.configure(scrollregion=self.grid_canvas.bbox("all")))
        self.grid_canvas.bind("<Configure>", self._grid_resized)
        self.grid_canvas.bind("<MouseWheel>", lambda e: self.grid_canvas.yview_scroll(-1 if e.delta > 0 else 1, "units"))
        self._tiles = {}
        self._grid_columns = 0

    def _build_editor(self, host):
        ui = self.ui
        host.columnconfigure(0, weight=1)
        host.rowconfigure(0, weight=1)
        card = ui._card(host, padx=16, pady=12)
        card.grid(row=0, column=0, sticky="nsew", padx=(0, 4))
        tools = tk.Frame(card, bg=SURFACE)
        tools.pack(fill="x")
        self.floor_switch = ui._segmented(tools, self.floor_var, tuple(FLOOR_LABELS[f] for f in FLOORS), self._floor_changed)
        self.floor_switch.pack(side="left")
        self.swatches = {}
        swatch_row = tk.Frame(tools, bg=SURFACE)
        swatch_row.pack(side="left", padx=(16, 0))
        for color, name in COLORS:
            swatch = tk.Canvas(swatch_row, width=ui._px(22), height=ui._px(22), bg=SURFACE, highlightthickness=0,
                               bd=0, cursor="hand2")
            swatch.pack(side="left", padx=(0, 5))
            swatch.bind("<Button-1>", lambda _e, value=color: self._set_color(value))
            self.swatches[color] = swatch
        self.start_button = RoundedButton(tools, text="新起点", command=self._toggle_start, bg=BUTTON_BG, fg=BUTTON_TEXT,
                                          highlightthickness=0, font=(FONT, 9, "bold"), padx=10, pady=5, cursor="hand2")
        self.start_button.pack(side="left", padx=(10, 0))
        frame = RoundedFrame(card, bg=INPUT_BG, radius=12, padx=0, pady=0, highlightthickness=0)
        status = tk.Frame(card, bg=SURFACE)
        status.pack(side="bottom", fill="x", pady=(8, 0))
        self.status = ui._label(status, "", size=9, color=MUTED, anchor="w", justify="left", wraplength=480)
        self.status.pack(side="left", fill="x", expand=True)
        frame.pack(fill="both", expand=True, pady=(10, 0))
        self.canvas = tk.Canvas(frame, bg=INPUT_BG, highlightthickness=0, bd=0, cursor="crosshair")
        self.canvas.pack(fill="both", expand=True, padx=6, pady=6)
        self.canvas.bind("<Configure>", lambda _e: self._schedule_render())
        self.canvas.bind("<ButtonRelease-1>", self._tap)
        for button in ("<Button-2>", "<Button-3>"):
            self.canvas.bind(button, self._remove_at)

        side = ui._card(host, padx=12, pady=12)
        side.grid(row=0, column=1, sticky="ns", padx=(4, 0))
        ui._label(side, "目标顺序", size=11, bold=True, anchor="w").pack(fill="x")
        ui._label(side, "点房间加目标，点走廊加必经点；右键删除。", size=8, color=SUBTLE, anchor="w",
                  justify="left", wraplength=ui._px(170)).pack(fill="x", pady=(2, 8))
        edits = tk.Frame(side, bg=SURFACE)
        edits.pack(side="bottom", fill="x", pady=(8, 0))
        self.undo_button = ui._ghost(edits, "撤销", self._undo, size=9)
        self.undo_button.pack(side="left")
        self.redo_button = ui._ghost(edits, "重做", self._redo, size=9)
        self.redo_button.pack(side="left")
        self.clear_button = ui._ghost(edits, "清空", self._clear, color=ERROR, size=9)
        self.clear_button.pack(side="right")
        self.copy_hint = RoundedButton(side, text="复制为我的图库", command=self._copy_gallery, bg=PRIMARY, fg="white",
                                       highlightthickness=0, font=(FONT, 9, "bold"), padx=10, pady=6, cursor="hand2")
        self.points_list = tk.Frame(side, bg=SURFACE, width=ui._px(190))
        self.points_list.pack(fill="both", expand=True)

    # Page sync ------------------------------------------------------------------
    def refresh(self):
        snapshot = self._snapshot()
        store = self._galleries()
        galleries = store.galleries() if store is not None and snapshot.get("available") else []
        signature = (snapshot.get("available"), snapshot.get("message"), tuple(galleries),
                     store.revision if store is not None else None, snapshot.get("inUse"))
        if signature == self._signature:
            return
        self._signature = signature
        problem = ((snapshot.get("message") or "自定义地图暂不可用") if not snapshot.get("available")
                   else getattr(store, "load_error", ""))
        if problem:
            self.notice.configure(text=problem)
            if not self.notice.winfo_manager():
                self.notice.pack(fill="x", padx=self.ui._px(24), pady=(0, 8), after=self.heading)
        else:
            self.notice.pack_forget()
        ids = [gallery for gallery, _name, _ro in galleries]
        if self.gallery_id not in ids:
            self.gallery_id = snapshot.get("inUse") or (ids[0] if ids else None)
        self._in_use = snapshot.get("inUse")
        self._maps = tuple(snapshot.get("maps") or ())
        self._render_gallery_list(galleries)
        self._render_gallery()

    def _render_gallery_list(self, galleries):
        for child in self.gallery_list.winfo_children():
            child.destroy()
        for gallery, name, readonly in galleries:
            selected = gallery == self.gallery_id
            row = tk.Frame(self.gallery_list, bg=BUTTON_BG if selected else SURFACE, cursor="hand2")
            row.pack(fill="x", pady=1)
            title = tk.Label(row, text=name, bg=row.cget("bg"), fg=BUTTON_TEXT if selected else TEXT,
                             font=(FONT, 10, "bold" if selected else "normal"), anchor="w", padx=10, pady=6)
            title.pack(side="left", fill="x", expand=True)
            tags = []
            if readonly:
                tags.append("只读")
            if gallery == self._in_use:
                tags.append("使用中")
            badge = tk.Label(row, text=" · ".join(tags), bg=row.cget("bg"), fg=GREEN if gallery == self._in_use else SUBTLE,
                             font=(FONT, 8, "bold"), padx=8)
            badge.pack(side="right")
            for widget in (row, title, badge):
                widget.bind("<Button-1>", lambda _e, value=gallery: self._select_gallery(value))

    def _render_gallery(self):
        store = self._galleries()
        if store is None or self.gallery_id is None:
            self.gallery_title.configure(text="")
            return
        name = store.name(self.gallery_id) or ""
        drawn = store.drawn_maps(self.gallery_id)
        self.gallery_title.configure(text=name)
        using = self.gallery_id == self._in_use
        self.use_button.configure(text="停止使用" if using else "在游戏中使用",
                                  bg=BUTTON_BG if using else PRIMARY, fg=BUTTON_TEXT if using else "white")
        note = f"已画 {len(drawn)} / {len(self._maps)} 张。"
        note += "默认图库只读；复制一份后就能编辑。" if store.readonly(self.gallery_id) else "点一张地图开始编辑，改动会自动保存。"
        if using:
            note += " 游戏内正在使用这个图库。"
        self.gallery_note.configure(text=note)
        readonly = store.readonly(self.gallery_id)
        for name_ in ("_gallery__rename_gallery", "_gallery__delete_gallery"):
            getattr(self, name_).configure(state="disabled" if readonly else "normal")
        self._layout_tiles(drawn)

    def _grid_resized(self, event):
        self.grid_canvas.itemconfigure(self._grid_item, width=event.width)
        store = self._galleries()
        if store is not None and self.gallery_id is not None:
            self._layout_tiles(store.drawn_maps(self.gallery_id))

    def _layout_tiles(self, drawn):
        width = max(1, self.grid_canvas.winfo_width())
        size = self.ui._px(THUMB)
        columns = max(1, (width + 8) // (size + 12))
        if set(self._tiles) != {m for m, _ in self._maps} or columns != self._grid_columns:
            for child in self.grid_frame.winfo_children():
                child.destroy()
            self._tiles = {}
            self._grid_columns = columns
            for index, (map_id, name) in enumerate(self._maps):
                tile = tk.Frame(self.grid_frame, bg=SURFACE, cursor="hand2")
                tile.grid(row=index // columns, column=index % columns, padx=6, pady=6, sticky="n")
                picture = tk.Label(tile, bg=IMAGE_BG, width=size, height=size, bd=0, highlightthickness=2,
                                   highlightbackground=SURFACE)
                picture.pack()
                label = tk.Label(tile, text=name, bg=SURFACE, fg=TEXT, font=(FONT, 9), wraplength=size, justify="center")
                label.pack(fill="x", pady=(4, 0))
                badge = tk.Label(tile, text="", bg=SURFACE, fg=GREEN, font=(FONT, 8, "bold"))
                badge.pack()
                for widget in (tile, picture, label, badge):
                    widget.bind("<Button-1>", lambda _e, value=map_id: self._open_map(value))
                    widget.bind("<MouseWheel>", lambda e: self.grid_canvas.yview_scroll(-1 if e.delta > 0 else 1, "units"))
                self._tiles[map_id] = (picture, badge)
                if map_id in self._thumbs:
                    picture.configure(image=self._thumbs[map_id], width=size, height=size)
                else:
                    self._thumb_queue.append(map_id)
            if self._thumb_queue:
                self.after(30, self._load_thumb)
        for map_id, (picture, badge) in self._tiles.items():
            badge.configure(text="● 已画路线" if map_id in drawn else "")
            picture.configure(highlightbackground=GREEN if map_id in drawn else SURFACE)

    def _load_thumb(self):
        library = self._library()
        while self._thumb_queue:
            map_id = self._thumb_queue.pop(0)
            if map_id in self._thumbs or map_id not in self._tiles or library is None:
                continue
            size = self.ui._px(THUMB)
            try:
                art = library.image(map_id, "1").copy()
            except (OSError, ValueError, KeyError):
                continue
            art.thumbnail((size - 8, size - 8), Image.Resampling.LANCZOS)
            base = Image.new("RGBA", (size, size), IMAGE_BG)
            base.alpha_composite(art, ((size - art.width) // 2, (size - art.height) // 2))
            photo = ImageTk.PhotoImage(base, master=self)
            self._thumbs[map_id] = photo
            self._tiles[map_id][0].configure(image=photo, width=size, height=size)
            self.after(10, self._load_thumb)
            return

    # Gallery actions ----------------------------------------------------------
    def _select_gallery(self, gallery_id):
        self.gallery_id = gallery_id
        self._signature = None
        self.refresh()

    def _ask_name(self, title, initial=""):
        return simpledialog.askstring(title, "图库名称（最多 24 个字）", initialvalue=initial, parent=self.winfo_toplevel())

    def _new_gallery(self):
        name = self._ask_name("新建图库", "我的路线")
        if name:
            try:
                self.gallery_id = self._galleries().create(name)
            except OSError as error:
                self._note(f"新建失败：{error}", error=True)
                return
            self._signature = None
            self.refresh()

    def _copy_gallery(self):
        store = self._galleries()
        source = self.gallery_id
        name = self._ask_name("复制图库", f"{store.name(source) or '图库'}（副本）")
        if name:
            try:
                self.gallery_id = store.create(name, copy_from=source)
            except OSError as error:
                self._note(f"复制失败：{error}", error=True)
                return
            self._signature = None
            if self.map_id is not None and self.editor_view.winfo_manager():
                self._load_map()
            self.refresh()

    def _rename_gallery(self):
        store = self._galleries()
        name = self._ask_name("重命名图库", store.name(self.gallery_id) or "")
        if name and store.rename(self.gallery_id, name):
            self._signature = None
            self.refresh()
            self.ui._refresh_route_choices()

    def _delete_gallery(self):
        store = self._galleries()
        if store.readonly(self.gallery_id):
            return
        if not self._confirm(f"删除图库「{store.name(self.gallery_id)}」？里面的路线会一起删除。"):
            return
        in_use = self.gallery_id == self._in_use
        if not store.delete(self.gallery_id):
            self._note("删除失败：图库没有保存，请检查软件目录是否可写。", error=True)
            return
        if in_use:
            self.assistant.use_custom_gallery(None)
        self.gallery_id = None
        self._signature = None
        self.refresh()
        self.ui._refresh_route_choices()

    def _confirm(self, text):
        from tkinter import messagebox
        return messagebox.askyesno("自定义地图", text, parent=self.winfo_toplevel())

    def _export_gallery(self):
        store = self._galleries()
        path = filedialog.asksaveasfilename(parent=self.winfo_toplevel(), title="导出图库", defaultextension=".json",
                                            initialfile=f"{store.name(self.gallery_id) or 'gallery'}.json",
                                            filetypes=(("星图图库", "*.json"),))
        if not path:
            return
        try:
            with open(path, "w", encoding="utf-8") as handle:
                json.dump(store.export(self.gallery_id), handle, ensure_ascii=False, indent=1)
            self._note(f"已导出到 {path}")
        except OSError as error:
            self._note(f"导出失败：{error}", error=True)

    def _import_gallery(self):
        path = filedialog.askopenfilename(parent=self.winfo_toplevel(), title="导入图库", filetypes=(("星图图库", "*.json"),))
        if not path:
            return
        try:
            with open(path, encoding="utf-8") as handle:
                self.gallery_id = self._galleries().import_gallery(json.load(handle))
            self._signature = None
            self.refresh()
            self.ui._refresh_route_choices()
        except (OSError, ValueError) as error:
            self._note(f"导入失败：{error}", error=True)

    def _toggle_use(self):
        target = None if self.gallery_id == self._in_use else self.gallery_id
        result = self.assistant.use_custom_gallery(target)
        self._note(result.get("message"), error=not result.get("success"))
        self._signature = None
        self.refresh()
        self.ui._refresh_route_choices()

    def _note(self, message, *, error=False):
        self.gallery_note.configure(text=message or "", fg=ERROR if error else MUTED)

    # Views ----------------------------------------------------------------------
    def _show_galleries(self):
        self.editor_view.pack_forget()
        self.back_button.pack_forget()
        self.galleries_view.pack(fill="both", expand=True, padx=self.ui._px(14), pady=(0, self.ui._px(14)))
        self.title.configure(text="自定义地图")
        self.subtitle.configure(text="在你的地图上排好路线，进游戏后会自动贴合地图。")
        self.map_id = None
        self._signature = None
        self.refresh()

    def _open_map(self, map_id):
        self.map_id = map_id
        current = self._snapshot()
        if current.get("currentMapId") == map_id and current.get("currentFloor") in FLOORS:
            self.floor = current["currentFloor"]
            self.floor_var.set(FLOOR_LABELS[self.floor])
        self.galleries_view.pack_forget()
        self.editor_view.pack(fill="both", expand=True, padx=self.ui._px(14), pady=(0, self.ui._px(14)))
        self.back_button.pack(side="left", padx=(0, 10), before=self.title.master)
        self.title.configure(text=self._library().name(map_id))
        self.subtitle.configure(text=f"图库：{self._galleries().name(self.gallery_id)}")
        self._past, self._future = [], []
        self.pending_start = False
        self._load_map()

    def _load_map(self):
        self.points = self._galleries().route(self.gallery_id, self.map_id)
        self.color = self.points[-1]["color"] if self.points else DEFAULT_COLOR
        self._replan()

    # Editing --------------------------------------------------------------------
    def _replan(self):
        self.segments, self.plan_error = [], ""
        if len(self.points) >= 2:
            try:
                self.segments = self._library().graph(self.map_id).plan(self.points)
            except RouteError as error:
                self.plan_error = str(error)
        self._render_points()
        self._schedule_render()

    def _commit(self, points):
        if self._readonly():
            return
        self._past.append([dict(p) for p in self.points])
        del self._past[:-128]
        self._future = []
        self.points = points
        if not self._galleries().set_route(self.gallery_id, self.map_id, points):
            self._message = ("保存失败，请检查软件目录是否可写。", True)
        self._replan()

    def _undo(self):
        if self._past:
            self._future.append(self.points)
            self.points = self._past.pop()
            if not self._galleries().set_route(self.gallery_id, self.map_id, self.points):
                self._message = ("保存失败，请检查软件目录是否可写。", True)
            self._replan()

    def _redo(self):
        if self._future:
            self._past.append(self.points)
            self.points = self._future.pop()
            if not self._galleries().set_route(self.gallery_id, self.map_id, self.points):
                self._message = ("保存失败，请检查软件目录是否可写。", True)
            self._replan()

    def _clear(self):
        if self.points:
            self._commit([])

    def _set_color(self, color):
        self.color = color
        self._paint_tools()

    def _toggle_start(self):
        self.pending_start = not self.pending_start
        self._paint_tools()

    def _floor_changed(self):
        self.floor = FLOOR_BY_LABEL.get(self.floor_var.get(), "1")
        self._schedule_render()

    def _native(self, event):
        if self._view is None:
            return None
        scale, ox, oy = self._view
        asset = self._library().floor(self.map_id, self.floor)
        x, y = (event.x - ox) / scale, (event.y - oy) / scale
        u, v, _ = asset.to_native @ [x, y, 1]
        return float(u), float(v)

    def _tap(self, event):
        if self.map_id is None:
            return
        if self._readonly():
            self._message = ("默认图库只读，请先“复制为我的图库”。", True)
            self._paint_status()
            return
        native = self._native(event)
        if native is None:
            return
        graph = self._library().graph(self.map_id)
        target = graph.pick(self.floor, *native)
        if target is None:
            self._message = ("请点击房间内部或可走的道路。", True)
            self._paint_status()
            return
        if len(self.points) >= MAX_POINTS:
            self._message = (f"一张地图最多添加 {MAX_POINTS} 个目标。", True)
            self._paint_status()
            return
        target["color"] = self.color
        if self.pending_start and self.points:
            target["start"] = True
        insert = len(self.points) if self.pending_start else insertion_index(self.points, self.segments, graph, target)
        previous = self.points[insert - 1] if insert > 0 else None
        if (not self.pending_start and previous and previous["floor"] == target["floor"]
                and previous.get("room") == target.get("room") and previous["x"] == target["x"] and previous["y"] == target["y"]):
            return
        points = [dict(p) for p in self.points]
        points.insert(insert, target)
        self.pending_start = False
        self._message = (None, False)
        self._commit(points)

    def _remove_at(self, event):
        if self._readonly() or self._view is None:
            return
        scale, ox, oy = self._view
        asset = self._library().floor(self.map_id, self.floor)
        inverse = asset.to_image
        best, index = self.ui._px(16) ** 2, None
        for i, point in enumerate(self.points):
            if point["floor"] != self.floor:
                continue
            x, y, _ = inverse @ [point["x"], point["y"], 1]
            d = (ox + x * scale - event.x) ** 2 + (oy + y * scale - event.y) ** 2
            if d <= best:
                best, index = d, i
        if index is not None:
            self._delete(index)

    def _delete(self, index):
        points = [dict(p) for p in self.points]
        if points[index].get("start") and index + 1 < len(points):
            points[index + 1]["start"] = True
        del points[index]
        if points:
            points[0].pop("start", None)
        self._commit(points)

    def _move_up(self, index):
        if index < 1:
            return
        points = [dict(p) for p in self.points]
        points[index - 1], points[index] = points[index], points[index - 1]
        self._commit(points)

    # Painting -------------------------------------------------------------------
    def _render_points(self):
        for child in self.points_list.winfo_children():
            child.destroy()
        readonly = self._readonly()
        for index, point in enumerate(self.points):
            row = tk.Frame(self.points_list, bg=SURFACE)
            row.pack(fill="x", pady=1)
            dot = tk.Canvas(row, width=10, height=10, bg=SURFACE, highlightthickness=0)
            dot.create_oval(1, 1, 9, 9, fill=point.get("color", DEFAULT_COLOR), outline=LINE)
            dot.pack(side="left", padx=(0, 6))
            kind = "房间" if point.get("kind") == "room" else "必经道路"
            first = index == 0 or point.get("start")
            text = f"{index + 1}. {FLOOR_LABELS[point['floor']]} · {'起点 · ' if first else ''}{kind}"
            tk.Label(row, text=text, bg=SURFACE, fg=TEXT, font=(FONT, 9), anchor="w").pack(side="left", fill="x", expand=True)
            if not readonly:
                self.ui._ghost(row, "删除", lambda i=index: self._delete(i), color=ERROR, size=8).pack(side="right")
                if index:
                    self.ui._ghost(row, "↑", lambda i=index: self._move_up(i), size=8).pack(side="right")
        if readonly:
            if not self.copy_hint.winfo_manager():
                self.copy_hint.pack(fill="x", pady=(0, 8), before=self.points_list)
        else:
            self.copy_hint.pack_forget()
        self._paint_tools()
        self._paint_status()

    def _paint_tools(self):
        px = self.ui._px
        readonly = self._readonly()
        for color, swatch in self.swatches.items():
            size = px(22)
            swatch.configure(width=size, height=size)
            swatch.delete("all")
            if color == self.color:
                swatch.create_oval(1, 1, size - 1, size - 1, outline=PRIMARY, width=2)
            swatch.create_oval(px(5), px(5), size - px(5), size - px(5), fill=color, outline=SUBTLE)
        self.start_button.configure(bg=PRIMARY if self.pending_start else BUTTON_BG,
                                    fg="white" if self.pending_start else BUTTON_TEXT,
                                    state="disabled" if readonly else "normal")
        self.undo_button.configure(state="normal" if self._past and not readonly else "disabled")
        self.redo_button.configure(state="normal" if self._future and not readonly else "disabled")
        self.clear_button.configure(state="normal" if self.points and not readonly else "disabled")

    def _paint_status(self):
        message, error = self._message
        if not message and self.plan_error:
            message, error = self.plan_error, True
        if message:
            self.status.configure(text=message, fg=ERROR if error else MUTED)
        elif self._readonly():
            self.status.configure(text="默认图库只读，复制一份后就能编辑。", fg=MUTED)
        elif not self.points:
            self.status.configure(text="点击房间添加目标；点击道路添加必经点。", fg=MUTED)
        elif len(self.points) < 2:
            self.status.configure(text="再点一个目标，就会自动连成路线。", fg=MUTED)
        else:
            self.status.configure(text="已按目标顺序连接可走路线 · 已自动保存", fg=GREEN)

    def _schedule_render(self):
        if self._render_job is None:
            self._render_job = self.after(16, self._render)

    def _render(self):
        self._render_job = None
        canvas = self.canvas
        canvas.delete("all")
        width, height = canvas.winfo_width(), canvas.winfo_height()
        library = self._library()
        if self.map_id is None or library is None or width < 20 or height < 20:
            return
        asset = library.floor(self.map_id, self.floor)
        art = library.image(self.map_id, self.floor)
        scale = min((width - 20) / art.width, (height - 20) / art.height)
        size = (max(1, round(art.width * scale)), max(1, round(art.height * scale)))
        ox, oy = (width - size[0]) / 2, (height - size[1]) / 2
        self._view = (scale, ox, oy)
        picture = art.resize(size, Image.Resampling.LANCZOS)
        icon = library.exit_icon()
        if icon is not None:
            side = max(14, round(max(art.size) * .025 * scale))
            icon = icon.resize((side, side), Image.Resampling.LANCZOS)
            for _label, x, y in asset.entrances:
                picture.alpha_composite(icon, (round(x * scale - side / 2), round(y * scale - side / 2)))
        inverse = asset.to_image
        def transform(point):
            x, y, _ = inverse @ [point[0], point[1], 1]
            return x * scale, y * scale
        graph = library.graph(self.map_id)
        draw_route(ImageDraw.Draw(picture), transform, graph.floor_lines(self.segments, self.floor),
                   marker_groups(self.points, self.floor), width=self.ui._px(3), pill=self.ui._px(10))
        self._photo = ImageTk.PhotoImage(picture, master=canvas)
        canvas.create_image(ox, oy, image=self._photo, anchor="nw")

    def _destroy(self, event):
        if event.widget is self and self._render_job is not None:
            self.after_cancel(self._render_job)
            self._render_job = None
