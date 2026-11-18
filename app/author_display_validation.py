"""Shared display-data checks, independent of installation and base discovery."""
import io
import math


MAX_IMAGE_PIXELS = 16 * 1024 * 1024
REFERENCE_FIELDS = ('referenceResource', 'referenceShapeHW', 'coordinateSpace',
                    'structureVersionKey', 'baseSnapshot', 'sourceVersion')


def valid_display_shape(value):
    return (isinstance(value, list) and len(value) == 2
            and all(type(axis) is int and 0 < axis <= 8192 for axis in value)
            and value[0] * value[1] <= MAX_IMAGE_PIXELS)


def validate_display_authority(document, *, reject):
    if (document.get('identityAuthority') is not False
            or document.get('structureValidationAuthority') is not False
            or document.get('crossFloorProjectionAllowed') is not False
            or document.get('currentFrameGeometryFitRequired') is not True):
        reject('binding-context-mismatch')


def validate_display_affine(value, *, reject):
    """Preserve V2's orientation and principal-scale bounds for full affines."""
    import numpy as np
    if (not isinstance(value, list) or len(value) != 3
            or any(not isinstance(row, list) or len(row) != 3 for row in value)
            or any(type(number) not in (int, float) or abs(number) > 100000
                   or not math.isfinite(number) for row in value for number in row)
            or value[2] != [0, 0, 1]):
        reject('invalid-display-affine')
    linear = np.asarray(value, dtype=np.float64)[:2, :2]
    scales = np.linalg.svd(linear, compute_uv=False)
    if (np.linalg.det(linear) <= 0 or scales[-1] < .01 - 1e-12 or scales[0] > 100 + 1e-10):
        reject('invalid-display-affine')


def validate_display_image(data, extension, shape, *, reject, decode_images=True, check_cancelled=None):
    """Validate stored pixels without cropping, scaling or inferring geometry."""
    from PIL import Image
    if not valid_display_shape(shape) or extension not in ('png', 'jpeg'):
        reject('image-format-mismatch')
    # Pillow exposes 16-bit RGB/RGBA PNG as RGB/RGBA. The consumer would decode
    # uint16, so check the stored depth before any full pixel allocation.
    if extension == 'png' and (len(data) < 29 or data[24] != 8):
        reject('image-bit-depth-mismatch')
    with Image.open(io.BytesIO(data)) as image:
        if (image.format != {'png': 'PNG', 'jpeg': 'JPEG'}[extension]
                or list(reversed(image.size)) != shape
                or image.mode not in (('RGB', 'RGBA') if extension == 'png' else ('RGB', 'L'))
                or getattr(image, 'n_frames', 1) != 1):
            reject('image-format-mismatch')
        if decode_images:
            image.load()
        if check_cancelled is not None:
            check_cancelled()


def validate_display_reference(row, expected, *, reject):
    for field in REFERENCE_FIELDS:
        if row.get(field) != expected[field]:
            reject('reference-binding-mismatch:' + field)
