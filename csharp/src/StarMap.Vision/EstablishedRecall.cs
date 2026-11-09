using System.Text.Json.Nodes;
using OpenCvSharp;
using static StarMap.Vision.EstablishedGroupFit;

namespace StarMap.Vision;

/// <summary>Small-patch correlation and native directed-turn voting from single_map_structure.</summary>
public static class EstablishedRecall
{
    public sealed record Result(IReadOnlyList<JsonObject> Candidates, JsonObject Diagnostics);
    private sealed record Peak(int Index, Point2d Translation, double Score, Point QueryPoint);
    private sealed record Vote(int Query, int Source, Point2d Translation);

    public static Result Retrieve(Mat reference, Mat query, double[][] linear, double queryScale, Func<bool>? cancelled = null)
    {
        var (origin, size, matrix) = SheetTransform(reference, linear, queryScale);
        if (Math.Max(size.Width, size.Height) > 4096 || Math.Min(size.Width, size.Height) < 16)
            return new([], new() { ["reason"] = "structure-index-size-unsupported" });
        using var raw = Geometry.Warp(reference, matrix, size);
        const int radius = 24;
        using var sheet = new Mat(); Cv2.CopyMakeBorder(raw, sheet, radius, radius, radius, radius, BorderTypes.Constant);
        // OpenCV documents a null/empty mask as the full image; this wrapper's annotation is non-nullable.
        var points = Cv2.GoodFeaturesToTrack(query, 16, .015, 24, mask: null!, blockSize: 7,
            useHarrisDetector: false, k: .04);
        if (points.Length == 0) return new([], new() { ["reason"] = "structure-query-no-corners" });
        var peaks = new List<Peak>();
        for (int index = 0; index < points.Length; index++)
        {
            if (cancelled?.Invoke() == true) return new([], new() { ["reason"] = "structure-work-obsolete" });
            int x = Numerics.Rint(points[index].X), y = Numerics.Rint(points[index].Y);
            int x0 = Math.Max(0, x - radius), x1 = Math.Min(query.Cols, x + radius);
            int y0 = Math.Max(0, y - radius), y1 = Math.Min(query.Rows, y + radius);
            using var patch = new Mat(query, new Rect(x0, y0, x1 - x0, y1 - y0));
            if (Cv2.Sum(patch).Val0 < 24 || Math.Min(patch.Rows, patch.Cols) < 16) continue;
            using var scores = new Mat(); Cv2.MatchTemplate(sheet, patch, scores, TemplateMatchModes.CCoeffNormed);
            for (int iteration = 0; iteration < 8; iteration++)
            {
                Cv2.MinMaxLoc(scores, out _, out double score, out _, out Point peak);
                if (score < .55) break;
                var translation = new Point2d((x0 - peak.X + radius - origin.X) / queryScale,
                    (y0 - peak.Y + radius - origin.Y) / queryScale);
                peaks.Add(new(index, translation, score, new(x, y)));
                Cv2.Circle(scores, peak, Math.Max(4, Numerics.Rint(32 * queryScale)), Scalar.All(-1), -1);
            }
        }
        var candidates = new List<JsonObject>();
        foreach (var peak in peaks)
        {
            if (cancelled?.Invoke() == true) return new([], new() { ["reason"] = "structure-work-obsolete" });
            var unique = new Dictionary<int, Peak>();
            foreach (var nearby in peaks.Where(p => Numerics.Norm(p.Translation - peak.Translation) <= 16))
                if (!unique.TryGetValue(nearby.Index, out var previous) || nearby.Score > previous.Score) unique[nearby.Index] = nearby;
            var support = unique.Values.ToArray(); if (support.Length < 3) continue;
            if (support.Max(a => support.Max(b => Math.Sqrt(Math.Pow(a.QueryPoint.X - b.QueryPoint.X, 2) + Math.Pow(a.QueryPoint.Y - b.QueryPoint.Y, 2)))) < 48) continue;
            var center = Median(support.Select(p => p.Translation));
            if (candidates.Any(c => Numerics.Norm(center - Position(c)) < 24)) continue;
            candidates.Add(Candidate(center, support.Length, support.Average(p => p.Score)));
        }
        // LINQ's stable OrderBy preserves original peak order for ties, like Python sort.
        var selected = candidates.OrderByDescending(c => Number(c["support"])).ThenByDescending(c => Number(c["score"])).Take(8).ToArray();
        return new(selected, new() { ["peaks"] = peaks.Count, ["candidates"] = candidates.Count,
            ["sheetShape"] = new JsonArray(sheet.Rows, sheet.Cols), ["corners"] = points.Length });
    }

    public static Result RetrieveNativeTurns(Mat reference, Mat frame, double[][] linear, Observation context, Func<bool>? cancelled = null)
    {
        context.RequireFrame(frame);
        var (origin, size, matrix) = SheetTransform(reference, linear, 1);
        using var sheet = Geometry.Warp(reference, matrix, size);
        var query = Geometry.Nodes(context).Where(n => n.Valid).ToArray();
        var source = Geometry.Contour(sheet).Where(n => n.Valid).ToArray();
        var votes = new List<Vote>();
        for (int qi = 0; qi < query.Length; qi++)
        {
            if (cancelled?.Invoke() == true) return new([], new() { ["reason"] = "structure-work-obsolete" });
            for (int si = 0; si < source.Length; si++)
                if (Math.Min(Numerics.Dot(source[si].Rays[0], query[qi].Rays[0]), Numerics.Dot(source[si].Rays[1], query[qi].Rays[1])) >= .95)
                    votes.Add(new(qi, si, query[qi].Point - source[si].Point - origin));
        }
        var buckets = new Dictionary<(int X, int Y), List<int>>();
        for (int i = 0; i < votes.Count; i++)
        {
            var key = Bucket(votes[i].Translation);
            if (!buckets.TryGetValue(key, out var indexes)) buckets[key] = indexes = [];
            indexes.Add(i);
        }
        var candidates = new List<JsonObject>();
        foreach (var vote in votes)
        {
            if (cancelled?.Invoke() == true) return new([], new() { ["reason"] = "structure-work-obsolete" });
            var (bx, by) = Bucket(vote.Translation); var indexes = new List<int>();
            for (int dx = -1; dx <= 1; dx++) for (int dy = -1; dy <= 1; dy++)
                if (buckets.TryGetValue((bx + dx, by + dy), out var rows)) indexes.AddRange(rows);
            indexes.Sort();
            var unique = new Dictionary<int, Point2d>(); var nearest = new Dictionary<int, double>();
            foreach (int i in indexes)
            {
                var neighbor = votes[i]; var delta = neighbor.Translation - vote.Translation;
                double distance = Numerics.Dot(delta, delta); if (distance > 64) continue;
                if (!unique.ContainsKey(neighbor.Query) || distance < nearest[neighbor.Query])
                { unique[neighbor.Query] = neighbor.Translation; nearest[neighbor.Query] = distance; }
            }
            if (unique.Count < 3) continue;
            var points = unique.Keys.Select(q => query[q].Point).ToArray();
            if (Numerics.Norm(new(points.Max(p => p.X) - points.Min(p => p.X), points.Max(p => p.Y) - points.Min(p => p.Y))) < 48) continue;
            var center = Median(unique.Values);
            if (candidates.Any(c => { var d = center - Position(c); return Numerics.Dot(d, d) < 24 * 24; })) continue;
            candidates.Add(Candidate(center, unique.Count, 1, "native-turn-constellation"));
        }
        return new(candidates.OrderByDescending(c => Number(c["support"])).Take(8).ToArray(), new() {
            ["queryTurns"] = query.Length, ["sourceTurns"] = source.Length, ["votes"] = votes.Count, ["candidates"] = candidates.Count });
    }

    private static (int X, int Y) Bucket(Point2d p) => ((int)Math.Floor(p.X / 8), (int)Math.Floor(p.Y / 8));
    private static Point2d Median(IEnumerable<Point2d> positions)
    { var points = positions.ToArray(); return new(Numerics.Median(points.Select(p => p.X)), Numerics.Median(points.Select(p => p.Y))); }
    private static (Point2d Origin, Size Size, double[][] Matrix) SheetTransform(Mat reference, double[][] linear, double scale)
    {
        double xx = linear[0][0] * scale, xy = linear[0][1] * scale;
        double yx = linear[1][0] * scale, yy = linear[1][1] * scale;
        var corners = new[] { new Point2d(0, 0), new Point2d(reference.Cols, 0), new Point2d(0, reference.Rows), new Point2d(reference.Cols, reference.Rows) }
            .Select(p => new Point2d(p.X * xx + p.Y * xy, p.X * yx + p.Y * yy)).ToArray();
        var origin = new Point2d(Math.Floor(corners.Min(p => p.X)), Math.Floor(corners.Min(p => p.Y)));
        var size = new Size((int)Math.Ceiling(corners.Max(p => p.X) - origin.X) + 1, (int)Math.Ceiling(corners.Max(p => p.Y) - origin.Y) + 1);
        return (origin, size, [ [xx, xy, -origin.X], [yx, yy, -origin.Y], [0, 0, 1] ]);
    }
}
