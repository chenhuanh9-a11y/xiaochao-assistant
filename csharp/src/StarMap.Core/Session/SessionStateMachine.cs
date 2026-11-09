namespace StarMap.Core.Session;

public sealed record MapCanvasEvidence(bool Detected, bool FlatMapCanvas = false, double BackdropRatio = 0);
public readonly record struct FloorResolution(string FloorId, string Reason);
public readonly record struct MapProbeTransition(int Misses, string? Transition);

/// <summary>Non-S0 floors must come from the target context's actual contract.</summary>
public interface IRuntimeFloorContract
{
    IReadOnlySet<string> FloorIds { get; }
    string? FloorIdFor(int? observed);
}

/// <summary>Pure policies from app/session_state_machine.py; none commits runtime state.</summary>
public static class SessionStateMachine
{
    public static bool VerifiedMapHotkeyFrame(MapCanvasEvidence evidence, int? selectedFloor)
        => evidence.Detected && selectedFloor is -1 or 1 or 2;

    public static bool LockedMapCanvasWithoutFloorIsAuthorized(MapCanvasEvidence evidence,
        int? selectedFloor, bool roundLocked)
        => roundLocked && evidence.Detected && evidence.FlatMapCanvas && evidence.BackdropRatio >= 0.80
            && selectedFloor is not (-1 or 1 or 2);

    public static FloorResolution ResolveRuntimeFloor(int? observed, string? manual,
        string season = "s0", IRuntimeFloorContract? contract = null)
    {
        if (season != "s0")
        {
            if (contract is null) return new("", "floor-contract-unavailable");
            if (manual is not null && contract.FloorIds.Contains(manual)) return new(manual, "manual");
            var floor = contract.FloorIdFor(observed);
            return floor is null ? new("", "manual-required") : new(floor, "automatic");
        }
        if (manual is "1" or "2" or "B1") return new(manual, "manual");
        return observed switch { -1 => new("B1", "automatic"), 1 => new("1", "automatic"),
            2 => new("2", "automatic"), _ => new("2", "manual-required") };
    }

    public static int? StableFloorFromObservations(IReadOnlyList<int?> observations, IReadOnlySet<int>? allowed = null)
    {
        var latest = observations.Count > 0 ? observations[^1] : null;
        return latest is int floor && (allowed?.Contains(floor) ?? floor is -1 or 1 or 2) ? floor : null;
    }

    public static (int Misses, bool Closed) MapClosedAfterProbeMisses(int previousMisses, bool probeOpen, int required = 2)
    {
        var misses = probeOpen ? 0 : previousMisses + 1;
        return (misses, misses >= required);
    }

    public static MapProbeTransition MapProbe(bool mapIsOpen, int previousMisses, bool? probeOpen,
        bool closeAllowed = true, int requiredMisses = 2)
    {
        if (!mapIsOpen) return new(0, null);
        if (probeOpen is null) return new(previousMisses, null);
        if (!closeAllowed && !probeOpen.Value) return new(0, null);
        var (misses, closed) = MapClosedAfterProbeMisses(previousMisses, probeOpen.Value, Math.Max(1, requiredMisses));
        return new(misses, closed ? "close" : null);
    }

    public static string MapHotkeyTransition(bool mapIsOpen, bool captureInProgress, bool silentCapture = false)
        => mapIsOpen ? "close" : captureInProgress ? silentCapture ? "cancel-silent" : "ignore" : "verify";

    public static bool GlobalKeyIsAllowed(nint? gameWindow, nint? foregroundWindow)
        => gameWindow is not null && gameWindow != 0 && foregroundWindow is not null
            && foregroundWindow != 0 && gameWindow == foregroundWindow;

    public static bool TrustedForegroundIsAllowed(nint? gameWindow, nint? foregroundWindow,
        int? assistantProcessId, int? foregroundProcessId)
        => GlobalKeyIsAllowed(gameWindow, foregroundWindow) || foregroundWindow is not null
            && foregroundWindow != 0 && assistantProcessId is > 0 && foregroundProcessId is > 0
            && assistantProcessId == foregroundProcessId;

    public static bool RankedEventIsCurrent(long? eventToken, long generation) => eventToken is null || eventToken == generation;
    public static bool DisplayEventIsCurrent(long? eventCycle, long? activeCycle)
        => eventCycle is null || activeCycle is not null && eventCycle == activeCycle;
    public static bool ConfirmedOverlaySourceIsAllowed(string? confirmedMapId, string? candidateMapId)
        => !string.IsNullOrEmpty(confirmedMapId) && !string.IsNullOrEmpty(candidateMapId) && confirmedMapId == candidateMapId;
}
