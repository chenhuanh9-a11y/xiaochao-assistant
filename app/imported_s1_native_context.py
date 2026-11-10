"""Current, component-bound S1 data for existing native consumers.

The decoded base supplies structure and coordinates. External controls supply
parameters, never the source-replay authority of the old disk-backed context.
Candidate policy, solving and display fallback stay with their existing owners.
"""
from imported_native_base import NativeBaseBundle
from imported_resource_packages import PackageView
from imported_s1_controls_profile import S1ControlsProfile
from s1_floor_observation import CAPABILITY
from s1_native_resources import S1_RECORDED_CANDIDATE_CAPABILITY
import star_map_import as files


def _reject(reason):
    code = 'imported-s1-native-context-' + reason
    raise files.ImportRejected(code, code=code)


class ImportedS1NativeContext:
    """Adapt one validated S1 base without granting automatic observation.

    Returned decoded objects retain their existing lifetime contract: callers
    must call assert_current before operations and cache hits. The same guard
    checks both components, including accesses that only return metadata.
    """

    def __init__(self, base_view, bundle, controls):
        if (not isinstance(base_view, PackageView) or not isinstance(bundle, NativeBaseBundle)
                or not isinstance(controls, S1ControlsProfile)):
            _reject('validated-components-required')
        self._base_view, self._bundle, self._controls = base_view, bundle, controls
        bundle.assert_current()
        controls.assert_current()
        self._root = base_view.root
        self._component_ref = base_view.component_ref
        if (bundle.component_ref != self._component_ref
                or bundle.reader.view.component_ref != self._component_ref
                or bundle.reader.root.resolve() != self._root.resolve()):
            _reject('base-bundle-mismatch')
        if bundle.season != 's1' or bundle.mode not in ('difficult', 'nightmare'):
            _reject('context-unsupported')
        if not bundle.ready:
            _reject('base-incomplete')
        context = controls.for_context(bundle.season, bundle.mode)
        if context is None or context.contract.floor_ids != tuple(bundle.pack.floors):
            _reject('controls-base-floors-mismatch')
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
            _reject('base-bundle-mismatch')

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
        # StructureCatalog owns its existing resource-key derivation. Use the
        # actual pinned package manifest, not a reconstructed native manifest.
        return self._base_view.manifest

    @property
    def controls(self):
        self.assert_current()
        return self._controls

    @property
    def neutral_relative(self):
        return self._decoded('interfaces')['neutralDisplay']

    @property
    def legacy_bindings(self):
        self.assert_current()
        return {}

    @property
    def unbound_layers(self):
        self.assert_current()
        return ()

    @property
    def catalog(self):
        self.assert_current()
        return None

    @property
    def live_gaps(self):
        self.assert_current()
        return ('s1-external-controls-floor-observation-unvalidated',)

    @property
    def floor_observation_capability(self):
        self.assert_current()
        return dict(contract=CAPABILITY,
            dataFloors=list(self._floor_ids), contractFloors=list(self._observed_values),
            manualMapObservation=False, automaticFloors=[], sourceReplayFloors=[],
            observedFloorValidated=False, automaticFloorEligible=False,
            validationScope='external-controls-unreviewed', liveValidated=False)

    @property
    def structure_candidate_capability(self):
        self.assert_current()
        # The existing runtime interprets candidateReady as permission to use
        # automatic floor observation even for recorded input. Only explicitly
        # supplied-floor scanning is supported by this data adapter.
        return dict(contract=S1_RECORDED_CANDIDATE_CAPABILITY['contract'],
            dataFloors=list(self._floor_ids), contractFloors=list(self._observed_values),
            operatorDeclaredScanReady=True, operatorDeclaredFloors=list(self._observed_values),
            candidateReady=False, observedFloors=[], observedFloorValidated=False,
            automaticFloorEligible=False, validationScope='external-controls-unreviewed',
            liveValidated=False)

    def has_reference_binding(self, map_id, floor):
        self.assert_current()
        return ('shared-structure', str(map_id), 'variant-01', str(floor)) in self._bundle.resources.entries

    def legacy_reference(self, map_id, floor):
        self.assert_current()
        return None

    def display_reference(self, map_id, floor='-1'):
        self.assert_current()
        return None

    def rendered_display_layer(self, map_id, floor, reference):
        self.assert_current()
        from s1_native_display import NativeDisplayResult
        # Source textures are not in the recognition base. The existing display
        # consumer renders its shared-structure fallback from the real provider.
        return NativeDisplayResult(None, 's1-native-display-not-installed')
