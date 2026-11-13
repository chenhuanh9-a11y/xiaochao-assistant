"""Real imported ZIPs, native files/leases and child-process-loss recovery.

The small JSON payloads exercise storage/selection ownership, not native map
semantics or GUI acceptance. Production callers supply their semantic decoder.
"""
from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
import uuid
import zipfile

import imported_resource_activation as activation
import imported_resource_archive as archives
from imported_resource_catalog import (CatalogSnapshot, ResourceResolver, ResourceCatalogError,
                                       PENDING_CATALOG, PENDING_REMOVAL, SCHEMA)
import imported_resource_removal as removal
from imported_resource_selection import inspect_selection
import map_package_upgrade as publication
import resource_transaction as locks


def wire(ref):
    return dict(packageId=ref.package_id, resourceVersion=ref.resource_version,
                componentId=ref.component_id, manifestSha256=ref.manifest_sha256)


def imported(root, package, *, season='s0', base=None, manifest_padding=0):
    archive = root.parent / (package + '.xingtu-route' if base else package + '.xingtu-base')
    context = dict(season=season, mode='nightmare')
    kinds = [('display', 'display')] if base else [('base', 'recognition-base'), ('controls', 'controls-profile')]
    components = [dict(componentId=name, type=kind, schemaVersion=1, contexts=[context],
        entrypoint='index.json', dependencies={'base': wire(base)} if base else {}) for name, kind in kinds]
    payloads = {name: json.dumps(dict(component=name, package=package)).encode() for name, _ in kinds}
    manifest = dict(schema='xingtu-resource-package', formatVersion=1, packageId=package,
        resourceVersion='v1', clientCompatibility=dict(resourceProtocol=1, requiredCapabilities=[]),
        components=components, files=[dict(componentId=name, path='index.json', bytes=len(raw),
            sha256=hashlib.sha256(raw).hexdigest()) for name, raw in payloads.items()])
    if manifest_padding:
        manifest['provenance'] = dict(note='x' * manifest_padding)
    with zipfile.ZipFile(archive, 'w') as target:
        target.writestr('manifest.json', json.dumps(manifest).encode())
        for name, raw in payloads.items():
            target.writestr(f'data/{name}/index.json', raw)
    summary = archives.save_archive(archive, root)
    return archive, summary.component_refs


CHILD = '''
import os, sys
from imported_resource_catalog import ResourceResolver
from imported_resource_removal import inspect_saved_version_removal, remove_saved_version
from imported_resource_selection import inspect_selection
root, package, version, digest, stop = sys.argv[1:]
resolver = ResourceResolver(root)
plan = inspect_saved_version_removal(resolver, package, version, digest)
def stage(name):
    if name == stop:
        os._exit(77)
remove_saved_version(resolver, plan, validate_selection=lambda value:inspect_selection(root, value), on_stage=stage)
'''


class ImportedResourceRemovalTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='imported-removal-')
        self.addCleanup(self.temp.cleanup)
        self.parent = Path(self.temp.name).resolve()
        self.root = self.parent / 'resources'
        self.archive, base = imported(self.root, 'base-package')
        self.base, self.controls = base
        self.route_archive, route = imported(self.root, 'route-package', base=self.base)
        self.route = route[0]
        self.other_archive, other = imported(self.root, 'other-author', base=self.base)
        self.other = other[0]
        self.s1_archive, s1 = imported(self.root, 's1-package', season='s1')
        self.s1base, self.s1controls = s1
        self.resolver = ResourceResolver(self.root)
        document = dict(schema=SCHEMA, generation=str(uuid.uuid4()), contexts={
            's0:nightmare': dict(recognitionBase=wire(self.base), controls=wire(self.controls), reviewedGroups=None),
            's1:nightmare': dict(recognitionBase=wire(self.s1base), controls=wire(self.s1controls), reviewedGroups=None)},
            displays=[wire(self.route), wire(self.other)])
        with locks.resource_write_lock(self.root):
            activation.activate_catalog_locked(self.root, document, expected_catalog_stamp=None,
                validate=self.validate)
        self.original_catalog = CatalogSnapshot(self.root).catalog_stamp
        self.sources = {path: path.read_bytes() for path in
                        (self.archive, self.route_archive, self.other_archive, self.s1_archive)}

    def validate(self, document):
        return inspect_selection(self.root, document)

    def package(self, ref):
        return self.root / 'packages' / ref.package_id / ref.resource_version

    def plan(self, ref=None):
        ref = ref or self.base
        return removal.inspect_saved_version_removal(self.resolver, ref.package_id,
            ref.resource_version, ref.manifest_sha256)

    def remove(self, plan=None, **kwargs):
        with locks.resource_runtime_lease(self.root):
            return removal.remove_saved_version(self.resolver, plan or self.plan(),
                validate_selection=self.validate, **kwargs)

    def recover(self):
        with locks.resource_write_lock(self.root):
            return removal.recover_saved_version_removal_locked(self.root)

    def lose(self, ref, stage):
        result = subprocess.run([sys.executable, '-c', CHILD, str(self.root), ref.package_id,
            ref.resource_version, ref.manifest_sha256, stage], capture_output=True, text=True, timeout=30)
        self.assertEqual(result.returncode, 77, result.stdout + result.stderr)

    def original_objects(self, ref=None):
        return {p.relative_to(self.root).as_posix(): (p.read_bytes(), publication._identity(p.stat()))
                for p in self.package(ref or self.base).rglob('*') if p.is_file()}

    def assert_sources(self):
        for path, raw in self.sources.items():
            self.assertEqual(path.read_bytes(), raw)

    def test_base_removal_detaches_only_dependent_context_and_authors_preserves_sources(self):
        plan = self.plan()
        self.assertEqual(plan['affectedContexts'], ['s0:nightmare'])
        self.assertEqual(plan['removedDisplays'], [wire(self.route), wire(self.other)])
        author_bytes = self.original_objects(self.route), self.original_objects(self.other)
        result = self.remove(plan)
        self.assertEqual(result['removedFiles'], 3)
        self.assertFalse(removal.removal_recovery_required(self.root))
        snapshot = self.resolver.snapshot()
        self.assertNotIn('s0:nightmare', snapshot.contexts)
        self.assertIn('s1:nightmare', snapshot.contexts)
        self.assertEqual(snapshot.displays, ())
        self.assertEqual((self.original_objects(self.route), self.original_objects(self.other)), author_bytes)
        self.assert_sources()

    def test_route_removal_preserves_base_and_other_author_selection(self):
        original = self.original_objects()
        plan = self.plan(self.route)
        self.assertEqual(plan['affectedContexts'], [])
        result = self.remove(plan)
        self.assertEqual(result['removedFiles'], 2)
        snapshot = self.resolver.snapshot()
        self.assertEqual(snapshot.contexts['s0:nightmare']['recognitionBase'], self.base)
        self.assertEqual(snapshot.displays, (self.other,))
        self.assertEqual(self.original_objects(), original)
        self.assert_sources()

    def test_saved_manifest_larger_than_catalog_limit_can_be_removed(self):
        archive, refs = imported(self.root, 'large-history', manifest_padding=2 * 1024 * 1024)
        original = archive.read_bytes()
        plan = self.plan(refs[0])
        self.assertGreater(len(plan['manifestBase64']), activation.LIMIT)
        result = self.remove(plan)
        self.assertEqual(result['removedFiles'], 3)
        self.assertEqual(result['affectedContexts'], [])
        self.assertEqual(self.resolver.snapshot().displays, (self.route, self.other))
        self.assertEqual(archive.read_bytes(), original)

    def test_legal_deep_inventory_can_reach_removal_confirmation(self):
        source = self.parent / 'deep.xingtu-route'
        raw = b'{}'
        paths = [f'{index:03d}/a/b/c/d/e/f/g/h/i/j/k/value.json' for index in range(500)]
        manifest = dict(schema='xingtu-resource-package', formatVersion=1,
            packageId='deep-history', resourceVersion='v1',
            clientCompatibility=dict(resourceProtocol=1, requiredCapabilities=[]),
            components=[dict(componentId='art', type='display', schemaVersion=1,
                contexts=[dict(season='s0', mode='nightmare')], entrypoint=paths[0], dependencies={})],
            files=[dict(componentId='art', path=path, bytes=len(raw),
                sha256=hashlib.sha256(raw).hexdigest()) for path in paths])
        with zipfile.ZipFile(source, 'x') as archive:
            archive.writestr('manifest.json', json.dumps(manifest))
            for path in paths:
                archive.writestr('data/art/' + path, raw)
        saved = archives.save_archive(source, self.root)
        plan = self.plan(saved.component_refs[0])
        self.assertGreater(len(plan['directoryIdentities']) + plan['fileCount'], 3 * 2000)
        self.assertEqual(plan['fileCount'], 501)
        self.assertEqual(plan['preserved'], [])
        self.assertEqual(plan['affectedContexts'], [])
        self.assertEqual(plan['catalogStamp'], self.original_catalog)

    def test_large_legal_deep_plan_and_journal_exceed_catalog_metadata_limit(self):
        source = self.parent / 'large-deep.xingtu-route'
        paths = [f'{index:03d}/' + '/'.join(f'd{level}' for level in range(30)) + '/value.json'
                 for index in range(750)]
        manifest = dict(schema='xingtu-resource-package', formatVersion=1,
            packageId='large-deep-history', resourceVersion='v1',
            clientCompatibility=dict(resourceProtocol=1, requiredCapabilities=[]),
            components=[dict(componentId='art', type='display', schemaVersion=1,
                contexts=[dict(season='s0', mode='nightmare')], entrypoint=paths[0], dependencies={})],
            files=[dict(componentId='art', path=path, bytes=2,
                sha256=hashlib.sha256(b'{}').hexdigest()) for path in paths], provenance=dict(note=''))
        raw = json.dumps(manifest).encode()
        manifest['provenance']['note'] = 'x' * (4 * 1024 * 1024 - len(raw) - 1024)
        with zipfile.ZipFile(source, 'x', compression=zipfile.ZIP_DEFLATED) as archive:
            archive.writestr('manifest.json', json.dumps(manifest).encode())
            for path in paths:
                archive.writestr('data/art/' + path, b'{}')
        saved = archives.save_archive(source, self.root)
        plan = self.plan(saved.component_refs[0])
        self.assertEqual(plan['fileCount'], 751)
        self.assertEqual(plan['preserved'], [])
        self.assertGreater(len(removal._encoded(plan)), publication.MAX_JOURNAL_BYTES)
        # Exercise the same owned metadata writer/reader as the transaction,
        # without moving every file in this pathological directory fixture.
        journal = dict(schema=removal.SCHEMA, plan=plan)
        payload = removal._encoded(journal)
        target = self.parent / 'large-removal-journal.json'
        stamp = removal._write_removal_metadata(target, payload)
        actual, read_stamp = removal._read_removal_metadata(target)
        self.assertEqual(actual, payload)
        self.assertEqual(read_stamp, stamp)
        self.assertEqual(json.loads(actual), journal)

    @unittest.skipUnless(os.name == 'nt', 'native same-handle empty directory disposition')
    def test_delete_then_reimport_same_version_uses_unmodified_archive_entry(self):
        result = self.remove(self.plan(self.route))
        self.assertEqual(result['status'], 'version-removed')
        self.assertFalse(self.package(self.route).exists())
        self.assertTrue(self.package(self.route).parent.is_dir())
        summary = archives.save_archive(self.route_archive, self.root)
        self.assertEqual(summary.status, 'saved')
        self.assertEqual(summary.component_refs, (self.route,))
        self.assert_sources()

    def test_unknown_modified_and_hardlinked_files_are_preserved_with_manifest(self):
        target = self.package(self.base)
        modified = target / 'data/base/index.json'
        modified.write_bytes(b'user edit')
        unknown = target / 'notes.txt'
        unknown.write_bytes(b'user notes')
        controls = target / 'data/controls/index.json'
        alias = self.parent / 'external-alias'
        os.link(controls, alias)
        manifest = (target / 'manifest.json').read_bytes()
        plan = self.plan()
        self.assertEqual({row['reason'] for row in plan['preserved']},
                         {'modified-file', 'unknown-file', 'linked-object'})
        self.assertEqual(plan['entries'], [])
        result = self.remove(plan)
        self.assertEqual(result['status'], 'version-partially-removed')
        self.assertEqual((target / 'manifest.json').read_bytes(), manifest)
        self.assertEqual(modified.read_bytes(), b'user edit')
        self.assertEqual(unknown.read_bytes(), b'user notes')
        self.assertEqual(controls.read_bytes(), alias.read_bytes())
        self.assertNotIn('s0:nightmare', self.resolver.snapshot().contexts)

    def test_partial_removal_deletes_only_declared_bytes_and_keeps_unknown_tree_provenance(self):
        target = self.package(self.base)
        unknown = target / 'private-notes' / 'mine.txt'
        unknown.parent.mkdir()
        unknown.write_bytes(b'unknown user bytes')
        manifest = (target / 'manifest.json').read_bytes()
        result = self.remove()
        self.assertEqual(result['status'], 'version-partially-removed')
        self.assertEqual(result['removedFiles'], 2)
        self.assertEqual(unknown.read_bytes(), b'unknown user bytes')
        self.assertEqual((target / 'manifest.json').read_bytes(), manifest)
        self.assertFalse((target / 'data/base/index.json').exists())
        self.assertFalse((target / 'data/controls/index.json').exists())

    def test_declared_symlink_preserves_external_target_and_manifest(self):
        target = self.package(self.route)
        path = target / 'data/display/index.json'
        path.unlink()  # Fixture-owned file only.
        outside = self.parent / 'external-symlink-target'
        outside.write_bytes(b'outside user data')
        try:
            path.symlink_to(outside)
        except OSError as error:
            self.skipTest(str(error))
        result = self.remove(self.plan(self.route))
        self.assertEqual(result['status'], 'version-partially-removed')
        self.assertEqual(result['removedFiles'], 0)
        self.assertEqual(result['preserved'][0]['reason'], 'linked-object')
        self.assertTrue(path.is_symlink())
        self.assertEqual(outside.read_bytes(), b'outside user data')
        self.assertTrue((target / 'manifest.json').exists())

    def test_version_directory_replacement_after_confirmation_is_not_adopted(self):
        plan = self.plan(self.route)
        path = self.package(self.route)
        retained = self.parent / 'original-version'
        path.rename(retained)
        shutil.copytree(retained, path)
        before = self.original_objects(self.route)
        with self.assertRaisesRegex(ValueError, 'plan-changed-reinspect-required'):
            self.remove(plan)
        self.assertEqual(self.original_objects(self.route), before)
        self.assertEqual(CatalogSnapshot(self.root).catalog_stamp, self.original_catalog)

    def test_plan_change_same_inode_edit_or_same_byte_replacement_is_refused(self):
        for replacement in (False, True):
            with self.subTest(replacement=replacement):
                plan = self.plan(self.route)
                path = self.package(self.route) / 'data/display/index.json'
                raw = path.read_bytes()
                if replacement:
                    path.rename(self.parent / 'foreign-original')
                    path.write_bytes(raw)
                else:
                    path.write_bytes(b'x' * len(raw))
                with self.assertRaisesRegex(ValueError, 'plan-changed-reinspect-required'):
                    self.remove(plan)
                self.assertEqual(CatalogSnapshot(self.root).catalog_stamp, self.original_catalog)
                self.assertFalse(removal.removal_recovery_required(self.root))
                path.write_bytes(raw)

    def test_changed_manifest_and_forged_plan_never_authorize_deletion(self):
        plan = self.plan()
        forged = deepcopy(plan)
        forged['entries'][0]['path'] = 'catalog.json'
        with self.assertRaisesRegex(ValueError, 'entry-not-owned'):
            self.remove(forged)
        path = self.package(self.base) / 'manifest.json'
        path.write_bytes(path.read_bytes() + b' ')
        with self.assertRaisesRegex(ValueError, 'manifest-changed'):
            self.remove(plan)
        self.assertEqual(CatalogSnapshot(self.root).catalog_stamp, self.original_catalog)

    def test_cancel_or_decoder_failure_after_moves_restores_original_objects_and_catalog(self):
        for decoder_failure in (False, True):
            with self.subTest(decoder_failure=decoder_failure):
                before = self.original_objects()
                cancelled = [False]
                calls = []
                def validate(document):
                    calls.append(True)
                    self.validate(document)
                    if decoder_failure and len(calls) == 2:
                        raise ValueError('fixture-decoder-failure')
                def stage(name):
                    if not decoder_failure and name == 'file-retained':
                        cancelled[0] = True
                with locks.resource_runtime_lease(self.root):
                    with self.assertRaises(ValueError):
                        removal.remove_saved_version(self.resolver, self.plan(), validate_selection=validate,
                            cancelled=lambda:cancelled[0], on_stage=stage)
                self.assertEqual(self.original_objects(), before)
                self.assertEqual(CatalogSnapshot(self.root).catalog_stamp, self.original_catalog)
                self.assertFalse(removal.removal_recovery_required(self.root))

    def test_new_unknown_file_before_commit_cancels_deletion_and_restores_provenance(self):
        before = self.original_objects(self.route)
        unknown = self.package(self.route) / 'user-added.txt'
        def stage(name):
            if name == 'catalog-published':
                unknown.write_bytes(b'late user content')
        with self.assertRaisesRegex(ValueError, 'source-changed-before-commit'):
            self.remove(self.plan(self.route), on_stage=stage)
        self.assertEqual(unknown.read_bytes(), b'late user content')
        for name, (raw, identity) in before.items():
            self.assertEqual((self.root / name).read_bytes(), raw)
            self.assertEqual(publication._identity((self.root / name).stat()), identity)
        self.assertEqual(CatalogSnapshot(self.root).catalog_stamp, self.original_catalog)

    def test_cancellation_or_exception_after_commit_never_restores_deleted_version(self):
        cancelled = [False]
        def stage(name):
            if name == 'committed':
                cancelled[0] = True
                raise OSError('fixture sync error after durable witness')
        result = self.remove(self.plan(self.route), on_stage=stage, cancelled=lambda:cancelled[0])
        self.assertIn(result['status'], ('version-removed', 'version-partially-removed'))
        self.assertEqual(self.resolver.snapshot().displays, (self.other,))
        self.assertFalse((self.package(self.route) / 'manifest.json').exists())

    def test_process_loss_before_and_after_commit_recovers_one_catalog_outcome(self):
        before = self.original_objects()
        for stage in ('removal-pending', 'file-retained', 'catalog-published'):
            with self.subTest(stage=stage):
                self.lose(self.base, stage)
                with self.assertRaisesRegex(ResourceCatalogError, 'removal-recovery-required'):
                    self.resolver.snapshot()
                self.assertEqual(self.recover()['status'], 'removal-rolled-back')
                self.assertEqual(self.original_objects(), before)
                self.assertEqual(CatalogSnapshot(self.root).catalog_stamp, self.original_catalog)
        self.lose(self.base, 'committed')
        result = self.recover()
        self.assertIn(result['status'], ('version-removed', 'version-partially-removed'))
        self.assertNotIn('s0:nightmare', self.resolver.snapshot().contexts)
        self.assertEqual(self.recover()['status'], 'removal-ready')
        self.assert_sources()

    def test_process_loss_after_catalog_pending_retired_still_blocks_and_recovers(self):
        self.lose(self.route, 'before-cleanup')
        self.assertFalse((self.root / PENDING_CATALOG).exists())
        self.assertTrue((self.root / PENDING_REMOVAL).exists())
        with self.assertRaisesRegex(ResourceCatalogError, 'removal-recovery-required'):
            self.resolver.snapshot()
        result = self.recover()
        self.assertEqual(result['removedFiles'], 2)
        self.assertEqual(self.resolver.snapshot().displays, (self.other,))

    def test_process_loss_during_restore_resumes_without_replacing_original_objects(self):
        before = self.original_objects(self.route)
        self.lose(self.route, 'file-retained')
        script = '''
import os, sys
from imported_resource_removal import recover_saved_version_removal_locked
from resource_transaction import resource_write_lock
def stage(name):
    if name == 'file-restored':
        os._exit(78)
with resource_write_lock(sys.argv[1]):
    recover_saved_version_removal_locked(sys.argv[1], on_stage=stage)
'''
        result = subprocess.run([sys.executable, '-c', script, str(self.root)],
                                capture_output=True, text=True, timeout=30)
        self.assertEqual(result.returncode, 78, result.stdout + result.stderr)
        self.assertTrue(removal.removal_recovery_required(self.root))
        self.assertEqual(self.recover()['status'], 'removal-rolled-back')
        self.assertEqual(self.original_objects(self.route), before)
        self.assertEqual(CatalogSnapshot(self.root).catalog_stamp, self.original_catalog)

    def test_partial_terminal_cleanup_never_deletes_later_recreated_file(self):
        self.lose(self.route, 'file-cleaned')
        path = self.package(self.route) / 'data/display/index.json'
        path.write_bytes(b'new user file')
        identity = publication._identity(path.stat())
        result = self.recover()
        self.assertEqual(result['status'], 'version-partially-removed')
        self.assertEqual(path.read_bytes(), b'new user file')
        self.assertEqual(publication._identity(path.stat()), identity)
        self.assertFalse(removal.removal_recovery_required(self.root))

    def test_foreign_empty_version_directory_is_not_pruned_after_commit(self):
        path = self.package(self.route)
        identity = []
        def stage(name):
            if name == 'before-cleanup':
                path.rename(self.parent / 'user-moved-empty-version')
                path.mkdir()
                identity.extend(publication._identity(path.stat()))
        result = self.remove(self.plan(self.route), on_stage=stage)
        self.assertEqual(result['status'], 'version-partially-removed')
        self.assertTrue(path.is_dir())
        self.assertEqual(publication._identity(path.stat()), identity)

    def test_native_cleanup_failure_is_recoverable_without_restoring_selected_version(self):
        original = removal.files._rollback_published
        def delete(path, identity, digest):
            if path.parent.name == 'removal-objects':
                return False
            return original(path, identity, digest)
        with patch.object(removal.files, '_rollback_published', side_effect=delete):
            with self.assertRaisesRegex(ValueError, 'cleanup-required'):
                self.remove(self.plan(self.route))
        self.assertTrue(removal.removal_recovery_required(self.root))
        self.assertFalse((self.package(self.route) / 'manifest.json').exists())
        self.recover()
        self.assertEqual(self.resolver.snapshot().displays, (self.other,))

    def test_quarantine_replacement_and_lost_commit_witness_fail_closed(self):
        self.lose(self.route, 'file-retained')
        pointer = json.loads((self.root / PENDING_REMOVAL).read_bytes())
        directory = self.root / 'transactions' / pointer['transactionId']
        path = directory / 'removal-objects/0'
        raw = path.read_bytes()
        path.rename(directory / 'retained-foreign-original')
        path.write_bytes(raw)
        with self.assertRaisesRegex(ValueError, 'confirmed-file-changed'):
            self.recover()
        self.assertTrue(removal.removal_recovery_required(self.root))

    def test_commit_witness_loss_does_not_restore_committed_version(self):
        self.lose(self.route, 'committed')
        pointer = json.loads((self.root / PENDING_REMOVAL).read_bytes())
        directory = self.root / 'transactions' / pointer['transactionId']
        (directory / 'committed.json').rename(directory / 'foreign-retained-witness')
        with self.assertRaisesRegex(ValueError, 'commit-outcome-uncertain'):
            self.recover()
        self.assertTrue(removal.removal_recovery_required(self.root))
        self.assertFalse((self.package(self.route) / 'manifest.json').exists())

    def test_same_byte_pending_marker_replacement_is_not_adopted(self):
        self.lose(self.route, 'file-retained')
        path = self.root / PENDING_REMOVAL
        raw = path.read_bytes()
        path.rename(path.with_name('foreign-original-pending-removal'))
        path.write_bytes(raw)
        with self.assertRaises(ValueError):
            self.recover()
        self.assertEqual(path.read_bytes(), raw)
        self.assertTrue(removal.removal_recovery_required(self.root))

    def test_root_replacement_cannot_recover_copied_transaction_records(self):
        self.lose(self.route, 'file-retained')
        original = self.parent / 'original-resource-root'
        self.root.rename(original)
        shutil.copytree(original, self.root)
        before = {p.relative_to(self.root): (p.read_bytes(), publication._identity(p.stat()))
                  for p in self.root.rglob('*') if p.is_file()}
        # Copying the root also replaces its journal object; this earlier
        # ownership check must fail before any catalog/resource restoration.
        with self.assertRaisesRegex(ValueError, 'resource-removal-journal-changed'):
            self.recover()
        after = {p.relative_to(self.root): (p.read_bytes(), publication._identity(p.stat()))
                 for p in self.root.rglob('*') if p.is_file()}
        self.assertEqual(after, before)
        self.assertTrue(removal.removal_recovery_required(self.root))

    @unittest.skipUnless(os.name == 'nt', 'Windows junction boundary')
    def test_junction_version_is_rejected_without_touching_its_target(self):
        path = self.package(self.route)
        before = self.original_objects(self.route)
        retained = self.parent / 'fixture-original-version'
        path.rename(retained)
        result = subprocess.run(['cmd', '/c', 'mklink', '/J', str(path), str(retained)],
                                capture_output=True, text=True, timeout=10)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        with self.assertRaisesRegex(ValueError, 'linked-target'):
            self.plan(self.route)
        for name, (raw, identity) in before.items():
            original = retained / Path(name).relative_to('packages/route-package/v1')
            self.assertEqual(original.read_bytes(), raw)
            self.assertEqual(publication._identity(original.stat()), identity)
        self.assertEqual(CatalogSnapshot(self.root).catalog_stamp, self.original_catalog)

    def test_foreign_file_during_restore_is_preserved_and_keeps_recovery_marker(self):
        self.lose(self.route, 'file-retained')
        pointer = json.loads((self.root / PENDING_REMOVAL).read_bytes())
        directory = self.root / 'transactions' / pointer['transactionId']
        journal = json.loads((directory / 'removal-journal.json').read_bytes())
        row = journal['plan']['entries'][0]
        path = self.root / row['path']
        path.write_bytes(b'external replacement')
        with self.assertRaisesRegex(ValueError, 'restore-conflict'):
            self.recover()
        self.assertEqual(path.read_bytes(), b'external replacement')
        self.assertTrue(removal.removal_recovery_required(self.root))


if __name__ == '__main__':
    unittest.main()
