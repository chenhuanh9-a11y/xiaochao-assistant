"""Qt notice/help adapter; published manifest validation remains UpdateService's job."""
from dataclasses import asdict
from datetime import datetime, timezone
import json
from pathlib import Path
import platform
import sys
from urllib.parse import urlsplit
from PySide6.QtCore import QObject, Property, QTimer, QUrl, Signal, Slot
from PySide6.QtGui import QDesktopServices
from backend import ROOT, Work
from client_paths import WORK_ROOT
from PySide6.QtCore import QThreadPool, Qt
from update_service import UpdateService, DOWNLOAD_HOSTS


class ClientInfoService(QObject):
    changed = Signal()

    def __init__(self, sandbox, *, fetch=None, open_url=None):
        super().__init__()
        self.sandbox = Path(sandbox).resolve()
        if not self.sandbox.is_relative_to(WORK_ROOT) or not (self.sandbox / ".qt-lab-owner").is_file():
            raise ValueError("notice state requires the isolated client sandbox")
        version = json.loads((ROOT / "version.json").read_text(encoding="utf-8"))
        self._version = version.get("displayVersion", "unknown")
        self._service = UpdateService(self._version, self.sandbox / "qt-update-state.json",
                                      **({"fetch": fetch} if fetch is not None else {}))
        self._message = "尚未检查更新"
        self._writing = False
        self._action_error = False
        self._operation = ""
        self._history_message = "检查更新后可查看往期公告"
        self._diagnostics = {}
        self._open_url = open_url or QDesktopServices.openUrl
        self._providers = {}
        self._pool = QThreadPool(self)
        self._pool.setMaxThreadCount(1)
        self._jobs = set()
        self._closed = False
        self._timer = QTimer(self)
        self._timer.setInterval(100)
        self._timer.timeout.connect(self._poll)

    version = Property(str, lambda s: s._version + " · Qt 迁移测试", constant=True)
    message = Property(str, lambda s: s._message, notify=changed)
    checking = Property(bool, lambda s: s._service.phase == "checking" or s._writing, notify=changed)
    updatePhase = Property(str, lambda s: s._service.phase, notify=changed)
    operation = Property(str, lambda s: s._operation, notify=changed)
    hasError = Property(bool, lambda s: s._action_error, notify=changed)
    hasUnread = Property(bool, lambda s: s._service.has_unread, notify=changed)
    skippedVersion = Property(str, lambda s: s._service.skipped_version, notify=changed)
    historyMessage = Property(str, lambda s: s._history_message, notify=changed)
    diagnosticsReceipt = Property("QVariantMap", lambda s: dict(s._diagnostics), notify=changed)
    release = Property("QVariantMap", lambda s: s._release_row(s._service.latest) if s._service.latest else {}, notify=changed)
    history = Property("QVariantList", lambda s: [s._release_row(r) for r in s._service.history
        if not s._service.latest or r.version != s._service.latest.version][:10], notify=changed)

    @staticmethod
    def _release_row(release):
        return dict(asdict(release), published_date=release.published_date)
    facts = Property("QVariantMap", lambda s: dict(version=s._version, entrypoint=str(Path(__file__).with_name("client.py")),
                    interpreter=sys.executable, platform=platform.platform(),
                    liveInput=bool(s._providers.get("input") and s._providers["input"].enabled)), notify=changed)
    supportCodes = Property("QVariantMap", lambda s: {
        key: QUrl.fromLocalFile(str(ROOT / "assets/support" / file)).toString()
        for key, file in (("wechat", "wechat.png"), ("alipay", "alipay.png"))}, constant=True)

    def setProviders(self, **providers):
        self._providers = providers
        for provider in providers.values():
            if hasattr(provider, "changed"):
                provider.changed.connect(self.changed)

    @Slot()
    def checkUpdates(self):
        if self._closed or self.checking:
            return
        if self._service.check(manual=True):
            self._action_error = False
            self._operation = "正在读取最新版本与更新历史…"
            self._message = self._service.message
            self._timer.start()
            self.changed.emit()

    def _poll(self):
        if self._closed:
            return
        event = self._service.poll()
        if event is not None:
            self._message = self._service.message
            self._action_error = "error" in event
            self._operation = ""
            if "error" not in event:
                self._history_message = ("更新历史暂时无法读取，可重新检查更新" if event.get("history") is None
                                         else "暂无更早的更新公告" if not self.history else "")
            self._timer.stop()
            self.changed.emit()

    def _write(self, function, message, *, operation="正在保存…", complete=None):
        if self.checking or self._closed:
            return False
        self._writing = True
        self._action_error = False
        self._operation = operation
        self.changed.emit()
        job = Work(function)
        self._jobs.add(job)
        def done(value, error):
            self._jobs.discard(job)
            if self._closed:
                return
            self._writing = False
            self._operation = ""
            self._action_error = bool(error)
            self._message = f"操作失败：{error}；请重试" if error else message
            if complete:
                complete(value, error)
            self.changed.emit()
        job.signals.done.connect(done, Qt.QueuedConnection)
        self._pool.start(job)
        return True

    def _notice_action(self, method, key, message):
        if self._service.latest is None:
            return
        version = self._service.latest.version
        def save():
            previous = dict(self._service._stored)
            try:
                method()
                persisted = json.loads(self._service.state_path.read_text(encoding="utf-8"))
                if persisted.get(key) != version:
                    raise OSError("提醒偏好未写入")
            except Exception:
                self._service._stored = previous
                raise
        self._write(save, message)

    @Slot()
    def markRead(self):
        self._notice_action(self._service.mark_notes_seen, "seenNotesVersion", "已标记为已读")

    @Slot()
    def skipVersion(self):
        self._notice_action(self._service.skip_latest, "skippedVersion", "已忽略此版本提醒")

    @Slot(str)
    def openDownloadPage(self, provider):
        if self._closed or self.checking:
            return
        release = self._service.latest
        url = release.pages.get(provider) if release else None
        try:
            parts = urlsplit(url or "")
            allowed = (provider in DOWNLOAD_HOSTS and parts.scheme == "https"
                       and parts.hostname == DOWNLOAD_HOSTS[provider] and not parts.username
                       and not parts.password and parts.port in (None, 443)
                       and not any(ord(c) < 32 for c in (url or "")))
            if not allowed:
                raise ValueError("当前版本没有可用的官方下载页")
            if not self._open_url(QUrl(url)):
                raise OSError("无法打开浏览器")
            self._message, self._action_error = "已打开官方下载页，请在浏览器中继续", False
        except Exception as error:
            self._message, self._action_error = str(error) + "；请重试", True
        self.changed.emit()

    @Slot()
    def exportDiagnostics(self):
        destination = self.sandbox / "diagnostics" / ("qt-client-" + datetime.now().strftime("%Y%m%d-%H%M%S-%f") + ".json")
        self._export_diagnostics(destination)

    @Slot(str)
    def exportDiagnosticsTo(self, value):
        url = QUrl(value)
        if not url.isLocalFile() or Path(url.toLocalFile()).suffix.lower() != ".json":
            self._message, self._action_error = "请选择本地 JSON 文件保存诊断", True
            self.changed.emit()
            return
        self._export_diagnostics(Path(url.toLocalFile()))

    def _export_diagnostics(self, destination):
        if self._closed or self.checking:
            return
        destination = Path(destination).resolve()
        report = dict(schema="xingtu-qt-client-diagnostics-v1", exportedAt=datetime.now(timezone.utc).isoformat(),
                      environment=self.facts)
        session = self._providers.get("session")
        if session is not None:
            state = session.snapshot
            report["session"] = {key: state.get(key) for key in (
                "mode", "profileId", "engineConnected", "busy", "generation", "availableCommands")}
        resources = self._providers.get("resources")
        if resources is not None:
            report["resourceStates"] = [{key: row.get(key) for key in ("packageId", "status")}
                                        for row in resources.packageRows]
        self._diagnostics = {}
        def save():
            destination.parent.mkdir(parents=True, exist_ok=True)
            # A selected existing file is never silently replaced.
            with destination.open("x", encoding="utf-8") as stream:
                json.dump(report, stream, ensure_ascii=False, indent=2)
            return dict(path=str(destination), bytes=destination.stat().st_size, exportedAt=report["exportedAt"])
        def complete(receipt, error):
            self._diagnostics = dict(receipt or {}, success=not bool(error), error=error or "")
        self._write(save, f"已导出到 {destination}", operation="正在导出本次诊断…", complete=complete)

    def shutdown(self):
        self._closed = True
        self._timer.stop()
        self._pool.waitForDone()
