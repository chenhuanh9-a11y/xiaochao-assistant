"""S1 author display protocol. Never provides recognition or geometry authority.

Only separately reviewed, hash-pinned registrations may be activated. Archive
metadata cannot promote its own coordinate claims. S0 remains a separate path.
"""
from __future__ import annotations

import hashlib
import io
import math
import re
from pathlib import Path

PACKAGE_ID = "s1-jiaye-handnotes"
PROFILE_ID = PACKAGE_ID
TITLE = "加页手记 · S1 困难与噩梦"
SCHEMA = "xingtu-s1-author-display-package-v1"
BINDINGS_SCHEMA = "xingtu-s1-author-display-bindings-v1"
SOURCE_VERSION = "2.104.3099014"
BINDINGS_PATH = f"_internal/references/author-packs/{PROFILE_ID}/display-bindings.json"
IMAGE_PREFIX = f"_internal/assets/authors/{PROFILE_ID}/"
MODES = ("difficult", "nightmare")
FLOORS = ("-1", "-2", "-3")
MAX_PIXELS = 16 * 1024 * 1024
# Filled only after the offline source/native registration and independent review.
# This is a coordinate approval, not a publisher signature or provenance claim.
APPROVED_BINDINGS = frozenset({
    'bde70366bbdaec962d14ffbb1b45f3423d5f0dde703eb2068bf28b2d97c8655c',
    # Original JPEGs, exact Windows native bytes, similarity-only registration.
    # Full outlines, held-out features and six-worst visual overlays reviewed.
    '8e5a11d41c2bdd5c4fdc55a19089e1005e8fd47434bde75affa40f8928ac4b9d',
})


def _api():
    import star_map_import
    return star_map_import


def _reject(reason):
    raise _api().ImportRejected(reason)


def layouts(mode):
    from s1_native_resources import MODE_LAYOUTS
    return MODE_LAYOUTS[mode] if mode in MODES else frozenset()


def image_path(mode, map_id, floor):
    return f"{IMAGE_PREFIX}{mode}/{map_id}/B{abs(int(floor))}.jpeg"


def allowed(path):
    if path == BINDINGS_PATH:
        return True
    for mode in MODES:
        for mid in layouts(mode):
            if path in {image_path(mode, mid, floor) for floor in FLOORS}:
                return True
    return False


def dependency_path(path):
    """Narrow season-specific read dependencies, never writable payload roots."""
    match = re.fullmatch(r"_internal/seasons/s1/(difficult|nightmare)/(.+)", path)
    if not match:
        return False
    mode, relative = match.groups()
    if relative in {"native-map-manifest.json", "references/shared-structure-index.json",
                    "references/coordinate-pack.json", "references/structure-version-bindings.json"}:
        return True
    for mid in layouts(mode):
        for floor in FLOORS:
            if relative in {f"references/entries/{mid}-{floor}-shared.npz",
                            f"assets/game-native/{mid}/{floor}.png"}:
                return True
    return False


def required_dependencies():
    paths = set()
    for mode in MODES:
        prefix = f"_internal/seasons/s1/{mode}/"
        paths.update(prefix + p for p in ("native-map-manifest.json", "references/shared-structure-index.json",
            "references/coordinate-pack.json", "references/structure-version-bindings.json"))
        for mid in layouts(mode):
            for floor in FLOORS:
                paths.update((prefix + f"references/entries/{mid}-{floor}-shared.npz",
                              prefix + f"assets/game-native/{mid}/{floor}.png"))
    return paths


def dependency_rows(rows):
    result = _api()._rows(rows)
    if ({r['path'] for r in result.values()} != required_dependencies()
            or any(not dependency_path(r['path']) for r in result.values())):
        _reject("s1-author-dependency-scope-mismatch")
    return result


def receipt_rows(receipt):
    api = _api()
    if receipt.get('season') != 's1' or receipt.get('schema') != SCHEMA:
        _reject('s1-author-receipt-context-mismatch')
    rows = api._rows(receipt.get('files'))
    if not all(allowed(r['path']) for r in rows.values()):
        _reject('s1-author-receipt-file-scope-mismatch')
    expected = {BINDINGS_PATH} | {image_path(mode,mid,floor)
        for mode in MODES for mid in layouts(mode) for floor in FLOORS}
    if {r['path'] for r in rows.values()} != expected:
        _reject('s1-author-receipt-incomplete')
    return rows, dependency_rows(receipt.get('requiresFiles'))


def _shape(value):
    return (isinstance(value, list) and len(value) == 2
            and all(type(x) is int and 0 < x <= 8192 for x in value)
            and value[0] * value[1] <= MAX_PIXELS)


def _matrix(value):
    if (not isinstance(value, list) or len(value) != 3
            or any(not isinstance(row, list) or len(row) != 3 for row in value)
            or any(type(n) not in (int, float) or not math.isfinite(n) or abs(n) > 100000
                   for row in value for n in row) or value[2] != [0, 0, 1]):
        _reject('s1-author-invalid-display-transform')
    a,b,c,d = value[0][0],value[0][1],value[1][0],value[1][1]
    if (not 1e-4 <= a*d-b*c <= 1e4 or abs(a-d)>1e-8 or abs(b+c)>1e-8):
        _reject('s1-author-invalid-display-transform')


def _image(data, shape):
    from PIL import Image, UnidentifiedImageError
    try:
        with Image.open(io.BytesIO(data)) as image:
            if image.format != 'JPEG' or list(reversed(image.size)) != shape or not _shape(shape):
                _reject('s1-author-image-canvas-mismatch')
            image.load()
            if image.mode not in ('RGB', 'L'):
                _reject('s1-author-image-pixel-format')
    except (OSError, ValueError, UnidentifiedImageError, Image.DecompressionBombError) as error:
        _reject('s1-author-invalid-image')


def validate_bindings(raw, rows, dependencies, read):
    """Check same bytes for image decode, registration, and current native inputs."""
    api = _api()
    digest = hashlib.sha256(raw).hexdigest()
    if digest not in APPROVED_BINDINGS:
        _reject('s1-author-registration-not-reviewed')
    doc = api._json(raw)
    if (doc.get('schema') != BINDINGS_SCHEMA or doc.get('season') != 's1'
            or doc.get('sourceVersion') != SOURCE_VERSION or doc.get('profileId') != PROFILE_ID
            or doc.get('identityAuthority') is not False or doc.get('structureValidationAuthority') is not False
            or doc.get('crossFloorProjectionAllowed') is not False
            or doc.get('currentFrameGeometryFitRequired') is not True):
        _reject('s1-author-binding-context-mismatch')
    expected = {(mode,mid,floor) for mode in MODES for mid in layouts(mode) for floor in FLOORS}
    missing = doc.get('missingAuthorLayers')
    if (not isinstance(missing,list) or len(missing)!=29 or
            {(r.get('mode'),r.get('mapId'),r.get('floor'),r.get('fallback')) for r in missing}
            != {(mode,mid,'-4','canonical-native-if-available') for mode in MODES for mid in layouts(mode)}):
        _reject('s1-author-missing-floor-contract')
    entries = doc.get('layers')
    if not isinstance(entries,list) or len(entries)!=len(expected):
        _reject('s1-author-layer-coverage-mismatch')
    native = {}
    for mode in MODES:
        prefix = f'_internal/seasons/s1/{mode}/'
        manifest = api._json(read(prefix+'native-map-manifest.json'))
        index = api._json(read(prefix+'references/shared-structure-index.json'))
        coordinates = api._json(read(prefix+'references/coordinate-pack.json'))
        if (manifest.get('season')!='s1' or manifest.get('mode')!=mode
                or manifest.get('sourceVersion')!=SOURCE_VERSION):
            _reject('s1-author-native-context-mismatch')
        native[mode] = (prefix, {r['path']:r for r in manifest['files']},
            {(r['mapId'],r['floor']):r for r in index['entries']}, coordinates['maps'])
    found=set()
    for row in entries:
        key=(row.get('mode'),row.get('mapId'),row.get('floor'))
        if key not in expected or key in found or row.get('season')!='s1':
            _reject('s1-author-duplicate-or-foreign-layer')
        found.add(key)
        mode,mid,floor=key
        path=image_path(*key)
        inv=rows.get(path.casefold())
        if (row.get('displayPath')!=path or inv is None
                or row.get('displaySha256')!=inv['sha256'] or row.get('displayBytes')!=inv['bytes']
                or not _shape(row.get('displayShapeHW')) or not _shape(row.get('referenceShapeHW'))):
            _reject('s1-author-display-inventory-mismatch')
        prefix, inventory, index, coordinates = native[mode]
        entry=index.get((mid,floor),{})
        reference=f'references/entries/{mid}-{floor}-shared.npz'
        image=f'assets/game-native/{mid}/{floor}.png'
        canvas=coordinates[mid]['floors'][floor]
        for rel, field in ((reference,'referenceSha256'),(image,'nativeImageSha256')):
            dep=dependencies.get((prefix+rel).casefold())
            native_row=inventory.get(rel)
            if (not dep or not native_row or dep['sha256']!=row.get(field)
                    or native_row['sha256']!=dep['sha256'] or native_row['bytes']!=dep['bytes']):
                _reject('s1-author-native-inventory-mismatch')
        if (row.get('referenceResource')!=prefix+reference or row.get('nativeImageResource')!=prefix+image
                or entry.get('resourcePath')!=reference or entry.get('profile')!='shared-structure'
                or row.get('sourceVersion')!=SOURCE_VERSION or row.get('baseSnapshot')!=entry.get('baseSnapshot')
                or row.get('structureVersionKey')!=entry.get('structureVersionKey')
                or row.get('coordinateSpace')!=f'game-native-pixels:{mid}:{floor}'
                or canvas.get('coordinateSpace')!=row['coordinateSpace']
                or canvas.get('referenceResource')!=reference
                or [canvas.get('coordinateHeight'),canvas.get('coordinateWidth')]!=row['referenceShapeHW']):
            _reject('s1-author-reference-binding-mismatch')
        _matrix(row.get('matrixDisplayToReference'))
        reg=row.get('registration',{})
        if (reg.get('status')!='offline-reviewed' or reg.get('method')!='affine-features-full-outline-review-v1'
                or type(reg.get('outlineIoU')) not in (int,float) or not .98<=reg['outlineIoU']<=1
                or type(reg.get('inliers')) is not int or reg['inliers']<12
                or not isinstance(reg.get('evidenceSha256'),str)
                or not re.fullmatch('[0-9a-f]{64}',reg['evidenceSha256'])):
            _reject('s1-author-registration-evidence-missing')
        _image(read(path),row['displayShapeHW'])
    if found!=expected:
        _reject('s1-author-layer-coverage-mismatch')
    return doc


def validate_archive(metadata, archive, root, rows, dependencies):
    api=_api()
    if (metadata.get('schema')!=SCHEMA or metadata.get('season')!='s1'
            or metadata.get('packageId')!=PACKAGE_ID or metadata.get('sourceVersion')!=SOURCE_VERSION):
        _reject('s1-author-package-context-mismatch')
    receipt_rows(metadata)
    # Reuse the archive's normalized, inventory-checked data names.
    def read(path):
        row=rows.get(path.casefold()) or dependencies.get(path.casefold())
        if row is None:
            _reject('s1-author-undeclared-file')
        if path.casefold() in rows:
            data=archive.read('data/'+path)
        else:
            target=api._target(root,path)
            with target.open('rb') as stream:data=stream.read(row['bytes']+1)
        if len(data)!=row['bytes'] or hashlib.sha256(data).hexdigest()!=row['sha256']:
            _reject('s1-author-file-changed')
        return data
    return validate_bindings(read(BINDINGS_PATH),rows,dependencies,read)
