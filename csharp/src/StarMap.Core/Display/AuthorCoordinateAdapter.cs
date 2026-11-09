using System.Text.Json;

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
