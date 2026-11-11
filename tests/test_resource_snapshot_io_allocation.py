"""Allocation and race regressions for the stable resource file reader."""
import os
from pathlib import Path
import stat
import tempfile
import tracemalloc
import unittest
from unittest.mock import patch

import resource_snapshot_io


class StableReadAllocationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / 'small-resource.json'

    @staticmethod
    def read(path, max_bytes):
        def no_links(candidate):
            info = Path(candidate).lstat()
            if stat.S_ISLNK(info.st_mode):
                raise ValueError('link')

        def reject(reason):
            raise ValueError(reason)

        return resource_snapshot_io.read_stable_file(
            path, max_bytes, no_links=no_links, reject=reject)

    def test_allocation_tracks_real_file_size_not_maximum_cap(self):
        expected = b'x' * 8192
        self.path.write_bytes(expected)
        tracemalloc.start()
        try:
            raw, row = self.read(self.path, 128 * 1024 * 1024)
            _current, peak = tracemalloc.get_traced_memory()
        finally:
            tracemalloc.stop()

        self.assertEqual(raw, expected)
        self.assertEqual(row['bytes'], len(expected))
        # A 128 MiB cap must not reserve a similarly sized buffer for an 8 KiB file.
        self.assertLess(peak, 4 * 1024 * 1024, f'traced peak was {peak:,} bytes')

    def test_growth_after_initial_fstat_is_still_rejected(self):
        self.path.write_bytes(b'initial')
        real_fstat = os.fstat
        calls = 0

        def grow_after_initial_observation(fd):
            nonlocal calls
            calls += 1
            info = real_fstat(fd)
            if calls == 1:
                with self.path.open('ab') as output:
                    output.write(b'-grew')
                    output.flush()
            return info

        with patch.object(resource_snapshot_io.os, 'fstat', grow_after_initial_observation):
            with self.assertRaisesRegex(ValueError, 'metadata-changed'):
                self.read(self.path, 1024)

    def test_exact_maximum_is_accepted_and_oversize_is_rejected(self):
        self.path.write_bytes(b'bounded')
        raw, _row = self.read(self.path, len(b'bounded'))
        self.assertEqual(raw, b'bounded')

        with self.assertRaisesRegex(ValueError, 'invalid-metadata-file'):
            self.read(self.path, len(b'bounded') - 1)


if __name__ == '__main__':
    unittest.main()
