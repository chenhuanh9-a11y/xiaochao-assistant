"""Prospective imported-resource activation for the Qt resource worker.

The caller owns the session's idle resource lease. This function serializes the
catalog operation, validates immutable package views against the exact proposed
selection, then delegates publication and recovery to the existing catalog
transaction.
"""
from contextlib import ExitStack
from collections import OrderedDict
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Event
import uuid

import imported_resource_activation as publication
import imported_resource_catalog as catalog
import imported_resource_selection as selection
import client_imported_activation_plan as activation_plan
from client_imported_providers import build_native_engine
from imported_resource_packages import ComponentRef, FileRef, PackageView
from map_package_upgrade import _identity
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


def _validate_context(inspected, paths, base_ref, controls_ref, *, season, mode, cancelled,
                      base_qualified=False):
    def operation_current():
        files._check_cancelled(cancelled)

    base_view = inspected.views.get(base_ref)
    controls_view = inspected.views.get(controls_ref)
    if base_view is None or controls_view is None:
        raise catalog.ResourceCatalogError('resource-component-not-selected')
    with ExitStack() as stack:
        for view in inspected.views.values():
            stack.enter_context(view.read_scope(cancelled))
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
            if not base_qualified:
                engine.qt_fast_structure_source.validate_all(
                    engine.qt_shared_provider, compare_derived=True, cancelled=cancelled)
                if engine.qt_logical_group_registry is not None:
                    engine.qt_logical_group_registry.validate_available(engine.qt_shared_provider)
            operation_current()
            engine.qt_assert_resource_current()
        finally:
            del engine


class SemanticQualifications:
    """Worker-owned bounded results, never file health or selection authority.

    No engine, image, array or filesystem verdict is retained. Actual immutable
    inputs must still be proved before reusing a pure semantic result.
    """
    def __init__(self):
        self._results = OrderedDict()

    def contains(self, key):
        return key in self._results

    def remember(self, keys):
        for key in keys:
            self._results[key] = None
            self._results.move_to_end(key)
        while len(self._results) > 32:
            self._results.popitem(last=False)


class _SelectionValidator:
    def __init__(self, root, paths, resolver, *, contexts=None, check_updates=None,
                 cancelled=None, qualifications=None):
        self.root, self.paths, self.resolver = root, paths, resolver
        self.contexts, self.check_updates, self.cancelled = contexts, check_updates, cancelled
        self.qualifications = qualifications
        self._qualified = set()

    def _current(self):
        # No CatalogSnapshot guard while publication has a pending marker.
        files._check_cancelled(self.cancelled)

    def _key(self, inspected, season, mode, base_ref, controls_ref):
        row = inspected.contexts[f'{season}:{mode}']
        if row['recognitionBase'] != base_ref or row['controls'] != controls_ref:
            raise catalog.ResourceCatalogError('resource-component-not-selected')
        displays = tuple(ref for ref in inspected.displays
            if {'season': season, 'mode': mode} in inspected.views[ref].component['contexts'])
        base = (self.root, tuple(_identity(self.root.stat())), season, mode,
            tuple(row[role] for role in _ROLES), frozenset(self.resolver.supported_capabilities),
            frozenset(self.resolver.project_registry_refs))
        return base, displays

    def _known(self, key):
        return key in self._qualified or (self.qualifications is not None
            and self.qualifications.contains(key))

    def _prove_inputs(self, inspected):
        with ExitStack() as stack:
            scopes = {ref: stack.enter_context(view.read_scope(self.cancelled))
                      for ref, view in inspected.views.items()}
            checked_packages = set()
            for ref, view in inspected.views.items():
                self._current()
                package = (ref.package_id, ref.resource_version, ref.manifest_sha256)
                if package not in checked_packages:
                    view.validate_inventory()
                    checked_packages.add(package)
                # The tree has just been read successfully. Each selected
                # component separately joins the final actual byte proof.
                for relative in view.inventory:
                    scopes[ref].record_consumed(FileRef(ref, relative))
            if self.check_updates is not None:
                self.check_updates()
            self._current()

    def _qualify_missing(self, inspected, pending):
        if not pending:
            return
        stopped = Event()

        def cancelled():
            return stopped.is_set() or (self.cancelled is not None and self.cancelled())

        def qualify(item):
            season, mode, base_ref, controls_ref, key, base_qualified = item
            _validate_context(inspected, self.paths, base_ref, controls_ref,
                season=season, mode=mode, cancelled=cancelled,
                base_qualified=base_qualified)
            return key

        if len(pending) == 1:
            keys = [qualify(pending[0])]
        else:
            # Context engines/providers are private and read_scope is thread
            # local. Only independent cold qualification runs in two workers;
            # publication, final proofs and result transfer remain here.
            with ThreadPoolExecutor(max_workers=2) as workers:
                futures = [workers.submit(qualify, item) for item in pending]
                try:
                    # Preserve context error precedence; a later failure must
                    # not cancel an earlier context and mask its real error.
                    keys = [future.result() for future in futures]
                except BaseException:
                    stopped.set()
                    for future in futures:
                        future.cancel()
                    raise
        for key in keys:
            self._qualified.add((key[0], ()))
            self._qualified.add(key)

    def __call__(self, document):
        self._current()
        if self.check_updates is not None:
            self.check_updates()
        inspected = selection.inspect_selection(self.root, document,
            supported_capabilities=self.resolver.supported_capabilities,
            project_registry_refs=self.resolver.project_registry_refs,
            cancelled=self.cancelled, assert_current=self._current)
        selected = self.contexts if self.contexts is not None else tuple(
            (*key.split(':'), row['recognitionBase'], row['controls'])
            for key, row in inspected.contexts.items()
            if row['recognitionBase'] is not None and row['controls'] is not None)
        reused = False
        pending = []
        for season, mode, base_ref, controls_ref in selected:
            self._current()
            key = self._key(inspected, season, mode, base_ref, controls_ref)
            base_key = (key[0], ())
            if self._known(key):
                reused = True
            else:
                base_qualified = self._known(base_key)
                pending.append((season, mode, base_ref, controls_ref, key, base_qualified))
                reused |= base_qualified
        # Base checks do not consume author data. Successful subresults never
        # combine separately qualified authors into a qualified author group.
        self._qualify_missing(inspected, pending)
        if reused or len(pending) > 1:
            # Parallel context scopes can finish at different instants. Join
            # them all before proving the complete selection once more here.
            self._prove_inputs(inspected)
        self._current()

    def committed(self):
        # Only successful publication/currentness transfers results to Service.
        if self.qualifications is not None:
            self.qualifications.remember(self._qualified)


def _prepare_display_updates(root, snapshot, display_refs, remove_display_refs,
                             allowed_contexts, operation_current):
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
        if (identity.season, identity.mode) not in allowed_contexts:
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
            if (new_identity.season, new_identity.mode) not in allowed_contexts:
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


def _activate_contexts_locked(root, snapshot, paths, resolver, contexts, *, display_refs,
                              cancelled, remove_display_refs=(), qualifications=None):
    proposals = contexts
    def operation_current():
        files._check_cancelled(cancelled)

    expected_stamp = snapshot.catalog_stamp
    context_keys = tuple(f'{season}:{mode}' for season, mode, _, _ in contexts)
    allowed_contexts = frozenset((season, mode) for season, mode, _, _ in contexts)
    displays, check_updates = _prepare_display_updates(root, snapshot, display_refs,
        remove_display_refs, allowed_contexts, operation_current)
    validate = _SelectionValidator(root, paths, resolver, contexts=proposals,
        check_updates=check_updates, cancelled=cancelled, qualifications=qualifications)

    already_active = (displays == snapshot.displays and all(
        (current := snapshot.contexts.get(context_key)) is not None
        and current['recognitionBase'] == base_ref
        and current['controls'] == controls_ref
        for context_key, (_, _, base_ref, controls_ref) in zip(context_keys, proposals)))
    contexts = {key:{role:_wire(row[role]) for role in _ROLES} for key,row in snapshot.contexts.items()}
    for context_key, (_, _, base_ref, controls_ref) in zip(context_keys, proposals):
        row = contexts.setdefault(context_key, {role:None for role in _ROLES})
        row.update(recognitionBase=_wire(base_ref), controls=_wire(controls_ref))
    document = dict(schema=catalog.SCHEMA,
        generation=snapshot.generation if already_active else str(uuid.uuid4()),
        contexts=contexts, displays=[_wire(ref) for ref in displays])
    if already_active:
        validate(document)
        operation_current()
        snapshot.assert_current()
        validate.committed()
        return dict(status='already-active', generation=snapshot.generation, contexts=list(context_keys))
    result = publication.activate_catalog_locked(root, document, expected_catalog_stamp=expected_stamp,
        validate=validate, cancelled=cancelled)
    validate.committed()
    return dict(result, contexts=list(context_keys))


def _activate_locked(root, snapshot, paths, resolver, *, season, mode, display_refs,
                     cancelled, base_ref=None, controls_ref=None, remove_display_refs=(), qualifications=None):
    context_key = f'{season}:{mode}'
    current = snapshot.contexts.get(context_key)
    if base_ref is None and controls_ref is None:
        if current is None or current['recognitionBase'] is None or current['controls'] is None:
            raise catalog.ResourceCatalogError('resource-display-base-context-not-active')
        base_ref, controls_ref = current['recognitionBase'], current['controls']
    outcome = _activate_contexts_locked(root, snapshot, paths, resolver,
        ((season, mode, base_ref, controls_ref),), display_refs=display_refs,
        remove_display_refs=remove_display_refs, cancelled=cancelled, qualifications=qualifications)
    outcome.pop('contexts', None)
    outcome['context'] = context_key
    return outcome


def activate_contexts(resolver, paths, contexts, *, display_refs=(), remove_display_refs=(),
                      expected_catalog_stamp=_UNSPECIFIED_CATALOG, cancelled=None, qualifications=None):
    """Publish all proposed base/control contexts in one catalog transaction.

    The caller owns the Session idle lease. Every context is semantically
    admitted against the same complete document before the single publication
    call. Display removals remain global and explicit; no omitted author is
    silently dropped. Data admission grants no live/rank permission.
    """
    contexts = activation_plan.validate_contexts(contexts)
    root = _resolved_root(resolver, paths)
    with resource_write_lock(root, wait=True, cancelled=cancelled):
        snapshot = _snapshot_for_root(resolver, root)
        if (expected_catalog_stamp is not _UNSPECIFIED_CATALOG
                and expected_catalog_stamp != snapshot.catalog_stamp):
            raise catalog.ResourceCatalogError('resource-catalog-superseded')
        return _activate_contexts_locked(root, snapshot, paths, resolver, contexts,
            display_refs=display_refs, remove_display_refs=remove_display_refs,
            cancelled=cancelled, qualifications=qualifications)


def activate_context(resolver, paths, base_ref, controls_ref, *, season='s0', mode='nightmare',
                     display_refs=(), remove_display_refs=(),
                     expected_catalog_stamp=_UNSPECIFIED_CATALOG, cancelled=None, qualifications=None):
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
            remove_display_refs=remove_display_refs, cancelled=cancelled, qualifications=qualifications)


def activate_display(resolver, paths, display_ref, *, cancelled=None, qualifications=None):
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
            mode=identity.mode, display_refs=(display_ref,), cancelled=cancelled, qualifications=qualifications)
        return dict(outcome, context=f'{identity.season}:{identity.mode}', profileId=identity.profile_id)


def activate_displays(resolver, paths, display_refs, *, cancelled=None, qualifications=None):
    """Admit every display in an imported archive in one selection change."""
    from imported_author_display import read_display_identity
    display_refs = tuple(display_refs)
    if not display_refs or any(not isinstance(ref, ComponentRef) for ref in display_refs):
        raise catalog.ResourceCatalogError('resource-catalog-reference-invalid')
    root = _resolved_root(resolver, paths)
    with resource_write_lock(root, wait=True, cancelled=cancelled):
        snapshot = _snapshot_for_root(resolver, root)
        contexts = {}
        for ref in display_refs:
            files._check_cancelled(cancelled)
            view = PackageView(root, ref, assert_current=lambda: files._check_cancelled(cancelled))
            identity = read_display_identity(view)
            key = f'{identity.season}:{identity.mode}'
            current = snapshot.contexts.get(key)
            if current is None or current['recognitionBase'] is None or current['controls'] is None:
                raise catalog.ResourceCatalogError('resource-display-base-context-not-active')
            contexts[key] = (identity.season, identity.mode, current['recognitionBase'], current['controls'])
        return _activate_contexts_locked(root, snapshot, paths, resolver, tuple(contexts.values()),
            display_refs=display_refs, cancelled=cancelled, qualifications=qualifications)


def validate_selected_resources(resolver, paths, document, *, cancelled=None):
    """Validate the remaining selection during a saved-version removal."""
    selected_resource_validator(resolver, paths, cancelled=cancelled)(document)


def selected_resource_validator(resolver, paths, *, cancelled=None, qualifications=None):
    """One private validator for both checks of a single removal transaction."""
    root = _resolved_root(resolver, paths)
    return _SelectionValidator(root, paths, resolver, cancelled=cancelled, qualifications=qualifications)
