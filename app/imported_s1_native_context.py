"""Current, component-bound S1 data for existing native consumers.

The decoded base supplies structure and coordinates. External controls supply
parameters, never the source-replay authority of the old disk-backed context.
Candidate policy, solving and display fallback stay with their existing owners.
"""
from imported_native_context import ImportedNativeContext
from imported_s1_controls_profile import S1ControlsProfile
from s1_floor_observation import CAPABILITY
from s1_native_resources import S1_RECORDED_CANDIDATE_CAPABILITY
import star_map_import as files


def _reject(reason):
    code = 'imported-s1-native-context-' + reason
    raise files.ImportRejected(code, code=code)


class ImportedS1NativeContext(ImportedNativeContext):
    """Adapt one validated S1 base without granting automatic observation.

    Returned decoded objects retain their existing lifetime contract: callers
    must call assert_current before operations and cache hits. The same guard
    checks both components, including accesses that only return metadata.
    """

    def __init__(self, base_view, bundle, controls):
        super().__init__(base_view, bundle, controls, season='s1', modes=('difficult', 'nightmare'),
                         controls_type=S1ControlsProfile, reject=_reject)

    @property
    def legacy_bindings(self):
        self.assert_current()
        return {}

    @property
    def unbound_layers(self):
        self.assert_current()
        return ()

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
