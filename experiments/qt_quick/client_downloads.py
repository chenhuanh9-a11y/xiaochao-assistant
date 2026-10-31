"""Qt ingress for the existing bounded map transport; no startup requests."""
from copy import deepcopy
from pathlib import Path
import sys
import threading

from PySide6.QtCore import QObject, Property, QRunnable, QThreadPool, QTimer, QUrl, Qt, Signal, Slot
from client_paths import APP_ROOT, owned_sandbox
if str(APP_ROOT) not in sys.path:
    sys.path.insert(0, str(APP_ROOT))
from star_map_download import MapPackageDownloadService
from star_map_import import verified_installed_package
from client_resources import package_problem, package_cancelled, _error_text


class _Signals(QObject):
    done = Signal(int, object, object)


class _Work(QRunnable):
    def __init__(self, generation, function):
        super().__init__()
        self.generation, self.function = generation, function
        self.signals = _Signals()

    def run(self):
        try:
            self.signals.done.emit(self.generation, self.function(), None)
        except Exception as error:
            self.signals.done.emit(self.generation, None, dict(reason=_error_text(error),
                code=_error_text(getattr(error, "reason", "")), errorType=type(error).__name__))


class OnlinePackagesService(QObject):
    changed = Signal()
    errorReported = Signal(object)

    def __init__(self, sandbox, resources, *, index_url=None, transport=None):
        super().__init__()
        self.sandbox = owned_sandbox(sandbox)
        self.resources = resources
        self.download_root = self.sandbox / "work/map-packages/downloads"
        self._index_url = index_url.strip() if isinstance(index_url, str) else ""
        self._transport = transport
        self._configured = bool(transport.configured) if transport is not None else bool(self._index_url)
        self._lock = threading.Lock()
        self._progress_cell = None
        self._progress = dict(receivedBytes=0, totalBytes=None)
        self._generation, self._job_generation = 0, None
        self._closed, self._busy = False, False
        self._job = None
        self._cancel = threading.Event()
        self._rows, self._entries = [], {}
        self._selected, self._active = "", ""
        self._selection_generation = self._request_selection_generation = 0
        self._operation, self._stage = "", "idle"
        self._resource_generation = 0
        self._message = "点击检查在线列表" if self._configured else "尚未配置地图下载地址，可继续使用本地图包导入。"
        self.pool = QThreadPool(self)
        self.pool.setMaxThreadCount(1)
        self._timer = QTimer(self)
        self._timer.setInterval(100)
        self._timer.timeout.connect(self._poll_progress)
        resources.changed.connect(self.changed)

    def _local_idle(self):
        return not self.resources.busy and not self.resources.pendingKind and not self.resources.recoveryRequired

    def _snapshot(self):
        ready = self._configured and not self._closed and not self._busy and self._local_idle()
        return dict(configured=self._configured, busy=self._busy, stage=self._stage,
            message=self._message, packages=deepcopy(self._rows), selectedPackageId=self._selected,
            activePackageId=self._active, progress=dict(self._progress),
            canCheck=ready, canDownload=ready and bool(self._entries),
            canCancel=self._busy and not self._cancel.is_set(),
            localMessage=self.resources.message, localBusy=self.resources.busy,
            localPendingKind=self.resources.pendingKind)

    snapshot = Property("QVariantMap", _snapshot, notify=changed)
    busy = Property(bool, lambda s: s._busy, notify=changed)

    def _get_transport(self):
        # TLS/context construction belongs to the worker as well as HTTP work.
        if self._transport is None:
            service = MapPackageDownloadService(self._index_url, self.download_root)
            with self._lock:
                self._transport = service
        return self._transport

    @Slot(str)
    def selectPackage(self, package_id):
        if package_id in self._entries and package_id != self._selected:
            self._selected = package_id
            self._selection_generation += 1
            self.changed.emit()

    @Slot()
    def checkPackages(self):
        self._begin("index", "")

    @Slot(str)
    def downloadPackage(self, package_id):
        if package_id not in self._entries:
            return
        service = self._transport
        if service is not None and service.attempts_remaining(package_id) <= 0:
            self._message = "已达手动重试上限，请重新检查在线列表。"
            self.changed.emit()
            return
        self.selectPackage(package_id)
        self._begin("download", package_id)

    def _begin(self, operation, package_id):
        if not self._snapshot()["canCheck"]:
            return
        self._generation += 1
        generation = self._generation
        self._job_generation = generation
        self._resource_generation = self.resources.operationGeneration
        self._request_selection_generation = self._selection_generation
        self._operation, self._active = operation, package_id
        self._busy = True
        self._stage = "checking" if operation == "index" else "downloading"
        self._message = "正在读取在线图包列表。" if operation == "index" else "正在下载并校验图包，尚未导入。"
        self._cancel = cancel = threading.Event()
        self._progress = dict(receivedBytes=0, totalBytes=None)
        expected = deepcopy(self._entries.get(package_id))
        with self._lock:
            self._progress_cell = None
        def progress(received, total):
            if not cancel.is_set():
                with self._lock:
                    self._progress_cell = (generation, received, total)
        def work():
            service = self._get_transport()
            if operation == "index":
                rows = service.read_index(cancel_event=cancel)
                return [dict(row, installation=verified_installed_package(self.sandbox,
                    row["packageId"], row.get("sha256"), cancelled=cancel.is_set)) for row in rows]
            return dict(path=str(service.download_package(package_id, cancel_event=cancel, progress=progress)),
                        expected=expected)
        self._job = _Work(generation, work)
        self._job.signals.done.connect(self._complete, Qt.QueuedConnection)
        self._timer.start()
        self.changed.emit()
        self.pool.start(self._job)

    def _poll_progress(self):
        with self._lock:
            latest, self._progress_cell = self._progress_cell, None
        if latest is None or latest[0] != self._generation or self._closed:
            return
        _, received, total = latest
        update = dict(receivedBytes=received if type(received) is int and received >= 0 else 0,
                      totalBytes=total if type(total) is int and total > 0 else None)
        if update != self._progress:
            self._progress = update
            self.changed.emit()

    @Slot(int, object, object)
    def _complete(self, generation, result, error):
        if generation != self._job_generation:
            return
        self._job_generation, self._job = None, None
        self._busy = False
        self._poll_progress()
        self._timer.stop()
        if self._closed:
            return
        if generation != self._generation or self._cancel.is_set() or (error and package_cancelled(error)):
            self._stage = "ready" if self._entries else "idle"
            self._message = "已取消；迟到的图包不会自动导入。"
        elif error:
            self._stage = "error"
            self._report_problem(error)
        elif self._operation == "index":
            try:
                entries = {row["packageId"]: dict(row) for row in result}
                if len(entries) != len(result):
                    raise ValueError("图包列表含重复标识")
                if self.resources.operationGeneration != self._resource_generation:
                    for row in entries.values():
                        row["installation"] = dict(status="unverified")
                self._entries, self._rows = entries, deepcopy(list(entries.values()))
                if self._selected not in entries:
                    self._selected = next(iter(entries), "")
                    self._selection_generation += 1
                self._stage, self._message = "ready", "在线列表已更新。"
            except (KeyError, TypeError, ValueError) as problem:
                self._stage = "error"
                self._report_problem(problem)
        else:
            self._stage = "cached"
            if (self._selected != self._active
                    or self._selection_generation != self._request_selection_generation
                    or self.resources.operationGeneration != self._resource_generation):
                self._message = "图包已校验并缓存；选择或本地操作已变化，未提交导入。"
            else:
                expected = {key: result["expected"][key] for key in ("packageId", "bytes", "sha256")}
                accepted = self.resources.importVerifiedArchive(QUrl.fromLocalFile(result["path"]).toString(),
                    expected, self._resource_generation)
                self._stage = "handoff" if accepted else "cached"
                self._message = ("已交给本地图包检查；覆盖确认和安装结果见本地管理。" if accepted
                                 else "图包已缓存；本地操作或会话忙，未提交导入，可稍后重试。")
        self.changed.emit()

    def _report_problem(self, error):
        problem = package_problem(error, self._operation, online=True)
        self._message = problem["summary"]
        self.errorReported.emit(problem)

    @Slot()
    def cancel(self):
        if not self._busy or self._cancel.is_set():
            return
        self._generation += 1
        self._cancel.set()
        with self._lock:
            service = self._transport
            self._progress_cell = None
        if service is not None:
            service.cancel_active()
        self._message = "正在取消下载，请等待当前请求结束。"
        self.changed.emit()

    @Slot()
    def shutdown(self):
        self.cancel()
        self._closed = True
        self._timer.stop()
        self.pool.waitForDone()
