"""Bound ZIP metadata allocation before Python constructs its ZipInfo list.

This checks the ordinary central directory, not member integrity or resource
semantics. ZIP64 end directories, split archives and prefixed executables are
outside this resource format; ZIP64 member-size extras remain supported.
"""
from __future__ import annotations

from dataclasses import dataclass
import operator
import struct


ZIP_END_WINDOW_BYTES = 65_557  # Ordinary EOCD plus its maximum 65,535-byte comment.
_END = b'PK\x05\x06'
_CENTRAL = b'PK\x01\x02'
_ZIP64_LOCATOR = b'PK\x06\x07'


def _fail(reject, reason):
    reject(reason)
    raise ValueError('ZIP rejection callback returned: ' + reason)


@dataclass(frozen=True)
class ZipDirectorySnapshot:
    """The actual bounded metadata suffix, at its original logical offsets."""

    length: int
    metadata_start: int
    metadata: bytes


def _snapshot(length, start, end, directory, tail, reject):
    tail_start = length - len(tail)
    overlap = max(start, tail_start)
    if directory[overlap - start:] != tail[overlap - tail_start:end - tail_start]:
        _fail(reject, 'directory-format')
    # These adjacent/overlapping ranges are original bytes, not reconstructed
    # ZIP records. Freeze their union so Python reads exactly the checked EOCD
    # and directory even when the source descriptor is modified afterwards.
    prefix = tail[:max(0, start - tail_start)]
    metadata = prefix + directory + tail[end - tail_start:]
    return ZipDirectorySnapshot(length, min(start, tail_start), metadata)


def validate_zip_directory(stream, *, max_entries, max_directory_bytes, reject) -> ZipDirectorySnapshot:
    """Inspect bounded bytes and actual records before constructing ZipFile.

    ``reject(reason)`` must raise the caller's format error. The stream must be
    seekable and remain open; its original position is restored on every path.
    Immutable byte streams may ignore the returned snapshot. Mutable sources
    must pass it to NonOwningBoundedReader for ZipFile's metadata parsing, and
    retain their payload integrity and source identity checks.
    """
    if (type(max_entries) is not int or max_entries < 0
            or type(max_directory_bytes) is not int or max_directory_bytes < 0):
        raise ValueError('ZIP directory limits must be nonnegative integers')
    if not callable(reject):
        raise TypeError('ZIP directory validation requires a rejection callback')
    original = stream.tell()
    try:
        stream.seek(0, 2)
        length = stream.tell()
        tail_size = min(length, ZIP_END_WINDOW_BYTES)
        stream.seek(length - tail_size)
        tail = stream.read(tail_size)
        if len(tail) != tail_size or len(tail) < 22:
            _fail(reject, 'directory-format')
        # Match ZipFile's no-comment fast path before searching. An EOCD field
        # itself may contain signature bytes, so an unconditional rfind differs.
        if tail[-22:-18] == _END and tail[-2:] == b'\0\0':
            offset = len(tail) - 22
        else:
            offset = tail.rfind(_END)
        if offset < 0 or len(tail) - offset < 22:
            _fail(reject, 'directory-format')
        end = length - tail_size + offset
        _, disk, directory_disk, disk_count, count, size, start, comment = struct.unpack_from(
            '<4s4H2IH', tail, offset)
        if (disk or directory_disk or disk_count != count or count == 0xffff
                or start == 0xffffffff or size == 0xffffffff
                or end + 22 + comment != length or start + size != end):
            _fail(reject, 'directory-format')
        if count > max_entries:
            _fail(reject, 'member-count')
        if size > max_directory_bytes:
            _fail(reject, 'directory-size')
        if count == 0:
            if size or start or end:
                _fail(reject, 'directory-format')
            return ZipDirectorySnapshot(length, 0, tail)
        if size < 46:
            _fail(reject, 'directory-format')
        stream.seek(start)
        directory = stream.read(size)
        if len(directory) != size:
            _fail(reject, 'directory-format')
        # ZipFile consults this locator even without ordinary ZIP64 sentinel
        # fields. It can be hidden inside the final central record's comment
        # and redirect Python to a completely different, much larger directory.
        # Reading the central bytes also covers a locator before a max-size
        # archive comment, where it lies just outside the EOCD tail window.
        if directory[-20:-16] == _ZIP64_LOCATOR:
            _fail(reject, 'directory-format')
        position, actual, first_local = 0, 0, None
        while position < size:
            if size - position < 46 or directory[position:position + 4] != _CENTRAL:
                _fail(reject, 'directory-format')
            name, extra, note, member_disk = struct.unpack_from('<4H', directory, position + 28)
            local = struct.unpack_from('<I', directory, position + 42)[0]
            if member_disk:
                _fail(reject, 'directory-format')
            first_local = local if first_local is None else min(first_local, local)
            position += 46 + name + extra + note
            actual += 1
            if actual > max_entries:
                _fail(reject, 'member-count')
            if position > size:
                _fail(reject, 'directory-format')
        if actual != count or first_local != 0:
            _fail(reject, 'directory-format')
        return _snapshot(length, start, end, directory, tail, reject)
    finally:
        stream.seek(original)


class NonOwningBoundedReader:
    """Limit every raw read while preserving the caller's descriptor ownership.

    Negative/omitted sizes become an explicit remaining-byte count. Thus normal
    ZipFile tail reads work, but a changed EOCD cannot request a huge allocation
    after directory preflight. Existing callers still own source-change checks.
    """

    def __init__(self, stream, *, max_read_bytes, reject, directory_snapshot=None):
        if type(max_read_bytes) is not int or max_read_bytes <= 0:
            raise ValueError('ZIP read limit must be a positive integer')
        if not callable(reject):
            raise TypeError('ZIP reader requires a rejection callback')
        if directory_snapshot is not None and not isinstance(directory_snapshot, ZipDirectorySnapshot):
            raise TypeError('ZIP reader requires a validated directory snapshot')
        self._stream = stream
        self._max_read_bytes = max_read_bytes
        self._reject = reject
        self._closed = False
        self._snapshot = directory_snapshot
        self._length = directory_snapshot.length if directory_snapshot is not None else None
        self._position = stream.tell() if directory_snapshot is not None else None

    def _check_open(self):
        if self._closed:
            raise ValueError('I/O operation on closed ZIP reader')

    @property
    def closed(self):
        return self._closed

    def close(self):
        self._closed = True
        self._snapshot = None

    def release_metadata(self):
        """After ZipFile construction, read member bodies from the original fd.

        Logical EOF remains the snapshot's EOF. Source checks still reject any
        growth or rewrite; dropping the metadata bytes does not grant trust.
        """
        self._check_open()
        self._snapshot = None

    def seekable(self):
        self._check_open()
        return self._stream.seekable()

    def seek(self, offset, whence=0):
        self._check_open()
        if self._length is not None:
            offset = operator.index(offset)
            if whence not in (0, 1, 2):
                raise ValueError('invalid seek whence')
            position = offset + (0 if whence == 0 else self._position if whence == 1 else self._length)
            if position < 0:
                raise OSError('negative seek position')
            self._position = position
            return position
        return self._stream.seek(offset, whence)

    def tell(self):
        self._check_open()
        if self._length is not None:
            return self._position
        return self._stream.tell()

    def read(self, size=-1):
        self._check_open()
        size = -1 if size is None else operator.index(size)
        if size < 0:
            if self._length is not None:
                size = max(0, self._length - self._position)
            else:
                position = self._stream.tell()
                try:
                    self._stream.seek(0, 2)
                    size = max(0, self._stream.tell() - position)
                finally:
                    self._stream.seek(position)
        if size > self._max_read_bytes:
            _fail(self._reject, 'read-size')
        if self._length is None:
            return self._stream.read(size)
        size = min(size, max(0, self._length - self._position))
        if not size:
            return b''
        start = self._position
        snapshot = self._snapshot
        if snapshot is None or start + size <= snapshot.metadata_start:
            self._stream.seek(start)
            data = self._stream.read(size)
        else:
            prefix_size = max(0, snapshot.metadata_start - start)
            prefix = b''
            if prefix_size:
                self._stream.seek(start)
                prefix = self._stream.read(prefix_size)
            if len(prefix) < prefix_size:
                data = prefix
            else:
                offset = max(start, snapshot.metadata_start) - snapshot.metadata_start
                data = prefix + snapshot.metadata[offset:offset + size - prefix_size]
        self._position += len(data)
        return data


__all__ = ['ZIP_END_WINDOW_BYTES', 'ZipDirectorySnapshot',
           'validate_zip_directory', 'NonOwningBoundedReader']
