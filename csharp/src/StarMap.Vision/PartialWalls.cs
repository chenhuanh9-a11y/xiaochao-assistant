using OpenCvSharp;
using System.Text.Json.Nodes;

namespace StarMap.Vision;

/// <summary>Held-out wall geometry for the source channels selected by fast structure alignment.</summary>
public static class PartialWalls
{
    public sealed record Plane(int Axis, int Sign, double QueryNormal, double SourceNormal,
        double Delta, double[] ObservedSpan, double[] SourceSpan)
    {
        public JsonObject Json() => new()
        {
            ["axis"] = Axis, ["sign"] = Sign, ["queryNormal"] = QueryNormal, ["sourceNormal"] = SourceNormal,
            ["delta"] = Delta, ["observedSpan"] = new JsonArray(ObservedSpan[0], ObservedSpan[1]),
            ["sourceSpan"] = new JsonArray(SourceSpan[0], SourceSpan[1])
        };
    }
    private static JsonArray Planes(IEnumerable<Plane> planes) => new(planes.Select(p => (JsonNode)p.Json()).ToArray());
    private static JsonArray Values(IEnumerable<double> values) => new(values.Select(v => (JsonNode)JsonValue.Create(v)!).ToArray());
    private static double Percentile(double[] values, double fraction)
    {
        var ordered = values.Order().ToArray(); double at = (ordered.Length - 1) * fraction;
        int lo = (int)Math.Floor(at), hi = (int)Math.Ceiling(at);
        return ordered[lo] + (ordered[hi] - ordered[lo]) * (at - lo);
    }
    private static JsonObject Statistics(double[] values, bool count = true)
    {
        if (values.Length == 0) throw new InvalidOperationException("Wall evidence is empty");
        var result = new JsonObject { ["medianPixels"] = Numerics.Median(values),
            ["p90Pixels"] = Percentile(values, .9), ["maxPixels"] = values.Max() };
        if (count) result["count"] = values.Length;
        return result;
    }
    private static double Number(JsonObject obj, string key) => Numerics.Scalar(obj[key]!);
    private static bool Success(JsonObject obj) => obj["success"]?.GetValue<bool>() == true;
    private static bool Valid(JsonObject stats) => Number(stats, "medianPixels") <= 3 && Number(stats, "p90Pixels") <= 8 && Number(stats, "maxPixels") <= 20;
    private static JsonObject Failure(string reason) => new() { ["success"] = false, ["reason"] = reason };
    private static double Spread(IEnumerable<Plane> planes) => planes.Max(p => p.QueryNormal) - planes.Min(p => p.QueryNormal);

    public static JsonObject FitPartialWallTranslation(IReadOnlyList<Plane> groups)
    {
        var correction = new List<double>(); var heldout = new List<double>(); var partitions = new JsonArray();
        for (int axis = 0; axis < 2; axis++)
        {
            var planes = groups.Where(p => p.Axis == axis).OrderBy(p => p.QueryNormal).ToArray();
            var train = planes.Where((_, i) => i % 2 == 0).ToArray();
            var validate = planes.Where((_, i) => i % 2 == 1).ToArray();
            if (Math.Min(train.Length, validate.Length) < 2) return Failure("wall-refinement-disjoint-support-insufficient");
            if (Math.Min(Spread(train), Spread(validate)) < 24) return Failure("wall-refinement-disjoint-spread-insufficient");
            double shift = Numerics.Median(train.Select(p => p.Delta));
            correction.Add(shift); heldout.AddRange(validate.Select(p => Math.Abs(p.Delta - shift)));
            partitions.Add(new JsonObject { ["axis"] = axis, ["support"] = Values(train.Select(p => p.QueryNormal)), ["holdout"] = Values(validate.Select(p => p.QueryNormal)) });
        }
        var validation = Statistics(heldout.ToArray());
        return new JsonObject { ["success"] = Valid(validation), ["correction"] = Values(correction), ["partitions"] = partitions, ["heldout"] = validation };
    }

    // This optional helper is not invoked by Fit when the original call has
    // no _source_lines. Supplying native lines must never silently change the
    // actual no-known-channel fast fallback into a different estimator.
    public static JsonObject FitPartialWallSimilarity(IReadOnlyList<Plane> groups, IReadOnlyList<Wall> nativeLines,
        double[][] target, bool requireHoldout = true)
    {
        double scale = target[0][0];
        bool Close(double x, double expected) => Math.Abs(x - expected) <= 1e-8 + 1e-5 * Math.Abs(expected);
        if (scale <= 0 || !Close(target[0][1], 0) || !Close(target[1][0], 0) || !Close(target[1][1], scale)) return Failure("wall-scale-axis-model-unavailable");
        var planes = new List<Plane>();
        foreach (var group in groups)
        {
            int axis = group.Axis, tangent = 1 - axis;
            var matches = new List<double>();
            foreach (var wall in nativeLines)
            {
                if (wall.Axis != axis || wall.Sign != group.Sign) continue;
                double normal = wall.Normal * target[axis][axis] + target[axis][2];
                double lo = wall.Lo * target[tangent][tangent] + target[tangent][2], hi = wall.Hi * target[tangent][tangent] + target[tangent][2];
                if (Math.Abs(normal - group.SourceNormal) <= 3 && Math.Min(hi, group.SourceSpan[1]) - Math.Max(lo, group.SourceSpan[0]) >= 12) matches.Add(normal);
            }
            if (matches.Count == 1) planes.Add(group with { SourceNormal = matches[0], Delta = group.QueryNormal - matches[0] });
        }
        var train = new List<Plane>(); var hold = new List<Plane>();
        for (int axis = 0; axis < 2; axis++)
        {
            var ordered = planes.Where(p => p.Axis == axis).OrderBy(p => p.QueryNormal).ToArray();
            var support = requireHoldout ? ordered.Where((_, i) => i % 2 == 0).ToArray() : ordered;
            var validate = requireHoldout ? ordered.Where((_, i) => i % 2 == 1).ToArray() : System.Array.Empty<Plane>();
            if (support.Length < 2 || requireHoldout && validate.Length < 2) return Failure("wall-scale-disjoint-support-insufficient");
            if (Spread(support) < 24 || requireHoldout && Spread(validate) < 24) return Failure("wall-scale-disjoint-spread-insufficient");
            train.AddRange(support); hold.AddRange(validate);
        }
        double[] center = { Numerics.Median(train.Where(p => p.Axis == 0).Select(p => p.SourceNormal)), Numerics.Median(train.Where(p => p.Axis == 1).Select(p => p.SourceNormal)) };
        using var design = new Mat(train.Count, 3, MatType.CV_64FC1); using var delta = new Mat(train.Count, 1, MatType.CV_64FC1);
        for (int i = 0; i < train.Count; i++)
        {
            var p = train[i]; design.Set(i, 0, p.SourceNormal - center[p.Axis]); design.Set(i, 1, p.Axis == 0 ? 1.0 : 0.0); design.Set(i, 2, p.Axis == 1 ? 1.0 : 0.0); delta.Set(i, 0, p.Delta);
        }
        using var solution = new Mat(); Cv2.Solve(design, delta, solution, DecompTypes.SVD);
        double[] beta = Enumerable.Range(0, 3).Select(i => solution.At<double>(i, 0)).ToArray();
        using var singular = new Mat(); using var u = new Mat(); using var vt = new Mat(); Cv2.SVDecomp(design, singular, u, vt, SVD.Flags.NoUV);
        double largest = singular.At<double>(0, 0), tolerance = largest * 2.220446049250313e-16 * Math.Max(train.Count, 3);
        int rank = Enumerable.Range(0, (int)singular.Total()).Count(i => singular.At<double>(i, 0) > tolerance);
        var evaluation = requireHoldout ? hold : train;
        var errors = evaluation.Select(p => Math.Abs(p.Delta - ((p.SourceNormal - center[p.Axis]) * beta[0] + beta[1 + p.Axis]))).ToArray();
        var validation = Statistics(errors);
        bool accepted = rank == 3 && beta.All(double.IsFinite) && Math.Abs(beta[0]) <= .08 && Valid(validation);
        var refined = target.Select(r => r.ToArray()).ToArray();
        for (int axis = 0; axis < 2; axis++)
        {
            for (int j = 0; j < 2; j++) refined[axis][j] *= 1 + beta[0];
            refined[axis][2] = (1 + beta[0]) * target[axis][2] + beta[axis + 1] - beta[0] * center[axis];
        }
        var result = new JsonObject { ["success"] = accepted, ["reason"] = "wall-native-uniform-scale",
            ["matrix"] = Matrix(refined), ["correction"] = Values(beta), ["heldout"] = requireHoldout ? validation : null,
            ["support"] = Planes(train), ["holdout"] = Planes(hold) };
        if (!requireHoldout) result["fittingResiduals"] = validation;
        return result;
    }

    private static JsonArray Matrix(double[][] matrix) => new(matrix.Select(row => (JsonNode)Values(row)).ToArray());
    private static void Merge(JsonObject destination, JsonObject source) { foreach (var value in source) destination[value.Key] = value.Value?.DeepClone(); }

    public static JsonObject Fit(Mat frame, StructureGeometry reference, double[][] target, Observation context,
        bool allowScaleRecovery = true, IReadOnlyList<Wall>? matchingSourceLines = null)
    {
        context.RequireFrame(frame);
        bool fitOnly = reference.KnownBoundary is not null;
        var qlines = Geometry.Lines(frame, context);
        using var projected = Geometry.Warp(reference.Clean, target, frame.Size());
        var native = reference.KnownBoundary is null ? null :
            matchingSourceLines ?? reference.NativeWalls ?? ExplicitWallSegments(reference.Clean, reference.KnownBoundary);
        var slines = native is null ? Geometry.VisibleWalls(projected) :
            native.Select(line => line with {
                Normal = line.Normal * target[line.Axis][line.Axis] + target[line.Axis][2],
                Lo = line.Lo * target[1-line.Axis][1-line.Axis] + target[1-line.Axis][2],
                Hi = line.Hi * target[1-line.Axis][1-line.Axis] + target[1-line.Axis][2] }).ToList();
        var groups = new List<Plane>();
        foreach (var q in qlines)
        {
            var compatible = slines.Where(s => s.Axis == q.Axis && s.Sign == q.Sign &&
                (fitOnly
                    ? Math.Min(q.Hi, s.Hi) - Math.Max(q.Lo, s.Lo) + 1 >= Math.Min(12, Math.Min(q.Hi-q.Lo-1, s.Hi-s.Lo-1))
                    : Math.Min(q.Hi, s.Hi) - Math.Max(q.Lo, s.Lo) >= Math.Min(12, q.Hi-q.Lo-1))
                && Math.Abs(q.Normal - s.Normal) <= 48).ToArray();
            if (compatible.Length == 0) continue;
            var source = compatible.OrderBy(s => Math.Abs(q.Normal - s.Normal)).First();
            var item = new Plane(q.Axis, q.Sign, q.Normal, source.Normal, q.Normal - source.Normal, new[] { q.Lo, q.Hi }, new[] { source.Lo, source.Hi });
            if (groups.Any(g => g.Axis == item.Axis && g.Sign == item.Sign && Math.Abs(g.QueryNormal - item.QueryNormal) <= 3)) continue;
            groups.Add(item);
        }
        var diagnostics = new JsonObject { ["querySegments"] = qlines.Count, ["sourceSegments"] = slines.Count, ["planes"] = Planes(groups) };
        JsonObject Rejected(string reason) => new() { ["success"] = false, ["reason"] = reason, ["diagnostics"] = diagnostics };
        var errors = new List<double>();
        for (int axis = 0; axis < 2; axis++)
        {
            var planes = groups.Where(p => p.Axis == axis).ToArray();
            if (planes.Length < 2 || Spread(planes) < 24) return Rejected("partial-wall-independent-planes-insufficient");
            for (int i = 0; i < planes.Length; i++) errors.Add(Math.Abs(planes[i].Delta - Numerics.Median(planes.Where((_, j) => j != i).Select(p => p.Delta))));
        }
        var validation = Statistics(errors.ToArray()); diagnostics["heldOutNormals"] = validation;
        var absolute = Statistics(groups.Select(p => Math.Abs(p.Delta)).ToArray(), false); diagnostics["candidateNormals"] = absolute;
        if (fitOnly && allowScaleRecovery && (!Valid(validation) || !Valid(absolute)))
        {
            var similarity = FitPartialWallSimilarity(groups, native!, target, requireHoldout: false);
            diagnostics["scaleRefinement"] = similarity;
            if (Success(similarity))
            {
                diagnostics["validationPolicy"] = "wall-similarity-then-author";
                return new() { ["success"] = true, ["reason"] = "partial-wall-similarity-speed-first",
                    ["matrix"] = similarity["matrix"]!.DeepClone(), ["diagnostics"] = diagnostics };
            }
        }
        if (!Valid(validation)) return Rejected("partial-wall-held-out-failed");
        if (!Valid(absolute)) return Rejected("partial-wall-candidate-failed");
        var refinement = FitPartialWallTranslation(groups); diagnostics["refinement"] = refinement;
        if (fitOnly)
        {
            var fitted = target.Select(row => row.ToArray()).ToArray();
            if (Success(refinement))
            {
                fitted[0][2] += Numerics.Scalar(refinement["correction"]![0]!);
                fitted[1][2] += Numerics.Scalar(refinement["correction"]![1]!);
            }
            diagnostics["validationPolicy"] = "wall-fit-then-author";
            return new() { ["success"] = true, ["reason"] = "partial-wall-fit-speed-first",
                ["matrix"] = Matrix(fitted), ["diagnostics"] = diagnostics };
        }
        Merge(diagnostics, RegionalGeometry.Evaluate(frame, context, projected));
        if (Success(refinement))
        {
            var refined = target.Select(r => r.ToArray()).ToArray();
            refined[0][2] += Numerics.Scalar(refinement["correction"]![0]!); refined[1][2] += Numerics.Scalar(refinement["correction"]![1]!);
            using var refinedProjection = Geometry.Warp(reference.Clean, refined, frame.Size());
            diagnostics["beforeRefinementRegions"] = diagnostics["regions"]?.DeepClone();
            Merge(diagnostics, RegionalGeometry.Evaluate(frame, context, refinedProjection));
            refinement["translation"] = new JsonArray(refined[0][2], refined[1][2]);
            if (diagnostics["regionalEdgeVerified"]?.GetValue<bool>() == true) target = refined;
        }
        return new JsonObject { ["success"] = diagnostics["regionalEdgeVerified"]?.GetValue<bool>() == true,
            ["reason"] = "partial-wall-fixed-linear", ["matrix"] = Matrix(target), ["diagnostics"] = diagnostics };
    }

    public static List<Wall> ExplicitWallSegments(Mat clean, Mat known) => ExplicitWallSegments(clean, known, known);

    public static List<Wall> ExplicitWallSegments(Mat clean, Mat known, Mat observed)
    {
        var lines = Geometry.VisibleWalls(clean, observed);
        using var divided = clean.Clone();
        divided.SetTo(Scalar.All(0), known);
        using var gx = new Mat();
        using var gy = new Mat();
        FrameResidual.Gradients(divided, gx, gy);
        var x = Geometry.Floats(gx);
        var y = Geometry.Floats(gy);
        var positive = Geometry.Bytes(observed);
        for (int direction = 0; direction < 4; direction++)
        {
            int axis = direction < 2 ? 0 : 1;
            int sign = direction is 0 or 3 ? 1 : -1;
            var pixels = new byte[x.Length];
            for (int i = 0; i < pixels.Length; i++)
                if (positive[i] > 0 && FrameResidual.HasNormal(x[i], y[i], direction)) pixels[i] = 1;
            using var field = Geometry.FromBytes(clean.Cols, clean.Rows, pixels);
            using var labels = new Mat();
            using var stats = new Mat();
            using var centers = new Mat();
            int count = Cv2.ConnectedComponentsWithStats(field, labels, stats, centers, PixelConnectivity.Connectivity8);
            for (int index = 1; index < count; index++)
            {
                int left = stats.At<int>(index,0), top = stats.At<int>(index,1);
                int w = stats.At<int>(index,2), h = stats.At<int>(index,3);
                if (stats.At<int>(index,4) < 12 || (axis == 0 ? w : h) > 4 || (axis == 0 ? h : w) < 12) continue;
                var normals = new List<double>();
                for (int row = top; row < top+h; row++)
                for (int col = left; col < left+w; col++)
                    if (labels.At<int>(row,col) == index) normals.Add(axis == 0 ? col : row);
                double normal = Numerics.Median(normals);
                int lo = axis == 0 ? top : left, hi = lo + (axis == 0 ? h : w) - 1;
                if (!lines.Any(old => old.Axis == axis && old.Sign == sign && Math.Abs(old.Normal-normal) <= 3
                    && Math.Min(old.Hi,hi) >= Math.Max(old.Lo,lo))) lines.Add(new(axis,sign,normal,lo,hi));
            }
        }
        return lines;
    }
}
