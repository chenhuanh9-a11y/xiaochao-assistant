"""Prospective catalog inventory/dependency checks, before semantic admission.

This module does not grant recognition, display registration or live authority.
Algorithm-specific decoders must validate the resulting component views before
the caller publishes the catalog under its idle resource operation lease.
"""
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType

import star_map_import as files
from imported_resource_catalog import ResourceCatalogError, ROLES, parse_catalog
from imported_resource_packages import PackageView


@dataclass(frozen=True)
class SelectionInspection:
    generation: str
    contexts: object
    displays: tuple
    views: object


def inspect_selection(resource_root, document, *, supported_capabilities=(), project_registry_refs=(),
                      cancelled=None, assert_current=None):
    """Check real immutable package inventories and the exact selected closure.

    Dependencies may refer to historical packages only when they are not a
    selected recognition/control source. A display cannot reactivate an older
    base by naming it as a dependency. Missing dependencies leave a saved package
    inert; they never cause a fallback or a partial catalog publication.
    """
    root = Path(resource_root).absolute()
    files._no_links(root)
    if root.exists() and not root.is_dir():
        raise ResourceCatalogError('resource-root-not-directory')
    generation, contexts, displays = parse_catalog(document)
    capabilities, trusted = frozenset(supported_capabilities), frozenset(project_registry_refs)
    views, checked_packages = {}, set()
    pending, uses, edges = [], {}, {}

    def check():
        files._check_cancelled(cancelled)
        if assert_current is not None:
            assert_current()

    def load(ref):
        check()
        if ref in views:
            return views[ref]
        if len(views) >= files.MAX_FILES:
            raise ResourceCatalogError('resource-dependency-count-limit')
        view = PackageView(root, ref, assert_current=assert_current)
        if not set(view.manifest['clientCompatibility']['requiredCapabilities']) <= capabilities:
            raise ResourceCatalogError('resource-client-capability-unsupported')
        package = (ref.package_id, ref.resource_version, ref.manifest_sha256)
        if package not in checked_packages:
            view.validate_inventory()
            checked_packages.add(package)
        views[ref] = view
        pending.append(ref)
        return view

    for key, row in contexts.items():
        season, mode = key.split(':', 1)
        for role, ref in row.items():
            if ref is None:
                continue
            view = load(ref)
            component = view.component
            if (component['type'] != ROLES[role]
                    or {'season': season, 'mode': mode} not in component['contexts']):
                raise ResourceCatalogError('resource-component-context-mismatch')
            if role == 'reviewedGroups' and ref not in trusted:
                raise ResourceCatalogError('resource-reviewed-group-registry-untrusted')
            uses.setdefault(ref, set()).add(key)
    for ref in displays:
        component = load(ref).component
        if component['type'] != 'display':
            raise ResourceCatalogError('resource-display-component-type-mismatch')
        uses.setdefault(ref, set()).update(
            f"{row['season']}:{row['mode']}" for row in component['contexts']
            if contexts.get(f"{row['season']}:{row['mode']}", {}).get('recognitionBase') is not None)

    # Iteration handles shared/cyclic metadata references without recursion or
    # repeatedly validating the same package. Decoders own semantic constraints.
    index = 0
    while index < len(pending):
        owner = load(pending[index])
        index += 1
        owner_contexts = {f"{row['season']}:{row['mode']}" for row in owner.component['contexts']}
        edges[owner.component_ref] = []
        for dependency in owner.dependencies.values():
            target = load(dependency)
            component = target.component
            common = owner_contexts & {f"{row['season']}:{row['mode']}" for row in component['contexts']}
            if not common:
                raise ResourceCatalogError('resource-dependency-context-mismatch')
            edges[owner.component_ref].append(dependency)

    # Supporting another context does not activate it. Propagate only the
    # contexts actually selected (or displayed against a selected base).
    work = [(ref, keys) for ref, keys in uses.items()]
    visited = set()
    while work:
        owner_ref, keys = work.pop()
        fresh = {key for key in keys if (owner_ref, key) not in visited}
        visited.update((owner_ref, key) for key in fresh)
        if not fresh:
            continue
        for dependency in edges[owner_ref]:
            component = views[dependency].component
            common = fresh & {f"{row['season']}:{row['mode']}" for row in component['contexts']}
            role = {'recognition-base': 'recognitionBase', 'controls-profile': 'controls',
                    'reviewed-group-registry': 'reviewedGroups'}.get(component['type'])
            if role is not None and common:
                if any(contexts.get(key, {}).get(role) != dependency for key in common):
                    raise ResourceCatalogError('resource-dependency-not-selected')
                if role == 'reviewedGroups' and dependency not in trusted:
                    raise ResourceCatalogError('resource-reviewed-group-registry-untrusted')
            work.append((dependency, common))
    check()
    return SelectionInspection(generation, contexts, displays, MappingProxyType(views))
