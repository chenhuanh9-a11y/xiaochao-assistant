"""Synthetic old installations only; no registry or real user directories."""
import json
import os
from pathlib import Path
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
        values = {
            'work/map-packages/author-routes.json': '{"packageId":"author-routes","requiresFiles":["shared-base"]}',
            'work/map-packages/authors/author-routes/snapshot.json': '{"schema":"snapshot"}',
            'work/maps/author-routes/manifest.json': '{"files":["maps/author-routes/floor.png"]}',
            'work/maps/author-routes/floor.png': 'synthetic-floor-bytes',
            'work/maps/shared-base/floor.png': 'synthetic-dependency-bytes',
            'work/user-routes/gallery.json': '{"gallery":"editable"}',
        }
        for name, data in values.items():
            self.write(name, data)
        with patch('managed_base_resources._package_constraints', return_value=({}, [], [])):
            result = self.migrate()
        self.assertEqual(result['scope'], 'settings-routes-and-map-resources')
        for name, data in values.items():
            self.assertEqual((self.new / 'work' / name.removeprefix('work/')).read_text(), data)
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
