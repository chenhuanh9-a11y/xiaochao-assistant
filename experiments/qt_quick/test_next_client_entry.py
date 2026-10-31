"""Actual QtClient/QML entry and exit, empty private resources, no native input.

Each case owns a subprocess, marked sandbox and real application event loop.
Network, update fetching and game/native hook startup are prohibited seams.
"""
import json
import os
from pathlib import Path
import subprocess
import sys
import unittest


def scenario(mode):
    import tempfile
    import threading
    import time
    from unittest.mock import patch
    from PySide6.QtCore import QObject, QPointF, QTimer, Qt
    from PySide6.QtGui import QFont, QFontDatabase, QGuiApplication
    from PySide6.QtQuick import QQuickItem
    from PySide6.QtTest import QTest
    from client_paths import WORK_ROOT
    from client import QtClient
    from client_theme import apply_light_theme
    from client_settings import FRESH_ACTIONS

    app = QGuiApplication([])
    font = Path("C:/Windows/Fonts/msyh.ttc")
    if font.is_file():
        QFontDatabase.addApplicationFont(str(font))
    app.setFont(QFont("Microsoft YaHei UI", 10))
    apply_light_theme(app)
    app.setQuitOnLastWindowClosed(False)
    WORK_ROOT.mkdir(exist_ok=True)
    release, account_entered, plaza_entered = threading.Event(), threading.Event(), threading.Event()
    client = None
    result = dict(mode=mode)

    def until(condition, timeout=5):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            app.processEvents()
            if condition():
                return
            time.sleep(.003)
        raise AssertionError("QtClient did not reach expected state")

    def find_item(name):
        # Repeater delegates have a visual parent but may not belong to the
        # QWindow's QObject descendant tree; follow the actual rendered scene.
        pending = [client.main.contentItem()]
        while pending:
            item = pending.pop()
            if item.objectName() == name:
                return item
            pending.extend(item.childItems())
        return None

    def click(name):
        button = find_item(name)
        if button is None:
            raise AssertionError("Missing actual QML control: " + name)
        QTest.qWait(40)
        if not button.isVisible() or not button.isEnabled():
            raise AssertionError("Unavailable actual QML control: " + name)
        point = button.mapToScene(QPointF(button.width() / 2, button.height() / 2)).toPoint()
        QTest.mouseClick(client.main, Qt.LeftButton, Qt.NoModifier, point)
        app.processEvents()

    def capture(name):
        target = os.environ.get("QT_NEXT_ENTRY_PROOF_DIR")
        if target:
            folder = Path(target)
            folder.mkdir(parents=True, exist_ok=True)
            QTest.qWait(60)
            assert client.main.grabWindow().save(str(folder / name))

    with tempfile.TemporaryDirectory(prefix="next-entry-", dir=WORK_ROOT) as folder, \
            patch("urllib.request.OpenerDirector.open", side_effect=AssertionError("No network in entry test")) as network, \
            patch("client_native_keys.PassThroughKeyboardSource.start", side_effect=AssertionError("No native listener in entry test")) as native_start:
        sandbox = Path(folder)
        (sandbox / ".qt-lab-owner").write_text("qt-quick-isolated-v1", encoding="utf-8")
        (sandbox / "_internal").mkdir()
        # An existing nine-slot preference must survive with a blank tenth slot.
        old_keys = list(FRESH_ACTIONS[:9])
        (sandbox / "ui-settings.json").write_text(json.dumps(dict(actionHotkeys=old_keys)), encoding="utf-8")
        fake_info = lambda url: dict(schemaVersion=1, platform="windows", version="1.0.5",
            versionCode=9, releaseNotes="Synthetic entry fixture", publishedAt="2026-10-30", releases=[])
        try:
            client = QtClient(app, sandbox, probe_output=sandbox / "probe", info_fetch=fake_info)
            client.main.resize(1100, 780)
            until(lambda: client._initialized and not client.session.busy and not client.settings.busy)
            result["windowNames"] = sorted(client.windows._windows)
            assert "manual" in client.windows._windows
            manual = client.windows._windows["manual"]
            picker = manual.findChild(QObject, "clientManualSelection")
            assert picker is not None and picker.property("service") is client.manual_selection
            assert not manual.isVisible()
            assert client.settings.persistedValues["actionHotkeys"] == old_keys + [""]
            assert client.input.configureSettings(dict(hotkey="G", actionHotkeys=old_keys))
            assert client.input.configureSettings(dict(hotkey="G", actionHotkeys=old_keys + ["F12"]))
            assert not client.input.enabled and not client.input.snapshot["hookRunning"]
            assert not client.capture.snapshot["enabled"]

            click("nav_plaza")
            assert client.main.property("page") == "plaza"
            assert client.main.findChild(QObject, "plazaSearch").property("visible")
            capture("plaza-1100x780.png")
            click("nav_account")
            assert client.main.property("page") == "account"
            assert client.main.findChild(QObject, "accountIdentifier").property("visible")
            capture("account-1100x780.png")
            click("nav_settings")
            assert client.main.property("page") == "settings"
            click("advancedShortcutsToggle")
            assert find_item("setting_action_9") is not None, "Manual map hotkey has no settings row"
            result["navigationAndLegacyKeys"] = True

            if mode == "pending":
                def fake_list(query):
                    plaza_entered.set()
                    assert release.wait(5), "fixture release timed out"
                    return []
                def fake_account(method, path, body=None, **kwargs):
                    from test_account_service_next import tokens
                    account_entered.set()
                    assert release.wait(5), "fixture release timed out"
                    return tokens()
                sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "tests"))
                client.plaza._list = fake_list
                client.account.service._api = fake_account
                click("nav_plaza"); click("plazaSearch")
                until(plaza_entered.is_set)
                click("nav_account")
                client.main.findChild(QObject, "accountIdentifier").setProperty("text", "fixture")
                client.main.findChild(QObject, "accountPassword").setProperty("text", "synthetic-password")
                click("accountLogin")
                until(account_entered.is_set)
                assert client.plaza.busy and client.account.busy

            beats, exits = [], []
            heartbeat = QTimer(); heartbeat.timeout.connect(lambda: beats.append(1)); heartbeat.start(10)
            client.coordinator.quitRequested.connect(lambda: exits.append("quit"))
            chosen = []
            def choose_exit():
                dialog = client.main.findChild(QObject, "clientCloseChoice")
                if not chosen and dialog.property("visible"):
                    chosen.append(True)
                    click("closeApplication")
                    if mode == "pending":
                        assert not client.coordinator.closeReady
                        QTimer.singleShot(220, release.set)
            chooser = QTimer(); chooser.timeout.connect(choose_exit); chooser.start(20)
            safety = QTimer(); safety.setSingleShot(True); safety.timeout.connect(lambda: app.exit(9)); safety.start(7000)
            QTimer.singleShot(0, client.main.close)
            started = time.monotonic()
            code = app.exec()
            safety.stop(); chooser.stop(); heartbeat.stop()
            result.update(exitCode=code, exitChoiceClicked=bool(chosen), exits=exits,
                          closeReady=client.coordinator.closeReady, beats=len(beats),
                          elapsed=round(time.monotonic() - started, 3), warnings=list(client.warnings))
            assert code == 0 and chosen and exits == ["quit"]
            assert client.coordinator.closeReady
            assert not client.account.pool.activeThreadCount() and not client.plaza.pool.activeThreadCount()
            assert all(not window.isVisible() for window in client.windows._windows.values())
            if mode == "pending":
                assert len(beats) >= 10 and not client.account.loggedIn
            assert not client.warnings, client.warnings
            network.assert_not_called(); native_start.assert_not_called()
            result["networkCalls"] = network.call_count
            result["nativeStarts"] = native_start.call_count
        finally:
            release.set()
            if client is not None:
                client.shutdown()
                result["shutdownCompleted"] = client._closed
                client.engine.deleteLater()
                app.processEvents()
    print(json.dumps(result, ensure_ascii=True))


class NextClientEntryTests(unittest.TestCase):
    def run_case(self, mode):
        environment = dict(os.environ, QT_QPA_PLATFORM="offscreen", QT_QUICK_BACKEND="software")
        run = subprocess.run([sys.executable, str(Path(__file__).resolve()), "--scenario", mode],
                             capture_output=True, text=True, encoding="utf-8", errors="replace",
                             timeout=25, env=environment)
        self.assertEqual(run.returncode, 0, run.stdout + run.stderr)
        result = json.loads(run.stdout.strip().splitlines()[-1])
        self.assertTrue(result["shutdownCompleted"], result)
        self.assertTrue(result["navigationAndLegacyKeys"], result)

    def test_actual_client_navigation_legacy_settings_and_idle_exit(self):
        self.run_case("idle")

    def test_actual_client_drains_account_and_plaza_workers_before_exit(self):
        self.run_case("pending")


if __name__ == "__main__":
    if len(sys.argv) == 3 and sys.argv[1] == "--scenario":
        scenario(sys.argv[2])
    else:
        unittest.main()
