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
from PySide6.QtGui import QFont, QFontDatabase, QGuiApplication
from PySide6.QtQml import QQmlApplicationEngine
from client_paths import APP_ROOT, DEFAULT_SANDBOX, FROZEN, RESOURCE_ROOT, WORK_ROOT, owned_sandbox

if str(APP_ROOT) not in sys.path:
    sys.path.insert(0, str(APP_ROOT))

HERE = Path(__file__).resolve().parent


def prepare_first_run(sandbox):
    """Stage a new test resource tree and publish it only when complete."""
    sandbox = Path(sandbox).resolve()
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
        from client_coordinator import ClientCoordinator, LiveImages
        self.app, self.sandbox = app, owned_sandbox(sandbox)
        self.settings = SettingsService(self.sandbox)
        self.capture = ClientCaptureProvider()
        self.input = NativeShortcutBackend()
        self.providers = build_engine_factories(self.sandbox / "_internal")
        self.runtime = LiveRuntime(engine_factories=self.providers, capture_provider=self.capture,
                                   allowed_read_root=WORK_ROOT, evidence_kind="live-client-capture")
        values = self.settings.persistedValues
        self.runtime.configure_settings(values)
        mode = values.get("gameMode", "nightmare")
        profile = values.get("modeRouteProfiles", {}).get(mode) or values.get("routeProfileId", "shared-structure")
        # Context initialization is a bounded state-only operation. Resource
        # preparation/recognition starts later on SessionService's worker.
        self.runtime.dispatch("set_context", {"mode": mode, "profileId": profile})
        self.session = SessionService(self.runtime)
        self.resources = ResourceService(self.sandbox)
        self.resources.setLeaseProvider(self.session)
        self.backend = Backend(self.sandbox)
        self.route_choices = Backend(self.sandbox)
        self.resources.resourcesChanged.connect(self.route_choices.refresh)
        self.resources.resourcesChanged.connect(self._schedule_resource_preparation)
        self.settings.changed.connect(lambda: self.route_choices.setMode(self.settings.values.get("gameMode", "nightmare")))
        self.route_choices.setMode(mode)
        self.windows = WindowController()
        self.images = LiveImages()
        self.coordinator = ClientCoordinator(backend=self.backend, settings=self.settings,
            resources=self.resources, session=self.session, windows=self.windows,
            native_input=self.input, capture=self.capture, runtime=self.runtime, images=self.images,
            **({"window_catalog": lambda: []} if probe_output else {}))
        self.info = ClientInfoService(self.sandbox, fetch=info_fetch)
        self.info.setProviders(session=self.session, resources=self.resources, input=self.input)
        self.engine = QQmlApplicationEngine()
        self.engine.addImageProvider("live", self.images)
        self.warnings = []
        self.engine.warnings.connect(lambda rows: self.warnings.extend(str(row) for row in rows))
        context = self.engine.rootContext()
        for key, value in dict(backend=self.backend, resourceModel=self.backend.model, routeChoices=self.route_choices,
            settingsService=self.settings, resourceService=self.resources, session=self.session,
            windows=self.windows, clientCoordinator=self.coordinator, infoService=self.info).items():
            context.setContextProperty(key, value)
        for name, file in (("main", "Client.qml"), ("controls", "ClientControls.qml"),
                           ("reference", "ClientReference.qml"), ("aligned", "ClientAligned.qml"),
                           ("replay", "Overlay.qml")):
            before = len(self.engine.rootObjects())
            self.engine.load(QUrl.fromLocalFile(str(HERE / file)))
            if len(self.engine.rootObjects()) != before + 1:
                raise RuntimeError(f"Cannot load Qt surface: {file}: " + " | ".join(self.warnings[-8:]))
            window = self.engine.rootObjects()[-1]
            self.windows.bindWindow(name, window)
        self.main = self.windows._windows["main"]
        self.coordinator.quitRequested.connect(app.quit)
        self._closed = False
        self._probe_output = probe_output
        self._initialized = False
        self._initialize_timer = QTimer(app)
        self._initialize_timer.setInterval(120)
        self._initialize_timer.timeout.connect(self._initialize_resources)
        self._initialize_timer.start()

    def _initialize_resources(self):
        if (self.resources.busy or self.resources.pendingKind or self.session.busy
                or self.session.snapshot.get("resourceLocked")):
            return
        self._initialize_timer.stop()
        self._initialized = True
        self.session.dispatch("inspect_engine_resources")

    def _schedule_resource_preparation(self):
        self._initialized = False
        self._initialize_timer.start()

    def shutdown(self):
        if self._closed:
            return
        self._closed = True
        self._initialize_timer.stop()
        self.coordinator.shutdown()
        self.session.shutdown()
        self.resources.shutdown()
        self.settings.shutdown()
        self.backend.shutdown()
        self.route_choices.shutdown()
        self.info.shutdown()
        self.windows.shutdown()


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
    app = QGuiApplication([])
    app.setOrganizationName("XingtuQtClientTest")
    app.setApplicationName("XingtuQtClientTest")
    app.setFont(QFont("Microsoft YaHei UI", 10))
    if args.probe_output and Path("C:/Windows/Fonts/msyh.ttc").is_file():
        QFontDatabase.addApplicationFont("C:/Windows/Fonts/msyh.ttc")
    args.sandbox = args.sandbox.resolve()
    holder = {}
    def open_client(sandbox):
        try:
            local_manifest = (lambda url: dict(schemaVersion=1, platform="windows", version="1.0.5",
                              versionCode=9, releaseNotes="Local probe", publishedAt="2026-10-30", releases=[])) if args.probe_output else None
            client = holder["client"] = QtClient(app, sandbox, probe_output=args.probe_output, info_fetch=local_manifest)
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
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
