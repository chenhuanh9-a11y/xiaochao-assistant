"""Real component reads through the unchanged entry/door/search decoders.

Synthetic graph and image fixtures exercise I/O, not game recognition quality.
Only the route-absence test substitutes geometric acceptance to reach that I/O
branch; all resource identity, JSON/NPZ decoding and image decoding stay real.
"""
import base64
import hashlib
import io
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import cv2
import numpy as np

from difficult_entry_identity import MapEntryGeometryIdentity
from difficult_side_entry_identity import MapLocalEntryIdentity
from imported_resource_packages import ComponentRef, PackageView
from imported_resource_reader import ComponentResourceReader
from shared_structure_reference import SharedStructureResourceStore
from side_door_recognition import SideDoorRecognizer
from side_entry_geometry import load_side_doors
from side_entry_search import load_index, write_index
from test_side_entry_search import graph


ENTRY_ROOT = 'references/entries'
TYPED_INDEX = 'references/declared-typed.json'
EXIT_INDEX = 'references/exit.json'
SHARED_INDEX = 'references/shared.json'
BINDING = dict(profile='native', mapId='map-a', floor='2', variant='variant-01')


def encoded(value):
    return json.dumps(value, separators=(',', ':')).encode('utf-8')


class EntryResourceIOTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.resource_root = Path(self.temp.name) / 'resources'
        self.package_root = self.resource_root / 'packages/entry-fixture/v1'
        self.component_root = self.package_root / 'data/base'
        self.component_root.mkdir(parents=True)
        self.points, self.rays, self.pairs = graph()
        self.item = dict(mapId='map-a', floor='2', unit=60., sourceProduct='source.npz',
                         routeResource='route.png', material='stone', region='fixture',
                         canvas=dict(width=1100, height=1000, layout='layout-a'), gates=[])
        turns = [dict(point=p.tolist(), rays=r.tolist(), next=int(pair[1]))
                 for p, r, pair in zip(self.points, self.rays, self.pairs)]
        self.side = dict(schemaVersion=1, mode='nightmare', profileId='native',
                         semanticGaps=[], entries=[dict(self.item, turns=turns)])
        chains = [dict(points=[self.points[i-1].tolist(), p.tolist(), self.points[(i+1) % len(self.points)].tolist()],
                       rays=self.rays[i].tolist()) for i, p in enumerate(self.points)]
        self.main = dict(schemaVersion=1, entries=[dict(self.item, chains=chains, outline=self.points.tolist())])
        self.door = dict(mapId='map-a', floor='2', layoutId='layout-a', structureVersionKey='layout/current/2',
                         sourceVersion='current', boundaryCenter=[30, 30], outwardDirection='E')
        door_data = dict(schemaVersion=1, mode='nightmare', profileId='native', entries=[self.door])
        typed = dict(entries=[dict(self.door, profile='native', resourcePath=ENTRY_ROOT+'/source.npz')])
        self.passport = dict(schemaVersion=1, mode='nightmare', profileId='native', entries=[], coverage=[])
        self.icon = np.random.default_rng(314).integers(0, 255, (8, 10), dtype=np.uint8)
        self.exit = dict(exitIcon=dict(width=10, height=8,
                                      pixels=base64.b64encode(self.icon.tobytes()).decode('ascii')), sideDoors=[])
        self.mask = np.zeros((7, 9), dtype=np.uint8)
        self.mask[1:5, 2:7] = 1
        stream = io.BytesIO()
        np.savez_compressed(stream, shared_mask=self.mask,
            metadata=json.dumps(dict(sourceKind='game-rendered-shared-structure', binding=BINDING,
                                     roles=dict(clean='shared_mask', validation='shared_mask'))))
        self.route = np.repeat((self.mask*100)[:, :, None], 3, axis=2)
        ok, image = cv2.imencode('.png', self.route)
        self.assertTrue(ok)
        self.files = {
            ENTRY_ROOT+'/entries.json': encoded(self.main),
            ENTRY_ROOT+'/side-entries.json': encoded(self.side),
            ENTRY_ROOT+'/side-door-anchors.json': encoded(door_data),
            ENTRY_ROOT+'/entry-passports.json': encoded(self.passport),
            ENTRY_ROOT+'/route.png': image.tobytes(),
            ENTRY_ROOT+'/source.npz': stream.getvalue(),
            TYPED_INDEX: encoded(typed),
            EXIT_INDEX: encoded(self.exit),
            SHARED_INDEX: encoded(dict(schemaVersion=1, resourceKind='shared-structure',
                entries=[dict(BINDING, resourcePath=ENTRY_ROOT+'/source.npz')]))}
        prepared_path = Path(self.temp.name) / 'side-entry-search.npz'
        write_index(prepared_path, [(self.item, self.points, self.rays, self.pairs)], {'map-a': self.door})
        self.files[ENTRY_ROOT+'/side-entry-search.npz'] = prepared_path.read_bytes()

    def package(self, *, omit=(), guard=None):
        for relative, raw in self.files.items():
            path = self.component_root / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(raw)
        manifest = dict(schema='xingtu-resource-package', formatVersion=1, packageId='entry-fixture',
            resourceVersion='v1', clientCompatibility=dict(resourceProtocol=1, requiredCapabilities=[]),
            components=[dict(componentId='base', type='recognition-base', schemaVersion=1,
                contexts=[dict(season='s0', mode='nightmare')], entrypoint=SHARED_INDEX, dependencies={})],
            files=[dict(componentId='base', path=relative, bytes=len(raw), sha256=hashlib.sha256(raw).hexdigest())
                   for relative, raw in self.files.items() if relative not in omit])
        raw = encoded(manifest)
        (self.package_root / 'manifest.json').write_bytes(raw)
        view = PackageView(self.resource_root, ComponentRef('entry-fixture', 'v1', 'base', hashlib.sha256(raw).hexdigest()),
                           assert_current=guard)
        reader = ComponentResourceReader(view)
        reads = []
        def read_resource(path):
            reads.append(Path(path).relative_to(self.component_root).as_posix())
            return reader(path)
        self.view, self.reader, self.reads = view, read_resource, reads
        self.root = self.component_root / ENTRY_ROOT
        self.store = SharedStructureResourceStore(self.component_root / SHARED_INDEX, self.component_root,
                                                  read_resource=read_resource)
        self.assertIsNone(self.store.error)
        return view

    def local(self, *, reader=True, route_loader=None):
        return MapLocalEntryIdentity(self.root, mode='nightmare', profile_id='native',
            exit_icon_path=self.component_root / EXIT_INDEX, structure_store=self.store,
            route_loader=route_loader, typed_index_path=self.component_root / TYPED_INDEX,
            read_resource=self.reader if reader else None)

    def front(self, *, reader=True):
        return MapEntryGeometryIdentity(self.root, profile_id='native', structure_store=self.store,
                                        read_resource=self.reader if reader else None)

    def test_constructors_read_all_declared_children_and_match_original_arrays(self):
        self.package()
        front, local = self.front(), self.local()
        direct_front, direct_local = self.front(reader=False), self.local(reader=False)
        self.assertIsNone(front.error)
        self.assertIsNone(local.side_door_error)
        self.assertIsNone(local.side_search_error)
        self.assertIsNone(local.passport_error)
        for actual, expected in zip(front.entries[0][1:], direct_front.entries[0][1:]):
            np.testing.assert_array_equal(actual, expected)
        for actual, expected in zip(local.entries[0][1:], direct_local.entries[0][1:]):
            np.testing.assert_array_equal(actual, expected)
        for name, value in direct_local.side_search['map-a', '2'].items():
            np.testing.assert_array_equal(local.side_search['map-a', '2'][name], value)
        self.assertEqual(local.passports, direct_local.passports)
        self.assertTrue(local.has_registered_floor('map-a', '2'))
        self.assertTrue({ENTRY_ROOT+'/'+name for name in ('entries.json', 'side-entries.json',
            'side-door-anchors.json', 'side-entry-search.npz', 'entry-passports.json')} <= set(self.reads))
        self.assertIn(TYPED_INDEX, self.reads)
        self.assertNotIn('references/nightmare-shared-structure-index.json', self.reads)

    def test_lazy_exit_uses_same_reader_on_first_actual_observation(self):
        self.package()
        local = self.local()
        self.assertNotIn(EXIT_INDEX, self.reads)
        frame = np.zeros((200, 400, 3), dtype=np.uint8)
        context = dict(observationBounds=(120, 40, 280, 150))
        first = local._entry_observations(frame, context=context)
        second = local._entry_observations(frame, context=context)
        np.testing.assert_array_equal(local._exit_recognizer.exit_icon, self.icon)
        self.assertEqual(first, second)
        self.assertEqual(self.reads.count(EXIT_INDEX), 1)

    def test_identity_engine_threads_reader_into_both_entries_and_lazy_exit(self):
        from map_identity_engine import MapIdentityEngine
        current = [True]
        def guard():
            if not current[0]:
                raise ValueError('catalog superseded')
        self.package(guard=guard)
        # Only the pack DTO and unused personal memory are synthetic; actual
        # engine/entry constructors, PackageView, NPZ and lazy exit reads run.
        item = SimpleNamespace(id='map-a', layers={'2': self.root/'source.npz'})
        pack = SimpleNamespace(mode='nightmare', maps=[item], by_id={'map-a': item})
        shared = lambda *_: None
        engine = MapIdentityEngine(pack, memory_store=object(), identity_root=self.root,
            identity_profile_id='native', identity_floors=('2',),
            exit_icon_path=self.component_root/EXIT_INDEX,
            entry_structure_store=self.store, shared_reference_loader=shared,
            entry_read_resource=self.reader, entry_typed_index_path=self.component_root/TYPED_INDEX)
        self.assertIsNone(engine.entry_identity.error)
        self.assertIsNone(engine.side_entry_error)
        self.assertIsNone(engine.side_entry_identity.side_search_error)
        self.assertIn(ENTRY_ROOT+'/entries.json', self.reads)
        self.assertIn(ENTRY_ROOT+'/side-entries.json', self.reads)
        self.assertIn(TYPED_INDEX, self.reads)
        self.assertNotIn(EXIT_INDEX, self.reads)
        current[0] = False
        with self.assertRaisesRegex(ValueError, 'catalog superseded'):
            engine.side_entry_identity._entry_observations(np.zeros((200, 400, 3), np.uint8))

    def test_undeclared_and_changed_main_file_fail_even_when_disk_decoder_works(self):
        relative = ENTRY_ROOT+'/entries.json'
        self.package(omit=[relative])
        self.assertIsNone(self.front(reader=False).error)
        self.assertIn('file-not-declared', self.front().error)
        self.package()
        (self.root/'entries.json').write_bytes(self.files[relative]+b' ')
        self.assertIsNone(self.front(reader=False).error)
        self.assertIn('integrity-mismatch', self.front().error)

    def test_expired_view_rejects_constructor_and_lazy_exit_without_disk_fallback(self):
        current = [True]
        def guard():
            if not current[0]:
                raise ValueError('catalog superseded')
        self.package(guard=guard)
        local = self.local()
        current[0] = False
        self.assertIn('catalog superseded', self.front().error)
        with self.assertRaisesRegex(ValueError, 'catalog superseded'):
            self.local()
        with self.assertRaisesRegex(ValueError, 'catalog superseded'):
            local._entry_observations(np.zeros((200, 400, 3), np.uint8))
        self.assertIsNone(SideDoorRecognizer(self.component_root / EXIT_INDEX).marked_retriever)

    def test_optional_npz_and_passports_cannot_fall_back_to_valid_undeclared_files(self):
        self.package(omit=[ENTRY_ROOT+'/side-entry-search.npz', ENTRY_ROOT+'/entry-passports.json'])
        local = self.local()
        direct = self.local(reader=False)
        self.assertEqual(local.side_search, {})
        self.assertIn('file-not-declared', local.side_search_error)
        self.assertIn('file-not-declared', local.passport_error)
        self.assertIsNone(direct.side_search_error)
        self.assertIsNone(direct.passport_error)

    def test_reader_mode_requires_store_and_does_not_create_legacy_store(self):
        self.package()
        with patch('author_structure_resources.AuthorStructureResourceStore', side_effect=AssertionError('legacy store entered')):
            for constructor in (MapEntryGeometryIdentity, MapLocalEntryIdentity):
                with self.subTest(constructor=constructor.__name__), self.assertRaisesRegex(ValueError, 'requires-structure-store'):
                    constructor(self.root, read_resource=self.reader)

    def test_route_absence_and_explicit_loader_are_authoritative(self):
        self.package()
        local, front = self.local(), self.front()
        with patch('nightmare_candidate.imread_unicode', side_effect=AssertionError('disk fallback entered')):
            self.assertIsNone(local._load_route('native', 'map-a', '2', 'route.png'))
            # Only substitute geometry to reach the existing route I/O branch.
            # The store still decodes the real NPZ through PackageView.
            with patch('single_map_structure.regional_validation', return_value=dict(regionalEdgeVerified=True)), \
                 patch('single_map_structure._static_validation_context', return_value={}), \
                 patch('single_map_structure.partial_wall_geometry', return_value=dict(success=False, reason='fixture', diagnostics={})):
                result = front.verify(np.zeros((7, 9, 3), np.uint8),
                    dict(mapId='map-a', hypothesis=dict(scale=1., translation=[0., 0.])),
                    context=dict(query=self.mask))
            self.assertFalse(result['success'])
        def route_loader(profile, map_id, floor):
            self.assertEqual((profile, map_id, floor), ('native', 'map-a', '2'))
            return cv2.imdecode(np.frombuffer(self.reader(self.root/'route.png'), np.uint8), cv2.IMREAD_UNCHANGED)
        loaded = self.local(route_loader=route_loader)._load_route('native', 'map-a', '2', 'unused.png')
        np.testing.assert_array_equal(loaded, self.route)
        self.assertIn(ENTRY_ROOT+'/route.png', self.reads)
        self.assertIsNone(self.local(route_loader=lambda *_: None)._load_route('native', 'map-a', '2', 'route.png'))

    def test_main_chain_and_side_turn_limits_reject_before_numpy_conversion(self):
        self.main['entries'][0]['chains'] *= 65  # 520 chains, beyond measured 124 with margin.
        self.files[ENTRY_ROOT+'/entries.json'] = encoded(self.main)
        self.side['entries'][0]['turns'] *= 129  # 1032 turns; measured maximum is 268.
        self.files[ENTRY_ROOT+'/side-entries.json'] = encoded(self.side)
        self.package()
        with patch('difficult_entry_identity.np.array', side_effect=AssertionError('numeric allocation entered')):
            self.assertIn('complexity: main chains', self.front().error)
        with patch('difficult_side_entry_identity.np.asarray', side_effect=AssertionError('numeric allocation entered')):
            with self.assertRaisesRegex(ValueError, 'complexity: side turns'):
                self.local()

    def test_search_pair_limit_rejects_before_reader_and_numpy_loader(self):
        self.package()
        entries = [(self.item, self.points, self.rays, np.tile(self.pairs, (129, 1)))]
        with patch('numpy.load', side_effect=AssertionError('numeric archive entered')):
            result, error = load_index(self.root, entries, {'map-a': self.door}, read_resource=self.reader)
        self.assertEqual(result, {})
        self.assertIn('complexity: side search graph', error)
        self.assertNotIn(ENTRY_ROOT+'/side-entry-search.npz', self.reads)

    def test_nested_passport_wall_values_are_rejected_before_correspondence(self):
        self.passport['entries'] = [dict(center=[0, 0], localInstances=[], knownWallLines=[
            dict(axis=[0]*2048, sign=[1]*2048, normal=[0]*2048, lo=[0]*2048, hi=[1]*2048)])]
        self.files[ENTRY_ROOT+'/entry-passports.json'] = encoded(self.passport)
        self.package()
        local = self.local()
        self.assertEqual(local.passports, [])
        self.assertIn('invalid entry wall coordinates', local.passport_error)

    def test_declared_typed_index_and_door_rejection_never_use_default_disk_index(self):
        self.package(omit=[TYPED_INDEX])
        default_index = self.component_root/'references/nightmare-shared-structure-index.json'
        default_index.write_bytes(self.files[TYPED_INDEX])
        entries = [(self.item, self.points, self.rays, self.pairs)]
        self.assertIsNone(load_side_doors(self.root, entries, 'nightmare', 'native')[1])
        doors, error = load_side_doors(self.root, entries, 'nightmare', 'native',
            typed_index_path=self.component_root/TYPED_INDEX, read_resource=self.reader)
        self.assertEqual(doors, {})
        self.assertIn('file-not-declared', error)

    def test_taxonomy_and_audited_reference_image_use_reader_and_match_disk_decode(self):
        descriptor = base64.b64encode(bytes(9*9)).decode('ascii')
        self.exit['sideDoors'] = [dict(mapId='map-24', contextAnchorTopology=dict(approach='left'),
                                      contextAnchorDescriptor=descriptor)]
        self.files[EXIT_INDEX] = encoded(self.exit)
        self.files['references/taxonomy.json'] = encoded(dict(records=[dict(map_id='map-24', shape_category='fixture')]))
        image = np.full((384, 384, 3), 100, dtype=np.uint8)
        ok, compressed = cv2.imencode('.png', image)
        self.assertTrue(ok)
        self.files['references/map-24.webp'] = compressed.tobytes()
        self.package()
        source, taxonomy = self.component_root/EXIT_INDEX, self.component_root/'references/taxonomy.json'
        direct = SideDoorRecognizer(source, taxonomy)
        via_reader = SideDoorRecognizer(source, taxonomy, read_resource=self.reader)
        self.assertEqual(via_reader.taxonomy_by_map, direct.taxonomy_by_map)
        self.assertIn('references/taxonomy.json', self.reads)
        self.assertIn('references/map-24.webp', self.reads)
        for name in ('context', 'wide_context', 'full_context', 'wide_room', 'full_room'):
            np.testing.assert_array_equal(getattr(via_reader.references[0], name), getattr(direct.references[0], name))
        self.assertGreater(via_reader.references[0].context.sum(), 0)
        # Available legacy files never authorize an undeclared repair image.
        self.package(omit=['references/map-24.webp'])
        with self.assertRaisesRegex(ValueError, 'file-not-declared'):
            SideDoorRecognizer(source, taxonomy, read_resource=self.reader)

    def test_reader_mode_rejects_unsupported_marked_poster_loader(self):
        self.package()
        with self.assertRaisesRegex(ValueError, 'marked-poster-unsupported'):
            SideDoorRecognizer(self.component_root/EXIT_INDEX, marked_poster_path=self.root/'poster.json',
                               read_resource=self.reader)


if __name__ == '__main__':
    unittest.main()
