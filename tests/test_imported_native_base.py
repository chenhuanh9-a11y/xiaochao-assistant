"""Actual producer ZIP/store consumers with small, synthetic structure pixels."""
from io import BytesIO
import hashlib
import json
from pathlib import Path
import tempfile
import struct
import unittest
from unittest.mock import patch
import zipfile

import numpy as np

import export_native_base_package as producer
import imported_native_base as native
import imported_resource_archive as archives
from imported_resource_packages import PackageView
import side_entry_search
import star_map_import as files
import test_export_native_base_package as fixtures


class ImportedNativeBaseTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.fixture = fixtures.NativeBaseExporterTests('test_real_roundtrip_preserves_full_competition_and_source_bytes')
        cls.fixture.setUp()
        cls.addClassCleanup(cls.fixture.doCleanups)
        coordinates = cls.fixture.read(producer.COORDINATES)
        for floor in producer.FLOORS:
            coordinates['maps'][cls.fixture.mid]['floors'][floor].update(coordinateWidth=8, coordinateHeight=8)
            coordinates['profiles']['shared-structure']['maps'][cls.fixture.mid]['floors'][floor]['evidence'] = dict(
                method='same-typed-shared-native-reference-canvas', algebraicallyExact=True)
        cls.fixture.write(producer.COORDINATES, coordinates)
        main = cls.fixture.read(producer.ENTRIES + '/entries.json')
        main['entries'][0].update(center=[2., 2.], kind='main', material='fixture-native-room')
        cls.fixture.write(producer.ENTRIES + '/entries.json', main)
        cls.fixture.export()
        cls.archive = cls.fixture.output
        with zipfile.ZipFile(cls.archive) as archive:
            cls.members = {name: archive.read(name) for name in archive.namelist()}

    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.counter = 0

    def view(self, changes=None, *, assert_current=None, manifest_change=None):
        self.counter += 1
        directory = self.root / str(self.counter)
        directory.mkdir()
        archive = self.archive
        if changes or manifest_change:
            members = dict(self.members)
            for path, change in (changes or {}).items():
                full = 'data/base/' + path
                value = members[full]
                members[full] = change(json.loads(value)) if path.endswith('.json') else change(value)
                if not isinstance(members[full], bytes):
                    members[full] = json.dumps(members[full], ensure_ascii=False).encode('utf-8')
            manifest = json.loads(members['manifest.json'])
            for row in manifest['files']:
                raw = members[f"data/{row['componentId']}/{row['path']}"]
                row.update(bytes=len(raw), sha256=hashlib.sha256(raw).hexdigest())
            if manifest_change:
                manifest_change(manifest)
            members['manifest.json'] = json.dumps(manifest).encode('utf-8')
            archive = directory / 'input.zip'
            with zipfile.ZipFile(archive, 'x', compression=zipfile.ZIP_DEFLATED) as output:
                for name, raw in members.items():
                    output.writestr(name, raw)
        resource_root = directory / 'resources'
        saved = archives.save_archive(archive, resource_root)
        ref = next(ref for ref in saved.component_refs if ref.component_id == 'base')
        self.assertFalse((resource_root / 'catalog.json').exists())
        return PackageView(resource_root, ref, assert_current=assert_current)

    @staticmethod
    def change(callback):
        def update(value):
            callback(value)
            return value
        return update

    def test_empty_io_errors_during_entry_construction_reject_partial_bundles(self):
        from imported_resource_reader import ComponentResourceReader
        original = ComponentResourceReader.__call__
        for target in ('entries.json', 'entry-passports.json', 'side-door-anchors.json', 'side-entry-search.npz'):
            with self.subTest(target=target):
                view, reads = self.view(), []
                def read(reader, path):
                    if Path(path).name == target:
                        reads.append(path)
                        # The first read validates the actual resource DTO;
                        # fail only when its owning entry loader reads it.
                        if len(reads) == 2:
                            raise PermissionError()
                    return original(reader, path)
                with patch.object(ComponentResourceReader, '__call__', read):
                    with self.assertRaisesRegex(files.ImportRejected, 'entry-loader-invalid'):
                        native.decode_native_base(view)
                self.assertGreaterEqual(len(reads), 2)

    def test_actual_producer_zip_decodes_all_declared_resources_without_other_roles(self):
        view = self.view()
        with patch.object(view, 'read', wraps=view.read) as reads:
            bundle = native.decode_native_base(view)
        self.assertTrue(bundle.ready)
        self.assertEqual(bundle.gaps, ())
        self.assertEqual((bundle.season, bundle.mode, bundle.component_ref), ('s0', 'nightmare', view.component_ref))
        self.assertEqual(len(bundle.pack.maps), 1)
        self.assertEqual(len(bundle.resources.entries), 3)
        self.assertEqual(len(bundle.entry_identity.entries), 1)
        self.assertEqual(len(bundle.side_entry_identity.entries), 3)
        self.assertEqual(bundle.pack.identity_profiles[self.fixture.mid], 'zhan10-speed')
        from nightmare_candidate import side_door_candidate_pool
        pool, _ = side_door_candidate_pool(bundle.pack.maps, None)
        self.assertEqual([item.id for item in pool], [self.fixture.mid])
        product = bundle.resources.load('zhan10-speed', self.fixture.mid, '1')
        np.testing.assert_array_equal(product.clean_mask, np.ones((8, 8), np.uint8))
        np.testing.assert_array_equal(bundle.adapters.matrix_profile_to_canonical(
            'shared-structure', self.fixture.mid, '1'), np.eye(3))
        self.assertEqual(bundle.side_entry_identity.passports, [])
        self.assertTrue(all(isinstance(item['gates'], list)
                            for item, *_ in bundle.side_entry_identity.entries))
        self.assertEqual(bundle.exit_recognizer.references, [])
        from imported_group_registry import RESOURCE_PATH
        paths = [call.args[0] for call in reads.call_args_list]
        self.assertFalse(any('author.png' in path or 'source-candidate' in path
                             or 'portable-layout' in path for path in paths))
        self.assertEqual({path for path in paths if 'groups' in path}, {RESOURCE_PATH})
        registry = bundle.reviewed_front_groups
        self.assertIsNotNone(registry)
        self.assertEqual(registry.file_ref.component, view.component_ref)
        self.assertEqual(registry.file_ref.path, RESOURCE_PATH)
        with patch.object(view, 'read', side_effect=AssertionError('cache guard read payloads')):
            bundle.assert_current()
            self.assertEqual(bundle.pack.mode, 'nightmare')

    def test_explicit_none_layer_is_a_gap_and_never_removes_the_competitor(self):
        view = self.view({producer.PACK: self.change(
            lambda v: v['maps'][0]['layers'].__setitem__('B1', None))})
        bundle = native.decode_native_base(view)
        self.assertFalse(bundle.ready)
        self.assertEqual(bundle.gaps, ('native-layer-unavailable:fixture-map/B1',))
        self.assertEqual(set(bundle.pack.by_id), {'fixture-map'})
        self.assertIsNone(bundle.pack.by_id['fixture-map'].layers['B1'])

    def test_other_contexts_and_unimplemented_semantic_roles_have_explicit_gaps(self):
        view = self.view()
        for season, mode in (('s0', 'difficult'), ('s1', 'nightmare'), ('s1', 'difficult')):
            with self.subTest(season=season, mode=mode), self.assertRaises(native.NativeBaseUnavailable) as caught:
                native.decode_native_base(view, season=season, mode=mode)
            self.assertEqual(caught.exception.resource_gaps, ('native-base-context-unsupported',))
        cases = {
            producer.COORDINATES: lambda v: v['profiles'].__setitem__('author', {}),
            producer.ENTRIES + '/entry-passports.json': lambda v: v['entries'].append({'authorizesIdentity': True}),
            producer.EXIT: lambda v: v['sideDoors'].append({'mapId': 'map-24'}),
        }
        for path, change in cases.items():
            with self.subTest(path=path), self.assertRaises(native.NativeBaseUnavailable):
                native.decode_native_base(self.view({path: self.change(change)}))

    def test_known_nested_runtime_references_cannot_escape_or_cross_components(self):
        mid = self.fixture.mid
        cases = [
            (producer.INDEX, lambda v: v['entries'][0].__setitem__('resourcePath', '../outside.npz')),
            (producer.VERSIONS, lambda v: v['entries'][0].__setitem__('typedReference', '../outside.npz')),
            (producer.NEUTRAL, lambda v: v['entries'][0].__setitem__('referenceResource', '../outside.npz')),
            (producer.COORDINATES, lambda v: v['maps'][mid]['floors']['1'].__setitem__('referenceResource', '../outside.npz')),
            (producer.ENTRIES + '/entries.json', lambda v: v['entries'][0].__setitem__('structureResource', '../../controls/profile.json')),
            (producer.ENTRIES + '/side-entries.json', lambda v: v['entries'][0].__setitem__('sourceIndex', '../../controls/profile.json')),
        ]
        for path, change in cases:
            with self.subTest(path=path), self.assertRaises(ValueError):
                native.decode_native_base(self.view({path: self.change(change)}))

    def test_provenance_paths_are_preserved_without_becoming_runtime_dependencies(self):
        view = self.view({producer.VERSIONS: self.change(lambda v: v['entries'][0].update(
            authorDisplay={'resource': 'Z:/absent-private-author.png'},
            rendererCatalog={'path': 'Z:/absent-producer/catalog.json'}))})
        self.assertTrue(native.decode_native_base(view).ready)

    def test_incomplete_or_duplicate_competitor_bindings_are_not_admitted(self):
        cases = [
            (producer.PACK, lambda v: v['maps'][0]['layers'].pop('B1')),
            (producer.INDEX, lambda v: v['entries'].pop()),
            (producer.INDEX, lambda v: v['entries'].append(dict(v['entries'][0]))),
            (producer.ENTRIES + '/entries.json', lambda v: v['entries'].clear()),
            (producer.ENTRIES + '/side-entries.json', lambda v: v['entries'].pop()),
        ]
        for path, change in cases:
            with self.subTest(path=path), self.assertRaises(ValueError):
                native.decode_native_base(self.view({path: self.change(change)}))

    def test_entry_numeric_and_shape_limits_precede_actual_entry_array_construction(self):
        cases = [
            (producer.ENTRIES + '/entries.json', lambda v: v['entries'][0].__setitem__('center', [[1], 2])),
            (producer.ENTRIES + '/entries.json', lambda v: v['entries'][0].__setitem__('outline', [[1, 2]] * 257)),
            (producer.ENTRIES + '/side-entries.json', lambda v: v['entries'][0].__setitem__('unit', 1e-300)),
            (producer.ENTRIES + '/side-entries.json', lambda v: v['entries'][0]['turns'][0].__setitem__('next', 2**63)),
        ]
        for path, change in cases:
            view = self.view({path: self.change(change)})
            with self.subTest(path=path), patch.object(native, 'MapEntryGeometryIdentity',
                    side_effect=AssertionError('unsafe entry reached array constructor')):
                with self.assertRaises(ValueError):
                    native.decode_native_base(view)

    def test_matrix_shape_and_native_canvas_are_checked_before_adapter_construction(self):
        mid = self.fixture.mid
        for matrix in ([[1, 0, 0]] * 4096, [[1, 0], [0, 1], [0, 0]],
                       [[[1], 0, 0], [0, 1, 0], [0, 0, 1]], [[1, 0, 2], [0, 1, 0], [0, 0, 1]]):
            view = self.view({producer.COORDINATES: self.change(lambda v:
                v['profiles']['shared-structure']['maps'][mid]['floors']['1'].__setitem__('matrixProfileToCanonical', matrix))})
            with self.subTest(matrix_rows=len(matrix)), patch.object(native, 'AuthorCoordinateAdapters',
                    side_effect=AssertionError('unsafe matrix reached NumPy adapter')):
                with self.assertRaises(ValueError):
                    native.decode_native_base(view)

    def test_search_grid_declarations_precede_the_existing_search_array_loader(self):
        def wrong_grid(raw):
            with np.load(BytesIO(raw), allow_pickle=False) as archive:
                arrays = {name: archive[name] for name in archive.files}
            name = next(name for name in arrays if name.endswith('_lower'))
            arrays[name] = np.zeros((1, 100, 100), np.uint8)
            target = BytesIO()
            np.savez_compressed(target, **arrays)
            return target.getvalue()
        view = self.view({producer.ENTRIES + '/side-entry-search.npz': wrong_grid})
        with patch.object(native, 'MapLocalEntryIdentity', side_effect=AssertionError('unsafe search array loaded')):
            with self.assertRaisesRegex(ValueError, 'search-grid-invalid'):
                native.decode_native_base(view)

    def test_prepared_search_values_must_equal_the_actual_generator_relations(self):
        values = {'deltas': 123456., 'denoms': 1e30, 'radius': 999., 'lower': 255}
        load_index = side_entry_search.load_index
        for altered in ('deltas', 'denoms', 'radius', 'lower', 'all'):
            fields = values if altered == 'all' else {altered: values[altered]}
            def corrupt(raw):
                with np.load(BytesIO(raw), allow_pickle=False) as archive:
                    arrays = {name: archive[name] for name in archive.files}
                for name in arrays:
                    field = name.rsplit('_', 1)[-1]
                    if field in fields:
                        arrays[name] = np.full_like(arrays[name], fields[field])
                target = BytesIO()
                np.savez_compressed(target, **arrays)
                return target.getvalue()
            view = self.view({producer.ENTRIES + '/side-entry-search.npz': corrupt})
            observed = []
            def actual_load(*args, **kwargs):
                result, error = load_index(*args, **kwargs)
                self.assertIsNone(error)
                self.assertTrue(result)
                for arrays in result.values():
                    for field, value in fields.items():
                        self.assertTrue(np.all(arrays[field] == value))
                observed.append(True)
                return result, error
            with self.subTest(altered=altered), patch.object(side_entry_search, 'load_index', side_effect=actual_load):
                with self.assertRaisesRegex(ValueError, 'search-derived-mismatch'):
                    native.decode_native_base(view)
                self.assertTrue(observed)

    def test_forged_small_orientation_axis_cannot_hide_an_oversized_rebuild(self):
        rays = [[[1., 0.], [0., 1.]], [[0., 1.], [-1., 0.]],
                [[-1., 0.], [0., -1.]], [[0., -1.], [1., 0.]]]
        def local_graph(value):
            row = next(row for row in value['entries'] if row['floor'] == '2')
            row['unit'] = .024
            for turn, pair in zip(row['turns'], rays):
                turn['rays'] = pair
        def small_axis(raw):
            with np.load(BytesIO(raw), allow_pickle=False) as archive:
                arrays = {name: archive[name] for name in archive.files}
            key = json.loads(str(arrays['metadata'].item()))['entries'][0]['key']
            arrays[key + '_rays'] = np.asarray(rays)
            arrays[key + '_cell'] = np.asarray(.024 / 12)
            arrays[key + '_groups'] = np.unique(np.rint(rays).astype(np.int8).reshape(4, 4),
                                                axis=0, return_inverse=True)[1]
            # One declared field fits the existing 32 MiB member budget. The
            # four real ray groups would rebuild >64 MiB of distance fields.
            arrays[key + '_lower'] = np.zeros((1, 4001, 4001), np.uint8)
            target = BytesIO()
            np.savez_compressed(target, **arrays)
            raw = target.getvalue()
            native.validate_npz_bytes(raw)
            return raw
        view = self.view({producer.ENTRIES + '/side-entries.json': self.change(local_graph),
                          producer.ENTRIES + '/side-entry-search.npz': small_axis})
        with patch.object(native, 'prepare_arrays', side_effect=AssertionError('unbounded rebuild started')):
            with self.assertRaisesRegex(ValueError, 'search-grid-invalid'):
                native.decode_native_base(view)

    def test_consumer_text_fields_are_required_bounded_strings(self):
        cases = [
            (producer.PACK, lambda v: v['maps'][0].__setitem__('name', {})),
            (producer.PACK, lambda v: v['maps'][0].__setitem__('direction', {})),
            (producer.PACK, lambda v: v['maps'][0].__setitem__('shape', [])),
            (producer.PACK, lambda v: v['maps'][0].__setitem__('name', 'x' * 257)),
            (producer.ENTRIES + '/entries.json', lambda v: v['entries'][0].__setitem__('material', {})),
            (producer.ENTRIES + '/side-entries.json', lambda v: v['entries'][0].__setitem__('region', {})),
        ]
        for path, change in cases:
            with self.subTest(path=path), self.assertRaisesRegex(ValueError, 'text-field-invalid'):
                native.decode_native_base(self.view({path: self.change(change)}))

    def test_local_rank_requires_an_explicit_gates_list(self):
        for missing in (True, False):
            def change(value):
                if missing:
                    value['entries'][0].pop('gates')
                else:
                    value['entries'][0]['gates'] = {}
            with self.subTest(missing=missing), self.assertRaisesRegex(ValueError, 'local-gates-invalid'):
                native.decode_native_base(self.view({producer.ENTRIES + '/side-entries.json': self.change(change)}))

    def test_search_metadata_bad_crc_is_a_stable_import_rejection(self):
        def broken_crc(raw):
            with np.load(BytesIO(raw), allow_pickle=False) as archive:
                arrays = {name: archive[name] for name in archive.files}
            arrays['metadata'] = np.asarray(str(arrays['metadata'].item()) + ' ' * 6000)
            target = BytesIO()
            np.savez(target, **arrays)
            raw = bytearray(target.getvalue())
            with zipfile.ZipFile(BytesIO(raw)) as archive:
                info = archive.getinfo('metadata.npy')
            crc = info.CRC ^ 1
            struct.pack_into('<I', raw, info.header_offset + 14, crc)
            end = raw.rfind(b'PK\x05\x06')
            position = struct.unpack_from('<I', raw, end + 16)[0]
            while position < end:
                name, extra, comment = struct.unpack_from('<3H', raw, position + 28)
                if raw[position + 46:position + 46 + name] == b'metadata.npy':
                    struct.pack_into('<I', raw, position + 16, crc)
                    break
                position += 46 + name + extra + comment
            # Header-only generic admission cannot reach the member's CRC yet.
            native.validate_npz_bytes(bytes(raw))
            return bytes(raw)
        view = self.view({producer.ENTRIES + '/side-entry-search.npz': broken_crc})
        with self.assertRaises(files.ImportRejected) as caught:
            native.decode_native_base(view)
        self.assertEqual(caught.exception.code, 'imported-native-base-invalid')

    def test_typed_pixel_binding_canvas_and_nonempty_checks_use_the_actual_loader(self):
        path = self.fixture.rows[0]['resourcePath']
        def changed_product(raw, change):
            with np.load(BytesIO(raw), allow_pickle=False) as archive:
                arrays = {name: archive[name] for name in archive.files}
            metadata = json.loads(str(arrays['metadata'].item()))
            if change == 'binding':
                metadata['binding']['mapId'] = 'different-competitor'
                arrays['metadata'] = np.asarray(json.dumps(metadata))
            elif change == 'canvas':
                arrays['shared_mask'] = np.ones((4, 4), np.uint8)
            else:
                arrays['shared_mask'] = np.zeros((8, 8), np.uint8)
            target = BytesIO()
            np.savez_compressed(target, **arrays)
            return target.getvalue()
        for change in ('binding', 'canvas', 'empty'):
            view = self.view({path: lambda raw: changed_product(raw, change)})
            with self.subTest(change=change), self.assertRaises(ValueError):
                native.decode_native_base(view)

    def test_guard_checks_both_external_generation_and_actual_manifest_identity(self):
        state = {'current': True}
        def check():
            if not state['current']:
                raise ValueError('fixture-superseded')
        bundle = native.decode_native_base(self.view(assert_current=check))
        state['current'] = False
        for name in ('pack', 'resources', 'versions', 'adapters', 'interfaces', 'reader',
                     'entry_identity', 'side_entry_identity', 'gaps', 'season', 'mode', 'component_ref'):
            with self.subTest(name=name), self.assertRaisesRegex(ValueError, 'fixture-superseded'):
                getattr(bundle, name)
        view = self.view()
        bundle = native.decode_native_base(view)
        manifest = view.root.parent.parent / 'manifest.json'
        raw = manifest.read_bytes()
        manifest.rename(manifest.with_name('retained-manifest.json'))
        manifest.write_bytes(raw)
        with self.assertRaisesRegex(ValueError, 'manifest-replaced'):
            bundle.assert_current()


if __name__ == '__main__':
    unittest.main()
