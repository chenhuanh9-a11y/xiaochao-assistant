"""Actual corrupt compressed NPZ through preview loader and desktop refresh.

No Tk or network. Root scheduler/widgets are spies; decompression is real.
"""
from pathlib import Path
import struct
import subprocess
import sys
import tempfile
from types import ModuleType
import unittest
import zlib

import numpy as np

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / 'app'))
if '--baseline' in sys.argv:
    i = sys.argv.index('--baseline')
    revision = sys.argv[i + 1]
    source = subprocess.check_output(['git', 'show', f'{revision}:app/map_resource_overview.py'],
                                     cwd=REPO, text=True, encoding='utf-8')
    baseline = ModuleType('map_resource_overview')
    sys.modules['map_resource_overview'] = baseline
    exec(compile(source, f'{revision}:app/map_resource_overview.py', 'exec'), baseline.__dict__)
    del sys.argv[i:i + 2]
from map_resource_overview import StructurePreview, load_structure_preview


class StructureCorruption(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        path = Path(self.folder.name) / 'damaged.npz'
        np.savez_compressed(path, shared_mask=np.eye(24, dtype=np.uint8))
        payload = bytearray(path.read_bytes())
        name_length, extra_length = struct.unpack_from('<HH', payload, 26)
        payload[30 + name_length + extra_length] = 0x07  # reserved deflate block type
        path.write_bytes(payload)
        self.asset = StructurePreview('fixture', 'Fixture', '1', path, 'shared_mask')

    def test_real_deflate_and_empty_input_normalize_at_resource_boundary(self):
        with self.assertRaises(zlib.error):
            with np.load(self.asset.path, allow_pickle=False) as archive:
                archive[self.asset.array]
        with self.assertRaisesRegex(ValueError, '结构图文件损坏') as caught:
            load_structure_preview(self.asset, (20, 20))
        self.assertIsInstance(caught.exception.__cause__, zlib.error)
        self.asset.path.write_bytes(b'')
        with self.assertRaisesRegex(ValueError, '结构图文件损坏') as caught:
            load_structure_preview(self.asset, (20, 20))
        self.assertIsInstance(caught.exception.__cause__, EOFError)


if __name__ == '__main__':
    unittest.main()
