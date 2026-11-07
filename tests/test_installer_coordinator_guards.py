"""Real temporary journal/reset fixtures; only Win32 handle borrowing is stubbed."""
from contextlib import contextmanager
import os
from pathlib import Path
import unittest
import uuid
from unittest.mock import Mock, patch

import test_resource_upgrade_reset as fixtures
import installer_resource_lease as lease_api
import installer_upgrade_journal as journal
import managed_base_resources as base
import resource_transaction as transaction
import resource_upgrade_reset as reset


class InstallerCoordinatorGuardsTests(unittest.TestCase):
    def setUp(self):
        self.case = fixtures.ResourceUpgradeResetTests('runTest')
        self.case.setUp()
        self.addCleanup(self.case.doCleanups)
        self.root = self.case.root
        self.plan = self.case.plan()
        self.sid = 'S-1-5-21-1-2-3-1000'
        self.session = str(uuid.uuid4())
        self.instance = journal.InstallerUpgradeJournal(
            self.root, owner_sid=self.sid, session_id=self.session,
            target_identity=dict(sourceCommit='a' * 40, displayVersion='1.1.2',
                                 numericVersion='1.0.0.24', executableSha256='b' * 64,
                                 resourceInventorySha256='c' * 64),
            report_sha256='d' * 64, plan_sha256=self.plan['planSha256'])
        self.marker = self.root / journal.PENDING

    @contextmanager
    def borrowed(self):
        # No OS verification claim: simulate its successful result, preserving
        # the production handle owner, registry, scope and cleanup behavior.
        owner = lease_api._HandleOwner(Mock(), 123)
        with patch.object(lease_api, '_duplicate_verified', return_value=owner):
            with lease_api.borrowed_installer_lease(self.root) as lease:
                yield lease
        self.assertIsNone(owner.handle)

    def scope(self, lease):
        return lease_api.installer_coordinator_scope(
            self.root, lease, self.session,
            validate=lambda: journal.read_status(self.root, owner_sid=self.sid))

    def begin(self):
        self.instance.begin()

    def prepare(self, lease):
        self.begin()
        with self.scope(lease):
            outcome = self.case.prepare(self.plan)
        self.tx = outcome['transactionId']
        self.instance.bind_resource(self.tx, verify_resource=lambda _: True)
        return outcome

    def assert_blocked(self):
        with self.assertRaisesRegex(ValueError, 'installer-upgrade-recovery-required'):
            transaction.assert_installer_upgrade_ready(self.root)
        self.case.unchanged_user_data()

    def test_runtime_lease_cannot_create_coordinator_scope(self):
        self.begin()
        with transaction.resource_runtime_lease(self.root) as lease:
            with self.assertRaisesRegex(ValueError, 'verified-borrowed-lease'):
                with self.scope(lease):
                    self.fail('ordinary runtime accepted')
            self.assert_blocked()

    def test_verified_borrowed_lease_reads_strict_journal_and_scopes_authority(self):
        self.begin()
        with self.borrowed() as lease:
            self.assert_blocked()
            with self.scope(lease) as record:
                self.assertEqual(record['status'], 'prepared')
                self.assertEqual(record['sessionId'], self.session)
                transaction.assert_resources_ready(self.root)
                self.assertEqual(journal.read_status(self.root, owner_sid=self.sid), record)
            self.assert_blocked()
        self.assert_blocked()

    def test_duck_typed_permit_cannot_authorize_runtime(self):
        self.begin()
        with transaction.resource_runtime_lease(self.root) as lease:
            lease._installer_forward_permit = Mock()
            lease._installer_forward_permit.allows.return_value = True
            self.assert_blocked()
            lease._installer_forward_permit.allows.assert_not_called()

    def test_malformed_generation_revokes_existing_capability(self):
        self.begin()
        with self.borrowed() as lease, self.scope(lease):
            generations = self.root / journal.HISTORY / self.session / 'generations'
            latest = sorted(generations.iterdir())[-1]
            latest.write_bytes(b'{broken')
            self.assert_blocked()

    def test_wrong_session_and_nested_capability_rejected(self):
        self.begin()
        with self.borrowed() as lease:
            with self.assertRaisesRegex(ValueError, 'identity-mismatch'):
                with lease_api.installer_coordinator_scope(self.root, lease, str(uuid.uuid4()),
                        validate=lambda: journal.read_status(self.root, owner_sid=self.sid)):
                    self.fail('wrong session accepted')
            with self.scope(lease):
                with self.assertRaisesRegex(ValueError, 'verified-borrowed-lease'):
                    with self.scope(lease):
                        self.fail('nested capability accepted')

    def test_same_bytes_foreign_inode_revokes_capability(self):
        self.begin()
        with self.borrowed() as lease, self.scope(lease):
            old_inode = self.marker.stat().st_ino
            replacement = self.marker.with_suffix('.replacement')
            replacement.write_bytes(self.marker.read_bytes())
            os.replace(replacement, self.marker)
            self.assertNotEqual(self.marker.stat().st_ino, old_inode)
            self.assert_blocked()

    def test_changed_bytes_revoke_capability_even_with_same_inode(self):
        self.begin()
        with self.borrowed() as lease, self.scope(lease):
            old_inode = self.marker.stat().st_ino
            self.marker.write_bytes(b'{}')
            self.assertEqual(self.marker.stat().st_ino, old_inode)
            self.assert_blocked()

    def test_malformed_marker_never_creates_capability(self):
        self.begin()
        self.marker.write_bytes(b'{broken')
        with self.borrowed() as lease:
            with self.assertRaises(ValueError):
                with self.scope(lease):
                    self.fail('malformed journal accepted')
            self.assert_blocked()

    def test_handle_closed_during_scope_revokes_capability(self):
        self.begin()
        with self.borrowed() as lease, self.scope(lease):
            lease._stream.close()
            self.assert_blocked()

    def test_ended_lease_capability_cannot_be_reused_on_new_lease(self):
        self.begin()
        with self.borrowed() as lease:
            with self.scope(lease):
                saved = lease._installer_forward_permit
                lease.close()
                self.assertFalse(saved.allows(self.root, self.marker))
        with self.borrowed() as other:
            other._installer_forward_permit = saved
            self.assert_blocked()

    def test_marker_change_during_validation_rejected(self):
        self.begin()
        def validate():
            record = journal.read_status(self.root, owner_sid=self.sid)
            self.marker.write_bytes(b'{}')
            return record
        with self.borrowed() as lease:
            with self.assertRaisesRegex(ValueError, 'marker-changed'):
                with lease_api.installer_coordinator_scope(self.root, lease, self.session, validate=validate):
                    self.fail('changed marker accepted')
            self.assert_blocked()

    def test_marker_blocks_startup_repair_and_public_recovery_even_under_permit(self):
        self.begin()
        with transaction.resource_runtime_lease(self.root):
            for action in (lambda: transaction.assert_resources_ready(self.root),
                           lambda: base.repair_managed_base_resources(self.root),
                           lambda: reset.recover_resource_upgrade(self.root)):
                with self.assertRaisesRegex(ValueError, 'installer-upgrade-recovery-required'):
                    action()
        with self.borrowed() as lease, self.scope(lease):
            for action in (lambda: base.repair_managed_base_resources(self.root),
                           lambda: reset.recover_resource_upgrade(self.root)):
                with self.assertRaisesRegex(ValueError, 'installer-upgrade-recovery-required'):
                    action()
        self.case.unchanged_user_data()
        self.assertTrue(self.marker.exists())

    def test_pre_native_rollback_preserves_original_resource_identities(self):
        old = self.case.old_snapshot(self.plan)
        with self.borrowed() as lease:
            self.prepare(lease)
            with self.scope(lease):
                with self.assertRaisesRegex(ValueError, 'direction-not-authorized'):
                    reset.finish_resource_upgrade(self.root, self.tx, setup_succeeded=True)
                result = reset.finish_resource_upgrade(self.root, self.tx, setup_succeeded=False)
            self.assertEqual(result['status'], 'rolled-back')
        self.case.assert_old_restored(old)

    def test_native_start_forbids_both_directions_until_verified_completion(self):
        with self.borrowed() as lease:
            self.prepare(lease)
            self.instance.start_native(verify_resource=lambda _: True)
            with self.scope(lease):
                for succeeded in (False, True):
                    with self.assertRaisesRegex(ValueError, 'direction-not-authorized'):
                        reset.finish_resource_upgrade(self.root, self.tx, setup_succeeded=succeeded)
                self.assertEqual(reset.resource_upgrade_status(self.root, self.tx)['status'], 'prepared-resources')
            self.instance.record_native_complete({'syntheticNative': True}, verify_native=lambda *_: True)
            with self.scope(lease):
                with self.assertRaisesRegex(ValueError, 'direction-not-authorized'):
                    reset.finish_resource_upgrade(self.root, self.tx, setup_succeeded=False)
                result = reset.finish_resource_upgrade(self.root, self.tx, setup_succeeded=True)
                self.assertEqual(result['status'], 'committed')
                with self.assertRaisesRegex(ValueError, 'direction-not-authorized'):
                    reset.finish_resource_upgrade(self.root, self.tx, setup_succeeded=False)
        self.case.unchanged_user_data()
        self.assertFalse(self.case.old.exists())
        self.assertTrue(self.marker.exists())

    def test_wrong_transaction_or_plan_cannot_use_native_completion(self):
        with self.borrowed() as lease:
            self.prepare(lease)
            self.instance.start_native(verify_resource=lambda _: True)
            self.instance.record_native_complete({'syntheticNative': True}, verify_native=lambda *_: True)
            with self.scope(lease):
                for tx, digest in ((str(uuid.uuid4()), self.plan['planSha256']), (self.tx, 'f' * 64)):
                    with self.assertRaisesRegex(ValueError, 'direction-not-authorized'):
                        transaction.assert_installer_resource_action(self.root, 'commit', tx, digest)
        self.case.unchanged_user_data()

    def test_journal_native_transition_revokes_prepared_rollback_authority(self):
        with self.borrowed() as lease:
            self.prepare(lease)
            with self.scope(lease):
                self.instance.start_native(verify_resource=lambda _: True)
                with self.assertRaises(ValueError):
                    transaction.assert_installer_resource_action(self.root, 'rollback', self.tx, self.plan['planSha256'])
        self.case.unchanged_user_data()


if __name__ == '__main__':
    unittest.main()
