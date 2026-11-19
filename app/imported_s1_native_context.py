"""Current, component-bound S1 data for existing native consumers.

The decoded base supplies structure and coordinates. Controls must match the
original observer through require_original_controls. A mismatch raises
OriginalControlsError; this context does not publish a soft qualification-gap
capability. Candidate policy, solving and display fallback stay with their
existing owners.
"""
from imported_native_context import ImportedNativeContext
from imported_original_controls import require_original_controls
from imported_s1_controls_profile import S1ControlsProfile
from s1_floor_observation import CAPABILITY, ORDER, SOURCE_REPLAY_FLOORS
from s1_native_resources import S1_RECORDED_CANDIDATE_CAPABILITY
import star_map_import as files


def _reject(reason):
    code = 'imported-s1-native-context-' + reason
    raise files.ImportRejected(code, code=code)


class ImportedS1NativeContext(ImportedNativeContext):
    """Adapt one validated S1 base without granting general live authority.

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

    def _require_original_controls(self):
        self.assert_current()
        require_original_controls(self._controls, season='s1', mode=self._bundle.mode)
        self.assert_current()

    @property
    def live_gaps(self):
        self._require_original_controls()
        return ('s1-floor-control-detector-unvalidated',)

    @property
    def floor_observation_capability(self):
        self._require_original_controls()
        return dict(contract=CAPABILITY, dataFloors=list(self._floor_ids),
            manualMapObservation=True, automaticFloors=list(ORDER), contractFloors=list(ORDER),
            sourceReplayFloors=list(SOURCE_REPLAY_FLOORS),
            syntheticOnlyFloors=[floor for floor in ORDER if floor not in SOURCE_REPLAY_FLOORS],
            validationScope='source-image-replay', liveValidated=False)

    @property
    def structure_candidate_capability(self):
        self._require_original_controls()
        return dict(S1_RECORDED_CANDIDATE_CAPABILITY, dataFloors=list(self._floor_ids),
            operatorDeclaredScanReady=True, operatorDeclaredFloors=list(self._observed_values),
            validationScope='source-image-replay')

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
