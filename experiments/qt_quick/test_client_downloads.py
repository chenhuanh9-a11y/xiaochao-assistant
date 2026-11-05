"""Online UI transport/consent seam: synthetic files, no sockets or game calls."""
import hashlib
import json
import os
from pathlib import Path
import tempfile
import subprocess
import sys
import threading
import time
import unittest
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PySide6.QtCore import QCoreApplication, QObject, QUrl, Signal
from client_paths import WORK_ROOT
from client_downloads import OnlinePackagesService
from star_map_download import DownloadError
import test_client_resources as resource_fixtures

APP = QCoreApplication.instance() or QCoreApplication([])


def until(condition):
    end = time.monotonic() + 3
    while time.monotonic() < end:
        APP.processEvents()
        if condition():
            return
        time.sleep(.002)
    raise AssertionError("bounded worker did not finish")


class Resources(QObject):
    changed = Signal()
    busy = False
    pendingKind = ""
    recoveryRequired = False
    operationGeneration = 0
    message = "local status"
    def __init__(self):
        super().__init__()
        self.calls = []
    def importVerifiedArchive(self, path, expected, generation):
        self.calls.append((path, expected, generation))
        return True


class Transport:
    configured = True
    def __init__(self, root):
        self.root = root
        self.data = b"synthetic zip transport bytes"
        self.rows = [dict(packageId="sample-author", title="Sample", summary="Fixture",
                          bytes=len(self.data), sha256=hashlib.sha256(self.data).hexdigest())]
        self.calls = []
        self.block = threading.Event()
        self.block.set()
    def read_index(self, **kw):
        self.calls.append("index")
        return self.rows
    def download_package(self, package, *, cancel_event, progress):
        self.calls.append(package)
        progress(1, len(self.data))
        self.block.wait(2)
        self.root.mkdir(parents=True, exist_ok=True)
        path = self.root / "fixture.zip"
        path.write_bytes(self.data)
        progress(len(self.data), len(self.data))
        return path
    def attempts_remaining(self, package): return 3
    def cancel_active(self): self.calls.append("cancel")


class OnlineTests(unittest.TestCase):
    def setUp(self):
        WORK_ROOT.mkdir(exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(prefix="online-qt-", dir=WORK_ROOT)
        self.root = Path(self.temp.name)
        (self.root / ".qt-lab-owner").write_text("qt-quick-isolated-v1")
        self.resources = Resources()
        self.transport = Transport(self.root / "work/map-packages/downloads")
        self.service = OnlinePackagesService(self.root, self.resources, transport=self.transport)
        self.problems = []
        self.service.errorReported.connect(self.problems.append)
    def tearDown(self):
        self.transport.block.set()
        self.service.shutdown()
        APP.processEvents()
        self.temp.cleanup()
    def index(self):
        self.service.checkPackages()
        until(lambda: not self.service.busy)
        self.assertEqual(self.service.snapshot["stage"], "ready")

    def test_no_startup_or_unconfigured_requests(self):
        self.assertEqual(self.transport.calls, [])
        other = OnlinePackagesService(self.root, self.resources)
        other.checkPackages()
        self.assertFalse(other.snapshot["canCheck"])
        self.assertFalse(other.busy)
        other.shutdown()

    def test_download_proof_goes_to_importer_not_install_success(self):
        self.index()
        self.service.downloadPackage("sample-author")
        until(lambda: not self.service.busy)
        self.assertEqual(self.service.snapshot["stage"], "handoff")
        path, expected, generation = self.resources.calls[0]
        self.assertTrue(QUrl(path).isLocalFile())
        self.assertEqual(expected, {k:self.transport.rows[0][k] for k in ("packageId","bytes","sha256")})
        self.assertEqual(generation, 0)
        self.assertEqual(self.service.snapshot["progress"]["receivedBytes"], len(self.transport.data))

    def installed_package(self):
        from star_map_import import BASE_DEPENDENCIES
        self.transport.rows[0]["packageId"] = "nightmare-suju"
        target = self.root / "_internal/assets/suju/nightmare-03/floor-2.png"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(b"pixels")
        receipt = self.root / "work/map-packages/nightmare-suju.json"
        receipt.parent.mkdir(parents=True, exist_ok=True)
        dependencies = []
        for relative in sorted(BASE_DEPENDENCIES):
            dependency = self.root / relative
            dependency.parent.mkdir(parents=True, exist_ok=True)
            dependency.write_bytes(b"base")
            dependencies.append(dict(path=relative, bytes=4, sha256=hashlib.sha256(b"base").hexdigest()))
        receipt.write_text(json.dumps(dict(packageId="nightmare-suju", revision=1,
            archiveSha256=self.transport.rows[0]["sha256"], requiresFiles=dependencies, files=[dict(
                path=target.relative_to(self.root).as_posix(), bytes=6,
                sha256=hashlib.sha256(b"pixels").hexdigest())])))
        return target, receipt

    def test_installed_bytes_without_cached_zip_skip_download_and_import_after_restart(self):
        self.installed_package()
        self.assertFalse(self.transport.root.exists())
        for _ in range(2):
            self.index()
            self.service.downloadPackage("nightmare-suju")
            until(lambda: not self.service.busy)
            self.assertEqual(self.transport.calls[-1], "index")
            self.assertEqual(self.resources.calls, [])
            self.assertEqual(self.service.snapshot["stage"], "ready")
            self.assertEqual(self.service.snapshot["packages"][0]["installation"]["status"], "installed")
            self.service.shutdown()
            self.service = OnlinePackagesService(self.root, self.resources, transport=self.transport)

    def test_missing_corrupt_receipt_or_new_digest_still_downloads(self):
        for failure in ("missing", "corrupt", "receipt", "new-version", "base-missing", "base-corrupt"):
            with self.subTest(failure=failure):
                target, receipt = self.installed_package()
                self.index()  # Earlier proof must not mask a subsequent change.
                if failure == "missing":
                    target.unlink()
                elif failure == "corrupt":
                    target.write_bytes(b"broken")  # Same size, different SHA.
                elif failure == "receipt":
                    receipt.write_text("{}")
                elif failure.startswith("base-"):
                    dependency = self.root / json.loads(receipt.read_text())["requiresFiles"][0]["path"]
                    if failure == "base-missing":
                        dependency.unlink()
                    else:
                        dependency.write_bytes(b"fail")  # Same size; hash must be checked.
                else:
                    document = json.loads(receipt.read_text())
                    document["archiveSha256"] = "b" * 64
                    receipt.write_text(json.dumps(document))
                self.service.downloadPackage("nightmare-suju")
                until(lambda: not self.service.busy)
                self.assertEqual(self.transport.calls[-1], "nightmare-suju")
                self.assertEqual(self.service.snapshot["stage"], "handoff")
        self.assertEqual(len(self.resources.calls), 6)

    def test_installed_verification_cannot_publish_after_cancel_selection_or_local_change(self):
        self.installed_package()
        self.transport.rows.append(dict(self.transport.rows[0], packageId="second-author"))
        for change in ("cancel", "selection", "resources"):
            self.index()
            self.service.selectPackage("nightmare-suju")
            entered, release = threading.Event(), threading.Event()
            def verify(*args, **kwargs):
                entered.set()
                release.wait(2)
                return dict(status="installed", archiveSha256=self.transport.rows[0]["sha256"])
            with patch("client_downloads.verified_installed_package", side_effect=verify):
                self.service.downloadPackage("nightmare-suju")
                self.assertTrue(entered.wait(2))
                if change == "cancel":
                    self.service.cancel()
                elif change == "selection":
                    self.service.selectPackage("second-author")
                    self.service.selectPackage("nightmare-suju")
                else:
                    self.resources.operationGeneration += 1
                release.set()
                until(lambda: not self.service.busy)
            self.assertEqual(self.resources.calls, [])
            self.assertNotIn("文件校验通过", self.service.snapshot["message"])
        self.assertNotIn("nightmare-suju", self.transport.calls)

    def test_cancel_or_local_generation_change_blocks_late_handoff(self):
        for cancelled in (True, False):
            self.index()
            self.transport.block.clear()
            self.service.downloadPackage("sample-author")
            if cancelled:
                self.service.cancel()
                self.assertTrue(self.service.busy)  # drain before allowing another request
            else:
                self.resources.operationGeneration += 1
            self.transport.block.set()
            until(lambda: not self.service.busy)
            self.assertEqual(self.resources.calls, [])

    def test_selection_change_blocks_late_handoff_and_failure_can_retry(self):
        self.transport.rows.append(dict(self.transport.rows[0], packageId="second-author"))
        self.index()
        self.transport.block.clear()
        self.service.downloadPackage("sample-author")
        self.service.selectPackage("second-author")
        self.transport.block.set()
        until(lambda: not self.service.busy)
        self.assertEqual(self.resources.calls, [])
        with patch.object(self.transport, "read_index", side_effect=ValueError("fixture failure")):
            self.service.checkPackages()
            until(lambda: not self.service.busy)
            self.assertEqual(self.service.snapshot["stage"], "error")
            self.assertTrue(self.service.snapshot["canCheck"])

    def test_selection_away_and_back_requires_a_new_download_request(self):
        self.transport.rows.append(dict(self.transport.rows[0], packageId="second-author"))
        self.index()
        self.transport.block.clear()
        self.service.downloadPackage("sample-author")
        self.service.selectPackage("second-author")
        self.service.selectPackage("sample-author")
        self.transport.block.set()
        until(lambda: not self.service.busy)
        self.assertEqual(self.resources.calls, [])
        self.assertEqual(self.service.snapshot["stage"], "cached")
        self.service.downloadPackage("sample-author")
        until(lambda: not self.service.busy)
        self.assertEqual(len(self.resources.calls), 1)

    def test_failure_reports_chinese_once_and_cancel_or_late_completion_is_not_error(self):
        raw = "private failure detail " + "原因" * 3000
        with patch.object(self.transport, "read_index", side_effect=DownloadError("network-or-file-error", raw)):
            self.service.checkPackages()
            generation = self.service._generation
            until(lambda: not self.service.busy)
        self.assertEqual(len(self.problems), 1)
        self.assertIn("暂时连接不上图包下载服务", self.service.snapshot["message"])
        self.assertNotIn("private failure", self.service.snapshot["message"])
        self.assertTrue(self.problems[0]["raw"].startswith("private failure detail"))
        self.assertLessEqual(len(self.problems[0]["raw"].encode("utf-8")), 4096)
        self.service._complete(generation, None, raw)
        self.assertEqual(len(self.problems), 1)
        self.index()
        self.transport.block.clear()
        self.service.downloadPackage("sample-author")
        self.service.cancel()
        self.transport.block.set()
        until(lambda: not self.service.busy)
        self.assertIn("已取消", self.service.snapshot["message"])
        self.assertEqual(len(self.problems), 1)


class VerifiedHandoffTests(unittest.TestCase):
    def setUp(self):
        self.fixture = resource_fixtures.ResourceServiceTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)

    def test_mismatched_tuple_is_rejected_before_any_install(self):
        fixture = self.fixture
        archive = fixture.package()
        cache = fixture.root / "work/map-packages/downloads"
        cache.mkdir(parents=True)
        selected = cache / "selected.zip"
        selected.write_bytes(archive.read_bytes())
        expected = dict(packageId="wrong-package", bytes=selected.stat().st_size,
                        sha256=hashlib.sha256(selected.read_bytes()).hexdigest())
        accepted = fixture.service.importVerifiedArchive(QUrl.fromLocalFile(str(selected)).toString(), expected, 0)
        self.assertTrue(accepted)
        until(lambda: not fixture.service.busy)
        self.assertEqual(fixture.changes, [])
        self.assertIn("binding-mismatch", fixture.problems[-1]["raw"])

    def test_stale_generation_cannot_start_and_correct_proof_uses_real_import(self):
        fixture = self.fixture
        archive = fixture.package()
        cache = fixture.root / "work/map-packages/downloads"
        cache.mkdir(parents=True)
        selected = cache / "selected.zip"
        selected.write_bytes(archive.read_bytes())
        expected = dict(packageId="nightmare-suju", bytes=selected.stat().st_size,
                        sha256=hashlib.sha256(selected.read_bytes()).hexdigest())
        self.assertFalse(fixture.service.importVerifiedArchive(str(selected), expected, 9))
        self.assertTrue(fixture.service.importVerifiedArchive(QUrl.fromLocalFile(str(selected)).toString(), expected, 0))
        until(lambda: not fixture.service.busy)
        self.assertEqual(fixture.changes, [True])


class OnlineQmlTests(unittest.TestCase):
    def test_owned_offscreen_component_and_check_button(self):
        code = '''
import json,sys
from pathlib import Path
sys.path.insert(0, 'experiments/qt_quick')
from PySide6.QtGui import QGuiApplication
from PySide6.QtCore import QUrl, QObject, QPoint, Qt
from PySide6.QtQuick import QQuickView
from PySide6.QtTest import QTest
app=QGuiApplication([])
from test_client_downloads import OnlineTests, until
case=OnlineTests(); case.setUp()
view=QQuickView(); warnings=[]
view.engine().warnings.connect(lambda rows: warnings.extend(str(row) for row in rows))
view.rootContext().setContextProperty('onlinePackageService',case.service)
view.setSource(QUrl.fromLocalFile(str(Path('experiments/qt_quick/ClientOnlinePackages.qml').resolve())))
assert view.status()==QQuickView.Ready, view.errors()
view.setResizeMode(QQuickView.SizeRootObjectToView);view.resize(900,500);view.show()
QTest.qWait(40)
button=view.rootObject().findChild(QObject,'onlineCheck')
point=button.mapToScene(QPoint(10,10))
QTest.mouseClick(view,Qt.LeftButton,Qt.NoModifier,point.toPoint())
until(lambda: not case.service.busy)
QTest.qWait(40)
assert case.transport.calls==['index'],case.transport.calls
assert view.rootObject().findChild(QObject,'onlinePackageList').property('count')==1
case.installed_package()
QTest.mouseClick(view,Qt.LeftButton,Qt.NoModifier,point.toPoint())
until(lambda: not case.service.busy)
QTest.qWait(40)
def visual_items(item):
    for child in item.childItems():
        yield child
        yield from visual_items(child)
installed=next(item for item in visual_items(view.rootObject()) if item.objectName()=='onlineDownload-nightmare-suju')
assert installed.property('text')=='校验本地安装'
point=installed.mapToScene(QPoint(10,10))
QTest.mouseClick(view,Qt.LeftButton,Qt.NoModifier,point.toPoint())
until(lambda: not case.service.busy)
assert case.transport.calls==['index','index'],case.transport.calls
assert not case.resources.calls,case.resources.calls
assert not warnings,warnings
view.setSource(QUrl());case.tearDown()
print(json.dumps({'qmlWarnings':warnings,'actualButtonDispatch':True,'installedClickSkipsDownload':True}))
'''
        result = subprocess.run([sys.executable, "-c", code], cwd=Path(__file__).resolve().parents[2],
            env=dict(os.environ, QT_QPA_PLATFORM="offscreen", QT_QUICK_BACKEND="software"),
            capture_output=True, text=True, timeout=10)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn('"actualButtonDispatch": true', result.stdout)


if __name__ == "__main__":
    unittest.main()
