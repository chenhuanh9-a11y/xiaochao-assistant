"""Actual snapshot journals through startup/helper; only native spawn/wait mocked."""
import json
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

import author_resource_snapshots as snapshots
import client_resource_startup_recovery as startup
import star_map_import as importer
import star_map_import_helper as process
import star_map_recovery_helper as helper
from resource_transaction import resource_runtime_lease, assert_resources_ready, ResourceTransactionError
import test_author_resource_snapshots as fixtures


class SnapshotRecoveryHandoffTests(unittest.TestCase):
    def setUp(self):
        self.fixture = fixtures.AuthorSnapshotTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.root = self.fixture.root
        self.fixture.install(self.fixture.package())
        self.old = self.fixture.view()
        self.old_bytes = {p: p.read_bytes() for p in self.old.directory.rglob('*') if p.is_file()}

    def interrupt(self, *, committed=False):
        point = '_clear_pointer' if committed else '_commit_outcome'
        with patch.object(snapshots, point, side_effect=fixtures.Crash()):
            with self.assertRaises(fixtures.Crash):
                self.fixture.install(self.fixture.package(fmt='jpeg'))
        self.assertTrue(self.fixture.pending().exists())

    def offer(self, *, accepted=True):
        confirm = Mock(return_value=accepted)
        with resource_runtime_lease(self.root):
            with self.assertRaises(ResourceTransactionError) as caught:
                assert_resources_ready(self.root)
            self.assertEqual(caught.exception.code, 'author-snapshot-recovery-required')
            with patch.object(process, '_launch_parent_bound_helper',
                              return_value=SimpleNamespace(pid=42, poll=lambda: None)) as launch:
                result = startup.offer_startup_recovery(self.root, caught.exception, confirm=confirm)
            self.assertIn('图片版本均会保留', confirm.call_args.args[0])
            self.assertTrue(self.fixture.pending().exists(), 'startup must not recover in process')
            if not accepted:
                launch.assert_not_called()
                self.assertEqual(result.status, 'cancelled')
                return None
            self.assertEqual(result.status, 'helper-started')
            args = launch.call_args.args[3]
            request = dict(request_file=args[1], request_sha256=args[3], install_root=self.root,
                           request_id=result.request_id, parent_pid=42, parent_created='1234')
            payload = json.loads(helper._request_path(self.root, result.request_id).read_bytes())
            marker = json.loads((self.root/'work/map-packages/qt-recovery-pending.json').read_bytes())
            self.assertEqual(marker['planSha256'], payload['plan']['planSha256'])
            self.assertEqual(payload['plan']['status'], 'author-snapshot-pending')
        return request

    def run_request(self, request):
        # No real installed process or EXE exists. Locks, request validation,
        # consent fingerprint, rollback and file identity checks remain real.
        with patch.object(process, 'wait_for_application_exit', return_value='exited-matching-process-handle'), \
                patch.object(importer, '_check_closed'):
            return helper.run_helper(**request)

    def assert_payloads_retained(self):
        for path, data in self.old_bytes.items():
            self.assertEqual(path.read_bytes(), data)
        self.assertEqual(len(list((self.root/snapshots.STORE/'versions').glob('*/*/manifest.json'))), 2)

    def test_uncommitted_snapshot_handoff_restores_previous_activation(self):
        self.interrupt()
        code, receipt = self.run_request(self.offer())
        self.assertEqual(code, 0, receipt)
        self.assertEqual(receipt['operationResult']['status'], 'author-snapshot-rolled-back')
        self.old.assert_active()
        self.assertFalse(self.fixture.pending().exists())
        assert_resources_ready(self.root)
        self.assert_payloads_retained()

    def test_committed_snapshot_handoff_only_clears_marker(self):
        self.interrupt(committed=True)
        current = self.fixture.view()
        code, receipt = self.run_request(self.offer())
        self.assertEqual(code, 0, receipt)
        self.assertEqual(receipt['operationResult']['status'], 'author-snapshot-committed')
        current.assert_active()
        with self.assertRaisesRegex(ValueError, 'activation-changed'):
            self.old.assert_active()
        self.assertFalse(self.fixture.pending().exists())
        self.assert_payloads_retained()

    def test_startup_cancel_keeps_pending_and_does_not_write_handoff(self):
        self.interrupt()
        active = self.root/snapshots.activation_path(self.fixture.profile)
        before = active.read_bytes()
        self.offer(accepted=False)
        self.assertEqual(active.read_bytes(), before)
        self.assertFalse((self.root/'work/map-packages/qt-recovery-pending.json').exists())
        self.assertFalse(list((self.root/'work/map-packages').glob('recovery-request-*')))
        self.assert_payloads_retained()

    def test_foreign_activation_after_consent_blocks_helper_without_mutation(self):
        self.interrupt()
        request = self.offer()
        active = self.root/snapshots.activation_path(self.fixture.profile)
        active.unlink()
        active.write_bytes(b'unknown replacement must be retained')
        code, receipt = self.run_request(request)
        self.assertEqual(code, 2, receipt)
        self.assertFalse(receipt['success'])
        self.assertEqual(receipt['errorCode'], 'author-snapshot-rollback-blocked')
        self.assertEqual(active.read_bytes(), b'unknown replacement must be retained')
        self.assertTrue(self.fixture.pending().exists())
        self.assert_payloads_retained()


if __name__ == '__main__':
    unittest.main()
