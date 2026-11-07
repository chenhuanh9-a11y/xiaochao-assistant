"""Actual import and display consumers over synthetic local images, not GUI QA."""
import hashlib
import io
import json
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch
import zipfile

from PIL import Image

sys.path[:0] = [str(Path(__file__).resolve().parents[1] / 'app')]
import generic_author_base as base
import generic_author_package as generic
import s1_author_displays as displays
import star_map_import as importer
import test_generic_author_package as fixtures
from map_resource_context import BrowseContext


class DeferredAuthorDecodeTests(unittest.TestCase):
    def setUp(self):
        self.fixture = fixtures.GenericPackageTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.root, self.profile = self.fixture.root, self.fixture.profile

    def runtime(self):
        native = SimpleNamespace(root=self.root / '_internal', mode='nightmare',
            catalog=SimpleNamespace(profiles={}, _raw={'profiles': []}, default_profile_id='shared-structure'),
            adapters=SimpleNamespace(profiles={}))
        return displays.S1AuthorDisplays(self.root / '_internal', native, season='s0')

    def test_import_decodes_but_list_does_not_and_display_decodes_selected_pixels(self):
        archive = self.fixture.package()
        with patch.object(Image.Image, 'load', autospec=True, side_effect=Image.Image.load) as decode:
            self.fixture.install(archive)
        self.assertGreater(decode.call_count, 0)
        with patch.object(Image.Image, 'load', side_effect=AssertionError('eager image decompression')):
            context = BrowseContext('nightmare', self.root / '_internal', None, None, None, (), {}, season='s0')
            self.assertEqual(len(displays.append_browse_profile(context).profiles), 1)
            runtime = self.runtime()
        path = runtime.binding(self.profile, 'map', 'B1')['path']
        with patch.object(displays.cv2, 'imdecode', wraps=displays.cv2.imdecode) as decode:
            self.assertEqual(runtime.pixels(path, 'aligned-overlay').shape, (8, 9, 4))
            self.assertEqual(decode.call_count, 1)
            runtime.pixels(path, 'aligned-overlay')
            self.assertEqual(decode.call_count, 1)
        original = path.read_bytes()
        try:
            damaged = bytearray(original); damaged[-1] ^= 1
            path.write_bytes(damaged)
            with self.assertRaises(ValueError):
                runtime.pixels(path, 'aligned-overlay')
        finally:
            path.write_bytes(original)

    def test_snapshot_inventory_verification_does_not_decompress_images(self):
        import author_resource_snapshots as snapshots
        importer.install_author_package_live(self.fixture.package(), self.root, application_idle=True, versioned=True)
        with patch.object(Image.Image, 'load', side_effect=AssertionError('eager inventory decompression')):
            result = snapshots.verified(self.root, self.profile)
        self.assertEqual(result['status'], 'installed')

    def test_snapshot_reader_has_the_same_deferred_decode_boundary(self):
        archive = self.fixture.package()
        importer.install_author_package_live(archive, self.root, application_idle=True, versioned=True)
        with patch.object(Image.Image, 'load', side_effect=AssertionError('eager image decompression')):
            runtime = self.runtime()
        self.assertIn(self.profile, runtime.profiles)
        path = runtime.binding(self.profile, 'map', 'B1')['path']
        self.assertEqual(runtime.pixels(path, 'aligned-overlay').shape, (8, 9, 4))

    def test_header_readable_invalid_pixels_are_rejected_at_import_and_actual_display(self):
        archive = self.fixture.package()
        self.fixture.install(archive)
        runtime = self.runtime()
        path = runtime.binding(self.profile, 'map', 'B1')['path']
        runtime.pixels(path, 'aligned-overlay')
        damaged = bytearray(path.read_bytes())
        damaged[damaged.index(b'IDAT') + 4] ^= 255
        damaged = bytes(damaged)
        with Image.open(io.BytesIO(damaged)) as header:
            self.assertEqual(header.size, (9, 8))
            with self.assertRaises(OSError):
                header.load()
        binding_path = self.root / generic.bindings_path(self.profile)
        document = json.loads(binding_path.read_bytes())
        layer = document['layers'][0]
        layer.update(displayBytes=len(damaged), displaySha256=hashlib.sha256(damaged).hexdigest())
        binding_bytes = fixtures.encoded(document)
        with zipfile.ZipFile(archive) as stream:
            metadata = json.loads(stream.read('map-package.json'))
        payload = {layer['displayPath']: damaged, generic.bindings_path(self.profile): binding_bytes}
        metadata['files'] = [fixtures.record(p, data) for p, data in payload.items()]
        rejected = self.root / 'invalid-pixels.zip'
        with zipfile.ZipFile(rejected, 'w') as stream:
            stream.writestr('map-package.json', fixtures.encoded(metadata))
            for p, data in payload.items():
                stream.writestr('data/' + p, data)
        with self.assertRaises(importer.ImportRejected):
            importer.inspect_package(rejected, self.root)
        # Model a fully edited on-disk receipt, not a package allowed by import.
        # Even if both hashes and headers agree, the final pixel consumer must
        # fail clearly. The pre-edit warm pixel cache cannot supply old pixels.
        path.write_bytes(damaged)
        with self.assertRaises(ValueError):
            runtime.pixels(path, 'aligned-overlay')
        binding_path.write_bytes(binding_bytes)
        receipt_path = self.root / f'work/map-packages/{self.profile}.json'
        receipt = json.loads(receipt_path.read_bytes())
        receipt['files'] = metadata['files']
        receipt_path.write_bytes(fixtures.encoded(receipt))
        runtime = self.runtime()
        self.assertIn(self.profile, runtime.profiles)
        with self.assertRaisesRegex(ValueError, 'display-decode-failed'):
            runtime.pixels(path, 'aligned-overlay')

    def test_negative_context_filter_reads_metadata_without_binding_or_base_work(self):
        for snapshot in (False, True):
            with self.subTest(snapshot=snapshot):
                if snapshot:
                    importer.install_author_package_live(self.fixture.package(color=(3, 2, 1, 90)), self.root,
                        application_idle=True, versioned=True)
                else:
                    self.fixture.install(self.fixture.package())
                with patch.object(displays.InstalledAuthorPackage, '_read', side_effect=AssertionError('binding read')), \
                        patch.object(base, 'resolve_layer', side_effect=AssertionError('base read')):
                    for season, mode in (('s1', 'nightmare'), ('s0', 'difficult')):
                        context = BrowseContext(mode, self.root / '_internal', None, None, None, (), {}, season=season)
                        self.assertFalse(displays.append_browse_profile(context).profiles)
                # Filtering is not a persistent negative cache; the same package
                # is available immediately for its actual context.
                self.assertIn(self.profile, self.runtime().profiles)

    def test_invalid_receipt_cannot_supply_a_silent_negative_filter(self):
        self.fixture.install(self.fixture.package())
        path = self.root / f'work/map-packages/{self.profile}.json'
        value = json.loads(path.read_bytes()); value['season'] = 's1'
        path.write_bytes(fixtures.encoded(value))
        self.assertTrue(displays._matches_context(self.root / '_internal', self.profile, 's0', 'nightmare'))
        context = BrowseContext('nightmare', self.root / '_internal', None, None, None, (), {}, season='s0')
        result = displays.append_browse_profile(context)
        self.assertFalse(result.profiles)
        self.assertTrue(result.warnings)


if __name__ == '__main__':
    unittest.main()
