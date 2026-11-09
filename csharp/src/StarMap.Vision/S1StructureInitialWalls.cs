using System.Text.Json.Nodes;
using OpenCvSharp;

namespace StarMap.Vision;

/// <summary>The initial solver's independent directed planes and exhaustive raster witness.</summary>
internal static class S1StructureInitialWalls
{
    internal sealed record Plane(int Axis, int Sign, double QueryNormal, double SourceNormal, int QueryEdge, int SourceEdge);
    internal sealed record WallFit(double[][] Matrix, Plane[] Planes, JsonObject HeldOut, JsonObject Raster)
    {
        public JsonObject Evidence() => new() { ["planes"] = new JsonArray(Planes.Select(p => (JsonNode)new JsonObject {
            ["axis"] = p.Axis, ["sign"] = p.Sign, ["queryNormal"] = p.QueryNormal, ["sourceNormal"] = p.SourceNormal,
            ["queryEdge"] = p.QueryEdge, ["sourceEdge"] = p.SourceEdge }).ToArray()), ["heldOutNormals"] = HeldOut.DeepClone(), ["rasterValidation"] = Raster.DeepClone() };
    }

    internal static WallFit? Fit(IReadOnlyList<Wall> query, IReadOnlyList<Wall> source, double scale, double[] translation,
        StructureGeometry reference, Dictionary<string, WallFit?> cache)
    {
        var choices = new int[query.Count]; Array.Fill(choices, -1);
        for (int qi = 0; qi < query.Count; qi++)
        {
            var q = query[qi]; double nearest = double.PositiveInfinity;
            double minimum = double.PositiveInfinity, maximum = double.NegativeInfinity;
            for (int si = 0; si < source.Count; si++)
            {
                var s = source[si]; if (q.Axis != s.Axis || q.Sign != s.Sign) continue;
                double distance = Distance(q, s, scale, translation);
                if (distance <= 3) { minimum = Math.Min(minimum, s.Normal); maximum = Math.Max(maximum, s.Normal); }
                if (distance < nearest) { nearest = distance; choices[qi] = si; }
            }
            if (nearest > 3) choices[qi] = -1;
            else if (maximum - minimum > 3) return null;
        }
        string identity = string.Join(',', choices);
        if (cache.TryGetValue(identity, out var prior)) return prior;
        cache[identity] = null;
        var groups = new List<Plane>();
        for (int i = 0; i < query.Count; i++)
        {
            if (choices[i] < 0) continue;
            var q = query[i]; var s = source[choices[i]];
            if (groups.Any(g => g.Axis == q.Axis && g.Sign == q.Sign && Math.Abs(g.QueryNormal - q.Normal) <= 3)) continue;
            if (groups.Any(g => g.Axis == q.Axis && g.Sign == q.Sign && Math.Abs(g.SourceNormal - s.Normal) <= 3)) return null;
            groups.Add(new(q.Axis, q.Sign, q.Normal, s.Normal, q.EdgeId, s.EdgeId));
        }
        for (int axis = 0; axis < 2; axis++)
        {
            var planes = groups.Where(g => g.Axis == axis).ToArray();
            if (planes.Length < 2 || planes.Max(g => g.QueryNormal) - planes.Min(g => g.QueryNormal) < 24) return null;
        }
        var model = LeastSquares(groups);
        if (model is null || model[0] <= 0) return null;
        var raster = Raster(query, reference.Clean, model, reference.Unknown);
        if (raster["verified"]!.GetValue<bool>() != true) return null;
        var errors = new List<double>();
        for (int i = 0; i < groups.Count; i++)
        {
            var held = LeastSquares(groups.Where((_, j) => j != i).ToArray());
            if (held is null || held[0] <= 0) return null;
            var p = groups[i]; errors.Add(Math.Abs(held[0] * p.SourceNormal + held[1 + p.Axis] - p.QueryNormal));
        }
        var statistics = Statistics(errors);
        if (statistics["medianPixels"]!.GetValue<double>() > 3 || statistics["p90Pixels"]!.GetValue<double>() > 8 || statistics["maxPixels"]!.GetValue<double>() > 20) return null;
        double[][] matrix = [[model[0], 0, model[1]], [0, model[0], model[2]], [0, 0, 1]];
        return cache[identity] = new(matrix, groups.ToArray(), statistics, raster);
    }

    private static double Distance(Wall q, Wall s, double scale, double[] translation)
    {
        int tangent = 1 - q.Axis;
        double overlap = Math.Min(q.Hi, scale*s.Hi+translation[tangent]) - Math.Max(q.Lo, scale*s.Lo+translation[tangent]);
        return overlap >= Math.Min(12, q.Hi-q.Lo-1) ? Math.Abs(scale*s.Normal+translation[q.Axis]-q.Normal) : double.PositiveInfinity;
    }

    private static double[]? LeastSquares(IReadOnlyList<Plane> groups)
    {
        if (groups.Count < 3) return null;
        using var design = new Mat(groups.Count, 3, MatType.CV_64FC1); using var observed = new Mat(groups.Count, 1, MatType.CV_64FC1);
        for (int i = 0; i < groups.Count; i++)
        {
            var g = groups[i]; design.Set(i, 0, g.SourceNormal); design.Set(i, 1, g.Axis == 0 ? 1d : 0d); design.Set(i, 2, g.Axis == 1 ? 1d : 0d); observed.Set(i, 0, g.QueryNormal);
        }
        using var singular = new Mat(); using var u = new Mat(); using var vt = new Mat(); Cv2.SVDecomp(design, singular, u, vt, SVD.Flags.NoUV);
        double largest = singular.At<double>(0, 0), tolerance = largest * 2.220446049250313e-16 * Math.Max(groups.Count, 3);
        if (Enumerable.Range(0, (int)singular.Total()).Count(i => singular.At<double>(i, 0) > tolerance) != 3) return null;
        using var solution = new Mat(); if (!Cv2.Solve(design, observed, solution, DecompTypes.SVD)) return null;
        var model = Enumerable.Range(0, 3).Select(i => solution.At<double>(i, 0)).ToArray();
        return model.All(double.IsFinite) ? model : null;
    }

    internal static JsonObject Statistics(IEnumerable<double> values)
    {
        var ordered = values.Order().ToArray();
        double Percentile(double fraction) { double at = (ordered.Length - 1)*fraction; int lo = (int)Math.Floor(at), hi = (int)Math.Ceiling(at); return ordered[lo]+(ordered[hi]-ordered[lo])*(at-lo); }
        return new() { ["count"] = ordered.Length, ["medianPixels"] = Numerics.Median(ordered), ["p90Pixels"] = Percentile(.9), ["maxPixels"] = ordered[^1] };
    }

    private sealed record Fragment(Point[] Points, int Direction);
    private static JsonObject Raster(IReadOnlyList<Wall> query, Mat reference, double[] model, Mat? sourceUnknown)
    {
        var fragments = new List<Fragment>();
        foreach (var q in query)
        {
            int lo = (int)Math.Ceiling(q.Lo), hi = (int)Math.Floor(q.Hi), normal = Numerics.Rint(q.Normal);
            var points = Enumerable.Range(lo, Math.Max(0, hi-lo+1)).Select(t => q.Axis == 0 ? new Point(normal, t) : new Point(t, normal)).ToArray();
            int direction = q.Axis == 0 ? (q.Sign > 0 ? 0 : 1) : (q.Sign > 0 ? 3 : 2);
            fragments.Add(new(points, direction));
        }
        var all = fragments.SelectMany(f => f.Points).ToArray();
        if (all.Length == 0) return new() { ["verified"] = false, ["reason"] = "no-observed-fragments" };
        int x0 = all.Min(p => p.X)-8, y0 = all.Min(p => p.Y)-8, x1 = all.Max(p => p.X)+9, y1 = all.Max(p => p.Y)+9;
        var size = new Size(x1-x0, y1-y0);
        double[][] matrix = [[model[0], 0, model[1]-x0], [0, model[0], model[2]-y0], [0, 0, 1]];
        using var projected = Geometry.Warp(reference, matrix, size);
        using var unknown = sourceUnknown is null ? new Mat(size, MatType.CV_8UC1, Scalar.All(0)) : Geometry.Warp(sourceUnknown, matrix, size);
        var excluded = Geometry.Bytes(unknown);
        using var gx = new Mat(); using var gy = new Mat(); FrameResidual.Gradients(projected, gx, gy);
        var x = Geometry.Floats(gx); var y = Geometry.Floats(gy);
        var distances = new float[4][];
        for (int direction = 0; direction < 4; direction++)
        {
            var inverse = new byte[x.Length];
            for (int i = 0; i < inverse.Length; i++) inverse[i] = excluded[i] == 0 && FrameResidual.HasNormal(x[i], y[i], direction) ? (byte)0 : (byte)1;
            using var raw = Geometry.FromBytes(size.Width, size.Height, inverse); using var field = new Mat();
            Cv2.DistanceTransform(raw, field, DistanceTypes.L2, DistanceTransformMasks.Mask3); distances[direction] = Geometry.Floats(field);
        }
        var errors = new List<double>(); var worst = new JsonArray(); int measured = 0;
        foreach (var fragment in fragments)
        {
            double maximum = double.NegativeInfinity; Point at = default;
            foreach (var p in fragment.Points)
            {
                int index = (p.Y-y0)*size.Width+p.X-x0; if (excluded[index] != 0) continue;
                measured++; double value = distances[fragment.Direction][index];
                if (value > maximum) { maximum = value; at = p; }
            }
            if (double.IsNegativeInfinity(maximum)) continue;
            errors.Add(maximum); worst.Add(new JsonObject { ["client"] = new JsonArray(at.X, at.Y), ["direction"] = fragment.Direction, ["errorPixels"] = maximum });
        }
        return new() { ["verified"] = errors.Count > 0 && errors.Max() <= 6, ["maxPixels"] = errors.Count > 0 ? errors.Max() : null,
            ["fragmentMaxPixels"] = new JsonArray(errors.Select(v => (JsonNode)JsonValue.Create(v)!).ToArray()), ["fragmentCount"] = fragments.Count,
            ["observedPixels"] = all.Length, ["measuredPixels"] = measured, ["sourceUnknownPixels"] = all.Length-measured, ["worstPoints"] = worst };
    }
}
