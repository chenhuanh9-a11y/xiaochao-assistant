"""Bound typed structure roles before the existing decoder allocates masks.

Only the bounded scalar metadata is decoded here. Binary membership, wall
coordinates and complete payload integrity remain the actual loader's work.
"""
from io import BytesIO

import numpy as np

from imported_array_archive import validate_npz_bytes
from imported_resource_reader import decode_resource_json


MAX_CANVAS_AXIS = 8192
MAX_CANVAS_PIXELS = 16 * 1024 * 1024
MAX_WALL_SEGMENTS = 65536
MAX_METADATA_BYTES = 512 * 1024


def _reject():
    raise ValueError('imported structure role declarations are invalid or oversized')


def validate_structure_declarations(raw, *, expected_binding=None, expected_shape=None):
    headers = validate_npz_bytes(raw)
    def field_header(name):
        if not isinstance(name, str):
            return None
        # NpzFile first resolves an exact raw member, then a field + '.npy'.
        # Stripping suffixes would check mask.npy.npy but load mask.npy.
        return headers.get(name if name in headers else name + '.npy')

    scalar = field_header('metadata')
    if (scalar is None or scalar[0] != () or scalar[1].kind != 'U'
            or scalar[1].itemsize > MAX_METADATA_BYTES):
        _reject()
    try:
        with np.load(BytesIO(raw), allow_pickle=False) as archive:
            text = str(archive['metadata'].item()).encode('utf-8')
    except (SystemError, UnicodeError) as error:
        # NumPy permits invalid uint32 codepoints in a well-formed NPY Unicode
        # payload; scalar conversion can raise SystemError rather than ValueError.
        raise ValueError('imported structure metadata Unicode is invalid') from error
    metadata = decode_resource_json(text)
    if not isinstance(metadata, dict) or metadata.get('sourceKind') not in {
            'game-rendered-shared-structure', 'legacy-shared-binary'}:
        _reject()
    if expected_binding is not None and metadata.get('binding') != dict(expected_binding):
        _reject()
    roles = metadata.get('roles')
    if not isinstance(roles, dict) or not {'clean', 'validation'} <= roles.keys():
        _reject()
    shape = None
    for role in ('clean', 'validation', 'known', 'unknown'):
        name = roles.get(role)
        if name is None and role in ('known', 'unknown'):
            continue
        header = field_header(name)
        if header is None:
            _reject()
        dimensions, dtype = header
        if (dtype != np.dtype('uint8') or len(dimensions) != 2
                or any(not 1 <= axis <= MAX_CANVAS_AXIS for axis in dimensions)
                or dimensions[0] * dimensions[1] > MAX_CANVAS_PIXELS
                or shape is not None and dimensions != shape):
            _reject()
        shape = dimensions
    if (roles.get('known') is None) != (roles.get('unknown') is None):
        _reject()
    if expected_shape is not None and shape != tuple(expected_shape):
        _reject()
    if 'canvas' in metadata:
        canvas = metadata['canvas']
        if not isinstance(canvas, dict):
            _reject()
        if 'shapeHW' in canvas and (not isinstance(canvas['shapeHW'], list)
                or any(type(axis) is not int for axis in canvas['shapeHW'])
                or tuple(canvas['shapeHW']) != shape):
            _reject()
        for field, size in (('height', shape[0]), ('width', shape[1])):
            if field in canvas and (type(canvas[field]) is not int or canvas[field] != size):
                _reject()
    wall_name = roles.get('nativeWalls')
    if wall_name is not None:
        header = field_header(wall_name)
        if header is None:
            _reject()
        dimensions, dtype = header
        if (len(dimensions) != 2 or dimensions[1] != 6
                or not 1 <= dimensions[0] <= MAX_WALL_SEGMENTS or dtype.kind not in 'fiu'):
            _reject()
    return metadata
