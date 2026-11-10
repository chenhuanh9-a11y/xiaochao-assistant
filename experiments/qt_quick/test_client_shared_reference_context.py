"""Real typed reference cache against an actual catalog lifetime change."""
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from uuid import uuid4

import numpy as np

from client_providers import SharedReferenceProvider
from imported_resource_catalog import ResourceResolver
from shared_structure_reference import SharedStructureResourceStore


class SharedReferenceContextTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        binding = dict(profile='native', mapId='map1', variant='variant-01', floor='1')
        canvas = dict(shapeHW=[8, 12], height=8, width=12)
        expected = dict(structureVersionKey='fixture-v1', sourceVersion='source-v1', baseSnapshot='base-v1')
        self.path = self.root / 'one.npz'
        np.savez_compressed(self.path, mask=np.ones((8, 12), np.uint8),
            metadata=json.dumps(dict(sourceKind='game-rendered-shared-structure',
                roles=dict(clean='mask', validation='mask'), binding=binding, canvas=canvas, **expected)))
        index = self.root / 'index.json'
        index.write_text(json.dumps(dict(schemaVersion=1, resourceKind='shared-structure',
            entries=[dict(binding, resourcePath='one.npz')])), encoding='utf-8')
        self.store = SharedStructureResourceStore(index, self.root)
        self.assertIsNone(self.store.error)
        self.versions = {('native', 'map1', 'variant-01', '1'): dict(
            expected=expected, binding=binding, path=self.path, canvas=canvas, sharedError=None)}
        self.snapshot = ResourceResolver(self.root).snapshot()

    def change_catalog(self):
        (self.root / 'catalog.json').write_text(json.dumps(dict(schema='xingtu-resource-catalog-v1',
            generation=str(uuid4()), contexts={}, displays=[])), encoding='utf-8')

    def provider(self):
        return SharedReferenceProvider(self.root, self.store, self.versions,
                                       check_current=self.snapshot.assert_current)

    def test_cached_reference_is_not_returned_after_catalog_changes(self):
        provider = self.provider()
        with patch.object(self.store, 'load', wraps=self.store.load) as load:
            reference = provider('display', 'map1', '1')
            self.assertIsNotNone(reference)
            self.assertIs(provider('display', 'map1', '1'), reference)
            load.assert_called_once()
            self.change_catalog()
            with self.assertRaisesRegex(ValueError, 'superseded'):
                provider('display', 'map1', '1')
            load.assert_called_once()

    def test_binding_metadata_is_not_returned_after_catalog_changes(self):
        provider = self.provider()
        self.assertEqual(provider.binding_metadata('display', 'map1', '1')['shape'], [8, 12])
        self.change_catalog()
        with self.assertRaisesRegex(ValueError, 'superseded'):
            provider.binding_metadata('display', 'map1', '1')

    def test_catalog_change_during_actual_load_does_not_publish_or_cache_result(self):
        provider = self.provider()
        original_load = self.store.load
        def load(*args, **kwargs):
            reference = original_load(*args, **kwargs)
            self.assertIsNotNone(reference)
            self.change_catalog()
            return reference
        with patch.object(self.store, 'load', side_effect=load):
            with self.assertRaisesRegex(ValueError, 'superseded'):
                provider('display', 'map1', '1')
        self.assertEqual(len(provider._cache), 0)


if __name__ == '__main__':
    unittest.main()
