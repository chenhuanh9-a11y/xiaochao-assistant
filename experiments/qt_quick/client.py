"""Opt-in Qt private client; the existing Tk and qt-lab launchers are untouched."""
from __future__ import annotations
import argparse
import json
import os
from pathlib import Path
import shutil
import sys
import traceback
from uuid import uuid4
from PySide6.QtCore import QTimer, QUrl, QThreadPool, Qt
from PySide6.QtGui import QFont, QFontDatabase, QGuiApplication, QIcon, QWindow
from PySide6.QtQml import QQmlApplicationEngine
from client_paths import APP_ROOT, DEFAULT_SANDBOX, FROZEN, PRODUCTION, RESOURCE_ROOT, WORK_ROOT, owned_sandbox, runtime_paths
from client_manual_map_service import ManualMapPickerService
from qt_startup_splash import set_startup_stage

if str(APP_ROOT) not in sys.path:
    sys.path.insert(0, str(APP_ROOT))

from season_context import selected_profile, context_key, season_label

HERE = Path(__file__).resolve().parent


def prepare_first_run(sandbox):
    """Stage a new test resource tree and publish it only when complete."""
    sandbox = Path(sandbox).resolve()
    if PRODUCTION:
        # An upgrade uses the installed tree in place and never seeds test data.
        return owned_sandbox(sandbox)
    if sandbox.exists():
        return owned_sandbox(sandbox)
    if not sandbox.is_relative_to(WORK_ROOT):
        raise ValueError("The Qt client can initialize only its own work directory")
    if not FROZEN:
        raise ValueError("Source client needs seed_sandbox.py before first launch")
    from seed_sandbox import seed
    staging = sandbox.with_name(sandbox.name + ".preparing-" + uuid4().hex)
    try:
        seed(Path(sys.executable).resolve().parent, staging)
        if sandbox.exists():
            raise ValueError("Runtime data appeared while preparing; reopen without overwriting it")
        os.replace(staging, sandbox)
        return owned_sandbox(sandbox)
    except Exception:
        # This exact fresh staging directory is ours; existing runtime data is
        # never deleted or replaced by first-run recovery.
        if staging.is_relative_to(WORK_ROOT) and staging.name.startswith(sandbox.name + ".preparing-"):
            shutil.rmtree(staging, ignore_errors=True)
        raise


class QtClient:
    def __init__(self, app, sandbox, *, probe_output=None, info_fetch=None, on_created=None,
                 g_cycle_evidence=False, g_cycle_full_input=False):
        if g_cycle_full_input and not g_cycle_evidence:
            raise ValueError("full solver evidence requires explicit test evidence")
        set_startup_stage("create-services")
        self._closed = False
        self._shutdown_complete = False
        if on_created is not None:
            # Retain even an object whose later service/QML construction fails.
            on_created(self)
        from backend import Backend
        from client_settings import SettingsService
        from client_resources import ResourceService
        from client_package_manager import PackageManagerService
        from client_session import SessionService
        from client_windows import WindowController
        from client_input import NativeShortcutBackend
        from client_capture import ClientCaptureProvider
        from client_providers import build_engine_factories
        from client_live_runtime import LiveRuntime
        from client_info import ClientInfoService
        from client_custom_gallery import CustomGalleryService
        from client_custom_display import CustomGalleryDisplay
        from client_downloads import OnlinePackagesService
        from client_route_plaza import RoutePlazaService
        from client_coordinator import ClientCoordinator, LiveImages
        from client_ui_feedback import UiFeedback
        from client_onboarding import OnboardingService
        from client_chest_auto_tap import ChestAutoTapService
        from client_preview import PreviewService
        from client_control_snap import ControlSnap
        from client_problems import ProblemsService
        from client_reference_presentation import ReferencePreviewProvider
        self.app, self.sandbox = app, owned_sandbox(sandbox)
        self.paths = runtime_paths(self.sandbox)
        # Startup housekeeping shared with the Tk client, before this session
        # opens its own logs: earlier logs beyond the diagnostic export window,
        # and download archives whose exact package is already installed.
        try:
            from diagnostic_export import DIAGNOSTIC_LOG_LIMIT, prune_diagnostic_logs
            from star_map_download import prune_installed_downloads
            prune_diagnostic_logs(self.paths.work_root / "nightmare-live", DIAGNOSTIC_LOG_LIMIT - 1)
            prune_installed_downloads(self.sandbox / "work/map-packages/downloads", self.sandbox)
        except Exception:
            pass  # Housekeeping never blocks startup; the files simply remain.
        from client_lifecycle_trace import create_lifecycle_trace
        from s1_candidate_purpose import packaged_native_candidate_purpose
        candidate_purpose = packaged_native_candidate_purpose()
        self.lifecycle_trace = create_lifecycle_trace(self.paths, candidate_purpose=candidate_purpose)
        from client_lifecycle import LifecycleJournal
        self.lifecycle = LifecycleJournal(self.paths.work_root)
        self.lifecycle.record_event(dict(stage="client-created"))
        self._closed = False
        from client_performance import PerformanceService
        self.performance = PerformanceService()
        from client_game_startup import GameStartupService
        self.game_startup = GameStartupService(self.paths.installation_root,
            production=bool(self.paths.production and not probe_output))
        self.settings = SettingsService(self.sandbox)
        self.custom_galleries = CustomGalleryService(self.sandbox)
        self.route_plaza = RoutePlazaService(self.sandbox, self.custom_galleries)
        self.custom_display = CustomGalleryDisplay(self.custom_galleries)
        self.custom_display.set_point_numbers(self.settings.persistedValues.get("routePointNumbers", False))
        self.settings.saved.connect(lambda: self.custom_display.set_point_numbers(
            self.settings.persistedValues.get("routePointNumbers", False)))
        from client_capture_exclusion import QtCaptureExclusion
        self.capture_exclusion = QtCaptureExclusion(parent=app,
            is_closing=lambda: self._closed or bool(getattr(getattr(self, "coordinator", None), "closing", False)))
        self.capture = ClientCaptureProvider(full_capture=self.capture_exclusion.full_capture)
        self.input = NativeShortcutBackend()
        self.providers = build_engine_factories(self.paths.resource_root)
        self.providers.set_custom_display(self.custom_display.layer, base_provider=self.custom_display.base_layer)
        self.runtime = LiveRuntime(engine_factories=self.providers, capture_provider=self.capture,
                                   allowed_read_root=WORK_ROOT, evidence_kind="live-client-capture",
                                    diagnostics=self.lifecycle_trace,
                                    s1_candidate_purpose=candidate_purpose)
        values = self.settings.persistedValues
        self.runtime.configure_settings(values)
        mode, profile, season = ClientCoordinator._settings_context(values)
        self._previous_author_profiles = {
            key: value for key, value in values.get("contextRouteProfiles", {}).items()
            if value and not str(value).startswith("custom:")}
        if not str(profile).startswith("custom:"):
            self._previous_author_profiles[context_key(mode, season)] = profile
        # Context initialization is a bounded state-only operation. Resource
        # preparation/recognition starts later on SessionService's worker.
        self.runtime.dispatch("set_context", {"season": season, "mode": mode, "profileId": profile})
        self.session = SessionService(self.runtime)
        self.resources = ResourceService(self.sandbox)
        self.resources.setLeaseProvider(self.session)
        self.package_manager = PackageManagerService(self.sandbox, self.resources)
        self.package_manager.setLeaseProvider(self.session)
        self.package_manager.resourcesChanged.connect(self.resources.resourcesChanged.emit)
        self.resources.resourcesChanged.connect(self.package_manager.requestRefresh)
        self.online_packages = OnlinePackagesService(self.sandbox, self.resources,
            index_url=None if probe_output else os.environ.get("STAR_MAP_PACKAGE_INDEX_URL"))
        self.backend = Backend(self.sandbox)
        self.route_choices = Backend(self.sandbox)
        self.resources.resourcesChanged.connect(self.route_choices.refresh)
        self.resources.resourcesChanged.connect(self._schedule_resource_preparation)
        self.resources.resourcesChanged.connect(self.custom_galleries.refresh)
        self.settings.changed.connect(lambda: self.route_choices.setContext(self.settings.values.get("gameSeason", "s0"), self.settings.values.get("gameMode", "nightmare")))
        self.route_choices.setContext(season, mode)
        self.windows = WindowController()
        self.windows.setBeforeShow(self.capture_exclusion.protectWindow)
        self.preview = PreviewService(self.backend, self.windows)
        self.backend.overlayRequested.connect(self.preview.show)
        self.images = LiveImages()
        self.coordinator = ClientCoordinator(backend=self.backend, settings=self.settings,
            resources=self.resources, session=self.session, windows=self.windows,
            native_input=self.input, capture=self.capture, runtime=self.runtime, images=self.images,
            **({"window_catalog": lambda: []} if probe_output else {}))
        self.manual_map_picker = ManualMapPickerService(
            settings=self.settings, windows=self.windows, input_backend=self.input,
            session=self.session, catalog_provider=self.runtime.manual_map_catalog,
            commit=self.coordinator.confirmManualMap,
            geometry_provider=self.coordinator.manualMapPickerGeometry, parent=self.coordinator)
        self.coordinator._manual_map_picker = self.manual_map_picker
        self.reference_preview = ReferencePreviewProvider(self.providers, parent=self.coordinator,
                                                         installation_root=self.sandbox)
        self.coordinator.setReferencePreviewProvider(self.reference_preview)
        self._reference_screens = set()
        self.app.screenAdded.connect(self._reference_screen_added)
        self.app.screenRemoved.connect(self._reference_screen_removed)
        for screen in self.app.screens():
            self._reference_screen_added(screen)
        self.coordinator.lifecycleEvent.connect(self.lifecycle.record_event)
        self.feedback = UiFeedback(settings=self.settings, windows=self.windows, session=self.session,
            coordinator=self.coordinator, enabled=not bool(probe_output))
        self.onboarding = OnboardingService(settings=self.settings, windows=self.windows,
            coordinator=self.coordinator, startup_enabled=not bool(probe_output))
        self.chest_auto_tap = ChestAutoTapService(self.paths, self.coordinator, self.input, self.settings)
        self._recovery_process = None
        self._recovery_request = None
        self._import_handoff = None
        if self.paths.production:
            from client_import_handoff import ImportHandoff
            self._import_handoff = ImportHandoff(self.sandbox)
            self.resources.setImportLauncher(self._import_handoff.launch)
            self.resources.importCloseRequested.connect(self.coordinator.requestClose)
            receipt = self._import_handoff.read_receipt()
            if receipt is not None:
                self.resources.recordImportReceipt(receipt)
            self.resources.setRecoveryLauncher(self._launch_recovery)
            self.resources.recoveryCloseRequested.connect(self.coordinator.requestClose)
            self._read_recovery_receipt()
        self.info = ClientInfoService(self.sandbox, fetch=info_fetch)
        from client_tutorial import TutorialService
        self.tutorial = TutorialService(self.paths.tutorial_state_path, settings=self.settings, parent=self.coordinator)
        self.problems = ProblemsService(self.paths, build_fingerprint=self.info._build_fingerprint,
            source_commit=self.info._source_commit)
        self.g_cycle_evidence = None
        self.g_cycle_reporter = None
        if g_cycle_evidence:
            from g_cycle_evidence import GCycleEvidenceRecorder
            from client_g_cycle_evidence import EvidenceReporter
            self.g_cycle_reporter = EvidenceReporter(self.problems)
            self.g_cycle_reporter.warning.connect(self.coordinator.notificationRequested)
            self.runtime.g_cycle_reporter = self.g_cycle_reporter
            self.g_cycle_evidence = GCycleEvidenceRecorder(
                self.paths.work_root / "g-cycle-test-evidence", enabled=True,
                result_callback=self.g_cycle_reporter.publish, full_input=g_cycle_full_input)
            self.g_cycle_evidence.start()
            self.runtime.g_cycle_evidence = self.g_cycle_evidence
            QTimer.singleShot(0, lambda: self.coordinator.notificationRequested.emit(
                "逐次开图留样测试已开启",
                ("本次进程会在本地保存已采集的完整游戏客户区求解输入，可能含昵称、聊天或其他游戏内信息。"
                 if g_cycle_full_input else "本次进程会在本地保存成功和失败的地图/楼层检测局部图，可能含昵称或聊天。") +
                "不自动上传；最多 100 个样本、256 MiB；后续写入时清理超过 24 小时的样本。"
                "退出后不会自动清理，需重新用测试参数开启。", "warning"))
        if self.lifecycle_trace is not None:
            self.lifecycle_trace.record("startup", buildFingerprint=self.info._build_fingerprint,
                sourceCommit=self.problems._source_commit)
        self.coordinator.errorReported.connect(self.problems.record)
        self.chest_auto_tap.errorReported.connect(self.problems.record)
        self.resources.errorReported.connect(self._resource_problem)
        self.package_manager.errorReported.connect(self._resource_problem)
        self.package_manager.removalCompleted.connect(
            lambda title, message: self.coordinator.notificationRequested.emit(title, message, "success"))
        self.online_packages.errorReported.connect(self._resource_problem)
        self.coordinator.traceReported.connect(self.problems.recordTrace)
        self.info.setProviders(session=self.session, resources=self.resources, input=self.input,
                               coordinator=self.coordinator, problems=self.problems,
                               settings=self.settings, capture=self.capture, performance=self.performance)
        self.coordinator.close_writers = (self.custom_galleries, self.info, self.online_packages,
                                         self.route_plaza,
                                         self.backend, self.route_choices, self.problems, self.reference_preview,
                                         self.capture_exclusion, self.manual_map_picker, self.package_manager,
                                         self.performance)
        if self.lifecycle_trace is not None:
            self.coordinator.close_writers += (self.lifecycle_trace,)
        if self.g_cycle_evidence is not None:
            # Keep ProblemsService accepting until evidence completion reports
            # have drained. Reporter includes its pending writes in close checks.
            self.coordinator.close_writers = tuple(w for w in self.coordinator.close_writers
                                                   if w is not self.problems)
            self.coordinator.close_writers += (self.g_cycle_evidence, self.g_cycle_reporter)
        self.custom_galleries.setUseHandler(self._use_custom_gallery)
        self.coordinator.close_writers += (self.tutorial,)
        self.custom_galleries.galleriesChanged.connect(self._custom_gallery_changed)
        self.custom_galleries.changed.connect(self._sync_custom_gallery_active)
        self.session.changed.connect(self._sync_custom_gallery_active)
        self._route_preferences_context = None
        self.session.changed.connect(self._refresh_route_preferences)
        self.resources.resourcesChanged.connect(lambda: self._refresh_route_preferences(force=True))
        self._refresh_route_preferences()
        self.settings.saved.connect(self._sync_custom_gallery_active)
        self._home_preview_request = None
        self._applying_home_preview = False
        self.info.homePreviewRequested.connect(self._open_home_preview)
        self.backend.changed.connect(self._finish_home_preview)
        self.backend.viewChanging.connect(self._cancel_home_preview)
        self.coordinator.pageRequested.connect(self._home_preview_page_changed)
        self.session.changed.connect(self._refresh_home_context)
        self.resources.resourcesChanged.connect(lambda: self._refresh_home_context(force=True))
        self._refresh_home_context()
        from client_floor_region import FloorRegionImages, FloorRegionService
        self.floor_region_images = FloorRegionImages()
        self.floor_region = FloorRegionService(self.runtime, self.settings, self.floor_region_images)
        self.session.changed.connect(self.floor_region.invalidateContext)
        self.engine = QQmlApplicationEngine()
        self.engine.addImageProvider("live", self.images)
        self.engine.addImageProvider("floorregion", self.floor_region_images)
        self.warnings = []
        self.engine.warnings.connect(self._record_qml_warnings)
        context = self.engine.rootContext()
        context.setContextProperty("productionProduct", PRODUCTION)
        context.setContextProperty("tutorialService", self.tutorial)
        for key, value in dict(backend=self.backend, resourceModel=self.backend.model, routeChoices=self.route_choices,
            settingsService=self.settings, gameStartupService=self.game_startup, resourceService=self.resources,
            packageManagerService=self.package_manager, session=self.session,
            windows=self.windows, clientCoordinator=self.coordinator, infoService=self.info,
            customGalleryService=self.custom_galleries, onlinePackageService=self.online_packages,
            routePlazaService=self.route_plaza,
            onboardingService=self.onboarding, chestAutoTapService=self.chest_auto_tap,
            previewService=self.preview, problemService=self.problems,
            floorRegionService=self.floor_region,
            pickerService=self.manual_map_picker).items():
            context.setContextProperty(key, value)
        set_startup_stage("create-windows")
        for name, file in (("main", "Client.qml"), ("controls", "ClientControls.qml"),
                           ("reference", "ClientReference.qml"), ("aligned", "ClientAligned.qml"),
                           ("preview", "ClientPreview.qml"),
                           ("toast", "ClientToast.qml"), ("map-picker", "ClientMapPicker.qml")):
            before = len(self.engine.rootObjects())
            self.engine.load(QUrl.fromLocalFile(str(HERE / file)))
            if len(self.engine.rootObjects()) != before + 1:
                raise RuntimeError(f"Cannot load Qt surface: {file}: " + " | ".join(self.warnings[-8:]))
            window = self.engine.rootObjects()[-1]
            self.windows.bindWindow(name, window)
        self.main = self.windows._windows["main"]
        self._main_frame_seen = False
        self.main.frameSwapped.connect(self._main_frame_ready, Qt.QueuedConnection)
        set_startup_stage("main-constructed")
        self.control_snap = ControlSnap()
        self.control_snap.bindWindow(self.windows._windows["controls"],
                                     self.settings.persistedValues.get("compactPosition"))
        self.coordinator.quitRequested.connect(app.quit)
        self._closed = False
        self._probe_output = probe_output
        self._initialized = False
        self._preparation_context_only = False
        self._initialize_timer = QTimer(app)
        self._initialize_timer.setInterval(120)
        self._initialize_timer.timeout.connect(self._initialize_resources)
        self.session.changed.connect(self._refresh_resource_preparation)
        self._initialize_timer.start()
        # Notices are a user-facing feature in source, isolated private and
        # production clients. Only automated probes suppress the shared check.
        self.info.scheduleStartupCheck(not bool(probe_output))

    def _record_qml_warnings(self, rows):
        self.warnings.extend(str(row) for row in rows)
        del self.warnings[:-128]

    def _main_frame_ready(self):
        if self._closed or self._main_frame_seen:
            return
        self._main_frame_seen = True
        # Submission is an observable rendering boundary, not proof of pixels
        # visible on a real monitor; the diagnostics name preserves that limit.
        set_startup_stage("main-frame-submitted")
        from qt_startup_splash import close_startup_splash
        close_startup_splash()
        self.performance.sample()

    def _reference_screen_added(self, screen):
        if screen not in self._reference_screens:
            self._reference_screens.add(screen)
            for signal in (screen.availableGeometryChanged, screen.geometryChanged,
                           screen.logicalDotsPerInchChanged):
                signal.connect(self.coordinator.referenceScreensChanged)
        self.coordinator.referenceScreensChanged()

    def _reference_screen_removed(self, screen):
        self._reference_screens.discard(screen)
        self.coordinator.referenceScreensChanged()

    def _resource_problem(self, event):
        if self._closed or not isinstance(event, dict) or event.get("kind") not in {"error", "warning"}:
            return
        self.problems.record(event)
        if not self.coordinator.closing:
            self.coordinator.notificationRequested.emit(event["title"], event["summary"], event["kind"])

    def _refresh_home_context(self, *, force=False):
        if self._closed or self.coordinator.closing:
            return
        mode, profile, season = self.session.appliedMode, self.session.appliedProfile, self.session.appliedSeason
        request = self._home_preview_request
        if request is not None and request[:3] != (mode, profile, season):
            self._home_preview_request = None
        self.info.setHomeContext(mode, profile, season=season, force=force)

    def _cancel_home_preview(self, _view_key=None):
        if not self._applying_home_preview:
            self._home_preview_request = None

    def _home_preview_page_changed(self, page):
        if page != "gallery":
            self._cancel_home_preview()

    def _open_home_preview(self, mode, profile, season, key):
        if (self._closed or self.coordinator.closing
                or (mode, profile, season) != (self.session.appliedMode, self.session.appliedProfile, self.session.appliedSeason)
                or not key.startswith(f"{season}/{mode}/{profile}/")):
            return
        # Explicit user navigation happens first; normal gallery initialization
        # cannot cancel the request that is installed immediately afterwards.
        self.coordinator.navigate("gallery")
        self._home_preview_request = (mode, profile, season, key)
        self._applying_home_preview = True
        try:
            self.backend.setContext(season, mode)
        finally:
            self._applying_home_preview = False
        self._finish_home_preview()

    def _finish_home_preview(self):
        request = self._home_preview_request
        if (request is None or self._applying_home_preview or self._closed
                or self.coordinator.closing or self.backend.busy):
            return
        mode, profile, season, key = request
        context = self.backend.context
        if context is None or context.mode != mode or self.backend.mode != mode or context.season != season or self.backend.season != season:
            self._home_preview_request = None
            return
        # setMode loads asynchronously; profiles/rows belong to the new context
        # only now. Clear old browsing filters before selecting the exact tile.
        self._applying_home_preview = True
        self._home_preview_request = None
        try:
            self.backend.setProfile(profile)
            if self.backend.profileId == profile:
                self.backend.clearGallerySearch()
                self.backend.setFloor("")
                self.backend.select(key)
        finally:
            self._applying_home_preview = False

    def _refresh_route_preferences(self, *, force=False):
        if self._closed or self.coordinator.closing:
            return
        context = self.runtime.route_preferences_context()
        if force or context != self._route_preferences_context:
            self._route_preferences_context = context
            self.settings.refreshRoutePreferences(self.runtime)

    def _launch_recovery(self, action, plan):
        """Worker-side handoff: launch first, then service requests normal close."""
        from star_map_recovery_helper import HELPER_NAME, launch_recovery_helper, cancel_recovery_request
        from client_paths import _no_links
        helper = self.sandbox / (HELPER_NAME + ".exe")
        _no_links(helper)
        if not helper.is_file():
            raise FileNotFoundError("Installed recovery helper is missing")
        request_id = uuid4().hex
        try:
            process = launch_recovery_helper(helper, self.sandbox, plan, action, request_id)
            pending = self.paths.work_root / "map-packages/qt-recovery-pending.json"
            _no_links(pending)
            temporary = pending.with_name(pending.name + "." + request_id + ".tmp")
            with temporary.open("x", encoding="utf-8") as stream:
                json.dump(dict(requestId=request_id, planSha256=plan["planSha256"], action=action), stream)
            os.replace(temporary, pending)
        except Exception:
            cancel_recovery_request(self.sandbox, request_id)
            raise
        self._recovery_process, self._recovery_request = process, request_id
        return request_id

    def _read_recovery_receipt(self):
        from star_map_recovery_helper import receipt_path
        from client_paths import _no_links
        pending = self.paths.work_root / "map-packages/qt-recovery-pending.json"
        receipt = receipt_path(self.sandbox)
        try:
            _no_links(pending)
            _no_links(receipt)
            if not pending.is_file() or not receipt.is_file():
                return
            if pending.stat().st_size > 65536 or receipt.stat().st_size > 1048576:
                return
            expected = json.loads(pending.read_text(encoding="utf-8"))
            result = json.loads(receipt.read_text(encoding="utf-8"))
            if not all(result.get(key) == expected.get(key) and expected.get(key)
                       for key in ("requestId", "planSha256", "action")):
                return
            self._recovery_receipt = result
            self.resources.recordRecoveryReceipt(result)
            self.resources.refresh()
        except (OSError, ValueError, TypeError, AttributeError):
            # Leave unmatched/malformed evidence intact for diagnostics.
            return

    def _initialize_resources(self):
        if self._closed or self.coordinator.closing:
            self._initialize_timer.stop()
            return
        if (self.resources.busy or self.resources.pendingKind or self.session.busy
                or self.session.snapshot.get("resourceLocked")):
            return
        self._initialize_timer.stop()
        self._initialized = True
        if self._preparation_context_only:
            # A timer scheduled for an unprepared context can outlive that
            # context. Do not disable controls/close popups to inspect a cached
            # context that is already prepared. Explicit resource changes and
            # startup still require their inspection, even with an old snapshot.
            state = self.session.snapshot
            season, mode = self.session.appliedSeason, self.session.appliedMode
            report = state.get("savedFrameCapabilities", {}).get(mode, {})
            if (report.get("season") == season and report.get("mode") == mode
                    and report.get("prepared") is True):
                return
        self.session.dispatch("inspect_engine_resources")

    def _schedule_resource_preparation(self, *, context_only=False):
        if self._closed or self.coordinator.closing:
            return
        self._initialized = False
        self._preparation_context_only = bool(context_only)
        self._initialize_timer.start()

    def _refresh_resource_preparation(self):
        """Prepare an unvalidated applied context through the existing worker.

        Startup can legitimately prepare only an unavailable future season.
        Returning to S0 must then validate S0 instead of waiting for a resource
        write. Observe committed Session state, never a settings draft or a
        desired context whose asynchronous switch has not completed.
        """
        if self._closed or self.coordinator.closing or self.session.busy:
            return
        state = self.session.snapshot
        if state.get("closed") or state.get("contextPending"):
            return
        season, mode = self.session.appliedSeason, self.session.appliedMode
        report = state.get("savedFrameCapabilities", {}).get(mode, {})
        if (report.get("season") == season and report.get("mode") == mode
                and report.get("prepared") is True):
            set_startup_stage("context-ready" if report.get("available") else "context-unavailable")
        if (report.get("season") != season or report.get("mode") != mode
                or report.get("prepared") is not False
                or report.get("reason") != "resource-validation-pending"):
            return
        receipt = state.get("lastReceipt", {})
        if receipt.get("action") == "inspect_engine_resources" and receipt.get("success") is False:
            # An unexpected registry error may leave its report unprepared.
            # Keep that failure visible rather than creating an endless retry
            # loop. A new applied context or explicit resource change may retry.
            return
        # Resource failures are prepared-but-unavailable and never retried in a
        # loop. A cancelled/stale preparation may remain unprepared; a later
        # idle signal retries it only for the current applied context.
        if not self._initialize_timer.isActive():
            self._schedule_resource_preparation(context_only=True)

    def _use_custom_gallery(self, gallery_id):
        if (self._closed or self.coordinator.closing or self.settings.busy or self.resources.busy or self.session.busy
                or getattr(self.resources, "pendingKind", "")):
            return dict(success=False, applied=False, message="请等待当前操作完成后再使用图集")
        values = self.settings.persistedValues
        season = values.get("gameSeason", "s0")
        mode = values.get("gameMode", "nightmare")
        if season != "s0":
            return dict(success=False, applied=False, message=f"{season_label(season)} 资源待补齐，不能使用 S0 自定义路线")
        previous = selected_profile(values, mode, season)
        remembered = getattr(self, "_previous_author_profiles", {})
        if previous and not str(previous).startswith("custom:"):
            remembered[context_key(mode, season)] = previous
        self._previous_author_profiles = remembered
        current_options = (self.route_choices.profiles
                           if (self.route_choices.season, self.route_choices.mode) == (season, mode) else ())
        fallback = remembered.get(context_key(mode, season)) or next((row["id"] for row in current_options
                         if not row["id"].startswith("custom:")), "shared-structure")
        proposal = self.custom_display.use_request(gallery_id, mode, fallback_profile=fallback)
        if not proposal.get("success"):
            return proposal
        profile = proposal["profileId"]
        profiles = dict(values.get("contextRouteProfiles", {}))
        profiles[context_key(mode, season)] = profile
        legacy_profiles = dict(values.get("modeRouteProfiles", {}))
        legacy_profiles[mode] = profile
        if not (self.settings.setValue("routeProfileId", profile)
                and self.settings.setValue("contextRouteProfiles", profiles)
                and self.settings.setValue("modeRouteProfiles", legacy_profiles)
                and self.settings.saveFields(["routeProfileId", "modeRouteProfiles", "contextRouteProfiles"])):
            return dict(success=False, applied=False, message=self.settings.message)
        return dict(success=True, applied=False, message="正在保存并应用图集，完成后更新使用状态")

    def _sync_custom_gallery_active(self):
        if self._closed or self.coordinator.closing:
            return
        state = self.runtime.snapshot()
        values = self.settings.persistedValues
        season = values.get("gameSeason", "s0")
        mode = values.get("gameMode", "nightmare")
        profile = selected_profile(values, mode, season)
        applied = (state.get("mode"), state.get("profileId")) == (mode, profile)
        active = self.custom_display.active_gallery(profile, mode) if applied and season == "s0" and state.get("season", "s0") == season else ""
        if self.custom_galleries.snapshot.get("inUse") != active:
            self.custom_galleries.setActiveGallery(active)

    def _custom_gallery_changed(self):
        if self._closed or self.coordinator.closing:
            return
        self.custom_display.invalidate()
        if str(self.runtime.snapshot().get("profileId", "")).startswith("custom:"):
            self.coordinator._revoke_runtime("custom-gallery-changed")
            self.session.refreshRuntime()
        self._sync_custom_gallery_active()

    def shutdown(self):
        performance = getattr(self, "performance", None)
        if performance is not None:
            performance.shutdown()
        from client_startup_cleanup import shutdown_client
        shutdown_client(self)


def main():
    from qt_startup_splash import close_startup_splash
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sandbox", type=Path, default=DEFAULT_SANDBOX)
    parser.add_argument("--probe-output", type=Path)
    parser.add_argument("--g-cycle-evidence", action="store_true",
                        help="this process only: bounded local G-attempt algorithm ROI evidence; may contain game text; no upload")
    parser.add_argument("--g-cycle-full-input", action="store_true",
                        help="with --g-cycle-evidence only: keep the existing full game-client solver input locally, including game text; no desktop capture or upload")
    parser.add_argument("--self-check", action="store_true", help="read-only packaged import/resource check, no windows or listeners")
    parser.add_argument("--verify-resources", action="store_true", help="with self-check, verify S0 and every included S1 provider without capture")
    parser.add_argument("--offline-resource-root", type=Path,
                        help="with self-check, verify the explicit S1 offline gallery root and decode its images")
    args = parser.parse_args()
    if args.g_cycle_full_input and not args.g_cycle_evidence:
        parser.error("--g-cycle-full-input requires --g-cycle-evidence")
    if args.g_cycle_evidence and (args.self_check or args.probe_output):
        parser.error("--g-cycle-evidence cannot be combined with self-check or UI probes")
    if args.verify_resources and not args.self_check:
        parser.error("--verify-resources requires --self-check")
    if args.offline_resource_root is not None and not args.self_check:
        parser.error("--offline-resource-root requires --self-check")
    if args.self_check:
        from client_engine import EngineAdapter
        from client_providers import build_engine_factories
        from client_live_runtime import LiveRuntime
        from client_input import NativeShortcutBackend
        result = dict(ready=True, frozen=FROZEN, resourceRoot=str(RESOURCE_ROOT),
                      qml=all((HERE / name).is_file() for name in (
                          "Client.qml", "ClientControls.qml", "ClientReference.qml", "ClientAligned.qml",
                          "ClientMapPicker.qml")),
                      tkLoaded="tkinter" in sys.modules,
                      nativeInputStarted=False, windowsCreated=False,
                      listenersStarted=False, captureStarted=False)
        if args.offline_resource_root is not None:
            from season_map_resources import verify_offline_inventory
            result["offlineGallery"] = verify_offline_inventory(args.offline_resource_root.resolve())
            result["ready"] = result["offlineGallery"]["ready"]
        if args.verify_resources:
            root = RESOURCE_ROOT if FROZEN else args.sandbox.resolve() / "_internal"
            from client_resource_startup import readable_resources
            installation = owned_sandbox(args.sandbox.resolve()) if PRODUCTION else args.sandbox.resolve()
            with readable_resources(installation, production=PRODUCTION):
                registry = build_engine_factories(root)
                registry.prepare()
                result["providers"] = registry.capabilities()
                from s1_private_resources import validated_s1_resources, S1_RUNTIME_MODES, private_profile_modes
                from packaged_product_identity import PRIVATE_S1_INSTANCES
                required_s1 = False
                native_modes = S1_RUNTIME_MODES
                formal_s1_contexts = None
                if FROZEN:
                    profile = json.loads((RESOURCE_ROOT / "installation-profile.json").read_text(encoding="utf-8"))
                    required_s1 = profile["installerInstance"]["id"] in PRIVATE_S1_INSTANCES
                    if required_s1:
                        native_modes = private_profile_modes(profile)
                    declared = profile.get("includedResourceContexts")
                    if PRODUCTION and "includedResourceContexts" in profile:
                        s0_contexts = {"s0:nightmare", "s0:difficult"}
                        if (type(declared) is not list or any(type(key) is not str for key in declared)
                                or len(declared) != len(set(declared))):
                            raise ValueError("Invalid production resource context declaration")
                        if set(declared) != s0_contexts:
                            expected = s0_contexts | {"s1:" + mode for mode in S1_RUNTIME_MODES}
                            if set(declared) != expected:
                                raise ValueError("Incomplete production S1 resource context declaration")
                            # A declaration requires validation; it never grants runtime capability.
                            formal_s1_contexts = expected
                            required_s1 = True
                native = validated_s1_resources(root, required=required_s1, modes=native_modes)
                result["resourceContexts"] = {"s0:" + mode: row for mode, row in result["providers"].items()}
                result["resourceContexts"].update({"s1:" + mode: row for mode, row in native["providers"].items()})
                result["s1Included"] = native["included"]
                if formal_s1_contexts is not None:
                    if native["included"] is not True or set(result["resourceContexts"]) != formal_s1_contexts:
                        raise ValueError("Declared production S1 resource context missing or extra")
                    for key, row in result["resourceContexts"].items():
                        season, mode = key.split(":", 1)
                        if (not isinstance(row, dict) or row.get("available") is not True
                                or row.get("prepared") is not True or row.get("season") != season
                                or row.get("mode") != mode):
                            raise ValueError("Declared production resource context unavailable or misbound: " + key)
            result["tkLoaded"] = "tkinter" in sys.modules
            result["ready"] = result["ready"] and all(row.get("available") for row in result["resourceContexts"].values())
        if FROZEN:
            WORK_ROOT.mkdir(parents=True, exist_ok=True)
            (WORK_ROOT / "client-self-check.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
        print(json.dumps(result))
        return 0 if result["ready"] and result["qml"] and not result["tkLoaded"] else 1
    if os.name == "nt":
        import ctypes
        from packaged_product_identity import qt_application_model_id
        model_id = qt_application_model_id()
        set_model_id = ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID
        set_model_id.argtypes = [ctypes.c_wchar_p]
        set_model_id.restype = ctypes.c_long
        if set_model_id(model_id) != 0:
            raise OSError("Could not set the Windows application identity")
    app = QGuiApplication([])
    # Ownership precedes engine creation, first-run workers and every native
    # listener. Production identity is stable across upgrades; probes are separate.
    from client_single_instance import SingleInstanceGuard, SingleInstanceError, instance_namespace, restore_main_window
    guard = None
    set_startup_stage("instance-check")
    try:
        guard = SingleInstanceGuard(instance_namespace(production=PRODUCTION, frozen=FROZEN,
            sandbox=args.sandbox, probe_output=args.probe_output), app)
        primary = guard.start()
    except (SingleInstanceError, OSError) as error:
        close_startup_splash()
        message = str(error) if isinstance(error, SingleInstanceError) else f"无法确认当前用户会话的启动锁，未启动第二个实例：{error}"
        print(message, file=sys.stderr)
        if os.name == "nt":
            import ctypes
            ctypes.windll.user32.MessageBoxW(None, message, "星图启动提示", 0x10)
        if guard is not None:
            guard.close()
        return 2
    if not primary:
        close_startup_splash()
        guard.close()
        return 0
    from client_resource_startup import production_resources
    from client_resource_startup_recovery import offer_startup_recovery
    def recover_startup(error):
        close_startup_splash()
        return offer_startup_recovery(resource_root, error)
    try:
        resource_root = owned_sandbox(args.sandbox.resolve()) if PRODUCTION else args.sandbox.resolve()
        set_startup_stage("resource-check")
        args.resource_startup_report = {}
        with production_resources(resource_root, production=PRODUCTION,
                on_failure=recover_startup, startup_report=args.resource_startup_report) as recovery:
            if recovery is not None:
                return recovery.exit_code
            return _run_primary_client(app, args, guard)
    finally:
        guard.close()


def _run_primary_client(app, args, guard):
    from qt_startup_splash import close_startup_splash
    from client_single_instance import restore_main_window
    app.setWindowIcon(QIcon(str(HERE / "icons" / "xingtu-round.png")))
    from client_theme import apply_light_theme
    apply_light_theme(app)
    app.setOrganizationName("XiaochaoAssistant" if PRODUCTION else "XingtuQtClientTest")
    app.setApplicationName("星图" if PRODUCTION else "XingtuQtClientTest")
    app.setFont(QFont("Microsoft YaHei UI", 10))
    if args.probe_output and Path("C:/Windows/Fonts/msyh.ttc").is_file():
        QFontDatabase.addApplicationFont("C:/Windows/Fonts/msyh.ttc")
    args.sandbox = args.sandbox.resolve()
    if PRODUCTION:
        owned_sandbox(args.sandbox)
    holder = {"activationPending": False}
    def restore_existing_main():
        client = holder.get("client")
        if client is None:
            holder["activationPending"] = True
            return 'starting'
        if client.coordinator.closing:
            return 'closing'
        try:
            restore_main_window(client.main)
            return 'restored' if client.main.isVisible() and client.main.visibility() != QWindow.Minimized else 'unavailable'
        except (RuntimeError, AttributeError):
            return 'unavailable'
    guard.setActivationHandler(restore_existing_main)
    def open_client(sandbox):
        try:
            sandbox = owned_sandbox(sandbox)
            local_manifest = (lambda url: dict(schemaVersion=1, platform="windows", version="1.0.5",
                              versionCode=9, releaseNotes="Local probe", publishedAt="2026-10-30", releases=[])) if args.probe_output else None
            client = holder["client"] = QtClient(app, sandbox, probe_output=args.probe_output, info_fetch=local_manifest,
                on_created=lambda partial: holder.update(client=partial),
                g_cycle_evidence=getattr(args, "g_cycle_evidence", False),
                g_cycle_full_input=getattr(args, "g_cycle_full_input", False))
            client.resources.setStartupCompatibilityReport(getattr(args, 'resource_startup_report', {}))
            if holder.pop("activationPending", False):
                restore_main_window(client.main)
            holder["quit_journal"] = lambda: client.lifecycle.record_event(dict(stage="about-to-quit"))
            app.aboutToQuit.connect(holder["quit_journal"])
            if "boot" in holder:
                holder["boot"].rootObjects()[0].hide()
            if args.probe_output:
                from probe_client import run_client_probe
                run_client_probe(client, args.probe_output)
        except Exception as error:
            holder["startup_error"] = error
            app.exit(1)
    if args.sandbox.exists():
        QTimer.singleShot(0, lambda: open_client(args.sandbox))
    else:
        from backend import Work
        boot = holder["boot"] = QQmlApplicationEngine()
        boot.rootContext().setContextProperty("productionProduct", PRODUCTION)
        boot.load(QUrl.fromLocalFile(str(HERE / "ClientBoot.qml")))
        if not boot.rootObjects():
            raise RuntimeError("Startup preparation window could not be loaded")
        close_startup_splash()
        pool = holder["bootPool"] = QThreadPool(app)
        job = holder["bootJob"] = Work(lambda: prepare_first_run(args.sandbox))
        def prepared(path, error):
            if error:
                boot.rootObjects()[0].setProperty("statusText", f"准备失败：{error}")
            else:
                open_client(path)
        job.signals.done.connect(prepared, Qt.QueuedConnection)
        pool.start(job)
        app.aboutToQuit.connect(pool.waitForDone)
    try:
        exit_code = app.exec()
        client = holder.get("client")
        lifecycle = getattr(client, "lifecycle", None)
        if lifecycle is not None:
            lifecycle.record_event(dict(stage="app-loop-return", exitCode=exit_code))
    finally:
        close_startup_splash()
        # Qt swallows exceptions in slots. Run cleanup synchronously here so a
        # partial constructor, direct quit or app.exec error all retain the
        # outer runtime lease until owned resource consumers have really drained.
        try:
            client = holder.get("client")
            if client is not None:
                try:
                    client.shutdown()
                except Exception as error:
                    if "startup_error" in holder:
                        raise error from holder["startup_error"]
                    raise
        finally:
            if "bootPool" in holder:
                holder["bootPool"].waitForDone()
            if "quit_journal" in holder:
                app.aboutToQuit.disconnect(holder["quit_journal"])
    if "startup_error" in holder:
        from qt_startup_diagnostics import report_startup_failure
        report_startup_failure(holder["startup_error"], stage="create-client")
        return 1
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
