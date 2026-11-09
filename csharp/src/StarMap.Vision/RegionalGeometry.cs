using System.Text.Json.Nodes;
using OpenCvSharp;

namespace StarMap.Vision;

public static class RegionalGeometry
{
    public static JsonObject Evaluate(Mat frame, Observation context, Mat projected, Mat? projectedUnknown = null)
    {
        context.RequireFrame(frame);
        var queryEdge = Geometry.Edge(context);
        using var kernel = Geometry.Kernel(3);
        using var sourceEdge = new Mat();
        Cv2.MorphologyEx(projected, sourceEdge, MorphTypes.Gradient, kernel);
        var observed = Geometry.Bytes(Geometry.Observed(frame, context));
        if (projectedUnknown is not null)
        {
            var excluded = Geometry.Bytes(projectedUnknown);
            for (int i = 0; i < excluded.Length; i++) if (excluded[i] > 0) observed[i] = 0;
        }
        int count = Cv2.CountNonZero(queryEdge);
        var regions = new JsonArray();
        var result = new JsonObject { ["regionalObservation"] = new JsonObject { ["maskEdges"] = count,
            ["contrastSupportedEdges"] = observed.Count(v => v != 0) }, ["regions"] = regions, ["regionalEdgeVerified"] = false };
        if (count == 0) return result;

        var q = Cv2.BoundingRect(queryEdge);
        var s = Cv2.BoundingRect(sourceEdge);
        var bounds = s.Width == 0 || s.Height == 0 ? new Rect(0, 0, frame.Cols, frame.Rows) : Rect.Union(q, s);
        using var roi = new Mat(sourceEdge, bounds);
        using var inverse = new Mat();
        using var distance = new Mat();
        Cv2.Threshold(roi, inverse, 0, 1, ThresholdTypes.BinaryInv);
        Cv2.DistanceTransform(inverse, distance, DistanceTypes.L2, DistanceTransformMasks.Mask3);
        var distances = Geometry.Floats(distance);
        bool verified = true;
        for (int gy = 0; gy < 3; gy++)
        for (int gx = 0; gx < 3; gx++)
        {
            int x0 = Numerics.Rint(q.X + q.Width * gx / 3.0), x1 = Numerics.Rint(q.X + q.Width * (gx+1) / 3.0);
            int y0 = Numerics.Rint(q.Y + q.Height * gy / 3.0), y1 = Numerics.Rint(q.Y + q.Height * (gy+1) / 3.0);
            var values = new List<float>();
            for (int y = y0; y < y1; y++)
            for (int x = x0; x < x1; x++)
                if (observed[y*frame.Cols+x] != 0) values.Add(distances[(y-bounds.Y)*bounds.Width + x-bounds.X]);
            if (values.Count < Math.Max(30,(int)(count*.015))) continue;
            values.Sort();
            int mid = values.Count / 2;
            double median = values.Count % 2 == 1 ? values[mid] : (values[mid-1] + values[mid]) / 2f;
            double within3 = values.Count(v => v <= 3) / (double)values.Count;
            double within6 = values.Count(v => v <= 6) / (double)values.Count;
            regions.Add(new JsonObject { ["cell"] = new JsonArray(gx,gy), ["count"] = values.Count,
                ["median"] = median, ["within3"] = within3, ["within6"] = within6 });
            verified &= median <= 4.5 && within3 >= .45 && within6 >= .62;
        }
        result["regionalEdgeVerified"] = regions.Count > 0 && verified;
        return result;
    }

    public static JsonObject GraphPoints(Mat frame, StructureGeometry reference, double[][] target,
        Observation context, bool allowScaleRecovery, bool useUnknownMask = true)
    {
        context.RequireFrame(frame);
        using var projected = Geometry.Warp(reference.Clean, target, frame.Size());
        var qnodes = Geometry.Nodes(context);
        var snodes = Geometry.Contour(projected);
        if (reference.KnownBoundary is { } boundary)
        {
            using var known = Geometry.Warp(boundary, target, frame.Size());
            using var kernel = Geometry.Kernel(3);
            Cv2.Dilate(known, known, kernel);
            if (reference.Unknown is { } unknown)
            {
                using var excluded = Geometry.Warp(unknown, target, frame.Size());
                known.SetTo(Scalar.All(0), excluded);
            }
            var measured = Geometry.SupportedChains(snodes, known).Select(c => c.Index).ToHashSet();
            for (int i = 0; i < snodes.Count; i++) snodes[i] = snodes[i] with { Valid = snodes[i].Valid && measured.Contains(i) };
        }
        var pairs = new HashSet<(int Q,int S)>();
        var adjacent = new List<(int Q,int S)>();
        var valid = snodes.Select((node, index) => (node,index)).Where(p => p.node.Valid).ToArray();
        for (int qi = 0; qi < qnodes.Count; qi++)
        {
            var query = qnodes[qi];
            if (!query.Valid) continue;
            foreach (var (source, si) in valid)
            {
                var delta = source.Point - query.Point;
                if (Numerics.Dot(delta, delta) <= 48*48 && Numerics.Dot(query.Rays[0], source.Rays[0]) >= .95
                    && Numerics.Dot(query.Rays[1], source.Rays[1]) >= .95) pairs.Add((qi,si));
            }
        }
        foreach (var (qi,si) in pairs.OrderBy(p => p.Q).ThenBy(p => p.S))
        {
            var q = qnodes[qi]; var s = snodes[si];
            if (pairs.Contains((q.Previous,s.Previous)) && Math.Abs(q.Lengths[0] - s.Lengths[0]) <= 3
                && pairs.Contains((q.Next,s.Next)) && Math.Abs(q.Lengths[1] - s.Lengths[1]) <= 3)
                adjacent.Add((qi,si));
        }
        var queryCounts = adjacent.CountBy(p => p.Q).ToDictionary();
        var sourceCounts = adjacent.CountBy(p => p.S).ToDictionary();
        var unique = adjacent.Where(p => queryCounts[p.Q] == 1 && sourceCounts[p.S] == 1).ToArray();
        var segments = new JsonArray();
        var graph = new JsonObject { ["queryTurns"] = qnodes.Count(n => n.Valid), ["sourceTurns"] = valid.Length,
            ["orientedPairs"] = pairs.Count, ["adjacentPairs"] = adjacent.Count, ["uniquePairs"] = unique.Length, ["segments"] = segments };
        foreach (var (qi,si) in unique)
        {
            var q = qnodes[qi]; var s = snodes[si];
            segments.Add(new JsonObject { ["queryPoint"] = new JsonArray(q.Point.X,q.Point.Y),
                ["projectedSourcePoint"] = new JsonArray(s.Point.X,s.Point.Y),
                ["queryLengths"] = new JsonArray(q.Lengths[0],q.Lengths[1]),
                ["projectedSourceLengths"] = new JsonArray(s.Lengths[0],s.Lengths[1]) });
        }
        if (unique.Length < 6) return new() { ["success"] = false, ["reason"] = "graph-adjacent-pairs-insufficient", ["graph"] = graph };
        var observed = unique.Select(p => qnodes[p.Q].Point).ToArray();
        var native = unique.Select(p => snodes[p.S].Point).Select(p =>
            new Point2d((p.X-target[0][2])/target[0][0], (p.Y-target[1][2])/target[1][1])).ToArray();
        var result = PointGeometry.FitAxisLockedPointGeometry(native, observed, target, observed, frame.Size(),
            new double[native.Length], new double[native.Length], qnodes.Count,
            maximumRelativeScaleCorrection: allowScaleRecovery ? .08 : 0);
        result["graph"] = graph;
        result["measuredPairs"] = new JsonObject { ["source"] = Numerics.Points(native), ["observed"] = Numerics.Points(observed) };
        string model = result["h0"]?["success"]?.GetValue<bool>() == true ? "h0" : "h1";
        bool success = result[model]?["success"]?.GetValue<bool>() == true;
        result["structureModel"] = success ? model : null;
        var matrix = success ? Numerics.Matrix(result[model]!["matrixSourceToClient"]!.AsArray()) : target;
        using var fitted = success ? Geometry.Warp(reference.Clean, matrix, frame.Size()) : null;
        using var mask = !useUnknownMask || reference.Unknown is null ? null : Geometry.Warp(reference.Unknown, matrix, frame.Size());
        foreach (var item in Evaluate(frame, context, fitted ?? projected, mask)) graph[item.Key] = item.Value?.DeepClone();
        return result;
    }
}
