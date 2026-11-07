"""Upgrade reset on isolated byte fixtures, not a native Setup execution."""
import hashlib
import json
import os
from pathlib import Path
import unittest
import uuid
from unittest.mock import patch
from types import SimpleNamespace

import managed_base_resources as base
import resource_upgrade_reset as reset
from resource_transaction import assert_resources_ready, resource_runtime_lease
import test_managed_base_resources as fixtures


class ResourceUpgradeResetTests(unittest.TestCase):
    def setUp(self):
        self.case = fixtures.ManagedBaseResourcesTests('runTest')
        self.case.setUp(); self.addCleanup(self.case.doCleanups)
        self.case.project()
        self.root = self.case.target
        self.write = self.case.write
        self.old = self.root / '_internal/references/old-orphan.json'
        self.write(self.old, b'unclassified original resource')
        self.protected = {}
        for name in ('assets/custom-maps/personal.png', '_internal/assets/custom-maps/custom.json',
                     'work/user-routes/route.json', 'work/nightmare-memory/ui-settings.json',
                     'work/seasons/s1/difficult-memory/learning.json',
                     '_internal/references/build-fingerprint.txt', 'private-note.txt'):
            path = self.root / name
            self.write(path, ('preserved:' + name).encode())
            self.protected[path] = (path.read_bytes(), path.stat().st_ino)

    def plan(self):
        return reset.inspect_resource_upgrade(self.root)

    def prepare(self, plan=None):
        plan = plan or self.plan()
        return reset.prepare_resource_upgrade(self.root, expected_plan_sha256=plan['planSha256'], confirmed=True)

    def unchanged_user_data(self):
        for path, expected in self.protected.items():
            self.assertEqual((path.read_bytes(), path.stat().st_ino), expected, str(path))

    def old_snapshot(self, plan):
        return {self.root / row['path']: (row['sha256'], row['identity']) for row in plan['oldFiles']}

    def assert_old_restored(self, snapshot):
        for path, (digest, identity) in snapshot.items():
            self.assertEqual(hashlib.sha256(path.read_bytes()).hexdigest(), digest)
            self.assertEqual(base.transaction._identity(path.stat()), identity)
        self.unchanged_user_data()
        self.assertFalse((self.root / reset.PENDING).exists())

    def test_preview_is_exact_and_does_not_claim_labels_as_ownership(self):
        for version, numeric in (('1.1.0', '1.0.0.22'), ('1.1.1', '1.0.0.23'), ('1.0.2', '1.0.0.2')):
            with self.subTest(version=version):
                self.write(self.root / '_internal/version.json', json.dumps(dict(
                    schema='identityv-app-version-v1', version=version, displayVersion=version,
                    numericVersion=numeric)).encode())
                plan = self.plan()
                self.assertEqual(plan['observedIdentity'][0]['observed']['version'], version)
                self.assertIn('not ownership proof', plan['observedIdentity'][0]['authority'])
                self.assertEqual(plan['oldFiles'][0]['classification'], 'unclassified-resource')
                self.assertFalse((self.root / reset.HISTORY).exists())
                self.unchanged_user_data()

    def test_explicit_confirmation_and_current_plan_required(self):
        plan = self.plan()
        with self.assertRaisesRegex(reset.ResourceResetError, 'explicit-confirmation'):
            reset.prepare_resource_upgrade(self.root, expected_plan_sha256=plan['planSha256'])
        self.old.write_bytes(b'changed after preview')
        with self.assertRaisesRegex(reset.ResourceResetError, 'plan-changed'):
            self.prepare(plan)
        self.assertEqual(self.old.read_bytes(), b'changed after preview')
        self.assertFalse((self.root / reset.PENDING).exists())

    def test_prepare_blocks_readers_commit_keeps_quarantine_and_new_plane_healthy(self):
        plan = self.plan(); snapshot = self.old_snapshot(plan)
        with resource_runtime_lease(self.root):
            outcome = self.prepare(plan)
            self.assertEqual(outcome['status'], 'prepared-resources')
            self.assertFalse(self.old.exists())
            with self.assertRaisesRegex(ValueError, 'resource-reset-recovery-required'):
                assert_resources_ready(self.root)
            with self.assertRaisesRegex(ValueError, 'resource-reset-recovery-required'):
                base.repair_managed_base_resources(self.root)
            result = reset.finish_resource_upgrade(self.root, outcome['transactionId'], setup_succeeded=True)
        self.assertEqual(result['status'], 'committed')
        directory = Path(result['retainedDirectory'])
        for index, row in enumerate(plan['oldFiles']):
            path = directory / 'old' / str(index)
            self.assertEqual((hashlib.sha256(path.read_bytes()).hexdigest(), base.transaction._identity(path.stat())),
                             snapshot[self.root / row['path']])
        self.assertEqual(base.repair_managed_base_resources(self.root)['status'], 'healthy')
        self.assertFalse((self.root / reset.PENDING).exists())
        self.unchanged_user_data()

    def test_installer_cancel_restores_all_original_object_identities(self):
        plan = self.plan(); snapshot = self.old_snapshot(plan)
        outcome = self.prepare(plan)
        self.assertEqual(reset.finish_resource_upgrade(self.root, outcome['transactionId'])['status'], 'rolled-back')
        self.assert_old_restored(snapshot)
        self.assertFalse((self.root / base.STATE).exists())
        self.assertFalse((self.root / reset.GENERATION).exists())

    def test_modified_receipt_payload_is_quarantined_not_lost_or_kept_active(self):
        package = 'nightmare-suju'
        path = '_internal/assets/suju/personal-edit.png'
        declared = self.case.row(path, b'original package bytes')
        receipt = dict(packageId=package, revision=1, archiveSha256='a' * 64, files=[declared])
        self.write(self.root / path, b'user edited package image')
        self.write(self.root / 'work/map-packages/nightmare-suju.json', json.dumps(receipt).encode())
        plan = self.plan()
        self.assertIn(package, plan['packagesToDownload'])
        self.assertEqual(plan['modifiedCount'], 1)
        snapshot = self.old_snapshot(plan)
        outcome = self.prepare(plan)
        self.assertFalse((self.root / path).exists())
        reset.finish_resource_upgrade(self.root, outcome['transactionId'], setup_succeeded=True)
        index = next(i for i, row in enumerate(plan['oldFiles']) if row['path'] == path)
        retained = Path(outcome['retainedDirectory']) / 'old' / str(index)
        self.assertEqual(retained.read_bytes(), b'user edited package image')
        self.assertEqual(base.transaction._identity(retained.stat()), snapshot[self.root / path][1])
        self.unchanged_user_data()

    def test_missing_or_corrupt_old_receipts_do_not_authorize_deletion(self):
        self.write(self.root / 'work/map-packages/nightmare-suju.json', b'broken old receipt')
        self.write(self.root / '_internal/assets/suju/unregistered.png', b'unknown image')
        plan = self.plan()
        self.assertEqual(plan['packagesToDownload'], [])
        self.assertGreaterEqual(plan['unclassifiedCount'], 2)
        snapshot = self.old_snapshot(plan)
        outcome = self.prepare(plan)
        reset.finish_resource_upgrade(self.root, outcome['transactionId'])
        self.assert_old_restored(snapshot)

    def test_partial_quarantine_failure_restores_all_old_files(self):
        self.write(self.root / '_internal/assets/unknown.png', b'unknown image')
        plan = self.plan(); snapshot = self.old_snapshot(plan)
        move = reset.transaction._move_owned
        calls = 0
        def fail(source, destination, *args):
            nonlocal calls
            if str(destination).startswith(str(self.root / reset.HISTORY)):
                calls += 1
                if calls == 2:
                    raise OSError('injected quarantine failure')
            return move(source, destination, *args)
        with patch.object(reset.transaction, '_move_owned', side_effect=fail), self.assertRaisesRegex(OSError, 'injected'):
            self.prepare(plan)
        self.assert_old_restored(snapshot)

    def test_process_loss_after_move_is_recoverable_without_claiming_success(self):
        plan = self.plan(); snapshot = self.old_snapshot(plan)
        move = reset.transaction._move_owned
        def interrupt(source, destination, *args):
            result = move(source, destination, *args)
            raise KeyboardInterrupt('lost after moving old object')
        with patch.object(reset.transaction, '_move_owned', side_effect=interrupt), self.assertRaises(KeyboardInterrupt):
            self.prepare(plan)
        self.assertTrue((self.root / reset.PENDING).exists())
        self.assertEqual(reset.recover_resource_upgrade(self.root)['status'], 'rolled-back')
        self.assert_old_restored(snapshot)

    def test_process_loss_after_new_link_restores_old_and_removes_only_pinned_new_files(self):
        plan = self.plan(); snapshot = self.old_snapshot(plan)
        link = reset.os.link
        def interrupt(source, target, *args, **kwargs):
            result = link(source, target, *args, **kwargs)
            if Path(target) == self.root / '_internal/difficult-native-manifest.json':
                raise KeyboardInterrupt('lost after publishing new object')
            return result
        with patch.object(reset.os, 'link', side_effect=interrupt), self.assertRaises(KeyboardInterrupt):
            self.prepare(plan)
        self.assertTrue((self.root / reset.PENDING).exists())
        self.assertEqual(reset.recover_resource_upgrade(self.root)['status'], 'rolled-back')
        self.assert_old_restored(snapshot)
        self.assertFalse((self.root / '_internal/difficult-native-manifest.json').exists())

    def test_foreign_reappeared_old_file_blocks_whole_rollback_and_keeps_both(self):
        outcome = self.prepare()
        self.old.write_bytes(b'external new object')
        before = (self.root / '_internal/difficult-native-manifest.json').read_bytes()
        with self.assertRaisesRegex(reset.ResourceResetError, 'rollback-blocked'):
            reset.finish_resource_upgrade(self.root, outcome['transactionId'])
        self.assertEqual(self.old.read_bytes(), b'external new object')
        self.assertEqual((self.root / '_internal/difficult-native-manifest.json').read_bytes(), before)
        self.assertTrue((self.root / reset.PENDING).exists())
        self.old.unlink()  # Fixture, not automatic conflict removal.
        self.assertEqual(reset.recover_resource_upgrade(self.root)['status'], 'rolled-back')

    def test_commit_rejects_new_foreign_files_and_modified_quarantine(self):
        outcome = self.prepare()
        extra = self.root / '_internal/references/late-unknown.json'
        extra.write_bytes(b'late external file')
        with self.assertRaisesRegex(reset.ResourceResetError, 'active-plane-changed'):
            reset.finish_resource_upgrade(self.root, outcome['transactionId'], setup_succeeded=True)
        self.assertEqual(extra.read_bytes(), b'late external file')
        extra.unlink()
        retained = Path(outcome['retainedDirectory']) / 'old/0'
        retained.write_bytes(b'changed quarantine data')
        with self.assertRaisesRegex(reset.ResourceResetError, 'quarantine-changed'):
            reset.finish_resource_upgrade(self.root, outcome['transactionId'], setup_succeeded=True)
        self.assertTrue((self.root / reset.PENDING).exists())

    def test_uncertain_committed_journal_write_never_rolls_back_new_resources(self):
        outcome = self.prepare()
        original = reset._write_journal
        def uncertain(directory, journal):
            original(directory, journal)
            if journal['status'] == 'committed':
                raise OSError('sync failed after committed replacement')
        with patch.object(reset, '_write_journal', side_effect=uncertain), self.assertRaisesRegex(OSError, 'sync failed'):
            reset.finish_resource_upgrade(self.root, outcome['transactionId'], setup_succeeded=True)
        self.assertTrue((self.root / reset.PENDING).exists())
        self.assertEqual(reset.recover_resource_upgrade(self.root)['status'], 'committed')
        self.assertFalse(self.old.exists())
        self.assertEqual(base.repair_managed_base_resources(self.root)['status'], 'healthy')

    def test_other_pending_operations_block_without_moving_bytes(self):
        for name in ('pending-base-repair.json', 'pending-upgrade.json', 'pending-additions.json',
                     'pending-removal.json', 'qt-import-pending.json', 'qt-recovery-pending.json'):
            with self.subTest(name=name):
                pointer = self.root / 'work/map-packages' / name
                self.write(pointer, b'pending bytes retained')
                with self.assertRaises(ValueError):
                    self.plan()
                self.assertTrue(self.old.exists())
                self.assertEqual(pointer.read_bytes(), b'pending bytes retained')
                pointer.unlink()

    def test_different_installation_identity_rejects_same_preview(self):
        other = fixtures.ManagedBaseResourcesTests('runTest')
        other.setUp(); self.addCleanup(other.doCleanups); other.project()
        self.write(other.target / '_internal/references/old-orphan.json', self.old.read_bytes())
        with self.assertRaisesRegex(reset.ResourceResetError, 'plan-changed'):
            reset.prepare_resource_upgrade(other.target, expected_plan_sha256=self.plan()['planSha256'], confirmed=True)
        self.assertFalse((other.target / reset.PENDING).exists())
        self.assertTrue(self.old.exists())

    def test_repeated_terminal_events_never_replay_changes(self):
        result = self.prepare()
        reset.finish_resource_upgrade(self.root, result['transactionId'])
        self.old.write_bytes(b'later user edit after complete rollback')
        self.assertEqual(reset.finish_resource_upgrade(self.root, result['transactionId'])['status'], 'rolled-back')
        self.assertEqual(self.old.read_bytes(), b'later user edit after complete rollback')
        self.assertEqual(reset.recover_resource_upgrade(self.root)['status'], 'not-needed')
        result = self.prepare()
        reset.finish_resource_upgrade(self.root, result['transactionId'], setup_succeeded=True)
        self.assertEqual(reset.finish_resource_upgrade(self.root, result['transactionId'], setup_succeeded=True)['status'], 'committed')
        self.assertEqual(reset.recover_resource_upgrade(self.root)['status'], 'not-needed')

    def test_low_disk_space_and_corrupt_candidate_fail_before_old_file_movement(self):
        plan = self.plan()
        with patch.object(reset.shutil, 'disk_usage', return_value=SimpleNamespace(free=0)):
            with self.assertRaisesRegex(reset.ResourceResetError, 'insufficient-free-space'):
                self.prepare(plan)
        self.assertTrue(self.old.exists())
        self.assertFalse((self.root / reset.PENDING).exists())
        candidate = self.root / base.CANDIDATE / 'payload/_internal/difficult-native-manifest.json'
        candidate.write_bytes(b'corrupted target candidate')
        with self.assertRaisesRegex(reset.ResourceResetError, 'candidate-integrity-mismatch'):
            self.plan()
        self.assertTrue(self.old.exists())

    def test_updated_program_labels_do_not_relabel_captured_old_resource_provenance(self):
        path = self.root / '_internal/version.json'
        self.write(path, json.dumps(dict(schema='identityv-app-version-v1', version='1.1.1')).encode())
        result = self.prepare()
        path.write_text(json.dumps(dict(schema='identityv-app-version-v1', version='next-fixture')))
        reset.finish_resource_upgrade(self.root, result['transactionId'], setup_succeeded=True)
        journal = reset._read_json(Path(result['retainedDirectory']) / 'journal.json')
        observed = journal['plan']['observedIdentity'][0]
        self.assertEqual(observed['observed']['version'], '1.1.1')
        self.assertNotEqual(observed['sha256'], hashlib.sha256(path.read_bytes()).hexdigest())

    def test_old_healthy_base_upgrade_can_be_cancelled_without_losing_state(self):
        # The first completed generation is also protected during a later reset.
        first = self.prepare()
        reset.finish_resource_upgrade(self.root, first['transactionId'], setup_succeeded=True)
        self.case.upgrade_candidate()
        plan = self.plan(); snapshot = self.old_snapshot(plan)
        second = self.prepare(plan)
        reset.finish_resource_upgrade(self.root, second['transactionId'])
        self.assert_old_restored(snapshot)

    def test_case_variants_of_preserved_roots_are_never_selected(self):
        paths = ('_internal/assets/Custom-Maps/personal-route.json',
                 '_internal/references/Build-Fingerprint.txt', 'work/map-packages/Import.Lock')
        for path in paths:
            self.write(self.root / path, b'case-variant protected content')
        plan = self.plan()
        self.assertFalse({row['path'] for row in plan['oldFiles']} & set(paths))
        for path in paths:
            self.assertFalse(reset._selected(path))
            self.assertFalse(reset._selected(path.upper()))
        outcome = self.prepare(plan)
        reset.finish_resource_upgrade(self.root, outcome['transactionId'], setup_succeeded=True)
        for path in paths:
            self.assertEqual((self.root / path).read_bytes(), b'case-variant protected content')

    def test_differently_cased_old_new_paths_restore_exact_old_object(self):
        old = self.root / '_internal/references/Catalog.JSON'
        self.write(old, b'old mixed-case catalogue')
        plan = self.plan(); snapshot = self.old_snapshot(plan)
        result = self.prepare(plan)
        reset.finish_resource_upgrade(self.root, result['transactionId'])
        self.assert_old_restored(snapshot)

    def test_candidate_cannot_target_case_variants_or_exact_root_of_preserved_data(self):
        for path in ('_internal/assets/Custom-Maps/private.png', '_internal/assets/custom-maps',
                     '_internal/references/Build-Fingerprint.txt'):
            with self.subTest(path=path):
                directory = self.case.folder / ('external-' + str(len(path)))
                directory.mkdir(exist_ok=True)
                data = b'candidate must not overwrite personal data'
                manifest = base.manifest_for_rows([self.case.row(path, data)], 'fixture')
                self.write(directory / 'manifest.json', base._encoded(manifest))
                self.write(directory / 'payload' / path, data)
                with self.assertRaisesRegex(reset.ResourceResetError, 'outside-base-scope'):
                    reset.inspect_resource_upgrade(self.root, candidate_directory=directory)
                self.assertFalse((self.root / reset.PENDING).exists())

    def test_partial_private_pointer_write_does_not_publish_a_torn_gate(self):
        write = reset.transaction._write_new
        def interrupt(path, data):
            if Path(path).name == 'pending-pointer.json':
                Path(path).write_bytes(data[:8])
                raise KeyboardInterrupt('partial private pointer')
            return write(path, data)
        with patch.object(reset.transaction, '_write_new', side_effect=interrupt), self.assertRaises(KeyboardInterrupt):
            self.prepare()
        self.assertFalse((self.root / reset.PENDING).exists())
        self.assertTrue(self.old.exists())
        self.assertEqual(reset.recover_resource_upgrade(self.root)['status'], 'not-needed')
        outcome = self.prepare()
        reset.finish_resource_upgrade(self.root, outcome['transactionId'])

    def test_status_disambiguates_commit_without_mutation_or_program_rollback(self):
        outcome = self.prepare()
        before = reset.resource_upgrade_status(self.root, outcome['transactionId'])
        self.assertEqual(before['status'], 'prepared-resources')
        self.assertTrue((self.root / reset.PENDING).exists())
        reset.finish_resource_upgrade(self.root, outcome['transactionId'], setup_succeeded=True)
        journal = Path(outcome['retainedDirectory']) / 'journal.json'
        original = (journal.read_bytes(), journal.stat().st_ino, journal.stat().st_mtime_ns)
        result = reset.resource_upgrade_status(self.root, outcome['transactionId'])
        self.assertEqual(result['status'], 'committed')
        self.assertEqual(result['planSha256'], outcome['planSha256'])
        self.assertEqual((journal.read_bytes(), journal.stat().st_ino, journal.stat().st_mtime_ns), original)

    def test_target_identity_is_strictly_bound_to_preview_and_generation(self):
        identity = dict(sourceCommit='a' * 40, displayVersion='1.1.2', numericVersion='1.0.0.24',
                        executableSha256='b' * 64, resourceInventorySha256='c' * 64)
        plan = reset.inspect_resource_upgrade(self.root, target_identity=identity)
        for value in ({**identity, 'extra': 'no'}, {**identity, 'sourceCommit': 'x'},
                      {**identity, 'numericVersion': '1.0.0.999999'}):
            with self.assertRaisesRegex(reset.ResourceResetError, 'invalid-target-identity'):
                reset.inspect_resource_upgrade(self.root, target_identity=value)
        with self.assertRaisesRegex(reset.ResourceResetError, 'plan-changed'):
            self.prepare(plan)
        result = reset.prepare_resource_upgrade(self.root, expected_plan_sha256=plan['planSha256'],
                                                confirmed=True, target_identity=identity)
        generation = json.loads((self.root / reset.GENERATION).read_text())
        self.assertEqual(generation['targetIdentity'], identity)
        reset.finish_resource_upgrade(self.root, result['transactionId'])

    def test_replaced_pending_marker_is_never_adopted_or_deleted(self):
        outcome = self.prepare()
        pending = self.root / reset.PENDING
        original = reset._stamp
        foreign = reset._encoded(dict(schema=reset.SCHEMA, transactionId=str(uuid.uuid4())))
        def replace_pending(path):
            # The complete foreign object has a different identity even if it
            # replaces the marker between a read and a later unlink attempt.
            replacement = pending.with_suffix('.foreign')
            replacement.write_bytes(foreign)
            os.replace(replacement, pending)
        def swap_before_stamp(path):
            if Path(path) == pending:
                replace_pending(path)
            return original(path)
        with patch.object(reset, '_stamp', side_effect=swap_before_stamp):
            with self.assertRaisesRegex(reset.ResourceResetError, 'pending-changed'):
                reset._clear_pending(self.root, outcome['transactionId'])
        self.assertEqual(pending.read_bytes(), foreign)
        retained = Path(outcome['retainedDirectory']) / 'pending-pointer.json'
        pending.unlink(); os.link(retained, pending)  # Restore only test fixture.
        def swap_after_stamp(path):
            result = original(path)
            if Path(path) == pending:
                replace_pending(path)
            return result
        with patch.object(reset, '_stamp', side_effect=swap_after_stamp):
            with self.assertRaisesRegex(reset.ResourceResetError, 'pending-changed'):
                reset._clear_pending(self.root, outcome['transactionId'])
        self.assertEqual(pending.read_bytes(), foreign)

    def test_existing_windows_parent_spelling_does_not_block_resource_commit(self):
        # On Windows this is the actual same parent used by the lowercase
        # candidate. Elsewhere model only the enumerator's spelling difference.
        if os.name == 'nt':
            (self.root / '_internal/references/Difficult-Native').mkdir()
        outcome = self.prepare()
        scan = reset._scan
        def retained_parent_spelling(root):
            return [dict(row, path=row['path'].replace('/difficult-native/', '/Difficult-Native/'))
                    for row in scan(root)]
        with patch.object(reset, '_scan', side_effect=retained_parent_spelling):
            self.assertEqual(reset.finish_resource_upgrade(
                self.root, outcome['transactionId'], setup_succeeded=True)['status'], 'committed')
        self.assertEqual(base.repair_managed_base_resources(self.root)['status'], 'healthy')

    def test_case_normalization_does_not_ignore_changed_file_identity(self):
        outcome = self.prepare()
        scan = reset._scan
        def changed_identity(root):
            rows = scan(root)
            rows[0] = dict(rows[0], path=rows[0]['path'].upper(), identity=[0, 0])
            return rows
        with patch.object(reset, '_scan', side_effect=changed_identity):
            with self.assertRaisesRegex(reset.ResourceResetError, 'active-plane-changed'):
                reset.finish_resource_upgrade(self.root, outcome['transactionId'], setup_succeeded=True)

    def test_journal_cannot_expand_scope_to_user_route(self):
        outcome = self.prepare()
        directory, journal = reset._load(self.root)
        original = json.loads(json.dumps(journal))
        item = journal['plan']['oldFiles'][0]
        item['path'] = 'work/user-routes/route.json'
        content = {k: v for k, v in journal['plan'].items() if k != 'planSha256'}
        journal['plan']['planSha256'] = reset._digest(content)
        reset._write_journal(directory, journal)
        with self.assertRaisesRegex(reset.ResourceResetError, 'invalid-old-inventory'):
            reset.recover_resource_upgrade(self.root)
        self.unchanged_user_data()
        self.assertTrue((self.root / reset.PENDING).exists())
        reset._write_journal(directory, original)
        reset.finish_resource_upgrade(self.root, outcome['transactionId'])

    def test_linked_resource_and_structural_collisions_block_before_mutation(self):
        outside = self.case.folder / 'outside'; outside.mkdir()
        link = self.root / '_internal/references/link'
        try:
            link.symlink_to(outside, target_is_directory=True)
        except (OSError, NotImplementedError):
            self.skipTest('symlink unavailable')
        with self.assertRaises(ValueError):
            self.plan()
        self.assertTrue(link.is_symlink())
        self.assertFalse((self.root / reset.PENDING).exists())
        link.unlink()
        target = self.root / '_internal/difficult-native-manifest.json'
        target.mkdir()
        with self.assertRaisesRegex(reset.ResourceResetError, 'target-not-file'):
            self.plan()
        self.assertFalse((self.root / reset.PENDING).exists())


if __name__ == '__main__':
    unittest.main()
