"""Real QtClient save/restart and timer wiring, with synthetic engine/native IO.

Each scenario runs in an isolated process and owned temporary installation. Only
native input, capture, heavy engine construction and explicit detector evidence
are fixtures. Settings, coordinator, session, runtime, S0 geometry validation,
ImportedEngineFactories caching/preparation and QTimer connections are real.
Real exported bases satisfy the mode-selection prerequisite. This lifecycle
fixture replaces heavy S0 engines and deliberately refuses S1 engine bindings;
that injected preparation failure is distinct from an absent imported base.
Display-only home/gallery metadata remains an empty fixture, as before these
tests had imported bases; queued Session and mode-selection state stay real.
It does not establish imported-package decoder or live readiness.
"""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("QT_QUICK_BACKEND", "software")


class SeasonPreparationTests(unittest.TestCase):
    def run_scenario(self, scenario):
        from client_paths import WORK_ROOT
        WORK_ROOT.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(prefix="qt-season-lifecycle-", dir=WORK_ROOT) as directory:
            root = Path(directory)
            (root / ".qt-lab-owner").write_text("qt-quick-isolated-v1", encoding="utf-8")
            seed_lifecycle_bases(root)
            # A separate process really saves S1 and shuts down before restart.
            stages = ("save-s1", scenario) if scenario == "cold-restart" else (scenario,)
            for stage in stages:
                result = subprocess.run([sys.executable, str(Path(__file__).resolve()),
                    "--scenario", stage, str(root)], env=dict(os.environ),
                    capture_output=True, text=True, timeout=30)
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                evidence = json.loads(result.stdout.strip().splitlines()[-1])
                self.assertTrue(evidence["closed"], evidence)
        return evidence

    def test_startup_availability_scan_is_not_repeated_by_opening_resource_page(self):
        self.run_scenario("lazy-inventory")

    def test_saved_s1_cold_restart_switch_to_s0_prepares_and_allows_start(self):
        self.run_scenario("cold-restart")

    def test_resource_change_during_s1_reprepares_s0_when_selected(self):
        self.run_scenario("resources-changed")

    def test_resource_change_overrides_old_context_only_preparation(self):
        self.run_scenario("resources-changed-cached")

    def test_rapid_season_switches_do_not_publish_s0_readiness_to_s1(self):
        self.run_scenario("rapid-switch")

    def test_cancelled_late_s0_preparation_never_publishes_to_s1(self):
        self.run_scenario("cancelled-late")

    def test_prepare_exception_is_not_retried_forever(self):
        self.run_scenario("prepare-error")

    def test_failed_preparation_does_not_loop_and_close_does_not_reschedule(self):
        self.run_scenario("failed-close")


class DeferredPreparationDispatchTests(unittest.TestCase):
    def test_only_context_preparation_may_skip_a_matching_prepared_snapshot(self):
        from types import SimpleNamespace
        from unittest.mock import Mock
        from client import QtClient
        for context_only, report, dispatch_expected in (
                (False, dict(season="s0", mode="nightmare", prepared=True), True),
                (True, dict(season="s0", mode="nightmare", prepared=True), False),
                (True, dict(season="s0", mode="nightmare", prepared=False), True),
                (True, dict(season="s1", mode="nightmare", prepared=True), True),
                (True, dict(season="s0", mode="difficult", prepared=True), True),
                (True, {}, True)):
            with self.subTest(context_only=context_only, report=report):
                session = SimpleNamespace(busy=False, appliedSeason="s0", appliedMode="nightmare",
                    snapshot=dict(savedFrameCapabilities=dict(nightmare=report)), dispatch=Mock())
                client = SimpleNamespace(_closed=False, coordinator=SimpleNamespace(closing=False),
                    resources=SimpleNamespace(busy=False, pendingKind=""), session=session,
                    _initialize_timer=Mock(), _preparation_context_only=context_only)
                QtClient._initialize_resources(client)
                client._initialize_timer.stop.assert_called_once_with()
                if dispatch_expected:
                    session.dispatch.assert_called_once_with("inspect_engine_resources")
                else:
                    session.dispatch.assert_not_called()


def seed_lifecycle_bases(root):
    """Real package data is a precondition; engine faults remain controlled."""
    from uuid import uuid4
    from client_paths import RuntimePaths
    from client_imported_providers import SUPPORTED_CAPABILITIES
    from imported_resource_activation import activate_catalog_locked
    from imported_resource_archive import save_archive
    from imported_resource_selection import inspect_selection
    from resource_transaction import resource_write_lock
    import test_imported_native_base as s0_fixture
    from test_export_s0_difficult_native_base_package import S0DifficultSourceFixture
    from test_export_s1_native_base_package import S1NativeBaseSourceFixture

    resource_root = RuntimePaths(root, False).imported_resource_root
    contexts = {}
    def add(archive, season, mode):
        saved = save_archive(archive, resource_root)
        refs = {ref.component_id: ref for ref in saved.component_refs}
        def wire(ref):
            return dict(packageId=ref.package_id, resourceVersion=ref.resource_version,
                        componentId=ref.component_id, manifestSha256=ref.manifest_sha256)
        contexts[f'{season}:{mode}'] = dict(recognitionBase=wire(refs['base']),
                                           controls=wire(refs['controls']), reviewedGroups=None)
    s0_fixture.ImportedNativeBaseTests.setUpClass()
    try:
        add(s0_fixture.ImportedNativeBaseTests.archive, 's0', 'nightmare')
    finally:
        s0_fixture.ImportedNativeBaseTests.doClassCleanups()
    difficult = S0DifficultSourceFixture(root / 'fixture-inputs' / 's0-difficult')
    difficult.export()
    add(difficult.output, 's0', 'difficult')
    nightmare = S1NativeBaseSourceFixture(root / 'fixture-inputs' / 's1-nightmare', mode='nightmare')
    nightmare.export()
    add(nightmare.output, 's1', 'nightmare')
    document = dict(schema='xingtu-resource-catalog-v1', generation=str(uuid4()), contexts=contexts, displays=[])
    # Fixture setup publishes an inspected selection. Product import/decoding
    # is covered separately; these scenarios replace the heavy engine builder.
    with resource_write_lock(resource_root):
        activate_catalog_locked(resource_root, document, expected_catalog_stamp=None,
            validate=lambda proposed: inspect_selection(resource_root, proposed,
                supported_capabilities=SUPPORTED_CAPABILITIES))


def scenario(name, root):
    queued_preparation = name == "context-superseded-queued-preparation"
    if queued_preparation:
        name = "context-superseded"
    from PySide6.QtCore import QTimer
    from PySide6.QtGui import QGuiApplication
    from PySide6.QtTest import QTest
    from client import QtClient
    from client_providers import EngineFactories, ProviderResourceBinding
    from client_imported_providers import ImportedEngineFactories, ImportedProviderUnavailable
    from client_input import NativeShortcutBackend
    from season_context import SeasonContext
    from test_client_input import Native
    from test_client_live_runtime import FixtureCapture, fixture_engine

    app = QGuiApplication.instance() or QGuiApplication([])
    calls, builds = [], []
    entered, release = threading.Event(), threading.Event()
    from client_imported_resources import ImportedResourceService
    from client_imported_browse import load_imported_browse_context
    from imported_resource_catalog import ResourceResolver
    empty_display_resolver = ResourceResolver(root / 'fixture-empty-display')
    def display_metadata(_source, mode, *, season='s0', cancelled=lambda: False, **kwargs):
        return load_imported_browse_context(empty_display_resolver, mode,
                                           season=season, cancelled=cancelled)
    inventory_reads = []
    original_scan = ImportedResourceService._scan
    def scan(service, verify):
        inventory_reads.append(True)
        return original_scan(service, verify)
    original_prepare = EngineFactories.prepare_context
    def prepare(self, mode, cancelled=None, *, season="s0"):
        calls.append(season)
        if name == "prepare-error":
            raise RuntimeError("controlled-prepare-error")
        return original_prepare(self, mode, cancelled, season=season)
    def build(self, mode, *, season="s0"):
        assert season == "s0", "The controlled S1 binding failure reached the engine builder"
        builds.append(mode)
        if name == "cancelled-late" and len(builds) == 1:
            entered.set()
            if not release.wait(6):
                raise RuntimeError("test did not release blocked engine construction")
        if name == "failed-close":
            raise ValueError("controlled-invalid-resource")
        return fixture_engine(mode)

    class LifecycleFactories(ImportedEngineFactories):
        def _resource_binding(self, mode, season="s0"):
            if season != "s0":
                raise ImportedProviderUnavailable("fixture-s1-unavailable", ("fixture-s1-unavailable",))
            return ProviderResourceBinding(SeasonContext(season=season, mode=mode), self.root)

        _build = build

    def wait(predicate, label, seconds=5):
        deadline = time.monotonic() + seconds
        while not predicate() and time.monotonic() < deadline:
            app.processEvents()
            time.sleep(.002)
        assert predicate(), f"{label}: {client.session.snapshot}; preparations={calls}"

    def idle():
        return (not client.settings.busy and not client.session.busy and not client.resources.busy
                and not client.session.snapshot.get("resourceLocked")
                and not client.coordinator._pending_context)

    def select(season):
        wait(idle, "before season save")
        client.coordinator.requestGameSeason(season)
        wait(lambda: idle() and client.session.appliedSeason == season, "saved context applied")
        assert json.loads(client.settings.path.read_text())["gameSeason"] == season

    def ready():
        return idle() and "start_round" in client.session.snapshot["availableCommands"]

    capture = FixtureCapture()
    capture.is_current = lambda *args, **kwargs: True
    client = None
    try:
        with patch("client_input.NativeShortcutBackend", side_effect=lambda *a, **k: NativeShortcutBackend(Native())), \
             patch("client_capture.ClientCaptureProvider", return_value=capture), \
             patch("client_coordinator.another_assistant_running", return_value=False), \
             patch("client_imported_providers.build_imported_engine_factories", side_effect=LifecycleFactories) as factory, \
             patch('client_imported_browse.load_imported_browse_context', side_effect=display_metadata), \
             patch.object(EngineFactories, "prepare_context", prepare), \
             patch.object(ImportedResourceService, "_scan", scan):
            client = QtClient(app, root, probe_output=root / "unused.json", info_fetch=lambda *a, **k: {})
            factory.assert_called_once_with(client.resource_resolver, client.paths)
            if name == "cancelled-late":
                wait(entered.is_set, "startup entered controlled build")
                wait(lambda: not client.resources.busy and client.resources.contextAvailable('s1', 'nightmare'),
                     "imported bases become selectable while engine preparation is blocked")
                # Save through the public coordinator while the real startup
                # session worker is blocked. No production callback is invoked.
                client.coordinator.requestGameSeason("s1")
                wait(lambda: client.session.snapshot.get("contextPending") and not client.settings.busy,
                     "saved S1 waits behind cancelled S0 construction")
                assert not release.is_set()
                assert "start_round" not in client.session.snapshot.get("availableCommands", [])
                QTimer.singleShot(40, release.set)
                wait(lambda: idle() and client.session._pool.activeThreadCount() == 0,
                     "cancelled S0 completion drained")
                state = client.session.snapshot
                assert state["season"] == "s1", state
                assert not state["savedFrameCapabilities"]["nightmare"]["available"], state
                assert "start_round" not in state["availableCommands"], state
                assert state.get("lastReceipt", {}).get("requestContext", {}).get("season", "s1") == "s1", state
                select("s0")
                wait(ready, "return after cancellation prepares remaining S0 modes")
                client.coordinator.requestGameMode("difficult")
                wait(lambda: idle() and client.session.appliedMode == "difficult", "apply remaining mode")
                wait(ready, "cancelled preparation must resume when its unprepared mode is selected")
            else:
                wait(lambda: idle() and bool(calls), "startup timer fired")
            if name in ("context-error", "context-superseded"):
                from test_client_manual_map import qml_item
                wait(ready, "initial S0 ready")
                home = qml_item(client.main, "homeGameContext")
                from PySide6.QtCore import QPoint, QPointF, Qt
                def choose_home_season(season):
                    for object_name in ("homeGameContextSeason", "homeGameContextSeasonOption_" + season):
                        # Popup delegates are created/polished asynchronously.
                        # Runtime idle does not imply that a QML item is ready
                        # for another mouse click (notably after a context retry).
                        def clickable():
                            item = qml_item(client.main, object_name)
                            return (item is not None and item.isVisible() and item.isEnabled()
                                    and item.width() > 0 and item.height() > 0)
                        wait(clickable, "clickable QML control " + object_name)
                        item = qml_item(client.main, object_name)
                        point = item.mapToScene(QPointF(item.width() / 2, item.height() / 2))
                        QTest.mouseClick(client.main, Qt.LeftButton, Qt.NoModifier,
                                         QPoint(round(point.x()), round(point.y())))
                        QTest.qWait(20)
                picker = qml_item(client.main, "homeGameContextSeason")
                original_dispatch = client.runtime.dispatch
                attempts = []
                def dispatch(action, payload, cancelled):
                    if action == "set_context":
                        attempts.append(dict(payload))
                        if len(attempts) in (1, 4):
                            entered.set()
                            if not release.wait(6):
                                raise RuntimeError("test did not release context")
                            if name != "context-superseded" or len(attempts) == 4:
                                raise RuntimeError("controlled-context-failure")
                    return original_dispatch(action, payload, cancelled)
                client.runtime.dispatch = dispatch
                choose_home_season("s1")
                wait(entered.is_set, "context worker entered")
                assert home.property("season") == "s0", home.property("season")
                assert picker.property("displayText") == "S0 · 厄运之女"
                assert not home.property("enabled")
                assert "正在切换" in client.coordinator.runtimeStatus
                assert "尚未登记" not in client.coordinator.runtimeStatus
                assert client.session.appliedSeason == "s0"
                evidence_dir = os.environ.get("QT_CONTEXT_EVIDENCE_DIR") if name == "context-error" else None
                if evidence_dir:
                    Path(evidence_dir).mkdir(parents=True, exist_ok=True)
                    QTest.qWait(100)
                    assert client.main.grabWindow().save(str(Path(evidence_dir) / "context-pending.png"))
                if name == "context-superseded":
                    client.coordinator._native_action("end-round")
                release.set()
                wait(idle, "failed context clears coordinator pending")
                QTest.qWait(500)
                assert len(attempts) == 1, attempts
                assert home.property("season") == "s0"
                assert home.property("enabled")
                assert picker.property("displayText") == "S0 · 厄运之女"
                assert not client.session.snapshot.get("contextPending")
                if name == "context-error":
                    assert "controlled-context-failure" in client.session.message
                assert json.loads(client.settings.path.read_text())["gameSeason"] == "s1"
                # A real failed Qt context change leaves the saved default ahead
                # of the runtime. Exercise _observe, not just the helper guard.
                import numpy as np
                assert client.runtime._season == "s0"
                assert client.runtime._settings["gameSeason"] == "s1"
                frame = np.zeros((720, 1280, 3), np.uint8)
                for mode in ("nightmare", "difficult"):
                    engine = fixture_engine(mode)
                    order = [2, 1, -1] if mode == "nightmare" else [2, 1]
                    # Stub only detector evidence. The real S0 composition,
                    # applied-season override and geometry binder still run.
                    evidence = dict(selectedFloor=1, reason="fixture-signed-column",
                        controlAxis="vertical", observedOrder=order,
                        tileBounds=[.80, .28, .04, .04],
                        centerPoints=[[.82, .25 + i * .05] for i in range(len(order))],
                        labelEvidence=[dict(floor=floor, negative=floor < 0) for floor in order])
                    with patch("floor_control_region.locate_vertical_floor_controls", return_value=evidence) as locate, \
                         patch("map_interface_observation.detect_map_interface", return_value=dict(detected=True)):
                        observation, floor, reading = client.runtime._observe(engine, frame)
                        assert locate.call_count == 1, locate.call_args_list
                        args, kwargs = locate.call_args
                        assert args[0] is frame and args[1] == mode and kwargs == {"season": "s0", "controls": None}
                        assert floor == "1", (mode, reading)
                        assert reading["floorSource"] == "fixed", (mode, reading)
                        assert reading["season"] == "s0" and reading["mode"] == mode
                        assert observation["mapOpen"] is True
                        assert reading["controlGeometryVerified"] is True, (mode, reading)
                        assert [row["floor"] for row in reading["controlRects"]] == order
                    # A selected-floor scalar alone is no longer enough. Nor
                    # may valid labels authorize geometry without map proof.
                    for detector_read, map_open in ((dict(evidence, labelEvidence=[]), True),
                                                    (evidence, False)):
                        with patch("floor_control_region.locate_vertical_floor_controls", return_value=detector_read), \
                             patch("map_interface_observation.detect_map_interface", return_value=dict(detected=map_open)):
                            _, floor, reading = client.runtime._observe(engine, frame)
                            assert floor is None, (mode, reading)
                            assert reading["selectedFloor"] is None, (mode, reading)
                            assert reading["controlGeometryVerified"] is False, (mode, reading)
                            assert "controlRects" not in reading, (mode, reading)
                            assert reading["reason"] == "vertical-control-geometry-unverified", (mode, reading)
                    # With no detector stub, a black frame must remain unread,
                    # even when independent map-open proof is supplied.
                    with patch("map_interface_observation.detect_map_interface", return_value=dict(detected=True)):
                        _, floor, reading = client.runtime._observe(engine, frame)
                        assert floor is None and reading["selectedFloor"] is None, (mode, reading)
                        assert reading["controlGeometryVerified"] is False, (mode, reading)
                        assert "controlRects" not in reading, (mode, reading)
                        assert reading["reason"] == "vertical-control-geometry-unverified", (mode, reading)
                assert client.runtime._season == "s0"
                assert client.runtime._settings["gameSeason"] == "s1"
                if evidence_dir:
                    assert client.main.grabWindow().save(str(Path(evidence_dir) / "context-failed.png"))
                # Same persisted target must be explicitly retryable without
                # rewriting unrelated draft settings or rolling defaults back.
                if queued_preparation:
                    # Keep the genuine S1 preparation queued across the return
                    # to cached S0, then expire it during a real open popup.
                    client._initialize_timer.setInterval(10000)
                choose_home_season("s1")
                wait(lambda: idle() and client.session.appliedSeason == "s1", "explicit retry")
                assert len(attempts) == 2, attempts
                assert home.property("season") == "s1"
                select("s0")
                wait(ready, "return to actual S0")
                if queued_preparation:
                    # Force the real queued-work interleaving that used to close
                    # the third popup after its header click. No widget state or
                    # runtime callback is replaced; the production timer and
                    # dispatch decision run normally, without a fake worker.
                    assert client._initialize_timer.isActive(), "S1 preparation must still be queued"
                    preparation_count = len(calls)
                    # Observe the already-created popup, without opening it by
                    # API. The timer must expire while the real header click's
                    # popup is open, and leave it open for the option click.
                    from PySide6.QtCore import QObject
                    popup = next(child for child in picker.findChildren(QObject)
                        if "QQuickPopup" in child.metaObject().className()
                        and child.metaObject().indexOfProperty("opened") >= 0)
                    popup_at_timeout = []
                    client._initialize_timer.timeout.connect(
                        lambda: popup_at_timeout.append(popup.property("opened")))
                    client._initialize_timer.start(0)
                # A subsequent refusal also allows selecting the already
                # running S0, correcting the saved default without a fake switch.
                choose_home_season("s1")
                if queued_preparation:
                    assert len(calls) == preparation_count, (preparation_count, calls)
                    assert popup_at_timeout == [True], popup_at_timeout
                wait(lambda: idle() and len(attempts) == 4, "second failed S1 attempt")
                assert client.session.appliedSeason == "s0"
                choose_home_season("s0")
                wait(lambda: idle() and client.settings.persistedValues["gameSeason"] == "s0",
                     "choose actual context after failure")
            elif name == "lazy-inventory":
                wait(ready, "current mode ready after startup availability scan")
                assert inventory_reads == [True], inventory_reads
                client.main.setProperty("page", "resources")
                QTest.qWait(350)
                assert idle()
                assert len(inventory_reads) == 1, inventory_reads
                client.main.setProperty("page", "resources")
                QTest.qWait(350)
                assert len(inventory_reads) == 1, inventory_reads
                client.main.setProperty("page", "home")
                QTest.qWait(200)
                assert len(inventory_reads) == 1, inventory_reads
            elif name == "save-s1":
                select("s1")
            elif name == "cold-restart":
                assert client.session.appliedSeason == "s1"
                assert builds == [], builds
                assert not client.providers.capabilities("s0")["nightmare"]["prepared"]
                select("s0")
                wait(ready, "S0 must prepare automatically after a real saved S1 restart")
                client.coordinator.startRound()
                wait(lambda: not client.session.busy, "start round")
                assert client.session.snapshot["lastReceipt"]["success"], client.session.snapshot
                assert client.session.snapshot["roundPreparing"]
            elif name == "resources-changed-cached":
                wait(ready, "initial S0 ready")
                client._initialize_timer.setInterval(10000)
                select("s1")
                select("s0")
                wait(ready, "cached S0 ready before explicit resource change")
                assert client._initialize_timer.isActive()
                count = len(calls)
                client.resources.resourcesChanged.emit()
                client._initialize_timer.start(0)
                wait(lambda: ready() and not client._initialize_timer.isActive()
                     and len(calls) > count, "explicit invalidation still inspects cached context")
                assert len(calls) == count + 1, (count, calls)
                assert len(builds) == 2, builds
            elif name == "resources-changed":
                wait(ready, "initial S0 ready")
                select("s1")
                client.resources.resourcesChanged.emit()
                wait(lambda: idle() and not client._initialize_timer.isActive(), "S1 resource refresh")
                assert not client.providers.capabilities("s0")["nightmare"]["prepared"]
                assert "start_round" not in client.session.snapshot["availableCommands"]
                select("s0")
                wait(ready, "invalidated S0 must prepare on return")
                assert len(builds) == 2, builds
            elif name == "rapid-switch":
                wait(ready, "initial S0 ready")
                for _ in range(3):
                    select("s1")
                    state = client.session.snapshot
                    assert not state["savedFrameCapabilities"]["nightmare"]["available"], state
                    assert "start_round" not in state["availableCommands"], state
                    select("s0")
                    wait(ready, "rapid S0 return")
                # A preparation timer queued while S1 was selected may still
                # be pending. Drain it before checking that idle does not retry;
                # it must not inspect an already-prepared cached S0 context.
                wait(lambda: idle() and not client._initialize_timer.isActive(),
                     "queued preparation drained after rapid S0 return")
                count = len(calls)
                QTest.qWait(500)
                assert len(calls) == count, (count, calls)
                assert len(builds) == 1, builds
            elif name == "prepare-error":
                QTest.qWait(700)
                assert len(calls) == 1, ("preparation exception must not spin", calls)
                assert not client.providers.capabilities()["nightmare"]["prepared"]
                assert "start_round" not in client.session.snapshot["availableCommands"]
                select("s1")
                select("s0")
                wait(lambda: idle() and len(calls) == 2, "new context permits a bounded retry")
                QTest.qWait(400)
                assert len(calls) == 2, calls
                client.resources.resourcesChanged.emit()
                wait(lambda: idle() and len(calls) == 3, "explicit resource change permits retry")
                QTest.qWait(400)
                assert len(calls) == 3, calls
            elif name == "failed-close":
                wait(lambda: idle() and client.providers.capabilities()[client.session.appliedMode]["prepared"], "failed preparation complete")
                assert "start_round" not in client.session.snapshot["availableCommands"]
                # A preparation timer queued while S1 was selected may still
                # be pending. Drain it before checking that idle does not retry;
                # it must not inspect an already-prepared cached S0 context.
                wait(lambda: idle() and not client._initialize_timer.isActive(),
                     "queued preparation drained after rapid S0 return")
                count = len(calls)
                QTest.qWait(500)
                assert len(calls) == count == 1, calls
                assert len(builds) == 1, builds
            before_close = len(calls)
            client.coordinator.requestClose()
            client.resources.resourcesChanged.emit()
            client.session.changed.emit()
            QTest.qWait(300)
            assert len(calls) == before_close, calls
            client.shutdown()
            before_close = len(calls)
            client.resources.resourcesChanged.emit()
            client.session.changed.emit()
            QTest.qWait(300)
            assert not client._initialize_timer.isActive()
            assert len(calls) == before_close, calls
            print(json.dumps(dict(scenario=name, closed=client._closed, preparations=calls, builds=builds)))
    finally:
        release.set()
        if client is not None:
            client.shutdown()


if __name__ == "__main__":
    if "--scenario" in sys.argv:
        scenario(sys.argv[2], Path(sys.argv[3]))
    else:
        unittest.main()
