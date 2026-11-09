using System.Text.Json.Nodes;
using OpenCvSharp;

namespace StarMap.Vision;

/// <summary>Detached shared/native target-floor inputs. A relation is not target-floor verification.</summary>
public sealed record EstablishedGroupContext(string MapId, string Floor, string RouteProfileId,
    double[][] RecognitionMatrixSourceToClient, IReadOnlySet<string> VerifiedFloors,
    IReadOnlySet<string> CanonicalGeometryVerifiedFloors, IReadOnlySet<string> RelationFloors,
    bool PoseIndexCandidatesEnabled = false);

/// <summary>Providers operate on this call's frame/snapshot and must include surface/session invalidation in Cancelled.</summary>
public sealed record EstablishedProviderContext(Mat Frame, StructureGeometry Reference,
    EstablishedGroupContext Group, Observation Observation, Func<bool> Cancelled);
public sealed record EstablishedEntryRecovery(IReadOnlyList<JsonObject> Rows, JsonObject Diagnostics);
public sealed record EstablishedPoseIndexCandidates(IReadOnlyList<double[][]> Matrices, JsonObject Diagnostics);
public sealed record EstablishedGroupProviders(
    Func<EstablishedProviderContext, EstablishedEntryRecovery>? EntryScaleRecovery = null,
    Func<EstablishedProviderContext, EstablishedPoseIndexCandidates>? PoseIndexCandidates = null);
public sealed record EstablishedGroupResult(JsonObject Fit, JsonObject Diagnostics);

/// <summary>
/// Numerical producer for single_map_structure.solve in the real Qt shared implicit domain.
/// It never commits group state, grants display authority, or uses previous frame pixels.
/// </summary>
public static class EstablishedGroupFit
{
    private static readonly string[] PointKeys = ["reason", "graph", "pairs", "acceptedPointCount",
        "supportPointCount", "validationPointCount", "h0Validation", "h1Validation", "structureModel", "scaleObservable", "scaleFactor"];

    public static EstablishedGroupResult Evaluate(Mat frame, StructureGeometry reference, EstablishedGroupContext group,
        Func<double[][], JsonObject> canonicalResidual, EstablishedGroupProviders? providers = null,
        Func<bool>? cancelled = null)
    {
        if (frame.Empty() || frame.Type() != MatType.CV_8UC3)
            throw new ArgumentException("Expected the complete BGR8 client frame.", nameof(frame));
        ArgumentNullException.ThrowIfNull(canonicalResidual);
        // No caller-owned matrix/collection is read after this snapshot.
        group = group with {
            RecognitionMatrixSourceToClient = Clone(group.RecognitionMatrixSourceToClient),
            VerifiedFloors = group.VerifiedFloors.ToHashSet(StringComparer.Ordinal),
            CanonicalGeometryVerifiedFloors = group.CanonicalGeometryVerifiedFloors.ToHashSet(StringComparer.Ordinal),
            RelationFloors = group.RelationFloors.ToHashSet(StringComparer.Ordinal)
        };
        var diagnostics = new JsonObject { ["searchStages"] = new JsonArray() };
        bool Obsolete() => cancelled?.Invoke() == true;
        EstablishedGroupResult Done(JsonObject fit) => new(fit, diagnostics);
        EstablishedGroupResult Cancelled() => Done(Failure("structure-work-obsolete"));
        if (Obsolete()) return Cancelled();
        if (group.VerifiedFloors.Count == 0) return Done(Failure("structure-no-reliable-scale"));
        var basis = group.RecognitionMatrixSourceToClient;
        if (!MatrixShape(basis)) return Done(Failure("structure-no-reliable-scale"));
        using var current = Geometry.Current(frame);
        if (current is null) return Done(new() { ["success"] = false, ["reason"] = "structure-query-unavailable",
            ["sharedProjectionGeometryInsufficient"] = true });
        bool translationOnly = group.VerifiedFloors.Contains(group.Floor);
        var providerContext = new EstablishedProviderContext(frame, reference, group, current, Obsolete);
        double queryScale = Math.Min(1, 640.0 / frame.Cols);
        using var resized = new Mat();
        if (queryScale < 1)
            Cv2.Resize(current.Query, resized, new Size(Math.Max(1, Numerics.Rint(frame.Cols * queryScale)),
                Math.Max(1, Numerics.Rint(frame.Rows * queryScale))), interpolation: InterpolationFlags.Area);
        else current.Query.CopyTo(resized);
        using var query = new Mat();
        Cv2.Threshold(resized, query, .22, 1, ThresholdTypes.Binary);
        var candidates = new List<JsonObject>();
        var accepted = new List<(double[][] Matrix, JsonObject Candidate)>();
        var matchingLines = MatchingNativeWalls(reference);

        void Validate(JsonObject candidate)
        {
            var target = candidate["seedMatrix"] is JsonArray seed ? Numerics.Matrix(seed) : Clone(basis);
            if (!IsotropicSeed(target)) { candidate["validationFailure"] = "invalid-isotropic-seed"; return; }
            target[0][2] = Number(candidate["translation"]?[0]); target[1][2] = Number(candidate["translation"]?[1]);
            candidate["refinedTranslation"] = Translation(target);
            var geometry = RegionalGeometry.GraphPoints(frame, reference, target, current, !translationOnly,
                useUnknownMask: reference.KnownBoundary is not null);
            string? model = geometry["structureModel"]?.ToString();
            var selected = model is not null ? geometry[model] as JsonObject : null;
            var point = new JsonObject(); foreach (string key in PointKeys) point[key] = geometry[key]?.DeepClone();
            candidate["pointGeometry"] = point;
            if (Success(selected))
            {
                target = Numerics.Matrix(selected!["matrixSourceToClient"]!.AsArray());
                candidate["refinedTranslation"] = Translation(target);
            }
            var pointTarget = Clone(target);
            bool verified = Success(selected) && Flag(geometry["graph"], "regionalEdgeVerified");
            int support = (int)Number(geometry["acceptedPointCount"], 0);
            var partial = PartialWalls.Fit(frame, reference, target, current, !translationOnly, matchingLines);
            candidate["partialWallGeometry"] = Without(partial, "matrix");
            if (reference.KnownBoundary is not null) verified = Success(partial);
            var holdout = partial["diagnostics"]?["heldOutNormals"];
            var absolute = partial["diagnostics"]?["candidateNormals"];
            string? wallReason = partial["reason"]?.ToString();
            bool wallAuthorRecovery = translationOnly && reference.KnownBoundary is not null &&
                (wallReason == "partial-wall-held-out-failed" && Number(holdout?["medianPixels"]) <= 3 && Number(holdout?["p90Pixels"]) <= 8 ||
                 wallReason == "partial-wall-candidate-failed" && Number(holdout?["maxPixels"]) <= 8 && Number(absolute?["maxPixels"]) <= 8);
            if (Success(partial))
            {
                target = Numerics.Matrix(partial["matrix"]!.AsArray()); candidate["refinedTranslation"] = Translation(target);
                verified = true; support = partial["diagnostics"]?["planes"]?.AsArray().Count ?? 0;
            }
            var residual = verified || wallAuthorRecovery ? canonicalResidual(Clone(target)).DeepClone().AsObject() :
                new JsonObject { ["verified"] = false, ["reason"] = "not-evaluated-geometry-failed" };
            candidate["residual"] = residual;
            if (wallAuthorRecovery && EstablishedScaleObservation.ResidualVerified(residual))
            {
                verified = true;
                candidate["wallAuthorRecovery"] = new JsonObject { ["reason"] = wallReason,
                    ["heldOut"] = holdout?.DeepClone(), ["candidateNormals"] = absolute?.DeepClone() };
            }
            if (!verified || !EstablishedScaleObservation.ResidualVerified(residual)) return;
            candidate["support"] = Math.Max((int)Number(candidate["support"], 0), support);
            candidate["currentFrameScaleObservation"] = EstablishedScaleObservation.Compose(geometry, pointTarget, target,
                residual, group.RouteProfileId, partial);
            if (!accepted.Any(other => MaximumExtentDisplacement(target, other.Matrix, reference.Clean.Size()) < 24))
                accepted.Add((Clone(target), candidate));
        }

        try
        {
            // Qt _AlignmentHost has no verifiedFrame candidate. Its live reachable local branch uses only the committed native pose.
            if (translationOnly || ((group.Floor is "1" or "2") &&
                group.CanonicalGeometryVerifiedFloors.Any(f => f is "1" or "2") && group.RelationFloors.Contains(group.Floor)))
            {
                if (Obsolete()) return Cancelled();
                var local = Candidate(new(basis[0][2], basis[1][2]), 0, 1,
                    translationOnly ? "committed-pose-local" : "shared-root-local");
                candidates.Add(local); Validate(local);
                diagnostics["searchStages"]!.AsArray().Add(new JsonObject { ["stage"] = "local", ["accepted"] = accepted.Count });
            }
            if (accepted.Count == 0 && !translationOnly && group.Floor == "1")
            {
                if (Obsolete()) return Cancelled();
                EstablishedEntryRecovery recovery;
                if (providers?.EntryScaleRecovery is null)
                    recovery = new([], new() { ["reason"] = "entry-scale-recovery-provider-unavailable", ["available"] = false });
                else
                {
                    try { recovery = providers.EntryScaleRecovery(providerContext); }
                    catch (Exception error) when (ProviderFailure(error))
                    { recovery = new([], new() { ["reason"] = "entry-resource-unavailable", ["error"] = error.Message }); }
                }
                if (Obsolete()) return Cancelled();
                diagnostics["entryRecovery"] = recovery.Diagnostics.DeepClone();
                var selected = recovery.Rows.Where(row => Flag(row, "poseVerified")).ToArray();
                if (selected.Length == 1)
                {
                    var check = selected[0]["verification"]!.DeepClone().AsObject();
                    var matrix = Numerics.Matrix(check["matrixSourceToClient"]!.AsArray());
                    var residual = canonicalResidual(Clone(matrix));
                    if (Obsolete()) return Cancelled();
                    if (!EstablishedScaleObservation.ResidualVerified(residual)) return Done(Failure("structure-current-geometry-failed"));
                    check["residual"] = residual.DeepClone();
                    return Done(new() { ["success"] = true, ["reason"] = "difficult-entry-scale-relocalization",
                        ["matrixSourceToClient"] = Matrix(matrix), ["coordinateSpace"] = "shared-structure-to-client",
                        ["routeProfileId"] = "shared-structure", ["selectedRouteCurrentFrameResidual"] = SharedResidual(residual),
                        ["difficultEntryValidation"] = check });
                }
            }
            if (accepted.Count == 0 && group.PoseIndexCandidatesEnabled)
            {
                if (Obsolete()) return Cancelled();
                EstablishedPoseIndexCandidates indexed;
                if (providers?.PoseIndexCandidates is null)
                    indexed = new([], new() { ["reason"] = "pose-index-provider-unavailable", ["available"] = false });
                else
                {
                    try { indexed = providers.PoseIndexCandidates(providerContext); }
                    catch (Exception error) when (ProviderFailure(error))
                    { indexed = new([], new() { ["reason"] = "pose-index-error", ["error"] = error.Message }); }
                }
                diagnostics["poseIndexCandidates"] = indexed.Diagnostics.DeepClone();
                double count = Number(indexed.Diagnostics["candidateCount"], indexed.Matrices.Count);
                if (count == 0) count = indexed.Matrices.Count;
                for (int i = 0; i < indexed.Matrices.Count; i++)
                {
                    if (Obsolete()) return Cancelled();
                    var matrix = Clone(indexed.Matrices[i]);
                    var candidate = Candidate(new(matrix[0][2], matrix[1][2]), 0, count - i, "indexed-structure-pose");
                    candidate["seedMatrix"] = Matrix(matrix); candidates.Add(candidate); Validate(candidate);
                    if (accepted.Count > 1) break;
                }
            }
            if (accepted.Count == 0)
            {
                if (Obsolete()) return Cancelled();
                var global = EstablishedRecall.Retrieve(reference.Clean, query, basis, queryScale, Obsolete);
                if (Obsolete()) return Cancelled();
                diagnostics["globalRecall"] = global.Diagnostics; candidates.AddRange(global.Candidates);
                foreach (var candidate in global.Candidates)
                {
                    if (Obsolete()) return Cancelled(); Validate(candidate); if (accepted.Count > 1) break;
                }
                if (accepted.Count == 0)
                {
                    if (Obsolete()) return Cancelled();
                    var native = EstablishedRecall.RetrieveNativeTurns(reference.Clean, frame, basis, current, Obsolete);
                    if (Obsolete()) return Cancelled();
                    diagnostics["nativeRecall"] = native.Diagnostics;
                    var extra = native.Candidates.Where(c => !global.Candidates.Any(old =>
                        Numerics.Norm(Position(c) - Position(old)) < 8)).ToArray();
                    candidates.AddRange(extra);
                    foreach (var candidate in extra)
                    {
                        if (Obsolete()) return Cancelled(); Validate(candidate); if (accepted.Count > 1) break;
                    }
                }
                diagnostics["searchStages"]!.AsArray().Add(new JsonObject { ["stage"] = "expanded", ["accepted"] = accepted.Count });
            }
            diagnostics["proposals"] = new JsonArray(candidates.Select(c => c.DeepClone()).ToArray());
            if (Obsolete()) return Cancelled();
            if (accepted.Count != 1)
            {
                var local = candidates.FirstOrDefault(c => c["origin"]?.ToString() is "committed-pose-local" or "shared-root-local");
                var lines = Geometry.Lines(frame, current);
                bool observable = Enumerable.Range(0, 2).All(axis => {
                    var values = lines.Where(line => line.Axis == axis).Select(line => line.Normal).ToArray();
                    return values.Length >= 2 && values.Max() - values.Min() >= 24;
                });
                bool insufficient = accepted.Count == 0 &&
                    local?["partialWallGeometry"]?["reason"]?.ToString() == "partial-wall-independent-planes-insufficient" &&
                    !observable && local?["pointGeometry"]?["reason"]?.ToString() == "graph-adjacent-pairs-insufficient";
                return Done(new() { ["success"] = false, ["reason"] = accepted.Count > 1 ? "structure-ambiguous" : "structure-current-geometry-failed",
                    ["sharedProjectionGeometryInsufficient"] = insufficient });
            }
            var (target, chosen) = accepted[0];
            return Done(new() { ["success"] = true, ["matrixSourceToClient"] = Matrix(target),
                ["coordinateSpace"] = "shared-structure-to-client", ["routeProfileId"] = "shared-structure",
                ["inliers"] = chosen["support"]?.DeepClone(), ["structureProposalOrigin"] = chosen["origin"]?.DeepClone(),
                ["structureModel"] = chosen["pointGeometry"]?["structureModel"]?.DeepClone(),
                ["currentFrameScaleObservation"] = chosen["currentFrameScaleObservation"]?.DeepClone(),
                ["selectedRouteCurrentFrameResidual"] = SharedResidual(chosen["residual"]!.AsObject()) });
        }
        catch (OperationCanceledException) { return Cancelled(); }
    }

    private static IReadOnlyList<Wall>? MatchingNativeWalls(StructureGeometry reference)
    {
        if (reference.KnownBoundary is not { } boundary) return null;
        // The real entry locator uses prebuilt native lines only with both measured channels.
        if (reference.Unknown is not { } unknown) return PartialWalls.ExplicitWallSegments(reference.Clean, boundary);
        if (reference.NativeWalls is { } native) return native;
        using var known = boundary.Clone(); using var kernel = Geometry.Kernel(3);
        Cv2.Dilate(known, known, kernel); known.SetTo(Scalar.All(0), unknown);
        return PartialWalls.ExplicitWallSegments(reference.Clean, boundary, known);
    }
    private static double MaximumExtentDisplacement(double[][] a, double[][] b, Size size) =>
        new[] { new Point2d(0, 0), new Point2d(size.Width, 0), new Point2d(0, size.Height), new Point2d(size.Width, size.Height) }
            .Max(p => Numerics.Norm(new((a[0][0] - b[0][0]) * p.X + (a[0][1] - b[0][1]) * p.Y + a[0][2] - b[0][2],
                (a[1][0] - b[1][0]) * p.X + (a[1][1] - b[1][1]) * p.Y + a[1][2] - b[1][2])));
    private static bool MatrixShape(double[][] matrix) => matrix.Length == 3 && matrix.All(row => row.Length == 3);
    private static bool IsotropicSeed(double[][] matrix) => MatrixShape(matrix) && matrix.SelectMany(row => row).All(double.IsFinite) &&
        Math.Abs(matrix[0][1]) <= 1e-8 && Math.Abs(matrix[1][0]) <= 1e-8 && matrix[0][0] > 0 && Math.Abs(matrix[0][0] - matrix[1][1]) <= 1e-8;
    private static bool ProviderFailure(Exception error) => error is ArgumentException or InvalidOperationException or KeyNotFoundException or IOException or OpenCVException or System.Text.Json.JsonException;
    private static JsonObject SharedResidual(JsonObject residual)
    {
        var result = residual.DeepClone().AsObject(); result["validationBasis"] = "shared-canonical-geometry-current-frame";
        result["poseVerifiedAgainstCanonicalGeometry"] = true; result["poseVerifiedAgainstSharedStructure"] = true;
        result["poseVerifiedAgainstRenderedProfile"] = false; return result;
    }
    internal static JsonObject Without(JsonObject source, string key)
    { var result = source.DeepClone().AsObject(); result.Remove(key); return result; }
    internal static JsonObject Candidate(Point2d translation, int support, double score, string? origin = null)
    {
        var result = new JsonObject { ["translation"] = new JsonArray(translation.X, translation.Y), ["support"] = support, ["score"] = score };
        if (origin is not null) result["origin"] = origin; return result;
    }
    internal static Point2d Position(JsonObject candidate) => new(Number(candidate["translation"]?[0]), Number(candidate["translation"]?[1]));
    internal static JsonArray Matrix(double[][] matrix) => new(matrix.Select(row => (JsonNode)new JsonArray(row.Select(v => (JsonNode)JsonValue.Create(v)!).ToArray())).ToArray());
    internal static double[][] Clone(double[][] matrix) => matrix.Select(row => row.ToArray()).ToArray();
    internal static JsonArray Translation(double[][] matrix) => new(matrix[0][2], matrix[1][2]);
    internal static double Number(JsonNode? node, double fallback = double.PositiveInfinity) => node is null ? fallback : Numerics.Scalar(node);
    internal static bool Flag(JsonNode? node, string key) => node?[key] is JsonValue value && value.TryGetValue<bool>(out bool flag) && flag;
    internal static bool Success(JsonNode? node) => Flag(node, "success");
    private static JsonObject Failure(string reason) => new() { ["success"] = false, ["reason"] = reason };
}
