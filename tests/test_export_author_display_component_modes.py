"""Synthetic producer evidence for the three author-display base contexts."""
import json
from io import BytesIO
from pathlib import Path
import tempfile
import unittest
import sys
import zipfile

import numpy as np
from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / 'app'), str(ROOT / 'tools'), str(ROOT / 'tests'),
                str(ROOT / 'experiments' / 'qt_quick')]

import export_author_display_component as display_exporter
import imported_resource_archive as archives
from client_providers import SharedReferenceProvider
from imported_author_display import decode_author_display
from imported_author_displays import ImportedAuthorDisplays
from imported_native_base import decode_native_base
from imported_resource_packages import PackageView
import test_export_native_base_package as s0n_fixture_module
from test_export_s0_difficult_native_base_package import LEGACY
from original_qt_resource_fixtures import OriginalS0DifficultSourceFixture, OriginalS1SourceFixture


def _synthetic_png():
    image = Image.new('RGBA', (5, 3), (24, 48, 72, 255))
    image.putpixel((1, 1), (1, 2, 3, 0))
    stream = BytesIO()
    image.save(stream, format='PNG')
    return stream.getvalue()


class AuthorDisplayComponentModeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        temporary = tempfile.TemporaryDirectory(prefix='author-display-mode-bases-')
        cls.addClassCleanup(temporary.cleanup)
        cls.root = Path(temporary.name).resolve()

        # Build each complete native base exactly once at class setup.
        s0n_case = s0n_fixture_module.NativeBaseExporterTests(
            'test_real_roundtrip_preserves_full_competition_and_source_bytes')
        s0n_case.setUp()
        try:
            # Reuse the existing public-decoder source fixture's explicit S0N
            # evidence fields; the base export itself still runs only once.
            coordinates = s0n_case.read(s0n_fixture_module.exporter.COORDINATES)
            for floor in s0n_fixture_module.exporter.FLOORS:
                coordinates['maps'][s0n_case.mid]['floors'][floor].update(
                    coordinateWidth=8, coordinateHeight=8)
                profile_floor = (coordinates['profiles']['shared-structure']
                    ['maps'][s0n_case.mid]['floors'][floor])
                profile_floor['evidence'] = dict(method='same-typed-shared-native-reference-canvas',
                    algebraicallyExact=True)
            s0n_case.write(s0n_fixture_module.exporter.COORDINATES, coordinates)
            main = s0n_case.read(s0n_fixture_module.exporter.ENTRIES + '/entries.json')
            main['entries'][0].update(center=[2., 2.], kind='main', material='fixture-native-room')
            s0n_case.write(s0n_fixture_module.exporter.ENTRIES + '/entries.json', main)
            # This shared fixture predates the original Qt resource contract.
            # Supply the metadata and empty source indexes required by the
            # real S0 nightmare loader before exporting it for this test.
            pack = s0n_case.read(s0n_fixture_module.exporter.PACK)
            pack['displayName'] = 'Synthetic original-layout nightmare'
            s0n_case.write(s0n_fixture_module.exporter.PACK, pack)
            s0n_case.write('references/native-similarity-groups.json', dict(
                schemaVersion=1, resourceKind='native-similarity-groups', groups=[]))
            s0n_case.write('references/nightmare-source-candidate-index.json',
                dict(schemaVersion=1, entries=[]))
            s0n_case.write('references/nightmare-portable-layout-seeds.json', dict(entries=[]))
            s0n_case.export()
            s0n_archive = cls.root / 's0-nightmare-base.zip'
            s0n_archive.write_bytes(s0n_case.output.read_bytes())
        finally:
            s0n_case.doCleanups()

        s0d_source = OriginalS0DifficultSourceFixture(cls.root / 's0-difficult-source')
        s0d_source.export()
        s1n_source = OriginalS1SourceFixture(cls.root / 's1-nightmare-source', mode='nightmare')
        s1n_source.export()

        cls.bases = {
            's0n': cls._install_base(s0n_archive, cls.root / 'installed-s0n', 's0', 'nightmare'),
            's0d': cls._install_base(s0d_source.output, cls.root / 'installed-s0d', 's0', 'difficult'),
            's1n': cls._install_base(s1n_source.output, cls.root / 'installed-s1n', 's1', 'nightmare'),
        }

    @staticmethod
    def _install_base(archive, resource_root, season, mode):
        inspected = archives.inspect_archive(archive)
        saved = archives.save_archive(archive, resource_root,
            expected_archive_sha256=inspected.archive_sha256)
        base_ref = next(ref for ref in saved.component_refs if ref.component_id == 'base')
        base_view = PackageView(resource_root, base_ref)
        base = decode_native_base(base_view, season=season, mode=mode)
        provider = SharedReferenceProvider(base_view.root, base.resources, base.versions,
            check_current=base.assert_current)
        return dict(archive=archive, resource_root=resource_root, ref=base_ref,
            view=base_view, base=base, provider=provider, season=season, mode=mode)

    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix='author-display-mode-case-')
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.image_root = self.root / 'images'
        self.image_root.mkdir()
        (self.image_root / 'synthetic.png').write_bytes(_synthetic_png())
        self.registration_path = self.root / 'registration.json'
        self.output = self.root / 'author-display.zip'

    def registration(self, profile_id, map_id, floor, matrix):
        value = dict(schema=display_exporter.INPUT_SCHEMA, profileId=profile_id,
            authorName='合成测试作者', displayName='合成路线', layers=[dict(
                mapId=map_id, floor=floor, image='synthetic.png',
                matrixDisplayToReference=matrix)])
        self.registration_path.write_text(json.dumps(value, ensure_ascii=False), encoding='utf-8')
        return value

    def export_and_verify(self, key, map_id, floor):
        selected = self.bases[key]
        base, provider, base_view = selected['base'], selected['provider'], selected['view']
        profile_id = 'author-mode-' + key
        matrix = [[1.2, .1, 2], [.15, .9, -1], [0, 0, 1]]
        raw_png = (self.image_root / 'synthetic.png').read_bytes()
        self.registration(profile_id, map_id, floor, matrix)
        rank_before = base.rank_ready
        pack_before = base.pack
        entry_before = base.entry_identity
        shared_matrix_before = base.adapters.matrix_profile_to_canonical(
            'shared-structure', map_id, floor)
        self.assertIsNotNone(shared_matrix_before)
        shared_matrix_before = np.asarray(shared_matrix_before).copy()

        result = display_exporter.export_component(selected['resource_root'], selected['ref'],
            self.registration_path, self.image_root, self.output,
            package_id='author-display-' + key, resource_version='synthetic-v1')
        self.assertEqual(result['componentId'], f'display-{selected["season"]}-{selected["mode"]}')
        self.assertEqual(result['context'], dict(season=selected['season'], mode=selected['mode']))
        self.assertEqual(result['layerCount'], 1)

        inspected = archives.inspect_archive(self.output)
        self.assertEqual(inspected.status, 'inspected')
        with zipfile.ZipFile(self.output) as archive:
            manifest = json.loads(archive.read('manifest.json'))
            component = manifest['components'][0]
            self.assertEqual((component['type'], component['schemaVersion']), ('display', 1))
            self.assertEqual(component['contexts'], [dict(season=selected['season'], mode=selected['mode'])])
            doc = json.loads(archive.read(f'data/{result["componentId"]}/display.json'))
            self.assertEqual(doc['schema'], display_exporter.SCHEMA)
            row = doc['layers'][0]
            self.assertEqual(row['matrixDisplayToReference'], matrix)
            self.assertEqual(archive.read(f'data/{result["componentId"]}/{row["displayPath"]}'), raw_png)

        saved = archives.save_archive(self.output, selected['resource_root'],
            expected_archive_sha256=inspected.archive_sha256)
        display_ref = next(ref for ref in saved.component_refs
                           if ref.component_id == result['componentId'])
        display_view = PackageView(selected['resource_root'], display_ref)
        decoded = decode_author_display(display_view, base, provider)
        self.assertEqual(decoded.qualification, 'user-provided-unreviewed')
        self.assertEqual(decoded.base_ref, selected['ref'])
        self.assertEqual(decoded.gaps, ())
        layer = decoded.layer(map_id, floor)
        self.assertEqual(layer.display_shape_hw, (3, 5))
        self.assertEqual(layer.matrix_display_to_reference,
            ((1.2, .1, 2), (.15, .9, -1), (0, 0, 1)))
        self.assertEqual(decoded.image_bytes(map_id, floor), raw_png)

        reference = provider('shared-structure', map_id, floor)
        self.assertIsNotNone(reference)
        self.assertEqual(base_view.path(layer.reference.path), reference.path)
        self.assertEqual(layer.reference_shape_hw, reference.shape)
        self.assertEqual(layer.reference.path, reference.path.relative_to(base_view.root).as_posix())
        for field in ('structureVersionKey', 'baseSnapshot', 'sourceVersion'):
            self.assertEqual(getattr(layer, {
                'structureVersionKey': 'structure_version_key',
                'baseSnapshot': 'base_snapshot', 'sourceVersion': 'source_version'}[field]),
                reference.metadata[field])

        runtime = ImportedAuthorDisplays([display_view], base, provider)
        self.assertIn(profile_id, runtime.profiles)
        binding = runtime.binding(profile_id, map_id, floor)
        self.assertIsNotNone(binding)
        self.assertTrue(binding['usable'])
        expected_matrix = shared_matrix_before @ np.asarray(matrix, dtype=np.float64)
        np.testing.assert_allclose(runtime.adapters.matrix_profile_to_canonical(profile_id, map_id, floor),
                                   expected_matrix)
        pixels = runtime.pixels(binding['path'], 'full-reference')
        self.assertEqual(tuple(pixels.shape), (3, 5, 4))
        self.assertEqual(tuple(int(value) for value in pixels[1, 1]), (3, 2, 1, 0))

        # The independent display remains unreviewed display data. It does not
        # alter the base's rank state, identity pack, entry evidence, or bridge.
        base.assert_current()
        self.assertEqual(base.rank_ready, rank_before)
        self.assertIs(base.pack, pack_before)
        self.assertIs(base.entry_identity, entry_before)
        np.testing.assert_array_equal(base.adapters.matrix_profile_to_canonical(
            'shared-structure', map_id, floor), shared_matrix_before)

    def test_s0_nightmare_native_component_roundtrip(self):
        selected = self.bases['s0n']
        self.export_and_verify('s0n', 'fixture-map', '1')

    def test_s0_difficult_native_component_roundtrip(self):
        selected = self.bases['s0d']
        map_id = next(mid for mid in selected['base'].pack.by_id if mid != LEGACY)
        self.export_and_verify('s0d', map_id, '1')

    def test_s0_difficult_legacy_map29_has_no_typed_reference_and_is_not_published(self):
        selected = self.bases['s0d']
        self.registration('author-mode-s0d-legacy', LEGACY, '1',
            [[1,0,0],[0,1,0],[0,0,1]])
        with self.assertRaisesRegex(ValueError,
                f'typed-reference-unavailable:{LEGACY}/1'):
            display_exporter.export_component(selected['resource_root'], selected['ref'],
                self.registration_path, self.image_root, self.output,
                package_id='author-display-s0d-legacy', resource_version='synthetic-v1')
        self.assertFalse(self.output.exists())

    def test_s1_nightmare_native_component_roundtrip(self):
        selected = self.bases['s1n']
        map_id = next(iter(selected['base'].pack.by_id))
        self.export_and_verify('s1n', map_id, '-4')


if __name__ == '__main__':
    unittest.main()
