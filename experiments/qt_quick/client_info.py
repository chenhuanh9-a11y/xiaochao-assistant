"""Qt notice/help adapter; published manifest validation remains UpdateService's job."""
import base64
from copy import deepcopy
from io import BytesIO
from dataclasses import asdict
from datetime import datetime, timezone
import json
from pathlib import Path
import platform
import re
import sys
from urllib.parse import urlsplit
from PySide6.QtCore import QObject, Property, QTimer, QUrl, Signal, Slot
from PySide6.QtGui import QDesktopServices
from backend import ROOT, Work
from map_resource_context import load_browse_context
from client_paths import PRODUCTION, WORK_ROOT, runtime_paths
from PySide6.QtCore import QThreadPool, Qt
from update_service import UpdateService, DOWNLOAD_HOSTS
from diagnostic_export import export_safe_diagnostic_bundle, diagnostic_package_metadata


class ClientInfoService(QObject):
    changed = Signal()
    startupAnnouncement = Signal("QVariantMap")
    homePreviewRequested = Signal(str, str, str)

    def __init__(self, sandbox, *, fetch=None, open_url=None):
        super().__init__()
        self.paths = runtime_paths(sandbox)
        self.sandbox = self.paths.installation_root
        version = json.loads((ROOT / "version.json").read_text(encoding="utf-8"))
        self._version = version.get("displayVersion", "unknown")
        self._app_version = version.get("version", "unknown")
        self._build_fingerprint = "UNKNOWN"
        self._package_metadata = {}
        profile_path = self.paths.resource_root / "installation-profile.json"
        try:
            if profile_path.stat().st_size <= 1024 * 1024:
                profile = json.loads(profile_path.read_text(encoding="utf-8"))
                commit = profile.get("sourceCommit", "")
                if isinstance(commit, str) and re.fullmatch(r"[0-9a-fA-F]{40}", commit):
                    self._build_fingerprint = commit[:8].upper()
                    self._package_metadata = dict(qtSourceCommit=commit, qtFingerprintScheme="source-commit-prefix",
                        qtInstallerInstance=(profile.get("installerInstance") or {}).get("id"),
                        resourceVersion=profile.get("resourceVersion"))
        except (OSError, ValueError, AttributeError, TypeError):
            pass
        self._package_metadata = dict(diagnostic_package_metadata(self.paths.installation_root,
            app_version=self._app_version, build_fingerprint=self._build_fingerprint), **self._package_metadata)
        self._include_failure_evidence = False
        self._service = UpdateService(self._version, self.paths.update_state_path,
                                      **({"fetch": fetch} if fetch is not None else {}))
        self._message = "尚未检查更新"
        self._writing = False
        self._action_error = False
        self._operation = ""
        self._history_message = "检查更新后可查看往期公告"
        self._diagnostics = {}
        self._help_topic_template = json.loads(Path(__file__).with_name("help-topics.json").read_text("utf-8"))
        self._home_previews = []
        self._home_context = None
        self._home_preview_generation = 0
        self._home_preview_job = None
        self._home_preview_pending = False
        self._open_url = open_url or QDesktopServices.openUrl
        self._providers = {}
        self._pool = QThreadPool(self)
        self._pool.setMaxThreadCount(1)
        self._jobs = set()
        self._closed = False
        self._timer = QTimer(self)
        self._timer.setInterval(100)
        self._timer.timeout.connect(self._poll)
        self._startup_requested = False
        self._startup_checked = False
        self._startup_timer = QTimer(self)
        self._startup_timer.setSingleShot(True)
        self._startup_timer.setInterval(2500)
        self._startup_timer.timeout.connect(self._startup_check)

    version = Property(str, lambda s: s._version + ("" if PRODUCTION else " · Qt 迁移测试"), constant=True)
    message = Property(str, lambda s: s._message, notify=changed)
    checking = Property(bool, lambda s: s._service.phase == "checking" or s._writing, notify=changed)
    updatePhase = Property(str, lambda s: s._service.phase, notify=changed)
    operation = Property(str, lambda s: s._operation, notify=changed)
    hasError = Property(bool, lambda s: s._action_error, notify=changed)
    hasUnread = Property(bool, lambda s: s._service.has_unread, notify=changed)
    skippedVersion = Property(str, lambda s: s._service.skipped_version, notify=changed)
    historyMessage = Property(str, lambda s: s._history_message, notify=changed)
    includeFailureEvidence = Property(bool, lambda s: s._include_failure_evidence, notify=changed)
    diagnosticsReceipt = Property("QVariantMap", lambda s: dict(s._diagnostics), notify=changed)
    release = Property("QVariantMap", lambda s: s._release_row(s._service.latest) if s._service.latest else {}, notify=changed)
    history = Property("QVariantList", lambda s: [s._release_row(r) for r in s._service.history
        if not s._service.latest or r.version != s._service.latest.version][:10], notify=changed)

    @staticmethod
    def _release_row(release):
        return dict(asdict(release), published_date=release.published_date)
    facts = Property("QVariantMap", lambda s: dict(version=s._version, entrypoint=str(Path(__file__).with_name("client.py")),
                    interpreter=sys.executable, platform=platform.platform(),
                    liveInput=bool(s._providers.get("input") and s._providers["input"].enabled),
                    displayEnvironment=s._display_environment_label(),
                    runtimeStatus=getattr(s._providers.get("coordinator"), "runtimeStatus", "等待游戏窗口信息")), notify=changed)

    def _display_environment_label(self):
        coordinator = self._providers.get("coordinator")
        bound = str(getattr(coordinator, "boundWindow", ""))
        row = next((row for row in getattr(coordinator, "gameWindows", [])
                    if str(row.get("hwnd")) == bound), None)
        if row is None:
            return "等待游戏窗口信息"
        return f"{row.get('width', '?')} × {row.get('height', '?')} · DPI {row.get('dpi', '?')}"
    supportCodes = Property("QVariantMap", lambda s: {
        key: QUrl.fromLocalFile(str(ROOT / "assets/support" / file)).toString()
        for key, file in (("wechat", "wechat.png"), ("alipay", "alipay.png"))}, constant=True)

    @property
    def _home_backend(self):
        coordinator = self._providers.get("coordinator")
        return getattr(coordinator, "backend", None)

    homePreviews = Property("QVariantList", lambda s: s._home_previews, notify=changed)
    helpImages = Property("QVariantMap", lambda s: {
        topic: QUrl.fromLocalFile(str(s.paths.resource_root / "assets/help" / name)).toString()
        for topic, name in (("quick-start", "home.png"), ("shortcuts-visibility", "settings.png"),
                            ("resources-opacity", "resources.png"), ("check-feedback", "help.png"))}, constant=True)

    def _help_rows(self):
        rows = deepcopy(self._help_topic_template)
        coordinator = self._providers.get("coordinator")
        settings = getattr(coordinator, "settings", None)
        values = settings.persistedValues if settings is not None else {}
        order = ("toggle-panel", "candidate-prev", "candidate-next", "confirm-round", "cycle-floor",
                 "end-round", "toggle-sheet-visibility", "start-round", "toggle-round")
        actions = dict(zip(order, values.get("actionHotkeys", [])))
        actions["map"] = values.get("hotkey", "")
        for row in rows:
            for step in row["steps"]:
                step["bindings"] = "  /  ".join(str(actions.get(key) or "未设置") for key in step["targets"])
        return rows
    helpTopics = Property("QVariantList", _help_rows, notify=changed)

    @Slot(str)
    def helpNavigate(self, page):
        coordinator = self._providers.get("coordinator")
        if coordinator is not None and page in {"preferences", "maps"}:
            coordinator.navigate("settings" if page == "preferences" else "gallery")

    def setHomeContext(self, mode, profile_id, *, force=False):
        """Receive the actual applied runtime context, never the browsing selection."""
        if self._closed or mode not in ("nightmare", "difficult"):
            return False
        context = (str(mode), str(profile_id or ""))
        if context == self._home_context and not force:
            return False
        self._home_context = context
        self._home_preview_generation += 1
        self._home_previews = []
        self.changed.emit()
        self._refresh_home_previews()
        return True

    @staticmethod
    def _home_rows(root, mode, profile_id):
        context = load_browse_context(root, mode)
        if context.error or context.pack is None:
            return []
        profile = next((p for p in context.profiles if p.profile_id == profile_id), None)
        rows = []
        if profile is not None:
            for item in profile.maps:
                floors = tuple(dict.fromkeys((str(context.pack.default_entry_floor), *item.floors)))
                chosen = next(((variant, floor, Path(variant.layers[floor]))
                    for floor in floors for variant in item.variants
                    if floor in variant.layers and context.path_stamps.get(Path(variant.layers[floor]))), None)
                if chosen is None:
                    continue
                variant, floor, path = chosen
                stamp = context.path_stamps[path]
                url = QUrl.fromLocalFile(str(path)); url.setQuery(f"v={stamp[0]}-{stamp[1]}")
                rows.append(dict(key=f"{mode}/{profile_id}/{item.id}/{variant.id}/{floor}",
                    title=item.name, floor=floor, mode=mode, profileId=profile_id,
                    structure=False, imageUrl=url.toString()))
                if len(rows) == 3:
                    break
        if rows:
            return rows
        from map_resource_overview import structure_previews, load_structure_preview
        for asset in structure_previews(context.pack):
            image = load_structure_preview(asset, (400, 250))
            stream = BytesIO(); image.save(stream, format="PNG")
            rows.append(dict(key="", title=asset.name, floor=asset.floor, mode=mode,
                profileId=profile_id, structure=True,
                imageUrl="data:image/png;base64," + base64.b64encode(stream.getvalue()).decode("ascii")))
        return rows

    def _refresh_home_previews(self):
        if self._closed or self._home_context is None:
            return
        if self._home_preview_job is not None:
            self._home_preview_pending = True
            return
        context = self._home_context
        generation = self._home_preview_generation
        job = self._home_preview_job = Work(lambda: self._home_rows(self.paths.resource_root, *context))
        self._jobs.add(job)
        def done(value, error):
            self._jobs.discard(job)
            self._home_preview_job = None
            if not self._closed and generation == self._home_preview_generation and context == self._home_context:
                self._home_previews = value if not error else []
                self.changed.emit()
            pending, self._home_preview_pending = self._home_preview_pending, False
            if pending and not self._closed:
                self._refresh_home_previews()
        job.signals.done.connect(done, Qt.QueuedConnection)
        self._pool.start(job)

    @Slot(str)
    def openHomePreview(self, key):
        row = next((row for row in self._home_previews if row.get("key") == key), None)
        if not self._closed and row is not None and self._home_context is not None:
            self.homePreviewRequested.emit(*self._home_context, str(key))

    def setProviders(self, **providers):
        self._set_failure_sampling(False)
        self._providers = providers
        for provider in providers.values():
            if hasattr(provider, "changed"):
                provider.changed.connect(self.changed)
        runtime = getattr(providers.get('coordinator'), 'runtime', None)
        configure = getattr(runtime, 'configure_failure_sampling', None)
        problems = providers.get('problems')
        if callable(configure) and problems is not None:
            configure(event_path=str(getattr(problems, 'log_path', '') or ''),
                build_fingerprint=self._build_fingerprint,
                source_commit=self._package_metadata.get('qtSourceCommit') or '')
        self._set_failure_sampling(self._include_failure_evidence)

    def _set_failure_sampling(self, enabled):
        runtime = getattr(self._providers.get('coordinator'), 'runtime', None)
        setter = getattr(runtime, 'set_failure_sampling_enabled', None)
        if callable(setter):
            setter(bool(enabled))

    @Slot(bool, result=bool)
    def scheduleStartupCheck(self, enabled=True):
        """Owner explicitly disables this for probes; constructors never fetch."""
        if not enabled or self._closed or self._startup_requested:
            return False
        self._startup_requested = True
        self._startup_timer.start()
        return True

    @Slot()
    def _startup_check(self):
        if self._closed or self._startup_checked:
            return
        self._startup_checked = True
        self._check_updates(manual=False)

    @Slot()
    def checkUpdates(self):
        self._check_updates(manual=True)

    def _check_updates(self, *, manual):
        if self._closed or self.checking:
            return
        if self._service.check(manual=manual):
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
            if "error" not in event and not event.get("manual", False) and self._service.should_prompt():
                self._service.mark_prompted()
                self.startupAnnouncement.emit(self._release_row(self._service.latest))

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

    @Slot(bool)
    def setIncludeFailureEvidence(self, include):
        if not self._closed and not self.checking:
            self._include_failure_evidence = bool(include)
            self._set_failure_sampling(self._include_failure_evidence)
            self.changed.emit()

    @Slot()
    def exportDiagnostics(self):
        destination = self.paths.installation_root / "outputs" / "诊断日志" / ("qt-client-" + datetime.now().strftime("%Y%m%d-%H%M%S-%f") + ".zip")
        self._export_diagnostics(destination)

    @Slot(str)
    def exportDiagnosticsTo(self, value):
        url = QUrl(value)
        if not url.isLocalFile() or Path(url.toLocalFile()).suffix.lower() != ".zip":
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
        problems = self._providers.get("problems")
        if problems is not None:
            report["recentProblems"] = problems.recent
        resources = self._providers.get("resources")
        if resources is not None:
            report["resourceStates"] = [{key: row.get(key) for key in ("packageId", "status")}
                                        for row in resources.packageRows]
        input_provider = self._providers.get("input")
        input_state = dict(input_provider.snapshot) if input_provider is not None else {}
        # Snapshot existing state only: no window discovery, foreground query or capture.
        state = session.snapshot if session is not None else {}
        window_environment = dict(schema="identityv-window-environment-v1", candidateCount=0,
            candidates=[], collectionStatus="existing-bound-state-only",
            gameWindowBound=bool(input_state.get("hwnd") and str(input_state.get("hwnd")) != "0"),
            readPermission=input_state.get("readPermission", "unknown"))
        coordinator = self._providers.get("coordinator")
        if coordinator is not None:
            bound = str(coordinator.boundWindow)
            rows = [row for row in coordinator.gameWindows if str(row.get("hwnd")) == bound][:1]
            window_environment["candidates"] = [{key: row.get(key) for key in
                ("width", "height", "dpi", "clientBounds") if key in row} for row in rows]
            window_environment["candidateCount"] = len(rows)
        runtime_context = dict(collectionStatus="existing-state-only",
            gameWindowBound=window_environment["gameWindowBound"], mapOpen=bool(state.get("mapOpen")),
            gameForeground=state.get("gameForeground", "unknown"))
        failure_roots = tuple(self.paths.memory_root(mode) for mode in ("nightmare", "difficult")) if self._include_failure_evidence else ()
        package_metadata = dict(self._package_metadata)
        self._diagnostics = {}
        def save():
            result = export_safe_diagnostic_bundle(self.paths.installation_root,
                self.paths.work_root / "nightmare-live", destination=destination,
                app_version=self._app_version, build_fingerprint=self._build_fingerprint,
                package_metadata=package_metadata, window_environment=window_environment,
                resource_root=self.paths.resource_root,
                runtime_context=runtime_context, failure_roots=failure_roots, summary=report,
                process_integrity=dict(level="Unknown", rank=-1, collectionStatus="not-collected"))
            return dict(result, path=str(result["path"]), bytes=destination.stat().st_size,
                        exportedAt=report["exportedAt"])
        def complete(receipt, error):
            self._diagnostics = dict(receipt or {}, success=not bool(error), error=error or "")
        self._write(save, f"已导出到 {destination}", operation="正在导出本次诊断…", complete=complete)

    def shutdown(self):
        self._set_failure_sampling(False)
        self._startup_timer.stop()
        self._closed = True
        self._home_preview_generation += 1
        self._timer.stop()
        self._pool.waitForDone()
