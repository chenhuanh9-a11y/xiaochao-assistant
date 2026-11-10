"""Actual ZIP/NPY headers, including compact inputs claiming huge allocations."""
from io import BytesIO
import struct
import unittest
from unittest.mock import patch
import zipfile

import numpy as np
from imported_array_archive import validate_npz_bytes
import star_map_import as files


def npy(value):
    stream = BytesIO()
    np.save(stream, value)
    return stream.getvalue()


def archive_bytes(rows, *, compression=zipfile.ZIP_DEFLATED):
    stream = BytesIO()
    with zipfile.ZipFile(stream, 'w', compression=compression) as archive:
        for name, raw in rows:
            archive.writestr(name, raw)
    return stream.getvalue()


class ImportedArrayArchiveTests(unittest.TestCase):
    def test_real_masks_metadata_and_fortran_array_keep_values(self):
        values = dict(clean=np.ones((12, 16), np.uint8), metadata=np.asarray('{"canvas":[12,16]}'),
                      walls=np.asfortranarray(np.arange(24, dtype=np.float32).reshape(4, 6)))
        raw = archive_bytes([(key + '.npy', npy(value)) for key, value in values.items()])
        with patch('numpy.load', side_effect=AssertionError('preflight must not allocate arrays')):
            validate_npz_bytes(raw)
        with np.load(BytesIO(raw), allow_pickle=False) as product:
            for key, expected in values.items():
                np.testing.assert_array_equal(product[key], expected)

    def test_huge_shape_with_tiny_physical_payload_rejected_without_allocation(self):
        for shape, reason in (((2**40, 2**40), 'shape'), ((2**20, 2**20), 'payload-size')):
            stream = BytesIO()
            np.lib.format.write_array_header_1_0(stream, dict(descr='|u1', fortran_order=False, shape=shape))
            raw = archive_bytes([('clean.npy', stream.getvalue())])
            with self.subTest(shape=shape), patch('numpy.load', side_effect=AssertionError('allocation attempted')):
                with self.assertRaisesRegex(files.ImportRejected, '^imported-array-' + reason + '$'):
                    validate_npz_bytes(raw)

    def test_length_mismatch_and_object_arrays_rejected(self):
        for payload in (npy(np.ones((2, 3), np.uint8))[:-1], npy(np.array([object()], dtype=object))):
            with self.subTest(payload_bytes=len(payload)):
                with self.assertRaises(files.ImportRejected):
                    validate_npz_bytes(archive_bytes([('clean.npy', payload)]))

    def test_zero_byte_dtype_and_oversized_empty_axis_are_rejected(self):
        for dtype, shape in (('|V0', (2**40,)), ('|u1', (2**40, 0))):
            stream = BytesIO()
            np.lib.format.write_array_header_1_0(stream, dict(descr=dtype, fortran_order=False, shape=shape))
            with self.subTest(dtype=dtype, shape=shape):
                with self.assertRaises(files.ImportRejected):
                    validate_npz_bytes(archive_bytes([('empty.npy', stream.getvalue())]))

    def test_version_two_npy_is_decoded_by_the_original_consumer(self):
        stream = BytesIO()
        expected = np.arange(12, dtype=np.float64).reshape(3, 4)
        np.lib.format.write_array(stream, expected, version=(2, 0))
        raw = archive_bytes([('matrix.npy', stream.getvalue())])
        validate_npz_bytes(raw)
        with np.load(BytesIO(raw), allow_pickle=False) as product:
            np.testing.assert_array_equal(product['matrix'], expected)

    def test_declared_header_length_is_bounded_before_numpy_header_reader(self):
        payload = np.lib.format.magic(2, 0) + struct.pack('<I', 2**32 - 1)
        with patch('numpy.lib.format.read_array_header_2_0', side_effect=AssertionError('unbounded header read')):
            with self.assertRaisesRegex(files.ImportRejected, '^imported-array-header-size$'):
                validate_npz_bytes(archive_bytes([('a.npy', payload)]))

    def test_paths_duplicate_case_and_non_npy_members_rejected(self):
        scalar = npy(np.asarray(1))
        for rows in ([('../outside.npy', scalar)], [('a.npy', scalar), ('A.npy', scalar)], [('metadata.json', b'{}')]):
            with self.subTest(names=[row[0] for row in rows]):
                with self.assertRaisesRegex(files.ImportRejected, 'member-format'):
                    validate_npz_bytes(archive_bytes(rows))

    def test_member_total_and_count_limits_apply_before_decode(self):
        raw = archive_bytes([('a.npy', npy(np.ones((64, 64), np.uint8))),
                             ('b.npy', npy(np.ones((64, 64), np.uint8)))])
        for setting, limit, reason in (('MAX_MEMBER_BYTES', 4000, 'expanded-size'),
                                       ('MAX_EXPANDED_BYTES', 8000, 'expanded-size'),
                                       ('MAX_MEMBERS', 1, 'member-count')):
            with self.subTest(setting=setting), patch('imported_array_archive.' + setting, limit):
                with self.assertRaisesRegex(files.ImportRejected, reason):
                    validate_npz_bytes(raw)

    def test_actual_directory_count_is_bounded_before_zipinfo_allocation(self):
        raw = archive_bytes([(f'a{index}.npy', b'') for index in range(4096)])
        for forged_count in (None, 1):
            candidate = bytearray(raw)
            if forged_count is not None:
                end = candidate.rfind(b'PK\x05\x06')
                struct.pack_into('<HH', candidate, end + 8, forged_count, forged_count)
            with self.subTest(forged_count=forged_count), patch('imported_array_archive.zipfile.ZipFile',
                    side_effect=AssertionError('unbounded central-directory parser called')):
                with self.assertRaisesRegex(files.ImportRejected, '^imported-array-member-count$'):
                    validate_npz_bytes(bytes(candidate))

    def test_zip64_shadow_directory_cannot_change_the_prechecked_directory(self):
        ordinary = archive_bytes([('a.npy', npy(np.asarray(1)))])
        end = ordinary.rfind(b'PK\x05\x06')
        start = struct.unpack_from('<I', ordinary, end + 16)[0]
        template = ordinary[start:start + 46]
        def record(name, comment=0):
            header = bytearray(template)
            struct.pack_into('<3H', header, 28, len(name), 0, comment)
            return bytes(header) + name
        real_records = b''.join(record(f'a{index}.npy'.encode()) for index in range(4095))
        fake = record(b'fake.npy', comment=76)
        real_directory = real_records + record(fake)
        zip64 = struct.pack('<4sQ2H2I4Q', b'PK\x06\x06', 44, 45, 45, 0, 0,
                            4096, 4096, len(real_directory), 0)
        locator = struct.pack('<4sIQI', b'PK\x06\x07', 0, len(real_directory), 1)
        plain_end = struct.pack('<4s4H2IH', b'PK\x05\x06', 0, 0, 1, 1,
                                len(fake) + 76, len(real_records) + 46, 0)
        raw = real_directory + zip64 + locator + plain_end
        # The standard parser sees a different directory from the ordinary EOCD.
        with zipfile.ZipFile(BytesIO(raw)) as archive:
            self.assertEqual(len(archive.infolist()), 4096)
        with patch('imported_array_archive.zipfile.ZipFile', side_effect=AssertionError('shadow directory parsed')):
            with self.assertRaisesRegex(files.ImportRejected, '^imported-array-directory-format$'):
                validate_npz_bytes(raw)

    def test_corrupt_archive_and_unsupported_compression_rejected(self):
        for raw in (b'not a zip', archive_bytes([('a.npy', npy(np.asarray(1)))], compression=zipfile.ZIP_BZIP2)):
            with self.assertRaises(files.ImportRejected):
                validate_npz_bytes(raw)


if __name__ == '__main__':
    unittest.main()
