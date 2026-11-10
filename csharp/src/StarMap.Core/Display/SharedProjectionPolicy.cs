using System.Text.Json;
using System.Text.Json.Nodes;
using StarMap.Core.Alignment;

namespace StarMap.Core.Display;

/// <summary>Current display provenance, independent of the group's historical geometry proofs.</summary>
public sealed record SharedProjectionSource(string MapId,string Floor,AlignmentCycleId CycleId,
    bool Accepted,bool Provisional,string? ProjectionSourceFloor=null);

public sealed record SharedProjectionDecision(bool Accepted,string Reason,string SourceFloor,bool SearchOnly);

/// <summary>Original relation/provenance admission. Runtime separately owns observation and resource lifetimes.</summary>
public static class SharedProjectionPolicy
{
    public static string SourceFloor(RoundLayerGroup group,string target,SharedProjectionSource? current) =>
        current is not null && current.Floor!=target?current.Floor
            :current?.ProjectionSourceFloor??group.TrackingFloor??group.AnchorFloor;

    public static SharedProjectionDecision Evaluate(RoundLayerGroup group,string map,string target,AlignmentCycleId cycle,
        string effectiveProfile,SharedProjectionSource? current,JsonObject? seed)
    {
        string source=SourceFloor(group,target,current);
        bool searchOnly=seed?["requiresCurrentFrameDisplayValidation"]?.GetValue<bool>()==true;
        SharedProjectionDecision Reject(string reason)=>new(false,reason,source,searchOnly);
        if(group.MapId!=map || source==target || source is not ("1" or "2") || target is not ("1" or "2") || group.TrackingFloor=="B1")
            return Reject("projection-normal-floors-only");
        var verified=group.CanonicalGeometryVerifiedFloors;
        if(searchOnly?!verified.Contains(source):!verified.Any(f=>f is "1" or "2"))return Reject("projection-source-geometry-unavailable");
        if(group.RouteProfileId!=effectiveProfile)return Reject("projection-profile-mismatch");
        if(seed?["targetToSourceMatrix"] is not { } matrix || Matrix3.TryRead(JsonSerializer.SerializeToElement(matrix)) is null)
            return Reject("projection-relation-unavailable");
        if(group.CanonicalPoseForFloor(target) is null)return Reject("projection-target-relation-unavailable");
        if(searchOnly)
        {
            bool sameScope=current?.MapId==map && current.CycleId==cycle;
            bool acceptedSource=sameScope && current!.Floor==source && current.Accepted;
            bool sameProvisional=sameScope && current!.Floor==target && current.Provisional;
            bool transition=current is not null && current.Floor!=target;
            if(group.CycleId!=cycle || !(acceptedSource||sameProvisional) || !transition&&!sameProvisional
                || seed!["boundedCrossFloorOnly"]?.GetValue<bool>()!=true)
                return Reject("projection-provenance-unavailable");
        }
        return new(true,"shared-root-provisional",source,searchOnly);
    }
}
