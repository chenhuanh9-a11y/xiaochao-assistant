"""Prepare declared S1 common-coordinate data; reuse the original algorithms.

Only activation reads the component. Current-frame work consumes immutable RAM
references and the existing bounded common-pool builder. Runtime still owns the
round pool, acknowledged materials, identity and display publication.
"""
from dataclasses import replace
from pathlib import Path
import threading
from types import MappingProxyType, SimpleNamespace

from imported_native_base import (NativeBaseBundle, S1_COORDINATE_INTERFACE,
    S1_COORDINATE_FILES, read_s1_coordinate_assets)
from imported_native_context import ImportedResourceOwners
from shared_structure_reference import load_shared_structure
from structure_pose_inputs import PreparedCoordinates, _array, _bytes_used, _freeze_reference
import star_map_import as files


# The original provider's existing index constraint, not a new publication key.
EXPECTED_INDEX_SHA256 = '6fee640f5bc87d465459840626cfd5f88b52cdb9b888b58c1b03b46f1370cc19'
DEFAULT_REFERENCE_BYTES = 512 * 1024 * 1024
DEFAULT_POOL_BYTES = 512 * 1024 * 1024


class PreparedS1CoordinateRecognition:
    def __init__(self, engine, catalog, *, cancelled=None,
                 expected_index_sha256=None,
                 max_reference_bytes=DEFAULT_REFERENCE_BYTES, max_pool_cache_bytes=DEFAULT_POOL_BYTES):
        from s1_gate_anchor import GateAnchorLocator
        from s1_pose_prior import CoordinatePriorIndex
        from s1_private_experiment import PrivateCoordinateObserver
        if expected_index_sha256 is None:
            expected_index_sha256 = EXPECTED_INDEX_SHA256
        bundle = getattr(engine, 'qt_native_base', None)
        if (not isinstance(bundle, NativeBaseBundle) or catalog.season != 's1'
                or bundle.season != 's1' or bundle.mode != catalog.mode
                or engine.pack is not bundle.pack or tuple(sorted(engine.pack.by_id)) != catalog.map_ids
                or tuple(engine.pack.floors) != catalog.floors):
            raise ValueError('s1-coordinate-context-mismatch')
        if type(max_reference_bytes) is not int or max_reference_bytes < 0:
            raise ValueError('s1-coordinate-reference-byte-budget')
        self.engine, self.catalog, self._lock = engine, catalog, threading.RLock()
        self._base, self._provider, self._adapters = bundle, engine.qt_shared_provider, engine.qt_adapters
        self._check = engine.qt_assert_resource_current
        self._revision = getattr(engine, 'qt_resource_revision', None)
        self._owners = self._provider._read_operation_owners(bundle)
        if type(self._owners) is not ImportedResourceOwners:
            raise ValueError('s1-coordinate-resource-owners-unavailable')
        self._owner_state = self._owners.read_state()
        self._provider_state = self._provider._read_owner_state()
        files._check_cancelled(cancelled)
        self._check()
        view, root, component, reader = bundle.read_binding()
        directory = bundle.interfaces.get(S1_COORDINATE_INTERFACE)
        if directory is None:
            raise ValueError('s1-coordinate-recognition-not-declared')
        payloads = read_s1_coordinate_assets(view, reader, directory,
            mode=catalog.mode, map_ids=catalog.map_ids, floors=catalog.floors)
        asset_root = root / directory
        def asset_read(path):
            relative = Path(path).relative_to(asset_root).as_posix()
            if relative not in payloads:
                raise ValueError('s1-coordinate-asset-not-prepared')
            return payloads[relative]
        locator = GateAnchorLocator(asset_dir=asset_root, read_resource=asset_read)
        references, retained = {}, 0
        # Read actual guarded bytes, not the ordinary mutable provider cache.
        # The static refs are bounded separately; no floor pools are preheated.
        entries = bundle.resources.entries
        for mid in catalog.map_ids:
            for floor in catalog.floors:
                files._check_cancelled(cancelled)
                key = ('shared-structure', mid, 'variant-01', floor)
                entry = entries.get(key)
                if entry is None:
                    raise ValueError('s1-coordinate-reference-missing:' + mid + '/' + floor)
                reference = load_shared_structure(root / entry['resourcePath'],
                    expected_binding=dict(profile='shared-structure', mapId=mid, variant='variant-01', floor=floor),
                    resource_root=root, read_resource=reader)
                if reference is None:
                    raise ValueError('s1-coordinate-reference-unavailable:' + mid + '/' + floor)
                snapshot = _freeze_reference(reference)
                roles = snapshot.metadata.get('roles') or {}
                if roles.get('clean') == roles.get('validation'):
                    snapshot = replace(snapshot, validation_mask=snapshot.clean_mask)
                retained += _bytes_used(snapshot)
                if retained > max_reference_bytes:
                    raise ValueError('s1-coordinate-reference-memory-budget')
                references[mid, floor] = snapshot
        self.references = MappingProxyType(references)
        self.retained_reference_bytes = retained
        self.resource_files = tuple(directory + '/' + name for name in S1_COORDINATE_FILES)
        provider = lambda mid, floor: self.references.get((str(mid), str(floor)))
        self.prior = CoordinatePriorIndex(asset_root / S1_COORDINATE_FILES[0],
            expected_sha256=expected_index_sha256, mode=catalog.mode, resource_key=catalog.resource_key,
            provider=provider, read_resource=asset_read)
        # Preserve every existing metadata, shape and axis check before attach.
        for mid in catalog.map_ids:
            for floor in catalog.floors:
                files._check_cancelled(cancelled)
                self.prior._native_to_common(mid, floor)
        coordinates = PreparedCoordinates(self._adapters.canonical_pack_id,
            MappingProxyType({('shared-structure', mid, floor): _array(
                self._adapters.matrix_profile_to_canonical('shared-structure', mid, floor))
                for mid in catalog.map_ids for floor in catalog.floors}))
        geometry_engine = SimpleNamespace(pack=engine.pack, qt_adapters=coordinates,
            qt_shared_provider=lambda profile, mid, floor: provider(mid, floor))
        self.observer = PrivateCoordinateObserver(geometry_engine, catalog, asset_root,
            locator=locator, coordinate_index=self.prior.index, max_pool_cache_bytes=max_pool_cache_bytes)
        files._check_cancelled(cancelled)
        self._check()
        if not self.current():
            raise ValueError('s1-coordinate-resource-context-changed')

    def current(self):
        """Existing owner/revision checks only; no file/catalog reads."""
        engine = self.engine
        revision = getattr(engine, 'qt_resource_revision', None)
        if self._revision is None and revision is not None:
            # Factory publication assigns its existing revision after _build.
            self._revision = revision
        current = getattr(engine, 'qt_resource_context_current', None)
        return bool(engine.qt_native_base is self._base and engine.qt_shared_provider is self._provider
            and engine.qt_adapters is self._adapters and engine.qt_assert_resource_current is self._check
            and getattr(engine, 'qt_resource_revision', None) == self._revision
            and (current is None or current())
            and self._provider._read_operation_owners(self._base) is self._owners
            and self._owners.read_state() == self._owner_state
            and self._provider._read_owner_state() == self._provider_state)

    def _unavailable(self, ids, floor, reason):
        return dict(schemaVersion=1, mode=self.catalog.mode, resourceKey=self.catalog.resource_key,
            floor=str(floor), coordinateVerified=False, identityAuthorized=False,
            evaluatedMapIds=list(ids), contradictedMapIds=[], unknownMapIds=list(ids), reason=reason)

    def prepare(self, frame, pool_ids, floor, *, cancelled, stage=None):
        with self._lock:
            ids = tuple(pool_ids)
            if not ids or not set(ids) <= set(self.catalog.map_ids) or str(floor) not in self.catalog.floors:
                return self._unavailable(ids, floor, 'coordinate-scope-invalid')
            stopped = lambda: cancelled() or not self.current()
            if stopped():
                return self._unavailable(ids, floor, 'coordinate-work-obsolete')
            result = self.observer.prepare(frame, ids, str(floor), cancelled=stopped, stage=stage)
            return self._unavailable(ids, floor, 'coordinate-work-obsolete') if stopped() else result

    def pose_prior(self, source, floor, owner_scope):
        # Index, references and coordinate composition are read-only. Do not
        # wait for the mutable pool lane while runtime may own its state lock.
        if not self.current():
            return None
        hint = self.prior.make_hint(source, target_floor=str(floor), current_owner_scope=owner_scope)
        return hint if self.current() else None

    def fixed_fit(self, frame, map_id, floor, cancelled, *, report, observation_bounds=None):
        # reference_to_client only reads the index. Each fixed_fit owns a new
        # canonical host, so it shares no mutable pool or residual state.
        stopped = lambda: cancelled() or not self.current()
        if stopped():
            return dict(success=False, reason='structure-work-obsolete')
        fit = self.observer.fixed_fit(frame, map_id, str(floor), stopped,
            report=report, observation_bounds=observation_bounds)
        return dict(success=False, reason='structure-work-obsolete') if stopped() else fit


def attach_imported_s1_coordinate_recognition(engine, catalog, **preparation):
    """Missing optional data is an explicit capability gap, never fake enable."""
    engine.qt_s1_private_coordinate_prepare = engine.qt_s1_pose_prior = None
    engine.qt_s1_coordinate_recognition = None
    try:
        prepared = PreparedS1CoordinateRecognition(engine, catalog, **preparation)
    except (OSError, ValueError, KeyError) as error:
        capability = dict(available=False, gaps=[str(error)])
        engine.qt_s1_coordinate_capability = capability
        engine.qt_resource_inventory['s1CoordinateRecognition'] = dict(capability)
        return None
    engine.qt_s1_coordinate_recognition = prepared
    engine.qt_s1_private_coordinate_prepare, engine.qt_s1_pose_prior = prepared.prepare, prepared.pose_prior
    capability = dict(available=True, gaps=[], referenceLayers=len(prepared.references),
        retainedReferenceBytes=prepared.retained_reference_bytes,
        poolCacheMaxEntries=3, poolCacheMaxBytes=prepared.observer.filter.max_cache_bytes)
    engine.qt_s1_coordinate_capability = capability
    engine.qt_resource_inventory['s1CoordinateRecognition'] = dict(capability)
    return prepared
