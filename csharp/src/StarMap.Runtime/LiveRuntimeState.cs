using System.Text.Json;
using StarMap.Core.Session;
using StarMap.Vision;

namespace StarMap.Runtime;

public sealed record LiveRuntimeOptions
{
    // app/map_open_preparation.py, not the first-visible-pixel performance target.
    public TimeSpan PreparationTimeout { get; init; } = TimeSpan.FromSeconds(1.05);
    public TimeSpan PreparationInterval { get; init; } = TimeSpan.FromSeconds(0.05);
    // Original client_engine.INITIAL_IDENTITY_BUDGET_SECONDS, shared by all
    // initial rank producers rather than a separate budget for each stage.
    public TimeSpan MainEntryBudget { get; init; } = TimeSpan.FromSeconds(1.85);
    public S1FloorUse S1FloorUse { get; init; } = S1FloorUse.Unavailable;
    public JsonElement? FloorSettings { get; init; }
    public bool SheetVisibilityEnabled { get; init; } = true;
    public bool Idvb0059Enabled { get; init; } = true;
    public TimeProvider TimeProvider { get; init; } = TimeProvider.System;
}

public sealed record RuntimeCandidate(string MapId, string IdentityProfileId, double Score,
    bool IdentityVerified, bool PoseVerified)
{
    public string IdentityKind { get; init; } = "map";
    public string? GroupId { get; init; }
    public string? Name { get; init; }
    public IReadOnlyList<string> MemberIds { get; init; } = Array.Empty<string>();
}

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
    public bool AlignedVisible { get; init; } = true;
    public string IdentityKind { get; init; } = "unresolved";
    public string? MapDisplayName { get; init; }
    public string? IdentityGroupId { get; init; }
    public IReadOnlyList<string> IdentityMemberIds { get; init; } = Array.Empty<string>();
    public RuntimeGroupChoice GroupChoice { get; init; } = new(0, null, null, null, null, "", "", Array.Empty<RuntimeGroupMember>());
    public RuntimeDisplayState Display { get; init; } = new(false, false, false, "pose-not-verified");
}
