"""Qt notice/help adapter; published manifest validation remains UpdateService's job."""
from dataclasses import asdict
from datetime import datetime, timezone
import json
from pathlib import Path
import platform
import sys
from PySide6.QtCore import QObject, Property, QTimer, QUrl, Signal, Slot
from PySide6.QtGui import QDesktopServices
from backend import ROOT, Work
from client_paths import WORK_ROOT
from PySide6.QtCore import QThreadPool, Qt
from update_service import UpdateService


class ClientInfoService(QObject):
    changed = Signal()

    def __init__(self, sandbox, *, fetch=None):
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
    release = Property("QVariantMap", lambda s: asdict(s._service.latest) if s._service.latest else {}, notify=changed)
    history = Property("QVariantList", lambda s: [asdict(r) for r in s._service.history], notify=changed)
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
            self._message = self._service.message
            self._timer.start()
            self.changed.emit()

    def _poll(self):
        if self._service.poll() is not None:
            self._message = self._service.message
            self._timer.stop()
            self.changed.emit()

    def _write(self, function, message):
        if self._writing or self._closed or self._service.phase == "checking":
            return
        self._writing = True
        self.changed.emit()
        job = Work(function)
        self._jobs.add(job)
        def done(value, error):
            self._jobs.discard(job)
            if self._closed:
                return
            self._writing = False
            self._message = f"操作失败：{error}" if error else message
            self.changed.emit()
        job.signals.done.connect(done, Qt.QueuedConnection)
        self._pool.start(job)

    @Slot()
    def markRead(self):
        self._write(self._service.mark_notes_seen, "已标记为已读")

    @Slot()
    def skipVersion(self):
        self._write(self._service.skip_latest, "已忽略此版本提醒")

    @Slot(str)
    def openDownloadPage(self, provider):
        release = self._service.latest
        url = release.pages.get(provider) if release else None
        if url:
            QDesktopServices.openUrl(QUrl(url))

    @Slot()
    def exportDiagnostics(self):
        # User-requested, deliberately excludes author pixels, game logs and full config.
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
        destination = self.sandbox / "diagnostics" / ("qt-client-" + datetime.now().strftime("%Y%m%d-%H%M%S") + ".json")
        def save():
            destination.parent.mkdir(parents=True, exist_ok=True)
            with destination.open("x", encoding="utf-8") as stream:
                json.dump(report, stream, ensure_ascii=False, indent=2)
        self._write(save, f"已导出到 {destination}")

    def shutdown(self):
        self._closed = True
        self._timer.stop()
        self._pool.waitForDone()
