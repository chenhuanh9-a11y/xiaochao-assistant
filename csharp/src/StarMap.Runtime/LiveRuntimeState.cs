using System.Text.Json;
using StarMap.Core.Session;
using StarMap.Vision;

namespace StarMap.Runtime;

public sealed record LiveRuntimeOptions
{
    // app/map_open_preparation.py, not the first-visible-pixel performance target.
    public TimeSpan PreparationTimeout { get; init; } = TimeSpan.FromSeconds(1.05);
    public TimeSpan PreparationInterval { get; init; } = TimeSpan.FromSeconds(0.05);
    public TimeSpan MainEntryBudget { get; init; } = TimeSpan.FromSeconds(1);
    public S1FloorUse S1FloorUse { get; init; } = S1FloorUse.Unavailable;
    public JsonElement? FloorSettings { get; init; }
    public TimeProvider TimeProvider { get; init; } = TimeProvider.System;
}

public sealed record RuntimeCandidate(string MapId, string IdentityProfileId, double Score,
    bool IdentityVerified, bool PoseVerified);

/// <summary>Verified native-reference to client geometry, explicitly not a canonical/display bridge.</summary>
public sealed class NativeGeometryPose
{
    public NativeGeometryPose(string mapId, string floorId, string resourceId, string frameId, double[][] matrix)
    {
        MapId = mapId; FloorId = floorId; ResourceId = resourceId; FrameId = frameId;
        Matrix = Array.AsReadOnly(matrix.Select(row => (IReadOnlyList<double>)Array.AsReadOnly(row.ToArray())).ToArray());
    }
    public string MapId { get; }
    public string FloorId { get; }
    public string ResourceId { get; }
    public string FrameId { get; }
    public IReadOnlyList<IReadOnlyList<double>> Matrix { get; }
    public double[][] ToMatrix() => Matrix.Select(row => row.ToArray()).ToArray();
}

public sealed record LiveRuntimeState(RuntimeContext Context, bool ResourcesBound, bool RoundActive,
    bool MapOpen, bool WorkActive, bool IdentityLocked, string IdentitySource, bool NativeGeometryVerified,
    NativeGeometryPose? NativePose, IReadOnlyList<RuntimeCandidate> Candidates, int SelectedCandidateIndex,
    string Reason, JsonElement Diagnostics, IReadOnlyList<string> CapabilityGaps)
{
    public bool Closed { get; init; }
    public string? ManualFloor { get; init; }
    public string? ObservedFloor { get; init; }
}
