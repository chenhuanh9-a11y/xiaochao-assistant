"""Current S0 difficult data, including explicitly retained legacy references."""
from copy import deepcopy

from imported_controls_profile import S0ControlsProfile
from imported_native_context import ImportedNativeContext
import star_map_import as files


def _reject(reason):
    code = 'imported-difficult-native-context-' + reason
    raise files.ImportRejected(code, code=code)


class ImportedDifficultNativeContext(ImportedNativeContext):
    """Supply current-frame structure candidates without admitting ranking."""

    def __init__(self, base_view, bundle, controls):
        super().__init__(base_view, bundle, controls, season='s0', modes=('difficult',),
                         controls_type=S0ControlsProfile, reject=_reject)
        source = bundle.reader.json(bundle.interfaces['sourceManifest'])
        self._pending_native_bindings = deepcopy(source.get('pendingOriginalBindings', []))
        self.assert_current()

    @property
    def legacy_bindings(self):
        self.assert_current()
        references = self._bundle.legacy_references
        return references.bindings if references is not None else {}

    @property
    def pending_native_bindings(self):
        self.assert_current()
        return deepcopy(self._pending_native_bindings)

    @property
    def live_gaps(self):
        self.assert_current()
        return ('s0-difficult-rank-unvalidated',)

    @property
    def structure_candidate_capability(self):
        self.assert_current()
        return dict(dataFloors=list(self._floor_ids), contractFloors=list(self._observed_values),
            operatorDeclaredScanReady=True, candidateReady=True,
            validationScope='current-frame-structure-candidates', liveValidated=False)

    def local(self, relative):
        self.assert_current()
        return self._base_view.path(relative)

    def has_reference_binding(self, map_id, floor):
        self.assert_current()
        profile = self._bundle.pack.identity_profiles.get(str(map_id))
        key = (profile, str(map_id), 'variant-01', str(floor))
        return key in self._bundle.resources.entries or key in self.legacy_bindings

    def legacy_reference(self, map_id, floor):
        self.assert_current()
        references = self._bundle.legacy_references
        result = references.reference(map_id, floor) if references is not None else None
        self.assert_current()
        return result
