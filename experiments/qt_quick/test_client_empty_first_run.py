"""Isolated source first run owns only its marker; no map copy or cleanup."""
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import client
import resource_transaction


class EmptyFirstRunTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.work = Path(self.temp.name) / 'work'
        self.target = self.work / 'isolated'
        self.patchers = [patch.object(client, 'PRODUCTION', False), patch.object(client, 'WORK_ROOT', self.work),
            patch('client_paths.WORK_ROOT', self.work)]
        for patcher in self.patchers:
            patcher.start()
            self.addCleanup(patcher.stop)

    def test_first_launch_creates_only_empty_owned_root_and_repeat_preserves_data(self):
        self.assertEqual(client.prepare_first_run(self.target), self.target)
        self.assertEqual({p.name for p in self.target.iterdir()}, {'.qt-lab-owner'})
        personal = self.target / 'personal.json'
        personal.write_bytes(b'personal data')
        self.assertEqual(client.prepare_first_run(self.target), self.target)
        self.assertEqual(personal.read_bytes(), b'personal data')

    def test_existing_unowned_target_is_not_overwritten(self):
        self.target.mkdir(parents=True)
        personal = self.target / 'personal.json'
        personal.write_bytes(b'keep')
        with self.assertRaises(ValueError):
            client.prepare_first_run(self.target)
        self.assertEqual(personal.read_bytes(), b'keep')
        self.assertFalse((self.target / '.qt-lab-owner').exists())

    def test_foreign_file_in_staging_survives_failed_publication(self):
        original = resource_transaction.rename_owned_directory_no_replace
        def inject(source, target, identity, *, before_move):
            (source / 'foreign.txt').write_bytes(b'keep foreign')
            return original(source, target, identity, before_move=before_move)
        with patch.object(resource_transaction, 'rename_owned_directory_no_replace', side_effect=inject):
            with self.assertRaisesRegex(ValueError, 'ownership changed'):
                client.prepare_first_run(self.target)
        staging = list(self.work.glob('isolated.preparing-*'))
        self.assertEqual(len(staging), 1)
        self.assertEqual((staging[0] / 'foreign.txt').read_bytes(), b'keep foreign')
        self.assertFalse(self.target.exists())


if __name__ == '__main__':
    unittest.main()
