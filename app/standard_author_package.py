"""Data-only author packages. Identity and structure always belong to the base pack."""
from __future__ import annotations

import io
import math
import re

from PIL import Image


def standard_author_mode(package_id):
    if not isinstance(package_id, str) or len(package_id) > 64:
        return None
    match = re.fullmatch(r"author-(nightmare|difficult)-[a-z0-9]+(?:-[a-z0-9]+)*", package_id)
    return match[1] if match else None


def standard_author_descriptor(metadata):
    """The same declaration is retained in the installed receipt and upgrade backup."""
    mode = standard_author_mode(metadata.get('packageId'))
    declaration = metadata.get('standardAuthor')
    if (type(metadata.get('schemaVersion')) is not int or metadata.get('schemaVersion') != 2
            or metadata.get('packageType') != 'author-display'
            or mode is None or not isinstance(declaration, dict)
            or set(declaration) != {'profileId', 'mode', 'displayName', 'authorName',
                                    'routeFamilyName', 'packageVersion'}
            or declaration.get('profileId') != metadata['packageId'] or declaration.get('mode') != mode):
        raise ValueError('invalid-standard-author-declaration')
    for key in ('displayName', 'authorName', 'routeFamilyName', 'packageVersion'):
        value = declaration[key]
        if (not isinstance(value, str) or not value.strip() or len(value) > 128
                or any(ord(char) < 32 for char in value)):
            raise ValueError('invalid-standard-author-' + key)
    return dict(declaration)


def standard_image_roots(package_id):
    return (f'_internal/assets/authors/{package_id}/',) if standard_author_mode(package_id) else ()


def _finite_number(value):
    if type(value) not in (int, float):
        return False
    try:
        return math.isfinite(value)
    except OverflowError:
        return False


def _matrix(value):
    if (not isinstance(value, list) or len(value) != 3
            or any(not isinstance(row, list) or len(row) != 3 for row in value)
            or any(not _finite_number(number)
                   for row in value for number in row)
            or value[2] != [0, 0, 1]
            or abs(value[0][0] * value[1][1] - value[0][1] * value[1][0]) <= 1e-12):
        raise ValueError('invalid-standard-author-matrix')
    if not _finite_number(value[0][0] * value[1][1] - value[0][1] * value[1][0]):
        raise ValueError('invalid-standard-author-matrix')


def validate_standard_author_payload(declaration, read_json, read_bytes, read_base_json, paths):
    """Validate before publication, including per-floor base identity and PNG decoding.

    This checks the prepared mapping, not whether an author's manual alignment is
    visually accurate. Runtime still requires a fit to the current game frame.
    """
    profile = declaration['profileId']
    folder = f'_internal/references/author-packs/{profile}/'
    catalog = read_json(folder + 'catalog.json')
    bindings = read_json(folder + 'display-bindings.json')
    profiles = catalog.get('profiles')
    if (catalog.get('schemaVersion') != 1 or not isinstance(profiles, list) or len(profiles) != 1
            or not isinstance(profiles[0], dict)):
        raise ValueError('invalid-standard-author-catalog')
    row = profiles[0]
    if (row.get('id') != profile
            or any(row.get(key) != declaration[key] for key in ('displayName', 'authorName', 'routeFamilyName'))
            or any(row.get(key) is not True for key in
                   ('runtimeEnabled', 'settingsSelectable', 'alignedOverlayEligible'))):
        raise ValueError('standard-author-catalog-declaration-mismatch')
    mode = declaration['mode']
    coordinate_file = ('difficult-native-coordinate-pack.json' if mode == 'difficult'
                       else 'nightmare-canonical-coordinate-pack.json')
    version_file = ('difficult-native-version-bindings.json' if mode == 'difficult'
                    else 'nightmare-structure-version-bindings.json')
    coordinate = read_base_json('_internal/references/' + coordinate_file)
    if (bindings.get('schema') != 'canonical-author-display-bindings-v1'
            or bindings.get('profileId') != profile
            or bindings.get('canonicalPackId') != coordinate.get('canonicalPackId')
            or bindings.get('coordinateReferenceProfileId') != coordinate.get('coordinateReferenceProfileId')):
        raise ValueError('standard-author-coordinate-context-mismatch')
    bases = {}
    for base in read_base_json('_internal/references/' + version_file)['entries']:
        resource = base['resourceKey']
        key = (base['mapId'], resource['floor'])
        if key in bases:
            raise ValueError('duplicate-standard-author-base')
        bases[key] = base
    maps = row.get('maps')
    if not isinstance(maps, dict) or not maps:
        raise ValueError('empty-standard-author-maps')
    layers = {}
    images = set()
    for map_id, variants in maps.items():
        # One canonical display per author/base/floor. Alternate originals stay outside the package.
        if not isinstance(variants, list) or len(variants) != 1 or not isinstance(variants[0], dict):
            raise ValueError('standard-author-requires-one-canonical-variant')
        variant = variants[0]
        if (not isinstance(variant.get('id'), str) or not variant['id']
                or not isinstance(variant.get('displayName'), str)
                or variant.get('resourceBase', 'asset-root') != 'asset-root'
                or variant.get('identityStatus') != 'display-only'
                or variant.get('displayPolicy') != 'requires-current-shared-structure-fit'
                or not isinstance(variant.get('layers'), dict) or not variant['layers']):
            raise ValueError('invalid-standard-author-variant')
        for floor, asset in variant['layers'].items():
            key = (map_id, floor)
            if key not in bases or not isinstance(asset, str):
                raise ValueError('unknown-standard-author-map-floor')
            path = '_internal/' + asset
            if path not in paths or not path.startswith(standard_image_roots(profile)):
                raise ValueError('standard-author-asset-outside-package')
            layers[key] = asset
            images.add(path)
    entries = bindings.get('entries')
    if not isinstance(entries, list) or not entries:
        raise ValueError('empty-standard-author-bindings')
    seen = set()
    for entry in entries:
        if not isinstance(entry, dict):
            raise ValueError('invalid-standard-author-binding')
        key = (entry.get('mapId'), entry.get('floor'))
        if key in seen or key not in layers:
            raise ValueError('duplicate-or-unknown-standard-author-binding')
        seen.add(key)
        base = bases[key]
        resource = base['resourceKey']
        version_key = f"{resource['layoutId']}@{resource['structureRawRecordSha256']}/{key[1]}"
        bridge = entry.get('coordinateBridge') or {}
        registration = entry.get('registration')
        if not isinstance(bridge, dict) or not isinstance(registration, dict):
            raise ValueError('invalid-standard-author-binding-policy')
        if (entry.get('profileId') != profile or entry.get('assetPath') != layers[key]
                or entry.get('nativeResourceKey') != resource
                or entry.get('structureVersionKey') != version_key
                or entry.get('sourceVersion', bindings.get('sourceVersion')) != base['sourceVersion']
                or entry.get('identityAuthority') is not False
                or entry.get('structureValidationAuthority') is not False
                or entry.get('currentFrameGeometryFitRequired') is not True
                or entry.get('crossFloorProjectionAllowed') is not False
                or bridge.get('baseToNativeIsIdentity') is not True
                or bridge.get('authorGeometryErrorUnchanged') is not True
                or registration.get('status') != 'confirmed'):
            raise ValueError('standard-author-base-or-policy-mismatch')
        matrix = entry.get('matrixAssetToCanonical')
        _matrix(matrix)
        if matrix != entry.get('matrixAssetToBase'):
            raise ValueError('standard-author-coordinate-matrix-mismatch')
    if seen != set(layers) or paths != images | {folder + name for name in ('catalog.json', 'display-bindings.json')}:
        raise ValueError('standard-author-inventory-mismatch')
    for path in images:
        with Image.open(io.BytesIO(read_bytes(path))) as image:
            if image.format != 'PNG' or image.mode != 'RGBA' or image.width * image.height > 64_000_000:
                raise ValueError('standard-author-requires-bounded-rgba-png')
            image.load()
