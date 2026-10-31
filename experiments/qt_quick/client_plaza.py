"""Qt route plaza worker. Downloads emit data; the gallery remains sole writer."""
from __future__ import annotations

from copy import deepcopy
import sys
import threading

from PySide6.QtCore import QObject, Property, QRunnable, QThreadPool, Qt, Signal, Slot
from client_paths import APP_ROOT
if str(APP_ROOT) not in sys.path:
    sys.path.insert(0, str(APP_ROOT))
from route_plaza import BATCH_SCHEMA, list_routes, prepare_download


class _Signals(QObject):
    done = Signal(int, object, object)


class _Job(QRunnable):
    def __init__(self, generation, function):
        super().__init__()
        self.generation, self.function = generation, function
        self.signals = _Signals()

    def run(self):
        try:
            self.signals.done.emit(self.generation, self.function(), None)
        except Exception as error:
            self.signals.done.emit(self.generation, None, str(error))


class PlazaService(QObject):
    changed = Signal()
    downloadReady = Signal(object)
    loginRequested = Signal()
    openGalleryRequested = Signal()

    def __init__(self, account_adapter, *, list_function=None, download_function=None):
        super().__init__()
        self.account = account_adapter
        self._list = list_function or list_routes
        self._download = download_function or prepare_download
        self._lock = threading.RLock()
        self._generation, self._job_generation = 0, None
        self._account_revision = None
        self._closed, self._busy, self._cancelled = False, False, False
        self._stage, self._operation = "idle", ""
        self._entries, self._rows = {}, []
        self._query, self._selected, self._active = "", "", ""
        self._message = "浏览玩家分享的噩梦路线；下载后可在自定义图库中使用。"
        self._loaded = False
        self._job = None
        self._pending_request = None
        self._committed_request = None
        self.pool = QThreadPool(self)
        self.pool.setMaxThreadCount(1)
        account_adapter.changed.connect(self._account_changed)

    def _snapshot(self):
        logged_in = bool(self.account.loggedIn)
        idle = not self._closed and not self._busy
        return dict(rows=deepcopy(self._rows), query=self._query, selectedRouteId=self._selected,
            activeRouteId=self._active, busy=self._busy, stage=self._stage, message=self._message,
            loaded=self._loaded, loggedIn=logged_in, canSearch=idle,
            canDownload=idle and logged_in, canCancel=self._busy and self._job is not None and not self._cancelled)

    snapshot = Property("QVariantMap", _snapshot, notify=changed)
    busy = Property(bool, lambda s: s._busy, notify=changed)

    @Slot(str)
    def search(self, query):
        if self._closed or self._busy:
            return
        self._query = str(query or "").strip()[:80]
        query = self._query
        self._begin("list", lambda: self._list(query))

    @Slot()
    def refresh(self):
        self.search(self._query)

    @Slot(str)
    def selectRoute(self, version_id):
        if self._closed or version_id not in self._entries or version_id == self._selected:
            return
        self._selected = version_id
        if self._busy and self._operation == "download" and self._job is not None:
            self.cancel()
        self.changed.emit()

    @Slot(str)
    def downloadRoute(self, version_id):
        if self._closed or self._busy or version_id not in self._entries:
            return
        if not self.account.loggedIn:
            self._message = "请先登录账号，再下载路线。"
            self.changed.emit()
            self.loginRequested.emit()
            return
        self._selected = self._active = version_id
        route = self._entries[version_id]
        self._account_revision = self.account.service.revision
        self._begin("download", lambda: self._download(self.account.service, route))

    def _begin(self, operation, function):
        with self._lock:
            self._generation += 1
            generation = self._generation
            self._job_generation = generation
            self._busy, self._cancelled = True, False
            self._operation = operation
            self._pending_request = None
            self._committed_request = None
        self._stage = "loading" if operation == "list" else "downloading"
        self._message = "正在读取路线广场…" if operation == "list" else "正在下载并校验路线包…"
        self._job = _Job(generation, function)
        self._job.signals.done.connect(self._complete, Qt.QueuedConnection)
        self.changed.emit()
        self.pool.start(self._job)

    @Slot(int, object, object)
    def _complete(self, generation, result, error):
        if generation != self._job_generation:
            return
        self._job_generation, self._job = None, None
        self._busy = False
        if self._closed:
            return
        if generation != self._generation or self._cancelled:
            self._stage = "ready" if self._loaded else "idle"
            self.changed.emit()
            return
        if self._operation == "download" and self.account.service.revision != self._account_revision:
            self._stage, self._message = "ready", "账号已变化，下载结果未导入。"
        elif error:
            self._stage, self._message = "error", error
        elif self._operation == "list":
            self._entries = {route.version_id: route for route in result}
            self._rows = [dict(versionId=r.version_id, workId=r.work_id, title=r.title,
                description=r.description, author=r.author, revision=r.revision, mapCount=r.map_count,
                downloadCount=r.download_count, updatedDate=r.updated_date) for r in result]
            if self._selected not in self._entries:
                self._selected = next(iter(self._entries), "")
            self._loaded = True
            self._stage = "ready"
            self._message = f"找到 {len(self._rows)} 套路线。" if self._rows else "没有找到路线，请换个关键词。"
        else:
            if not isinstance(result, dict) or result.get("schema") != BATCH_SCHEMA:
                self._stage, self._message = "error", "下载结果格式无法识别。"
                self.changed.emit()
                return
            with self._lock:
                self._pending_request = generation
                self._busy = True
            self._stage, self._message = "importing", "路线已校验，正在交给自定义图库保存…"
            batch = dict(deepcopy(result), requestId=generation, accountRevision=self._account_revision)
            self.changed.emit()
            self.downloadReady.emit(batch)
            return
        self.changed.emit()

    def isCurrentDownload(self, request_id, account_revision):
        """Worker-safe guard while the gallery prepares a batch for saving."""
        with self._lock:
            return (not self._closed and self._pending_request == request_id == self._generation
                and not self._cancelled and self.account.service.revision == account_revision == self._account_revision)

    def beginImport(self, request_id, account_revision):
        """Accept one local write, atomically with cancellation/account handling.

        The sole gallery worker calls this immediately before its write. Once
        accepted, that writer owns completion even if the account changes. The
        lock is released here; it is never held across conversion or disk IO.
        """
        with self._lock:
            if self._committed_request is not None or not self.isCurrentDownload(request_id, account_revision):
                return False
            self._committed_request = request_id
            return True

    @Slot(int, bool, str)
    def finishImport(self, request_id, success, message):
        with self._lock:
            if self._closed or self._pending_request != request_id:
                return
            current = (self._committed_request == request_id
                or self.isCurrentDownload(request_id, self._account_revision))
            self._pending_request = None
            self._committed_request = None
            self._busy = False
        self._stage = "installed" if success and current else "error"
        self._message = ((str(message) or ("路线已保存到自定义图库。" if success else "路线未保存，请重试。"))
            if current else "账号或下载请求已变化，旧导入回执已忽略。")
        self.changed.emit()

    @Slot()
    def cancel(self):
        # Once handed to the gallery writer, cancellation belongs to that owner.
        if not self._busy or self._job is None or self._cancelled:
            return
        with self._lock:
            self._generation += 1
            self._cancelled = True
        self._message = "已取消；等待当前请求结束，迟到结果不会导入。"
        self.changed.emit()

    @Slot()
    def _account_changed(self):
        with self._lock:
            if self._closed:
                return
            if self._busy and self._operation == "download" and self.account.service.revision != self._account_revision:
                if self._committed_request is not None:
                    self._message = "账号已变化，正在完成已开始的图库保存。"
                else:
                    self._generation += 1
                    self._cancelled = True
                    self._pending_request = None
                    if self._job is None:
                        self._busy = False
                    self._stage, self._message = "ready", "账号已变化，下载结果不会导入。"
        self.changed.emit()

    @Slot()
    def requestLogin(self):
        if not self._closed:
            self.loginRequested.emit()

    @Slot()
    def openGallery(self):
        if not self._closed:
            self.openGalleryRequested.emit()

    @Slot()
    def shutdown(self):
        with self._lock:
            self._closed = True
            self._generation += 1
            self._pending_request = None
        self.pool.waitForDone()


ClientPlazaService = PlazaService
