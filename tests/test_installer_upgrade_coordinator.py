"""Offline orchestration fixtures; no Windows registry, shell or real user writes."""
import contextlib
import copy
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch
import uuid

from installer_upgrade_coordinator import Coordinator
from installer_upgrade_journal import read_status, PENDING


class Resources:
    def __init__(self, args, target):
        self.args, self.target = args, target
        self.tx = str(uuid.uuid4())
        self.state, self.pending = None, False
        self.lost = False
        self.restored = True
        self.writes = []

    def resource_upgrade_status(self, root, transaction_id=None):
        if transaction_id is None and not self.pending:
            return dict(status='not-needed')
        assert transaction_id is None or transaction_id == self.tx
        return dict(status=self.state, transactionId=self.tx, planSha256=self.args.plan_sha256,
                    targetIdentity=copy.deepcopy(self.target))

    def prepare_resource_upgrade(self, root, **kwargs):
        assert (Path(root) / PENDING).exists(), 'journal must precede resource mutation'
        self.writes.append('prepare')
        self.state, self.pending, self.restored = 'prepared-resources', True, False
        if self.lost:
            raise OSError('lost prepare receipt')
        return self.resource_upgrade_status(root)

    def finish_resource_upgrade(self, root, tx, *, setup_succeeded):
        self.writes.append('commit' if setup_succeeded else 'rollback')
        self.state = 'committed' if setup_succeeded else 'rolled-back'
        self.pending = False
        self.restored = not setup_succeeded

    def inspect_resource_upgrade(self, root, **kwargs):
        assert kwargs['target_identity'] == self.target
        return dict(planSha256=self.args.plan_sha256 if self.restored else '0' * 64)


class Observer:
    def __init__(self, target):
        self.target = target
        self.context = dict(target_identity=target, target_report_sha256='d' * 64)
        self.match = True

    def preflight_native_evidence(self, root, **context):
        return dict(knownBytes=0, knownNodes=0)  # explicit orchestration-only stub

    def capture_native_completion(self, root, **context):
        return dict(test='native-evidence', target=context['target_identity'])

    def verify_native_completion(self, evidence, root, **context):
        if not self.match or context != self.context:
            raise ValueError('native changed')
        return evidence  # real observer returns dict, journal requires literal True

    def resume_context_from_evidence(self, evidence, root):
        return self.context


class CoordinatorTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.sid = 'S-1-5-21-1-2-3-1000'
        self.target = dict(sourceCommit='a' * 40, displayVersion='1.2.3', numericVersion='1.2.3.4',
                           executableSha256='b' * 64, resourceInventorySha256='c' * 64)
        self.args = SimpleNamespace(action='prepare', install_root=str(self.root), confirmed=True,
            session_id=str(uuid.uuid4()), target_report_sha256='d' * 64, plan_sha256='e' * 64,
            restore_watcher=True, candidate_directory='fixture-candidate', transaction_id=None)
        self.api = Resources(self.args, self.target)
        self.observer = Observer(self.target)
        self.context_patch = patch('installer_upgrade_coordinator.native_context', return_value=self.observer.context)
        self.context_patch.start()
        self.addCleanup(self.context_patch.stop)

    def execute(self, action, target=None):
        self.args.action = action
        return Coordinator(self.args, self.api, target or self.target, object(), self.sid,
            observer=self.observer, scope=lambda *a, **k: contextlib.nullcontext()).execute()

    def complete_native(self):
        self.execute('prepare')
        self.execute('start-native')
        self.execute('native-complete')

    def test_entire_success_and_historical_status(self):
        self.complete_native()
        result = self.execute('commit')
        self.assertEqual(result['status'], 'committed')
        self.assertTrue(result['installerFinalized'])
        self.assertTrue(result['resourceCommitted'])
        self.assertTrue(result['restoreWatcher'])
        self.assertFalse((self.root / PENDING).exists())
        self.assertEqual(self.execute('status')['status'], 'committed')
        self.assertEqual(self.execute('commit')['status'], 'committed')

    def test_start_native_reloads_context_and_rejects_changed_inventory(self):
        self.execute('prepare')
        with patch('installer_upgrade_coordinator.native_context',
                   side_effect=ValueError('installer-program-inventory-changed')) as fresh:
            with self.assertRaisesRegex(ValueError, 'installer-program-inventory-changed'):
                self.execute('start-native')
        fresh.assert_called_once_with(self.args, self.target)
        self.assertEqual(read_status(self.root, owner_sid=self.sid)['status'], 'prepared')
        self.assertEqual(self.api.writes, ['prepare'])

    def test_resume_never_applies_new_budget_to_old_completed_evidence(self):
        self.complete_native()
        with patch('installer_upgrade_journal._remove_owned_marker', side_effect=OSError('clear failed')):
            with self.assertRaises(OSError):
                self.execute('commit')
        with patch.object(self.observer, 'preflight_native_evidence', side_effect=AssertionError('new budget')), \
                patch('installer_upgrade_coordinator.native_context', side_effect=AssertionError('new package')):
            self.assertEqual(self.execute('resume-native-complete', dict(self.target, sourceCommit='f'*40))['status'], 'committed')

    def test_prepare_lost_receipt_restores_same_plan(self):
        self.api.lost = True
        with self.assertRaises(OSError):
            self.execute('prepare')
        self.assertIsNone(read_status(self.root, owner_sid=self.sid)['resourceTransactionId'])
        self.assertEqual(self.execute('rollback')['status'], 'aborted')
        self.assertEqual(self.api.writes, ['prepare', 'rollback'])
        self.assertFalse((self.root / PENDING).exists())

    def test_native_boundary_blocks_rollback_and_second_start(self):
        self.execute('prepare')
        self.execute('start-native')
        for action in ['rollback', 'start-native', 'commit', 'resume-native-complete']:
            with self.assertRaises(ValueError):
                self.execute(action)
        self.assertEqual(self.api.writes, ['prepare'])
        self.assertTrue((self.root / PENDING).exists())

    def test_missing_resource_marker_cannot_start_native(self):
        self.execute('prepare')
        self.api.pending = False
        with self.assertRaises(ValueError):
            self.execute('start-native')
        self.assertEqual(read_status(self.root, owner_sid=self.sid)['status'], 'prepared')

    def test_native_changed_never_commits(self):
        self.complete_native()
        self.observer.match = False
        with self.assertRaises(ValueError):
            self.execute('commit')
        self.assertEqual(self.api.writes, ['prepare'])
        self.assertTrue((self.root / PENDING).exists())

    def test_resume_uses_old_context_not_new_package(self):
        self.complete_native()
        newer = dict(self.target, sourceCommit='f' * 40)
        self.assertEqual(self.execute('resume-native-complete', newer)['status'], 'committed')

    def test_resource_commit_receipt_loss_resumes_forward(self):
        self.complete_native()
        finish = self.api.finish_resource_upgrade
        def lose(*args, **kwargs):
            finish(*args, **kwargs)
            raise OSError('lost resource commit receipt')
        with patch.object(self.api, 'finish_resource_upgrade', side_effect=lose):
            with self.assertRaises(OSError):
                self.execute('commit')
        pending = self.execute('installer-pending')
        self.assertEqual(pending['installerStatus'], 'native-complete')
        self.assertTrue(pending['resourceCommitted'])
        self.assertFalse(pending['installerFinalized'])
        self.assertNotIn('nativeEvidence', pending)
        self.assertEqual(self.execute('resume-native-complete')['status'], 'committed')

    def test_resume_rechecks_native_program_objects(self):
        self.complete_native()
        self.observer.match = False
        with self.assertRaises(ValueError):
            self.execute('resume-native-complete')
        self.assertEqual(self.api.writes, ['prepare'])
        self.assertTrue((self.root / PENDING).exists())

    def test_completed_marker_clear_failure_retries_forward(self):
        self.complete_native()
        with patch('installer_upgrade_journal._remove_owned_marker', side_effect=OSError('clear failed')):
            with self.assertRaises(OSError):
                self.execute('commit')
        result = self.execute('installer-pending')
        self.assertEqual(result['installerStatus'], 'completed')
        self.assertTrue(result['resourceCommitted'])
        self.assertFalse(result['installerFinalized'])
        self.assertNotEqual(result['status'], 'committed')
        self.assertEqual(self.execute('resume-native-complete')['status'], 'committed')

    def test_native_complete_receipt_loss_is_resumable(self):
        from installer_upgrade_journal import InstallerUpgradeJournal
        self.execute('prepare')
        self.execute('start-native')
        original = InstallerUpgradeJournal.record_native_complete
        def lose(journal, *args, **kwargs):
            original(journal, *args, **kwargs)
            raise OSError('native completion receipt lost')
        with patch.object(InstallerUpgradeJournal, 'record_native_complete', lose):
            with self.assertRaises(OSError):
                self.execute('native-complete')
        self.assertEqual(self.execute('installer-pending')['installerStatus'], 'native-complete')
        self.assertEqual(self.execute('resume-native-complete')['status'], 'committed')

    def test_unbound_abort_requires_reinspection_not_absence(self):
        coordinator = Coordinator(self.args, self.api, self.target, object(), self.sid)
        coordinator._journal().begin()
        self.api.restored = False
        with self.assertRaises(ValueError):
            self.execute('rollback')
        self.assertTrue((self.root / PENDING).exists())

    def test_unknown_marker_and_wrong_session_fail_closed(self):
        self.execute('prepare')
        self.args.session_id = str(uuid.uuid4())
        with self.assertRaises(ValueError):
            self.execute('rollback')
        self.assertEqual(self.api.writes, ['prepare'])

    def test_unbegun_status_and_restoration(self):
        self.assertEqual(self.execute('status')['status'], 'not-needed')
        self.api.restored = False
        with self.assertRaises(ValueError):
            self.execute('rollback')
        self.api.restored = True
        self.assertEqual(self.execute('rollback')['status'], 'aborted')

    def test_real_core_and_borrowed_scope_restore_exact_original(self):
        import installer_resource_lease as bridge
        import resource_upgrade_reset as reset
        import test_resource_upgrade_reset as fixtures
        fixture = fixtures.ResourceUpgradeResetTests('runTest')
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        self.root = fixture.root
        self.args.install_root = str(self.root)
        self.args.candidate_directory = None
        plan = reset.inspect_resource_upgrade(self.root, target_identity=self.target)
        self.args.plan_sha256 = plan['planSha256']
        snapshot = fixture.old_snapshot(plan)
        owner = bridge._HandleOwner(SimpleNamespace(CloseHandle=lambda handle: True), 123)
        with patch.object(bridge, '_duplicate_verified', return_value=owner):
            with bridge.borrowed_installer_lease(self.root) as lease:
                coordinator = Coordinator(self.args, reset, self.target, lease, self.sid,
                                          observer=self.observer)
                self.args.action = 'prepare'
                self.assertEqual(coordinator.execute()['resourceStatus'], 'prepared-resources')
                self.args.action = 'rollback'
                self.assertEqual(coordinator.execute()['status'], 'aborted')
                fixture.assert_old_restored(snapshot)
                self.assertFalse((self.root / PENDING).exists())

    def test_real_core_forward_commit_with_fresh_scopes(self):
        import installer_resource_lease as bridge
        import resource_upgrade_reset as reset
        import test_resource_upgrade_reset as fixtures
        fixture = fixtures.ResourceUpgradeResetTests('runTest')
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        self.root = fixture.root
        self.args.install_root = str(self.root)
        self.args.candidate_directory = None
        self.args.plan_sha256 = reset.inspect_resource_upgrade(
            self.root, target_identity=self.target)['planSha256']
        owner = bridge._HandleOwner(SimpleNamespace(CloseHandle=lambda handle: True), 123)
        with patch.object(bridge, '_duplicate_verified', return_value=owner):
            with bridge.borrowed_installer_lease(self.root) as lease:
                for action in ('prepare', 'start-native', 'native-complete', 'commit'):
                    self.args.action = action
                    result = Coordinator(self.args, reset, self.target, lease, self.sid,
                                         observer=self.observer).execute()
                self.assertTrue(result['installerFinalized'])
                self.assertTrue(result['resourceCommitted'])
                self.assertFalse((self.root / PENDING).exists())
                self.assertFalse((self.root / reset.PENDING).exists())
                fixture.unchanged_user_data()

    def test_helper_routes_all_mutations_through_coordinator(self):
        import ast
        import inspect
        import star_map_reset_helper as helper
        tree = ast.parse(inspect.getsource(helper.run))
        branches = [node for node in ast.walk(tree) if isinstance(node, ast.If)
                    and isinstance(node.test, ast.Compare)
                    and isinstance(node.test.left, ast.Attribute)
                    and node.test.left.attr == 'action']
        legacy = next(node for node in branches if isinstance(node.test.comparators[0], ast.Set))
        actions = {item.value for item in legacy.test.comparators[0].elts}
        self.assertEqual(actions, {'inspect', 'pending', 'resume-committed'})
        source = ast.unparse(legacy.orelse)
        self.assertIn('Coordinator(', source)
        self.assertNotIn('_execute(', source)

    def test_unconfirmed_prepare_never_creates_marker(self):
        self.args.confirmed = False
        with self.assertRaises(ValueError):
            self.execute('prepare')
        self.assertFalse((self.root / PENDING).exists())


if __name__ == '__main__':
    unittest.main()
