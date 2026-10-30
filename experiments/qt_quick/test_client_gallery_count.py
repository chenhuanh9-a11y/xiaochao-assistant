"""Map quantities deduplicate floor/variant rows, while browsing keeps each row."""
import json
from pathlib import Path
import tempfile
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from test_client_gallery_layout import APP
from backend import Backend
from client_paths import WORK_ROOT
from map_resource_model import ResourceMap, ResourceVariant


def until(predicate):
    deadline = time.monotonic() + 3
    while time.monotonic() < deadline:
        APP.processEvents()
        if predicate():
            return
        time.sleep(.002)
    raise AssertionError('Gallery callback timed out')


class GalleryMapCountTests(unittest.TestCase):
    def test_maps_count_available_identities_once_across_floors_variants_and_stale_search(self):
        WORK_ROOT.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(prefix='gallery-map-count-', dir=WORK_ROOT) as folder:
            root = Path(folder)
            (root / '.qt-lab-owner').write_text('qt-quick-isolated-v1')
            a, b, missing = root/'a.png', root/'b.png', root/'missing.png'
            maps = (
                ResourceMap('map-a', 'Map A', (ResourceVariant('v1', 'Route 1', {'1': a, '2': a}), ResourceVariant('v2', 'Route 2', {'1': a, '2': a}))),
                ResourceMap('map-b', 'Map B', (ResourceVariant('v1', 'Route 1', {'1': missing, '2': b}),)),
                ResourceMap('map-c', 'Map C', (ResourceVariant('v1', 'Route 1', {'1': missing}),)),
            )
            profile = SimpleNamespace(profile_id='author', name='Author', available_layers=5, available_maps=2, maps=maps, map_count=3, first_preview=('map-a','1',a))
            context = SimpleNamespace(error='', profiles=[profile], path_stamps={a:(1,1),b:(1,1),missing:None})
            with patch('backend.load_browse_context', return_value=context):
                backend = Backend(root)
                until(lambda: not backend.busy)
                self.assertEqual(backend.profiles[0]['availableMaps'], 2)
                self.assertEqual(backend.profiles[0]['available'], 5)
                self.assertEqual(backend.count, 7)
                self.assertEqual(backend.mapCount, 2)
                backend.setFloor('1')
                self.assertEqual((backend.count, backend.mapCount), (4, 1))
                self.assertEqual(backend.message, '已读取 1 份图')
                backend.setFloor('2')
                self.assertEqual((backend.count, backend.mapCount), (3, 2))
                backend.setFloor('')
                backend.search('Map C')
                self.assertEqual((backend.count, backend.mapCount), (1, 0))
                backend.clearGallerySearch()
                self.assertEqual((backend.count, backend.mapCount), (7, 2))
                self.assertEqual(backend._query, '')
                backend.shutdown()


if __name__ == '__main__':
    unittest.main()
