"""Real picker/settings/windows and QtClient lifecycle; only OS input/data are fixtures.

PNG inputs and settings live in temporary owned sandboxes. No game, installed
configuration, OS listener or checked-in map asset is used.
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
from s0_test_fixtures import synthetic_read
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("QT_QUICK_BACKEND", "software")

from PySide6.QtCore import QPointF, Qt
from PySide6.QtQuick import QQuickView
from PySide6.QtTest import QTest
from PySide6.QtCore import QUrl

from client_input import NativeShortcutBackend
from client_manual_map_service import ManualMapPickerService
from client_windows import WindowController
from test_client_manual_map import (APP, FakeSession, FakeSettings, Native, catalog,
                                    load_picker, qml_item, write_png)
from test_client_shortcut_delivery import FixtureSource
import test_client_settings as settings_fixtures
from test_client_settings import visual_item
from PySide6.QtCore import QCoreApplication, QEvent
from test_client_input import Native as HookNative


def wait(test, predicate):
    deadline = time.monotonic() + 5
    while not predicate() and time.monotonic() < deadline:
        APP.processEvents()
        time.sleep(.002)
    test.assertTrue(predicate(), "real Qt service/window completion timeout")


def click(window, item, button=Qt.LeftButton):
    position = item.mapToScene(QPointF(item.width()/2, item.height()/2)).toPoint()
    QTest.mouseClick(window, button, Qt.NoModifier, position)
    APP.processEvents()


def save_qa_image(image, name):
    """Optional local/CI evidence from synthetic data, never a source asset."""
    destination = os.environ.get("QT_PICKER_QA_OUTPUT")
    if destination:
        directory = Path(destination)
        directory.mkdir(parents=True, exist_ok=True)
        if not image.save(str(directory / name)):
            raise AssertionError("could not save rendered QA evidence")


def dispose_picker(service, windows, engine, component, window):
    service.shutdown()
    windows.shutdown()
    window.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
    component.deleteLater()
    engine.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)


def dispose_view(view):
    view.close()
    view.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)


class PickerInputIntegrationTests(unittest.TestCase):
    def run_selection(self, hook):
        native = HookNative() if hook else Native()
        source = FixtureSource() if hook else None
        keys = NativeShortcutBackend(native, source=source)
        self.addCleanup(keys.shutdown)
        actions, commits = [], []
        keys.actionTriggered.connect(actions.append)
        self.assertTrue(keys.configure({"map": "G", "candidate-next": "F1"}))
        keys.enableManagement(); keys.bindWindow(42)
        self.assertTrue(keys.enable())
        windows = WindowController()
        service = ManualMapPickerService(settings=FakeSettings(), windows=windows,
            input_backend=keys, session=FakeSession("difficult"),
            catalog_provider=lambda: catalog("difficult", [dict(id="n1", name="北-红门", direction="上")]),
            commit=lambda map_id: commits.append(map_id) or True)
        self.addCleanup(service.shutdown)
        engine, component, window, warnings = load_picker(service)
        self.addCleanup(dispose_picker, service, windows, engine, component, window)
        windows.bindWindow("map-picker", window)
        service.open("integration")
        wait(self, lambda: not service.busy and len(service.options) == 1 and window.isVisible())
        self.assertTrue(keys._picker_keys)
        if not hook:
            keys.pollOnce()  # Arm the newly visible picker with all keys released.

        def press(vk, down, foreground=42):
            native.foreground = foreground
            if hook:
                event = source._transition_event(vk, 0, 0, down, foreground)
                source.eventReceived.emit(event)
                APP.processEvents()
            else:
                native.states[vk] = 0x8001 if down else 0
                keys.pollOnce()

        # Neither another application nor Alt+F1 selects the real service card.
        if hook:
            native.process = 1234
        press(0x70, True, 77); press(0x70, False, 77)
        press(0xA4 if hook else 0x12, True)
        press(0x70, True); press(0x70, False)
        press(0xA4 if hook else 0x12, False)
        self.assertEqual(commits, [])
        self.assertTrue(window.isVisible())
        press(0x70, True)
        self.assertEqual(commits, ["n1"])
        self.assertFalse(service.visible)
        self.assertFalse(window.isVisible())
        self.assertFalse(windows.snapshot["map-picker"]["visible"])
        self.assertEqual(keys._picker_keys, {})
        self.assertEqual(actions, [], "selection must not also execute candidate-next")
        press(0x70, True)  # Repeat while held is still not a new action.
        self.assertEqual(actions, [])
        press(0x70, False)
        press(0x70, True); press(0x70, False)
        self.assertEqual(commits, ["n1"])
        self.assertEqual(actions, ["candidate-next"])
        self.assertEqual(warnings, [])

    def test_hook_packet_selects_real_service_closes_qml_and_restores_action(self):
        self.run_selection(True)

    def test_poll_selects_real_service_closes_qml_and_restores_action(self):
        self.run_selection(False)

    def test_queued_assistant_f1_losing_foreground_does_not_select(self):
        native, source = HookNative(), FixtureSource()
        native.window_process_id = lambda hwnd: os.getpid() if hwnd == 55 else 1234
        keys = NativeShortcutBackend(native, source=source)
        self.addCleanup(keys.shutdown)
        actions, commits = [], []
        keys.actionTriggered.connect(actions.append)
        self.assertTrue(keys.configure({"map": "G", "candidate-next": "F1"}))
        keys.enableManagement(); keys.bindWindow(42); self.assertTrue(keys.enable())
        windows = WindowController()
        service = ManualMapPickerService(settings=FakeSettings(), windows=windows,
            input_backend=keys, session=FakeSession("difficult"),
            catalog_provider=lambda: catalog("difficult", [dict(id="n1", name="北-红门", direction="上")]),
            commit=lambda map_id: commits.append(map_id) or True)
        engine, component, window, warnings = load_picker(service)
        self.addCleanup(dispose_picker, service, windows, engine, component, window)
        windows.bindWindow("map-picker", window)
        service.open("queued-foreground")
        wait(self, lambda: not service.busy and window.isVisible())
        native.foreground = 55
        source.eventReceived.emit(source._transition_event(0x70, 0, 0, True, 55))
        native.foreground = 77  # Another app takes focus before queued delivery.
        APP.processEvents()
        self.assertEqual(commits, [])
        self.assertEqual(actions, [])
        self.assertTrue(service.visible and window.isVisible())
        self.assertTrue(keys._picker_keys)
        source.eventReceived.emit(source._transition_event(0x70, 0, 0, False, 77))
        APP.processEvents()
        # A fresh assistant-foreground choice is still valid; non-activating
        # game choices are independently exercised by both routing tests above.
        native.foreground = 55
        source.eventReceived.emit(source._transition_event(0x70, 0, 0, True, 55))
        APP.processEvents()
        self.assertEqual(commits, ["n1"])
        self.assertFalse(service.visible or window.isVisible())
        self.assertEqual(actions, [])
        self.assertEqual(warnings, [])

    def test_qml_thumbnail_pixels_and_missing_image_mouse_selection(self):
        with tempfile.TemporaryDirectory() as directory:
            png = write_png(directory, "temporary.png", color=(40, 120, 220))
            windows, keys, commits = WindowController(), NativeShortcutBackend(Native()), []
            self.addCleanup(keys.shutdown)
            service = ManualMapPickerService(settings=FakeSettings(), windows=windows,
                input_backend=keys, session=FakeSession("difficult"),
                catalog_provider=lambda: catalog("difficult", [
                    dict(id="has-image", name="有缩略图", direction="上", thumbnailPath=str(png)),
                    dict(id="missing", name="无缩略图", direction="上", thumbnailPath=str(Path(directory)/"missing.png"))]),
                commit=lambda map_id: commits.append(map_id) or True)
            self.addCleanup(service.shutdown)
            engine, component, window, warnings = load_picker(service)
            self.addCleanup(dispose_picker, service, windows, engine, component, window)
            windows.bindWindow("map-picker", window)
            service.open("pixels")
            wait(self, lambda: not service.busy and qml_item(window, "pickerThumbnail0") is not None)
            thumb = qml_item(window, "pickerThumbnail0")
            wait(self, lambda: thumb.property("paintedWidth") > 0)
            QTest.qWait(50)
            pixels = window.grabWindow()
            self.assertFalse(pixels.isNull())
            save_qa_image(pixels, "picker-temporary-and-missing.png")
            center = thumb.mapToScene(QPointF(thumb.width()/2, thumb.height()/2)).toPoint()
            color = pixels.pixelColor(center)
            self.assertLess(abs(color.red()-40), 8)
            self.assertLess(abs(color.green()-120), 8)
            self.assertLess(abs(color.blue()-220), 8)
            self.assertFalse(qml_item(window, "pickerThumbnail1").property("visible"))
            first, missing = qml_item(window, "pickerOption0"), qml_item(window, "pickerOption1")
            self.assertGreater(missing.width(), 250, "missing thumbnail must not collapse its map card")
            self.assertLessEqual(abs(first.width()-missing.width()), 1)
            click(window, qml_item(window, "pickerOption1"))
            self.assertEqual(commits, ["missing"])
            self.assertFalse(window.isVisible())
            self.assertEqual(warnings, [])


class PickerPreparedRoundLifecycleTests(unittest.TestCase):
    def test_prepared_round_end_and_authorized_resource_replace_revoke_old_qml(self):
        from client_live_runtime import LiveRuntime
        from client_session import SessionService
        from test_client_live_runtime import FixtureCapture, fixture_engine
        import test_client_coordinator as seams
        with patch("map_interface_observation.detect_map_interface", return_value={"detected": True}), \
             patch("floor_control_region.locate_vertical_floor_controls",
                   side_effect=lambda frame, mode, **kw: synthetic_read(mode)):
            original = fixture_engine("difficult")
            original.pack.maps[0].name = "旧资源地图"
            current = {"engine": original}
            runtime = LiveRuntime(engine_factories={"difficult": lambda: current["engine"]},
                capture_provider=FixtureCapture(), evidence_kind="synthetic")
            runtime.dispatch("set_context", {"mode": "difficult", "profileId": "shared-structure"})
            runtime.configure_settings({"mapSelectionMode": "manual"})
            self.assertTrue(runtime.dispatch("start_round")["success"])
            self.assertTrue(runtime.snapshot()["roundPreparing"])
            self.assertFalse(runtime.snapshot()["roundActive"])
            session = SessionService(runtime)
            self.addCleanup(session.shutdown)
            seams.CoordinatorTests.setUpClass()
            owner = seams.CoordinatorTests(); owner.setUp()
            self.addCleanup(owner.tearDown)
            coordinator = owner.coordinator
            coordinator.runtime, coordinator.session = runtime, session
            keys, windows = NativeShortcutBackend(Native()), WindowController()
            self.addCleanup(keys.shutdown)
            service = ManualMapPickerService(settings=FakeSettings(), windows=windows,
                input_backend=keys, session=session, catalog_provider=runtime.manual_map_catalog,
                commit=coordinator.confirmManualMap)
            engine, component, window, warnings = load_picker(service)
            self.addCleanup(dispose_picker, service, windows, engine, component, window)
            windows.bindWindow("map-picker", window)
            service.open("prepared-round")
            wait(self, lambda: not service.busy and bool(service.options))
            self.assertEqual(service.options[0]["mapId"], "fixture-map")
            self.assertTrue(runtime.dispatch("end_round")["success"])
            session.refreshRuntime(); APP.processEvents()
            self.assertFalse(service.visible or window.isVisible(), "ending a preparing round must revoke its picker")
            self.assertEqual(keys._picker_keys, {})
            lease = session.acquireResourceLease()
            self.assertTrue(lease, "the real resource lease must allow the ended round")
            self.assertFalse(service.open("during-resource-lease"))
            self.assertFalse(window.isVisible())
            runtime.invalidate("resources-changed"); session.refreshRuntime()
            replacement = fixture_engine("difficult")
            replacement.pack.maps[0].id = "new-map"
            replacement.pack.maps[0].name = "新资源地图"
            replacement.pack.by_id = {row.id: row for row in replacement.pack.maps}
            current["engine"] = replacement
            session.releaseResourceLease(lease)
            self.assertTrue(runtime.dispatch("start_round")["success"])
            self.assertTrue(runtime.dispatch("capture_now")["success"])
            runtime.dispatch("close_map"); session.refreshRuntime(); APP.processEvents()
            self.assertFalse(service.choose(0), "old catalog must not enqueue a stale accepted command")
            self.assertEqual(runtime.snapshot()["mapId"], "")
            service.open("new-round")
            wait(self, lambda: not service.busy and bool(service.options))
            self.assertEqual(service.options[0]["mapId"], "new-map")
            self.assertTrue(service.choose(0))
            wait(self, lambda: not session.busy)
            self.assertEqual(runtime.snapshot()["mapId"], "new-map")
            self.assertNotEqual(session.snapshot["lastReceipt"].get("reason"), "invalid-manual-map")
            self.assertFalse(window.isVisible())
            self.assertEqual(warnings, [])


class RecorderGuiIntegrationTests(unittest.TestCase):
    def test_real_mouse_hits_recorder_above_scroll_and_saves_restart(self):
        storage = settings_fixtures.SettingsTests(); storage.setUpClass(); storage.setUp()
        self.addCleanup(storage.tearDown)
        service = storage.service()
        view = QQuickView()
        self.addCleanup(dispose_view, view)
        view.setResizeMode(QQuickView.SizeRootObjectToView)
        view.rootContext().setContextProperty("settingsService", service)
        view.setSource(QUrl.fromLocalFile(str(Path(__file__).with_name("ClientSettings.qml"))))
        self.assertFalse(view.errors())
        view.show()
        for width, height, key in ((720, 740, Qt.Key_F20), (1100, 900, Qt.Key_F21)):
            with self.subTest(size=(width, height)):
                view.resize(width, height); QTest.qWait(30)
                panel = view.rootObject()
                click(view, visual_item(panel, "record_hotkey"))
                self.assertTrue(service.shortcutRecording)
                QTest.qWait(40)  # Let the actual visible recorder finish its layout.
                area = visual_item(panel, "shortcutCaptureArea")
                # Defocus intentionally: an actual mouse click must hit the
                # overlaid recorder, not the scroll content underneath it.
                visual_item(panel, "settingsMainCard").forceActiveFocus()
                click(view, area, Qt.RightButton)
                self.assertEqual(service.shortcutCandidate, "MOUSE2")
                click(view, area)
                self.assertTrue(area.property("activeFocus"))
                QTest.keyClick(view, key)
                expected = "F20" if key == Qt.Key_F20 else "F21"
                self.assertEqual(service.shortcutCandidate, expected)
                image = view.grabWindow()
                self.assertFalse(image.isNull())
                save_qa_image(image, f"recorder-{width}x{height}.png")
                # Recorder's white input surface is visible, not merely in the
                # QObject tree. Sample its left interior, away from text/border.
                point = area.mapToScene(QPointF(8, area.height()/2)).toPoint()
                self.assertGreater(image.pixelColor(point).lightness(), 240)
                click(view, visual_item(panel, "shortcutCaptureConfirm"))
                wait(self, lambda: not service.busy)
                self.assertFalse(service.shortcutRecording)
                self.assertEqual(json.loads(storage.path.read_text())["hotkey"], expected)
                restarted = storage.service()
                self.assertEqual(restarted.persistedValues["hotkey"], expected)
                self.assertEqual(restarted.persistedValues["actionHotkeys"][9], "")


class QtClientPickerShutdownTests(unittest.TestCase):
    def test_actual_client_close_drains_real_picker_and_settings_pools(self):
        run = subprocess.run([sys.executable, str(Path(__file__).resolve()), "--shutdown-scenario"],
                             env=dict(os.environ), capture_output=True, text=True, timeout=20)
        self.assertEqual(run.returncode, 0, run.stdout + run.stderr)
        result = json.loads(run.stdout.strip().splitlines()[-1])
        self.assertEqual(result["code"], 0, result)
        self.assertEqual(result["events"], ["quit-requested"], result)
        self.assertTrue(result["exitClicked"], result)
        self.assertTrue(result["closed"] and result["closeReady"], result)
        self.assertTrue(result["writerFinished"] and result["catalogFinished"], result)
        self.assertTrue(result["pickerClosed"] and result["keysReleased"], result)
        self.assertEqual(result["pickerOptions"], [], result)
        self.assertFalse(any(result["pools"].values()), result)
        self.assertGreater(result["beatsAfterClose"], 5, result)
        self.assertEqual(result["visibleWindows"], [], result)
        self.assertFalse(result["captureEnabled"] or result["hookRunning"], result)


def shutdown_scenario():
    from PySide6.QtCore import QTimer
    from client import QtClient
    from client_paths import WORK_ROOT
    # Actual QtClient, service pools, QML windows and close pipeline. Replace
    # only foreign OS enumeration/listeners and remote update reads.
    WORK_ROOT.mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="qt-picker-close-", dir=WORK_ROOT) as directory:
        root = Path(directory)
        (root/".qt-lab-owner").write_text("qt-quick-isolated-v1", encoding="utf-8")
        with patch("client_coordinator.another_assistant_running", return_value=False), \
             patch("client_input.NativeWindowAPI", return_value=HookNative()):
            client = QtClient(APP, root, probe_output=root/"unused-probe.json",
                              info_fetch=lambda *_args, **_kwargs: {})
        APP.aboutToQuit.connect(client.shutdown)
        # Startup resource inventory owns a real exclusive lease. Do not open
        # an idle picker until that authorized resource read has released it.
        wait(unittest.TestCase(), lambda: not client.session.snapshot.get("resourceLocked")
             and not client.resources.busy and not client.session.busy)
        catalog_release, write_release = threading.Event(), threading.Event()
        catalog_entered, write_entered = threading.Event(), threading.Event()
        catalog_done, write_done = threading.Event(), threading.Event()
        original_write = client.settings._write
        def slow_catalog():
            catalog_entered.set()
            if not catalog_release.wait(4):
                raise RuntimeError("catalog release timeout")
            catalog_done.set()
            return catalog("nightmare", [dict(id="late", name="迟到地图")])
        def slow_write(payload, expected):
            write_entered.set()
            if not write_release.wait(4):
                raise RuntimeError("writer release timeout")
            result = original_write(payload, expected)
            write_done.set()
            return result
        client.manual_map_picker._catalog_provider = slow_catalog
        client.settings._write = slow_write
        client.settings.setValue("mapSelectionMode", "manual")
        if not client.settings.save():
            raise AssertionError(client.settings.message)
        client.manual_map_picker.open("shutdown-in-flight")
        if not catalog_entered.wait(2) or not write_entered.wait(2):
            raise AssertionError("real owned workers never started")
        events, beats = [], []
        client.coordinator.quitRequested.connect(lambda: events.append("quit-requested"))
        beat = QTimer(); beat.timeout.connect(lambda: beats.append(client.coordinator.closing)); beat.start(10)
        # A timer, rather than a helper releasing the work, detects any accidental
        # waitForDone() on the UI thread before the application is ready to quit.
        QTimer.singleShot(40, client.main.close)
        chosen = []
        def choose_exit():
            from PySide6.QtCore import QObject
            dialog = client.main.findChild(QObject, "clientCloseChoice")
            if chosen or dialog is None or not dialog.property("visible"):
                return
            button = qml_item(client.main, "closeApplication")
            chosen.append(True)
            click(client.main, button)
        chooser = QTimer(); chooser.timeout.connect(choose_exit); chooser.start(15)
        QTimer.singleShot(130, write_release.set)
        QTimer.singleShot(380, catalog_release.set)
        QTimer.singleShot(4500, lambda: (events.append("timeout"), catalog_release.set(), write_release.set(), APP.exit(7)))
        code = APP.exec()
        client.shutdown()  # idempotence after aboutToQuit
        owners = {"settings": client.settings, "session": client.session,
                  "resources": client.resources, "picker": client.manual_map_picker,
                  "backend": client.backend, "routeChoices": client.route_choices,
                  "reference": client.reference_preview, "info": client.info,
                  "online": client.online_packages, "galleries": client.custom_galleries,
                  "problems": client.problems}
        pools = {name: getattr(owner, "pool", getattr(owner, "_pool", None)).activeThreadCount()
                 for name, owner in owners.items() if getattr(owner, "pool", getattr(owner, "_pool", None)) is not None}
        print(json.dumps(dict(code=code, events=events, closed=client._closed,
            closeReady=client.coordinator.closeReady, exitClicked=bool(chosen), catalogFinished=catalog_done.is_set(),
            writerFinished=write_done.is_set() and json.loads(client.settings.path.read_text())["mapSelectionMode"] == "manual",
            pickerClosed=client.manual_map_picker._closed and not client.manual_map_picker.visible,
            keysReleased=not client.input._picker_keys, pickerOptions=client.manual_map_picker.options, pools=pools, beatsAfterClose=sum(beats),
            visibleWindows=[name for name, window in client.windows._windows.items() if window.isVisible()],
            captureEnabled=client.capture.snapshot["enabled"], hookRunning=client.input.snapshot["hookRunning"])))


if __name__ == "__main__":
    if "--shutdown-scenario" in sys.argv:
        shutdown_scenario()
    else:
        unittest.main()
