"""Read-only choices for authors affected by a proposed base activation.

The plan deliberately contains no selection or transaction identity. The
service owns those mutable decisions and must revalidate a chosen option via
the normal ``activate_context`` path.
"""
from dataclasses import dataclass

import imported_author_display
import imported_resource_catalog as catalog
from imported_resource_packages import ComponentRef, PackageView
import imported_resource_inventory
import star_map_import as files


@dataclass(frozen=True)
class ActivationChoice:
    key: str
    label: str
    display_ref: ComponentRef | None


@dataclass(frozen=True)
class ActivationAuthorRow:
    key: str
    label: str
    scope: str
    old_ref: ComponentRef
    choices: tuple[ActivationChoice, ...]


@dataclass(frozen=True)
class ContextActivationPlan:
    base_ref: ComponentRef
    controls_ref: ComponentRef
    season: str
    mode: str
    catalog_stamp: dict | None
    rows: tuple[ActivationAuthorRow, ...]


_ROLE_TYPES = {'recognitionBase': 'recognition-base', 'controls': 'controls-profile'}
_TRANSIENT_CODES = frozenset({
    'resource-catalog-superseded', 'resource-inventory-superseded',
    'resource-inventory-cancelled', 'import-cancelled',
})


def _reference_key(ref):
    return f'{ref.package_id}/{ref.resource_version}/{ref.component_id}@{ref.manifest_sha256}'


def _raise_transient(error):
    code = getattr(error, 'code', None)
    if code in _TRANSIENT_CODES or (isinstance(code, str)
            and ('superseded' in code or 'cancelled' in code)):
        raise error


def _component_for_context(snapshot, ref, role, season, mode, operation_current):
    operation_current()
    view = snapshot.component_view(ref)
    component = view.component
    if component.get('type') != _ROLE_TYPES[role]:
        raise catalog.ResourceCatalogError('resource-component-role-mismatch')
    wanted = {'season': season, 'mode': mode}
    if wanted not in component.get('contexts', ()):
        raise catalog.ResourceCatalogError('resource-component-context-mismatch')
    operation_current()
    return view


def _is_one_exact_base_dependency(view, base_ref, operation_current):
    operation_current()
    dependencies = view.dependencies
    operation_current()
    return len(dependencies) == 1 and next(iter(dependencies.values())) == base_ref


def _read_identity(view, operation_current):
    operation_current()
    try:
        identity = imported_author_display.read_display_identity(view)
    except (OSError, ValueError, TypeError, KeyError) as error:
        _raise_transient(error)
        # A caller may have canceled or the catalog may have changed while
        # parsing. Those conditions must never become an unknown-scope row.
        operation_current()
        return None
    operation_current()
    return identity


def _inventory_display_refs(inventory, operation_current):
    """Rebuild exact refs from the public rows returned by the real inventory."""
    refs = []
    for row in inventory.rows:
        operation_current()
        if row.get('status') not in ('saved', 'saved-unverified', 'selected'):
            continue
        package_id, version = row.get('packageId'), row.get('resourceVersion')
        digest = row.get('manifestSha256')
        if not all(isinstance(item, str) and item for item in (package_id, version, digest)):
            continue
        for component in row.get('components', ()):
            operation_current()
            if not isinstance(component, dict) or component.get('type') != 'display':
                continue
            component_id = component.get('componentId')
            if not isinstance(component_id, str) or not component_id:
                continue
            try:
                refs.append(ComponentRef(package_id, version, component_id, digest))
            except (TypeError, ValueError):
                # A malformed inventory row is not a candidate.
                continue
    return tuple(refs)


def _matching_choices(resolver, old_ref, old_identity, base_ref,
                      candidates, operation_current):
    matches = {}
    for candidate_ref in candidates:
        operation_current()
        if (candidate_ref == old_ref
                or candidate_ref.package_id != old_ref.package_id
                or candidate_ref.component_id != old_ref.component_id):
            continue
        view = PackageView(resolver.root, candidate_ref, assert_current=operation_current)
        identity = _read_identity(view, operation_current)
        if identity is None:
            continue
        if identity != old_identity:
            continue
        try:
            compatible = _is_one_exact_base_dependency(view, base_ref, operation_current)
        except (OSError, ValueError, TypeError, KeyError) as error:
            _raise_transient(error)
            operation_current()
            continue
        if compatible:
            matches[candidate_ref] = identity

    choices = []
    for candidate_ref in sorted(matches, key=lambda ref: (ref.resource_version, ref.manifest_sha256)):
        operation_current()
        identity = matches[candidate_ref]
        choices.append(ActivationChoice(
            key='display:' + _reference_key(candidate_ref),
            label=f'{identity.profile_id} / {candidate_ref.resource_version}',
            display_ref=candidate_ref,
        ))
    return tuple(choices)


def plan_context_activation(resolver, base_ref, controls_ref, *, season, mode, cancelled=None):
    """Plan explicit author choices for a proposed base/controls context.

    Only the catalog, manifests and display registration documents are read.
    Saved candidate images are not opened or decoded. No catalog or package is
    modified, and ``activate_context`` remains responsible for final admission.
    """
    if not isinstance(base_ref, ComponentRef) or not isinstance(controls_ref, ComponentRef):
        raise catalog.ResourceCatalogError('resource-catalog-reference-invalid')
    context_key = f'{season}:{mode}'
    if context_key not in catalog.CONTEXTS:
        raise catalog.ResourceCatalogError('native-base-context-unsupported')

    snapshot = resolver.snapshot()

    def operation_current():
        files._check_cancelled(cancelled)
        snapshot.assert_current()

    operation_current()
    _component_for_context(snapshot, base_ref, 'recognitionBase', season, mode, operation_current)
    _component_for_context(snapshot, controls_ref, 'controls', season, mode, operation_current)

    affected = []
    for old_ref in snapshot.displays:
        operation_current()
        old_view = PackageView(resolver.root, old_ref, assert_current=operation_current)
        identity = _read_identity(old_view, operation_current)
        if identity is None:
            affected.append((old_ref, None))
            continue
        # An explicitly readable author in another context is out of scope.
        if (identity.season, identity.mode) != (season, mode):
            continue
        try:
            compatible = _is_one_exact_base_dependency(old_view, base_ref, operation_current)
        except (OSError, ValueError, TypeError, KeyError) as error:
            _raise_transient(error)
            operation_current()
            affected.append((old_ref, None))
            continue
        if not compatible:
            affected.append((old_ref, identity))

    operation_current()
    stamp = snapshot.catalog_stamp
    # No affected author means no whole-store scan is needed.
    if not affected:
        snapshot.assert_current()
        return ContextActivationPlan(base_ref, controls_ref, season, mode, stamp, ())

    inventory = imported_resource_inventory.list_saved_versions(
        resolver, verify=False, cancelled=cancelled)
    operation_current()
    if inventory.generation != snapshot.generation:
        raise catalog.ResourceCatalogError('resource-catalog-superseded')
    candidates = _inventory_display_refs(inventory, operation_current)

    rows = []
    for old_ref, identity in affected:
        operation_current()
        ref_key = _reference_key(old_ref)
        stop = ActivationChoice(
            key='stop:' + ref_key,
            label='全局停用此组件（保留文件）',
            display_ref=None,
        )
        if identity is None:
            rows.append(ActivationAuthorRow(
                key='author:' + ref_key,
                label=f'{old_ref.package_id} / {old_ref.resource_version} / {old_ref.component_id}',
                scope='适用模式未知',
                old_ref=old_ref,
                choices=(stop,),
            ))
            continue

        replacements = _matching_choices(
            resolver, old_ref, identity, base_ref, candidates, operation_current)
        scope = f'{identity.season}/{identity.mode}'
        rows.append(ActivationAuthorRow(
            key='author:' + ref_key,
            label=(f'{identity.profile_id} · {old_ref.package_id}/'
                   f'{old_ref.resource_version}/{old_ref.component_id}'),
            scope=scope,
            old_ref=old_ref,
            choices=(*replacements, stop),
        ))

    operation_current()
    snapshot.assert_current()
    return ContextActivationPlan(base_ref, controls_ref, season, mode, stamp, tuple(rows))
