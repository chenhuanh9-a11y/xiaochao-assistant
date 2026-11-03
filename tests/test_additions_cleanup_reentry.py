"""Durable additive outcomes and partial cleanup, with real isolated ZIP imports."""
import json
import os
import subprocess
import sys
from pathlib import Path
import unittest
from unittest.mock import patch

import star_map_import as importer
import map_package_upgrade as upgrade
import map_package_recovery_plan as recovery
from resource_transaction import assert_resources_ready, resource_write_lock
import test_live_author_import as fixtures


class AdditionsCleanupReentryTests(unittest.TestCase):
    def setUp(self):
        self.f = fixtures.LiveAuthorImportTests('runTest')
        self.f.setUp()
        self.addCleanup(self.f.doCleanups)
        self.root = self.f.root
        self.names = [fixtures.SUJU.replace('floor-2.png', 'floor-1.png'), fixtures.SUJU]
        self.payload = {name: ('display:'+name).encode() for name in self.names}
        self.package = self.f.package('nightmare-suju', self.payload)

    def fail_stage_cleanup(self):
        original = importer._rollback_published
        def remove(path, *args):
            if '/staging-' in path.as_posix() and path.as_posix().endswith(fixtures.SUJU):
                return False
            return original(path, *args)
        return patch.object(importer, '_rollback_published', side_effect=remove)

    def pending_stage(self):
        pending = self.root / upgrade.ADDITIONS_PENDING
        self.assertTrue(pending.exists(), 'Cleanup failure must retain the resource gate')
        with self.assertRaisesRegex(ValueError, 'additions-recovery-required'):
            assert_resources_ready(self.root)
        return self.root / json.loads(pending.read_bytes())['stage']

    def committed_cleanup_failure(self):
        with self.fail_stage_cleanup():
            with self.assertRaisesRegex(ValueError, 'additions-stage-cleanup-incomplete'):
                self.f.live(self.package)
        return self.pending_stage()

    def recover(self):
        with resource_write_lock(self.root):
            plan = recovery.inspect_locked(self.root)
            self.assertIn(plan, recovery.history_locked(self.root))
            return plan, recovery.execute_locked(self.root, plan, 'recover')

    def test_committed_partial_stage_cleanup_keeps_gate_and_reenters_after_user_delete(self):
        stage = self.committed_cleanup_failure()
        self.assertFalse((stage / self.names[0]).exists(), 'Fixture must exercise partial cleanup')
        self.assertTrue((stage / self.names[1]).exists())
        receipt = self.f.receipt().read_bytes()
        (self.root / self.names[0]).unlink()
        plan, result = self.recover()
        self.assertTrue(plan['cleanupOnly'])
        self.assertEqual(result['status'], 'additions-committed')
        self.assertFalse((self.root / self.names[0]).exists())
        self.assertEqual(self.f.receipt().read_bytes(), receipt)
        self.assertEqual((self.root / self.names[1]).read_bytes(), self.payload[self.names[1]])
        self.assertFalse(stage.exists())
        assert_resources_ready(self.root)

    def test_committed_cleanup_preserves_later_replacement_and_deleted_receipt(self):
        self.committed_cleanup_failure()
        path = self.root / self.names[1]
        path.unlink()
        path.write_bytes(b'new-user-object')
        identity = upgrade._identity(path.stat())
        self.f.receipt().unlink()
        _, result = self.recover()
        self.assertEqual(result['status'], 'additions-committed')
        self.assertEqual(path.read_bytes(), b'new-user-object')
        self.assertEqual(upgrade._identity(path.stat()), identity)
        self.assertFalse(self.f.receipt().exists())
        assert_resources_ready(self.root)

    def interrupted(self):
        link = importer.os.link
        def interrupt(source, target, *args, **kwargs):
            link(source, target, *args, **kwargs)
            if Path(target) == self.root / self.names[0]:
                raise KeyboardInterrupt('loss-after-first-addition')
        with patch.object(importer.os, 'link', side_effect=interrupt):
            with self.assertRaises(KeyboardInterrupt):
                self.f.live(self.package)

    def test_rolled_back_partial_cleanup_keeps_gate_without_deleting_later_user_objects(self):
        self.interrupted()
        with resource_write_lock(self.root), self.fail_stage_cleanup():
            with self.assertRaisesRegex(ValueError, 'additions-stage-cleanup-incomplete'):
                upgrade.recover_pending_additions(self.root)
        stage = self.pending_stage()
        for name in self.names:
            self.assertFalse((self.root / name).exists())
        self.f.write(self.names[0], b'later-user-created-object')
        plan, result = self.recover()
        self.assertTrue(plan['cleanupOnly'])
        self.assertEqual(result['status'], 'additions-rolled-back')
        self.assertEqual((self.root / self.names[0]).read_bytes(), b'later-user-created-object')
        self.assertFalse((self.root / self.names[1]).exists())
        self.assertFalse(stage.exists())
        assert_resources_ready(self.root)

    def test_missing_stage_witness_before_terminal_outcome_is_still_rejected(self):
        self.interrupted()
        stage = self.pending_stage()
        (stage / self.names[1]).unlink()
        with resource_write_lock(self.root):
            with self.assertRaisesRegex(ValueError, 'additions-retained-object-changed'):
                upgrade.recover_pending_additions(self.root)
        self.assertTrue((self.root / upgrade.ADDITIONS_PENDING).exists())
        self.assertTrue((self.root / self.names[0]).exists())

    def test_changed_surviving_pin_is_not_deleted_or_used_to_mutate_active_data(self):
        stage = self.committed_cleanup_failure()
        (self.root / self.names[1]).write_bytes(b'user-edited-shared-inode')
        with resource_write_lock(self.root):
            with self.assertRaisesRegex(ValueError, 'additions-retained-object-changed'):
                upgrade.recover_pending_additions(self.root)
        self.assertEqual((stage / self.names[1]).read_bytes(), b'user-edited-shared-inode')
        self.assertEqual((self.root / self.names[1]).read_bytes(), b'user-edited-shared-inode')
        self.pending_stage()

    def test_pointer_clear_failure_is_reentrant_after_payload_cleanup(self):
        original = importer._rollback_published
        def remove(path, *args):
            return False if path == self.root / upgrade.ADDITIONS_PENDING else original(path, *args)
        with patch.object(importer, '_rollback_published', side_effect=remove):
            with self.assertRaisesRegex(ValueError, 'additions-pointer-could-not-be-cleared'):
                self.f.live(self.package)
        stage = self.pending_stage()
        self.assertTrue(all(not (stage / name).exists() for name in self.names))
        self.recover()
        self.assertFalse(stage.exists())
        assert_resources_ready(self.root)

    def test_durable_outcome_survives_write_acknowledgment_failure(self):
        write = upgrade._write_new
        def fail_after_write(path, data):
            write(path, data)
            if path.name == upgrade.ADDITIONS_OUTCOME:
                raise OSError('failure-after-durable-outcome')
        with patch.object(upgrade, '_write_new', side_effect=fail_after_write):
            with self.assertRaisesRegex(OSError, 'failure-after-durable-outcome'):
                self.f.live(self.package)
        self.pending_stage()
        (self.root / self.names[0]).unlink()
        _, result = self.recover()
        self.assertEqual(result['status'], 'additions-committed')
        self.assertFalse((self.root / self.names[0]).exists())
        assert_resources_ready(self.root)

    def test_mismatched_terminal_witness_blocks_before_retained_or_active_mutation(self):
        stage = self.committed_cleanup_failure()
        marker = stage / upgrade.ADDITIONS_OUTCOME
        value = json.loads(marker.read_bytes())
        value['journalSha256'] = '0' * 64
        marker.write_text(json.dumps(value), encoding='utf-8')
        before = {p.relative_to(self.root).as_posix(): p.read_bytes() for p in self.root.rglob('*') if p.is_file()}
        with resource_write_lock(self.root):
            with self.assertRaisesRegex(ValueError, 'additions-outcome-witness-changed'):
                upgrade.recover_pending_additions(self.root)
        after = {p.relative_to(self.root).as_posix(): p.read_bytes() for p in self.root.rglob('*') if p.is_file()}
        self.assertEqual(after, before)
        self.pending_stage()

    def test_directory_cleanup_failure_keeps_gate_and_reenters_with_no_payload_copies(self):
        rmdir = Path.rmdir
        def fail_stage_directory(path):
            if '/staging-' in path.as_posix() and path.name == 'nightmare-03':
                raise OSError('busy-stage-directory')
            return rmdir(path)
        with patch.object(Path, 'rmdir', fail_stage_directory):
            with self.assertRaisesRegex(ValueError, 'additions-stage-cleanup-incomplete'):
                self.f.live(self.package)
        stage = self.pending_stage()
        self.assertTrue(all(not (stage / name).exists() for name in self.names))
        self.recover()
        self.assertFalse(stage.exists())
        assert_resources_ready(self.root)

    def test_only_metadata_can_remain_after_gate_clear_and_registered_removal_cleans_it(self):
        remove = importer._rollback_published
        def busy_metadata(path, *args):
            return False if path.name == upgrade.ADDITIONS_OUTCOME else remove(path, *args)
        with patch.object(importer, '_rollback_published', side_effect=busy_metadata):
            self.f.live(self.package)
        assert_resources_ready(self.root)
        stages = list((self.root / upgrade.STATE).glob('staging-*'))
        self.assertEqual(len(stages), 1)
        stage = stages[0]
        self.assertEqual({p.name for p in stage.iterdir()}, {'addition-journal.json', upgrade.ADDITIONS_OUTCOME})
        import map_package_removal as removal
        plan = removal.inspect_package_removal(self.root, 'nightmare-suju')
        removal.remove_packages(self.root, 'nightmare-suju', application_idle=True,
            expected_plan_sha256=plan['planSha256'])
        self.assertFalse(stage.exists())
        self.assertTrue(all(not (self.root / name).exists() for name in self.names))

    def test_actual_process_exit_during_terminal_cleanup_keeps_gate_and_releases_os_lock(self):
        code = """
import os,sys
from pathlib import Path
import star_map_import as importer
root=Path(sys.argv[1]); package=Path(sys.argv[2]); suffix=sys.argv[3]
remove=importer._rollback_published
def stop_after_delete(path,*args):
    result=remove(path,*args)
    if result and '/staging-' in path.as_posix() and path.as_posix().endswith(suffix):
        os._exit(92)
    return result
importer._rollback_published=stop_after_delete
importer.install_author_package_live(package,root,application_idle=True)
"""
        completed = subprocess.run([sys.executable, '-c', code, str(self.root), str(self.package), self.names[0]],
            env=dict(os.environ, PYTHONPATH=str(Path(__file__).resolve().parents[1] / 'app')),
            capture_output=True, timeout=15)
        self.assertEqual(completed.returncode, 92, completed.stderr)
        stage = self.pending_stage()
        self.assertFalse((stage / self.names[0]).exists())
        self.assertTrue((stage / upgrade.ADDITIONS_OUTCOME).exists())
        plan, result = self.recover()
        self.assertTrue(plan['cleanupOnly'])
        self.assertEqual(result['status'], 'additions-committed')
        self.assertFalse(stage.exists())
        for name, data in self.payload.items():
            self.assertEqual((self.root / name).read_bytes(), data)
        assert_resources_ready(self.root)

    def test_terminal_plan_and_helper_flag_are_bounded_to_cleanup_only_additions(self):
        self.committed_cleanup_failure()
        with resource_write_lock(self.root):
            plan = recovery.inspect_locked(self.root)
        import star_map_recovery_helper as helper
        from client_resource_startup_recovery import _validate_plan, _confirmation_message
        request = dict(schema=helper.SCHEMA, requestId='bounded-request', plan=plan, action='recover')
        self.assertEqual(helper._validate_request(request, 'bounded-request')['plan'], plan)
        self.assertEqual(_validate_plan(plan, 'additions'), plan)
        self.assertIn('不会撤回、恢复或重写当前资源', _confirmation_message(plan, 'additions'))
        for value in (False, 1, 'true'):
            changed = dict(plan, cleanupOnly=value)
            with self.assertRaises(ValueError):
                helper._validate_request(dict(request, plan=changed), 'bounded-request')
            with self.assertRaises(ValueError):
                _validate_plan(changed, 'additions')
        with self.assertRaises(ValueError):
            helper._validate_request(dict(request, plan=dict(plan, status='removal-pending')), 'bounded-request')


if __name__ == '__main__':
    unittest.main()
