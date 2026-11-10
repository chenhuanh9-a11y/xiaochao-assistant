"""Catalog selection/expiry using real files, not recognition qualification."""
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
import uuid
from unittest.mock import patch

from imported_resource_catalog import CatalogSnapshot, ResourceCatalogError, ResourceResolver, SCHEMA
from imported_resource_packages import ComponentRef, PackageView


def encoded(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True).encode('utf-8')


class ImportedCatalogTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.folder = Path(self.temp.name).resolve()
        self.root = self.folder / 'resources'

    def catalog(self, contexts=None, displays=None):
        value = dict(schema=SCHEMA, generation=uuid.uuid4().hex,
                     contexts=contexts or {}, displays=displays or [])
        self.root.mkdir(parents=True, exist_ok=True)
        (self.root / 'catalog.json').write_bytes(encoded(value))
        return value

    def component(self, name, kind, *, season='s0', mode='nightmare', provenance=None, capabilities=()):
        # Only inventory/selection are under test; {} is not a usable map.
        data = b'{}'
        row = dict(componentId=name, path='entry.json', bytes=len(data),
                   sha256=hashlib.sha256(data).hexdigest())
        manifest = dict(schema='xingtu-resource-package', formatVersion=1,
                        packageId='fixture-' + name, resourceVersion='1',
                        clientCompatibility=dict(resourceProtocol=1, requiredCapabilities=list(capabilities)),
                        components=[dict(componentId=name, type=kind, schemaVersion=1,
                                         contexts=[dict(season=season, mode=mode)],
                                         entrypoint='entry.json', dependencies={})],
                        files=[row], provenance=provenance or {})
        raw = encoded(manifest)
        folder = self.root / 'packages' / manifest['packageId'] / '1'
        file = folder / 'data' / name / 'entry.json'
        file.parent.mkdir(parents=True)
        file.write_bytes(data)
        (folder / 'manifest.json').write_bytes(raw)
        return dict(packageId=manifest['packageId'], resourceVersion='1', componentId=name,
                    manifestSha256=hashlib.sha256(raw).hexdigest())

    @staticmethod
    def context(base=None, controls=None, groups=None):
        return dict(recognitionBase=base, controls=controls, reviewedGroups=groups)

    def test_absent_catalog_is_empty_without_creating_default_resources(self):
        before = list(self.folder.iterdir())
        snapshot = CatalogSnapshot(self.root)
        self.assertIsNone(snapshot.generation)
        for season in ('s0', 's1'):
            for mode in ('difficult', 'nightmare'):
                context = snapshot.resolve_context(season, mode)
                self.assertFalse(context.ready_for_validation)
                self.assertIsNone(context.base)
                self.assertEqual(context.gaps, ('recognition-base-missing', 'controls-profile-missing'))
        self.assertEqual(list(self.folder.iterdir()), before)

    def test_real_component_roots_do_not_use_installed_internal_tree(self):
        base = self.component('base', 'recognition-base')
        controls = self.component('controls', 'controls-profile')
        value = self.catalog({'s0:nightmare': self.context(base, controls)})
        snapshot = CatalogSnapshot(self.root)
        context = snapshot.resolve_context('s0', 'nightmare')
        self.assertTrue(context.ready_for_validation)  # Still not map/pose authority.
        self.assertEqual(context.generation, value['generation'])
        self.assertEqual(context.base.root, self.root / 'packages/fixture-base/1/data/base')
        self.assertEqual(context.base.read('entry.json'), b'{}')
        self.assertEqual(snapshot.read(context.base.file_ref('entry.json')), b'{}')
        self.assertFalse((self.root / '_internal').exists())
        self.assertFalse(snapshot.resolve_context('s1', 'nightmare').ready_for_validation)

    def test_missing_controls_does_not_grant_full_context_readiness(self):
        base = self.component('base', 'recognition-base')
        self.catalog({'s0:nightmare': self.context(base)})
        context = CatalogSnapshot(self.root).resolve_context('s0', 'nightmare')
        self.assertIsNotNone(context.base)
        self.assertFalse(context.ready_for_validation)
        self.assertEqual(context.gaps, ('controls-profile-missing',))

    def test_component_wrong_context_and_damage_remain_unavailable(self):
        base = self.component('base', 'recognition-base', mode='difficult')
        controls = self.component('controls', 'controls-profile')
        self.catalog({'s0:nightmare': self.context(base, controls)})
        context = CatalogSnapshot(self.root).resolve_context('s0', 'nightmare')
        self.assertIsNone(context.base)
        self.assertIsNotNone(context.controls)
        self.assertIn('recognition-base-unavailable', context.gaps)
        self.assertIn(('recognition-base', 'resource-component-context-mismatch'), context.issues)

    def test_missing_component_or_entrypoint_reports_unavailable_before_provider(self):
        base = self.component('base', 'recognition-base')
        controls = self.component('controls', 'controls-profile')
        self.catalog({'s0:nightmare': self.context(base, controls)})
        entry = self.root / 'packages/fixture-base/1/data/base/entry.json'
        entry.unlink()
        for damage in ('missing-entry', 'missing-component', 'directory-entry'):
            with self.subTest(damage=damage):
                if damage == 'missing-component':
                    entry.parent.rmdir()
                elif damage == 'directory-entry':
                    entry.mkdir(parents=True)
                context = ResourceResolver(self.root).resolve_context('s0', 'nightmare')
                self.assertFalse(context.ready_for_validation)
                self.assertIsNone(context.base)
                self.assertIsNotNone(context.controls)
                self.assertIn('recognition-base-unavailable', context.gaps)

    def test_normal_package_cannot_self_declare_reviewed_group_authority(self):
        groups = self.component('groups', 'reviewed-group-registry', provenance=dict(reviewed=True))
        self.catalog({'s0:nightmare': self.context(groups=groups)})
        snapshot = CatalogSnapshot(self.root)
        context = snapshot.resolve_context('s0', 'nightmare')
        self.assertIsNone(context.groups)
        self.assertIn('reviewed-group-registry-untrusted', context.optional_gaps)
        # The caller's explicit source qualification is distinct from manifest data.
        ref = ComponentRef(package_id=groups['packageId'], resource_version='1', component_id='groups',
                           manifest_sha256=groups['manifestSha256'])
        qualified = CatalogSnapshot(self.root, project_registry_refs=(ref,))
        self.assertIsNotNone(qualified.resolve_context('s0', 'nightmare').groups)

    def test_required_algorithm_capability_comes_from_program_not_package(self):
        base = self.component('base', 'recognition-base', capabilities=('typed-structure-v1',))
        controls = self.component('controls', 'controls-profile')
        self.catalog({'s0:nightmare': self.context(base, controls)})
        context = CatalogSnapshot(self.root).resolve_context('s0', 'nightmare')
        self.assertFalse(context.ready_for_validation)
        self.assertIn(('recognition-base', 'resource-client-capability-unsupported'), context.issues)
        qualified = CatalogSnapshot(self.root, supported_capabilities=('typed-structure-v1',))
        self.assertTrue(qualified.resolve_context('s0', 'nightmare').ready_for_validation)

    def test_replacing_catalog_with_same_bytes_revokes_old_snapshot(self):
        self.catalog()
        snapshot = CatalogSnapshot(self.root)
        path = self.root / 'catalog.json'
        raw = path.read_bytes()
        path.rename(self.root / 'retained-catalog.json')
        path.write_bytes(raw)
        with self.assertRaisesRegex(ResourceCatalogError, 'superseded'):
            snapshot.resolve_context('s0', 'nightmare')

    def test_first_activation_revokes_previously_empty_snapshot(self):
        snapshot = CatalogSnapshot(self.root)
        self.catalog()
        with self.assertRaisesRegex(ResourceCatalogError, 'superseded'):
            snapshot.assert_current()

    def test_resolver_context_and_views_reject_catalog_replaced_after_resolution(self):
        base = self.component('base', 'recognition-base')
        controls = self.component('controls', 'controls-profile')
        self.catalog({'s0:nightmare': self.context(base, controls)})
        context = ResourceResolver(self.root).resolve_context('s0', 'nightmare')
        self.assertTrue(context.ready_for_validation)
        self.assertEqual(context.base.read('entry.json'), b'{}')
        self.catalog()
        for operation in (context.assert_current, lambda: context.ready_for_validation,
                          lambda: context.base.root, lambda: context.base.path('entry.json'),
                          lambda: context.base.read('entry.json'), lambda: context.base.component):
            with self.subTest(operation=operation):
                with self.assertRaisesRegex(ResourceCatalogError, 'superseded'):
                    operation()

    def test_catalog_changed_during_actual_payload_read_rejects_result(self):
        import resource_snapshot_io
        base = self.component('base', 'recognition-base')
        controls = self.component('controls', 'controls-profile')
        self.catalog({'s0:nightmare': self.context(base, controls)})
        context = ResourceResolver(self.root).resolve_context('s0', 'nightmare')
        original = resource_snapshot_io.read_stable_file
        def read_then_activate(path, *args, **kwargs):
            result = original(path, *args, **kwargs)
            if path.name == 'entry.json':
                self.catalog()
            return result
        with patch.object(resource_snapshot_io, 'read_stable_file', side_effect=read_then_activate):
            with self.assertRaisesRegex(ResourceCatalogError, 'superseded'):
                context.base.read('entry.json')

    def test_new_generation_does_not_silently_use_old_base_dependency(self):
        old = self.component('old', 'recognition-base')
        current = self.component('current', 'recognition-base')
        controls = self.component('controls', 'controls-profile')
        self.catalog({'s0:nightmare': self.context(current, controls)})
        old_ref = ComponentRef(package_id=old['packageId'], resource_version='1', component_id='old',
                               manifest_sha256=old['manifestSha256'])
        historical = PackageView(self.root, old_ref)
        self.assertEqual(historical.read('entry.json'), b'{}')
        with self.assertRaisesRegex(ResourceCatalogError, 'not-selected'):
            ResourceResolver(self.root).snapshot().read(historical.file_ref('entry.json'))

    def test_unknown_retired_context_and_invalid_catalog_fail_explicitly(self):
        self.catalog({'s1:difficulty2': self.context()})
        with self.assertRaisesRegex(ResourceCatalogError, 'context-invalid'):
            CatalogSnapshot(self.root)
        self.catalog()
        with self.assertRaisesRegex(ResourceCatalogError, 'context-unsupported'):
            CatalogSnapshot(self.root).resolve_context('s0', 'normal')
        (self.root / 'catalog.json').write_bytes(encoded(dict(schema='old')))
        with self.assertRaisesRegex(ResourceCatalogError, 'format-unsupported'):
            CatalogSnapshot(self.root)


if __name__ == '__main__':
    unittest.main()
