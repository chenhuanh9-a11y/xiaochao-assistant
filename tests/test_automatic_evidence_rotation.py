"""Offline contracts plus native Windows handle tests (not GUI/game tests)."""
import hashlib
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'app'))
import failure_sampling as sampling


def fixture(root, index=0, *, data=b'png', identity=True, age='2020-01-01T00:00:00+00:00'):
    directory = root / 'failures' / 'automatic-v2'
    directory.mkdir(parents=True, exist_ok=True)
    image = directory / ('auto-%032x.png' % index)
    image.write_bytes(data)
    info = image.stat()
    row = dict(event='automatic-failure-sample', recordedAt=age,
               samplePath=image.relative_to(root).as_posix(),
               sampleBytes=len(data), sampleSha256=hashlib.sha256(data).hexdigest())
    if identity:
        row['sampleIdentity'] = [info.st_dev, info.st_ino]
    return image, row


def journal(root, rows):
    (root / 'failures/automatic-v2/automatic-failures.jsonl').write_text(
        ''.join(json.dumps(row) + '\n' for row in rows))


class RotationContracts(unittest.TestCase):
    def test_v2_inventory_requires_original_identity(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            image, row = fixture(root, identity=False)
            journal(root, [row])
            result = sampling.automatic_storage_inventory(root, namespace='automatic-v2')
            self.assertNotEqual(result['status'], 'ready')
            self.assertTrue(image.exists())

    def test_same_bytes_replacement_identity_not_owned(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            image, row = fixture(root)
            row['sampleIdentity'][1] += 1
            journal(root, [row])
            self.assertEqual(sampling.automatic_storage_inventory(root, namespace='automatic-v2')['status'], 'ownership-changed')

    def test_read_over_one_mib_and_missing_retirement_record(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            image, row = fixture(root, data=b'x' * (1024 * 1024 + 1))
            journal(root, [row])
            result = sampling.automatic_storage_inventory(root, namespace='automatic-v2')
            self.assertEqual(result['status'], 'ready')
            self.assertEqual(result['images'], [image])
            image.unlink()  # Test simulates crash after successful handle deletion.
            result = sampling.automatic_storage_inventory(root, namespace='automatic-v2')
            self.assertEqual(result['status'], 'ready')
            self.assertEqual(result['missingRows'], [row])

    def test_pending_intent_unknown_and_hardlink_fail_closed(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            image, row = fixture(root)
            row['event'] = 'automatic-failure-image-intent'
            journal(root, [row])
            self.assertEqual(sampling.automatic_storage_inventory(root, namespace='automatic-v2')['status'], 'automatic-image-recovery-required')
            row['event'] = 'automatic-failure-sample'
            journal(root, [row])
            os.link(image, root / 'second-link')
            self.assertNotEqual(sampling.automatic_storage_inventory(root, namespace='automatic-v2')['status'], 'ready')

    def test_hardlinked_journal_has_ownership_reason_without_mutating_sentinel(self):
        for namespace in ('automatic', 'automatic-v2'):
            with self.subTest(namespace=namespace), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary) / 'root'
                directory = root / 'failures' / namespace
                directory.mkdir(parents=True)
                sentinel = Path(temporary) / 'external-sentinel'
                sentinel.write_bytes(b'unchanged external sentinel')
                os.link(sentinel, directory / 'automatic-failures.jsonl')
                report = sampling.automatic_storage_inventory(root, namespace=namespace)
                self.assertEqual(report['status'], 'ownership-unavailable')
                self.assertEqual(sentinel.read_bytes(), b'unchanged external sentinel')
                self.assertEqual(sentinel.stat().st_nlink, 2)

    def test_legacy_cleanup_is_read_only_even_if_expired(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            legacy = root / 'failures/automatic'
            legacy.mkdir(parents=True)
            path = legacy / ('auto-' + 'a' * 32 + '.png')
            path.write_bytes(b'old')
            row = dict(event='automatic-failure-sample', recordedAt='2020-01-01',
                       samplePath=path.relative_to(root).as_posix(), sampleSha256=hashlib.sha256(b'old').hexdigest())
            record = legacy / 'automatic-failures.jsonl'
            record.write_text(json.dumps(row))
            before = record.read_bytes()
            sampling.cleanup_automatic_failure_samples(root)
            self.assertEqual(record.read_bytes(), before)
            self.assertTrue(path.exists())

    @unittest.skipIf(os.name == 'nt', 'POSIX refusal contract')
    def test_posix_never_retires_or_creates(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            image, row = fixture(root)
            with self.assertRaises(OSError):
                sampling.retire_automatic_image(root, row)
            with self.assertRaises(OSError):
                sampling.create_automatic_image(root, row['samplePath'], b'new')
            self.assertTrue(image.exists())

    def test_namespace_is_not_a_path(self):
        with self.assertRaises(ValueError):
            sampling.automatic_storage_inventory('.', namespace='../elsewhere')


@unittest.skipUnless(os.name == 'nt', 'native Windows READ|DELETE handle contract; not tested on POSIX')
class NativeWindowsRotation(unittest.TestCase):
    def test_create_and_retire_larger_than_one_mib(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            relative = 'failures/automatic-v2/auto-' + 'a' * 32 + '.png'
            with sampling.automatic_storage_transaction(root, create=True, namespace='automatic-v2') as status:
                self.assertEqual(status, 'ready')
                owned = sampling.create_automatic_image(root, relative, b'x' * (1024 * 1024 + 1))
                row = dict(owned, samplePath=relative)
                self.assertEqual(sampling.retire_automatic_image(root, row), 'retired')
                self.assertFalse((root / relative).exists())
                self.assertEqual(sampling.retire_automatic_image(root, row), 'missing')

    def test_changed_file_is_not_deleted(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            image, row = fixture(root)
            image.write_bytes(b'bad')
            with sampling.automatic_storage_transaction(root, namespace='automatic-v2') as status:
                self.assertEqual(status, 'ready')
                with self.assertRaises(OSError):
                    sampling.retire_automatic_image(root, row)
            self.assertEqual(image.read_bytes(), b'bad')


class PureRotationDecisions(unittest.TestCase):
    """Mocks prove selection and journal transitions, not Windows safety."""
    def inventory(self, sizes):
        rows = [dict(event='automatic-failure-sample', recordedAt='2026-01-%02dT00:00:00+00:00' % (i + 1),
                     samplePath='failures/automatic-v2/auto-%032x.png' % i,
                     sampleBytes=size, sampleIdentity=[1, i + 1], sampleSha256='0' * 64)
                for i, size in enumerate(sizes)]
        return dict(status='ready', directory=Path('/fixture/failures/automatic-v2'),
                    rows=[(b'', row) for row in rows], imageRows=rows,
                    imageBytes=sum(sizes), images=[], missingRows=[])

    def test_seventh_image_selects_only_oldest(self):
        inventory = self.inventory([1] * 6)
        with patch.object(sampling, '_require_transaction'), \
                patch.object(sampling, 'automatic_storage_inventory', return_value=inventory), \
                patch.object(sampling, '_retire_rows') as retire:
            result = sampling.ensure_automatic_capacity('/fixture', 1)
        self.assertEqual(result['status'], 'ready')
        self.assertEqual(retire.call_args.args[2], inventory['imageRows'][:1])

    def test_byte_capacity_selects_enough_oldest_rows(self):
        inventory = self.inventory([16 * 1024 * 1024] * 4)
        with patch.object(sampling, '_require_transaction'), \
                patch.object(sampling, 'automatic_storage_inventory', return_value=inventory), \
                patch.object(sampling, '_retire_rows') as retire:
            result = sampling.ensure_automatic_capacity('/fixture', 32 * 1024 * 1024)
        self.assertEqual(result['status'], 'ready')
        self.assertEqual(retire.call_args.args[2], inventory['imageRows'][:2])

    def test_exact_capacity_needs_no_retirement(self):
        inventory = self.inventory([32 * 1024 * 1024])
        with patch.object(sampling, '_require_transaction'), \
                patch.object(sampling, 'automatic_storage_inventory', return_value=inventory), \
                patch.object(sampling, '_retire_rows') as retire:
            result = sampling.ensure_automatic_capacity('/fixture', 32 * 1024 * 1024)
        self.assertEqual(result['status'], 'ready')
        self.assertEqual(retire.call_args.args[2], [])

    def test_unknown_pending_or_changed_inventory_never_retires(self):
        for status in ('ownership-unavailable', 'ownership-changed', 'automatic-image-recovery-required'):
            with self.subTest(status=status), patch.object(sampling, '_require_transaction'), \
                    patch.object(sampling, 'automatic_storage_inventory', return_value={'status': status}), \
                    patch.object(sampling, '_recover_automatic_image_intent_locked', return_value=status), \
                    patch.object(sampling, 'retire_automatic_image') as retire:
                result = sampling.ensure_automatic_capacity('/fixture', 1)
                self.assertEqual(result['status'], status)
                retire.assert_not_called()

    def test_retirement_revalidates_and_journal_failure_preserves_count(self):
        inventory = self.inventory([1] * 6)
        with patch.object(sampling, '_require_transaction'), \
                patch.object(sampling, 'automatic_storage_inventory', return_value=inventory), \
                patch.object(sampling, 'retire_automatic_image', return_value='retired') as retire, \
                patch.object(sampling, 'replace_automatic_journal', side_effect=OSError('journal-failed')):
            result = sampling.ensure_automatic_capacity('/fixture', 1)
        retire.assert_called_once_with('/fixture', inventory['imageRows'][0], namespace='automatic-v2')
        self.assertEqual(result, dict(status='automatic-storage-operation-failed', removed=1, complete=False))

    def test_missing_row_repair_only_clears_image_binding(self):
        inventory = self.inventory([2])
        row = inventory['imageRows'][0]
        row['reason'] = 'sample-failed'
        with patch.object(sampling, 'replace_automatic_journal') as replace:
            sampling._repair_retired_rows(inventory, {row['samplePath']}, namespace='automatic-v2')
        saved = json.loads(replace.call_args.args[1])
        self.assertEqual(saved['reason'], row['reason'])
        self.assertEqual(saved['missingReason'], 'automatic-image-retired')
        for field in ('samplePath', 'sampleIdentity', 'sampleSha256', 'sampleBytes'):
            self.assertNotIn(field, saved)
        self.assertIn('sampleIdentity', row)  # No mutation of inventory evidence.


@unittest.skipUnless(os.name == 'nt', 'requires native Windows handles; no POSIX simulation of native safety')
class NativeWindowsAdversarial(unittest.TestCase):
    def test_swap_after_inventory_same_content_cannot_authorize_retirement(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            image, row = fixture(root)
            journal(root, [row])
            with sampling.automatic_storage_transaction(root, namespace='automatic-v2') as status:
                self.assertEqual(status, 'ready')
                scanned = sampling.automatic_storage_inventory(root, namespace='automatic-v2')
                replacement = root / 'replacement.png'
                replacement.write_bytes(image.read_bytes())
                os.replace(replacement, image)
                with self.assertRaises(OSError):
                    sampling.retire_automatic_image(root, scanned['imageRows'][0])
                with self.assertRaises(OSError):
                    sampling.read_automatic_image(root, scanned['imageRows'][0])
            self.assertTrue(image.exists())

    def test_retirement_handle_denies_external_writer_and_delete(self):
        import subprocess
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            image, row = fixture(root)
            original = sampling._mark_delete_image
            def assert_exclusive(fd):
                program = """import os, sys
for action in (lambda: open(sys.argv[1], 'wb'), lambda: os.unlink(sys.argv[1])):
    try: action()
    except OSError: continue
    raise SystemExit(9)
"""
                child = subprocess.run([sys.executable, '-c', program, str(image)], capture_output=True)
                self.assertEqual(child.returncode, 0, child.stderr)
                original(fd)
            with sampling.automatic_storage_transaction(root, namespace='automatic-v2') as status:
                self.assertEqual(status, 'ready')
                with patch.object(sampling, '_mark_delete_image', side_effect=assert_exclusive):
                    self.assertEqual(sampling.retire_automatic_image(root, row), 'retired')
            self.assertFalse(image.exists())

    def test_capacity_rotation_and_expiry_leave_legacy_untouched(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            rows = [fixture(root, i)[1] for i in range(6)]
            journal(root, rows)
            old = root / 'failures/automatic/sentinel.png'
            old.parent.mkdir()
            old.write_bytes(b'legacy')
            with sampling.automatic_storage_transaction(root, namespace='automatic-v2') as status:
                self.assertEqual(status, 'ready')
                result = sampling.ensure_automatic_capacity(root, 1)
                self.assertEqual(result, dict(status='ready', removed=1, complete=True))
                self.assertFalse((root / rows[0]['samplePath']).exists())
                result = sampling._cleanup_automatic_failure_samples_locked(root, namespace='automatic-v2')
                self.assertEqual(result, dict(status='ready', removed=5, complete=True))
            self.assertEqual(old.read_bytes(), b'legacy')

    def test_crash_after_retirement_before_journal_repair_recovers_missing(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            image, row = fixture(root)
            journal(root, [row])
            with sampling.automatic_storage_transaction(root, namespace='automatic-v2') as status:
                self.assertEqual(status, 'ready')
                sampling.retire_automatic_image(root, row)
            self.assertFalse(image.exists())
            report = sampling.cleanup_automatic_failure_samples(root, namespace='automatic-v2')
            self.assertTrue(report['complete'])
            saved = json.loads((root / 'failures/automatic-v2/automatic-failures.jsonl').read_bytes())
            self.assertNotIn('sampleIdentity', saved)
            self.assertNotIn('samplePath', saved)


class PureRecoveryDecisions(unittest.TestCase):
    """Mocked journal/open calls test policy only, not platform handle safety."""
    def test_missing_intent_cancels_but_present_object_remains_pending(self):
        from types import SimpleNamespace
        intent = dict(event='automatic-failure-image-intent',
                      imageTransactionSchema='automatic-image-intent-v1',
                      samplePath='failures/automatic-v2/auto-' + 'b' * 32 + '.png',
                      sampleSha256='a' * 64, sampleBytes=3)
        payload = json.dumps(intent).encode()
        directory = Path('/fixture/failures/automatic-v2')
        for missing in (True, False):
            with self.subTest(missing=missing), \
                    patch.object(sampling, '_require_transaction', return_value=directory), \
                    patch.object(sampling, '_automatic_state', SimpleNamespace(journal=(directory, 123, payload))), \
                    patch('diagnostic_storage._read_fd', return_value=payload), \
                    patch.object(sampling, '_open_image_read', side_effect=FileNotFoundError() if missing else None, return_value=456), \
                    patch.object(sampling.os, 'close'), \
                    patch.object(sampling, 'replace_automatic_journal') as replace:
                status = sampling._recover_automatic_image_intent_locked('/fixture', namespace='automatic-v2')
                if missing:
                    self.assertEqual(status, 'ready')
                    replace.assert_called_once_with(directory, b'', namespace='automatic-v2')
                else:
                    self.assertEqual(status, 'automatic-image-recovery-required')
                    replace.assert_not_called()

    def test_read_errors_never_embed_path_or_os_text(self):
        row = dict(samplePath='failures/automatic-v2/auto-' + 'b' * 32 + '.png',
                   sampleSha256='a' * 64, sampleBytes=3, sampleIdentity=[1, 2])
        with patch.object(sampling, '_require_transaction'), \
                patch.object(sampling, '_open_image_read', side_effect=OSError('secret/path/private user')):
            with self.assertRaisesRegex(OSError, '^automatic-image-read-unavailable$'):
                sampling.read_automatic_image('/fixture', row)

    def test_legacy_intent_is_never_rewritten(self):
        with patch.object(sampling, 'replace_automatic_journal') as replace:
            self.assertEqual(sampling._recover_automatic_image_intent_locked('/fixture'),
                             'automatic-legacy-read-only')
            replace.assert_not_called()
