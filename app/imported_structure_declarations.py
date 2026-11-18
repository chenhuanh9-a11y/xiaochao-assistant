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


def _field_header(headers, name):
    if not isinstance(name, str):
        return None
    # NpzFile resolves an exact raw member first, then name + '.npy'. Keep
    # preflight aligned with that rule so aliases cannot hide a larger member.
    return headers.get(name if name in headers else name + '.npy')


def validate_structure_declarations(raw, *, expected_binding=None, expected_shape=None):
    headers = validate_npz_bytes(raw)
    def field_header(name):
        return _field_header(headers, name)

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


def validate_legacy_structure_declarations(raw, *, expected_shape=None):
    """Bound metadata-free legacy author arrays before the legacy decoder.

    Historical author products use fixed member names rather than a typed
    ``metadata.roles`` declaration. This checks only bounded NPY headers; the
    existing decoder remains responsible for binary values and wall contents.
    """
    headers = validate_npz_bytes(raw)
    required = _field_header(headers, 'validation_mask')
    if required is None:
        _reject()
    shape, dtype = required
    if (dtype != np.dtype('uint8') or len(shape) != 2
            or any(not 1 <= axis <= MAX_CANVAS_AXIS for axis in shape)
            or shape[0] * shape[1] > MAX_CANVAS_PIXELS):
        _reject()
    if expected_shape is not None and shape != tuple(expected_shape):
        _reject()

    clean_fields = ('clean_mask', 'clean_edge_5px', 'clean_distance')
    clean_headers = [_field_header(headers, name) for name in clean_fields]
    present = [header is not None for header in clean_headers]
    if any(present):
        if not all(present):
            _reject()
        for index, (dimensions, field_dtype) in enumerate(clean_headers):
            if dimensions != shape:
                _reject()
            if index < 2 and field_dtype != np.dtype('uint8'):
                _reject()
            if index == 2 and field_dtype.kind != 'f':
                _reject()

    known = _field_header(headers, 'known_boundary')
    unknown = _field_header(headers, 'unknown_mask')
    if (known is None) != (unknown is None):
        _reject()
    for declaration in (known, unknown):
        if declaration is not None and (declaration[0] != shape
                                         or declaration[1] != np.dtype('uint8')):
            _reject()

    walls = _field_header(headers, 'native_wall_segments')
    if walls is not None:
        dimensions, wall_dtype = walls
        if (len(dimensions) != 2 or dimensions[1] != 6
                or not 1 <= dimensions[0] <= MAX_WALL_SEGMENTS
                or wall_dtype.kind not in 'fiu'):
            _reject()
    return shape
