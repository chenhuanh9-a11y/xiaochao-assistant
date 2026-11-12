"""Prepare bounded, immutable RAM inputs for the existing structure solvers.

Only resource preparation reads resources. Returned inputs contain no disk reader,
catalog guard or publication permission. The runtime owns resource revisions,
cancellation and result submission. Factory-prepared models and compressed
layer payloads deliberately retain their admitted RAM.
Explicit resource preparation/integrity checks can discover later disk damage.
"""
from __future__ import annotations

from collections import OrderedDict
from collections.abc import Mapping
from copy import copy
from dataclasses import dataclass, field, replace
from io import BytesIO
import os
from pathlib import Path
import threading
import time
from types import MappingProxyType
from weakref import WeakValueDictionary
import zipfile
import zlib

import numpy as np

from fast_structure_alignment import PreparedStructure
from imported_native_base import NativeBaseBundle
from imported_resource_packages import FileRef
from shared_structure_reference import SharedStructureReference, load_shared_structure
import star_map_import as files


DEFAULT_CACHE_BYTES = 512 * 1024 * 1024


class _FrozenDict(dict):
    """Keep dict-compatible legacy query semantics without writable metadata."""
    def _readonly(self, *args, **kwargs):
        raise TypeError("prepared-pose-metadata-is-read-only")

    __setitem__ = __delitem__ = clear = pop = popitem = setdefault = update = __ior__ = _readonly

    def __copy__(self):
        return self

    def __deepcopy__(self, memo):
        return self


def _array(value):
    if value is None:
        return None
    value = np.asarray(value)
    # A view backed by bytes cannot be made writable with setflags().
    return np.frombuffer(value.tobytes(order="C"), dtype=value.dtype).reshape(value.shape)


def _freeze(value):
    if isinstance(value, np.ndarray):
        return _array(value)
    if isinstance(value, Mapping):
        return _FrozenDict({key: _freeze(item) for key, item in value.items()})
    if isinstance(value, (list, tuple)):
        return tuple(_freeze(item) for item in value)
    return value


def _path_key(path):
    # Lexical normalization only. Query-time path identity never touches disk.
    return os.path.normcase(os.path.abspath(os.fspath(path)))


def _bytes_used(value, seen=None):
    seen = set() if seen is None else seen
    if id(value) in seen:
        return 0
    seen.add(id(value))
    if isinstance(value, np.ndarray):
        return value.nbytes
    if isinstance(value, bytes):
        return len(value)
    if isinstance(value, Mapping):
        return sum(_bytes_used(item, seen) for item in value.values())
    if isinstance(value, (tuple, list)):
        return sum(_bytes_used(item, seen) for item in value)
    if hasattr(value, "__dict__"):
        return _bytes_used(vars(value), seen)
    return 0


class PoseInputsUnavailable(ValueError):
    """A real resource/preparation gap, without identity exclusion authority."""


class _RecordingReader:
    def __init__(self, reader):
        self.reader, self.payloads = reader, {}
        self._root = reader.root

    def __call__(self, path):
        # Keep semantic preflight inside the component read scope. In
        # particular, the existing batch reader attaches the actual FileRef to
        # NPZ-header failures; optional consumers must not guess their origin.
        return self.read_from(path, read_resource=lambda candidate:
            self.reader.read_many((candidate,), expected_root=self.reader.root)[0])

    def read_from(self, path, *, read_resource):
        """Record a batch/failure reader's original preflighted result once."""
        key = _path_key(path)
        previous = self.payloads.get(key)
        if previous is not None:
            return previous[1]
        raw = read_resource(path)
        self.payloads[key] = (Path(path), bytes(raw))
        return raw

    def record_admitted(self, paths, *, cancelled=None):
        """Recheck backing bytes for models admitted under this exact manifest.

        This is only for static declarations already decoded by the bundle.
        Optional/unadmitted inputs retain __call__ and its semantic preflight.
        """
        paths = tuple(Path(path) for path in paths if _path_key(path) not in self.payloads)
        relatives = tuple(self.reader.relative_path(path) for path in paths)
        payloads = self.reader.view.read_many(relatives, expected_root=self._root,
                                              cancelled=cancelled)
        for path, raw in zip(paths, payloads):
            self.payloads[_path_key(path)] = (path, raw)

    def recheck(self, *, cancelled=None):
        if self.reader.root != self._root:
            raise PoseInputsUnavailable("pose-input-resource-binding-changed")
        recorded = tuple(self.payloads.values())
        relatives = tuple(self.reader.relative_path(path) for path, _ in recorded)
        # Admission or the first preparation read already checked semantics.
        # Re-read every actual file under the original byte/manifest/selection
        # checks; exact byte equality needs no further JSON/NPZ decode.
        current = self.reader.view.read_many(relatives, expected_root=self._root,
                                             cancelled=cancelled)
        for (_, raw), actual in zip(recorded, current):
            if actual != raw:
                raise PoseInputsUnavailable("pose-input-bytes-changed-during-preparation")


@dataclass(frozen=True)
class _RamReader:
    payloads: Mapping

    def __call__(self, path):
        try:
            return self.payloads[_path_key(path)]
        except KeyError as error:
            raise PoseInputsUnavailable("pose-input-not-prepared:" + os.fspath(path)) from error


@dataclass(frozen=True)
class PoseInputBinding:
    season: str
    mode: str
    component_ref: object
    resource_revision: int
    engine_identity: int
    base_identity: int
    provider_identity: int
    source_identity: int


@dataclass(frozen=True)
class PreparedCoordinates:
    canonical_pack_id: str
    matrices: Mapping

    def matrix_profile_to_canonical(self, profile, map_id, floor):
        return self.matrices.get((str(profile), str(map_id), str(floor)))


@dataclass(frozen=True)
class PoseIndexSource:
    """The existing S1 coordinate-prior source, separate from grid proposals."""
    path: Path
    expected_sha256: str
    mode: str
    resource_key: str


@dataclass(frozen=True)
class PreparedCoordinatePrior:
    mode: str
    resource_key: str
    index_sha256: str
    matrices: Mapping

    def _native_to_common(self, map_id, floor):
        return self.matrices[(str(map_id), str(floor))]

    def make_hint(self, source, *, target_floor, current_owner_scope):
        from s1_pose_prior import CoordinatePriorIndex
        # Reuse the original numerical and acknowledged-pose policy.
        return CoordinatePriorIndex.make_hint(self, source, target_floor=target_floor,
                                             current_owner_scope=current_owner_scope)


@dataclass(frozen=True)
class _ReferenceTable:
    layers: Mapping

    def __call__(self, profile, map_id, floor):
        # The real SharedReferenceProvider also selects a unique map/floor;
        # profile is not an alternate author-image resource namespace.
        key = (str(map_id), str(floor))
        if key not in self.layers:
            raise PoseInputsUnavailable("pose-input-layer-not-prepared:" + "/".join(key))
        return self.layers[key]

    def load(self, profile, map_id, floor, *, variant="variant-01", variant_id=None, route_shape=None):
        if (variant_id or variant) != "variant-01":
            return None
        value = self(profile, map_id, floor)
        return value if value is None or route_shape is None or value.shape == tuple(route_shape) else None


@dataclass(frozen=True)
class _MapInfo:
    id: str
    name: str


@dataclass(frozen=True)
class _Pack:
    pack_id: str
    mode: str
    floors: tuple
    maps: tuple
    by_id: Mapping
    identity_profiles: Mapping


@dataclass(frozen=True)
class _LocatorTemplates:
    main: object
    side: object

    def new_queries(self):
        # Original algorithms retain per-query observation caches. Static arrays
        # and payloads are shared immutable RAM, never the live engine's caches.
        main, side = copy(self.main), copy(self.side)
        main._observation, main._wall_lines = None, {}
        side.geometry_provider, side.author_visible_provider = main.geometry, main.author_visible
        if side._exit_recognizer is not None:
            side._exit_recognizer = copy(side._exit_recognizer)
        return main, side


class _PreparedGridQuery:
    def __init__(self, entries, arrays, descriptors, gaps=()):
        self.entries, self.cache, self.descriptors = entries, arrays, descriptors
        self.gaps = tuple(gaps)

    def load(self, map_id, floor):
        return self.cache.get((map_id, floor))

    def propose(self, frame, map_id, floor, *, context=None, cancelled=None):
        from difficult_grid import DifficultGridLocator
        return DifficultGridLocator.propose(self, frame, map_id, floor,
                                             context=context, cancelled=cancelled)


@dataclass(frozen=True)
class PreparedStructurePoseInputs:
    binding: PoseInputBinding
    map_id: str
    floor: str
    floors: tuple
    references: Mapping
    preparations: Mapping
    # Actual successful optional payload reads; a FileRef alone grants no fit.
    preparation_resources: Mapping
    adapters: PreparedCoordinates
    pack: _Pack
    capabilities: Mapping
    gaps: tuple
    preparation_started: float
    preparation_finished: float
    retained_bytes: int
    pose_index_enabled: bool
    coordinate_prior: PreparedCoordinatePrior | None
    coordinate_prior_source: PoseIndexSource | None
    _locators: _LocatorTemplates = field(repr=False, compare=False)
    _grid: object = field(repr=False, compare=False)

    @property
    def preparation_seconds(self):
        return self.preparation_finished - self.preparation_started

    def reference(self, profile, map_id, floor):
        key = (str(map_id), str(floor))
        if key not in self.references:
            raise PoseInputsUnavailable("pose-input-layer-not-prepared:" + "/".join(key))
        return self.references[key]

    def require_scope(self, map_id, floor):
        """Check only the declared RAM query scope; it grants no runtime authority."""
        key = (str(map_id), str(floor))
        if key not in self.references:
            raise PoseInputsUnavailable("pose-input-layer-not-prepared:" + "/".join(key))

    def prepared(self, reference, *, cancelled=None):
        if cancelled is not None:
            files._check_cancelled(cancelled)  # Cooperative memory-only caller signal.
        for key, value in self.references.items():
            if value is reference:
                return self.preparations[key]
        raise PoseInputsUnavailable("pose-input-reference-not-owned")

    def new_geometry_engine(self):
        return _GeometryEngine(self)


class _GeometryEngine:
    def __init__(self, inputs):
        self.inputs, self.pack, self.qt_adapters = inputs, inputs.pack, inputs.adapters
        self.qt_season = inputs.binding.season
        self.entry_identity, self.side_entry_identity = inputs._locators.new_queries()
        self.identity_floors = inputs.floors
        self.identity_profile_id = self.pack.identity_profiles[inputs.map_id]
        self.qt_shared_provider = _ReferenceTable(inputs.references)

    def identity_profile_for_map(self, map_id):
        return self.pack.identity_profiles[str(map_id)]

    def pose_index_candidates(self, frame, map_id, floor, *, cancelled=None, context=None):
        if not self.inputs.pose_index_enabled:
            raise PoseInputsUnavailable("pose-index-inputs-not-prepared")
        if self.inputs._grid is None:
            from map_identity_engine import MapIdentityEngine
            return MapIdentityEngine.pose_index_candidates(self, frame, map_id, floor,
                                                           cancelled=cancelled, context=context)
        return self.inputs._grid.propose(frame, map_id, str(floor), cancelled=cancelled, context=context)


def _freeze_reference(reference):
    return SharedStructureReference(*(_array(getattr(reference, name)) for name in
        ("clean_mask", "validation_mask")), _freeze(reference.metadata), reference.path,
        _array(reference.known_boundary), _array(reference.unknown_mask),
        _freeze(reference.native_wall_segments), reference.resource_root)


def _new_reference(provider_reference, bundle, read, root):
    metadata = provider_reference.metadata
    if metadata.get("sourceKind") == "legacy-shared-binary":
        from author_structure_resources import AuthorStructureResourceStore
        legacy = bundle.legacy_references
        if legacy is None:
            raise PoseInputsUnavailable("pose-input-legacy-declaration-missing")
        store = AuthorStructureResourceStore(legacy._store.index_path, legacy._store.resource_root,
            read_resource=read, check_current=bundle.assert_current)
        binding = metadata["binding"]
        product = store.load(binding["profile"], binding["mapId"], binding["floor"],
                             route_shape=provider_reference.shape)
        if product is None:
            raise PoseInputsUnavailable("pose-input-legacy-product-unavailable")
        return SharedStructureReference(product.clean_mask, product.validation_mask, metadata,
            provider_reference.path, product.known_boundary, product.unknown_mask,
            product.native_wall_segments, root)
    return load_shared_structure(provider_reference.path, expected_binding=metadata["binding"],
        resource_root=root, read_resource=read)


def _prepare_locators(values, pack, references, read, interfaces, root, map_id, floors, *, cancelled=None):
    from difficult_entry_identity import MapEntryGeometryIdentity
    from difficult_side_entry_identity import MapLocalEntryIdentity
    from side_door_recognition import SideDoorRecognizer
    table = _ReferenceTable(MappingProxyType(dict(references)))
    directory = root / interfaces["identityEntries"]
    admitted_main, admitted_side = values['entry_identity'], values['side_entry_identity']
    reusable = (admitted_main.error is None
        and (not admitted_side.semantic_side_entries
             or admitted_side.side_door_error is None and admitted_side.side_search_error is None)
        and (not admitted_side.passport_source_available or admitted_side.passport_error is None))
    if reusable:
        # These exact static models were decoded at base admission. Verify their
        # actual backing files again, then copy query state instead of parsing
        # and reconstructing the entire catalogue for every requested map.
        paths = [directory / 'entries.json', directory / 'side-entries.json']
        if admitted_side.semantic_side_entries:
            paths.extend((directory / 'side-door-anchors.json', directory / 'side-entry-search.npz'))
        if admitted_side.passport_source_available:
            paths.append(directory / 'entry-passports.json')
        read.record_admitted(paths, cancelled=cancelled)
        main, side = copy(admitted_main), copy(admitted_side)
        main._observation, main._wall_lines = None, {}
        main.route_loader = side.route_loader = None
        main.structures = side.structures = table
        main.shared_reference_provider = side.shared_reference_provider = table
        main.profile_for_map = side.profile_for_map = pack.identity_profiles.__getitem__
        side.geometry_provider = side.author_visible_provider = None
        side.passports, side.entry_coverage = list(side.passports), list(side.entry_coverage)
        side._exit_recognizer = None
    else:
        # An admitted capability miss retains the original loaders' failure and
        # recovery classification; it is not a reusable successful model.
        main = MapEntryGeometryIdentity(directory, structure_store=table,
            shared_reference_provider=table, profile_id=interfaces["identityProfile"],
            profile_for_map=pack.identity_profiles.__getitem__, read_resource=read)
        side = MapLocalEntryIdentity(directory, mode=pack.mode, floors=pack.floors,
            structure_store=table, shared_reference_provider=table,
            profile_id=interfaces["identityProfile"], profile_for_map=pack.identity_profiles.__getitem__,
            read_resource=read, typed_index_path=root / interfaces["typedIndex"],
            exit_icon_path=root / interfaces["exitIcon"] if values['semantic_side_entries'] else None,
            semantic_side_entries=values['semantic_side_entries'],
            passport_source_available=values['passport_source_available'])
    if main.error is not None:
        raise PoseInputsUnavailable("pose-input-main-locator:" + main.error)
    if side.side_door_error != admitted_side.side_door_error or side.side_search_error != admitted_side.side_search_error:
        raise PoseInputsUnavailable("pose-input-side-locator-capability-changed")
    passports = values['legacy_passports']
    if passports is not None:
        declaration = interfaces.get("legacyClean") or {}
        if reusable:
            read.record_admitted((root / declaration["passports"],), cancelled=cancelled)
        else:
            from imported_resource_reader import decode_resource_json
            passports = decode_resource_json(read(root / declaration["passports"]))
            side.passports.extend(passports["entries"])
            side.entry_coverage.extend(passports["coverage"])
    # Finish the existing lazy icon decoder in the preparation stage.
    if side.semantic_side_entries:
        side._exit_recognizer = SideDoorRecognizer(side.exit_icon_path, read_resource=read)
    wanted = lambda item: (map_id is None or item.get("mapId") == map_id) and str(item.get("floor")) in floors
    main.entries = _freeze([row for row in main.entries if wanted(row[0])])
    side.entries = _freeze([row for row in side.entries if wanted(row[0])])
    side.registered_floors = frozenset((row[0]["mapId"], str(row[0]["floor"])) for row in side.entries)
    side.passports = _freeze([row for row in side.passports if wanted(row)])
    side.entry_coverage = _freeze([row for row in side.entry_coverage if wanted(row)])
    side.semantic_gaps = _freeze(side.semantic_gaps)
    side.side_doors = _freeze({key: value for key, value in side.side_doors.items()
                               if map_id is None or key == map_id})
    side.side_search = _freeze({key: value for key, value in side.side_search.items()
                                if (map_id is None or key[0] == map_id) and key[1] in floors})
    if side._exit_recognizer is not None:
        side._exit_recognizer.exit_icon = _array(side._exit_recognizer.exit_icon)
        for reference in side._exit_recognizer.references:
            for name, value in vars(reference).items():
                setattr(reference, name, _freeze(value))
        side._exit_recognizer.references = tuple(side._exit_recognizer.references)
        side._exit_recognizer.taxonomy_by_map = _freeze(side._exit_recognizer.taxonomy_by_map)
    return main, side


def _scope_locators(templates, pack, references, map_id, floors):
    """Select immutable rows; only query state and the target table are new."""
    main, side = templates.new_queries()
    table = _ReferenceTable(MappingProxyType(dict(references)))
    for locator in (main, side):
        locator.structures = locator.shared_reference_provider = table
        locator.profile_for_map = pack.identity_profiles.__getitem__
    wanted = lambda item: item.get('mapId') == map_id and str(item.get('floor')) in floors
    main.entries = tuple(row for row in main.entries if wanted(row[0]))
    side.entries = tuple(row for row in side.entries if wanted(row[0]))
    side.registered_floors = frozenset((row[0]['mapId'], str(row[0]['floor'])) for row in side.entries)
    side.passports = tuple(row for row in side.passports if wanted(row))
    side.entry_coverage = tuple(row for row in side.entry_coverage if wanted(row))
    side.side_doors = _FrozenDict({key: row for key, row in side.side_doors.items() if key == map_id})
    side.side_search = _FrozenDict({key: row for key, row in side.side_search.items()
                                   if key[0] == map_id and key[1] in floors})
    return _LocatorTemplates(main, side)


@dataclass(frozen=True)
class PreparedStructureCommonModels:
    """One admitted context's static RAM, shared by cold target preparations.

    Only the production factory installs this snapshot. Managed context changes
    use the existing engine revision/current callback. Target bytes still enter
    the original component reader; unknown/independent/legacy paths stay guarded.
    """
    pack: _Pack
    coordinates: PreparedCoordinates
    locators: _LocatorTemplates
    capabilities: Mapping
    layers: Mapping
    retained_bytes: int
    _engine: object = field(repr=False, compare=False)
    _bundle: object = field(repr=False, compare=False)
    _provider: object = field(repr=False, compare=False)
    _source: object = field(repr=False, compare=False)
    _owners: object = field(repr=False, compare=False)
    _owner_state: tuple = field(repr=False, compare=False)
    _provider_state: tuple = field(repr=False, compare=False)
    _source_state: tuple = field(repr=False, compare=False)
    _entry_state: tuple = field(repr=False, compare=False)
    _check: object = field(repr=False, compare=False)
    _root: Path = field(repr=False, compare=False)
    _component: object = field(repr=False, compare=False)
    _reader: object = field(repr=False, compare=False)
    _season: str = field(repr=False, compare=False)

    @staticmethod
    def entry_state(engine):
        main, side = engine.entry_identity, engine.side_entry_identity
        return id(main), id(main.entries), id(side), id(side.entries), id(side.passports), id(side.entry_coverage)

    def assert_current(self, engine, revision, cancelled):
        files._check_cancelled(cancelled)
        current = getattr(engine, 'qt_resource_context_current', None)
        if (engine is not self._engine or getattr(engine, 'qt_native_base', None) is not self._bundle
                or getattr(engine, 'qt_shared_provider', None) is not self._provider
                or getattr(engine, 'qt_fast_structure_source', None) is not self._source
                or getattr(engine, 'qt_assert_resource_current', None) is not self._check
                or getattr(engine, 'qt_resource_revision', revision) != revision
                or current is not None and not current()
                or self._owners.read_state() != self._owner_state
                or self._provider._read_owner_state() != self._provider_state
                or self._source._read_state() != self._source_state
                or self.entry_state(engine) != self._entry_state):
            raise PoseInputsUnavailable('pose-input-resource-binding-changed')
        files._check_cancelled(cancelled)


def prepare_structure_pose_common_models(engine, *, cancelled=None):
    """Factory-only static preparation; no mask/distance atlas is retained."""
    from client_providers import SharedReferenceProvider
    from imported_fast_structure import ImportedFastStructureSource
    from imported_resource_reader import ComponentResourceReader
    bundle = getattr(engine, 'qt_native_base', None)
    provider, source = getattr(engine, 'qt_shared_provider', None), getattr(engine, 'qt_fast_structure_source', None)
    if not isinstance(bundle, NativeBaseBundle) or type(provider) is not SharedReferenceProvider:
        return None
    owners = provider._read_operation_owners(bundle)
    values = bundle._values
    reader = values['reader']
    check = getattr(engine, 'qt_assert_resource_current', None)
    if (owners is None or check is not provider._check_current
            or type(reader) is not ComponentResourceReader or type(source) is not ImportedFastStructureSource
            or source is not values['fast_structure_source'] or source.reader is not reader
            or source.view is not bundle._view or reader.view is not bundle._view
            or values['adapters'].native_static_context is not None):
        return None
    files._check_cancelled(cancelled)
    check()
    owner_state, provider_state, source_state = owners.read_state(), provider._read_owner_state(), source._read_state()
    entry_state = PreparedStructureCommonModels.entry_state(engine)
    root, component = bundle._root, values['component_ref']
    read = _RecordingReader(reader)
    interfaces, pack = values['interfaces'], values['pack']
    read.record_admitted((root / bundle._view.component['entrypoint'], root / interfaces['identityPack'],
        root / interfaces['typedIndex'], root / interfaces['coordinatePack'],
        *((root / source.index_path,) if source.index_path is not None else ())), cancelled=cancelled)
    profiles = MappingProxyType(dict(pack.identity_profiles))
    info = tuple(_MapInfo(str(item.id), str(item.name)) for item in pack.maps)
    ram_pack = _Pack(pack.pack_id, pack.mode, tuple(pack.floors), info,
                    MappingProxyType({item.id: item for item in info}), profiles)
    matrices = {(profile, mid, floor): _array(values['adapters'].matrix_profile_to_canonical(profile, mid, floor))
        for mid in profiles for floor in pack.floors for profile in dict.fromkeys(('shared-structure', profiles[mid]))}
    coordinates = PreparedCoordinates(values['adapters'].canonical_pack_id, MappingProxyType(matrices))
    models = dict(values, entry_identity=engine.entry_identity, side_entry_identity=engine.side_entry_identity)
    main, side = _prepare_locators(models, ram_pack, {}, read, interfaces, root, None, pack.floors,
                                  cancelled=cancelled)
    ram_reader = _RamReader(MappingProxyType({key: raw for key, (_, raw) in read.payloads.items()}))
    main.read_resource = side.read_resource = ram_reader
    if side._exit_recognizer is not None:
        side._exit_recognizer.read_resource = ram_reader
    locators = _LocatorTemplates(main, side)
    capabilities = _freeze(dict(rankReady=values['rank_ready'], semanticSideEntries=values['semantic_side_entries'],
        passportSourceAvailable=values['passport_source_available'], mainError=main.error,
        sideDoorError=side.side_door_error, sideSearchError=side.side_search_error, passportError=side.passport_error))
    legacy = owners.legacy_keys()
    layers = {}
    for key, row in provider.versions.items():
        target = key[1], key[3]
        if target not in legacy and not row.get('sharedError'):
            layers[target] = (_freeze(row), source.entries.get(key))
    read.recheck(cancelled=cancelled)
    check()
    common = PreparedStructureCommonModels(ram_pack, coordinates, locators, capabilities,
        MappingProxyType(layers), _bytes_used((ram_pack, coordinates, locators, capabilities, layers)),
        engine, bundle, provider, source, owners, owner_state, provider_state, source_state, entry_state,
        check, root, component, reader, values['season'])
    common.assert_current(engine, getattr(engine, 'qt_resource_revision', 0), cancelled)
    return common if common.retained_bytes < DEFAULT_CACHE_BYTES else None


def _original_native_loaders(common):
    """Only omit readers whose real work this RAM preparation implements."""
    from client_providers import SharedReferenceProvider
    from imported_fast_structure import ImportedFastStructureSource
    from shared_structure_reference import SharedStructureResourceStore
    resources = common._provider.resources
    return (getattr(common._provider._load, '__func__', None) is SharedReferenceProvider._load
        and common._owners.base_view._assert_current == common._owners.controls_view._assert_current
        and type(resources) is SharedStructureResourceStore
        and getattr(resources.load, '__func__', None) is SharedStructureResourceStore.load
        and getattr(common._source.read, '__func__', None) is ImportedFastStructureSource.read
        and getattr(common._source.decode, '__func__', None) is ImportedFastStructureSource.decode)


def _common_reference_valid(reference, row):
    return (reference is not None and np.any(reference.clean_mask)
        and _freeze(reference.metadata.get('canvas')) == row['canvas']
        and all(_freeze(reference.metadata.get(name)) == value for name, value in row['expected'].items()))


def _reference_decode_metadata(value):
    # Saved metadata is JSON. Restore its list form for the unchanged fast
    # decoder's exact comparison with JSON, without copying any mask arrays.
    if isinstance(value, Mapping):
        return {key: _reference_decode_metadata(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return [_reference_decode_metadata(item) for item in value]
    return value


@dataclass(frozen=True)
class PreparedStructureStaticPayloads:
    """The factory's actual admitted bytes; distance fields stay target-local."""
    common: PreparedStructureCommonModels
    payloads: Mapping
    references: Mapping
    retained_bytes: int
    _coordinate: object = field(repr=False, compare=False)
    _reference_source: object = field(repr=False, compare=False)

    def assert_current(self, engine, revision, cancelled):
        self.common.assert_current(engine, revision, cancelled)
        if (getattr(engine, 'qt_pose_common_models', None) is not self.common
                or self._coordinate is not None and (
                    getattr(engine, 'qt_s1_coordinate_recognition', None) is not self._coordinate
                    or self._coordinate.references is not self._reference_source
                    or not self._coordinate.current())):
            raise PoseInputsUnavailable('pose-input-resource-binding-changed')


def prepare_structure_pose_static_payloads(engine, *, cancelled=None):
    """Called once by the managed imported factory after its real admission.

    Keep compressed bytes rather than a whole-context distance atlas. A failed
    optional payload is a retained miss, never a retry or an engine rejection.
    Required payload failures, cancellation and selection/owner changes retain
    their original attribution. Independent/custom readers keep the old path.
    """
    common = getattr(engine, 'qt_pose_common_models', None)
    if not isinstance(common, PreparedStructureCommonModels) or not _original_native_loaders(common):
        return None
    revision = getattr(engine, 'qt_resource_revision', 0)
    common.assert_current(engine, revision, cancelled)
    common._check()
    coordinate, references = getattr(engine, 'qt_s1_coordinate_recognition', None), {}
    if coordinate is not None:
        from client_s1_coordinate_recognition import PreparedS1CoordinateRecognition
        if type(coordinate) is not PreparedS1CoordinateRecognition or coordinate.engine is not engine:
            return None
        if not coordinate.current():
            return None
        for key, (row, _) in common.layers.items():
            reference = coordinate.references.get(key)
            if (not _common_reference_valid(reference, row)
                    or _path_key(reference.path) != _path_key(row['path'])):
                raise PoseInputsUnavailable('pose-input-reference-binding-mismatch')
            references[key] = reference
    typed_paths, optional, paths = [], set(), []
    for key, (row, declaration) in common.layers.items():
        if key not in references:
            typed_paths.append(Path(row['path']))
        if declaration is not None:
            reference_ref, prepared_ref = declaration
            if common._reader.relative_path(row['path']) != reference_ref.path:
                return None  # Preserve the original source's mismatch handling.
            path = common._root / prepared_ref.path
            paths.append(path)
            optional.add(prepared_ref)
    # Inventory bytes are already bound to the admitted component. Do not load
    # a snapshot that cannot fit together with its common models/references.
    typed_paths, paths = tuple(dict.fromkeys(typed_paths)), tuple(dict.fromkeys(paths))
    references = MappingProxyType(references)
    required_bytes = _bytes_used(references) + sum(
        common._reader._inventory[common._reader.relative_path(path)]['bytes'] for path in typed_paths)
    if required_bytes >= engine.qt_pose_input_cache.max_bytes:
        return None
    payloads = {}
    def stage(file_ref, raw):
        payloads[_path_key(common._root / file_ref.path)] = raw
    if typed_paths:
        try:
            common._reader.read_many(typed_paths, expected_root=common._root,
                                     cancelled=cancelled, on_payload=stage)
        except (OSError, ValueError):
            common._reader.view._assert_current()
            common.assert_current(engine, revision, cancelled)
            raise
    # The original typed declaration decoder owns this source capability too.
    # In particular, a reference without validation authority must never read
    # its fast preparation, even if that optional file is broken.
    from imported_structure_declarations import validate_structure_declarations
    permitted = set()
    for key, (row, declaration) in common.layers.items():
        common.assert_current(engine, revision, cancelled)
        if key in references:
            metadata = references[key].metadata
        else:
            try:
                metadata = validate_structure_declarations(payloads[_path_key(row['path'])],
                    expected_binding=row['binding'], expected_shape=row['canvas']['shapeHW'])
            except (OSError, ValueError) as error:
                error.resource_file_ref = FileRef(common._component, common._reader.relative_path(row['path']))
                raise
        if metadata.get('authorizesStructureValidation') is not False and declaration is not None:
            permitted.add(declaration[1])
    optional.intersection_update(permitted)
    pending = tuple(path for path in paths
        if FileRef(common._component, common._reader.relative_path(path)) in optional)
    required_bytes += sum(common._reader._inventory[common._reader.relative_path(path)]['bytes'] for path in pending)
    if required_bytes >= engine.qt_pose_input_cache.max_bytes:
        return None
    while pending:
        try:
            common._reader.read_many(pending, expected_root=common._root,
                                     cancelled=cancelled, on_payload=stage)
        except (OSError, ValueError) as error:
            failed = getattr(error, 'resource_file_ref', None)
            # A payload failure precedes read_many's final lifetime check.
            # Complete that check even for an optional miss; no other error
            # may be turned into a missing fast structure.
            common._reader.view._assert_current()
            common.assert_current(engine, revision, cancelled)
            if failed not in optional:
                raise
            failed_key = _path_key(common._root / failed.path)
            pending = tuple(path for path in pending
                if _path_key(path) not in payloads and _path_key(path) != failed_key)
        else:
            pending = ()
    common._check()
    snapshot = PreparedStructureStaticPayloads(common, MappingProxyType(payloads), references,
        _bytes_used((payloads, references)), coordinate, coordinate.references if coordinate is not None else None)
    snapshot.assert_current(engine, revision, cancelled)
    return snapshot


def _prepare_from_common(engine, common, map_id, floor, floors, revision, indexed, cancelled, started):
    from imported_fast_structure import decode_prepared
    common.assert_current(engine, revision, cancelled)
    payloads = getattr(engine, 'qt_pose_static_payloads', None)
    if isinstance(payloads, PreparedStructureStaticPayloads):
        payloads.assert_current(engine, revision, cancelled)
    else:
        payloads = None
    references, preparations, resources, gaps, matrices = {}, {}, {}, [], {}
    for target_floor in floors:
        common.assert_current(engine, revision, cancelled)
        key = map_id, target_floor
        row, declaration = common.layers[key]
        path = row['path']
        try:
            raw = (None if key in payloads.references else payloads.payloads[_path_key(path)]) if payloads else (
                common._reader.read_many((path,), expected_root=common._root, cancelled=cancelled)[0])
        except (OSError, ValueError) as error:
            required_ref = FileRef(common._component, common._reader.relative_path(path))
            if getattr(error, 'resource_file_ref', None) != required_ref:
                raise  # Selection/manifest/cancellation failures are not cold misses.
            common.assert_current(engine, revision, cancelled)
            from imported_fast_structure import _PreparedReadFailure
            failure = _PreparedReadFailure(common._source, error.with_traceback(None), {},
                                           cancelled, common._source_state)
            try:
                failure.assert_current()
                # Deliver the first actual error through the original provider
                # and decoder, without another file read. Cold decoding yields
                # a gap; a warm required reference still must decode successfully.
                current = common._provider.read_with_resource('shared-structure', map_id, target_floor,
                                                              read_resource=failure.read_resource)
                reference = (None if current is None else load_shared_structure(path,
                    expected_binding=row['binding'], resource_root=common._root,
                    read_resource=failure.read_resource))
                # Decoder failure is a cold miss only while its selection and
                # owners remain current, including after it consumed the error.
                failure.assert_current()
                common.assert_current(engine, revision, cancelled)
                if current is not None and reference is None:
                    raise PoseInputsUnavailable('pose-input-reference-decode-unavailable')
            finally:
                failure.release()
        else:
            if payloads is not None and key in payloads.references:
                reference = payloads.references[key]
            else:
                # The root was already resolved/admitted by the real factory.
                # Passing no root avoids Path.resolve doing filesystem work in
                # the original byte decoder; restore that same admitted root.
                reference = load_shared_structure(path, expected_binding=row['binding'],
                    resource_root=common._root if payloads is None else None,
                    read_resource=_RamReader({_path_key(path): raw}))
                if payloads is not None and reference is not None:
                    reference = replace(reference, resource_root=common._root)
        resources[key], preparations[key] = None, None
        if not (payloads is not None and key in payloads.references) and not _common_reference_valid(reference, row):
            references[key] = None
            gaps.append('shared-reference-unavailable:' + target_floor)
            continue
        references[key] = reference if payloads is not None and key in payloads.references else _freeze_reference(reference)
        fast_allowed = reference.metadata.get('authorizesStructureValidation') is not False
        if fast_allowed and declaration is not None:
            reference_ref, prepared_ref = declaration
            if common._reader.relative_path(path) != reference_ref.path:
                raise PoseInputsUnavailable('pose-input-reference-binding-mismatch')
            try:
                raw = payloads.payloads.get(_path_key(common._root / prepared_ref.path)) if payloads else (
                    common._reader.read_many((common._root / prepared_ref.path,), expected_root=common._root,
                                            cancelled=cancelled)[0])
            except (OSError, ValueError) as error:
                if getattr(error, 'resource_file_ref', None) != prepared_ref:
                    raise
                # A payload exception precedes read_many's final selection
                # check. Preserve it on this optional-failure boundary too.
                common._reader.view._assert_current()
                common.assert_current(engine, revision, cancelled)
            else:
                if raw is not None:
                    resources[key] = prepared_ref
                    try:
                        decode_reference = (replace(reference, metadata=_reference_decode_metadata(reference.metadata))
                            if payloads is not None and key in payloads.references else reference)
                        decoded = decode_prepared(raw, decode_reference)
                        preparations[key] = PreparedStructure(_array(decoded.distance), _array(decoded.chains),
                            _array(decoded.rays), decoded.pitch, tuple(decoded.shape))
                    except (OSError, ValueError):
                        pass
        if preparations[key] is None:
            gaps.append(('fast-prepared-unavailable:' if fast_allowed else
                         'fast-structure-validation-unavailable:') + target_floor)
        for profile in dict.fromkeys(('shared-structure', common.pack.identity_profiles[map_id])):
            matrix = common.coordinates.matrix_profile_to_canonical(profile, map_id, target_floor)
            matrices[(profile, map_id, target_floor)] = matrix
            if matrix is None:
                gaps.append('coordinate-bridge-unavailable:' + profile + '/' + target_floor)
    locators = _scope_locators(common.locators, common.pack, references, map_id, floors)
    adapters = PreparedCoordinates(common.coordinates.canonical_pack_id, MappingProxyType(matrices))
    binding = PoseInputBinding(common._season, common.pack.mode, common._component, revision,
        id(engine), id(common._bundle), id(common._provider), id(common._source))
    references, preparations, resources = map(MappingProxyType, (references, preparations, resources))
    retained = _bytes_used((references, preparations, resources, adapters, locators))
    if payloads is not None:
        payloads.assert_current(engine, revision, cancelled)
    common.assert_current(engine, revision, cancelled)
    return PreparedStructurePoseInputs(binding, map_id, floor, floors, references, preparations, resources,
        adapters, common.pack, common.capabilities, tuple(gaps), started, time.perf_counter(), retained,
        indexed, None, None, locators, None)


def _prepare_grid(engine, map_id, floors, read, enabled):
    if not enabled:
        return None
    from map_identity_engine import MapIdentityEngine
    provider = getattr(engine, "pose_index_candidates", None)
    if getattr(provider, "__func__", None) is MapIdentityEngine.pose_index_candidates:
        return None
    from difficult_grid import DifficultGridLocator
    from difficult_mode import DifficultCandidateEngine
    locator = getattr(engine, "grid_locator", None)
    if (getattr(provider, "__func__", None) is not DifficultCandidateEngine.pose_index_candidates
            or type(locator) is not DifficultGridLocator):
        raise PoseInputsUnavailable("pose-index-provider-prepare-adapter-required")
    entries, arrays, descriptors, gaps = {}, {}, {}, []
    for floor in floors:
        key = (map_id, floor)
        row = locator.entries.get(key)
        if row is None:
            continue
        entries[key] = _freeze(row)
        if row.get("gridDisabledReason"):
            continue
        path = locator.root / row["gridResource"]
        try:
            raw = read(path)
        except (OSError, ValueError) as error:
            from imported_resource_packages import FileRef
            expected = FileRef(read.reader.view.component_ref, read.reader.relative_path(path))
            if getattr(error, "resource_file_ref", None) != expected:
                raise  # Context, ownership, cancellation and manifest failures stay terminal.
            gaps.append("pose-index-grid-unavailable:" + floor)
            continue
        try:
            with np.load(BytesIO(raw), allow_pickle=False) as archive:
                chains, rays = np.asarray(archive["grid_chains"], float), np.asarray(archive["grid_rays"], float)
        except (OSError, ValueError, TypeError, KeyError, zipfile.BadZipFile, zlib.error, EOFError):
            # Header preflight is not an array-body integrity check. Keep the
            # existing optional archive-decode failures separate from the
            # component reader/owner checks above and the final recheck below.
            gaps.append("pose-index-grid-unavailable:" + floor)
            continue
        if (chains.ndim != 3 or chains.shape[1:] != (3, 2) or rays.shape != (len(chains), 2, 2)
                or not np.isfinite(chains).all() or not np.isfinite(rays).all()):
            gaps.append("pose-index-grid-unavailable:" + floor)
            continue
        delta = chains[:, [0, 2]] - chains[:, 1, None]
        arrays[key] = (_array(chains), _array(rays))
        descriptors[key] = (_array(delta), _array(np.sum(delta * delta, axis=(1, 2))))
    return _PreparedGridQuery(_freeze(entries), MappingProxyType(arrays), MappingProxyType(descriptors), gaps)


def prepare_structure_pose_inputs(engine, map_id, floor, *, resource_revision,
                                 recovery_floors=(), pose_index_enabled=False,
                                 pose_index_source=None, cancelled=None):
    """Read/validate one known map plus explicitly needed recovery floors.

    The measured interval starts before any resource read and ends after the
    final source/binding checks. It belongs to preparation and end-to-end time,
    never to a pure numerical fit. Production factories share their admitted
    common RAM and read only missing layer payloads. Compatibility paths retain
    their complete live checks; the bounded target cache may reuse RAM inputs.
    """
    started = time.perf_counter()
    if type(resource_revision) is not int or type(pose_index_enabled) is not bool:
        raise TypeError("pose-input-revision-and-pose-index-setting-required")
    bundle = getattr(engine, "qt_native_base", None)
    if not isinstance(bundle, NativeBaseBundle):
        raise PoseInputsUnavailable("pose-input-validated-native-base-required")
    common = getattr(engine, 'qt_pose_common_models', None)
    if isinstance(common, PreparedStructureCommonModels) and pose_index_source is None:
        map_id, floor = str(map_id), str(floor)
        floors = tuple(dict.fromkeys((floor, *(str(value) for value in recovery_floors))))
        if map_id not in common.pack.by_id or not set(floors) <= set(common.pack.floors):
            raise PoseInputsUnavailable('pose-input-map-or-floor-not-declared')
        # Independent guards and real legacy resources retain their complete
        # original preparation. Native target reads need only admitted models.
        from map_identity_engine import MapIdentityEngine
        native_index = (not pose_index_enabled or getattr(getattr(engine, 'pose_index_candidates', None),
                                                        '__func__', None) is MapIdentityEngine.pose_index_candidates)
        if (getattr(engine, 'qt_assert_resource_current', None) is common._check and native_index
                and callable(getattr(engine, 'qt_resource_context_current', None))
                and _original_native_loaders(common)
                and all((map_id, target) in common.layers for target in floors)):
            return _prepare_from_common(engine, common, map_id, floor, floors, resource_revision,
                                        pose_index_enabled, cancelled, started)
    provider, source = engine.qt_shared_provider, engine.qt_fast_structure_source
    check = engine.qt_assert_resource_current
    # This operation checks the actual live context before exposing its small
    # static model snapshot. It grants no reference reads until a successful
    # byte batch is attached below. Independent/legacy guards keep their path.
    preparation_operation = None
    begin_prepare = getattr(provider, 'begin_prepare_operation', None)
    if callable(begin_prepare) and check is getattr(provider, '_check_current', None):
        preparation_operation = begin_prepare(bundle, source, cancelled=cancelled)
    from imported_native_context import ImportedResourceOwners
    if preparation_operation is None:
        check()
        view, root, component, reader = bundle.read_binding()
        model_state = ImportedResourceOwners.bundle_state(bundle)
        values = MappingProxyType(dict(bundle._values))
    else:
        view, root, component, reader = preparation_operation.binding
        values = preparation_operation.values
        model_state = ImportedResourceOwners.bundle_state(bundle)
    pack, interfaces, season = (values[name] for name in ('pack', 'interfaces', 'season'))
    map_id, floor = str(map_id), str(floor)
    floors = tuple(dict.fromkeys((floor, *(str(value) for value in recovery_floors))))
    if map_id not in pack.by_id or not set(floors) <= set(pack.floors):
        raise PoseInputsUnavailable("pose-input-map-or-floor-not-declared")
    def source_state():
        return (source.view, source.reader, source.root, source.reader.view, source.reader.root,
                source.index_path, source.entries, source.view.component_ref,
                source.reader.view.component_ref)
    source_binding = source_state()
    provider_fields = {name: getattr(provider, name) for name in
        ("root", "resources", "versions", "_legacy_reference_loader", "_check_current")
        if hasattr(provider, name)}
    read = _RecordingReader(reader)
    # The admission decoder already validated these semantics. Read actual
    # declarations again for this preparation, then decode coordinates afresh.
    read.record_admitted((root / view.component["entrypoint"], root / interfaces["identityPack"],
                         root / interfaces["typedIndex"], root / interfaces["coordinatePack"]),
                        cancelled=cancelled)
    from nightmare_author_coordinate_adapter import AuthorCoordinateAdapters
    coordinates = AuthorCoordinateAdapters(root / interfaces["coordinatePack"], read_resource=read)
    profiles = dict(pack.identity_profiles)
    info = tuple(_MapInfo(str(item.id), str(item.name)) for item in pack.maps)
    ram_pack = _Pack(pack.pack_id, pack.mode, tuple(pack.floors), info,
                     MappingProxyType({item.id: item for item in info}), MappingProxyType(profiles))
    batch = operation = None
    if (callable(getattr(provider, "batch_bindings", None))
            and callable(getattr(provider, "read_with_resource", None))
            and callable(getattr(source, "prepare_batch", None))
            and getattr(provider, "resources", None) is values['resources']
            and getattr(provider, "versions", None) is values['versions']):
        requests = tuple(("shared-structure", map_id, target_floor) for target_floor in floors)
        bindings = (provider.batch_bindings(requests, operation=preparation_operation)
                    if preparation_operation is not None else provider.batch_bindings(requests))
        # The typed metadata owns fast-validation use. Do not pre-read its
        # optional payload or index before the fresh reference establishes it.
        batch = source.prepare_batch(bindings, cancelled=cancelled,
                                     include_prepared=False)
        begin = getattr(provider, "begin_read_operation", None)
        if callable(begin):
            operation = (begin(bundle, batch, cancelled=cancelled, preparation=preparation_operation)
                         if preparation_operation is not None else begin(bundle, batch, cancelled=cancelled))
    from imported_fast_structure import _PreparedReadFailure
    # Preserve the first required failure at the original reference decoder,
    # rather than retrying into success and delivering it to a later consumer.
    reference_read = read
    if type(batch) is _PreparedReadFailure:
        def reference_read(path):
            return read.read_from(path, read_resource=batch.read_resource)
    references, prepared, preparations, preparation_resources, matrices, gaps = {}, {}, {}, {}, {}, []
    try:
        for target_floor in floors:
            files._check_cancelled(cancelled)
            key = (map_id, target_floor)
            preparation_resources[key] = None
            fresh_native = False
            if operation is not None:
                current, fresh_native = operation.read_fresh_reference("shared-structure", map_id,
                    target_floor, read_resource=batch.read_resource)
            else:
                current = (provider.read_with_resource("shared-structure", map_id, target_floor,
                    read_resource=batch.read_resource) if batch is not None
                    else provider("shared-structure", map_id, target_floor))
            if current is None:
                references[key], prepared[key] = None, None
                gaps.append("shared-reference-unavailable:" + target_floor)
                continue
            if (current.metadata.get("binding", {}).get("mapId"),
                    str(current.metadata.get("binding", {}).get("floor"))) != key:
                raise PoseInputsUnavailable("pose-input-reference-binding-mismatch")
            if fresh_native:
                # Only this successful native operation decoded the actual
                # batch bytes with the provider's full original checks. Keep
                # those bytes for the final proof instead of decoding again.
                reference = current
                read.payloads[_path_key(current.path)] = (Path(current.path), batch.read_resource(current.path))
            else:
                reference = _new_reference(current, bundle, reference_read, root)
            if reference is None:
                raise PoseInputsUnavailable("pose-input-reference-decode-unavailable")
            references[key] = _freeze_reference(reference)
            fast_available = reference.metadata.get("authorizesStructureValidation") is not False
            payload = source.read(current, cancelled=cancelled) if fast_available else None
            if payload is not None:
                identity = payload[1]
                resource = identity[2] if isinstance(identity, tuple) and len(identity) > 2 else None
                if isinstance(resource, FileRef):
                    preparation_resources[key] = resource
            value = None
            if payload is not None:
                try:
                    decoded = source.decode(payload[0], current)
                    value = PreparedStructure(_array(decoded.distance), _array(decoded.chains),
                        _array(decoded.rays), decoded.pitch, tuple(decoded.shape))
                except (OSError, ValueError):
                    pass  # An optional preparation never invents a usable value.
            prepared[key] = value
            if fast_available:
                preparations[key] = (current, payload[1] if payload is not None else None, value)
            if value is None:
                gaps.append(("fast-prepared-unavailable:" if fast_available else
                             "fast-structure-validation-unavailable:") + target_floor)
            for profile in dict.fromkeys(("shared-structure", profiles[map_id])):
                matrix = coordinates.matrix_profile_to_canonical(profile, map_id, target_floor)
                matrices[(profile, map_id, target_floor)] = _array(matrix)
                if matrix is None:
                    gaps.append("coordinate-bridge-unavailable:" + profile + "/" + target_floor)
    finally:
        if batch is not None:
            batch.release()
    batch = payload = decoded = None
    main, side = _prepare_locators(values, ram_pack, references, read, interfaces, root, map_id, floors,
                                  cancelled=cancelled)
    grid = _prepare_grid(engine, map_id, floors, read, pose_index_enabled)
    if grid is not None:
        gaps.extend(grid.gaps)
    prior = None
    if pose_index_source is not None:
        from s1_pose_prior import CoordinatePriorIndex
        declaration = pose_index_source
        index = CoordinatePriorIndex(declaration.path, expected_sha256=declaration.expected_sha256,
            mode=declaration.mode, resource_key=declaration.resource_key,
            provider=lambda mid, fl: references.get((str(mid), str(fl))))
        transforms = {}
        for target_floor in floors:
            try:
                transforms[(map_id, target_floor)] = _array(index._native_to_common(map_id, target_floor))
            except (KeyError, TypeError, ValueError, OSError):
                gaps.append("coordinate-prior-unavailable:" + target_floor)
        prior = PreparedCoordinatePrior(index.mode, index.resource_key, index.index_sha256,
                                        MappingProxyType(transforms))
    # Freeze the only callback retained by the original locator query objects.
    ram_reader = _RamReader(MappingProxyType({key: raw for key, (_, raw) in read.payloads.items()}))
    main.read_resource = side.read_resource = ram_reader
    if side._exit_recognizer is not None:
        side._exit_recognizer.read_resource = ram_reader
    capabilities = _freeze(dict(rankReady=values['rank_ready'],
        semanticSideEntries=values['semantic_side_entries'],
        passportSourceAvailable=values['passport_source_available'],
        mainError=main.error, sideDoorError=side.side_door_error,
        sideSearchError=side.side_search_error, passportError=side.passport_error))
    read.recheck(cancelled=cancelled)
    if pose_index_source is not None:
        # Keep the existing source/digest admission at preparation publication,
        # without retaining the file-reading index/provider in the RAM result.
        CoordinatePriorIndex(pose_index_source.path,
            expected_sha256=pose_index_source.expected_sha256,
            mode=pose_index_source.mode, resource_key=pose_index_source.resource_key,
            provider=lambda mid, fl: references.get((str(mid), str(fl))))
    for current, identity, value in preparations.values():
        payload = source.read(current, cancelled=cancelled)
        if value is not None and (payload is None or payload[1] != identity):
            raise PoseInputsUnavailable("pose-input-preparation-changed")
    owner_operation = preparation_operation if preparation_operation is not None else operation
    if owner_operation is not None:
        owner_operation.finish()
    if preparation_operation is None:
        check()
        final_view, final_root, final_component, final_reader = bundle.read_binding()
    else:
        # finish() performed the real live context proof and compared all of
        # its current model/binding owners. Reuse the proven binding in RAM.
        final_view, final_root, final_component, final_reader = preparation_operation.binding
    if (getattr(engine, "qt_native_base", None) is not bundle or engine.qt_shared_provider is not provider
            or engine.qt_fast_structure_source is not source or engine.qt_assert_resource_current is not check
            or (final_view, final_root, final_component, final_reader) != (view, root, component, reader)
            or ImportedResourceOwners.bundle_state(bundle) != model_state
            or source_binding != source_state()
            or any(getattr(provider, name) is not value for name, value in provider_fields.items())):
        raise PoseInputsUnavailable("pose-input-resource-binding-changed")
    files._check_cancelled(cancelled)
    binding = PoseInputBinding(season, pack.mode, component, resource_revision,
        id(engine), id(bundle), id(provider), id(source))
    locators = _LocatorTemplates(main, side)
    immutable_refs, immutable_prepared = MappingProxyType(references), MappingProxyType(prepared)
    immutable_resources = MappingProxyType(preparation_resources)
    adapters = PreparedCoordinates(coordinates.canonical_pack_id, MappingProxyType(matrices))
    retained = _bytes_used((immutable_refs, immutable_prepared, immutable_resources, adapters, locators, grid, prior))
    return PreparedStructurePoseInputs(binding, map_id, floor, floors, immutable_refs, immutable_prepared,
        immutable_resources, adapters, ram_pack, capabilities, tuple(gaps), started, time.perf_counter(), retained,
        pose_index_enabled, prior, pose_index_source, locators, grid)


class StructurePoseInputCache:
    """Bounded static RAM reuse; the existing resource owner calls invalidate."""
    def __init__(self, *, max_entries=2, max_bytes=DEFAULT_CACHE_BYTES):
        if type(max_entries) is not int or max_entries < 1 or type(max_bytes) is not int or max_bytes < 1:
            raise ValueError("pose-input-cache-bounds-required")
        self.max_entries, self.max_bytes = max_entries, max_bytes
        self._entries = OrderedDict()
        self._references = WeakValueDictionary()
        self._lock = threading.RLock()

    def share_references(self, inputs):
        """Preserve verified reference identity without retaining extra masks.

        Preparation has already validated the exact component and resource
        owner. A live input or temporal prior can keep that same immutable
        reference alive across scope changes and ordinary cache eviction.
        """
        references = dict(inputs.references)
        with self._lock:
            for layer, reference in references.items():
                if reference is not None:
                    key = inputs.binding, layer
                    previous = self._references.get(key)
                    if previous is None:
                        self._references[key] = reference
                    else:
                        references[layer] = previous
        references = MappingProxyType(references)
        table = _ReferenceTable(references)
        main, side = inputs._locators.new_queries()
        for locator in (main, side):
            locator.structures = table
            locator.shared_reference_provider = table
        locators = _LocatorTemplates(main, side)
        retained = _bytes_used((references, inputs.preparations, inputs.preparation_resources, inputs.adapters,
                               locators, inputs._grid, inputs.coordinate_prior))
        return replace(inputs, references=references, _locators=locators, retained_bytes=retained)

    def put(self, engine, inputs):
        if inputs.binding.engine_identity != id(engine):
            raise ValueError("pose-input-cache-engine-mismatch")
        key = (id(engine), inputs.binding.resource_revision, inputs.map_id, inputs.floor,
               inputs.floors, inputs.pose_index_enabled)
        key += (inputs.coordinate_prior_source,)
        with self._lock:
            self._entries.pop(key, None)
            if inputs.retained_bytes > self.max_bytes:
                return False  # Valid one-shot data is usable without retaining it.
            self._entries[key] = (engine, inputs)
            while (len(self._entries) > self.max_entries or
                   sum(value.retained_bytes for _, value in self._entries.values()) > self.max_bytes):
                self._entries.popitem(last=False)
        return True

    def get(self, engine, map_id, floor, *, resource_revision, recovery_floors=(),
            pose_index_enabled=False, pose_index_source=None):
        payloads = getattr(engine, 'qt_pose_static_payloads', None)
        if isinstance(payloads, PreparedStructureStaticPayloads):
            payloads.assert_current(engine, resource_revision, None)
        floors = tuple(dict.fromkeys((str(floor), *(str(value) for value in recovery_floors))))
        key = (id(engine), resource_revision, str(map_id), str(floor), floors,
               pose_index_enabled, pose_index_source)
        with self._lock:
            entry = self._entries.get(key)
            if entry is None or entry[0] is not engine:
                return None
            value, binding = entry[1], entry[1].binding
            if (id(getattr(engine, "qt_native_base", None)) != binding.base_identity
                    or id(getattr(engine, "qt_shared_provider", None)) != binding.provider_identity
                    or id(getattr(engine, "qt_fast_structure_source", None)) != binding.source_identity):
                self._entries.pop(key)
                return None
            self._entries.move_to_end(key)
            return value

    def invalidate(self):
        with self._lock:
            self._entries.clear()
            self._references.clear()

    @property
    def retained_bytes(self):
        with self._lock:
            return sum(value.retained_bytes for _, value in self._entries.values())
