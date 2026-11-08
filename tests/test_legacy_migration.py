"""Synthetic old installations only; no registry or real user directories."""
import json
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
        self.write('_internal/installation-profile.json', json.dumps(dict(product='星图',
            installerInstance=dict(product='星图', id='production', appId=PRODUCTION_APP_ID))))
        self.write('work/nightmare-memory/ui-settings.json', json.dumps(dict(
            automaticFailureScreenshots=False, actionHotkeys=[''] * 9 + ['Alt+F10'])))
        for name in ('custom-galleries.json', 'custom-routes.json', 'update-state.json', 'tutorial-state.json'):
            self.write('work/nightmare-memory/' + name, '{}')
        for name in ('nightmare-memory/samples/a.png', 'difficult-memory/confirmed.jsonl',
                     'seasons/s1/nightmare-memory/accepted/a.png', 'user-routes/a.json'):
            self.write('work/' + name, 'synthetic')
        self.write('work/nightmare-memory/failures/private.png', 'excluded')
        self.write('outputs/诊断日志/private.zip', 'excluded')

    def write(self, name, data):
        p = self.old / name
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(data, encoding='utf-8')

    def snapshot(self):
        return {p.relative_to(self.old).as_posix(): p.read_bytes() for p in self.old.rglob('*') if p.is_file()}

    def test_complete_unchanged_idempotent_preferences(self):
        before = self.snapshot()
        result = migration.migrate(self.old, self.new)
        self.assertEqual(result, migration.migrate(self.old, self.new))
        self.assertEqual(before, self.snapshot())
        settings = json.loads((self.new / 'work/nightmare-memory/ui-settings.json').read_text())
        self.assertIs(settings['automaticFailureScreenshots'], False)
        self.assertEqual(settings['actionHotkeys'][9], 'Alt+F10')
        self.assertFalse((self.new / 'work/nightmare-memory/failures').exists())
        self.assertNotIn(str(self.old), (self.new / 'legacy-migration.json').read_text())

    def test_program_files_layout(self):
        moved = self.root / 'Program Files/XiaochaoAssistant'
        moved.parent.mkdir()
        self.old.rename(moved)
        migration.migrate(moved, self.new)

    def test_conflict_no_overwrite(self):
        target = self.new / 'work/nightmare-memory/ui-settings.json'
        target.parent.mkdir(parents=True)
        target.write_text('existing')
        before = self.snapshot()
        with self.assertRaisesRegex(migration.MigrationError, 'destination-conflict'):
            migration.migrate(self.old, self.new)
        self.assertEqual(target.read_text(), 'existing')
        self.assertEqual(before, self.snapshot())

    def test_receipts_and_pending_fail_closed(self):
        for name in ('work/map-packages/installed/broken.json', 'work/qt-recovery-pending.json'):
            with self.subTest(name=name):
                self.write(name, '{broken')
                with self.assertRaises(migration.MigrationError):
                    migration.migrate(self.old, self.new)
                self.assertFalse((self.new / 'work').exists())
                (self.old / name).unlink()
                if 'map-packages' in name:
                    (self.old / 'work/map-packages/installed').rmdir()
                    (self.old / 'work/map-packages').rmdir()

    def test_interrupted_publication_resumes(self):
        original = migration.rename_no_replace
        def fail(source, target):
            if target == self.new / 'work':
                raise OSError('private path must not escape')
            return original(source, target)
        with patch.object(migration, 'rename_no_replace', side_effect=fail):
            with self.assertRaises(migration.MigrationError):
                migration.migrate(self.old, self.new)
        self.assertFalse((self.new / 'work').exists())
        self.assertNotIn('private path', (self.new / 'legacy-migration.json').read_text())
        migration.migrate(self.old, self.new)

    def test_partial_copy_resumes_without_deleting_partial(self):
        stage = self.new / '.legacy-migration-work/nightmare-memory'
        stage.mkdir(parents=True)
        (stage / 'ui-settings.json.migration-copy').write_text('torn')
        migration.migrate(self.old, self.new)
        self.assertEqual([p.read_text() for p in self.new.glob('.legacy-partial-*')], ['torn'])

    def test_private_identity_rejected(self):
        self.write('_internal/installation-profile.json', json.dumps(dict(product='XingtuQtClientTest',
            installerInstance=dict(product='XingtuQtClientTest', id='feature-test', appId='private'))))
        with self.assertRaisesRegex(migration.MigrationError, 'production-identity-required'):
            migration.migrate(self.old, self.new)

    def test_changed_source_and_links_rejected(self):
        self.new.mkdir(parents=True)
        (self.new / '.legacy-migration-work').mkdir()
        (self.new / '.legacy-migration-work/extra').write_text('conflict')
        with self.assertRaises(migration.MigrationError):
            migration.migrate(self.old, self.new)
        self.assertFalse((self.new / 'work').exists())


if __name__ == '__main__':
    unittest.main()
