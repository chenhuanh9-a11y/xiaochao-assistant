"""Actual archive/selection/decoder lifecycle across compatible base versions."""
import numpy as np

import client_imported_activation as activation
from client_imported_activation_plan import plan_context_activation
from client_imported_browse import load_imported_browse_context
from imported_author_display import decode_author_display
from imported_author_displays import ImportedAuthorDisplays
from imported_resource_packages import ComponentRef
from imported_resource_catalog import ResourceCatalogError, SCHEMA
from imported_resource_removal import inspect_saved_version_removal, remove_saved_version
from imported_resource_selection import inspect_selection
from resource_transaction import resource_runtime_lease
from original_qt_resource_fixtures import save_s1_base_revision
from test_client_imported_author_selection import _AuthorSelectionFixture
import star_map_import as files


class AuthorBaseCompatibilityTests(_AuthorSelectionFixture):
    def declared_other_version(self):
        ref = self.refs['base']
        return dict(packageId=ref.package_id, resourceVersion='not-installed',
                    componentId=ref.component_id, manifestSha256='a'*64)

    def revise(self, *, snapshot=None, source_kind=None):
        return save_s1_base_revision(self.source.output, self.root/'base-v2.zip',
                                     self.resources, 'v2', snapshot=snapshot, source_kind=source_kind)

    def switch(self, refs, **kwargs):
        with resource_runtime_lease(self.resources):
            return activation.activate_context(self.resolver(), self.paths, refs['base'], refs['controls'],
                                               season='s1', mode='difficult', **kwargs)

    def test_uninstalled_declared_version_imports_selects_browses_and_consumes_current_base(self):
        view, document, members = self.package(dependency=self.declared_other_version())
        self.seed()
        before_base = self.resolver().snapshot().contexts['s1:difficult']['recognitionBase']
        self.assertEqual(self.activate(view)['status'], 'catalog-activated')
        snapshot = self.resolver().snapshot()
        self.assertEqual(snapshot.contexts['s1:difficult']['recognitionBase'], before_base)
        self.assertFalse((self.resources/'packages'/before_base.package_id/'not-installed').exists())
        decoded = decode_author_display(view, self.base, self.provider)
        self.assertEqual(decoded.base_ref, before_base)
        self.assertEqual(decoded.image_bytes(self.source.ids[0], '-4'), members['images/route.png'])
        consumer = ImportedAuthorDisplays([view], self.base, self.provider)
        layer = decoded.layers[0]
        binding = consumer.binding(decoded.profile_id, layer.map_id, layer.floor)
        expected = self.base.adapters.matrix_profile_to_canonical('shared-structure',layer.map_id,layer.floor)
        expected = expected @ np.asarray(document['layers'][0]['matrixDisplayToReference'])
        np.testing.assert_array_equal(binding['matrix'], expected)
        self.assertEqual(consumer.pixels(decoded.image_path(layer.map_id,layer.floor),'full-reference').shape, (3,5,4))
        browse = load_imported_browse_context(self.resolver(), 'difficult', season='s1')
        self.assertIn('author-fixture', [profile.profile_id for profile in browse.profiles])
        document = dict(schema=SCHEMA, generation=snapshot.generation,
                        contexts={key:{role:self.wire(ref) if ref else None for role,ref in row.items()}
                                  for key,row in snapshot.contexts.items()}, displays=[self.wire(view.component_ref)])
        inspected = inspect_selection(self.resources, document, supported_capabilities=self.resolver().supported_capabilities)
        missing = self.declared_other_version()
        self.assertNotIn(ComponentRef(missing['packageId'], missing['resourceVersion'],
                                     missing['componentId'], missing['manifestSha256']), inspected.views)

    def test_compatible_base_upgrade_keeps_author_without_replacement_dialog(self):
        view, _, _ = self.package()
        self.seed(displays=(view.component_ref,))
        refs = self.revise()
        plan = plan_context_activation(self.resolver(), refs['base'], refs['controls'],season='s1',mode='difficult')
        self.assertEqual(plan.rows, ())
        self.assertEqual(self.switch(refs)['status'], 'catalog-activated')
        snapshot = self.resolver().snapshot()
        self.assertEqual(snapshot.displays, (view.component_ref,))
        self.assertEqual(snapshot.contexts['s1:difficult']['recognitionBase'], refs['base'])
        browse = load_imported_browse_context(self.resolver(), 'difficult',season='s1')
        self.assertIn('author-fixture', [profile.profile_id for profile in browse.profiles])

    def test_changed_reference_snapshot_still_requires_explicit_stop_and_commit_rejects(self):
        view, _, _ = self.package()
        self.seed(displays=(view.component_ref,))
        refs = self.revise(snapshot='fixture-base-s1-v2')
        before = (self.resources/'catalog.json').read_bytes()
        plan = plan_context_activation(self.resolver(), refs['base'], refs['controls'],season='s1',mode='difficult')
        self.assertEqual(len(plan.rows), 1)
        self.assertEqual([choice.display_ref for choice in plan.rows[0].choices], [None])
        with self.assertRaisesRegex(files.ImportRejected, 'reference-binding-mismatch:baseSnapshot'):
            self.switch(refs)
        self.assertEqual((self.resources/'catalog.json').read_bytes(), before)

    def test_cross_version_never_accepts_wrong_structure_floor_or_coordinate_space(self):
        for field, value in [('structureVersionKey','wrong'), ('coordinateSpace','wrong'),
                             ('referenceShapeHW',[4,4]), ('floor','wrong-floor')]:
            with self.subTest(field=field):
                view, _, _ = self.package(lambda d:d['layers'][0].update({field:value}),
                                          dependency=self.declared_other_version())
                with self.assertRaises(files.ImportRejected):
                    decode_author_display(view, self.base, self.provider)
                self.seed()
                before = (self.resources/'catalog.json').read_bytes()
                with self.assertRaises(files.ImportRejected):
                    self.activate(view)
                self.assertEqual((self.resources/'catalog.json').read_bytes(), before)

    def test_reference_role_gap_offers_stop_instead_of_silent_failed_upgrade(self):
        view, _, _ = self.package()
        self.seed(displays=(view.component_ref,))
        refs = self.revise(source_kind='legacy-shared-binary')
        plan = plan_context_activation(self.resolver(),refs['base'],refs['controls'],season='s1',mode='difficult')
        self.assertEqual(len(plan.rows), 1)
        self.assertEqual([choice.display_ref for choice in plan.rows[0].choices], [None])
        before = (self.resources/'catalog.json').read_bytes()
        with self.assertRaisesRegex(ResourceCatalogError, 'resource-display-no-usable-layers'):
            self.switch(refs)
        self.assertEqual((self.resources/'catalog.json').read_bytes(), before)
        self.assertEqual(self.switch(refs,remove_display_refs=(view.component_ref,))['status'], 'catalog-activated')

    def test_removing_unused_old_base_retains_route_and_removing_current_base_stops_it(self):
        view, _, _ = self.package()
        self.seed(displays=(view.component_ref,))
        refs = self.revise()
        self.switch(refs)
        old = self.refs['base']
        resolver = self.resolver()
        plan = inspect_saved_version_removal(resolver, old.package_id,old.resource_version,old.manifest_sha256)
        self.assertEqual(plan['affectedContexts'], [])
        self.assertEqual(plan['removedDisplays'], [])
        with resource_runtime_lease(self.resources):
            remove_saved_version(resolver, plan,
                validate_selection=activation.selected_resource_validator(resolver,self.paths))
        self.assertEqual(resolver.snapshot().displays, (view.component_ref,))
        browse = load_imported_browse_context(resolver,'difficult',season='s1')
        self.assertIn('author-fixture', [profile.profile_id for profile in browse.profiles])
        current = refs['base']
        plan = inspect_saved_version_removal(resolver,current.package_id,current.resource_version,current.manifest_sha256)
        self.assertEqual(plan['affectedContexts'], ['s1:difficult'])
        self.assertEqual(plan['removedDisplays'], [self.wire(view.component_ref)])

    def test_removing_still_selected_old_controls_stops_context_and_compatible_route_together(self):
        view, _, _ = self.package()
        self.seed(displays=(view.component_ref,))
        refs = self.revise()
        self.switch(dict(base=refs['base'],controls=self.refs['controls']))
        resolver = self.resolver()
        old = self.refs['base']
        plan = inspect_saved_version_removal(resolver,old.package_id,old.resource_version,old.manifest_sha256)
        self.assertEqual(plan['affectedContexts'], ['s1:difficult'])
        self.assertEqual(plan['removedDisplays'], [self.wire(view.component_ref)])
        with resource_runtime_lease(self.resources):
            result = remove_saved_version(resolver,plan,
                validate_selection=activation.selected_resource_validator(resolver,self.paths))
        self.assertEqual(result['status'], 'version-removed')
        snapshot = resolver.snapshot()
        self.assertNotIn('s1:difficult',snapshot.contexts)
        self.assertEqual(snapshot.displays, ())
