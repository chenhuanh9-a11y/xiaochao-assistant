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
from imported_resource_packages import ComponentRef, PackageView
from resource_transaction import resource_write_lock
import star_map_import as files


_ROLES = ('recognitionBase', 'controls', 'reviewedGroups')
_CONTEXTS = frozenset({('s0', 'nightmare'), ('s0', 'difficult'), ('s1', 'difficult'), ('s1', 'nightmare')})
_UNSPECIFIED_CATALOG = object()


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
        engine = build_native_engine(base_view, controls_view, paths, season=season, mode=mode, require_rank=False)
        try:
            from imported_author_display import decode_author_display
            profiles = set()
            for display_ref in inspected.displays:
                operation_current()
                view = inspected.views[display_ref]
                if {'season': season, 'mode': mode} not in view.component['contexts']:
                    continue
                display = decode_author_display(view, engine.qt_native_base, engine.qt_shared_provider)
                profile = display.profile_id
                if profile in profiles:
                    raise catalog.ResourceCatalogError('resource-display-profile-conflict')
                profiles.add(profile)
                if not display.layers:
                    raise catalog.ResourceCatalogError('resource-display-no-usable-layers')
            operation_current()
            engine.qt_fast_structure_source.validate_all(
                engine.qt_shared_provider, compare_derived=True, cancelled=cancelled)
            operation_current()
            engine.qt_assert_resource_current()
        finally:
            # The prospective engine has no lifetime beyond semantic admission.
            del engine

    return validate


def _prepare_display_updates(root, snapshot, display_refs, remove_display_refs,
                             season, mode, operation_current):
    """Apply explicit updates/stops; never drop an omitted author implicitly."""
    from imported_author_display import read_display_identity
    removed, removed_logical = set(), set()
    for ref in remove_display_refs:
        operation_current()
        if not isinstance(ref, ComponentRef):
            raise catalog.ResourceCatalogError('resource-catalog-reference-invalid')
        if ref in removed:
            raise catalog.ResourceCatalogError('resource-display-removal-duplicate')
        if ref not in snapshot.displays:
            raise catalog.ResourceCatalogError('resource-display-removal-not-selected')
        removed.add(ref)
        removed_logical.add((ref.package_id, ref.component_id))
    displays = [ref for ref in snapshot.displays if ref not in removed]
    updates, requested = [], set()
    for display_ref in display_refs:
        operation_current()
        if not isinstance(display_ref, ComponentRef):
            raise catalog.ResourceCatalogError('resource-catalog-reference-invalid')
        logical = (display_ref.package_id, display_ref.component_id)
        # Explicit stop is not a migration bypass when the old identity cannot
        # be read. Such a replacement still needs the normal header comparison.
        if logical in removed_logical:
            raise catalog.ResourceCatalogError('resource-display-update-removal-conflict')
        if logical in requested:
            raise catalog.ResourceCatalogError('resource-display-selection-ambiguous')
        requested.add(logical)
        previous = [ref for ref in snapshot.displays
                    if (ref.package_id, ref.component_id) == logical]
        if len(previous) > 1:
            raise catalog.ResourceCatalogError('resource-display-selection-ambiguous')
        previous_ref = previous[0] if previous else None
        new_view = PackageView(root, display_ref, assert_current=operation_current)
        identity = read_display_identity(new_view)
        if (identity.season, identity.mode) != (season, mode):
            raise catalog.ResourceCatalogError('resource-component-context-mismatch')
        old_view = PackageView(root, previous_ref, assert_current=operation_current) if previous_ref else None
        updates.append((new_view, old_view, previous_ref))
        if previous_ref is None:
            displays.append(display_ref)
        else:
            displays = [display_ref if ref == previous_ref else ref for ref in displays]

    def check_updates():
        for new_view, old_view, previous_ref in updates:
            operation_current()
            new_identity = read_display_identity(new_view)
            if (new_identity.season, new_identity.mode) != (season, mode):
                raise catalog.ResourceCatalogError('resource-component-context-mismatch')
            if old_view is not None:
                try:
                    old_identity = read_display_identity(old_view)
                except (OSError, ValueError) as cause:
                    operation_current()
                    error = catalog.ResourceCatalogError('resource-previous-display-metadata-unavailable')
                    error.previous_display_ref = previous_ref
                    raise error from cause
                if old_identity != new_identity:
                    raise catalog.ResourceCatalogError('resource-display-identity-mismatch')

    check_updates()
    return tuple(displays), check_updates


def _snapshot_for_root(resolver, root):
    snapshot = resolver.snapshot()
    if Path(snapshot.root).resolve() != root:
        raise catalog.ResourceCatalogError('resource-root-mismatch')
    return snapshot


def _activate_locked(root, snapshot, paths, resolver, *, season, mode, display_refs,
                     cancelled, base_ref=None, controls_ref=None, remove_display_refs=()):
    def operation_current():
        files._check_cancelled(cancelled)

    expected_stamp = snapshot.catalog_stamp
    displays, check_updates = _prepare_display_updates(root, snapshot, display_refs, remove_display_refs,
        season, mode, operation_current)
    context_key = f'{season}:{mode}'
    current = snapshot.contexts.get(context_key)
    if base_ref is None and controls_ref is None:
        if current is None or current['recognitionBase'] is None or current['controls'] is None:
            raise catalog.ResourceCatalogError('resource-display-base-context-not-active')
        base_ref, controls_ref = current['recognitionBase'], current['controls']
    validate_context = _validate_proposal(root, paths, resolver,
        base_ref, controls_ref, season=season, mode=mode, cancelled=cancelled)

    def validate(document):
        operation_current()
        # Only the proposed closure is decoded. Old replaced images/base data
        # may be damaged; their exact old display header still binds the scope.
        check_updates()
        validate_context(document)
        operation_current()

    already_active = (current is not None and current['recognitionBase'] == base_ref
        and current['controls'] == controls_ref and displays == snapshot.displays)
    contexts = {key:{role:_wire(row[role]) for role in _ROLES} for key,row in snapshot.contexts.items()}
    row = contexts.setdefault(context_key, {role:None for role in _ROLES})
    row.update(recognitionBase=_wire(base_ref), controls=_wire(controls_ref))
    document = dict(schema=catalog.SCHEMA,
        generation=snapshot.generation if already_active else str(uuid.uuid4()),
        contexts=contexts, displays=[_wire(ref) for ref in displays])
    if already_active:
        validate(document)
        operation_current()
        snapshot.assert_current()
        return dict(status='already-active', generation=snapshot.generation, context=context_key)
    return publication.activate_catalog_locked(root, document, expected_catalog_stamp=expected_stamp,
        validate=validate, cancelled=cancelled)


def activate_context(resolver, paths, base_ref, controls_ref, *, season='s0', mode='nightmare',
                     display_refs=(), remove_display_refs=(),
                     expected_catalog_stamp=_UNSPECIFIED_CATALOG, cancelled=None):
    """Select B/C and explicitly requested display updates in one transaction.

    display_refs is an update subset, not a replacement list. Omitted authors
    remain selected and their exact base dependencies must still be satisfied.
    remove_display_refs explicitly stops exact globally selected components,
    preserving their files. It cannot overlap an updated logical component.
    A supplied expected_catalog_stamp binds a reviewed proposal to its catalog;
    None explicitly means the reviewed store had no catalog yet.
    The caller owns the Session idle lease. Data admission grants no live/rank.
    """
    if (season, mode) not in _CONTEXTS:
        raise catalog.ResourceCatalogError('native-base-context-unsupported')
    if not isinstance(base_ref, ComponentRef) or not isinstance(controls_ref, ComponentRef):
        raise catalog.ResourceCatalogError('resource-catalog-reference-invalid')
    root = _resolved_root(resolver, paths)
    with resource_write_lock(root, wait=True, cancelled=cancelled):
        snapshot = _snapshot_for_root(resolver, root)
        if (expected_catalog_stamp is not _UNSPECIFIED_CATALOG
                and expected_catalog_stamp != snapshot.catalog_stamp):
            raise catalog.ResourceCatalogError('resource-catalog-superseded')
        return _activate_locked(root, snapshot, paths, resolver, season=season, mode=mode,
            base_ref=base_ref, controls_ref=controls_ref, display_refs=display_refs,
            remove_display_refs=remove_display_refs, cancelled=cancelled)


def activate_display(resolver, paths, display_ref, *, cancelled=None):
    """Select one display version while preserving the currently active base."""
    from imported_author_display import read_display_identity
    if not isinstance(display_ref, ComponentRef):
        raise catalog.ResourceCatalogError('resource-catalog-reference-invalid')
    root = _resolved_root(resolver, paths)
    with resource_write_lock(root, wait=True, cancelled=cancelled):
        snapshot = _snapshot_for_root(resolver, root)
        view = PackageView(root, display_ref, assert_current=lambda:files._check_cancelled(cancelled))
        identity = read_display_identity(view)
        outcome = _activate_locked(root, snapshot, paths, resolver, season=identity.season,
            mode=identity.mode, display_refs=(display_ref,), cancelled=cancelled)
        return dict(outcome, context=f'{identity.season}:{identity.mode}', profileId=identity.profile_id)
