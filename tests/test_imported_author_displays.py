"""Actual imported factory -> author pixels -> reference bridge consumers.

ZIP fixtures are synthetic resources, not screenshot/game acceptance. Only the
non-unit canonical basis test replaces a mathematical adapter return value.
"""
from copy import deepcopy
from unittest.mock import patch
from uuid import uuid4

import cv2
import numpy as np

from client_imported_providers import SUPPORTED_CAPABILITIES, build_imported_engine_factories
from client_paths import RuntimePaths
from client_route_preferences import prepare_display_layer
from imported_author_displays import ImportedAuthorDisplays
from imported_resource_catalog import ResourceCatalogError, ResourceResolver
from test_imported_author_display import _AuthorDisplayFixture, encoded, image_bytes


class ImportedAuthorDisplaysTests(_AuthorDisplayFixture):
    def factory(self, views):
        self.catalog_document = dict(schema='xingtu-resource-catalog-v1', generation=str(uuid4()),
            contexts={'s1:difficult':dict(recognitionBase=self.wire(self.refs['base']),
                controls=self.wire(self.refs['controls']), reviewedGroups=None)},
            displays=[self.wire(view.component_ref) for view in views])
        self.catalog_path = self.resources/'catalog.json'
        self.catalog_path.write_bytes(encoded(self.catalog_document))
        resolver = ResourceResolver(self.resources, supported_capabilities=SUPPORTED_CAPABILITIES)
        factories = build_imported_engine_factories(resolver, RuntimePaths(self.root, False))
        engine = factories._get('difficult', season='s1')
        return factories, engine

    def supersede_catalog(self):
        self.catalog_document['generation'] = str(uuid4())
        self.catalog_path.write_bytes(encoded(self.catalog_document))

    def assert_structure(self, engine, floor='-4'):
        mid = self.source.ids[0]
        result = prepare_display_layer(engine, mid, floor, 'author-fixture')
        reference = engine.qt_shared_provider('shared-structure', mid, floor)
        self.assertEqual(result['displayKind'], 'shared-structure')
        self.assertEqual(result['resourceIdentity']['path'], str(reference.path))
        self.assertEqual(result['resourceIdentity']['binding']['floor'], floor)
        np.testing.assert_array_equal(result['matrixDisplayToReference'], np.eye(3))
        np.testing.assert_array_equal(result['pixels'][...,3] != 0, reference.clean_mask != 0)
        return result

    def test_default_factory_preserves_identity_and_qualification_while_displaying_png_and_jpeg(self):
        def jpeg(document):
            document['layers'].append(self.layer('-3', 'images/route.jpg'))
        view, document, _ = self.package(jpeg, payloads={'images/route.jpg':image_bytes('JPEG')})
        original_maps = deepcopy(self.base.adapters.maps)
        factories, engine = self.factory([view])
        optional, base = engine.qt_optional_displays, engine.qt_native_base
        mid = self.source.ids[0]
        self.assertIs(engine.pack, base.pack)
        self.assertIs(engine.entry_shared_reference_provider, engine.qt_shared_provider)
        self.assertEqual(tuple(engine.identity_floors), tuple(self.source.floors))
        self.assertEqual(base.adapters.maps, original_maps)
        for floor in ('-4', '-3'):
            result = prepare_display_layer(engine, mid, floor, 'author-fixture')
            self.assertEqual(result['displayKind'], 'author-route')
            self.assertEqual(result['routeProfileId'], 'author-fixture')
            self.assertEqual(result['pixels'].shape, (3,5,4))
            self.assertFalse(result['pixels'].flags.writeable)
            np.testing.assert_allclose(result['matrixDisplayToReference'], document['layers'][0]['matrixDisplayToReference'])
            row = optional.binding('author-fixture', mid, floor)
            self.assertEqual(row['status'], 'user-provided-unreviewed')
            self.assertIs(optional.pixels(row['path'], 'minimap'), result['pixels'])
        self.assertEqual(optional.pixels(optional.binding('author-fixture', mid, '-4')['path'], 'full-reference')[1,2,3], 0)
        report = factories.capabilities('s1')['difficult']
        self.assertTrue(report['dataReady'])
        self.assertTrue(report['rankReady'])
        self.assertFalse(report['liveReady'])
        self.assertFalse(engine.qt_native_context.structure_candidate_capability['candidateReady'])
        self.assertFalse(engine.memory_store.sample_validator(np.zeros((8,8,3), np.uint8)))
        self.assertIsNone(engine.memory_store.portable_seed_path)
        self.assertFalse(engine.memory_store.root.exists())
        self.assert_structure(engine, '-2')
        # Warm decoded pixels never revive damaged author bytes.
        view.path('images/route.png').write_bytes(b'damaged PNG')
        self.assert_structure(engine)
        self.assertTrue(factories._get('difficult', season='s1').qt_rank_ready)

    def test_canonical_composition_uses_reference_basis_without_changing_base_adapter(self):
        view, document, _ = self.package()
        base_adapter = self.base.adapters
        source = np.array([[2.,0,7],[0,3.,-5],[0,0,1.]])
        original = deepcopy(base_adapter.maps)
        actual_getter = type(base_adapter).matrix_profile_to_canonical

        def matrix(adapter, profile, map_id, floor):
            return source if profile == 'shared-structure' else actual_getter(adapter, profile, map_id, floor)

        # This replaces only a canonical coordinate basis, never a solver or
        # reference provider. Native fixtures currently use an identity basis.
        with patch.object(type(base_adapter), 'matrix_profile_to_canonical', new=matrix):
            optional = ImportedAuthorDisplays([view], self.base, self.provider)
            result = optional.adapters.matrix_profile_to_canonical('author-fixture', self.source.ids[0], '-4')
            expected = np.asarray(document['layers'][0]['matrixDisplayToReference'])
            np.testing.assert_allclose(result, source @ expected)
            np.testing.assert_allclose(np.linalg.inv(source) @ result, expected)
        self.assertEqual(base_adapter.maps, original)
        self.assertNotIn('author-fixture', base_adapter.profiles)

    def test_optional_failures_conflicts_and_manifest_replacement_leave_base_ready(self):
        view, _, _ = self.package()
        other, _, _ = self.package()
        factories, engine = self.factory([view, other])
        self.assert_structure(engine)
        self.assertIn('display-profile-conflict', engine.qt_resource_inventory['optionalResourceGaps'])
        self.assertTrue(engine.qt_rank_ready)
        self.assertTrue(factories.capabilities('s1')['difficult']['dataReady'])
        # Start a fresh selected generation with one author, then replace only
        # its physical manifest, retaining identical bytes and component ref.
        _, engine = self.factory([view])
        prepare_display_layer(engine, self.source.ids[0], '-4', 'author-fixture')
        manifest = view.root.parent.parent/'manifest.json'
        replacement = manifest.with_suffix('.replacement')
        replacement.write_bytes(manifest.read_bytes())
        replacement.replace(manifest)
        self.assert_structure(engine)
        self.assertTrue(engine.qt_rank_ready)
        engine.qt_assert_resource_current()

    def test_decode_time_image_damage_falls_back_but_catalog_change_rejects_all_returns(self):
        view, _, _ = self.package()
        _, engine = self.factory([view])
        decode = cv2.imdecode
        path = view.path('images/route.png')

        def damage(data, flags):
            pixels = decode(data, flags)
            path.write_bytes(b'changed while decoding')
            return pixels

        with patch('imported_author_displays.cv2.imdecode', side_effect=damage):
            self.assert_structure(engine)
        self.assertTrue(engine.qt_rank_ready)
        path.write_bytes(image_bytes())

        def change_catalog(data, flags):
            pixels = decode(data, flags)
            self.supersede_catalog()
            return pixels

        with patch('imported_author_displays.cv2.imdecode', side_effect=change_catalog):
            with self.assertRaises(ResourceCatalogError):
                prepare_display_layer(engine, self.source.ids[0], '-4', 'author-fixture')

        # Exercise the existing custom early return too: a complete-looking
        # layer cannot escape the final catalog check after a late change.
        _, engine = self.factory([view])
        def custom(*args):
            self.supersede_catalog()
            return dict(routeProfileId='shared-structure')
        engine.qt_custom_display = custom
        with self.assertRaises(ResourceCatalogError):
            prepare_display_layer(engine, self.source.ids[0], '-4', 'custom:fixture')

    def test_owned_optional_without_binding_never_reads_an_unregistered_catalog_path(self):
        view, _, _ = self.package()
        _, engine = self.factory([view])
        optional = engine.qt_optional_displays
        # Local seam fault: retain a catalog profile but remove its binding.
        # No image reader or shared structure provider is replaced.
        optional.bindings.clear()
        with patch('nightmare_candidate.imread_unicode', side_effect=AssertionError('unbounded optional read')):
            self.assert_structure(engine)

    def test_catalog_change_while_waiting_for_cache_publication_rejects_direct_pixels(self):
        view, _, _ = self.package()
        _, engine = self.factory([view])
        optional = engine.qt_optional_displays
        path = optional.binding('author-fixture', self.source.ids[0], '-4')['path']
        lock = optional._lock
        owner = self

        class CatalogChangingLock:
            entries = 0

            def __enter__(self):
                self.entries += 1
                if self.entries == 2:
                    # Model catalog publication while the pixel worker waits
                    # for its cache lock, after its last payload read/check.
                    owner.supersede_catalog()
                return lock.__enter__()

            def __exit__(self, *args):
                return lock.__exit__(*args)

        optional._lock = CatalogChangingLock()
        with self.assertRaises(ResourceCatalogError):
            optional.pixels(path, 'full-reference')
