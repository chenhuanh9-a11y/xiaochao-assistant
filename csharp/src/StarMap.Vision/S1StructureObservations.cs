using OpenCvSharp;
using System.Text.Json.Nodes;

namespace StarMap.Vision;

internal static class S1StructureObservations
{
    internal sealed record Island(Rect Bounds, JsonObject Component);
    public static IReadOnlyList<Island> Islands(Mat frame, Rect domain)
    {
        using var gray = new Mat(); Cv2.CvtColor(frame, gray, ColorConversionCodes.BGR2GRAY);
        using var search = new Mat(frame.Size(), MatType.CV_8UC1, Scalar.All(0));
        using (var roi = new Mat(gray, domain)) using (var mask = new Mat(search, domain)) Cv2.Threshold(roi, mask, 74, 255, ThresholdTypes.Binary);
        using var labels = new Mat(); using var stats = new Mat(); using var centers = new Mat();
        int count = Cv2.ConnectedComponentsWithStats(search, labels, stats, centers, PixelConnectivity.Connectivity8);
        var islands = new List<Island>();
        for (int i = 1; i < count; i++)
        {
            int x = stats.At<int>(i, 0), y = stats.At<int>(i, 1), w = stats.At<int>(i, 2), h = stats.At<int>(i, 3), area = stats.At<int>(i, 4);
            if (area < 1000 || w > (int)(frame.Cols * .58) || h > (int)(frame.Rows * .62)) continue;
            int left = Math.Max(0, x - 8), top = Math.Max(0, y - 8);
            islands.Add(new(new Rect(left, top, Math.Min(frame.Cols, x + w + 8) - left, Math.Min(frame.Rows, y + h + 8) - top),
                new() { ["index"] = i, ["x"] = x, ["y"] = y, ["width"] = w, ["height"] = h, ["area"] = area,
                    ["center"] = new JsonArray(centers.At<double>(i, 0), centers.At<double>(i, 1)) }));
        }
        return islands.OrderByDescending(i => i.Component["area"]!.GetValue<int>()).ToArray();
    }

    public static List<FastObservedChain> MarkerProposals(Mat frame, Observation context, IReadOnlyList<FastObservedChain> measured)
    {
        var nodes = Geometry.Nodes(context); var observed = Geometry.Bytes(Geometry.Observed(frame, context));
        var measuredIndexes = measured.Select(c => c.Index).ToHashSet();
        if (nodes.Select((node, i) => measuredIndexes.Contains(i) || !node.Valid).All(v => v)) return [];
        var bounds = context.Bounds;
        int x0 = Math.Max(0, bounds.X - 2), y0 = Math.Max(0, bounds.Y - 2), x1 = Math.Min(frame.Cols, bounds.Right + 2), y1 = Math.Min(frame.Rows, bounds.Bottom + 2);
        using var patch = new Mat(frame, new Rect(x0, y0, x1 - x0, y1 - y0));
        using var hsv = new Mat(); Cv2.CvtColor(patch, hsv, ColorConversionCodes.BGR2HSV);
        var pixels = Geometry.Bytes(hsv); var mask = new byte[patch.Rows * patch.Cols];
        for (int i = 0; i < mask.Length; i++) if (pixels[3*i+1] >= 88 && pixels[3*i+2] >= 80) mask[i] = 1;
        if (!mask.Any(v => v != 0)) return [];
        using var marker = Geometry.FromBytes(patch.Cols, patch.Rows, mask); using var kernel = Geometry.Kernel(5);
        Cv2.Dilate(marker, marker, kernel); mask = Geometry.Bytes(marker);
        int markerRows = marker.Rows, markerCols = marker.Cols;
        for (int y = 0; y < markerRows; y++) for (int x = 0; x < markerCols; x++)
            if (x + x0 < bounds.X + 3 || x + x0 >= bounds.Right - 3 || y + y0 < bounds.Y + 3 || y + y0 >= bounds.Bottom - 3) mask[y*markerCols+x] = 0;
        var proposals = new List<FastObservedChain>();
        for (int i = 0; i < nodes.Count; i++)
        {
            if (measuredIndexes.Contains(i)) continue;
            var node = nodes[i]; int[] indices = [node.Previous, i, node.Next];
            if (indices.Any(j => !nodes[j].Valid)) continue;
            var points = indices.Select(j => nodes[j].Point).ToArray();
            if (points.Any(p => p.X < x0 || p.Y < y0 || p.X >= x1 || p.Y >= y1 || p.X < 5 || p.Y < 5 || p.X > frame.Cols-6 || p.Y > frame.Rows-6)) continue;
            bool supported = true;
            foreach (var endpoint in new[] { points[0], points[2] })
            {
                var delta = endpoint - node.Point;
                if (Math.Min(Math.Abs(delta.X), Math.Abs(delta.Y)) > 3) { supported = false; break; }
                int n = (int)Math.Ceiling(Numerics.Norm(delta)); int run = 0, maximum = 0;
                for (int j = 0; j <= n; j++)
                {
                    var p = node.Point + delta * (j == n ? 1.0 : j * (1.0 / n));
                    int x = Numerics.Rint(p.X), y = Numerics.Rint(p.Y);
                    bool evidence = observed[y*frame.Cols+x] != 0;
                    bool occluded = mask[(y-y0)*markerCols+x-x0] != 0;
                    if (!evidence && !occluded) { supported = false; break; }
                    run = evidence ? run + 1 : 0; maximum = Math.Max(maximum, run);
                }
                if (maximum < 12) supported = false;
                if (!supported) break;
            }
            if (supported) proposals.Add(new(points.Select(p => new[] { p.X, p.Y }).ToArray(), node.Rays.Select(p => new[] { p.X, p.Y }).ToArray()) { Index = i });
        }
        return proposals;
    }

    public static JsonArray Bounds(Rect bounds) => new(bounds.X, bounds.Y, bounds.Right, bounds.Bottom);
    public static JsonArray Matrix(double[][] matrix) => new(matrix.Select(row => (JsonNode)new JsonArray(row.Select(v => (JsonNode)JsonValue.Create(v)!).ToArray())).ToArray());
    public static double[][] Matrix(JsonNode matrix) => Numerics.Matrix(matrix.AsArray());
    public static bool Success(JsonNode? value) => value?["success"]?.GetValue<bool>() == true;
    public static double PoseDistance(double[][] a, double[][] b, Size size)
    {
        double maximum = 0;
        foreach (var p in new[] { new Point2d(0, 0), new Point2d(size.Width, 0), new Point2d(0, size.Height), new Point2d(size.Width, size.Height) })
        {
            double x = p.X*(a[0][0]-b[0][0]) + p.Y*(a[0][1]-b[0][1]) + a[0][2]-b[0][2];
            double y = p.X*(a[1][0]-b[1][0]) + p.Y*(a[1][1]-b[1][1]) + a[1][2]-b[1][2];
            maximum = Math.Max(maximum, Math.Sqrt(x*x+y*y));
        }
        return maximum;
    }
}
