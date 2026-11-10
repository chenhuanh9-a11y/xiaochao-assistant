"""Typed role limits at the actual shared-product decoder, before mask work."""
from io import BytesIO
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import numpy as np

from shared_structure_reference import load_shared_structure


def product(shape=(8, 12), *, walls=None, canvas=None):
    metadata = dict(sourceKind='game-rendered-shared-structure',
                    roles=dict(clean='mask', validation='mask'),
                    binding=dict(profile='native', mapId='map1', variant='variant-01', floor='1'))
    if canvas is not None:
        metadata['canvas'] = canvas
    arrays = dict(mask=np.ones(shape, np.uint8))
    if walls is not None:
        metadata['roles']['nativeWalls'] = 'walls'
        arrays['walls'] = walls
    stream = BytesIO()
    np.savez_compressed(stream, metadata=json.dumps(metadata), **arrays)
    return stream.getvalue(), metadata


class ImportedStructureDeclarationTests(unittest.TestCase):
    def test_actual_decoder_accepts_a_bound_compact_product(self):
        raw, metadata = product(canvas=dict(shapeHW=[8, 12], height=8, width=12))
        result = load_shared_structure(Path('unused.npz'), read_resource=lambda _: raw,
                                       expected_binding=metadata['binding'], expected_shape=(8, 12))
        self.assertIsNotNone(result)
        np.testing.assert_array_equal(result.clean_mask, np.ones((8, 12), np.uint8))
        self.assertEqual(result.metadata, metadata)

    def test_imported_canvas_axis_is_rejected_before_mask_conversion(self):
        raw, _ = product((1, 8193))
        with patch('shared_structure_reference._binary', side_effect=AssertionError('mask conversion entered')):
            self.assertIsNone(load_shared_structure(Path('unused.npz'), read_resource=lambda _: raw))

    def test_imported_declared_canvas_mismatch_is_rejected_before_mask_conversion(self):
        raw, _ = product(canvas=dict(shapeHW=[8, 13], height=8, width=13))
        with patch('shared_structure_reference._binary', side_effect=AssertionError('mask conversion entered')):
            self.assertIsNone(load_shared_structure(Path('unused.npz'), read_resource=lambda _: raw))

    def test_imported_wall_count_is_rejected_before_any_mask_or_wall_work(self):
        raw, _ = product(walls=np.zeros((65537, 6), np.float32))
        with patch('shared_structure_reference._binary', side_effect=AssertionError('mask conversion entered')):
            self.assertIsNone(load_shared_structure(Path('unused.npz'), read_resource=lambda _: raw))

    def test_trusted_path_decoder_keeps_its_existing_canvas_policy(self):
        raw, _ = product((1, 8193))
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'legacy.npz'
            path.write_bytes(raw)
            result = load_shared_structure(path)
        self.assertIsNotNone(result)
        self.assertEqual(result.shape, (1, 8193))

    def test_unsigned_wall_coordinates_match_the_existing_loader(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'walls.npz'
            for dtype in (np.uint8, np.uint16, np.uint32, np.uint64):
                with self.subTest(dtype=dtype):
                    raw, _ = product(walls=np.asarray([[0, 1, 2, 1, 3, 0]], dtype=dtype))
                    path.write_bytes(raw)
                    trusted = load_shared_structure(path)
                    imported = load_shared_structure(path, read_resource=lambda _: raw)
                    self.assertIsNotNone(trusted)
                    self.assertIsNotNone(imported)
                    self.assertEqual(imported.native_wall_segments, trusted.native_wall_segments)

    def test_raw_member_alias_cannot_select_an_unchecked_mask(self):
        metadata = dict(sourceKind='game-rendered-shared-structure',
            roles=dict(clean='mask.npy', validation='mask.npy'),
            canvas=dict(shapeHW=[8, 12], height=8, width=12))
        stream = BytesIO()
        np.savez_compressed(stream, metadata=json.dumps(metadata),
                            **{'mask': np.ones((1, 8193), np.uint8),
                               'mask.npy': np.ones((8, 12), np.uint8)})
        result = load_shared_structure(Path('unused.npz'), read_resource=lambda _: stream.getvalue())
        self.assertTrue(result is None, 'raw member bypassed the declared mask bounds')

    def test_raw_member_alias_cannot_select_unchecked_wall_rows(self):
        metadata = dict(sourceKind='game-rendered-shared-structure',
            roles=dict(clean='mask', validation='mask', nativeWalls='walls.npy'))
        stream = BytesIO()
        np.savez_compressed(stream, metadata=json.dumps(metadata), mask=np.ones((8, 12), np.uint8),
            **{'walls': np.tile(np.asarray([[0, 1, 2, 1, 3, 0]], np.float32), (65537, 1)),
               'walls.npy': np.asarray([[0, 1, 2, 1, 3, 0]], np.float32)})
        result = load_shared_structure(Path('unused.npz'), read_resource=lambda _: stream.getvalue())
        self.assertTrue(result is None, 'raw member bypassed the declared wall count')

    def test_invalid_unicode_codepoints_in_metadata_are_rejected(self):
        for codepoint in (0x110000, 0xffffffff):
            with self.subTest(codepoint=codepoint):
                stream = BytesIO()
                # A real NPY Unicode scalar can contain non-Unicode uint32
                # payloads even though its header and CRC are well formed.
                metadata = np.asarray(codepoint, np.uint32).view('U1')
                np.savez_compressed(stream, metadata=metadata, mask=np.ones((1, 1), np.uint8))
                self.assertIsNone(load_shared_structure(Path('unused.npz'),
                    read_resource=lambda _: stream.getvalue()))


if __name__ == '__main__':
    unittest.main()
