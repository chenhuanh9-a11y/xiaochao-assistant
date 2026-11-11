"""Synthetic old installations only; no registry or real user directories."""
import json
import os
from pathlib import Path
import stat
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'app'))
import legacy_migration as migration
from packaged_product_identity import PRODUCTION_APP_ID


class MigrationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.old = self.root / 'LocalAppData/Programs/XiaochaoAssistant'
        self.new = self.root / 'new/program'
        self.identity = dict(sourceKind='qtifw', product='星图', appId=PRODUCTION_APP_ID,
            installRoot=str(self.old), dataRoot=str(self.old / 'work'),
            uninstallRecordSha256='a'*64, maintenanceToolSha256='b'*64)
        self.write('_internal/installation-profile.json', json.dumps(dict(product='星图',
            installerInstance=dict(product='星图', id='production', appId=PRODUCTION_APP_ID))))
        self.write('work/nightmare-memory/ui-settings.json', json.dumps(dict(
            automaticFailureEvidence=False, actionHotkeys=[''] * 9 + ['Alt+F10'])))
        for name in ('custom-galleries.json', 'custom-routes.json', 'update-state.json', 'tutorial-state.json'):
            self.write('work/nightmare-memory/' + name, '{}')
        for name in ('nightmare-memory/samples/a.png', 'difficult-memory/confirmed.jsonl',
                     'seasons/s1/nightmare-memory/accepted/a.png', 'user-routes/a.json'):
            self.write('work/' + name, 'synthetic')
        self.write('work/nightmare-memory/failures/private.png', 'excluded')
        self.write('outputs/诊断日志/private.zip', 'excluded')

    def migrate(self):
        return migration.migrate(self.old, self.new, identity=self.identity)

    def write(self, name, data):
        p = self.old / name
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(data, encoding='utf-8')

    def snapshot(self):
        return {p.relative_to(self.old).as_posix(): p.read_bytes() for p in self.old.rglob('*') if p.is_file()}

    def other_process(self, script, *args):
        return subprocess.run([sys.executable, '-B', '-c',
            "import sys; from pathlib import Path; sys.path.insert(0, sys.argv[1]); " + script,
            str(Path(migration.__file__).parent), str(self.old), *map(str, args)],
            capture_output=True, text=True, timeout=20)

    def assert_other_resource_process_busy(self):
        other = self.other_process(
            "from resource_transaction import resource_runtime_lease, ResourceTransactionError\n"
            "try:\n lease = resource_runtime_lease(sys.argv[2])\n"
            "except ResourceTransactionError as error:\n print(error.code)\n"
            "else:\n lease.close(); print('unexpected-lock-acquired')\n")
        self.assertEqual(other.returncode, 0, other.stderr)
        self.assertEqual(other.stdout.strip(), 'resource-busy')

    def test_complete_unchanged_idempotent_preferences(self):
        before = self.snapshot()
        result = self.migrate()
        self.assertEqual(result, self.migrate())
        self.assertEqual(before, self.snapshot())
        settings = json.loads((self.new / 'work/nightmare-memory/ui-settings.json').read_text())
        self.assertIs(settings['automaticFailureEvidence'], False)
        try:
            from client_settings import SettingsService
            from client_paths import RuntimePaths
            from PySide6.QtGui import QGuiApplication
        except ImportError:
            self.skipTest('PySide6 is not installed in this environment')
        app = QGuiApplication.instance() or QGuiApplication([])
        with patch('client_settings.runtime_paths', return_value=RuntimePaths(self.new, True)):
            service = SettingsService(self.new)
            try:
                self.assertFalse(service.loadFailed)
                self.assertEqual(service.path, self.new / 'work/nightmare-memory/ui-settings.json')
                self.assertEqual(service.persistedValues['actionHotkeys'], settings['actionHotkeys'])
                self.assertIs(service.persistedValues['automaticFailureEvidence'], False)
            finally:
                service.shutdown()
        self.assertEqual(settings['actionHotkeys'][9], 'Alt+F10')
        self.assertFalse((self.new / 'work/nightmare-memory/failures').exists())
        journal = (self.new / 'legacy-migration.json').read_text()
        self.assertNotIn(str(self.old), journal)
        self.assertNotIn('ui-settings.json', journal)
        self.assertNotIn('a.png', journal)

    def test_program_files_layout(self):
        moved = self.root / 'Program Files/XiaochaoAssistant'
        moved.parent.mkdir()
        self.old.rename(moved)
        migration.migrate(moved, self.new, identity=dict(self.identity, installRoot=str(moved), dataRoot=str(moved / 'work')))

    def test_conflict_no_overwrite(self):
        target = self.new / 'work/nightmare-memory/ui-settings.json'
        target.parent.mkdir(parents=True)
        target.write_text('existing')
        before = self.snapshot()
        with self.assertRaisesRegex(migration.MigrationError, 'destination-conflict'):
            self.migrate()
        self.assertEqual(target.read_text(), 'existing')
        self.assertEqual(before, self.snapshot())

    def test_receipts_and_pending_fail_closed(self):
        for name in ('work/map-packages/pending-upgrade.json', 'work/qt-recovery-pending.json'):
            with self.subTest(name=name):
                self.write(name, '{broken')
                with self.assertRaises(migration.MigrationError):
                    self.migrate()
                self.assertFalse((self.new / 'work').exists())
                (self.old / name).unlink()
                if 'map-packages' in name:
                    (self.old / 'work/map-packages').rmdir()

    def test_idle_persistent_import_lock_migrates_without_source_writes(self):
        # The production resource lease intentionally leaves this file behind.
        from resource_transaction import resource_runtime_lease
        with resource_runtime_lease(self.old):
            pass
        lock = self.old / 'work/map-packages/import.lock'
        self.assertTrue(lock.exists())
        before = self.snapshot()
        result = self.migrate()
        self.assertTrue(result['migrated'])
        self.assertEqual(result, self.migrate())
        self.assertEqual(before, self.snapshot())
        self.assertFalse((self.new / 'work/map-packages/import.lock').exists())

    def test_read_only_nonempty_import_lock_bytes_are_preserved(self):
        self.write('work/map-packages/import.lock', 'old-lock-bytes')
        lock = self.old / 'work/map-packages/import.lock'
        before = self.snapshot()
        lock.chmod(stat.S_IREAD)
        try:
            self.assertTrue(self.migrate()['migrated'])
            self.assertEqual(before, self.snapshot())
        finally:
            lock.chmod(stat.S_IREAD | stat.S_IWRITE)

    def test_busy_source_os_lock_in_another_process_blocks_migration(self):
        from resource_transaction import resource_runtime_lease
        self.write('work/map-packages/import.lock', 'preserve-old-lock')
        before = self.snapshot()
        with resource_runtime_lease(self.old):
            other = self.other_process(
                "import json; import legacy_migration as migration\n"
                "try:\n migration.migrate(sys.argv[2], sys.argv[3], identity=json.loads(sys.argv[4]))\n"
                "except migration.MigrationError as error:\n print(str(error))\n"
                "else:\n print('unexpected-migration-success')\n",
                self.new, json.dumps(self.identity))
        self.assertEqual(other.returncode, 0, other.stderr)
        self.assertEqual(other.stdout.strip(), 'resource-import-busy-or-recovery-required')
        self.assertFalse((self.new / 'work').exists())
        self.assertEqual(before, self.snapshot())

    def test_source_os_lock_remains_held_through_copy_recheck_publish_and_complete(self):
        self.write('work/map-packages/import.lock', '')
        before = self.snapshot()
        seen = set()
        real_source_file, real_inventory = migration._source_file, migration._inventory
        real_rename, real_save = migration.rename_no_replace, migration._save
        source_inventories = 0
        def check(phase):
            if phase not in seen:
                self.assert_other_resource_process_busy()
                seen.add(phase)
        def source_file(source, relative):
            check('copy')
            return real_source_file(source, relative)
        def inventory(source, **kwargs):
            nonlocal source_inventories
            if source == self.old:
                source_inventories += 1
                if source_inventories == 2:
                    check('source-recheck')
            return real_inventory(source, **kwargs)
        def rename(source, target):
            if target == self.new / 'work':
                check('publish')
            return real_rename(source, target)
        def save(path, state):
            if state['status'] == 'complete':
                check('complete')
            return real_save(path, state)
        with patch.object(migration, '_source_file', side_effect=source_file), \
             patch.object(migration, '_inventory', side_effect=inventory), \
             patch.object(migration, 'rename_no_replace', side_effect=rename), \
             patch.object(migration, '_save', side_effect=save):
            self.migrate()
        self.assertEqual(seen, {'copy', 'source-recheck', 'publish', 'complete'})
        self.assertEqual(before, self.snapshot())
        # The read-only lease is released after the entire migration returns.
        from resource_transaction import resource_runtime_lease
        with resource_runtime_lease(self.old):
            pass

    def test_no_source_import_lock_or_directory_is_created(self):
        package_root = self.old / 'work/map-packages'
        self.assertFalse(package_root.exists())
        before = self.snapshot()
        self.migrate()
        self.assertFalse(package_root.exists())
        self.assertEqual(before, self.snapshot())

    def test_import_lock_appearing_during_copy_is_not_ignored(self):
        real_source_file = migration._source_file
        def source_file(source, relative):
            self.write('work/map-packages/import.lock', 'new-lock')
            return real_source_file(source, relative)
        with patch.object(migration, '_source_file', side_effect=source_file):
            with self.assertRaisesRegex(migration.MigrationError, 'resource-import-busy-or-recovery-required'):
                self.migrate()
        self.assertFalse((self.new / 'work').exists())
        self.assertEqual((self.old / 'work/map-packages/import.lock').read_text(), 'new-lock')

    def test_only_live_exact_root_lock_can_be_ignored(self):
        self.write('work/map-packages/import.lock', '')
        for value in (None, False, True, object()):
            with self.subTest(value=value):
                with self.assertRaisesRegex(migration.MigrationError, 'resource-import-busy-or-recovery-required'):
                    migration._inventory(self.old, import_lock=value)
                with self.assertRaises(migration.MigrationError):
                    migration._check_source_transactions(self.old, value)
        with migration._SourceImportLock(self.old) as lease:
            migration._inventory(self.old, import_lock=lease)
        with self.assertRaisesRegex(migration.MigrationError, 'resource-import-busy-or-recovery-required'):
            migration._inventory(self.old, import_lock=lease)

    def test_nested_import_lock_and_pending_marker_remain_rejected_with_root_lease(self):
        self.write('work/map-packages/import.lock', '')
        for name in ('work/map-packages/authors/import.lock', 'work/map-packages/pending-upgrade.json',
                     'work/qt-import-pending.json'):
            with self.subTest(name=name):
                self.write(name, '{}')
                before = self.snapshot()
                with self.assertRaises(migration.MigrationError):
                    self.migrate()
                self.assertFalse((self.new / 'work').exists())
                self.assertEqual(before, self.snapshot())
                (self.old / name).unlink()

    def test_reparse_import_lock_is_rejected(self):
        lock = self.old / 'work/map-packages/import.lock'
        lock.parent.mkdir(parents=True)
        target = self.old / 'work/user-routes'
        if os.name == 'nt':
            # Real junctions exercise reparse rejection without symlink privilege.
            created = subprocess.run(['cmd', '/c', 'mklink', '/J', str(lock), str(target)],
                                     capture_output=True, timeout=20)
            self.assertEqual(created.returncode, 0, created.stderr)
        else:
            lock.symlink_to(target, target_is_directory=True)
        with self.assertRaises(migration.MigrationError):
            self.migrate()
        self.assertFalse((self.new / 'work').exists())
        self.assertEqual((target / 'a.json').read_text(), 'synthetic')

    def test_file_import_lock_link_evidence_is_rejected_before_open(self):
        # File symlink creation needs a Windows privilege unavailable in CI.
        # Substitute only the existing path-safety link observation.
        import resource_transaction
        self.write('work/map-packages/import.lock', 'preserve-link-target')
        lock = self.old / 'work/map-packages/import.lock'
        before = self.snapshot()
        real_check = resource_transaction.is_link_or_reparse
        with patch('resource_transaction.is_link_or_reparse',
                   side_effect=lambda path: Path(path) == lock or real_check(path)):
            with self.assertRaises(migration.MigrationError):
                self.migrate()
        self.assertEqual(before, self.snapshot())
        self.assertFalse((self.new / 'work').exists())

    def test_hardlinked_import_lock_is_rejected(self):
        lock = self.old / 'work/map-packages/import.lock'
        lock.parent.mkdir(parents=True)
        os.link(self.old / 'work/user-routes/a.json', lock)
        before = self.snapshot()
        with self.assertRaises(migration.MigrationError):
            self.migrate()
        self.assertEqual(before, self.snapshot())
        self.assertFalse((self.new / 'work').exists())

    def test_source_lease_does_not_ignore_destination_import_lock_during_idempotent_check(self):
        from resource_transaction import resource_runtime_lease
        self.write('work/map-packages/import.lock', '')
        self.migrate()
        with resource_runtime_lease(self.new):
            pass
        before = {p.relative_to(self.new / 'work'): p.read_bytes()
                  for p in (self.new / 'work').rglob('*') if p.is_file()}
        with self.assertRaisesRegex(migration.MigrationError, 'resource-import-busy-or-recovery-required'):
            self.migrate()
        self.assertEqual(before, {p.relative_to(self.new / 'work'): p.read_bytes()
                                 for p in (self.new / 'work').rglob('*') if p.is_file()})

    def test_interrupted_publication_resumes(self):
        original = migration.rename_no_replace
        def fail(source, target):
            if target == self.new / 'work':
                raise OSError('private path must not escape')
            return original(source, target)
        with patch.object(migration, 'rename_no_replace', side_effect=fail):
            with self.assertRaises(migration.MigrationError):
                self.migrate()
        self.assertFalse((self.new / 'work').exists())
        self.assertNotIn('private path', (self.new / 'legacy-migration.json').read_text())
        self.migrate()

    def test_partial_copy_resumes_without_deleting_partial(self):
        stage = self.new / '.legacy-migration-work/nightmare-memory'
        stage.mkdir(parents=True)
        (stage / 'ui-settings.json.migration-copy').write_text('torn')
        self.migrate()
        self.assertEqual(list(self.new.glob('.legacy-partial-*')), [])

    def test_private_identity_rejected(self):
        self.write('_internal/installation-profile.json', json.dumps(dict(product='XingtuQtClientTest',
            installerInstance=dict(product='XingtuQtClientTest', id='feature-test', appId='private'))))
        with self.assertRaisesRegex(migration.MigrationError, 'production-identity-required'):
            self.migrate()

    def test_unknown_install_identity_and_changed_data_root_fail_closed(self):
        self.identity['appId'] = 'not-the-production-app'
        with self.assertRaisesRegex(migration.MigrationError, 'source-install-identity-unverified'):
            self.migrate()
        self.identity['appId'] = PRODUCTION_APP_ID
        self.identity['dataRoot'] = str(self.old / 'other')
        with self.assertRaisesRegex(migration.MigrationError, 'source-data-root-mismatch'):
            self.migrate()

    def test_map_package_resources_receipts_snapshots_and_dependencies_are_copied_as_a_set(self):
        import hashlib
        author_bytes, dependency_bytes = b'synthetic-author-map', b'synthetic-shared-dependency'
        values = {
            'work/map-packages/legacy-extra.json': json.dumps(dict(packageId='legacy-extra', revision=1,
                archiveSha256='a'*64, files=[dict(path='_internal/assets/authors/legacy/map.png',
                bytes=len(author_bytes), sha256=hashlib.sha256(author_bytes).hexdigest())], requiresFiles=[dict(
                path='_internal/references/legacy/shared.json', bytes=len(dependency_bytes),
                sha256=hashlib.sha256(dependency_bytes).hexdigest())])),
            'work/map-packages/authors/author-routes/snapshot.json': '{"schema":"snapshot"}',
            'work/user-routes/gallery.json': '{"gallery":"editable"}',
        }
        for name, data in values.items():
            self.write(name, data)
        self.write('_internal/assets/authors/legacy/map.png', author_bytes.decode())
        self.write('_internal/references/legacy/shared.json', dependency_bytes.decode())
        with patch('managed_base_resources._package_constraints', return_value=({}, [], [])):
            result = self.migrate()
        self.assertEqual(result['scope'], 'settings-routes-and-map-resources-unverified')
        self.assertEqual((self.new / 'work/map-packages/legacy-unverified/metadata/legacy-extra.json').read_text(), values['work/map-packages/legacy-extra.json'])
        self.assertEqual((self.new / 'work/map-packages/legacy-unverified/payload/legacy-extra/_internal/assets/authors/legacy/map.png').read_bytes(), author_bytes)
        self.assertEqual((self.new / 'work/map-packages/legacy-unverified/payload/legacy-extra/_internal/references/legacy/shared.json').read_bytes(), dependency_bytes)
        self.assertEqual((self.new / 'work/map-packages/legacy-unverified/metadata/authors/author-routes/snapshot.json').read_text(), values['work/map-packages/authors/author-routes/snapshot.json'])
        self.assertEqual((self.new / 'work/user-routes/gallery.json').read_text(), values['work/user-routes/gallery.json'])
        with patch('managed_base_resources._package_constraints', return_value=({}, [], [])):
            self.assertEqual(self.migrate()['files'], result['files'])
        self.assertTrue(result['sourceUnchanged'])

    def test_unexpected_staging_file_rejected(self):
        self.new.mkdir(parents=True)
        (self.new / '.legacy-migration-work').mkdir()
        (self.new / '.legacy-migration-work/extra').write_text('conflict')
        with self.assertRaises(migration.MigrationError):
            self.migrate()
        self.assertFalse((self.new / 'work').exists())

    def test_overlap_rejected_before_any_write(self):
        before = sorted(str(p.relative_to(self.old)) for p in self.old.rglob('*'))
        alias = self.root / 'old-alias'
        alias.symlink_to(self.old, target_is_directory=True)
        for target in (self.old, self.old / 'new', self.old / 'missing/../new',
                       self.old.parent, alias / 'new'):
            with self.subTest(target=target):
                with self.assertRaisesRegex(migration.MigrationError, 'overlapping-roots'):
                    migration.migrate(self.old, target, identity=self.identity)
                self.assertEqual(before, sorted(str(p.relative_to(self.old)) for p in self.old.rglob('*')))

    def test_unreadable_directory_blocks_scan(self):
        # Inject scandir denial: chmod alone cannot deny a root test process.
        original = os.scandir
        blocked = self.old / 'work/nightmare-memory/samples'
        def scandir(path):
            if Path(path) == blocked:
                raise PermissionError('private absolute path')
            return original(path)
        with patch.object(os, 'scandir', side_effect=scandir):
            with self.assertRaisesRegex(migration.MigrationError, 'source-scan-error'):
                self.migrate()
        self.assertFalse((self.new / 'work').exists())
        self.migrate()

    def test_corrupt_journal_preserved(self):
        self.new.mkdir(parents=True)
        path = self.new / 'legacy-migration.json'
        valid = dict(schema=1, status='pending', source='a' * 64, files={})
        bad = ['{broken', '[]', 'null', '{}']
        for update in (dict(schema=True), dict(status=[]), dict(files=[]),
                       dict(source=None), dict(files={'x': {}}),
                       dict(files={'a'*64: dict(bytes=-1, sha256='b'*64)}),
                       dict(files={'a'*64: dict(bytes=True, sha256='b'*64)}),
                       dict(files={'a'*64: dict(bytes=1, sha256='bad')})):
            bad.append(json.dumps({**valid, **update}))
        for payload in bad:
            with self.subTest(payload=payload):
                path.write_text(payload)
                with self.assertRaisesRegex(migration.MigrationError, 'invalid-journal'):
                    self.migrate()
                self.assertEqual(path.read_text(), payload)
                self.assertFalse((self.new / 'work').exists())
                self.assertFalse(path.with_suffix('.new').exists())

    def test_same_size_different_digest_rejected(self):
        stage = self.new / '.legacy-migration-work/nightmare-memory/samples'
        stage.mkdir(parents=True)
        (stage / 'a.png').write_text('SYNTHETIC')
        with self.assertRaisesRegex(migration.MigrationError, 'staging-conflict'):
            self.migrate()
        self.assertFalse((self.new / 'work').exists())

    def test_source_changes_during_migration_rejected(self):
        original = migration.rename_no_replace
        changed = False
        def rename(source, target):
            nonlocal changed
            result = original(source, target)
            if not changed:
                changed = True
                self.write('work/nightmare-memory/samples/a.png', 'SYNTHETIC')
            return result
        with patch.object(migration, 'rename_no_replace', side_effect=rename):
            with self.assertRaisesRegex(migration.MigrationError, 'source-changed|copy-mismatch'):
                self.migrate()
        self.assertFalse((self.new / 'work').exists())

    def test_links_rejected(self):
        for directory in (False, True):
            link = self.old / 'work/nightmare-memory/linked'
            link.symlink_to(self.old / ('work/user-routes' if directory else 'work/user-routes/a.json'),
                            target_is_directory=directory)
            with self.subTest(directory=directory):
                with self.assertRaises(ValueError):
                    self.migrate()
                self.assertFalse((self.new / 'work').exists())
            link.unlink()

    def test_crash_after_publication_resumes(self):
        original = migration._save
        def save(path, state):
            if state['status'] == 'complete':
                raise KeyboardInterrupt()
            return original(path, state)
        with patch.object(migration, '_save', side_effect=save):
            with self.assertRaises(KeyboardInterrupt):
                self.migrate()
        before = {p.relative_to(self.new / 'work'): p.read_bytes()
                  for p in (self.new / 'work').rglob('*') if p.is_file()}
        self.assertEqual(json.loads((self.new / 'legacy-migration.json').read_text())['status'], 'publishing')
        self.migrate()
        self.assertEqual(before, {p.relative_to(self.new / 'work'): p.read_bytes()
                                 for p in (self.new / 'work').rglob('*') if p.is_file()})


if __name__ == '__main__':
    unittest.main()
