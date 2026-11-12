"""Prepare bounded, immutable RAM inputs for the existing structure solvers.

Only preparation reads resources. Returned inputs contain no disk reader,
catalog guard or publication permission. The runtime owns resource revisions,
cancellation and result submission. A later preparation rechecks source bytes;
an existing snapshot deliberately keeps the bytes it was prepared from.
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

    def __call__(self, path):
        # Keep semantic preflight inside the component read scope. In
        # particular, the existing batch reader attaches the actual FileRef to
        # NPZ-header failures; optional consumers must not guess their origin.
        raw = self.reader.read_many((path,), expected_root=self.reader.root)[0]
        key = _path_key(path)
        previous = self.payloads.get(key)
        if previous is not None and previous[1] != raw:
            raise PoseInputsUnavailable("pose-input-bytes-changed-during-preparation")
        self.payloads[key] = (Path(path), bytes(raw))
        return raw

    def recheck(self):
        for path, raw in tuple(self.payloads.values()):
            if self.reader(path) != raw:
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


def _prepare_locators(bundle, pack, references, read, interfaces, root, map_id, floors):
    from difficult_entry_identity import MapEntryGeometryIdentity
    from difficult_side_entry_identity import MapLocalEntryIdentity
    from side_door_recognition import SideDoorRecognizer
    table = _ReferenceTable(MappingProxyType(dict(references)))
    directory = root / interfaces["identityEntries"]
    main = MapEntryGeometryIdentity(directory, structure_store=table,
        shared_reference_provider=table, profile_id=interfaces["identityProfile"],
        profile_for_map=pack.identity_profiles.__getitem__, read_resource=read)
    side = MapLocalEntryIdentity(directory, mode=pack.mode, floors=pack.floors,
        structure_store=table, shared_reference_provider=table,
        profile_id=interfaces["identityProfile"], profile_for_map=pack.identity_profiles.__getitem__,
        read_resource=read, typed_index_path=root / interfaces["typedIndex"],
        exit_icon_path=root / interfaces["exitIcon"] if bundle.semantic_side_entries else None,
        semantic_side_entries=bundle.semantic_side_entries,
        passport_source_available=bundle.passport_source_available)
    if main.error is not None:
        raise PoseInputsUnavailable("pose-input-main-locator:" + main.error)
    if side.side_door_error != bundle.side_entry_identity.side_door_error or side.side_search_error != bundle.side_entry_identity.side_search_error:
        raise PoseInputsUnavailable("pose-input-side-locator-capability-changed")
    passports = bundle.legacy_passports
    if passports is not None:
        from imported_resource_reader import decode_resource_json
        declaration = interfaces.get("legacyClean") or {}
        passports = decode_resource_json(read(root / declaration["passports"]))
        side.passports.extend(passports["entries"])
        side.entry_coverage.extend(passports["coverage"])
    # Finish the existing lazy icon decoder in the preparation stage.
    if side.semantic_side_entries:
        side._exit_recognizer = SideDoorRecognizer(side.exit_icon_path, read_resource=read)
    wanted = lambda item: item.get("mapId") == map_id and str(item.get("floor")) in floors
    main.entries = _freeze([row for row in main.entries if wanted(row[0])])
    side.entries = _freeze([row for row in side.entries if wanted(row[0])])
    side.registered_floors = frozenset((row[0]["mapId"], str(row[0]["floor"])) for row in side.entries)
    side.passports = _freeze([row for row in side.passports if wanted(row)])
    side.entry_coverage = _freeze([row for row in side.entry_coverage if wanted(row)])
    side.semantic_gaps = _freeze(side.semantic_gaps)
    side.side_doors = _freeze({key: value for key, value in side.side_doors.items() if key == map_id})
    side.side_search = _freeze({key: value for key, value in side.side_search.items()
                                if key[0] == map_id and key[1] in floors})
    if side._exit_recognizer is not None:
        side._exit_recognizer.exit_icon = _array(side._exit_recognizer.exit_icon)
        for reference in side._exit_recognizer.references:
            for name, value in vars(reference).items():
                setattr(reference, name, _freeze(value))
        side._exit_recognizer.references = tuple(side._exit_recognizer.references)
        side._exit_recognizer.taxonomy_by_map = _freeze(side._exit_recognizer.taxonomy_by_map)
    return main, side


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
    never to a pure numerical fit. This function always performs new checks;
    the separate bounded cache can reuse its immutable result in RAM.
    """
    started = time.perf_counter()
    if type(resource_revision) is not int or type(pose_index_enabled) is not bool:
        raise TypeError("pose-input-revision-and-pose-index-setting-required")
    bundle = getattr(engine, "qt_native_base", None)
    if not isinstance(bundle, NativeBaseBundle):
        raise PoseInputsUnavailable("pose-input-validated-native-base-required")
    provider, source = engine.qt_shared_provider, engine.qt_fast_structure_source
    check = engine.qt_assert_resource_current
    check()
    view, root, component, reader = bundle.read_binding()
    pack, interfaces, season = bundle.pack, bundle.interfaces, bundle.season
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
    read(root / view.component["entrypoint"])
    read(root / interfaces["identityPack"])
    read(root / interfaces["typedIndex"])
    from nightmare_author_coordinate_adapter import AuthorCoordinateAdapters
    coordinates = AuthorCoordinateAdapters(root / interfaces["coordinatePack"], read_resource=read)
    profiles = dict(pack.identity_profiles)
    info = tuple(_MapInfo(str(item.id), str(item.name)) for item in pack.maps)
    ram_pack = _Pack(pack.pack_id, pack.mode, tuple(pack.floors), info,
                     MappingProxyType({item.id: item for item in info}), MappingProxyType(profiles))
    references, prepared, preparations, matrices, gaps = {}, {}, {}, {}, []
    for target_floor in floors:
        files._check_cancelled(cancelled)
        key = (map_id, target_floor)
        current = provider("shared-structure", map_id, target_floor)
        if current is None:
            references[key], prepared[key] = None, None
            gaps.append("shared-reference-unavailable:" + target_floor)
            continue
        if (current.metadata.get("binding", {}).get("mapId"),
                str(current.metadata.get("binding", {}).get("floor"))) != key:
            raise PoseInputsUnavailable("pose-input-reference-binding-mismatch")
        reference = _new_reference(current, bundle, read, root)
        if reference is None:
            raise PoseInputsUnavailable("pose-input-reference-decode-unavailable")
        references[key] = _freeze_reference(reference)
        fast_available = reference.metadata.get("authorizesStructureValidation") is not False
        payload = source.read(current, cancelled=cancelled) if fast_available else None
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
    main, side = _prepare_locators(bundle, ram_pack, references, read, interfaces, root, map_id, floors)
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
    capabilities = _freeze(dict(rankReady=bundle.rank_ready,
        semanticSideEntries=bundle.semantic_side_entries,
        passportSourceAvailable=bundle.passport_source_available,
        mainError=main.error, sideDoorError=side.side_door_error,
        sideSearchError=side.side_search_error, passportError=side.passport_error))
    read.recheck()
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
    check()
    final_view, final_root, final_component, final_reader = bundle.read_binding()
    if (getattr(engine, "qt_native_base", None) is not bundle or engine.qt_shared_provider is not provider
            or engine.qt_fast_structure_source is not source or engine.qt_assert_resource_current is not check
            or (final_view, final_root, final_component, final_reader) != (view, root, component, reader)
            or source_binding != source_state()
            or any(getattr(provider, name) is not value for name, value in provider_fields.items())):
        raise PoseInputsUnavailable("pose-input-resource-binding-changed")
    files._check_cancelled(cancelled)
    binding = PoseInputBinding(season, pack.mode, component, resource_revision,
        id(engine), id(bundle), id(provider), id(source))
    locators = _LocatorTemplates(main, side)
    immutable_refs, immutable_prepared = MappingProxyType(references), MappingProxyType(prepared)
    adapters = PreparedCoordinates(coordinates.canonical_pack_id, MappingProxyType(matrices))
    retained = _bytes_used((immutable_refs, immutable_prepared, adapters, locators, grid, prior))
    return PreparedStructurePoseInputs(binding, map_id, floor, floors, immutable_refs, immutable_prepared,
        adapters, ram_pack, capabilities, tuple(gaps), started, time.perf_counter(), retained,
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
        retained = _bytes_used((references, inputs.preparations, inputs.adapters,
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
