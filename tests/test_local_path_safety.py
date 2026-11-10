"""Real filesystem link rejection through both production resource guards."""
import os
from pathlib import Path
import stat
import tempfile
import unittest
from unittest.mock import patch

import local_path_safety as paths
import resource_transaction as transaction
import star_map_import as importer


class LocalPathSafetyTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()

    def assert_rejected(self, path):
        with self.assertRaisesRegex(importer.ImportRejected, 'linked-target'):
            importer._no_links(path)
        with self.assertRaisesRegex(transaction.ResourceTransactionError, 'resource-linked-target'):
            transaction._safe(path)

    def symlink(self, link, target, *, directory=False):
        try:
            link.symlink_to(target, target_is_directory=directory)
        except OSError as error:
            if os.name == 'nt' and getattr(error, 'winerror', None) == 1314:
                self.skipTest('Windows token cannot create symlinks')
            raise

    def test_real_regular_and_missing_components_match_original_guard(self):
        file = self.root / 'plain.txt'
        file.write_bytes(b'fixture')
        for path in (self.root, file, self.root / 'missing' / 'child'):
            importer._no_links(path)
            self.assertEqual(transaction._safe(path), path)
        self.assertFalse(paths.is_link_or_reparse(file))
        with self.assertRaises(FileNotFoundError):
            paths.is_link_or_reparse(self.root / 'missing')

    def test_real_file_directory_and_dangling_links_remain_rejected(self):
        file = self.root / 'file'
        file.write_bytes(b'fixture')
        directory = self.root / 'directory'
        directory.mkdir()
        for name, target, is_dir in (('file-link', file, False),
                                     ('directory-link', directory, True),
                                     ('dangling', self.root / 'absent', False)):
            link = self.root / name
            self.symlink(link, target, directory=is_dir)
            self.assertTrue(paths.is_link_or_reparse(link))
            self.assert_rejected(link)
            if is_dir:
                self.assert_rejected(link / 'not-yet-created' / 'file')

    def test_later_replacement_is_rechecked_instead_of_cached(self):
        directory = self.root / 'slot'
        directory.mkdir()
        child = directory / 'future'
        importer._no_links(child)
        transaction._safe(child)
        directory.rename(self.root / 'retained-original')
        self.symlink(directory, self.root / 'retained-original', directory=True)
        self.assert_rejected(child)

    def test_failed_windows_query_uses_original_lstat_result_and_errors(self):
        file = self.root / 'file'
        file.write_bytes(b'fixture')
        with patch.object(paths, '_get_attributes', return_value=0xFFFFFFFF):
            self.assertFalse(paths.is_link_or_reparse(file))
            with self.assertRaises(FileNotFoundError):
                paths.is_link_or_reparse(self.root / 'missing')
            with patch.object(Path, 'lstat', side_effect=PermissionError('injected denied')):
                with self.assertRaises(PermissionError):
                    paths.is_link_or_reparse(file)

    def test_nul_is_not_silently_truncated_by_windows_api(self):
        with patch.object(paths, '_get_attributes', return_value=0) as query:
            with self.assertRaises(ValueError):
                paths.is_link_or_reparse(self.root / 'file\x00ignored')
            query.assert_not_called()

    def test_real_long_path_keeps_existing_lstat_semantics(self):
        directory = self.root
        for _ in range(8):
            directory /= 'long-resource-directory-0123456789'
        directory.mkdir(parents=True)
        file = directory / 'reference.npz'
        file.write_bytes(b'resource')
        self.assertGreater(len(str(file)), 260)
        self.assertFalse(paths.is_link_or_reparse(file))
        importer._no_links(file)
        transaction._safe(file)

    @unittest.skipUnless(os.name == 'nt', 'native Windows attribute semantics')
    def test_native_query_agrees_with_lstat(self):
        file = self.root / 'file'
        file.write_bytes(b'fixture')
        for path in (file, self.root, *self.root.parents):
            info = path.lstat()
            expected = stat.S_ISLNK(info.st_mode) or bool(info.st_file_attributes & 0x400)
            self.assertEqual(paths.is_link_or_reparse(path), expected)


if __name__ == '__main__':
    unittest.main()
