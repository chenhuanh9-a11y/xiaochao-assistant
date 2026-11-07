"""Temp-only repro: real Qt adapters and removal; no installed app/game/hook.

Run from qt-field-fix with PYTHONPATH=app;tools;experiments/qt_quick;tests.
Directory-policy binding is replaced, filesystem/reader/writer/leases are real.
The unfixed output is before=1, during=0, after-file=True, after-count=0.
"""
import hashlib
import json
import os
from pathlib import Path
import tempfile
import threading
import time
from types import SimpleNamespace
from unittest.mock import patch

os.environ["QT_QPA_PLATFORM"] = "offscreen"
from PIL import Image
from PySide6.QtGui import QGuiApplication
import backend as b
import client_package_manager as pm
import client_resources as rs
from client_session import SessionService
import star_map_import as imp
from resource_transaction import resource_runtime_lease
from resource_transaction import resource_write_lock

import unittest
app = QGuiApplication.instance() or QGuiApplication([])


def wait(predicate):
    deadline = time.monotonic() + 5
    while not predicate() and time.monotonic() < deadline:
        app.processEvents()
        time.sleep(.002)
    assert predicate(), "fixture timeout"


class ResourceReaderTests(unittest.TestCase):
    def test_home_preview_waits_for_resource_writer_before_reading(self):
        import test_client_info as fixtures
        fixture = fixtures.InfoTests()
        fixture.setUpClass(); fixture.setUp()
        try:
            service = fixture.service()
            with patch.object(service, '_home_rows', wraps=service._home_rows) as read:
                with resource_runtime_lease(fixture.root), resource_write_lock(fixture.root):
                    service.setHomeContext('nightmare', 'shared-structure')
                    wait(lambda: service._pool.activeThreadCount() == 1)
                    time.sleep(.06); app.processEvents()
                    read.assert_not_called()
                wait(lambda: service._home_preview_job is None)
                read.assert_called_once()
        finally:
            fixture.tearDown()

    def test_reference_preview_cannot_read_during_writer_and_cancel_does_not_wait_for_writer(self):
        from client_reference_presentation import ReferencePreviewProvider
        from unittest.mock import Mock
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            factory = Mock(side_effect=AssertionError('Reference reader entered writer transaction'))
            provider = ReferencePreviewProvider({'nightmare': factory}, installation_root=root)
            try:
                with resource_runtime_lease(root), resource_write_lock(root):
                    provider.request('nightmare', 'author')
                    wait(lambda: provider.pool.activeThreadCount() == 1)
                    time.sleep(.06); app.processEvents()
                    factory.assert_not_called()
                    provider.close()
                    self.assertTrue(provider.pool.waitForDone(1000), 'Cancelled reader must release shutdown')
                app.processEvents()
                factory.assert_not_called()
            finally:
                provider.close(); provider.pool.waitForDone()

    def test_reference_preview_entry_and_ready_respect_session_resource_lease(self):
        import test_client_coordinator as fixtures
        from unittest.mock import Mock
        import numpy as np
        fixture = fixtures.CoordinatorTests()
        fixture.setUpClass(); fixture.setUp()
        try:
            owner = fixture.coordinator
            owner._reference_preview_provider = SimpleNamespace(request=Mock())
            fixture.session.snapshot['resourceLocked'] = True
            owner.beginReferenceEdit()
            owner._reference_preview_provider.request.assert_not_called()
            context = (*owner._applied_context, 1)
            owner._reference_preview_context = context
            owner._reference_preview_ready(dict(context=context, requestId=1, previewOnly=True,
                pixels=np.zeros((2, 2, 4), dtype=np.uint8)))
            self.assertFalse(owner._reference)
            fixture.session.snapshot['resourceLocked'] = False
            owner.beginReferenceEdit()
            owner._reference_preview_provider.request.assert_called_once()
        finally:
            fixture.tearDown()

    def test_browser_waits_for_removal_rollback_and_receives_final_invalidation(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            internal = root / "_internal"
            (internal / "references").mkdir(parents=True)
            target = internal / "assets/suju/fixture.png"
            target.parent.mkdir(parents=True)
            Image.new("RGB", (8, 6), "red").save(target)
            (internal / "references/nightmare-map-pack.json").write_text(json.dumps(dict(
                packId="fixture", defaultEntryFloor=1, floors=["1"], maps=[dict(id="map-0",
                name="Map", resourceBase="resource-root", routeProfileId="suju",
                layers={"1": "assets/suju/fixture.png"})])), encoding="utf-8")
            (internal / "references/nightmare-author-route-catalog.json").write_text(json.dumps(dict(
                schemaVersion=1, defaultProfileId="suju", profiles=[dict(id="suju", displayName="suju",
                authorName="suju", routeFamilyName="fixture", runtimeEnabled=False, settingsSelectable=False,
                maps={"map-0": [dict(id="v1", displayName="Variant", identityStatus="display-only",
                displayPolicy="preview-only", layers={"1": "assets/suju/fixture.png"})]})])), encoding="utf-8")
            dependencies = []
            for name in sorted(rs.importer.BASE_DEPENDENCIES):
                dependency = root / name
                if not dependency.exists():
                    dependency.write_bytes(b"fixture-base")
                data = dependency.read_bytes()
                dependencies.append(dict(path=name, bytes=len(data), sha256=hashlib.sha256(data).hexdigest()))
            receipt = root / "work/map-packages/nightmare-suju.json"
            receipt.parent.mkdir(parents=True)
            receipt.write_text(json.dumps(dict(packageId="nightmare-suju", revision=1,
                archiveSha256="a" * 64, requiresFiles=dependencies, files=[dict(path=target.relative_to(root).as_posix(),
                bytes=target.stat().st_size, sha256=hashlib.sha256(target.read_bytes()).hexdigest())])), encoding="utf-8")
            paths = SimpleNamespace(installation_root=root, resource_root=internal, work_root=root / "work")
            with patch.object(b, "runtime_paths", return_value=paths), patch.object(pm,
                    "validate_installation_root", return_value=root), \
                    patch.object(rs, "validate_installation_root", return_value=root), resource_runtime_lease(root):
                browser = b.Backend(root)
                session = SessionService()
                resources = rs.ResourceService(root)
                resources.setLeaseProvider(session)
                manager = pm.PackageManagerService(root, resources)
                manager.setLeaseProvider(session)
                wait(lambda: not browser.busy)
                self.assertEqual(browser.mapCount, 1)
                changed = []
                manager.resourcesChanged.connect(lambda: changed.append(True))
                manager.resourcesChanged.connect(browser.refresh)
                manager.requestRemove("nightmare-suju")
                wait(lambda: bool(manager.pendingKind))
                deleted, resume = threading.Event(), threading.Event()
                real = imp._rollback_published

                def unlink(path, *args, **kwargs):
                    done = real(path, *args, **kwargs)
                    if Path(path) == target:
                        deleted.set()
                        assert resume.wait(5), "fixture writer release timeout"
                        raise OSError("fixture failure after deleting visible author")
                    return done

                try:
                    with patch.object(imp, "_rollback_published", side_effect=unlink):
                        manager.confirmOperation(True)
                        wait(deleted.is_set)
                        self.assertTrue(session.snapshot["resourceLocked"]); self.assertTrue(manager.busy)
                        browser.refresh()
                        until = time.monotonic() + .15
                        while time.monotonic() < until:
                            app.processEvents(); time.sleep(.002)
                        self.assertTrue(browser.busy, "Reader must wait for the resource transaction")
                        self.assertEqual(browser.mapCount, 1, "Never publish a deleted-but-uncommitted snapshot")
                        resume.set()
                        wait(lambda: not manager.busy)
                    wait(lambda: not browser.busy)
                    self.assertTrue(target.exists())
                    self.assertEqual(browser.mapCount, 1)
                    self.assertTrue(changed, "Rollback must invalidate resource consumers as well")
                finally:
                    resume.set()
                    manager.shutdown()
                    manager.pool.waitForDone()
                    resources.shutdown(); resources.pool.waitForDone()
                    browser.shutdown()
                    browser.pool.waitForDone()
                    session.shutdown()
                    app.processEvents()


if __name__ == "__main__":
    unittest.main()
