"""S1 nightmare producer round trips over the registered 15-map pool."""
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
import sys
import zipfile

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / 'app'), str(ROOT / 'tools'), str(ROOT / 'tests')]

import export_native_base_package as exporter
import imported_resource_archive as archives
from imported_native_base import decode_native_base
from imported_resource_packages import PackageView
from imported_resource_reader import ComponentResourceReader
from imported_s1_controls_profile import S1ControlsProfile
from s1_native_resources import MODE_LAYOUTS
from test_export_s1_native_base_package import S1NativeBaseSourceFixture


class S1NightmareNativeBaseExporterTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.fixture = S1NativeBaseSourceFixture(self.root, mode='nightmare')

    def _installed(self):
        saved = archives.save_archive(self.fixture.output, self.root / 'installed')
        return {ref.component_id: PackageView(self.root / 'installed', ref)
                for ref in saved.component_refs}

    def test_s1_nightmare_15_map_pool_roundtrips_with_proposals_unprivileged(self):
        source_files = {path.relative_to(self.fixture.prefixRoot).as_posix(): path.read_bytes()
                        for path in self.fixture.prefixRoot.rglob('*') if path.is_file()}
        result = self.fixture.export()
        self.assertEqual((result['season'], result['mode'], result['mapCount'], result['typedLayerCount']),
                         ('s1', 'nightmare', 15, 60))
        self.assertEqual(result['mainEntries'], 15)
        self.assertEqual(result['localEntries'], 60)
        self.assertEqual(result['sha256'], hashlib.sha256(self.fixture.output.read_bytes()).hexdigest())
        self.assertEqual(source_files,
                         {path.relative_to(self.fixture.prefixRoot).as_posix(): path.read_bytes()
                          for path in self.fixture.prefixRoot.rglob('*') if path.is_file()})

        with zipfile.ZipFile(self.fixture.output) as archive:
            manifest = json.loads(archive.read('manifest.json'))
        self.assertEqual(manifest['clientCompatibility']['requiredCapabilities'],
                         ['native-s1-nightmare-base-v1'])
        self.assertEqual(manifest['components'][0]['contexts'], [dict(season='s1', mode='nightmare')])

        views = self._installed()
        base, controls_view = views['base'], views['controls']
        reader = ComponentResourceReader(base)
        context = reader.json('resource-context.json')
        self.assertEqual((context['schema'], context['season'], context['mode']),
                         ('xingtu-native-recognition-base-v1', 's1', 'nightmare'))
        self.assertEqual(context['runtimeInterfaces'], exporter.S1_INTERFACES)

        decoded = decode_native_base(base, season='s1', mode='nightmare')
        self.assertEqual(len(decoded.pack.maps), 15)
        self.assertEqual({row.id for row in decoded.pack.maps}, set(MODE_LAYOUTS['nightmare']))
        self.assertEqual(len(decoded.resources.entries), 60)
        self.assertEqual(len(decoded.entry_identity.entries), 15)
        self.assertEqual(len(decoded.side_entry_identity.entries), 60)
        self.assertIsNone(decoded.exit_recognizer)
        self.assertEqual(decoded.side_entry_identity.side_doors, {})
        self.assertEqual(decoded.side_entry_identity.side_search, {})
        self.assertEqual(decoded.side_entry_identity.passports, [])
        self.assertEqual(decoded.side_entry_identity.entry_coverage, [])

        main_entries = reader.json(exporter.S1_ENTRIES + '/entries.json')['entries']
        self.assertEqual(len(main_entries), 15)
        self.assertTrue(all(row['proposalOnly'] is True and row['authorizesPose'] is False
                            and row['authorizesIdentity'] is False and row['gates'] == []
                            for row in main_entries))
        local_entries = reader.json(exporter.S1_ENTRIES + '/side-entries.json')['entries']
        self.assertEqual(len(local_entries), 60)
        self.assertTrue(all(row['featureRole'] == 'local-contour-proposals-not-semantic-side-exits'
                            and row['gates'] == [] for row in local_entries))

        controls = S1ControlsProfile(controls_view)
        control_context = controls.for_context('s1', 'nightmare')
        self.assertIsNotNone(control_context)
        self.assertEqual(control_context.contract.floor_ids, self.fixture.floors)
        self.assertIsNone(controls.for_context('s1', 'difficult'))

        for relative in (self.fixture.pack_path, self.fixture.index_path, self.fixture.neutral_path,
                         *(self.fixture.entries_path + '/' + name
                           for name in ('entries.json', 'side-entries.json', 'entry-passports.json'))):
            self.assertEqual(base.read(relative), source_files[relative])
        for resource in self.fixture.resources.values():
            self.assertEqual(base.read(resource), source_files[resource])

    def test_nightmare_rejects_a_cross_mode_map_pool(self):
        pack = self.fixture.read_json(self.fixture.pack_path)
        pack['maps'][0]['id'] = next(iter(MODE_LAYOUTS['difficult']))
        pack['maps'][0]['sourceLayout'] = pack['maps'][0]['id']
        self.fixture.write_json(self.fixture.pack_path, pack)
        self.fixture.refresh_after_source_edit()

        with self.assertRaisesRegex(ValueError, 's1-map-inventory'):
            self.fixture.export()
        self.assertFalse(self.fixture.output.exists())

    def test_wrong_source_inventory_rejects_without_publishing(self):
        inventory = json.loads(self.fixture.inventory.read_bytes())
        key = self.fixture.sourcePrefix + '/' + self.fixture.pack_path
        inventory['files'][key]['sha256'] = '0' * 64
        self.fixture.inventory.write_text(json.dumps(inventory), encoding='utf-8')

        with self.assertRaisesRegex(ValueError, 'source-inventory-mismatch'):
            self.fixture.export()
        self.assertFalse(self.fixture.output.exists())


if __name__ == '__main__':
    unittest.main()
