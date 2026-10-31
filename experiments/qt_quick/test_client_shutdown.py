"""Actual QWindow close/quit with real coordinator and fake owned workers."""
from pathlib import Path
import json
import os
import subprocess
import sys
import tempfile
import unittest


def scenario(mode, destination):
    from types import SimpleNamespace
    import threading
    import time
    from unittest.mock import Mock
    from PySide6.QtCore import QObject, QPointF, QTimer, QUrl, QRunnable, QThreadPool, Qt
    from PySide6.QtGui import QGuiApplication
    from PySide6.QtQml import QQmlApplicationEngine
    from PySide6.QtQuick import QQuickItem
    from PySide6.QtTest import QTest
    import test_client_coordinator as seams
    app = QGuiApplication([])
    seams.CoordinatorTests.setUpClass()
    fixture = seams.CoordinatorTests()
    fixture.setUp()
    coordinator = fixture.coordinator
    zero = SimpleNamespace(activeThreadCount=lambda: 0)
    fixture.resources.pool = fixture.settings.pool = fixture.session._pool = zero
    fixture.resources.canCancel = False
    coordinator.capture = SimpleNamespace(disable=Mock())
    release = threading.Event()
    pool = QThreadPool()
    class Worker(QRunnable):
        def run(self):
            release.wait(2)
            if mode in ("settings", "resources"):
                Path(destination).write_text("accepted-write-completed", encoding="utf-8")
    if mode != "idle":
        owner = fixture.session if mode == "cancellable" else getattr(fixture, mode)
        setattr(owner, "_pool" if mode == "cancellable" else "pool", pool)
        pool.start(Worker())
    fixture.session.stop = Mock(side_effect=release.set if mode == "cancellable" else None)
    if mode in ("settings", "resources"):
        QTimer.singleShot(240, release.set)
    events, beats = [], []
    coordinator.quitRequested.connect(lambda: events.append("quit-requested"))
    coordinator.quitRequested.connect(app.quit)
    qml = Path(__file__).with_name("Client.qml").read_text(encoding="utf-8")
    closing = "onClosing:" + qml.split("onClosing:", 1)[1].split("    RowLayout", 1)[0]
    choice = qml[qml.index("    Dialog {\n        id: closeChoice"):qml.index("    FileDialog { id: replayPicker")]
    popup = qml[qml.rfind("    Popup {"):].rsplit("\n}", 1)[0]
    engine = QQmlApplicationEngine()
    engine.rootContext().setContextProperty("clientCoordinator", coordinator)
    engine.loadData(("import QtQuick\nimport QtQuick.Controls.Basic\nimport QtQuick.Layouts\nApplicationWindow { id:root; visible:true; width:600; height:500; " + closing + choice + popup + "\n}").encode(), QUrl.fromLocalFile(str(Path(__file__).with_name("owned-close.qml"))))
    window = engine.rootObjects()[0]
    heartbeat = QTimer(); heartbeat.timeout.connect(lambda: beats.append(1)); heartbeat.start(10)
    QTimer.singleShot(30, window.close)
    chosen = []
    def choose_exit():
        dialog = window.findChild(QObject, "clientCloseChoice")
        if chosen or not dialog.property("visible"):
            return
        button = window.findChild(QQuickItem, "closeApplication")
        position = button.mapToScene(QPointF(button.width()/2, button.height()/2)).toPoint()
        chosen.append(True)
        QTest.mouseClick(window, Qt.LeftButton, Qt.NoModifier, position)
    chooser = QTimer(); chooser.timeout.connect(choose_exit); chooser.start(20)
    QTimer.singleShot(1800, lambda: (events.append("timeout"), app.exit(7)))
    started = time.monotonic()
    code = app.exec()
    elapsed = time.monotonic() - started
    release.set(); pool.waitForDone(1000)
    result = dict(code=code, events=events, elapsed=elapsed, closeReady=coordinator.closeReady,
        beats=len(beats), inputStopped=fixture.input.disable.called,
        captureStopped=coordinator.capture.disable.called,
        writerFinished=Path(destination).exists(), activeWorkers=pool.activeThreadCount(), exitChoiceClicked=bool(chosen))
    fixture.tearDown()
    print(json.dumps(result))


class ShutdownTests(unittest.TestCase):
    def run_case(self, mode):
        environment = dict(os.environ, QT_QPA_PLATFORM="offscreen", QT_QUICK_BACKEND="software")
        with tempfile.TemporaryDirectory() as directory:
            run = subprocess.run([sys.executable, str(Path(__file__).resolve()), mode,
                                  str(Path(directory) / "committed.txt")], env=environment,
                                 capture_output=True, text=True, timeout=8)
            self.assertEqual(run.returncode, 0, run.stderr + run.stdout)
            result = json.loads(run.stdout.strip().splitlines()[-1])
        self.assertEqual(result["code"], 0, result)
        self.assertEqual(result["events"], ["quit-requested"], result)
        self.assertTrue(result["closeReady"], result)
        self.assertTrue(result["exitChoiceClicked"], result)
        self.assertTrue(result["inputStopped"] and result["captureStopped"], result)
        self.assertEqual(result["activeWorkers"], 0)
        self.assertLess(result["elapsed"], 1.5)
        return result

    def test_idle_window_close_exits(self):
        self.run_case("idle")

    def test_pending_cancellable_worker_stops_then_exits(self):
        self.run_case("cancellable")

    def test_accepted_settings_writer_drains_with_responsive_event_loop(self):
        result = self.run_case("settings")
        self.assertTrue(result["writerFinished"])
        self.assertGreater(result["beats"], 10)

    def test_accepted_resource_writer_drains_before_quit(self):
        self.assertTrue(self.run_case("resources")["writerFinished"])


if __name__ == "__main__":
    if len(sys.argv) == 3:
        scenario(sys.argv[1], sys.argv[2])
    else:
        unittest.main()
