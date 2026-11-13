"""Lazy, explicit-root production providers for saved-frame diagnostics.

No environment overrides, development roots, Tk assistant, captures or listeners.
``prepare`` and factory invocation belong on SessionService's worker.
"""
from __future__ import annotations

from collections import OrderedDict
from dataclasses import dataclass
import copy
import json
from pathlib import Path
import sys
import threading
import time
from types import MappingProxyType
from client_paths import APP_ROOT, PRODUCTION, runtime_paths

APP = str(APP_ROOT)
if APP not in sys.path:
    sys.path.insert(0, APP)


from season_context import CONTEXTS, SEASONS, RUNTIME_MODES, modes_for_season, known_modes_for_season, is_retired_mode, context_key, normalize_season, SeasonContext


@dataclass(frozen=True)
class ProviderResourceBinding:
    context: SeasonContext
    root: Path
    resource_revision: int | None = None


class ProviderUnavailable(RuntimeError):
    unavailable = True


class DisplayResourceChanged(ValueError):
    """The display consumer proved its actual resource/source owner changed."""


def _is_proven_display_resource_change(error):
    from imported_resource_catalog import ResourceCatalogError
    from star_map_import import ImportRejected
    if isinstance(error, ResourceCatalogError):
        return error.code == 'resource-catalog-superseded'
    return isinstance(error, ImportRejected) and error.code in (
        'resource-package-manifest-changed', 'resource-package-manifest-replaced',
        'resource-package-component-root-changed', 'resource-package-component-reference-changed',
        'imported-native-base-component-root-changed', 'imported-native-base-legacy-component-root-changed',
        'imported-controls-component-root-changed', 'imported-s1-controls-component-root-changed',
        'imported-difficult-native-context-base-bundle-mismatch', 'imported-s1-native-context-base-bundle-mismatch')

def _local(root, relative):
    path = (root / str(relative)).resolve()
    if not path.is_relative_to(root):
        raise ValueError(f"resource escapes explicit root: {relative}")
    return path


def _read(root, relative):
    path = _local(root, relative)
    if not path.is_file():
        raise ProviderUnavailable(f"missing-resource:{relative}")
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"resource metadata must be an object: {relative}")
    return data


def _build_memory_store(pack, memory_root, resource_root, *, production=PRODUCTION, season="s0", controls=None):
    """Original exemplar policy, with diagnostic stores/seeds kept isolated."""
    season = normalize_season(season)
    from nightmare_candidate import NightmareMemoryStore
    if season != "s0":
        validator = lambda frame: False  # No learning labels before validated S1 map controls.
    else:
        from s0_vertical_controls import observe_s0_map
        validator = lambda frame: observe_s0_map(frame, pack.mode, season=season,
                                                controls=controls)[0].get("detected") is True
    return NightmareMemoryStore(pack, memory_root, sample_validator=validator,
        portable_seed_path=Path(resource_root) / "references" / "nightmare-portable-layout-seeds.json"
        if production and season == "s0" else
        Path(resource_root) / ".qt-no-portable-seeds.json" if season == "s0" else None)


class _SharedReferenceReadOperation:
    """One bounded live operation; only a successful byte batch grants reads."""
    def __init__(self, provider, owners, binding, cancelled):
        self.provider, self.owners, self.cancelled = provider, owners, cancelled
        if binding is None:
            self.source = owners.bundle._values['fast_structure_source']
            self.source_state, self.paths = self.source._read_state(), None
        else:
            self.source, self.source_state, self.paths = binding
        self.provider_state, self.owner_state = provider._read_owner_state(), owners.read_state()
        self.closed = False
        self.assert_owned()
        provider._assert_current()
        self.assert_owned()
        self.legacy_keys = owners.legacy_keys()
        self.assert_owned()
        self.values = MappingProxyType(dict(owners.bundle._values))
        self.binding = (owners.bundle._view, owners.bundle._root,
                        self.values['component_ref'], self.values['reader'])
        self.assert_owned()

    def assert_owned(self):
        import star_map_import as files
        files._check_cancelled(self.cancelled)
        if (self.closed or self.provider._read_owner_state() != self.provider_state
                or self.owners.read_state() != self.owner_state
                or self.source._read_state() != self.source_state):
            raise ValueError('structure-identity-resource-owner-changed')

    def attach_batch(self, batch):
        """Advance an already checked preparation only after a real full read."""
        from imported_fast_structure import _PreparedReadBatch
        self.assert_owned()
        if type(batch) is not _PreparedReadBatch:
            return False  # The original guarded consumers retain first failures.
        source, state, paths = batch.verified_binding()
        if self.paths is not None or source is not self.source or state != self.source_state:
            raise ValueError('structure-identity-resource-owner-changed')
        self.paths = paths
        self.assert_owned()
        return True

    def read_reference(self, profile, map_id, floor, *, read_resource):
        return self._read_reference(profile, map_id, floor, read_resource=read_resource, fresh=False)[0]

    def read_fresh_reference(self, profile, map_id, floor, *, read_resource):
        """Return a fresh native decode and whether this operation proved it.

        Legacy/unknown lookups still use the original guarded provider path;
        their returned objects do not qualify for direct RAM preparation.
        """
        return self._read_reference(profile, map_id, floor, read_resource=read_resource, fresh=True)

    def _read_reference(self, profile, map_id, floor, *, read_resource, fresh):
        from imported_fast_structure import _PreparedReadBatch
        self.assert_owned()
        batch = getattr(read_resource, '__self__', None)
        if (type(batch) is not _PreparedReadBatch
                or getattr(read_resource, '__func__', None) is not _PreparedReadBatch.read_resource
                or batch.verified_binding() != (self.source, self.source_state, self.paths)):
            raise ValueError('structure-identity-resource-owner-changed')
        provider = self.provider
        # Only an admitted, disjoint native lookup may omit the legacy attempt.
        # Actual legacy and unknown lookups retain the original guarded loader.
        keys = [key for key in provider.versions if key[1] == str(map_id) and key[3] == str(floor)]
        if (str(map_id), str(floor)) in self.legacy_keys or len(keys) != 1:
            return provider.read_with_resource(profile, map_id, floor, read_resource=read_resource), False
        relative = self.source.reader.relative_path(provider.versions[keys[0]]['path'])
        if relative not in self.paths:
            raise ValueError('structure-identity-resource-owner-changed')
        return provider._load(profile, map_id, floor, read_resource=read_resource, operation=self,
                              force_fresh=fresh), fresh

    def finish(self):
        try:
            self.assert_owned()
            self.provider._assert_current()
            self.assert_owned()
        finally:
            self.closed = True


class SharedReferenceProvider:
    """Version-checked typed references; six-entry cache, no pose/UI state."""
    def __init__(self, root, resources, versions, *, legacy_reference_loader=None, check_current=None,
                 operation_owners=None):
        if check_current is not None and not callable(check_current):
            raise TypeError('reference lifetime guard must be callable')
        if legacy_reference_loader is not None and not callable(legacy_reference_loader):
            raise TypeError('legacy reference loader must be callable')
        self.root, self.resources, self.versions = root, resources, versions
        self._legacy_reference_loader = legacy_reference_loader
        self._check_current = check_current
        self._operation_owners = operation_owners
        self._cache = OrderedDict()
        self._lock = threading.RLock()
        from native_structure_display_material import NativeStructureDisplayMaterials
        self._display_materials = NativeStructureDisplayMaterials()
        self._managed_display_context = None

    def _assert_current(self):
        if self._check_current is not None:
            self._check_current()

    def __call__(self, profile, map_id, floor):
        return self._load(profile, map_id, floor)

    def batch_bindings(self, requests, *, operation=None):
        """Real native lookup paths; legacy references keep their own loader."""
        self._assert_read_current(operation)
        result = []
        for profile, map_id, floor in requests:
            keys = [key for key in self.versions if key[1] == str(map_id) and key[3] == str(floor)]
            if len(keys) == 1 and not self.versions[keys[0]].get("sharedError"):
                row = self.versions[keys[0]]
                result.append((row["binding"], row["path"]))
        self._assert_read_current(operation)
        return tuple(result)

    def read_with_resource(self, profile, map_id, floor, *, read_resource):
        """Use an operation reader with the original checks and cache behavior."""
        return self._load(profile, map_id, floor, read_resource=read_resource)

    def _read_owner_state(self):
        return tuple(id(getattr(self, name)) for name in ('root', 'resources', 'versions',
            '_legacy_reference_loader', '_check_current', '_operation_owners', '_cache', '_lock'))

    def _read_operation_owners(self, bundle):
        from imported_legacy_references import ImportedLegacyReferences
        from imported_native_context import ImportedResourceOwners
        owners, guard, legacy = self._operation_owners, self._check_current, self._legacy_reference_loader
        if (type(owners) is not ImportedResourceOwners
                or owners.bundle is not bundle or owners.base_view is not bundle._view
                or owners.controls._view is not owners.controls_view
                or self.resources is not bundle._values['resources']
                or self.versions is not bundle._values['versions']
                or getattr(guard, '__self__', None) is not owners
                or getattr(guard, '__func__', None) is not ImportedResourceOwners.assert_current):
            return None
        if ((owners.legacy is None and legacy is not None) or (owners.legacy is not None and (
                type(owners.legacy) is not ImportedLegacyReferences
                or getattr(legacy, '__self__', None) is not owners.legacy
                or getattr(legacy, '__func__', None) is not ImportedLegacyReferences.reference))):
            return None
        return owners

    def begin_prepare_operation(self, bundle, source, *, cancelled=None):
        """Check live owners once, without granting any byte-batch read yet."""
        owners = self._read_operation_owners(bundle)
        if owners is None or source is not bundle._values['fast_structure_source']:
            return None
        return _SharedReferenceReadOperation(self, owners, None, cancelled)

    def begin_read_operation(self, bundle, batch, *, cancelled=None, preparation=None):
        from imported_fast_structure import _PreparedReadBatch
        if preparation is not None:
            self._assert_read_current(preparation)
            if preparation.owners.bundle is not bundle:
                raise ValueError('structure-identity-resource-owner-changed')
            return preparation if preparation.attach_batch(batch) else None
        if type(batch) is not _PreparedReadBatch:
            return None
        owners = self._read_operation_owners(bundle)
        if owners is None:
            return None
        binding = batch.verified_binding()
        if binding[0] is not bundle._values['fast_structure_source']:
            return None
        return _SharedReferenceReadOperation(self, owners, binding, cancelled)

    def _assert_read_current(self, operation):
        if operation is None:
            self._assert_current()
        else:
            if type(operation) is not _SharedReferenceReadOperation or operation.provider is not self:
                raise ValueError('structure-identity-resource-owner-changed')
            operation.assert_owned()

    def _consume_reference(self, reference, operation, consume):
        try:
            return reference if consume is None else consume(reference)
        finally:
            # A display consumer reads the actual reference before this same
            # original post guard, including when its metadata read fails.
            self._assert_read_current(operation)

    def _freeze_native_display_reference(self, reference):
        from imported_resource_reader import ComponentResourceReader
        from shared_structure_reference import SharedStructureResourceStore
        from native_structure_display_material import freeze_native_reference
        owners = self._operation_owners
        bundle = getattr(owners, 'bundle', None)
        if bundle is None or self._read_operation_owners(bundle) is None:
            return reference
        reader, source = bundle._values['reader'], bundle._values['fast_structure_source']
        if (type(self.resources) is not SharedStructureResourceStore
                or getattr(self.resources.load, '__func__', None) is not SharedStructureResourceStore.load
                or type(reader) is not ComponentResourceReader
                or self.resources.read_resource is not reader or source.reader is not reader
                or reader.view is not bundle._view or source.view is not bundle._view
                or reader.root != bundle._root or source.root != bundle._root):
            return reference
        return freeze_native_reference(reference, self)

    def native_display_layer(self, reference, *, variant_id):
        from shared_structure_display import structure_display_layer
        owners = self._operation_owners
        source = owners.bundle._values['fast_structure_source']
        reader = owners.bundle._values['reader']
        scope = (self._read_owner_state(), owners.read_state(), source._read_state(), reader._owner_state())
        return self._display_materials.layer(reference, self, scope, variant_id, structure_display_layer)

    def _display_ram_state(self, engine, owners, source, reader):
        return (tuple(id(getattr(engine, name, None)) for name in (
            'qt_native_base', 'qt_shared_provider', 'qt_fast_structure_source', 'qt_controls_profile',
            'qt_native_context', 'qt_assert_resource_current', 'qt_resource_root')),
            engine.qt_context_key, engine.qt_season, self._read_owner_state(),
            owners.read_state(), source._read_state(), reader._owner_state())

    def _bind_managed_display(self, factory, engine, key, revision):
        """Reuse the real factory publication/current predicate, never a caller flag."""
        import weakref
        if (not isinstance(factory, EngineFactories) or factory._revision != revision
                or factory._engines.get(key) is not engine):
            return
        bundle = getattr(engine, 'qt_native_base', None)
        owners = self._read_operation_owners(bundle)
        if (owners is None or getattr(engine, 'qt_shared_provider', None) is not self
                or getattr(engine, 'qt_controls_profile', None) is not owners.controls
                or getattr(engine, 'qt_native_context', None) is not owners.native
                or getattr(engine, 'qt_assert_resource_current', None) is not self._check_current):
            return
        source, reader = bundle._values['fast_structure_source'], bundle._values['reader']
        current = engine.qt_resource_context_current
        self._managed_display_context = (weakref.ref(engine), current, revision, owners, source, reader,
            self._display_ram_state(engine, owners, source, reader))

    def _assert_managed_display_current(self, engine):
        binding = self._managed_display_context
        if binding is None or binding[0]() is not engine:
            return False
        _, current, revision, owners, source, reader, state = binding
        try:
            valid = (getattr(engine, 'qt_resource_context_current', None) is current
                and getattr(engine, 'qt_resource_revision', None) == revision and current()
                and self._display_ram_state(engine, owners, source, reader) == state)
        except (AttributeError, KeyError, TypeError):
            valid = False  # A once-admitted RAM owner/model has been replaced.
        if not valid:
            raise DisplayResourceChanged('display-resource-owner-changed')
        return True

    def _consume_display_reference(self, engine, map_id, floor, consume):
        """Consume admitted native RAM, or prepare it with the original guards.

        This does not use or grant byte-batch/fresh-read permission. Unknown
        readers, independent guards and real legacy lookups keep their caller.
        """
        from imported_resource_reader import ComponentResourceReader
        from native_structure_display_material import owns_native_arrays
        from shared_structure_display import structure_display_identity
        self._assert_managed_display_current(engine)
        bundle = getattr(engine, 'qt_native_base', None)
        owners = self._read_operation_owners(bundle)
        check, source = getattr(engine, 'qt_assert_resource_current', None), getattr(engine, 'qt_fast_structure_source', None)
        if (owners is None or getattr(engine, 'qt_shared_provider', None) is not self
                or check is not self._check_current
                or getattr(self._load, '__func__', None) is not SharedReferenceProvider._load):
            return NotImplemented
        reader = bundle._values['reader']
        if (source is not bundle._values['fast_structure_source'] or type(reader) is not ComponentResourceReader
                or self.resources.read_resource is not reader or source.reader is not reader
                or reader.view is not bundle._view or source.view is not bundle._view
                or reader.root != bundle._root or source.root != bundle._root):
            return NotImplemented
        if owners.legacy is not None and str(map_id) in owners.legacy._maps:
            return NotImplemented
        keys = [key for key in self.versions if key[1] == str(map_id) and key[3] == str(floor)]
        if len(keys) != 1 or self.versions[keys[0]].get('sharedError'):
            return NotImplemented
        key = keys[0]
        provider_state, owner_state = self._read_owner_state(), owners.read_state()
        source_state, reader_state = source._read_state(), reader._owner_state()

        def current_owners():
            if (getattr(engine, 'qt_shared_provider', None) is not self
                    or getattr(engine, 'qt_native_base', None) is not bundle
                    or getattr(engine, 'qt_assert_resource_current', None) is not check
                    or getattr(engine, 'qt_fast_structure_source', None) is not source
                    or self._read_owner_state() != provider_state or owners.read_state() != owner_state
                    or source._read_state() != source_state or reader._owner_state() != reader_state):
                raise DisplayResourceChanged('display-resource-owner-changed')

        reference, identity = None, None
        def consuming(actual):
            nonlocal reference, identity
            current_owners()
            reference, identity = actual, structure_display_identity(actual)
            return consume(actual)

        with self._lock:
            # Keep this bounded read/consumer/post-guard and its cache proof
            # together: ordinary readers may evict this key as soon as we leave.
            current_owners()
            managed = self._assert_managed_display_current(engine)
            cached = self._cache.get(key) if managed else None
            if cached is not None and owns_native_arrays(cached, self):
                # _load publishes these arrays only after its complete actual
                # decode/pre/post checks. Managed replacement invalidates the
                # factory predicate; consuming the held RAM needs no file IO.
                try:
                    value = consuming(cached)
                finally:
                    self._assert_managed_display_current(engine)
                    current_owners()
                self._cache.move_to_end(key)
            else:
                if managed and cached is not None:
                    # Engine construction may have cached an ordinary mutable
                    # decode. Prepare its real bytes once; never bless that RAM.
                    self._cache.pop(key)
                    self._display_materials.discard(cached)
                value = self._load('shared-structure', map_id, floor, consume=consuming)
            if reference is None:
                raise ValueError('display-reference-unavailable')
            self._assert_managed_display_current(engine)
            current_owners()
            if structure_display_identity(reference) != identity or self._cache.get(key) is not reference:
                raise DisplayResourceChanged('display-resource-changed')
            current_owners()
            return value

    def _load(self, profile, map_id, floor, *, read_resource=None, operation=None, force_fresh=False, consume=None):
        import numpy as np
        if force_fresh and operation is None:
            raise ValueError('structure-identity-resource-owner-changed')
        self._assert_read_current(operation)
        if operation is None and self._legacy_reference_loader is not None:
            legacy = self._legacy_reference_loader(map_id, floor)
            if legacy is not None:
                return self._consume_reference(legacy, operation, consume)
        keys = [key for key in self.versions if key[1] == str(map_id) and key[3] == str(floor)]
        if len(keys) != 1:
            return None
        key = keys[0]
        version = self.versions[key]
        if version.get("sharedError"):
            return None
        with self._lock:
            # Prepared RAM must originate in this operation's actual bytes,
            # since the ordinary provider cache has writable arrays/metadata.
            reference = None if force_fresh else self._cache.get(key)
            if reference is not None:
                value = self._consume_reference(reference, operation, consume)
                self._cache.move_to_end(key)
                return value
            if read_resource is None:
                reference = self.resources.load(key[0], key[1], key[3], variant=key[2])
            else:
                reference = self.resources.load(key[0], key[1], key[3], variant=key[2],
                                                read_resource=read_resource)
            if (reference is None or not np.any(reference.clean_mask)
                    or reference.path.resolve() != version["path"]
                    or reference.metadata.get("canvas") != version["canvas"]
                    or any(reference.metadata.get(name) != value for name, value in version["expected"].items())):
                return None
            if not force_fresh and operation is None and read_resource is None:
                reference = self._freeze_native_display_reference(reference)
            value = self._consume_reference(reference, operation, consume)
            if force_fresh:
                # Keep this mutable decode private until its caller freezes
                # it; ordinary cache readers must not acquire it in between.
                return value
            self._cache[key] = reference
            while len(self._cache) > 6:
                _, removed = self._cache.popitem(last=False)
                self._display_materials.discard(removed)
            return value

    def binding_metadata(self, profile, map_id, floor):
        self._assert_current()
        if self._legacy_reference_loader is not None:
            legacy = self._legacy_reference_loader(map_id, floor)
            if legacy is not None:
                self._assert_current()
                return dict(path=legacy.path, shape=list(legacy.shape), metadata=legacy.metadata)
        keys = [key for key in self.versions if key[1] == str(map_id) and key[3] == str(floor)]
        if len(keys) != 1:
            return None
        row = self.versions[keys[0]]
        if row.get("sharedError"):
            return None
        self._assert_current()
        return dict(path=row["path"], shape=row["canvas"]["shapeHW"],
                    metadata=dict(row["expected"], binding=row["binding"]))


def pose_inputs_current(engine, inputs):
    """Only RAM ownership checks; resource validation belongs to preparation."""
    binding = inputs.binding
    current = getattr(engine, 'qt_resource_context_current', None)
    return bool((current is None or current())
        and binding.engine_identity == id(engine)
        and binding.resource_revision == getattr(engine, 'qt_resource_revision', 0)
        and binding.base_identity == id(getattr(engine, 'qt_native_base', None))
        and binding.provider_identity == id(getattr(engine, 'qt_shared_provider', None))
        and binding.source_identity == id(getattr(engine, 'qt_fast_structure_source', None)))


def prepare_pose_solver(engine, map_id, floor, *, group=None, settings=None, steady=None,
                        steady_input=None, pose_priors=None, collect_fit_diagnostics=False,
                        usable_fit_callback=None, cancelled=None):
    """Prepare/reuse static data, then construct the unchanged numerical solver.

    The caller owns cancellation and result publication. Its returned preparation
    metadata is separate from numerical fit timing. Historical non-imported
    providers retain their public compatibility solver.
    """
    from structure_pose_solver import StructurePoseSolver, PreparedStructurePoseSolver
    options = dict(pose_priors=pose_priors,
        collect_fit_diagnostics=collect_fit_diagnostics, usable_fit_callback=usable_fit_callback)
    if getattr(engine, 'qt_native_base', None) is None:
        return StructurePoseSolver(engine, group, settings, steady=steady, **options), dict(kind='legacy-provider')
    if steady is not None:
        raise TypeError('native steady input must be prepared outside numerical fit')
    from structure_pose_inputs import prepare_structure_pose_inputs
    current = getattr(engine, 'qt_resource_context_current', None)
    if current is not None and not current():
        raise ProviderUnavailable('resource-context-superseded')
    if cancelled is not None and cancelled():
        raise ProviderUnavailable('pose-preparation-cancelled')
    settings = dict(settings or {})
    cache = engine.qt_pose_input_cache
    revision = getattr(engine, 'qt_resource_revision', 0)
    recovery = tuple(sorted(str(value) for value in getattr(group, 'verified_floors', ())))
    indexed = bool(settings.get('poseIndexCandidatesEnabled', settings.get('difficultGridEnabled', False)))
    started = time.perf_counter()
    with engine.qt_pose_prepare_lock:
        inputs = cache.get(engine, map_id, floor, resource_revision=revision,
            recovery_floors=recovery, pose_index_enabled=indexed)
        hit = inputs is not None
        if inputs is None:
            def obsolete():
                return (current is not None and not current()) or (cancelled is not None and cancelled())
            try:
                inputs = prepare_structure_pose_inputs(engine, map_id, floor,
                    resource_revision=revision, recovery_floors=recovery,
                    pose_index_enabled=indexed, cancelled=obsolete)
            except (OSError, ValueError):
                if current is not None and not current():
                    raise ProviderUnavailable('resource-context-superseded') from None
                if cancelled is not None and cancelled():
                    raise ProviderUnavailable('pose-preparation-cancelled') from None
                raise
        if not pose_inputs_current(engine, inputs):
            raise ProviderUnavailable('resource-context-superseded')
        if cancelled is not None and cancelled():
            raise ProviderUnavailable('pose-preparation-cancelled')
        if not hit:
            inputs = cache.share_references(inputs)
        retained = cache.put(engine, inputs) if not hit else True
    host = PreparedStructurePoseSolver(inputs, group, settings, steady_input=steady_input, **options)
    return host, dict(kind='prepared-ram', cacheHit=hit, cacheRetained=retained,
        elapsedMs=(time.perf_counter()-started)*1000, retainedBytes=inputs.retained_bytes,
        resourceRevision=revision)


def attach_structure_scanner(engine, catalog, *, private_fit=None):
    """Attach the shared scanner without choosing any resource fallback.

    Legacy private coordinates are supplied by their owning provider. Imported
    bases can use the same scanner with only their checked shared references.
    """
    from s1_structure_selection import StructureCandidateScanner
    from entry_evidence_work import FrameSnapshot, SharedObservationDependencies
    from client_structure_recognition import DirectStructureRecognition
    from client_local_structure_recognition import PreparedLocalStructureRecognition
    from recognition_race import RecognitionObservation, RecognitionRace

    # Engine assembly already runs on the preparation worker. Read the complete
    # static domain here, before any current-frame numerical recognition begins.
    direct = DirectStructureRecognition(engine, catalog)
    engine.qt_direct_structure_recognition = direct
    local = (PreparedLocalStructureRecognition(engine, catalog)
        if catalog.season == 's0' and getattr(engine, 'qt_native_base', None) is not None else None)
    engine.qt_local_structure_recognition = local
    coordinate = getattr(engine, 'qt_s1_coordinate_recognition', None) if catalog.season == 's1' else None
    race = RecognitionRace(clock=time.perf_counter) if local is not None or coordinate is not None else None
    engine.qt_recognition_race = race

    def check_current():
        if getattr(engine, 'qt_native_base', None) is not None:
            if not direct.current():
                raise ProviderUnavailable('resource-context-superseded')
            return
        check = getattr(engine, 'qt_assert_resource_current', None)
        if check is not None:
            check()

    def structure_scan(frame, *, floor, floor_source="operator-declared", cancelled=None, budget_seconds=None,
                       on_progress=None, candidate_order=None, preferred_map_id=None,
                       preferred_material_committed=None, private_coordinate_report=None, planned_map_ids=None,
                       first_material_committed=None, pose_priors=None, collect_fit_diagnostics=False,
                       on_entry_evidence=None, deadline=None, frame_snapshot=None,
                       on_direct_recognition=None, on_entry_observation=None,
                       identity_committed=None, on_coordinate_work=None,
                       on_coordinate_history=None, coordinate_pool_ids=None,
                       coordinate_pool_revision=None, coordinate_source_current=None):
        check_current()
        if getattr(engine, 'qt_native_base', None) is not None:
            # Admit the actual selection once before starting either worker.
            # Their numerical work and callbacks keep the existing RAM owner
            # checks; a cached engine alone cannot attest an updated catalog.
            source_current = getattr(engine, 'qt_assert_resource_current', None)
            if source_current is not None:
                source_current()
        if not getattr(engine, 'qt_rank_ready', True) and not getattr(engine, 'qt_structure_scan_ready', False):
            raise ProviderUnavailable(';'.join(getattr(engine, 'qt_rank_gaps', ())) or 'recognition-rank-unavailable')
        from structure_pose_solver import StructurePoseSolver
        host = StructurePoseSolver(engine, pose_priors=pose_priors,
            collect_fit_diagnostics=collect_fit_diagnostics)
        preparation_rows = []
        entry_work = None
        fixed = private_coordinate_report is not None and private_coordinate_report.get("coordinateVerified") is True
        if fixed and private_fit is None:
            raise ProviderUnavailable('private-coordinate-provider-unavailable')
        fit = (lambda f, mid, fl, cancel, **kw: private_fit(
            f, mid, fl, cancel, report=private_coordinate_report, **kw)) if fixed else host.fit
        native = getattr(engine, 'qt_native_base', None) is not None
        if native and not fixed:
            def fit(f, mid, fl, cancel, **kw):
                # Preparation owns resource reads; the unchanged numerical fit
                # consumes immutable RAM inputs and the scan's shared observation.
                from structure_pose_inputs import PoseInputsUnavailable
                started = time.perf_counter()
                try:
                    prepared, metadata = prepare_pose_solver(engine, mid, fl,
                        pose_priors=pose_priors, collect_fit_diagnostics=collect_fit_diagnostics,
                        cancelled=cancel)
                except (PoseInputsUnavailable, ProviderUnavailable) as error:
                    preparation_rows.append(dict(mapId=mid, floor=fl, kind='preparation-unavailable',
                        reason=str(error), elapsedMs=(time.perf_counter()-started)*1000))
                    return dict(success=False, reason=('structure-work-obsolete'
                        if not direct.current() or (cancel is not None and cancel()) else str(error)))
                row = dict(metadata, mapId=mid, floor=fl)
                preparation_rows.append(row)
                if not pose_inputs_current(engine, prepared.inputs) or (cancel is not None and cancel()):
                    return dict(success=False, reason='structure-work-obsolete')
                began = time.perf_counter()
                try:
                    if entry_work is not None and 'entry_observation' not in kw:
                        kw['entry_observation'] = entry_work
                    result = prepared.fit(f, mid, fl, cancel, **kw)
                finally:
                    row['fitMs'] = (time.perf_counter()-began)*1000
                    host.events.extend(prepared.events)
                    for name, count in prepared.diagnostic_event_counts.items():
                        host.diagnostic_event_counts[name] = host.diagnostic_event_counts.get(name, 0)+count
                if not pose_inputs_current(engine, prepared.inputs) or (cancel is not None and cancel()):
                    return dict(success=False, reason='structure-work-obsolete')
                return result
        prepare_fit = (None if fixed else
            lambda f, bounds: host.prepare_structure_scan(f, bounds, fit_callback=fit,
                automatic_region=catalog.season == 's0'))
        scanner = StructureCandidateScanner(catalog, fit, clock=time.perf_counter,
            prepare_fit=prepare_fit)
        if frame_snapshot is not None and (not isinstance(frame_snapshot, FrameSnapshot)
                or frame_snapshot.pixels is not frame):
            raise ValueError('structure-scan-frame-snapshot-mismatch')
        snapshot = frame_snapshot if frame_snapshot is not None else FrameSnapshot(frame)
        dependencies = (SharedObservationDependencies(snapshot, floor)
                        if local is not None and identity_committed is not None else None)
        identity_context = local.capture_identity_context() if local is not None else None
        began = time.perf_counter()
        direct_deadline = min(deadline if deadline is not None else float('inf'),
            began+budget_seconds if budget_seconds is not None else float('inf'))
        # The footprint algorithm remains an explicit diagnostic. Its unqualified
        # proposals do not run in the production identity race or delay startup.
        if on_direct_recognition is not None and direct.resources is None:
            direct.prepare()
        direct_run = (direct.start(snapshot, floor, cancelled=cancelled, deadline=direct_deadline)
            if on_direct_recognition is not None else None)
        direct_published = False

        def collect_direct():
            nonlocal direct_published
            if direct_run is None:
                return dict(status='not-requested', authorizesIdentity=False, lock=False)
            value = direct_run.collect()
            if not direct_published and value['status'] != 'pending':
                if not (cancelled is not None and cancelled()):
                    check_current()
                    if on_direct_recognition is not None:
                        on_direct_recognition(copy.deepcopy(value))
                direct_published = True
            return value

        def scan_candidate(stop, progress, entry):
            nonlocal entry_work
            def prepare_entry(snap, fl, **options):
                nonlocal entry_work
                # Each worker owns its caches. Source qualification and the
                # mature candidate plan are unchanged.
                query = (local._new_query_engine(dependencies=dependencies, cancelled=stop)
                         if local is not None else engine)
                if local is not None:
                    local.bind_query_context(query, identity_context)
                entry_work = query.prepare_entry_observation(snap, fl, **options)
                return entry_work
            def publish_entry(value):
                return entry(value, entry_observation=entry_work)
            return scanner.scan(snapshot.pixels, floor=floor, floor_source=floor_source,
                cancelled=stop, budget_seconds=budget_seconds,
                on_progress=progress, candidate_order=candidate_order,
                preferred_map_id=preferred_map_id,
                preferred_material_committed=(lambda mid: progress(None, material='preferred', map_id=mid))
                    if preferred_material_committed is not None else None,
                planned_map_ids=planned_map_ids,
                first_material_committed=(lambda mid: progress(None, material='first', map_id=mid))
                    if first_material_committed is not None else None,
                prepare_entry=prepare_entry if catalog.season == 's0' and native else None,
                on_entry_evidence=publish_entry, deadline=deadline, frame_snapshot=snapshot)

        def consume_entry(value, *, entry_observation=None, channel='candidate'):
            check_current()
            if cancelled is not None and cancelled():
                return False
            if on_entry_observation is not None:
                return on_entry_observation(value, entry_observation=entry_observation, channel=channel)
            return on_entry_evidence(value) if on_entry_evidence is not None else False

        def consume_coordinate(work, phase):
            check_current()
            if cancelled is not None and cancelled():
                return False
            return on_coordinate_work(work, phase=phase) if on_coordinate_work is not None else False

        def consume_progress(value=None, *, material=None, map_id=None,
                             coordinate_work=None, coordinate_phase=None):
            check_current()
            if cancelled is not None and cancelled():
                return False
            if coordinate_work is not None:
                return consume_coordinate(coordinate_work, coordinate_phase)
            if material is not None:
                callback = preferred_material_committed if material == 'preferred' else first_material_committed
                return callback(map_id) if callback is not None else False
            value['directStructureRecognition'] = collect_direct()
            return on_progress(value) if on_progress is not None else None

        try:
            # Only the runtime's actual commit predicate enables competition.
            # Legacy/custom callers keep the original synchronous candidate API.
            if (race is not None and identity_committed is not None
                    and (local is not None or on_coordinate_work is not None)):
                last_report = None
                candidate_terminal = None
                local_report = None
                errors = []
                def candidate_lane(observation, stop, progress, entry):
                    return scan_candidate(stop, progress, entry)
                def structure_lane(observation, stop, progress, entry):
                    if local is not None:
                        return local.run(snapshot, floor, cancelled=stop,
                            deadline=direct_deadline, on_entry=entry, identity_context=identity_context,
                            dependencies=dependencies)
                    from common_coordinate_observation import CommonCoordinateObservationWork
                    from entry_evidence_work import ObservationWorkScheduler
                    work = CommonCoordinateObservationWork(coordinate, snapshot, floor,
                        coordinate_pool_ids, coordinate_pool_revision, cancelled=stop,
                        source_cancelled=(lambda: not coordinate_source_current())
                            if coordinate_source_current is not None else stop)
                    try:
                        progress(None, coordinate_work=work, coordinate_phase='registered')
                        def observed(job):
                            if job.phase == 'common-coordinate':
                                choice = progress(None, coordinate_work=work, coordinate_phase='coordinate')
                                work.choose_owner_map(choice)
                        status = ObservationWorkScheduler(clock=time.perf_counter).drive(work,
                            cancelled=stop, deadline=direct_deadline, on_job_finished=observed)
                        if status == 'complete' and work.map_id is not None:
                            entry(None, coordinate_work=work)
                        return dict(status=status, algorithm='entrance-common-coordinate-filter',
                            floor=floor, frameKey=snapshot.frame_key, resourceKey=catalog.resource_key,
                            coordinateMs=work.coordinate_ms, fitMs=work.fit_ms,
                            reason=(work.report or {}).get('reason'),
                            evaluatedMapIds=list((work.report or {}).get('evaluatedMapIds', ())))
                    finally:
                        work.close()
                        # This callback only queues the actual report for the
                        # existing historical owner; it never publishes pixels.
                        if (work.coordinate_complete and not work.ledger_consumed
                                and on_coordinate_history is not None):
                            on_coordinate_history(work)
                def progress(lane, value=None, **options):
                    nonlocal last_report
                    result = consume_progress(value, **options)
                    if value is not None:
                        last_report = value
                    return result
                def entry(lane, value, *, coordinate_work=None, **options):
                    if coordinate_work is not None:
                        return consume_coordinate(coordinate_work, 'complete')
                    return consume_entry(value, channel=lane, **options)
                def terminal(lane, value):
                    nonlocal candidate_terminal, local_report
                    if lane == 'candidate':
                        candidate_terminal = value
                        consume_progress(value)
                    else:
                        local_report = value
                outcome = race.run(RecognitionObservation(snapshot, floor, catalog.resource_key),
                    candidate_lane, structure_lane, identity_committed=identity_committed,
                    on_progress=progress,
                    on_entry=entry,
                    on_terminal=terminal, on_error=lambda lane, error: errors.append((lane, error)),
                    cancelled=cancelled, deadline=direct_deadline)
                report = candidate_terminal if candidate_terminal is not None else scanner.unresolved_report(
                    snapshot, floor, floor_source, previous=last_report,
                    include_pending=outcome['status'] == 'identity-committed',
                    elapsed_seconds=time.perf_counter()-began)
                if (candidate_terminal is None and outcome['status'] == 'unresolved'
                        and time.perf_counter() >= direct_deadline):
                    report['status'] = 'budget-unresolved'
                if outcome['status'] in ('cancelled', 'closed', 'superseded'):
                    # Preserve the ordinary scanner cancellation protocol.
                    report = scanner.unresolved_report(snapshot, floor, floor_source,
                        elapsed_seconds=time.perf_counter()-began)
                    report.update(status='cancelled')
                if errors and candidate_terminal is None and outcome['status'] != 'identity-committed':
                    raise errors[0][1]
                # Queue results transfer by reference. The candidate terminal
                # may be this report itself, so diagnostics must not embed the
                # raw lane payload or retain its private observation working set.
                report['recognitionRace'] = dict(status=outcome['status'], winner=outcome['winner'],
                    lanes={name: dict(state=row['state'], cancelRequested=row['cancelRequested'],
                        **(dict(errorType=type(row['error']).__name__) if row.get('error') is not None else {}))
                        for name, row in outcome['lanes'].items()})
                report['localStructureRecognition'] = local_report
                if (outcome['status'] == 'identity-committed' and candidate_terminal is None
                        and report['supportedMapIds']):
                    # Transfer an already completed canonical pose before the
                    # locked map's material asks this ticket for it. Never wait
                    # for the losing worker or promote this pose to identity.
                    consume_progress(report)
            else:
                report = scan_candidate(cancelled, consume_progress, consume_entry)
            report['directStructureRecognition'] = collect_direct()
        finally:
            # A winning candidate/entry must not wait for the other kernel. The
            # single bounded worker cooperatively drains without late callbacks.
            if direct_run is not None:
                direct_run.cancel()
        check_current()
        report['posePreparation'] = preparation_rows
        report["posePriorOutcomes"] = [{key: event[key] for key in
            ("mapId", "floor", "accepted", "fallback", "elapsedMs") if key in event}
            for event in host.events if event.get("event") == "experimental-pose-prior"][:6]
        if collect_fit_diagnostics and report['status'] != 'cancelled':
            try:
                from s1_fit_diagnostics import scan_summary
                report['fitEvidence'] = scan_summary(host.events, mode=catalog.mode,
                    resource_key=catalog.resource_key, floor=floor, frame_shape=frame.shape,
                    event_counts=host.diagnostic_event_counts, outcomes=report.get('outcomes', ()))
            except Exception:
                pass  # Evidence cannot turn a solver result into a failure.
        return report

    def structure_rank(frame, *, floor, budget_seconds=None):
        report = structure_scan(frame, floor=floor, budget_seconds=budget_seconds)
        engine.last_s1_structure_scan = report
        engine.last_difficult_evidence = {key: value for key, value in report.items() if key != "poses"}
        return StructureCandidateScanner.rank_rows(report, engine.pack)

    engine.qt_structure_scan = structure_scan
    if catalog.season == 's1':
        engine.qt_s1_structure_scan = structure_scan
        engine.qt_s1_structure_rank = structure_rank
    return engine


class EngineFactories(dict):
    """A mapping of lazy per-mode factories plus cached capability reports.

    Construction performs no resource IO. ``prepare`` validates/builds on the
    caller's worker. Until then modes report resource-validation-pending.
    """
    MODES = ("nightmare", "difficult")  # Legacy S0 dict facade remains unchanged.
    cache_preparation_failures = True

    def __init__(self, resource_root, *, context_roots=None, nightmare_interfaces=None,
                 memory_root_provider=None, optional_displays=True, controls_profile=None):
        self.root = Path(resource_root).resolve()
        roots = dict(context_roots or {})
        if not set(roots) <= set(CONTEXTS):
            raise ValueError("unknown explicit provider root context")
        self.context_roots = MappingProxyType({key: Path(value).resolve() for key, value in roots.items()})
        self.nightmare_interfaces = MappingProxyType(dict(nightmare_interfaces or {}))
        if memory_root_provider is not None and not callable(memory_root_provider):
            raise TypeError('memory root provider must be callable')
        self.memory_root_provider = memory_root_provider
        self.optional_displays = bool(optional_displays)
        self.controls_profile = controls_profile
        self._lock = threading.RLock()
        self._build_locks = {key: threading.Lock() for key in CONTEXTS}
        self._engines = {}
        self._custom_display = None
        self._base_display = None
        self._revision = 0
        self._reports = self._initial_reports()
        super().__init__({mode: lambda mode=mode: self._get(mode) for mode in self.MODES})

    def _initial_reports(self):
        return {context_key(mode, season): dict(available=False, dataReady=False, rankReady=False,
                    prepared=not CONTEXTS[context_key(mode, season)].registered,
                    season=season, mode=mode, contextKey=context_key(mode, season),
                    reason="retired-mode" if is_retired_mode(mode, season) else "resource-validation-pending" if CONTEXTS[context_key(mode, season)].registered else f"season-resources-unavailable:{season}")
                for season in SEASONS for mode in known_modes_for_season(season)}

    def capabilities(self, season="s0"):
        season = normalize_season(season)
        with self._lock:
            return {mode: copy.deepcopy(self._reports[context_key(mode, season)]) for mode in known_modes_for_season(season)}

    def context_capabilities(self):
        with self._lock:
            return copy.deepcopy(self._reports)

    def get_engine(self, mode, season="s0"):
        return self._get(mode, season)

    def set_custom_display(self, provider, *, base_provider=None):
        """Attach a display-only source; identity and geometry remain untouched."""
        if provider is not None and not callable(provider):
            raise TypeError("custom display provider must be callable")
        if base_provider is not None and not callable(base_provider):
            raise TypeError("base display provider must be callable")
        with self._lock:
            self._custom_display, self._base_display = provider, base_provider
            for key, engine in self._engines.items():
                season, mode = key.split(":", 1)
                self._attach_custom_display(engine, mode, season)

    def _attach_custom_display(self, engine, mode, season="s0"):
        if normalize_season(season) != "s0":
            engine.qt_custom_display = engine.qt_base_display = None
            return
        provider = self._custom_display
        engine.qt_custom_display = (lambda profile, map_id, floor, reference:
            provider(profile, map_id, floor, reference, mode)) if provider is not None else None
        base = self._base_display
        engine.qt_base_display = (lambda map_id, floor, reference:
            base(map_id, floor, reference, mode)) if base is not None else None

    def prepare(self, cancelled=None, *, season="s0"):
        for mode in modes_for_season(season):
            if cancelled and (cancelled.is_set() if hasattr(cancelled, "is_set") else cancelled()):
                break
            try:
                self._get(mode, season)
            except ProviderUnavailable:
                pass
        return self.capabilities(season)

    def prepare_context(self, mode, cancelled=None, *, season="s0"):
        """Prepare only the applied mode; other modes retain explicit pending state.

        Full inventory/self-check callers keep using prepare(). The client's
        existing context-change scheduler prepares another mode when selected.
        """
        if mode not in known_modes_for_season(season):
            raise ValueError("unknown resource preparation context")
        if not (cancelled and (cancelled.is_set() if hasattr(cancelled, "is_set") else cancelled())):
            try:
                self._get(mode, season)
            except ProviderUnavailable:
                pass
        return self.capabilities(season)

    def invalidate(self):
        """Nonblocking resource-lifetime boundary; old worker builds cannot publish."""
        with self._lock:
            self._revision += 1
            for engine in self._engines.values():
                race = getattr(engine, 'qt_recognition_race', None)
                if race is not None:
                    race.close()
                direct = getattr(engine, 'qt_direct_structure_recognition', None)
                if direct is not None:
                    direct.close()
                cache = getattr(engine, 'qt_pose_input_cache', None)
                if cache is not None:
                    cache.invalidate()
            self._engines.clear()
            self._reports = self._initial_reports()

    def _resource_binding(self, mode, season="s0"):
        """Resolve one declared context, never discover or combine season roots.

        S0 keeps its legacy root. S1 resolves only an exact native mode root and
        validates its declaration before cache lookup; no directory discovery.
        """
        context = SeasonContext(season=season, mode=mode)
        if is_retired_mode(mode, season):
            raise ProviderUnavailable("retired-mode")
        if not context.registered:
            raise ProviderUnavailable(f"season-resources-unavailable:{season}")
        if season == "s1":
            from s1_native_resources import registration
            root = self.context_roots.get(context.key, self.root / "seasons/s1" / mode)
            try:
                registration(root, mode)
            except (OSError, ValueError, KeyError, TypeError) as error:
                raise ProviderUnavailable(f"season-resources-unavailable:s1:{mode}:{error}") from error
            return ProviderResourceBinding(context=context, root=root)
        return ProviderResourceBinding(context=context, root=self.context_roots.get(context.key, self.root))

    def _resource_failure_fields(self, error):
        return {}

    @staticmethod
    def _engine_capability_fields(engine, season):
        rank_ready = bool(getattr(engine, 'qt_rank_ready', True))
        rank_gaps = list(getattr(engine, 'qt_rank_gaps', ())) if not rank_ready else []
        if not rank_ready and not rank_gaps:
            rank_gaps = ['recognition-rank-unavailable']
        live_gaps = list(getattr(engine, 'qt_live_gaps', ()))
        if not rank_ready:
            live_gaps = list(dict.fromkeys(live_gaps + rank_gaps))
        return dict(available=rank_ready, dataReady=True, rankReady=rank_ready, rankGaps=rank_gaps,
            reason='saved-frame-rank-ready' if rank_ready else 'recognition-data-only',
            liveGaps=live_gaps, liveReady=rank_ready and bool(getattr(engine, 'qt_live_ready', season == 's0')))

    @staticmethod
    def _unavailable_engine_fields(gap):
        return dict(available=False, dataReady=False, rankReady=False, rankGaps=[gap],
                    liveReady=False, liveGaps=[gap])

    def _get(self, mode, season="s0"):
        season = normalize_season(season)
        try:
            binding = self._resource_binding(mode, season)
        except ProviderUnavailable as error:
            with self._lock:
                if getattr(error, 'resource_revision', self._revision) == self._revision:
                    key = context_key(mode, season)
                    self._reports[key].update(prepared=True, reason=str(error),
                        **self._unavailable_engine_fields('resource-context-unavailable'),
                        **self._resource_failure_fields(error))
            raise
        key = binding.context.key
        with self._build_locks[key]:
            with self._lock:
                if binding.resource_revision is not None and binding.resource_revision != self._revision:
                    raise ProviderUnavailable('resource-context-superseded')
                if key in self._engines:
                    self._reports[key].update(prepared=True,
                        **self._engine_capability_fields(self._engines[key], season))
                    return self._engines[key]
                if self._reports[key]["prepared"] and self.cache_preparation_failures:
                    raise ProviderUnavailable(self._reports[key]["reason"])
                revision = self._revision
            try:
                engine = self._build(mode, season=season)
            except Exception as error:
                reason = f"{mode}:{type(error).__name__}:{error}"
                with self._lock:
                    if revision == self._revision:
                        self._reports[key] = dict(prepared=True, reason=reason,
                            season=season, mode=mode, contextKey=key,
                            **self._unavailable_engine_fields('resource-validation-failed'),
                            **self._resource_failure_fields(error))
                    else:
                        reason = 'resource-context-superseded'
                raise ProviderUnavailable(reason) from error
            with self._lock:
                if revision != self._revision:
                    raise ProviderUnavailable("resource-context-superseded")
                engine.qt_season = season
                engine.qt_context_key = key
                engine.qt_resource_revision = revision
                def context_current(engine=engine, key=key, revision=revision):
                    with self._lock:
                        return self._revision == revision and self._engines.get(key) is engine
                engine.qt_resource_context_current = context_current
                self._engines[key] = engine
                provider = getattr(engine, 'qt_shared_provider', None)
                if type(provider) is SharedReferenceProvider:
                    provider._bind_managed_display(self, engine, key, revision)
                self._attach_custom_display(engine, mode, season)
                self._reports[key] = dict(prepared=True,
                                       season=season, mode=mode, contextKey=key,
                                       mapCount=len(engine.pack.maps), floors=list(engine.identity_floors),
                                       liveAuthorization=False, resourceRoot=str(binding.root),
                                       floorObservation=dict(getattr(getattr(engine, "qt_native_context", None),
                                                                     "floor_observation_capability", {})),
                                       inventory=getattr(engine, "qt_resource_inventory", {}),
                                       candidateObservation=dict(getattr(getattr(engine, "qt_native_context", None), "structure_candidate_capability", {})),
                                       **self._engine_capability_fields(engine, season))
            return engine

    def _build(self, mode, *, season="s0"):
        binding = self._resource_binding(mode, season)
        # All heavyweight production imports and resource parsing are worker-only.
        from author_route_catalog import AuthorRouteCatalog
        from map_identity_engine import MapIdentityEngine
        from nightmare_author_coordinate_adapter import AuthorCoordinateAdapters
        from nightmare_candidate import NightmarePack
        from shared_structure_reference import SharedStructureResourceStore
        from shared_structure_context import load_shared_version_context

        root = binding.root
        native = None
        catalog_error = ""
        if season == "s1":
            from s1_native_resources import S1NativeContext
            from difficult_native_identity import NativeDifficultPack, NativeDifficultCandidateEngine
            native = S1NativeContext(root, mode)
            pack = NativeDifficultPack(root, native.interfaces["identityPack"], display_context=native,
                                       expected_mode=mode, expected_season=season)
            native.validate_identity_pack(pack)
            resources, adapters, catalog = native.resources, native.adapters, native.catalog
            entry_relative = native.interfaces["identityEntries"]
            entry_root = native.local(entry_relative)
            neutral, version_pack = native.neutral_relative, native.version_pack
            profile, exit_icon = "shared-structure", None
            engine_type = NativeDifficultCandidateEngine
        elif mode == "nightmare":
            interfaces = dict(identityPack="references/nightmare-map-pack.json",
                typedIndex="references/nightmare-shared-structure-index.json",
                coordinatePack="references/nightmare-canonical-coordinate-pack.json",
                neutralDisplay="references/nightmare-structure-display.json",
                identityEntries="references/nightmare-entries",
                exitIcon="references/difficult-exit-icon.json", identityProfile="zhan10-speed")
            interfaces.update(self.nightmare_interfaces)
            raw = _read(root, interfaces['identityPack'])
            # NightmarePack supports developer/environment source roots. Reject
            # those manifests before constructing it, keeping every path local.
            if not raw.get("maps") or any(row.get("resourceBase") != "resource-root" for row in raw["maps"]):
                raise ValueError("nightmare manifest must bind resource-root for every map")
            for row in raw["maps"]:
                for relative in [row["oneFlow"], *row["layers"].values()]:
                    _local(root, relative)
            pack = NightmarePack(_local(root, interfaces['identityPack']), resource_root=root)
            resources = SharedStructureResourceStore(_local(root, interfaces['typedIndex']), root)
            if resources.error:
                raise ValueError(resources.error)
            adapters = AuthorCoordinateAdapters(_local(root, interfaces['coordinatePack']))
            catalog = None
            # Author display is optional and never recognition evidence.
            catalog_path = _local(root, "references/nightmare-author-route-catalog.json")
            if self.optional_displays and catalog_path.is_file():
                try:
                    catalog = AuthorRouteCatalog(catalog_path, asset_base=root)
                except (OSError, ValueError, KeyError, TypeError) as error:
                    # Match the original typed-resource path: optional display
                    # metadata must not disable independently validated geometry.
                    catalog_error = f"{type(error).__name__}:{error}"
            entry_relative = interfaces['identityEntries']
            entry_root = _local(root, entry_relative)
            neutral = interfaces['neutralDisplay']
            profile = interfaces['identityProfile']
            exit_icon = _local(root, interfaces['exitIcon']) if interfaces['exitIcon'] else None
            version_pack = pack
            engine_type = MapIdentityEngine
        else:
            interfaces = _read(root, "difficult-native-manifest.json").get("runtimeInterfaces") or {}
            if "identityPack" not in interfaces:
                raise ProviderUnavailable("unsupported-contract:difficult native identityPack not declared")
            # Validate every runtime path before the existing context touches it.
            for name, value in interfaces.items():
                if isinstance(value, str):
                    _local(root, value)
            from difficult_native_context import DifficultNativeContext
            from difficult_native_identity import NativeDifficultPack, NativeDifficultCandidateEngine
            from client_paths import bundled_author_root
            native = DifficultNativeContext(root, display_fallback_root=bundled_author_root() if season == "s0" else None)
            pack = NativeDifficultPack(root, interfaces["identityPack"], display_context=native)
            native.validate_identity_pack(pack)
            resources, adapters, catalog = native.resources, native.adapters, native.catalog
            entry_root = native.local(interfaces["identityEntries"])
            neutral, entry_relative = native.neutral_relative, ""
            profile, exit_icon = "shared-structure", native.local(interfaces["exitIcon"])
            version_pack = native.version_pack
            engine_type = NativeDifficultCandidateEngine
        for name in ("entries.json", "side-entries.json"):
            _read(root, entry_root.relative_to(root) / name)
        if exit_icon is not None and not exit_icon.is_file():
            raise ProviderUnavailable(f"missing-resource:{exit_icon.relative_to(root)}")
        # Production ranking may read the original exemplar/history store.
        # Constructor performs no IO; ranking submits no human-label writes.
        # Explicit live manual confirmation reuses this store on its worker.
        # Source/test diagnostics retain isolated memory.
        memory_root = (self.memory_root_provider(mode, season) if self.memory_root_provider is not None else
                       runtime_paths().memory_root(mode, season) if PRODUCTION else
                       root / ".qt-saved-frame-memory" / mode if season == "s0" else
                       root / ".qt-saved-frame-memory" / season / mode)
        memory = _build_memory_store(pack, memory_root, root, season=season, controls=self.controls_profile)
        provider = SharedReferenceProvider(root, resources, {},
            legacy_reference_loader=native.legacy_reference if native is not None else None)
        engine = engine_type(pack, identity_root=entry_root, identity_profile_id=profile,
            identity_floors=pack.floors, exit_icon_path=exit_icon,
            entry_structure_store=resources, entry_shared_reference_provider=provider,
            shared_reference_loader=provider, entry_context_is_current=lambda: True,
            memory_store=memory, native_similarity_root=root,
            native_similarity_binding_provider=provider.binding_metadata,
            structure_profile_provider=lambda: "shared-structure",
            structure_cache_context_provider=lambda: "")
        if engine.entry_identity.error:
            raise ValueError(f"entry-index-unavailable:{engine.entry_identity.error}")
        if engine.side_entry_identity is None:
            raise ValueError(f"side-entry-index-unavailable:{engine.side_entry_error}")
        if native is not None:
            native.attach_legacy_entry_metadata(engine.side_entry_identity)
        versions = load_shared_version_context(root, version_pack, resources, adapters, catalog,
            engine.side_entry_identity.entries, neutral_relative=neutral,
            author_profile_id="suju" if mode == "nightmare" else "zhan10-speed",
            entry_root_relative=entry_relative)
        failures = [f"{key[1]}/{key[3]}:{row['sharedError']}" for key, row in versions.items() if row.get("sharedError")]
        if not versions or failures:
            raise ValueError("shared-resource-contract:" + ";".join(failures[:3] or ["empty-version-inventory"]))
        if native is not None:
            native.validate_versions(versions)
        provider.versions = versions
        # Decode each typed product through its original loader, one at a time;
        # the bounded provider cache prevents retaining an entire mask atlas.
        for key in versions:
            if provider(key[0], key[1], key[3]) is None:
                raise ValueError(f"typed-product-unavailable:{key[1]}/{key[3]}")
        similarity = engine.native_similarity_index
        if similarity.error:
            raise ValueError(f"native-similarity-unavailable:{similarity.error}")
        engine.qt_resource_inventory = dict(typedLayers=len(versions),
            mainEntries=len(engine.entry_identity.entries), sideEntries=len(engine.side_entry_identity.entries),
            declaredMissingNativeLayers=(native.unbound_layers if native is not None else []),
            optionalAuthorCatalogError=catalog_error,
            optionalSideDoorError=engine.side_entry_identity.side_door_error,
            optionalSideSearchError=engine.side_entry_identity.side_search_error)
        engine.qt_shared_provider = provider
        # Local compatibility cohorts remain recognition evidence only. The
        # separately confirmed list is scoped by Runtime to F1 front entries.
        from client_group_registry import ConfirmedFrontGroups
        engine.qt_logical_group_registry = ConfirmedFrontGroups(pack, season=season)
        engine.qt_resource_root = root
        if season == "s1":
            # Separate complete schema2 inventory root from selected mode root.
            engine.qt_s1_index_resource_root = self.root
        engine.qt_adapters = adapters
        engine.qt_controls_profile = self.controls_profile
        engine.qt_catalog = catalog
        engine.qt_native_context = native
        engine.qt_season = season
        engine.qt_live_gaps = tuple(getattr(native, "live_gaps", ()))
        engine.qt_live_ready = not engine.qt_live_gaps
        if season == "s1":
            from s1_author_displays import S1AuthorDisplays
            if self.optional_displays:
                optional = S1AuthorDisplays(self.root, native)
                engine.qt_optional_displays = optional
                engine.qt_adapters, engine.qt_catalog = optional.adapters, optional.catalog
                engine.qt_resource_inventory["optionalAuthorErrors"] = dict(optional.errors)
            else:
                engine.qt_optional_displays = None
            from s1_structure_selection import StructureCatalog
            catalog = StructureCatalog.from_engine(engine)
            private_observer = None
            def private_coordinate_prepare(frame, pool_ids, floor, *, cancelled, stage=None):
                nonlocal private_observer
                from client_paths import RESOURCE_ROOT
                from s1_private_experiment import PrivateCoordinateObserver
                if private_observer is None:
                    private_observer = PrivateCoordinateObserver(engine, catalog, RESOURCE_ROOT / "s1-private-assets")
                return private_observer.prepare(frame, pool_ids, floor, cancelled=cancelled, stage=stage)
            engine.qt_s1_private_coordinate_prepare = private_coordinate_prepare
            pose_index = None
            def pose_prior(source, floor, owner_scope):
                nonlocal pose_index
                from client_paths import RESOURCE_ROOT
                from s1_pose_prior import CoordinatePriorIndex
                if pose_index is None:
                    pose_index = CoordinatePriorIndex(
                        RESOURCE_ROOT / "s1-private-assets" / "s1_entrance_coordinate_index.json",
                        expected_sha256="6fee640f5bc87d465459840626cfd5f88b52cdb9b888b58c1b03b46f1370cc19",
                        mode=catalog.mode, resource_key=catalog.resource_key,
                        provider=lambda mid, fl: engine.qt_shared_provider("shared-structure", mid, fl))
                return pose_index.make_hint(source, target_floor=floor, current_owner_scope=owner_scope)
            engine.qt_s1_pose_prior = pose_prior
            attach_structure_scanner(engine, catalog,
                private_fit=lambda *args, **kwargs: private_observer.fixed_fit(*args, **kwargs))
            return engine
        if not self.optional_displays:
            # New resource catalogs own displays separately from base geometry.
            engine.qt_optional_displays = None
            return engine
        from optional_author_displays import OptionalAuthorDisplays
        optional = OptionalAuthorDisplays(root, pack, catalog, adapters, versions, mode=mode)
        from s1_author_displays import S1AuthorDisplays
        from types import SimpleNamespace
        generic = S1AuthorDisplays(self.root, SimpleNamespace(root=root, mode=mode,
            catalog=optional.catalog, adapters=optional.adapters), season=season, fallback=optional)
        engine.qt_resource_inventory["optionalAuthorErrors"] = dict(generic.errors)
        if generic.profiles - optional.profiles:
            optional = generic
        engine.qt_optional_displays = optional
        engine.qt_adapters, engine.qt_catalog = optional.adapters, optional.catalog
        return engine


def build_engine_factories(resource_root, *, context_roots=None, nightmare_interfaces=None,
                           memory_root_provider=None, optional_displays=True, controls_profile=None):
    """Pass the exact sandbox ``_internal`` path; no parent/root discovery."""
    return EngineFactories(resource_root, context_roots=context_roots,
        nightmare_interfaces=nightmare_interfaces, memory_root_provider=memory_root_provider,
        optional_displays=optional_displays, controls_profile=controls_profile)


def bind_identity_context(engine, *, priority, context):
    """Bind immutable owner snapshots to the existing ranker's temporal seams."""
    if not callable(priority) or not callable(context):
        raise TypeError("identity context providers must be callable")
    engine.native_similarity_priority_provider = priority
    engine.native_similarity_context_provider = context
