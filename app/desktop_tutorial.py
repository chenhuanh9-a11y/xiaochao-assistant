"""In-app tutorial: chapter index, step player and the per-user progress file.

DesktopMainUI owns the window and opens it from the help page and home. The
tutorial only reads hotkey bindings and switches main pages; it never starts a
round or changes settings. Step images load one at a time and are released
when the window closes, so building the help page decodes no artwork.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import tkinter as tk

from PIL import Image, ImageOps, ImageTk

from desktop_display import WindowDisplay
from desktop_main_ui import ACTION_NAMES, _button, _icon_tile, _rounded_image
from desktop_rounded import RoundedFrame, set_rounded_scale
from desktop_theme import (BG, SURFACE, LINE, MUTED, SUBTLE, PRIMARY, BUTTON_BG, BUTTON_TEXT, GREEN,
                           SUCCESS_BG, IMAGE_BG, INPUT_BG, SCROLL_THUMB, SCROLL_HOVER, NIGHT, NIGHT_TEXT,
                           NIGHT_MUTED, NIGHT_HOVER, STAR_GOLD, FONT)


def _step(title, text, image, *, tips=(), keys=(), sketch=False):
    return dict(title=title, text=text, image=image, tips=tuple(tips), keys=tuple(keys), sketch=sketch)


# Texts follow the player's real order. Keys name binding targets; the window
# shows the player's current binding next to the "新装默认" value in the text.
CHAPTERS = (
    dict(id="start", title="快速上手", summary="星图能做什么，三步怎么用", icon="help", page=None, steps=(
        _step("星图能帮你做什么",
              "打开游戏地图时，星图会认出当前地图和楼层，把作者路线小抄直接贴在地图上。"
              "还可以打开定点地图，把一张小抄固定显示在屏幕上。",
              "start-1.webp", sketch=True,
              tips=("贴合小抄：跟着游戏地图走，开图就贴上", "定点地图：固定在你摆好的位置")),
        _step("第一次打开：核对地图键",
              "首次启动会出现“准备快捷键”。地图键要和游戏里打开地图的键一致，默认是 G；"
              "再用控件键把游戏控件显示、隐藏各一次。",
              "start-2.webp", keys=("map", "toggle-panel"),
              tips=("之后想改，到设置页的快捷键里点键帽重新录入",)),
        _step("用法只有三步",
              "选好难度和作者路线 → 开始本局 → 回到游戏按地图键开图。后面几章会一步一步讲。",
              "start-3.webp", sketch=True),
        _step("遇到问题先看帮助页",
              "左侧“帮助”里有版本、当前运行状态和导出排查信息，这份教程也随时能从这里打开。",
              "start-4.webp"),
    )),
    dict(id="route", title="选难度和作者路线", summary="在设置页选好这局用的路线", icon="settings",
         page="preferences", steps=(
        _step("找到“游戏与路线”",
              "点左侧“设置”，找到“游戏与路线”。游戏难度和作者路线都在这里。",
              "route-1.webp", tips=("看不到选项时，点一下卡片标题展开",)),
        _step("选游戏难度",
              "噩梦和困难二选一，要和你这局实际打的难度一致。换难度后，作者路线的列表也会跟着换。",
              "route-2.webp"),
        _step("选作者路线",
              "在“作者路线”里选你习惯的作者，游戏里贴的就是这位作者的路线。"
              "换作者只换显示的路线，不会改变地图识别。",
              "route-3.webp", tips=("列表里没有想要的作者？先到“地图资源”导入图包",)),
    )),
    dict(id="round", title="开局与自动贴合", summary="开始本局，按地图键自动贴合", icon="play", page="home", steps=(
        _step("开局前点“开始本局”",
              "在主页面点“开始本局”，或按开始/结束本局键（新装默认 F10）。显示“已准备”后切回游戏。",
              "round-1.webp", keys=("toggle-round",),
              tips=("“已准备”还不算开始，等你在游戏里打开地图才真正开始",
                    "不打了就点“取消准备”；取消准备不等于结束一局")),
        _step("回到游戏，按地图键开图",
              "进入对局后，按地图键（默认 G）打开游戏地图，把地图完整打开。",
              "round-2.webp", keys=("map",), sketch=True),
        _step("自动识别并贴合",
              "星图会自动认出地图和楼层，把作者路线小抄贴到地图上，不需要手动选图。",
              "round-3.webp", sketch=True,
              tips=("楼层显示“待确认”时，把地图完整打开稍等一下", "一直认不出来，看“排查与反馈”一章")),
        _step("关图再开、换楼层都会跟上",
              "关掉地图再打开，或在游戏地图上切换楼层，小抄都会自动重新贴合。",
              "round-4.webp", sketch=True),
        _step("打完点“结束本局”",
              "这局结束后，在主页面点“结束本局”，或再按一次开始/结束本局键。"
              "结束后停止本局的定位和贴图，设置、日志和作者图都会保留。",
              "round-5.webp", keys=("toggle-round",)),
    )),
    dict(id="control", title="游戏控件", summary="悬浮球、收起和显隐开关", icon="panel", page=None, steps=(
        _step("呼出游戏控件",
              "在游戏里按控件键（新装默认 F9），屏幕上会出现游戏控件，能看到本局状态、地图和楼层；再按一次隐藏。",
              "control-1.webp", keys=("toggle-panel",), sketch=True),
        _step("贴合和定点分开开关",
              "“贴合”管贴在游戏地图上的小抄，“定点”管固定显示的定点地图，两个互不影响。",
              "control-2.webp", sketch=True, tips=("不透明度在设置页的“小抄显示”里调，0% 是完全透明",
                                      "想在控件里放更多内容，到设置页“游戏控件”点“自定义…”")),
        _step("收起成悬浮球",
              "点右上角“收起”，控件会缩成一个小球，不挡视线；点球上的“展开”恢复。",
              "control-3.webp", sketch=True, tips=("收起后小球还在；按控件键是把整个控件隐藏",)),
        _step("临时隐藏小抄",
              "按显示/隐藏小抄键（新装默认 F11）可以临时藏起小抄，再按一次显示。"
              "隐藏期间识别和贴合照常进行，再显示时位置还是对的。",
              "control-4.webp", keys=("toggle-sheet-visibility",), sketch=True),
    )),
    dict(id="reference", title="定点地图", summary="固定显示一张小抄，自己摆位置", icon="eye",
         page="preferences", steps=(
        _step("打开定点地图",
              "在设置页“小抄显示”里找到“定点地图”，点“显示”；游戏控件里的“定点”也能开关。"
              "定点地图固定显示在屏幕上，不跟着游戏地图走。",
              "reference-1.webp"),
        _step("点“编辑位置”，拖到顺手的地方",
              "点“编辑位置”后，按住定点地图拖到合适的位置。移动后仍保持编辑状态，可以继续调整。",
              "reference-2.webp", sketch=True,
              tips=("也可以把“编辑位置”放到游戏控件上：设置页“游戏控件”点“自定义…”",)),
        _step("点“完成”",
              "调好后点同一个按钮上的“完成”，定点地图恢复鼠标穿透，不会挡住游戏里的点击。",
              "reference-3.webp"),
    )),
    dict(id="keys", title="常用快捷键", summary="查看和修改快捷键", icon="keyboard", page="preferences", steps=(
        _step("以主页上的键帽为准",
              "主页面右侧的“快捷键”卡显示你当前的绑定。新装默认：地图 G、游戏控件 F9、"
              "开始/结束本局 F10、显示/隐藏小抄 F11；从旧版本升级的可能不一样。",
              "keys-1.webp", keys=("map", "toggle-panel", "toggle-round", "toggle-sheet-visibility")),
        _step("在设置页改键",
              "在设置页的“快捷键”里点键帽，按下新的组合，核对后点“确认”。"
              "和别的键冲突时会提示，重新录一次就好；取消会保留原来的键。",
              "keys-2.webp"),
    )),
    dict(id="resources", title="导入作者图包", summary="地图资源页导入并使用", icon="map", page="maps", steps=(
        _step("打开“地图资源”",
              "点左侧“地图资源”，可以看到当前难度下各位作者的图包是否已经安装。",
              "resources-1.webp"),
        _step("导入本地图包",
              "拿到作者图包（ZIP）后，点右上角“导入本地图包”选择文件，等待导入完成。",
              "resources-2.webp"),
        _step("点“使用”才会切换",
              "导入不会自动换作者。选中这位作者，点“使用××”，游戏里才会换成这位作者的路线。",
              "resources-3.webp", tips=("也可以在设置页的“作者路线”里切换",)),
    )),
    dict(id="custom", title="自定义地图", summary="自己画路线，进游戏自动贴合", icon="route", page="custom", steps=(
        _step("复制一份图库",
              "打开“自定义地图”。默认图库只读，先在左边点“复制”，再在副本上编辑。",
              "custom-1.webp", tips=("打开一张地图后，也可以点“复制为我的图库”",)),
        _step("点房间添加目标",
              "打开一张地图，按顺序点击要去的房间，星图会自动连好可走的路线；点道路可以加必经点。",
              "custom-2.webp", sketch=True, tips=("改动会自动保存",)),
        _step("在游戏中使用",
              "画好后点“在游戏中使用”，进游戏开图时贴的就是你画的路线。不想用了点“停止使用”。",
              "custom-3.webp"),
    )),
    dict(id="trouble", title="排查与反馈", summary="出问题时怎么看、怎么导出", icon="note", page="help", steps=(
        _step("先看当前运行信息",
              "帮助页里有版本和当前运行状态，先看看提示说了什么，很多问题按提示就能解决。",
              "trouble-1.webp"),
        _step("记下发生了什么",
              "记下时间、难度、地图或楼层、你做了什么、等了多久，以及当前的快捷键。",
              "trouble-2.webp", sketch=True),
        _step("导出排查信息",
              "点帮助页里的“导出排查信息”。文件会附上最近失败时的游戏画面和识别记录，"
              "先自己看一下内容，再发给反馈的人。",
              "trouble-3.webp"),
    )),
)
CHAPTER_IDS = tuple(chapter["id"] for chapter in CHAPTERS)
PAGE_ACTIONS = {"preferences": "去设置页", "home": "回到主页面", "maps": "去地图资源",
                "custom": "去自定义地图", "help": "去帮助页"}

FAQ = (
    ("打开地图后没有贴上小抄", ("先确认主页面已点“开始本局”", "地图键要和游戏里的一致",
                          "把游戏地图完整打开，稍等一下", "检查贴合小抄有没有被隐藏")),
    ("贴上了，但楼层不对", ("在游戏地图上切到正确的楼层再看", "仍然不对，到帮助页导出排查信息")),
    ("小抄太淡或者看不见", ("在设置页“小抄显示”里调高贴合不透明度，0% 是完全透明", "确认游戏控件里的“贴合”是打开的")),
    ("快捷键没反应", ("以主页面“快捷键”卡上的键帽为准", "和其他软件冲突时，到设置页重新录入")),
    ("导入图包后路线没变", ("导入不会自动换作者", "到地图资源点“使用××”，或在设置页选作者路线")),
    ("游戏控件挡住了画面", ("点“收起”缩成悬浮球", "或按控件键（新装默认 F9）把整个控件隐藏")),
    ("“取消准备”和“结束本局”有什么不同", ("“已准备”时还没开始，取消准备只是不开这局",
                                   "“结束本局”停止正在进行的定位和贴图")),
)


def step_images():
    return tuple(step["image"] for chapter in CHAPTERS for step in chapter["steps"])


def find_chapter(chapter_id):
    return next((chapter for chapter in CHAPTERS if chapter["id"] == chapter_id), None)


class TutorialProgress:
    """Seen chapters and the one-time home invite. Losing this file only re-shows hints."""

    def __init__(self, path) -> None:
        self.path = Path(path)
        self.done: set[str] = set()
        self.invited = False
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            data = None
        if isinstance(data, dict):
            done = data.get("done")
            if isinstance(done, list):
                self.done = {value for value in done if value in CHAPTER_IDS}
            self.invited = data.get("invited") is True

    def mark_done(self, chapter_id) -> bool:
        if chapter_id not in CHAPTER_IDS or chapter_id in self.done:
            return False
        self.done.add(chapter_id)
        self._save()
        return True

    def mark_invited(self) -> None:
        if not self.invited:
            self.invited = True
            self._save()

    def _save(self) -> None:
        payload = dict(version=1, done=sorted(self.done), invited=self.invited)
        temp = self.path.with_name(self.path.name + ".tmp")
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            temp.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
            os.replace(temp, self.path)
        except OSError:
            pass  # Progress is a convenience; the tutorial itself keeps working.


class TutorialWindow:
    """One stable window with an index view and a step player."""

    IMAGE_MAX_DIP = (960, 540)

    def __init__(self, ui, progress, image_root, *, on_progress=None) -> None:
        self.ui = ui
        self.progress = progress
        self.image_root = Path(image_root)
        self.on_progress = on_progress
        self.window = None
        self.display = None
        self.chapter = None
        self.step = 0
        self._shown = None
        self._keys_text = None
        self._source = None
        self._photo = None
        self._image_key = None
        self._image_job = None
        self._image_generation = 0
        self._icon_photos = {}

    # Lifecycle --------------------------------------------------------------
    def open(self, chapter_id=None) -> None:
        if self.window is None or not self.window.winfo_exists():
            self._build()
        if chapter_id and find_chapter(chapter_id):
            self.show_chapter(chapter_id)
        elif self.chapter is None:
            self.show_index()
        else:
            self._render_step()
        self.window.deiconify()
        self.window.lift()
        self.window.focus_set()

    def close(self) -> None:
        if self.window is None:
            return
        self._release_image()
        self.window.withdraw()

    def _px(self, value):
        return round(value * (self.display.scale if self.display is not None else 1.0))

    def _display_changed(self, scale) -> None:
        set_rounded_scale(self.window, scale)
        self._icon_photos = {}
        self._apply_icons()
        self._draw_progress()
        self._image_key = None
        self._queue_image()

    def _destroyed(self, event) -> None:
        if event.widget is not self.window:
            return
        self._release_image()
        self.window = None
        self.display = None
        self.chapter = None
        self._shown = None

    def _release_image(self) -> None:
        self._image_generation += 1
        if self._image_job is not None and self.window is not None:
            try:
                self.window.after_cancel(self._image_job)
            except tk.TclError:
                pass
        self._image_job = None
        if self._source is not None:
            self._source[1].close()
            self._source = None
        self._photo = None
        self._image_key = None
        try:
            self.stage_label.configure(image="")
        except (AttributeError, tk.TclError):
            pass

    # Construction -----------------------------------------------------------
    def _build(self) -> None:
        ui = self.ui
        window = self.window = tk.Toplevel(ui.window)
        window.withdraw()
        window.title("使用教程 · 星图")
        window.configure(bg=BG)
        window.protocol("WM_DELETE_WINDOW", self.close)
        window.bind("<Escape>", self._escape)
        window.bind("<Left>", lambda _event: self.go(self.step - 1))
        window.bind("<Right>", lambda _event: self.go(self.step + 1))
        window.bind("<FocusIn>", lambda event: self._sync_keys() if event.widget is window else None)
        window.bind("<MouseWheel>", self._wheel)
        window.bind("<Destroy>", self._destroyed, add="+")
        self._icon_labels = []
        header = tk.Frame(window, bg=BG)
        header.pack(fill="x", padx=28, pady=(18, 12))
        self.back_button = ui._ghost(header, "‹  目录", self.show_index, size=9)
        self.count_label = ui._label(header, size=9, color=SUBTLE)
        self.count_label.pack(side="right", anchor="s")
        words = tk.Frame(header, bg=BG)
        words.pack(side="left", fill="x", expand=True)
        self.title_label = ui._label(words, "使用教程", size=18, bold=True, anchor="w")
        self.title_label.pack(fill="x")
        self.subtitle_label = ui._label(words, size=9, color=MUTED, anchor="w")
        self.subtitle_label.pack(fill="x", pady=(2, 0))
        body = tk.Frame(window, bg=BG)
        body.pack(fill="both", expand=True)
        self.index_view = self._build_index(body)
        self.chapter_view = self._build_chapter(body)
        # Own DIP scaling like the main window; it centers on the owner's monitor.
        self.display = WindowDisplay(window, ui.assistant, preferred=(900, 740), minimum=(620, 580),
                                     on_scale=self._display_changed)
        self.display.start(ui.window)
        set_rounded_scale(window, self.display.scale)
        self._apply_icons()

    def _build_index(self, parent):
        ui = self.ui
        view = tk.Frame(parent, bg=BG)
        canvas = tk.Canvas(view, bg=BG, highlightthickness=0, bd=0, width=1, height=1)
        bar = tk.Scrollbar(view, orient="vertical", command=canvas.yview, bg=SCROLL_THUMB,
                           activebackground=SCROLL_HOVER, troughcolor=BG, relief="flat", bd=0,
                           highlightthickness=0, width=11)
        canvas.configure(yscrollcommand=bar.set)
        bar.pack(side="right", fill="y")
        canvas.pack(side="left", fill="both", expand=True)
        content = tk.Frame(canvas, bg=BG)
        item = canvas.create_window((0, 0), window=content, anchor="nw")

        def fit(_event=None):
            width = max(1, canvas.winfo_width())
            if round(float(canvas.itemcget(item, "width"))) != width:
                canvas.itemconfigure(item, width=width)
            canvas.configure(scrollregion=canvas.bbox("all"))
        content.bind("<Configure>", fit)
        canvas.bind("<Configure>", fit)
        self.index_canvas = canvas
        inner = tk.Frame(content, bg=BG)
        inner.pack(fill="x", padx=28, pady=(0, 18))

        hero = ui._card(inner, padx=26, pady=22, bg=NIGHT, line=False)
        hero.pack(fill="x", pady=(0, 14))
        ui._label(hero, "一分钟看懂星图怎么用", size=16, bold=True, color=NIGHT_TEXT, anchor="w").pack(fill="x")
        ui._label(hero, "不知道从哪开始？先看“快速上手”，再挑你要用的功能。", size=10, color=NIGHT_MUTED,
                  anchor="w", justify="left", wraplength=560).pack(fill="x", pady=(6, 12))
        chips = tk.Frame(hero, bg=NIGHT)
        chips.pack(fill="x")
        for index, text in enumerate(("选路线", "开始本局", "按地图键开图"), 1):
            chip = RoundedFrame(chips, bg=NIGHT_HOVER, radius=10, padx=10, pady=4, highlightthickness=0)
            chip.pack(side="left", padx=(0, 8))
            ui._label(chip, f"{index}  {text}", size=9, bold=True, color=NIGHT_TEXT).pack()
        start = _button(hero, "开始学习", lambda: self.show_chapter("start"), primary=True)
        start.pack(anchor="w", pady=(16, 0))

        ui._label(inner, "按功能学习", size=12, bold=True, anchor="w").pack(fill="x", pady=(6, 10))
        grid = tk.Frame(inner, bg=BG)
        grid.pack(fill="x")
        grid.columnconfigure((0, 1), weight=1, uniform="chapter")
        self._done_labels = {}
        for index, chapter in enumerate(CHAPTERS):
            card = ui._card(grid, padx=16, pady=14)
            card.grid(row=index // 2, column=index % 2, sticky="nsew",
                      padx=(0, 6) if index % 2 == 0 else (6, 0), pady=(0, 12))
            row = tk.Frame(card, bg=SURFACE)
            row.pack(fill="x")
            tile = tk.Label(row, bg=SURFACE, bd=0)
            tile._xingtu_icon = chapter["icon"]
            tile.pack(side="left", anchor="n", padx=(0, 12))
            self._icon_labels.append(tile)
            ui._label(row, "›", size=14, color=SUBTLE).pack(side="right")
            words = tk.Frame(row, bg=SURFACE)
            words.pack(side="left", fill="x", expand=True)
            ui._label(words, chapter["title"], size=11, bold=True, anchor="w").pack(fill="x")
            ui._label(words, f"{chapter['summary']} · {len(chapter['steps'])} 步", size=9, color=SUBTLE,
                      anchor="w", justify="left", wraplength=220).pack(fill="x", pady=(2, 0))
            done = tk.Label(words, text="已看完", bg=SUCCESS_BG, fg=GREEN, font=(FONT, 8, "bold"), padx=6, pady=1)
            self._done_labels[chapter["id"]] = done
            ui._clickable(card, lambda chapter_id=chapter["id"]: self.show_chapter(chapter_id))
        self._sync_done()

        ui._label(inner, "常见问题", size=12, bold=True, anchor="w").pack(fill="x", pady=(10, 10))
        faq = ui._card(inner, padx=20, pady=6)
        faq.pack(fill="x")
        for index, (question, answers) in enumerate(FAQ):
            if index:
                tk.Frame(faq, bg=LINE, height=1).pack(fill="x")
            item_frame = tk.Frame(faq, bg=SURFACE)
            item_frame.pack(fill="x")
            head = tk.Frame(item_frame, bg=SURFACE)
            head.pack(fill="x", pady=10)
            chevron = ui._label(head, "›", size=13, color=SUBTLE)
            chevron.pack(side="right")
            ui._label(head, question, size=10, bold=True, anchor="w").pack(side="left", fill="x", expand=True)
            answer = tk.Frame(item_frame, bg=SURFACE)
            for line in answers:
                ui._label(answer, f"·  {line}", size=9, color=MUTED, anchor="w", justify="left",
                          wraplength=560).pack(fill="x", pady=(0, 4))
            ui._clickable(head, lambda answer=answer, chevron=chevron: self._toggle_answer(answer, chevron))
        return view

    def _build_chapter(self, parent):
        ui = self.ui
        view = tk.Frame(parent, bg=BG)
        card = ui._card(view, padx=24, pady=18)
        card.pack(fill="both", expand=True, padx=28, pady=(0, 20))
        self.progress_bar = tk.Canvas(card, width=1, height=4, bg=SURFACE, highlightthickness=0, bd=0)
        self.progress_bar.pack(fill="x", pady=(0, 14))
        self.progress_bar.bind("<Configure>", lambda _event: self._draw_progress())
        nav = tk.Frame(card, bg=SURFACE)
        nav.pack(side="bottom", fill="x", pady=(14, 0))
        self.next_button = _button(nav, "下一步", self._next, primary=True)
        self.next_button.pack(side="right")
        self.prev_button = _button(nav, "上一步", lambda: self.go(self.step - 1))
        self.prev_button.pack(side="right", padx=(0, 8))
        self.page_button = ui._ghost(nav, "", self._open_page, size=9)
        ui._label(nav, "← → 键也能切换", size=8, color=SUBTLE).pack(side="left")
        caption = tk.Frame(card, bg=SURFACE)
        caption.pack(side="bottom", fill="x")
        top = tk.Frame(caption, bg=SURFACE)
        top.pack(fill="x", pady=(12, 6))
        self.badge_label = tk.Label(top, bg=BUTTON_BG, fg=BUTTON_TEXT, font=(FONT, 8, "bold"), padx=8, pady=2)
        self.badge_label.pack(side="left")
        self.sketch_label = ui._label(top, "示意图 · 以实际画面为准", size=8, color=SUBTLE)
        self.heading_label = ui._label(caption, size=14, bold=True, anchor="w", justify="left", wraplength=760)
        self.heading_label.pack(fill="x")
        self.text_label = ui._label(caption, size=10, anchor="w", justify="left", wraplength=760)
        self.text_label.pack(fill="x", pady=(6, 0))
        self.keys_label = ui._label(caption, size=9, color=PRIMARY, anchor="w", justify="left", wraplength=760)
        self.tips_frame = tk.Frame(caption, bg=SURFACE)
        self.tip_labels = [ui._label(self.tips_frame, size=9, color=MUTED, anchor="w", justify="left",
                                     wraplength=740) for _ in range(3)]
        self.stage = tk.Frame(card, bg=SURFACE)
        self.stage.pack(fill="both", expand=True)
        self.stage_label = tk.Label(self.stage, bg=SURFACE, bd=0, fg=MUTED, font=(FONT, 9))
        self.stage_label.place(relx=.5, rely=.5, anchor="center")
        self.stage.bind("<Configure>", self._queue_image)
        return view

    def _apply_icons(self) -> None:
        size = self._px(36)
        for tile in self._icon_labels:
            key = tile._xingtu_icon, size
            if key not in self._icon_photos:
                self._icon_photos[key] = ImageTk.PhotoImage(
                    _icon_tile(self.ui._icons, tile._xingtu_icon, size), master=self.window)
            tile.configure(image=self._icon_photos[key])

    # Views ------------------------------------------------------------------
    def show_index(self) -> None:
        if self.window is None:
            return
        self._release_image()
        self.chapter = None
        self._shown = None
        self.chapter_view.pack_forget()
        self.back_button.pack_forget()
        self.title_label.configure(text="使用教程")
        self.subtitle_label.configure(text="跟着图一步步上手，看完的章节会标上“已看完”。")
        self.count_label.configure(text="")
        self._sync_done()
        self.index_view.pack(fill="both", expand=True)

    def show_chapter(self, chapter_id, step=0) -> None:
        chapter = find_chapter(chapter_id)
        if chapter is None or self.window is None:
            return
        self.chapter = chapter
        self.step = max(0, min(len(chapter["steps"]) - 1, step))
        self.index_view.pack_forget()
        self.back_button.pack(side="left", anchor="n", padx=(0, self._px(12)), pady=(self._px(4), 0),
                              before=self.title_label.master)
        self.title_label.configure(text=chapter["title"])
        self.subtitle_label.configure(text=chapter["summary"])
        self.chapter_view.pack(fill="both", expand=True)
        self._render_step()

    def go(self, index) -> None:
        if self.chapter is None:
            return
        index = max(0, min(len(self.chapter["steps"]) - 1, index))
        if index != self.step:
            self.step = index
            self._render_step()

    def _next(self) -> None:
        if self.chapter is None:
            return
        if self.step < len(self.chapter["steps"]) - 1:
            self.go(self.step + 1)
            return
        if self.progress.mark_done(self.chapter["id"]) and callable(self.on_progress):
            self.on_progress()
        self.show_index()

    def _render_step(self) -> None:
        chapter = self.chapter
        signature = chapter["id"], self.step
        if signature == self._shown:
            self._sync_keys()
            return
        self._shown = signature
        steps = chapter["steps"]
        step = steps[self.step]
        last = self.step == len(steps) - 1
        self.count_label.configure(text=f"{self.step + 1} / {len(steps)}")
        self.badge_label.configure(text=f"第 {self.step + 1} 步")
        self.heading_label.configure(text=step["title"])
        self.text_label.configure(text=step["text"])
        if step["sketch"]:
            self.sketch_label.pack(side="left", padx=(self._px(10), 0))
        else:
            self.sketch_label.pack_forget()
        self._keys_text = None
        self._sync_keys()
        tips = step["tips"]
        for label, tip in zip(self.tip_labels, tips + ("",) * len(self.tip_labels)):
            label.configure(text=f"·  {tip}" if tip else "")
            if tip:
                label.pack(fill="x", pady=(0, self._px(2)))
            else:
                label.pack_forget()
        if tips:
            self.tips_frame.pack(fill="x", pady=(self._px(8), 0))
        else:
            self.tips_frame.pack_forget()
        self.prev_button.configure(state="disabled" if self.step == 0 else "normal")
        self.next_button.configure(text="看完了" if last else "下一步")
        if last and chapter["page"]:
            self.page_button.configure(text=f"{PAGE_ACTIONS[chapter['page']]}  ›")
            self.page_button.pack(side="right", padx=(0, self._px(12)))
        else:
            self.page_button.pack_forget()
        self._draw_progress()
        self._release_image()
        self._queue_image()

    def _sync_keys(self) -> None:
        if self.chapter is None or self.window is None:
            return
        targets = self.chapter["steps"][self.step]["keys"]
        text = ""
        if targets:
            getter = getattr(self.ui.assistant, "hotkey_editor_snapshot", None)
            try:
                bindings = (getter() if callable(getter) else {}).get("bindings") or {}
            except Exception:  # A failed snapshot only hides the hint line.
                bindings = {}
            values = [f"{ACTION_NAMES.get(target, target)} {(bindings.get(target) or {}).get('text') or '未设置'}"
                      for target in targets]
            text = "你当前的绑定：" + "；".join(values)
        if text == self._keys_text:
            return
        self._keys_text = text
        self.keys_label.configure(text=text)
        if text:
            self.keys_label.pack(fill="x", pady=(self._px(6), 0), after=self.text_label)
        else:
            self.keys_label.pack_forget()

    def _draw_progress(self) -> None:
        bar = self.progress_bar
        bar.delete("all")
        if self.chapter is None:
            return
        count = len(self.chapter["steps"])
        width, gap = max(1, bar.winfo_width()), self._px(6)
        segment = max(1, (width - gap * (count - 1)) / count)
        for index in range(count):
            x = index * (segment + gap)
            bar.create_rectangle(x, 0, x + segment, max(2, bar.winfo_height()), width=0,
                                 fill=PRIMARY if index <= self.step else IMAGE_BG)

    def _sync_done(self) -> None:
        for chapter_id, label in getattr(self, "_done_labels", {}).items():
            if chapter_id in self.progress.done:
                if not label.winfo_manager():
                    label.pack(anchor="w", pady=(self._px(6), 0))
            elif label.winfo_manager():
                label.pack_forget()

    def _toggle_answer(self, answer, chevron) -> None:
        if answer.winfo_manager():
            answer.pack_forget()
            chevron.configure(text="›")
        else:
            answer.pack(fill="x", pady=(0, self._px(8)))
            chevron.configure(text="⌄")

    # Images -----------------------------------------------------------------
    def _queue_image(self, _event=None) -> None:
        if self.window is None or self.chapter is None:
            return
        if self._image_job is not None:
            self.window.after_cancel(self._image_job)
        self._image_generation += 1
        generation = self._image_generation
        self._image_job = self.window.after(60, lambda: self._render_image(generation))

    def _render_image(self, generation) -> None:
        if self.window is None or self.chapter is None or generation != self._image_generation:
            return
        self._image_job = None
        width, height = self.stage.winfo_width(), self.stage.winfo_height()
        if width <= 1 or height <= 1:
            return
        name = self.chapter["steps"][self.step]["image"]
        box = (min(width, self._px(self.IMAGE_MAX_DIP[0])), min(height, self._px(self.IMAGE_MAX_DIP[1])))
        if self._image_key == (name, box):
            return
        try:
            if self._source is None or self._source[0] != name:
                if self._source is not None:
                    self._source[1].close()
                    self._source = None
                with Image.open(self.image_root / name) as source:
                    self._source = name, source.convert("RGB")
            with ImageOps.contain(self._source[1], box, Image.Resampling.LANCZOS) as fitted:
                rounded = _rounded_image(fitted, self._px(12), SURFACE)
            self._photo = ImageTk.PhotoImage(rounded, master=self.window)
            self.stage_label.configure(image=self._photo, text="")
        except (OSError, ValueError, Image.DecompressionBombError):
            self._photo = None
            self.stage_label.configure(image="", text="这一步的配图暂不可用，按下方文字操作即可。")
        self._image_key = name, box

    # Input ------------------------------------------------------------------
    def _open_page(self) -> None:
        page = self.chapter["page"] if self.chapter else None
        if not page:
            return
        self.ui.show()
        self.ui.show_page(page)

    def _escape(self, _event=None):
        if self.chapter is not None:
            self.show_index()
        else:
            self.close()
        return "break"

    def _wheel(self, event):
        if self.chapter is None and self.index_view.winfo_manager():
            self.index_canvas.yview_scroll(-1 if event.delta > 0 else 1, "units")
