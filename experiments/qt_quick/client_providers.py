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


class SharedReferenceProvider:
    """Version-checked typed references; six-entry cache, no pose/UI state."""
    def __init__(self, root, resources, versions, *, native=None, check_current=None):
        if check_current is not None and not callable(check_current):
            raise TypeError('reference lifetime guard must be callable')
        self.root, self.resources, self.versions, self.native = root, resources, versions, native
        self._check_current = check_current
        self._cache = OrderedDict()
        self._lock = threading.RLock()

    def _assert_current(self):
        if self._check_current is not None:
            self._check_current()

    def __call__(self, profile, map_id, floor):
        import numpy as np
        self._assert_current()
        if self.native is not None:
            legacy = self.native.legacy_reference(map_id, floor)
            if legacy is not None:
                self._assert_current()
                return legacy
        keys = [key for key in self.versions if key[1] == str(map_id) and key[3] == str(floor)]
        if len(keys) != 1:
            return None
        key = keys[0]
        version = self.versions[key]
        if version.get("sharedError"):
            return None
        with self._lock:
            reference = self._cache.get(key)
            if reference is not None:
                self._assert_current()
                self._cache.move_to_end(key)
                return reference
            reference = self.resources.load(key[0], key[1], key[3], variant=key[2])
            if (reference is None or not np.any(reference.clean_mask)
                    or reference.path.resolve() != version["path"]
                    or reference.metadata.get("canvas") != version["canvas"]
                    or any(reference.metadata.get(name) != value for name, value in version["expected"].items())):
                return None
            self._assert_current()
            self._cache[key] = reference
            while len(self._cache) > 6:
                self._cache.popitem(last=False)
            return reference

    def binding_metadata(self, profile, map_id, floor):
        self._assert_current()
        if self.native is not None:
            legacy = self.native.legacy_reference(map_id, floor)
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


def attach_structure_scanner(engine, catalog, *, private_fit=None):
    """Attach the shared scanner without choosing any resource fallback.

    Legacy private coordinates are supplied by their owning provider. Imported
    bases can use the same scanner with only their checked shared references.
    """
    from s1_structure_selection import StructureCandidateScanner

    def check_current():
        check = getattr(engine, 'qt_assert_resource_current', None)
        if check is not None:
            check()

    def structure_scan(frame, *, floor, floor_source="operator-declared", cancelled=None, budget_seconds=None,
                       on_progress=None, candidate_order=None, preferred_map_id=None,
                       preferred_material_committed=None, private_coordinate_report=None, planned_map_ids=None,
                       first_material_committed=None, pose_priors=None, collect_fit_diagnostics=False,
                       on_entry_evidence=None, deadline=None, frame_snapshot=None):
        check_current()
        if not getattr(engine, 'qt_rank_ready', True):
            raise ProviderUnavailable(';'.join(getattr(engine, 'qt_rank_gaps', ())) or 'recognition-rank-unavailable')
        from client_live_runtime import _AlignmentHost
        host = _AlignmentHost(engine, pose_priors=pose_priors,
            collect_fit_diagnostics=collect_fit_diagnostics)
        fixed = private_coordinate_report is not None and private_coordinate_report.get("coordinateVerified") is True
        if fixed and private_fit is None:
            raise ProviderUnavailable('private-coordinate-provider-unavailable')
        fit = (lambda f, mid, fl, cancel, **kw: private_fit(
            f, mid, fl, cancel, report=private_coordinate_report, **kw)) if fixed else host.fit
        scanner = StructureCandidateScanner(catalog, fit, clock=time.perf_counter,
            prepare_fit=None if fixed else host.prepare_structure_scan)
        def progress(value):
            check_current()
            on_progress(value)
        report = scanner.scan(frame, floor=floor, floor_source=floor_source,
            cancelled=cancelled, budget_seconds=budget_seconds,
            on_progress=progress if on_progress is not None else None, candidate_order=candidate_order,
            preferred_map_id=preferred_map_id, preferred_material_committed=preferred_material_committed,
            planned_map_ids=planned_map_ids, first_material_committed=first_material_committed,
            prepare_entry=(engine.prepare_entry_observation if catalog.season == 's0'
                and getattr(engine, 'qt_native_base', None) is not None else None),
            on_entry_evidence=on_entry_evidence, deadline=deadline, frame_snapshot=frame_snapshot)
        check_current()
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
                self._engines[key] = engine
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
        provider = SharedReferenceProvider(root, resources, {}, native=native)
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
