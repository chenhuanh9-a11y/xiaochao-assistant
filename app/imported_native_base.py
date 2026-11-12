"""Semantic admission of declared native recognition bases.

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
S1_COORDINATE_INTERFACE = 's1CoordinateRecognition'
S1_COORDINATE_DIRECTORY = 'references/s1-coordinate'
S1_COORDINATE_FILES = ('s1_entrance_coordinate_index.json', 'template_manifest.json',
                       'native_glyph_template.png')


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
        from imported_fast_structure import ImportedFastStructureSource
        self._values['fast_structure_source'] = ImportedFastStructureSource(
            view, values['reader'], interfaces=values['interfaces'], versions=values['versions'])
        from imported_group_registry import ImportedFrontGroups, INTERFACE as GROUP_INTERFACE
        group_path = values['interfaces'].get(GROUP_INTERFACE)
        self._values['reviewed_front_groups'] = ImportedFrontGroups(self, group_path) if group_path else None

    def _current_view_root(self):
        view = self._view
        root = view.root  # Actual bounded manifest/root load.
        if view is not self._view or root != self._root:
            _reject('component-root-changed')
        return view, root

    def assert_current(self):
        self._current_view_root()

    def read_binding(self):
        """Read one current view and its declared fields for context validation."""
        view, root = self._current_view_root()
        return view, root, self._values['component_ref'], self._values['reader']

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
    legacy_references = property(lambda self: self._cached('legacy_references'))
    legacy_passports = property(lambda self: self._cached('legacy_passports'))
    native_source = property(lambda self: self._cached('native_source'))
    semantic_side_entries = property(lambda self: self._cached('semantic_side_entries'))
    passport_source_available = property(lambda self: self._cached('passport_source_available'))
    rank_ready = property(lambda self: self._cached('rank_ready'))
    gaps = property(lambda self: self._cached('gaps'))
    fast_structure_source = property(lambda self: self._cached('fast_structure_source'))
    reviewed_front_groups = property(lambda self: self._cached('reviewed_front_groups'))
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


def _canvas(value, *, shape_only=False):
    if not isinstance(value, dict):
        _reject('canvas-invalid')
    height, width = value.get('height'), value.get('width')
    if shape_only:
        shape = value.get('shapeHW')
        if not isinstance(shape, list) or len(shape) != 2:
            _reject('canvas-invalid')
        height, width = shape
        if any(name in value and (type(value[name]) is not int or value[name] != size)
               for name, size in (('height', height), ('width', width))):
            _reject('canvas-invalid')
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


def read_s1_coordinate_assets(view, reader, directory, *, mode, map_ids, floors):
    """Read an optional declared base resource through its ordinary owner."""
    if not isinstance(directory, str) or files._relative(directory) != directory:
        _reject('s1-coordinate-directory-invalid')
    paths = [directory + '/' + name for name in S1_COORDINATE_FILES]
    for path in paths:
        _reference(view, path, Path(path).suffix)
    root = reader.root
    raw = reader.read_many(tuple(root / path for path in paths), expected_root=root)
    payloads = dict(zip(S1_COORDINATE_FILES, raw))
    index = decode_resource_json(payloads[S1_COORDINATE_FILES[0]])
    manifest = decode_resource_json(payloads['template_manifest.json'])
    from s1_native_resources import SOURCE_VERSION
    if (index.get('schema') != 's1-common-entrance-coordinate-index-v1'
            or index.get('sourceVersion') != SOURCE_VERSION
            or manifest.get('template_file') != 'native_glyph_template.png'
            or index.get('glyphTemplateSha256') != manifest.get('template_sha256')):
        _reject('s1-coordinate-assets-invalid')
    entries = index.get('maps', {}).get(mode)
    if not isinstance(entries, dict) or set(entries) != set(map_ids):
        _reject('s1-coordinate-map-inventory')
    for mid in map_ids:
        if not isinstance(entries[mid], dict) or set(entries[mid]) != set(floors):
            _reject('s1-coordinate-floor-inventory')
        for entry in entries[mid].values():
            try:
                matrix = np.asarray(entry['imageToEntranceXZ'], dtype=float)
                shape = entry['shapeHW']
                valid = (matrix.shape == (3, 3) and np.isfinite(matrix).all()
                    and np.array_equal(matrix[:2, :2], [[1, 0], [0, -1]])
                    and np.array_equal(matrix[2], [0, 0, 1])
                    and np.array_equal(matrix[:2, 2], np.rint(matrix[:2, 2]))
                    and isinstance(shape, list) and len(shape) == 2
                    and all(type(value) is int and 0 < value <= MAX_CANVAS_AXIS for value in shape)
                    and math.prod(shape) <= MAX_CANVAS_PIXELS
                    and all(isinstance(entry[key], str) and re.fullmatch('[0-9a-f]{64}', entry[key])
                        for key in ('sourceCatalogSha256', 'sourceRendererMetadataSha256')))
            except (ValueError, TypeError, KeyError):
                valid = False
            if not valid:
                _reject('s1-coordinate-entry-invalid')
    # Reuse the original calibration decoder over the exact guarded batch.
    from s1_gate_anchor import GateAnchorLocator
    GateAnchorLocator(asset_dir=root / directory,
        read_resource=lambda path: payloads[Path(path).name])
    return payloads


def _checked_reference_matches(view, relative, checked, extension=None):
    """Compare an alias with an exact reference checked earlier in this decode.

    Payload consumers still read through the owning view. Different aliases
    retain the original path/type checks and their original rejection reason.
    """
    return relative == checked or _reference(view, relative, extension) == checked


def _entry_reference(view, directory, relative):
    # The old entry schema has an explicit document-directory anchor. Its
    # sourceIndex '../index.json' is resolved to a canonical component FileRef,
    # never accepted by a general filesystem fallback.
    if (not isinstance(relative, str) or not relative or relative.startswith('/')
            or '\\' in relative or ':' in relative or posixpath.normpath(relative) != relative):
        _reject('entry-reference-invalid')
    return _reference(view, posixpath.normpath(posixpath.join(directory, relative)))


def _entry_rows(payload, expected, canvases, products, view, directory, index, *, main,
                mode='nightmare', profile=PROFILE, proposal_only=False, profile_for_map=None,
                reference_anchor='document-relative', legacy_keys=(), validate_semantics=None):
    if (payload.get('schemaVersion'), payload.get('mode'), payload.get('profileId')) != (1, mode, profile):
        _reject('entry-context-invalid')
    rows = _rows(payload.get('entries'), expected, 'main-entry' if main else 'local-entry')
    if reference_anchor not in ('document-relative', 'component-root'):
        _reject('entry-reference-anchor-invalid')
    anchor = directory if reference_anchor == 'document-relative' else ''
    total = 0
    for key, row in rows.items():
        _text(row.get('material') if main else row.get('region'))
        width, height = _canvas(canvases[key], shape_only=key in legacy_keys)
        row_profile = profile_for_map(key[0]) if profile_for_map is not None else profile
        if (row.get('canvas') != canvases[key] or row.get('profileId') != row_profile
                or row.get('variantId') != 'variant-01'
                or _entry_reference(view, anchor, row.get('structureResource')) != products[key]['resourcePath']
                or row.get('sourceProduct') != (products[key]['resourcePath'] if reference_anchor == 'component-root'
                                                else Path(products[key]['resourcePath']).name)):
            _reject('entry-typed-binding-invalid')
        if 'sourceIndex' in row and _entry_reference(view, anchor, row['sourceIndex']) != index:
            _reject('entry-index-binding-invalid')
        if any(name in row and row[name] != products[key].get(name)
               for name in ('sourceVersion', 'structureVersionKey', 'baseSnapshot')):
            _reject('entry-version-binding-invalid')
        if 'routeResource' in row:
            raise NativeBaseUnavailable('native-base-author-entry-unsupported')
        if proposal_only:
            if row.get('gates') != [] or row.get('knownWallSupportUnavailable') is not True:
                _reject('proposal-entry-semantics-invalid')
            if main:
                if (row.get('kind') != 'main' or row.get('proposalOnly') is not True
                        or row.get('authorizesPose') is not False or row.get('authorizesIdentity') is not False):
                    _reject('proposal-entry-authority-invalid')
            else:
                import cv2
                if row.get('featureRole') != 'local-contour-proposals-not-semantic-side-exits':
                    _reject('proposal-local-role-invalid')
                if row.get('extractorEnvironment', {}).get('opencv') != cv2.__version__:
                    raise NativeBaseUnavailable('native-base-extractor-runtime-mismatch')
        if validate_semantics is not None:
            validate_semantics(key, row, main)
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


def _typed_declarations(view, reader, paths, expected, profile, identity_rows):
    """Read the shared typed/index/version/neutral contract for its exact pool."""
    index = reader.json(paths['typedIndex'])
    if (index.get('schemaVersion'), index.get('resourceKind')) != (1, 'shared-structure'):
        _reject('typed-index-invalid')
    typed = _rows(index.get('entries'), expected, 'typed')
    selected = set()
    for key, row in typed.items():
        relative = _reference(view, row.get('resourcePath'), '.npz')
        if row.get('profile') != profile or row.get('variant') != 'variant-01' or relative.casefold() in selected:
            _reject('typed-binding-invalid')
        selected.add(relative.casefold())
    for row in identity_rows:
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
        if not _checked_reference_matches(view, row.get('referenceResource'), typed[key]['resourcePath'], '.npz'):
            _reject('neutral-reference-invalid')
        version = versions_rows[key]
        for value, wanted in ((version.get('typedReference'), typed[key]['resourcePath']),
                              (version.get('packReference'), paths['identityPack']),
                              (version.get('adapterReference'), paths['coordinatePack']),
                              (version.get('neutralDisplay', {}).get('index'), paths['neutralDisplay']),
                              (version.get('sharedLookup', {}).get('resourcePath'), typed[key]['resourcePath'])):
            if not _checked_reference_matches(view, value, wanted):
                _reject('version-reference-invalid')
    return typed, registry_path, neutral, canvases


def _verify_typed_products(view, reader, resources, versions, coordinate_index=None):
    for key, row in resources.entries.items():
        relative = row['resourcePath']
        raw = reader(view.path(relative))
        metadata = validate_structure_declarations(raw,
            expected_binding=versions[key]['binding'], expected_shape=versions[key]['canvas']['shapeHW'])
        if metadata.get('sourceKind') != 'game-rendered-shared-structure':
            _reject('typed-source-kind-invalid')
        calibration = metadata.get('calibration')
        if calibration is not None:
            _identity_matrix(calibration.get('matrixCurrentReferenceToNative'))

        def read_preflighted(path):
            if reader.relative_path(path) != relative:
                _reject('typed-product-invalid')
            # Repeat the actual file proof at the loader's original read point.
            # Only the generic NPZ preflight above is reusable; the loader
            # still validates typed declarations and decodes the complete arrays.
            return reader.view.read(relative)

        product = resources.load(key[0], key[1], key[3], variant=key[2],
                                 read_resource=read_preflighted)
        if (product is None or not np.any(product.clean_mask)
                or list(product.shape) != versions[key]['canvas']['shapeHW']
                or product.metadata.get('canvas') != versions[key]['canvas']
                or any(product.metadata.get(name) != value for name, value in versions[key]['expected'].items())):
            _reject('typed-product-invalid')
        if coordinate_index is not None:
            coordinate_index.provider = lambda mid, floor: product if (mid, floor) == (key[1], key[3]) else None
            try:
                coordinate_index._native_to_common(key[1], key[3])
            except (KeyError, TypeError, ValueError):
                _reject('s1-coordinate-reference-binding-mismatch:' + key[1] + '/' + key[3])
        del product, raw


def _decode(view, season, mode):
    if (season, mode) == ('s0', 'difficult'):
        from imported_difficult_native_base import decode_s0_difficult
        return decode_s0_difficult(view)
    if (season, mode) not in {('s0', 'nightmare'), ('s1', 'difficult'), ('s1', 'nightmare')}:
        raise NativeBaseUnavailable('native-base-context-unsupported')
    if not isinstance(view, PackageView):
        _reject('package-view-required')
    with view.admission_read_scope() as scope:
        return _decode_component(scope, view, season, mode)


def _bind_live_readers(reader, resources, adapters, main, local, exit_recognizer=None):
    # These objects are still private. Published consumers must perform their
    # ordinary actual view reads, including lazily constructed exit readers.
    resources.read_resource = reader
    adapters._read_resource = reader
    main.read_resource = local.read_resource = reader
    if local._exit_recognizer is not None:
        local._exit_recognizer.read_resource = reader
    if exit_recognizer is not None:
        exit_recognizer.read_resource = reader


def _admission_owner_state(bundle):
    """RAM ownership of the unpublished result, including all retained readers."""
    values = bundle._values
    resources, adapters = values['resources'], values['adapters']
    main, local, exit_recognizer = (values[name] for name in
                                  ('entry_identity', 'side_entry_identity', 'exit_recognizer'))
    fast = values['fast_structure_source']
    legacy = values['legacy_references']
    groups = values['reviewed_front_groups']
    return (id(bundle._view), bundle._root, values['component_ref'],
        tuple(id(values[name]) for name in ('reader', 'pack', 'resources', 'adapters', 'versions',
            'entry_identity', 'side_entry_identity', 'exit_recognizer', 'legacy_references', 'fast_structure_source')),
        (id(resources.read_resource), resources.resource_root, resources.index_path, id(resources.entries)),
        id(adapters._read_resource),
        (main.root, id(main.read_resource), id(main.structures), id(main.shared_reference_provider)),
        (local.root, id(local.read_resource), id(local.structures), id(local.shared_reference_provider)),
        None if local._exit_recognizer is None else id(local._exit_recognizer.read_resource),
        None if exit_recognizer is None else id(exit_recognizer.read_resource),
        fast._read_state(),
        None if groups is None else (id(groups), id(groups._base), id(groups._view), groups._path, groups.file_ref),
        None if legacy is None else legacy.read_owner_state())


def _bind_bundle_live_reader(bundle, reader):
    # Fast/groups and legacy use actual live reads even during construction.
    # Their temporary observer must not remain in a published model.
    values = bundle._values
    values['reader'] = reader
    values['fast_structure_source'].reader = reader
    legacy = values['legacy_references']
    if legacy is not None:
        legacy._reader = legacy._store.read_resource = reader


def _decode_component(view, live_view, season, mode):
    proposal_only = season == 's1'
    floors = frozenset(('-1', '-2', '-3', '-4')) if proposal_only else FLOORS
    profile = 'shared-structure' if proposal_only else PROFILE
    component = view.component
    if component.get('type') != 'recognition-base' or component.get('schemaVersion') != 1:
        _reject('component-type-invalid')
    if {'season': season, 'mode': mode} not in component['contexts']:
        raise NativeBaseUnavailable('native-base-context-unsupported')
    if component['dependencies']:
        raise NativeBaseUnavailable('native-base-dependencies-unsupported')
    live_reader = ComponentResourceReader(live_view)
    reader = live_reader if view is live_view else live_reader.for_admission(view)
    admission_live_reader = live_reader if view is live_view else live_reader.recording_for_admission(view)
    context = reader.json(component['entrypoint'])
    if context.get('schema') != SCHEMA:
        raise NativeBaseUnavailable('native-base-schema-unsupported')
    if (context.get('season'), context.get('mode')) != (season, mode):
        raise NativeBaseUnavailable('native-base-context-unsupported')
    interfaces = context.get('runtimeInterfaces')
    interface_fields = (_INTERFACES - {'exitIcon'}) | {'entryEvidenceRole'} if proposal_only else _INTERFACES
    optional_interfaces = {'fastStructureIndex', 'reviewedFrontGroups'}
    if proposal_only:
        optional_interfaces.add(S1_COORDINATE_INTERFACE)
    if (set(context) != {'schema', 'season', 'mode', 'runtimeInterfaces'}
            or not isinstance(interfaces, dict)
            or set(interfaces) - optional_interfaces != interface_fields
            or interfaces.get('identityProfile') != profile
            or proposal_only and interfaces.get('entryEvidenceRole') != 'proposal-only'):
        _reject('interfaces-invalid')
    paths = {name: _reference(view, interfaces[name], '.json') for name in
             ('identityPack', 'typedIndex', 'coordinatePack', 'neutralDisplay')}
    if 'fastStructureIndex' in interfaces:
        _reference(view, interfaces['fastStructureIndex'], '.json')
    if 'reviewedFrontGroups' in interfaces:
        _reference(view, interfaces['reviewedFrontGroups'], '.json')
    if not proposal_only:
        paths['exitIcon'] = _reference(view, interfaces['exitIcon'], '.json')
    directory = interfaces['identityEntries']
    if not isinstance(directory, str) or files._relative(directory) != directory:
        _reject('entry-directory-invalid')
    entry_files = ('entries.json', 'side-entries.json', 'entry-passports.json') + (
        () if proposal_only else ('side-door-anchors.json', 'side-entry-search.npz'))
    entries = {name: _reference(view, directory + '/' + name, '.npz' if name.endswith('.npz') else '.json')
               for name in entry_files}
    identity = reader.json(paths['identityPack'])
    _validate_imported_identity_ids(identity)
    if ((identity.get('schemaVersion'), identity.get('season'), identity.get('mode')) != (1, season, mode)
            or set(identity['floors']) != floors or type(identity.get('defaultEntryFloor')) is not int
            or str(identity['defaultEntryFloor']) not in floors):
        _reject('identity-context-invalid')
    if proposal_only:
        from s1_native_resources import MODE_LAYOUTS
        if (identity['floors'] != ['-1', '-2', '-3', '-4'] or identity['defaultEntryFloor'] != -1
                or {row['id'] for row in identity['maps']} != MODE_LAYOUTS[mode]):
            _reject('identity-context-inventory-invalid')
    gaps = []
    expected = {(row['id'], floor) for row in identity['maps'] for floor in floors}
    for row in identity['maps']:
        for name in ('name', 'direction', 'shape'):
            _text(row.get(name))
        if (set(row['layers']) != floors or row.get('identityProfileId') != profile
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
    typed, registry_path, neutral, canvases = _typed_declarations(
        view, reader, paths, expected, profile, identity['maps'])
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
    if any(row.get('profile') != profile or row.get('variant') != 'variant-01' for row in lookups.values()):
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
        if set(maps[mid].get('floors', {})) != floors or set(shared[mid].get('floors', {})) != floors:
            _reject('coordinate-floor-inventory-invalid')
        for floor in floors:
            row, bridge = maps[mid]['floors'][floor], shared[mid]['floors'][floor]
            width, height = _canvas(canvases[mid, floor])
            if (not _checked_reference_matches(view, row.get('referenceResource'), typed[mid, floor]['resourcePath'], '.npz')
                    or row.get('coordinateWidth', width) != width or row.get('coordinateHeight', height) != height):
                _reject('coordinate-canvas-invalid')
            if 'matrixCanonicalToLegacyRecognition' in row:
                if not proposal_only:
                    raise NativeBaseUnavailable('native-base-legacy-coordinates-unsupported')
                _identity_matrix(row['matrixCanonicalToLegacyRecognition'])
            _identity_matrix(bridge.get('matrixProfileToCanonical'))
    main_raw, local_raw = reader.json(entries['entries.json']), reader.json(entries['side-entries.json'])
    _entry_rows(main_raw, {(mid, '-1' if proposal_only else '1') for mid in ids}, canvases, typed,
        view, directory, paths['typedIndex'], main=True, mode=mode, profile=profile, proposal_only=proposal_only)
    local_rows = _entry_rows(local_raw, expected, canvases, typed, view, directory, paths['typedIndex'],
        main=False, mode=mode, profile=profile, proposal_only=proposal_only)
    passports = reader.json(entries['entry-passports.json'])
    if passports.get('entries') != [] or passports.get('coverage') != []:
        raise NativeBaseUnavailable('native-base-semantic-passports-unsupported')
    if not proposal_only:
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
    main = MapEntryGeometryIdentity(entry_root, structure_store=resources, profile_id=profile,
        profile_for_map=pack.identity_profiles.__getitem__, read_resource=reader)
    local = MapLocalEntryIdentity(entry_root, mode=mode, profile_id=profile, floors=pack.floors,
        exit_icon_path=None if proposal_only else view.path(paths['exitIcon']), structure_store=resources,
        profile_for_map=pack.identity_profiles.__getitem__, read_resource=reader,
        typed_index_path=view.path(paths['typedIndex']), semantic_side_entries=not proposal_only)
    if any(error is not None for error in
           (main.error, local.passport_error, local.side_door_error, local.side_search_error)):
        _reject('entry-loader-invalid')
    if proposal_only and (local.side_doors or local.side_search or local.exit_icon_path is not None):
        _reject('proposal-side-evidence-invalid')
    for item, points, rays, pairs in local.entries:
        if proposal_only or str(item['floor']) != '2':
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
    coordinate_index = None
    if proposal_only and S1_COORDINATE_INTERFACE in interfaces:
        coordinate_directory = interfaces[S1_COORDINATE_INTERFACE]
        coordinate_assets = read_s1_coordinate_assets(view, reader, coordinate_directory,
            mode=mode, map_ids=pack.by_id, floors=floors)
        from s1_pose_prior import CoordinatePriorIndex
        coordinate_relative = coordinate_directory + '/' + S1_COORDINATE_FILES[0]
        coordinate_index = CoordinatePriorIndex(reader.root / coordinate_relative,
            expected_sha256=view.inventory[coordinate_relative]['sha256'], mode=mode,
            resource_key='admission-only', provider=lambda mid, floor: None,
            read_resource=lambda path: coordinate_assets[Path(path).name])
    _verify_typed_products(view, reader, resources, versions, coordinate_index)
    exit_recognizer = None if proposal_only else SideDoorRecognizer(view.path(paths['exitIcon']), read_resource=reader)
    _bind_live_readers(live_reader, resources, adapters, main, local, exit_recognizer)
    result = NativeBaseBundle(live_view, pack=pack, resources=resources, adapters=adapters,
        versions=MappingProxyType(versions), interfaces=MappingProxyType(dict(interfaces)), reader=admission_live_reader,
        season=season, mode=mode, component_ref=view.component_ref,
        entry_identity=main, side_entry_identity=local,
        exit_recognizer=exit_recognizer,
        legacy_references=None, legacy_passports=None, native_source=pack,
        semantic_side_entries=not proposal_only, passport_source_available=True, rank_ready=True,
        gaps=tuple(gaps))
    _bind_bundle_live_reader(result, live_reader)
    if view is not live_view:
        view.register_owner(lambda: _admission_owner_state(result))
    result.assert_current()
    return result


def decode_native_base(view, *, season='s0', mode='nightmare'):
    """Decode supported base data without authorizing activation or identity."""
    try:
        return _decode(view, season, mode)
    except (files.ImportRejected, NativeBaseUnavailable):
        raise
    except (OSError, ValueError, TypeError, KeyError, IndexError, OverflowError,
            AttributeError, SystemError, zipfile.BadZipFile, zlib.error, EOFError) as error:
        code = 'imported-native-base-invalid'
        raise files.ImportRejected(code, code=code) from error


__all__ = ['decode_native_base', 'NativeBaseBundle', 'NativeBaseUnavailable']
