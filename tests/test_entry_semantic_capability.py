"""S1 local contours keep their real structure data without exit semantics."""
import hashlib
import json
from pathlib import Path
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / 'app'), str(ROOT / 'tools'), str(ROOT / 'tests')]

from difficult_side_entry_identity import MapLocalEntryIdentity
from imported_resource_packages import ComponentRef, PackageView
from imported_resource_reader import ComponentResourceReader
from shared_structure_reference import SharedStructureResourceStore
from test_export_s1_native_base_package import S1NativeBaseSourceFixture


class EntrySemanticCapabilityTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix='entry-semantic-capability-')
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name).resolve()

    def package(self, mode='difficult', *, include_passports=True):
        """Build a real PackageView inventory from the S1 source fixture."""
        source = S1NativeBaseSourceFixture(self.root / ('source-' + mode))
        package_root = self.root / ('resources-' + mode) / 'packages/s1-entry-fixture/v1'
        component_root = package_root / 'data/base'
        component_root.mkdir(parents=True)
        inventory = []
        for source_path in source.prefixRoot.rglob('*'):
            if not source_path.is_file():
                continue
            relative = source_path.relative_to(source.prefixRoot).as_posix()
            if not include_passports and relative == source.entries_path + '/entry-passports.json':
                continue
            raw = source_path.read_bytes()
            # The nightmare subtest is a declaration-only seam fixture. Its
            # local contour/passport payload remains the actual nonempty S1
            # source fixture; the package context and its declarations agree.
            if mode == 'nightmare' and relative in (
                    source.entries_path + '/side-entries.json',
                    source.entries_path + '/entry-passports.json'):
                payload = json.loads(raw)
                payload['mode'] = 'nightmare'
                raw = json.dumps(payload, ensure_ascii=False, sort_keys=True,
                                 separators=(',', ':')).encode('utf-8')
            target = component_root / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(raw)
            inventory.append(dict(componentId='base', path=relative, bytes=len(raw),
                                  sha256=hashlib.sha256(raw).hexdigest()))

        manifest = dict(schema='xingtu-resource-package', formatVersion=1,
            packageId='s1-entry-fixture', resourceVersion='v1',
            clientCompatibility=dict(resourceProtocol=1, requiredCapabilities=[]),
            components=[dict(componentId='base', type='recognition-base', schemaVersion=1,
                contexts=[dict(season='s1', mode=mode)],
                entrypoint='references/shared-structure-index.json', dependencies={})],
            files=inventory)
        manifest_raw = json.dumps(manifest, separators=(',', ':')).encode('utf-8')
        (package_root / 'manifest.json').write_bytes(manifest_raw)
        resource_root = package_root.parents[2]
        ref = ComponentRef('s1-entry-fixture', 'v1', 'base', hashlib.sha256(manifest_raw).hexdigest())
        view = PackageView(resource_root, ref)
        reader = ComponentResourceReader(view)
        reads = []

        def read_resource(path):
            reads.append(Path(path).resolve().relative_to(view.root).as_posix())
            return reader(path)

        typed_index = view.path('references/shared-structure-index.json')
        store = SharedStructureResourceStore(typed_index, view.root, read_resource=read_resource)
        self.assertIsNone(store.error)
        entry_root = view.root / source.entries_path
        return source, view, reader, read_resource, reads, store, typed_index, entry_root

    def test_disabled_semantic_side_entries_load_real_s1_data_for_all_negative_floors(self):
        for mode in ('difficult', 'nightmare'):
            with self.subTest(mode=mode):
                source, view, reader, read_resource, reads, store, typed_index, entry_root = self.package(mode)
                product = store.load('shared-structure', source.ids[0], source.floors[0])
                self.assertIsNotNone(product)
                self.assertEqual(len(store.entries), len(source.ids) * len(source.floors))

                local = MapLocalEntryIdentity(entry_root, mode=mode, profile_id='shared-structure',
                    floors=source.floors, structure_store=store, read_resource=read_resource,
                    typed_index_path=typed_index, semantic_side_entries=False)
                self.assertFalse(local.semantic_side_entries)
                self.assertEqual(len(local.entries), len(source.ids) * len(source.floors))
                self.assertEqual({(str(row['mapId']), str(row['floor'])) for row, *_ in local.entries},
                                 {(map_id, floor) for map_id in source.ids for floor in source.floors})
                self.assertEqual(local.passports, [])
                self.assertEqual(local.entry_coverage, [])
                self.assertIsNone(local.passport_error)
                self.assertEqual(local.side_doors, {})
                self.assertEqual(local.side_search, {})
                self.assertIsNone(local.side_door_error)
                self.assertIsNone(local.side_search_error)
                self.assertIsNone(local.exit_icon_path)
                self.assertIsNone(local._exit_recognizer)

                # Exercise the real lazy observation entry point after
                # construction. No default path or undeclared package path is
                # consulted when semantic exits do not apply.
                self.assertEqual(local._entry_observations(None), [])
                self.assertIsNone(local._exit_recognizer)
                self.assertIn(source.entries_path + '/side-entries.json', reads)
                self.assertIn(source.entries_path + '/entry-passports.json', reads)
                self.assertIn('references/shared-structure-index.json', reads)
                self.assertTrue(any(path.endswith('.npz') for path in reads))
                self.assertFalse(any(('side-door-anchors.json' in path or
                                      'side-entry-search.npz' in path or
                                      'exit-icon' in path or 'exitIcon' in path)
                                     for path in reads))
                # The package contains no semantic-exit children; omission is
                # part of the source fixture, not replaced with empty files.
                self.assertFalse((entry_root / 'side-door-anchors.json').is_file())
                self.assertFalse((entry_root / 'side-entry-search.npz').is_file())

    def test_map_identity_engine_threads_disabled_capability_to_real_constructor(self):
        from difficult_native_identity import NativeDifficultPack
        from map_identity_engine import MapIdentityEngine

        source, view, reader, read_resource, reads, store, typed_index, entry_root = self.package()
        pack = NativeDifficultPack(view.root, 'references/map-pack.json', expected_mode='difficult',
            expected_season='s1', read_resource=read_resource)
        engine = MapIdentityEngine(pack, identity_root=entry_root,
            identity_profile_id='shared-structure', identity_floors=source.floors,
            entry_structure_store=store, entry_read_resource=read_resource,
            entry_typed_index_path=typed_index, entry_semantic_side_entries=False,
            entry_passport_source_available=False)
        self.assertIsNone(engine.side_entry_error)
        self.assertFalse(engine.side_entry_identity.semantic_side_entries)
        self.assertFalse(engine.side_entry_identity.passport_source_available)
        self.assertEqual(engine.side_entry_identity.passport_error, 'entry-passports-source-unavailable')
        self.assertEqual(engine.side_entry_identity.entries[0][0]['featureRole'],
                         'local-contour-proposals-not-semantic-side-exits')
        self.assertEqual(engine.side_entry_identity._entry_observations(None), [])
        self.assertFalse(any(('side-door-anchors.json' in path or
                              'side-entry-search.npz' in path or
                              path.endswith('entry-passports.json') or
                              'exit-icon' in path or 'exitIcon' in path)
                             for path in reads))

    def test_semantic_side_entries_flag_requires_an_actual_bool(self):
        _, _, _, read_resource, _, store, typed_index, entry_root = self.package()
        for value in (None, 0, 1, 'false'):
            with self.subTest(value=value), self.assertRaisesRegex(TypeError, 'must-be-bool'):
                MapLocalEntryIdentity(entry_root, mode='difficult', profile_id='shared-structure',
                    floors=('-1', '-2', '-3', '-4'), structure_store=store,
                    read_resource=read_resource, typed_index_path=typed_index,
                    semantic_side_entries=value)

    def test_declared_missing_passport_source_keeps_gap_without_reading_an_empty_file(self):
        source, view, reader, read_resource, reads, store, typed_index, entry_root = self.package(
            include_passports=False)
        local = MapLocalEntryIdentity(entry_root, mode='difficult', profile_id='shared-structure',
            floors=source.floors, structure_store=store, read_resource=read_resource,
            typed_index_path=typed_index, semantic_side_entries=False, passport_source_available=False)
        self.assertEqual(len(local.entries), len(source.ids) * len(source.floors))
        self.assertEqual(local.passport_error, 'entry-passports-source-unavailable')
        self.assertEqual(local.passports, [])
        self.assertEqual(local.entry_coverage, [])
        self.assertFalse(any(path.endswith('entry-passports.json') for path in reads))
        self.assertFalse((entry_root / 'entry-passports.json').exists())
        # The old default still tries its actual declared path and reports
        # unavailable data. No synthetic coverage or replacement file is made.
        MapLocalEntryIdentity(entry_root, mode='difficult', profile_id='shared-structure',
            floors=source.floors, structure_store=store, read_resource=read_resource,
            typed_index_path=typed_index, semantic_side_entries=False)
        self.assertTrue(any(path.endswith('entry-passports.json') for path in reads))

    def test_passport_source_availability_requires_a_bool_before_io(self):
        source, _, _, read_resource, reads, store, typed_index, entry_root = self.package()
        reads.clear()
        for value in (None, 0, 1, 'false'):
            with self.subTest(value=value), self.assertRaisesRegex(TypeError, 'passport_source_available-must-be-bool'):
                MapLocalEntryIdentity(entry_root, mode='difficult', profile_id='shared-structure',
                    floors=source.floors, structure_store=store, read_resource=read_resource,
                    typed_index_path=typed_index, passport_source_available=value)
        self.assertEqual(reads, [])


if __name__ == '__main__':
    unittest.main()
