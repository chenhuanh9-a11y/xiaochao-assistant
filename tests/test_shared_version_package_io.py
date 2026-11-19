"""Actual version-context IO through a real immutable PackageView.

This checks dependency metadata and NPZ declaration reads. Identity/pose solving,
author display and external decoder admission are outside this module's scope.
"""
from io import BytesIO
import hashlib
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest

import numpy as np
from imported_resource_packages import ComponentRef, PackageView
from shared_structure_context import load_shared_version_context


def raw_json(value):
    return json.dumps(value).encode('utf-8')


class SharedVersionPackageIOTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.store_root = Path(self.temp.name).resolve()
        self.package_root = self.store_root / 'packages/fixture/v1'
        self.root = self.package_root / 'data/base'
        self.root.mkdir(parents=True)
        self.binding = dict(profile='shared-structure', mapId='map1', floor='1', variant='variant-01')
        self.version = dict(structureVersionKey='layout1@' + 'a' * 64 + '/1', sourceVersion='source-v1', baseSnapshot='base-v1')
        self.canvas = dict(shapeHW=[12, 16], width=16, height=12, floor='1', coordinateSpace='fixture-pixels')
        resource_key = dict(layoutId='layout1', structureRawRecordSha256='a' * 64, floor='1')
        self.entry = dict(self.binding, resourcePath='one.npz', **self.version)
        self.documents = {
            'identity.json': dict(maps=[dict(id='map1', sourceLayout='layout1', structureContentSha256='a' * 64,
                                              sourceVersion='source-v1')]),
            'typed.json': dict(schemaVersion=1, resourceKind='shared-structure', structureVersionBindings='versions.json',
                               entries=[self.entry]),
            'versions.json': dict(schemaVersion=1, resourceKind='native-structure-version-bindings', entries=[
                dict(mapId='map1', floor='1', resourceKey=resource_key, sourceVersion='source-v1',
                     sharedLookup=dict(self.binding, resourcePath='one.npz'), typedReference='one.npz',
                     neutralDisplay=dict(baseResourceKey=resource_key))]),
            'neutral.json': dict(schemaVersion=1, resourceKind='typed-shared-structure-display', indexPath='typed.json',
                entries=[dict(mapId='map1', floor='1', binding=self.binding, referenceResource='one.npz',
                              canvas=self.canvas, **self.version)]),
        }
        stream = BytesIO()
        np.savez_compressed(stream, metadata=np.asarray(json.dumps(dict(self.version, binding=self.binding, canvas=self.canvas))),
                            clean=np.ones((12, 16), np.uint8), validation=np.ones((12, 16), np.uint8))
        self.payloads = {'one.npz': stream.getvalue()}
        self.pack = SimpleNamespace(manifest_path=self.root / 'identity.json', by_id={'map1': object()},
                                    floors=('1',), identity_profiles={'map1': 'shared-structure'})
        self.resources = SimpleNamespace(index_path=self.root / 'typed.json',
                                        entries={('shared-structure', 'map1', 'variant-01', '1'): self.entry})
        adapter = dict(self.version, referenceResource='one.npz')
        self.adapters = SimpleNamespace(coordinate_reference_profile_id='shared-structure',
            maps={'map1': dict(floors={'1': adapter})},
            profiles={'shared-structure': dict(maps={'map1': dict(floors={'1': adapter})})})
        self.reads = []
        self.write_package()

    def write_package(self, *, undeclared=()):
        payloads = dict(self.payloads, **{name: raw_json(value) for name, value in self.documents.items()})
        for name, raw in payloads.items():
            (self.root / name).write_bytes(raw)
        manifest = dict(schema='xingtu-resource-package', formatVersion=1, packageId='fixture', resourceVersion='v1',
            clientCompatibility=dict(resourceProtocol=1, requiredCapabilities=[]),
            components=[dict(componentId='base', type='recognition-base', schemaVersion=1,
                contexts=[dict(season='s0', mode='nightmare')], entrypoint='identity.json', dependencies={})],
            files=[dict(componentId='base', path=name, bytes=len(raw), sha256=hashlib.sha256(raw).hexdigest())
                   for name, raw in payloads.items() if name not in undeclared])
        raw = raw_json(manifest)
        (self.package_root / 'manifest.json').write_bytes(raw)
        self.view = PackageView(self.store_root, ComponentRef('fixture', 'v1', 'base', hashlib.sha256(raw).hexdigest()))

    def reader(self, path):
        relative = Path(path).relative_to(self.view.root).as_posix()
        self.reads.append(relative)
        return self.view.read(relative)

    def context(self, *, packaged=True):
        return load_shared_version_context(self.root, self.pack, self.resources, self.adapters, None, (),
            neutral_relative='neutral.json', read_resource=self.reader if packaged else None)

    def test_package_reads_exact_metadata_and_npz_with_same_version_verdict(self):
        legacy = self.context(packaged=False)
        packaged = self.context()
        self.assertEqual(packaged, legacy)
        row = next(iter(packaged.values()))
        self.assertIsNone(row['sharedError'])
        self.assertEqual(row['authorError'], 'author-not-installed')
        self.assertEqual(self.reads, ['typed.json', 'versions.json', 'neutral.json', 'identity.json', 'one.npz'])

    def test_real_npz_with_changed_inventory_bytes_cannot_fall_back_to_path(self):
        path = self.root / 'one.npz'
        path.write_bytes(path.read_bytes() + b'changed bytes')
        with np.load(path, allow_pickle=False) as archive:
            self.assertEqual(archive['clean'].shape, (12, 16))
        row = next(iter(self.context().values()))
        self.assertEqual(row['sharedError'], 'shared-NPZ-declaration-unavailable')

    def test_undeclared_existing_dependency_is_not_read_from_disk(self):
        self.write_package(undeclared=('versions.json',))
        self.assertTrue((self.root / 'versions.json').is_file())
        with self.assertRaises(ValueError):
            self.context()

    def test_traversing_dependency_is_rejected_before_reader(self):
        self.documents['typed.json']['structureVersionBindings'] = '../outside.json'
        self.write_package()
        (self.root.parent / 'outside.json').write_bytes(raw_json(self.documents['versions.json']))
        with self.assertRaisesRegex(ValueError, 'escapes'):
            self.context()
        self.assertEqual(self.reads, ['typed.json'])


if __name__ == '__main__':
    unittest.main()
