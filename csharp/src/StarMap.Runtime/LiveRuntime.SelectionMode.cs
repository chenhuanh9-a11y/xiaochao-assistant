namespace StarMap.Runtime;

public sealed partial class LiveRuntime
{
    private S0MapSelectionMode _mapSelectionMode;
    private S0MapSelectionMode EffectiveMapSelectionMode => _selection.Season == "s1" ? S0MapSelectionMode.Auto : _mapSelectionMode;
    private bool ManualSelectionRequired => _roundActive && EffectiveMapSelectionMode == S0MapSelectionMode.Manual && _mapId is null;

    /// <summary>S0 identity-selection preference. A chosen identity/pose and
    /// the round's accumulated exclusions are not reset by this setting.</summary>
    public bool SetMapSelectionMode(S0MapSelectionMode mode)
    {
        if (!Enum.IsDefined(mode)) throw new ArgumentOutOfRangeException(nameof(mode));
        lock (_sync)
        {
            if (_closed) return false;
            if (_mapSelectionMode == mode) return true;
            _mapSelectionMode = mode;
            if (_selection.Season == "s0" && _mapId is null)
            {
                // This is an external policy change, not an epoch advanced by
                // the current worker. Its old progress/result stays retired.
                _epoch++; ClearFloorClickIntent(preservePolicy: true);
                _pose = null; _mapRecovery = null;
                if (IsStructureProvisional) _identitySource = "";
                _s1ProvisionalMapId = null;
                RevokeDisplay("map-selection-mode-changed");
                _structureSession?.RevokeCurrent(); _structureTicket = null;
                _reason = ManualSelectionRequired && _mapOpen && _floor is not null
                    ? "manual-map-selection-pending" : "map-selection-mode-changed";
            }
            Publish(); return true;
        }
    }
}
