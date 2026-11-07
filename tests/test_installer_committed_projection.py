"""Real resource core/journal/permit with fixture lease and native observer.

Temporary byte fixtures only; not a native Setup, registry or Windows handle test.
"""
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import installer_resource_lease as bridge
from installer_upgrade_coordinator import Coordinator
from installer_upgrade_journal import PENDING, read_status
import resource_upgrade_reset as reset
from resource_transaction import assert_resources_ready
import test_installer_upgrade_coordinator as coordinator_fixtures
import test_resource_upgrade_reset as resource_fixtures


class CommittedProjectionTests(unittest.TestCase):
    @contextmanager
    def session(self):
        case = coordinator_fixtures.CoordinatorTests('runTest'); case.setUp()
        fixture = resource_fixtures.ResourceUpgradeResetTests('runTest'); fixture.setUp()
        try:
            case.root = fixture.root; case.args.install_root = str(case.root)
            case.args.candidate_directory = None
            (case.root / 'work/map-packages/old-empty-history').mkdir(parents=True)
            case.args.plan_sha256 = reset.inspect_resource_upgrade(case.root,
                target_identity=case.target)['planSha256']
            owner = bridge._HandleOwner(SimpleNamespace(CloseHandle=lambda handle: True), 123)
            with patch.object(bridge, '_duplicate_verified', return_value=owner):
                with bridge.borrowed_installer_lease(case.root) as lease:
                    def execute(action):
                        case.args.action = action
                        return Coordinator(case.args, reset, case.target, lease, case.sid,
                                           observer=case.observer).execute()
                    for action in ('prepare', 'start-native', 'native-complete'):
                        result = execute(action)
                    yield case, fixture, execute, result['transactionId']
        finally:
            fixture.doCleanups(); case.doCleanups()

    def lose_receipt(self, execute):
        finish = reset.finish_resource_upgrade
        def lose(*args, **kwargs):
            finish(*args, **kwargs)
            raise OSError('fixture lost committed resource response')
        with patch.object(reset, 'finish_resource_upgrade', side_effect=lose):
            with self.assertRaises(OSError):
                execute('commit')

    def assert_blocked(self, case, execute):
        with self.assertRaises(ValueError):
            execute('resume-native-complete')
        self.assertTrue((case.root / PENDING).exists())
        self.assertTrue(read_status(case.root, owner_sid=case.sid)['pending'])
        with self.assertRaisesRegex(ValueError, 'installer-upgrade-recovery-required'):
            assert_resources_ready(case.root)

    def test_lost_commit_receipt_rejects_active_and_quarantine_drift(self):
        for scenario in ('active-bytes', 'active-identity', 'quarantine-identity',
                         'quarantine-directory', 'empty-pending'):
            with self.subTest(scenario=scenario), self.session() as (case, fixture, execute, tx):
                self.lose_receipt(execute)
                directory, journal = reset._load(case.root, tx)
                if scenario.startswith('active'):
                    row = next(row for row in journal['newFiles'] if not row['path'].startswith('work/'))
                    changed = case.root / row['path']
                    if scenario == 'active-identity':
                        data = changed.read_bytes(); changed.rename(directory / 'original-active')
                        changed.write_bytes(data)
                    else:
                        changed.write_bytes(b'changed')
                elif scenario == 'quarantine-identity':
                    changed = directory / 'old/0'; data = changed.read_bytes()
                    changed.rename(directory / 'original-old'); changed.write_bytes(data)
                elif scenario == 'quarantine-directory':
                    # Inject an empty directory into an inventoried old skeleton.
                    roots = list((directory / 'old-directories').iterdir())
                    if not roots:
                        self.fail('fixture must exercise an archived directory')
                    (roots[0] / 'foreign').mkdir()
                else:
                    (case.root / 'work/map-packages/pending-author-snapshot.json').mkdir()
                self.assert_blocked(case, execute)

    def test_late_drift_after_finish_is_rechecked_before_finalize(self):
        with self.session() as (case, fixture, execute, tx):
            finish = reset.finish_resource_upgrade
            def changed_after_finish(*args, **kwargs):
                result = finish(*args, **kwargs)
                _, journal = reset._load(case.root, tx)
                row = next(row for row in journal['newFiles'] if not row['path'].startswith('work/'))
                (case.root / row['path']).write_bytes(b'changed after first successful verification')
                return result
            with patch.object(reset, 'finish_resource_upgrade', side_effect=changed_after_finish):
                with self.assertRaises(ValueError):
                    execute('commit')
            self.assertTrue((case.root / PENDING).exists())
            self.assertEqual(read_status(case.root, owner_sid=case.sid)['status'], 'native-complete')

    def test_lost_receipt_without_drift_resumes_and_completed_history_does_not_freeze_resources(self):
        with self.session() as (case, fixture, execute, tx):
            self.lose_receipt(execute)
            result = execute('resume-native-complete')
            self.assertTrue(result['installerFinalized']); self.assertTrue(result['restoreWatcher'])
            self.assertFalse((case.root / PENDING).exists())
            # A later fully authorized reset is a real resource generation change.
            plan = reset.inspect_resource_upgrade(case.root)
            later = reset.prepare_resource_upgrade(case.root,
                expected_plan_sha256=plan['planSha256'], confirmed=True)
            reset.finish_resource_upgrade(case.root, later['transactionId'], setup_succeeded=True)
            self.assertEqual(execute('commit')['status'], 'committed')
            self.assertEqual(reset.resource_upgrade_status(case.root, tx)['status'], 'committed')
            assert_resources_ready(case.root)

    def test_strong_verification_requires_fresh_installer_permit(self):
        with self.session() as (case, fixture, execute, tx):
            self.lose_receipt(execute)
            with self.assertRaisesRegex(ValueError, 'installer-upgrade-recovery-required'):
                reset.resource_upgrade_status(case.root, tx, verify_projection=True)
            self.assertTrue((case.root / PENDING).exists())

    def test_old_file_only_journal_cannot_forward_without_directory_evidence(self):
        with self.session() as (case, fixture, execute, tx):
            self.lose_receipt(execute)
            directory, journal = reset._load(case.root, tx)
            # Preserve the already bound plan while modeling an old journal
            # lacking the new creation witness. No new evidence may be inferred.
            journal.pop('createdDirectories')
            reset._write_journal(directory, journal)
            self.assert_blocked(case, execute)
            self.assertEqual(reset.resource_upgrade_status(case.root, tx)['status'], 'committed')


if __name__ == '__main__':
    unittest.main()
