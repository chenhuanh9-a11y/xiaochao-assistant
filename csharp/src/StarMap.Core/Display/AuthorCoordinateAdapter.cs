using System.Text.Json;
using System.Text.Json.Nodes;
using StarMap.Core.Alignment;

namespace StarMap.Core.Display;

/// <summary>Production AuthorCoordinateAdapters admission rules; matrices alone are not evidence.</summary>
public sealed class AuthorCoordinateAdapter
{
    public const string CanonicalSchema = "identityv-nightmare-canonical-coordinate-pack-v1";
    public const string LegacySchema = "identityv-nightmare-author-coordinate-adapters-v1";
    private readonly JsonElement raw;
    private readonly Func<string, string, string, JsonElement, DisplayAffine, NativeCornerValidation>? nativeValidator;
    public string Schema { get; }
    public string ReferenceProfile { get; }
    public string PackId { get; }

    public AuthorCoordinateAdapter(JsonElement document,
        Func<string, string, string, JsonElement, DisplayAffine, NativeCornerValidation>? nativeValidator = null)
    {
        raw = document.Clone();
        this.nativeValidator = nativeValidator;
        Schema = J.String(raw, "schema") ?? "";
        if (Schema is not (CanonicalSchema or LegacySchema)) throw new InvalidDataException("unsupported-author-coordinate-schema");
        var policy = J.Get(raw, "policy");
        if (!J.True(policy, Schema == CanonicalSchema ? "requiresCurrentFrameGeometryFit" : "requiresCurrentFrameRecognitionFit")
            || !J.False(policy, "authorizesMapIdentity") || !J.False(policy, "authorizesCrossFloorProjection"))
            throw new InvalidDataException("unsafe-author-coordinate-policy");
        ReferenceProfile = J.String(raw, "coordinateReferenceProfileId") ?? "wang-qingxin";
        PackId = J.String(raw, "canonicalPackId") ?? J.String(raw, "recognitionPackId") ?? "wang-qingxin-nightmare-v1";
    }

    public JsonElement? Floor(string profile, string map, string floor) =>
        J.Get(J.Get(J.Get(J.Get(J.Get(raw,"profiles"),profile),"maps"),map),"floors") is { } floors ? J.Get(floors,floor) : null;

    /// <summary>Original reviewed search proposal only; it grants no current-frame or display authority.</summary>
    public JsonObject? CrossFloorSearchSeed(string map,string fromFloor,string toFloor,Matrix3 source,string? authorizationProfile=null)
    {
        string profile=string.IsNullOrEmpty(authorizationProfile)?ReferenceProfile:authorizationProfile;
        var container=Schema==CanonicalSchema?J.Get(J.Get(raw,"canonicalCrossFloorSearchCandidates"),map)
            :J.Get(J.Get(J.Get(raw,"profiles"),profile),"crossFloorSearchCandidates") is {} legacy?J.Get(legacy,map):null;
        var pairs=J.Get(container,"pairs");
        JsonElement? row=pairs is {ValueKind:JsonValueKind.Array} values
            ?values.EnumerateArray().FirstOrDefault(item=>J.Get(item,"fromFloor")?.ToString()==fromFloor&&J.Get(item,"toFloor")?.ToString()==toFloor):null;
        if(row is not {ValueKind:JsonValueKind.Object})return null;
        string? review=J.String(row,"reviewStatus");var evidence=J.Get(row,"evidence");
        if(!J.True(row,"currentFrameSearchSeedAllowed")||!J.False(row,"directProjectionAllowed")
            ||!J.False(row,"zeroExplorationDisplayAllowed")||!J.False(row,"liveGameVerified")
            ||review is not ("player-visual-pass-current-frame-required" or "player-visual-marginal-pass-current-frame-required")
            ||!Truthy(J.Get(evidence,"directProjectionWithheldReason")))return null;
        var trial=J.Get(J.Get(raw,"policy"),"playerReviewedCrossFloorProjectionTrial");
        bool allowed=J.True(trial,"enabled")&&Contains(J.Get(trial,"profileIds"),profile)&&Contains(J.Get(trial,"reviewStatuses"),review)
            &&J.True(trial,"requiresConfirmedRoundMap")&&J.True(trial,"requiresStableTargetFloor")
            &&J.True(trial,"requiresVerifiedSourceFloorTransform")&&J.True(trial,"projectionOnlyUntilTargetStructureVerified");
        if(J.Get(row,"targetToSourceMatrix") is not {} targetRaw||Matrix3.TryRead(targetRaw) is not {} target
            ||Math.Abs(source[0,0]*source[1,1]-source[0,1]*source[1,0])<=1e-9
            ||Matrix3.Multiply(source,target) is not {} predicted)return null;
        return new JsonObject {
            ["mapId"]=map,["fromFloor"]=fromFloor,["toFloor"]=toFloor,
            ["matrixSourceToClient"]=JsonSerializer.SerializeToNode(predicted.ToArray()),
            ["sourceMatrixToClient"]=JsonSerializer.SerializeToNode(source.ToArray()),["targetToSourceMatrix"]=JsonSerializer.SerializeToNode(target.ToArray()),
            ["confidence"]=.85,["runtimeVerified"]=allowed,["sourceFloorTransformVerified"]=true,["currentFrameDisplayValidated"]=false,
            ["directProjectionAllowed"]=allowed,["zeroExplorationDisplayAllowed"]=allowed,["profileMappingVerified"]=allowed,
            ["playerReviewedProjectionTrial"]=allowed,["requiresStableTargetFloor"]=true,["projectionOnlyUntilTargetStructureVerified"]=true,
            ["boundedCrossFloorOnly"]=!allowed,["profileCurrentFrameSearchSeed"]=true,["mappingProfileId"]=PackId,
            ["matrixCoordinatePackId"]=PackId,["matrixCoordinateProfileId"]=PackId,["reviewStatus"]=review,
            ["candidateId"]=J.Get(row,"candidateId") is {} candidate?JsonNode.Parse(candidate.GetRawText()):null,
            ["transferSource"]=allowed?"offline-player-reviewed-coordinate-binding-trial":"offline-profile-reviewed-current-frame-search-seed",
            ["evidence"]=evidence is {} saved?JsonNode.Parse(saved.GetRawText()):new JsonObject() };
        static bool Contains(JsonElement? value,string? wanted)=>value is {ValueKind:JsonValueKind.Array} list
            &&list.EnumerateArray().Any(item=>item.ValueKind==JsonValueKind.String&&item.GetString()==wanted);
        static bool Truthy(JsonElement? value)=>value is {} item&&item.ValueKind switch {
            JsonValueKind.Null or JsonValueKind.Undefined or JsonValueKind.False=>false,
            JsonValueKind.String=>!string.IsNullOrEmpty(item.GetString()),JsonValueKind.Array=>item.GetArrayLength()>0,
            JsonValueKind.Object=>item.EnumerateObject().Any(),JsonValueKind.Number=>item.GetDouble()!=0,_=>true };
    }

    public bool TryProfileToCanonical(string profile, string map, string floor, out DisplayAffine matrix, out string reason)
    {
        matrix = default;
        reason = "author-coordinate-row-unavailable";
        var row = Floor(profile,map,floor);
        if (row is null || !J.True(row,"usableForCurrentFrameRendering") || !J.False(row,"crossFloorProjectionAllowed")) return false;
        var evidence = J.Get(row,"evidence");
        string method = J.String(evidence,"method") ?? "";
        bool native = J.Get(evidence,"type") is { ValueKind: not JsonValueKind.Null }
            || J.True(evidence,"nativeStaticRegistration") || method.StartsWith("native-",StringComparison.Ordinal);
        if (!DisplayAffine.TryRead(J.Get(row, Schema == LegacySchema ? "matrixProfileToRecognition" : "matrixProfileToCanonical"),out matrix))
        { reason = "author-coordinate-matrix-invalid"; return false; }
        if (native)
        {
            reason = "native-static-corner-context-unavailable";
            if (Schema == LegacySchema || J.String(evidence,"type") != "native-static-corner-registration" || nativeValidator is null) return false;
            var result = nativeValidator(profile,map,floor,evidence!.Value,matrix);
            reason = result.Accepted ? "accepted" : string.Join(";",result.Reasons);
            return result.Accepted;
        }
        if (Schema == CanonicalSchema && J.True(evidence,"algebraicallyExact")) { reason = "accepted"; return true; }
        bool processed = method is "processed-author-direct-axis-locked-current-frame-required"
            or "processed-author-axis-locked-via-lianghapi-current-frame-required"
            or "processed-author-axis-locked-via-zhan10-speed-current-frame-required";
        var coverage = J.Get(evidence,"supportCoverage");
        var residual = J.Get(evidence,"residual");
        var summary = J.Get(evidence,"matrixSummary");
        int inliers = processed ? 8 : floor == "B1" ? 40 : 100;
        int cells = processed ? 2 : floor == "B1" ? 4 : 6;
        double span = processed ? .12 : 0;
        bool accepted = J.Number(evidence,"inlierMatches",0) >= inliers
            && J.Number(coverage,"coveredQuadrants",0) >= (processed ? 2 : 3)
            && J.Number(coverage,"coveredGridCells4x4",0) >= cells
            && J.Number(coverage,"spanWidthRatio",0) >= span && J.Number(coverage,"spanHeightRatio",0) >= span
            && J.Number(residual,"p90Pixels",999) <= 20.5
            && Math.Abs(J.Number(summary,"rotationDegrees",999)) <= 1e-8
            && Math.Abs(J.Number(summary,"shearDegrees",999)) <= 1e-8
            && J.Number(summary,"anisotropy",999) <= 1.13
            && (!processed || J.True(evidence,"currentFrameSelectedRouteResidualRequired"));
        reason = accepted ? "accepted" : "author-coordinate-evidence-rejected";
        return accepted;
    }
}

internal static class J
{
    internal static JsonElement? Get(JsonElement? e,string key) => e is { ValueKind: JsonValueKind.Object } value && value.TryGetProperty(key,out var p) ? p : null;
    internal static string? String(JsonElement? e,string key) => Get(e,key) is { ValueKind: JsonValueKind.String } p ? p.GetString() : null;
    internal static bool True(JsonElement? e,string key) => Get(e,key) is { ValueKind: JsonValueKind.True };
    internal static bool False(JsonElement? e,string key) => Get(e,key) is { ValueKind: JsonValueKind.False };
    internal static double Number(JsonElement? e,string key,double fallback = double.NaN) => Get(e,key) is { ValueKind: JsonValueKind.Number } p && p.TryGetDouble(out var n) ? n : fallback;
    internal static bool Equal(JsonElement? a,JsonElement? b) => a is { } av && b is { } bv && JsonElement.DeepEquals(av,bv);
    internal static int[] Ints(JsonElement? e) => e is { ValueKind: JsonValueKind.Array } p
        ? p.EnumerateArray().Select(v => v.ValueKind == JsonValueKind.Number && v.TryGetInt32(out var n) ? n : -1).ToArray() : [];
}
