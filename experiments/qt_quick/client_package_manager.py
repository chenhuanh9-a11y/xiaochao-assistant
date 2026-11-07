"""Qt deletion adapter over the receipt-owned production removal transaction."""
from __future__ import annotations

import os
from pathlib import Path
import threading

from PySide6.QtCore import QObject, Property, QThreadPool, Qt, Signal, Slot
from client_paths import validate_installation_root
from client_resources import _Work, package_problem, package_cancelled, package_title
from client_resource_errors import error_fields
import map_package_removal as removal
from map_package_summary import declared_packages


class PackageManagerService(QObject):
    changed = Signal()
    errorReported = Signal(object)
    confirmationRequested = Signal(str, str)
    resourcesChanged = Signal()
    removalCompleted = Signal(str, str)

    def __init__(self, sandbox, resources, parent=None):
        super().__init__(parent)
        self.sandbox = Path(os.path.abspath(sandbox))
        validate_installation_root(self.sandbox)
        self.resources = resources
        self.pool = QThreadPool(self)
        self.pool.setMaxThreadCount(1)
        self._rows, self._busy, self._pending = [], False, None
        self._message, self._kind = "", ""
        self._token, self._jobs = 0, {}
        self._closed, self._recovery = False, False
        self._lease_provider, self._lease_id = None, None
        self._cancel = threading.Event()
        self._completion = None
        self._refresh_requested = False
        self.resources.changed.connect(self._drain_refresh)

    packageRows = Property("QVariantList", lambda s: s._rows, notify=changed)
    busy = Property(bool, lambda s: s._busy, notify=changed)
    operationStage = Property(str, lambda s: ("cancelling" if s._cancel.is_set() else "manage-" + s._kind) if s._busy else "", notify=changed)
    pendingKind = Property(str, lambda s: "remove" if s._pending is not None else "", notify=changed)
    canCancel = Property(bool, lambda s: s._busy and not s._cancel.is_set(), notify=changed)
    recoveryRequired = Property(bool, lambda s: s._recovery, notify=changed)
    message = Property(str, lambda s: s._message, notify=changed)

    def setLeaseProvider(self, provider):
        if self._busy or self._pending is not None or self._lease_id is not None:
            raise RuntimeError("Cannot replace an active package lease provider")
        self._lease_provider = provider

    def _release(self):
        if self._lease_id is not None:
            lease, self._lease_id = self._lease_id, None
            self._lease_provider.releaseResourceLease(lease)

    def _available(self):
        return (not self._closed and not self._busy and self._pending is None and not self._recovery
                and not self.resources.busy and not self.resources.pendingKind
                and not self.resources.recoveryRequired)

    def _start(self, kind, function, message):
        if self._lease_id is None:
            if self._lease_provider is None:
                self._message = "资源会话尚未准备好，请稍后重试。"
                self.changed.emit()
                return False
            lease = self._lease_provider.acquireResourceLease()
            if not lease:
                self._message = "当前会话或资源操作仍在运行，请停止后再删除图包。"
                self.changed.emit()
                return False
            self._lease_id = lease
        self._token += 1
        self._busy, self._kind, self._message = True, kind, message
        self._cancel = cancel = threading.Event()
        def guarded():
            validate_installation_root(self.sandbox)
            return function(cancel)
        job = _Work(self._token, guarded, lambda: (self.sandbox / removal.PENDING).exists())
        self._jobs[self._token] = job
        job.signals.done.connect(self._completed, Qt.QueuedConnection)
        self.changed.emit()
        self.pool.start(job)
        return True

    @Slot()
    def refresh(self):
        self.requestRefresh()

    @Slot()
    def requestRefresh(self):
        if not self._closed:
            self._refresh_requested = True
            self._drain_refresh()

    @Slot()
    def _drain_refresh(self):
        if self._refresh_requested and self._available():
            if self._start("refresh", lambda cancel: declared_packages(self.sandbox, cancelled=cancel.is_set),
                           "正在查看已导入图包和上次操作记录…"):
                self._refresh_requested = False

    @Slot(str)
    def requestRemove(self, package_id):
        if self._available():
            self._inspect(package_id)

    @Slot()
    def requestClearAll(self):
        if self._available():
            self._inspect(None)

    def _inspect(self, package_id):
        self._start("inspect", lambda cancel: removal.inspect_package_removal(self.sandbox, package_id,
            cancelled=cancel.is_set), "正在核对本次删除范围…")

    @Slot(bool)
    def confirmOperation(self, accepted):
        if self._closed or self._busy or self._pending is None:
            return
        plan, self._pending = self._pending, None
        if not accepted:
            self._message = "已取消删除，图包未改动。"
            self._release()
            self.changed.emit()
            self._drain_refresh()
            return
        self._start("remove", lambda cancel: removal.remove_packages(self.sandbox, plan["packageId"],
            application_idle=True, expected_plan_sha256=plan["planSha256"], cancelled=cancel.is_set),
            "正在删除图包并清理本次临时文件…")

    @Slot()
    def cancel(self):
        if self._pending is not None:
            self.confirmOperation(False)
        elif self._busy:
            self._cancel.set()
            self._message = "正在取消，请等待本次操作确认结果…"
            self.changed.emit()

    @Slot(int, object, object)
    def _completed(self, token, result, error):
        if self._jobs.pop(token, None) is None:
            return
        if self._closed or token != self._token:
            if self._closed:
                self._release()
            return
        kind, self._kind, self._busy = self._kind, "", False
        if error:
            self._completion = None
            self._recovery = bool(error.get("recovery"))
            code = error_fields(error)["code"]
            if package_cancelled(error) and not self._recovery:
                self._message = "删除已取消；已撤回本次文件改动。"
            else:
                problem = package_problem(error, "package-" + kind)
                if code == "removal-committed-cleanup-required":
                    problem["summary"] = "图包已删除，但本次临时文件还没清理完。请正常退出并重启，按提示继续清理；清理结束后才会提示完成。"
                elif self._recovery:
                    problem["summary"] = "图包删除还没完成。请正常退出并重启，按提示恢复图包；无法确认来源的文件会保留。"
                elif kind == "remove":
                    problem["summary"] = "图包删除未完成；本次文件改动已撤回。详细原因可在帮助页的“最近问题”查看。"
                elif kind == "refresh-after-remove":
                    problem["summary"] = "图包删除与临时副本清理已完成，但状态核对失败，请重新核对状态。"
                self._message = problem["summary"]
                self.errorReported.emit(problem)
            if not self._recovery:
                self._release()
            self.changed.emit()
            if kind in {"remove", "refresh-after-remove"}:
                # Consumers may have observed a transient state before rollback.
                # With pending recovery the lease remains held and read guards
                # must refuse refresh until the helper finishes recovery.
                self.resourcesChanged.emit()
            self._drain_refresh()
            return
        if kind in {"inspect", "refresh"} and self._cancel.is_set():
            self._message = "已取消核对，图包未改动。"
            self._release()
        elif kind == "inspect":
            self._pending = result
            self._message = "请确认删除范围。"
            title = "清理全部已导入图包" if result["packageId"] is None else "删除图包：" + package_title(result["packageId"])
            text = (f"将删除 {len(result['packageIds'])} 个图包的 {result['fileCount']} 个图包文件，"
                    f"共 {result['bytes']} 字节，包含已确认属于这些图包的旧副本。\n\n"
                    "成功后不保留这些图包的备份副本。软件必需的地图文件、其他图包仍需使用的文件、自定义路线，以及来源不明或改动过的文件会保留。点击取消可返回，不会开始删除。"
                    f"\n本次明确保留 {len(result['preserved'])} 个文件。")
            self.changed.emit()
            self.confirmationRequested.emit(title, text)
            return
        elif kind == "remove":
            self._completion = result
            self._start("refresh-after-remove", lambda cancel: declared_packages(self.sandbox),
                        "图包删除与临时副本清理已完成，正在核对状态…")
            return
        elif kind in {"refresh", "refresh-after-remove"}:
            self._rows = result
            self._message = "图包清单已读取；删除前将核对实际范围。" if result else "没有已登记的图包。"
            self._release()
            if kind == "refresh-after-remove":
                self.resourcesChanged.emit()
            if self._completion is not None:
                done, self._completion = self._completion, None
                self._message = (f"图包删除完成，已清理 {done['removedFiles']} 个图包文件，本次临时文件已清理完。"
                    if done["removedFiles"] else "没有需要删除的图包文件；保留项目未改动。")
                if done["unverifiedPreservedCount"]:
                    self._message = (f"已清理 {done['removedFiles']} 个已确认属于图包的文件，本次临时文件已清理完；"
                        f"仍保留 {done['unverifiedPreservedCount']} 个来源不明或改动过的项目，未能全部删除。")
                    problem = package_problem("removal-preserved-items", "package-remove")
                    problem.update(summary=self._message, kind="warning")
                    self.errorReported.emit(problem)
                elif done["removedFiles"]:
                    self.removalCompleted.emit("图包删除完成", self._message)
        self.changed.emit()
        self._drain_refresh()

    @Slot()
    def shutdown(self):
        self._closed = True
        self._refresh_requested = False
        self._pending = None
        self._cancel.set()
        if not self._busy:
            self._release()
