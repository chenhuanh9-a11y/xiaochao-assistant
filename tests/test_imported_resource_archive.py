"""Real ZIP and filesystem coverage for inert resource archive storage."""
import hashlib
import json
import os
from pathlib import Path
import struct
import subprocess
import sys
import tempfile
import unittest
import zipfile
from unittest.mock import patch

import imported_resource_archive as archives
import imported_resource_packages as packages
import resource_transaction


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True,
                      separators=(',', ':')).encode('utf-8')


class ImportedResourceArchiveTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.resource_root = self.root / 'resources'
        self.old_install = self.root / 'old-install'
        (self.old_install / '_internal').mkdir(parents=True)
        (self.old_install / '_internal' / 'sentinel.dat').write_bytes(b'legacy-install-data')
        self.archive_path = self.root / 'resource.zip'
        self.payloads = {
            ('base', 'index.json'): b'{"maps":[]}',
            ('display', 'poster.webp'): b'actual-display-bytes',
            ('controls', 'profile.json'): b'{"templates":[]}',
        }
        self.manifest = {
            'schema': 'xingtu-resource-package',
            'formatVersion': 1,
            'packageId': 'demo',
            'resourceVersion': 'v1',
            'clientCompatibility': {
                'resourceProtocol': 1,
                'requiredCapabilities': [],
            },
            'components': [
                {'componentId': 'base', 'type': 'recognition-base',
                 'schemaVersion': 1, 'contexts': [{'season': 's0', 'mode': 'nightmare'}],
                 'entrypoint': 'index.json', 'dependencies': {}},
                {'componentId': 'display', 'type': 'display',
                 'schemaVersion': 1, 'contexts': [{'season': 's0', 'mode': 'nightmare'}],
                 'entrypoint': 'poster.webp',
                 'dependencies': {'base': {'componentId': 'base'}}},
                {'componentId': 'controls', 'type': 'controls-profile',
                 'schemaVersion': 1, 'contexts': [{'season': 's0', 'mode': 'nightmare'}],
                 'entrypoint': 'profile.json', 'dependencies': {}},
            ],
            'files': [
                {'componentId': component, 'path': path,
                 'bytes': len(raw), 'sha256': hashlib.sha256(raw).hexdigest()}
                for (component, path), raw in self.payloads.items()
            ],
        }
        self.write_archive()

    def write_archive(self, *, extra=None, compression=zipfile.ZIP_STORED):
        with zipfile.ZipFile(self.archive_path, 'w', compression=compression) as archive:
            archive.writestr('manifest.json', canonical(self.manifest))
            for (component, relative), raw in self.payloads.items():
                archive.writestr(f'data/{component}/{relative}', raw)
            for name, raw in (extra or {}).items():
                archive.writestr(name, raw)
        self.source_bytes = self.archive_path.read_bytes()

    def package_path(self, version='v1'):
        return self.resource_root / 'packages' / 'demo' / version

    def test_inspect_multi_component_package_is_read_only_and_returns_full_refs(self):
        summary = archives.inspect_archive(self.archive_path)

        self.assertEqual(summary.status, 'inspected')
        self.assertEqual(summary.package_id, 'demo')
        self.assertEqual(summary.resource_version, 'v1')
        self.assertEqual(summary.archive_sha256, hashlib.sha256(self.source_bytes).hexdigest())
        self.assertEqual(len(summary.component_refs), 3)
        self.assertEqual({ref.component_id for ref in summary.component_refs},
                         {'base', 'display', 'controls'})
        self.assertTrue(all(ref.package_id == 'demo' and ref.resource_version == 'v1'
                            and ref.manifest_sha256 == summary.manifest_sha256
                            for ref in summary.component_refs))
        self.assertFalse(self.resource_root.exists())
        self.assertEqual(self.archive_path.read_bytes(), self.source_bytes)

    def test_inspect_hashes_every_payload_without_creating_a_temporary_tree(self):
        observed = {}
        read_member = archives._read_member

        def recording_read(archive, info, limit, cancelled, sink=None, expected=None):
            digest = read_member(archive, info, limit, cancelled, sink=sink, expected=expected)
            if info.filename.startswith('data/'):
                self.assertIsNotNone(sink)
                observed[info.filename] = digest.hex()
            return digest

        with patch.object(tempfile, 'TemporaryDirectory',
                          side_effect=AssertionError('inspect must stay stream-only')):
            with patch.object(archives, '_read_member', side_effect=recording_read):
                archives.inspect_archive(self.archive_path)
        expected = {
            f'data/{component}/{relative}': hashlib.sha256(raw).hexdigest()
            for (component, relative), raw in self.payloads.items()
        }
        self.assertEqual(observed, expected)

    def test_save_publishes_complete_inert_tree_and_identical_import_is_idempotent(self):
        with patch.object(archives.os, 'fsync', wraps=os.fsync) as sync_file:
            first = archives.save_archive(self.archive_path, self.resource_root)
        self.assertGreaterEqual(sync_file.call_count, len(self.payloads) + 1)
        self.assertEqual(first.status, 'saved')
        self.assertEqual(set(path.relative_to(self.package_path()).as_posix()
                             for path in self.package_path().rglob('*') if path.is_file()),
                         {'manifest.json', 'data/base/index.json',
                          'data/display/poster.webp', 'data/controls/profile.json'})
        for (component, relative), expected in self.payloads.items():
            self.assertEqual((self.package_path() / 'data' / component / relative).read_bytes(), expected)
        packages.PackageView(self.resource_root, first.component_refs[0]).validate_inventory()
        self.assertFalse((self.resource_root / 'catalog.json').exists())
        self.assertEqual((self.old_install / '_internal' / 'sentinel.dat').read_bytes(),
                         b'legacy-install-data')

        second = archives.save_archive(self.archive_path, self.resource_root)
        self.assertEqual(second.status, 'already-saved')
        self.assertEqual(second.archive_sha256, first.archive_sha256)
        self.assertEqual(list((self.resource_root / 'staging').iterdir()), [])

    def test_same_version_content_conflict_is_rejected_and_existing_tree_preserved(self):
        archives.save_archive(self.archive_path, self.resource_root)
        original = (self.package_path() / 'data/base/index.json').read_bytes()
        self.payloads[('base', 'index.json')] = b'{"maps":["different"]}'
        self.manifest['files'][0]['bytes'] = len(self.payloads[('base', 'index.json')])
        self.manifest['files'][0]['sha256'] = hashlib.sha256(
            self.payloads[('base', 'index.json')]).hexdigest()
        self.write_archive()

        with self.assertRaisesRegex(ValueError, 'version-conflict'):
            archives.save_archive(self.archive_path, self.resource_root)
        self.assertEqual((self.package_path() / 'data/base/index.json').read_bytes(), original)
        self.assertFalse((self.resource_root / 'catalog.json').exists())

    def test_existing_version_requires_full_inventory_validation(self):
        archives.save_archive(self.archive_path, self.resource_root)
        extra = self.package_path() / 'data/base/unlisted.bin'
        extra.write_bytes(b'unknown')

        with self.assertRaisesRegex(ValueError, 'version-conflict'):
            archives.save_archive(self.archive_path, self.resource_root)
        self.assertEqual(extra.read_bytes(), b'unknown')

    def test_existing_version_retry_still_checks_archive_payload_crc(self):
        archives.save_archive(self.archive_path, self.resource_root)
        original = (self.package_path() / 'data/base/index.json').read_bytes()
        with zipfile.ZipFile(self.archive_path) as archive:
            info = archive.getinfo('data/base/index.json')
        with self.archive_path.open('r+b') as handle:
            handle.seek(info.header_offset + 26)
            name_len, extra_len = struct.unpack('<HH', handle.read(4))
            data_offset = info.header_offset + 30 + name_len + extra_len
            handle.seek(data_offset)
            byte = handle.read(1)
            handle.seek(data_offset)
            handle.write(bytes([byte[0] ^ 1]))

        with self.assertRaisesRegex(ValueError, 'corrupt-entry'):
            archives.save_archive(self.archive_path, self.resource_root)
        self.assertEqual((self.package_path() / 'data/base/index.json').read_bytes(), original)

    def test_legacy_map_package_manifest_has_stable_error_and_source_is_untouched(self):
        with zipfile.ZipFile(self.archive_path, 'w') as archive:
            archive.writestr('map-package.json', b'{"schemaVersion":1,"revision":1}')
            archive.writestr('data/old/', b'')
            archive.writestr('data/old.json', b'old')
        source = self.archive_path.read_bytes()

        with self.assertRaisesRegex(ValueError, 'legacy-format-unsupported') as caught:
            archives.inspect_archive(self.archive_path)
        self.assertEqual(caught.exception.code, 'legacy-format-unsupported')
        with self.assertRaisesRegex(ValueError, 'legacy-format-unsupported'):
            archives.save_archive(self.archive_path, self.resource_root)
        self.assertEqual(self.archive_path.read_bytes(), source)
        self.assertFalse((self.resource_root / 'packages').exists())

    def test_unknown_file_and_directory_entry_are_rejected(self):
        self.write_archive(extra={'data/base/extra.bin': b'not declared'})
        with self.assertRaisesRegex(ValueError, 'archive-inventory-mismatch'):
            archives.inspect_archive(self.archive_path)

        with zipfile.ZipFile(self.archive_path, 'w') as archive:
            archive.writestr('manifest.json', canonical(self.manifest))
            for (component, relative), raw in self.payloads.items():
                archive.writestr(f'data/{component}/{relative}', raw)
            archive.writestr('data/base/', '')
        with self.assertRaisesRegex(ValueError, 'unsupported-entry'):
            archives.inspect_archive(self.archive_path)

    def test_duplicate_casefolded_zip_path_is_rejected(self):
        with zipfile.ZipFile(self.archive_path, 'w') as archive:
            archive.writestr('manifest.json', canonical(self.manifest))
            for (component, relative), raw in self.payloads.items():
                archive.writestr(f'data/{component}/{relative}', raw)
            archive.writestr('DATA/base/index.json', b'other')
        with self.assertRaisesRegex(ValueError, 'duplicate-archive-path'):
            archives.inspect_archive(self.archive_path)

    def test_traversal_unsupported_compression_and_container_limits_are_rejected(self):
        self.write_archive(extra={'data/base/../escape.bin': b'unsafe'})
        with self.assertRaisesRegex(ValueError, 'unsafe-path'):
            archives.inspect_archive(self.archive_path)

        try:
            with zipfile.ZipFile(self.archive_path, 'w', compression=zipfile.ZIP_BZIP2) as archive:
                archive.writestr('manifest.json', canonical(self.manifest))
                for (component, relative), raw in self.payloads.items():
                    archive.writestr(f'data/{component}/{relative}', raw)
        except (NotImplementedError, RuntimeError):
            self.skipTest('BZIP2 ZIP entries are unavailable in this Python build')
        with self.assertRaisesRegex(ValueError, 'unsupported-entry'):
            archives.inspect_archive(self.archive_path)

        self.write_archive()
        with patch.object(archives, 'MAX_ARCHIVE_BYTES', len(self.source_bytes) - 1):
            with self.assertRaisesRegex(ValueError, 'archive-size-limit'):
                archives.inspect_archive(self.archive_path)
        with patch.object(archives, 'MANIFEST_LIMIT', 8):
            with self.assertRaisesRegex(ValueError, 'manifest-too-large'):
                archives.inspect_archive(self.archive_path)

    def test_bad_crc_is_rejected_while_reading_real_zip_payload(self):
        info_offset = None
        with zipfile.ZipFile(self.archive_path) as archive:
            info_offset = archive.getinfo('data/base/index.json').header_offset
        with self.archive_path.open('r+b') as handle:
            handle.seek(info_offset + 26)
            name_len, extra_len = struct.unpack('<HH', handle.read(4))
            data_offset = info_offset + 30 + name_len + extra_len
            handle.seek(data_offset)
            original = handle.read(1)
            handle.seek(data_offset)
            handle.write(bytes([original[0] ^ 0x01]))

        with self.assertRaisesRegex(ValueError, 'corrupt-entry'):
            archives.inspect_archive(self.archive_path)

        uncertain_root = self.root / 'uncertain-resources'
        with self.assertRaisesRegex(ValueError, 'corrupt-entry'):
            archives.save_archive(self.archive_path, uncertain_root)
        stages = list((uncertain_root / 'staging').iterdir())
        self.assertEqual(len(stages), 1)
        self.assertTrue((stages[0] / 'packages/demo/v1/data/base/index.json').exists())
        self.assertFalse((uncertain_root / 'packages/demo/v1').exists())

    def test_cancelled_inspect_never_creates_resource_root_and_cancelled_save_cleans_own_stage(self):
        with self.assertRaisesRegex(ValueError, 'cancelled'):
            archives.inspect_archive(self.archive_path, cancelled=lambda: True)
        self.assertFalse(self.resource_root.exists())

        calls = 0

        def cancel_after_stage_started():
            nonlocal calls
            calls += 1
            return calls >= 10

        with self.assertRaisesRegex(ValueError, 'cancelled'):
            archives.save_archive(self.archive_path, self.resource_root,
                                  cancelled=cancel_after_stage_started)
        self.assertFalse(self.package_path().exists())
        self.assertEqual(list((self.resource_root / 'staging').iterdir()), [])
        self.assertFalse((self.resource_root / 'catalog.json').exists())

    def test_selected_archive_digest_and_mid_inspect_path_change_are_rejected(self):
        with self.assertRaisesRegex(ValueError, 'selected-archive-changed'):
            archives.inspect_archive(self.archive_path, expected_archive_sha256='0' * 64)

        original_read = archives._read_member

        def touch_after_payload_read(archive, info, limit, cancelled, sink=None, expected=None):
            result = original_read(archive, info, limit, cancelled, sink=sink, expected=expected)
            if info.filename == 'data/base/index.json':
                current = self.archive_path.stat()
                os.utime(self.archive_path, ns=(current.st_atime_ns,
                                                current.st_mtime_ns + 5_000_000_000))
            return result

        with patch.object(archives, '_read_member', side_effect=touch_after_payload_read):
            with self.assertRaisesRegex(ValueError, 'changed'):
                archives.inspect_archive(self.archive_path)

    def test_source_symlink_is_rejected(self):
        linked = self.root / 'linked.zip'
        try:
            linked.symlink_to(self.archive_path)
        except (OSError, NotImplementedError):
            self.skipTest('symbolic links are unavailable in this test environment')
        with self.assertRaisesRegex(ValueError, 'linked-target'):
            archives.inspect_archive(linked)

    def test_resource_root_creates_missing_parent_chain_but_rejects_file_or_link(self):
        deep_root = self.root / 'new-parent' / 'another-parent' / 'resources'
        result = archives.save_archive(self.archive_path, deep_root)
        self.assertEqual(result.status, 'saved')
        self.assertTrue((deep_root / 'packages/demo/v1/manifest.json').is_file())

        file_parent = self.root / 'file-parent'
        file_parent.write_bytes(b'preserve')
        with self.assertRaisesRegex(ValueError, 'resource-root-not-directory'):
            archives.save_archive(self.archive_path, file_parent / 'nested' / 'resources')
        self.assertEqual(file_parent.read_bytes(), b'preserve')
        self.assertFalse((file_parent / 'nested').exists())

        outside = self.root / 'outside-parent'
        outside.mkdir()
        linked_parent = self.root / 'linked-parent'
        try:
            linked_parent.symlink_to(outside, target_is_directory=True)
        except (OSError, NotImplementedError):
            self.skipTest('symbolic links are unavailable in this test environment')
        with self.assertRaisesRegex(ValueError, 'linked-target'):
            archives.save_archive(self.archive_path, linked_parent / 'nested' / 'resources')
        self.assertFalse((outside / 'nested').exists())

    def test_cancelled_save_preserves_replaced_stage_and_foreign_data(self):
        state = {'cancelled': False, 'foreign': None, 'displaced': None}
        validate = archives._validate_staged

        def replace_after_validation(root, refs):
            validate(root, refs)
            if root.parent == self.resource_root / 'staging':
                state['displaced'] = root.with_name(root.name + '-displaced')
                root.rename(state['displaced'])
                root.mkdir()
                state['foreign'] = root / 'foreign-user-data.txt'
                state['foreign'].write_bytes(b'foreign-owned')
                state['cancelled'] = True

        with patch.object(archives, '_validate_staged', side_effect=replace_after_validation):
            with self.assertRaisesRegex(ValueError, 'cancelled'):
                archives.save_archive(self.archive_path, self.resource_root,
                                      cancelled=lambda: state['cancelled'])
        self.assertEqual(state['foreign'].read_bytes(), b'foreign-owned')
        self.assertTrue((state['displaced'] / 'packages/demo/v1/manifest.json').is_file())
        self.assertFalse(self.package_path().exists())

    def test_replaced_stage_after_validation_cannot_publish_foreign_candidate(self):
        state = {'foreign': None, 'displaced': None}
        validate = archives._validate_staged

        def replace_after_validation(root, refs):
            validate(root, refs)
            if root.parent == self.resource_root / 'staging':
                state['displaced'] = root.with_name(root.name + '-displaced')
                root.rename(state['displaced'])
                candidate = root / 'packages' / 'demo' / 'v1'
                candidate.mkdir(parents=True)
                state['foreign'] = candidate / 'foreign-user-data.txt'
                state['foreign'].write_bytes(b'foreign-owned')

        with patch.object(archives, '_validate_staged', side_effect=replace_after_validation):
            with self.assertRaisesRegex(ValueError, 'resource-directory-source-changed'):
                archives.save_archive(self.archive_path, self.resource_root)
        self.assertEqual(state['foreign'].read_bytes(), b'foreign-owned')
        self.assertTrue((state['displaced'] / 'packages/demo/v1/manifest.json').is_file())
        self.assertFalse(self.package_path().exists())

    def test_unknown_file_added_after_validation_fails_before_directory_publication(self):
        state = {'foreign': None}
        validate = archives._validate_staged

        def add_after_validation(root, refs):
            validate(root, refs)
            if root.parent == self.resource_root / 'staging':
                state['foreign'] = root / 'packages' / 'demo' / 'v1' / 'foreign-user-data.txt'
                state['foreign'].write_bytes(b'foreign-owned')

        with patch.object(archives, '_validate_staged', side_effect=add_after_validation):
            with self.assertRaisesRegex(ValueError, 'object-changed'):
                archives.save_archive(self.archive_path, self.resource_root)
        self.assertEqual(state['foreign'].read_bytes(), b'foreign-owned')
        self.assertFalse(self.package_path().exists())

    def test_postpublication_inventory_failure_preserves_target_and_foreign_file(self):
        move = archives.resource_transaction.rename_owned_directory_no_replace
        foreign = self.package_path() / 'data' / 'base' / 'foreign-user-data.txt'

        def move_then_add_foreign(source, target, expected_identity, *, before_move):
            result = move(source, target, expected_identity, before_move=before_move)
            foreign.write_bytes(b'foreign-owned')
            return result

        with patch.object(archives.resource_transaction,
                          'rename_owned_directory_no_replace', side_effect=move_then_add_foreign):
            with self.assertRaisesRegex(ValueError, 'undeclared-file'):
                archives.save_archive(self.archive_path, self.resource_root)
        self.assertEqual(foreign.read_bytes(), b'foreign-owned')
        self.assertTrue((self.package_path() / 'manifest.json').is_file())
        self.assertFalse((self.resource_root / 'catalog.json').exists())

    def test_cancelled_save_preserves_unknown_file_added_to_owned_stage(self):
        state = {'cancelled': False, 'foreign': None}
        validate = archives._validate_staged

        def add_after_validation(root, refs):
            validate(root, refs)
            if root.parent == self.resource_root / 'staging':
                state['foreign'] = root / 'foreign-user-data.txt'
                state['foreign'].write_bytes(b'new unowned object')
                state['cancelled'] = True

        with patch.object(archives, '_validate_staged', side_effect=add_after_validation):
            with self.assertRaisesRegex(ValueError, 'cancelled'):
                archives.save_archive(self.archive_path, self.resource_root,
                                      cancelled=lambda: state['cancelled'])
        self.assertEqual(state['foreign'].read_bytes(), b'new unowned object')
        self.assertTrue(state['foreign'].parent.exists())
        self.assertFalse(self.package_path().exists())

    def test_existing_version_symlink_is_preserved_and_never_followed(self):
        self.resource_root.mkdir()
        package_parent = self.resource_root / 'packages' / 'demo'
        package_parent.mkdir(parents=True)
        outside = self.root / 'outside-version'
        outside.mkdir()
        linked_version = package_parent / 'v1'
        try:
            linked_version.symlink_to(outside, target_is_directory=True)
        except (OSError, NotImplementedError):
            self.skipTest('symbolic links are unavailable in this test environment')

        with self.assertRaisesRegex(ValueError, 'linked-target'):
            archives.save_archive(self.archive_path, self.resource_root)
        self.assertEqual(list(outside.iterdir()), [])
        self.assertTrue(linked_version.is_symlink())

    def test_save_observes_resource_root_cross_process_lock(self):
        self.resource_root.mkdir()
        child_code = (
            'from pathlib import Path; import sys,time; '
            'from resource_transaction import resource_write_lock; '
            'lock=resource_write_lock(Path(sys.argv[1]), wait=True); '
            'lock.__enter__(); print("locked", flush=True); time.sleep(1.2); lock.__exit__(None,None,None)'
        )
        env = os.environ.copy()
        source_root = Path(__file__).resolve().parents[1]
        app_root = source_root / 'app'
        env['PYTHONPATH'] = os.pathsep.join(
            [str(app_root), str(source_root / 'tools'), str(source_root / 'tests'),
             str(source_root / 'experiments' / 'qt_quick')])
        process = subprocess.Popen(
            [sys.executable, '-c', child_code, str(self.resource_root)],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, env=env)
        self.addCleanup(lambda: process.poll() is None and process.kill())
        self.assertEqual(process.stdout.readline().strip(), 'locked')
        with self.assertRaisesRegex(ValueError, 'resource-busy'):
            archives.save_archive(self.archive_path, self.resource_root)
        process.wait(timeout=5)
        process.stdout.close()
        process.stderr.close()
        self.assertFalse(self.package_path().exists())
        self.assertFalse((self.resource_root / 'catalog.json').exists())


if __name__ == '__main__':
    unittest.main()
