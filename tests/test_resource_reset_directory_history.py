"""Independent reset seam regressions; all data is owned TemporaryDirectory fixture.
Run with app, tests and experiments/qt_quick on PYTHONPATH.
"""
from pathlib import Path
import sys
import unittest
REPO = Path(__file__).resolve().parents[1]
for relative in ('app', 'tests', 'experiments/qt_quick'):
    sys.path.insert(0, str(REPO / relative))
import managed_base_resources as base
import resource_upgrade_reset as reset
import author_resource_snapshots as snapshots
import test_managed_base_resources as base_fixtures
import test_author_resource_snapshots as author_fixtures
import test_upgrade_retirement as upgrade_fixtures
import map_package_upgrade as upgrade
from client_resource_startup import production_resources
from unittest.mock import patch
from resource_transaction import assert_resources_ready

class ResetDirectorySeams(unittest.TestCase):
    def fixture(self, kind):
        case = kind('runTest'); case.setUp(); self.addCleanup(case.doCleanups)
        return case

    def reset(self, root, candidate=None):
        plan = reset.inspect_resource_upgrade(root, candidate_directory=candidate)
        result = reset.prepare_resource_upgrade(root, candidate_directory=candidate,
            expected_plan_sha256=plan['planSha256'], confirmed=True)
        self.assertEqual(reset.finish_resource_upgrade(root, result['transactionId'],
            setup_succeeded=True)['status'], 'committed')
        self.assertFalse((root / reset.PENDING).exists())

    def startup(self, root):
        with production_resources(root, production=True):
            pass

    def test_normal_reset_boots(self):
        case = self.fixture(base_fixtures.ManagedBaseResourcesTests)
        case.project()
        self.reset(case.target)
        self.startup(case.target)

    def test_legacy_base_history_boots_after_reset(self):
        case = self.fixture(base_fixtures.ManagedBaseResourcesTests)
        old = case.legacy_completed_repair()
        self.assertEqual(base._load_history(case.target, old.name)[1]['status'], 'committed')
        self.assertFalse((case.target / base.PENDING).exists())
        self.reset(case.target)
        self.startup(case.target)

    def test_legacy_upgrade_history_boots_after_reset(self):
        case = self.fixture(upgrade_fixtures.UpgradeRetirementTests)
        _, old = case.legacy_install()
        self.assertEqual(upgrade._load_journal(case.root, old.name)[1]['status'], 'committed')
        self.assertFalse((case.root / upgrade.PENDING).exists())
        candidate_case = self.fixture(base_fixtures.ManagedBaseResourcesTests)
        candidate_case.project()
        self.reset(case.root, candidate_case.target / base.CANDIDATE)
        self.startup(case.root)

    def test_legacy_upgrade_history_rollback_preserves_objects_and_boots(self):
        case = self.fixture(upgrade_fixtures.UpgradeRetirementTests)
        _, old = case.legacy_install()
        paths = [old, *old.rglob('*')]
        before = {p: (reset.transaction._identity(p.stat()), p.read_bytes() if p.is_file() else None)
                  for p in paths}
        candidate_case = self.fixture(base_fixtures.ManagedBaseResourcesTests)
        candidate_case.project()
        candidate = candidate_case.target / base.CANDIDATE
        plan = reset.inspect_resource_upgrade(case.root, candidate_directory=candidate)
        result = reset.prepare_resource_upgrade(case.root, candidate_directory=candidate,
            expected_plan_sha256=plan['planSha256'], confirmed=True)
        reset.finish_resource_upgrade(case.root, result['transactionId'])
        self.assertEqual({p: (reset.transaction._identity(p.stat()), p.read_bytes() if p.is_file() else None)
                          for p in paths}, before)
        self.assertEqual(upgrade._load_journal(case.root, old.name)[1]['status'], 'committed')
        self.startup(case.root)

    def author_case(self, shape):
        case = self.fixture(author_fixtures.AuthorSnapshotTests)
        archive = case.package(); case.install(archive)
        candidate = case.root.parent / 'same-base-candidate'; rows = []
        for source in (case.root / '_internal/references').rglob('*'):
            if source.is_file():
                relative = source.relative_to(case.root).as_posix(); data = source.read_bytes()
                target = candidate / 'payload' / relative
                target.parent.mkdir(parents=True, exist_ok=True); target.write_bytes(data)
                rows.append(dict(path=relative, bytes=len(data), sha256=reset.files._sha(source)))
        (candidate / 'manifest.json').write_bytes(base._encoded(base.manifest_for_rows(rows, 'fixture-same-base')))
        activation = case.root / snapshots.activation_path(case.profile)
        if shape == 'file': activation.write_bytes(b'broken activation metadata')
        else: activation.unlink(); activation.mkdir()
        self.reset(case.root, candidate)
        return case.install(archive)

    def test_damaged_author_file_can_reimport_after_reset(self):
        self.assertEqual(self.author_case('file')['status'], 'author-installed-live')

    def test_damaged_author_directory_can_reimport_after_reset(self):
        self.assertEqual(self.author_case('directory')['status'], 'author-installed-live')

class ResetDirectoryRecoveryTests(unittest.TestCase):
    def setUp(self):
        self.case = base_fixtures.ManagedBaseResourcesTests('runTest')
        self.case.setUp(); self.addCleanup(self.case.doCleanups)
        self.history = self.case.legacy_completed_repair()
        self.root = self.case.target

    def prepare(self):
        plan = reset.inspect_resource_upgrade(self.root)
        return reset.prepare_resource_upgrade(self.root,
            expected_plan_sha256=plan['planSha256'], confirmed=True)

    def snapshot(self):
        return {p.relative_to(self.root).as_posix():
                (reset.transaction._identity(p.stat()), p.read_bytes() if p.is_file() else None)
                for p in [self.history, *self.history.rglob('*')]}

    def test_cancel_and_recovery_restore_original_history_objects(self):
        for recover in (False, True):
            with self.subTest(recover=recover):
                before = self.snapshot()
                result = self.prepare()
                if recover:
                    reset.recover_resource_upgrade(self.root)
                else:
                    reset.finish_resource_upgrade(self.root, result['transactionId'])
                self.assertEqual(self.snapshot(), before)
                self.assertEqual(base._load_history(self.root, self.history.name)[1]['status'], 'committed')
                assert_resources_ready(self.root)
        # Real production retirement still recognizes restored legacy history.
        with production_resources(self.root, production=True):
            pass
        self.assertFalse((self.root / base.PENDING).exists())

    def test_no_replace_rename_preserves_racing_empty_destination(self):
        source = self.root / 'source-empty'; source.mkdir()
        target = self.root / 'target-empty'; target.mkdir()
        identities = [reset.transaction._identity(p.stat()) for p in (source, target)]
        with self.assertRaises(OSError):
            reset._rename_directory_no_replace(source, target)
        self.assertEqual([reset.transaction._identity(p.stat()) for p in (source, target)], identities)

    def test_crash_after_directory_rename_recovers_identity(self):
        before = self.snapshot()
        rename = reset._rename_directory_no_replace
        def interrupted(source, target):
            rename(source, target)
            raise RuntimeError('fixture crash after rename')
        with patch.object(reset, '_rename_directory_no_replace', side_effect=interrupted):
            with self.assertRaises(ValueError):
                self.prepare()
        reset.recover_resource_upgrade(self.root)
        self.assertEqual(self.snapshot(), before)
        assert_resources_ready(self.root)

    def test_crash_before_directory_intent_write_does_not_publish_untracked_parent(self):
        fresh = base_fixtures.ManagedBaseResourcesTests('runTest')
        fresh.setUp(); self.addCleanup(fresh.doCleanups); fresh.project()
        self.root = fresh.target
        plan = reset.inspect_resource_upgrade(self.root)
        write = reset._write_journal
        def crash(directory, journal):
            if journal.get('createdDirectories'):
                raise KeyboardInterrupt('fixture interruption before directory intent')
            return write(directory, journal)
        with patch.object(reset, '_write_journal', side_effect=crash):
            with self.assertRaises(KeyboardInterrupt):
                self.prepare()
        reset.recover_resource_upgrade(self.root)
        self.assertEqual(reset.inspect_resource_upgrade(self.root)['planSha256'], plan['planSha256'])

    def test_directory_replacement_and_new_empty_directory_invalidate_preview(self):
        plan = reset.inspect_resource_upgrade(self.root)
        extra = self.history / 'new-empty-directory'
        extra.mkdir()
        with self.assertRaisesRegex(ValueError, 'plan-changed'):
            reset.prepare_resource_upgrade(self.root,
                expected_plan_sha256=plan['planSha256'], confirmed=True)
        extra.rmdir()
        original = self.history.with_name('retained-original')
        self.history.rename(original)
        self.history.mkdir()
        with self.assertRaisesRegex(ValueError, 'plan-changed'):
            reset.prepare_resource_upgrade(self.root,
                expected_plan_sha256=plan['planSha256'], confirmed=True)
        self.assertTrue((original / 'journal.json').is_file())
        self.assertFalse((self.root / reset.PENDING).exists())

    def test_late_unknown_content_is_preserved_and_blocks_directory_migration(self):
        real_move = reset._move_directory_skeletons
        foreign = self.history / 'private-note.txt'
        def inject(root, directory, plan, *, restore=False):
            if not restore:
                foreign.write_bytes(b'late unknown user bytes')
            return real_move(root, directory, plan, restore=restore)
        with patch.object(reset, '_move_directory_skeletons', side_effect=inject):
            with self.assertRaisesRegex(ValueError, 'directory-not-empty'):
                self.prepare()
        self.assertEqual(foreign.read_bytes(), b'late unknown user bytes')
        self.assertEqual(base._load_history(self.root, self.history.name)[1]['status'], 'committed')
        self.assertFalse((self.root / reset.PENDING).exists())

    def test_rollback_conflicting_directory_or_link_never_overwrites(self):
        result = self.prepare()
        parent = self.history.parent
        parent.mkdir(parents=True)
        unknown = parent / 'private.txt'; unknown.write_bytes(b'keep')
        with self.assertRaisesRegex(ValueError, 'directory-target-appeared'):
            reset.finish_resource_upgrade(self.root, result['transactionId'])
        self.assertEqual(unknown.read_bytes(), b'keep')
        unknown.unlink(); parent.rmdir()
        try:
            parent.symlink_to(self.root / '_internal/references', target_is_directory=True)
        except OSError:
            reset.recover_resource_upgrade(self.root)
            self.skipTest('directory symlink creation is unavailable on this platform')
        with self.assertRaises(ValueError):
            reset.recover_resource_upgrade(self.root)
        self.assertTrue(parent.is_symlink())
        parent.unlink()
        reset.recover_resource_upgrade(self.root)
        self.assertEqual(base._load_history(self.root, self.history.name)[1]['status'], 'committed')

    def test_commit_rejects_late_empty_pending_marker_and_quarantine_contents(self):
        result = self.prepare()
        marker = self.root / 'work/map-packages/pending-author-snapshot.json'
        marker.mkdir()
        with self.assertRaisesRegex(ValueError, 'active-directories-changed'):
            reset.finish_resource_upgrade(self.root, result['transactionId'], setup_succeeded=True)
        self.assertTrue(marker.is_dir()); marker.rmdir()
        quarantine = Path(result['retainedDirectory']) / 'old-directories'
        foreign = next(quarantine.iterdir()) / 'private-note.txt'
        foreign.write_bytes(b'keep')
        with self.assertRaisesRegex(ValueError, 'directory-not-empty'):
            reset.finish_resource_upgrade(self.root, result['transactionId'], setup_succeeded=True)
        self.assertEqual(foreign.read_bytes(), b'keep')


if __name__ == '__main__':
    unittest.main()
