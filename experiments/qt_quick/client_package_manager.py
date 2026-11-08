"""Qt package management and verified retained-author rollback transactions."""
from __future__ import annotations

import os
from pathlib import Path
import threading

from PySide6.QtCore import QObject, Property, QThreadPool, Qt, Signal, Slot
from client_paths import validate_installation_root
from client_resources import _Work, package_problem, package_cancelled, package_title
from client_resource_errors import error_fields
import map_package_removal as removal
import author_resource_snapshots as snapshots
import author_snapshot_history as history
from map_package_summary import declared_packages


class PackageManagerService(QObject):
    changed = Signal()
    errorReported = Signal(object)
    confirmationRequested = Signal(str, str)
    resourcesChanged = Signal()
    removalCompleted = Signal(str, str)
    historyReady = Signal(str)

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
        self._history_rows, self._history_package = [], ""
        self._publishing_history_result = False
        self._refresh_result_notice = ""
        self.resources.resourcesChanged.connect(self._invalidate_history)
        self.resources.changed.connect(self._drain_refresh)

    packageRows = Property("QVariantList", lambda s: s._rows, notify=changed)
    busy = Property(bool, lambda s: s._busy, notify=changed)
    operationStage = Property(str, lambda s: ("cancelling" if s._cancel.is_set() else "manage-" + s._kind) if s._busy else "", notify=changed)
    pendingKind = Property(str, lambda s: s._pending.get("action", "remove") if s._pending is not None else "", notify=changed)
    historyRows = Property("QVariantList", lambda s: s._history_rows, notify=changed)
    historyPackageId = Property(str, lambda s: s._history_package, notify=changed)
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
                self._message = "当前会话或资源操作仍在运行，请停止后再管理图包。"
                self.changed.emit()
                return False
            self._lease_id = lease
        if kind != 'refresh':
            self._refresh_result_notice = ""
        self._token += 1
        self._busy, self._kind, self._message = True, kind, message
        self._cancel = cancel = threading.Event()
        def guarded():
            validate_installation_root(self.sandbox)
            return function(cancel)
        job = _Work(self._token, guarded, lambda: (self.sandbox / removal.PENDING).exists() or (self.sandbox / snapshots.PENDING).exists())
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
        # The real client forwards our resourcesChanged back through the
        # resource service. A freshly verified restore result already includes
        # this same package inventory, so do not re-enter refresh for that echo.
        if not self._closed and not self._publishing_history_result:
            self._refresh_requested = True
            self._drain_refresh()

    @Slot()
    def _drain_refresh(self):
        if self._refresh_requested and self._available():
            if self._start("refresh", lambda cancel: declared_packages(self.sandbox, cancelled=cancel.is_set),
                           self._refresh_result_notice or "正在查看已导入图包和上次操作记录…"):
                self._refresh_requested = False
            elif self._refresh_result_notice:
                self._message = self._refresh_result_notice + ' 资源会话忙，列表核对仍待完成。'
                self.changed.emit()

    @Slot()
    def _invalidate_history(self):
        if self._history_rows and not self._publishing_history_result:
            self._history_rows = []
            self.changed.emit()

    @Slot(str)
    def requestHistory(self, package_id):
        if self._available():
            self._start("history", lambda cancel: (package_id, history.list_versions(
                self.sandbox, package_id, cancelled=cancel.is_set)), "正在核对保留版本的来源记录…")

    @Slot(str, str)
    def requestRestoreVersion(self, package_id, transaction_id):
        if self._available():
            self._start("inspect-restore", lambda cancel: history.inspect_restore(
                self.sandbox, package_id, transaction_id, cancelled=cancel.is_set),
                "正在核对历史版本内容与当前底模…")

    @Slot()
    def dismissHistory(self):
        # Closing this view cancels only its reads/unaccepted confirmation.
        # Once accepted, restore owns the normal write/recovery lifecycle and
        # must not be silently cancelled by navigation or a closing popup.
        if self._busy and self._kind in {'history', 'inspect-restore'}:
            self.cancel()
        elif self._pending is not None and self._pending.get('action') == 'restore-author-version':
            self.confirmOperation(False)

    @Slot(str)
    def requestRemove(self, package_id):
        if self._available():
            self._inspect(package_id)

    @Slot()
    def requestClearAll(self):
        if self._available():
            self._inspect(None)

    def _inspect(self, package_id):
        def inspect(cancel):
            return removal.inspect_package_removal(self.sandbox, package_id, cancelled=cancel.is_set)
        self._start("inspect", inspect, "正在核对本次处理范围…")

    @Slot(bool)
    def confirmOperation(self, accepted):
        if self._closed or self._busy or self._pending is None:
            return
        plan, self._pending = self._pending, None
        if not accepted:
            self._message = ("已取消回退，当前作者版本未改动。" if plan.get('action') == 'restore-author-version'
                             else "已取消删除，图包未改动。")
            self._release()
            self.changed.emit()
            self._drain_refresh()
            return
        if plan.get('action') == 'restore-author-version':
            self._start("restore", lambda cancel: history.restore_version(self.sandbox, plan['packageId'],
                plan['sourceTransactionId'], application_idle=True, expected_plan_sha256=plan['planSha256'],
                cancelled=cancel.is_set), "正在切换到已核对的作者历史版本…")
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
            is_history = kind in {'history', 'inspect-restore', 'restore', 'refresh-after-restore'}
            if package_cancelled(error) and not self._recovery:
                self._message = (self._refresh_result_notice + ' 已取消后续列表核对。' if kind == 'refresh' and self._refresh_result_notice else
                                 "作者版本已切换；已取消列表核对，请重新核对当前状态。" if kind == 'refresh-after-restore' else
                                 "历史版本操作已取消；未提交的切换已撤回。" if is_history
                                 else "删除已取消；已撤回本次文件改动。")
                if kind == 'refresh': self._refresh_result_notice = ''
            else:
                problem = package_problem(error, "package-" + kind)
                if code == "removal-committed-cleanup-required":
                    problem["summary"] = "图包已删除，但本次临时文件还没清理完。请正常退出并重启，按提示继续清理；清理结束后才会提示完成。"
                elif self._recovery:
                    problem["summary"] = (
                        "作者版本切换结果需要恢复核对。请正常退出并重启，按提示恢复资源；当前版本是否已提交将以事务记录核验，无法确认来源的文件会保留。"
                        if is_history else "图包删除还没完成。请正常退出并重启，按提示恢复图包；无法确认来源的文件会保留。")
                elif is_history:
                    problem['summary'] = (
                        '历史版本没有与当前底模核对通过的层，不能回退。当前版本和全部文件均保留。'
                        if code == 'author-history-no-compatible-layers' else
                        '历史记录、文件或当前状态已改变，回退未能确认完成。请重新核对历史版本；不会覆盖改动过的历史图片。'
                        if kind != 'refresh-after-restore' else
                        '作者版本已切换，但列表核对未完成。请重新核对状态，不要重复操作。')
                elif kind == 'refresh' and self._refresh_result_notice:
                    problem['summary'] = self._refresh_result_notice + ' 列表仍未核对完成。'
                    self._refresh_result_notice = ''
                elif kind == "remove":
                    problem["summary"] = "图包删除未完成；本次文件改动已撤回。详细原因可在帮助页的“最近问题”查看。"
                elif kind == "refresh-after-remove":
                    problem["summary"] = "图包删除与临时副本清理已完成，但状态核对失败，请重新核对状态。"
                self._message = problem["summary"]
                self.errorReported.emit(problem)
            if not self._recovery:
                self._release()
            self.changed.emit()
            if kind in {"remove", "refresh-after-remove", "restore", "refresh-after-restore"}:
                # Consumers may have observed a transient state before rollback.
                # With pending recovery the lease remains held and read guards
                # must refuse refresh until the helper finishes recovery.
                self._invalidate_history()
                if is_history:
                    # Keep the known terminal result visible while an explicit
                    # follow-up inventory scan updates the package rows. The
                    # client signal echo must not re-enter and replace it.
                    notice = self._message
                    self._publishing_history_result = True
                    try:
                        self.resourcesChanged.emit()
                    finally:
                        self._publishing_history_result = False
                    if not self._recovery:
                        self._refresh_result_notice = notice
                        self._refresh_requested = True
                else:
                    self.resourcesChanged.emit()
            self._drain_refresh()
            return
        if kind in {"inspect", "refresh", "history", "inspect-restore"} and self._cancel.is_set():
            self._message = (self._refresh_result_notice + ' 已取消后续列表核对。'
                             if kind == 'refresh' and self._refresh_result_notice else "已取消核对，图包未改动。")
            if kind == 'refresh': self._refresh_result_notice = ''
            self._release()
        elif kind == "history":
            self._history_package, self._history_rows = result
            self._message = "历史列表已读取；回退前还会核对完整内容与当前底模。"
            self._release()
            self.changed.emit()
            self.historyReady.emit(self._history_package)
            self._drain_refresh()
            return
        elif kind == "inspect-restore":
            self._pending = result
            self._message = "请确认作者版本回退范围。"
            self.changed.emit()
            self.confirmationRequested.emit('回退作者图版本',
                result['title'] + '\n目标版本：' + result['snapshotId'][:12]
                + f"\n当前可用 {result['activeLayerCount']} 层，另有 {len(result['inactiveLayers'])} 层保持停用。"
                + '\n仅切换作者显示版本，不回退程序、识别底模或设置；图片和所有历史版本仍保留。'
                + '\n确认后重新校验并切换，未提交的失败会恢复原激活记录。取消不改动当前版本。')
            return
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
        elif kind == "restore":
            self._completion = result
            package = result['packageId']
            self._start("refresh-after-restore", lambda cancel: (declared_packages(self.sandbox, cancelled=cancel.is_set),
                history.list_versions(self.sandbox, package, cancelled=cancel.is_set)), "作者版本已切换，正在核对当前状态…")
            return
        elif kind == "refresh-after-restore":
            self._rows, self._history_rows = result
            done, self._completion = self._completion, None
            self._history_package = done['packageId']
            message = f"作者图已回退，当前启用 {done['activeLayerCount']} 层；程序、底模和历史文件未改动。"
            self._message = message
            self._release()
            self._publishing_history_result = True
            try:
                self.resourcesChanged.emit()
            finally:
                self._publishing_history_result = False
            # Preserve the established completion-to-notification wiring.
            self.removalCompleted.emit('作者图已回退', message)
        elif kind == "remove":
            self._completion = result
            self._start("refresh-after-remove", lambda cancel: declared_packages(self.sandbox),
                        "图包删除与临时副本清理已完成，正在核对状态…")
            return
        elif kind in {"refresh", "refresh-after-remove"}:
            self._rows = result
            self._message = self._refresh_result_notice or ("" if result else "没有已登记的图包。")
            self._refresh_result_notice = ""
            self._release()
            if kind == "refresh-after-remove":
                self._invalidate_history()
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
