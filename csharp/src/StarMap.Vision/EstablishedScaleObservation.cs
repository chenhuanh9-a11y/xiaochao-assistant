using System.Text.Json.Nodes;
using static StarMap.Vision.EstablishedGroupFit;

namespace StarMap.Vision;

/// <summary>Selected point/wall scale proof, never inferred from a merely accepted pose.</summary>
public static class EstablishedScaleObservation
{
    public static JsonObject? Compose(JsonObject geometry, double[][] measuredMatrix, double[][] finalMatrix,
        JsonObject residual, string profile, JsonObject? wallGeometry = null)
    {
        if (!Valid(measuredMatrix) || !Valid(finalMatrix) || !Flag(residual, "verified")) return null;
        var measured = Clone(measuredMatrix); var final = Clone(finalMatrix); JsonObject? proof = null;
        var h1 = geometry["h1"] as JsonObject;
        var h1Matrix = ReadMatrix(h1?["matrixSourceToClient"]);
        if (Flag(geometry, "scaleObservable") && Success(h1) &&
            (geometry["structureModel"]?.ToString() == "h1" || geometry["mode"]?.ToString() == "scale-plus-translation") && Same(measured, h1Matrix))
            proof = new() { ["success"] = true, ["scaleObservable"] = true, ["basis"] = "disjoint-point-h1",
                ["validation"] = geometry["h1Validation"]?.DeepClone(), ["supportPointCount"] = geometry["supportPointCount"]?.DeepClone() };
        var initialWall = geometry["wallScale"];
        if (proof is null && Nonempty(initialWall?["heldOutNormals"]) && Flag(initialWall?["rasterValidation"], "verified"))
            proof = new() { ["success"] = true, ["scaleObservable"] = true, ["basis"] = "directed-wall-held-out",
                ["validation"] = initialWall!["heldOutNormals"]!.DeepClone(), ["supportPointCount"] = initialWall["planes"]?.AsArray().Count ?? 0 };
        var wallMatrix = ReadMatrix(wallGeometry?["matrix"]);
        if (!SameLinear(measured, final))
        {
            var similarity = wallGeometry?["diagnostics"]?["scaleRefinement"];
            var similarityMatrix = ReadMatrix(similarity?["matrix"]);
            if (!Success(wallGeometry) || !Success(similarity) || !Nonempty(similarity?["heldout"]) ||
                similarityMatrix is null || !SameLinear(final, similarityMatrix)) return null;
            measured = similarityMatrix;
            proof = new() { ["success"] = true, ["scaleObservable"] = true, ["basis"] = "wall-similarity-held-out",
                ["validation"] = similarity!["heldout"]!.DeepClone(), ["supportPointCount"] = similarity["support"]?.AsArray().Count ?? 0 };
        }
        if (proof is null || Number(proof["validation"]?["count"], 0) == 0) return null;
        if (!Same(measured, final) && (!Success(wallGeometry) || !Same(wallMatrix, final))) return null;
        return new() { ["routeProfileId"] = profile, ["geometry"] = proof,
            ["scaleMatrixSourceToClient"] = Matrix(measured), ["matrixSourceToClient"] = Matrix(final) };
    }

    /// <summary>The same three evidence forms accepted by rendered_profile_residual_is_verified.</summary>
    public static bool ResidualVerified(JsonObject? residual)
    {
        if (!Flag(residual, "verified")) return false;
        string? basis = residual?["validationBasis"]?.ToString();
        if (Flag(residual, "poseVerifiedAgainstRenderedProfile") && basis == "actual-rendered-profile-structural-mask") return true;
        if (Flag(residual, "poseVerifiedAgainstCanonicalGeometry") && Flag(residual, "profileRenderDerivedFromCanonical") &&
            basis == "shared-canonical-geometry-current-frame" && !string.IsNullOrEmpty(residual?["coordinatePackId"]?.ToString())) return true;
        if (!Flag(residual, "poseVerifiedAgainstSourceStructure") || basis != "source-structure-heldout-current-frame") return false;
        try
        {
            double median = Number(residual?["proposedTransformMedianError"]), p90 = Number(residual?["proposedTransformP90Error"]);
            return Flag(residual, "sourceCoordinatesVerified") && Flag(residual, "sourceFloorIsProducerLabelOnly") &&
                residual?["sourceCoordinateSpace"]?.ToString() == "producer-source-png-pixels" && Flag(residual, "sourcePngIsDisplayImage") &&
                !string.IsNullOrEmpty(residual?["sourceGroup"]?.ToString()) && (residual?["gameFloor"]?.ToString() is "1" or "2" or "B1") &&
                Number(residual?["heldOutInliers"], 0) >= 5 && Number(residual?["heldOutCellCount"], 0) >= 4 &&
                double.IsFinite(median) && double.IsFinite(p90) && median <= 4 && p90 <= 8;
        }
        catch (Exception error) when (error is InvalidDataException or InvalidOperationException) { return false; }
    }
    private static bool Nonempty(JsonNode? node) => node is JsonObject o ? o.Count > 0 : node is JsonArray a && a.Count > 0;
    private static double[][]? ReadMatrix(JsonNode? node) => node is JsonArray a && a.Count == 3 &&
        a.All(row => row is JsonArray { Count: 3 }) ? Numerics.Matrix(a) : null;
    private static bool Valid(double[][] m) => m.Length == 3 && m.All(row => row.Length == 3 && row.All(double.IsFinite));
    private static bool Same(double[][]? a, double[][]? b) => a is not null && b is not null &&
        Valid(a) && Valid(b) && Enumerable.Range(0, 3).All(i => Enumerable.Range(0, 3).All(j => Math.Abs(a[i][j] - b[i][j]) <= 1e-10));
    private static bool SameLinear(double[][] a, double[][] b) =>
        Enumerable.Range(0, 2).All(i => Enumerable.Range(0, 2).All(j => Math.Abs(a[i][j] - b[i][j]) <= 1e-10));
}
