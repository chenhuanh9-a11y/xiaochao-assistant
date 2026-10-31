"""Opt-in Qt private client; the existing Tk and qt-lab launchers are untouched."""
from __future__ import annotations
import argparse
import json
import os
from pathlib import Path
import shutil
import sys
from uuid import uuid4
from PySide6.QtCore import QTimer, QUrl, QThreadPool, Qt
from PySide6.QtGui import QFont, QFontDatabase, QGuiApplication, QIcon, QWindow
from PySide6.QtQml import QQmlApplicationEngine
from client_paths import APP_ROOT, DEFAULT_SANDBOX, FROZEN, PRODUCTION, RESOURCE_ROOT, WORK_ROOT, owned_sandbox, runtime_paths

if str(APP_ROOT) not in sys.path:
    sys.path.insert(0, str(APP_ROOT))

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
    def __init__(self, app, sandbox, *, probe_output=None, info_fetch=None):
        from backend import Backend
        from client_settings import SettingsService
        from client_resources import ResourceService
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
        from client_lifecycle_trace import create_lifecycle_trace
        self.lifecycle_trace = create_lifecycle_trace(self.paths)
        from client_lifecycle import LifecycleJournal
        self.lifecycle = LifecycleJournal(self.paths.work_root)
        self.lifecycle.record_event(dict(stage="client-created"))
        self._closed = False
        self.settings = SettingsService(self.sandbox)
        self.custom_galleries = CustomGalleryService(self.sandbox)
        self.custom_display = CustomGalleryDisplay(self.custom_galleries)
        from client_capture_exclusion import QtCaptureExclusion
        self.capture_exclusion = QtCaptureExclusion(parent=app,
            is_closing=lambda: self._closed or bool(getattr(getattr(self, "coordinator", None), "closing", False)))
        self.capture = ClientCaptureProvider(full_capture=self.capture_exclusion.full_capture)
        self.input = NativeShortcutBackend()
        self.providers = build_engine_factories(self.paths.resource_root)
        self.providers.set_custom_display(self.custom_display.layer, base_provider=self.custom_display.base_layer)
        self.runtime = LiveRuntime(engine_factories=self.providers, capture_provider=self.capture,
                                   allowed_read_root=WORK_ROOT, evidence_kind="live-client-capture",
                                   diagnostics=self.lifecycle_trace)
        values = self.settings.persistedValues
        self.runtime.configure_settings(values)
        mode = values.get("gameMode", "nightmare")
        profile = values.get("modeRouteProfiles", {}).get(mode) or values.get("routeProfileId", "shared-structure")
        self._previous_author_profiles = {
            key: value for key, value in values.get("modeRouteProfiles", {}).items()
            if value and not str(value).startswith("custom:")}
        if not str(profile).startswith("custom:"):
            self._previous_author_profiles[mode] = profile
        # Context initialization is a bounded state-only operation. Resource
        # preparation/recognition starts later on SessionService's worker.
        self.runtime.dispatch("set_context", {"mode": mode, "profileId": profile})
        self.session = SessionService(self.runtime)
        self.resources = ResourceService(self.sandbox)
        self.resources.setLeaseProvider(self.session)
        self.online_packages = OnlinePackagesService(self.sandbox, self.resources,
            index_url=None if probe_output else os.environ.get("STAR_MAP_PACKAGE_INDEX_URL"))
        self.backend = Backend(self.sandbox)
        self.route_choices = Backend(self.sandbox)
        self.resources.resourcesChanged.connect(self.route_choices.refresh)
        self.resources.resourcesChanged.connect(self._schedule_resource_preparation)
        self.resources.resourcesChanged.connect(self.custom_galleries.refresh)
        self.settings.changed.connect(lambda: self.route_choices.setMode(self.settings.values.get("gameMode", "nightmare")))
        self.route_choices.setMode(mode)
        self.windows = WindowController()
        self.windows.setBeforeShow(self.capture_exclusion.protectWindow)
        self.preview = PreviewService(self.backend, self.windows)
        self.backend.overlayRequested.connect(self.preview.show)
        self.images = LiveImages()
        self.coordinator = ClientCoordinator(backend=self.backend, settings=self.settings,
            resources=self.resources, session=self.session, windows=self.windows,
            native_input=self.input, capture=self.capture, runtime=self.runtime, images=self.images,
            **({"window_catalog": lambda: []} if probe_output else {}))
        self.reference_preview = ReferencePreviewProvider(self.providers, parent=self.coordinator)
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
        self.problems = ProblemsService(self.paths, build_fingerprint=self.info._build_fingerprint,
            source_commit=self.info._package_metadata.get('qtSourceCommit') or self.info._package_metadata.get('releaseReportSourceCommit') or '')
        if self.lifecycle_trace is not None:
            self.lifecycle_trace.record("startup", buildFingerprint=self.info._build_fingerprint,
                sourceCommit=self.problems._source_commit)
        self.coordinator.errorReported.connect(self.problems.record)
        self.resources.errorReported.connect(self._resource_problem)
        self.online_packages.errorReported.connect(self._resource_problem)
        self.coordinator.traceReported.connect(self.problems.recordTrace)
        self.info.setProviders(session=self.session, resources=self.resources, input=self.input,
                               coordinator=self.coordinator, problems=self.problems)
        self.coordinator.close_writers = (self.custom_galleries, self.info, self.online_packages,
                                         self.backend, self.route_choices, self.problems, self.reference_preview,
                                         self.capture_exclusion)
        if self.lifecycle_trace is not None:
            self.coordinator.close_writers += (self.lifecycle_trace,)
        self.custom_galleries.setUseHandler(self._use_custom_gallery)
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
        self.engine = QQmlApplicationEngine()
        self.engine.addImageProvider("live", self.images)
        self.warnings = []
        self.engine.warnings.connect(lambda rows: self.warnings.extend(str(row) for row in rows))
        context = self.engine.rootContext()
        context.setContextProperty("productionProduct", PRODUCTION)
        for key, value in dict(backend=self.backend, resourceModel=self.backend.model, routeChoices=self.route_choices,
            settingsService=self.settings, resourceService=self.resources, session=self.session,
            windows=self.windows, clientCoordinator=self.coordinator, infoService=self.info,
            customGalleryService=self.custom_galleries, onlinePackageService=self.online_packages,
            onboardingService=self.onboarding, chestAutoTapService=self.chest_auto_tap,
            previewService=self.preview, problemService=self.problems).items():
            context.setContextProperty(key, value)
        for name, file in (("main", "Client.qml"), ("controls", "ClientControls.qml"),
                           ("reference", "ClientReference.qml"), ("aligned", "ClientAligned.qml"),
                           ("replay", "Overlay.qml"), ("preview", "ClientPreview.qml"),
                           ("toast", "ClientToast.qml")):
            before = len(self.engine.rootObjects())
            self.engine.load(QUrl.fromLocalFile(str(HERE / file)))
            if len(self.engine.rootObjects()) != before + 1:
                raise RuntimeError(f"Cannot load Qt surface: {file}: " + " | ".join(self.warnings[-8:]))
            window = self.engine.rootObjects()[-1]
            self.windows.bindWindow(name, window)
        self.main = self.windows._windows["main"]
        self.control_snap = ControlSnap()
        self.control_snap.bindWindow(self.windows._windows["controls"],
                                     self.settings.persistedValues.get("compactPosition"))
        self.coordinator.quitRequested.connect(app.quit)
        self._closed = False
        self._probe_output = probe_output
        self._initialized = False
        self._initialize_timer = QTimer(app)
        self._initialize_timer.setInterval(120)
        self._initialize_timer.timeout.connect(self._initialize_resources)
        self._initialize_timer.start()
        self.info.scheduleStartupCheck(bool(self.paths.production and not probe_output))

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
        mode, profile = self.session.appliedMode, self.session.appliedProfile
        request = self._home_preview_request
        if request is not None and request[:2] != (mode, profile):
            self._home_preview_request = None
        self.info.setHomeContext(mode, profile, force=force)

    def _cancel_home_preview(self, _view_key=None):
        if not self._applying_home_preview:
            self._home_preview_request = None

    def _home_preview_page_changed(self, page):
        if page != "gallery":
            self._cancel_home_preview()

    def _open_home_preview(self, mode, profile, key):
        if (self._closed or self.coordinator.closing
                or (mode, profile) != (self.session.appliedMode, self.session.appliedProfile)
                or not key.startswith(f"{mode}/{profile}/")):
            return
        # Explicit user navigation happens first; normal gallery initialization
        # cannot cancel the request that is installed immediately afterwards.
        self.coordinator.navigate("gallery")
        self._home_preview_request = (mode, profile, key)
        self._applying_home_preview = True
        try:
            self.backend.setMode(mode)
        finally:
            self._applying_home_preview = False
        self._finish_home_preview()

    def _finish_home_preview(self):
        request = self._home_preview_request
        if (request is None or self._applying_home_preview or self._closed
                or self.coordinator.closing or self.backend.busy):
            return
        mode, profile, key = request
        context = self.backend.context
        if context is None or context.mode != mode or self.backend.mode != mode:
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
        self.session.dispatch("inspect_engine_resources")

    def _schedule_resource_preparation(self):
        if self._closed or self.coordinator.closing:
            return
        self._initialized = False
        self._initialize_timer.start()

    def _use_custom_gallery(self, gallery_id):
        if (self._closed or self.coordinator.closing or self.settings.busy or self.resources.busy or self.session.busy
                or getattr(self.resources, "pendingKind", "")):
            return dict(success=False, applied=False, message="请等待当前操作完成后再使用图集")
        values = self.settings.persistedValues
        mode = values.get("gameMode", "nightmare")
        previous = values.get("modeRouteProfiles", {}).get(mode) or values.get("routeProfileId", "")
        remembered = getattr(self, "_previous_author_profiles", {})
        if previous and not str(previous).startswith("custom:"):
            remembered[mode] = previous
        self._previous_author_profiles = remembered
        current_options = (self.route_choices.profiles
                           if getattr(self.route_choices, "mode", mode) == mode else ())
        fallback = remembered.get(mode) or next((row["id"] for row in current_options
                         if not row["id"].startswith("custom:")), "shared-structure")
        proposal = self.custom_display.use_request(gallery_id, mode, fallback_profile=fallback)
        if not proposal.get("success"):
            return proposal
        profile = proposal["profileId"]
        profiles = dict(values.get("modeRouteProfiles", {}))
        profiles[mode] = profile
        if not (self.settings.setValue("routeProfileId", profile)
                and self.settings.setValue("modeRouteProfiles", profiles)
                and self.settings.saveFields(["routeProfileId", "modeRouteProfiles"])):
            return dict(success=False, applied=False, message=self.settings.message)
        return dict(success=True, applied=False, message="正在保存并应用图集，完成后更新使用状态")

    def _sync_custom_gallery_active(self):
        if self._closed or self.coordinator.closing:
            return
        state = self.runtime.snapshot()
        values = self.settings.persistedValues
        mode = values.get("gameMode", "nightmare")
        profile = values.get("modeRouteProfiles", {}).get(mode) or values.get("routeProfileId", "")
        applied = (state.get("mode"), state.get("profileId")) == (mode, profile)
        active = self.custom_display.active_gallery(profile, mode) if applied else ""
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
        if self._closed:
            return
        self.lifecycle.record_event(dict(stage="shutdown-entered", closing=self.coordinator.closing,
                                         closeReady=self.coordinator.closeReady))
        self._closed = True
        self.problems.shutdown()
        if self.lifecycle_trace is not None:
            self.lifecycle_trace.shutdown()
        self.chest_auto_tap.shutdown()
        if self._import_handoff is not None and not self.coordinator.closeReady:
            self._import_handoff.cancel()
        if self._recovery_request and not self.coordinator.closeReady:
            from star_map_recovery_helper import cancel_recovery_request
            cancel_recovery_request(self.sandbox, self._recovery_request)
        self._initialize_timer.stop()
        self.coordinator.shutdown()
        self.session.shutdown()
        self.capture_exclusion.shutdown()
        self.resources.shutdown()
        self.settings.shutdown()
        self.preview.shutdown()
        self.reference_preview.close()
        self.reference_preview.pool.waitForDone()
        self.control_snap.shutdown()
        self.backend.shutdown()
        self.route_choices.shutdown()
        self.info.shutdown()
        self.custom_galleries.shutdown()
        self.online_packages.shutdown()
        self.windows.shutdown()
        self.lifecycle.record_event(dict(stage="shutdown-completed", closing=self.coordinator.closing,
                                         closeReady=self.coordinator.closeReady))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sandbox", type=Path, default=DEFAULT_SANDBOX)
    parser.add_argument("--probe-output", type=Path)
    parser.add_argument("--self-check", action="store_true", help="read-only packaged import/resource check, no windows or listeners")
    parser.add_argument("--verify-resources", action="store_true", help="with self-check, construct both explicit-root providers without capture")
    args = parser.parse_args()
    if args.verify_resources and not args.self_check:
        parser.error("--verify-resources requires --self-check")
    if args.self_check:
        from client_engine import EngineAdapter
        from client_providers import build_engine_factories
        from client_live_runtime import LiveRuntime
        from client_input import NativeShortcutBackend
        result = dict(ready=True, frozen=FROZEN, resourceRoot=str(RESOURCE_ROOT),
                      qml=all((HERE / name).is_file() for name in (
                          "Client.qml", "ClientControls.qml", "ClientReference.qml", "ClientAligned.qml")),
                      tkLoaded="tkinter" in sys.modules,
                      nativeInputStarted=False)
        if args.verify_resources:
            root = RESOURCE_ROOT if FROZEN else args.sandbox.resolve() / "_internal"
            registry = build_engine_factories(root)
            registry.prepare()
            result["providers"] = registry.capabilities()
            result["tkLoaded"] = "tkinter" in sys.modules
            result["ready"] = all(row.get("available") for row in result["providers"].values())
        if FROZEN:
            WORK_ROOT.mkdir(parents=True, exist_ok=True)
            (WORK_ROOT / "client-self-check.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
        print(json.dumps(result))
        return 0 if result["ready"] and result["qml"] and not result["tkLoaded"] else 1
    if os.name == "nt":
        import ctypes
        model_id = "Xingtu.Assistant" if PRODUCTION else ("Xingtu.QtClientTest" if FROZEN else "Xingtu.QtClientSource")
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
    try:
        guard = SingleInstanceGuard(instance_namespace(production=PRODUCTION, frozen=FROZEN,
            sandbox=args.sandbox, probe_output=args.probe_output), app)
        primary = guard.start()
    except (SingleInstanceError, OSError) as error:
        message = str(error) if isinstance(error, SingleInstanceError) else f"无法确认当前用户会话的启动锁，未启动第二个实例：{error}"
        print(message, file=sys.stderr)
        if os.name == "nt":
            import ctypes
            ctypes.windll.user32.MessageBoxW(None, message, "星图启动提示", 0x10)
        if guard is not None:
            guard.close()
        return 2
    if not primary:
        guard.close()
        return 0
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
            local_manifest = (lambda url: dict(schemaVersion=1, platform="windows", version="1.0.5",
                              versionCode=9, releaseNotes="Local probe", publishedAt="2026-10-30", releases=[])) if args.probe_output else None
            client = holder["client"] = QtClient(app, sandbox, probe_output=args.probe_output, info_fetch=local_manifest)
            if holder.pop("activationPending", False):
                restore_main_window(client.main)
            app.aboutToQuit.connect(lambda: client.lifecycle.record_event(dict(stage="about-to-quit")))
            app.aboutToQuit.connect(client.shutdown)
            if "boot" in holder:
                holder["boot"].rootObjects()[0].hide()
            if args.probe_output:
                from probe_client import run_client_probe
                run_client_probe(client, args.probe_output)
        except Exception as error:
            print(f"Qt client startup failed: {type(error).__name__}: {error}", file=sys.stderr)
            if "boot" in holder:
                holder["boot"].rootObjects()[0].setProperty("statusText", f"准备失败：{error}")
            else:
                app.exit(1)
    if args.sandbox.exists():
        QTimer.singleShot(0, lambda: open_client(owned_sandbox(args.sandbox)))
    else:
        from backend import Work
        boot = holder["boot"] = QQmlApplicationEngine()
        boot.rootContext().setContextProperty("productionProduct", PRODUCTION)
        boot.load(QUrl.fromLocalFile(str(HERE / "ClientBoot.qml")))
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
        if client is not None:
            client.lifecycle.record_event(dict(stage="app-loop-return", exitCode=exit_code))
        return exit_code
    finally:
        # Keep ownership through aboutToQuit's existing writer-drain callbacks.
        guard.close()
        client = holder.get("client")
        if client is not None:
            client.lifecycle.record_event(dict(stage="instance-guard-closed"))


if __name__ == "__main__":
    raise SystemExit(main())
