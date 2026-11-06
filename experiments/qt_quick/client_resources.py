"""Qt resource actions over the production receipt/transaction helpers.

Uses the shared baked-production or marked-isolated installation contract. Workers
never touch Qt state; their queued results are checked against operation tokens.
An upgrade cannot be cancelled once submitted: the transaction owns rollback
before commit and temporary-object cleanup afterwards. Additive author imports
and removals honour cancellation before commit.
"""
from __future__ import annotations

import json
import os
import re
from pathlib import Path
import sys
import threading
from uuid import uuid4

from PySide6.QtCore import QObject, Property, QRunnable, QThreadPool, QTimer, QUrl, Qt, Signal, Slot

from client_paths import RESOURCE_ROOT as ROOT, validate_installation_root, bundled_author_root, runtime_paths
sys.path.insert(0, str(ROOT / "app"))
import star_map_import as importer
import author_package_management as manager
import client_resource_recovery as recovery
from client_resource_errors import error_text as _error_text, error_fields, error_category


def package_cancelled(error):
    return error_fields(error)["code"] in {"cancelled", "import-cancelled", "import-request-cancelled"}


def package_problem(error, action, *, online=False, recovery_required=False):
    """Small presentation adapter; original details stay separate for diagnosis."""
    fields = error_fields(error)
    raw, code = fields["reason"], fields["code"]
    category = error_category(code)
    title = "在线图包操作未完成" if online else "图包操作未完成"
    summary = ("这次在线图包操作没有完成。请刷新图包列表后重试。" if online else
               "这次图包操作没有完成。请点击“核对状态”查看当前结果，再重试。")
    if code == "non-local-archive":
        summary = "请选择保存在本机的 ZIP 图包文件，不能直接导入网页地址。"
    elif recovery_required:
        title, summary = "上次图包操作还没完成", "暂时不能继续导入或删除图包。请点击“恢复记录”，按提示完成上次操作。"
    elif action == "refresh-after-write":
        summary = "图包文件已经处理完，但还没确认是否可用。请点击“核对状态”再检查一次。"
    elif category == "network":
        summary = "暂时连接不上图包下载服务，这次下载没有完成。请检查网络，稍后重试。"
    elif category == "url":
        summary = "这个图包的下载地址暂时不可用。请联系图包提供者，也可以导入已经下载到本机的 ZIP 图包。"
    elif category == "binding":
        summary = "下载到的图包与所选项目不一致，因此没有导入。请刷新列表，再下载需要的图包。"
    elif category == "integrity":
        summary = "图包中的文件与包内说明不一致，暂时不能导入。请重新获取图包提供者发布的完整文件。"
    elif category == "limit":
        summary = "这个图包或下载列表过大，当前软件无法处理。请向图包提供者获取适用于当前版本的文件。"
    elif code == "dependency-contract-changed-reimport-unsupported":
        summary = "图包文件相同，但依赖要求已改变，当前不能仅更新依赖记录。本次未导入，原图包和安装记录未改动。"
    elif category == "dependency":
        summary = "这个图包需要的地图文件有缺失，或版本与当前软件不匹配，暂时不能导入。请查看图包适用的版本，使用与当前软件配套的图包。"
    elif category == "conflict":
        summary = "图包文件或安装记录发生了变化，这次操作已停止。请点击“核对状态”，重新确认后再试。"
    elif category == "unsafe":
        summary = "这个图包不能按当前方式安全导入，操作已停止。请使用图包提供者发布的原始 ZIP 文件。"
    elif category == "busy":
        summary = "本局或另一个图包操作还在进行。请先结束本局，或等待当前操作完成后再试。"
    elif category == "helper":
        summary = "图包处理没有完成，软件还没收到可以确认的结果。请点击“核对状态”；仍有问题时，可导出诊断信息寻求帮助。"
    elif category == "file":
        summary = "软件暂时无法访问图包文件。请确认磁盘空间足够、文件没有被其他程序占用，再重试。"
    elif category == "package":
        summary = ("暂时无法读取在线图包列表。请刷新列表；仍有问题时，请联系图包提供者。" if online else
                   "暂时无法读取这个图包，或它的格式不受支持。请选择图包提供者发布、适用于当前软件版本的 ZIP 文件。")
    return dict(title=title, summary=summary + " 可在“帮助 → 最近问题”查看详情。",
                raw=raw, action=("online:" if online else "resource:") + action,
                phase="operation", kind="error", errorCode=code, errorDetails=fields["details"])


def package_title(package_id):
    if package_id == "all-managed":
        return "全部已导入图包"
    return manager.PACKAGE_NAMES.get(package_id, "待确认的图包")


def recovery_status_label(status):
    return {
        "preparing": "上次更新准备中断", "prepared": "上次更新未完成",
        "committed": "更新已完成，待清理临时文件", "rolling-back": "正在恢复更新前的图包",
        "rollback-incomplete": "恢复尚未完成", "rolled-back": "已恢复，待清理临时文件",
        "additions-pending": "上次导入未完成", "removal-pending": "上次删除未完成",
        "upgrade-cleanup-pending": "还需清理临时文件", "upgrade-cleanup-available": "有临时文件可清理",
    }.get(status, "需要重新核对")


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
            self.signals.done.emit(self.token, None, dict(error_fields(error), recovery=recovery))


def _confirmation_fingerprint(result):
    return json.dumps({key: result.get(key) for key in (
        "packageId", "archiveSha256", "previousReceiptSha256", "files",
        "replaceFiles", "missingFiles", "legacyReceiptSha256")}, sort_keys=True, separators=(",", ":"))


def _duplicate_import(result):
    """Only a committed import with no additions/replacements is a duplicate."""
    return (isinstance(result, dict) and type(result.get("added")) is int
            and result["added"] == 0 and type(result.get("alreadyPresent")) is int
            and result["alreadyPresent"] > 0 and not result.get("replaceFiles")
            and not result.get("replaced"))


class ResourceService(QObject):
    changed = Signal()
    errorReported = Signal(object)
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
        self._recovery_verified_notice = "已恢复操作前的图包，状态已确认。"
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
    operationStage = Property(str, lambda s: ("cancelling" if s._cancel.is_set() else s._kind) if s._busy else "", notify=changed)
    pendingKind = Property(str, lambda s: s._kind if s._pending else "", notify=changed)
    canCancel = Property(bool, lambda s: s._can_cancel, notify=changed)
    recoveryRequired = Property(bool, lambda s: s._recovery, notify=changed)
    operationGeneration = Property(int, lambda s: s._token, notify=changed)
    recoveryTransactions = Property("QVariantList", lambda s: s._recovery_rows, notify=changed)
    recoveryExecutionAllowed = Property(bool, lambda s: s._recovery_launcher is not None or bool(s._recovery_allowed()), notify=changed)
    recoveryHandoffPending = Property(bool, lambda s: s._recovery_handoff, notify=changed)
    recoveryUsesHelper = Property(bool, lambda s: s._recovery_launcher is not None, notify=changed)

    @Slot(str, result=str)
    def packageTitle(self, package_id):
        return package_title(package_id)

    @Slot(str, result=str)
    def recoveryStatusLabel(self, status):
        return recovery_status_label(status)

    def setImportLauncher(self, launcher):
        if not self._available() or not callable(launcher):
            raise RuntimeError("Cannot change import launcher during an operation")
        self._import_launcher = launcher

    def recordImportReceipt(self, receipt):
        # Owner validates the persisted request binding before forwarding it.
        if type(receipt.get("success")) is not bool:
            raise ValueError("Invalid import receipt")
        self._import_duplicate = bool(receipt["success"]) and _duplicate_import(receipt)
        self._import_notice = (("这个图包已经安装，正在确认当前状态。" if self._import_duplicate else
            "上次导入已完成，正在确认图包是否可用。") if receipt["success"] else
            self._report_problem(dict(receipt, reason=receipt.get("reason", "import-helper-result-unavailable")),
                                 "import-receipt", deferred=True))
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
            status = result.get('status') if isinstance(result, dict) else None
            cleanup_only = status == 'upgrade-committed' or (isinstance(result, dict) and result.get('cleanupOnly') is True)
            additions = status in {'additions-rolled-back', 'additions-committed'}
            removal = status in {'removal-rolled-back', 'removal-committed'}
            identity_key = 'additionTransactionId' if additions else 'removalTransactionId' if removal else 'upgradeTransactionId'
            if (not isinstance(result, dict) or status not in {'rolled-back', 'upgrade-committed', 'additions-rolled-back', 'additions-committed',
                                                             'removal-rolled-back', 'removal-committed'}
                    or ('cleanupOnly' in result and type(result['cleanupOnly']) is not bool)
                    or (cleanup_only and (type(result.get('temporaryCopiesRemaining')) is not int
                                          or result['temporaryCopiesRemaining'] != 0))
                    or result.get(identity_key) != receipt.get("transactionId")):
                raise ValueError("Recovery receipt does not confirm the completed transaction outcome")
            outcome = ('未完成的导入已撤销' if status == 'additions-rolled-back' else
                       '上次导入已完成' if status == 'additions-committed' else
                       '未完成的删除已撤销，图包已恢复' if status == 'removal-rolled-back' else
                       '上次删除和清理已完成' if status == 'removal-committed' else
                       '上次更新产生的临时文件已清理完' if cleanup_only else
                       '已恢复操作前的图包')
            self._recovery_notice = outcome + '，正在核对当前状态。'
            self._recovery_verified_notice = outcome + '，状态已确认。'
            self._receipt_needs_notification = True
        else:
            self._recovery_notice = self._report_problem(dict(receipt, reason=receipt.get("reason", "recovery-helper-result-unavailable")),
                                                        "recovery-receipt", recovery_required=True, deferred=True)
        self.changed.emit()

    def _report_problem(self, error, action, *, recovery_required=False, deferred=False):
        if package_cancelled(error):
            return ("恢复操作已取消；上次未完成的操作仍需处理，可稍后点击“恢复记录”继续。"
                    if recovery_required else "操作已取消。")
        problem = package_problem(error, action, recovery_required=recovery_required)
        if deferred:
            # Startup receipts precede composition's ProblemsService wiring.
            QTimer.singleShot(0, lambda: self.errorReported.emit(problem) if not self._closed else None)
        else:
            self.errorReported.emit(problem)
        return problem["summary"]

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
            self._message = "现在还不能处理恢复。请先结束本局，再退出并重新打开软件，按启动提示处理。"
            self.changed.emit()
            return
        def inspect(cancel):
            importer._check_cancelled(cancel.is_set)
            with importer._writer_locked_installation(self.sandbox, require_closed=False) as (root, _state):
                plan = recovery.inspect_locked(root, transaction_id)
                if action == "rollback" and plan["status"] in {
                        "rolled-back", "upgrade-cleanup-pending", "upgrade-cleanup-available"}:
                    raise importer.ImportRejected("recovery-operation-no-longer-valid")
                return action, plan
        self._start("inspect-recovery", inspect, "正在核对恢复范围…")

    @Slot()
    def requestRecovery(self):
        self._request_recovery("recover")

    @Slot(str)
    def requestRollback(self, transaction_id):
        self._request_recovery("rollback", transaction_id)

    @Slot(str)
    def requestCleanup(self, transaction_id):
        self._request_recovery("recover", transaction_id)

    def setLeaseProvider(self, provider):
        """Inject the session coordinator before exposing this service to QML."""
        if self._busy or self._pending is not None or self._lease_id is not None:
            raise RuntimeError("Cannot replace an active resource lease provider")
        self._lease_provider = provider

    def _pending_recovery(self):
        # A malformed, linked, or unreadable pointer still forbids consumers.
        # This bounded metadata probe does not inspect resource contents.
        for name in ("pending-upgrade.json", "pending-base-repair.json",
                     "pending-additions.json", "pending-removal.json"):
            try:
                (self.sandbox / "work/map-packages" / name).lstat()
            except FileNotFoundError:
                continue
            except OSError:
                return True
            return True
        return False

    def _release_lease(self):
        if not self._closed:
            self._recovery = self._recovery or self._pending_recovery()
            if self._recovery or self._recovery_handoff or self._import_handoff:
                # Every error/cancel/history exit shares this rule. Recovery
                # reuses this lease; only a verified scan or safe shutdown can
                # reopen Session's runtime/maintenance/initialization commands.
                return
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
                self._message = "本局或另一个操作还在进行。请先结束本局，或等待操作完成后再管理图包。"
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
        job = _Work(self._token, guarded, self._pending_recovery)
        self._jobs[self._token] = job
        job.signals.done.connect(self._completed, Qt.ConnectionType.QueuedConnection)
        self.changed.emit()
        self.pool.start(job)

    def _snapshot(self, cancel):
        importer._pending_upgrade(self.sandbox)
        rows = manager.managed_author_packages_snapshot(self.sandbox, cancelled=cancel.is_set)
        from bundled_author_resources import bundled_summary
        bundled = bundled_summary(bundled_author_root())
        from season_map_resources import native_builtin_summaries
        native = native_builtin_summaries(runtime_paths(self.sandbox).resource_root, cancelled=cancel.is_set)
        return rows + ([bundled] if bundled else []) + native

    @Slot()
    def refresh(self):
        if self._available():
            self._start("refresh", self._snapshot, "正在核对本地资源…")

    @Slot(str)
    @Slot(str, str)
    def importArchive(self, file_url, season="s0"):
        if not self._available() or self._recovery:
            return
        if season not in {"s0", "s1"}:
            self._message = "这个赛季尚未支持作者图包导入"
            self.changed.emit()
            return
        url = QUrl(file_url)
        path = Path(url.toLocalFile()) if url.isLocalFile() else Path(file_url)
        if (url.scheme() and not url.isLocalFile()) or not str(file_url).strip():
            self._message = self._report_problem("non-local-archive", "inspect")
            self.changed.emit()
            return
        self._start("inspect", lambda cancel: (str(path), importer.inspect_package(path, self.sandbox, season=season)),
                    "正在检查图包文件…")

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
            self._message = self._report_problem(error, "online-binding")
            self.changed.emit()
            return False
        def inspect(cancel):
            importer._no_links(path)
            if path.stat().st_size != expected["bytes"]:
                raise importer.ImportRejected("online-archive-size-mismatch")
            result = importer.inspect_package(path, self.sandbox, season="s0")
            if (result.get("packageId") != expected["packageId"]
                    or result.get("archiveSha256") != expected["sha256"]
                    or path.stat().st_size != expected["bytes"]):
                raise importer.ImportRejected("online-archive-binding-mismatch")
            return str(path), result
        self._start("inspect", inspect, "正在确认下载的图包与所选项目一致…")
        return self._busy

    def _install(self, path, result):
        season = result.get("season", "s0")
        live = importer.supports_live_author_import(result)
        if not live and self._import_launcher is not None:
            launcher = self._import_launcher
            def launch(_cancel):
                with importer._writer_locked_installation(self.sandbox, require_closed=False) as (root, _state):
                    current = importer.inspect_package(path, root, season=season)
                    if _confirmation_fingerprint(current) != _confirmation_fingerprint(result):
                        raise importer.ImportRejected("resource-plan-changed-reinspect-required")
                    request_id = launcher(path, dict(result))
                if not isinstance(request_id, str) or not request_id:
                    raise importer.ImportRejected("import-helper-launch-not-confirmed")
                return request_id
            self._start("launch-import", launch, "正在准备导入，随后会关闭软件…", cancellable=False)
            return
        def install(cancel):
            if live:
                # The session lease remains held through commit. Revalidate
                # consent under the same OS writer lock as the shared live
                # transaction, including the previous receipt fingerprint.
                with importer._writer_locked_installation(self.sandbox, require_closed=False) as (root, state):
                    current = importer.inspect_package(path, root, season=season)
                    if _confirmation_fingerprint(current) != _confirmation_fingerprint(result):
                        raise importer.ImportRejected("resource-plan-changed-reinspect-required")
                    return importer._install_staged(path, root, state, season=season, live=True,
                        expected_archive_sha256=result["archiveSha256"], cancelled=cancel.is_set)
            # Hold the same writer lock between confirmation recheck and the
            # existing staging/upgrade transaction; never expand an old consent.
            with importer._locked_installation(self.sandbox, True) as (root, state):
                current = importer.inspect_package(path, root, season=season)
                if _confirmation_fingerprint(current) != _confirmation_fingerprint(result):
                    raise importer.ImportRejected("resource-plan-changed-reinspect-required")
                return importer._install_staged(path, root, state, season=season,
                    expected_archive_sha256=result["archiveSha256"])
        self._start("install", install, "正在安装图包，请等待完成…",
                    cancellable=live)

    @Slot(bool)
    def confirmOperation(self, accepted):
        if self._closed or self._busy or self._pending is None:
            return
        pending, kind = self._pending, self._kind
        self._pending, self._kind = None, ""
        if not accepted:
            self._release_lease()
            self._message = "已取消，这次操作没有更改图包。"
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
                self._start("launch-recovery", launch, "正在准备处理上次操作，随后会关闭软件…", cancellable=False)
                return
            if not self._recovery_allowed():
                self._message = "软件又开始使用地图文件了，这次处理已停止。请结束本局，重新核对后再试。"
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
            self._start("recover", execute, "正在处理上次未完成的操作，请稍候…", cancellable=False)
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
            self._message = "正在取消，请等待文件处理结束…"
            self.changed.emit()

    @Slot(int, object, object)
    def _completed(self, token, result, error):
        if self._jobs.pop(token, None) is None:
            return
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
            code = error_fields(error)["code"]
            self._recovery = self._recovery or code in {
                "upgrade-recovery-required", "pending-upgrade", "base-recovery-required",
                "upgrade-cleanup-incomplete", "base-cleanup-incomplete",
                "additions-recovery-required", "removal-recovery-required"} or recovery
            if self._recovery:
                self._rows = [dict(row, canRemove=False, status="recovery-required") for row in self._rows]
                self._message = self._report_problem(error, kind, recovery_required=True)
            elif package_cancelled(error):
                self._message = "操作已取消。"
            else:
                # Do not claim rollback succeeded: recovery conflicts retain backups.
                self._message = self._report_problem(error, kind)
            self._kind = ""
            self._release_lease()
            self.changed.emit()
            if kind in {"install", "refresh-after-write"}:
                # A failed transaction may have changed files before rollback.
                # Invalidate caches even then; pending journals keep new readers
                # behind assert_resources_ready until explicit recovery.
                # Closed-process recovery has no concurrent runtime readers;
                # the live package manager owns deletion invalidation.
                self.resourcesChanged.emit()
            return
        if kind in ("refresh", "inspect", "inspect-remove", "refresh-after-write", "recovery-history", "inspect-recovery") and self._cancel.is_set():
            self._message, self._kind = "操作已取消。", ""
            if kind == "refresh-after-write":
                self._import_completion_message = ""
                self._message = "已停止检查。刚才完成的图包操作仍然保留，可点击“核对状态”继续检查。"
            self._release_lease()
            self.changed.emit()
            return
        if kind == "recovery-history":
            self._recovery_rows, self._kind = result, ""
            self._recovery = self._recovery or any(row["pending"] for row in result)
            self._message = "已找到上次操作的处理记录。" if result else "没有需要恢复或清理的图包操作。"
        elif kind == "launch-recovery":
            self._kind, self._recovery_handoff = "", True
            self._message = "准备完成，正在关闭软件以处理上次操作。完成后请重新打开软件查看结果。"
            self.changed.emit()
            # Keep the lease until shutdown. Never emit resourcesChanged or
            # report success for a helper which has only started waiting.
            self.recoveryCloseRequested.emit()
            return
        elif kind == "inspect-recovery":
            action, plan = result
            self._pending, self._kind = result, "recovery"
            additions = plan['status'] == 'additions-pending'
            removal = plan['status'] == 'removal-pending'
            cleanup = (plan.get('cleanupOnly') is True
                       or plan['status'] in {'upgrade-cleanup-pending', 'upgrade-cleanup-available'})
            self._message = "请确认如何处理上次未完成的操作。"
            self.changed.emit()
            explanation = ("只清理上次操作留下的临时文件，不会把当前图包退回旧版本。"
                           if cleanup else "尚未完成的导入会撤销；已经完成的导入会保留，并检查是否还有需要清理的文件。"
                           if additions else "尚未完成的删除会撤销并恢复图包；已经完成的删除只继续清理，不会重新放回已删除的图包。"
                           if removal else "尚未完成的更新会恢复到操作前；已经完成的更新只清理临时文件，不会退回旧版本。")
            scope = f"涉及 {plan['fileCount']} 个文件。" if plan["fileCount"] else "将检查上次操作的剩余记录。"
            self.confirmationRequested.emit("清理临时文件" if cleanup else "处理上次导入" if additions else "处理上次删除" if removal else "处理上次更新",
                package_title(plan["packageId"]) + "\n" + scope + "\n\n" + explanation
                + "\n无法确认来源或被改动的文件会保留；若因此无法完成，会告诉你。\n\n点击取消可返回，不会开始这次处理。"
                + ("\n确认后软件会正常退出，再进行处理。完成后请重新打开软件查看结果。" if self._recovery_launcher is not None else ""))
            return
        elif kind == "launch-import":
            self._kind, self._import_handoff = "", True
            self._message = "准备完成，正在关闭软件以导入图包。完成后请重新打开软件查看结果。"
            self.changed.emit()
            self.importCloseRequested.emit()
            return
        elif kind == "refresh":
            self._rows, self._recovery, self._kind = result, False, ""
            if self._import_receipt_notification:
                self._import_receipt_notification = False
                self._import_notice = ("这个图包已经安装，无需重复导入，状态已确认。" if self._import_duplicate else
                                       "上次图包导入已完成，状态已确认。")
                self.resourcesChanged.emit()
                if self._helper_popup_request:
                    title = next((str(row.get("title") or row.get("name") or package_title(self._import_package_id))
                                  for row in result if row.get("packageId") == self._import_package_id),
                                 package_title(self._import_package_id))
                    self.importCompleted.emit(title, self._import_notice)
            self._message = "图包状态已核对。" if result else "还没有安装图包，可以导入本地 ZIP 图包。"
            if self._receipt_needs_notification:
                self._receipt_needs_notification = False
                self._recovery_notice = self._recovery_verified_notice
                self.resourcesChanged.emit()
        elif kind == "inspect":
            path, inspection = result
            self._import_package_id = str(inspection.get("packageId", ""))
            if (inspection.get("replaceFiles") or (self._import_launcher is not None
                    and not importer.supports_live_author_import(inspection))):
                self._kind, self._pending = "overwrite", (path, inspection)
                self._message = "请确认是否更新这个图包。"
                self.changed.emit()
                self.confirmationRequested.emit("更新图包", ("已识别到受支持的旧版图包，无需手动寻找或删除文件。\n\n" if inspection.get("legacyReceiptSha256") else "") + "将更新“%s”中的 %s 个文件，并新增 %s 个文件。\n\n只替换能确认属于这个图包、且没有被改动的文件。处理失败时会恢复尚未完成的更改；成功后会清理临时文件。\n点击取消可返回，不会开始更新。" % (
                    package_title(inspection["packageId"]), len(inspection["replaceFiles"]), len(inspection["missingFiles"]))
                    + ("\n\n确认后软件会正常退出再导入，完成后请重新打开查看结果。" if self._import_launcher is not None and not importer.supports_live_author_import(inspection) else ""))
                return
            self._install(path, inspection)
            return
        elif kind == "inspect-remove":
            self._kind, self._pending = "remove", result
            self._message = "等待确认备份并移除。"
            self.changed.emit()
            self.confirmationRequested.emit("备份并移除图包", "%s\n将保留备份并移除 %s 个地图图片文件（%s 字节），其中 %s 个有改动、%s 个已缺失。保留 %s 个软件必需文件，以及来源不明的文件和自定义路线。\n\n此操作会保留备份，但不能在软件中一键恢复。" % (
                result["title"], result["fileCount"], result["bytes"], len(result["modifiedFiles"]),
                len(result["missingFiles"]), result["preservedCoreFiles"]))
            return
        elif kind in ("install", "remove", "recover"):
            # A successful return is the commit result even if Cancel arrived
            # just after the helper's commit point. Never label that cancelled.
            self._kind = ""
            duplicate = kind == "install" and _duplicate_import(result)
            # Keep the final feedback pending until the actual-state scan succeeds.
            self._import_completion_message = (("这个图包已经安装，无需重复导入。" if duplicate else
                "图包安装完成，可以使用了。") if kind == "install" else "")
            self._message = ("图包已移除，备份已保留。" if kind == "remove" else
                             "这个图包已经安装，正在核对当前状态…" if duplicate else
                             "图包文件已安装，正在核对是否可用…")
            self.resourcesChanged.emit()
            if kind == "recover":
                self._recovery_rows = []
                self._message = ("新增导入记录已处理，正在核对状态…"
                                 if result.get('status') in {'additions-rolled-back', 'additions-committed'}
                                 else "图包删除记录已处理，正在核对状态…"
                                 if result.get('status') in {'removal-rolled-back', 'removal-committed'}
                                 else "上次更新产生的临时文件已清理，正在核对状态…"
                                 if result.get('status') == 'upgrade-committed' or result.get('cleanupOnly') is True
                                 else "已恢复操作前的图包，正在核对状态…")
            message = self._message
            self._start("refresh-after-write", self._snapshot, message)
            return
        elif kind == "refresh-after-write":
            self._rows, self._recovery, self._kind = result, False, ""
            if self._import_completion_message:
                self._message = self._import_completion_message
                title = next((str(row.get("title") or row.get("name") or package_title(self._import_package_id))
                              for row in result if row.get("packageId") == self._import_package_id),
                             package_title(self._import_package_id))
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
