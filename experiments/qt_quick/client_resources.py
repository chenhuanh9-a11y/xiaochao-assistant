"""Qt resource actions over the production receipt/transaction helpers.

Only a marked installation beneath this checkout's work/ is accepted. Workers
never touch Qt state; their queued results are checked against operation tokens.
An upgrade cannot be cancelled once submitted: the shared transaction owns its
rollback. Additive author imports/removals honour cancellation before commit.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import sys
import threading

from PySide6.QtCore import QObject, Property, QRunnable, QThreadPool, QUrl, Qt, Signal, Slot

from client_paths import RESOURCE_ROOT as ROOT, WORK_ROOT
sys.path.insert(0, str(ROOT / "app"))
import star_map_import as importer
import author_package_management as manager


class _Signals(QObject):
    done = Signal(int, object, object)


class _Work(QRunnable):
    def __init__(self, token, function, recovery_probe):
        super().__init__()
        self.token, self.function = token, function
        self.recovery_probe = recovery_probe
        self.signals = _Signals()

    def run(self):
        try:
            self.signals.done.emit(self.token, self.function(), None)
        except Exception as error:
            try:
                recovery = self.recovery_probe()
            except OSError:
                recovery = False
            self.signals.done.emit(self.token, None, dict(reason=str(error), recovery=recovery))


def _confirmation_fingerprint(result):
    return json.dumps({key: result.get(key) for key in (
        "packageId", "archiveSha256", "previousReceiptSha256", "files",
        "replaceFiles", "missingFiles")}, sort_keys=True, separators=(",", ":"))


class ResourceService(QObject):
    changed = Signal()
    confirmationRequested = Signal(str, str)
    resourcesChanged = Signal()

    def __init__(self, sandbox: Path, parent=None):
        super().__init__(parent)
        self.sandbox = Path(os.path.abspath(sandbox))
        self._validate_root()
        self.pool = QThreadPool(self)
        self.pool.setMaxThreadCount(1)
        self._rows, self._busy, self._message = [], False, ""
        self._pending, self._kind = None, ""
        self._token, self._jobs = 0, {}
        self._cancel = threading.Event()
        self._closed, self._can_cancel = False, False
        self._recovery = False
        self._lease_provider, self._lease_id = None, None

    packageRows = Property("QVariantList", lambda s: s._rows, notify=changed)
    busy = Property(bool, lambda s: s._busy, notify=changed)
    message = Property(str, lambda s: s._message, notify=changed)
    pendingKind = Property(str, lambda s: s._kind if s._pending else "", notify=changed)
    canCancel = Property(bool, lambda s: s._can_cancel, notify=changed)
    recoveryRequired = Property(bool, lambda s: s._recovery, notify=changed)

    def setLeaseProvider(self, provider):
        """Inject the session coordinator before exposing this service to QML."""
        if self._busy or self._pending is not None:
            raise RuntimeError("Cannot replace an active resource lease provider")
        self._lease_provider = provider

    def _release_lease(self):
        if self._lease_id is not None:
            lease_id, self._lease_id = self._lease_id, None
            self._lease_provider.releaseResourceLease(lease_id)

    def _validate_root(self):
        importer._no_links(self.sandbox)
        if not self.sandbox.resolve().is_relative_to(WORK_ROOT):
            raise ValueError("Only a marked sandbox beneath this clone's work/ is writable")
        marker = self.sandbox / ".qt-lab-owner"
        importer._no_links(marker)
        if not marker.is_file() or marker.read_text(encoding="utf-8").strip() != "qt-quick-isolated-v1":
            raise ValueError("Missing or invalid Qt sandbox owner marker")

    def _available(self):
        return not self._closed and not self._busy and self._pending is None

    def _start(self, kind, function, message, *, cancellable=True):
        if self._lease_provider is not None and self._lease_id is None:
            lease = self._lease_provider.acquireResourceLease()
            if not lease:
                self._kind, self._pending = "", None
                self._message = "当前会话正在运行或处理操作，请停止后再管理资源。"
                self.changed.emit()
                return
            self._lease_id = lease
        self._token += 1
        self._kind, self._busy, self._can_cancel = kind, True, cancellable
        self._message = message
        self._cancel = cancel = threading.Event()
        def guarded():
            self._validate_root()
            importer._check_cancelled(cancel.is_set)
            return function(cancel)
        job = _Work(self._token, guarded,
                    lambda: (self.sandbox / "work/map-packages/pending-upgrade.json").exists())
        self._jobs[self._token] = job
        job.signals.done.connect(self._completed, Qt.ConnectionType.QueuedConnection)
        self.changed.emit()
        self.pool.start(job)

    def _snapshot(self, cancel):
        importer._pending_upgrade(self.sandbox)
        return manager.managed_author_packages_snapshot(self.sandbox, cancelled=cancel.is_set)

    @Slot()
    def refresh(self):
        if self._available():
            self._start("refresh", self._snapshot, "正在核对本地资源…")

    @Slot(str)
    def importArchive(self, file_url):
        if not self._available() or self._recovery:
            return
        url = QUrl(file_url)
        path = Path(url.toLocalFile()) if url.isLocalFile() else Path(file_url)
        if (url.scheme() and not url.isLocalFile()) or not str(file_url).strip():
            self._message = "请选择本地 ZIP 资源包。"
            self.changed.emit()
            return
        self._start("inspect", lambda cancel: (str(path), importer.inspect_package(path, self.sandbox)),
                    "正在校验资源包…")

    @Slot(str)
    def requestRemove(self, package_id):
        if self._available() and not self._recovery:
            self._start("inspect-remove", lambda cancel: manager.inspect_author_package_removal(
                self.sandbox, package_id), "正在核对备份与移除范围…")

    def _install(self, path, result):
        live = importer.supports_live_author_import(result)
        def install(cancel):
            if live:
                return importer.install_author_package_live(path, self.sandbox, application_idle=True,
                    expected_archive_sha256=result["archiveSha256"], cancelled=cancel.is_set)
            # Hold the same writer lock between confirmation recheck and the
            # existing staging/upgrade transaction; never expand an old consent.
            with importer._locked_installation(self.sandbox, True) as (root, state):
                current = importer.inspect_package(path, root)
                if _confirmation_fingerprint(current) != _confirmation_fingerprint(result):
                    raise importer.ImportRejected("resource-plan-changed-reinspect-required")
                return importer._install_staged(path, root, state,
                    expected_archive_sha256=result["archiveSha256"])
        self._start("install", install, "正在安装资源…" if live else "正在执行资源事务，请等待完成…",
                    cancellable=live)

    @Slot(bool)
    def confirmOperation(self, accepted):
        if self._closed or self._busy or self._pending is None:
            return
        pending, kind = self._pending, self._kind
        self._pending, self._kind = None, ""
        if not accepted:
            self._release_lease()
            self._message = "已取消，资源未改动。"
            self.changed.emit()
            return
        if kind == "overwrite":
            self._install(*pending)
        elif kind == "remove":
            plan = pending
            self._start("remove", lambda cancel: manager.remove_author_package_live(
                self.sandbox, plan["packageId"], application_idle=True,
                expected_receipt_sha256=plan["receiptSha256"],
                expected_plan_sha256=plan["planSha256"], cancelled=cancel.is_set), "正在备份并移除资源…")

    @Slot()
    def cancel(self):
        if self._pending is not None:
            self.confirmOperation(False)
        elif self._busy and self._can_cancel:
            self._cancel.set()
            self._can_cancel = False
            self._message = "正在请求取消；等待事务确认最终结果…"
            self.changed.emit()

    @Slot(int, object, object)
    def _completed(self, token, result, error):
        self._jobs.pop(token, None)
        if self._closed or token != self._token:
            if self._closed:
                self._release_lease()
            return
        kind = self._kind
        self._busy, self._can_cancel = False, False
        if error:
            recovery = error.get("recovery", False) if isinstance(error, dict) else False
            error = error["reason"] if isinstance(error, dict) else str(error)
            self._recovery = "upgrade-recovery-required" in error or "pending-upgrade" in error or recovery
            if self._recovery:
                self._rows = [dict(row, canRemove=False, status="recovery-required") for row in self._rows]
                self._message = "资源事务需要恢复。备份与日志已保留；请关闭客户端，核对后使用现有恢复工具。"
            elif "import-cancelled" in error:
                self._message = "操作已取消。"
            else:
                # Do not claim rollback succeeded: recovery conflicts retain backups.
                self._message = "资源操作失败：" + error
            self._kind = ""
            self._release_lease()
            self.changed.emit()
            return
        if kind in ("refresh", "inspect", "inspect-remove", "refresh-after-write") and self._cancel.is_set():
            self._message, self._kind = "操作已取消。", ""
            if kind == "refresh-after-write":
                self._message = "状态核对已取消；已完成的资源改动仍然保留，可重新核对。"
            self._release_lease()
            self.changed.emit()
            return
        if kind == "refresh":
            self._rows, self._recovery, self._kind = result, False, ""
            self._message = "本地资源已核对。" if result else "尚未安装资源，可导入 ZIP 资源包。"
        elif kind == "inspect":
            path, inspection = result
            if inspection.get("replaceFiles"):
                self._kind, self._pending = "overwrite", (path, inspection)
                self._message = "等待确认资源更新。"
                self.changed.emit()
                self.confirmationRequested.emit("确认更新资源", "将替换 %s 中 %s 个已登记且未改动的文件，并新增 %s 个文件。旧文件将由现有事务备份；未知文件与本地改动不会覆盖。\n\n仅作用于此 Qt 隔离副本。" % (
                    inspection["packageId"], len(inspection["replaceFiles"]), len(inspection["missingFiles"])))
                return
            self._install(path, inspection)
            return
        elif kind == "inspect-remove":
            self._kind, self._pending = "remove", result
            self._message = "等待确认备份并移除。"
            self.changed.emit()
            self.confirmationRequested.emit("备份并移除资源", "%s\n将备份并移除 %s 个作者显示文件（%s 字节），其中 %s 个有本地改动、%s 个已缺失。保留 %s 个核心文件，以及未登记文件和自定义路线。\n\n备份保留在隔离副本 work/map-package-removals；此操作不提供自动恢复。" % (
                result["title"], result["fileCount"], result["bytes"], len(result["modifiedFiles"]),
                len(result["missingFiles"]), result["preservedCoreFiles"]))
            return
        elif kind in ("install", "remove"):
            # A successful return is the commit result even if Cancel arrived
            # just after the helper's commit point. Never label that cancelled.
            self._kind = ""
            self._message = ("资源已备份并移除。备份：" + result["backupPath"] if kind == "remove"
                             else "资源安装完成，正在核对状态…")
            self.resourcesChanged.emit()
            message = self._message
            self._start("refresh-after-write", self._snapshot, message)
            return
        elif kind == "refresh-after-write":
            self._rows, self._recovery, self._kind = result, False, ""
        self._release_lease()
        self.changed.emit()

    @Slot()
    def shutdown(self):
        """Stop accepting work; in-flight transactions retain their actual outcome."""
        self._closed = True
        self._pending = None
        if not self._busy:
            self._release_lease()
        if self._can_cancel:
            self._cancel.set()
        # Caller must keep this QObject alive until the pool drains. Never
        # terminate a thread halfway through a receipt or upgrade transaction.
