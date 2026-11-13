"""Explicit joint resource choices through the actual Qt service and catalog.

Only source assets and capture are synthetic. No input listener, game, native
file picker or physical display is operated by this component journey.
"""
import json
import os
from pathlib import Path
import threading
import time
from unittest.mock import patch
from uuid import uuid4
import zipfile

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
os.environ.setdefault('QT_QUICK_BACKEND', 'software')
from PySide6.QtCore import QObject, QPoint, QPointF, Qt, QUrl
from PySide6.QtGui import QFontDatabase, QGuiApplication
from PySide6.QtQuick import QQuickView
from PySide6.QtTest import QTest
import numpy as np

import client_imported_activation as activation
from client_imported_providers import build_imported_engine_factories
from client_imported_resources import ImportedResourceService
from client_live_runtime import LiveRuntime
from client_route_preferences import prepare_display_layer
from client_session import SessionService
from imported_resource_archive import save_archive
from test_client_imported_author_selection import _AuthorSelectionFixture, changed_png
from test_client_live_runtime import FixtureCapture


class ClientImportedActivationPlanUiTests(_AuthorSelectionFixture):
    @classmethod
    def setUpClass(cls):
        cls.app = QGuiApplication.instance() or QGuiApplication([])
        if os.name == 'nt':
            for name in ('msyh.ttc', 'msyhbd.ttc'):
                QFontDatabase.addApplicationFont(str(Path(os.environ['WINDIR'])/'Fonts'/name))
        super().setUpClass()

    def setUp(self):
        super().setUp()
        self.active_resolver = self.resolver()
        self.factories = build_imported_engine_factories(self.active_resolver, self.paths)
        self.capture = FixtureCapture()
        self.runtime = LiveRuntime(engine_factories=self.factories, capture_provider=self.capture,
                                   evidence_kind='synthetic')
        self.session = SessionService(self.runtime)
        self.service = ImportedResourceService(self.active_resolver, paths=self.paths,
                                               engine_factories=self.factories)
        self.service.setLeaseProvider(self.session)
        self.errors, self.changes = [], []
        self.service.errorReported.connect(self.errors.append)
        self.service.resourcesChanged.connect(lambda: self.changes.append(True))
        self.service.resourcesChanged.connect(lambda: self.runtime.invalidate('resources-changed'))
        self.addCleanup(self.close)

    def close(self):
        self.service.shutdown()
        self.service.pool.waitForDone()
        self.app.processEvents()
        self.session.shutdown()
        self.session._pool.waitForDone()
        self.runtime.shutdown()
        self.app.processEvents()

    def wait(self, condition=None, timeout=90):
        condition = condition or (lambda:not self.service.busy)
        deadline = time.monotonic() + timeout
        while not condition() and time.monotonic() < deadline:
            self.app.processEvents()
            time.sleep(.005)
        self.assertTrue(condition(), self.service.message)

    def revision(self):
        with zipfile.ZipFile(self.source.output) as archive:
            members = {name:archive.read(name) for name in archive.namelist()}
        manifest = json.loads(members['manifest.json'])
        manifest['resourceVersion'] = 'base-v2'
        members['manifest.json'] = json.dumps(manifest).encode('utf-8')
        path = self.root/'base-v2.zip'
        with zipfile.ZipFile(path, 'x', compression=zipfile.ZIP_DEFLATED) as archive:
            for name,raw in members.items():
                archive.writestr(name, raw)
        saved = save_archive(path, self.resources)
        return {ref.component_id:ref for ref in saved.component_refs}

    def scenario(self):
        first, _, _ = self.package(package_id='author-versioned')
        other, _, _ = self.package(package_id='author-stopped', profile_id='author-stopped')
        self.seed(displays=[first.component_ref, other.component_ref])
        next_refs = self.revision()
        second, _, _ = self.package(package_id='author-versioned', resource_version='v2',
            dependency=self.wire(next_refs['base']), payloads={'images/route.png':changed_png()})
        self.package(package_id='author-versioned', resource_version='v3',
                     dependency=self.wire(next_refs['base']))
        self.service.listPackages()
        self.wait()
        return first, other, next_refs, second

    @staticmethod
    def find(item, name):
        if item.objectName() == name:
            return item
        for child in item.childItems():
            found = ClientImportedActivationPlanUiTests.find(child, name)
            if found is not None:
                return found
        return None

    def click(self, view, name):
        deadline = time.monotonic() + 7
        while time.monotonic() < deadline:
            self.app.processEvents()
            item = self.find(view.contentItem(), name)
            if item is not None and item.isVisible() and item.isEnabled():
                center = item.mapToScene(QPointF(item.width()/2, item.height()/2))
                clipped = False
                ancestor = item.parentItem()
                while ancestor is not None:
                    if ancestor.clip():
                        top = ancestor.mapFromScene(item.mapToScene(QPointF(0, 0)))
                        clipped = clipped or top.y() < 0 or top.y() + item.height() > ancestor.height()
                    ancestor = ancestor.parentItem()
                if not clipped and 0 < center.y() < view.height() and 0 < center.x() < view.width():
                    QTest.mouseClick(view, Qt.LeftButton, Qt.NoModifier, center.toPoint())
                    self.app.processEvents()
                    return
            listing = self.find(view.contentItem(), 'managedPackageList')
            self.assertIsNotNone(listing)
            QTest.wheelEvent(view, listing.mapToScene(QPointF(listing.width()/2, listing.height()/2)),
                            QPoint(0, -120))
            QTest.qWait(25)
            while listing.property('moving') and time.monotonic() < deadline:
                QTest.qWait(10)
        observed = []
        def collect(item):
            if item.objectName().startswith('activatePackageButton-'):
                point = item.mapToScene(QPointF())
                observed.append(dict(name=item.objectName(), visible=item.isVisible(), enabled=item.isEnabled(),
                                     x=point.x(), y=point.y()))
            for child in item.childItems():
                collect(child)
        collect(view.contentItem())
        evidence = Path(__file__).resolve().parents[2]/'work/startup-performance-20261110/joint-plan-ui'/uuid4().hex[:10]
        evidence.mkdir(parents=True)
        view.grabWindow().save(str(evidence/'pointer-failure.png'))
        (evidence/'pointer-failure.json').write_text(json.dumps(observed, indent=2), encoding='utf-8')
        self.fail('No enabled visible control: ' + name + '\n' + json.dumps(dict(
            buttons=observed, busy=self.service.busy, stage=self.service.operationStage,
            plan=self.service.activationPlanOpen, recovery=self.service.recoveryRequired,
            allowed=view.rootObject().property('refreshAllowed'))))

    def choose_all(self, plan, updated_ref):
        for row in plan.rows:
            choice = next(choice for choice in row.choices
                if choice.display_ref == updated_ref) if row.old_ref.package_id == 'author-versioned' else row.choices[-1]
            self.assertTrue(self.service.chooseActivationAuthor(self.service.activationPlanId, row.key, choice.key))

    def test_explicit_plan_pointer_commit_updates_base_and_author_and_stops_only_chosen_component(self):
        first, other, next_refs, second = self.scenario()
        before = (self.resources/'catalog.json').read_bytes()
        old_contexts = dict(self.active_resolver.snapshot().contexts)
        package_model, choice_model = self.service.packageModel, self.service.activationPlanModel
        view = QQuickView()
        self.addCleanup(view.deleteLater)
        self.addCleanup(view.close)
        view.setResizeMode(QQuickView.SizeRootObjectToView)
        view.rootContext().setContextProperty('resourceService', self.service)
        view.setSource(QUrl.fromLocalFile(str(Path(__file__).with_name('ClientImportedResources.qml'))))
        self.assertEqual(view.errors(), [])
        view.resize(650, 470)
        view.show()
        view.requestActivate()
        view.rootObject().setProperty('active', True)
        QTest.qWait(150)  # Let the page's normal delayed inventory read start.
        self.wait()
        target_key = f'{next_refs["base"].package_id}/{next_refs["base"].resource_version}'
        self.click(view, f'activatePackageButton-{target_key}-s1-difficult')
        self.wait()
        self.assertTrue(self.service.activationPlanOpen, self.errors)
        self.assertTrue(view.rootObject().property('planVisible'), 'visible page/window must present its plan')
        self.assertFalse(self.service.pendingWrites)
        self.assertFalse(self.service.canCommitActivationPlan)
        with self.assertRaises(RuntimeError):
            self.service.setLeaseProvider(self.session)
        self.assertEqual((self.resources/'catalog.json').read_bytes(), before)
        self.assertFalse(self.service.commitActivationPlan(self.service.activationPlanId))
        lease = self.session.acquireResourceLease()
        self.assertTrue(lease)  # Planning/user deliberation holds no lease.
        self.session.releaseResourceLease(lease)
        plan = self.service._activation_plan
        self.assertEqual({row.old_ref for row in plan.rows}, {first.component_ref, other.component_ref})
        for row in plan.rows:
            self.wait(lambda:self.find(view.contentItem(), 'activationAuthorChoice-' + row.key) is not None,
                      timeout=5)
            combo = self.find(view.contentItem(), 'activationAuthorChoice-' + row.key)
            self.assertIsNotNone(combo)
            self.assertEqual(combo.property('currentIndex'), -1)
            choice_index = next(i for i,choice in enumerate(row.choices)
                if choice.display_ref == second.component_ref) if row.old_ref == first.component_ref else len(row.choices)-1
            combo.forceActiveFocus(Qt.TabFocusReason)
            QTest.keyClick(view, Qt.Key_Space)
            QTest.keyClick(view, Qt.Key_Home)
            for _ in range(choice_index):
                QTest.keyClick(view, Qt.Key_Down)
            QTest.keyClick(view, Qt.Key_Return)
            self.app.processEvents()
        self.assertTrue(self.service.canCommitActivationPlan)
        self.assertIn('base-v2', self.service.activationPlanSummary)
        self.assertIn('保留文件', self.service.activationPlanSummary)
        plan_id = self.service.activationPlanId
        lease = self.session.acquireResourceLease()
        self.assertTrue(lease)
        try:
            self.assertFalse(self.service.commitActivationPlan(plan_id))
            self.assertEqual(self.service.activationPlanId, plan_id)
            self.assertTrue(self.service.canCommitActivationPlan)
            self.assertEqual((self.resources/'catalog.json').read_bytes(), before)
        finally:
            self.session.releaseResourceLease(lease)
        evidence = Path(__file__).resolve().parents[2]/'work/startup-performance-20261110/joint-plan-ui'/uuid4().hex[:10]
        evidence.mkdir(parents=True)
        for width, height in ((650,470), (1000,720)):
            view.resize(width, height)
            view.requestUpdate()
            QTest.qWait(100)
            self.assertTrue(view.grabWindow().save(str(evidence/f'plan-{width}x{height}.png')))
        view.hide()
        QTest.qWait(25)
        self.assertFalse(view.rootObject().property('planVisible'))
        self.assertEqual(self.service.activationPlanId, plan_id)
        view.show()
        view.requestActivate()
        QTest.qWait(25)
        self.assertTrue(view.rootObject().property('planVisible'))
        self.click(view, 'commitActivationPlanButton')
        self.wait()
        self.assertFalse(self.service.activationPlanOpen)
        self.assertFalse(self.errors, self.errors)
        self.assertEqual(self.changes, [True])
        snapshot = self.active_resolver.snapshot()
        self.assertEqual(snapshot.displays, (second.component_ref,))
        self.assertEqual(snapshot.contexts['s1:difficult']['recognitionBase'], next_refs['base'])
        self.assertEqual(snapshot.contexts['s1:difficult']['controls'], next_refs['controls'])
        self.assertEqual(snapshot.contexts['s0:nightmare'], old_contexts['s0:nightmare'])
        self.assertTrue(other.path('images/route.png').is_file())
        self.assertIs(self.service.packageModel, package_model)
        self.assertIs(self.service.activationPlanModel, choice_model)
        engine = self.factories.get_engine('difficult', season='s1')
        layer = prepare_display_layer(engine, self.source.ids[0], '-4', 'author-fixture')
        np.testing.assert_array_equal(layer['pixels'], np.full((3,5,4), (93,92,91,94), np.uint8))
        self.assertFalse(engine.memory_store.root.exists())
        self.assertTrue(engine.qt_rank_ready)
        self.assertFalse(engine.qt_live_ready)
        self.assertFalse((self.resources/'transactions/pending-catalog.json').exists())

    def test_imported_base_plan_is_presented_while_resources_page_is_inactive(self):
        first, other, next_refs, second = self.scenario()
        archive = self.root/'base-v2.zip'
        self.assertTrue(archive.is_file(), archive)
        import_source = self.root/'base-v2.xingtu-base'
        import_source.write_bytes(archive.read_bytes())
        view = QQuickView()
        self.addCleanup(view.deleteLater)
        self.addCleanup(view.close)
        view.setResizeMode(QQuickView.SizeRootObjectToView)
        view.rootContext().setContextProperty('resourceService', self.service)
        view.setSource(QUrl.fromLocalFile(str(Path(__file__).with_name('ClientImportedResources.qml'))))
        self.assertEqual(view.errors(), [])
        view.resize(650, 470)
        view.show()
        view.requestActivate()
        root = view.rootObject()
        # In Client.qml this persistent panel remains instantiated under the
        # non-current StackLayout page while Gallery/Downloads uses its import.
        root.setProperty('active', False)
        root.setProperty('visible', False)
        self.assertFalse(root.isVisible())
        self.assertFalse(root.property('active'))
        dialog = root.findChild(QObject, 'resourceActivationPlanDialog')
        self.assertIsNotNone(dialog)
        completions = []
        self.service.importCompleted.connect(lambda *args: completions.append(args))

        # Run the actual asynchronous import entry with the original-layout
        # base update generated by this synthetic fixture. It is already in
        # the package store so author fixtures can bind to its immutable ref;
        # importArchive still repeats admission, automatic planning and commit.
        self.assertTrue(self.service.importArchive(QUrl.fromLocalFile(str(import_source)).toString()))
        self.wait(lambda: self.service.activationPlanOpen or bool(self.errors)
                  or not self.service.busy, timeout=90)
        self.assertTrue(self.service.activationPlanOpen, self.errors or self.service.message)
        self.assertFalse(self.errors, self.errors)
        self.assertEqual(self.service._kind, '')
        self.assertEqual(self.service._automatic_import, 'base')
        self.assertTrue(root.property('operationPending'))
        self.assertFalse(self.service.pendingWrites)
        self.assertTrue(root.property('planVisible'))
        self.wait(lambda: dialog.property('visible'), timeout=5)
        self.assertTrue(dialog.property('visible'))

        plan = self.service._activation_plan
        self.assertEqual({row.old_ref for row in plan.rows}, {first.component_ref, other.component_ref})
        for row in plan.rows:
            combo = self.find(view.contentItem(), 'activationAuthorChoice-' + row.key)
            self.assertIsNotNone(combo, 'the visible plan did not expose an author choice')
            self.assertTrue(combo.isVisible())
            self.assertEqual(combo.property('currentIndex'), -1)
            choice_index = next(i for i, choice in enumerate(row.choices)
                if choice.display_ref == second.component_ref) if row.old_ref == first.component_ref else len(row.choices)-1
            combo.forceActiveFocus(Qt.TabFocusReason)
            QTest.keyClick(view, Qt.Key_Space)
            QTest.keyClick(view, Qt.Key_Home)
            for _ in range(choice_index):
                QTest.keyClick(view, Qt.Key_Down)
            QTest.keyClick(view, Qt.Key_Return)
            self.app.processEvents()
        self.assertTrue(self.service.canCommitActivationPlan)
        lease = self.session.acquireResourceLease()
        self.assertTrue(lease, 'an uncommitted plan must not hold a session lease')
        self.session.releaseResourceLease(lease)
        self.click(view, 'commitActivationPlanButton')
        self.wait()
        self.assertFalse(self.errors, self.errors)
        self.assertFalse(self.service.activationPlanOpen)
        self.assertFalse(root.property('operationPending'))
        self.assertFalse(self.service._automatic_import)
        self.assertFalse(self.session._leases)
        self.assertEqual([item[0] for item in completions], ['基础资源已启用'], completions)
        snapshot = self.active_resolver.snapshot()
        self.assertEqual(snapshot.displays, (second.component_ref,))
        self.assertEqual(snapshot.contexts['s1:difficult']['recognitionBase'], next_refs['base'])
        self.assertEqual(snapshot.contexts['s1:difficult']['controls'], next_refs['controls'])

    def test_dismiss_late_plan_and_stale_catalog_do_not_publish_or_reopen(self):
        first, other, next_refs, second = self.scenario()
        target_key = f'{next_refs["base"].package_id}/{next_refs["base"].resource_version}'
        entered, release = threading.Event(), threading.Event()
        from client_imported_activation_plan import plan_context_activation
        def delayed(*args, **kwargs):
            plan = plan_context_activation(*args, **kwargs)
            entered.set()
            self.assertTrue(release.wait(5))
            return plan
        before = (self.resources/'catalog.json').read_bytes()
        with patch('client_imported_activation_plan.plan_context_activation', side_effect=delayed):
            self.assertTrue(self.service.activatePackage(target_key, 's1', 'difficult'))
            try:
                self.assertTrue(entered.wait(5))
                self.service.dismissActivationPlan()
            finally:
                release.set()
            self.wait()
        self.assertFalse(self.service.activationPlanOpen)
        self.assertEqual((self.resources/'catalog.json').read_bytes(), before)
        self.assertFalse(self.changes)
        self.assertTrue(self.service.activatePackage(target_key, 's1', 'difficult'))
        self.wait()
        plan_id = self.service.activationPlanId
        self.choose_all(self.service._activation_plan, second.component_ref)
        self.assertFalse(self.service.chooseActivationAuthor('stale', 'missing', 'missing'))
        self.seed(displays=[first.component_ref, other.component_ref])
        changed = (self.resources/'catalog.json').read_bytes()
        self.assertTrue(self.service.commitActivationPlan(plan_id))
        self.wait()
        self.assertEqual(self.errors[-1]['errorCode'], 'resource-catalog-superseded')
        self.assertEqual((self.resources/'catalog.json').read_bytes(), changed)
        self.assertFalse(self.service.activationPlanOpen)
        self.assertFalse(self.changes)
        self.assertFalse(self.service.pendingWrites)
        lease = self.session.acquireResourceLease()
        self.assertTrue(lease)
        self.session.releaseResourceLease(lease)
