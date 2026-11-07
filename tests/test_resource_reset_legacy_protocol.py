"""Compatibility boundaries for the unpublished 18d1cc0 file-only protocol.

The producer below replays its file/pin/move steps on owned byte fixtures. It
does not run Git, an old EXE, Setup, or the new directory-quarantine algorithm.
Directory evidence is deliberately absent, rather than stripped from a new
transaction whose directories were already moved by the new implementation.
"""
import os
from pathlib import Path
import unittest
import uuid

import managed_base_resources as base
import resource_upgrade_reset as reset
from resource_transaction import assert_resources_ready, resource_write_lock
import test_resource_upgrade_reset as fixtures


def prepare_file_only_fixture(root):
    """Narrow fixture producer for 18d1cc0's persisted prepared-resources shape."""
    with resource_write_lock(root):
        plan = reset.inspect_resource_upgrade(root)
        plan.pop('oldDirectories', None)
        plan.pop('planSha256')
        plan['planSha256'] = reset._digest(plan)
        reset._validate_plan(plan)
        candidate, _manifest = reset._candidate(root)
        transaction_id = str(uuid.uuid4())
        rows, generated = reset._new_payloads(plan, transaction_id)
        directory = root / reset.HISTORY / transaction_id
        directory.mkdir(parents=True)
        journal = dict(schema=reset.SCHEMA, transactionId=transaction_id,
                       status='preparing', plan=plan,
                       newFiles=[dict(row, identity=None) for row in rows])
        reset._write_journal(directory, journal)
        pointer = directory / 'pending-pointer.json'
        reset.transaction._write_new(pointer, reset._encoded(
            dict(schema=reset.SCHEMA, transactionId=transaction_id)))
        os.link(pointer, root / reset.PENDING)
        for index, row in enumerate(journal['newFiles']):
            data = generated.get(row['path'])
            if data is None:
                data = (candidate / 'payload' / row['path']).read_bytes()
            pin = directory / 'new' / str(index)
            reset.transaction._write_new(pin, data)
            row['identity'] = reset.transaction._capture(pin, row)
            reset._write_journal(directory, journal)
        journal['status'] = 'moving'
        reset._write_journal(directory, journal)
        for index, row in enumerate(plan['oldFiles']):
            reset.transaction._move_owned(root / row['path'], directory / 'old' / str(index),
                                          row, row['identity'])
        for index, row in enumerate(journal['newFiles']):
            target = root / row['path']
            target.parent.mkdir(parents=True, exist_ok=True)
            os.link(directory / 'new' / str(index), target)
        journal['status'] = 'prepared-resources'
        reset._write_journal(directory, journal)
        return directory, journal


class LegacyResetProtocolTests(unittest.TestCase):
    def setUp(self):
        self.case = fixtures.ResourceUpgradeResetTests('runTest')
        self.case.setUp()
        self.addCleanup(self.case.doCleanups)
        self.root = self.case.root
        self.directory, self.journal = prepare_file_only_fixture(self.root)
        self.transaction_id = self.journal['transactionId']
        self.old_snapshot = self.case.old_snapshot(self.journal['plan'])
        self.assertNotIn('oldDirectories', self.journal['plan'])
        self.assertNotIn('createdDirectories', self.journal)
        self.assertFalse((self.root / 'work/pending-installer-upgrade.json').exists())

    def objects(self):
        return {path.relative_to(self.root).as_posix():
                (reset.transaction._identity(path.stat()),
                 path.read_bytes() if path.is_file() else None)
                for path in self.root.rglob('*')}

    def test_old_prepared_cannot_commit_without_directory_evidence(self):
        before = self.objects()
        with self.assertRaisesRegex(reset.ResourceResetError, 'directory-evidence-required'):
            reset.finish_resource_upgrade(self.root, self.transaction_id, setup_succeeded=True)
        self.assertEqual(self.objects(), before)
        self.assertEqual(reset.resource_upgrade_status(self.root)['status'], 'prepared-resources')
        self.assertTrue((self.root / reset.PENDING).exists())
        self.case.unchanged_user_data()
        with self.assertRaisesRegex(ValueError, 'resource-reset-recovery-required'):
            assert_resources_ready(self.root)

    def assert_restored(self):
        self.case.assert_old_restored(self.old_snapshot)
        self.assertEqual(reset.resource_upgrade_status(
            self.root, self.transaction_id)['status'], 'rolled-back')
        assert_resources_ready(self.root)
        # File-only history does not authorize deleting its unrecorded empty
        # publication directories. This asserts file recovery, not that an old
        # installer without directory witnesses can be finalized automatically.
        self.assertFalse((self.root / base.STATE).exists())
        self.assertFalse((self.root / reset.GENERATION).exists())

    def test_old_prepared_rollback_restores_original_files(self):
        result = reset.finish_resource_upgrade(self.root, self.transaction_id)
        self.assertEqual(result['status'], 'rolled-back')
        self.assert_restored()

    def test_old_prepared_recovery_restores_original_files(self):
        self.assertEqual(reset.recover_resource_upgrade(self.root)['status'], 'rolled-back')
        self.assert_restored()

    def test_old_completed_history_does_not_freeze_later_resource_generation(self):
        # Model a commit already durably completed by the old producer. The new
        # implementation is not asked to approve a missing-witness transition.
        self.journal['status'] = 'committed'
        reset._write_journal(self.directory, self.journal)
        reset._clear_pending(self.root, self.transaction_id)
        self.assertEqual(reset.resource_upgrade_status(
            self.root, self.transaction_id)['status'], 'committed')

        # A real subsequent reset changes generation/active object identities.
        plan = reset.inspect_resource_upgrade(self.root)
        later = reset.prepare_resource_upgrade(self.root,
            expected_plan_sha256=plan['planSha256'], confirmed=True)
        reset.finish_resource_upgrade(self.root, later['transactionId'], setup_succeeded=True)
        before = self.objects()
        self.assertEqual(reset.finish_resource_upgrade(self.root, self.transaction_id,
            setup_succeeded=True)['status'], 'committed')
        self.assertEqual(self.objects(), before)
        self.assertEqual(reset.resource_upgrade_status(
            self.root, self.transaction_id)['status'], 'committed')
        self.case.unchanged_user_data()
        assert_resources_ready(self.root)


if __name__ == '__main__':
    unittest.main()
