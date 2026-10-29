"""Actual corrupt compressed NPZ through preview loader and desktop refresh.

No Tk or network. Root scheduler/widgets are spies; decompression is real.
"""
import ast
from concurrent.futures import Future
from pathlib import Path
import struct
import subprocess
import sys
import tempfile
from types import ModuleType, SimpleNamespace
import unittest
from unittest.mock import Mock
import zlib

import numpy as np
from PIL import Image

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

    def test_home_missing_tile_result_does_not_stop_refresh_scheduler(self):
        tree = ast.parse((REPO / 'app/desktop_main_ui.py').read_text(encoding='utf-8'))
        load = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == '_load_home_previews')
        ns = dict(load_structure_preview=load_structure_preview, Image=Image)
        exec(compile(ast.Module(body=[load], type_ignores=[]), 'desktop_main_ui.py', 'exec'), ns)
        result = ns['_load_home_previews']([('structure', self.asset)], (20, 20), 3)
        self.assertEqual(result, [None])
        methods = [n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)
                   and n.name in {'_poll_home_previews', '_refresh_view'}]
        cls = ast.ClassDef(name='Subject', bases=[], keywords=[], body=methods, decorator_list=[])
        exec(compile(ast.fix_missing_locations(ast.Module(body=[cls], type_ignores=[])), 'desktop_main_ui.py', 'exec'), ns)
        ui = ns['Subject']()
        ui._closed = False
        ui._poll_updates = Mock()
        ui._home_preview_context = 'current'
        ui._home_preview_active = ('current', [Mock()])
        ui._home_preview_future = Future()
        ui._home_preview_future.set_result(result)
        ui.root = Mock()
        ui.window = Mock(winfo_viewable=Mock(return_value=False))
        ui._refresh_view()
        ui.root.after.assert_called_once_with(350, ui._refresh_view)
        self.assertIsNotNone(ui._refresh_job)


if __name__ == '__main__':
    unittest.main()
