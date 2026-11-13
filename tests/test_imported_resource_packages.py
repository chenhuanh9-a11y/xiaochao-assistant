"""Synthetic package bytes exercise container and read integrity only."""
import hashlib
import json
import os
from pathlib import Path
import tempfile
import unittest
import copy
from unittest.mock import patch

import imported_resource_packages as packages
import resource_snapshot_io
import star_map_import as imp


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True,
                      separators=(',', ':')).encode('utf-8')


class ImportedResourcePackageTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.resource_root = Path(self.temp.name) / 'resources'
        self.package_root = self.resource_root / 'packages' / 'demo' / 'v1'
        self.data_root = self.package_root / 'data'
        self.base_root = self.data_root / 'base'
        self.display_root = self.data_root / 'display'
        self.base_root.mkdir(parents=True)
        self.display_root.mkdir(parents=True)
        self.base_bytes = b'{"maps":[]}'
        self.display_bytes = b'actual display bytes'
        (self.base_root / 'index.json').write_bytes(self.base_bytes)
        (self.display_root / 'poster.webp').write_bytes(self.display_bytes)
        self.manifest = {
            'schema': 'xingtu-resource-package',
            'formatVersion': 1,
            'packageId': 'demo',
            'resourceVersion': 'v1',
            'clientCompatibility': {'resourceProtocol': 1, 'requiredCapabilities': []},
            'components': [
                {'componentId': 'base', 'type': 'recognition-base',
                 'schemaVersion': 1, 'contexts': [{'season': 's0', 'mode': 'nightmare'}],
                 'entrypoint': 'index.json', 'dependencies': {}},
                {'componentId': 'display', 'type': 'display',
                 'schemaVersion': 1, 'contexts': [{'season': 's0', 'mode': 'nightmare'}],
                 'entrypoint': 'poster.webp', 'dependencies': {'base': {'componentId': 'base'}}},
            ],
            'files': [
                {'componentId': 'base', 'path': 'index.json',
                 'bytes': len(self.base_bytes), 'sha256': hashlib.sha256(self.base_bytes).hexdigest()},
                {'componentId': 'display', 'path': 'poster.webp',
                 'bytes': len(self.display_bytes), 'sha256': hashlib.sha256(self.display_bytes).hexdigest()},
            ],
            'provenance': {'source': 'synthetic-test'},
            'displayName': 'Synthetic package',
        }
        self.original_manifest = copy.deepcopy(self.manifest)
        self.write_manifest()

    def write_manifest(self):
        self.manifest_bytes = canonical(self.manifest)
        (self.package_root / 'manifest.json').write_bytes(self.manifest_bytes)
        self.manifest_sha256 = hashlib.sha256(self.manifest_bytes).hexdigest()
        self.base_ref = packages.ComponentRef('demo', 'v1', 'base', self.manifest_sha256)
        self.display_ref = packages.ComponentRef('demo', 'v1', 'display', self.manifest_sha256)

    def view(self, ref=None):
        return packages.PackageView(self.resource_root, ref or self.base_ref)

    def test_valid_package_and_same_package_components_use_real_roots(self):
        base = self.view()
        display = self.view(self.display_ref)
        self.assertEqual(base.root, self.base_root)
        self.assertEqual(display.root, self.display_root)
        self.assertEqual(base.read('index.json'), self.base_bytes)
        self.assertEqual(display.read('poster.webp'), self.display_bytes)
        self.assertEqual(base.component['type'], 'recognition-base')
        self.assertEqual(base.inventory['index.json']['bytes'], len(self.base_bytes))
        self.assertEqual(base.file_ref('index.json'), packages.FileRef(self.base_ref, 'index.json'))
        self.assertEqual(base.manifest['formatVersion'], 1)
        self.assertEqual(display.dependencies['base'], self.base_ref)

    def test_repeated_consumers_reuse_decoding_but_still_read_current_manifest(self):
        view = self.view()
        original_json = packages.files._json
        manifest_path = self.package_root / 'manifest.json'
        with patch.object(packages.files, '_json', wraps=original_json) as decode:
            self.assertEqual(view.root, self.base_root)
            exposed = view.manifest
            exposed['components'][0]['contexts'].clear()
            view.component['dependencies']['foreign'] = {'componentId': 'missing'}
            self.assertEqual(view.read('index.json'), self.base_bytes)
            self.assertEqual(view.component['contexts'], [{'season': 's0', 'mode': 'nightmare'}])
            self.assertEqual(view.dependencies, {})
            self.assertEqual(decode.call_count, 1)
            # Decoded declarations are reusable, not a cached filesystem verdict.
            manifest_path.write_bytes(self.manifest_bytes.replace(b'"v1"', b'"v2"'))
            with self.assertRaisesRegex(ValueError, 'manifest-changed'):
                view.root

    def test_reused_decoding_rejects_same_byte_manifest_replacement_and_stale_selection(self):
        current = [True]
        def guard():
            if not current[0]:
                raise ValueError('selection-superseded')
        view = packages.PackageView(self.resource_root, self.base_ref, assert_current=guard)
        self.assertEqual(view.read('index.json'), self.base_bytes)
        current[0] = False
        with self.assertRaisesRegex(ValueError, 'selection-superseded'):
            view.root
        current[0] = True
        manifest_path = self.package_root / 'manifest.json'
        original_stamp = manifest_path.stat()
        retained = self.package_root / 'retained-manifest.json'
        manifest_path.rename(retained)
        manifest_path.write_bytes(self.manifest_bytes)
        os.utime(manifest_path, ns=(original_stamp.st_atime_ns, original_stamp.st_mtime_ns))
        with self.assertRaisesRegex(ValueError, 'manifest-replaced'):
            view.root

    def test_reused_decoding_is_bound_to_the_complete_component_reference(self):
        view = self.view()
        self.assertEqual(view.root, self.base_root)
        view.component_ref = self.display_ref
        self.assertEqual(view.root, self.display_root)
        self.assertEqual(view.component['type'], 'display')
        (self.data_root / 'unregistered').mkdir()
        view.component_ref = packages.ComponentRef('demo', 'v1', 'unregistered', self.manifest_sha256)
        with self.assertRaisesRegex(ValueError, 'component-identity-mismatch'):
            view.root

    def test_undeclared_other_component_and_installation_fallback_are_refused(self):
        installation = Path(self.temp.name) / 'install'
        fallback = installation / '_internal' / 'secret.json'
        fallback.parent.mkdir(parents=True)
        fallback.write_text('secret', encoding='utf-8')
        view = self.view()
        for path in ('poster.webp', '_internal/secret.json'):
            with self.subTest(path=path), self.assertRaises(ValueError):
                view.path(path)
        with self.assertRaises(ValueError):
            view.read('not-declared.json')

    def test_cross_package_dependencies_are_resolved_as_refs_only(self):
        dependency = packages.ComponentRef('separate', 'v2', 'base2', '2' * 64)
        self.manifest['components'][1]['dependencies'] = {
            'base': {'packageId': dependency.package_id,
                     'resourceVersion': dependency.resource_version,
                     'componentId': dependency.component_id,
                     'manifestSha256': dependency.manifest_sha256},
        }
        self.write_manifest()
        view = self.view(self.display_ref)
        self.assertEqual(view.dependencies['base'], dependency)
        self.assertFalse((self.resource_root / 'packages' / 'separate').exists())

    def test_unknown_component_directory_is_rejected_by_full_inventory_validation(self):
        unknown = self.data_root / 'unregistered'
        unknown.mkdir()
        (unknown / 'payload.bin').write_bytes(b'undeclared')
        with self.assertRaisesRegex(ValueError, 'unknown-component-directory'):
            self.view().validate_inventory()

    def test_undeclared_payload_file_is_rejected_by_full_inventory_validation(self):
        (self.base_root / 'extra.bin').write_bytes(b'extra')
        with self.assertRaisesRegex(ValueError, 'undeclared-file'):
            self.view().validate_inventory()

    def test_manifest_digest_identity_and_component_identity_are_bound(self):
        wrong_hash = packages.ComponentRef('demo', 'v1', 'base', '0' * 64)
        with self.assertRaisesRegex(ValueError, 'manifest-changed'):
            packages.PackageView(self.resource_root, wrong_hash).root

        self.manifest['packageId'] = 'other'
        self.write_manifest()
        with self.assertRaisesRegex(ValueError, 'package-identity-mismatch'):
            self.view().root

    def test_version_component_entrypoint_and_context_contracts_are_checked(self):
        cases = [
            ('version', lambda: self.manifest.update(resourceVersion='v2'), 'package-identity-mismatch'),
            ('component', lambda: self.manifest['components'][0].update(componentId='unknown'), 'component-identity-mismatch'),
            ('entrypoint', lambda: self.manifest['components'][0].update(entrypoint='missing.json'), 'entrypoint-not-in-inventory'),
            ('context', lambda: self.manifest['components'][0].update(contexts=[{'season': 's0', 'mode': 'difficulty2'}]), 'invalid-component-contexts'),
        ]
        for name, mutate, expected in cases:
            with self.subTest(case=name):
                self.manifest = copy.deepcopy(self.original_manifest)
                mutate()
                self.write_manifest()
                with self.assertRaisesRegex(ValueError, expected):
                    self.view().root

    def test_client_compatibility_is_canonical_and_preserves_unknown_capabilities(self):
        self.manifest['clientCompatibility']['requiredCapabilities'] = ['future-decoder']
        self.write_manifest()
        self.assertEqual(self.view().manifest['clientCompatibility']['requiredCapabilities'],
                         ['future-decoder'])
        invalid_values = [
            {'resourceProtocol': 2, 'requiredCapabilities': []},
            {'resourceProtocol': 1, 'requiredCapabilities': ['duplicate', 'duplicate']},
            {'resourceProtocol': 1, 'requiredCapabilities': [3]},
            {'resourceProtocol': 1, 'requiredCapabilities': [], 'extra': True},
        ]
        for value in invalid_values:
            with self.subTest(value=value):
                self.manifest = copy.deepcopy(self.original_manifest)
                self.manifest['clientCompatibility'] = value
                self.write_manifest()
                with self.assertRaisesRegex(ValueError, 'invalid-client-compatibility'):
                    self.view().manifest

    def test_display_name_reuses_plain_text_limit(self):
        for value in ('<b>title</b>', 'line\nfeed', 'x' * 121):
            with self.subTest(value=value[:20]):
                self.manifest = copy.deepcopy(self.original_manifest)
                self.manifest['displayName'] = value
                self.write_manifest()
                with self.assertRaisesRegex(ValueError, 'invalid-display-name'):
                    self.view().manifest

    def test_missing_corrupt_replaced_and_manifest_changed_files_are_refused(self):
        view = self.view()
        (self.base_root / 'index.json').unlink()
        with self.assertRaisesRegex(ValueError, 'file-unavailable'):
            view.read('index.json')
        with self.assertRaisesRegex(ValueError, 'incomplete-file-inventory'):
            view.validate_inventory()

        # A byte replacement with a different digest remains rejected even
        # when the declared path and length still match.
        replacement = self.base_root / 'replacement.json'
        replacement.write_bytes(b'{"maps":!!}')
        replacement.replace(self.base_root / 'index.json')
        with self.assertRaisesRegex(ValueError, 'file-integrity-mismatch'):
            view.read('index.json')
        with self.assertRaisesRegex(ValueError, 'file-integrity-mismatch'):
            view.validate_inventory()

        self.write_manifest()
        view = self.view()
        changed = self.package_root / 'manifest.json'
        changed.write_bytes(self.manifest_bytes + b' ')
        with self.assertRaisesRegex(ValueError, 'manifest-changed'):
            view.read('index.json')

    def test_same_bytes_manifest_replacement_during_payload_read_is_rejected(self):
        view = self.view()
        manifest_path = self.package_root / 'manifest.json'
        payload_path = self.base_root / 'index.json'
        replacement = self.package_root / 'replacement-manifest.json'
        real_read = resource_snapshot_io.read_stable_file

        def replace_after_payload(path, *args, **kwargs):
            result = real_read(path, *args, **kwargs)
            if Path(path) == payload_path:
                original_stamp = manifest_path.stat()
                replacement.write_bytes(self.manifest_bytes)
                os.utime(replacement, ns=(original_stamp.st_atime_ns,
                                          original_stamp.st_mtime_ns))
                os.replace(replacement, manifest_path)
            return result

        with patch.object(packages.resource_snapshot_io, 'read_stable_file', replace_after_payload):
            with self.assertRaisesRegex(ValueError, 'manifest-replaced'):
                view.read('index.json')

    def batch_fixture(self, *, assert_current=None):
        raw = b'second resource'
        (self.base_root / 'second.bin').write_bytes(raw)
        self.manifest['files'].append(dict(componentId='base', path='second.bin',
            bytes=len(raw), sha256=hashlib.sha256(raw).hexdigest()))
        self.write_manifest()
        return packages.PackageView(self.resource_root, self.base_ref,
                                    assert_current=assert_current), raw

    def test_selected_batch_reads_actual_payloads_without_repeating_manifest_io(self):
        view, second = self.batch_fixture()
        manifest_path = self.package_root / 'manifest.json'
        real_read = resource_snapshot_io.read_stable_file
        with patch.object(packages.resource_snapshot_io, 'read_stable_file', wraps=real_read) as read:
            self.assertEqual(view.read_many(('index.json', 'second.bin')),
                             (self.base_bytes, second))
        # The operation still checks the real manifest before and after its
        # selected payloads; it does not re-enter one scope for every file.
        self.assertEqual(sum(Path(call.args[0]) == manifest_path for call in read.call_args_list), 2)
        path = self.base_root / 'second.bin'
        stamp = path.stat()
        path.write_bytes(b'x' * len(second))
        os.utime(path, ns=(stamp.st_atime_ns, stamp.st_mtime_ns))
        with self.assertRaisesRegex(ValueError, 'file-integrity-mismatch'):
            view.read_many(('index.json', 'second.bin'))

    def test_batch_rejects_manifest_replacement_and_selection_change_mid_read(self):
        for action in ('replace-manifest', 'change-selection', 'change-component'):
            with self.subTest(action=action):
                current = [True]
                def guard():
                    if not current[0]:
                        raise ValueError('selection-superseded')
                view, _ = self.batch_fixture(assert_current=guard)
                manifest_path = self.package_root / 'manifest.json'
                real_read = resource_snapshot_io.read_stable_file
                def change_after_first(path, *args, **kwargs):
                    result = real_read(path, *args, **kwargs)
                    if Path(path) == self.base_root / 'index.json':
                        if action == 'replace-manifest':
                            replacement = self.package_root / 'replacement-manifest.json'
                            replacement.write_bytes(manifest_path.read_bytes())
                            replacement.replace(manifest_path)
                        elif action == 'change-component':
                            view.component_ref = self.display_ref
                        else:
                            current[0] = False
                    return result
                with patch.object(packages.resource_snapshot_io, 'read_stable_file', change_after_first):
                    with self.assertRaisesRegex(ValueError,
                            'manifest-replaced|selection-superseded|component-reference-changed'):
                        view.read_many(('index.json', 'second.bin'))
                # Keep this loop's fixture inventory independent.
                self.manifest['files'].pop()

    def test_batch_never_returns_partial_or_cross_component_data(self):
        view, _ = self.batch_fixture()
        for paths in (('index.json', 'missing.bin'), ('index.json', '../display/poster.webp'),
                      ('index.json', 'poster.webp')):
            with self.subTest(paths=paths), self.assertRaises(ValueError):
                view.read_many(paths)

    def test_manifest_rejects_casefold_collisions_unknown_components_and_traversal(self):
        self.manifest['files'].append(dict(self.manifest['files'][0], path='INDEX.json'))
        self.write_manifest()
        with self.assertRaisesRegex(ValueError, 'duplicate-file-path'):
            self.view().root

        self.manifest['files'].pop()
        self.manifest['files'][0]['componentId'] = 'missing-component'
        self.write_manifest()
        with self.assertRaisesRegex(ValueError, 'unknown-file-component'):
            self.view().root

        self.manifest['files'][0]['componentId'] = 'base'
        self.manifest['files'][0]['path'] = '../outside'
        self.write_manifest()
        with self.assertRaises(ValueError):
            self.view().root

    def test_real_link_in_component_tree_is_rejected(self):
        target = self.base_root / 'index.json'
        link = self.base_root / 'alias.json'
        try:
            link.symlink_to(target)
        except (OSError, NotImplementedError) as error:
            self.skipTest('filesystem does not permit creating a real symlink: ' + str(error))
        with self.assertRaises(ValueError):
            self.view().validate_inventory()

    def test_single_file_read_does_not_enumerate_hundreds_of_payload_files(self):
        for index in range(320):
            path = f'blob-{index:03d}.bin'
            data = f'payload-{index}'.encode('ascii')
            (self.base_root / path).write_bytes(data)
            self.manifest['files'].append({
                'componentId': 'base', 'path': path, 'bytes': len(data),
                'sha256': hashlib.sha256(data).hexdigest(),
            })
        self.write_manifest()
        view = self.view()
        real_scandir = os.scandir
        data_scans = []

        def count_data_scans(path):
            candidate = Path(path)
            if candidate.is_relative_to(self.data_root):
                data_scans.append(candidate)
            return real_scandir(path)

        with patch.object(packages.os, 'scandir', count_data_scans):
            self.assertEqual(view.read('blob-319.bin'), b'payload-319')
        self.assertEqual(data_scans, [])
        self.assertEqual(sum(path.is_file() for path in self.data_root.rglob('*')), 322)

    def test_shared_reader_rejects_a_file_modified_during_read(self):
        path = Path(self.temp.name) / 'changing.json'
        path.write_bytes(b'{"v":1}')
        real_fstat = os.fstat
        calls = 0

        def change_before_end(fd):
            nonlocal calls
            calls += 1
            if calls == 2:
                current = path.stat()
                path.write_bytes(b'{"v":2}')
                os.utime(path, ns=(current.st_atime_ns, current.st_mtime_ns + 2_000_000_000))
            return real_fstat(fd)

        def reject(reason):
            raise imp.ImportRejected('resource-package-file-' + reason)

        with patch.object(resource_snapshot_io.os, 'fstat', change_before_end):
            with self.assertRaisesRegex(ValueError, 'resource-package-file-metadata-changed'):
                resource_snapshot_io.read_stable_file(
                    path, imp.MAX_FILE_BYTES, no_links=imp._no_links,
                    reject=reject)


if __name__ == '__main__':
    unittest.main()
