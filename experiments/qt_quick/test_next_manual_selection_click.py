"""Actual QtClient picker clicks -> Session worker; synthetic capture/fit only.

QTest delivers events to offscreen QQuickWindows, never to the system mouse.
The diagnostic material ACK is explicit: synthetic pixels cannot pass the
Coordinator's production live-capture gate. Saved-frame fit evidence is separate.
"""
import json
import os
from pathlib import Path
import subprocess
import sys
import unittest


def scenario():
    import tempfile
    import threading
    import time
    from types import SimpleNamespace
    from unittest.mock import Mock, patch
    from PySide6.QtCore import QPointF, Qt
    from PySide6.QtGui import QGuiApplication, QImage
    from PySide6.QtTest import QTest
    from client import QtClient
    from client_paths import WORK_ROOT
    from client_theme import apply_light_theme
    import test_client_live_runtime as fixtures

    app = QGuiApplication([])
    app.setQuitOnLastWindowClosed(False)
    apply_light_theme(app)
    fx = fixtures.LiveRuntimeContracts()
    fx.setUp()
    engine, runtime = fx.engine, fx.runtime
    engine.pack.default_entry_floor = "1"
    engine.pack.maps[0].name = "路线甲 / 原地图"
    for map_id, name in (("second-map", "路线甲 / 目标地图"), ("other-map", "路线乙 / 另一地图")):
        item = SimpleNamespace(id=map_id, name=name)
        engine.pack.maps.append(item)
        engine.pack.by_id[map_id] = item
    engine.rank = Mock(side_effect=AssertionError("Manual picker must not rank identity"))
    worker_calls, clicks = [], []
    original = runtime.dispatch

    def dispatch(action, *args, **kwargs):
        worker_calls.append((action, threading.get_ident()))
        return original(action, *args, **kwargs)

    runtime.dispatch = dispatch
    client = None
    result = dict(evidenceKind="synthetic-offscreen", materialAck="explicit-diagnostic-only")

    def until(condition, label, timeout=6):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            app.processEvents()
            if condition():
                return
            time.sleep(.003)
        raise AssertionError(label + ": " + str(client.session.snapshot if client else "not initialized"))

    def find(window, name):
        pending = [window.contentItem()]
        while pending:
            item = pending.pop()
            if (item.objectName() == name or name == "选择本局地图"
                    and "MenuItem" in item.metaObject().className() and item.property("text") == name):
                return item
            pending.extend(item.childItems())
        raise AssertionError("Missing actual QML control: " + name)

    def click(window, name):
        QTest.qWait(40)
        item = find(window, name)
        assert window.isVisible() and item.isVisible() and item.isEnabled(), name
        point = item.mapToScene(QPointF(item.width()/2, item.height()/2)).toPoint()
        assert 0 <= point.x() < window.width() and 0 <= point.y() < window.height(), (name, point)
        QTest.mouseClick(window, Qt.LeftButton, Qt.NoModifier, point)
        clicks.append(name)
        app.processEvents()

    def open_picker(controls):
        click(controls, "controlMoreButton")
        click(controls, "选择本局地图")

    def drive(action):
        client.session.dispatch(action)
        until(lambda: not client.session.busy and not client.session._jobs, action)
        receipt = client.session.snapshot["lastReceipt"]
        assert receipt["action"] == action, receipt
        return receipt

    WORK_ROOT.mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="manual-click-", dir=WORK_ROOT) as folder, \
            patch("urllib.request.OpenerDirector.open", side_effect=AssertionError("No network")) as network, \
            patch("client_native_keys.PassThroughKeyboardSource.start", side_effect=AssertionError("No native listener")) as native, \
            patch("client_live_runtime.LiveRuntime", return_value=runtime), \
            patch("client_capture.ClientCaptureProvider", return_value=fx.capture):
        sandbox = Path(folder)
        (sandbox / ".qt-lab-owner").write_text("qt-quick-isolated-v1", encoding="utf-8")
        (sandbox / "_internal").mkdir()
        (sandbox / "ui-settings.json").write_text(json.dumps(dict(mapSelectionMode="manual",
            gameMode="nightmare", routeProfileId="fixture", modeRouteProfiles={"nightmare": "fixture"})), encoding="utf-8")
        fake_info = lambda url: dict(schemaVersion=1, platform="windows", version="1.0.5",
            versionCode=9, releaseNotes="Synthetic picker fixture", publishedAt="2026-10-30", releases=[])
        try:
            client = QtClient(app, sandbox, probe_output=sandbox/"probe", info_fetch=fake_info)
            until(lambda: client._initialized and not client.session.busy and not client.settings.busy,
                  "QtClient initialization")
            assert drive("start_round")["success"]
            assert drive("capture_now")["reason"] == "manual-map-selection-required"
            until(lambda: client.manual_selection.snapshot["canOpen"], "Picker enabled")
            controls = client.windows._windows["controls"]
            manual = client.windows._windows["manual"]
            client.windows.show("controls")
            open_picker(controls)
            until(manual.isVisible, "Actual manual window opens")
            assert client.manual_selection.snapshot["stage"] == "groups"
            click(manual, "manualPickerOption_0")
            assert client.manual_selection.snapshot["stage"] == "maps"
            assert client.manual_selection.snapshot["options"][1]["mapId"] == "second-map"
            click(manual, "manualPickerOption_1")
            until(lambda: not manual.isVisible() and not client.session.busy and not client.session._jobs,
                  "Member request finishes and picker closes")
            receipt = client.session.snapshot["lastReceipt"]
            assert receipt["action"] == "select_manual_map" and receipt["success"], receipt
            assert client.session.snapshot["desiredManualMapId"] == "second-map"
            assert client.session.snapshot["mapId"] == "", "Identity committed before material ACK"
            token = receipt["pendingSiblingToken"]
            pending = runtime.pending_sibling_payload(token)
            assert pending and not pending["liveAuthorization"]
            assert (pending["mapId"], pending["floor"]) == ("second-map", "1")
            pixels = pending["pixels"]
            image = QImage(pixels.data, pixels.shape[1], pixels.shape[0], pixels.strides[0], QImage.Format_RGBA8888).copy()
            assert not image.isNull(), "Synthetic staged material did not decode"
            assert runtime.acknowledge_sibling(token, True)["success"]
            client.session.refreshRuntime()
            state = client.session.snapshot
            assert (state["mapId"], state["manualSelectedMapId"], state["floor"]) == ("second-map", "second-map", "1")
            for material in (runtime.render_payload(token), runtime.reference_payload()):
                assert (material["mapId"], material["floor"], material["identitySource"]) == (
                    "second-map", "1", "manual-map-selection")
            open_picker(controls)
            until(manual.isVisible, "Picker reopens after choice")
            click(manual, "manualPickerClose")
            until(lambda: not manual.isVisible(), "Close button hides picker")
            open_picker(controls)
            until(manual.isVisible, "Picker reopens after explicit close")
            click(controls, "controlRoundAction")
            until(lambda: not client.session.busy and not client.session._jobs
                  and not client.session.snapshot["roundActive"], "End-round button finishes")
            until(lambda: not manual.isVisible(), "End-round hides picker")
            state = client.session.snapshot
            assert state["lastReceipt"]["action"] == "end_round"
            assert state["manualSelectedMapId"] == state["desiredManualMapId"] == state["mapId"] == ""
            assert state["mapSelectionMode"] == "manual"
            assert not client.manual_selection.snapshot["canOpen"]
            click(controls, "controlMoreButton")
            assert not find(controls, "选择本局地图").isEnabled()
            selections = [thread for action, thread in worker_calls if action == "select_manual_map"]
            assert len(selections) == 1 and selections[0] != threading.get_ident(), worker_calls
            engine.rank.assert_not_called()
            network.assert_not_called(); native.assert_not_called()
            assert not client.warnings, client.warnings
            result.update(clicks=clicks, workerSelectionCount=len(selections), selectedMap="second-map",
                selectedFloor="1", endRoundReset=True, modePreserved=True, warnings=list(client.warnings),
                networkCalls=network.call_count, nativeStarts=native.call_count)
        finally:
            if client is not None:
                client.shutdown()
                result["shutdownCompleted"] = client._closed
                result["sessionJobsDrained"] = not client.session._jobs
                client.engine.deleteLater()
                app.processEvents()
            fx.doCleanups()
    print(json.dumps(result, ensure_ascii=True))


class ManualSelectionClickTests(unittest.TestCase):
    def test_actual_controls_picker_worker_and_round_reset(self):
        run = subprocess.run([sys.executable, str(Path(__file__).resolve()), "--scenario"],
            capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=35,
            env=dict(os.environ, QT_QPA_PLATFORM="offscreen", QT_QUICK_BACKEND="software"))
        self.assertEqual(run.returncode, 0, run.stdout + run.stderr)
        result = json.loads(run.stdout.strip().splitlines()[-1])
        self.assertTrue(result["shutdownCompleted"] and result["sessionJobsDrained"], result)
        self.assertTrue(result["endRoundReset"] and result["modePreserved"], result)
        print(json.dumps(result, ensure_ascii=True))


if __name__ == "__main__":
    if sys.argv[1:] == ["--scenario"]:
        scenario()
    else:
        unittest.main()
