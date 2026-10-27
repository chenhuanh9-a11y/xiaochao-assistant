"""Small explicit native grids exercise the producer, not real-frame identity."""
import importlib.util
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

import numpy as np


SPEC = importlib.util.spec_from_file_location('native_group_builder',
    Path(__file__).resolve().parents[1] / 'tools/maps/build_native_similarity_groups.py')
builder = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(builder)
MEMBERS = ('nightmare-04', 'nightmare-12', 'nightmare-19')


class NativeSimilarityResourceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.source = self.root / 'source'
        self.output = self.root / 'candidate'
        self.entries = []
        self.metadata = {}
        self.masks = {}
        for i, map_id in enumerate(MEMBERS):
            width, x0 = (6, 0) if i == 2 else (4, 2)
            mask = np.zeros((4, width), np.uint8)
            if i == 0:
                mask[1, 1] = 1
            elif i == 1:
                mask[1, 2] = 1
            else:
                mask[1, [1, 3]] = 1
            binding = dict(profile='zhan10-speed', mapId=map_id, floor='1', variant='variant-01')
            metadata = dict(sourceKind='game-rendered-shared-structure', binding=binding,
                canvas=dict(coordinateSpace=f'game-native-pixels:fixture-{i}:1', layout=f'fixture-{i}',
                    floor='1', worldFloor=2, width=width, height=4, shapeHW=[4, width], axes='x-right,y-down'),
                roles=dict(clean='shared_mask', validation='shared_mask'),
                sourceGeometry=dict(layout=f'fixture-{i}', worldFloor=2, targetPixelsPerVoxel=128,
                    bounds=dict(minX=x0, minY=0, maxX=6, maxY=4), nativeShapeHW=[4, width],
                    matrixRendererPixelToProduct=np.eye(3).tolist()),
                structureVersionKey=f'fixture-{i}@synthetic/1', sourceVersion='fixture-current',
                baseSnapshot='fixture-base', authorPixelsUsedForStructure=False, authorValidationUsed=False)
            self.entries.append(dict(**binding, resourcePath=f'references/{map_id}.npz',
                **{k:metadata[k] for k in ('structureVersionKey', 'sourceVersion', 'baseSnapshot')}))
            self.metadata[map_id], self.masks[map_id] = metadata, mask
            self.write_resource(map_id)
        self.write_index()

    def write_resource(self, map_id):
        path = self.source / f'references/{map_id}.npz'
        path.parent.mkdir(parents=True, exist_ok=True)
        np.savez_compressed(path, shared_mask=self.masks[map_id], metadata=json.dumps(self.metadata[map_id]))

    def write_index(self):
        path = self.source / builder.SHARED_INDEX_NAME
        path.write_text(json.dumps(dict(schemaVersion=1, resourceKind='shared-structure', entries=self.entries)), encoding='utf-8')

    def build(self, **changes):
        args = dict(member_ids=MEMBERS, profile='zhan10-speed')
        args.update(changes)
        return builder.build_group(self.source, self.output, **args)

    def test_three_member_world_bridge_and_all_pair_positive_differences(self):
        payload = self.build()
        floor = payload['groups'][0]['floors'][0]
        self.assertEqual(floor['canvas']['shapeHW'], [4, 6])
        self.assertEqual([m['matrixNativeToGroup'][0][2] for m in floor['members']], [2., 2., 0.])
        self.assertEqual(len(floor['pairs']), 3)
        with np.load(self.output / floor['differenceResourcePath'], allow_pickle=False) as archive:
            expected = np.zeros((3, 4, 6), np.uint8)
            expected[0, 1, 3] = 1
            expected[1, 1, 4] = 1
            expected[2, 1, [1, 3]] = 1
            for i, member in enumerate(floor['members']):
                np.testing.assert_array_equal(archive[member['array']], expected[i])
                self.assertEqual(builder.sha256(self.output / member['resourcePath']), member['resourceSha256'])
            self.assertEqual(int(archive[floor['unionArray']].sum()), 3)
            self.assertEqual(int(archive[floor['commonArray']].sum()), 0)
            pairs = {(p['memberA'], p['memberB']):p for p in floor['pairs']}
            # Fixture world origins are explicitly x=2,2,0. Reconstruct from
            # original inputs, without consuming the producer's bridge/arrays.
            originals = {
                MEMBERS[0]: np.pad(self.masks[MEMBERS[0]], ((0, 0), (2, 0))) > 0,
                MEMBERS[1]: np.pad(self.masks[MEMBERS[1]], ((0, 0), (2, 0))) > 0,
                MEMBERS[2]: self.masks[MEMBERS[2]] > 0,
            }
            expected_pairs = ((MEMBERS[0], MEMBERS[1]), (MEMBERS[0], MEMBERS[2]), (MEMBERS[1], MEMBERS[2]))
            self.assertEqual(set(pairs), set(expected_pairs))
            for member_a, member_b in expected_pairs:
                pair = pairs[(member_a, member_b)]
                np.testing.assert_array_equal(archive[pair['aOnlyArray']],
                    np.logical_and(originals[member_a], np.logical_not(originals[member_b])))
                np.testing.assert_array_equal(archive[pair['bOnlyArray']],
                    np.logical_and(originals[member_b], np.logical_not(originals[member_a])))
            pair = pairs[(MEMBERS[0], MEMBERS[2])]
            self.assertEqual(int(archive[pair['aOnlyArray']].sum()), 0)
            self.assertEqual(np.argwhere(archive[pair['bOnlyArray']]).tolist(), [[1, 1]])
        self.assertEqual(json.loads((self.output / builder.INDEX_NAME).read_text(encoding='utf-8')), payload)
        self.assertEqual(floor['differenceSha256'], builder.sha256(self.output / floor['differenceResourcePath']))

    def test_reject_stale_version_before_writing(self):
        self.entries[0]['structureVersionKey'] = 'stale/1'
        self.write_index()
        with self.assertRaisesRegex(ValueError, 'stale shared index'):
            self.build()
        self.assertFalse(self.output.exists())

    def test_reject_wrong_floor_and_unknown_member(self):
        for changes in (dict(floor='2'), dict(member_ids=(*MEMBERS, 'unknown'))):
            with self.subTest(changes=changes), self.assertRaisesRegex(ValueError, 'missing or invalid native binding'):
                self.build(**changes)
        self.assertFalse(self.output.exists())

    def test_reject_metadata_binding_not_only_index_binding(self):
        self.metadata[MEMBERS[0]]['binding']['floor'] = '2'
        self.write_resource(MEMBERS[0])
        with self.assertRaisesRegex(ValueError, 'missing or invalid native binding'):
            self.build()

    def test_reject_non_native_and_anisotropic_geometry(self):
        metadata = self.metadata[MEMBERS[0]]
        metadata['sourceGeometry']['matrixRendererPixelToProduct'][0][2] = 7
        self.write_resource(MEMBERS[0])
        with self.assertRaisesRegex(ValueError, 'direct native'):
            self.build()
        metadata['sourceGeometry']['matrixRendererPixelToProduct'] = np.eye(3).tolist()
        metadata['sourceGeometry']['bounds']['minX'] = 1
        self.write_resource(MEMBERS[0])
        with self.assertRaisesRegex(ValueError, 'isotropic'):
            self.build()

    def test_y_down_bridge_uses_world_max_y(self):
        metadata = self.metadata[MEMBERS[0]]
        metadata['sourceGeometry']['bounds'].update(minY=-2, maxY=2)
        self.write_resource(MEMBERS[0])
        floor = self.build()['groups'][0]['floors'][0]
        self.assertEqual(floor['canvas']['shapeHW'], [6, 6])
        self.assertEqual(floor['members'][0]['matrixNativeToGroup'][1][2], 2.)
        with np.load(self.output / floor['differenceResourcePath'], allow_pickle=False) as archive:
            self.assertEqual(np.argwhere(archive['member_0']).tolist(), [[3, 3]])

    def test_preserve_existing_output(self):
        self.build()
        digest = builder.sha256(self.output / builder.INDEX_NAME)
        with self.assertRaisesRegex(ValueError, 'preserve previous candidates'):
            self.build()
        self.assertEqual(digest, builder.sha256(self.output / builder.INDEX_NAME))

    def test_reject_index_outside_resource_root(self):
        for index in ('../source-index.json', str(self.source / builder.SHARED_INDEX_NAME)):
            with self.subTest(index=index), self.assertRaisesRegex(ValueError, 'relative to the resource root'):
                self.build(shared_index=index)
        self.assertFalse(self.output.exists())

    def test_reject_shared_index_colliding_with_group_index_before_writing(self):
        for case, index in enumerate((builder.INDEX_NAME, 'references/./native-similarity-groups.json',
                                      'references/native-similarity/native-04-12-19-1.npz')):
            with self.subTest(index=index):
                source_index = self.source / index
                source_index.parent.mkdir(parents=True, exist_ok=True)
                source_index.write_bytes((self.source / builder.SHARED_INDEX_NAME).read_bytes())
                # Each case gets an independent candidate to expose both aliases.
                self.output = self.root / f'collision-{case}'
                with self.assertRaisesRegex(ValueError, 'output target collision'):
                    self.build(shared_index=index)
                self.assertFalse(self.output.exists())

    def test_reject_reference_dependency_colliding_with_group_npz(self):
        relative = 'references/native-similarity/native-04-12-19-1.npz'
        target = self.source / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(self.source / self.entries[0]['resourcePath'], target)
        self.entries[0]['resourcePath'] = relative
        self.write_index()
        with self.assertRaisesRegex(ValueError, 'output target collision'):
            self.build()
        self.assertFalse(self.output.exists())

    def test_reject_windows_shared_index_aliases_before_writing(self):
        original = (self.source / builder.SHARED_INDEX_NAME).read_bytes()
        for case, relative in enumerate((
                builder.INDEX_NAME + '.', builder.INDEX_NAME + ' ',
                'references/native-similarity/native-04-12-19-1.npz.')):
            with self.subTest(relative=relative):
                # On Windows these writes address the corresponding plain name.
                source_index = self.source / relative
                source_index.parent.mkdir(parents=True, exist_ok=True)
                source_index.write_bytes(original)
                self.output = self.root / f'windows-alias-{case}'
                with self.assertRaisesRegex(ValueError, 'Windows output path component'):
                    self.build(shared_index=relative)
                self.assertFalse(self.output.exists())

    def test_reject_windows_reference_directory_alias_before_writing(self):
        relative = 'references/native./nightmare-04.npz'
        target = self.source / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(self.source / self.entries[0]['resourcePath'], target)
        self.entries[0]['resourcePath'] = relative
        self.write_index()
        with self.assertRaisesRegex(ValueError, 'Windows output path component'):
            self.build()
        self.assertFalse(self.output.exists())

    def test_reject_windows_stream_and_device_targets_before_read_or_write(self):
        for relative in ('references/native-similarity-groups.json:stream',
                         'references/CON.json', 'references/LPT1/index.json'):
            with self.subTest(relative=relative):
                with self.assertRaisesRegex(ValueError, 'Windows output path component'):
                    self.build(shared_index=relative)
                self.assertFalse(self.output.exists())

    def test_cli_lists_additive_products_without_replacing_full_base_index(self):
        # A complete base has a fourth map that this candidate must not remove.
        metadata = json.loads(json.dumps(self.metadata[MEMBERS[0]]))
        metadata['binding']['mapId'] = 'nightmare-03'
        extra_path = 'references/nightmare-03.npz'
        np.savez_compressed(self.source / extra_path, shared_mask=self.masks[MEMBERS[0]],
                            metadata=json.dumps(metadata))
        self.entries.append(dict(self.entries[0], mapId='nightmare-03', resourcePath=extra_path))
        self.write_index()
        original_index = (self.source / builder.SHARED_INDEX_NAME).read_bytes()
        command = [sys.executable, str(Path(builder.__file__)), str(self.source),
            '--output-root', str(self.output), '--profile', 'zhan10-speed', '--group-id', 'native-04-12-19']
        for member in MEMBERS:
            command.extend(['--member', member])
        result = subprocess.run(command, capture_output=True, text=True, check=True)
        summary = json.loads(result.stdout)
        self.assertFalse(summary['candidateIsFullPackageReplacement'])
        integration = summary['completeBaseIntegration']
        self.assertEqual(set(integration['newFiles']), {
            builder.INDEX_NAME, 'references/native-similarity/native-04-12-19-1.npz'})
        self.assertEqual(integration['preserveBaseFiles'], [builder.SHARED_INDEX_NAME])
        self.assertIn(builder.SHARED_INDEX_NAME, integration['candidateOnlyFiles'])
        self.assertEqual(len(json.loads((self.output / builder.SHARED_INDEX_NAME).read_text())['entries']), 3)
        stage = self.root / 'full-base-stage'
        shutil.copytree(self.source, stage)
        for name in integration['newFiles']:
            target = stage / name
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(self.output / name, target)
        self.assertEqual((stage / builder.SHARED_INDEX_NAME).read_bytes(), original_index)
        self.assertEqual((self.source / builder.SHARED_INDEX_NAME).read_bytes(), original_index)
        self.assertEqual((stage / extra_path).read_bytes(), (self.source / extra_path).read_bytes())
        for dependency in integration['referenceDependencies']:
            self.assertNotIn(dependency['resourcePath'], integration['newFiles'])
            self.assertEqual(builder.sha256(stage / dependency['resourcePath']), dependency['resourceSha256'])


if __name__ == '__main__':
    unittest.main()
