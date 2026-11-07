"""Complete source-pinned legacy recognition through real guarded base repair."""
from pathlib import Path
import hashlib
import tempfile
import unittest
from unittest.mock import patch

import managed_base_resources as managed


class LegacyS0BaseAdoptionTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.old = {'_internal/references/one.json': b'published-one',
                    '_internal/references/two.json': b'published-two'}
        self.new = {name: data + b'-new' for name, data in self.old.items()}
        self.previous = [self.row(name, data) for name, data in self.old.items()]
        self.policy = (('a' * 40, 'b' * 64, tuple((r['path'], r['bytes'], r['sha256'])
                                              for r in self.previous)),)
        self.enterContext(patch.object(managed, 'REVIEWED_LEGACY_S0_BASE_RELEASES', self.policy))
        for name, data in self.old.items():
            self.write(name, data)
        self.stage()
        self.write('work/nightmare-memory/ui-settings.json', b'preserve-user-setting')

    @staticmethod
    def row(name, data):
        return dict(path=name, bytes=len(data), sha256=hashlib.sha256(data).hexdigest())

    def write(self, name, data):
        target = self.root / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)

    def stage(self, names=None):
        names = list(self.new) if names is None else names
        manifest = managed.manifest_for_rows([self.row(name, self.new[name]) for name in names], 'fixture-new')
        self.write(managed.CANDIDATE + '/manifest.json', managed._encoded(manifest))
        for name in names:
            self.write(managed.CANDIDATE + '/payload/' + name, self.new[name])

    def test_complete_published_bytes_upgrade_without_claiming_user_data(self):
        self.assertEqual(len(managed.plan_managed_base_resources(self.root)['changes']), 2)
        self.assertEqual(managed.repair_managed_base_resources(self.root)['status'], 'repaired')
        for name, data in self.new.items():
            self.assertEqual((self.root / name).read_bytes(), data)
        self.assertEqual((self.root / 'work/nightmare-memory/ui-settings.json').read_bytes(), b'preserve-user-setting')
        self.assertEqual(managed.repair_managed_base_resources(self.root)['status'], 'healthy')

    def test_one_mutation_or_missing_file_blocks_whole_adoption(self):
        name = next(iter(self.old))
        for changed in (b'user-edited', None):
            if changed is None:
                (self.root / name).unlink()
            else:
                self.write(name, changed)
            self.assertEqual(managed.plan_managed_base_resources(self.root)['status'], 'blocked')
            with self.assertRaises(managed.ManagedBaseResourceError):
                managed.repair_managed_base_resources(self.root)
            other = list(self.old)[1]
            self.assertEqual((self.root / other).read_bytes(), self.old[other])
            self.assertFalse((self.root / managed.STATE).exists())
            self.write(name, self.old[name])

    def test_candidate_omission_does_not_relinquish_legacy_ownership(self):
        self.stage([next(iter(self.old))])
        self.assertEqual(managed.plan_managed_base_resources(self.root)['status'], 'blocked')
        self.assertFalse((self.root / managed.STATE).exists())

    def test_incoming_manifest_without_source_lock_cannot_authorize_adoption(self):
        with patch.object(managed, 'REVIEWED_LEGACY_S0_BASE_RELEASES', ()):
            self.assertEqual(managed.plan_managed_base_resources(self.root)['status'], 'blocked')

    def test_author_dependency_constraint_still_blocks_known_legacy_upgrade(self):
        with patch.object(managed, '_package_constraints', return_value=({}, [('author', self.previous[0])], [])):
            plan = managed.plan_managed_base_resources(self.root)
            self.assertEqual(plan['status'], 'blocked')
            self.assertIn('installed-package-dependency-conflict', [row['reason'] for row in plan['issues']])

    def test_failed_replacement_rolls_back_adopted_original_bytes(self):
        original = managed.transaction._move_owned
        calls = []
        def fail_second(*args, **kwargs):
            calls.append(args)
            if len(calls) == 2:
                raise OSError('owned negative fixture')
            return original(*args, **kwargs)
        with patch.object(managed.transaction, '_move_owned', side_effect=fail_second):
            with self.assertRaises(OSError):
                managed.repair_managed_base_resources(self.root)
        for name, data in self.old.items():
            self.assertEqual((self.root / name).read_bytes(), data)
        self.assertFalse((self.root / managed.STATE).exists())

    def test_malformed_source_policy_fails_closed(self):
        for value in ([], (('bad', 'b' * 64, ()),), self.policy * 2):
            with self.subTest(value=value), patch.object(managed, 'REVIEWED_LEGACY_S0_BASE_RELEASES', value):
                with self.assertRaises(managed.ManagedBaseResourceError):
                    managed.plan_managed_base_resources(self.root)


if __name__ == '__main__':
    unittest.main()
