"""Allocation preflight for NPZ resources before their existing decoders run.

These are program decoder limits, not package-supplied permissions. They cover
the measured current four-context resources with room for larger products.
No ndarray is created here; identity and geometry still need their own decoder.
"""
from io import BytesIO
import math
import struct
import zipfile
import zlib

import numpy as np

import star_map_import as files
from resource_zip_io import validate_zip_directory


# Current source inventory: largest member 13,769,780 bytes, archive expanded
# 68,863,748 bytes and 261 members. Payload shapes also get role-specific checks
# in the caller; these aggregate limits alone cannot validate a typed product.
MAX_MEMBER_BYTES = 32 * 1024 * 1024
MAX_EXPANDED_BYTES = 128 * 1024 * 1024
MAX_MEMBERS = 1024
MAX_HEADER_BYTES = 10_000
MAX_DIRECTORY_BYTES = 1024 * 1024


def _reject(reason):
    code = 'imported-array-' + reason
    raise files.ImportRejected(code, code=code)


def validate_npz_bytes(raw):
    """Reject oversized or inconsistent NPY declarations before allocation.

    The actual consumer must subsequently decode and validate required fields,
    metadata, exact dtypes, canvas/graph dimensions and finite numeric values.
    This checks declared payload lengths. Actual truncation and unread member
    CRCs still require the consumer to read its arrays; this is not integrity
    or semantic admission of a product. Multi-disk/ZIP64 end directories and
    self-extracting prefixes are outside this resource decoder's format.
    """
    if not isinstance(raw, bytes) or len(raw) > files.MAX_FILE_BYTES:
        _reject('archive-size')
    try:
        source = BytesIO(raw)
        validate_zip_directory(source, max_entries=MAX_MEMBERS,
                               max_directory_bytes=MAX_DIRECTORY_BYTES, reject=_reject)
        with zipfile.ZipFile(source) as archive:
            rows = archive.infolist()
            if not rows or len(rows) > MAX_MEMBERS:
                _reject('member-count')
            names, total = set(), 0
            for row in rows:
                name = row.filename
                if (not name.endswith('.npy') or '/' in name or '\\' in name
                        or name in {'.npy', '..npy'} or '\0' in name or name.casefold() in names
                        or row.is_dir() or row.flag_bits & 1
                        or row.compress_type not in (zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED)):
                    _reject('member-format')
                names.add(name.casefold())
                total += row.file_size
                if row.file_size > MAX_MEMBER_BYTES or total > MAX_EXPANDED_BYTES:
                    _reject('expanded-size')
                with archive.open(row) as stream:
                    version = np.lib.format.read_magic(stream)
                    if version not in ((1, 0), (2, 0)):
                        _reject('npy-version')
                    prefix = stream.read(2 if version == (1, 0) else 4)
                    header_length = struct.unpack('<H' if version == (1, 0) else '<I', prefix)[0]
                    if header_length > MAX_HEADER_BYTES:
                        _reject('header-size')
                    header = stream.read(header_length)
                    if len(header) != header_length:
                        _reject('header-size')
                    # NumPy's max_header_size is checked after reading the
                    # declared length. Bound that read before calling it.
                    header_source = BytesIO(prefix + header)
                    if version == (1, 0):
                        shape, fortran, dtype = np.lib.format.read_array_header_1_0(header_source, max_header_size=MAX_HEADER_BYTES)
                    else:
                        shape, fortran, dtype = np.lib.format.read_array_header_2_0(header_source, max_header_size=MAX_HEADER_BYTES)
                    if dtype.hasobject:
                        _reject('object-dtype')
                    if dtype.itemsize <= 0:
                        _reject('dtype-size')
                    if any(type(axis) is not int or not 0 <= axis <= MAX_MEMBER_BYTES for axis in shape):
                        _reject('shape')
                    expected = math.prod(shape) * dtype.itemsize
                    if expected > MAX_MEMBER_BYTES or stream.tell() + expected != row.file_size:
                        _reject('payload-size')
    except files.ImportRejected:
        raise
    except (OSError, ValueError, TypeError, EOFError, struct.error, RecursionError,
            zipfile.BadZipFile, zlib.error, NotImplementedError) as error:
        raise files.ImportRejected('imported-array-invalid', code='imported-array-invalid') from error
