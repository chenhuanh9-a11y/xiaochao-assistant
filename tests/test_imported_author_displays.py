"""Actual imported factory -> author pixels -> reference bridge consumers.

ZIP fixtures are synthetic resources, not screenshot/game acceptance. Only the
non-unit canonical basis test replaces a mathematical adapter return value.
"""
from copy import deepcopy
import ctypes
import os
from pathlib import Path
from unittest.mock import patch
from uuid import uuid4

import cv2
import numpy as np

from client_imported_providers import SUPPORTED_CAPABILITIES, build_imported_engine_factories
from client_paths import RuntimePaths
from client_providers import DisplayResourceChanged
from client_route_preferences import prepare_display_layer
from imported_author_displays import ImportedAuthorDisplays
from imported_resource_catalog import ResourceCatalogError, ResourceResolver
from test_imported_author_display import _AuthorDisplayFixture, encoded, image_bytes


class ImportedAuthorDisplaysTests(_AuthorDisplayFixture):
    def factory(self, views, *, resource_root=None):
        self.catalog_document = dict(schema='xingtu-resource-catalog-v1', generation=str(uuid4()),
            contexts={'s1:difficult':dict(recognitionBase=self.wire(self.refs['base']),
                controls=self.wire(self.refs['controls']), reviewedGroups=None)},
            displays=[self.wire(view.component_ref) for view in views])
        self.catalog_path = self.resources/'catalog.json'
        self.catalog_path.write_bytes(encoded(self.catalog_document))
        resolver = ResourceResolver(self.resources if resource_root is None else resource_root,
                                    supported_capabilities=SUPPORTED_CAPABILITIES)
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

    def short_path_alias(self, path):
        if os.name != 'nt':
            self.skipTest('GetShortPathNameW is Windows-only')
        get_short_path = ctypes.windll.kernel32.GetShortPathNameW
        get_short_path.argtypes = (ctypes.c_wchar_p, ctypes.c_wchar_p, ctypes.c_uint32)
        get_short_path.restype = ctypes.c_uint32
        buffer = ctypes.create_unicode_buffer(32768)
        length = get_short_path(str(path), buffer, len(buffer))
        if not length or length >= len(buffer):
            self.skipTest('GetShortPathNameW could not obtain a real alias for this temp path')
        alias = Path(buffer.value)
        if alias == path:
            self.skipTest('8.3 aliases are unavailable for this temp path on this volume')
        self.assertTrue(alias.samefile(path))
        return alias

    def test_windows_short_root_factory_selects_original_author_pixels(self):
        view, document, members = self.package()
        alias = self.short_path_alias(self.resources)
        _, engine = self.factory([view], resource_root=alias)
        mid, floor = self.source.ids[0], '-4'
        result = prepare_display_layer(engine, mid, floor, 'author-fixture')
        self.assertEqual(result['displayKind'], 'author-route')
        self.assertEqual(result['preferredProfileId'], 'author-fixture')
        self.assertEqual(result['effectiveProfileId'], 'author-fixture')
        self.assertFalse(result['fallback'])
        np.testing.assert_array_equal(result['pixels'], cv2.imdecode(
            np.frombuffer(members['images/route.png'], np.uint8), cv2.IMREAD_UNCHANGED))
        np.testing.assert_array_equal(result['matrixDisplayToReference'],
                                      document['layers'][0]['matrixDisplayToReference'])
        optional = engine.qt_optional_displays
        row = optional.binding('author-fixture', mid, floor)
        self.assertNotEqual(row['path'], row['path'].resolve())
        self.assertTrue(row['path'].samefile(view.path('images/route.png')))
        for path in (row['path'], row['path'].resolve()):
            with self.subTest(path=str(path)):
                self.assertIs(optional.pixels(path, 'aligned-overlay'), result['pixels'])
        self.assertEqual(row['status'], 'user-provided-unreviewed')
        view.path('images/route.png').write_bytes(b'damaged after alias cache admission')
        self.assert_structure(engine)
        with self.assertRaises(ValueError):
            optional.pixels(row['path'].resolve(), 'aligned-overlay')
        view.path('images/route.png').write_bytes(members['images/route.png'])
        self.assertIs(optional.pixels(row['path'].resolve(), 'aligned-overlay'), result['pixels'])
        self.supersede_catalog()
        with self.assertRaises(ResourceCatalogError):
            optional.pixels(row['path'].resolve(), 'aligned-overlay')
        with self.assertRaises(DisplayResourceChanged) as changed:
            prepare_display_layer(engine, mid, floor, 'author-fixture')
        self.assertIsInstance(changed.exception.__cause__, ResourceCatalogError)
        self.assertEqual(changed.exception.__cause__.code, 'resource-catalog-superseded')

    def test_alias_lookup_keeps_components_separate_and_rejects_external_pixels(self):
        view, _, members = self.package()
        other_pixels = np.full((3, 5, 4), (71, 83, 97, 109), np.uint8)
        other, _, _ = self.package(profile_id='author-other', payloads={
            'images/route.png': cv2.imencode('.png', other_pixels)[1].tobytes()})
        foreign, _, _ = self.package(profile_id='author-unselected')
        alias = self.short_path_alias(self.resources)
        _, engine = self.factory([view, other], resource_root=alias)
        optional = engine.qt_optional_displays
        mid, floor = self.source.ids[0], '-4'
        first = optional.binding('author-fixture', mid, floor)
        second = optional.binding('author-other', mid, floor)
        self.assertEqual(first['layer'].image.path, second['layer'].image.path)
        self.assertNotEqual(first['layer'].image, second['layer'].image)
        np.testing.assert_array_equal(optional.pixels(first['path'].resolve(), 'full-reference'),
            cv2.imdecode(np.frombuffer(members['images/route.png'], np.uint8), cv2.IMREAD_UNCHANGED))
        np.testing.assert_array_equal(optional.pixels(second['path'].resolve(), 'full-reference'), other_pixels)
        external = self.root/'route.png'
        external.write_bytes(members['images/route.png'])
        for path in (foreign.path('images/route.png'), external):
            with self.subTest(path=str(path)), self.assertRaisesRegex(
                    ValueError, 'imported-author-display-path-unregistered'):
                optional.pixels(path, 'full-reference')
        # Exercise the reverse spelling too: long-root registration, short
        # input. A different selected catalog still uses real package readers.
        _, engine = self.factory([view, other])
        optional = engine.qt_optional_displays
        for profile in ('author-fixture', 'author-other'):
            row = optional.binding(profile, mid, floor)
            short = self.short_path_alias(row['path'])
            self.assertIs(optional.pixels(short, 'full-reference'),
                          optional.pixels(row['path'], 'full-reference'))

    def test_alias_lookup_rejects_link_into_registered_component(self):
        view, _, _ = self.package()
        _, engine = self.factory([view], resource_root=self.short_path_alias(self.resources))
        link = self.root/'author-link'
        try:
            link.symlink_to(view.root, target_is_directory=True)
        except OSError as error:
            self.skipTest(f'directory symlinks unavailable: {error}')
        with self.assertRaisesRegex(ValueError, 'linked-target'):
            engine.qt_optional_displays.pixels(link/'images/route.png', 'full-reference')

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
        candidate = engine.qt_native_context.structure_candidate_capability
        self.assertTrue(candidate['candidateReady'])
        self.assertEqual(candidate['validationScope'], 'source-image-replay')
        self.assertFalse(candidate['liveValidated'])
        observation = engine.qt_native_context.floor_observation_capability
        self.assertEqual(observation['validationScope'], 'source-image-replay')
        self.assertFalse(observation['liveValidated'])
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

    def test_factory_optional_manifest_replacement_after_resolution_keeps_base_ready(self):
        view, _, _ = self.package()
        prior, _ = self.factory([view])
        prior.invalidate()
        resolver = ResourceResolver(self.resources,supported_capabilities=SUPPORTED_CAPABILITIES)
        factories = build_imported_engine_factories(resolver,RuntimePaths(self.root,False))
        resolve = factories._context_resources
        def replace_after_resolution(*args,**kwargs):
            resources,revision = resolve(*args,**kwargs)
            manifest = view.root.parent.parent/'manifest.json'
            replacement = manifest.with_suffix('.replacement')
            replacement.write_bytes(manifest.read_bytes())
            replacement.replace(manifest)
            return resources,revision
        with patch.object(factories,'_context_resources',replace_after_resolution):
            engine = factories._build('difficult',season='s1')
        self.assertTrue(engine.qt_rank_ready)
        self.assertTrue(engine.qt_display_preparation_failed)
        self.assert_structure(engine)

    def test_factory_optional_damage_after_image_validation_keeps_base_ready(self):
        import imported_author_display as displays
        view, _, _ = self.package()
        prior, _ = self.factory([view])
        prior.invalidate()
        target = view.path('images/route.png')
        resolver = ResourceResolver(self.resources,supported_capabilities=SUPPORTED_CAPABILITIES)
        factories = build_imported_engine_factories(resolver,RuntimePaths(self.root,False))
        validate = displays.validate_display_image
        def change_after_validation(*args,**kwargs):
            result = validate(*args,**kwargs)
            target.write_bytes(b'changed after actual author image validation')
            return result
        with patch.object(displays,'validate_display_image',change_after_validation):
            engine = factories._build('difficult',season='s1')
        self.assertTrue(engine.qt_rank_ready)
        self.assertTrue(engine.qt_display_preparation_failed)
        self.assert_structure(engine)

    def test_factory_optional_decode_cannot_hide_changed_base_or_catalog(self):
        import imported_author_display as displays
        view, _, _ = self.package()
        prior, previous = self.factory([view])
        prior.invalidate()
        base_target = previous.qt_native_base.reader.view.path(previous.qt_native_base.interfaces['identityPack'])
        original_base = base_target.read_bytes()
        validate = displays.validate_display_image
        resolver = ResourceResolver(self.resources,supported_capabilities=SUPPORTED_CAPABILITIES)
        def build_with(change):
            factories = build_imported_engine_factories(resolver,RuntimePaths(self.root,False))
            def changed(*args,**kwargs):
                result = validate(*args,**kwargs)
                change()
                return result
            with patch.object(displays,'validate_display_image',changed):
                return factories._build('difficult',season='s1')
        try:
            with self.assertRaisesRegex(ValueError,'file-integrity-mismatch'):
                build_with(lambda:base_target.write_bytes(b'X'*len(original_base)))
        finally:
            base_target.write_bytes(original_base)
        with self.assertRaisesRegex(ValueError,'resource-catalog-superseded'):
            build_with(self.supersede_catalog)

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
            with self.assertRaises(DisplayResourceChanged) as changed:
                prepare_display_layer(engine, self.source.ids[0], '-4', 'author-fixture')
        self.assertIsInstance(changed.exception.__cause__, ResourceCatalogError)
        self.assertEqual(changed.exception.__cause__.code, 'resource-catalog-superseded')

        # Exercise the existing custom early return too: a complete-looking
        # layer cannot escape the final catalog check after a late change.
        _, engine = self.factory([view])
        def custom(*args):
            self.supersede_catalog()
            return dict(routeProfileId='shared-structure')
        engine.qt_custom_display = custom
        with self.assertRaises(DisplayResourceChanged) as changed:
            prepare_display_layer(engine, self.source.ids[0], '-4', 'custom:fixture')
        self.assertIsInstance(changed.exception.__cause__, ResourceCatalogError)
        self.assertEqual(changed.exception.__cause__.code, 'resource-catalog-superseded')

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
