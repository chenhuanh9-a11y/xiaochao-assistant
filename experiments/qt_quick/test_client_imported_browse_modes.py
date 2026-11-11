"""Selected-component browse loading across the remaining synthetic modes."""
import copy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from uuid import uuid4

import numpy as np

import export_author_display_component as display_exporter
import generic_author_package
import imported_resource_archive as archives
from client_imported_browse import imported_home_rows, load_imported_browse_context
from client_imported_providers import SUPPORTED_CAPABILITIES
from difficult_entry_identity import MapEntryGeometryIdentity
from difficult_side_entry_identity import MapLocalEntryIdentity
from imported_resource_activation import activate_catalog_locked
from imported_resource_catalog import ResourceResolver, SCHEMA
from imported_resource_packages import PackageView
from imported_resource_selection import inspect_selection
from resource_transaction import resource_write_lock
import star_map_import as files
import test_export_author_display_component_modes as source_modes


class ImportedBrowseModeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # Reuse the producer test's complete class-level synthetic source/base
        # preparation. It exports S0N, S0D and S1N exactly once and is imported
        # as a module so unittest does not discover or rerun its TestCase.
        source_modes.AuthorDisplayComponentModeTests.setUpClass()
        cls.addClassCleanup(source_modes.AuthorDisplayComponentModeTests.doClassCleanups)
        cls.bases = source_modes.AuthorDisplayComponentModeTests.bases

    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix='imported-browse-mode-')
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.image_root = self.root / 'synthetic-images'
        self.image_root.mkdir()
        self.image_bytes = source_modes._synthetic_png()
        (self.image_root / 'route.png').write_bytes(self.image_bytes)

    @staticmethod
    def _wire(ref):
        return dict(packageId=ref.package_id, resourceVersion=ref.resource_version,
            componentId=ref.component_id, manifestSha256=ref.manifest_sha256)

    @staticmethod
    def _control_ref(base_info):
        refs = archives.inspect_archive(base_info['archive']).component_refs
        return next(ref for ref in refs if ref.component_id == 'controls')

    def _export_display(self, key, profile_id, map_id, floor, package_id, *, output_name):
        info = self.bases[key]
        registration_path = self.root / f'{output_name}-registration.json'
        output = self.root / f'{output_name}.zip'
        matrix = [[1.15, .1, 2], [.2, .95, -1], [0, 0, 1]]
        registration_path.write_text(json.dumps(dict(
            schema=display_exporter.INPUT_SCHEMA, profileId=profile_id,
            authorName='合成浏览作者', displayName='合成浏览路线', layers=[dict(
                mapId=map_id, floor=floor, image='route.png',
                matrixDisplayToReference=matrix)]), ensure_ascii=False), encoding='utf-8')
        result = display_exporter.export_component(info['resource_root'], info['ref'],
            registration_path, self.image_root, output, package_id=package_id,
            resource_version='browse-synthetic-v1')
        self.assertEqual(result['componentId'], f'display-{info["season"]}-{info["mode"]}')
        inspected = archives.inspect_archive(output)
        saved = archives.save_archive(output, info['resource_root'],
            expected_archive_sha256=inspected.archive_sha256)
        display_ref = next(ref for ref in saved.component_refs
                           if ref.component_id == result['componentId'])
        view = PackageView(info['resource_root'], display_ref)
        return info, output, display_ref, view, matrix

    def _seed_catalog(self, info, selected_display_ref):
        root = info['resource_root']
        resolver = ResourceResolver(root, supported_capabilities=SUPPORTED_CAPABILITIES)
        context_key = f'{info["season"]}:{info["mode"]}'
        catalog = dict(schema=SCHEMA, generation=str(uuid4()),
            contexts={context_key:dict(recognitionBase=self._wire(info['ref']),
                controls=self._wire(self._control_ref(info)), reviewedGroups=None)},
            displays=[self._wire(selected_display_ref)] if selected_display_ref is not None else [])

        def validate(document):
            return inspect_selection(root, document, supported_capabilities=SUPPORTED_CAPABILITIES)

        previous = resolver.snapshot()
        with resource_write_lock(root):
            activate_catalog_locked(root, catalog,
                expected_catalog_stamp=previous.catalog_stamp, validate=validate)
        snapshot = resolver.snapshot()
        self.assertEqual(snapshot.displays, (selected_display_ref,) if selected_display_ref is not None else ())
        self.assertEqual(snapshot.contexts[context_key]['recognitionBase'], info['ref'])
        self.assertEqual(snapshot.contexts[context_key]['controls'], self._control_ref(info))
        return resolver

    def _browse_success(self, key, map_id, floor, *, check_s0d_legacy_absence=False):
        info = self.bases[key]
        profile_id = 'author-browse-' + key
        package_id = 'browse-display-' + key
        info, _output, selected_ref, view, _matrix = self._export_display(
            key, profile_id, map_id, floor, package_id, output_name='selected-' + key)

        # This real package is installed but deliberately absent from catalog.json.
        # A tree walk would find it; the selected-component loader must ignore it.
        _orphan_info, _orphan_output, orphan_ref, orphan_view, _ = self._export_display(
            key, 'author-unselected-' + key, map_id, floor,
            'browse-display-unselected-' + key, output_name='unselected-' + key)
        self.assertNotEqual(orphan_ref, selected_ref)
        self.assertTrue(orphan_view.root.is_dir())
        self.assertTrue(orphan_view.path(orphan_view.component['entrypoint']).is_file())

        catalog = self._seed_catalog(info, selected_ref)
        catalog_stamp_before = catalog.snapshot().catalog_stamp
        base = info['base']
        base.assert_current()
        self.assertEqual(base.component_ref, info['ref'])
        self.assertIsNone(base.entry_identity._observation)
        map_ids_before = tuple(sorted(base.pack.by_id))
        rank_before = base.rank_ready
        identity_before = copy.deepcopy((
            [row[0] for row in base.entry_identity.entries],
            [row[0] for row in base.side_entry_identity.entries]))
        bridge_before = base.adapters.matrix_profile_to_canonical('shared-structure', map_id, floor).copy()

        ranked, captured = [], []
        main_rank, main_geometry = MapEntryGeometryIdentity.rank, MapEntryGeometryIdentity.geometry
        side_rank = MapLocalEntryIdentity.rank
        def track_main_rank(instance, *args, **kwargs):
            ranked.append('main')
            return main_rank(instance, *args, **kwargs)
        def track_side_rank(instance, *args, **kwargs):
            ranked.append('side')
            return side_rank(instance, *args, **kwargs)
        def track_geometry(instance, *args, **kwargs):
            captured.append('entry-frame-geometry')
            return main_geometry(instance, *args, **kwargs)
        with patch.object(generic_author_package, 'profile_ids',
                          wraps=generic_author_package.profile_ids) as legacy_tree_discovery, \
             patch.object(MapEntryGeometryIdentity, 'rank', track_main_rank), \
             patch.object(MapEntryGeometryIdentity, 'geometry', track_geometry), \
             patch.object(MapLocalEntryIdentity, 'rank', track_side_rank):
            context = load_imported_browse_context(catalog, info['mode'], season=info['season'])
        self.assertEqual(legacy_tree_discovery.call_count, 0)
        self.assertEqual(ranked, [])
        self.assertEqual(captured, [])
        self.assertEqual(catalog.snapshot().catalog_stamp, catalog_stamp_before)

        self.assertEqual(context.mode, info['mode'])
        self.assertEqual(context.season, info['season'])
        self.assertEqual(context.model, None)
        self.assertEqual(context.assets.signature[1], info['ref'])
        self.assertEqual(context.assets.signature[2], (selected_ref,))
        structure_id = 's1-native-structure' if info['season'] == 's1' else 'shared-structure'
        self.assertEqual([row.profile_id for row in context.profiles], [structure_id, profile_id])
        self.assertEqual(context.profiles[0].available_layers, len(base.pack.maps) * len(base.pack.floors))
        summary = context.profiles[1]
        self.assertEqual(summary.name, '合成浏览路线')
        self.assertEqual(summary.package_id, selected_ref.package_id)
        route_profile = context.catalog.profiles[profile_id]
        route_variant = route_profile.maps[map_id][0]
        self.assertEqual((route_variant.identity_status, route_variant.display_policy),
                         ('display-only-user-provided', 'display-only'))
        binding = context.assets._displays.binding(profile_id, map_id, floor)
        self.assertEqual(binding['status'], 'user-provided-unreviewed')
        self.assertEqual(summary.map_count, len(base.pack.maps))
        self.assertEqual(summary.available_maps, 1)
        self.assertEqual(summary.available_layers, 1)
        self.assertEqual(summary.missing_floors, len(base.pack.maps) * len(base.pack.floors) - 1)
        self.assertEqual(summary.first_preview[:2], (map_id, floor))
        self.assertEqual([item.id for item in summary.maps], [map_id])
        self.assertEqual(summary.maps[0].name, base.pack.by_id[map_id].name)
        self.assertEqual(len(summary.maps[0].variants), 1)
        self.assertEqual(summary.maps[0].variants[0].id, 'variant-01')
        self.assertEqual(summary.maps[0].variants[0].name, '合成浏览作者')
        self.assertEqual({f for variant in summary.maps[0].variants for f in variant.layers}, {floor})
        self.assertEqual(set(context.floor_metadata), {str(value) for value in base.pack.floors})
        expected_matrix = (base.adapters.matrix_profile_to_canonical(
            'shared-structure', map_id, floor) @ np.asarray(_matrix, dtype=np.float64))
        np.testing.assert_allclose(context.assets._displays.adapters.matrix_profile_to_canonical(
            profile_id, map_id, floor), expected_matrix)

        path = summary.first_preview[2]
        self.assertTrue(path.is_relative_to(view.root))
        self.assertIn(f'packages/{selected_ref.package_id}/{selected_ref.resource_version}/data/'
                      f'{selected_ref.component_id}/images/', path.as_posix())
        self.assertNotIn('_internal/assets/authors', path.as_posix())
        self.assertEqual(context.assets.key(path), (profile_id, map_id, floor))
        np.testing.assert_array_equal(context.assets.pixels(context.assets.key(path))[1, 1], (3, 2, 1, 0))

        if info['season'] == 's1' and floor == '-4':
            self.assertEqual(context.floor_metadata['-4']['label'], 'B4')
        else:
            self.assertEqual(context.floor_metadata[floor]['label'],
                {'1':'1 楼', '2':'2 楼', 'B1':'B1 地下室'}.get(floor, floor))

        base.assert_current()
        self.assertEqual(base.component_ref, info['ref'])
        self.assertEqual(base.rank_ready, rank_before)
        self.assertEqual(tuple(sorted(base.pack.by_id)), map_ids_before)
        self.assertEqual(([row[0] for row in base.entry_identity.entries],
                          [row[0] for row in base.side_entry_identity.entries]), identity_before)
        np.testing.assert_array_equal(base.adapters.matrix_profile_to_canonical(
            'shared-structure', map_id, floor), bridge_before)
        if check_s0d_legacy_absence:
            from test_export_s0_difficult_native_base_package import LEGACY
            self.assertIn(LEGACY, base.pack.by_id)
            self.assertNotIn(LEGACY, [item.id for item in summary.maps])
            self.assertIsNone(context.catalog.route_path(profile_id, LEGACY, '1'))
        return context, info, selected_ref, view

    def test_s0_nightmare_browse_reads_only_the_selected_component(self):
        info = self.bases['s0n']
        self._browse_success('s0n', 'fixture-map', '1')

    def test_s0_difficult_browse_keeps_legacy_map29_as_a_gap(self):
        info = self.bases['s0d']
        native_map = next(mid for mid in info['base'].pack.by_id
                          if mid != 'difficult-map-29')
        self._browse_success('s0d', native_map, '1', check_s0d_legacy_absence=True)

        # A submitted legacy-only layer has no native typed reference. The
        # producer must refuse it, so the browser cannot synthesize an author
        # layer from the legacy layout/transform.
        registration = self.root / 's0d-legacy-registration.json'
        output = self.root / 's0d-legacy.zip'
        registration.write_text(json.dumps(dict(schema=display_exporter.INPUT_SCHEMA,
            profileId='author-browse-s0d-legacy', authorName='合成作者', displayName='不应生成',
            layers=[dict(mapId='difficult-map-29', floor='1', image='route.png',
                matrixDisplayToReference=[[1,0,0],[0,1,0],[0,0,1]])]),
            ensure_ascii=False), encoding='utf-8')
        with self.assertRaisesRegex(ValueError, 'typed-reference-unavailable:difficult-map-29/1'):
            display_exporter.export_component(info['resource_root'], info['ref'], registration,
                self.image_root, output, package_id='browse-display-s0d-legacy',
                resource_version='browse-synthetic-v1')
        self.assertFalse(output.exists())

    def test_selected_s0d_without_author_exposes_structure_and_real_legacy_pixels(self):
        info = self.bases['s0d']
        resolver = self._seed_catalog(info, None)
        before = resolver.snapshot().catalog_stamp
        context = load_imported_browse_context(resolver, 'difficult', season='s0')
        self.assertEqual([profile.profile_id for profile in context.profiles], ['shared-structure'])
        profile = context.profiles[0]
        self.assertEqual((profile.map_count, profile.available_maps, profile.available_layers), (30, 30, 60))
        legacy = next(item for item in profile.maps if item.id == 'difficult-map-29')
        self.assertEqual(len(legacy.variants), 1)
        self.assertEqual(set(legacy.variants[0].layers), {'1', '2'})
        for floor, path in legacy.variants[0].layers.items():
            with self.subTest(floor=floor):
                reference = info['base'].legacy_references.reference(legacy.id, floor)
                metadata = reference.metadata.copy()
                self.assertEqual(path, reference.path)
                self.assertEqual(context.assets.key(path), ('shared-structure', legacy.id, floor))
                pixels = context.assets.pixels(context.assets.key(path))
                self.assertEqual(pixels.shape, (*reference.shape, 4))
                np.testing.assert_array_equal(pixels[..., 3] != 0, reference.clean_mask != 0)
                self.assertEqual(tuple(pixels[reference.clean_mask != 0][0]), (218, 228, 238, 255))
                self.assertFalse(pixels.flags.writeable)
                self.assertEqual(reference.metadata, metadata)
                self.assertEqual(metadata['sourceKind'], 'legacy-shared-binary')
                self.assertIsNone(metadata['sourceLayout'])
                self.assertEqual(metadata['staticAcceptance'], 'working-draft-not-batch-accepted')
                self.assertFalse(metadata['authorizesMapIdentity'])
                self.assertFalse(metadata['authorizesStructureValidation'])
        self.assertFalse(info['base'].rank_ready)
        self.assertEqual(resolver.snapshot().catalog_stamp, before)

    def test_remaining_modes_without_author_return_real_structure_home_rows(self):
        for mode_key in ('s0n', 's0d', 's1n'):
            with self.subTest(mode=mode_key):
                info = self.bases[mode_key]
                resolver = self._seed_catalog(info, None)
                before = resolver.snapshot().catalog_stamp
                rows = imported_home_rows(resolver, info['mode'], 'shared-structure', info['season'])
                self.assertEqual(len(rows), min(3, len(info['base'].pack.maps)))
                self.assertEqual(len({row['title'] for row in rows}), len(rows))
                self.assertTrue(all(row['structure'] and row['key'] == '' for row in rows))
                self.assertTrue(all(row['profileId'] == 'shared-structure' for row in rows))
                self.assertTrue(all(row['displayKind'] == '结构底图' for row in rows))
                self.assertTrue(all(row['imageUrl'].startswith('data:image/png;base64,') for row in rows))
                self.assertEqual(resolver.snapshot().catalog_stamp, before)

    def test_s1_nightmare_browse_preserves_negative_floor_label_and_pixels(self):
        info = self.bases['s1n']
        map_id = next(iter(info['base'].pack.by_id))
        context, _info, selected_ref, view = self._browse_success('s1n', map_id, '-4')
        self.assertEqual(context.floor_metadata['-4']['label'], 'B4')
        self.assertTrue(str(context.profiles[1].first_preview[2]).startswith(str(view.root)))


if __name__ == '__main__':
    unittest.main()
