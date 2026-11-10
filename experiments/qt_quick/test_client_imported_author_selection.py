"""Actual author-version selection; catalog and image consumers are not mocked."""
from io import BytesIO
import json
from unittest.mock import patch
from uuid import uuid4
import zipfile

import numpy as np
from PIL import Image

import client_imported_activation as activation
from client_imported_providers import SUPPORTED_CAPABILITIES, build_imported_engine_factories
from client_paths import RuntimePaths
from client_route_preferences import prepare_display_layer
from imported_author_display import decode_author_display, read_display_identity
from imported_native_base import decode_native_base
from imported_resource_activation import activate_catalog_locked
from imported_resource_archive import save_archive
from imported_resource_catalog import ResourceCatalogError, ResourceResolver, SCHEMA
from imported_resource_packages import PackageView
from imported_resource_selection import inspect_selection
from resource_transaction import resource_runtime_lease, resource_write_lock
import star_map_import as files
from test_imported_author_display import _AuthorDisplayFixture
from test_client_imported_s1_activation import _inert_component


def changed_png():
    output = BytesIO()
    Image.new('RGBA', (5,3), (91,92,93,94)).save(output, format='PNG')
    return output.getvalue()


class _AuthorSelectionFixture(_AuthorDisplayFixture):
    def resource_root(self):
        self.paths = RuntimePaths(self.root, False)
        return self.paths.imported_resource_root

    def resolver(self):
        return ResourceResolver(self.resources, supported_capabilities=SUPPORTED_CAPABILITIES)

    def seed(self, *, displays=(), active_base=True):
        document = dict(schema=SCHEMA, generation=str(uuid4()),
            contexts={
                's0:nightmare':dict(recognitionBase=None, controls=None, reviewedGroups=None),
                's1:difficult':dict(recognitionBase=self.wire(self.refs['base']) if active_base else None,
                    controls=self.wire(self.refs['controls']) if active_base else None, reviewedGroups=None)},
            displays=[self.wire(ref) for ref in displays])
        resolver = self.resolver()
        # The source/base has already passed the actual public decoder in the
        # fixture. Seed only this precondition; each API call below constructs
        # and checks the real engine/provider twice before publishing.
        def inspect(proposed):
            inspect_selection(self.resources, proposed, supported_capabilities=SUPPORTED_CAPABILITIES)
        with resource_write_lock(self.resources):
            activate_catalog_locked(self.resources, document,
                expected_catalog_stamp=resolver.snapshot().catalog_stamp, validate=inspect)

    def activate(self, view, **kwargs):
        with resource_runtime_lease(self.resources):
            return activation.activate_display(self.resolver(), self.paths, view.component_ref, **kwargs)


class ClientImportedAuthorSelectionTests(_AuthorSelectionFixture):
    def test_add_replace_and_repeat_preserve_other_selection_and_show_new_pixels(self):
        dormant = _inert_component(self.resources, 'display', 'display')
        self.seed(displays=[dormant])
        initial_contexts = dict(self.resolver().snapshot().contexts)
        first, _, _ = self.package(package_id='author-versioned')
        with patch('imported_author_display.decode_author_display', wraps=decode_author_display) as decoded:
            outcome = self.activate(first)
        self.assertEqual(outcome['status'], 'catalog-activated')
        self.assertEqual(outcome['context'], 's1:difficult')
        self.assertEqual(outcome['profileId'], 'author-fixture')
        self.assertEqual(decoded.call_count, 2)
        other, _, _ = self.package(package_id='author-other', profile_id='author-other')
        self.activate(other)
        resolver = self.resolver()
        factory = build_imported_engine_factories(resolver, self.paths)
        old_engine = factory.get_engine('difficult', season='s1')
        old_layer = prepare_display_layer(old_engine, self.source.ids[0], '-4', 'author-fixture')
        next_view, document, _ = self.package(package_id='author-versioned', resource_version='v2',
            payloads={'images/route.png':changed_png()})
        self.activate(next_view)
        selected = resolver.snapshot()
        self.assertEqual(selected.displays, (dormant, next_view.component_ref, other.component_ref))
        self.assertEqual(dict(selected.contexts), initial_contexts)
        with self.assertRaises(ResourceCatalogError):
            prepare_display_layer(old_engine, self.source.ids[0], '-4', 'author-fixture')
        engine = factory.get_engine('difficult', season='s1')
        layer = prepare_display_layer(engine, self.source.ids[0], '-4', 'author-fixture')
        self.assertEqual(layer['displayKind'], 'author-route')
        np.testing.assert_array_equal(layer['pixels'], np.full((3,5,4), (93,92,91,94), np.uint8))
        self.assertFalse(np.array_equal(layer['pixels'], old_layer['pixels']))
        np.testing.assert_allclose(layer['matrixDisplayToReference'], document['layers'][0]['matrixDisplayToReference'])
        self.assertEqual(tuple(engine.pack.by_id), tuple(old_engine.pack.by_id))
        self.assertEqual(engine.pack.floors, old_engine.pack.floors)
        self.assertTrue(engine.qt_rank_ready)
        self.assertFalse(engine.qt_live_ready)
        self.assertFalse(engine.memory_store.root.exists())
        catalog_path = self.resources/'catalog.json'
        before = catalog_path.read_bytes()
        with patch('imported_author_display.decode_author_display', wraps=decode_author_display) as decoded:
            repeated = self.activate(next_view)
        self.assertEqual(repeated['status'], 'already-active')
        # One callback rechecks both selected authors in this context.
        self.assertEqual([call.args[0].component_ref for call in decoded.call_args_list],
                         [next_view.component_ref, other.component_ref])
        self.assertEqual(catalog_path.read_bytes(), before)

    def test_old_pixels_can_be_repaired_but_unknown_old_metadata_is_not_inferred(self):
        self.seed()
        first, _, _ = self.package(package_id='author-versioned')
        self.activate(first)
        first.path('images/route.png').write_bytes(b'broken old picture')
        self.assertEqual(read_display_identity(first).profile_id, 'author-fixture')
        second, _, _ = self.package(package_id='author-versioned', resource_version='v2')
        self.activate(second)
        self.assertEqual(self.resolver().snapshot().displays, (second.component_ref,))
        third, _, _ = self.package(package_id='author-versioned', resource_version='v3')
        manifest = second.root.parent.parent/'manifest.json'
        registration = second.path('display.json')
        saved_manifest = manifest.read_bytes()
        catalog_path = self.resources/'catalog.json'
        before = catalog_path.read_bytes()
        manifest.write_bytes(b'{}')
        with self.assertRaisesRegex(ResourceCatalogError, 'previous-display-metadata-unavailable') as failed:
            self.activate(third)
        self.assertEqual(failed.exception.previous_display_ref, second.component_ref)
        self.assertEqual(catalog_path.read_bytes(), before)
        manifest.write_bytes(saved_manifest)
        # Inject the next fault through its known fixture path. The original
        # view correctly becomes stale when manifest metadata is rewritten.
        registration.write_bytes(b'{}')
        with self.assertRaisesRegex(ResourceCatalogError, 'previous-display-metadata-unavailable'):
            self.activate(third)
        self.assertEqual(catalog_path.read_bytes(), before)

    def test_changed_identity_ambiguous_old_versions_and_cross_package_profile_conflict_are_rejected(self):
        self.seed()
        first, _, _ = self.package(package_id='author-versioned')
        self.activate(first)
        before = (self.resources/'catalog.json').read_bytes()
        renamed, _, _ = self.package(package_id='author-versioned', resource_version='renamed', profile_id='author-renamed')
        wrong_context, _, _ = self.package(lambda d:d.update(mode='nightmare'), package_id='author-versioned',
            resource_version='other-mode', context=dict(season='s1',mode='nightmare'))
        for view in (renamed, wrong_context):
            with self.subTest(version=view.component_ref.resource_version):
                with self.assertRaisesRegex(ResourceCatalogError, 'display-identity-mismatch'):
                    self.activate(view)
                self.assertEqual((self.resources/'catalog.json').read_bytes(), before)
        different_package, _, _ = self.package(package_id='another-owner')
        with self.assertRaisesRegex(ResourceCatalogError, 'display-profile-conflict'):
            self.activate(different_package)
        self.assertEqual((self.resources/'catalog.json').read_bytes(), before)
        version2, _, _ = self.package(package_id='author-versioned', resource_version='v2')
        self.seed(displays=[first.component_ref, version2.component_ref])
        before = (self.resources/'catalog.json').read_bytes()
        with self.assertRaisesRegex(ResourceCatalogError, 'display-selection-ambiguous'):
            self.activate(version2)
        self.assertEqual((self.resources/'catalog.json').read_bytes(), before)

    def test_missing_base_and_invalid_new_display_leave_saved_versions_inert(self):
        self.seed(active_base=False)
        view, _, _ = self.package()
        before = (self.resources/'catalog.json').read_bytes()
        with self.assertRaisesRegex(ResourceCatalogError, 'base-context-not-active'):
            self.activate(view)
        self.assertEqual((self.resources/'catalog.json').read_bytes(), before)
        self.seed()
        bad, _, _ = self.package(payloads={'images/route.png':b'not an image'})
        before = (self.resources/'catalog.json').read_bytes()
        with self.assertRaises(files.ImportRejected):
            self.activate(bad)
        self.assertEqual((self.resources/'catalog.json').read_bytes(), before)
        unsafe, _, _ = self.package(lambda document:document.update(identityAuthority=True))
        with self.assertRaisesRegex(files.ImportRejected, 'binding-context-mismatch'):
            self.activate(unsafe)
        wrong_role, _, _ = self.package(dependency=self.wire(self.refs['controls']))
        with self.assertRaisesRegex(files.ImportRejected, 'base-dependency-mismatch'):
            self.activate(wrong_role)
        self.assertEqual((self.resources/'catalog.json').read_bytes(), before)

    def test_a_valid_historical_base_dependency_cannot_select_itself(self):
        self.seed()
        with zipfile.ZipFile(self.source.output) as archive:
            members = {name:archive.read(name) for name in archive.namelist()}
        manifest = json.loads(members['manifest.json'])
        manifest['resourceVersion'] = 'historical-base'
        members['manifest.json'] = json.dumps(manifest).encode('utf-8')
        path = self.root/'historical-base.zip'
        with zipfile.ZipFile(path, 'x', compression=zipfile.ZIP_DEFLATED) as archive:
            for name,raw in members.items():
                archive.writestr(name, raw)
        saved = save_archive(path, self.resources)
        historical_ref = next(ref for ref in saved.component_refs if ref.component_id == 'base')
        historical = decode_native_base(PackageView(self.resources, historical_ref), season='s1', mode='difficult')
        self.assertTrue(historical.ready)
        view, _, _ = self.package(dependency=self.wire(historical_ref))
        before = (self.resources/'catalog.json').read_bytes()
        with self.assertRaisesRegex(ResourceCatalogError, 'dependency-not-selected'):
            self.activate(view)
        self.assertEqual((self.resources/'catalog.json').read_bytes(), before)
        self.assertEqual(self.resolver().snapshot().contexts['s1:difficult']['recognitionBase'], self.refs['base'])

    def test_second_semantic_check_cancel_restores_previous_author_version(self):
        self.seed()
        first, _, _ = self.package(package_id='author-versioned')
        self.activate(first)
        second, _, _ = self.package(package_id='author-versioned', resource_version='v2')
        before = (self.resources/'catalog.json').read_bytes()
        state = dict(calls=0, cancelled=False)

        def decode_then_cancel(*args):
            display = decode_author_display(*args)
            state['calls'] += 1
            if state['calls'] == 2:
                state['cancelled'] = True
            return display

        with patch('imported_author_display.decode_author_display', side_effect=decode_then_cancel):
            with self.assertRaisesRegex(files.ImportRejected, 'import-cancelled'):
                self.activate(second, cancelled=lambda:state['cancelled'])
        self.assertEqual(state['calls'], 2)
        self.assertEqual((self.resources/'catalog.json').read_bytes(), before)
        self.assertFalse((self.resources/'transactions/pending-catalog.json').exists())

    def test_old_header_change_between_checks_rejects_update_without_erasing_the_fault(self):
        self.seed()
        first, _, _ = self.package(package_id='author-versioned')
        self.activate(first)
        registration = first.path('display.json')
        second, _, _ = self.package(package_id='author-versioned', resource_version='v2')
        before = (self.resources/'catalog.json').read_bytes()
        state = dict(calls=0)

        def decode_then_damage_old_header(*args):
            display = decode_author_display(*args)
            state['calls'] += 1
            if state['calls'] == 1:
                registration.write_bytes(b'changed between semantic checks')
            return display

        with patch('imported_author_display.decode_author_display', side_effect=decode_then_damage_old_header):
            with self.assertRaisesRegex(ResourceCatalogError, 'previous-display-metadata-unavailable'):
                self.activate(second)
        self.assertEqual(state['calls'], 1)
        self.assertEqual((self.resources/'catalog.json').read_bytes(), before)
        self.assertEqual(registration.read_bytes(), b'changed between semantic checks')
        self.assertFalse((self.resources/'transactions/pending-catalog.json').exists())
