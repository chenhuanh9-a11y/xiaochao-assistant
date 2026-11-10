"""S1D native producer round trips over registered 14-map synthetic resources."""
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
import sys
from unittest.mock import patch

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / 'app'), str(ROOT / 'tools')]

import export_native_base_package as exporter
import imported_resource_archive as archives
from imported_native_base import decode_native_base
from imported_resource_packages import PackageView
from imported_resource_reader import ComponentResourceReader
from imported_s1_controls_profile import S1ControlsProfile
from nightmare_author_coordinate_adapter import CANONICAL_SCHEMA
from s1_native_resources import MODE_LAYOUTS, SOURCE_VERSION


class S1NativeBaseSourceFixture:
    """Small, internally consistent source tree reusable by importer tests.

    The identity competition is the registered full S1 difficult pool, while
    typed canvases stay tiny so tests exercise the real archive/readers cheaply.
    """

    def __init__(self, root, *, package_id='fixture-s1-difficult', resource_version='fixture-v1'):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self.sourceRoot = self.root / 'source-root'
        self.prefixRoot = self.sourceRoot / exporter.S1_PREFIX
        self.inventory = self.root / 'source-inventory.json'
        self.output = self.root / 's1-native-base.zip'
        self.package_id = package_id
        self.resource_version = resource_version
        self.ids = tuple(sorted(MODE_LAYOUTS['difficult']))
        self.floors = exporter.S1_FLOORS
        self.pack_path = exporter.S1_PACK
        self.index_path = exporter.S1_INDEX
        self.versions_path = 'references/structure-version-bindings.json'
        self.coordinates_path = exporter.S1_COORDINATES
        self.neutral_path = exporter.S1_NEUTRAL
        self.entries_path = exporter.S1_ENTRIES
        self.resources = {}
        self._build()

    def _write_bytes(self, relative, raw):
        path = self.prefixRoot / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(raw)
        return raw

    def _write_root_bytes(self, relative, raw):
        path = self.sourceRoot / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(raw)
        return raw

    def write_json(self, relative, value):
        return self._write_bytes(relative, json.dumps(value, ensure_ascii=False,
            sort_keys=True, separators=(',', ':')).encode('utf-8'))

    def read_json(self, relative):
        return json.loads((self.prefixRoot / relative).read_bytes())

    def _build(self):
        typed_rows, version_rows, neutral_rows = [], [], []
        pack_maps, coordinate_maps, shared_maps = [], {}, {}
        identity_profiles = {}
        for mid in self.ids:
            raw_hash = hashlib.sha256(('fixture-structure:' + mid).encode()).hexdigest()
            identity_profiles[mid] = 'shared-structure'
            layers = {}
            coordinate_floors, profile_floors = {}, {}
            for floor in self.floors:
                resource = f'references/structures/{mid}-{floor}.npz'
                binding = dict(profile='shared-structure', mapId=mid, floor=floor, variant='variant-01')
                version_key = f'{mid}@{raw_hash}/{floor}'
                base_snapshot = 'fixture-base-s1-v1'
                canvas = dict(width=8, height=8, shapeHW=[8, 8], layout=mid, floor=floor,
                              coordinateSpace='fixture-native:' + mid + ':' + floor)
                common = dict(structureVersionKey=version_key, sourceVersion=SOURCE_VERSION,
                              baseSnapshot=base_snapshot)
                metadata = dict(sourceKind='game-rendered-shared-structure', binding=binding,
                    canvas=canvas, roles=dict(clean='shared_mask', validation='shared_mask'), **common)
                target = self.prefixRoot / resource
                target.parent.mkdir(parents=True, exist_ok=True)
                np.savez_compressed(target, metadata=np.asarray(json.dumps(metadata, ensure_ascii=False)),
                                    shared_mask=np.ones((8, 8), np.uint8))
                self.resources[mid, floor] = resource
                typed_rows.append(dict(binding, resourcePath=resource, **common))
                neutral_rows.append(dict(mapId=mid, floor=floor, binding=binding,
                    referenceResource=resource, canvas=canvas, **common))
                layers[floor] = resource
                resource_key = dict(layoutId=mid, structureRawRecordSha256=raw_hash, floor=floor)
                version_rows.append(dict(mapId=mid, resourceKey=resource_key,
                    typedReference=resource, sharedLookup=dict(binding, resourcePath=resource),
                    neutralDisplay=dict(baseResourceKey=resource_key), **common))
                coordinate_floors[floor] = dict(referenceResource=resource, sourceVersion=SOURCE_VERSION,
                    structureVersionKey=version_key, coordinateWidth=8, coordinateHeight=8,
                    matrixCanonicalToLegacyRecognition=np.eye(3).tolist())
                profile_floors[floor] = dict(referenceResource=resource, sourceVersion=SOURCE_VERSION,
                    structureVersionKey=version_key, matrixProfileToCanonical=np.eye(3).tolist(),
                    usableForCurrentFrameRendering=True, crossFloorProjectionAllowed=False,
                    evidence=dict(algebraicallyExact=True))
            coordinate_maps[mid] = dict(floors=coordinate_floors)
            shared_maps[mid] = dict(floors=profile_floors)
            pack_maps.append(dict(id=mid, name='Fixture ' + mid, direction='unknown', doorCount=0,
                shape='native', sourceLayout=mid, sourceVersion=SOURCE_VERSION,
                structureContentSha256=raw_hash, identityProfileId='shared-structure',
                routeProfileId='shared-structure', oneFlow=None, sideDoorCrop=[0, 0, 8, 8],
                layers=layers))

        self.write_json(self.pack_path, dict(schemaVersion=1, season='s1', mode='difficult',
            packId='fixture-s1-difficult', displayName='Fixture S1 difficult',
            floors=list(self.floors), defaultEntryFloor=-1, identityProfiles=identity_profiles,
            maps=pack_maps))
        self.write_json(self.index_path, dict(schemaVersion=1, resourceKind='shared-structure',
            structureVersionBindings=self.versions_path, entries=typed_rows))
        self.write_json(self.versions_path, dict(schemaVersion=1,
            resourceKind='native-structure-version-bindings', entries=version_rows))
        self.write_json(self.neutral_path, dict(schemaVersion=1,
            resourceKind='typed-shared-structure-display', indexPath=self.index_path,
            entries=neutral_rows))
        self.write_json(self.coordinates_path, dict(schema=CANONICAL_SCHEMA,
            canonicalPackId='fixture-s1-canonical', coordinateReferenceProfileId='shared-structure',
            policy=dict(requiresCurrentFrameGeometryFit=True, authorizesMapIdentity=False,
                        authorizesCrossFloorProjection=False), maps=coordinate_maps,
            profiles={'shared-structure': dict(maps=shared_maps)},
            canonicalCrossFloorSearchCandidates={}))

        main_entries, local_entries = [], []
        for mid in self.ids:
            for floor in self.floors:
                resource = self.resources[mid, floor]
                filename = Path(resource).name
                typed = next(row for row in typed_rows if row['mapId'] == mid and row['floor'] == floor)
                common = dict(mapId=mid, floor=floor, profileId='shared-structure', variantId='variant-01',
                    sourceKind='game-rendered-shared-structure', sourceVersion=SOURCE_VERSION,
                    structureVersionKey=typed['structureVersionKey'], baseSnapshot=typed['baseSnapshot'],
                    unit=12., sourceProduct=filename, structureResource='../structures/' + filename,
                    sourceIndex='../shared-structure-index.json', canvas=neutral_rows[
                        self.floors.index(floor) + self.ids.index(mid) * len(self.floors)]['canvas'], gates=[],
                    knownWallSupportUnavailable=True)
                turns = [dict(point=point, rays=[[1., 0.], [0., 1.]], next=(index + 1) % 4)
                         for index, point in enumerate(([1., 1.], [6., 1.], [6., 6.], [1., 6.]))]
                local_entries.append(dict(common, region='fixture-region', featureRole=
                    'local-contour-proposals-not-semantic-side-exits',
                    extractorEnvironment=dict(opencv=self._opencv_version()), turns=turns))
                if floor == '-1':
                    main_entries.append(dict(common, floor='-1', material='fixture-stone', kind='main',
                        sourceKind='game-entry-room-proposal',
                        proposalOnly=True, authorizesPose=False, authorizesIdentity=False,
                        center=[4., 4.], outline=[[1., 1.], [6., 1.], [6., 6.], [1., 6.]],
                        chains=[dict(points=[[1., 1.], [2., 2.], [3., 3.]],
                                     rays=[[1., 0.], [0., 1.]])]))
        self.write_json(self.entries_path + '/entries.json', dict(schemaVersion=1, season='s1',
            mode='difficult', profileId='shared-structure', entries=main_entries,
            semanticGaps=['fixture-main-authority-unavailable']))
        self.write_json(self.entries_path + '/side-entries.json', dict(schemaVersion=1, season='s1',
            mode='difficult', profileId='shared-structure', entries=local_entries,
            semanticGaps=['fixture-semantic-side-exits-unavailable']))
        self.write_json(self.entries_path + '/entry-passports.json', dict(schemaVersion=1, season='s1',
            mode='difficult', profileId='shared-structure', entries=[], coverage=[]))

        # These source-only display declarations are deliberately inventoried,
        # but are not part of the portable recognition base.
        self.write_json('references/author-catalog.json', dict(schemaVersion=1, profiles=[]))
        self._write_bytes('assets/game-native/fixture.png', b'fixture-display-only')
        self._write_bytes('assets/author/fixture.png', b'fixture-author-display-only')
        manifest_path = self.prefixRoot / 'native-map-manifest.json'
        interfaces = dict(identityPack=self.pack_path, typedIndex=self.index_path,
            versionBindings=self.versions_path, coordinatePack=self.coordinates_path,
            neutralDisplay=self.neutral_path, identityEntries=self.entries_path,
            authorCatalog='references/author-catalog.json')
        manifest_files = []
        for path in sorted(p for p in self.prefixRoot.rglob('*') if p.is_file() and p != manifest_path):
            relative = path.relative_to(self.prefixRoot).as_posix()
            raw = path.read_bytes()
            manifest_files.append(dict(path=relative, bytes=len(raw), sha256=hashlib.sha256(raw).hexdigest()))
        self.write_json('native-map-manifest.json', dict(schemaVersion=1, season='s1', mode='difficult',
            sourceVersion=SOURCE_VERSION, runtimeInterfaces=interfaces,
            opencvVersion=self._opencv_version(), capabilities=dict(sideEntries=False, floorControls=False),
            files=manifest_files))
        self._write_root_bytes('seasons/s1/nightmare/unrelated.bin', b'other-context-must-not-be-exported')
        self._write_root_bytes('references/unrelated-root.bin', b'outside-mode-prefix-must-not-be-exported')
        self.refresh_inventory()

    @staticmethod
    def _opencv_version():
        import cv2
        return cv2.__version__

    def refresh_manifest(self):
        path = self.prefixRoot / 'native-map-manifest.json'
        manifest = json.loads(path.read_bytes())
        rows = []
        for source_path in sorted(p for p in self.prefixRoot.rglob('*') if p.is_file() and p != path):
            relative = source_path.relative_to(self.prefixRoot).as_posix()
            raw = source_path.read_bytes()
            rows.append(dict(path=relative, bytes=len(raw), sha256=hashlib.sha256(raw).hexdigest()))
        manifest['files'] = rows
        path.write_text(json.dumps(manifest, ensure_ascii=False, sort_keys=True, separators=(',', ':')),
                        encoding='utf-8')

    def refresh_inventory(self):
        rows = {}
        for path in self.sourceRoot.rglob('*'):
            if not path.is_file():
                continue
            relative = path.relative_to(self.sourceRoot).as_posix()
            raw = path.read_bytes()
            rows[relative] = dict(bytes=len(raw), sha256=hashlib.sha256(raw).hexdigest())
        self.inventory.write_text(json.dumps(dict(files=rows), sort_keys=True, separators=(',', ':')),
                                  encoding='utf-8')

    def refresh_after_source_edit(self):
        self.refresh_manifest()
        self.refresh_inventory()

    def export(self, **kwargs):
        return exporter.export_package(self.sourceRoot, self.output, package_id=self.package_id,
            resource_version=self.resource_version, source_inventory=self.inventory,
            season='s1', mode='difficult', **kwargs)


class S1NativeBaseExporterTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.fixture = S1NativeBaseSourceFixture(self.root)

    def _installed(self):
        saved = archives.save_archive(self.fixture.output, self.root / 'installed')
        return {ref.component_id: PackageView(self.root / 'installed', ref) for ref in saved.component_refs}

    def test_s1_difficult_full_inventory_roundtrips_and_keeps_proposals_unprivileged(self):
        before = {path.relative_to(self.fixture.sourceRoot).as_posix(): path.read_bytes()
                  for path in self.fixture.sourceRoot.rglob('*') if path.is_file()}
        result = self.fixture.export()
        self.assertEqual((result['season'], result['mode'], result['mapCount'], result['typedLayerCount']),
                         ('s1', 'difficult', 14, 56))
        self.assertEqual(result['sourceInventoryFiles'], len(json.loads(self.fixture.inventory.read_bytes())['files']))
        self.assertEqual(result['sha256'], hashlib.sha256(self.fixture.output.read_bytes()).hexdigest())
        self.assertEqual(before, {path.relative_to(self.fixture.sourceRoot).as_posix(): path.read_bytes()
                                  for path in self.fixture.sourceRoot.rglob('*') if path.is_file()})
        views = self._installed()
        base, controls_view = views['base'], views['controls']
        reader = ComponentResourceReader(base)
        context = reader.json('resource-context.json')
        self.assertEqual(context, dict(schema='xingtu-native-recognition-base-v1', season='s1', mode='difficult',
            runtimeInterfaces=exporter.S1_INTERFACES))
        self.assertNotIn('exitIcon', context['runtimeInterfaces'])
        self.assertFalse(any(('side-door' in path or 'side-entry-search' in path or 'exit-icon' in path
                              or 'author-catalog' in path or path.startswith('assets/'))
                             for path in base.inventory))
        root_inventory = json.loads(self.fixture.inventory.read_bytes())['files']
        self.assertIn('seasons/s1/nightmare/unrelated.bin', root_inventory)
        self.assertIn('references/unrelated-root.bin', root_inventory)
        self.assertNotIn('seasons/s1/nightmare/unrelated.bin', base.inventory)
        self.assertNotIn('references/unrelated-root.bin', base.inventory)
        self.assertFalse((self.root / 'installed' / 'catalog.json').exists())

        decoded = decode_native_base(base, season='s1', mode='difficult')
        self.assertEqual((decoded.season, decoded.mode), ('s1', 'difficult'))
        self.assertEqual(len(decoded.pack.maps), 14)
        self.assertEqual(len(decoded.resources.entries), 56)
        self.assertIsNone(decoded.exit_recognizer)
        self.assertEqual(decoded.gaps, ())
        self.assertEqual(decoded.side_entry_identity.side_doors, {})
        self.assertEqual(decoded.side_entry_identity.side_search, {})
        self.assertEqual(decoded.side_entry_identity.passports, [])
        self.assertEqual(decoded.side_entry_identity.entry_coverage, [])
        self.assertTrue(all(item[0]['featureRole'] == 'local-contour-proposals-not-semantic-side-exits'
                            and item[0]['gates'] == [] for item in decoded.side_entry_identity.entries))
        controls = S1ControlsProfile(controls_view)
        control_context = controls.for_context('s1', 'difficult')
        self.assertIsNotNone(control_context)
        self.assertEqual(control_context.contract.floor_ids, self.fixture.floors)
        np.testing.assert_allclose(controls.fixed_search_region, (.80, .12, .12, .44))
        np.testing.assert_allclose(controls.expanded_search_region, (.55, .04, .435, .89))
        self.assertIsNone(controls.for_context('s1', 'nightmare'))

        source_coordinates = self.fixture.read_json(self.fixture.coordinates_path)
        output_coordinates = reader.json(exporter.S1_COORDINATES)
        for mid in self.fixture.ids:
            for floor in self.fixture.floors:
                self.assertEqual(output_coordinates['maps'][mid]['floors'][floor]
                                 ['matrixCanonicalToLegacyRecognition'],
                                 source_coordinates['maps'][mid]['floors'][floor]
                                 ['matrixCanonicalToLegacyRecognition'])
        for name in ('entries.json', 'side-entries.json', 'entry-passports.json'):
            self.assertEqual(base.read(exporter.S1_ENTRIES + '/' + name),
                (self.fixture.prefixRoot / self.fixture.entries_path / name).read_bytes())
        self.assertEqual(base.read(exporter.S1_PACK), (self.fixture.prefixRoot / self.fixture.pack_path).read_bytes())
        self.assertEqual(base.read(exporter.S1_INDEX), (self.fixture.prefixRoot / self.fixture.index_path).read_bytes())
        self.assertEqual(base.read(exporter.S1_NEUTRAL), (self.fixture.prefixRoot / self.fixture.neutral_path).read_bytes())

    def test_s1_competition_floor_and_required_source_fail_closed(self):
        mutations = ('missing-map', 'bad-floor', 'missing-required')
        for change in mutations:
            with self.subTest(change=change):
                temp = tempfile.TemporaryDirectory()
                self.addCleanup(temp.cleanup)
                fixture = S1NativeBaseSourceFixture(Path(temp.name))
                if change == 'missing-map':
                    pack = fixture.read_json(fixture.pack_path)
                    pack['maps'].pop()
                    fixture.write_json(fixture.pack_path, pack)
                elif change == 'bad-floor':
                    index = fixture.read_json(fixture.index_path)
                    index['entries'][0]['floor'] = '-5'
                    fixture.write_json(fixture.index_path, index)
                else:
                    required = fixture.prefixRoot / fixture.entries_path / 'entry-passports.json'
                    required.unlink()
                fixture.refresh_after_source_edit()
                with self.assertRaises(ValueError):
                    fixture.export()
                self.assertFalse(fixture.output.exists())

    def test_wrong_root_source_inventory_rejects_without_source_or_output_changes(self):
        before = (self.fixture.prefixRoot / self.fixture.pack_path).read_bytes()
        inventory = json.loads(self.fixture.inventory.read_bytes())
        key = self.fixture.pack_path
        inventory['files']['seasons/s1/difficult/' + key]['sha256'] = '0' * 64
        self.fixture.inventory.write_text(json.dumps(inventory), encoding='utf-8')
        with self.assertRaisesRegex(ValueError, 'source-inventory-mismatch'):
            self.fixture.export()
        self.assertFalse(self.fixture.output.exists())
        self.assertEqual((self.fixture.prefixRoot / self.fixture.pack_path).read_bytes(), before)

    def test_s1_nightmare_is_explicitly_unsupported(self):
        with self.assertRaisesRegex(ValueError, 'context-unsupported'):
            exporter.export_package(self.fixture.sourceRoot, self.fixture.output,
                package_id='fixture-s1-nightmare', resource_version='fixture-v1',
                source_inventory=self.fixture.inventory, season='s1', mode='nightmare')
        self.assertFalse(self.fixture.output.exists())

    def test_s0_roundtrip_rejects_empty_permission_error_from_entry_consumer(self):
        from test_export_native_base_package import NativeBaseExporterTests as S0SourceFixtureCase
        import difficult_side_entry_identity as side_entry_module
        from difficult_side_entry_identity import MapLocalEntryIdentity as RealMapLocalEntryIdentity
        import shared_structure_reference as shared_module
        from shared_structure_reference import SharedStructureResourceStore as RealStructureStore

        cases = (
            ('entry-passports', exporter.ENTRIES + '/entry-passports.json', 'roundtrip-entry'),
            ('shared-index', exporter.INDEX, 'roundtrip-typed-index'),
        )
        for case, relative_failure_path, expected_reason in cases:
            with self.subTest(case=case):
                fixture = S0SourceFixtureCase('test_real_roundtrip_preserves_full_competition_and_source_bytes')
                fixture.setUp()
                try:
                    injected_reads = []
                    local_instances = []
                    structure_stores = []
                    real_reader_call = ComponentResourceReader.__call__

                    def fail_one_read(reader, path):
                        relative = Path(path).resolve().relative_to(reader._canonical_root).as_posix()
                        if relative == relative_failure_path and not injected_reads:
                            injected_reads.append(relative)
                            raise PermissionError()
                        return real_reader_call(reader, path)

                    def capture_real_local_entry(*args, **kwargs):
                        local = RealMapLocalEntryIdentity(*args, **kwargs)
                        local_instances.append(local)
                        return local

                    def capture_real_structure_store(*args, **kwargs):
                        store = RealStructureStore(*args, **kwargs)
                        structure_stores.append(store)
                        return store

                    with patch.object(ComponentResourceReader, '__call__', new=fail_one_read), \
                            patch.object(side_entry_module, 'MapLocalEntryIdentity',
                                         new=capture_real_local_entry), \
                            patch.object(shared_module, 'SharedStructureResourceStore',
                                         new=capture_real_structure_store):
                        with self.assertRaisesRegex(ValueError, expected_reason):
                            fixture.export()
                    self.assertEqual(injected_reads, [relative_failure_path])
                    if case == 'entry-passports':
                        self.assertEqual(len(local_instances), 1)
                        self.assertEqual(local_instances[0].passport_error, '')
                    else:
                        self.assertEqual(len(structure_stores), 1)
                        self.assertEqual(structure_stores[0].error, '')
                        self.assertEqual(structure_stores[0].entries, {})
                    self.assertFalse(fixture.output.exists())
                finally:
                    fixture.doCleanups()


if __name__ == '__main__':
    unittest.main()
