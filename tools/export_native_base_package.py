"""Export a declared S0 nightmare or S1 native source into an inert base ZIP.

S0 keeps its existing byte-preserving structure/entry flow. S1 difficult and
nightmare share the declared map, typed, neutral and proposal flow, enriching
only version/coordinate references and controls metadata. This producer does
not activate a catalog or grant proposal pose/identity authority.
"""
from __future__ import annotations

import argparse
from copy import deepcopy
import hashlib
from io import BytesIO
import json
import os
from pathlib import Path
import posixpath
import shutil
import stat
import sys
import tempfile
import zipfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'app'))

import numpy as np

import imported_resource_archive as archives
import map_package_upgrade as publication
from imported_resource_packages import ComponentRef, PackageView
from imported_resource_reader import ComponentResourceReader, decode_resource_json
from resource_snapshot_io import read_stable_file
import star_map_import as files


FLOORS = ('1', '2', 'B1')
PROFILE = 'zhan10-speed'
PACK = 'references/nightmare-map-pack.json'
INDEX = 'references/nightmare-shared-structure-index.json'
VERSIONS = 'references/nightmare-structure-version-bindings.json'
COORDINATES = 'references/nightmare-canonical-coordinate-pack.json'
NEUTRAL = 'references/nightmare-structure-display.json'
ENTRIES = 'references/nightmare-entries'
EXIT = 'references/difficult-exit-icon.json'
INTERFACES = dict(identityPack=PACK, typedIndex=INDEX, coordinatePack=COORDINATES,
                  neutralDisplay=NEUTRAL, identityEntries=ENTRIES, exitIcon=EXIT,
                  identityProfile=PROFILE)
_SOURCE_FILES = (PACK, INDEX, VERSIONS, COORDINATES, NEUTRAL, EXIT,
                 *(ENTRIES + '/' + name for name in ('entries.json', 'side-entries.json',
                   'entry-passports.json', 'side-door-anchors.json', 'side-entry-search.npz')))

S1_PREFIX = 'seasons/s1/difficult'
S1_MANIFEST = S1_PREFIX + '/native-map-manifest.json'
S1_PREFIXES = {'difficult': S1_PREFIX, 'nightmare': 'seasons/s1/nightmare'}
S1_PACK = 'references/map-pack.json'
S1_INDEX = 'references/shared-structure-index.json'
S1_COORDINATES = 'references/coordinate-pack.json'
S1_NEUTRAL = 'references/structure-display.json'
S1_ENTRIES = 'references/entries'
S1_ENTRY_FILES = ('entries.json', 'side-entries.json', 'entry-passports.json')
S1_FLOORS = ('-1', '-2', '-3', '-4')
S1_PROFILE = 'shared-structure'
S1_INTERFACES = dict(identityPack=S1_PACK, typedIndex=S1_INDEX,
    coordinatePack=S1_COORDINATES, neutralDisplay=S1_NEUTRAL,
    identityEntries=S1_ENTRIES, identityProfile=S1_PROFILE,
    entryEvidenceRole='proposal-only')


def _reject(reason):
    raise ValueError('native-base-export-' + reason)


def _encoded(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'),
                      allow_nan=False).encode('utf-8')


def _relative(value):
    if files._relative(value) != value:
        _reject('noncanonical-path')
    return value


class _Source:
    def __init__(self, root, inventory):
        self.root = Path(root).absolute()
        files._no_links(self.root)
        if not self.root.is_dir():
            _reject('source-root-unavailable')
        self.inventory = None
        if inventory is not None:
            raw, _ = read_stable_file(Path(inventory), files.MAX_FILE_BYTES,
                no_links=files._no_links, reject=_reject)
            self.inventory = decode_resource_json(raw)['files']
            if not isinstance(self.inventory, dict):
                _reject('source-inventory-invalid')
        self.raw = {}

    def read(self, relative):
        relative = _relative(relative)
        if relative not in self.raw:
            path = files._target(self.root, relative)
            raw, _ = read_stable_file(path, files.MAX_FILE_BYTES,
                no_links=files._no_links, reject=_reject)
            if self.inventory is not None:
                expected = self.inventory.get(relative)
                if expected != dict(bytes=len(raw), sha256=hashlib.sha256(raw).hexdigest()):
                    _reject('source-inventory-mismatch:' + relative)
            self.raw[relative] = raw
        return self.raw[relative]

    def json(self, relative):
        return decode_resource_json(self.read(relative))


def _rows(rows, expected, label):
    if not isinstance(rows, list):
        _reject(label + '-invalid')
    result = {}
    for row in rows:
        key = (row['mapId'], str(row.get('floor', row.get('resourceKey', {}).get('floor'))))
        if key in result:
            _reject(label + '-duplicate-binding')
        result[key] = row
    if set(result) != expected:
        _reject(label + '-incomplete-inventory')
    return result


def _entry_resource(relative):
    # Existing entry JSON explicitly uses entry-directory-relative references.
    # Normalize that anchor, then enforce the actual component-local target.
    if not isinstance(relative, str) or relative.startswith('/') or '\\' in relative or ':' in relative:
        _reject('entry-resource-path')
    return _relative(posixpath.normpath(ENTRIES + '/' + relative))


def _controls_payloads():
    from floor_contract import floor_contract
    from floor_control_region import AUTO_VERTICAL_SEARCH_AREA, VERTICAL_SEARCH_AREA
    from imported_controls_profile import DETECTOR_ID, SCHEMA
    from s0_floor_templates import _bank, DIGIT_HEIGHT, HEIGHT, TEMPLATE_FAMILY, WIDTH
    contract = floor_contract('nightmare', season='s0')
    vectors, labels = _bank()
    target = BytesIO()
    np.save(target, vectors, allow_pickle=False)
    profile = dict(schema=SCHEMA, detectorId=DETECTOR_ID,
        contexts={'s0:nightmare': dict(
            floors=[dict(observedValue=f.observed_value, floorId=f.floor_id) for f in contract.floors],
            visualOrder=list(reversed(contract.observed_values)))},
        fixedSearchRegion=list(VERTICAL_SEARCH_AREA), expandedSearchRegion=list(AUTO_VERTICAL_SEARCH_AREA),
        templateBank=dict(path='templates.npy', labels=labels.tolist(), family=TEMPLATE_FAMILY,
                          shapeHW=[HEIGHT, WIDTH], digitHeight=DIGIT_HEIGHT))
    return {'profile.json': _encoded(profile), 'templates.npy': target.getvalue()}


def _base_payloads(source):
    raw = source.json(PACK)
    if (type(raw.get('schemaVersion')) is not int or raw['schemaVersion'] not in (1, 2)
            or raw.get('mode', 'nightmare') != 'nightmare'
            or raw.get('season', 's0') != 's0' or not raw.get('maps')
            or type(raw.get('defaultEntryFloor')) is not int or raw['defaultEntryFloor'] not in (1, 2)):
        _reject('source-context-invalid')
    maps = raw['maps']
    ids = [row['id'] for row in maps]
    if len(set(ids)) != len(ids) or len({mid.casefold() for mid in ids}) != len(ids):
        _reject('map-inventory-duplicate')
    expected = {(mid, floor) for mid in ids for floor in FLOORS}
    for row in maps:
        if (row.get('identityProfileId') != PROFILE or row.get('resourceBase') != 'resource-root'
                or set(row.get('layers', {})) != set(FLOORS)):
            _reject('map-layer-or-profile-incomplete:' + row['id'])
    index = source.json(INDEX)
    if (index.get('schemaVersion'), index.get('resourceKind'), index.get('structureVersionBindings')) != (
            1, 'shared-structure', VERSIONS):
        _reject('typed-index-contract')
    typed = _rows(index['entries'], expected, 'typed')
    paths = set()
    for (mid, floor), row in typed.items():
        path = _relative(row['resourcePath'])
        if (row.get('profile') != PROFILE or row.get('variant') != 'variant-01'
                or Path(path).suffix != '.npz' or path.casefold() in paths):
            _reject('typed-resource-binding')
        paths.add(path.casefold())
        source.read(path)
    versions = _rows(source.json(VERSIONS)['entries'], expected, 'versions')
    for row in versions.values():
        if (row.get('packReference') != PACK or row.get('adapterReference') != COORDINATES
                or row.get('neutralDisplay', {}).get('index') != NEUTRAL):
            _reject('version-reference-binding')
    neutral_index = source.json(NEUTRAL)
    if neutral_index.get('indexPath') != INDEX:
        _reject('neutral-index-binding')
    neutral = _rows(neutral_index['entries'], expected, 'neutral')
    local = _rows(source.json(ENTRIES + '/side-entries.json')['entries'], expected, 'local-entry')
    main = _rows(source.json(ENTRIES + '/entries.json')['entries'],
                 {(mid, '1') for mid in ids}, 'main-entry')
    _rows(source.json(ENTRIES + '/side-door-anchors.json')['entries'],
          {(mid, '2') for mid in ids}, 'side-door')
    for collection in (main, local):
        for key, row in collection.items():
            if (_entry_resource(row['structureResource']) != typed[key]['resourcePath']
                    or row['sourceProduct'] != Path(typed[key]['resourcePath']).name
                    or row.get('profileId') != PROFILE
                    or row.get('variantId') != 'variant-01'):
                _reject('entry-typed-binding')
            if 'sourceIndex' in row and _entry_resource(row['sourceIndex']) != INDEX:
                _reject('entry-index-binding')
    for key, row in neutral.items():
        if row.get('referenceResource') != typed[key]['resourcePath']:
            _reject('neutral-typed-binding')
    coordinates = source.json(COORDINATES)
    shared = coordinates.get('profiles', {}).get('shared-structure')
    if (not isinstance(shared, dict) or set(coordinates.get('maps', {})) != set(ids)
            or set(shared.get('maps', {})) != set(ids)
            or coordinates.get('coordinateReferenceProfileId') != 'shared-structure'
            or coordinates.get('referenceProvider', {}).get('indexPath') != INDEX):
        _reject('coordinate-inventory')
    lookups = _rows(coordinates['referenceProvider'].get('lookupBindings'), expected, 'coordinate-lookup')
    if any(row.get('profile') != PROFILE or row.get('variant') != 'variant-01' for row in lookups.values()):
        _reject('coordinate-lookup-profile')
    if coordinates.get('canonicalCrossFloorSearchCandidates') not in (None, {}):
        _reject('cross-floor-reference-requires-review')
    for mid in ids:
        for collection in (coordinates['maps'], shared['maps']):
            if set(collection[mid].get('floors', {})) != set(FLOORS):
                _reject('coordinate-floor-incomplete')
        for floor in FLOORS:
            if coordinates['maps'][mid]['floors'][floor].get('referenceResource') != typed[mid, floor]['resourcePath']:
                _reject('coordinate-typed-binding')
    converted = deepcopy(raw)
    converted.update(schemaVersion=1, sourceIdentitySchemaVersion=raw['schemaVersion'],
                     season='s0', mode='nightmare', floors=list(FLOORS))
    for row in converted['maps']:
        row.update(layers={floor: typed[row['id'], floor]['resourcePath'] for floor in FLOORS},
                   oneFlow=None, routeProfileId='shared-structure')
    from difficult_native_identity import _validate_imported_identity_ids
    _validate_imported_identity_ids(converted)
    coordinates = deepcopy(coordinates)
    coordinates['profiles'] = {'shared-structure': coordinates['profiles']['shared-structure']}
    # The selected source has no cross-floor relation; never infer one from an
    # author profile or silently discard a relation this producer cannot map.
    exit_template = dict(exitIcon=source.json(EXIT)['exitIcon'], sideDoors=[], role='exit-template-only')
    for path in _SOURCE_FILES:
        source.read(path)
    payloads = dict(source.raw)
    payloads[PACK] = _encoded(converted)
    payloads[COORDINATES] = _encoded(coordinates)
    payloads[EXIT] = _encoded(exit_template)
    payloads['resource-context.json'] = _encoded(dict(schema='xingtu-native-recognition-base-v1',
        season='s0', mode='nightmare', runtimeInterfaces=INTERFACES))
    return payloads, len(ids)


def _s1_local(value, label):
    if (not isinstance(value, str) or not value or value.startswith('/')
            or '\\' in value or ':' in value or value != posixpath.normpath(value)
            or '..' in value.split('/') or value == '.'):
        _reject('s1-' + label + '-path')
    return _relative(value)


def _s1_prefix(mode):
    prefix = S1_PREFIXES.get(mode)
    if prefix is None:
        _reject('s1-mode-unsupported')
    return prefix


def _s1_read(source, relative, label, *, mode='difficult'):
    return source.read(_s1_prefix(mode) + '/' + _s1_local(relative, label))


def _s1_entry_resource(relative):
    if (not isinstance(relative, str) or not relative or relative.startswith('/')
            or '\\' in relative or ':' in relative):
        _reject('s1-entry-resource-path')
    resolved = posixpath.normpath(S1_ENTRIES + '/' + relative)
    if resolved == '..' or resolved.startswith('../'):
        _reject('s1-entry-resource-path')
    return _relative(resolved)


def _s1_source_manifest(source, mode='difficult'):
    prefix = _s1_prefix(mode)
    try:
        manifest = source.json(prefix + '/native-map-manifest.json')
    except OSError:
        _reject('s1-source-manifest-unavailable:' + mode)
    from s1_native_resources import SOURCE_VERSION
    required_interfaces = {'identityPack', 'typedIndex', 'versionBindings', 'coordinatePack',
                           'neutralDisplay', 'identityEntries', 'authorCatalog'}
    if ((manifest.get('schemaVersion'), manifest.get('season'), manifest.get('mode'),
         manifest.get('sourceVersion')) != (1, 's1', mode, SOURCE_VERSION)):
        _reject('s1-source-manifest-context')
    interfaces = manifest.get('runtimeInterfaces')
    if not isinstance(interfaces, dict) or not required_interfaces <= set(interfaces):
        _reject('s1-source-manifest-interfaces')
    paths = {key: _s1_local(interfaces[key], 'interface-' + key) for key in required_interfaces}
    capabilities = manifest.get('capabilities')
    if not isinstance(capabilities, dict) or capabilities.get('sideEntries') is not False:
        _reject('s1-side-entry-capability-unsupported')
    declared = manifest.get('files')
    if not isinstance(declared, list) or not declared:
        _reject('s1-source-file-inventory-missing')
    by_path = {}
    for row in declared:
        if not isinstance(row, dict):
            _reject('s1-source-file-inventory-invalid')
        relative = _s1_local(row.get('path'), 'source-inventory')
        size, digest = row.get('bytes'), row.get('sha256')
        if (type(size) is not int or size < 0 or not isinstance(digest, str)
                or len(digest) != 64 or any(ch not in '0123456789abcdef' for ch in digest)):
            _reject('s1-source-file-inventory-invalid')
        if relative.casefold() in by_path:
            _reject('s1-source-file-inventory-duplicate')
        by_path[relative.casefold()] = (relative, size, digest)
    return paths, by_path


def _s1_checked_read(source, relative, manifest_files, label, *, mode='difficult'):
    local = _s1_local(relative, label)
    try:
        raw = _s1_read(source, local, label, mode=mode)
    except OSError:
        _reject('s1-required-source-unavailable:' + local)
    stamp = manifest_files.get(local.casefold())
    if stamp is None or stamp[0] != local or stamp[1] != len(raw) or stamp[2] != hashlib.sha256(raw).hexdigest():
        _reject('s1-manifest-file-mismatch:' + local)
    return raw


def _s1_version_payload(source, relative, expected, typed, pack_path, coordinate_path, neutral_path,
                        manifest_files, *, mode='difficult'):
    raw = decode_resource_json(_s1_checked_read(source, relative, manifest_files, 'version-bindings', mode=mode))
    if (raw.get('schemaVersion'), raw.get('resourceKind')) != (1, 'native-structure-version-bindings'):
        _reject('s1-version-bindings-contract')
    rows = _rows(raw.get('entries'), expected, 's1-version')
    for key, row in rows.items():
        for field, wanted in (('packReference', pack_path), ('adapterReference', coordinate_path)):
            if field in row and row[field] != wanted:
                _reject('s1-version-reference-conflict')
            row[field] = wanted
        neutral = row.get('neutralDisplay')
        if neutral is None:
            neutral = {}
        if not isinstance(neutral, dict) or ('index' in neutral and neutral['index'] != neutral_path):
            _reject('s1-version-neutral-reference-conflict')
        neutral['index'] = neutral_path
        row['neutralDisplay'] = neutral
        if row.get('typedReference') != typed[key]['resourcePath']:
            _reject('s1-version-typed-reference')
        lookup = row.get('sharedLookup')
        if not isinstance(lookup, dict) or lookup.get('resourcePath') != typed[key]['resourcePath']:
            _reject('s1-version-shared-lookup')
    return _encoded(raw)


def _s1_coordinate_payload(source, relative, expected, typed, index_path, manifest_files, *, mode='difficult'):
    coordinates = decode_resource_json(_s1_checked_read(source, relative, manifest_files, 'coordinate-pack', mode=mode))
    from nightmare_author_coordinate_adapter import CANONICAL_SCHEMA
    if (coordinates.get('schema') != CANONICAL_SCHEMA
            or coordinates.get('coordinateReferenceProfileId') != S1_PROFILE
            or set(coordinates.get('maps', {})) != {mid for mid, _ in expected}
            or set(coordinates.get('profiles', {})) != {S1_PROFILE}):
        _reject('s1-coordinate-inventory')
    lookup = [{key: row[key] for key in ('profile', 'mapId', 'floor', 'variant')}
              for _, row in sorted(typed.items())]
    provider = dict(indexPath=index_path, resourceRoot='.', lookupBindings=lookup)
    if 'referenceProvider' in coordinates and coordinates['referenceProvider'] != provider:
        _reject('s1-coordinate-reference-provider-conflict')
    coordinates['referenceProvider'] = provider
    # Keep every shared identity matrix, including the canonical-to-legacy
    # recognition projection, unchanged as source identity evidence.
    shared = coordinates['profiles'][S1_PROFILE].get('maps', {})
    if set(shared) != {mid for mid, _ in expected}:
        _reject('s1-shared-coordinate-inventory')
    for mid, floor in expected:
        if (set(coordinates['maps'][mid].get('floors', {})) != set(S1_FLOORS)
                or set(shared[mid].get('floors', {})) != set(S1_FLOORS)
                or coordinates['maps'][mid]['floors'][floor].get('referenceResource') != typed[mid, floor]['resourcePath']):
            _reject('s1-coordinate-floor-incomplete')
    return _encoded(coordinates)


def _s1_base_payloads(source, mode='difficult'):
    interfaces, manifest_files = _s1_source_manifest(source, mode)
    from s1_native_resources import MODE_LAYOUTS, SOURCE_VERSION
    ids_expected = set(MODE_LAYOUTS[mode])
    if len(ids_expected) != (14 if mode == 'difficult' else 15):
        _reject('s1-registered-map-pool-invalid')
    pack_source_path = interfaces['identityPack']
    index_source_path = interfaces['typedIndex']
    coordinate_source_path = interfaces['coordinatePack']
    neutral_source_path = interfaces['neutralDisplay']
    entries_source_path = interfaces['identityEntries']
    version_source_path = interfaces['versionBindings']
    pack_raw = _s1_checked_read(source, pack_source_path, manifest_files, 'identity-pack', mode=mode)
    index_raw = _s1_checked_read(source, index_source_path, manifest_files, 'typed-index', mode=mode)
    neutral_raw = _s1_checked_read(source, neutral_source_path, manifest_files, 'neutral-display', mode=mode)
    pack = decode_resource_json(pack_raw)
    if ((pack.get('schemaVersion'), pack.get('season'), pack.get('mode'), pack.get('floors'),
         pack.get('defaultEntryFloor')) != (1, 's1', mode, list(S1_FLOORS), -1)
            or not isinstance(pack.get('maps'), list) or len(pack['maps']) != len(ids_expected)):
        _reject('s1-identity-pack-contract')
    ids = {row.get('id') for row in pack['maps'] if isinstance(row, dict)}
    if ids != ids_expected or len(ids) != len(pack['maps']):
        _reject('s1-map-inventory')
    expected = {(mid, floor) for mid in ids for floor in S1_FLOORS}
    index = decode_resource_json(index_raw)
    if ((index.get('schemaVersion'), index.get('resourceKind'), index.get('structureVersionBindings'))
            != (1, 'shared-structure', version_source_path)):
        _reject('s1-typed-index-contract')
    typed = _rows(index.get('entries'), expected, 's1-typed')
    seen_paths = set()
    for key, row in typed.items():
        path = _s1_local(row.get('resourcePath'), 'typed-resource')
        if (row.get('profile') != S1_PROFILE or row.get('variant', 'variant-01') != 'variant-01'
                or not path.endswith('.npz') or path.casefold() in seen_paths):
            _reject('s1-typed-resource-binding')
        seen_paths.add(path.casefold())
        _s1_checked_read(source, path, manifest_files, 'typed-product', mode=mode)
    for row in pack['maps']:
        mid = row['id']
        if (row.get('sourceLayout') != mid or row.get('sourceVersion') != SOURCE_VERSION
                or row.get('identityProfileId') != S1_PROFILE
                or set(row.get('layers', {})) != set(S1_FLOORS)):
            _reject('s1-map-layer-or-profile-incomplete:' + mid)
        for floor in S1_FLOORS:
            path = _s1_local(row['layers'][floor], 'map-layer')
            if path != typed[mid, floor]['resourcePath']:
                _reject('s1-map-typed-reference-mismatch')
    versions = _s1_version_payload(source, version_source_path, expected, typed,
                                   S1_PACK, S1_COORDINATES, S1_NEUTRAL, manifest_files, mode=mode)
    neutral = decode_resource_json(neutral_raw)
    if ((neutral.get('schemaVersion'), neutral.get('resourceKind'), neutral.get('indexPath'))
            != (1, 'typed-shared-structure-display', index_source_path)):
        _reject('s1-neutral-index-binding')
    neutral_rows = _rows(neutral.get('entries'), expected, 's1-neutral')
    for key, row in neutral_rows.items():
        if row.get('referenceResource') != typed[key]['resourcePath']:
            _reject('s1-neutral-typed-binding')
    coordinate_raw = _s1_coordinate_payload(source, coordinate_source_path, expected, typed,
                                              S1_INDEX, manifest_files, mode=mode)
    entry_raw = {}
    for name in S1_ENTRY_FILES:
        relative = entries_source_path + '/' + name
        entry_raw[name] = _s1_checked_read(source, relative, manifest_files, 'entry-' + name, mode=mode)
    main = decode_resource_json(entry_raw['entries.json'])
    local = decode_resource_json(entry_raw['side-entries.json'])
    passports = decode_resource_json(entry_raw['entry-passports.json'])
    if ((main.get('schemaVersion'), main.get('season'), main.get('mode'), main.get('profileId'))
            not in ((1, 's1', mode, S1_PROFILE), (1, None, mode, S1_PROFILE))):
        _reject('s1-main-entry-contract')
    if ((local.get('schemaVersion'), local.get('season'), local.get('mode'), local.get('profileId'))
            not in ((1, 's1', mode, S1_PROFILE), (1, None, mode, S1_PROFILE))):
        _reject('s1-local-entry-contract')
    main_rows = _rows(main.get('entries'), {(mid, '-1') for mid in ids}, 's1-main-entry')
    local_rows = _rows(local.get('entries'), expected, 's1-local-entry')
    for rows, role in ((main_rows, 'main'), (local_rows, 'local')):
        for key, row in rows.items():
            if (row.get('profileId') != S1_PROFILE or row.get('variantId', 'variant-01') != 'variant-01'
                    or row.get('knownWallSupportUnavailable') is not True
                    or row.get('gates') != []
                    or _s1_entry_resource(row.get('structureResource')) != typed[key]['resourcePath']
                    or row.get('sourceProduct') != Path(typed[key]['resourcePath']).name):
                _reject('s1-' + role + '-entry-typed-binding')
            if role == 'main' and (row.get('kind') != 'main' or row.get('proposalOnly') is not True
                    or row.get('authorizesPose') is not False or row.get('authorizesIdentity') is not False):
                _reject('s1-main-entry-authority')
            if role == 'local' and (row.get('featureRole') != 'local-contour-proposals-not-semantic-side-exits'
                    or row.get('sourceKind') != 'game-rendered-shared-structure'
                    or row.get('extractorEnvironment', {}).get('opencv') != __import__('cv2').__version__):
                _reject('s1-local-entry-role-or-extractor')
    if ((passports.get('schemaVersion'), passports.get('mode'), passports.get('profileId'))
            != (1, mode, S1_PROFILE) or passports.get('entries') != []
            or passports.get('coverage') != []):
        _reject('s1-passports-must-remain-unavailable')
    for name, row in neutral_rows.items():
        if not isinstance(row.get('canvas'), dict):
            _reject('s1-neutral-canvas-missing')
    payloads = {S1_PACK: pack_raw, S1_INDEX: index_raw, S1_NEUTRAL: neutral_raw,
                S1_COORDINATES: coordinate_raw, version_source_path: versions}
    for name, raw in entry_raw.items():
        payloads[S1_ENTRIES + '/' + name] = raw
    for path in seen_paths:
        # Resolve case-insensitively back to the canonical source path already validated above.
        original = next(row['resourcePath'] for row in typed.values()
                        if row['resourcePath'].casefold() == path)
        payloads[original] = _s1_checked_read(source, original, manifest_files, 'typed-product', mode=mode)
    payloads['resource-context.json'] = _encoded(dict(schema='xingtu-native-recognition-base-v1',
        season='s1', mode=mode, runtimeInterfaces=S1_INTERFACES))
    return payloads, len(ids)


def _s1_controls_payloads(mode='difficult'):
    from floor_contract import floor_contract
    from imported_s1_controls_profile import DETECTOR_ID, SCHEMA
    from s1_floor_observation import EXPANDED_SEARCH_BOUNDS, ORDER, SEARCH_BOUNDS
    from s1_map_ui_geometry import MIN_CORRELATION, SEARCH, SIZES

    def xyxy_to_xywh(region):
        x0, y0, x1, y1 = region
        return [x0, y0, x1 - x0, y1 - y0]

    contract = floor_contract(mode, season='s1')
    if contract is None or tuple(contract.observed_values) != tuple(ORDER):
        _reject('s1-control-floor-contract')
    profile = dict(schema=SCHEMA, detectorId=DETECTOR_ID,
        contexts={f's1:{mode}': dict(
            floors=[dict(observedValue=f.observed_value, floorId=f.floor_id) for f in contract.floors],
            visualOrder=list(ORDER))},
        fixedSearchRegion=xyxy_to_xywh(SEARCH_BOUNDS),
        expandedSearchRegion=xyxy_to_xywh(EXPANDED_SEARCH_BOUNDS),
        mapUi=dict(searchRegions={key: xyxy_to_xywh(value) for key, value in SEARCH.items()},
                   sizes=list(SIZES), minimumCorrelation=dict(MIN_CORRELATION)))
    return {'profile.json': _encoded(profile)}


def _round_trip_s1(path, destination, mode):
    """Exercise the declared S1 decoder and its imported controls view."""
    from imported_native_base import decode_native_base
    from imported_s1_controls_profile import S1ControlsProfile
    inspected = archives.inspect_archive(path)
    saved = archives.save_archive(path, destination,
                                  expected_archive_sha256=inspected.archive_sha256)
    refs = {ref.component_id: ref for ref in saved.component_refs}
    base = PackageView(destination, refs['base'])
    bundle = decode_native_base(base, season='s1', mode=mode)
    controls_view = PackageView(destination, refs['controls'])
    controls = S1ControlsProfile(controls_view)
    context = controls.for_context('s1', mode)
    if (context is None or set(context.contract.floor_ids) != set(bundle.pack.floors)
            or {(key[1], key[3]) for key in bundle.resources.entries}
            != {(row.id, floor) for row in bundle.pack.maps for floor in bundle.pack.floors}):
        _reject('roundtrip-s1-controls-or-typed-inventory')
    bundle.assert_current()
    return dict(mapCount=len(bundle.pack.maps), typedLayerCount=len(bundle.resources.entries),
        mainEntries=len(bundle.entry_identity.entries), localEntries=len(bundle.side_entry_identity.entries),
        controlsDetector=controls.detector_id, controlsFloors=list(context.contract.floor_ids),
        proposalOnly=True, mode=mode)


def _round_trip(path, destination, *, season='s0', mode='nightmare'):
    """Exercise the real container, installed views and current data consumers."""
    if season == 's1' and mode in {'difficult', 'nightmare'}:
        return _round_trip_s1(path, destination, mode)
    if (season, mode) != ('s0', 'nightmare'):
        _reject('context-unsupported')
    from difficult_entry_identity import MapEntryGeometryIdentity
    from difficult_native_identity import NativeDifficultPack
    from difficult_side_entry_identity import MapLocalEntryIdentity
    from imported_controls_profile import S0ControlsProfile
    from nightmare_author_coordinate_adapter import AuthorCoordinateAdapters
    from shared_structure_context import load_shared_version_context
    from shared_structure_reference import SharedStructureResourceStore
    from side_door_recognition import SideDoorRecognizer
    inspected = archives.inspect_archive(path)
    saved = archives.save_archive(path, destination,
                                  expected_archive_sha256=inspected.archive_sha256)
    refs = {ref.component_id: ref for ref in saved.component_refs}
    base = PackageView(destination, refs['base'])
    reader = ComponentResourceReader(base)
    pack = NativeDifficultPack(base.root, PACK, expected_mode='nightmare', expected_season='s0',
                               read_resource=reader)
    resources = SharedStructureResourceStore(base.path(INDEX), base.root, read_resource=reader)
    if resources.error is not None:
        _reject('roundtrip-typed-index:' + resources.error)
    adapters = AuthorCoordinateAdapters(base.path(COORDINATES), read_resource=reader)
    entry_root = base.root / ENTRIES
    main = MapEntryGeometryIdentity(entry_root, structure_store=resources,
        profile_id=PROFILE, profile_for_map=pack.identity_profiles.__getitem__, read_resource=reader)
    local = MapLocalEntryIdentity(entry_root, mode='nightmare', profile_id=PROFILE, floors=FLOORS,
        exit_icon_path=base.path(EXIT), structure_store=resources,
        profile_for_map=pack.identity_profiles.__getitem__, read_resource=reader,
        typed_index_path=base.path(INDEX))
    errors = [main.error, local.side_door_error, local.side_search_error, local.passport_error]
    if any(error is not None for error in errors):
        _reject('roundtrip-entry:' + ';'.join(str(error) for error in errors if error))
    SideDoorRecognizer(base.path(EXIT), read_resource=reader)
    versions = load_shared_version_context(base.root, pack, resources, adapters, None, local.entries,
        neutral_relative=NEUTRAL, entry_root_relative=ENTRIES, read_resource=reader)
    if (set(versions) != set(resources.entries)
            or any(row['sharedError'] for row in versions.values())):
        _reject('roundtrip-version-binding')
    for profile, mid, variant, floor in resources.entries:
        product = resources.load(profile, mid, floor, variant=variant)
        if (product is None or not np.any(product.clean_mask)
                or list(product.shape) != versions[profile, mid, variant, floor]['canvas'].get('shapeHW')):
            _reject('roundtrip-typed-product:' + mid + '/' + floor)
    controls = S0ControlsProfile(PackageView(destination, refs['controls']))
    if set(controls.for_context('s0', 'nightmare').contract.floor_ids) != set(pack.floors):
        _reject('roundtrip-controls-floors')
    return dict(mapCount=len(pack.maps), typedLayerCount=len(resources.entries),
                mainEntries=len(main.entries), localEntries=len(local.entries),
                controlsTemplates=len(controls.templates.labels),
                controlsLabels=list(controls.templates.label_values))


def _discard_owned_temporary(path, identity):
    """Remove only our original file; an unavailable or replaced path stays."""
    try:
        files._no_links(path)
        with path.open('rb') as handle:
            info = os.fstat(handle.fileno())
            if not stat.S_ISREG(info.st_mode) or (info.st_dev, info.st_ino) != identity:
                return False
            digest = hashlib.file_digest(handle, 'sha256').hexdigest()
        # Windows reopens READ|DELETE with share READ only, then checks this
        # identity/content and applies disposition to that same native handle.
        return files._rollback_published(path, identity, digest)
    except FileNotFoundError:
        return True
    except (OSError, ValueError):
        return False


def _publish_output(staged, output, digest):
    """Finish and fsync a same-directory temporary before a no-replace move."""
    files._no_links(output.parent)
    descriptor, name = tempfile.mkstemp(prefix='.' + output.name + '.', suffix='.tmp', dir=output.parent)
    temporary = Path(name)
    info = os.fstat(descriptor)
    identity = (info.st_dev, info.st_ino)
    try:
        with os.fdopen(descriptor, 'wb') as target, staged.open('rb') as handle:
            shutil.copyfileobj(handle, target, length=1024 * 1024)
            target.flush()
            os.fsync(target.fileno())
        # Reuse the existing owned-file move: verify exact bytes and original
        # identity, then publish without replacing any destination. On Windows
        # verification and FileRenameInfo use the same exclusive native handle.
        publication._move_owned(temporary, output,
            dict(path=temporary.name, bytes=staged.stat().st_size, sha256=digest),
            list(identity), linkless=True)
    except BaseException as error:
        if not _discard_owned_temporary(temporary, identity):
            error.add_note('native-base-export-temporary-preserved: ' + str(temporary))
        raise


def export_package(source_root, output, *, package_id, resource_version, source_inventory=None,
                   season='s0', mode='nightmare'):
    """Validate and exclusively create the requested ZIP; never alter source."""
    output = Path(output).absolute()
    files._no_links(output)
    if output.exists():
        raise FileExistsError('native-base-export-output-exists')
    source = _Source(source_root, source_inventory)
    if output.resolve().is_relative_to(source.root.resolve()):
        _reject('output-inside-source')
    if (season, mode) not in (('s0', 'nightmare'), ('s1', 'difficult'), ('s1', 'nightmare')):
        _reject('context-unsupported')
    # Reuse the installed protocol's identity validation, not a new ID policy.
    ComponentRef(package_id, resource_version, 'base', '0' * 64)
    if (season, mode) == ('s0', 'nightmare'):
        base, map_count = _base_payloads(source)
        controls = _controls_payloads()
    else:
        base, map_count = _s1_base_payloads(source, mode=mode)
        controls = _s1_controls_payloads(mode=mode)
    payloads = {'base': base, 'controls': controls}
    context = [dict(season=season, mode=mode)]
    manifest = dict(schema='xingtu-resource-package', formatVersion=1,
        packageId=package_id, resourceVersion=resource_version,
        clientCompatibility=dict(resourceProtocol=1, requiredCapabilities=(
            ['native-s1-nightmare-base-v1'] if (season, mode) == ('s1', 'nightmare') else [])),
        components=[dict(componentId=component, type=kind, schemaVersion=1,
                         contexts=context, entrypoint=entry, dependencies={})
                    for component, kind, entry in [('base', 'recognition-base', 'resource-context.json'),
                        ('controls', 'controls-profile', 'profile.json')]],
        files=[dict(componentId=component, path=path, bytes=len(raw),
                    sha256=hashlib.sha256(raw).hexdigest())
               for component, members in payloads.items() for path, raw in sorted(members.items())])
    manifest_raw = _encoded(manifest)
    PackageView._validate_manifest(manifest,
        ComponentRef(package_id, resource_version, 'base', hashlib.sha256(manifest_raw).hexdigest()))
    with tempfile.TemporaryDirectory(prefix='xingtu-native-base-export-') as temporary:
        staged = Path(temporary) / 'package.zip'
        with zipfile.ZipFile(staged, 'x', compression=zipfile.ZIP_DEFLATED) as archive:
            archive.writestr('manifest.json', manifest_raw)
            for component, members in payloads.items():
                for path, raw in sorted(members.items()):
                    archive.writestr(f'data/{component}/{path}', raw)
        checked = _round_trip(staged, Path(temporary) / 'roundtrip-resources',
                              season=season, mode=mode)
        if checked['mapCount'] != map_count:
            _reject('roundtrip-map-inventory')
        with staged.open('rb') as handle:
            digest = hashlib.file_digest(handle, 'sha256').hexdigest()
        _publish_output(staged, output, digest)
        result = dict(output=str(output), bytes=output.stat().st_size, sha256=digest,
            packageId=package_id, resourceVersion=resource_version,
            sourceFiles=len(source.raw), sourceInventoryChecked=source.inventory is not None,
            files=len(manifest['files']), catalogActivated=False, **checked)
        if season == 's1':
            result.update(season=season, mode=mode,
                          sourceInventoryFiles=len(source.inventory) if source.inventory is not None else None)
        return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source-root', required=True, type=Path)
    parser.add_argument('--output', required=True, type=Path)
    parser.add_argument('--package-id', default='s0-nightmare-native')
    parser.add_argument('--resource-version', required=True)
    parser.add_argument('--source-inventory', type=Path)
    parser.add_argument('--season', choices=('s0', 's1'), default='s0')
    parser.add_argument('--mode', choices=('nightmare', 'difficult'), default='nightmare')
    args = parser.parse_args()
    print(json.dumps(export_package(args.source_root, args.output, package_id=args.package_id,
        resource_version=args.resource_version, source_inventory=args.source_inventory,
        season=args.season, mode=args.mode),
        ensure_ascii=False, sort_keys=True))


if __name__ == '__main__':
    main()
