"""Real Loader persistence and full client composition with fixture native/engine IO."""
import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("QT_QUICK_BACKEND", "software")
import json
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch
from PySide6.QtCore import QObject, QUrl, QCoreApplication, QEvent
from PySide6.QtGui import QGuiApplication
from PySide6.QtQml import QQmlComponent, QQmlEngine
from PySide6.QtTest import QTest

APP = QGuiApplication.instance() or QGuiApplication([])


def wait(predicate, seconds=5):
    deadline = time.monotonic() + seconds
    while not predicate() and time.monotonic() < deadline:
        QTest.qWait(5)
    assert predicate(), "QML operation did not settle"


class LazyPageTests(unittest.TestCase):
    def test_unvisited_page_absent_then_instance_persists_draft_and_geometry(self):
        engine = QQmlEngine()
        component = QQmlComponent(engine)
        component.setData(b'''import QtQuick
import "."
ClientLazyPage {
 width: 400; height: 300
 sourceComponent: Component { Item { objectName: "lazyFixture"; property string draft: "" } }
}''', QUrl.fromLocalFile(str(Path(__file__).with_name("lazy-test.qml"))))
        page = component.create()
        self.assertIsNotNone(page, str(component.errors()))
        try:
            self.assertIsNone(page.property("item"))
            page.setProperty("selected", True)
            wait(lambda: page.property("item") is not None)
            item = page.property("item")
            item.setProperty("draft", "kept")
            page.setProperty("selected", False)
            page.setProperty("selected", True)
            APP.processEvents()
            self.assertIs(page.property("item"), item)
            self.assertEqual(item.property("draft"), "kept")
            self.assertEqual((item.width(), item.height()), (400, 300))
        finally:
            page.deleteLater()
            APP.processEvents()

    def test_real_client_uses_lazy_nonhome_pages_and_keeps_resource_dialog_host(self):
        result = composition(check_lazy=True)
        self.assertEqual(result["unvisitedPageCount"], 8)


def composition(*, check_lazy=False):
    network_attempts = []
    def no_network(*args, **kwargs):
        network_attempts.append(True)
        raise AssertionError("Offline composition test attempted networking")
    with patch("socket.socket.connect", side_effect=no_network), \
         patch("socket.socket.connect_ex", side_effect=no_network), \
         patch("socket.socket.sendto", side_effect=no_network), \
         patch("socket.create_connection", side_effect=no_network), \
         patch("socket.getaddrinfo", side_effect=no_network):
        result = _composition(check_lazy=check_lazy)
    assert not network_attempts, "Offline test attempted networking"
    return result


def _composition(*, check_lazy=False):
    from client import QtClient
    from client_paths import WORK_ROOT
    from client_providers import EngineFactories
    from client_route_plaza import RoutePlazaService
    from test_client_input import Native
    from test_client_live_runtime import FixtureCapture, fixture_engine
    import psutil
    WORK_ROOT.mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="qt-lazy-composition-", dir=WORK_ROOT) as directory:
        root = Path(directory)
        (root / ".qt-lab-owner").write_text("qt-quick-isolated-v1")
        capture = FixtureCapture()
        capture.is_current = lambda *a, **k: True
        client = None
        def offline_plaza(*args, **kwargs):
            return RoutePlazaService(*args, **kwargs, listing=lambda query: [])
        start = time.monotonic()
        try:
            with patch("client_input.NativeWindowAPI", return_value=Native()), \
                 patch("client_capture.ClientCaptureProvider", return_value=capture), \
                 patch("client_coordinator.another_assistant_running", return_value=False), \
                 patch("client_route_plaza.RoutePlazaService", side_effect=offline_plaza), \
                 patch.object(EngineFactories, "_build", lambda self, mode, **kw: fixture_engine(mode)):
                client = QtClient(APP, root, probe_output=root / "unused.json", info_fetch=lambda *a, **k: {})
                construction_ms = (time.monotonic()-start)*1000
                wait(lambda: not client.session.busy and client._initialized)
                QTest.qWait(100)
                names = ("Gallery", "Settings", "Replay", "Help", "Recognition", "Custom", "Downloads", "Plaza")
                loaders = [client.main.findChild(QObject, "lazy"+name+"Page") for name in names]
                result = dict(constructionMs=construction_ms, workingSetBytes=psutil.Process().memory_info().rss,
                    qmlObjectCount=sum(1+len(window.findChildren(QObject)) for window in client.engine.rootObjects()),
                    unvisitedPageCount=sum(page is not None and page.property("item") is None for page in loaders),
                    evidence="fixture-composition-not-packaged-startup")
                if check_lazy:
                    assert result["unvisitedPageCount"] == 8, result
                    assert client.main.findChild(QObject, "resourceArchiveDialog") is not None
                    client.main.setProperty("page", "settings")
                    wait(lambda: loaders[1].property("item") is not None)
                    settings_page = loaders[1].property("item")
                    client.main.setProperty("page", "home")
                    client.main.setProperty("page", "settings")
                    assert loaders[1].property("item") is settings_page
                    assert all(page.property("item") is None for i, page in enumerate(loaders) if i != 1)
                    for key, loader in zip(("gallery", "settings", "replay", "help", "recognition",
                                             "custom", "downloads", "plaza"), loaders):
                        client.main.setProperty("page", key)
                        wait(lambda: loader.property("item") is not None)
                    assert not client.warnings, client.warnings
                return result
        finally:
            if client is not None:
                client.shutdown()
                client.engine.deleteLater()
                QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
                APP.processEvents()


if __name__ == "__main__":
    import sys
    if "--measure" in sys.argv:
        print(json.dumps(composition()))
    else:
        unittest.main()
