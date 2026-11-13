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
    'native-s1-difficult-base-v1', 'native-s1-nightmare-base-v1', 's1-signed-column-geometry-v1', 'original-qt-resources-v1'})


class ImportedProviderUnavailable(ProviderUnavailable):
    def __init__(self, reason, gaps, *, resource_revision=None):
        super().__init__(reason)
        self.resource_gaps = tuple(gaps)
        if resource_revision is not None:
            self.resource_revision = resource_revision


def load_native_resources(base_view, controls_view, *, season='s0', mode='nightmare', require_rank=False):
    """Decode original resource DTOs; observers/rankers remain the donor code."""
    from imported_controls_profile import S0ControlsProfile
    from imported_s1_controls_profile import S1ControlsProfile
    from imported_original_base import decode_original_base, OriginalNativeContext
    from imported_original_controls import require_original_controls
    from imported_native_context import ImportedResourceOwners
    from client_providers import SharedReferenceProvider
    bundle = decode_original_base(base_view, season=season, mode=mode)
    controls = S1ControlsProfile(controls_view) if season == 's1' else S0ControlsProfile(controls_view)
    require_original_controls(controls, season=season, mode=mode)
    context = controls.for_context(season, mode)
    if context is None or set(context.contract.floor_ids) != set(bundle.pack.floors):
        raise ImportedProviderUnavailable('controls-base-floors-mismatch', ('controls-base-floors-mismatch',))
    native = OriginalNativeContext(base_view, bundle, controls) if season == 's1' or mode == 'difficult' else None
    if native is not None:
        bundle.pack.display_context = native
    owners = ImportedResourceOwners(base_view, bundle, controls, native, bundle.legacy_references)
    check_current = owners.assert_current
    provider = SharedReferenceProvider(base_view.root, bundle.resources, bundle.versions,
        legacy_reference_loader=bundle.legacy_references.reference if bundle.legacy_references is not None else None,
        check_current=check_current, operation_owners=owners)
    check_current()
    return bundle, controls, native, provider, check_current


def build_native_engine(base_view, controls_view, paths, *, season='s0', mode='nightmare', require_rank=True):
    from client_original_providers import build_original_engine
    return build_original_engine(base_view, controls_view, paths, season=season, mode=mode, require_rank=require_rank)


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
        def entry_failed():
            with self._lock:
                if self._engines.get(key) is engine:
                    self._engines.pop(key)
                    engine.qt_rank_ready = engine.qt_live_ready = False
                    engine.qt_rank_gaps = ('original-entry-index-unavailable',)
                    self._reports[key].update(prepared=True,reason='original-entry-index-unavailable',
                        resourceGaps=['original-entry-index-unavailable'],
                        **self._unavailable_engine_fields('original-entry-index-unavailable'))
        engine.qt_entry_resource_failed = entry_failed
        entry_check = getattr(engine,'qt_assert_entry_resources_current',None)
        if callable(entry_check):
            try:
                entry_check()
            except (OSError,ValueError) as error:
                entry_failed()
                raise ImportedProviderUnavailable('original-entry-index-unavailable',
                    ('original-entry-index-unavailable',)) from error
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
