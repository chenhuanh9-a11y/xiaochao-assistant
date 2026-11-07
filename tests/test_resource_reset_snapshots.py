"""Reset/snapshot seams on real synthetic transactions, not native Setup QA."""
import hashlib
import json
from pathlib import Path
import unittest
from unittest.mock import patch

import author_resource_snapshots as snapshots
import resource_upgrade_reset as reset
import star_map_reset_helper as helper
from resource_transaction import assert_resources_ready, resource_write_lock
import test_author_resource_snapshots as author_fixtures
import test_managed_base_resources as base_fixtures


class ResourceResetSnapshotTests(unittest.TestCase):
    def setUp(self):
        self.author = author_fixtures.AuthorSnapshotTests('runTest')
        self.author.setUp()
        self.addCleanup(self.author.doCleanups)
        self.base = base_fixtures.ManagedBaseResourcesTests('runTest')
        self.base.setUp()
        self.addCleanup(self.base.doCleanups)
        self.base.project()
        self.root = self.author.root
        self.candidate = self.base.target / reset.base.CANDIDATE
        self.author.install(self.author.package())
        self.profile = self.author.profile
        self.activation = self.root / snapshots.activation_path(self.profile)
        self.user = self.root / 'work/user-routes/personal-route.json'
        self.user.parent.mkdir(parents=True)
        self.user.write_bytes(b'personal route')

    def plan(self):
        return reset.inspect_resource_upgrade(self.root, candidate_directory=self.candidate)

    def prepare(self, plan):
        return reset.prepare_resource_upgrade(self.root, candidate_directory=self.candidate,
            expected_plan_sha256=plan['planSha256'], confirmed=True)

    def deactivate(self):
        with resource_write_lock(self.root):
            plan = snapshots.inspect_deactivation(self.root, self.profile)
            snapshots.deactivate_locked(self.root, plan)

    def observed(self):
        return {p.relative_to(self.root).as_posix():
                (hashlib.sha256(p.read_bytes()).hexdigest(), reset.transaction._identity(p.stat()))
                for p in self.root.rglob('*') if p.is_file() and p.name != 'import.lock'}

    def test_active_snapshot_is_a_readonly_reinstall_hint_not_ownership(self):
        before = self.observed()
        with patch.object(snapshots.SnapshotPackage, 'document',
                          side_effect=AssertionError('notice must not decode author displays')):
            plan = self.plan()
        self.assertEqual(plan['packagesToDownload'], [self.profile])
        self.assertEqual(before, self.observed())
        rows = [row for row in plan['oldFiles'] if row['path'].startswith(snapshots.STORE + '/')]
        self.assertGreater(len(rows), 2)
        self.assertTrue(all(row['classification'] == 'package-state-or-history' for row in rows))
        self.assertTrue(all(row['sources'] == [] for row in rows))
        text = helper._summary(plan, dict(displayVersion='1.1.2', sourceCommit='a' * 40))
        self.assertIn(self.profile, text)
        self.assertIn('不永久删除', text)

    def test_retained_old_versions_do_not_duplicate_the_active_package(self):
        self.author.install(self.author.package(color=(3, 4, 5, 120)))
        self.assertEqual(len(list((self.root / snapshots.STORE / 'versions' / self.profile).iterdir())), 2)
        self.assertEqual(self.plan()['packagesToDownload'], [self.profile])

    def test_tombstone_suppresses_old_history_and_stale_flat_receipt(self):
        receipt = self.author.view().manifest['receipt']
        self.deactivate()
        # A stale flat record cannot revive an explicitly deactivated author.
        legacy = self.root / ('work/map-packages/' + self.profile + '.json')
        legacy.write_text(json.dumps(receipt), encoding='utf-8')
        before = self.observed()
        plan = self.plan()
        self.assertEqual(plan['packagesToDownload'], [])
        self.assertTrue(any(row['path'].startswith(snapshots.STORE + '/versions/') for row in plan['oldFiles']))
        self.assertEqual(before, self.observed())

    def test_malformed_activation_and_manifest_never_become_package_identity(self):
        original = self.activation.read_bytes()
        value = json.loads(original)
        receipt = self.author.view().manifest['receipt']
        legacy = self.root / ('work/map-packages/' + self.profile + '.json')
        legacy.write_text(json.dumps(receipt), encoding='utf-8')
        for damaged in (b'not json', json.dumps(dict(value, packageId='author-other')).encode(),
                        json.dumps(dict(value, manifestSha256='0' * 64)).encode()):
            with self.subTest(damaged=damaged[:40]):
                self.activation.write_bytes(damaged)
                before = self.observed()
                plan = self.plan()
                self.assertEqual(plan['packagesToDownload'], [])
                self.assertEqual(before, self.observed())
                self.assertIn(snapshots.activation_path(self.profile), [r['path'] for r in plan['oldFiles']])
        self.activation.write_bytes(original)

    def test_missing_manifest_is_unknown_without_reviving_flat_receipt(self):
        view = self.author.view()
        (self.root / ('work/map-packages/' + self.profile + '.json')).write_text(
            json.dumps(view.manifest['receipt']), encoding='utf-8')
        (view.directory / 'manifest.json').unlink()
        self.assertEqual(self.plan()['packagesToDownload'], [])

    def test_directory_activation_never_revives_stale_flat_receipt(self):
        receipt = self.author.view().manifest['receipt']
        (self.root / ('work/map-packages/' + self.profile + '.json')).write_text(
            json.dumps(receipt), encoding='utf-8')
        self.activation.unlink()
        self.activation.mkdir()
        for nested in (False, True):
            with self.subTest(nested=nested):
                if nested:
                    (self.activation / 'unknown.txt').write_bytes(b'unknown user bytes')
                self.assertTrue(snapshots.managed(self.root, self.profile))
                before = self.observed()
                self.assertEqual(self.plan()['packagesToDownload'], [])
                self.assertEqual(before, self.observed())

    def test_damaged_payload_still_gets_reinstall_hint_but_remains_untrusted(self):
        view = self.author.view()
        payload = view.directory / next(iter(view.paths.values()))
        payload.write_bytes(b'user modified image or display metadata')
        before = self.observed()
        self.assertEqual(self.plan()['packagesToDownload'], [self.profile])
        self.assertEqual(before, self.observed())

    def test_author_pending_blocks_inspect_and_prepare_before_candidate_reads(self):
        plan = self.plan()
        (self.root / snapshots.PENDING).write_bytes(b'pending author operation')
        before = self.observed()
        with patch.object(reset, '_candidate', side_effect=AssertionError('must gate first')):
            for operation in (self.plan, lambda: self.prepare(plan)):
                with self.assertRaisesRegex(ValueError, 'author-snapshot-recovery-required'):
                    operation()
        self.assertEqual(before, self.observed())
        self.assertFalse((self.root / reset.PENDING).exists())

    def test_reset_pending_blocks_startup_and_author_writer(self):
        archive = self.author.package(color=(3, 4, 5, 120))
        (self.root / reset.PENDING).write_bytes(b'pending reset operation')
        before = self.observed()
        with self.assertRaisesRegex(ValueError, 'resource-reset-recovery-required'):
            assert_resources_ready(self.root)
        with self.assertRaisesRegex(ValueError, 'resource-reset-recovery-required'):
            self.author.install(archive)
        self.assertEqual(before, self.observed())

    def test_changed_activation_requires_review_again_before_prepare(self):
        plan = self.plan()
        self.deactivate()
        before = self.observed()
        with self.assertRaisesRegex(ValueError, 'plan-changed'):
            self.prepare(plan)
        self.assertEqual(before, self.observed())
        self.assertFalse((self.root / reset.PENDING).exists())

    def test_unexpected_author_pending_blocks_commit_and_retains_both_records(self):
        prepared = self.prepare(self.plan())
        marker = self.root / snapshots.PENDING
        marker.write_bytes(b'unexpected concurrent author record')
        before = self.observed()
        with self.assertRaisesRegex(ValueError, 'active-plane-changed'):
            reset.finish_resource_upgrade(self.root, prepared['transactionId'], setup_succeeded=True)
        self.assertEqual(before, self.observed())
        self.assertEqual(reset.resource_upgrade_status(self.root)['status'], 'prepared-resources')
        self.assertTrue(marker.exists())
        self.assertTrue((self.root / reset.PENDING).exists())

    def test_prepare_then_rollback_restores_snapshot_bytes_and_identity(self):
        before = self.observed()
        prepared = self.prepare(self.plan())
        self.assertFalse(self.activation.exists())
        self.assertEqual(prepared['packagesToDownload'], [self.profile])
        self.assertEqual(self.user.read_bytes(), b'personal route')
        result = reset.finish_resource_upgrade(self.root, prepared['transactionId'], setup_succeeded=False)
        self.assertEqual(result['status'], 'rolled-back')
        # Transaction journals remain by design, outside the old resource plane.
        current = self.observed()
        for name, value in before.items():
            self.assertEqual(current[name], value, name)
        self.assertEqual(self.plan()['packagesToDownload'], [self.profile])

    def test_prepare_commit_retains_snapshot_history_and_notice(self):
        prepared = self.prepare(self.plan())
        result = reset.finish_resource_upgrade(self.root, prepared['transactionId'], setup_succeeded=True)
        self.assertEqual(result['status'], 'committed')
        self.assertEqual(result['packagesToDownload'], [self.profile])
        self.assertFalse(self.activation.exists())
        self.assertTrue(Path(result['retainedDirectory']).is_dir())
        self.assertEqual(self.user.read_bytes(), b'personal route')

    def test_confirmation_bounds_visible_list_but_preserves_total(self):
        plan = self.plan()
        plan['packagesToDownload'] = [f'author-fixture-{n}' for n in range(20)]
        text = helper._summary(plan, dict(displayVersion='1.1.2', sourceCommit='a' * 40))
        self.assertIn('20 项', text)
        self.assertIn('author-fixture-11', text)
        self.assertNotIn('author-fixture-12', text)
        self.assertIn('另 8 项', text)
        self.assertEqual(len(plan['packagesToDownload']), 20)


if __name__ == '__main__':
    unittest.main()
