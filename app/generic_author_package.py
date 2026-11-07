"""User-supplied display data, never recognition/structure authority.

V2 validates integrity, namespaces and installed coordinate contracts. It does
not assert that the publisher or their geometric registration was reviewed.
Legacy packages retain their separate, stricter original contracts.
"""
from __future__ import annotations

import hashlib
import io
import math
import re
from pathlib import Path

SCHEMA = 'xingtu-author-display-package-v2'
BINDINGS_SCHEMA = 'xingtu-author-display-bindings-v2'
MAX_LAYERS = 1000


def _api():
    import star_map_import
    return star_map_import


def _reject(reason):
    raise _api().ImportRejected('author-v2-' + reason)


def is_generic_profile(value):
    return isinstance(value, str) and re.fullmatch(r'author-[a-z0-9](?:[a-z0-9-]{0,46}[a-z0-9])?', value) is not None


def bindings_path(profile):
    if not is_generic_profile(profile):
        _reject('invalid-profile')
    return f'_internal/references/author-packs/{profile}/display-bindings.json'


def valid_season(value):
    return isinstance(value, str) and re.fullmatch(r's(?:0|[1-9][0-9]{0,3})', value) is not None


def _component(value):
    return isinstance(value, str) and re.fullmatch(r'[a-zA-Z0-9][a-zA-Z0-9_-]{0,79}', value) is not None


def image_path(profile, season, mode, map_id, floor, extension='png'):
    if (not is_generic_profile(profile) or not valid_season(season) or not _component(mode)
            or not _component(map_id) or not isinstance(floor, str)
            or not re.fullmatch(r'-?[a-zA-Z0-9][a-zA-Z0-9_-]{0,15}', floor) or extension not in ('png', 'jpeg')):
        _reject('invalid-image-identity')
    return f'_internal/assets/authors/{profile}/{season}/{mode}/{map_id}/{floor}.{extension}'


def allowed(path, package_id):
    if not is_generic_profile(package_id) or not isinstance(path, str):
        return False
    if path == bindings_path(package_id):
        return True
    prefix = f'_internal/assets/authors/{package_id}/'
    if not path.startswith(prefix):
        return False
    pieces = path[len(prefix):].split('/')
    if len(pieces) != 4 or '.' not in pieces[3]:
        return False
    floor, extension = pieces[3].rsplit('.', 1)
    try:
        return path == image_path(package_id, *pieces[:3], floor, extension)
    except ValueError:
        return False


def dependency_rows(values, season):
    if not valid_season(season):
        _reject('invalid-season')
    rows = _api()._rows(values)
    # The authoritative resolver later requires the exact dependency closure.
    # This preliminary scope cannot read credentials, code, work data or authors.
    for row in rows.values():
        path = row['path']
        if season == 's0':
            accepted = (path.startswith('_internal/references/') or path.startswith('_internal/assets/')
                        or path == '_internal/difficult-native-manifest.json')
        else:
            accepted = path.startswith(f'_internal/seasons/{season}/')
        if (not accepted or '/author-packs/' in path or '/authors/' in path
                or Path(path).suffix not in ('.json', '.npz', '.png', '.webp', '.jpeg')):
            _reject('dependency-scope-mismatch')
    return rows


def receipt_rows(receipt):
    profile = receipt.get('packageId')
    if (receipt.get('schema') != SCHEMA or not is_generic_profile(profile)
            or type(receipt.get('revision')) is not int or receipt['revision'] != 1):
        _reject('receipt-context-mismatch')
    rows = _api()._rows(receipt.get('files'))
    if (bindings_path(profile).casefold() not in rows or len(rows) < 2
            or any(not allowed(row['path'], profile) for row in rows.values())):
        _reject('receipt-file-scope-mismatch')
    prefix = f'_internal/assets/authors/{profile}/{receipt.get("season")}/'
    if any(row['path'] != bindings_path(profile) and not row['path'].startswith(prefix) for row in rows.values()):
        _reject('receipt-season-mismatch')
    return rows, dependency_rows(receipt.get('requiresFiles'), receipt.get('season'))


def validate_receipt_closure(root, receipt, rows, dependencies):
    """Never let a shortened receipt erase another package's constraints.

    Derive layer identities from owned namespace paths, independently of the
    mutable display binding/pixels. Modified images remain removable.
    """
    from generic_author_base import resolve_layer, check_browse_cancelled
    prefix = f'_internal/assets/authors/{receipt["packageId"]}/{receipt["season"]}/'
    required = set()
    for row in rows.values():
        check_browse_cancelled()
        if row['path'] == bindings_path(receipt['packageId']):
            continue
        mode, map_id, filename = row['path'][len(prefix):].split('/')
        floor = filename.rsplit('.', 1)[0]
        try:
            base = resolve_layer(root, receipt['season'], mode, map_id, floor)
        except ValueError as error:
            _reject('receipt-base-unavailable')
        required.update(base['dependencies'])
    if required != {row['path'] for row in dependencies.values()}:
        _reject('receipt-dependency-closure-mismatch')


def profile_ids(resource_root, *, strict=False):
    """Receipt-name discovery only; every consumer must validate its bytes."""
    api = _api()
    directory = api._target(Path(resource_root).parent, 'work/map-packages')
    if not directory.is_dir():
        return ()
    found = []
    for path in sorted(directory.iterdir()):
        profile = path.stem.casefold()
        if path.suffix.casefold() == '.json' and is_generic_profile(profile):
            try:
                api._no_links(path)
                if path.name != f'{profile}.json' and not (directory / f'{profile}.json').exists():
                    # On a case-sensitive filesystem this is a different path;
                    # never silently omit it from mutation constraint discovery.
                    _reject('noncanonical-receipt-name')
                if path.is_file():
                    if profile in found:
                        _reject('duplicate-receipt-name')
                    found.append(profile)
                elif strict:
                    _reject('invalid-receipt-file')
            except (OSError, ValueError):
                if strict:
                    raise
                # Runtime discovery cannot grant an invalid optional display.
                continue
    return tuple(found)


def _label(value):
    # Captions reach Qt labels and tooltips using AutoText. Names are plain
    # metadata, never a markup/image-loading channel.
    return (isinstance(value, str) and bool(value.strip()) and len(value) <= 120
            and not any(ord(c) < 32 or c in '<>' for c in value))


def validate_display_affine(value):
    """Bound display-only affine bridges without imposing a recognition model.

    Legacy S1 approvals remain similarity-only. V2 also preserves inherited S0
    anisotropic scale/shear. The former similarity determinant bounds imply
    principal scales 0.01..100; apply that same range to BOTH affine axes so an
    area-preserving but numerically near-singular shear cannot pass.
    """
    import numpy as np
    if (not isinstance(value, list) or len(value) != 3
            or any(not isinstance(row, list) or len(row) != 3 for row in value)
            or any(type(n) not in (int, float) or abs(n) > 100000 or not math.isfinite(n)
                   for row in value for n in row) or value[2] != [0, 0, 1]):
        _reject('invalid-display-affine')
    linear = np.asarray(value, dtype=np.float64)[:2, :2]
    scales = np.linalg.svd(linear, compute_uv=False)
    if (np.linalg.det(linear) <= 0 or scales[-1] < .01 - 1e-12
            or scales[0] > 100 + 1e-10):
        _reject('invalid-display-affine')


def validate_bindings(raw, rows, dependencies, read, *, root, dependency_paths=None):
    from generic_author_base import resolve_layer, check_browse_cancelled
    from s1_author_package import _shape
    from PIL import Image, UnidentifiedImageError
    api = _api()
    doc = api._json(raw)
    profile, season = doc.get('profileId'), doc.get('season')
    if (doc.get('schema') != BINDINGS_SCHEMA or not is_generic_profile(profile) or not valid_season(season)
            or not _label(doc.get('authorName')) or not _label(doc.get('displayName'))
            or doc.get('identityAuthority') is not False or doc.get('structureValidationAuthority') is not False
            or doc.get('crossFloorProjectionAllowed') is not False
            or doc.get('currentFrameGeometryFitRequired') is not True):
        _reject('binding-context-mismatch')
    layers = doc.get('layers')
    if not isinstance(layers, list) or not 1 <= len(layers) <= MAX_LAYERS:
        _reject('invalid-layers')
    expected = {bindings_path(profile)}
    required = set()
    seen = set()
    for row in layers:
        check_browse_cancelled()
        if not isinstance(row, dict):
            _reject('invalid-layer')
        key = (row.get('mode'), row.get('mapId'), row.get('floor'))
        if any(not isinstance(value, str) for value in key) or key in seen or row.get('season') != season:
            _reject('duplicate-or-foreign-layer')
        seen.add(key)
        path = row.get('displayPath')
        if not isinstance(path, str):
            _reject('invalid-image-path')
        extension = path.rsplit('.', 1)[-1]
        if path != image_path(profile, season, *key, extension):
            _reject('display-namespace-mismatch')
        expected.add(path)
        inv = rows.get(path.casefold())
        if (inv is None or row.get('displaySha256') != inv['sha256'] or row.get('displayBytes') != inv['bytes']
                or not _shape(row.get('displayShapeHW')) or not _shape(row.get('referenceShapeHW'))):
            _reject('display-inventory-mismatch')
        try:
            base = resolve_layer(Path(root), season, *key)
        except ValueError as error:
            _reject('base-unavailable:' + str(error))
        for field in ('referenceResource', 'referenceShapeHW', 'coordinateSpace',
                      'structureVersionKey', 'baseSnapshot', 'sourceVersion'):
            if row.get(field) != base[field]:
                _reject('reference-binding-mismatch:' + field)
        required.update(base['dependencies'])
        if dependency_paths is not None:
            dependency_paths[key] = tuple(base['dependencies'])
        dep = dependencies.get(base['referenceResource'].casefold())
        if dep is None or row.get('referenceSha256') != dep['sha256']:
            _reject('reference-inventory-mismatch')
        if base.get('nativeImageResource'):
            native = dependencies.get(base['nativeImageResource'].casefold())
            if (row.get('nativeImageResource') != base['nativeImageResource'] or native is None
                    or row.get('nativeImageSha256') != native['sha256']):
                _reject('native-inventory-mismatch')
        validate_display_affine(row.get('matrixDisplayToReference'))
        data = read(path)
        check_browse_cancelled()
        try:
            with Image.open(io.BytesIO(data)) as image:
                if (image.format != {'png': 'PNG', 'jpeg': 'JPEG'}[extension]
                        or list(reversed(image.size)) != row['displayShapeHW']
                        or image.mode not in (('RGB', 'RGBA') if extension == 'png' else ('RGB', 'L'))
                        or getattr(image, 'n_frames', 1) != 1):
                    _reject('image-format-mismatch')
                image.load()
                check_browse_cancelled()
        except (OSError, ValueError, UnidentifiedImageError, Image.DecompressionBombError) as error:
            _reject('invalid-image')
    if expected != {row['path'] for row in rows.values()}:
        _reject('unlisted-or-missing-display-file')
    if required != {row['path'] for row in dependencies.values()}:
        _reject('dependency-closure-mismatch')
    # Re-check all dependency bytes at use time, not only at initial import.
    for path in required:
        check_browse_cancelled()
        read(path)
    return doc


def validate_archive(metadata, archive, root, rows, dependencies):
    api = _api()
    receipt_rows(metadata)
    def read(path):
        row = rows.get(path.casefold()) or dependencies.get(path.casefold())
        if row is None:
            _reject('undeclared-file')
        if path.casefold() in rows:
            data = archive.read('data/' + path)
        else:
            with api._target(root, path).open('rb') as stream:
                data = stream.read(row['bytes'] + 1)
        if len(data) != row['bytes'] or hashlib.sha256(data).hexdigest() != row['sha256']:
            _reject('file-changed')
        return data
    doc = validate_bindings(read(bindings_path(metadata['packageId'])), rows, dependencies, read, root=root)
    if doc['profileId'] != metadata['packageId'] or doc['season'] != metadata['season']:
        _reject('package-binding-mismatch')
    return doc
