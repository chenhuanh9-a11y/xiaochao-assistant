using System.Runtime.InteropServices;
using System.Text.Json.Nodes;
using OpenCvSharp;

namespace StarMap.Vision;

public static class Geometry
{
    internal static byte[] Bytes(Mat source)
    {
        using var contiguous = source.IsContinuous() ? null : source.Clone();
        var mat = contiguous ?? source;
        var bytes = new byte[checked(mat.Rows * mat.Cols * mat.ElemSize())];
        Marshal.Copy(mat.Data, bytes, 0, bytes.Length);
        return bytes;
    }

    internal static float[] Floats(Mat source)
    {
        using var contiguous = source.IsContinuous() ? null : source.Clone();
        var mat = contiguous ?? source;
        var result = new float[checked(mat.Rows * mat.Cols)];
        Marshal.Copy(mat.Data, result, 0, result.Length);
        return result;
    }

    internal static Mat FromBytes(int width, int height, byte[] pixels)
    {
        if (pixels.Length != checked(height * width)) throw new ArgumentException("Mask size mismatch.");
        var result = new Mat(height, width, MatType.CV_8UC1);
        Marshal.Copy(pixels, 0, result.Data, pixels.Length);
        return result;
    }

    internal static Mat Kernel(int n) => new(n, n, MatType.CV_8UC1, Scalar.All(1));

    internal static Mat ColorMask(Mat patch)
    {
        using var hsv = new Mat();
        Cv2.CvtColor(patch, hsv, ColorConversionCodes.BGR2HSV);
        var color = Bytes(hsv);
        var mask = new byte[checked(patch.Rows * patch.Cols)];
        for (int i = 0; i < mask.Length; i++)
        {
            int hue = color[3*i], saturation = color[3*i+1], value = color[3*i+2];
            bool liveColor = saturation <= 72 || (hue >= 4 && hue <= 32 && saturation >= 22 && saturation <= 175);
            if (liveColor && value >= 68 && value <= 205) mask[i] = 1;
        }
        var result = FromBytes(patch.Cols, patch.Rows, mask);
        using var kernel = Kernel(3);
        Cv2.MorphologyEx(result, result, MorphTypes.Open, kernel);
        return result;
    }

    public static Observation? Current(Mat frame, Rect? observationBounds = null)
    {
        if (frame.Empty()) return null;
        if (frame.Type() != MatType.CV_8UC3)
            throw new ArgumentException("Current geometry requires an 8-bit BGR client frame.", nameof(frame));
        int width = frame.Cols, height = frame.Rows;
        int left = (int)(width * .16), top = (int)(height * .22);
        var bounds = observationBounds ?? new Rect(left, top,
            (int)(width * .76) - left, (int)(height * .80) - top);
        if (bounds.X < 0 || bounds.Y < 0 || bounds.Width <= 0 || bounds.Height <= 0
            || (long)bounds.X + bounds.Width > width || (long)bounds.Y + bounds.Height > height)
        {
            if (observationBounds.HasValue) throw new ArgumentException("Observation bounds must lie inside the frame.");
            return null;
        }
        using var gray = new Mat();
        Cv2.CvtColor(frame, gray, ColorConversionCodes.BGR2GRAY);
        using var search = new Mat(height, width, MatType.CV_8UC1, Scalar.All(0));
        using (var roi = new Mat(gray, bounds))
        using (var mask = new Mat(search, bounds))
            Cv2.Threshold(roi, mask, 74, 255, ThresholdTypes.Binary);
        using var labels = new Mat();
        using var stats = new Mat();
        using var centers = new Mat();
        int count = Cv2.ConnectedComponentsWithStats(search, labels, stats, centers, PixelConnectivity.Connectivity8);
        int best = 0, area = 0;
        for (int index = 1; index < count; index++)
        {
            int candidateArea = stats.At<int>(index, 4);
            if (candidateArea >= 1000 && candidateArea > area
                && stats.At<int>(index, 2) <= (int)(width * .58) && stats.At<int>(index, 3) <= (int)(height * .62))
            {
                best = index;
                area = candidateArea;
            }
        }
        if (best == 0) return null;
        int x = stats.At<int>(best, 0), y = stats.At<int>(best, 1);
        int w = stats.At<int>(best, 2), h = stats.At<int>(best, 3);
        left = Math.Max(0, x - 8);
        top = Math.Max(0, y - 8);
        bounds = new Rect(left, top, Math.Min(width, x + w + 8) - left, Math.Min(height, y + h + 8) - top);
        var component = new JsonObject { ["index"] = best, ["x"] = x, ["y"] = y,
            ["width"] = w, ["height"] = h, ["area"] = area,
            ["center"] = new JsonArray(centers.At<double>(best, 0), centers.At<double>(best, 1)) };
        return FromComponent(frame, bounds, component);
    }

    /// <summary>Reuse the selected current-frame component at its actual padded crop, in full-client coordinates.</summary>
    public static Observation? FromComponent(Mat frame, Rect bounds, JsonObject component)
    {
        using var patch = new Mat(frame, bounds);
        using var mask = ColorMask(patch);
        if (Cv2.CountNonZero(mask) < 300) return null;
        using var canvas = new Mat(frame.Size(), MatType.CV_8UC1, Scalar.All(0));
        using (var roi = new Mat(canvas, bounds)) mask.CopyTo(roi);
        using var kernel = Kernel(3);
        var query = new Mat();
        Cv2.Dilate(canvas, query, kernel);
        return new Observation(frame, query, bounds, component);
    }

    public static List<Corner> Contour(Mat mask)
    {
        Cv2.FindContours(mask, out Point[][] contours, out _, RetrievalModes.List, ContourApproximationModes.ApproxSimple);
        var nodes = new List<Corner>();
        foreach (var contour in contours)
        {
            var polygon = Cv2.ApproxPolyDP(contour, 2, true);
            if (polygon.Length < 4 || Math.Abs(Cv2.ContourArea(polygon)) < 100) continue;
            int start = nodes.Count;
            for (int i = 0; i < polygon.Length; i++)
            {
                int previous = (i + polygon.Length - 1) % polygon.Length;
                int next = (i + 1) % polygon.Length;
                Point2d point = new(polygon[i].X, polygon[i].Y);
                Point2d[] rays = [new(polygon[previous].X-point.X, polygon[previous].Y-point.Y),
                    new(polygon[next].X-point.X, polygon[next].Y-point.Y)];
                var lengths = rays.Select(Numerics.Norm).ToArray();
                for (int arm = 0; arm < 2; arm++)
                {
                    double divisor = Math.Max(lengths[arm], 1e-9);
                    rays[arm] = new(rays[arm].X / divisor, rays[arm].Y / divisor);
                }
                double det = rays[0].X * rays[1].Y - rays[0].Y * rays[1].X;
                nodes.Add(new(point, rays, lengths, start + previous, start + next, lengths.Min() >= 8 && Math.Abs(det) > .45));
            }
        }
        return nodes;
    }

    public static List<Corner> Nodes(Observation o) => o.Nodes ??= Contour(o.Query);

    public static Mat Edge(Observation o)
    {
        if (o.Edge is not null) return o.Edge;
        using var kernel = Kernel(3);
        var gradient = new Mat();
        Cv2.MorphologyEx(o.Query, gradient, MorphTypes.Gradient, kernel);
        return o.Edge = gradient;
    }

    internal static Mat SharpEdges(Mat patch, Mat query, bool markerGuard)
    {
        using var gray8 = new Mat();
        using var gray = new Mat();
        Cv2.CvtColor(patch, gray8, ColorConversionCodes.BGR2GRAY);
        gray8.ConvertTo(gray, MatType.CV_32FC1);
        using var smooth = new Mat();
        using var gx = new Mat();
        using var gy = new Mat();
        Cv2.GaussianBlur(gray, smooth, new Size(3,3), 0);
        Cv2.Sobel(smooth, gx, MatType.CV_32FC1, 1, 0);
        Cv2.Sobel(smooth, gy, MatType.CV_32FC1, 0, 1);
        using var max = new Mat();
        using var min = new Mat();
        using var neighborhood = Kernel(9);
        Cv2.Dilate(gray, max, neighborhood);
        Cv2.Erode(gray, min, neighborhood);
        var x = Floats(gx); var y = Floats(gy); var hi = Floats(max); var lo = Floats(min);
        var sharp = new byte[x.Length];
        for (int i = 0; i < sharp.Length; i++)
        {
            float dx = x[i] / 8f, dy = y[i] / 8f, span = hi[i] - lo[i];
            if (MathF.Sqrt(dx*dx + dy*dy) >= span * .25f && span > 0) sharp[i] = 1;
        }
        using var supported = FromBytes(patch.Cols, patch.Rows, sharp);
        using var kernel = Kernel(3);
        Cv2.Dilate(supported, supported, kernel);
        if (supported.Size() != query.Size()) Cv2.Resize(supported, supported, query.Size(), 0, 0, InterpolationFlags.Nearest);
        using var edge = new Mat();
        Cv2.MorphologyEx(query, edge, MorphTypes.Gradient, kernel, borderType: BorderTypes.Isolated);
        Cv2.BitwiseAnd(edge, supported, edge);
        if (markerGuard)
        {
            using var hsv = new Mat();
            Cv2.CvtColor(patch, hsv, ColorConversionCodes.BGR2HSV);
            using var marker = new Mat();
            Cv2.InRange(hsv, new Scalar(0,88,80), new Scalar(255,255,255), marker);
            using var guard = Kernel(5);
            Cv2.Dilate(marker, marker, guard);
            edge.SetTo(Scalar.All(0), marker);
        }
        return edge.Clone();
    }

    public static Mat Observed(Mat frame, Observation o)
    {
        o.RequireFrame(frame);
        if (o.Observed is not null) return o.Observed;
        var edge = Edge(o);
        var extent = Cv2.BoundingRect(edge);
        using var canvas = new Mat(edge.Size(), MatType.CV_8UC1, Scalar.All(0));
        if (extent.Width > 0 && extent.Height > 0)
        {
            var rect = new Rect(extent.X-5, extent.Y-5, extent.Width+10, extent.Height+10)
                .Intersect(new Rect(0, 0, frame.Cols, frame.Rows));
            using var patch = new Mat(frame, rect);
            using var query = new Mat(o.Query, rect);
            using var sharp = SharpEdges(patch, query, markerGuard: true);
            using var currentEdge = new Mat(edge, rect);
            using var target = new Mat(canvas, rect);
            Cv2.BitwiseAnd(currentEdge, sharp, target);
        }
        var result = new Mat(edge.Size(), MatType.CV_8UC1, Scalar.All(0));
        if (o.Bounds.Width > 6 && o.Bounds.Height > 6)
        {
            var interior = new Rect(o.Bounds.X+3, o.Bounds.Y+3, o.Bounds.Width-6, o.Bounds.Height-6);
            using var source = new Mat(canvas, interior);
            using var dest = new Mat(result, interior);
            source.CopyTo(dest);
        }
        return o.Observed = result;
    }

    public static List<FastObservedChain> ObservedChains(Mat frame, Observation o)
    {
        o.RequireFrame(frame);
        if (o.Chains is not null) return o.Chains;
        return o.Chains = SupportedChains(Nodes(o), Observed(frame, o));
    }

    internal static List<FastObservedChain> SupportedChains(List<Corner> nodes, Mat mask)
    {
        var pixels = Bytes(mask);
        var chains = new List<FastObservedChain>();
        for (int i = 0; i < nodes.Count; i++)
        {
            var node = nodes[i];
            int[] indices = [node.Previous, i, node.Next];
            if (indices.Any(index => !nodes[index].Valid)) continue;
            var points = indices.Select(index => nodes[index].Point).ToArray();
            if (points.Any(p => p.X < 5 || p.Y < 5 || p.X > mask.Cols-6 || p.Y > mask.Rows-6)) continue;
            if (!Supported(points[0]) || !Supported(points[2])) continue;
            chains.Add(new(points.Select(p => new[]{p.X, p.Y}).ToArray(), node.Rays.Select(p => new[]{p.X, p.Y}).ToArray()) { Index = i });

            bool Supported(Point2d endpoint)
            {
                var delta = endpoint - node.Point;
                int intervals = (int)Math.Ceiling(Numerics.Norm(delta));
                double step = 1.0 / intervals;
                for (int j = 0; j <= intervals; j++)
                {
                    var p = node.Point + delta * (j == intervals ? 1.0 : j * step);
                    if (pixels[Numerics.Rint(p.Y) * mask.Cols + Numerics.Rint(p.X)] == 0) return false;
                }
                return true;
            }
        }
        return chains;
    }

    public static List<Wall> Lines(Mat frame, Observation o)
    {
        o.RequireFrame(frame);
        return o.Lines ??= VisibleWalls(o.Query, Observed(frame, o), Nodes(o));
    }

    public static List<Wall> VisibleWalls(Mat mask, Mat? observed = null, List<Corner>? nodes = null)
    {
        nodes ??= Contour(mask);
        var pixels = observed is null ? null : Bytes(observed);
        var walls = new List<Wall>();
        for (int i = 0; i < nodes.Count; i++)
        {
            var start = nodes[i].Point;
            var delta = nodes[nodes[i].Next].Point - start;
            bool vertical = Math.Abs(delta.X) < Math.Abs(delta.Y);
            double length = Math.Abs(vertical ? delta.Y : delta.X);
            if (length < 12 || Math.Abs(vertical ? delta.X : delta.Y) > 3) continue;
            var fragment = new List<Point2d>();
            int intervals = (int)Math.Ceiling(length);
            double step = 1.0 / intervals;
            for (int j = 0; j <= intervals; j++)
            {
                var p = start + delta * (j == intervals ? 1.0 : j * step);
                if (pixels is null || pixels[Numerics.Rint(p.Y)*mask.Cols + Numerics.Rint(p.X)] > 0) fragment.Add(p);
                else Flush();
            }
            Flush();

            void Flush()
            {
                if (fragment.Count >= 12)
                    walls.Add(new(vertical ? 0 : 1, Math.Sign(vertical ? delta.Y : delta.X),
                        Numerics.Median(fragment.Select(p => vertical ? p.X : p.Y)),
                        fragment.Min(p => vertical ? p.Y : p.X), fragment.Max(p => vertical ? p.Y : p.X), i));
                fragment.Clear();
            }
        }
        return walls;
    }

    public static Mat Warp(Mat source, double[][] matrix, Size clientSize)
    {
        using var affine = new Mat(2,3,MatType.CV_64FC1);
        for (int row = 0; row < 2; row++)
            for (int column = 0; column < 3; column++) affine.Set(row, column, matrix[row][column]);
        var projected = new Mat();
        Cv2.WarpAffine(source, projected, affine, clientSize, InterpolationFlags.Nearest);
        return projected;
    }
}
