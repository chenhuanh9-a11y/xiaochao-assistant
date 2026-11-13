"""Guarded data assembly for the original Qt/Python algorithms.

No recognition, candidate policy, fit or numerical conversion lives here. The
archive retains its original resource layout and coordinates; the existing
PackageView reader owns every payload. Author images remain separate displays.
"""
from pathlib import Path
from io import BytesIO
from copy import deepcopy
from types import MappingProxyType, SimpleNamespace
import json

import numpy as np

from imported_resource_reader import ComponentResourceReader
from imported_resource_packages import FileRef
from imported_native_base import (NativeBaseBundle, NativeBaseUnavailable,
    _bounded_list, _bounded_points, MAX_METADATA_BYTES)
from imported_native_context import ImportedNativeContext
from shared_structure_context import load_shared_version_context
from shared_structure_reference import SharedStructureResourceStore
from nightmare_author_coordinate_adapter import AuthorCoordinateAdapters
import star_map_import as files


SCHEMA = 'xingtu-original-qt-base-v1'
CAPABILITY = 'original-qt-resources-v1'
PRIVATE_FILES = ('s1_entrance_coordinate_index.json', 'template_manifest.json', 'native_glyph_template.png')


class OriginalResourceReadFailure(NativeBaseUnavailable):
    resource_failure = True

    def __init__(self):
        super().__init__('original-entry-index-unavailable')


def _preflight_original_entries(reader, interfaces):
    """Existing imported-data limits, before the original numeric constructors."""
    directory = interfaces['identityEntries']
    typed = reader.json(interfaces['typedIndex'])
    _bounded_list(typed['entries'],1024,'typed bindings')
    main = reader.json(directory+'/entries.json')
    total = 0
    for item in _bounded_list(main['entries'],256,'main entries'):
        chains = _bounded_list(item['chains'],512,'main chains')
        outline = _bounded_points(item['outline'],256,'main outline')
        total += len(chains)
        if total > 65536 or not outline or len(chains)*len(outline) > 131072:
            raise ValueError('entry-resource-complexity: main distances')
        for chain in chains:
            if (len(_bounded_points(chain['points'],3,'main chain points')) != 3
                    or len(_bounded_points(chain['rays'],2,'main chain rays')) != 2):
                raise ValueError('invalid entry chain shape')
    local = reader.json(directory+'/side-entries.json')
    total = 0
    for item in _bounded_list(local['entries'],256,'side entries'):
        turns = _bounded_list(item['turns'],1024,'side turns')
        total += len(turns)
        if total > 65536:
            raise ValueError('entry-resource-complexity: side turns total')
        for turn in turns:
            _bounded_points([turn['point']],1,'side turn point')
            if len(_bounded_points(turn['rays'],2,'side turn rays')) != 2 or type(turn['next']) is not int:
                raise ValueError('invalid side turn shape')
        for gate in _bounded_list(item.get('gates',[]),64,'side gates'):
            _bounded_points([gate['center']],1,'side gate center')
    relative = directory+'/side-door-anchors.json'
    if relative in reader.view.inventory:
        doors = reader.json(relative)
        for door in _bounded_list(doors['entries'],256,'side doors'):
            _bounded_points([door['boundaryCenter']],1,'side-door center')
    def walls(value,maximum,label):
        for line in _bounded_list(value,maximum,label):
            if any(isinstance(line[key],(list,dict)) for key in ('axis','sign','normal','lo','hi')):
                raise ValueError('invalid entry wall coordinates: '+label)
    passports = {directory+'/entry-passports.json'}
    if interfaces.get('legacyClean'):
        passports.add(interfaces['legacyClean']['passports'])
    for relative in passports:
        if relative not in reader.view.inventory:
            continue  # Original algorithms allow genuinely absent optional data.
        value = reader.json(relative)
        _bounded_list(value['coverage'],256,'passport coverage')
        for item in _bounded_list(value['entries'],1024,'passports'):
            _bounded_points([item['center']],1,'passport center')
            walls(item['knownWallLines'],256,'passport walls')
            for room in (_bounded_list(item['localInstances'],128,'passport instances')
                         + _bounded_list(item.get('optionalRooms',[]),64,'optional rooms')):
                _bounded_points(room['outline'],256,'passport outline')
                walls(room.get('knownWallLines',[]),256,'instance walls')
                for port in _bounded_list(room.get('ports',[]),64,'instance ports'):
                    _bounded_points(port['points'],64,'port points')
    relative = directory+'/side-entry-search.npz'
    if relative in reader.view.inventory:
        from imported_array_archive import validate_npz_bytes
        from imported_resource_reader import decode_resource_json
        raw = reader(reader.view.path(relative))
        headers = validate_npz_bytes(raw)
        header = headers.get('metadata.npy')
        if header is None or header[0] != () or header[1].kind != 'U' or header[1].itemsize > MAX_METADATA_BYTES:
            raise ValueError('invalid side search metadata')
        with np.load(BytesIO(raw),allow_pickle=False) as archive:
            value = decode_resource_json(str(archive['metadata'].item()).encode('utf8'))
        for row in _bounded_list(value['entries'],256,'side search records'):
            for name in ('points','rays','pairs'):
                header = headers.get(row['key']+'_'+name+'.npy')
                if header is None or not header[0] or header[0][0] > 1024:
                    raise ValueError('entry-resource-complexity: side search graph')
    return local


def require_original_base(view, *, season, mode):
    reader = ComponentResourceReader(view)
    context = reader.json(view.component['entrypoint'])
    if context.get('schema') != SCHEMA:
        raise NativeBaseUnavailable('original-qt-base-required')
    if (context.get('season'), context.get('mode')) != (season, mode):
        raise NativeBaseUnavailable('original-qt-base-context-mismatch')
    if set(context) != {'schema', 'season', 'mode', 'originalManifest', 'runtimeInterfaces'}:
        raise NativeBaseUnavailable('original-qt-base-context-invalid')
    return reader, context


class OriginalFastStructureSource:
    """Read the original prepared files through their actual package owner."""
    def __init__(self, view, reader, versions):
        self.view, self.reader, self.root = view, reader, view.root
        entries, gaps = {}, []
        for key, version in versions.items():
            relative = 'references/fast-structure/' + version['path'].stem + '-fast.npz'
            if relative in view.inventory:
                entries[key] = FileRef(view.component_ref, relative)
            else:
                gaps.append('fast-prepared-not-provided:' + key[1] + '/' + key[3])
        self.entries, self.gaps = MappingProxyType(entries), tuple(gaps)
        self.reference_keys = tuple(versions)
        from fast_structure_alignment import _prepared, _prepared_lock
        self._prepared, self._prepared_lock = _prepared, _prepared_lock
        self._prepared_owner = None
        self._prepared_proofs = {}

    def assert_current(self, cancelled=None):
        files._check_cancelled(cancelled)
        if (self.view.root != self.root or self.reader.view is not self.view
                or self.reader.root != self.root):
            raise NativeBaseUnavailable('original-qt-base-component-root-changed')

    def _read_state(self):
        return (id(self.view),id(self.reader),self.root,id(self.entries),id(self.reference_keys),
            id(self.reader.view),self.view.component_ref,self.reader.view.component_ref,self.reader.root,
            id(self._prepared),id(self._prepared_lock))

    @staticmethod
    def _prepared_arrays(entry):
        from structure_cache_memory import reference_arrays
        reference,prepared = entry[:2]
        return (*reference_arrays(reference),prepared.distance,prepared.chains,prepared.rays)

    @staticmethod
    def _value_state(prepared):
        return (prepared.shape,prepared.pitch,tuple((id(a),a.shape,a.dtype.str,a.flags.writeable)
            for a in (prepared.distance,prepared.chains,prepared.rays)))

    def _managed_reference(self, reference):
        from native_structure_display_material import owns_native_arrays
        owner_ref = getattr(reference,'_display_owner',None)
        owner = owner_ref() if owner_ref is not None else None
        if owner is None or not owns_native_arrays(reference,owner):
            return False
        binding = getattr(owner,'_managed_display_context',None)
        engine = binding[0]() if binding is not None else None
        return (engine is not None and getattr(engine,'qt_fast_structure_source',None) is self
            and owner._assert_managed_display_current(engine))

    def _clear_prepared(self):
        with self._prepared_lock:
            self._discard_prepared_locked()
            self._prepared_owner = None

    def _discard_prepared_locked(self):
        for key in tuple(self._prepared):
            if key[:2] == ('imported-original',id(self)):
                self._prepared.pop(key)
        self._prepared_proofs.clear()

    def read(self, reference, *, cancelled=None, strict=False):
        files._check_cancelled(cancelled)
        state = self._read_state()
        try:
            managed = not strict and self._managed_reference(reference)
        except (OSError,ValueError):
            self._clear_prepared()
            raise
        owner_ref = getattr(reference,'_display_owner',None)
        owner = owner_ref() if owner_ref is not None else None
        if (not strict and owner is not None and owner._managed_display_context is not None
                and not managed):
            self._clear_prepared()
            raise NativeBaseUnavailable('original-qt-base-resource-owner-changed')
        metadata = reference.metadata
        cache_key = ('imported-original',id(self),id(reference))
        with self._prepared_lock:
            if self._prepared_owner != state:
                self._discard_prepared_locked()
                self._prepared_owner = state
            for key in tuple(self._prepared_proofs):
                if key not in self._prepared:
                    self._prepared_proofs.pop(key)
            entry = self._prepared.get(cache_key) if managed else None
            proof = self._prepared_proofs.get(cache_key)
            if entry is not None and proof is not None:
                if (entry[0] is reference and proof[0] == metadata
                        and proof[1] == (reference.path,reference.resource_root)
                        and proof[2] == self._value_state(entry[1])
                        and self._read_state() == state and self._managed_reference(reference)):
                    self._prepared.move_to_end(cache_key)
                    files._check_cancelled(cancelled)
                    return entry[1]
                self._prepared.pop(cache_key)
                self._prepared_proofs.pop(cache_key)
        try:
            self.assert_current(cancelled)
        except (OSError,ValueError):
            self._clear_prepared()
            raise
        binding = reference.metadata['binding']
        key = (binding['profile'], binding['mapId'], binding['variant'], binding['floor'])
        ref = self.entries.get(key)
        if strict:
            try:
                self.reader(reference.path)
            except (OSError,ValueError):
                self._clear_prepared()
                raise
        if ref is None:
            return None
        from imported_fast_structure import decode_prepared
        try:
            value = decode_prepared(self.reader(self.view.path(ref.path)), reference)
            self.assert_current(cancelled)
        except (OSError,ValueError):
            self._clear_prepared()
            raise
        if managed and self._read_state() == state and self._managed_reference(reference):
            from fast_structure_alignment import PreparedStructure, PREPARED_CACHE_MAX_BYTES
            from native_structure_display_material import _immutable
            from structure_cache_memory import trim_prepared_cache
            value = PreparedStructure(*(_immutable(getattr(value,name))
                for name in ('distance','chains','rays')),value.pitch,value.shape)
            proof = (deepcopy(metadata),(reference.path,reference.resource_root),self._value_state(value))
            with self._prepared_lock:
                self._prepared[cache_key] = (reference,value)
                self._prepared_proofs[cache_key] = proof
                self._prepared.move_to_end(cache_key)
                trim_prepared_cache(self._prepared,max_entries=4,max_bytes=PREPARED_CACHE_MAX_BYTES,
                    arrays_for=self._prepared_arrays)
        return value

    def validate_all(self, provider, *, compare_derived=False, cancelled=None):
        self._clear_prepared()
        for key in self.reference_keys:
            self.assert_current(cancelled)
            reference = provider(key[0], key[1], key[3])
            if reference is None:
                raise NativeBaseUnavailable('original-qt-base-reference-unavailable')
            prepared = self.read(reference, cancelled=cancelled, strict=True)
            if prepared is not None and compare_derived:
                # Explicit admission/integrity work only. Ordinary frame reads
                # retain the original prepared bytes and original solver.
                from fast_structure_alignment import prepare
                actual = prepare(reference.clean_mask)
                self.assert_current(cancelled)
                differences = [name for name in ('distance','chains','rays')
                    if not np.array_equal(getattr(prepared,name),getattr(actual,name))]
                differences += [name for name in ('shape','pitch') if getattr(prepared,name) != getattr(actual,name)]
                if differences:
                    raise NativeBaseUnavailable('original-qt-base-fast-derived-mismatch:'+key[1]+'/'+key[3]
                        + ':'+','.join(differences))
                self.read(reference, cancelled=cancelled, strict=True)
        self.assert_current(cancelled)


def decode_original_base(view, *, season='s0', mode='nightmare'):
    reader, context = require_original_base(view, season=season, mode=mode)
    root, interfaces = view.root, dict(context['runtimeInterfaces'])
    for name in ('identityPack', 'typedIndex', 'coordinatePack', 'neutralDisplay', 'identityEntries'):
        value = interfaces.get(name)
        if not isinstance(value, str) or files._relative(value) != value:
            raise NativeBaseUnavailable('original-qt-base-interface-invalid')
    local = _preflight_original_entries(reader,interfaces)
    original = reader.json(context['originalManifest'])
    identity = reader.json(interfaces['identityPack'])
    if identity.get('season', season) != season:
        raise NativeBaseUnavailable('original-qt-base-identity-season-mismatch')
    if season == 's0' and mode == 'nightmare':
        from nightmare_candidate import NightmarePack
        from difficult_native_identity import _validate_imported_identity_ids
        _validate_imported_identity_ids(dict(identity, floors=list(NightmarePack.floors)))
        if any(row.get('resourceBase') != 'resource-root' for row in identity['maps']):
            raise NativeBaseUnavailable('original-qt-base-external-path-unsupported')
        pack = NightmarePack(view.path(interfaces['identityPack']), read_resource=reader)
        version_pack = pack
    else:
        from difficult_native_identity import NativeDifficultPack
        pack = NativeDifficultPack(root, interfaces['identityPack'], expected_mode=mode,
            expected_season=season if season == 's1' else None, read_resource=reader)
        source = reader.json(interfaces['sourceManifest']) if season == 's0' else identity
        version_pack = SimpleNamespace(by_id={row['id']:row for row in source['maps']},
            floors=tuple(map(str, source['floors'])), identity_profiles=source['identityProfiles'],
            manifest_path=view.path(interfaces['sourceManifest'] if season == 's0' else interfaces['identityPack']))
    resources = SharedStructureResourceStore(view.path(interfaces['typedIndex']), root, read_resource=reader)
    if resources.error:
        raise NativeBaseUnavailable('original-qt-base-typed-index-unavailable')
    adapters = AuthorCoordinateAdapters(view.path(interfaces['coordinatePack']), read_resource=reader)
    versions = load_shared_version_context(root, version_pack, resources, adapters, None,
        [(row,) for row in local['entries']], neutral_relative=interfaces['neutralDisplay'],
        entry_root_relative='' if (season,mode) == ('s0','difficult') else interfaces['identityEntries'],
        read_resource=reader)
    if not versions or any(row['sharedError'] for row in versions.values()):
        raise NativeBaseUnavailable('original-qt-base-version-binding-invalid')
    # Metadata alone cannot qualify a layer. Decode every declared typed
    # product, including layers which have no optional fast preparation.
    # Use the runtime's original decoder and the same reference binding proof.
    for key, version in versions.items():
        if view.root != root:
            raise NativeBaseUnavailable('original-qt-base-component-root-changed')
        reference = resources.load(key[0],key[1],key[3],variant=key[2])
        if (reference is None or not np.any(reference.clean_mask)
                or reference.path.resolve() != version['path']
                or list(reference.shape) != version['canvas']['shapeHW']
                or reference.metadata.get('canvas') != version['canvas']
                or any(reference.metadata.get(name) != value
                       for name,value in version['expected'].items())):
            raise NativeBaseUnavailable('original-qt-base-typed-product-invalid')
        if view.root != root:
            raise NativeBaseUnavailable('original-qt-base-component-root-changed')
    legacy = None
    if season == 's0' and mode == 'difficult' and interfaces.get('legacyClean'):
        from imported_legacy_references import ImportedLegacyReferences
        declaration = interfaces['legacyClean']
        legacy = ImportedLegacyReferences(view, declaration,
            expected_maps=declaration['mapIds'], floors=pack.floors, reader=reader)
    wanted = {(item.id, str(floor)) for item in pack.maps for floor in pack.floors}
    actual = {(key[1],key[3]) for key in versions}
    if legacy is not None:
        actual.update((key[1],key[3]) for key in legacy.bindings)
    if actual != wanted:
        raise NativeBaseUnavailable('original-qt-base-layer-inventory-incomplete')
    # Original controls and private coordinate data are independent resources.
    # Validate ownership now; the original observers still own all decisions.
    if season == 's1':
        for name in PRIVATE_FILES:
            reader(view.path('s1-private-assets/' + name))
    bundle = NativeBaseBundle(view, pack=pack, resources=resources, adapters=adapters,
        versions=MappingProxyType(versions), interfaces=MappingProxyType(interfaces), reader=reader,
        season=season, mode=mode, component_ref=view.component_ref,
        entry_identity=None, side_entry_identity=None, exit_recognizer=None,
        legacy_references=legacy, legacy_passports=None, native_source=version_pack,
        semantic_side_entries=season == 's0', passport_source_available=True,
        rank_ready=True, gaps=())
    bundle._values['fast_structure_source'] = OriginalFastStructureSource(view, reader, versions)
    bundle._values['original_qt'] = True
    bundle._values['original_manifest'] = original
    bundle.assert_current()
    return bundle


class OriginalNativeContext(ImportedNativeContext):
    """Original native data capabilities, guarded by the active B/C views."""
    def __init__(self, view, bundle, controls):
        from imported_controls_profile import S0ControlsProfile
        from imported_s1_controls_profile import S1ControlsProfile
        def reject(reason):
            raise NativeBaseUnavailable('original-qt-' + reason)
        super().__init__(view, bundle, controls, season=bundle.season,
            modes=(bundle.mode,), controls_type=S1ControlsProfile if bundle.season == 's1'
            else S0ControlsProfile, reject=reject)
        self._bridges = {}
        if (bundle.season,bundle.mode) == ('s0','difficult'):
            from difficult_native_context import DifficultNativeContext
            document = bundle.reader.json(bundle.interfaces['coordinateBridges'])
            for map_id,item in document['maps'].items():
                for floor,row in item['floors'].items():
                    self._bridges[map_id,str(floor)] = DifficultNativeContext.matrix(row['matrixLegacyRouteToNative'])

    manifest = property(lambda s:(s.assert_current(),s._bundle._values['original_manifest'])[1])
    version_pack = property(lambda s:s._bundle.native_source)
    legacy_bindings = property(lambda s:s._bundle.legacy_references.bindings
        if s._bundle.legacy_references is not None else {})
    unbound_layers = ()

    @property
    def live_gaps(self):
        self.assert_current()
        return ('s1-floor-controls-unregistered',) if self.season == 's1' else ()

    @property
    def floor_observation_capability(self):
        self.assert_current()
        if self.season != 's1':
            return {}
        from s1_floor_observation import CAPABILITY, SOURCE_REPLAY_FLOORS, ORDER
        return dict(contract=CAPABILITY, manualMapObservation=True,
            automaticFloors=list(ORDER), contractFloors=list(ORDER),
            sourceReplayFloors=list(SOURCE_REPLAY_FLOORS),
            syntheticOnlyFloors=[floor for floor in ORDER if floor not in SOURCE_REPLAY_FLOORS],
            evidence='private-test-four-floor-contract', liveValidated=False)

    @property
    def structure_candidate_capability(self):
        self.assert_current()
        if self.season != 's1':
            return {}
        from s1_native_resources import S1_RECORDED_CANDIDATE_CAPABILITY
        return dict(S1_RECORDED_CANDIDATE_CAPABILITY)

    def local(self, relative):
        self.assert_current()
        return self._base_view.path(relative)

    def has_reference_binding(self, map_id, floor):
        self.assert_current()
        return any(key[1] == str(map_id) and key[3] == str(floor)
                   for key in (*self.versions, *self.legacy_bindings))

    def legacy_reference(self, map_id, floor):
        self.assert_current()
        source = self._bundle.legacy_references
        return source.reference(map_id, floor) if source is not None else None

    def legacy_route_to_native(self, map_id, floor):
        self.assert_current()
        return self._bridges.get((str(map_id),str(floor)))

    def attach_legacy_entry_metadata(self, locator):
        declaration = self.interfaces.get('legacyClean')
        if declaration is None or locator is None:
            return
        payload = self.reader.json(declaration['passports'])
        if any(row['mapId'] not in declaration['mapIds'] for row in payload['entries'] + payload['coverage']):
            raise NativeBaseUnavailable('original-qt-base-legacy-scope-invalid')
        locator.passports.extend(payload['entries'])
        locator.entry_coverage.extend(payload['coverage'])
        locator.legacy_passport_source = str(self.local(declaration['passports']))

    def display_reference(self, map_id, floor='-1'):
        self.assert_current()
        return None

    def rendered_display_layer(self, map_id, floor, reference):
        self.assert_current()
        from s1_native_display import NativeDisplayResult
        return NativeDisplayResult(None, 's1-native-display-not-installed')
