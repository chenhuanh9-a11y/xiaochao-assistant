"""Read-only activation snapshots for imported resources, independent of EXEs.

This module resolves declared package components. Presence is readiness for
semantic validation by the provider, never live recognition authorization.
"""
from dataclasses import dataclass, field
from pathlib import Path
from types import MappingProxyType
import re
import uuid

import star_map_import as files
from author_resource_snapshots import _metadata
from imported_resource_packages import ComponentRef, FileRef, PackageView


SCHEMA = 'xingtu-resource-catalog-v1'
CONTEXTS = frozenset(f'{season}:{mode}' for season in ('s0', 's1')
                     for mode in ('difficult', 'nightmare'))
ROLES = {'recognitionBase': 'recognition-base', 'controls': 'controls-profile',
         'reviewedGroups': 'reviewed-group-registry'}


class ResourceCatalogError(ValueError):
    def __init__(self, code):
        super().__init__(code)
        self.code = code


def _issue_code(error):
    if isinstance(error, FileNotFoundError):
        return 'resource-component-not-installed'
    if isinstance(error, OSError):
        return 'resource-component-unreadable'
    code = getattr(error, 'code', None)
    return code if isinstance(code, str) and re.fullmatch(r'[a-z0-9-]{1,120}', code) else 'resource-component-invalid'


def _reference(value):
    if (not isinstance(value, dict)
            or set(value) != {'packageId', 'resourceVersion', 'componentId', 'manifestSha256'}
            or any(not isinstance(part, str) or not part for part in value.values())):
        raise ResourceCatalogError('resource-catalog-reference-invalid')
    return ComponentRef(package_id=value['packageId'], resource_version=value['resourceVersion'],
                        component_id=value['componentId'], manifest_sha256=value['manifestSha256'])


@dataclass(frozen=True)
class ContextResources:
    season: str
    mode: str
    generation: str | None
    base: object | None
    controls: object | None
    groups: object | None
    displays: tuple
    gaps: tuple
    optional_gaps: tuple
    issues: tuple = ()
    _check_current: object = field(default=None, repr=False, compare=False)

    def assert_current(self):
        if self._check_current is not None:
            self._check_current()

    @property
    def ready_for_validation(self):
        self.assert_current()
        return not self.gaps


class CatalogSnapshot:
    """An exact catalog generation; no directory discovery or builtin fallback."""
    def __init__(self, resource_root, *, project_registry_refs=(), supported_capabilities=()):
        self.root = Path(resource_root).absolute()
        files._no_links(self.root)
        if self.root.exists() and not self.root.is_dir():
            raise ResourceCatalogError('resource-root-not-directory')
        self.catalog_path = files._target(self.root, 'catalog.json')
        self._trusted_registries = frozenset(project_registry_refs)
        self._supported_capabilities = frozenset(supported_capabilities)
        self._catalog_stamp = None
        self.generation = None
        self.contexts = MappingProxyType({})
        self.displays = ()
        try:
            raw, stamp = _metadata(self.catalog_path)
        except FileNotFoundError:
            return  # A clean installation has no activated resource components.
        value = files._json(raw)
        if (not isinstance(value, dict) or value.get('schema') != SCHEMA):
            raise ResourceCatalogError('resource-catalog-format-unsupported')
        if set(value) != {'schema', 'generation', 'contexts', 'displays'}:
            raise ResourceCatalogError('resource-catalog-invalid')
        generation = value['generation']
        try:
            if not isinstance(generation, str):
                raise ValueError('generation must be a string')
            uuid.UUID(generation)
        except (ValueError, AttributeError):
            raise ResourceCatalogError('resource-catalog-generation-invalid') from None
        contexts, displays = value['contexts'], value['displays']
        if (not isinstance(contexts, dict) or not set(contexts) <= CONTEXTS
                or not isinstance(displays, list)):
            raise ResourceCatalogError('resource-catalog-context-invalid')
        parsed = {}
        for key, row in contexts.items():
            if not isinstance(row, dict) or set(row) != set(ROLES):
                raise ResourceCatalogError('resource-catalog-context-invalid')
            parsed[key] = MappingProxyType({role: None if ref is None else _reference(ref)
                                            for role, ref in row.items()})
        selected = tuple(_reference(row) for row in displays)
        if len(set(selected)) != len(selected):
            raise ResourceCatalogError('resource-catalog-display-duplicate')
        self.contexts = MappingProxyType(parsed)
        self.displays = selected
        self.generation, self._catalog_stamp = generation, stamp

    def assert_current(self):
        try:
            _, current = _metadata(self.catalog_path)
        except FileNotFoundError:
            if self._catalog_stamp is None:
                return
            raise ResourceCatalogError('resource-catalog-superseded') from None
        if current != self._catalog_stamp:
            raise ResourceCatalogError('resource-catalog-superseded')

    def component_view(self, ref):
        self.assert_current()
        view = PackageView(self.root, ref, assert_current=self.assert_current)
        required = view.manifest['clientCompatibility']['requiredCapabilities']
        if not set(required) <= self._supported_capabilities:
            raise ResourceCatalogError('resource-client-capability-unsupported')
        # Manifest presence alone cannot make a deleted component ready. The
        # decoder owns payload integrity/semantics, without a runtime tree scan.
        view.path(view.component['entrypoint'])
        self.assert_current()
        return view

    def read(self, file_ref):
        """Read selected data; inactive history uses PackageView without authority."""
        if not isinstance(file_ref, FileRef):
            raise ResourceCatalogError('resource-file-reference-invalid')
        selected = set(self.displays)
        for row in self.contexts.values():
            selected.update(ref for role, ref in row.items() if ref is not None
                            and (role != 'reviewedGroups' or ref in self._trusted_registries))
        if file_ref.component not in selected:
            raise ResourceCatalogError('resource-component-not-selected')
        view = self.component_view(file_ref.component)
        raw = view.read(file_ref.path)
        self.assert_current()
        return raw

    def resolve_context(self, season, mode):
        key = f'{season}:{mode}'
        if key not in CONTEXTS:
            raise ResourceCatalogError('resource-context-unsupported')
        self.assert_current()
        row = self.contexts.get(key, {})
        views, gaps, optional, issues = {}, [], [], []
        for role, kind in ROLES.items():
            ref = row.get(role)
            target = optional if role == 'reviewedGroups' else gaps
            if ref is None:
                target.append(f'{kind}-missing')
                views[role] = None
                continue
            if role == 'reviewedGroups' and ref not in self._trusted_registries:
                optional.append('reviewed-group-registry-untrusted')
                views[role] = None
                continue
            try:
                view = self.component_view(ref)
                contexts = view.component.get('contexts', [])
                if (view.component.get('type') != kind
                        or {'season': season, 'mode': mode} not in contexts):
                    raise ResourceCatalogError('resource-component-context-mismatch')
            except (OSError, ValueError) as error:
                if isinstance(error, ResourceCatalogError) and error.code == 'resource-catalog-superseded':
                    raise
                target.append(f'{kind}-unavailable')
                issues.append((kind, _issue_code(error)))
                views[role] = None
                continue
            views[role] = view
        displays = []
        for ref in self.displays:
            try:
                view = self.component_view(ref)
                if (view.component.get('type') == 'display'
                        and {'season': season, 'mode': mode} in view.component.get('contexts', [])):
                    displays.append(view)
            except (OSError, ValueError) as error:
                if isinstance(error, ResourceCatalogError) and error.code == 'resource-catalog-superseded':
                    raise
                optional.append('display-unavailable')
                issues.append(('display', _issue_code(error)))
        self.assert_current()
        return ContextResources(season, mode, self.generation,
                                views['recognitionBase'], views['controls'], views['reviewedGroups'],
                                tuple(displays), tuple(gaps), tuple(optional), tuple(issues),
                                self.assert_current)


class ResourceResolver:
    """Shared configuration, with fresh snapshots resolved on caller workers."""
    def __init__(self, resource_root, *, supported_capabilities=(), project_registry_refs=()):
        self.root = Path(resource_root).absolute()
        self.supported_capabilities = frozenset(supported_capabilities)
        self.project_registry_refs = frozenset(project_registry_refs)

    def snapshot(self):
        return CatalogSnapshot(self.root, supported_capabilities=self.supported_capabilities,
                               project_registry_refs=self.project_registry_refs)

    def resolve_context(self, season, mode):
        return self.snapshot().resolve_context(season, mode)
