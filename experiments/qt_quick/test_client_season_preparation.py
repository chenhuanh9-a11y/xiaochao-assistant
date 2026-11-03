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
    original_prepare = EngineFactories.prepare
    def prepare(self, cancelled=None, *, season="s0"):
        calls.append(season)
        if name == "prepare-error":
            raise RuntimeError("controlled-prepare-error")
        return original_prepare(self, cancelled, season=season)
    def build(self, mode):
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
             patch.object(EngineFactories, "prepare", prepare):
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
                wait(lambda: idle() and client.session.pool.activeThreadCount() == 0,
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
            if name == "save-s1":
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
                assert len(builds) == 4, builds
            elif name == "rapid-switch":
                wait(ready, "initial S0 ready")
                for _ in range(3):
                    select("s1")
                    state = client.session.snapshot
                    assert not state["savedFrameCapabilities"]["nightmare"]["available"], state
                    assert "start_round" not in state["availableCommands"], state
                    select("s0")
                    wait(ready, "rapid S0 return")
                count = len(calls)
                QTest.qWait(500)
                assert len(calls) == count, (count, calls)
                assert len(builds) == 2, builds
            elif name == "prepare-error":
                QTest.qWait(700)
                assert len(calls) == 1, ("preparation exception must not spin", calls)
                assert not client.providers.capabilities()["nightmare"]["prepared"]
                assert "start_round" not in client.session.snapshot["availableCommands"]
            elif name == "failed-close":
                wait(lambda: idle() and all(x["prepared"] for x in client.providers.capabilities().values()), "failed preparation complete")
                assert "start_round" not in client.session.snapshot["availableCommands"]
                count = len(calls)
                QTest.qWait(500)
                assert len(calls) == count == 1, calls
                assert len(builds) == 2, builds
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
