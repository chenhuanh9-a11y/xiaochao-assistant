"""Route a display dependency to the selected version of its base component.

This is a dependency candidate, not proof of display compatibility. The author
decoder still checks every layer against the selected base's actual reference,
structure version and coordinate space before activation or display.
"""


def matches_display_base(view, base_ref):
    if base_ref is None or view.component['type'] != 'display':
        return False
    dependencies = tuple(view.dependencies.values())
    if len(dependencies) != 1:
        return False
    dependency, = dependencies
    return (dependency.package_id, dependency.component_id) == (base_ref.package_id, base_ref.component_id)


def selected_display_base(view, contexts):
    """Resolve only the display's own context; never activate its old base."""
    component = view.component
    if component['type'] != 'display' or len(component['contexts']) != 1:
        return None
    context, = component['contexts']
    base = contexts.get(context['season']+':'+context['mode'], {}).get('recognitionBase')
    return base if matches_display_base(view, base) else None
