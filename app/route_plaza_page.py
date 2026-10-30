"""Route plaza page and the account dialog.

The list of shared nightmare routes is public; downloading signs in first.
A downloaded gallery appears read-only under 自定义地图, where it can be used
in game or copied for editing. Network calls run on worker threads and their
results come back through ``after`` polling on the Tk thread.
"""
from __future__ import annotations

import queue
import threading
import tkinter as tk

from account_service import AccountError
from desktop_display import WindowDisplay
from desktop_rounded import RoundedButton, RoundedFrame
from desktop_theme import (BG, BUTTON_BG, BUTTON_HOVER, BUTTON_TEXT, ERROR, FONT, GREEN, INPUT_BG, LINE, MUTED,
                           ON_PRIMARY, PRIMARY, PRIMARY_HOVER, SUBTLE, SURFACE, TEXT)
import route_plaza

POLL_MS = 80


def _button(parent, text, command, *, primary=False, size=10):
    return RoundedButton(parent, text=text, command=command,
                         bg=PRIMARY if primary else BUTTON_BG, fg=ON_PRIMARY if primary else BUTTON_TEXT,
                         activebackground=PRIMARY_HOVER if primary else BUTTON_HOVER,
                         activeforeground=ON_PRIMARY if primary else BUTTON_TEXT, disabledforeground=SUBTLE,
                         highlightthickness=0, font=(FONT, size, "bold"), padx=14, pady=7, cursor="hand2")


def _entry(parent, *, secret=False, width=28):
    holder = RoundedFrame(parent, bg=INPUT_BG, radius=10, padx=10, pady=6, highlightthickness=1,
                          highlightbackground=LINE)
    entry = tk.Entry(holder, bg=INPUT_BG, fg=TEXT, insertbackground=TEXT, relief="flat", bd=0,
                     highlightthickness=0, font=(FONT, 10), width=width, show="•" if secret else "")
    entry.pack(fill="x")
    holder.entry = entry
    return holder


class Worker:
    """Runs one blocking call at a time and hands the result back on the Tk thread."""

    def __init__(self, widget):
        self.widget = widget
        self.busy = False
        self._results: queue.Queue = queue.Queue()

    def run(self, call, done) -> bool:
        if self.busy:
            return False
        self.busy = True

        def work():
            try:
                self._results.put((done, call(), None))
            except AccountError as failure:
                self._results.put((done, None, failure))
            except Exception as failure:  # Unexpected: show it instead of hanging the page.
                self._results.put((done, None, AccountError(f"出错了：{failure}")))
        threading.Thread(target=work, name="xingtu-route-plaza", daemon=True).start()
        self.widget.after(POLL_MS, self._poll)
        return True

    def _poll(self):
        try:
            done, result, failure = self._results.get_nowait()
        except queue.Empty:
            if self.widget.winfo_exists():
                self.widget.after(POLL_MS, self._poll)
            return
        self.busy = False
        if self.widget.winfo_exists():
            done(result, failure)


class RoutePlazaPage(tk.Frame):
    def __init__(self, parent, ui):
        super().__init__(parent, bg=BG)
        self.ui = ui
        self.assistant = ui.assistant
        self.routes: list[route_plaza.PlazaRoute] = []
        self.loaded = False
        self._worker = Worker(self)
        self._downloading = None
        self._signature = None
        self._account_dialog = None
        self._build()

    # Data ---------------------------------------------------------------------
    def _account(self):
        return self.assistant.account_service()

    def _library(self):
        return self.assistant.custom_map_library()

    def _galleries(self):
        return self.assistant.custom_galleries()

    # Construction ---------------------------------------------------------------
    def _build(self):
        ui = self.ui
        head = tk.Frame(self, bg=BG)
        head.pack(fill="x", padx=ui._px(24), pady=(ui._px(22), ui._px(12)))
        words = tk.Frame(head, bg=BG)
        words.pack(side="left", fill="x", expand=True)
        ui._label(words, "路线广场", size=22, bold=True, anchor="w").pack(fill="x")
        ui._label(words, "下载其他玩家分享的噩梦路线，下载后在「自定义地图」里选用。", size=9, color=MUTED,
                  anchor="w", justify="left", wraplength=520).pack(fill="x", pady=(2, 0))
        account = tk.Frame(head, bg=BG)
        account.pack(side="right", anchor="n")
        self.account_label = ui._label(account, "", size=9, color=MUTED)
        self.account_label.pack(side="left", padx=(0, 8))
        self.login_button = _button(account, "登录", self._open_account, primary=True, size=9)
        self.logout_button = ui._ghost(account, "退出登录", self._logout, color=MUTED, size=9)

        card = ui._card(self, padx=18, pady=14)
        card.pack(fill="both", expand=True, padx=ui._px(14), pady=(0, ui._px(14)))
        bar = tk.Frame(card, bg=SURFACE)
        bar.pack(fill="x")
        self.search = _entry(bar, width=24)
        self.search.pack(side="left")
        self.search.entry.bind("<Return>", lambda _e: self.reload())
        _button(bar, "搜索", self.reload, size=9).pack(side="left", padx=(8, 0))
        self.refresh_button = ui._ghost(bar, "刷新", self.reload, size=9)
        self.refresh_button.pack(side="right")
        self.status = ui._label(card, "", size=9, color=MUTED, anchor="w", justify="left", wraplength=620)
        self.status.pack(fill="x", pady=(10, 6))

        holder = tk.Frame(card, bg=SURFACE)
        holder.pack(fill="both", expand=True)
        self.canvas = tk.Canvas(holder, bg=SURFACE, highlightthickness=0, bd=0)
        scroll = tk.Scrollbar(holder, orient="vertical", command=self.canvas.yview, width=10, relief="flat", bd=0)
        self.canvas.configure(yscrollcommand=scroll.set)
        scroll.pack(side="right", fill="y")
        self.canvas.pack(side="left", fill="both", expand=True)
        self.canvas._desktop_scroll_owner = self
        self.list_frame = tk.Frame(self.canvas, bg=SURFACE)
        self._list_item = self.canvas.create_window((0, 0), window=self.list_frame, anchor="nw")
        self.list_frame.bind("<Configure>", lambda _e: self.canvas.configure(scrollregion=self.canvas.bbox("all")))
        self.canvas.bind("<Configure>", lambda e: self.canvas.itemconfigure(self._list_item, width=e.width))
        self.canvas.bind("<MouseWheel>", lambda e: self.canvas.yview_scroll(-1 if e.delta > 0 else 1, "units"))

    # Page sync ------------------------------------------------------------------
    def refresh(self):
        """Called while the page is shown; loads the list once and keeps badges current."""
        if not self.loaded and not self._worker.busy:
            self.reload()
        account = self._account()
        store = self._galleries()
        signature = (account.username, account.revision, store.revision, self._downloading)
        if signature == self._signature:
            return
        self._signature = signature
        if account.username:
            self.account_label.configure(text=f"已登录：{account.username}")
            self.login_button.pack_forget()
            self.logout_button.pack(side="left")
        else:
            self.account_label.configure(text="未登录 · 登录后可下载")
            self.logout_button.pack_forget()
            self.login_button.pack(side="left")
        self._render()

    def _note(self, text, *, error=False):
        self.status.configure(text=text, fg=ERROR if error else MUTED)

    def reload(self):
        query = self.search.entry.get()

        def done(routes, failure):
            self.loaded = True
            if failure is not None:
                self._note(f"路线广场加载失败：{failure}", error=True)
                return
            self.routes = routes
            self._note(f"找到 {len(routes)} 套路线。" if routes else
                       ("没有找到匹配的路线。" if query.strip() else "广场里还没有路线。"))
            self._signature = None
            self.refresh()
        if self._worker.run(lambda: route_plaza.list_routes(query), done):
            self._note("正在加载路线广场…")

    def _render(self):
        for child in self.list_frame.winfo_children():
            child.destroy()
        installed = self._galleries().plaza_versions()
        for route in self.routes:
            self._route_card(route, installed.get(route.work_id))

    def _route_card(self, route, installed_version):
        ui = self.ui
        row = RoundedFrame(self.list_frame, bg=INPUT_BG, radius=12, padx=14, pady=10, highlightthickness=0)
        row.pack(fill="x", pady=(0, 8))
        right = tk.Frame(row, bg=INPUT_BG)
        right.pack(side="right", padx=(12, 0))
        left = tk.Frame(row, bg=INPUT_BG)
        left.pack(side="left", fill="x", expand=True)
        ui._label(left, route.title, size=11, bold=True, anchor="w").pack(fill="x")
        meta = " · ".join(part for part in (f"作者 {route.author}", route.map_count and f"{route.map_count} 张图",
                                            f"下载 {route.download_count} 次",
                                            route.updated_date and f"更新于 {route.updated_date}") if part)
        ui._label(left, meta, size=8, color=SUBTLE, anchor="w").pack(fill="x", pady=(2, 0))
        if route.description:
            ui._label(left, route.description, size=9, color=MUTED, anchor="w", justify="left",
                      wraplength=480).pack(fill="x", pady=(4, 0))
        if self._downloading == route.version_id:
            _button(right, "下载中…", lambda: None, size=9).pack()
        elif installed_version == route.version_id:
            ui._label(right, "已下载", size=9, bold=True, color=GREEN).pack()
            ui._ghost(right, "去使用  ›", lambda: self.ui.show_page("custom"), size=9).pack(pady=(2, 0))
        else:
            _button(right, "更新路线" if installed_version else "下载路线",
                    lambda value=route: self._download(value), primary=True, size=9).pack()

    # Actions --------------------------------------------------------------------
    def _download(self, route):
        if self._worker.busy:
            return
        if not self._account().username:
            self._open_account(after=lambda: self._download(route))
            return

        def done(result, failure):
            self._downloading = None
            if failure is not None:
                self._note(f"「{route.title}」下载失败：{failure}", error=True)
                if not self._account().username:
                    self._note("登录已失效，请重新登录后再下载。", error=True)
            else:
                _gallery, report = result
                lost = []
                if report["missingMaps"]:
                    lost.append(f"{report['missingMaps']} 张图本软件没有")
                if report["droppedPoints"]:
                    lost.append(f"{report['droppedPoints']} 个点不在道路上")
                self._note(f"已下载「{route.title}」：{report['maps']} 张图的路线" +
                           (f"（{'，'.join(lost)}，已跳过）" if lost else "") + "。到「自定义地图」里选用。")
                self.ui._refresh_route_choices()
            self._signature = None
            self.refresh()
        if self._worker.run(lambda: route_plaza.install(self._account(), self._library(), self._galleries(), route), done):
            self._downloading = route.version_id
            self._note(f"正在下载「{route.title}」…")
            self.refresh()

    def _logout(self):
        def done(_result, _failure):
            self._note("已退出登录。")
            self._signature = None
            self.refresh()
        self._worker.run(self._account().logout, done)

    def _open_account(self, after=None):
        dialog = self._account_dialog
        if dialog is not None and dialog.winfo_exists():
            dialog.lift()
            return
        self._account_dialog = AccountDialog(self.ui, self._account(), on_signed_in=lambda: self._signed_in(after))

    def _signed_in(self, after):
        self._signature = None
        self.refresh()
        if after is not None:
            after()


class AccountDialog(tk.Toplevel):
    """Sign in, create an account or reset the password with the recovery code."""

    MODES = ("登录", "注册", "找回密码")

    def __init__(self, ui, account, *, on_signed_in):
        super().__init__(ui.window)
        self.ui = ui
        self.account = account
        self.on_signed_in = on_signed_in
        self._worker = Worker(self)
        self.mode = tk.StringVar(self, "登录")
        self.title("星图账号")
        self.transient(ui.window)
        self.configure(bg=SURFACE)
        body = tk.Frame(self, bg=SURFACE)
        body.pack(fill="both", expand=True, padx=24, pady=20)
        ui._label(body, "星图账号", size=16, bold=True, anchor="w").pack(fill="x")
        ui._label(body, "和安卓端是同一个账号，可以直接用已有账号登录。", size=9, color=MUTED,
                  anchor="w").pack(fill="x", pady=(2, 12))
        ui._segmented(body, self.mode, self.MODES, self._layout).pack(anchor="w")
        self.form = tk.Frame(body, bg=SURFACE)
        self.form.pack(fill="x", pady=(14, 0))
        self.fields = {}
        for key, label, secret in (("identifier", "用户名或邮箱", False), ("username", "用户名", False),
                                   ("email", "邮箱", False), ("code", "恢复码", False),
                                   ("password", "密码", True), ("confirm", "确认密码", True)):
            row = tk.Frame(self.form, bg=SURFACE)
            ui._label(row, label, size=9, bold=True, anchor="w").pack(fill="x")
            entry = _entry(row, secret=secret, width=32)
            entry.pack(fill="x", pady=(3, 8))
            entry.entry.bind("<Return>", lambda _e: self._submit())
            row.label = row.winfo_children()[0]
            self.fields[key] = row
        self.hint = ui._label(body, "", size=8, color=SUBTLE, anchor="w", justify="left", wraplength=360)
        self.hint.pack(fill="x")
        self.message = ui._label(body, "", size=9, color=ERROR, anchor="w", justify="left", wraplength=360)
        self.message.pack(fill="x", pady=(4, 0))
        actions = tk.Frame(body, bg=SURFACE)
        actions.pack(fill="x", side="bottom", pady=(12, 0))
        self.submit = _button(actions, "登录", self._submit, primary=True)
        self.submit.pack(side="left")
        ui._ghost(actions, "取消", self.destroy, color=MUTED).pack(side="right")
        self.protocol("WM_DELETE_WINDOW", self.destroy)
        self.bind("<Escape>", lambda _e: self.destroy())
        self.bind("<Destroy>", self._forget_traces, add="+")
        self._layout()
        WindowDisplay(self, ui.assistant, preferred=(440, 520), minimum=(400, 440)).start(ui.window)

    def _forget_traces(self, event):
        if event.widget is not self:
            return
        traces = getattr(self.ui, "_preference_traces", [])
        for variable, token in [item for item in traces if item[0] is self.mode]:
            variable.trace_remove("write", token)
            traces.remove((variable, token))

    def _value(self, key):
        return self.fields[key].winfo_children()[1].entry.get()

    def _layout(self):
        mode = self.mode.get()
        keys = {"登录": ("identifier", "password"), "注册": ("username", "email", "password", "confirm"),
                "找回密码": ("identifier", "code", "password", "confirm")}[mode]
        for key, row in self.fields.items():
            row.pack_forget()
        for key in keys:
            self.fields[key].pack(fill="x")
        self.fields["password"].label.configure(text="新密码" if mode == "找回密码" else "密码")
        self.hint.configure(text={
            "登录": "" if self.account.persistent else "本机不保存登录状态，关闭软件后需要重新登录。",
            "注册": "用户名 3–32 位英文小写、数字或下划线；密码 6–128 位。注册后会显示一次恢复码，用来找回密码。",
            "找回密码": "用注册时拿到的恢复码重设密码，所有设备都会退出登录。"}[mode])
        self.submit.configure(text={"登录": "登录", "注册": "注册并登录", "找回密码": "重设密码"}[mode])
        self.message.configure(text="")
        self.fields[keys[0]].winfo_children()[1].entry.focus_set()

    def _submit(self):
        mode = self.mode.get()
        values = {key: self._value(key) for key in self.fields}  # The worker thread must not touch Tk.
        if mode != "登录" and values["password"] != values["confirm"]:
            self.message.configure(text="两次输入的密码不一致", fg=ERROR)
            return
        if mode == "登录":
            call = lambda: self.account.login(values["identifier"], values["password"])
        elif mode == "注册":
            call = lambda: self.account.register(values["username"], values["password"], values["email"])
        else:
            call = lambda: self.account.recover(values["identifier"], values["code"], values["password"])

        def done(result, failure):
            self.submit.configure(state="normal")
            if failure is not None:
                self.message.configure(text=str(failure), fg=ERROR)
                return
            if mode == "找回密码":
                self.mode.set("登录")
                self._layout()
                self.message.configure(text=result, fg=GREEN)
                return
            if mode == "注册" and result:
                self._show_recovery_code(result)
                return
            self.destroy()
            self.on_signed_in()
        if self._worker.run(call, done):
            self.submit.configure(state="disabled")
            self.message.configure(text="请稍候…", fg=MUTED)

    def _show_recovery_code(self, code):
        for child in self.winfo_children():
            child.destroy()
        body = tk.Frame(self, bg=SURFACE)
        body.pack(fill="both", expand=True, padx=24, pady=20)
        self.ui._label(body, "注册成功", size=16, bold=True, anchor="w").pack(fill="x")
        self.ui._label(body, "请抄下恢复码并妥善保存。它只显示这一次，忘记密码时要用它重设。", size=9, color=MUTED,
                       anchor="w", justify="left", wraplength=360).pack(fill="x", pady=(4, 12))
        box = RoundedFrame(body, bg=INPUT_BG, radius=12, padx=14, pady=12, highlightthickness=0)
        box.pack(fill="x")
        tk.Label(box, text=code, bg=INPUT_BG, fg=TEXT, font=("Consolas", 16, "bold")).pack()
        actions = tk.Frame(body, bg=SURFACE)
        actions.pack(fill="x", pady=(16, 0))

        def copy():
            self.clipboard_clear()
            self.clipboard_append(code)
            copied.configure(text="已复制")
        _button(actions, "复制恢复码", copy).pack(side="left")
        copied = self.ui._label(actions, "", size=9, color=GREEN)
        copied.pack(side="left", padx=(8, 0))

        def finish():
            self.destroy()
            self.on_signed_in()
        _button(actions, "我已保存", finish, primary=True).pack(side="right")
        self.protocol("WM_DELETE_WINDOW", finish)
