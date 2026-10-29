"""Production cache locking/publication, controlled response bytes; no network."""
import hashlib
import io
from pathlib import Path
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'app'))
import star_map_download as download


class Response(io.BytesIO):
    def __init__(self, body):
        super().__init__(body)
        self.headers = {'Content-Length': str(len(body))}


class DownloadCacheRecovery(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / '下载缓存-测试'
        self.root.mkdir()
        self.good = b'verified-package-bytes'
        digest = hashlib.sha256(self.good).hexdigest()
        self.service = download.MapPackageDownloadService('https://example.invalid/index.json', self.root)
        # Only catalog transport is controlled; cache verification, Windows file
        # locks, quarantine, download hashing and atomic publication are real.
        self.service._packages = {'nightmare-suju': dict(packageId='nightmare-suju',
            bytes=len(self.good), sha256=digest, url='https://example.invalid/package.zip')}
        self.cached = self.root / f'nightmare-suju-{digest}.zip'

    def backups(self):
        return list(self.root.glob('invalid-cache-*/*.zip'))

    def test_bad_size_and_same_size_bad_hash_are_preserved_then_redownloaded(self):
        for bad in (b'bad', b'x'*len(self.good)):
            with self.subTest(size=len(bad)):
                self.cached.write_bytes(bad)
                before = set(self.backups())
                unrelated = self.root/'user-selected.zip'
                unrelated.write_bytes(b'user-content')
                with patch.object(self.service, '_open', return_value=Response(self.good)) as request:
                    result = self.service.download_package('nightmare-suju')
                self.assertEqual(result.read_bytes(), self.good)
                request.assert_called_once()
                saved, = set(self.backups()) - before
                self.assertEqual(saved.read_bytes(), bad)
                self.assertEqual(unrelated.read_bytes(), b'user-content')
                self.assertEqual(list(self.root.glob('*.part')), [])

    def test_verified_cache_is_reused_without_download_or_backup(self):
        self.cached.write_bytes(self.good)
        with patch.object(self.service, '_open', side_effect=AssertionError('cache opened network')):
            self.assertEqual(self.service.download_package('nightmare-suju'), self.cached)
        self.assertEqual(self.backups(), [])

    def test_network_failure_then_manual_retry_does_not_reuse_poisoned_cache(self):
        self.cached.write_bytes(b'broken')
        with patch.object(self.service, '_open', side_effect=TimeoutError('controlled-timeout')):
            with self.assertRaises(download.DownloadError):
                self.service.download_package('nightmare-suju')
        self.assertFalse(self.cached.exists())
        self.assertEqual([p.read_bytes() for p in self.backups()], [b'broken'])
        with patch.object(self.service, '_open', return_value=Response(self.good)):
            self.assertEqual(self.service.download_package('nightmare-suju').read_bytes(), self.good)
        self.assertEqual(len(self.backups()), 1)

    def test_cancel_after_quarantine_keeps_backup_and_next_request_can_succeed(self):
        self.cached.write_bytes(b'broken')
        cancelled = threading.Event()
        real = self.service._quarantine_invalid_cache
        def quarantine(*args):
            real(*args)
            cancelled.set()
        with patch.object(self.service, '_quarantine_invalid_cache', side_effect=quarantine), \
                patch.object(self.service, '_open', side_effect=AssertionError('cancel opened network')):
            with self.assertRaises(download.DownloadCancelled):
                self.service.download_package('nightmare-suju', cancel_event=cancelled)
        self.assertEqual([p.read_bytes() for p in self.backups()], [b'broken'])
        with patch.object(self.service, '_open', return_value=Response(self.good)):
            self.assertEqual(self.service.download_package('nightmare-suju').read_bytes(), self.good)

    def test_invalid_new_download_never_publishes_or_discards_backup(self):
        self.cached.write_bytes(b'old-broken')
        with patch.object(self.service, '_open', return_value=Response(b'x'*len(self.good))):
            with self.assertRaisesRegex(download.DownloadError, 'package-size-or-sha-mismatch'):
                self.service.download_package('nightmare-suju')
        self.assertFalse(self.cached.exists())
        self.assertEqual([p.read_bytes() for p in self.backups()], [b'old-broken'])
        self.assertEqual(list(self.root.glob('*.part')), [])

    def test_cache_cannot_be_changed_or_replaced_between_validation_and_handle_rename(self):
        self.cached.write_bytes(b'broken')
        replacement = self.root/'unrelated.zip'
        replacement.write_bytes(b'new-external-content')
        real = download._rename_locked_file
        def race(stream, target):
            with self.assertRaises(PermissionError):
                self.cached.write_bytes(b'new-external-content')
            with self.assertRaises(PermissionError):
                replacement.replace(self.cached)
            return real(stream, target)
        with patch.object(download, '_rename_locked_file', side_effect=race), \
                patch.object(self.service, '_open', return_value=Response(self.good)):
            self.service.download_package('nightmare-suju')
        self.assertEqual(self.cached.read_bytes(), self.good)
        self.assertEqual(replacement.read_bytes(), b'new-external-content')
        self.assertEqual([p.read_bytes() for p in self.backups()], [b'broken'])

    def test_bad_cache_does_not_bypass_manual_retry_limit(self):
        self.cached.write_bytes(b'broken')
        with patch.object(self.service, '_open', side_effect=TimeoutError('controlled-timeout')) as request:
            for _ in range(download.MAX_MANUAL_ATTEMPTS):
                with self.assertRaises(download.DownloadError):
                    self.service.download_package('nightmare-suju')
            with self.assertRaisesRegex(download.DownloadError, 'manual-retry-limit'):
                self.service.download_package('nightmare-suju')
        self.assertEqual(request.call_count, download.MAX_MANUAL_ATTEMPTS)
        self.assertEqual(len(self.backups()), 1)


if __name__ == '__main__':
    unittest.main()
