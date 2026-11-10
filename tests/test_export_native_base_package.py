"""Producer round trips with tiny real JSON/NPZ source products, not game data."""
import base64
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import zipfile

import numpy as np

import export_native_base_package as exporter
import imported_resource_archive as archives
from imported_resource_packages import PackageView
from imported_resource_reader import ComponentResourceReader
from imported_controls_profile import S0ControlsProfile
from side_entry_search import write_index


class NativeBaseExporterTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.source = self.root / 'source'
        self.output = self.root / 'package.zip'
        self.entry_root = self.source / exporter.ENTRIES
        self.entry_root.mkdir(parents=True)
        self.mid = 'fixture-map'
        self.version = 'fixture-source-v1'
        self.raw_hash = 'a' * 64
        self.rows = []
        self.local = []
        self.neutral = []
        self.versions = []
        self.coordinate_floors = {}
        self.shared_floors = {}
        self.prepared_entries = []
        for floor in exporter.FLOORS:
            resource = f'{exporter.ENTRIES}/{self.mid}-{floor}.npz'
            binding = dict(profile=exporter.PROFILE, mapId=self.mid, floor=floor, variant='variant-01')
            version_key = f'104001@{self.raw_hash}/{floor}'
            canvas = dict(width=8, height=8, shapeHW=[8, 8], layout='104001', floor=floor,
                          coordinateSpace='fixture-native:' + floor)
            common = dict(structureVersionKey=version_key, sourceVersion=self.version, baseSnapshot='fixture-base')
            metadata = dict(sourceKind='game-rendered-shared-structure', binding=binding, canvas=canvas,
                            roles=dict(clean='shared_mask', validation='shared_mask'), **common)
            np.savez_compressed(self.source / resource, metadata=np.asarray(json.dumps(metadata)),
                                shared_mask=np.ones((8, 8), np.uint8))
            self.rows.append(dict(binding, resourcePath=resource, **common))
            self.neutral.append(dict(mapId=self.mid, floor=floor, binding=binding,
                referenceResource=resource, canvas=canvas, **common))
            points = np.array([[1., 1.], [6., 1.], [6., 6.], [1., 6.]])
            rays = np.array([[[1., 0.], [0., 1.]]] * 4)
            pairs = np.array([[0, 1], [1, 2], [2, 3], [3, 0]])
            local = dict(mapId=self.mid, floor=floor, profileId=exporter.PROFILE,
                variantId='variant-01', unit=12., sourceKind='game-rendered-shared-structure',
                sourceProduct=Path(resource).name, structureResource=Path(resource).name,
                sourceIndex='../nightmare-shared-structure-index.json', canvas=canvas,
                turns=[dict(point=point.tolist(), rays=ray.tolist(), next=int(pair[1]))
                       for point, ray, pair in zip(points, rays, pairs)], gates=[], **common)
            self.local.append(local)
            self.prepared_entries.append((local, points, rays, pairs))
            resource_key = dict(layoutId='104001', structureRawRecordSha256=self.raw_hash, floor=floor)
            self.versions.append(dict(mapId=self.mid, resourceKey=resource_key, sourceVersion=self.version,
                typedReference=resource, sharedLookup=dict(binding, resourcePath=resource),
                packReference=exporter.PACK, adapterReference=exporter.COORDINATES,
                neutralDisplay=dict(index=exporter.NEUTRAL, baseResourceKey=resource_key),
                authorDisplay=dict(resource='assets/author.png', baseResourceKey=resource_key,
                                   registration='producer-provenance-only')))
            self.coordinate_floors[floor] = dict(referenceResource=resource,
                structureVersionKey=version_key, sourceVersion=self.version)
            self.shared_floors[floor] = dict(structureVersionKey=version_key,
                matrixProfileToCanonical=np.eye(3).tolist(), usableForCurrentFrameRendering=True,
                crossFloorProjectionAllowed=False)
        self.map_row = dict(id=self.mid, name='Fixture map', direction='unknown', doorCount=0,
            shape='unknown', resourceBase='resource-root', sourceLayout='104001',
            structureContentSha256=self.raw_hash, sourceVersion=self.version,
            identityProfileId=exporter.PROFILE, routeProfileId='old-author', oneFlow='assets/author.png',
            layers={floor: f'assets/old-{floor}.png' for floor in exporter.FLOORS},
            sideDoorCrop=[0, 0, 8, 8], targetGuid='retained-source-guid',
            classificationKnown=False, customMetadata={'retain': [1, 2, 3]})
        self.write(exporter.PACK, dict(schemaVersion=2, packId='fixture-native-pack',
            defaultEntryFloor=2, maps=[self.map_row], sourceVersion=self.version))
        self.write(exporter.INDEX, dict(schemaVersion=1, resourceKind='shared-structure',
            entries=self.rows, structureVersionBindings=exporter.VERSIONS))
        self.write(exporter.VERSIONS, dict(schemaVersion=1,
            resourceKind='native-structure-version-bindings', entries=self.versions))
        self.write(exporter.NEUTRAL, dict(schemaVersion=1, resourceKind='typed-shared-structure-display',
            indexPath=exporter.INDEX, entries=self.neutral))
        self.write(exporter.COORDINATES, dict(schema='identityv-nightmare-canonical-coordinate-pack-v1',
            canonicalPackId='fixture-canonical', coordinateReferenceProfileId='shared-structure',
            referenceProvider=dict(indexPath=exporter.INDEX,
                lookupBindings=[{key: row[key] for key in ('profile', 'mapId', 'floor', 'variant')}
                                for row in self.rows]),
            policy=dict(requiresCurrentFrameGeometryFit=True, authorizesMapIdentity=False,
                        authorizesCrossFloorProjection=False),
            maps={self.mid: dict(floors=self.coordinate_floors)},
            profiles={'shared-structure': dict(maps={self.mid: dict(floors=self.shared_floors)}),
                      'old-author': dict(maps={'author-only': {}})},
            canonicalCrossFloorSearchCandidates={}))
        main = dict(self.local[0], floor='1', structureResource=Path(self.rows[0]['resourcePath']).name,
                    sourceProduct=Path(self.rows[0]['resourcePath']).name,
                    outline=[[2., 2.], [3., 2.], [3., 3.]],
                    chains=[dict(points=[[1., 1.], [2., 2.], [3., 3.]], rays=[[1., 0.], [0., 1.]])])
        self.write(exporter.ENTRIES + '/entries.json', dict(schemaVersion=1, mode='nightmare',
            profileId=exporter.PROFILE, entries=[main], semanticGaps=['fixture-semantic-gap']))
        self.write(exporter.ENTRIES + '/side-entries.json', dict(schemaVersion=1, mode='nightmare',
            profileId=exporter.PROFILE, entries=self.local, semanticGaps=['fixture-semantic-gap']))
        self.write(exporter.ENTRIES + '/entry-passports.json', dict(schemaVersion=1, mode='nightmare',
            profileId=exporter.PROFILE, entries=[], coverage=[], semanticGaps=['fixture-passports-unavailable']))
        second = next(row for row in self.local if row['floor'] == '2')
        door = dict(mapId=self.mid, floor='2', layoutId='104001',
            structureVersionKey=second['structureVersionKey'], sourceVersion=self.version,
            boundaryCenter=[3., 7.], outwardDirection='S')
        self.write(exporter.ENTRIES + '/side-door-anchors.json', dict(schemaVersion=1, mode='nightmare',
            profileId=exporter.PROFILE, entries=[door]))
        write_index(self.entry_root / 'side-entry-search.npz', self.prepared_entries, {self.mid: door})
        self.write(exporter.EXIT, dict(exitIcon=dict(width=8, height=8,
            pixels=base64.b64encode(np.arange(64, dtype=np.uint8).tobytes()).decode()),
            sideDoors=[dict(mapId='author-only-descriptor')]))
        self.write('references/native-similarity-groups.json', {'groups': ['must-not-be-exported']})
        self.write('references/nightmare-source-candidate-index.json', {'entries': ['must-not-be-exported']})
        self.write('references/nightmare-portable-layout-seeds.json', {'entries': ['must-not-be-exported']})
        self.write('assets/author.png', {'sentinel': 'must-not-be-exported'})

    def write(self, relative, value):
        path = self.source / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(value, ensure_ascii=False), encoding='utf-8')

    def read(self, relative):
        return json.loads((self.source / relative).read_bytes())

    def export(self, **kwargs):
        return exporter.export_package(self.source, self.output, package_id='fixture-package',
                                       resource_version='v1', **kwargs)

    def test_real_roundtrip_preserves_full_competition_and_source_bytes(self):
        original = {p.relative_to(self.source).as_posix(): p.read_bytes()
                    for p in self.source.rglob('*') if p.is_file()}
        result = self.export()
        self.assertEqual((result['mapCount'], result['typedLayerCount']), (1, 3))
        self.assertEqual(result['sha256'], hashlib.sha256(self.output.read_bytes()).hexdigest())
        self.assertFalse(result['catalogActivated'])
        self.assertEqual(original, {p.relative_to(self.source).as_posix(): p.read_bytes()
                                  for p in self.source.rglob('*') if p.is_file()})
        installed = self.root / 'installed'
        saved = archives.save_archive(self.output, installed)
        views = {ref.component_id: PackageView(installed, ref) for ref in saved.component_refs}
        reader = ComponentResourceReader(views['base'])
        context = reader.json('resource-context.json')
        self.assertEqual(context['runtimeInterfaces'], exporter.INTERFACES)
        pack = reader.json(exporter.PACK)
        self.assertEqual(pack['schemaVersion'], 1)
        self.assertEqual(pack['sourceIdentitySchemaVersion'], 2)
        row = pack['maps'][0]
        self.assertEqual(row, dict(self.map_row, oneFlow=None, routeProfileId='shared-structure',
            layers={floor: typed['resourcePath'] for floor, typed in zip(exporter.FLOORS, self.rows)}))
        self.assertEqual(set(reader.json(exporter.COORDINATES)['profiles']), {'shared-structure'})
        self.assertEqual(reader.json(exporter.EXIT)['sideDoors'], [])
        self.assertEqual(reader.json(exporter.EXIT)['role'], 'exit-template-only')
        for path in (exporter.INDEX, exporter.VERSIONS, exporter.NEUTRAL,
                     *(exporter.ENTRIES + '/' + name for name in
                       ('entries.json', 'side-entries.json', 'entry-passports.json',
                        'side-door-anchors.json', 'side-entry-search.npz')),
                     *(row['resourcePath'] for row in self.rows)):
            self.assertEqual(views['base'].read(path), original[path])
        self.assertFalse(any('assets/' in path or 'groups' in path or 'source-candidate' in path
                             or 'portable-layout' in path for path in views['base'].inventory))
        controls = S0ControlsProfile(views['controls'])
        self.assertEqual(len(controls.templates.label_values), 12)
        from s0_floor_templates import _bank
        vectors, labels = _bank()
        np.testing.assert_array_equal(controls.templates.vectors, vectors)
        np.testing.assert_array_equal(controls.templates.labels, labels)
        self.assertEqual(controls.for_context('s0', 'nightmare').contract.floor_id_for(-1), 'B1')
        self.assertIsNone(controls.for_context('s0', 'difficult'))
        self.assertFalse((installed / 'catalog.json').exists())

    def test_missing_map_layer_or_typed_competitor_is_rejected_without_output(self):
        for change in ('map-layer', 'typed-layer', 'main-map', 'local-layer', 'coordinate-layer'):
            with self.subTest(change=change):
                if change == 'map-layer':
                    value = self.read(exporter.PACK)
                    original = deepcopy(value)
                    value['maps'][0]['layers'].pop('B1')
                    path = exporter.PACK
                elif change == 'coordinate-layer':
                    value = self.read(exporter.COORDINATES)
                    original = deepcopy(value)
                    value['maps'][self.mid]['floors'].pop('B1')
                    path = exporter.COORDINATES
                else:
                    path = {'typed-layer': exporter.INDEX,
                            'main-map': exporter.ENTRIES + '/entries.json',
                            'local-layer': exporter.ENTRIES + '/side-entries.json'}[change]
                    value = self.read(path)
                    original = deepcopy(value)
                    value['entries'].pop()
                self.write(path, value)
                with self.assertRaises(ValueError):
                    self.export()
                self.assertFalse(self.output.exists())
                self.write(path, original)

    def test_duplicate_binding_and_unsafe_typed_path_are_rejected_without_output(self):
        original = self.read(exporter.INDEX)
        for change in ('duplicate', 'traversal', 'absolute'):
            value = deepcopy(original)
            if change == 'duplicate':
                value['entries'].append(deepcopy(value['entries'][0]))
            else:
                value['entries'][0]['resourcePath'] = '../outside.npz' if change == 'traversal' else '/outside.npz'
            self.write(exporter.INDEX, value)
            with self.subTest(change=change), self.assertRaises(ValueError):
                self.export()
            self.assertFalse(self.output.exists())

    def test_inventory_mismatch_does_not_modify_source_or_create_output(self):
        inventory = self.root / 'inventory.json'
        inventory.write_text(json.dumps({'files': {}}), encoding='utf-8')
        before = (self.source / exporter.PACK).read_bytes()
        with self.assertRaisesRegex(ValueError, 'source-inventory-mismatch'):
            self.export(source_inventory=inventory)
        self.assertEqual((self.source / exporter.PACK).read_bytes(), before)
        self.assertFalse(self.output.exists())

    def test_typed_pixels_must_match_the_declared_canvas(self):
        path = self.source / self.rows[0]['resourcePath']
        with np.load(path, allow_pickle=False) as product:
            metadata = product['metadata'].copy()
        np.savez_compressed(path, metadata=metadata, shared_mask=np.ones((4, 4), np.uint8))
        with self.assertRaisesRegex(ValueError, 'roundtrip-typed-product'):
            self.export()
        self.assertFalse(self.output.exists())

    def test_existing_output_is_preserved_and_output_inside_source_is_rejected(self):
        self.output.write_bytes(b'user-owned-existing-output')
        with self.assertRaises(FileExistsError):
            self.export()
        self.assertEqual(self.output.read_bytes(), b'user-owned-existing-output')
        inside = self.source / 'new.zip'
        with self.assertRaisesRegex(ValueError, 'output-inside-source'):
            exporter.export_package(self.source, inside, package_id='fixture-package', resource_version='v1')
        self.assertFalse(inside.exists())

    def test_partial_output_copy_failure_allows_a_complete_retry(self):
        before = (self.source / exporter.PACK).read_bytes()

        def partial_copy(source, target, **kwargs):
            target.write(source.read(64))
            target.flush()
            raise OSError('injected-after-64-bytes')

        with patch.object(exporter.shutil, 'copyfileobj', side_effect=partial_copy):
            with self.assertRaisesRegex(OSError, 'injected-after-64-bytes'):
                self.export()
        self.assertFalse(self.output.exists())
        self.assertEqual(list(self.root.glob('.package.zip.*.tmp')), [])
        self.assertEqual((self.source / exporter.PACK).read_bytes(), before)
        result = self.export()
        self.assertEqual(result['sha256'], hashlib.sha256(self.output.read_bytes()).hexdigest())
        self.assertEqual(archives.inspect_archive(self.output).status, 'inspected')

    def publication_fixture(self):
        staged = self.root / 'validated.zip'
        with zipfile.ZipFile(staged, 'x') as archive:
            archive.writestr('payload.bin', b'x' * 256)
        raw = staged.read_bytes()
        return staged, raw, hashlib.sha256(raw).hexdigest()

    def test_flush_and_fsync_failures_leave_no_final_output_and_allow_retry(self):
        staged, raw, digest = self.publication_fixture()
        fdopen = exporter.os.fdopen

        class FlushFailure:
            def __init__(self, handle):
                self.handle = handle

            def __getattr__(self, name):
                return getattr(self.handle, name)

            def __enter__(self):
                return self

            def __exit__(self, *args):
                return self.handle.__exit__(*args)

            def flush(self):
                raise OSError('injected-flush-failure')

        def fail_writer_flush(descriptor, mode, *args, **kwargs):
            handle = fdopen(descriptor, mode, *args, **kwargs)
            return FlushFailure(handle) if mode == 'wb' else handle

        for failure in ('flush', 'fsync'):
            output = self.root / f'{failure}.zip'
            fault = (patch.object(exporter.os, 'fdopen', side_effect=fail_writer_flush)
                     if failure == 'flush' else
                     patch.object(exporter.os, 'fsync', side_effect=OSError('injected-fsync-failure')))
            with self.subTest(failure=failure), fault:
                with self.assertRaisesRegex(OSError, 'injected-' + failure + '-failure'):
                    exporter._publish_output(staged, output, digest)
            self.assertFalse(output.exists())
            self.assertEqual(list(self.root.glob(f'.{output.name}.*.tmp')), [])
            exporter._publish_output(staged, output, digest)
            self.assertEqual(output.read_bytes(), raw)
            self.assertEqual(staged.read_bytes(), raw)

    def test_replaced_temporary_is_preserved_and_does_not_block_final_retry(self):
        staged, raw, digest = self.publication_fixture()
        discard = exporter._discard_owned_temporary
        state = {}

        def partial_copy(source, target, **kwargs):
            target.write(source.read(64))
            target.flush()
            raise OSError('injected-after-64-bytes')

        def replace_before_cleanup(path, identity):
            displaced = path.with_name(path.name + '-displaced')
            path.rename(displaced)
            path.write_bytes(b'foreign-user-data')
            state.update(path=path, displaced=displaced)
            return discard(path, identity)

        with patch.object(exporter.shutil, 'copyfileobj', side_effect=partial_copy), \
                patch.object(exporter, '_discard_owned_temporary', side_effect=replace_before_cleanup):
            with self.assertRaisesRegex(OSError, 'injected-after-64-bytes') as caught:
                exporter._publish_output(staged, self.output, digest)
        self.assertFalse(self.output.exists())
        self.assertEqual(state['path'].read_bytes(), b'foreign-user-data')
        self.assertEqual(state['displaced'].read_bytes(), raw[:64])
        self.assertIn(str(state['path']), '\n'.join(caught.exception.__notes__))
        exporter._publish_output(staged, self.output, digest)
        self.assertEqual(self.output.read_bytes(), raw)
        self.assertEqual(state['path'].read_bytes(), b'foreign-user-data')
        self.assertEqual(state['displaced'].read_bytes(), raw[:64])

    def test_final_output_created_by_another_writer_is_never_replaced_or_removed(self):
        staged, _, digest = self.publication_fixture()
        move = exporter.publication._move_owned

        def create_foreign_destination(source, destination, *args, **kwargs):
            destination.write_bytes(b'foreign-final-output')
            return move(source, destination, *args, **kwargs)

        with patch.object(exporter.publication, '_move_owned', side_effect=create_foreign_destination):
            with self.assertRaises(OSError):
                exporter._publish_output(staged, self.output, digest)
        self.assertEqual(self.output.read_bytes(), b'foreign-final-output')
        self.assertEqual(list(self.root.glob('.package.zip.*.tmp')), [])


if __name__ == '__main__':
    unittest.main()
