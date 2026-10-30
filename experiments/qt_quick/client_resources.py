"""Qt resource actions over the production receipt/transaction helpers.

Uses the shared baked-production or marked-isolated installation contract. Workers
never touch Qt state; their queued results are checked against operation tokens.
An upgrade cannot be cancelled once submitted: the shared transaction owns its
rollback. Additive author imports/removals honour cancellation before commit.
"""
from __future__ import annotations

import json
import os
import re
from pathlib import Path
import sys
import threading
from uuid import uuid4

from PySide6.QtCore import QObject, Property, QRunnable, QThreadPool, QUrl, Qt, Signal, Slot

from client_paths import RESOURCE_ROOT as ROOT, validate_installation_root
sys.path.insert(0, str(ROOT / "app"))
import star_map_import as importer
import author_package_management as manager
import client_resource_recovery as recovery


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


def _duplicate_import(result):
    """Only a committed import with no additions/replacements is a duplicate."""
    return (isinstance(result, dict) and type(result.get("added")) is int
            and result["added"] == 0 and type(result.get("alreadyPresent")) is int
            and result["alreadyPresent"] > 0 and not result.get("replaceFiles")
            and not result.get("replaced"))


class ResourceService(QObject):
    changed = Signal()
    confirmationRequested = Signal(str, str)
    resourcesChanged = Signal()
    recoveryCloseRequested = Signal()
    importCloseRequested = Signal()
    importCompleted = Signal(str, str)

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
        self._recovery_rows = []
        self._recovery_allowed = lambda: False
        self._recovery_launcher = None
        self._recovery_handoff = False
        self._recovery_notice = ""
        self._receipt_needs_notification = False
        self._lease_provider, self._lease_id = None, None
        self._import_launcher = None
        self._import_handoff = False
        self._import_notice = ""
        self._import_receipt_notification = False
        self._import_duplicate = False
        self._import_completion_message = ""
        self._import_package_id = ""
        self._helper_popup_request = ""

    packageRows = Property("QVariantList", lambda s: s._rows, notify=changed)
    busy = Property(bool, lambda s: s._busy, notify=changed)
    message = Property(str, lambda s: "\n".join(filter(None, (s._import_notice, s._recovery_notice, s._message))), notify=changed)
    pendingKind = Property(str, lambda s: s._kind if s._pending else "", notify=changed)
    canCancel = Property(bool, lambda s: s._can_cancel, notify=changed)
    recoveryRequired = Property(bool, lambda s: s._recovery, notify=changed)
    operationGeneration = Property(int, lambda s: s._token, notify=changed)
    recoveryTransactions = Property("QVariantList", lambda s: s._recovery_rows, notify=changed)
    recoveryExecutionAllowed = Property(bool, lambda s: s._recovery_launcher is not None or bool(s._recovery_allowed()), notify=changed)
    recoveryHandoffPending = Property(bool, lambda s: s._recovery_handoff, notify=changed)
    recoveryUsesHelper = Property(bool, lambda s: s._recovery_launcher is not None, notify=changed)

    def setImportLauncher(self, launcher):
        if not self._available() or not callable(launcher):
            raise RuntimeError("Cannot change import launcher during an operation")
        self._import_launcher = launcher

    def recordImportReceipt(self, receipt):
        # Owner validates the persisted request binding before forwarding it.
        if type(receipt.get("success")) is not bool:
            raise ValueError("Invalid import receipt")
        self._import_duplicate = bool(receipt["success"]) and _duplicate_import(receipt)
        self._import_notice = (("上次导入的资源内容已存在，正在核对实际状态。" if self._import_duplicate else
            "上次资源导入已完成，正在核对实际状态。") if receipt["success"] else
            "上次资源未完成导入：" + str(receipt.get("reason", "结果不可用")))
        self._import_receipt_notification = bool(receipt["success"])
        self._import_package_id = str(receipt.get("packageId", ""))
        request = str(receipt.get("requestId", ""))
        if receipt["success"] and re.fullmatch(r"[A-Za-z0-9-]{1,64}", request):
            seen = self.sandbox / "work/map-packages/qt-import-notice-seen.json"
            try:
                importer._no_links(seen)
                acknowledged = json.loads(seen.read_text("utf-8")).get("requestId") if seen.is_file() else None
            except (OSError, ValueError, TypeError, AttributeError):
                acknowledged = None
            self._helper_popup_request = request if acknowledged != request else ""
        self.changed.emit()

    @Slot()
    def acknowledgeImportCompletion(self):
        """Acknowledge only the owner's validated helper receipt, never its result."""
        request = self._helper_popup_request
        if not request:
            return
        target = self.sandbox / "work/map-packages/qt-import-notice-seen.json"
        temporary = target.with_name(target.name + "." + uuid4().hex + ".tmp")
        try:
            importer._no_links(target)
            target.parent.mkdir(parents=True, exist_ok=True)
            with temporary.open("x", encoding="utf-8") as stream:
                json.dump({"requestId": request}, stream)
            os.replace(temporary, target)
            self._helper_popup_request = ""
        except OSError:
            # Acknowledgement failure cannot change the completed transaction.
            pass
        finally:
            if temporary.exists():
                temporary.unlink()

    def setRecoveryLauncher(self, launcher):
        """Inject a separate helper launcher; it must wait for normal parent exit.

        The worker callback receives (action, exact_plan) and returns requestId.
        The owner retains the Popen, handles its receipt on the next launch, and
        connects recoveryCloseRequested to the existing normal close sequence.
        This callback never grants permission for in-process recovery writes.
        """
        if not self._available() or not callable(launcher):
            raise RuntimeError("Cannot change recovery launcher during an operation")
        self._recovery_launcher = launcher
        self.changed.emit()

    def setRecoveryAllowedProvider(self, provider):
        """Caller proves all resource consumers stopped, not merely hidden."""
        if self._busy or self._pending is not None or not callable(provider):
            raise RuntimeError("Cannot change recovery phase during an operation")
        self._recovery_allowed = provider
        self.changed.emit()

    def recordRecoveryReceipt(self, receipt):
        """Display an owner's request-bound helper result on the next launch.

        The owner verifies persisted requestId, planSha256 and action first.
        Success invalidates consumers only after the next actual resource scan.
        """
        if (not isinstance(receipt, dict) or type(receipt.get("success")) is not bool
                or not re.fullmatch(r"[A-Za-z0-9-]{1,64}", str(receipt.get("requestId", "")))
                or not re.fullmatch(r"[0-9a-f]{64}", str(receipt.get("planSha256", "")))
                or receipt.get("action") not in ("recover", "rollback")):
            raise ValueError("Invalid request-bound recovery receipt")
        if receipt["success"]:
            result = receipt.get("operationResult", {})
            if (not isinstance(result, dict) or result.get("status") != "rolled-back"
                    or result.get("upgradeTransactionId") != receipt.get("transactionId")):
                raise ValueError("Recovery receipt does not confirm old-resource restoration")
            self._recovery_notice = "恢复工具已还原旧资源，正在核对实际状态。"
            self._receipt_needs_notification = True
        else:
            self._recovery_notice = "恢复工具未能完成，备份和冲突文件已保留。详情：" + str(receipt.get("reason", "结果未提供失败原因"))
        self.changed.emit()

    def _recovery_history(self, cancel):
        importer._check_cancelled(cancel.is_set)
        # Inspection serializes with import writers but does not require closure.
        with importer._writer_locked_installation(self.sandbox, require_closed=False) as (root, _state):
            return recovery.history_locked(root)

    @Slot()
    def inspectRecovery(self):
        if self._available():
            self._start("recovery-history", self._recovery_history, "正在核对恢复记录…")

    def _request_recovery(self, action, transaction_id=None):
        if not self._available():
            return
        if self._recovery_launcher is None and not self._recovery_allowed():
            self._message = "请先停止识别和所有资源读取，再进入资源恢复阶段。"
            self.changed.emit()
            return
        def inspect(cancel):
            importer._check_cancelled(cancel.is_set)
            with importer._writer_locked_installation(self.sandbox, require_closed=False) as (root, _state):
                plan = recovery.inspect_locked(root, transaction_id)
                if action == "rollback" and plan["status"] == "rolled-back":
                    raise importer.ImportRejected("recovery-operation-no-longer-valid")
                return action, plan
        self._start("inspect-recovery", inspect, "正在核对恢复范围…")

    @Slot()
    def requestRecovery(self):
        self._request_recovery("recover")

    @Slot(str)
    def requestRollback(self, transaction_id):
        self._request_recovery("rollback", transaction_id)

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
        validate_installation_root(self.sandbox)

    def _available(self):
        return (not self._closed and not self._busy and self._pending is None
                and not self._recovery_handoff and not self._import_handoff)

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

    def importVerifiedArchive(self, file_url, expected, expected_generation):
        """Bind an online selection before reusing the original local import."""
        if (not self._available() or self._recovery or type(expected_generation) is not int
                or expected_generation != self._token):
            return False
        try:
            expected = dict(expected)
            if (set(expected) != {"packageId", "bytes", "sha256"}
                    or not isinstance(expected["packageId"], str) or len(expected["packageId"]) > 64
                    or not re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", expected["packageId"])
                    or type(expected["bytes"]) is not int or expected["bytes"] <= 0
                    or not re.fullmatch(r"[0-9a-f]{64}", str(expected["sha256"]))):
                raise ValueError("在线图包校验信息无效")
            url = QUrl(file_url)
            if url.scheme() and not url.isLocalFile():
                raise ValueError("在线图包必须位于本地下载缓存")
            path = Path(url.toLocalFile() if url.isLocalFile() else file_url)
            importer._no_links(path)
            if not path.resolve().is_relative_to(self.sandbox / "work/map-packages/downloads"):
                raise ValueError("在线图包不在此客户端的下载缓存内")
        except (OSError, ValueError, TypeError, KeyError) as error:
            self._message = str(error)
            self.changed.emit()
            return False
        def inspect(cancel):
            importer._no_links(path)
            if path.stat().st_size != expected["bytes"]:
                raise importer.ImportRejected("online-archive-size-mismatch")
            result = importer.inspect_package(path, self.sandbox)
            if (result.get("packageId") != expected["packageId"]
                    or result.get("archiveSha256") != expected["sha256"]
                    or path.stat().st_size != expected["bytes"]):
                raise importer.ImportRejected("online-archive-binding-mismatch")
            return str(path), result
        self._start("inspect", inspect, "正在核对在线索引与本地图包。")
        return self._busy

    def _install(self, path, result):
        live = importer.supports_live_author_import(result)
        if not live and self._import_launcher is not None:
            launcher = self._import_launcher
            def launch(_cancel):
                with importer._writer_locked_installation(self.sandbox, require_closed=False) as (root, _state):
                    current = importer.inspect_package(path, root)
                    if _confirmation_fingerprint(current) != _confirmation_fingerprint(result):
                        raise importer.ImportRejected("resource-plan-changed-reinspect-required")
                    request_id = launcher(path, dict(result))
                if not isinstance(request_id, str) or not request_id:
                    raise importer.ImportRejected("import-helper-launch-not-confirmed")
                return request_id
            self._start("launch-import", launch, "正在准备导入工具，随后正常关闭客户端。", cancellable=False)
            return
        def install(cancel):
            if live:
                # The session lease remains held through commit. Revalidate
                # consent under the same OS writer lock as the shared live
                # transaction, including the previous receipt fingerprint.
                with importer._writer_locked_installation(self.sandbox, require_closed=False) as (root, state):
                    current = importer.inspect_package(path, root)
                    if _confirmation_fingerprint(current) != _confirmation_fingerprint(result):
                        raise importer.ImportRejected("resource-plan-changed-reinspect-required")
                    return importer._install_staged(path, root, state, live=True,
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
        elif kind == "recovery":
            action, plan = pending
            if self._recovery_launcher is not None:
                launcher = self._recovery_launcher
                def launch(_cancel):
                    # The helper repeats this after normal process exit under
                    # the unchanged closed-app lock. Launch is not a commit.
                    with importer._writer_locked_installation(self.sandbox, require_closed=False) as (root, _state):
                        if recovery.inspect_locked(root, plan["transactionId"]) != plan:
                            raise importer.ImportRejected("recovery-plan-changed-reinspect-required")
                    request_id = launcher(action, dict(plan))
                    if not isinstance(request_id, str) or not request_id:
                        raise importer.ImportRejected("recovery-helper-launch-not-confirmed")
                    return request_id
                self._start("launch-recovery", launch, "正在准备恢复工具，随后正常关闭客户端。", cancellable=False)
                return
            if not self._recovery_allowed():
                self._message = "资源读取状态已改变，请重新进入恢复阶段并核对。"
                self._release_lease()
                self.changed.emit()
                return
            def execute(_cancel):
                if not self._recovery_allowed():
                    raise importer.ImportRejected("resource-consumers-not-stopped")
                with importer._locked_installation(self.sandbox, True) as (root, _state):
                    if not self._recovery_allowed():
                        raise importer.ImportRejected("resource-consumers-not-stopped")
                    return recovery.execute_locked(root, plan, action)
            self._start("recover", execute, "正在恢复旧资源，请等待事务完成…", cancellable=False)
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
            if kind == "refresh-after-write":
                self._import_completion_message = ""
            recovery = error.get("recovery", False) if isinstance(error, dict) else False
            error = error["reason"] if isinstance(error, dict) else str(error)
            self._recovery = "upgrade-recovery-required" in error or "pending-upgrade" in error or recovery
            if self._recovery:
                self._rows = [dict(row, canRemove=False, status="recovery-required") for row in self._rows]
                self._message = "资源事务需要恢复。备份与日志已保留，请核对恢复记录并在恢复阶段还原旧资源。详情：" + error
            elif "import-cancelled" in error:
                self._message = "操作已取消。"
            else:
                # Do not claim rollback succeeded: recovery conflicts retain backups.
                self._message = "资源操作失败：" + error
            self._kind = ""
            self._release_lease()
            self.changed.emit()
            return
        if kind in ("refresh", "inspect", "inspect-remove", "refresh-after-write", "recovery-history", "inspect-recovery") and self._cancel.is_set():
            self._message, self._kind = "操作已取消。", ""
            if kind == "refresh-after-write":
                self._import_completion_message = ""
                self._message = "状态核对已取消；已完成的资源改动仍然保留，可重新核对。"
            self._release_lease()
            self.changed.emit()
            return
        if kind == "recovery-history":
            self._recovery_rows, self._kind = result, ""
            self._recovery = any(row["pending"] for row in result)
            self._message = "恢复记录已核对。" if result else "没有可恢复的升级记录。"
        elif kind == "launch-recovery":
            self._kind, self._recovery_handoff = "", True
            self._message = "恢复工具已准备，正在正常关闭客户端；重新打开后核对实际结果。"
            self.changed.emit()
            # Keep the lease until shutdown. Never emit resourcesChanged or
            # report success for a helper which has only started waiting.
            self.recoveryCloseRequested.emit()
            return
        elif kind == "inspect-recovery":
            action, plan = result
            self._pending, self._kind = result, "recovery"
            self._message = "等待确认恢复旧资源。"
            self.changed.emit()
            self.confirmationRequested.emit("恢复旧资源" if action == "recover" else "回滚已完成升级",
                "%s\n事务：%s\n状态：%s，涉及 %s 个资源。\n核对指纹：%s\n\n恢复旧文件和登记记录并保留备份。本地改动或确认范围变化会拒绝执行；不会继续安装新版本。" % (
                    plan["packageId"], plan["transactionId"], plan["status"], plan["fileCount"], plan["planSha256"])
                + ("\n\n确认后将正常关闭客户端。恢复工具仅在客户端正常退出后执行；重新打开客户端核对结果。" if self._recovery_launcher is not None else ""))
            return
        elif kind == "launch-import":
            self._kind, self._import_handoff = "", True
            self._message = "导入工具已准备；正在正常关闭客户端。重新打开后核对结果。"
            self.changed.emit()
            self.importCloseRequested.emit()
            return
        elif kind == "refresh":
            self._rows, self._recovery, self._kind = result, False, ""
            if self._import_receipt_notification:
                self._import_receipt_notification = False
                self._import_notice = ("上次导入的资源内容已存在，实际状态已核对。" if self._import_duplicate else
                                       "上次资源导入已完成，实际状态已核对。")
                self.resourcesChanged.emit()
                if self._helper_popup_request:
                    title = next((str(row.get("title") or row.get("name") or self._import_package_id)
                                  for row in result if row.get("packageId") == self._import_package_id),
                                 self._import_package_id or "地图资源包")
                    self.importCompleted.emit(title, self._import_notice)
            self._message = "本地资源已核对。" if result else "尚未安装资源，可导入 ZIP 资源包。"
            if self._receipt_needs_notification:
                self._receipt_needs_notification = False
                self._recovery_notice = "恢复工具已还原旧资源，实际状态已核对。"
                self.resourcesChanged.emit()
        elif kind == "inspect":
            path, inspection = result
            self._import_package_id = str(inspection.get("packageId", ""))
            if (inspection.get("replaceFiles") or (self._import_launcher is not None
                    and not importer.supports_live_author_import(inspection))):
                self._kind, self._pending = "overwrite", (path, inspection)
                self._message = "等待确认资源更新。"
                self.changed.emit()
                self.confirmationRequested.emit("确认更新资源", "将替换 %s 中 %s 个已登记且未改动的文件，并新增 %s 个文件。旧文件将由现有事务备份；未知文件与本地改动不会覆盖。\n\n仅作用于当前资源目录。" % (
                    inspection["packageId"], len(inspection["replaceFiles"]), len(inspection["missingFiles"]))
                    + ("\n\n确认后正常关闭客户端，由独立导入工具执行；请重新打开客户端核对结果。" if self._import_launcher is not None and not importer.supports_live_author_import(inspection) else ""))
                return
            self._install(path, inspection)
            return
        elif kind == "inspect-remove":
            self._kind, self._pending = "remove", result
            self._message = "等待确认备份并移除。"
            self.changed.emit()
            self.confirmationRequested.emit("备份并移除资源", "%s\n将备份并移除 %s 个作者显示文件（%s 字节），其中 %s 个有本地改动、%s 个已缺失。保留 %s 个核心文件，以及未登记文件和自定义路线。\n\n备份保留在当前资源目录 work/map-package-removals；此操作不提供自动恢复。" % (
                result["title"], result["fileCount"], result["bytes"], len(result["modifiedFiles"]),
                len(result["missingFiles"]), result["preservedCoreFiles"]))
            return
        elif kind in ("install", "remove", "recover"):
            # A successful return is the commit result even if Cancel arrived
            # just after the helper's commit point. Never label that cancelled.
            self._kind = ""
            duplicate = kind == "install" and _duplicate_import(result)
            # Keep the final feedback pending until the actual-state scan succeeds.
            self._import_completion_message = (("资源内容已存在，登记与状态已核对。" if duplicate else
                "资源安装完成，登记与状态已核对。") if kind == "install" else "")
            self._message = ("资源已备份并移除。备份：" + result["backupPath"] if kind == "remove" else
                             "资源内容已存在，正在核对登记与状态…" if duplicate else
                             "资源安装完成，正在核对状态…")
            self.resourcesChanged.emit()
            if kind == "recover":
                self._recovery_rows = []
                self._message = "旧资源已恢复，正在核对状态…"
            message = self._message
            self._start("refresh-after-write", self._snapshot, message)
            return
        elif kind == "refresh-after-write":
            self._rows, self._recovery, self._kind = result, False, ""
            if self._import_completion_message:
                self._message = self._import_completion_message
                title = next((str(row.get("title") or row.get("name") or self._import_package_id)
                              for row in result if row.get("packageId") == self._import_package_id),
                             self._import_package_id or "地图资源包")
                self.importCompleted.emit(title, self._message)
            self._import_completion_message = ""
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
