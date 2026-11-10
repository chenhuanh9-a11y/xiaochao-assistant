"""Shared lifetime and component binding for imported native data contexts."""
from imported_native_base import NativeBaseBundle
from imported_resource_packages import PackageView


class ImportedNativeContext:
    """Bind a validated base and controls; subclasses own capability semantics."""

    def __init__(self, base_view, bundle, controls, *, season, modes, controls_type, reject):
        if (not isinstance(base_view, PackageView) or not isinstance(bundle, NativeBaseBundle)
                or not isinstance(controls, controls_type)):
            reject('validated-components-required')
        self._reject = reject
        self._base_view, self._bundle, self._controls = base_view, bundle, controls
        bundle.assert_current()
        controls.assert_current()
        self._root = base_view.root
        self._component_ref = base_view.component_ref
        if (bundle.component_ref != self._component_ref
                or bundle.reader.view.component_ref != self._component_ref
                or bundle.reader.root.resolve() != self._root.resolve()):
            reject('base-bundle-mismatch')
        if bundle.season != season or bundle.mode not in modes:
            reject('context-unsupported')
        if not bundle.ready:
            reject('base-incomplete')
        context = controls.for_context(bundle.season, bundle.mode)
        if context is None or context.contract.floor_ids != tuple(bundle.pack.floors):
            reject('controls-base-floors-mismatch')
        self._floor_ids = context.contract.floor_ids
        self._observed_values = context.contract.observed_values
        self.assert_current()

    def assert_current(self):
        self._bundle.assert_current()
        self._controls.assert_current()
        if (self._base_view.component_ref != self._component_ref
                or self._bundle.component_ref != self._component_ref
                or self._base_view.root != self._root
                or self._bundle.reader.root.resolve() != self._root.resolve()):
            self._reject('base-bundle-mismatch')

    def _decoded(self, name):
        self.assert_current()
        return getattr(self._bundle, name)

    pack = property(lambda self: self._decoded('pack'))
    resources = property(lambda self: self._decoded('resources'))
    adapters = property(lambda self: self._decoded('adapters'))
    versions = property(lambda self: self._decoded('versions'))
    interfaces = property(lambda self: self._decoded('interfaces'))
    reader = property(lambda self: self._decoded('reader'))
    season = property(lambda self: self._decoded('season'))
    mode = property(lambda self: self._decoded('mode'))

    @property
    def root(self):
        self.assert_current()
        return self._root

    @property
    def component_ref(self):
        self.assert_current()
        return self._component_ref

    @property
    def manifest(self):
        self.assert_current()
        return self._base_view.manifest

    @property
    def controls(self):
        self.assert_current()
        return self._controls

    @property
    def neutral_relative(self):
        return self._decoded('interfaces')['neutralDisplay']

    @property
    def catalog(self):
        self.assert_current()
        return None
