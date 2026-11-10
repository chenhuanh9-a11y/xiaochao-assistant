"""Default catalog boundary while the external-data decoders are connected.

An empty store is ordinary unavailable data. In particular, a component root
must not enter a legacy loader that assumes trusted IDs and nested paths.
Presence is reported separately from algorithm readiness.
"""
import re

from season_context import context_key, is_retired_mode
from client_providers import EngineFactories, ProviderUnavailable


# Capabilities are granted by completed program decoders, never package claims.
SUPPORTED_CAPABILITIES = frozenset()


class ImportedProviderUnavailable(ProviderUnavailable):
    def __init__(self, reason, gaps):
        super().__init__(reason)
        self.resource_gaps = tuple(gaps)


class ImportedEngineFactories(EngineFactories):
    def __init__(self, resolver, paths):
        super().__init__(resolver.root)
        self.resolver, self.paths = resolver, paths

    def _resource_binding(self, mode, season='s0'):
        if is_retired_mode(mode, season):
            raise ImportedProviderUnavailable('retired-mode', ('retired-mode',))
        try:
            resources = self.resolver.snapshot().resolve_context(season, mode)
        except (OSError, ValueError, TypeError, KeyError) as error:
            code = getattr(error, 'code', None)
            if not isinstance(code, str) or not re.fullmatch('[a-z0-9-]{1,120}', code):
                code = 'resource-context-unavailable'
            raise ImportedProviderUnavailable(code, ('resource-context-unavailable',)) from error
        if resources.gaps:
            raise ImportedProviderUnavailable(';'.join(resources.gaps), resources.gaps)
        raise ImportedProviderUnavailable('recognition-decoder-unavailable', ('recognition-decoder-unavailable',))

    def _get(self, mode, season='s0'):
        try:
            return super()._get(mode, season)
        except ProviderUnavailable as error:
            with self._lock:
                self._reports[context_key(mode, season)]['resourceGaps'] = list(
                    getattr(error, 'resource_gaps', ('resource-validation-failed',)))
            raise

    def _build(self, mode, *, season='s0'):
        raise ImportedProviderUnavailable('recognition-decoder-unavailable', ('recognition-decoder-unavailable',))


def build_imported_engine_factories(resolver, paths):
    return ImportedEngineFactories(resolver, paths)
