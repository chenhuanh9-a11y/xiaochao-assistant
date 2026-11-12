"""Default catalog factories with explicit external-data admission.

An empty store is ordinary unavailable data. In particular, a component root
must not enter a legacy loader that assumes trusted IDs and nested paths.
Presence is reported separately from algorithm readiness.
"""
from copy import deepcopy
import re
import threading

from season_context import SeasonContext, context_key, is_retired_mode
from client_providers import EngineFactories, ProviderResourceBinding, ProviderUnavailable


# Capabilities are granted by completed program decoders, never package claims.
SUPPORTED_CAPABILITIES = frozenset({'native-s0-nightmare-base-v1', 'native-s0-difficult-base-v1', 's0-signed-templates-v1',
    'native-s1-difficult-base-v1', 'native-s1-nightmare-base-v1', 's1-signed-column-geometry-v1'})


class ImportedProviderUnavailable(ProviderUnavailable):
    def __init__(self, reason, gaps, *, resource_revision=None):
        super().__init__(reason)
        self.resource_gaps = tuple(gaps)
        if resource_revision is not None:
            self.resource_revision = resource_revision


def load_native_resources(base_view, controls_view, *, season='s0', mode='nightmare', require_rank=False):
    """Decode one base/control context and its guarded reference provider.

    This data assembly creates no engine, memory store or scanner. Callers own
    its operation/catalog lifetime; images use the same real legacy references
    as ranking consumers without receiving ranking or live permission.
    """
    from imported_controls_profile import S0ControlsProfile
    from imported_s1_controls_profile import S1ControlsProfile
    from imported_s1_native_context import ImportedS1NativeContext
    from imported_difficult_native_context import ImportedDifficultNativeContext
    from imported_native_base import decode_native_base
    from client_providers import SharedReferenceProvider

    bundle = decode_native_base(base_view, season=season, mode=mode)
    if not bundle.ready:
        raise ImportedProviderUnavailable('native-base-incomplete', bundle.gaps)
    rank_ready = bundle.rank_ready
    if require_rank and not rank_ready:
        raise ImportedProviderUnavailable('native-base-rank-unavailable', ('native-base-rank-unavailable',))
    if controls_view.component['dependencies']:
        raise ImportedProviderUnavailable('controls-dependencies-unsupported', ('controls-dependencies-unsupported',))
    controls = S1ControlsProfile(controls_view) if season == 's1' else S0ControlsProfile(controls_view)
    context = controls.for_context(season, mode)
    pack, root = bundle.pack, base_view.root
    if context is None or set(context.contract.floor_ids) != set(pack.floors):
        raise ImportedProviderUnavailable('controls-base-floors-mismatch', ('controls-base-floors-mismatch',))

    if season == 's1':
        native = ImportedS1NativeContext(base_view, bundle, controls)
    elif mode == 'difficult':
        native = ImportedDifficultNativeContext(base_view, bundle, controls)
    else:
        native = None

    from imported_native_context import ImportedResourceOwners
    legacy_references = bundle.legacy_references
    owners = ImportedResourceOwners(base_view, bundle, controls, native, legacy_references)
    check_current = owners.assert_current
    provider = SharedReferenceProvider(root, bundle.resources, bundle.versions,
        legacy_reference_loader=legacy_references.reference if legacy_references is not None else None,
        check_current=check_current, operation_owners=owners)
    check_current()
    return bundle, controls, native, provider, check_current


def build_native_engine(base_view, controls_view, paths, *, season='s0', mode='nightmare', require_rank=True):
    """Use the same decoded views for prospective and active construction.

    Callers own their operation/catalog lifetime. This function never resolves
    another catalog, publishes selection or writes personal learning data.
    Data activation may set require_rank=False; the decoded rank capability is
    still installed on the engine and checked by every ranking entry.
    """
    from difficult_native_identity import NativeDifficultCandidateEngine
    from nightmare_candidate import NightmareMemoryStore
    from s0_vertical_controls import observe_s0_map
    from client_providers import _build_memory_store, attach_structure_scanner

    bundle, controls, native, provider, check_current = load_native_resources(
        base_view, controls_view, season=season, mode=mode, require_rank=require_rank)
    pack, root, interfaces = bundle.pack, base_view.root, bundle.interfaces
    rank_ready = bundle.rank_ready

    def entry_current():
        try:
            check_current()
            return True
        except (OSError, ValueError):
            return False

    if season == 's1':
        memory = _build_memory_store(pack, paths.memory_root(mode, season), root, season=season, controls=controls)
    else:
        sample_validator = ((lambda frame: observe_s0_map(frame, mode, season=season, controls=controls)[0].get('detected') is True)
                            if rank_ready else (lambda frame: False))
        memory = NightmareMemoryStore(pack, paths.memory_root(mode, season), portable_seed_path=None,
                                      sample_validator=sample_validator)
    engine = NativeDifficultCandidateEngine(pack, identity_root=root / interfaces['identityEntries'],
        identity_profile_id=interfaces['identityProfile'], identity_floors=pack.floors,
        exit_icon_path=base_view.path(interfaces['exitIcon']) if bundle.semantic_side_entries else None,
        entry_semantic_side_entries=bundle.semantic_side_entries,
        entry_passport_source_available=bundle.passport_source_available,
        entry_structure_store=bundle.resources, entry_shared_reference_provider=provider,
        entry_read_resource=bundle.reader, entry_typed_index_path=base_view.path(interfaces['typedIndex']),
        shared_reference_loader=provider, entry_context_is_current=entry_current,
        memory_store=memory, native_similarity_root=None,
        structure_profile_provider=lambda: 'shared-structure',
        structure_cache_context_provider=lambda: str(base_view.component_ref))
    local = engine.side_entry_identity
    if (engine.entry_identity.error is not None or local is None
            or any(error is not None for error in
                   (local.side_door_error, local.side_search_error))
            or (local.passport_error is not None if bundle.passport_source_available else
                local.passport_error != 'entry-passports-source-unavailable')):
        raise ImportedProviderUnavailable('native-entry-construction-failed', ('native-entry-construction-failed',))
    if bundle.legacy_passports is not None:
        local.passports.extend(deepcopy(bundle.legacy_passports['entries']))
        local.entry_coverage.extend(deepcopy(bundle.legacy_passports['coverage']))
    engine.qt_native_base = bundle
    from structure_pose_inputs import StructurePoseInputCache
    engine.qt_pose_input_cache = StructurePoseInputCache()
    engine.qt_pose_prepare_lock = threading.RLock()
    engine.qt_assert_resource_current = check_current
    engine.qt_shared_provider = provider
    engine.qt_fast_structure_source = bundle.fast_structure_source
    engine.qt_adapters = bundle.adapters
    engine.qt_controls_profile = controls
    engine.qt_resource_root = root
    engine.qt_catalog = engine.qt_optional_displays = None
    engine.qt_native_context = native
    engine.qt_logical_group_registry = bundle.reviewed_front_groups
    engine.qt_live_gaps = tuple(native.live_gaps) if native is not None else ()
    engine.qt_rank_ready = rank_ready
    engine.qt_structure_scan_ready = bool(native is not None
        and native.structure_candidate_capability.get('operatorDeclaredScanReady') is True)
    engine.qt_rank_gaps = () if rank_ready else ('native-base-rank-unavailable',)
    engine.qt_live_ready = rank_ready and not engine.qt_live_gaps
    engine.qt_season = season
    engine.qt_resource_inventory = dict(typedLayers=len(bundle.versions),
        legacyLayers=len(bundle.legacy_references.bindings) if bundle.legacy_references is not None else 0,
        mainEntries=len(engine.entry_identity.entries), sideEntries=len(engine.side_entry_identity.entries),
        semanticSideEntries=local.semantic_side_entries,
        fastPreparedLayers=len(bundle.fast_structure_source.entries),
        fastPreparedGaps=list(bundle.fast_structure_source.gaps),
        reviewedGroupCount=len(bundle.reviewed_front_groups.groups) if bundle.reviewed_front_groups is not None else 0,
        reviewedGroupReviewSource=(bundle.reviewed_front_groups.review_source if bundle.reviewed_front_groups is not None else None),
        reviewedGroupGaps=(dict(bundle.reviewed_front_groups.group_gaps) if bundle.reviewed_front_groups is not None else
                           {'registration': ('reviewed-front-groups-not-provided',)}))
    from structure_pose_inputs import prepare_structure_pose_common_models
    engine.qt_pose_common_models = prepare_structure_pose_common_models(engine)
    if engine.qt_pose_common_models is not None:
        engine.qt_resource_inventory['commonPoseModelBytes'] = engine.qt_pose_common_models.retained_bytes
        engine.qt_pose_input_cache.max_bytes -= engine.qt_pose_common_models.retained_bytes
    if season == 's1':
        from s1_structure_selection import StructureCatalog
        from client_s1_coordinate_recognition import attach_imported_s1_coordinate_recognition
        engine.qt_s1_index_resource_root = root
        catalog = StructureCatalog.from_engine(engine)
        coordinate = attach_imported_s1_coordinate_recognition(engine, catalog)
        attach_structure_scanner(engine, catalog,
            private_fit=coordinate.fixed_fit if coordinate is not None else None)
    elif rank_ready or engine.qt_structure_scan_ready:
        from s1_structure_selection import StructureCatalog
        attach_structure_scanner(engine, StructureCatalog.from_engine(engine))
    check_current()
    return engine


class ImportedEngineFactories(EngineFactories):
    # Failed reports stop automatic Qt preparation loops. An explicit new
    # request retries actual data, including I/O errors wrapped by decoders.
    cache_preparation_failures = False

    def __init__(self, resolver, paths):
        super().__init__(resolver.root)
        self.resolver, self.paths = resolver, paths
        self._catalog_seen, self._catalog_stamp = False, None

    def _context_resources(self, mode, season):
        if is_retired_mode(mode, season):
            raise ImportedProviderUnavailable('retired-mode', ('retired-mode',))
        with self._lock:
            revision = self._revision
        try:
            snapshot = self.resolver.snapshot()
            with self._lock:
                snapshot.assert_current()
                stamp = snapshot.catalog_stamp
                if self._catalog_seen and stamp != self._catalog_stamp:
                    self.invalidate()
                self._catalog_seen, self._catalog_stamp = True, stamp
                revision = self._revision
            resources = snapshot.resolve_context(season, mode)
        except (OSError, ValueError, TypeError, KeyError) as error:
            code = getattr(error, 'code', None)
            if not isinstance(code, str) or not re.fullmatch('[a-z0-9-]{1,120}', code):
                code = 'resource-context-unavailable'
            raise ImportedProviderUnavailable(code, ('resource-context-unavailable',),
                resource_revision=revision) from error
        if resources.gaps:
            raise ImportedProviderUnavailable(';'.join(resources.gaps), resources.gaps, resource_revision=revision)
        if (season, mode) not in {('s0', 'nightmare'), ('s0', 'difficult'), ('s1', 'difficult'), ('s1', 'nightmare')}:
            raise ImportedProviderUnavailable('recognition-decoder-unavailable',
                ('recognition-decoder-unavailable',), resource_revision=revision)
        return resources, revision

    def _resource_binding(self, mode, season='s0'):
        resources, revision = self._context_resources(mode, season)
        try:
            return ProviderResourceBinding(SeasonContext(season=season, mode=mode), resources.base.root, revision)
        except (OSError, ValueError) as error:
            raise ImportedProviderUnavailable('resource-component-unavailable',
                ('resource-component-unavailable',), resource_revision=revision) from error

    def _resource_failure_fields(self, error):
        gaps = getattr(error, 'resource_gaps', None)
        code = getattr(error, 'code', None)
        if gaps is None and isinstance(code, str) and re.fullmatch('[a-z0-9-]{1,120}', code):
            gaps = (code,)
        return dict(resourceGaps=list(gaps or ('resource-validation-failed',)))

    def needs_display_revalidation(self, mode, *, season='s0'):
        """Read preparation state only; the resource owner performs invalidation."""
        key = context_key(mode, season)
        with self._lock:
            engine = self._engines.get(key)
            return engine is not None and engine.qt_display_preparation_failed

    def _get(self, mode, season='s0'):
        key = context_key(mode, season)
        engine = super()._get(mode, season)
        check = getattr(engine, 'qt_assert_resource_current', None)
        if check is not None:
            try:
                check()
            except (OSError, ValueError) as error:
                with self._lock:
                    if self._engines.get(key) is engine:
                        self._engines.pop(key)
                        self._reports[key].update(prepared=True,
                            reason='resource-context-superseded', resourceGaps=['resource-context-superseded'],
                            **self._unavailable_engine_fields('resource-context-superseded'))
                raise ImportedProviderUnavailable('resource-context-superseded',
                                                   ('resource-context-superseded',)) from error
        with self._lock:
            if self._engines.get(key) is not engine:
                raise ImportedProviderUnavailable('resource-context-superseded', ('resource-context-superseded',))
            self._reports[key].update(prepared=True, resourceGaps=[],
                **self._engine_capability_fields(engine, season))
        return engine

    def _build(self, mode, *, season='s0'):
        resources, _ = self._context_resources(mode, season)
        engine = build_native_engine(resources.base, resources.controls, self.paths,
                                     season=season, mode=mode, require_rank=False)
        if resources.displays:
            from imported_author_displays import ImportedAuthorDisplays
            optional = ImportedAuthorDisplays(resources.displays, engine.qt_native_base,
                engine.qt_shared_provider, check_current=engine.qt_assert_resource_current)
            engine.qt_optional_displays = optional
            engine.qt_catalog, engine.qt_adapters = optional.catalog, optional.adapters
        else:
            optional = None
        engine.qt_display_preparation_failed = ('display-unavailable' in resources.optional_gaps
            or optional is not None and optional.preparation_failed)
        engine.qt_resource_inventory['optionalResourceGaps'] = list(resources.optional_gaps) + (
            list(optional.errors.values()) if optional is not None else []) + (
            ['reviewed-groups-decoder-unavailable'] if resources.groups is not None else [])
        engine.qt_assert_resource_current()
        return engine


def build_imported_engine_factories(resolver, paths):
    return ImportedEngineFactories(resolver, paths)
