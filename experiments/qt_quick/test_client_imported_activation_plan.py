"""Real-package coverage for the read-only imported activation planner."""
import hashlib
import json
from unittest import TestCase
from unittest.mock import patch
import zipfile

import client_imported_activation_plan as planning
import imported_author_display
from imported_resource_archive import save_archive
from imported_resource_catalog import ResourceCatalogError
from imported_resource_packages import PackageView
import star_map_import as files
from test_client_imported_author_selection import _AuthorSelectionFixture


class ClientImportedActivationPlanTests(TestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        # Use the existing fixture by composition. Subclassing it here would
        # also collect and rerun its unrelated author-selection test methods.
        _AuthorSelectionFixture.setUpClass()

    @classmethod
    def tearDownClass(cls):
        try:
            _AuthorSelectionFixture.doClassCleanups()
        finally:
            super().tearDownClass()

    def setUp(self):
        self.fixture = _AuthorSelectionFixture('runTest')
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)

    def revision(self, version):
        fixture = self.fixture
        with zipfile.ZipFile(fixture.source.output) as archive:
            members = {name: archive.read(name) for name in archive.namelist()}
        manifest = json.loads(members['manifest.json'])
        manifest['resourceVersion'] = version
        members['manifest.json'] = json.dumps(manifest).encode('utf-8')
        path = fixture.root / (version + '.zip')
        with zipfile.ZipFile(path, 'x', compression=zipfile.ZIP_DEFLATED) as archive:
            for name, raw in members.items():
                archive.writestr(name, raw)
        saved = save_archive(path, fixture.resources)
        return {ref.component_id: ref for ref in saved.component_refs}

    def other_context_display(self):
        """Save a metadata-only, dependency-free author for the inactive context."""
        fixture = self.fixture
        package_id, version, component_id = 'other-context-author', 'v1', 'display'
        entry = 'display.json'
        document = json.dumps(dict(schema=imported_author_display.SCHEMA,
            profileId='author-other-context', season='s1', mode='nightmare')).encode('utf-8')
        manifest = dict(schema='xingtu-resource-package', formatVersion=1,
            packageId=package_id, resourceVersion=version,
            clientCompatibility=dict(resourceProtocol=1, requiredCapabilities=[]),
            components=[dict(componentId=component_id, type='display', schemaVersion=1,
                contexts=[dict(season='s1', mode='nightmare')], entrypoint=entry, dependencies={})],
            files=[dict(componentId=component_id, path=entry, bytes=len(document),
                sha256=hashlib.sha256(document).hexdigest())])
        archive_path = fixture.root / 'other-context.zip'
        with zipfile.ZipFile(archive_path, 'x', compression=zipfile.ZIP_DEFLATED) as archive:
            archive.writestr('manifest.json', json.dumps(manifest).encode('utf-8'))
            archive.writestr('data/' + component_id + '/' + entry, document)
        saved = save_archive(archive_path, fixture.resources)
        return PackageView(fixture.resources, saved.component_refs[0])

    def plan(self, refs):
        return planning.plan_context_activation(
            self.fixture.resolver(), refs['base'], refs['controls'],
            season='s1', mode='difficult')

    def test_b1_d1_to_b2_offers_only_exact_replacement_and_explicit_stop(self):
        fixture = self.fixture
        old, _, _ = fixture.package(package_id='versioned-author')
        fixture.seed(displays=(old.component_ref,))
        proposed = self.revision('base-v2')
        replacement, _, _ = fixture.package(
            package_id='versioned-author', resource_version='v2',
            dependency=fixture.wire(proposed['base']))

        # A broken bitmap is intentionally immaterial to metadata planning.
        replacement.path('images/route.png').write_bytes(b'not an image')
        catalog_before = (fixture.resources / 'catalog.json').read_bytes()
        plan = self.plan(proposed)

        self.assertEqual(plan.base_ref, proposed['base'])
        self.assertEqual(plan.controls_ref, proposed['controls'])
        self.assertEqual(plan.season, 's1')
        self.assertEqual(plan.mode, 'difficult')
        self.assertIsInstance(plan.catalog_stamp, dict)
        self.assertEqual(len(plan.rows), 1)
        row = plan.rows[0]
        self.assertEqual(row.old_ref, old.component_ref)
        self.assertEqual(row.scope, 's1/difficult')
        self.assertEqual(row.label,
            'author-fixture · versioned-author/v1/art')
        self.assertEqual([choice.display_ref for choice in row.choices],
                         [replacement.component_ref, None])
        self.assertIn('保留文件', row.choices[-1].label)
        self.assertEqual((fixture.resources / 'catalog.json').read_bytes(), catalog_before)

    def test_multiple_exact_versions_stay_unselected_and_unrelated_authors_are_excluded(self):
        fixture = self.fixture
        old, _, _ = fixture.package(package_id='versioned-author')
        fixture.seed(displays=(old.component_ref,))
        proposed = self.revision('base-v2')
        matching = []
        for version in ('v2', 'v3'):
            view, _, _ = fixture.package(package_id='versioned-author',
                resource_version=version, dependency=fixture.wire(proposed['base']))
            matching.append(view.component_ref)
        fixture.package(package_id='other-author', resource_version='v2',
            dependency=fixture.wire(proposed['base']))
        fixture.package(package_id='versioned-author', resource_version='v4',
            profile_id='different-profile', dependency=fixture.wire(proposed['base']))
        fixture.package(lambda document: document.update(mode='nightmare'),
            package_id='versioned-author', resource_version='v5',
            context=dict(season='s1', mode='nightmare'),
            dependency=fixture.wire(proposed['base']))
        fixture.package(package_id='versioned-author', resource_version='v6',
            dependency=fixture.wire(fixture.refs['base']))

        row = self.plan(proposed).rows[0]
        self.assertEqual({choice.display_ref for choice in row.choices},
                         {*matching, None})
        self.assertIsNone(next((choice.display_ref for choice in row.choices
                                if choice.display_ref is None), object()))
        self.assertEqual(sum(choice.display_ref is None for choice in row.choices), 1)

    def test_exactly_compatible_and_other_context_authors_are_not_listed(self):
        fixture = self.fixture
        compatible, _, _ = fixture.package(package_id='compatible-author',
            dependency=fixture.wire(fixture.refs['base']))
        other_context = self.other_context_display()
        fixture.seed(displays=(compatible.component_ref, other_context.component_ref))

        plan = self.plan(fixture.refs)
        self.assertEqual(plan.rows, ())

    def test_unreadable_old_header_only_allows_global_stop_without_mutation(self):
        fixture = self.fixture
        old, _, _ = fixture.package(package_id='unknown-author')
        fixture.seed(displays=(old.component_ref,))
        proposed = self.revision('base-v2')
        old_entry = old.path('display.json')
        old_entry.write_bytes(b'{}')
        catalog_before = (fixture.resources / 'catalog.json').read_bytes()
        image_before = old.path('images/route.png').read_bytes()

        plan = self.plan(proposed)

        self.assertEqual(len(plan.rows), 1)
        row = plan.rows[0]
        self.assertEqual(row.scope, '适用模式未知')
        self.assertEqual(len(row.choices), 1)
        self.assertIsNone(row.choices[0].display_ref)
        self.assertEqual(row.label, 'unknown-author / v1 / art')
        self.assertEqual(old_entry.read_bytes(), b'{}')
        self.assertEqual(old.path('images/route.png').read_bytes(), image_before)
        self.assertEqual((fixture.resources / 'catalog.json').read_bytes(), catalog_before)

    def test_cancellation_during_real_header_read_is_not_converted_to_unknown_scope(self):
        fixture = self.fixture
        old, _, _ = fixture.package(package_id='cancel-author')
        fixture.seed(displays=(old.component_ref,))
        proposed = self.revision('base-v2')
        actual = imported_author_display.read_display_identity
        state = {'cancelled': False}

        def read_then_cancel(view):
            identity = actual(view)
            state['cancelled'] = True
            return identity

        with patch.object(imported_author_display, 'read_display_identity',
                          side_effect=read_then_cancel):
            with self.assertRaisesRegex(files.ImportRejected, 'import-cancelled'):
                planning.plan_context_activation(fixture.resolver(), proposed['base'],
                    proposed['controls'], season='s1', mode='difficult',
                    cancelled=lambda: state['cancelled'])

    def test_catalog_change_during_real_header_read_is_rejected(self):
        fixture = self.fixture
        old, _, _ = fixture.package(package_id='racing-author')
        fixture.seed(displays=(old.component_ref,))
        proposed = self.revision('base-v2')
        actual = imported_author_display.read_display_identity
        state = {'changed': False}

        def read_then_change_catalog(view):
            identity = actual(view)
            if not state['changed']:
                state['changed'] = True
                fixture.seed(displays=(old.component_ref,))
            return identity

        with patch.object(imported_author_display, 'read_display_identity',
                          side_effect=read_then_change_catalog):
            with self.assertRaisesRegex(ResourceCatalogError, 'resource-catalog-superseded'):
                self.plan(proposed)


if __name__ == '__main__':
    import unittest
    unittest.main()
