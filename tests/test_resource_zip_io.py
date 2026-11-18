"""Real ZIP metadata fixtures and raw-read allocation boundaries."""
from io import BytesIO
import struct
import unittest
import zipfile

from resource_zip_io import (NonOwningBoundedReader, ZIP_END_WINDOW_BYTES, ZipDirectorySnapshot,
                             validate_zip_directory)


class Rejected(ValueError):
    pass


def reject(reason):
    raise Rejected(reason)


def archive_bytes(*, count=3, compression=zipfile.ZIP_STORED, comment=b'', local_zip64=False,
                  payload=b'payload'):
    target = BytesIO()
    with zipfile.ZipFile(target, 'w', compression=compression) as archive:
        archive.comment = comment
        for index in range(count):
            if local_zip64:
                with archive.open(f'file-{index}.bin', 'w', force_zip64=True) as member:
                    member.write(payload)
            else:
                archive.writestr(f'file-{index}.bin', payload)
    return target.getvalue()


def zip64_shadow_directory(raw, count=4096):
    """Ordinary EOCD describes one record; Python's ZIP64 locator selects many."""
    end = raw.rfind(b'PK\x05\x06')
    start = struct.unpack_from('<I', raw, end + 16)[0]
    template = raw[start:start + 46]

    def record(name, comment=0):
        header = bytearray(template)
        struct.pack_into('<3H', header, 28, len(name), 0, comment)
        return bytes(header) + name

    preceding = b''.join(record(f'file-{index}.bin'.encode()) for index in range(count - 1))
    fake = record(b'fake.bin', comment=76)
    directory = preceding + record(fake)
    zip64 = struct.pack('<4sQ2H2I4Q', b'PK\x06\x06', 44, 45, 45, 0, 0,
                        count, count, len(directory), 0)
    locator = struct.pack('<4sIQI', b'PK\x06\x07', 0, len(directory), 1)
    ordinary = struct.pack('<4s4H2IH', b'PK\x05\x06', 0, 0, 1, 1,
                           len(fake) + 76, len(preceding) + 46, 0)
    return directory + zip64 + locator + ordinary


class RecordingStream(BytesIO):
    def __init__(self, raw):
        super().__init__(raw)
        self.read_sizes = []

    def read(self, size=-1):
        self.read_sizes.append(size)
        return super().read(size)


class ResourceZipIOTests(unittest.TestCase):
    def validate(self, stream, *, max_entries=10, max_directory_bytes=4096):
        return validate_zip_directory(stream, max_entries=max_entries,
            max_directory_bytes=max_directory_bytes, reject=reject)

    def test_real_stored_deflated_and_local_zip64_members_remain_readable(self):
        for compression in (zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED):
            for local_zip64 in (False, True):
                with self.subTest(compression=compression, local_zip64=local_zip64):
                    raw = archive_bytes(compression=compression, local_zip64=local_zip64)
                    stream = BytesIO(raw)
                    stream.seek(7)
                    snapshot = self.validate(stream)
                    self.assertIsInstance(snapshot, ZipDirectorySnapshot)
                    self.assertEqual(stream.tell(), 7)
                    reader = NonOwningBoundedReader(stream, max_read_bytes=ZIP_END_WINDOW_BYTES,
                                                   reject=reject, directory_snapshot=snapshot)
                    with zipfile.ZipFile(reader) as archive:
                        reader.release_metadata()
                        self.assertEqual(len(archive.infolist()), 3)
                        self.assertEqual(archive.read('file-0.bin'), b'payload')
                    self.assertFalse(stream.closed)
                    reader.close()
                    self.assertFalse(stream.closed)

    def test_empty_and_maximum_comment_archives_use_bounded_tail_reads(self):
        for count in (0, 3):
            for comment in (b'', b'comment', b'x' * 65535):
                with self.subTest(count=count, comment_bytes=len(comment)):
                    raw = archive_bytes(count=count, comment=comment)
                    stream = RecordingStream(raw)
                    snapshot = self.validate(stream)
                    self.assertEqual(snapshot.metadata, raw[snapshot.metadata_start:])
                    self.assertEqual(snapshot.length, len(raw))
                    self.assertLessEqual(stream.read_sizes[0], ZIP_END_WINDOW_BYTES)
                    self.assertTrue(all(0 <= size <= ZIP_END_WINDOW_BYTES
                                        for size in stream.read_sizes))

    def test_snapshot_contains_only_original_bounded_metadata_bytes(self):
        raw = archive_bytes(count=1200, payload=b'x' * 128)
        snapshot = self.validate(BytesIO(raw), max_entries=1200, max_directory_bytes=128 * 1024)
        self.assertGreater(snapshot.metadata_start, 0)
        self.assertEqual(snapshot.metadata, raw[snapshot.metadata_start:])
        self.assertLessEqual(len(snapshot.metadata), 128 * 1024 + ZIP_END_WINDOW_BYTES)

    def test_source_rewrite_cannot_replace_snapshot_metadata_and_body_still_uses_source(self):
        stream = RecordingStream(archive_bytes())
        snapshot = self.validate(stream)
        stream.seek(0)
        stream.truncate()
        stream.write(archive_bytes(count=4096, payload=b'changed'))
        stream.read_sizes.clear()
        reader = NonOwningBoundedReader(stream, max_read_bytes=ZIP_END_WINDOW_BYTES,
                                       reject=reject, directory_snapshot=snapshot)
        with zipfile.ZipFile(reader) as archive:
            self.assertEqual(len(archive.infolist()), 3)
            self.assertEqual(stream.read_sizes, [])
            reader.release_metadata()
            with self.assertRaisesRegex(zipfile.BadZipFile, 'CRC'):
                archive.read('file-0.bin')
        self.assertTrue(stream.read_sizes)
        self.assertFalse(stream.closed)

    def test_metadata_modified_between_tail_and_central_reads_is_rejected(self):
        raw = archive_bytes()
        end = raw.rfind(b'PK\x05\x06')
        start = struct.unpack_from('<I', raw, end + 16)[0]

        class MutatingStream(BytesIO):
            def read(self, size=-1):
                result = super().read(size)
                if self.tell() == len(raw):
                    position = self.tell()
                    self.seek(start + 38)
                    self.write(bytes([raw[start + 38] ^ 1]))
                    self.seek(position)
                return result

        stream = MutatingStream(raw)
        with self.assertRaisesRegex(Rejected, '^directory-format$'):
            self.validate(stream)
        self.assertEqual(stream.tell(), 0)

    def test_forged_smaller_count_checks_actual_records_before_acceptance(self):
        raw = bytearray(archive_bytes())
        end = raw.rfind(b'PK\x05\x06')
        struct.pack_into('<2H', raw, end + 8, 1, 1)
        stream = BytesIO(raw)
        stream.seek(9)
        with self.assertRaisesRegex(Rejected, '^directory-format$'):
            self.validate(stream)
        self.assertEqual(stream.tell(), 9)
        with self.assertRaisesRegex(Rejected, '^member-count$'):
            self.validate(stream, max_entries=2)

    def test_declared_count_and_directory_byte_limits_precede_directory_read(self):
        for overrides, expected in (({'max_entries': 2}, 'member-count'),
                                    ({'max_directory_bytes': 1}, 'directory-size')):
            stream = RecordingStream(archive_bytes())
            with self.subTest(overrides=overrides), self.assertRaisesRegex(Rejected, '^' + expected + '$'):
                self.validate(stream, **overrides)
            self.assertEqual(len(stream.read_sizes), 1)

    def test_zip64_shadow_cannot_replace_the_prechecked_ordinary_directory(self):
        raw = zip64_shadow_directory(archive_bytes(count=1))
        with zipfile.ZipFile(BytesIO(raw)) as archive:
            self.assertEqual(len(archive.infolist()), 4096)
        with self.assertRaisesRegex(Rejected, '^directory-format$'):
            self.validate(BytesIO(raw))

    def test_zip64_locator_is_detected_before_a_maximum_archive_comment(self):
        raw = bytearray(zip64_shadow_directory(archive_bytes(count=1)))
        struct.pack_into('<H', raw, len(raw) - 2, 65535)
        raw.extend(b'x' * 65535)
        with self.assertRaisesRegex(Rejected, '^directory-format$'):
            self.validate(BytesIO(raw))

    def test_zip64_end_sentinels_split_disks_and_malformed_records_are_rejected(self):
        original = archive_bytes()
        end = original.rfind(b'PK\x05\x06')
        start = struct.unpack_from('<I', original, end + 16)[0]
        changes = ((end + 4, '<H', 1), (end + 6, '<H', 1),
                   (end + 8, '<H', 2), (end + 8, '<2H', (65535, 65535)),
                   (end + 12, '<I', 0xffffffff), (end + 16, '<I', 0xffffffff),
                   (start + 34, '<H', 1), (start + 28, '<H', 65535))
        for position, fmt, values in changes:
            raw = bytearray(original)
            struct.pack_into(fmt, raw, position, *(values if isinstance(values, tuple) else (values,)))
            with self.subTest(position=position, fmt=fmt), self.assertRaises(Rejected):
                self.validate(BytesIO(raw))
        for raw in (b'not a ZIP', original[:-1], original + b'trailing data'):
            with self.subTest(raw_bytes=len(raw)), self.assertRaises(Rejected):
                self.validate(BytesIO(raw))

    def test_sfx_prefixes_with_original_or_corrected_offsets_are_rejected(self):
        original = archive_bytes()
        prefix = b'MZprefix'
        raw = bytearray(prefix + original)
        with self.assertRaisesRegex(Rejected, '^directory-format$'):
            self.validate(BytesIO(raw))
        end = raw.rfind(b'PK\x05\x06')
        start = struct.unpack_from('<I', raw, end + 16)[0] + len(prefix)
        struct.pack_into('<I', raw, end + 16, start)
        position = start
        while position < end:
            local = struct.unpack_from('<I', raw, position + 42)[0]
            struct.pack_into('<I', raw, position + 42, local + len(prefix))
            name, extra, note = struct.unpack_from('<3H', raw, position + 28)
            position += 46 + name + extra + note
        with self.assertRaisesRegex(Rejected, '^directory-format$'):
            self.validate(BytesIO(raw))

    def test_proxy_rejects_huge_positive_or_remaining_read_before_underlying_read(self):
        stream = RecordingStream(b'x' * 128)
        reader = NonOwningBoundedReader(stream, max_read_bytes=32, reject=reject)
        for size in (2**40, -1, None):
            with self.subTest(size=size), self.assertRaisesRegex(Rejected, '^read-size$'):
                reader.read(size)
            self.assertEqual(stream.tell(), 0)
            self.assertEqual(stream.read_sizes, [])
        reader.seek(-22, 2)
        self.assertEqual(reader.read(), b'x' * 22)
        self.assertEqual(stream.read_sizes, [22])
        reader.seek(-20, 2)
        self.assertEqual(reader.read(None), b'x' * 20)
        self.assertEqual(stream.read_sizes, [22, 20])

    def test_proxy_computes_remaining_again_after_source_growth(self):
        stream = RecordingStream(b'x' * 22)
        reader = NonOwningBoundedReader(stream, max_read_bytes=32, reject=reject)
        self.assertEqual(reader.read(), b'x' * 22)
        stream.seek(0, 2)
        stream.write(b'x' * 128)
        reader.seek(0)
        with self.assertRaisesRegex(Rejected, '^read-size$'):
            reader.read()
        self.assertEqual(stream.read_sizes, [22])


if __name__ == '__main__':
    unittest.main()
