"""Linkless single-file cache/receipt publication, synthetic transport only.

POSIX tests substitute the Windows locked-handle adapter, not hashes or publisher
state. Native Windows variants use real locks/FileRenameInfo and are separate.
"""
from contextlib import contextmanager, ExitStack
import errno
import hashlib
import os
from pathlib import Path
import threading
import unittest
from unittest.mock import patch

import resource_transaction as resource
import star_map_download as download
import author_package_management as manager
import test_download_cache_recovery as cache_fixture
import test_legacy_author_adoption as adoption_fixture


def unsupported(*_a, **_kw):
    raise OSError(errno.EOPNOTSUPP, 'no hardlinks')


def assert_destination_collision(test, error):
    # The Windows handle publisher exposes native ERROR_FILE_EXISTS or
    # ERROR_ALREADY_EXISTS; POSIX renameat2 exposes EEXIST. Other I/O failures
    # are not success.
    if os.name == 'nt':
        test.assertIn(error.errno, {80, 183})
        test.assertEqual(error.strerror, 'upgrade-owned-target-rename-failed')
    else:
        test.assertIsInstance(error, FileExistsError)
        test.assertEqual(error.errno, errno.EEXIST)


class LinklessAdoption(unittest.TestCase):
    def fixture(self):
        case = adoption_fixture.LegacyAdoptionTests('runTest')
        case.setUp(); self.addCleanup(case.doCleanups)
        case.f.write(adoption_fixture.fixture.SUJU, b'approved')
        return case

    def test_exact_legacy_receipt_adopts_without_links_and_is_idempotent(self):
        case = self.fixture()
        original = case.f.root / adoption_fixture.fixture.SUJU
        before = (original.read_bytes(), original.stat().st_ino)
        with patch.object(resource.os, 'link', side_effect=unsupported):
            self.assertEqual(manager.adopt_legacy_author_package(case.f.root, 'nightmare-suju')['status'], 'legacy-adopted')
            self.assertEqual(manager.adopt_legacy_author_package(case.f.root, 'nightmare-suju')['status'], 'already-managed')
        self.assertEqual((original.read_bytes(), original.stat().st_ino), before)

    def test_cancel_and_receipt_destination_race_do_not_claim_foreign_data(self):
        case = self.fixture()
        import map_package_upgrade as transaction
        move = transaction._move_owned
        original = case.f.root / adoption_fixture.fixture.SUJU
        original_stamp = (original.read_bytes(), transaction._identity(original.stat()))
        foreign = []
        def race(source, target, *args, **kwargs):
            if Path(target) == case.f.receipt():
                Path(target).write_bytes(b'foreign receipt')
                foreign.append((Path(target).read_bytes(), transaction._identity(Path(target).stat())))
            return move(source, target, *args, **kwargs)
        with patch.object(resource.os, 'link', side_effect=unsupported):
            with self.assertRaisesRegex(ValueError, 'cancelled'):
                manager.adopt_legacy_author_package(case.f.root, 'nightmare-suju', cancelled=lambda: True)
            self.assertFalse(case.f.receipt().exists())
            with patch.object(transaction, '_move_owned', side_effect=race), self.assertRaises(OSError) as rejected:
                manager.adopt_legacy_author_package(case.f.root, 'nightmare-suju')
        assert_destination_collision(self, rejected.exception)
        self.assertEqual(len(foreign), 1)  # The actual publisher encountered the raced target.
        self.assertEqual((case.f.receipt().read_bytes(), transaction._identity(case.f.receipt().stat())), foreign[0])
        self.assertEqual((original.read_bytes(), transaction._identity(original.stat())), original_stamp)
        case.f.assert_base_preserved()
        self.assertEqual(list((case.f.root / 'work/map-packages').glob('legacy-receipt-*.tmp')), [])


class CacheProtocol:
    def setUp(self):
        self.case = cache_fixture.DownloadCacheRecovery('runTest')
        self.case.setUp(); self.addCleanup(self.case.doCleanups)
        self.stack = ExitStack(); self.addCleanup(self.stack.close)
        self.stack.enter_context(patch.object(resource.os, 'link', side_effect=unsupported))
        self.stack.enter_context(patch.object(self.case.service, '_open', side_effect=lambda *_: cache_fixture.Response(self.case.good)))

    def download(self, **kwargs):
        return self.case.service.download_package('nightmare-suju', **kwargs)

    def test_success_then_reuse_never_needs_hardlink(self):
        self.assertEqual(self.download().read_bytes(), self.case.good)
        self.assertEqual(self.download().read_bytes(), self.case.good)
        self.assertEqual(list(self.case.root.glob('*.part')), [])

    def test_cancel_after_publication_moves_same_locked_object_back_then_cleans(self):
        cancelled = threading.Event()
        rename = download._rename_locked_file
        def interrupt(stream, target):
            result = rename(stream, target)
            if target == self.case.cached:
                cancelled.set()
            return result
        with patch.object(download, '_rename_locked_file', side_effect=interrupt), self.assertRaises(download.DownloadCancelled):
            self.download(cancel_event=cancelled)
        self.assertFalse(self.case.cached.exists())
        self.assertEqual(list(self.case.root.glob('*.part')), [])

    def test_existing_destination_is_never_overwritten(self):
        rename = download._rename_locked_file
        def race(stream, target):
            if target == self.case.cached:
                target.write_bytes(b'unknown other writer')
            return rename(stream, target)
        with patch.object(download, '_rename_locked_file', side_effect=race), self.assertRaises(download.DownloadError):
            self.download()
        self.assertEqual(self.case.cached.read_bytes(), b'unknown other writer')

    def test_cancel_rollback_blocked_keeps_both_objects(self):
        cancelled = threading.Event()
        rename = download._rename_locked_file
        def race(stream, target):
            if target == self.case.cached:
                result = rename(stream, target); cancelled.set(); return result
            target.write_bytes(b'foreign temp')
            return rename(stream, target)
        with patch.object(download, '_rename_locked_file', side_effect=race), self.assertRaisesRegex(download.DownloadError, 'cache-publication-rollback-required'):
            self.download(cancel_event=cancelled)
        self.assertEqual(self.case.cached.read_bytes(), self.case.good)
        self.assertEqual([p.read_bytes() for p in self.case.root.glob('*.part')], [b'foreign temp'])


@unittest.skipIf(os.name == 'nt', 'Native Windows class covers actual locks')
class PortableCacheProtocol(CacheProtocol, unittest.TestCase):
    def setUp(self):
        super().setUp()
        paths = {}
        @contextmanager
        def unlocked_fixture(path, *, writable=False, movable=False, expected_identity=None):
            with path.open('r+b' if writable else 'rb') as stream:
                info = os.fstat(stream.fileno())
                if expected_identity is not None:
                    self.assertEqual((info.st_dev, info.st_ino), expected_identity)
                if writable:
                    self.assertTrue(movable)
                paths[id(stream)] = path
                yield stream
        def move_fixture(stream, target):
            resource.rename_no_replace(paths[id(stream)], target)
            paths[id(stream)] = target
        self.stack.enter_context(patch.object(download, '_locked_file', side_effect=unlocked_fixture))
        self.stack.enter_context(patch.object(download, '_rename_locked_file', side_effect=move_fixture))


@unittest.skipUnless(os.name == 'nt', 'Windows handles require native Windows')
class NativeCacheProtocol(CacheProtocol, unittest.TestCase):
    pass


class LinklessDiagnosticExport(unittest.TestCase):
    def test_selected_zip_without_links_and_raced_destination_preserved(self):
        import tempfile
        import zipfile
        import diagnostic_export as export
        import map_package_upgrade as transaction
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            live = root / 'logs'; live.mkdir()
            destination = root / 'diagnostic.zip'
            with patch.object(resource.os, 'link', side_effect=unsupported):
                export.export_safe_diagnostic_bundle(root, live, app_version='1.1.2',
                    build_fingerprint='synthetic', destination=destination)
            self.assertTrue(zipfile.is_zipfile(destination))
            original = (destination.read_bytes(), transaction._identity(destination.stat()))
            raced = root / 'raced.zip'
            move = transaction._move_owned
            foreign = []
            def race(source, target, *args, **kwargs):
                if Path(target) == raced:
                    target.write_bytes(b'foreign file')
                    foreign.append((target.read_bytes(), transaction._identity(target.stat())))
                # Exercise the real Windows handle publisher or POSIX rename,
                # rather than a platform-specific helper Windows never calls.
                return move(source, target, *args, **kwargs)
            with patch.object(transaction, '_move_owned', side_effect=race), self.assertRaises(OSError) as rejected:
                export.export_safe_diagnostic_bundle(root, live, app_version='1.1.2',
                    build_fingerprint='synthetic', destination=raced)
            assert_destination_collision(self, rejected.exception)
            self.assertEqual(len(foreign), 1)
            self.assertEqual((raced.read_bytes(), transaction._identity(raced.stat())), foreign[0])
            self.assertEqual((destination.read_bytes(), transaction._identity(destination.stat())), original)
            self.assertEqual(list(root.glob('.diagnostic-export-*')), [])

class ConsumedTemporaryRetention(unittest.TestCase):
    def test_adoption_consumed_temp_name_can_be_reused_by_foreign_writer(self):
        case = adoption_fixture.LegacyAdoptionTests('runTest')
        case.setUp(); self.addCleanup(case.doCleanups)
        case.f.write(adoption_fixture.fixture.SUJU, b'approved')
        import map_package_upgrade as transaction
        move = transaction._move_owned
        foreign = []
        def race(source, target, *args, **kwargs):
            result = move(source, target, *args, **kwargs)
            if Path(source).name.startswith('legacy-receipt-'):
                Path(source).write_bytes(b'foreign writer after publication'); foreign.append(Path(source))
            return result
        with patch.object(resource.os, 'link', side_effect=unsupported), patch.object(transaction, '_move_owned', side_effect=race):
            self.assertEqual(manager.adopt_legacy_author_package(case.f.root, 'nightmare-suju')['status'], 'legacy-adopted')
        self.assertEqual([p.read_bytes() for p in foreign], [b'foreign writer after publication'])

    def test_diagnostic_flushes_writable_created_object_and_preserves_foreign_temp(self):
        import tempfile
        import diagnostic_export as export
        import map_package_upgrade as transaction
        move, sync = transaction._move_owned, os.fsync
        foreign, writable_checks = [], []
        def race(source, target, *args, **kwargs):
            result = move(source, target, *args, **kwargs)
            if Path(source).name.startswith('.diagnostic-export-'):
                Path(source).write_bytes(b'foreign after zip'); foreign.append(Path(source))
            return result
        def check_writable(fd):
            # fcntl is Linux-only; native tests exercise FlushFileBuffers itself.
            if os.name != 'nt':
                import fcntl
                flags = fcntl.fcntl(fd, fcntl.F_GETFL)
                if not os.path.isdir('/proc/self/fd/' + str(fd)):
                    self.assertNotEqual(flags & os.O_ACCMODE, os.O_RDONLY)
                    writable_checks.append(fd)
            return sync(fd)
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary); live = root / 'logs'; live.mkdir()
            with patch.object(transaction, '_move_owned', side_effect=race), patch.object(os, 'fsync', side_effect=check_writable):
                export.export_safe_diagnostic_bundle(root, live, app_version='1.1.2',
                    build_fingerprint='fixture', destination=root / 'log.zip')
            self.assertEqual([p.read_bytes() for p in foreign], [b'foreign after zip'])
            if os.name != 'nt':
                self.assertTrue(writable_checks)
