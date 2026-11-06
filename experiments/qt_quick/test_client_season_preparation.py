"""Real QtClient save/restart and timer wiring, with synthetic engine/native IO.

Each scenario runs in an isolated process and owned temporary installation. Only
native input, capture and heavy engine construction are fixtures. Settings,
coordinator, session, runtime, EngineFactories and QTimer connections are real.
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

    def test_hidden_resource_page_does_not_scan_until_opened(self):
        self.run_scenario("lazy-inventory")

    def test_saved_s1_cold_restart_switch_to_s0_prepares_and_allows_start(self):
        self.run_scenario("cold-restart")

    def test_resource_change_during_s1_reprepares_s0_when_selected(self):
        self.run_scenario("resources-changed")

    def test_rapid_season_switches_do_not_publish_s0_readiness_to_s1(self):
        self.run_scenario("rapid-switch")

    def test_cancelled_late_s0_preparation_never_publishes_to_s1(self):
        self.run_scenario("cancelled-late")

    def test_prepare_exception_is_not_retried_forever(self):
        self.run_scenario("prepare-error")

    def test_failed_preparation_does_not_loop_and_close_does_not_reschedule(self):
        self.run_scenario("failed-close")


def scenario(name, root):
    from PySide6.QtCore import QTimer
    from PySide6.QtGui import QGuiApplication
    from PySide6.QtTest import QTest
    from client import QtClient
    from client_providers import EngineFactories
    from test_client_input import Native
    from test_client_live_runtime import FixtureCapture, fixture_engine

    app = QGuiApplication.instance() or QGuiApplication([])
    calls, builds = [], []
    entered, release = threading.Event(), threading.Event()
    from client_resources import ResourceService
    inventory_reads = []
    original_snapshot = ResourceService._declarations
    def snapshot(service, cancel):
        inventory_reads.append(True)
        return original_snapshot(service, cancel)
    original_prepare = EngineFactories.prepare_context
    def prepare(self, mode, cancelled=None, *, season="s0"):
        calls.append(season)
        if name == "prepare-error":
            raise RuntimeError("controlled-prepare-error")
        return original_prepare(self, mode, cancelled, season=season)
    def build(self, mode, *, season="s0"):
        assert season == "s0", "Unregistered season reached the resource builder"
        builds.append(mode)
        if name == "cancelled-late" and len(builds) == 1:
            entered.set()
            if not release.wait(6):
                raise RuntimeError("test did not release blocked engine construction")
        if name == "failed-close":
            raise ValueError("controlled-invalid-resource")
        return fixture_engine(mode)

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
        with patch("client_input.NativeWindowAPI", return_value=Native()), \
             patch("client_capture.ClientCaptureProvider", return_value=capture), \
             patch("client_coordinator.another_assistant_running", return_value=False), \
             patch.object(EngineFactories, "_build", build), \
             patch.object(EngineFactories, "prepare_context", prepare), \
             patch.object(ResourceService, "_declarations", snapshot):
            client = QtClient(app, root, probe_output=root / "unused.json", info_fetch=lambda *a, **k: {})
            if name == "cancelled-late":
                wait(entered.is_set, "startup entered controlled build")
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
                with patch("map_interface_observation.detect_selected_floor", return_value=dict(selectedFloor=1)), \
                     patch("map_interface_observation.detect_map_interface", return_value=dict(detected=True)):
                    for mode in ("nightmare", "difficult"):
                        _, floor, reading = client.runtime._observe(fixture_engine(mode), np.zeros((720, 1280, 3), np.uint8))
                        assert floor == "1", (mode, reading)
                        assert reading["floorSource"] == "fixed", (mode, reading)
                if evidence_dir:
                    assert client.main.grabWindow().save(str(Path(evidence_dir) / "context-failed.png"))
                # Same persisted target must be explicitly retryable without
                # rewriting unrelated draft settings or rolling defaults back.
                choose_home_season("s1")
                wait(lambda: idle() and client.session.appliedSeason == "s1", "explicit retry")
                assert len(attempts) == 2, attempts
                assert home.property("season") == "s1"
                select("s0")
                wait(ready, "return to actual S0")
                # A subsequent refusal also allows selecting the already
                # running S0, correcting the saved default without a fake switch.
                choose_home_season("s1")
                wait(lambda: idle() and len(attempts) == 4, "second failed S1 attempt")
                assert client.session.appliedSeason == "s0"
                choose_home_season("s0")
                wait(lambda: idle() and client.settings.persistedValues["gameSeason"] == "s0",
                     "choose actual context after failure")
            elif name == "lazy-inventory":
                wait(ready, "current mode ready without management scan")
                assert inventory_reads == [], inventory_reads
                client.main.setProperty("page", "resources")
                wait(lambda: bool(inventory_reads) and idle() and not client.package_manager.busy,
                     "opening management page triggers inventory")
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
                # inspect the now-cached S0 context once. Drain that bounded
                # scheduled work before testing that idle does not retry.
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
                # inspect the now-cached S0 context once. Drain that bounded
                # scheduled work before testing that idle does not retry.
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
