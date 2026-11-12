"""S0 difficult data: native products and explicitly unbound legacy rivals.

This decoder does not enable the old S0 ranking path. Native proposals, legacy
working drafts and missing semantic passports remain distinct capabilities.
"""
from copy import deepcopy
from types import MappingProxyType, SimpleNamespace

import numpy as np

from difficult_entry_identity import MapEntryGeometryIdentity
from difficult_native_identity import NativeDifficultPack, _validate_imported_identity_ids
from difficult_side_entry_identity import MapLocalEntryIdentity
from imported_legacy_references import ImportedLegacyReferences
from imported_native_base import (
    SCHEMA, NativeBaseBundle, NativeBaseUnavailable, _canvas, _checked_reference_matches, _entry_rows,
    _identity_matrix, _number, _points, _reference, _reject, _rows, _text,
    _typed_declarations, _verify_typed_products,
)
from imported_resource_packages import PackageView
from imported_resource_reader import ComponentResourceReader
from imported_structure_declarations import MAX_CANVAS_AXIS
from nightmare_author_coordinate_adapter import AuthorCoordinateAdapters, CANONICAL_SCHEMA
from shared_structure_context import load_shared_version_context
from shared_structure_reference import SharedStructureResourceStore
import star_map_import as files


FLOORS = ('1', '2')
PROFILE = 'shared-structure'
LEGACY_PROFILE = 'zhan10-speed'
ENTRY_ROLE = 'native-proposals-with-legacy-working-drafts'
INTERFACES = frozenset(('identityPack', 'sourceManifest', 'typedIndex', 'coordinatePack',
    'neutralDisplay', 'identityEntries', 'identityProfile', 'entryEvidenceRole',
    'entryResourceAnchor', 'nativePassports', 'legacyClean'))


def _affine(value):
    if not isinstance(value, list) or len(value) != 3:
        _reject('legacy-matrix-invalid')
    for row in value:
        if not isinstance(row, list) or len(row) != 3:
            _reject('legacy-matrix-invalid')
        for number in row:
            _number(number, -MAX_CANVAS_AXIS, MAX_CANVAS_AXIS)
    if value[2] != [0, 0, 1]:
        _reject('legacy-matrix-invalid')
    matrix = np.asarray(value, dtype=float)
    if abs(np.linalg.det(matrix[:2, :2])) <= 1e-12:
        _reject('legacy-matrix-invalid')
    return matrix


def _coordinates(view, reader, paths, typed, canvases, profiles, legacy_bindings):
    document = reader.json(paths['coordinatePack'])
    legacy_ids = {key[1] for key in legacy_bindings}
    wanted_profiles = {PROFILE, LEGACY_PROFILE} if legacy_ids else {PROFILE}
    if (document.get('schema') != CANONICAL_SCHEMA
            or document.get('coordinateReferenceProfileId') != PROFILE
            or set(document.get('profiles', {})) != wanted_profiles
            or document.get('canonicalCrossFloorSearchCandidates') not in (None, {})):
        _reject('difficult-coordinate-contract-invalid')
    provider = document.get('referenceProvider', {})
    if (not _checked_reference_matches(view, provider.get('indexPath'), paths['typedIndex'], '.json')
            or provider.get('resourceRoot', '.') != '.'):
        _reject('coordinate-index-invalid')
    lookups = _rows(provider.get('lookupBindings'), set(typed), 'coordinate-lookup')
    for key, row in lookups.items():
        if (row.get('profile') != PROFILE or row.get('variant') != 'variant-01'
                or not _checked_reference_matches(view, row.get('resourcePath'), typed[key]['resourcePath'], '.npz')):
            _reject('coordinate-lookup-invalid')
    maps = document.get('maps', {})
    shared = document['profiles'][PROFILE].get('maps', {})
    if set(maps) != set(profiles) or set(shared) != set(profiles):
        _reject('coordinate-map-inventory-invalid')
    if legacy_ids and set(document['profiles'][LEGACY_PROFILE].get('maps', {})) != legacy_ids:
        _reject('legacy-coordinate-profile-inventory-invalid')
    legacy_by_floor = {(key[1], key[3]): canvas for key, canvas in legacy_bindings.items()}
    for mid in profiles:
        if set(maps[mid].get('floors', {})) != set(FLOORS) or set(shared[mid].get('floors', {})) != set(FLOORS):
            _reject('coordinate-floor-inventory-invalid')
        for floor in FLOORS:
            key = (mid, floor)
            row, bridge = maps[mid]['floors'][floor], shared[mid]['floors'][floor]
            legacy = legacy_by_floor.get(key)
            width, height = _canvas(canvases[key], shape_only=legacy is not None)
            product = legacy if legacy is not None else typed[key]
            reference_matches = (_reference(view, row.get('referenceResource'), '.npz') == product['resourcePath']
                if legacy is not None else
                _checked_reference_matches(view, row.get('referenceResource'), product['resourcePath'], '.npz'))
            if (not reference_matches
                    or row.get('coordinateWidth') != width or row.get('coordinateHeight') != height):
                _reject('coordinate-canvas-invalid')
            if legacy is None:
                _identity_matrix(bridge.get('matrixProfileToCanonical'))
                if 'matrixCanonicalToLegacyRecognition' in row:
                    _reject('native-legacy-coordinate-bridge-unexpected')
                continue
            if (row.get('referenceLayerRole') != 'legacy-clean-binary-only-not-native'
                    or row.get('coordinateSpace') != legacy['coordinateSpace']):
                _reject('legacy-coordinate-role-invalid')
            inverse = _affine(row.get('matrixCanonicalToLegacyRecognition'))
            for profile in (PROFILE, LEGACY_PROFILE):
                record = document['profiles'][profile]['maps'][mid].get('floors', {}).get(floor)
                if not isinstance(record, dict):
                    _reject('legacy-coordinate-floor-incomplete')
                matrix = _affine(record.get('matrixProfileToCanonical'))
                evidence = record.get('evidence', {})
                if (record.get('crossFloorProjectionAllowed') is not False
                        or record.get('usableForCurrentFrameRendering') is not True
                        or any(record.get(name) != legacy[name] for name in
                               ('structureVersionKey', 'sourceVersion', 'baseSnapshot'))
                        or evidence.get('authorizesMapIdentity') is not False
                        or evidence.get('authorizesStructureValidation') is not False
                        or evidence.get('sourceLayout') is not None
                        or evidence.get('staticAcceptance') != legacy['staticAcceptance']
                        or evidence.get('sourceCommit') != legacy['sourceCommit']
                        or not np.allclose(matrix @ inverse, np.eye(3), atol=1e-9, rtol=0)):
                    _reject('legacy-coordinate-evidence-invalid')
    return document


def _entry_policy(legacy_keys):
    def validate(key, row, main):
        if key in legacy_keys:
            if (row.get('sourceKind') != 'legacy-manually-reconstructed-clean-structure'
                    or row.get('sourceClass') != 'legacy-clean' or row.get('sourceLayout') is not None
                    or row.get('staticAcceptance') != 'working-draft-not-batch-accepted'
                    or row.get('rankStatus') != 'NOT_TESTED'
                    or any(row.get(name) not in (None, False) for name in ('authorizesPose', 'authorizesIdentity'))
                    or row.get('runtimeAuthorPixelsUsed') is not False):
                _reject('legacy-entry-role-invalid')
            return
        if row.get('knownWallSupportUnavailable') is not True or row.get('authorFeaturesUsed') is not False:
            _reject('difficult-entry-authority-invalid')
        if main:
            if (row.get('sourceKind') != 'game-entry-room-proposal' or row.get('kind') != 'main'
                    or row.get('proposalOnly') is not True or row.get('authorizesPose') is not False
                    or row.get('authorizesIdentity') is not False):
                _reject('proposal-entry-authority-invalid')
        else:
            if (row.get('sourceKind') != 'game-rendered-shared-structure'
                    or row.get('featureRole') != 'texture-footprint local-contour proposals; not known walls'
                    or not isinstance(row.get('gates'), list) or len(row['gates']) != 1):
                _reject('difficult-local-role-invalid')
            gate = row['gates'][0]
            if (gate.get('sourceKind') != 'game-script-static-side-door-spawn-marker'
                    or gate.get('semanticRole') != 'nearby exit marker only; not doorway endpoints or state'
                    or any(gate.get(name) is not False for name in
                           ('doorWidthKnown', 'sourceStateKnown', 'outwardDirectionKnown'))):
                _reject('difficult-static-marker-authority-invalid')
    return validate


def decode_s0_difficult(view):
    if not isinstance(view, PackageView):
        _reject('package-view-required')
    component = view.component
    if component.get('type') != 'recognition-base' or component.get('schemaVersion') != 1:
        _reject('component-type-invalid')
    if dict(season='s0', mode='difficult') not in component['contexts']:
        raise NativeBaseUnavailable('native-base-context-unsupported')
    if component['dependencies']:
        raise NativeBaseUnavailable('native-base-dependencies-unsupported')
    reader = ComponentResourceReader(view)
    context = reader.json(component['entrypoint'])
    if (set(context) != {'schema', 'season', 'mode', 'runtimeInterfaces'}
            or context.get('schema') != SCHEMA or (context.get('season'), context.get('mode')) != ('s0', 'difficult')):
        _reject('difficult-context-invalid')
    interfaces = context['runtimeInterfaces']
    if (not isinstance(interfaces, dict) or set(interfaces) - {'reviewedFrontGroups', 'fastStructureIndex'} != INTERFACES
            or interfaces.get('identityProfile') != PROFILE or interfaces.get('entryEvidenceRole') != ENTRY_ROLE
            or interfaces.get('entryResourceAnchor') != 'component-root'
            or interfaces.get('nativePassports') != 'unavailable'):
        _reject('difficult-interfaces-invalid')
    paths = {name: _reference(view, interfaces[name], '.json') for name in
             ('identityPack', 'sourceManifest', 'typedIndex', 'coordinatePack', 'neutralDisplay')}
    directory = interfaces['identityEntries']
    if not isinstance(directory, str) or files._relative(directory) != directory:
        _reject('entry-directory-invalid')
    entry_paths = {name: _reference(view, directory + '/' + name, '.json')
                   for name in ('entries.json', 'side-entries.json')}
    identity, source = reader.json(paths['identityPack']), reader.json(paths['sourceManifest'])
    _validate_imported_identity_ids(identity)
    if ((identity.get('schemaVersion'), identity.get('season'), identity.get('mode'), identity.get('floors'))
            != (1, 's0', 'difficult', list(FLOORS)) or len(identity['maps']) != 30
            or type(identity.get('defaultEntryFloor')) is not int
            or str(identity['defaultEntryFloor']) not in FLOORS):
        _reject('difficult-identity-context-invalid')
    source_rows = source.get('maps')
    if (source.get('schemaVersion') != 1 or source.get('mode') != 'difficult'
            or source.get('season') not in (None, 's0') or source.get('floors') != list(FLOORS)
            or not isinstance(source_rows, list) or not 29 <= len(source_rows) <= 30):
        _reject('difficult-native-source-invalid')
    ids = {row['id'] for row in identity['maps']}
    native_ids = {row.get('id') for row in source_rows if isinstance(row, dict)}
    if (len(native_ids) != len(source_rows) or not native_ids <= ids
            or any(source.get('identityProfiles', {}).get(mid) != PROFILE for mid in native_ids)):
        _reject('difficult-native-source-inventory-invalid')
    legacy_ids = ids - native_ids
    declaration = interfaces['legacyClean']
    if legacy_ids:
        if not isinstance(declaration, dict) or declaration.get('profileId') != LEGACY_PROFILE:
            _reject('legacy-declaration-invalid')
        legacy = ImportedLegacyReferences(view, declaration, expected_maps=legacy_ids, floors=FLOORS, reader=reader)
        legacy_bindings = legacy.bindings
    else:
        if declaration is not None:
            _reject('legacy-declaration-unexpected')
        legacy, legacy_bindings = None, {}
    legacy_keys = {(key[1], key[3]) for key in legacy_bindings}
    pending = _rows(source.get('pendingOriginalBindings', []), legacy_keys, 'native-pending-bindings')
    for row in pending.values():
        _text(row.get('reason'))
    profiles = {mid: PROFILE if mid in native_ids else LEGACY_PROFILE for mid in ids}
    if identity.get('identityProfiles') != profiles:
        _reject('difficult-identity-profile-inventory-invalid')
    gaps = []
    for row in identity['maps']:
        for name in ('name', 'direction', 'shape'):
            _text(row.get(name))
        if (set(row['layers']) != set(FLOORS) or row.get('identityProfileId') != profiles[row['id']]
                or row.get('routeProfileId') != PROFILE or row.get('oneFlow') is not None):
            _reject('identity-layer-contract-invalid')
        if row['id'] in legacy_ids and (row.get('sourceLayout') is not None
                or row.get('sourceClass') != 'legacy-clean' or row.get('rankStatus') != 'NOT_TESTED'):
            _reject('legacy-identity-role-invalid')
        for floor, path in row['layers'].items():
            if path is None:
                gaps.append('native-layer-unavailable:' + row['id'] + '/' + floor)
            else:
                _reference(view, path, '.npz')
        crop = row.get('sideDoorCrop', row.get('previewBox', [0, 0, 0, 0]))
        if not isinstance(crop, list) or len(crop) != 4:
            _reject('identity-crop-invalid')
        for value in crop:
            _number(value, 0, MAX_CANVAS_AXIS)
    expected = {(mid, floor) for mid in native_ids for floor in FLOORS}
    typed, registry_path, neutral, canvases = _typed_declarations(view, reader, paths, expected, PROFILE,
        [row for row in identity['maps'] if row['id'] in native_ids])
    products = dict(typed)
    for key, canvas in legacy_bindings.items():
        binding = (key[1], key[3])
        canvases[binding] = canvas
        products[binding] = canvas
        row = next(row for row in identity['maps'] if row['id'] == key[1])
        if row['layers'][key[3]] is not None and row['layers'][key[3]] != canvas['resourcePath']:
            _reject('legacy-identity-reference-mismatch')
    coordinates = _coordinates(view, reader, paths, typed, canvases, profiles, legacy_bindings)
    if neutral.get('resourceRoot', '.') != '.':
        _reject('resource-root-invalid')
    for document in (identity, coordinates):
        if 'structureVersionBindings' in document and _reference(view, document['structureVersionBindings']) != registry_path:
            _reject('version-registry-invalid')
    policy = _entry_policy(legacy_keys)
    options = dict(mode='difficult', profile=PROFILE, reference_anchor='component-root',
        profile_for_map=profiles.__getitem__, legacy_keys=legacy_keys, validate_semantics=policy)
    main_raw, local_raw = reader.json(entry_paths['entries.json']), reader.json(entry_paths['side-entries.json'])
    _entry_rows(main_raw, {(mid, '1') for mid in ids}, canvases, products, view, directory,
                paths['typedIndex'], main=True, **options)
    _entry_rows(local_raw, {(mid, floor) for mid in ids for floor in FLOORS}, canvases, products,
                view, directory, paths['typedIndex'], main=False, **options)
    passports = None
    if legacy is not None:
        path = _reference(view, declaration.get('passports'), '.json')
        passports = reader.json(path)
        if (passports.get('schemaVersion'), passports.get('mode'), passports.get('profileId')) != (1, 'difficult', LEGACY_PROFILE):
            _reject('legacy-passport-context-invalid')
        # A floor can contain several glyph passports, or none. Coverage is
        # per floor; individual passports are per source entry, not per floor.
        MapLocalEntryIdentity._bound_passports(passports)
        seen = set()
        for row in passports['entries']:
            key = (row.get('mapId'), row.get('floor'))
            if key not in legacy_keys:
                _reject('legacy-passport-binding-invalid')
            canvas = canvases[key]
            identity = (*key, row.get('sourceEntryId'))
            _text(identity[2])
            if (identity in seen or row.get('sourceCommit') != canvas['sourceCommit']
                    or _reference(view, declaration['resourceRoot'] + '/' + row.get('structureResource', ''), '.npz')
                    != canvas['resourcePath']):
                _reject('legacy-passport-binding-invalid')
            seen.add(identity)
            width, height = _canvas(canvas, shape_only=True)
            _points([row.get('center')], 1, width, height, exact=1)
        _rows(passports.get('coverage'), legacy_keys, 'legacy-passport-coverage')
    pack = NativeDifficultPack(view.root, paths['identityPack'], expected_mode='difficult',
                               expected_season='s0', read_resource=reader)
    resources = SharedStructureResourceStore(view.path(paths['typedIndex']), view.root, read_resource=reader)
    if resources.error is not None or len(resources.entries) != len(expected):
        _reject('typed-store-invalid')
    adapters = AuthorCoordinateAdapters(view.path(paths['coordinatePack']), read_resource=reader)
    for mid in ids:
        for floor in FLOORS:
            wanted = np.asarray(coordinates['profiles'][PROFILE]['maps'][mid]['floors'][floor]['matrixProfileToCanonical'])
            actual = adapters.matrix_profile_to_canonical(PROFILE, mid, floor)
            if actual is None or not np.array_equal(actual, wanted):
                _reject('difficult-canvas-adapter-invalid')
    entry_root = view.root / directory
    main = MapEntryGeometryIdentity(entry_root, structure_store=resources, profile_id=PROFILE,
        profile_for_map=pack.identity_profiles.__getitem__, read_resource=reader)
    local = MapLocalEntryIdentity(entry_root, mode='difficult', profile_id=PROFILE, floors=FLOORS,
        structure_store=resources, profile_for_map=pack.identity_profiles.__getitem__, read_resource=reader,
        typed_index_path=view.path(paths['typedIndex']), semantic_side_entries=False, passport_source_available=False)
    if (main.error is not None or local.side_door_error is not None or local.side_search_error is not None
            or len(main.entries) != len(ids) or len(local.entries) != len(ids) * len(FLOORS)):
        _reject('entry-loader-invalid')
    if passports is not None:
        local.passports.extend(deepcopy(passports['entries']))
        local.entry_coverage.extend(deepcopy(passports['coverage']))
    version_pack = SimpleNamespace(by_id={row['id']: row for row in source_rows}, floors=FLOORS,
        identity_profiles={mid: PROFILE for mid in native_ids}, manifest_path=view.path(paths['sourceManifest']))
    versions = load_shared_version_context(view.root, version_pack, resources, adapters, None, local.entries,
        neutral_relative=paths['neutralDisplay'], entry_root_relative='', read_resource=reader)
    if set(versions) != set(resources.entries) or any(row['sharedError'] is not None for row in versions.values()):
        _reject('version-binding-invalid')
    _verify_typed_products(view, reader, resources, versions)
    result = NativeBaseBundle(view, pack=pack, resources=resources, adapters=adapters,
        versions=MappingProxyType(versions), interfaces=MappingProxyType(deepcopy(interfaces)), reader=reader,
        season='s0', mode='difficult', component_ref=view.component_ref, entry_identity=main,
        side_entry_identity=local, exit_recognizer=None, legacy_references=legacy,
        legacy_passports=deepcopy(passports), native_source=version_pack,
        semantic_side_entries=False, passport_source_available=False, rank_ready=False, gaps=tuple(gaps))
    result.assert_current()
    return result
