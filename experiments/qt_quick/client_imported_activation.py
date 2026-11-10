"""Prospective imported-resource activation for the Qt resource worker.

The caller owns the session's idle resource lease. This function serializes the
catalog operation, validates immutable package views against the exact proposed
selection, then delegates publication and recovery to the existing catalog
transaction.
"""
from pathlib import Path
import uuid

import imported_resource_activation as publication
import imported_resource_catalog as catalog
import imported_resource_selection as selection
from client_imported_providers import build_native_engine
from imported_resource_packages import ComponentRef
from resource_transaction import resource_write_lock
import star_map_import as files


_ROLES = ('recognitionBase', 'controls', 'reviewedGroups')


def _wire(ref):
    if ref is None:
        return None
    if not isinstance(ref, ComponentRef):
        raise catalog.ResourceCatalogError('resource-catalog-reference-invalid')
    return dict(packageId=ref.package_id, resourceVersion=ref.resource_version,
                componentId=ref.component_id, manifestSha256=ref.manifest_sha256)


def _resolved_root(resolver, paths):
    root = Path(resolver.root).absolute()
    files._no_links(root)
    root = root.resolve()
    configured = Path(paths.imported_resource_root).absolute()
    files._no_links(configured)
    configured = configured.resolve()
    if configured != root:
        raise catalog.ResourceCatalogError('resource-root-mismatch')
    return root


def _validate_proposal(root, paths, resolver, base_ref, controls_ref, *, season, mode, cancelled):
    def operation_current():
        # Prospective PackageViews must not use a CatalogSnapshot guard: the
        # publication transaction deliberately has a pending marker while it
        # runs its second semantic validation.
        files._check_cancelled(cancelled)

    def validate(document):
        operation_current()
        inspected = selection.inspect_selection(
            root, document,
            supported_capabilities=resolver.supported_capabilities,
            project_registry_refs=resolver.project_registry_refs,
            cancelled=cancelled,
            assert_current=operation_current,
        )
        base_view = inspected.views.get(base_ref)
        controls_view = inspected.views.get(controls_ref)
        if base_view is None or controls_view is None:
            raise catalog.ResourceCatalogError('resource-component-not-selected')
        engine = build_native_engine(base_view, controls_view, paths, season=season, mode=mode)
        try:
            operation_current()
            engine.qt_assert_resource_current()
        finally:
            # The prospective engine has no lifetime beyond semantic admission.
            del engine

    return validate


def activate_context(resolver, paths, base_ref, controls_ref, *, season='s0', mode='nightmare',
                     cancelled=None):
    """Activate only one supported imported recognition context.

    The caller must already hold its Session idle resource lease. The catalog
    write lock here serializes this operation with package/catalog writers.
    Only the implemented native S0 nightmare and S1 difficult/nightmare base/control
    pairs are admitted; no legacy factory or builtin fallback is consulted.
    """
    if (season, mode) not in {('s0', 'nightmare'), ('s1', 'difficult'), ('s1', 'nightmare')}:
        raise catalog.ResourceCatalogError('native-base-context-unsupported')
    if not isinstance(base_ref, ComponentRef) or not isinstance(controls_ref, ComponentRef):
        raise catalog.ResourceCatalogError('resource-catalog-reference-invalid')
    base_wire, controls_wire = _wire(base_ref), _wire(controls_ref)
    root = _resolved_root(resolver, paths)

    with resource_write_lock(root, wait=True, cancelled=cancelled):
        snapshot = resolver.snapshot()
        if Path(snapshot.root).resolve() != root:
            raise catalog.ResourceCatalogError('resource-root-mismatch')
        expected_stamp = snapshot.catalog_stamp
        context_key = f'{season}:{mode}'
        current = snapshot.contexts.get(context_key)
        already_active = (current is not None
                          and current['recognitionBase'] == base_ref
                          and current['controls'] == controls_ref)

        contexts = {
            key: {role: _wire(row[role]) for role in _ROLES}
            for key, row in snapshot.contexts.items()
        }
        row = contexts.setdefault(context_key, {role: None for role in _ROLES})
        row['recognitionBase'] = base_wire
        row['controls'] = controls_wire
        document = dict(
            schema=catalog.SCHEMA,
            generation=snapshot.generation if already_active else str(uuid.uuid4()),
            contexts=contexts,
            displays=[_wire(ref) for ref in snapshot.displays],
        )
        validate = _validate_proposal(root, paths, resolver, base_ref, controls_ref,
                                      season=season, mode=mode, cancelled=cancelled)

        if already_active:
            # Idempotence still proves the installed package contents and the
            # current decoder semantics, but does not publish a new generation.
            validate(document)
            files._check_cancelled(cancelled)
            snapshot.assert_current()
            return dict(status='already-active', generation=snapshot.generation,
                        context=context_key)

        return publication.activate_catalog_locked(
            root, document, expected_catalog_stamp=expected_stamp,
            validate=validate, cancelled=cancelled,
        )
