"""Actual QtClient composition and its registered QML names; native/data fixtures only."""
import json
import os
from pathlib import Path
import subprocess
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
import tempfile
import time
import unittest
from unittest.mock import patch

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
os.environ.setdefault('QT_QUICK_BACKEND', 'software')

class RecaptureCompositionTests(unittest.TestCase):
    def test_actual_client_main_button_reaches_real_focus_session_and_provider(self):
        run = subprocess.run([sys.executable, str(Path(__file__).resolve()), '--composition'],
            env=dict(os.environ), capture_output=True, text=True, timeout=30)
        self.assertEqual(run.returncode, 0, run.stdout + run.stderr)
        result = json.loads(run.stdout.strip().splitlines()[-1])
        self.assertEqual(result['registeredName'], 'clientCoordinator')
        self.assertTrue(result['receipt']['success'], result)
        self.assertEqual(result['newCaptures'], 1, result)
        self.assertEqual(result['focusRequests'], 1, result)
        self.assertEqual(result['qmlErrors'], [], result)
        self.assertTrue(result['explicitHomeOpened'], result)
        self.assertTrue(result['closed'], result)


def composition():
    from PySide6.QtCore import QCoreApplication, QEvent, QPointF, Qt
    from PySide6.QtTest import QTest
    from client import QtClient
    from client_paths import WORK_ROOT
    from test_client_recapture_focus import APP, RecaptureFocusTests
    from test_client_manual_map import qml_item
    from test_client_input import Native as InputNative
    from client_input import NativeShortcutBackend
    import test_client_live_runtime as fixtures

    fixture = RecaptureFocusTests('test_main_qml_click_returns_game_then_captures_via_real_guard')
    fixture.setUp()
    client = None
    class Factories(dict):
        def set_custom_display(self, *args, **kwargs): pass
    providers = Factories(nightmare=lambda: fixtures.fixture_engine(),
                         difficult=lambda: fixtures.fixture_engine('difficult'))
    WORK_ROOT.mkdir(exist_ok=True)
    try:
        with tempfile.TemporaryDirectory(prefix='qt-recapture-composition-', dir=WORK_ROOT) as directory:
            root = Path(directory)
            (root / '.qt-lab-owner').write_text('qt-quick-isolated-v1', encoding='utf-8')
            with patch('client_input.NativeShortcutBackend', side_effect=lambda *a, **k: NativeShortcutBackend(InputNative())), \
                 patch('client_capture.ClientCaptureProvider', return_value=fixture.capture), \
                 patch('client_imported_providers.build_imported_engine_factories', return_value=providers) as factory:
                client = QtClient(APP, root, probe_output=root / 'probe-unused.json', info_fetch=lambda *a, **k: {})
                factory.assert_called_once_with(client.resource_resolver, client.paths)
                assert client.resource_resolver.root == client.paths.imported_resource_root
            for timer in (client.coordinator._foreground_timer, client.coordinator._tracking_timer):
                timer.stop()
            def wait(predicate):
                end = time.monotonic() + 8
                while not predicate() and time.monotonic() < end:
                    APP.processEvents(); time.sleep(.002)
                assert predicate(), 'actual QtClient service timeout'
            wait(lambda: not client.session.busy and not client.resources.busy
                 and not client.session.snapshot.get('resourceLocked'))
            client.coordinator._bound = '42'
            client.coordinator._native_windows = fixture.native
            client.coordinator._game_focus._native = fixture.native
            client.session.dispatch('start_round')
            wait(lambda: not client.session.busy)
            assert client.session.snapshot['lastReceipt']['success'], client.session.snapshot
            client.main.hide()
            controls = client.windows._windows['controls']
            controls.setProperty('collapsed', False)
            controls.setProperty('expandedHeightLimit', 2000)
            client.windows.show('controls'); APP.processEvents()
            client.coordinator.navigate("home"); APP.processEvents()
            explicit_open = client.main.isVisible() and client.main.property("page") == "home"
            assert explicit_open
            more = qml_item(client.main, "homeMapMore")
            point = more.mapToScene(QPointF(more.width()/2, more.height()/2)).toPoint()
            QTest.mouseClick(client.main, Qt.LeftButton, Qt.NoModifier, point)
            QTest.qWait(180); APP.processEvents()
            button = qml_item(client.main, "homeRecapture")
            assert button is not None and button.isEnabled(), client.session.snapshot
            before = len(fixture.backend.captures)
            fixture.native.foreground = 77
            point = button.mapToScene(QPointF(button.width()/2, button.height()/2)).toPoint()
            QTest.mouseClick(client.main, Qt.LeftButton, Qt.NoModifier, point)
            wait(lambda: not client.session.busy)
            receipt = client.session.snapshot['lastReceipt']
            errors = [str(error) for error in client.warnings
                      if 'ReferenceError' in str(error) or 'recaptureFromMain' in str(error)]
            result = dict(registeredName='clientCoordinator', receipt=receipt,
                newCaptures=len(fixture.backend.captures)-before,
                focusRequests=len(fixture.native.mutations()), qmlErrors=errors, explicitHomeOpened=explicit_open)
            client.shutdown()
            result['closed'] = client._closed
            print(json.dumps(result))
    finally:
        if client is not None:
            client.shutdown()
        fixture.doCleanups()
        QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)

if __name__ == '__main__':
    if '--composition' in sys.argv: composition()
    else: unittest.main()
