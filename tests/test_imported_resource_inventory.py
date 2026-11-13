"""Read-only saved imported resource inventory behavior."""
import hashlib
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import uuid

import resource_snapshot_io
import imported_resource_inventory as inventory
from imported_resource_catalog import ResourceResolver, SCHEMA as CATALOG_SCHEMA
from imported_resource_inventory import (ImportedResourceInventoryError,
                                         list_saved_versions)
from imported_resource_packages import ComponentRef, MANIFEST_LIMIT, PackageView
import imported_resource_packages as packages


def encoded(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True,
                      separators=(',', ':')).encode('utf-8')


class ImportedResourceInventoryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / 'resources'
        self.resolver = ResourceResolver(self.root)

    def package(self, package_id, version='1', components=None, *, display='Fixture', extra=None):
        components = components or [dict(componentId='base', type='recognition-base',
                                          contexts=[dict(season='s0', mode='nightmare')])]
        files = []
        files_by_component = {}
        manifest_components = []
        for component in components:
            component_id = component['componentId']
            payload = encoded(dict(component=component_id))
            entrypoint = component.get('entrypoint', 'entry.json')
            files.append(dict(componentId=component_id, path=entrypoint, bytes=len(payload),
                               sha256=hashlib.sha256(payload).hexdigest()))
            files_by_component[component_id] = (entrypoint, payload)
            manifest_components.append(dict(componentId=component_id, type=component['type'],
                                            schemaVersion=1, contexts=component['contexts'],
                                            entrypoint=entrypoint, dependencies={}))
        manifest = dict(schema='xingtu-resource-package', formatVersion=1,
                        packageId=package_id, resourceVersion=version,
                        clientCompatibility=dict(resourceProtocol=1, requiredCapabilities=[]),
                        components=manifest_components, files=files,
                        displayName=display)
        raw = encoded(manifest)
        folder = self.root / 'packages' / package_id / version
        for component_id, (entrypoint, payload) in files_by_component.items():
            target = folder / 'data' / component_id / entrypoint
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(payload)
        (folder / 'manifest.json').write_bytes(raw)
        return dict(packageId=package_id, resourceVersion=version,
                    manifestSha256=hashlib.sha256(raw).hexdigest(),
                    componentRefs=[dict(packageId=package_id, resourceVersion=version,
                                        componentId=component['componentId'],
                                        manifestSha256=hashlib.sha256(raw).hexdigest())
                                   for component in components],
                    folder=folder, manifest=manifest)

    def maximum_file_package(self):
        """A real package at the manifest protocol's exact file-count limit."""
        package_id = 'maximum-files'
        version = '1'
        payloads = [('entry.json', b'{}')]
        payloads.extend((f'payload-{index:04d}.bin', b'x')
                        for index in range(1, packages.files.MAX_FILES))
        file_rows = [dict(componentId='base', path=path, bytes=len(payload),
                          sha256=hashlib.sha256(payload).hexdigest())
                     for path, payload in payloads]
        manifest = dict(schema='xingtu-resource-package', formatVersion=1,
                        packageId=package_id, resourceVersion=version,
                        clientCompatibility=dict(resourceProtocol=1, requiredCapabilities=[]),
                        components=[dict(componentId='base', type='recognition-base',
                                         schemaVersion=1,
                                         contexts=[dict(season='s0', mode='nightmare')],
                                         entrypoint='entry.json', dependencies={})],
                        files=file_rows, displayName='Maximum file inventory')
        raw = encoded(manifest)
        self.assertLessEqual(len(raw), MANIFEST_LIMIT)
        folder = self.root / 'packages' / package_id / version
        data = folder / 'data' / 'base'
        data.mkdir(parents=True)
        for path, payload in payloads:
            (data / path).write_bytes(payload)
        (folder / 'manifest.json').write_bytes(raw)
        return folder

    def catalog(self, *, contexts=None, displays=None):
        value = dict(schema=CATALOG_SCHEMA, generation=uuid.uuid4().hex,
                     contexts=contexts or {}, displays=displays or [])
        self.root.mkdir(parents=True, exist_ok=True)
        (self.root / 'catalog.json').write_bytes(encoded(value))
        return value

    @staticmethod
    def role_context(base=None, controls=None, groups=None):
        return dict(recognitionBase=base, controls=controls, reviewedGroups=groups)

    def test_missing_root_returns_empty_without_creating_it(self):
        snapshot = list_saved_versions(self.resolver)
        self.assertEqual(snapshot.rows, ())
        self.assertIsNone(snapshot.generation)
        self.assertIsNone(snapshot.catalog_issue)
        self.assertFalse(self.root.exists())

    def test_lists_real_multicomponent_and_inactive_versions_with_selected_role_context(self):
        base = self.package('multi', components=[
            dict(componentId='base', type='recognition-base',
                 contexts=[dict(season='s0', mode='nightmare')]),
            dict(componentId='controls', type='controls-profile',
                 contexts=[dict(season='s0', mode='nightmare')]),
        ])
        inactive = self.package('history', '2', components=[
            dict(componentId='display', type='display',
                 contexts=[dict(season='s1', mode='difficult')]),
        ])
        refs = base['componentRefs']
        catalog = self.catalog(contexts={
            's0:nightmare': self.role_context(refs[0], refs[1]),
        })

        snapshot = list_saved_versions(self.resolver)

        self.assertEqual(snapshot.generation, catalog['generation'])
        self.assertIsNone(snapshot.catalog_issue)
        rows = {row['key']: row for row in snapshot.rows}
        active_row = rows['multi/1']
        self.assertEqual(active_row['status'], 'selected')
        self.assertEqual(active_row['selectedContexts'], ['s0:nightmare'])
        self.assertEqual(active_row['displayName'], 'Fixture')
        self.assertEqual([item['type'] for item in active_row['components']],
                         ['recognition-base', 'controls-profile'])
        self.assertEqual(rows['history/2']['status'], 'saved-unverified')
        self.assertEqual(rows['history/2']['selectedContexts'], [])
        for row in snapshot.rows:
            self.assertFalse(row['canRemove'])
            self.assertTrue(row['canInspectRemoval'])
            self.assertEqual(row['storageFormat'], '')

    def test_self_declared_reviewed_group_does_not_count_as_selected(self):
        groups = self.package('groups', components=[
            dict(componentId='registry', type='reviewed-group-registry',
                 contexts=[dict(season='s0', mode='nightmare')]),
        ])
        ref = groups['componentRefs'][0]
        self.catalog(contexts={'s0:nightmare': self.role_context(groups=ref)})

        row = list_saved_versions(self.resolver).rows[0]

        self.assertEqual(row['status'], 'saved-unverified')
        self.assertEqual(row['selectedContexts'], [])

    def test_missing_entrypoint_is_damaged_but_does_not_hide_other_saved_rows(self):
        missing = self.package('missing')
        self.package('healthy')
        (missing['folder'] / 'data' / 'base' / 'entry.json').unlink()

        rows = {row['key']: row for row in list_saved_versions(self.resolver).rows}

        self.assertEqual(rows['missing/1']['status'], 'damaged')
        self.assertEqual(rows['missing/1']['issue'], 'resource-package-entrypoint-unavailable')
        self.assertEqual(rows['healthy/1']['status'], 'saved-unverified')

    def test_catalog_manifest_hash_drift_damages_expected_saved_version(self):
        package = self.package('active')
        ref = package['componentRefs'][0]
        self.catalog(contexts={'s0:nightmare': self.role_context(base=ref)})
        changed = dict(package['manifest'], displayName='Edited after activation')
        (package['folder'] / 'manifest.json').write_bytes(encoded(changed))

        row = list_saved_versions(self.resolver).rows[0]

        self.assertEqual(row['status'], 'damaged')
        self.assertEqual(row['issue'], 'resource-package-manifest-changed')
        self.assertEqual(row['selectedContexts'], [])

    def test_unknown_payload_is_not_read_in_fast_mode_and_full_verification_reports_it(self):
        package = self.package('unknown')
        unknown = package['folder'] / 'data' / 'base' / 'orphan.bin'
        unknown.write_bytes(b'unknown but retained')
        reads = []
        original = resource_snapshot_io.read_stable_file

        def spy(path, *args, **kwargs):
            reads.append(Path(path).name)
            return original(path, *args, **kwargs)

        with patch.object(resource_snapshot_io, 'read_stable_file', side_effect=spy):
            quick = list_saved_versions(self.resolver)
        self.assertEqual(quick.rows[0]['status'], 'saved-unverified')
        self.assertNotIn('entry.json', reads)
        self.assertNotIn('orphan.bin', reads)

        full = list_saved_versions(self.resolver, verify=True)
        self.assertEqual(full.rows[0]['status'], 'damaged')
        self.assertEqual(full.rows[0]['issue'], 'resource-package-undeclared-file')

    def test_valid_maximum_file_package_is_verified_without_exporting_file_inventory(self):
        folder = self.maximum_file_package()
        manifest_sha = hashlib.sha256((folder / 'manifest.json').read_bytes()).hexdigest()
        ref = ComponentRef('maximum-files', '1', 'base', manifest_sha)
        self.assertTrue(PackageView(self.root, ref).validate_inventory())

        snapshot = list_saved_versions(self.resolver, verify=False)

        row = snapshot.rows[0]
        self.assertEqual(row['status'], 'saved-unverified')
        self.assertEqual(row['key'], 'maximum-files/1')
        self.assertEqual(len(row['components']), 1)
        self.assertNotIn('files', row)
        self.assertNotIn('inventory', row)


    def test_malformed_manifest_is_a_damaged_row_and_other_versions_remain_visible(self):
        broken = self.package('broken')
        self.package('valid')
        (broken['folder'] / 'manifest.json').write_bytes(b'{not json')

        rows = {row['key']: row for row in list_saved_versions(self.resolver).rows}

        self.assertEqual(rows['broken/1']['status'], 'damaged')
        self.assertEqual(rows['broken/1']['issue'], 'invalid-json')
        self.assertEqual(rows['valid/1']['status'], 'saved-unverified')

    def test_linked_version_is_reported_without_following_it(self):
        self.package('linked')
        linked_path = self.root / 'packages' / 'linked' / '2'
        outside = Path(self.temp.name) / 'outside'
        outside.mkdir()
        try:
            os.symlink(outside, linked_path, target_is_directory=True)
        except (OSError, NotImplementedError) as error:
            self.skipTest(f'filesystem does not permit symlink fixture: {error}')

        rows = {row['key']: row for row in list_saved_versions(self.resolver).rows}

        self.assertEqual(rows['linked/2']['status'], 'damaged')
        self.assertEqual(rows['linked/2']['issue'],
                         'resource-inventory-linked-or-unreadable-object')
        self.assertFalse((outside / 'manifest.json').exists())

    def test_pending_catalog_lists_saved_packages_but_never_marks_them_selected(self):
        package = self.package('pending')
        ref = package['componentRefs'][0]
        self.catalog(contexts={'s0:nightmare': self.role_context(base=ref)})
        pending = self.root / 'transactions' / 'pending-catalog.json'
        pending.parent.mkdir(parents=True)
        pending.write_bytes(b'pending')

        snapshot = list_saved_versions(self.resolver)

        self.assertEqual(snapshot.catalog_issue, 'resource-catalog-recovery-required')
        self.assertIsNone(snapshot.generation)
        self.assertEqual(snapshot.rows[0]['status'], 'saved-unverified')
        self.assertEqual(snapshot.rows[0]['selectedContexts'], [])

    def test_corrupt_catalog_still_lists_versions_without_claiming_selection(self):
        package = self.package('catalog-broken')
        ref = package['componentRefs'][0]
        self.catalog(contexts={'s0:nightmare': self.role_context(base=ref)})
        (self.root / 'catalog.json').write_bytes(b'{broken')

        snapshot = list_saved_versions(self.resolver)

        self.assertEqual(snapshot.catalog_issue, 'invalid-json')
        self.assertIsNone(snapshot.generation)
        self.assertEqual(snapshot.rows[0]['status'], 'saved-unverified')
        self.assertEqual(snapshot.rows[0]['selectedContexts'], [])

    def test_cancelled_scan_stops_with_stable_code(self):
        self.package('cancel')

        with self.assertRaises(ImportedResourceInventoryError) as caught:
            list_saved_versions(self.resolver, cancelled=lambda: True)

        self.assertEqual(caught.exception.code, 'resource-inventory-cancelled')

    def test_catalog_replacement_during_manifest_read_supersedes_result(self):
        self.package('race')
        original = resource_snapshot_io.read_stable_file
        changed = False

        def read_then_replace(path, *args, **kwargs):
            nonlocal changed
            result = original(path, *args, **kwargs)
            if Path(path).name == 'manifest.json' and not changed:
                changed = True
                self.catalog()
            return result

        with patch.object(resource_snapshot_io, 'read_stable_file', side_effect=read_then_replace):
            with self.assertRaises(ImportedResourceInventoryError) as caught:
                list_saved_versions(self.resolver)

        self.assertEqual(caught.exception.code, 'resource-catalog-superseded')

    def test_manifest_rewrite_after_selected_context_resolution_supersedes_snapshot(self):
        package = self.package('manifest-race')
        ref = package['componentRefs'][0]
        self.catalog(contexts={'s0:nightmare': self.role_context(base=ref)})
        original_selection = inventory._selected_contexts

        def select_then_rewrite(snapshot, budget):
            selected = original_selection(snapshot, budget)
            changed_manifest = dict(package['manifest'], displayName='Edited after selection')
            (package['folder'] / 'manifest.json').write_bytes(encoded(changed_manifest))
            return selected

        with patch.object(inventory, '_selected_contexts', side_effect=select_then_rewrite):
            with self.assertRaises(ImportedResourceInventoryError) as caught:
                list_saved_versions(self.resolver)

        self.assertEqual(caught.exception.code, 'resource-inventory-superseded')
        self.assertNotIn(str(self.root), str(caught.exception))

    def test_catalog_becoming_symlink_after_manifest_read_is_superseded_without_path(self):
        package = self.package('race-link')
        ref = package['componentRefs'][0]
        self.catalog(contexts={'s0:nightmare': self.role_context(base=ref)})
        catalog_path = self.root / 'catalog.json'
        outside = Path(self.temp.name) / 'outside-catalog.json'
        outside.write_bytes(catalog_path.read_bytes())
        original = resource_snapshot_io.read_stable_file
        changed = False

        def read_then_link(path, *args, **kwargs):
            nonlocal changed
            result = original(path, *args, **kwargs)
            if Path(path) == package['folder'] / 'manifest.json' and not changed:
                changed = True
                catalog_path.unlink()
                os.symlink(outside, catalog_path)
            return result

        with patch.object(resource_snapshot_io, 'read_stable_file', side_effect=read_then_link):
            with self.assertRaises(ImportedResourceInventoryError) as caught:
                list_saved_versions(self.resolver)

        self.assertEqual(caught.exception.code, 'resource-catalog-superseded')
        self.assertNotIn(str(self.root), str(caught.exception))

    def test_catalog_io_error_at_final_current_check_is_superseded(self):
        self.package('race-io')
        self.catalog()
        catalog_path = self.root / 'catalog.json'
        original = resource_snapshot_io.read_stable_file
        catalog_reads = 0
        observed_io_errors = []

        def read_then_replace_with_directory(path, *args, **kwargs):
            nonlocal catalog_reads
            path = Path(path)
            if path == catalog_path:
                catalog_reads += 1
            try:
                result = original(path, *args, **kwargs)
            except OSError as error:
                if path == catalog_path:
                    observed_io_errors.append(type(error))
                raise
            # With an empty catalog, reads 4..11 are resolve_context's four
            # start/end generation checks. Replacing after read 11 leaves the
            # outer snapshot.assert_current() as the next real filesystem read.
            if path == catalog_path and catalog_reads == 11:
                catalog_path.rename(self.root / 'catalog-retained.json')
                catalog_path.mkdir()
            return result

        with patch.object(resource_snapshot_io, 'read_stable_file',
                          side_effect=read_then_replace_with_directory):
            with self.assertRaises(ImportedResourceInventoryError) as caught:
                list_saved_versions(self.resolver)

        self.assertTrue(observed_io_errors)
        self.assertEqual(caught.exception.code, 'resource-catalog-superseded')
        self.assertNotIn(str(self.root), str(caught.exception))


if __name__ == '__main__':
    unittest.main()
