"""Product import prerequisites, read under the existing resource write lock."""
from imported_author_display import read_display_identity
from imported_resource_catalog import CONTEXTS, ResourceCatalogError
from imported_resource_reader import ComponentResourceReader
import star_map_import as files


def _context_base(snapshot, season, mode, *, cancelled=None, validated=None):
    resources = snapshot.resolve_context(season, mode)
    if resources.gaps or resources.base is None or resources.controls is None:
        raise ResourceCatalogError('resource-display-base-context-not-active')
    # Activation decoded the semantics. Recheck every selected component file
    # on this worker; a healthy header cannot hide a missing/corrupt structure.
    # Component-scoped reads keep optional author damage independent of base.
    validated = set() if validated is None else validated
    for view in (resources.base, resources.controls):
        if view.component_ref in validated:
            continue
        reader = ComponentResourceReader(view)
        entrypoint = view.component['entrypoint']
        entry = reader.json(entrypoint)
        if view.component['type'] == 'recognition-base':
            from imported_original_base import SCHEMA
            if entry.get('schema') != SCHEMA:
                raise ResourceCatalogError('original-qt-base-required')
        for relative in view.inventory:
            files._check_cancelled(cancelled)
            if relative != entrypoint:
                reader(view.path(relative))
        validated.add(view.component_ref)
    snapshot.assert_current()
    return resources.base.component_ref


def available_base_contexts(snapshot, *, cancelled=None):
    """Worker-only projection of imported, active and readable B/C contexts."""
    available = []
    validated = set()
    for key in sorted(CONTEXTS):
        files._check_cancelled(cancelled)
        season, mode = key.split(':')
        try:
            _context_base(snapshot, season, mode, cancelled=cancelled, validated=validated)
        except (OSError, ValueError):
            continue
        available.append(key)
    files._check_cancelled(cancelled)
    snapshot.assert_current()
    return tuple(available)


def admit_import(views, resolver, *, cancelled=None):
    """Require complete base ZIPs and an already active base for authors.

    This validates admission, not recognition/pose authority. New base payloads
    still pass the normal native decoders before catalog publication.
    """
    bases, controls, authors = {}, {}, []
    for view in views:
        files._check_cancelled(cancelled)
        component = view.component
        kind = component['type']
        if kind == 'display':
            authors.append(view)
            continue
        if kind not in ('recognition-base', 'controls-profile'):
            continue
        target = bases if kind == 'recognition-base' else controls
        for context in component['contexts']:
            key = context['season'] + ':' + context['mode']
            if key not in CONTEXTS or key in target:
                raise ResourceCatalogError('resource-import-base-context-ambiguous')
            target[key] = view.component_ref
    if bases.keys() != controls.keys():
        raise ResourceCatalogError('resource-import-base-controls-pair-required')
    if authors:
        snapshot = resolver.snapshot()
        validated = set()
        for view in authors:
            files._check_cancelled(cancelled)
            identity = read_display_identity(view)
            base = _context_base(snapshot, identity.season, identity.mode,
                                 cancelled=cancelled, validated=validated)
            dependencies = tuple(view.dependencies.values())
            if dependencies != (base,):
                raise ResourceCatalogError('resource-display-base-version-mismatch')
        snapshot.assert_current()
    files._check_cancelled(cancelled)
    return tuple((*key.split(':'), bases[key], controls[key]) for key in sorted(bases))
