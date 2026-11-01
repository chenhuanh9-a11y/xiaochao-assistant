"""Retirement outcomes through actual Qt services/UI and startup consent.

Filesystem transaction proofs live in production tests. Here only the read-only
plan boundary is substituted for the UI click; no native helper is launched.
"""
from pathlib import Path
import time
import unittest
from unittest.mock import patch
from uuid import uuid4

from PySide6.QtCore import Qt, QUrl
from PySide6.QtGui import QGuiApplication
from PySide6.QtQuick import QQuickView
from PySide6.QtTest import QTest

APP = QGuiApplication.instance() or QGuiApplication([])
import test_client_resources as fixtures
import client_resource_recovery as recovery
import client_resource_startup_recovery as startup
import qt_startup_diagnostics as diagnostics


def cleanup_plan(*, pending=True):
    return dict(transactionId=str(uuid4()), packageId='nightmare-suju',
                status='upgrade-cleanup-pending' if pending else 'upgrade-cleanup-available',
                pending=pending, fileCount=2, blockedPaths=[], planSha256='a' * 64)


class RetirementPresentationTests(unittest.TestCase):
    def setUp(self):
        self.fixture = fixtures.ResourceServiceTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.service = self.fixture.service

    def test_committed_cleanup_receipt_is_not_old_resource_restoration(self):
        receipt = dict(success=True, requestId='bound-request', planSha256='a' * 64,
            action='recover', transactionId='transaction',
            operationResult=dict(status='upgrade-committed', upgradeTransactionId='transaction',
                                 temporaryCopiesRemaining=0))
        self.service.recordRecoveryReceipt(receipt)
        self.service.refresh()
        self.fixture.settle()
        self.assertIn('临时文件', self.service.message)
        self.assertNotIn('还原旧资源', self.service.message)
        receipt['operationResult'].update(status='rolled-back', cleanupOnly=True)
        self.service.recordRecoveryReceipt(receipt)
        self.service.refresh()
        self.fixture.settle()
        self.assertIn('临时文件', self.service.message)
        self.assertNotIn('还原旧资源', self.service.message)
        for incomplete in (1, False, None):
            receipt['operationResult']['temporaryCopiesRemaining'] = incomplete
            with self.assertRaises(ValueError):
                self.service.recordRecoveryReceipt(receipt)
        receipt['operationResult']['temporaryCopiesRemaining'] = 0
        receipt['operationResult']['upgradeTransactionId'] = 'wrong'
        with self.assertRaises(ValueError):
            self.service.recordRecoveryReceipt(receipt)

    def test_startup_accepts_terminal_cleanup_without_promising_rollback(self):
        plan = cleanup_plan()
        self.assertEqual(startup._validate_plan(plan, 'upgrade'), plan)
        message = startup._confirmation_message(plan, 'upgrade')
        self.assertIn('清理临时副本', message)
        self.assertNotIn('恢复到本次升级前', message)

    def test_cleanup_diagnostics_keep_safe_reason_and_relative_object(self):
        from managed_base_resources import ManagedBaseResourceError
        from star_map_import import ImportRejected
        path = 'work/map-packages/base-repairs/' + str(uuid4()) + '/backup/_internal/references/base.npz'
        issues = [dict(path=path, reason='cleanup-object-unwritable'),
                  dict(path='C:/Users/private-user/secret', reason='private-secret')]
        for error in (ManagedBaseResourceError('base-cleanup-incomplete', issues),
                      ImportRejected('upgrade-cleanup-incomplete', details=dict(issues=issues))):
            with self.subTest(code=error.code):
                code, record = diagnostics._details(error, 'run-client')
                self.assertEqual(code, 'QT-STARTUP-RESOURCE')
                self.assertIn('resourceCode=' + error.code, record)
                self.assertIn('issue.0.path=' + path, record)
                self.assertIn('cleanup-object-unwritable', record)
                self.assertNotIn('private-user', record)
                self.assertNotIn('private-secret', record)

    def test_actual_qml_history_click_requests_cleanup_not_rollback(self):
        self.service.setRecoveryAllowedProvider(lambda: True)
        view = QQuickView()
        view.setResizeMode(QQuickView.SizeRootObjectToView)
        view.rootContext().setContextProperty('resourceService', self.service)
        view.setSource(QUrl.fromLocalFile(str(Path(__file__).with_name('ClientResources.qml'))))
        self.assertFalse(view.errors())
        view.resize(1000, 600)
        view.show()
        try:
            APP.processEvents()
            self.fixture.settle()
            plan = cleanup_plan(pending=False)
            self.service._recovery_rows = [plan]
            self.service.changed.emit()
            QTest.qWait(60)
            items, queue = [], [view.contentItem()]
            while queue:
                item = queue.pop()
                items.append(item)
                queue.extend(item.childItems())
            displayed = [item.property('text') for item in items if isinstance(item.property('text'), str)]
            self.assertTrue(any(self.service.packageTitle(plan['packageId']) in text and
                self.service.recoveryStatusLabel(plan['status']) in text for text in displayed))
            self.assertFalse(any(plan['transactionId'] in text or plan['status'] in text for text in displayed))
            button = next((item for item in items if item.objectName() ==
                           'resourceTransactionAction-' + plan['transactionId']), None)
            if button is None:
                button = next((item for item in items if item.property('text') == '回滚'), None)
            self.assertIsNotNone(button)
            self.assertEqual(button.property('text'), '清理临时副本')
            # QTest does not run the normal window/render loop. Flush pending
            # layouts on the real visual tree before deriving the click point;
            # in particular, a clipped ListView can still have height zero.
            ancestors, parent = [], button
            while parent is not None:
                ancestors.append(parent)
                parent = parent.parentItem()
            deadline = time.monotonic() + 2.0
            layout_ready = False
            while time.monotonic() < deadline:
                for item in reversed(ancestors):
                    item.ensurePolished()
                view.requestUpdate()
                QTest.qWait(20)
                point = button.mapToScene(button.boundingRect().center())
                layout_ready = (view.rootObject().width() == view.width() and
                    all(not item.clip() or item.boundingRect().contains(
                        item.mapFromScene(point)) for item in ancestors))
                if layout_ready:
                    break
            self.assertTrue(layout_ready, 'real QML click target remained clipped')
            self.assertTrue(button.isVisible())
            self.assertTrue(button.isEnabled())
            with patch.object(recovery, 'inspect_locked', return_value=plan):
                QTest.mouseClick(view, Qt.LeftButton, Qt.NoModifier,
                                button.mapToScene(button.boundingRect().center()).toPoint())
                APP.processEvents()
                self.fixture.settle()
            self.assertEqual(self.service._pending[0], 'recover')
            self.assertIn('不会把当前图包退回旧版本', self.fixture.confirmations[-1][1])
            self.assertNotIn('并保留备份', self.fixture.confirmations[-1][1])
            self.service.confirmOperation(False)
        finally:
            view.close()
            view.deleteLater()
            APP.processEvents()


if __name__ == '__main__':
    unittest.main()
