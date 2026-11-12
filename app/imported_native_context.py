"""Shared lifetime and component binding for imported native data contexts."""
from imported_native_base import NativeBaseBundle
from imported_resource_packages import PackageView


class ImportedResourceOwners:
    """Actual base/controls owners used by one explicit reference read operation."""
    def __init__(self, base_view, bundle, controls, native=None, legacy=None):
        self.base_view, self.bundle, self.controls = base_view, bundle, controls
        self.controls_view = controls._view
        self.native, self.legacy = native, legacy

    def assert_current(self):
        if self.native is not None:
            self.native.assert_current()
        else:
            self.bundle.assert_current()
            self.controls.assert_current()
            self.bundle.assert_current()

    @staticmethod
    def _view_state(view):
        return (id(view), view.resource_root, view.component_ref, id(view._assert_current))

    def read_state(self):
        """RAM ownership only; guarded properties belong at the two boundaries."""
        bundle, controls, native, legacy = self.bundle, self.controls, self.native, self.legacy
        values = bundle._values
        reader = values['reader']
        return (self._view_state(self.base_view), self._view_state(self.controls_view),
            id(bundle), id(bundle._view), bundle._root, values['component_ref'],
            tuple(id(values[name]) for name in ('pack', 'resources', 'versions', 'reader', 'fast_structure_source')),
            id(reader.view), reader.root, id(controls), id(controls._view), controls._root,
            None if native is None else (id(native), id(native._base_view), id(native._bundle),
                id(native._controls), native._root, native._component_ref),
            None if legacy is None else legacy.read_owner_state())

    def legacy_keys(self):
        return (frozenset((key[1], key[3]) for key in self.legacy.bindings)
                if self.legacy is not None else frozenset())


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
        # Keep base checks around controls: a controls check can observe a
        # concurrent base replacement. Binding fields share the final read.
        self._bundle.assert_current()
        self._controls.assert_current()
        view, root, component_ref, reader = self._bundle.read_binding()
        base_root = root
        if view is not self._base_view:
            base_root = self._base_view.root
            # An independent view check can supersede the bundle or reader.
            # Acquire their final binding after that check, as the old reader
            # property did; the common same-view path needs no extra read.
            view, _, component_ref, reader = self._bundle.read_binding()
        if (self._base_view.component_ref != self._component_ref
                or component_ref != self._component_ref
                or view.component_ref != component_ref
                or base_root != self._root
                or reader.root.resolve() != self._root.resolve()):
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
