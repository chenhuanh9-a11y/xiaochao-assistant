"""Semantic admission of the S0 nightmare native recognition base.

Only the declared component tree is read. This module decodes resources, not
candidate/session policy, catalog activation, author displays or personal data.
"""
from __future__ import annotations

import base64
from io import BytesIO
import math
from pathlib import Path
import posixpath
import re
from types import MappingProxyType
import zipfile
import zlib

import numpy as np

from difficult_entry_identity import MapEntryGeometryIdentity
from difficult_native_identity import NativeDifficultPack, _validate_imported_identity_ids
from difficult_side_entry_identity import MapLocalEntryIdentity
from imported_array_archive import MAX_MEMBER_BYTES, validate_npz_bytes
from imported_resource_packages import PackageView
from imported_resource_reader import ComponentResourceReader, decode_resource_json
from imported_structure_declarations import (MAX_CANVAS_AXIS, MAX_CANVAS_PIXELS,
    MAX_METADATA_BYTES, validate_structure_declarations)
from nightmare_author_coordinate_adapter import AuthorCoordinateAdapters, CANONICAL_SCHEMA
from shared_structure_context import load_shared_version_context
from shared_structure_reference import SharedStructureResourceStore
from side_door_recognition import SideDoorRecognizer
from side_entry_search import prepare_arrays
from side_entry_geometry import _bounded_list, _bounded_points
import star_map_import as files


SCHEMA = 'xingtu-native-recognition-base-v1'
FLOORS = frozenset(('1', '2', 'B1'))
PROFILE = 'zhan10-speed'
_INTERFACES = frozenset(('identityPack', 'typedIndex', 'coordinatePack', 'neutralDisplay',
                       'identityEntries', 'exitIcon', 'identityProfile'))
_SEARCH_KEY = re.compile(r'[A-Za-z0-9][A-Za-z0-9_-]{0,63}\Z')


class NativeBaseUnavailable(ValueError):
    unavailable = True

    def __init__(self, reason):
        super().__init__(reason)
        self.code = reason
        self.resource_gaps = (reason,)


def _reject(reason):
    code = 'imported-native-base-' + reason
    raise files.ImportRejected(code, code=code)


class NativeBaseBundle:
    """Validated declarations; every cached access checks the owning view.

    Consumers retaining a returned object must call assert_current before each
    operation/cache hit. Payload reads additionally retain PackageView checks.
    """
    def __init__(self, view, **values):
        self._view, self._root = view, view.root
        self._values = values

    def assert_current(self):
        if self._view.root != self._root:  # Actual bounded manifest/root load.
            _reject('component-root-changed')

    def _cached(self, name):
        self.assert_current()
        return self._values[name]

    pack = property(lambda self: self._cached('pack'))
    resources = property(lambda self: self._cached('resources'))
    adapters = property(lambda self: self._cached('adapters'))
    versions = property(lambda self: self._cached('versions'))
    interfaces = property(lambda self: self._cached('interfaces'))
    reader = property(lambda self: self._cached('reader'))
    entry_identity = property(lambda self: self._cached('entry_identity'))
    side_entry_identity = property(lambda self: self._cached('side_entry_identity'))
    exit_recognizer = property(lambda self: self._cached('exit_recognizer'))
    season = property(lambda self: self._cached('season'))
    mode = property(lambda self: self._cached('mode'))
    component_ref = property(lambda self: self._cached('component_ref'))
    gaps = property(lambda self: self._cached('gaps'))
    ready = property(lambda self: not self.gaps)


def _number(value, low, high):
    if type(value) not in (int, float):
        _reject('numeric-field-invalid')
    try:
        valid = math.isfinite(value) and low <= value <= high
    except OverflowError:
        valid = False
    if not valid:
        _reject('numeric-field-invalid')
    return value


def _text(value):
    if not isinstance(value, str) or not 1 <= len(value) <= 256 or not value.strip():
        _reject('text-field-invalid')


def _points(value, maximum, width, height, *, exact=None, rays=False):
    rows = _bounded_points(value, maximum, 'native base points')
    if exact is not None and len(rows) != exact:
        _reject('point-shape-invalid')
    for x, y in rows:
        _number(x, -1.000001 if rays else 0, 1.000001 if rays else width)
        _number(y, -1.000001 if rays else 0, 1.000001 if rays else height)
        if rays and not .9 <= math.hypot(x, y) <= 1.1:
            _reject('ray-invalid')
    return rows


def _canvas(value):
    if not isinstance(value, dict):
        _reject('canvas-invalid')
    height, width = value.get('height'), value.get('width')
    if (type(height) is not int or type(width) is not int
            or not 1 <= height <= MAX_CANVAS_AXIS or not 1 <= width <= MAX_CANVAS_AXIS
            or height * width > MAX_CANVAS_PIXELS or value.get('shapeHW') != [height, width]):
        _reject('canvas-invalid')
    return width, height


def _identity_matrix(value):
    # Reject nested/ragged/oversized values before NumPy sees a matrix.
    if not isinstance(value, list) or len(value) != 3:
        _reject('matrix-shape-invalid')
    for i, row in enumerate(value):
        if not isinstance(row, list) or len(row) != 3:
            _reject('matrix-shape-invalid')
        for j, number in enumerate(row):
            _number(number, -MAX_CANVAS_AXIS, MAX_CANVAS_AXIS)
            if number != int(i == j):
                _reject('shared-canvas-matrix-invalid')


def _rows(rows, expected, label):
    _bounded_list(rows, 768, label)
    result = {}
    for row in rows:
        if not isinstance(row, dict):
            _reject(label + '-invalid')
        floor = row.get('floor', row.get('resourceKey', {}).get('floor'))
        key = (row.get('mapId'), floor)
        if not all(isinstance(value, str) for value in key) or key in result:
            _reject(label + '-duplicate-or-invalid-binding')
        result[key] = row
    if set(result) != expected:
        _reject(label + '-inventory-incomplete')
    return result


def _reference(view, relative, extension=None):
    if not isinstance(relative, str) or files._relative(relative) != relative:
        _reject('reference-invalid')
    if extension is not None and Path(relative).suffix.casefold() != extension:
        _reject('reference-type-invalid')
    view.path(relative)  # Inventory ownership, link checks and current manifest.
    return relative


def _entry_reference(view, directory, relative):
    # The old entry schema has an explicit document-directory anchor. Its
    # sourceIndex '../index.json' is resolved to a canonical component FileRef,
    # never accepted by a general filesystem fallback.
    if (not isinstance(relative, str) or not relative or relative.startswith('/')
            or '\\' in relative or ':' in relative or posixpath.normpath(relative) != relative):
        _reject('entry-reference-invalid')
    return _reference(view, posixpath.normpath(directory + '/' + relative))


def _entry_rows(payload, expected, canvases, typed, view, directory, index, *, main):
    if (payload.get('schemaVersion'), payload.get('mode'), payload.get('profileId')) != (1, 'nightmare', PROFILE):
        _reject('entry-context-invalid')
    rows = _rows(payload.get('entries'), expected, 'main-entry' if main else 'local-entry')
    total = 0
    for key, row in rows.items():
        _text(row.get('material') if main else row.get('region'))
        width, height = _canvas(canvases[key])
        if (row.get('canvas') != canvases[key] or row.get('profileId') != PROFILE
                or row.get('variantId') != 'variant-01'
                or _entry_reference(view, directory, row.get('structureResource')) != typed[key]['resourcePath']
                or row.get('sourceProduct') != Path(typed[key]['resourcePath']).name):
            _reject('entry-typed-binding-invalid')
        if 'sourceIndex' in row and _entry_reference(view, directory, row['sourceIndex']) != index:
            _reject('entry-index-binding-invalid')
        if any(name in row and row[name] != typed[key].get(name)
               for name in ('sourceVersion', 'structureVersionKey', 'baseSnapshot')):
            _reject('entry-version-binding-invalid')
        if 'routeResource' in row:
            raise NativeBaseUnavailable('native-base-author-entry-unsupported')
        _number(row.get('unit'), 1e-6, MAX_CANVAS_AXIS)
        if main:
            _points([row.get('center')], 1, width, height, exact=1)
            outline = _points(row.get('outline'), 256, width, height)
            chains = _bounded_list(row.get('chains'), 512, 'main chains')
            if not chains or not outline or len(chains) * len(outline) > 131072:
                _reject('main-entry-geometry-invalid')
            total += len(chains)
            for chain in chains:
                _points(chain.get('points'), 3, width, height, exact=3)
                _points(chain.get('rays'), 2, width, height, exact=2, rays=True)
            room = row.get('entryRoomSource')
            if room is not None:
                if (not isinstance(room, dict) or type(room.get('roomConfigId')) is not int
                        or type(room.get('rotation')) is not int or not 0 <= room['rotation'] <= 3):
                    _reject('main-entry-room-invalid')
        else:
            if 'gates' not in row or not isinstance(row['gates'], list):
                _reject('local-gates-invalid')
            turns = _bounded_list(row.get('turns'), 1024, 'side turns')
            if not turns:
                _reject('local-entry-geometry-invalid')
            total += len(turns)
            for turn in turns:
                _points([turn.get('point')], 1, width, height, exact=1)
                _points(turn.get('rays'), 2, width, height, exact=2, rays=True)
                if type(turn.get('next')) is not int or not -1 <= turn['next'] < len(turns):
                    _reject('local-entry-next-invalid')
        for gate in _bounded_list(row.get('gates', []), 64, 'entry gates'):
            _points([gate.get('center')], 1, width, height, exact=1)
        if total > 65536:
            _reject('entry-complexity')
    return rows


def _search_declarations(raw, local_rows, typed):
    headers = validate_npz_bytes(raw)
    declaration = headers.get('metadata.npy')
    if (declaration is None or declaration[0] != () or declaration[1].kind != 'U'
            or declaration[1].itemsize > MAX_METADATA_BYTES):
        _reject('search-metadata-invalid')
    with np.load(BytesIO(raw), allow_pickle=False) as archive:
        metadata = decode_resource_json(str(archive['metadata'].item()).encode('utf-8'))
    targets = {key for key in local_rows if key[1] == '2'}
    if metadata.get('schemaVersion') != 1:
        _reject('search-schema-invalid')
    records = _rows(metadata.get('entries'), targets, 'search')
    keys = set()
    for binding, record in records.items():
        key = record.get('key')
        if not isinstance(key, str) or not _SEARCH_KEY.fullmatch(key) or key in keys:
            _reject('search-key-invalid')
        keys.add(key)
        row = local_rows[binding]
        if any(record.get(name) != typed[binding].get(name) for name in ('sourceVersion', 'structureVersionKey')):
            _reject('search-version-invalid')
        count = len(row['turns'])
        pairs = sum(turn['next'] >= 0 for turn in row['turns'])
        width, height = _canvas(row['canvas'])
        cell = row['unit'] / 12
        grid = (math.ceil(height / cell) + 1, math.ceil(width / cell) + 1)
        # Rays have already passed the bounded 2x2 numeric check. Count the
        # same nearest-integer orientation groups as prepare_arrays before it
        # allocates distance fields; a forged small 'lower' axis must not hide
        # a much larger actual rebuild. No heavy array is created here.
        orientations = len({tuple(round(value) for ray in turn['rays'] for value in ray)
                            for turn in row['turns']})
        if (grid[0] * grid[1] > MAX_CANVAS_PIXELS
                or orientations * grid[0] * grid[1] > MAX_MEMBER_BYTES):
            _reject('search-grid-invalid')
        lower = headers.get(key + '_lower.npy')
        if (lower is None or lower[1] != np.dtype('uint8') or len(lower[0]) != 3
                or lower[0][0] != orientations or lower[0][1:] != grid):
            _reject('search-grid-invalid')
        shapes = dict(points=(count, 2), rays=(count, 2, 2), pairs=(pairs, 2), cell=(),
            first=(lower[0][0],), groups=(count,), deltas=(pairs, 2), denoms=(pairs,), radius=(pairs,))
        for name, shape in shapes.items():
            header = headers.get(key + '_' + name + '.npy')
            kinds = 'iu' if name in ('pairs', 'first', 'groups') else 'f'
            if header is None or header[0] != shape or header[1].kind not in kinds:
                _reject('search-field-invalid')


def _decode(view, season, mode):
    if (season, mode) != ('s0', 'nightmare'):
        raise NativeBaseUnavailable('native-base-context-unsupported')
    if not isinstance(view, PackageView):
        _reject('package-view-required')
    component = view.component
    if component.get('type') != 'recognition-base' or component.get('schemaVersion') != 1:
        _reject('component-type-invalid')
    if {'season': season, 'mode': mode} not in component['contexts']:
        raise NativeBaseUnavailable('native-base-context-unsupported')
    if component['dependencies']:
        raise NativeBaseUnavailable('native-base-dependencies-unsupported')
    reader = ComponentResourceReader(view)
    context = reader.json(component['entrypoint'])
    if context.get('schema') != SCHEMA:
        raise NativeBaseUnavailable('native-base-schema-unsupported')
    if (context.get('season'), context.get('mode')) != (season, mode):
        raise NativeBaseUnavailable('native-base-context-unsupported')
    interfaces = context.get('runtimeInterfaces')
    if (set(context) != {'schema', 'season', 'mode', 'runtimeInterfaces'}
            or not isinstance(interfaces, dict) or set(interfaces) != _INTERFACES
            or interfaces.get('identityProfile') != PROFILE):
        _reject('interfaces-invalid')
    paths = {name: _reference(view, interfaces[name], '.json') for name in
             ('identityPack', 'typedIndex', 'coordinatePack', 'neutralDisplay', 'exitIcon')}
    directory = interfaces['identityEntries']
    if not isinstance(directory, str) or files._relative(directory) != directory:
        _reject('entry-directory-invalid')
    entries = {name: _reference(view, directory + '/' + name, '.npz' if name.endswith('.npz') else '.json')
               for name in ('entries.json', 'side-entries.json', 'entry-passports.json',
                            'side-door-anchors.json', 'side-entry-search.npz')}
    identity = reader.json(paths['identityPack'])
    _validate_imported_identity_ids(identity)
    if ((identity.get('schemaVersion'), identity.get('season'), identity.get('mode')) != (1, season, mode)
            or set(identity['floors']) != FLOORS or type(identity.get('defaultEntryFloor')) is not int
            or str(identity['defaultEntryFloor']) not in FLOORS):
        _reject('identity-context-invalid')
    gaps = []
    expected = {(row['id'], floor) for row in identity['maps'] for floor in FLOORS}
    for row in identity['maps']:
        for name in ('name', 'direction', 'shape'):
            _text(row.get(name))
        if (set(row['layers']) != FLOORS or row.get('identityProfileId') != PROFILE
                or row.get('routeProfileId') != 'shared-structure' or row.get('oneFlow') is not None):
            _reject('identity-layer-contract-invalid')
        _number(row.get('doorCount', 0), 0, 65536)
        crop = row.get('sideDoorCrop', row.get('previewBox', [0, 0, 0, 0]))
        if not isinstance(crop, list) or len(crop) != 4:
            _reject('identity-crop-invalid')
        for number in crop:
            _number(number, 0, MAX_CANVAS_AXIS)
        for floor, relative in row['layers'].items():
            if relative is None:
                gaps.append('native-layer-unavailable:' + row['id'] + '/' + floor)
            else:
                _reference(view, relative, '.npz')
    index = reader.json(paths['typedIndex'])
    if (index.get('schemaVersion'), index.get('resourceKind')) != (1, 'shared-structure'):
        _reject('typed-index-invalid')
    typed = _rows(index.get('entries'), expected, 'typed')
    selected = set()
    for key, row in typed.items():
        relative = _reference(view, row.get('resourcePath'), '.npz')
        if row.get('profile') != PROFILE or row.get('variant') != 'variant-01' or relative.casefold() in selected:
            _reject('typed-binding-invalid')
        selected.add(relative.casefold())
    for row in identity['maps']:
        for floor, relative in row['layers'].items():
            if relative is not None and relative != typed[row['id'], floor]['resourcePath']:
                _reject('identity-typed-reference-mismatch')
    registry_path = _reference(view, index.get('structureVersionBindings'), '.json')
    versions_raw = reader.json(registry_path)
    versions_rows = _rows(versions_raw.get('entries'), expected, 'version')
    neutral = reader.json(paths['neutralDisplay'])
    if (neutral.get('schemaVersion'), neutral.get('resourceKind'), neutral.get('indexPath')) != (
            1, 'typed-shared-structure-display', paths['typedIndex']):
        _reject('neutral-index-invalid')
    neutral_rows = _rows(neutral.get('entries'), expected, 'neutral')
    canvases = {key: row.get('canvas') for key, row in neutral_rows.items()}
    for key, row in neutral_rows.items():
        _canvas(canvases[key])
        if _reference(view, row.get('referenceResource'), '.npz') != typed[key]['resourcePath']:
            _reject('neutral-reference-invalid')
        version = versions_rows[key]
        for value, wanted in ((version.get('typedReference'), typed[key]['resourcePath']),
                              (version.get('packReference'), paths['identityPack']),
                              (version.get('adapterReference'), paths['coordinatePack']),
                              (version.get('neutralDisplay', {}).get('index'), paths['neutralDisplay']),
                              (version.get('sharedLookup', {}).get('resourcePath'), typed[key]['resourcePath'])):
            if _reference(view, value) != wanted:
                _reject('version-reference-invalid')
    coordinates = reader.json(paths['coordinatePack'])
    ids = {mid for mid, _ in expected}
    if (coordinates.get('schema') != CANONICAL_SCHEMA
            or coordinates.get('coordinateReferenceProfileId') != 'shared-structure'
            or set(coordinates.get('profiles', {})) != {'shared-structure'}
            or coordinates.get('canonicalCrossFloorSearchCandidates') not in (None, {})):
        raise NativeBaseUnavailable('native-base-coordinate-contract-unsupported')
    provider = coordinates.get('referenceProvider', {})
    if _reference(view, provider.get('indexPath'), '.json') != paths['typedIndex']:
        _reject('coordinate-index-invalid')
    lookups = _rows(provider.get('lookupBindings'), expected, 'coordinate-lookup')
    if any(row.get('profile') != PROFILE or row.get('variant') != 'variant-01' for row in lookups.values()):
        _reject('coordinate-lookup-invalid')
    if provider.get('resourceRoot', '.') != '.' or neutral.get('resourceRoot', '.') != '.':
        _reject('resource-root-invalid')
    for declared in (identity, coordinates):
        if 'structureVersionBindings' in declared and _reference(view, declared['structureVersionBindings']) != registry_path:
            _reject('version-registry-invalid')
    maps = coordinates.get('maps', {})
    shared = coordinates['profiles']['shared-structure'].get('maps', {})
    if set(maps) != ids or set(shared) != ids:
        _reject('coordinate-map-inventory-invalid')
    for mid in ids:
        if set(maps[mid].get('floors', {})) != FLOORS or set(shared[mid].get('floors', {})) != FLOORS:
            _reject('coordinate-floor-inventory-invalid')
        for floor in FLOORS:
            row, bridge = maps[mid]['floors'][floor], shared[mid]['floors'][floor]
            width, height = _canvas(canvases[mid, floor])
            if (_reference(view, row.get('referenceResource'), '.npz') != typed[mid, floor]['resourcePath']
                    or row.get('coordinateWidth', width) != width or row.get('coordinateHeight', height) != height):
                _reject('coordinate-canvas-invalid')
            if 'matrixCanonicalToLegacyRecognition' in row:
                raise NativeBaseUnavailable('native-base-legacy-coordinates-unsupported')
            _identity_matrix(bridge.get('matrixProfileToCanonical'))
    main_raw, local_raw = reader.json(entries['entries.json']), reader.json(entries['side-entries.json'])
    _entry_rows(main_raw, {(mid, '1') for mid in ids}, canvases, typed, view, directory, paths['typedIndex'], main=True)
    local_rows = _entry_rows(local_raw, expected, canvases, typed, view, directory, paths['typedIndex'], main=False)
    passports = reader.json(entries['entry-passports.json'])
    if passports.get('entries') != [] or passports.get('coverage') != []:
        raise NativeBaseUnavailable('native-base-semantic-passports-unsupported')
    doors = reader.json(entries['side-door-anchors.json'])
    door_rows = _rows(doors.get('entries'), {(mid, '2') for mid in ids}, 'side-door')
    for key, row in door_rows.items():
        _points([row.get('boundaryCenter')], 1, *_canvas(canvases[key]), exact=1)
    _search_declarations(reader(view.path(entries['side-entry-search.npz'])), local_rows, typed)
    exit_data = reader.json(paths['exitIcon'])
    if exit_data.get('role') != 'exit-template-only' or exit_data.get('sideDoors') != []:
        raise NativeBaseUnavailable('native-base-exit-contract-unsupported')
    icon = exit_data.get('exitIcon', {})
    width, height = icon.get('width'), icon.get('height')
    if (type(width) is not int or type(height) is not int or not 1 <= width <= 256
            or not 1 <= height <= 256 or not isinstance(icon.get('pixels'), str)
            or len(icon['pixels']) > 4 * math.ceil(width * height / 3)
            or len(base64.b64decode(icon['pixels'], validate=True)) != width * height):
        _reject('exit-template-invalid')
    pack = NativeDifficultPack(view.root, paths['identityPack'], expected_mode=mode,
                               expected_season=season, read_resource=reader)
    resources = SharedStructureResourceStore(view.path(paths['typedIndex']), view.root, read_resource=reader)
    if resources.error is not None:
        _reject('typed-store-invalid')
    adapters = AuthorCoordinateAdapters(view.path(paths['coordinatePack']), read_resource=reader)
    for mid, floor in expected:
        matrix = adapters.matrix_profile_to_canonical('shared-structure', mid, floor)
        if matrix is None or not np.array_equal(matrix, np.eye(3)):
            _reject('shared-canvas-adapter-invalid')
    entry_root = view.root / directory
    main = MapEntryGeometryIdentity(entry_root, structure_store=resources, profile_id=PROFILE,
        profile_for_map=pack.identity_profiles.__getitem__, read_resource=reader)
    local = MapLocalEntryIdentity(entry_root, mode=mode, profile_id=PROFILE, floors=pack.floors,
        exit_icon_path=view.path(paths['exitIcon']), structure_store=resources,
        profile_for_map=pack.identity_profiles.__getitem__, read_resource=reader,
        typed_index_path=view.path(paths['typedIndex']))
    if any(error is not None for error in
           (main.error, local.side_door_error, local.side_search_error, local.passport_error)):
        _reject('entry-loader-invalid')
    for item, points, rays, pairs in local.entries:
        if str(item['floor']) != '2':
            continue
        arrays = local.side_search[item['mapId'], item['floor']]
        if any(not np.isfinite(value).all() for value in arrays.values()):
            _reject('search-values-invalid')
        if np.any(arrays['radius'] < 0) or np.any(arrays['denoms'] <= 0):
            _reject('search-values-invalid')
        # This is import/admission validation, not per-frame preprocessing.
        # These fields decide whether rival seeds get searched, so shape and
        # finiteness cannot establish their conservativeness. Reuse the actual
        # producer once per bounded floor and compare every consumed field.
        rebuilt = prepare_arrays(item, points, rays, pairs, local.side_doors[item['mapId']])
        for name, value in rebuilt.items():
            if not np.array_equal(arrays[name], value):
                _reject('search-derived-mismatch-' + name)
        del rebuilt
    versions = load_shared_version_context(view.root, pack, resources, adapters, None, local.entries,
        neutral_relative=paths['neutralDisplay'], entry_root_relative=directory, read_resource=reader)
    if set(versions) != set(resources.entries) or any(row['sharedError'] for row in versions.values()):
        _reject('version-binding-invalid')
    for key, row in resources.entries.items():
        raw = reader(view.path(row['resourcePath']))
        metadata = validate_structure_declarations(raw,
            expected_binding=versions[key]['binding'], expected_shape=versions[key]['canvas']['shapeHW'])
        if metadata.get('sourceKind') != 'game-rendered-shared-structure':
            _reject('typed-source-kind-invalid')
        calibration = metadata.get('calibration')
        if calibration is not None:
            _identity_matrix(calibration.get('matrixCurrentReferenceToNative'))
        product = resources.load(key[0], key[1], key[3], variant=key[2])
        if (product is None or not np.any(product.clean_mask)
                or list(product.shape) != versions[key]['canvas']['shapeHW']
                or product.metadata.get('canvas') != versions[key]['canvas']
                or any(product.metadata.get(name) != value for name, value in versions[key]['expected'].items())):
            _reject('typed-product-invalid')
        del product, raw
    result = NativeBaseBundle(view, pack=pack, resources=resources, adapters=adapters,
        versions=MappingProxyType(versions), interfaces=MappingProxyType(dict(interfaces)), reader=reader,
        season=season, mode=mode, component_ref=view.component_ref,
        entry_identity=main, side_entry_identity=local,
        exit_recognizer=SideDoorRecognizer(view.path(paths['exitIcon']), read_resource=reader), gaps=tuple(gaps))
    result.assert_current()
    return result


def decode_native_base(view, *, season='s0', mode='nightmare'):
    """Decode a declared S0N base without authorizing activation or identity."""
    try:
        return _decode(view, season, mode)
    except (files.ImportRejected, NativeBaseUnavailable):
        raise
    except (OSError, ValueError, TypeError, KeyError, IndexError, OverflowError,
            AttributeError, SystemError, zipfile.BadZipFile, zlib.error, EOFError) as error:
        code = 'imported-native-base-invalid'
        raise files.ImportRejected(code, code=code) from error


__all__ = ['decode_native_base', 'NativeBaseBundle', 'NativeBaseUnavailable']
