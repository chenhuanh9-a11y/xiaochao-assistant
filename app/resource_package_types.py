"""Filename conventions for native base and author route packages.

Package contents remain ZIP archives. The public suffix identifies the role of
the package and is checked against already-validated manifest components.
"""
from pathlib import Path
from urllib.parse import urlsplit


BASE_SUFFIX = '.xingtu-base'
ROUTE_SUFFIX = '.xingtu-route'
PACKAGE_SUFFIXES = (BASE_SUFFIX, ROUTE_SUFFIX)


class PackageTypeError(ValueError):
    """A stable, machine-readable package naming or kind error."""

    def __init__(self, code):
        self.code = code
        super().__init__(code)


def infer_package_suffix(components):
    """Infer a package role from validated manifest component declarations."""
    types = []
    for component in components:
        if not isinstance(component, dict):
            raise PackageTypeError('resource-package-kind-unsupported')
        component_type = component.get('type')
        if not isinstance(component_type, str):
            raise PackageTypeError('resource-package-kind-unsupported')
        types.append(component_type)

    if not types:
        raise PackageTypeError('resource-package-kind-unsupported')

    type_set = set(types)
    if type_set == {'display'}:
        return ROUTE_SUFFIX

    base_types = {'recognition-base', 'controls-profile', 'recognition-hints'}
    if (type_set <= base_types
            and {'recognition-base', 'controls-profile'} <= type_set):
        return BASE_SUFFIX

    raise PackageTypeError('resource-package-kind-unsupported')


def validate_package_filename(path, components, *, allow_legacy_zip=True):
    """Check a package suffix against its manifest role.

    A legacy ``.zip`` is accepted as an untyped transport name and returns
    ``None``. It never grants the archive a base or route role.
    """
    suffix = Path(path).suffix.casefold()
    if suffix == '.zip' and allow_legacy_zip:
        return None
    if suffix not in PACKAGE_SUFFIXES:
        raise PackageTypeError('resource-package-extension-unsupported')

    inferred = infer_package_suffix(components)
    if suffix != inferred:
        raise PackageTypeError('resource-package-extension-mismatch')
    return inferred


def preferred_download_suffix(url):
    """Return a known package suffix from a URL path, else ZIP for transport."""
    path = urlsplit(str(url or '')).path.casefold()
    for suffix in PACKAGE_SUFFIXES:
        if path.endswith(suffix):
            return suffix
    return '.zip'
