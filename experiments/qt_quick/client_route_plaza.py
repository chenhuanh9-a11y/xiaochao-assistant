"""Qt presentation for PR #23's account/plaza services; no Tk or game owner."""
from __future__ import annotations

import threading
import logging
from PySide6.QtCore import QObject, Property, QRunnable, QThreadPool, Qt, Signal, Slot
from PySide6.QtGui import QGuiApplication

from backend import ResourceRows
from client_paths import runtime_paths
from account_service import AccountError, AccountService, BLOCKED_CODES
import route_plaza


class PlazaRows(ResourceRows):
    fields = ("key", "title", "description", "author", "mapCount", "downloadCount", "updatedDate", "installed", "updateAvailable")


class _Signals(QObject):
    done = Signal(object, object)


class _Work(QRunnable):
    def __init__(self, function):
        super().__init__()
        self.function = function
        self.signals = _Signals()

    def run(self):
        try:
            result, error = self.function(), None
        except AccountError as failure:
            # Preserve status/code without carrying credential-bearing frames
            # or transport context across the queued signal.
            result, error = None, AccountError(str(failure), failure.status, failure.code)
        except Exception as failure:
            # Exception reprs may contain request bodies/URLs. Never log or expose them.
            logging.getLogger(__name__).warning("Plaza worker failed (%s)", type(failure).__name__)
            result, error = None, "操作失败，请稍后重试"
        finally:
            self.function = None  # Release submitted credentials promptly.
        self.signals.done.emit(result, error)


class RoutePlazaService(QObject):
    changed = Signal()

    def __init__(self, sandbox, galleries, *, account=None, listing=route_plaza.list_routes):
        super().__init__()
        self.account = account or AccountService(runtime_paths(sandbox).settings_path.parent)
        self.galleries = galleries
        self.listing = listing
        self.model = PlazaRows()
        self.pool = QThreadPool(self)
        self.pool.setMaxThreadCount(1)
        self._routes = {}
        self._query = ""
        self._loaded = self._active = self._busy = self._closed = self._closing = False
        self._generation = self._dialog_generation = 0
        self._job = None
        self._operation = self._download = ""
        self._message = "打开路线广场后读取公开路线"
        self._error = False
        self._account_open = False
        self._account_mode = "登录"
        self._account_message = self._recovery_code = ""
        self._pending_download = None
        self._cancel = threading.Event()
        galleries.galleriesChanged.connect(self._refresh_rows)

    def _snapshot(self):
        return dict(busy=self._busy, operation=self._operation, message=self._message, error=self._error,
                    username=self.account.username or "", persistent=self.account.persistent,
                    loaded=self._loaded, count=len(self._routes), downloading=self._download,
                    query=self._query, accountOpen=self._account_open, accountMode=self._account_mode,
                    accountMessage=self._account_message, recoveryPending=bool(self.account.pending_recovery_code),
                    hasRecoveryCode=bool(self._visible_recovery_code()))

    snapshot = Property("QVariantMap", _snapshot, notify=changed)
    routeModel = Property(QObject, lambda s: s.model, constant=True)
    def _visible_recovery_code(self):
        # Only expose an unacknowledged code belonging to the current account.
        code = self.account.pending_recovery_code
        return code if self._account_open and code == self._recovery_code else ""

    recoveryCode = Property(str, _visible_recovery_code, notify=changed)

    def _note(self, message, error=False):
        self._message, self._error = message, error
        self.changed.emit()

    @Slot(bool)
    def setActive(self, active):
        if self._closed or self._closing or self._active == active:
            return
        self._active = active
        if active:
            self._refresh_rows()
            if not self._loaded and not self._busy:
                self.search(self._query)
        else:
            self.closeAccount()

    def _refresh_rows(self):
        if not self._active or self._closed or self._closing:
            return
        installed = self.galleries.plazaSources()
        self.model.reconcile([dict(key=r.version_id, title=r.title, description=r.description, author=r.author,
            mapCount=r.map_count, downloadCount=r.download_count, updatedDate=r.updated_date,
            installed=installed.get(r.work_id, {}).get("versionId") == r.version_id,
            updateAvailable=r.work_id in installed and installed[r.work_id]["versionId"] != r.version_id
                and r.revision > installed[r.work_id].get("revision", 0))
            for r in self._routes.values()])

    def _run(self, operation, function):
        if self._closed or self._closing or self._busy:
            return False
        self._busy, self._operation = True, operation
        self._generation += 1
        self._request = (self._generation, self._dialog_generation)
        job = self._job = _Work(function)
        job.signals.done.connect(self._complete, Qt.QueuedConnection)
        self.changed.emit()
        self.pool.start(job)
        return True

    @Slot(str)
    def search(self, query):
        if self._busy or self._closing or self._closed:
            return
        self._query = str(query).strip()[:80]
        value = self._query
        if self._run("list", lambda: self.listing(value)):
            self._note("正在加载路线广场…")

    @Slot(object, object)
    def _complete(self, result, error):
        generation, dialog = self._request
        self._job = None
        if self._operation == "download":
            if self._closed or self._closing or generation != self._generation:
                self._busy, self._operation, self._download = False, "", ""
                return
            route, context, cancel = self._download_request
            if cancel.is_set() or not self.galleries.plazaContextCurrent(context):
                error = AccountError("已取消下载，未导入路线")
            if error:
                self._download_complete(route, generation, None, error)
            elif not self.galleries.installPlaza(result, context, cancel.is_set,
                    lambda result, error: self._download_complete(route, generation, result, error)):
                self._download_complete(route, generation, None, "自定义图库正忙，请完成当前操作后重试。")
            return
        self._busy = False
        operation, self._operation = self._operation, ""
        if self._closed or self._closing or generation != self._generation:
            return
        if operation == "list":
            self._loaded = True
            if error:
                self._note("路线广场加载失败：" + str(error), True)
            else:
                self._routes = {r.version_id: r for r in result}
                self._refresh_rows()
                self._note(f"找到 {len(self._routes)} 套路线。" if result else
                           "没有找到匹配的路线。" if self._query else "广场里还没有路线。")
        elif operation == "logout":
            self._note("已退出登录。")
        elif self._account_open and dialog == self._dialog_generation:
            if error:
                self._account_message = str(error)
                self.changed.emit()
            elif operation == "找回密码":
                self._account_mode, self._account_message = "登录", result
                self._pending_download = None
                self.changed.emit()
            elif operation == "注册" and self.account.pending_recovery_code:
                self._recovery_code = self.account.pending_recovery_code
                self._account_message = "注册成功。请保存恢复码；确认保存后，本机不再显示。"
                self.changed.emit()
            else:
                self.finishSignIn()
        else:
            # An accepted call can finish after dismissal; publish session state,
            # but never restore the dialog or resume its old download intention.
            self.changed.emit()

    @Slot()
    def openAccount(self):
        if self._closed or self._closing or self._account_open or self._busy:
            return
        self._dialog_generation += 1
        self._account_open = True
        self._account_mode = "登录"
        self._recovery_code = self.account.pending_recovery_code
        self._account_message = ("请保存账号恢复码；确认保存后，本机不再显示。退出软件后，本机不再保留。"
                                 if self._recovery_code else "")
        self.changed.emit()

    @Slot()
    def closeAccount(self):
        self._account_open = False
        self._dialog_generation += 1
        self._pending_download = None
        self._recovery_code = self._account_message = ""
        self.changed.emit()

    @Slot(str)
    def setAccountMode(self, mode):
        if self._busy or self._visible_recovery_code() or mode not in ("登录", "注册", "找回密码"):
            return
        self._account_mode, self._account_message = mode, ""
        self.changed.emit()

    @Slot(str, str, str, str, str, str)
    def submitAccount(self, identifier, username, email, code, password, confirmation):
        if not self._account_open or self._visible_recovery_code() or self._busy:
            return
        mode = self._account_mode
        if mode != "登录" and password != confirmation:
            self._account_message = "两次输入的密码不一致"
            self.changed.emit()
            return
        if mode == "登录":
            function = lambda: self.account.login(identifier, password)
        elif mode == "注册":
            function = lambda: self.account.register(username, password, email)
        else:
            function = lambda: self.account.recover(identifier, code, password)
        if self._run(mode, function):
            self._account_message = "请稍候…"
            self.changed.emit()

    @Slot()
    def finishSignIn(self):
        if self._busy or not self._account_open or not self.account.username:
            return
        pending = self._pending_download
        self.account.acknowledge_recovery_code(self._visible_recovery_code())
        self.closeAccount()
        self._note("已登录，可以下载路线。")
        if pending is not None and self._active and self._routes.get(pending.version_id) is pending:
            self.downloadRoute(pending.version_id)

    @Slot()
    def copyRecoveryCode(self):
        code = self._visible_recovery_code()
        if code:
            QGuiApplication.clipboard().setText(code)
            self._account_message = "已复制恢复码，请妥善保存，保存后请清空剪贴板。"
            self.changed.emit()

    @Slot()
    def logout(self):
        if self._busy:
            return
        self.closeAccount()
        self._run("logout", self.account.logout)

    @Slot(str)
    def downloadRoute(self, version_id):
        if self._closed or self._closing or self._busy:
            return
        route = self._routes.get(version_id)
        if route is None:
            return
        if not self.account.username:
            self.openAccount()
            self._pending_download = route
            return
        context = self.galleries.plazaInstallContext()
        if context is None:
            self._note("自定义图库正忙，请完成当前操作后重试。", True)
            return
        self._cancel = cancel = threading.Event()
        self._download_request = (route, context, cancel)
        self._download = version_id
        library, _generation = context
        self._run("download", lambda: route_plaza.prepare_install(self.account, library, route,
                                                                 cancelled=cancel.is_set))
        self._note(f"正在下载并校验「{route.title}」…")

    def _download_complete(self, route, generation, result, error):
        self._busy, self._operation, self._download = False, "", ""
        if self._closed or self._closing or generation != self._generation:
            return
        self._refresh_rows()
        if error:
            self._note(f"「{route.title}」下载失败：{error}", True)
            if (isinstance(error, AccountError) and error.status in (400, 401, 403, 422)
                    and error.code not in BLOCKED_CODES and not self.account.username and self._active):
                self.openAccount()
                self._account_message = "登录已失效，请重新登录后继续下载。"
                self._pending_download = route
                self.changed.emit()
        else:
            report = result["report"]
            lost = []
            if report["missingMaps"]:
                lost.append(f"{report['missingMaps']} 张图本软件没有")
            if report["droppedPoints"]:
                lost.append(f"{report['droppedPoints']} 个点不在道路上")
            self._note(f"已下载「{route.title}」：{report['maps']} 张图的路线" +
                       (f"（{'，'.join(lost)}，已跳过）" if lost else "") + "。到「自定义地图」里选用。")

    @Slot()
    def cancelDownload(self):
        if self._operation == "download":
            self._cancel.set()
            self._note("正在取消，网络请求结束后停止导入…")

    @Slot()
    def cancel(self):
        """Application close: drain accepted work without allowing another request."""
        self._closing = True
        self.account.shutdown()
        self._cancel.set()
        self._generation += 1
        self.closeAccount()

    def shutdown(self):
        self.cancel()
        self._closed = True
        self.pool.waitForDone()
