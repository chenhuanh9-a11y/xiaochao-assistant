"""Original d7063612 engine assembly over current imported resource owners.

Algorithm classes, scanner and fit order are the original implementation. This
module supplies only guarded bytes, independent displays and resource lifetime.
"""
from pathlib import Path
from contextlib import ExitStack, contextmanager
from types import SimpleNamespace


def build_original_engine(base_view, controls_view, paths, *, season, mode, require_rank=True):
    with ExitStack() as stack:
        for view in (base_view, controls_view):
            stack.enter_context(view.read_scope())
        return _build_original_engine(base_view, controls_view, paths,
            season=season, mode=mode, require_rank=require_rank)


def _build_original_engine(base_view, controls_view, paths, *, season, mode, require_rank):
    from client_imported_providers import load_native_resources, ImportedProviderUnavailable
    from client_providers import _build_memory_store
    from map_identity_engine import MapIdentityEngine
    from difficult_native_identity import NativeDifficultCandidateEngine
    from client_group_registry import ConfirmedFrontGroups
    class OriginalRegisteredGroups(ConfirmedFrontGroups):
        def validate_available(self, reference_provider):
            for group in self.groups:
                if set(group['memberIds']) <= self.map_ids:
                    self.validate(group, reference_provider)
    bundle, controls, native, provider, check_current = load_native_resources(
        base_view, controls_view, season=season, mode=mode, require_rank=require_rank)
    root, pack, interfaces = base_view.root, bundle.pack, bundle.interfaces
    reader = bundle.reader
    declared = frozenset(base_view.inventory)
    read_failure, engine_owner, read_attempts = [None], [None], set()
    def failed_read():
        read_failure[0] = True
        engine = engine_owner[0]
        notify = getattr(engine,'qt_entry_resource_failed',None)
        if callable(notify):
            notify()
    def assert_reads_current():
        check_current()
        if read_failure[0] is not None:
            from imported_original_base import OriginalResourceReadFailure
            raise OriginalResourceReadFailure()
    def read_original(path):
        from imported_original_base import OriginalResourceReadFailure
        try:
            relative = reader.relative_path(path)
        except (OSError,ValueError) as error:
            failed_read()
            raise OriginalResourceReadFailure() from error
        read_attempts.add(relative)
        try:
            return reader(path)
        except (OSError,ValueError) as error:
            if relative in declared:
                failed_read()
                raise OriginalResourceReadFailure() from error
            raise
    def current():
        try:
            assert_reads_current()
            return True
        except (OSError, ValueError):
            return False
    memory = _build_memory_store(pack, paths.memory_root(mode, season), root,
        season=season, controls=controls)
    engine_type = MapIdentityEngine if (season, mode) == ('s0', 'nightmare') else NativeDifficultCandidateEngine
    exit_icon = base_view.path(interfaces['exitIcon']) if season == 's0' else None
    engine = engine_type(pack, identity_root=root / interfaces['identityEntries'],
        identity_profile_id=interfaces['identityProfile'], identity_floors=pack.floors,
        exit_icon_path=exit_icon, entry_structure_store=bundle.resources,
        entry_shared_reference_provider=provider, shared_reference_loader=provider,
        entry_context_is_current=current, entry_read_resource=read_original,
        entry_typed_index_path=base_view.path(interfaces['typedIndex']),
        memory_store=memory, native_similarity_root=root,
        native_similarity_binding_provider=provider.binding_metadata,
        native_similarity_read_resource=read_original,
        structure_profile_provider=lambda:'shared-structure',
        structure_cache_context_provider=lambda:str(base_view.component_ref))
    engine_owner[0] = engine
    assert_reads_current()
    if engine.entry_identity.error:
        raise ImportedProviderUnavailable('original-entry-index-unavailable', (engine.entry_identity.error,))
    if engine.side_entry_identity is None:
        raise ImportedProviderUnavailable('original-side-entry-index-unavailable', (str(engine.side_entry_error),))
    if native is not None:
        native.attach_legacy_entry_metadata(engine.side_entry_identity)
    # Original optional decoders can decline unavailable channels. A declared
    # channel which they actually read must still be semantically healthy.
    local = engine.side_entry_identity
    for name,error in (('entry-passports.json',local.passport_error),
                       ('side-door-anchors.json',local.side_door_error),
                       ('side-entry-search.npz',local.side_search_error)):
        relative = interfaces['identityEntries']+'/'+name
        if error is None or relative not in declared or relative not in read_attempts:
            continue
        import json
        if name.endswith('.npz'):
            from io import BytesIO
            import numpy as np
            with np.load(BytesIO(read_original(root/relative)),allow_pickle=False) as archive:
                value = json.loads(str(archive['metadata'].item()))
            empty = value.get('schemaVersion') == 1 and value.get('entries') == []
        elif name == 'side-door-anchors.json':
            value = json.loads(read_original(root/relative))
            empty = ((value.get('schemaVersion'),value.get('mode'),value.get('profileId'))
                == (1,mode,interfaces['identityProfile']) and value.get('entries') == [])
        else:
            empty = False
        if not empty:
            from imported_native_base import NativeBaseUnavailable
            raise NativeBaseUnavailable('original-declared-'+name.replace('.','-')+'-invalid')
    similarity = engine.native_similarity_index
    if similarity.error:
        raise ImportedProviderUnavailable('original-similarity-unavailable', (similarity.error,))
    engine.qt_native_base, engine.qt_controls_profile = bundle, controls
    engine.qt_shared_provider, engine.qt_fast_structure_source = provider, bundle.fast_structure_source
    engine.qt_adapters, engine.qt_catalog, engine.qt_optional_displays = bundle.adapters, None, None
    engine.qt_native_context, engine.qt_resource_root, engine.qt_season = native, root, season
    engine.qt_assert_resource_current, engine.qt_resource_context_current = check_current, current
    engine.qt_assert_entry_resources_current = assert_reads_current
    engine.qt_resource_views = (base_view, controls_view)
    engine.qt_optional_resource_views = ()
    owners = provider._operation_owners

    def read_resource_owners():
        return (tuple(engine.qt_resource_views), tuple(engine.qt_optional_resource_views),
                id(engine.qt_resource_context_current),
                tuple(id(getattr(engine, name, None)) for name in (
                    'qt_native_base', 'qt_controls_profile', 'qt_native_context',
                    'qt_shared_provider', 'qt_fast_structure_source',
                    'qt_adapters', 'qt_catalog', 'qt_optional_displays')),
                provider._read_owner_state(), owners.read_state())

    def verify_resource_read_scope():
        scopes = [view._read_operation() for view in dict.fromkeys(engine.qt_resource_views)]
        if not all(scope is not None for scope in scopes):
            with resource_read_scope() as operation:
                operation.verify()
            return
        scopes += [scope for view in engine.qt_optional_resource_views
                   if (scope := view._read_operation()) is not None and not scope._discarded]
        for scope in scopes:
            scope.verify()
        assert_reads_current()
        if not engine.qt_resource_context_current():
            raise ValueError('resource-context-superseded')

    @contextmanager
    def optional_read_scope(view):
        if view not in engine.qt_optional_resource_views:
            raise ValueError('display-resource-owner-changed')
        base_operation = base_view._read_operation()
        enter = getattr(base_operation, '_original_optional_reader', None)
        if not callable(enter):
            with view.read_scope() as operation:
                yield operation
            return
        operation = enter(view)
        try:
            yield operation
        except (OSError, ValueError):
            operation.discard_optional()
            raise

    @contextmanager
    def resource_read_scope(cancelled=None):
        # Ordinary donor rank/Host consumers retain their actual reader types.
        # Only their bounded operation reuses metadata and checked payloads.
        views = tuple(engine.qt_resource_views)
        current_owner = engine.qt_resource_context_current
        state = read_resource_owners()
        def assert_owned():
            if (tuple(engine.qt_resource_views) != views
                    or engine.qt_resource_context_current is not current_owner
                    or not current_owner() or read_resource_owners() != state):
                raise ValueError('resource-context-superseded')
        with ExitStack() as stack:
            scopes = [stack.enter_context(view.read_scope(cancelled)) for view in dict.fromkeys(views)]
            for scope in scopes:
                scope.register_owner(read_resource_owners)
            installed = not hasattr(scopes[0], '_original_optional_reader')
            if installed:
                def enter_optional(view):
                    operation = view._read_operation()
                    if operation is None:
                        operation = stack.enter_context(view.read_scope(cancelled))
                        operation.register_owner(read_resource_owners)
                    operation._assert_owners()
                    return operation
                scopes[0]._original_optional_reader = enter_optional
            assert_owned()
            try:
                yield SimpleNamespace(verify=verify_resource_read_scope)
                assert_owned()
            finally:
                if installed:
                    del scopes[0]._original_optional_reader
    engine.qt_resource_read_scope = resource_read_scope
    engine.qt_verify_resource_read_scope = verify_resource_read_scope
    engine.qt_optional_resource_read_scope = optional_read_scope
    engine.qt_logical_group_registry = OriginalRegisteredGroups(pack, season=season)
    engine.qt_live_gaps = tuple(native.live_gaps) if native is not None else ()
    engine.qt_live_ready = not engine.qt_live_gaps
    engine.qt_rank_ready, engine.qt_rank_gaps = True, ()
    engine.qt_resource_inventory = dict(typedLayers=len(bundle.versions),
        mainEntries=len(engine.entry_identity.entries), sideEntries=len(engine.side_entry_identity.entries),
        declaredMissingNativeLayers=getattr(native,'unbound_layers',()),
        optionalSideDoorError=engine.side_entry_identity.side_door_error,
        optionalSideSearchError=engine.side_entry_identity.side_search_error,
        fastPreparedLayers=len(bundle.fast_structure_source.entries),
        fastPreparedGaps=list(bundle.fast_structure_source.gaps),
        semanticSideEntries=season == 's0', passportSourceAvailable=True,
        originalAlgorithm='d7063612')
    bundle._values['entry_identity'], bundle._values['side_entry_identity'] = engine.entry_identity, engine.side_entry_identity
    def owned(operation):
        def run(*args, **kwargs):
            with resource_read_scope():
                assert_reads_current()
                result = operation(*args, **kwargs)
                assert_reads_current()
                return result
        return run
    for name in ('rank', 'rank_established_group'):
        setattr(engine, name, owned(getattr(engine, name)))
    if season == 's1':
        engine.qt_s1_index_resource_root = root
        attach_original_s1_scanner(engine, native, bundle)
    assert_reads_current()
    return engine


def attach_original_s1_scanner(engine, native, bundle):
    """The donor S1 closure, with only its private asset reads made explicit."""
    root = bundle.reader.root

    from s1_structure_selection import StructureCatalog, StructureCandidateScanner
    catalog = StructureCatalog.from_engine(engine)
    private_observer = None
    def private_coordinate_prepare(frame, pool_ids, floor, *, cancelled, stage=None):
        nonlocal private_observer
        # Private calibration is declared by this base component.
        from s1_private_experiment import PrivateCoordinateObserver
        if private_observer is None:
            private_observer = PrivateCoordinateObserver(engine, catalog, root / "s1-private-assets", read_resource=bundle.reader)
        return private_observer.prepare(frame, pool_ids, floor, cancelled=cancelled, stage=stage)
    engine.qt_s1_private_coordinate_prepare = private_coordinate_prepare
    pose_index = None
    def pose_prior(source, floor, owner_scope):
        nonlocal pose_index
        # Private calibration is declared by this base component.
        from s1_pose_prior import CoordinatePriorIndex
        if pose_index is None:
            pose_index = CoordinatePriorIndex(
                root / "s1-private-assets" / "s1_entrance_coordinate_index.json",
                expected_sha256="6fee640f5bc87d465459840626cfd5f88b52cdb9b888b58c1b03b46f1370cc19",
                mode=catalog.mode, resource_key=catalog.resource_key,
                provider=lambda mid, fl: engine.qt_shared_provider("shared-structure", mid, fl), read_resource=bundle.reader)
        return pose_index.make_hint(source, target_floor=floor, current_owner_scope=owner_scope)
    engine.qt_s1_pose_prior = pose_prior
    def structure_scan(frame, *, floor, floor_source="operator-declared", cancelled=None, budget_seconds=None,
                       on_progress=None, candidate_order=None, preferred_map_id=None,
                       preferred_material_committed=None, private_coordinate_report=None, planned_map_ids=None,
                       first_material_committed=None, pose_priors=None, collect_fit_diagnostics=False):
        # Lazy import keeps provider construction free of live IO.
        from client_live_runtime import _AlignmentHost
        host = _AlignmentHost(engine, pose_priors=pose_priors,
            collect_fit_diagnostics=collect_fit_diagnostics)
        fixed = private_coordinate_report is not None and private_coordinate_report.get("coordinateVerified") is True
        fit = (lambda f, mid, fl, cancel, **kw: private_observer.fixed_fit(
            f, mid, fl, cancel, report=private_coordinate_report, **kw)) if fixed else host.fit
        scanner = StructureCandidateScanner(catalog, fit,
            prepare_fit=None if fixed else host.prepare_structure_scan)
        report = scanner.scan(frame, floor=floor, floor_source=floor_source,
                            cancelled=cancelled, budget_seconds=budget_seconds,
                            on_progress=on_progress, candidate_order=candidate_order,
                            preferred_map_id=preferred_map_id,
                            preferred_material_committed=preferred_material_committed,
                            planned_map_ids=planned_map_ids, first_material_committed=first_material_committed)
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
    engine.qt_s1_structure_scan = structure_scan
    engine.qt_s1_structure_rank = structure_rank
