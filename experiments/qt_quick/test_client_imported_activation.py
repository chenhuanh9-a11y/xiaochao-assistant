"""Actual package, prospective-engine and catalog activation regressions."""
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
import uuid
from unittest.mock import patch
import zipfile

import cv2
import numpy as np

import client_imported_activation as imported_activation
from client_imported_providers import (SUPPORTED_CAPABILITIES,
                                       build_imported_engine_factories)
from client_paths import RuntimePaths
from imported_resource_activation import activate_catalog_locked
from imported_resource_archive import save_archive
from imported_resource_catalog import ResourceCatalogError, ResourceResolver, SCHEMA
from imported_resource_packages import ComponentRef
from imported_resource_selection import inspect_selection
from resource_transaction import resource_runtime_lease, resource_write_lock
import star_map_import as files
from imported_original_base import decode_original_base
from original_qt_resource_fixtures import OriginalS0NightmareSourceFixture


def _wire(ref):
    return dict(packageId=ref.package_id, resourceVersion=ref.resource_version,
                componentId=ref.component_id, manifestSha256=ref.manifest_sha256)


def _encoded(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True,
                      separators=(',', ':')).encode('utf-8')


class ClientImportedActivationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        source_temp = tempfile.TemporaryDirectory(prefix='original-s0n-activation-source-')
        cls.addClassCleanup(source_temp.cleanup)
        cls.source = OriginalS0NightmareSourceFixture(Path(source_temp.name))
        cls.source.export()

    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.install_root = Path(temporary.name).resolve()
        self.paths = RuntimePaths(self.install_root, False)
        # Resolve the Windows temp root before it becomes a resource-lock key.
        self.root = Path(self.paths.imported_resource_root).resolve()
        saved = save_archive(self.source.output, self.root)
        self.refs = {ref.component_id: ref for ref in saved.component_refs}
        self.resolver = ResourceResolver(
            self.root, supported_capabilities=SUPPORTED_CAPABILITIES)

    def save_revision(self, version, *, wrong_control_floor=False):
        """Reversion the real synthetic exporter ZIP and keep its full inventory valid."""
        members = {}
        with zipfile.ZipFile(self.source.output) as archive:
            members = {name: archive.read(name) for name in archive.namelist()}
        manifest = json.loads(members['manifest.json'])
        manifest['resourceVersion'] = version
        if wrong_control_floor:
            profile_path = 'data/controls/profile.json'
            profile = json.loads(members[profile_path])
            profile['contexts']['s0:nightmare']['floors'][0]['floorId'] = 'fixture-wrong-floor'
            members[profile_path] = _encoded(profile)
        for row in manifest['files']:
            raw = members[f"data/{row['componentId']}/{row['path']}"]
            row.update(bytes=len(raw), sha256=hashlib.sha256(raw).hexdigest())
        members['manifest.json'] = _encoded(manifest)
        archive_path = self.install_root / f'{version}.zip'
        with zipfile.ZipFile(archive_path, 'x', compression=zipfile.ZIP_DEFLATED) as archive:
            for name, raw in members.items():
                archive.writestr(name, raw)
        saved = save_archive(archive_path, self.root)
        return {ref.component_id: ref for ref in saved.component_refs}

    def activate(self, refs=None, **kwargs):
        return imported_activation.activate_context(
            self.resolver, self.paths,
            (refs or self.refs)['base'], (refs or self.refs)['controls'], **kwargs)

    def _inert_component(self, component_id, component_type, *, season='s0', mode='nightmare'):
        payload = b'{}'
        package_id, version = f'fixture-qt-{component_id}', '1'
        manifest = dict(
            schema='xingtu-resource-package', formatVersion=1,
            packageId=package_id, resourceVersion=version,
            clientCompatibility=dict(resourceProtocol=1, requiredCapabilities=[]),
            components=[dict(componentId=component_id, type=component_type, schemaVersion=1,
                             contexts=[dict(season=season, mode=mode)],
                             entrypoint='entry.json', dependencies={})],
            files=[dict(componentId=component_id, path='entry.json', bytes=len(payload),
                        sha256=hashlib.sha256(payload).hexdigest())],
        )
        raw_manifest = _encoded(manifest)
        package = self.root / 'packages' / package_id / version
        entry = package / 'data' / component_id / 'entry.json'
        entry.parent.mkdir(parents=True)
        entry.write_bytes(payload)
        (package / 'manifest.json').write_bytes(raw_manifest)
        return ComponentRef(package_id, version, component_id,
                            hashlib.sha256(raw_manifest).hexdigest())

    def seed_catalog(self, *, groups=None, displays=(), other_context=True):
        contexts = {
            's0:nightmare': dict(recognitionBase=None, controls=None,
                                 reviewedGroups=_wire(groups) if groups else None),
        }
        if other_context:
            contexts['s1:difficult'] = dict(recognitionBase=None, controls=None,
                                           reviewedGroups=None)
        document = dict(schema=SCHEMA, generation=str(uuid.uuid4()), contexts=contexts,
                        displays=[_wire(ref) for ref in displays])

        def validate(proposed):
            inspect_selection(
                self.root, proposed,
                supported_capabilities=self.resolver.supported_capabilities,
                project_registry_refs=self.resolver.project_registry_refs)

        with resource_write_lock(self.root):
            return activate_catalog_locked(
                self.root, document, expected_catalog_stamp=None, validate=validate)

    def _valid_display_component(self):
        from client_providers import SharedReferenceProvider
        from imported_author_display import SCHEMA as DISPLAY_SCHEMA
        from imported_resource_packages import PackageView
        view = PackageView(self.root, self.refs['base'])
        base = decode_original_base(view, season='s0', mode='nightmare')
        provider = SharedReferenceProvider(view.root, base.resources, base.versions,
                                           check_current=base.assert_current)
        mid, floor = next(iter(base.pack.by_id)), base.pack.floors[0]
        reference = provider('shared-structure', mid, floor)
        row = dict(mapId=mid, floor=floor, displayPath='route.png', displayShapeHW=[3,5],
            matrixDisplayToReference=np.eye(3).tolist(), referenceShapeHW=list(reference.shape),
            referenceResource=reference.path.relative_to(view.root).as_posix(),
            coordinateSpace=reference.metadata['canvas']['coordinateSpace'],
            **{key:reference.metadata[key] for key in ('structureVersionKey','sourceVersion','baseSnapshot')})
        document = dict(schema=DISPLAY_SCHEMA, profileId='author-activation-fixture', authorName='fixture',
            displayName='fixture', season='s0', mode='nightmare', baseDependency='base', layers=[row],
            identityAuthority=False, structureValidationAuthority=False, crossFloorProjectionAllowed=False,
            currentFrameGeometryFitRequired=True)
        members = {'entry.json':_encoded(document),
            'route.png':cv2.imencode('.png', np.full((3,5,4),(40,30,60,255),np.uint8))[1].tobytes()}
        manifest = dict(schema='xingtu-resource-package', formatVersion=1, packageId='fixture-valid-display',
            resourceVersion='1', clientCompatibility=dict(resourceProtocol=1, requiredCapabilities=[]),
            components=[dict(componentId='display', type='display', schemaVersion=1,
                contexts=[dict(season='s0',mode='nightmare')], entrypoint='entry.json',
                dependencies={'base':_wire(self.refs['base'])})],
            files=[dict(componentId='display', path=path, bytes=len(raw), sha256=hashlib.sha256(raw).hexdigest())
                for path,raw in members.items()])
        path = self.install_root/'valid-display.zip'
        with zipfile.ZipFile(path, 'x', compression=zipfile.ZIP_DEFLATED) as archive:
            archive.writestr('manifest.json', _encoded(manifest))
            for name,raw in members.items():
                archive.writestr('data/display/'+name, raw)
        return save_archive(path, self.root).component_refs[0]

    def test_activation_updates_only_target_roles_and_preserves_other_selection(self):
        groups = self._inert_component('reviewed', 'reviewed-group-registry')
        display = self._valid_display_component()
        self.resolver = ResourceResolver(
            self.root, supported_capabilities=SUPPORTED_CAPABILITIES,
            project_registry_refs=(groups,))
        self.seed_catalog(groups=groups, displays=(display,))
        before = self.resolver.snapshot()

        with resource_runtime_lease(self.root):
            result = self.activate()

        after = self.resolver.snapshot()
        self.assertEqual(result['status'], 'catalog-activated')
        self.assertEqual(after.contexts['s0:nightmare']['recognitionBase'], self.refs['base'])
        self.assertEqual(after.contexts['s0:nightmare']['controls'], self.refs['controls'])
        self.assertEqual(after.contexts['s0:nightmare']['reviewedGroups'], groups)
        self.assertEqual(after.contexts['s1:difficult'], before.contexts['s1:difficult'])
        self.assertEqual(after.displays, (display,))
        self.assertNotEqual(after.generation, before.generation)

    def test_metadata_only_display_cannot_become_active_with_its_context(self):
        display = self._inert_component('display', 'display')
        self.seed_catalog(displays=(display,))
        before = (self.root/'catalog.json').read_bytes()
        with resource_runtime_lease(self.root):
            with self.assertRaisesRegex(files.ImportRejected, 'schema-invalid'):
                self.activate()
        self.assertEqual((self.root/'catalog.json').read_bytes(), before)

    def test_identical_selection_is_semantically_checked_without_catalog_or_engine_invalidation(self):
        with resource_runtime_lease(self.root):
            first = self.activate()
            self.assertEqual(first['status'], 'catalog-activated')
            factory = build_imported_engine_factories(self.resolver, self.paths)
            engine = factory.get_engine('nightmare')
            catalog_path = self.root / 'catalog.json'
            original_bytes = catalog_path.read_bytes()

            result = self.activate()

            self.assertEqual(result['status'], 'already-active')
            self.assertEqual(catalog_path.read_bytes(), original_bytes)
            self.assertIs(factory.get_engine('nightmare'), engine)

    def test_semantically_wrong_controls_package_keeps_previous_catalog(self):
        bad_refs = self.save_revision('wrong-controls-v2', wrong_control_floor=True)
        with resource_runtime_lease(self.root):
            self.activate()
            original_bytes = (self.root / 'catalog.json').read_bytes()

            with self.assertRaisesRegex(ValueError, 'original-controls-floor-context-mismatch'):
                self.activate(bad_refs)

            self.assertEqual((self.root / 'catalog.json').read_bytes(), original_bytes)

    def test_cancel_after_catalog_publication_recovers_previous_catalog(self):
        new_refs = self.save_revision('cancel-target-v2')
        with resource_runtime_lease(self.root):
            self.activate()
            catalog_path = self.root / 'catalog.json'
            original_bytes = catalog_path.read_bytes()
            cancelled = [False]
            real_activate = activate_catalog_locked

            def cancel_after_publish(root, document, **kwargs):
                def stage(name):
                    if name == 'catalog-published':
                        cancelled[0] = True
                return real_activate(root, document, on_stage=stage, **kwargs)

            with patch.object(imported_activation.publication, 'activate_catalog_locked',
                              side_effect=cancel_after_publish):
                with self.assertRaisesRegex(ValueError, 'cancelled'):
                    self.activate(new_refs, cancelled=lambda: cancelled[0])

            self.assertEqual(catalog_path.read_bytes(), original_bytes)
            self.assertFalse((self.root / 'transactions' / 'pending-catalog.json').exists())

    def test_stage_exception_after_retaining_old_catalog_uses_transaction_recovery(self):
        new_refs = self.save_revision('stage-failure-v2')
        with resource_runtime_lease(self.root):
            self.activate()
            catalog_path = self.root / 'catalog.json'
            original_bytes = catalog_path.read_bytes()
            real_activate = activate_catalog_locked

            def fail_after_retention(root, document, **kwargs):
                def stage(name):
                    if name == 'previous-retained':
                        raise OSError('fixture-stage-failure')
                return real_activate(root, document, on_stage=stage, **kwargs)

            with patch.object(imported_activation.publication, 'activate_catalog_locked',
                              side_effect=fail_after_retention):
                with self.assertRaisesRegex(OSError, 'fixture-stage-failure'):
                    self.activate(new_refs)

            self.assertEqual(catalog_path.read_bytes(), original_bytes)
            self.assertFalse((self.root / 'transactions' / 'pending-catalog.json').exists())

    def test_catalog_change_between_snapshot_and_publish_is_not_overwritten(self):
        new_refs = self.save_revision('concurrent-target-v2')
        with resource_runtime_lease(self.root):
            self.activate()
            catalog_path = self.root / 'catalog.json'
            competing = json.loads(catalog_path.read_bytes())
            competing['generation'] = str(uuid.uuid4())
            competing_bytes = _encoded(competing)
            real_activate = activate_catalog_locked

            def change_catalog_then_publish(root, document, **kwargs):
                catalog_path.write_bytes(competing_bytes)
                return real_activate(root, document, **kwargs)

            with patch.object(imported_activation.publication, 'activate_catalog_locked',
                              side_effect=change_catalog_then_publish):
                with self.assertRaisesRegex(ResourceCatalogError, 'changed-reinspect-required'):
                    self.activate(new_refs)

            self.assertEqual(catalog_path.read_bytes(), competing_bytes)

    def test_unknown_context_is_explicitly_unsupported(self):
        with self.assertRaises(ResourceCatalogError) as caught:
            self.activate(season='s2', mode='difficult')
        self.assertEqual(caught.exception.code, 'native-base-context-unsupported')
        self.assertFalse((self.root / 'catalog.json').exists())

    def test_idempotent_selection_rejects_catalog_change_during_validation(self):
        self.activate()
        catalog_path = self.root / 'catalog.json'
        competing = json.loads(catalog_path.read_bytes())
        competing['generation'] = str(uuid.uuid4())
        competing_bytes = _encoded(competing)
        real_build = imported_activation.build_native_engine
        def replace_catalog(*args, **kwargs):
            engine = real_build(*args, **kwargs)
            catalog_path.write_bytes(competing_bytes)
            return engine
        with patch.object(imported_activation, 'build_native_engine', side_effect=replace_catalog):
            with self.assertRaisesRegex(ResourceCatalogError, 'superseded'):
                self.activate()
        self.assertEqual(catalog_path.read_bytes(), competing_bytes)


if __name__ == '__main__':
    unittest.main()
