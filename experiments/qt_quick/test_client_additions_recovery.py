"""Actual additive transaction, Qt worker, confirmation, and receipt contracts."""
import unittest
from pathlib import Path
from unittest.mock import patch

import test_client_resources as fixtures
from client_resources import importer
import map_package_upgrade as upgrade


class AdditionsRecoveryTests(unittest.TestCase):
    def setUp(self):
        self.fixture = fixtures.ResourceServiceTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.service = self.fixture.service
        self.root = self.fixture.root

    def interrupt(self):
        original = importer.os.link
        def publish(source, target, *args, **kwargs):
            original(source, target, *args, **kwargs)
            if Path(target) == self.root / fixtures.IMAGE:
                raise KeyboardInterrupt('fixture loss during publication')
        with patch.object(importer.os, 'link', side_effect=publish):
            with self.assertRaises(KeyboardInterrupt):
                importer.install_package(self.fixture.package(), self.root, application_stopped=True)

    def test_worker_exposes_additions_recovery_and_confirmation_then_restores(self):
        self.interrupt()
        self.service.refresh()
        self.fixture.settle()
        self.assertTrue(self.service.recoveryRequired)
        self.service.setRecoveryAllowedProvider(lambda: True)
        self.fixture.request_recovery()
        self.assertIn('尚未完成的导入会撤销', self.fixture.confirmations[-1][1])
        self.assertNotIn('保留备份', self.fixture.confirmations[-1][1])
        self.service.confirmOperation(True)
        self.fixture.settle()
        self.assertFalse((self.root / fixtures.IMAGE).exists())
        self.assertFalse((self.root / upgrade.ADDITIONS_PENDING).exists())
        self.assertFalse(self.service.recoveryRequired)
        self.assertEqual(len(self.fixture.changes), 1)

    def test_additions_receipt_preserves_commit_or_rollback_meaning_after_scan(self):
        for status, phrase in [('additions-committed', '上次导入已完成'), ('additions-rolled-back', '未完成的导入已撤销'),
                              ('removal-committed', '上次删除和清理已完成'), ('removal-rolled-back', '未完成的删除已撤销，图包已恢复')]:
            identity_key = 'removalTransactionId' if status.startswith('removal-') else 'additionTransactionId'
            receipt = dict(success=True, requestId='bound-request', planSha256='a' * 64,
                action='recover', transactionId='transaction',
                operationResult=dict(status=status, **{identity_key: 'transaction'}))
            self.service.recordRecoveryReceipt(receipt)
            self.service.refresh()
            self.fixture.settle()
            self.assertIn(phrase, self.service.message)
            self.assertNotIn('已还原旧资源', self.service.message)
            receipt['operationResult'][identity_key] = 'wrong'
            with self.assertRaises(ValueError):
                self.service.recordRecoveryReceipt(receipt)


if __name__ == '__main__':
    unittest.main()
