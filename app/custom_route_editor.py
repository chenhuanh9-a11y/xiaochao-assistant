"""Custom map page: draw a numbered route on each built-in nightmare floor.

The page edits waypoints in native structure pixels; the assistant renders
the saved route into the same canvas for the in-game overlay. Styling comes
from the desktop window (cards, labels, segmented controls).
"""
from __future__ import annotations

import tkinter as tk

import cv2
import numpy as np
from PIL import Image, ImageTk

from custom_routes import (COLORS, FLOOR_LABELS, FLOORS, MAX_POINTS, FloorRoute, Walkable,
                           route_polyline)
from desktop_rounded import RoundedButton, RoundedFrame
from desktop_theme import (BG, BUTTON_BG, BUTTON_TEXT, ERROR, FONT, GREEN, IMAGE_BG, INPUT_BG, LINE,
                           MUTED, PRIMARY, SUBTLE, SURFACE, TEXT, TINT)

FLOOR_BY_LABEL = {label: floor for floor, label in FLOOR_LABELS.items()}
SHORT_FLOORS = {"B1": "B1", "1": "1F", "2": "2F"}
MAP_FILL = (223, 230, 241)
MAP_EDGE = (150, 166, 194)
HIT_RADIUS = 12


def _hex(rgb) -> str:
    return "#%02x%02x%02x" % tuple(rgb)


def _darker(color: str, amount=.55) -> str:
    value = color.lstrip("#")
    return _hex(round(int(value[index:index + 2], 16) * amount) for index in (0, 2, 4))


def _map_picture(mask: np.ndarray, size: tuple[int, int]) -> Image.Image:
    """Footprint with soft outlines, fitted to ``size`` (display pixels)."""
    width, height = size
    alpha = cv2.resize((mask > 0).astype(np.float32), (width, height), interpolation=cv2.INTER_AREA)
    edge = cv2.morphologyEx((alpha > .5).astype(np.uint8), cv2.MORPH_GRADIENT, np.ones((3, 3), np.uint8))
    canvas = np.empty((height, width, 3), np.float32)
    canvas[:] = (245, 247, 251)
    for channel in range(3):
        canvas[:, :, channel] = canvas[:, :, channel] * (1 - alpha) + MAP_FILL[channel] * alpha
    canvas[edge > 0] = MAP_EDGE
    return Image.fromarray(np.clip(canvas, 0, 255).astype(np.uint8), "RGB")


class CustomRouteEditor(tk.Frame):
    def __init__(self, parent, ui):
        super().__init__(parent, bg=BG)
        self.ui = ui
        self.assistant = ui.assistant
        self.map_id = None
        self.floor = "1"
        self._maps = ()
        self._rows = {}
        self._history: dict[tuple[str, str], list[FloorRoute]] = {}
        self._cache = {}  # (map_id, floor) -> (mask, Walkable)
        self._picture = None
        self._picture_key = None
        self._view = None  # (scale, offset_x, offset_y)
        self._drag = None
        self._render_job = None
        self._signature = None
        self.floor_var = tk.StringVar(self, FLOOR_LABELS[self.floor])
        self.in_game_var = tk.StringVar(self, "关闭")
        self._build()
        self.bind("<Destroy>", self._destroy, add="+")

    # Construction ------------------------------------------------------------
    def _build(self):
        ui = self.ui
        head = tk.Frame(self, bg=BG)
        head.pack(fill="x", padx=ui._px(24), pady=(ui._px(22), ui._px(12)))
        self.heading = head
        # The switch is packed first so a narrow window shortens the text, not it.
        toggle = tk.Frame(head, bg=BG)
        toggle.pack(side="right", anchor="s", padx=(12, 0))
        self.in_game_label = ui._label(toggle, "游戏内使用", size=9, color=MUTED)
        self.in_game_label.pack(side="left", padx=(0, 8))
        self.in_game_switch = ui._segmented(toggle, self.in_game_var, ("关闭", "开启"), self._toggle_in_game)
        self.in_game_switch.pack(side="left")
        words = tk.Frame(head, bg=BG)
        words.pack(side="left", fill="x", expand=True)
        ui._label(words, "自定义地图", size=22, bold=True, anchor="w").pack(fill="x")
        ui._label(words, "在内置地图上点出自己的路线，进游戏后会自动贴合当前地图。", size=9,
                  color=MUTED, anchor="w", justify="left", wraplength=420).pack(fill="x", pady=(2, 0))

        body = tk.Frame(self, bg=BG)
        body.pack(fill="both", expand=True, padx=ui._px(24 - 10), pady=(0, ui._px(14)))
        body.columnconfigure(1, weight=1)
        body.rowconfigure(0, weight=1)

        library = ui._card(body, padx=10, pady=12)
        library.grid(row=0, column=0, sticky="ns", padx=(0, 4))
        ui._label(library, "地图", size=11, bold=True, anchor="w").pack(fill="x", padx=8, pady=(0, 2))
        self.library_note = ui._label(library, "", size=8, color=SUBTLE, anchor="w")
        self.library_note.pack(fill="x", padx=8, pady=(0, 8))
        holder = tk.Frame(library, bg=SURFACE)
        holder.pack(fill="both", expand=True)
        self.list_canvas = tk.Canvas(holder, bg=SURFACE, highlightthickness=0, bd=0, width=ui._px(176))
        self.list_canvas.pack(side="left", fill="both", expand=True)
        self.list_canvas._desktop_scroll_owner = self
        self.list_frame = tk.Frame(self.list_canvas, bg=SURFACE)
        self._list_item = self.list_canvas.create_window((0, 0), window=self.list_frame, anchor="nw")
        self.list_frame.bind("<Configure>", lambda _e: self.list_canvas.configure(
            scrollregion=self.list_canvas.bbox("all")))
        self.list_canvas.bind("<Configure>", lambda e: self.list_canvas.itemconfigure(self._list_item, width=e.width))
        self._wheel_tag = f"CustomRouteList-{id(self)}"
        self.list_canvas.bind_class(self._wheel_tag, "<MouseWheel>", lambda e: self.list_canvas.yview_scroll(
            -1 if e.delta > 0 else 1, "units"))
        self.list_canvas.bindtags((self._wheel_tag, *self.list_canvas.bindtags()))

        editor = ui._card(body, padx=18, pady=14)
        editor.grid(row=0, column=1, sticky="nsew", padx=(4, 0))
        self.editor_card = editor
        tools = tk.Frame(editor, bg=SURFACE)
        tools.pack(fill="x")
        self.tools = tools
        self.map_title = ui._label(tools, "选择一张地图", size=13, bold=True, anchor="w")
        self.map_title.pack(side="left")
        self.current_button = ui._ghost(tools, "本局地图", self._jump_to_current, size=9)
        self.floor_row = tk.Frame(editor, bg=SURFACE)

        options = tk.Frame(editor, bg=SURFACE)
        options.pack(fill="x", pady=(12, 10))
        self.options = options
        self.swatches = {}
        for color in COLORS:
            swatch = tk.Canvas(options, width=ui._px(22), height=ui._px(22), bg=SURFACE,
                               highlightthickness=0, bd=0, cursor="hand2")
            swatch.pack(side="left", padx=(0, 6))
            swatch.bind("<Button-1>", lambda _e, value=color: self._set_color(value))
            self.swatches[color] = swatch
        self.snap_button = RoundedButton(options, text="贴合通道", command=self._toggle_snap, bg=BUTTON_BG,
                                         fg=BUTTON_TEXT, highlightthickness=0, font=(FONT, 9, "bold"),
                                         padx=12, pady=5, cursor="hand2")
        self.snap_button.pack(side="left", padx=(8, 0))

        # Status sits below the canvas but is packed first so it is never squeezed out.
        status = tk.Frame(editor, bg=SURFACE)
        status.pack(side="bottom", fill="x", pady=(10, 0))
        self.status_row = status
        self.hint = ui._label(status, "左键加点 · 拖动调整 · 右键删除", size=8, color=SUBTLE)
        self.status = ui._label(status, "", size=9, color=MUTED, anchor="w")
        self.status.pack(side="left", fill="x", expand=True)

        # Created on the card so narrow layouts can re-home them with pack(in_=...).
        self.floor_switch = ui._segmented(editor, self.floor_var, tuple(FLOOR_LABELS[f] for f in FLOORS),
                                          self._floor_changed)
        self.clear_button = ui._ghost(editor, "清空本层", self._clear, color=ERROR, size=9)
        self.undo_button = ui._ghost(editor, "撤销", self._undo, size=9)
        self._compact = None
        editor.bind("<Configure>", lambda event: self._layout(event.width), add="+")
        self._layout(800)

        frame = RoundedFrame(editor, bg=INPUT_BG, radius=12, padx=0, pady=0, highlightthickness=0)
        frame.pack(fill="both", expand=True)
        self.canvas = tk.Canvas(frame, bg=INPUT_BG, highlightthickness=0, bd=0, cursor="crosshair")
        self.canvas.pack(fill="both", expand=True, padx=6, pady=6)
        self.canvas.bind("<Configure>", lambda _e: self._schedule_render())
        self.canvas.bind("<ButtonPress-1>", self._press)
        self.canvas.bind("<B1-Motion>", self._motion)
        self.canvas.bind("<ButtonRelease-1>", self._release)
        for button in ("<Button-2>", "<Button-3>"):
            self.canvas.bind(button, self._remove_at)


    def _layout(self, width):
        """Wide: floors beside the title, edit actions beside the tools.
        Narrow: floors on their own row, edit actions in the status row."""
        compact = width < self.ui._px(450)
        if compact == self._compact:
            return
        self._compact = compact
        for widget in (self.floor_switch, self.clear_button, self.undo_button, self.hint):
            widget.pack_forget()
        self.status.pack_forget()
        if compact:
            self.floor_row.pack(fill="x", pady=(10, 0), after=self.tools)
            self.floor_switch.pack(in_=self.floor_row, side="left")
            self.clear_button.pack(in_=self.status_row, side="right")
            self.undo_button.pack(in_=self.status_row, side="right", padx=(0, 4))
        else:
            self.floor_row.pack_forget()
            self.floor_switch.pack(in_=self.tools, side="right")
            self.clear_button.pack(in_=self.options, side="right")
            self.undo_button.pack(in_=self.options, side="right", padx=(0, 4))
            self.hint.pack(in_=self.status_row, side="right")
        self.status.pack(side="left", fill="x", expand=True)
        for widget in (self.floor_switch, self.clear_button, self.undo_button):
            widget.lift()
        self.list_canvas.configure(width=self.ui._px(132 if compact else 176))

    # Data ----------------------------------------------------------------------
    def _store(self):
        getter = getattr(self.assistant, "custom_route_store", None)
        return getter() if callable(getter) else None

    def _snapshot(self):
        getter = getattr(self.assistant, "custom_route_snapshot", None)
        return getter() if callable(getter) else dict(available=False, message="当前版本不支持自定义地图。")

    def _floor_data(self, map_id, floor):
        key = (map_id, floor)
        if key not in self._cache:
            getter = getattr(self.assistant, "custom_route_canvas", None)
            mask = getter(map_id, floor) if callable(getter) else None
            self._cache[key] = (mask, Walkable(mask)) if isinstance(mask, np.ndarray) and mask.ndim == 2 else (None, None)
            while len(self._cache) > 6:
                self._cache.pop(next(iter(self._cache)))
        return self._cache[key]

    def _route(self) -> FloorRoute:
        store = self._store()
        return store.route(self.map_id, self.floor) if store is not None and self.map_id else FloorRoute()

    def _commit(self, route: FloorRoute, *, record=True):
        store = self._store()
        if store is None or not self.map_id:
            return
        key = (self.map_id, self.floor)
        if record:
            history = self._history.setdefault(key, [])
            history.append(self._route())
            del history[:-40]
        saved = store.set_route(self.map_id, self.floor, route)
        self._feedback(None if saved else "保存失败，请检查软件目录是否可写。", error=not saved)
        self._refresh_rows()
        self._schedule_render()

    # Page sync -----------------------------------------------------------------
    def refresh(self):
        """Called when the page is shown and on the window's refresh tick."""
        snapshot = self._snapshot()
        maps = tuple(snapshot.get("maps") or ())
        store = self._store()
        signature = (snapshot.get("available"), snapshot.get("message"), maps,
                     store.revision if store is not None else None, snapshot.get("currentMapId"))
        self.in_game_var.set("开启" if snapshot.get("useInGame") else "关闭")
        if signature == self._signature:
            return
        self._signature = signature
        self._current = (snapshot.get("currentMapId"), snapshot.get("currentFloor"))
        if snapshot.get("currentMapId") in {item[0] for item in maps}:
            if not self.current_button.winfo_manager():
                self.current_button.pack(side="left", padx=(10, 0))
        else:
            self.current_button.pack_forget()
        if maps != self._maps:
            self._maps = maps
            self._cache.clear()
            self._build_rows()
            if self.map_id not in {item[0] for item in maps}:
                self.map_id = None
                current = snapshot.get("currentMapId")
                self._select(current if current in {item[0] for item in maps} else (maps[0][0] if maps else None))
        if not snapshot.get("available"):
            self.library_note.configure(text=snapshot.get("message") or "地图资源未就绪")
        self._refresh_rows()
        self._schedule_render()

    def _build_rows(self):
        for child in self.list_frame.winfo_children():
            child.destroy()
        self._rows = {}
        for map_id, name in self._maps:
            row = tk.Frame(self.list_frame, bg=SURFACE, cursor="hand2")
            row.pack(fill="x", pady=1)
            title = tk.Label(row, text=name, bg=SURFACE, fg=TEXT, font=(FONT, 10), anchor="w", padx=10, pady=6)
            title.pack(side="left", fill="x", expand=True)
            badge = tk.Label(row, text="", bg=SURFACE, fg=PRIMARY, font=(FONT, 8, "bold"), padx=8)
            badge.pack(side="right")
            for widget in (row, title, badge):
                widget.bind("<Button-1>", lambda _e, value=map_id: self._select(value))
                widget.bind("<Enter>", lambda _e, value=map_id: self._paint_row(value, hover=True))
                widget.bind("<Leave>", lambda _e, value=map_id: self._paint_row(value))
                widget.bindtags((self._wheel_tag, *widget.bindtags()))
            self._rows[map_id] = (row, title, badge)

    def _paint_row(self, map_id, *, hover=False):
        row = self._rows.get(map_id)
        if row is None:
            return
        selected = map_id == self.map_id
        bg = BUTTON_BG if selected else TINT if hover else SURFACE
        for widget in row:
            widget.configure(bg=bg)
        row[1].configure(fg=BUTTON_TEXT if selected else TEXT,
                         font=(FONT, 10, "bold" if selected else "normal"))

    def _refresh_rows(self):
        store = self._store()
        drawn = 0
        for map_id, (_row, _title, badge) in self._rows.items():
            floors = store.floors_with_routes(map_id) if store is not None else set()
            drawn += bool(floors)
            badge.configure(text=" ".join(SHORT_FLOORS[f] for f in FLOORS if f in floors))
            self._paint_row(map_id)
        if self._maps:
            self.library_note.configure(text=f"{len(self._maps)} 张 · 已画 {drawn} 张")

    def _select(self, map_id):
        if map_id is None:
            self.map_id = None
            self._schedule_render()
            return
        self.map_id = map_id
        name = dict(self._maps).get(map_id, map_id)
        self.map_title.configure(text=name)
        if self._floor_data(map_id, self.floor)[0] is None:
            available = [floor for floor in FLOORS if self._floor_data(map_id, floor)[0] is not None]
            if available:
                self.floor = available[0]
                self.floor_var.set(FLOOR_LABELS[self.floor])
        self._refresh_rows()
        self._feedback(None)
        self._schedule_render()

    def _jump_to_current(self):
        map_id, floor = getattr(self, "_current", (None, None))
        if floor in FLOORS:
            self.floor = floor
            self.floor_var.set(FLOOR_LABELS[floor])
        if map_id:
            self._select(map_id)

    def _floor_changed(self):
        self.floor = FLOOR_BY_LABEL.get(self.floor_var.get(), "1")
        self._feedback(None)
        self._schedule_render()

    def _toggle_in_game(self):
        enabled = self.in_game_var.get() == "开启"
        setter = getattr(self.assistant, "set_custom_route_in_game", None)
        result = setter(enabled) if callable(setter) else dict(success=False, message="当前版本不支持。")
        if isinstance(result, dict) and result.get("success") is False:
            self.in_game_var.set("关闭" if enabled else "开启")
        self._feedback(str((result or {}).get("message") or "") or None,
                       error=isinstance(result, dict) and result.get("success") is False)

    def _set_color(self, color):
        route = self._route()
        if route.color != color:
            self._commit(FloorRoute(route.points, color, route.snap))
        self._paint_tools(route=FloorRoute(route.points, color, route.snap))

    def _toggle_snap(self):
        route = self._route()
        self._commit(FloorRoute(route.points, route.color, not route.snap))

    def _undo(self):
        history = self._history.get((self.map_id, self.floor))
        if history:
            self._commit(history.pop(), record=False)

    def _clear(self):
        if not self._route().empty:
            self._commit(FloorRoute(color=self._route().color, snap=self._route().snap))
            self._feedback("已清空本层，可点“撤销”恢复。")

    def _feedback(self, message, *, error=False):
        self._message = (message, error)
        self._paint_status()

    def _paint_status(self):
        message, error = getattr(self, "_message", (None, False))
        if message:
            self.status.configure(text=message, fg=ERROR if error else MUTED)
            return
        route = self._route()
        mask = self._floor_data(self.map_id, self.floor)[0] if self.map_id else None
        if self.map_id and mask is None:
            text, color = "这一层的地图结构未安装，暂时不能编辑。", ERROR
        elif route.empty:
            text, color = "在地图上点击第一个路点开始画路线。", MUTED
        else:
            text, color = f"已自动保存 · {len(route.points)} 个路点", GREEN
        self.status.configure(text=text, fg=color)

    def _paint_tools(self, route=None):
        route = route or self._route()
        px = self.ui._px
        for color, swatch in self.swatches.items():
            swatch.configure(width=px(22), height=px(22))
            swatch.delete("all")
            size = px(22)
            if color == route.color:
                swatch.create_oval(1, 1, size - 1, size - 1, outline=color, width=2)
            swatch.create_oval(px(5), px(5), size - px(5), size - px(5), fill=color, outline="")
        self.snap_button.configure(text="贴合通道：开" if route.snap else "贴合通道：关",
                                   bg=BUTTON_BG if route.snap else IMAGE_BG,
                                   fg=BUTTON_TEXT if route.snap else MUTED)
        history = self._history.get((self.map_id, self.floor))
        self.undo_button.configure(state="normal" if history else "disabled")
        self.clear_button.configure(state="disabled" if route.empty else "normal")

    # Canvas --------------------------------------------------------------------
    def _schedule_render(self):
        if self._render_job is None:
            self._render_job = self.after(16, self._render)

    def _render(self):
        self._render_job = None
        canvas = self.canvas
        canvas.delete("all")
        self._paint_tools()
        self._paint_status()
        width, height = canvas.winfo_width(), canvas.winfo_height()
        mask, walkable = self._floor_data(self.map_id, self.floor) if self.map_id else (None, None)
        if mask is None or width < 20 or height < 20:
            self._view = None
            text = "选择左侧的地图开始绘制" if not self.map_id else "该层地图结构未安装"
            canvas.create_text(width / 2, height / 2, text=text, fill=SUBTLE, font=(FONT, 10))
            return
        native_h, native_w = mask.shape
        margin = 12
        scale = min((width - margin * 2) / native_w, (height - margin * 2) / native_h)
        size = (max(1, round(native_w * scale)), max(1, round(native_h * scale)))
        ox, oy = (width - size[0]) / 2, (height - size[1]) / 2
        self._view = (scale, ox, oy)
        key = (self.map_id, self.floor, size)
        if key != self._picture_key:
            self._picture = ImageTk.PhotoImage(_map_picture(mask, size), master=canvas)
            self._picture_key = key
        canvas.create_image(ox, oy, image=self._picture, anchor="nw")
        route = self._route()
        if self._drag is not None:
            points = list(route.points)
            points[self._drag["index"]] = self._drag["point"]
            route = FloorRoute(tuple(points), route.color, route.snap)
        segments = (route_polyline(route, walkable) if self._drag is None
                    else [[a, b] for a, b in zip(route.points, route.points[1:])])
        line = max(3, round(self.ui._px(4)))
        edge = _darker(route.color)
        for segment in segments:
            flat = [value for x, y in segment for value in (ox + x * scale, oy + y * scale)]
            canvas.create_line(*flat, fill=edge, width=line + 3, capstyle="round", joinstyle="round")
        for segment in segments:
            flat = [value for x, y in segment for value in (ox + x * scale, oy + y * scale)]
            canvas.create_line(*flat, fill=route.color, width=line, capstyle="round", joinstyle="round")
        radius = self.ui._px(10)
        for index, (x, y) in enumerate(route.points, start=1):
            cx, cy = ox + x * scale, oy + y * scale
            canvas.create_oval(cx - radius, cy - radius, cx + radius, cy + radius,
                               fill="white", outline=edge, width=2)
            canvas.create_text(cx, cy, text=str(index), fill=edge, font=(FONT, 8, "bold"))

    def _native(self, event):
        if self._view is None:
            return None
        scale, ox, oy = self._view
        return (round((event.x - ox) / scale), round((event.y - oy) / scale))

    def _hit(self, event):
        if self._view is None:
            return None
        scale, ox, oy = self._view
        best = None
        for index, (x, y) in enumerate(self._route().points):
            distance = ((ox + x * scale - event.x) ** 2 + (oy + y * scale - event.y) ** 2) ** .5
            if distance <= self.ui._px(HIT_RADIUS) and (best is None or distance < best[1]):
                best = (index, distance)
        return best[0] if best else None

    def _place(self, point):
        """Snap a native point when the route follows corridors."""
        mask, walkable = self._floor_data(self.map_id, self.floor)
        route = self._route()
        if route.snap and walkable is not None:
            return walkable.snap(point)
        height, width = mask.shape
        return (min(width - 1, max(0, point[0])), min(height - 1, max(0, point[1])))

    def _press(self, event):
        index = self._hit(event)
        if index is not None:
            self._drag = dict(index=index, point=self._route().points[index], moved=False)
            return
        point = self._native(event)
        if point is None:
            return
        route = self._route()
        if len(route.points) >= MAX_POINTS:
            self._feedback(f"每层最多 {MAX_POINTS} 个路点。", error=True)
            return
        placed = self._place(point)
        if placed is None:
            self._feedback("这里不在地图范围内，换个位置试试。", error=True)
            return
        self._feedback(None)
        self._commit(FloorRoute((*route.points, placed), route.color, route.snap))

    def _motion(self, event):
        if self._drag is None:
            return
        point = self._native(event)
        if point is not None:
            self._drag.update(point=point, moved=True)
            self._schedule_render()

    def _release(self, _event):
        drag, self._drag = self._drag, None
        if drag is None or not drag["moved"]:
            self._schedule_render()
            return
        placed = self._place(drag["point"])
        if placed is None:
            self._feedback("这里不在地图范围内，路点已复位。", error=True)
            self._schedule_render()
            return
        route = self._route()
        points = list(route.points)
        points[drag["index"]] = placed
        self._commit(FloorRoute(tuple(points), route.color, route.snap))

    def _remove_at(self, event):
        index = self._hit(event)
        if index is None:
            return
        route = self._route()
        points = list(route.points)
        del points[index]
        self._commit(FloorRoute(tuple(points), route.color, route.snap))

    def _destroy(self, event):
        if event.widget is self and self._render_job is not None:
            self.after_cancel(self._render_job)
            self._render_job = None
