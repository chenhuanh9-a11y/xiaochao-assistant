"""Route a display dependency to the selected version of its base component.

This is a dependency candidate, not proof of display compatibility. The author
decoder still checks every layer against the selected base's actual reference,
structure version and coordinate space before activation or display.
"""


def _declared_compatible_base_ids(root, base_ref):
    try:
        from imported_resource_packages import PackageView
        from imported_resource_reader import ComponentResourceReader
        base_view = PackageView(root, base_ref)
        with base_view.read_scope():
            doc = ComponentResourceReader(base_view).json(base_view.component['entrypoint'])
            return tuple(str(x) for x in (doc.get('compatibleBasePackageIds') or ()) if x)
    except Exception:
        return ()


def matches_display_base(view, base_ref, *, compatible_package_ids=()):
    if base_ref is None or view.component['type'] != 'display':
        return False
    dependencies = tuple(view.dependencies.values())
    if len(dependencies) != 1:
        return False
    dependency, = dependencies
    if dependency.component_id != base_ref.component_id:
        return False
    if dependency.package_id == base_ref.package_id:
        return True
    allowed_ids = {str(x) for x in (compatible_package_ids or ()) if x}
    if not allowed_ids and hasattr(view, 'root'):
        allowed_ids.update(_declared_compatible_base_ids(view.root, base_ref))
    return dependency.package_id in allowed_ids


def selected_display_base(view, contexts):
    """Resolve only the display's own context; never activate its old base."""
    component = view.component
    if component['type'] != 'display' or len(component['contexts']) != 1:
        return None
    context, = component['contexts']
    base = contexts.get(context['season']+':'+context['mode'], {}).get('recognitionBase')
    return base if matches_display_base(view, base) else None
