"""Synthetic temporary trees only: cleanup must never follow links."""
import os
from pathlib import Path
import sys
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'app'))
import nightmare_candidate as candidate


class RetentionLinkTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name).resolve()
        self.root = self.base / 'memory'
        self.root.mkdir()
        self.outside = self.base / 'outside'
        self.outside.mkdir()
        self.sentinel = self.outside / 'old.jpg'
        self.sentinel.write_bytes(b'outside evidence')

    def link(self, link, target):
        try:
            link.symlink_to(target, target_is_directory=target.is_dir())
        except OSError as error:
            self.skipTest(f'symlink unavailable: {error}')

    def test_root_and_ancestor_links_are_not_enumerated(self):
        alias = self.base / 'alias'
        self.link(alias, self.outside)
        nested = self.outside / 'nested'
        nested.mkdir()
        (nested / 'old.jpg').write_bytes(b'keep')
        self.assertEqual(candidate._oldest_first(alias), [])
        self.assertEqual(candidate._oldest_first(alias / 'nested'), [])

    def test_file_and_directory_links_are_not_candidates(self):
        self.link(self.root / 'linked.jpg', self.sentinel)
        self.link(self.root / 'nested', self.outside)
        own = self.root / 'own.jpg'
        own.write_bytes(b'owned')
        self.assertEqual([entry.path for entry in candidate._oldest_first(self.root)], [own])

    def test_outside_directory_cannot_supply_candidates(self):
        self.assertEqual(candidate._oldest_first(self.outside, self.root), [])
        self.assertEqual(candidate._oldest_first(self.root / '..' / 'outside', self.root), [])

    def test_default_environment_root_link_cannot_evict_external_files(self):
        import subprocess
        failures = self.outside / 'failures'
        failures.mkdir()
        sentinel = failures / 'old.jpg'
        sentinel.write_bytes(b'KEEP')
        alias = self.base / 'alias'
        self.link(alias, self.outside)
        script = '''
from types import SimpleNamespace
import nightmare_candidate as c
s = c.NightmareMemoryStore(SimpleNamespace(pack_id='p', maps=[], by_id={}))
s._failure_sampling.max_disk_bytes = 1
try:
    s._reserve_failure_bytes(0)
except OSError:
    pass
'''
        result = subprocess.run([sys.executable, '-B', '-c', script],
                                env=dict(os.environ, IDENTITYV_NIGHTMARE_MEMORY_ROOT=str(alias),
                                         PYTHONPATH=str(Path(candidate.__file__).parent)),
                                capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(sentinel.exists())
        self.assertEqual(sentinel.read_bytes(), b'KEEP')

    def test_replaced_regular_candidate_is_preserved(self):
        own = self.root / 'old.jpg'
        own.write_bytes(b'old')
        listed = candidate._oldest_first(self.root)
        own.rename(self.root / 'saved.jpg')
        own.write_bytes(b'new evidence')
        candidate._delete_quietly(listed, self.root)
        self.assertTrue(own.exists())
        self.assertEqual(own.read_bytes(), b'new evidence')

    def test_failure_eviction_rechecks_listed_identity(self):
        store = candidate.NightmareMemoryStore(SimpleNamespace(pack_id='p', maps=[], by_id={}),
                                               root=self.root, portable_seed_path=self.base / 'none')
        failures = self.root / 'failures'
        failures.mkdir()
        own = failures / 'old.jpg'
        own.write_bytes(b'old')
        original = candidate._oldest_first
        def replaced(*args):
            listed = original(*args)
            own.rename(failures / 'saved.jpg')
            own.write_bytes(b'new evidence')
            return listed
        store._failure_sampling.max_disk_bytes = 1
        with patch.object(candidate, '_oldest_first', replaced):
            try:
                store._reserve_failure_bytes(0)
            except OSError:
                pass
        self.assertTrue(own.exists())
        self.assertEqual(own.read_bytes(), b'new evidence')

    def test_sample_cleanup_preserves_linked_floor_and_user_evidence(self):
        pack = SimpleNamespace(pack_id='p', by_id={'m': SimpleNamespace(id='m')},
                               maps=[SimpleNamespace(id='m')])
        store = candidate.NightmareMemoryStore(pack, root=self.root,
                                               portable_seed_path=self.base / 'none')
        directory = self.root / 'samples' / 'm'
        directory.mkdir(parents=True)
        self.link(directory / '1', self.outside)
        for index in range(20):
            (self.outside / f'{index}.jpg').write_bytes(b'keep')
        for category in ('accepted', 'rejected', 'user-gallery'):
            (self.root / category).mkdir()
            (self.root / category / 'old.jpg').write_bytes(b'keep')
        store._prune_samples('m', '1')
        self.assertEqual(len(list(self.outside.iterdir())), 21)
        for category in ('accepted', 'rejected', 'user-gallery'):
            self.assertEqual((self.root / category / 'old.jpg').read_bytes(), b'keep')

    @unittest.skipUnless(os.name == 'nt', 'requires Windows junction filesystem')
    def test_real_windows_junction_is_not_traversed(self):
        import subprocess
        junction = self.root / 'junction'
        subprocess.run(['cmd', '/c', 'mklink', '/J', str(junction), str(self.outside)],
                       check=True, capture_output=True)
        try:
            self.assertEqual(candidate._oldest_first(junction), [])
            self.assertEqual(candidate._oldest_first(self.root), [])
        finally:
            junction.rmdir()

    def test_reparse_attribute_is_rejected_even_without_symlink_mode(self):
        own = self.root / 'old.jpg'
        own.write_bytes(b'owned')
        original = Path.lstat
        def lstat(path, *args, **kwargs):
            result = original(path, *args, **kwargs)
            if path == self.root:
                return SimpleNamespace(st_mode=result.st_mode, st_file_attributes=0x400)
            return result
        with patch.object(Path, 'lstat', lstat):
            self.assertEqual(candidate._oldest_first(self.root), [])

    def test_deletion_rechecks_parent_and_candidate_containment(self):
        directory = self.root / 'floor'
        directory.mkdir()
        own = directory / 'old.jpg'
        own.write_bytes(b'owned')
        listed = candidate._oldest_first(self.root)
        directory.rename(self.root / 'moved')
        self.link(directory, self.outside)
        outside = candidate._oldest_first(self.outside)
        candidate._delete_quietly(listed + outside, self.root)
        self.assertEqual(self.sentinel.read_bytes(), b'outside evidence')
        self.assertTrue((self.root / 'moved' / 'old.jpg').exists())

    def test_failure_accounting_excludes_links_and_keeps_journal(self):
        pack = SimpleNamespace(pack_id='p', by_id={}, maps=[])
        store = candidate.NightmareMemoryStore(pack, root=self.root,
                                               portable_seed_path=self.base / 'none')
        failures = self.root / 'failures'
        failures.mkdir()
        self.link(failures / 'linked.jpg', self.sentinel)
        self.link(failures / 'nested', self.outside)
        journal = self.root / 'alignment-failures.jsonl'
        journal.write_bytes(b'{}\n')
        store._failure_sampling.max_disk_bytes = 4
        store._reserve_failure_bytes(1)
        self.assertEqual(store._failure_disk_bytes, 4)
        self.assertTrue((failures / 'linked.jpg').is_symlink())
        self.assertEqual(journal.read_bytes(), b'{}\n')
        self.assertEqual(self.sentinel.read_bytes(), b'outside evidence')


if __name__ == '__main__':
    unittest.main()
