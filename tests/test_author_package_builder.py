"""Prepared images -> real V2 ZIP -> Qt snapshot import and actual BGRA reader.

Game bases are tiny typed fixture resources. No registry/solver/import/decoder
is replaced; these checks do not establish real-author alignment or game QA.
"""
import json
import os
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import cv2
import numpy as np
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'app'))
import star_map_import as api
import generic_author_package as protocol
from author_package_builder import build_author_package, SOURCE_SCHEMA
from s1_author_displays import S1AuthorDisplays
import test_generic_author_package as fixtures


class AuthorPackageBuilderTests(unittest.TestCase):
    def setUp(self):
        self.fixture = fixtures.GenericPackageTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.root = self.fixture.root
        self.prepared = self.root.parent / 'prepared'
        self.prepared.mkdir()
        self.image = self.prepared / 'layer.png'
        Image.new('RGBA', (9, 8), (17, 41, 93, 203)).save(self.image)
        self.recipe = dict(schema=SOURCE_SCHEMA, profileId='author-never-registered', season='s0',
            authorName='新作者', displayName='新作者 · 路线', layers=[dict(mode='nightmare', mapId='map',
                floor='B1', image='layer.png', matrixDisplayToReference=[[1.2, .1, 2], [0, 1.1, 3], [0, 0, 1]])])
        self.output = self.root.parent / 'built.zip'

    def write_recipe(self):
        (self.prepared / 'author-package.json').write_text(json.dumps(self.recipe, ensure_ascii=False), encoding='utf-8')

    def build(self):
        self.write_recipe()
        return build_author_package(self.prepared, self.root, self.output)

    def test_real_cli_pack_snapshot_import_pixels_and_removal(self):
        self.write_recipe()
        env = dict(os.environ, PYTHONIOENCODING='utf-8')
        command = subprocess.run([sys.executable, str(Path(api.__file__)), 'pack', str(self.prepared), str(self.root),
            '--output', str(self.output)], capture_output=True, text=True, encoding='utf-8', env=env, timeout=60)
        self.assertEqual(command.returncode, 0, command.stderr + command.stdout)
        result = json.loads(command.stdout)
        self.assertEqual(result['sha256'], api._sha(self.output))
        self.assertFalse((self.root / 'work/map-packages/author-never-registered.json').exists())
        inspected = api.inspect_package(self.output, self.root, versioned=True)
        self.assertEqual(inspected['schema'], protocol.SCHEMA)
        api.install_author_package_live(self.output, self.root, application_idle=True, versioned=True)
        native = SimpleNamespace(root=self.root / '_internal', mode='nightmare',
            catalog=SimpleNamespace(profiles={}, _raw={'profiles': []}, default_profile_id='shared-structure'),
            adapters=SimpleNamespace(profiles={}))
        runtime = S1AuthorDisplays(self.root / '_internal', native, season='s0')
        binding = runtime.binding(self.recipe['profileId'], 'map', 'B1')
        self.assertIsNotNone(binding)
        np.testing.assert_array_equal(runtime.pixels(binding['path'], 'aligned-overlay')[2, 3], [93, 41, 17, 203])
        np.testing.assert_allclose(binding['matrix'] @ [4, 5, 1], [7.3, 8.5, 1])
        self.assertIsNone(runtime.binding(self.recipe['profileId'], 'map', 'F2'))
        import map_package_removal as removal
        plan = removal.inspect_package_removal(self.root, self.recipe['profileId'])
        removal.remove_packages(self.root, self.recipe['profileId'], application_idle=True,
                                expected_plan_sha256=plan['planSha256'])
        self.assertFalse(removal.managed_packages_snapshot(self.root))

    def test_s1_both_modes_use_their_native_negative_floor_contract(self):
        import test_s1_author_loading_work as s1_fixture
        for mode in ('nightmare', 'difficult'):
            with self.subTest(mode=mode):
                _archive, _profile, mode_root, interfaces, _count = s1_fixture.make_package(self.root, mode=mode)
                maps = json.loads((mode_root / interfaces['identityPack']).read_text())['maps']
                self.recipe.update(season='s1', profileId='author-new-' + mode)
                self.recipe['layers'][0].update(mode=mode, mapId=maps[0]['id'], floor='-1')
                self.output = self.root.parent / (mode + '.zip')
                self.build()
                inspected = api.inspect_package(self.output, self.root, season='s1', versioned=True)
                self.assertEqual(inspected['season'], 's1')
                api.install_author_package_live(self.output, self.root, application_idle=True, season='s1', versioned=True)

    def test_16bit_image_rejected_and_no_zip_published(self):
        data = cv2.imencode('.png', np.full((8, 9, 4), 65535, np.uint16))[1]
        self.image.write_bytes(data.tobytes())
        with self.assertRaisesRegex(api.ImportRejected, '^author-v2-invalid-image$'):
            self.build()
        self.assertFalse(self.output.exists())

    def test_missing_floor_and_singular_map_are_not_guessed(self):
        self.recipe['layers'][0]['floor'] = 'F2'
        with self.assertRaises(ValueError):
            self.build()
        self.assertFalse(self.output.exists())
        self.recipe['layers'][0].update(floor='B1', matrixDisplayToReference=[[0, 0, 0], [0, 0, 0], [0, 0, 1]])
        with self.assertRaises(ValueError):
            self.build()
        self.assertFalse(self.output.exists())

    def test_duplicate_layer_and_source_escape_rejected(self):
        self.recipe['layers'].append(dict(self.recipe['layers'][0]))
        with self.assertRaisesRegex(api.ImportRejected, 'duplicate'):
            self.build()
        self.recipe['layers'].pop()
        self.recipe['layers'][0]['image'] = '../outside.png'
        with self.assertRaises(api.ImportRejected):
            self.build()
        self.assertFalse(self.output.exists())

    def test_existing_zip_is_never_overwritten(self):
        self.output.write_bytes(b'existing package')
        with self.assertRaises(FileExistsError):
            self.build()
        self.assertEqual(self.output.read_bytes(), b'existing package')

    def test_pending_base_recovery_blocks_before_publishing(self):
        pending = self.root / 'work/map-packages/pending-base-repair.json'
        pending.parent.mkdir(parents=True, exist_ok=True)
        pending.write_text('{}')
        with self.assertRaisesRegex(api.ImportRejected, 'base-recovery-required'):
            self.build()
        self.assertFalse(self.output.exists())

    def test_cli_unknown_floor_returns_structured_error_without_zip(self):
        self.recipe['layers'][0]['floor'] = 'F2'
        self.write_recipe()
        command = subprocess.run([sys.executable, str(Path(api.__file__)), 'pack', str(self.prepared), str(self.root),
            '--output', str(self.output)], capture_output=True, text=True, encoding='utf-8',
            env=dict(os.environ, PYTHONIOENCODING='utf-8'), timeout=60)
        self.assertEqual(command.returncode, 2)
        self.assertFalse(json.loads(command.stdout)['success'])
        self.assertNotIn('Traceback', command.stderr)
        self.assertFalse(self.output.exists())

    def test_zip_output_does_not_require_hardlinks(self):
        with patch('os.link', side_effect=OSError('hardlinks unsupported')):
            self.build()
        self.assertEqual(api.inspect_package(self.output, self.root, versioned=True)['schema'], protocol.SCHEMA)


if __name__ == '__main__':
    unittest.main()
