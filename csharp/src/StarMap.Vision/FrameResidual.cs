using System.Text.Json.Nodes;
using OpenCvSharp;

namespace StarMap.Vision;

/// <summary>Numeric selected_route_current_frame_residual for the exact shared validation mask.</summary>
public static class FrameResidual
{
    private static readonly string[] Directions = ["inside-right", "inside-left", "inside-down", "inside-up"];

    public static JsonObject Evaluate(Mat frame, Observation observation, Mat validationMask, double[][] matrix, int? maximumDimension = 960)
    {
        observation.RequireFrame(frame);
        if (matrix.Length != 3 || matrix.Any(row => row.Length != 3 || row.Any(v => !double.IsFinite(v))))
            return Reject("invalid-transform");
        if (maximumDimension is <= 0) throw new ArgumentOutOfRangeException(nameof(maximumDimension));
        using var patch = new Mat(frame, observation.Bounds);
        using var query = Geometry.ColorMask(patch);
        int longest = Math.Max(query.Rows, query.Cols);
        double scale = maximumDimension is { } limit && longest > limit ? limit / (double)longest : 1;
        if (scale < 1) Cv2.Resize(query, query,
            new Size(Math.Max(1,Numerics.Rint(query.Cols*scale)), Math.Max(1,Numerics.Rint(query.Rows*scale))),
            0, 0, InterpolationFlags.Nearest);
        int queryPixels = Numerics.Rint(Cv2.CountNonZero(query) / (scale*scale));
        if (queryPixels < 1500) return Reject("insufficient-current-map-pixels", queryPixels);
        using var affine = new Mat(2,3,MatType.CV_32FC1);
        for (int y = 0; y < 2; y++)
        for (int x = 0; x < 3; x++)
        {
            float value = (float)matrix[y][x];
            if (x == 2) value -= y == 0 ? observation.Bounds.X : observation.Bounds.Y;
            affine.Set(y, x, value * (float)scale);
        }
        using var projected = new Mat();
        Cv2.WarpAffine(validationMask, projected, affine, query.Size(), InterpolationFlags.Nearest);
        if (Cv2.CountNonZero(projected) == 0) return Reject("selected-route-outside-visible-map", queryPixels);
        using var kernel = Geometry.Kernel(3);
        using var gradient = new Mat();
        Cv2.MorphologyEx(projected, gradient, MorphTypes.Gradient, kernel);
        using var observed = Geometry.SharpEdges(patch, query, markerGuard: false);
        int count = Cv2.CountNonZero(observed);
        int edgeCount = Numerics.Rint(count/scale);
        if (edgeCount < 200 || Cv2.CountNonZero(gradient) == 0) return Reject("insufficient-wall-edges", queryPixels, edgeCount);
        var edges = Geometry.Bytes(observed);
        var sourceEdges = Geometry.Bytes(gradient);
        int cells = 0, width = query.Cols, height = query.Rows, minimum = Math.Max(30, (int)(count*.015));
        for (int gy = 0; gy < 3; gy++)
        for (int gx = 0; gx < 3; gx++)
        {
            int cellCount = 0;
            for (int y = Numerics.Rint(gy*height/3.0); y < Numerics.Rint((gy+1)*height/3.0); y++)
            for (int x = Numerics.Rint(gx*width/3.0); x < Numerics.Rint((gx+1)*width/3.0); x++)
                if (edges[y*width+x] != 0) cellCount++;
            if (cellCount >= minimum) cells++;
        }

        var normalResiduals = new JsonObject();
        var metrics = new (double Median,double Six)?[4];
        using var qx = new Mat(); using var qy = new Mat();
        using var sx = new Mat(); using var sy = new Mat();
        Gradients(query,qx,qy); Gradients(projected,sx,sy);
        var qX = Geometry.Floats(qx); var qY = Geometry.Floats(qy);
        var sX = Geometry.Floats(sx); var sY = Geometry.Floats(sy);
        for (int direction = 0; direction < 4; direction++)
        {
            var indexes = new List<int>();
            var support = new byte[edges.Length];
            int sourceCount = 0;
            for (int i = 0; i < edges.Length; i++)
            {
                if (edges[i] > 0 && HasNormal(qX[i],qY[i],direction)) indexes.Add(i);
                if (sourceEdges[i] > 0 && HasNormal(sX[i],sY[i],direction))
                {
                    support[i] = 1;
                    sourceCount++;
                }
            }
            int normalCount = Numerics.Rint(indexes.Count/scale);
            if (normalCount < 200) continue;
            float[] distances = sourceCount == 0 ? Enumerable.Repeat(float.PositiveInfinity, indexes.Count).ToArray()
                : SelectDistances(support, width, height, scale, indexes);
            var (median, three, six) = Summarize(distances);
            normalResiduals[Directions[direction]] = new JsonObject { ["queryEdgeCount"] = normalCount, ["medianPixels"] = median,
                ["withinThreePixels"] = three, ["withinSixPixels"] = six, ["verified"] = median <= 4.5 && three >= .45 && six >= .62 };
            metrics[direction] = (median,six);
        }
        var conflicts = new JsonArray();
        for (int axis = 0; axis < 2; axis++)
            if (metrics[axis*2] is { } a && metrics[axis*2+1] is { } b && a.Six < .62 && b.Six < .62
                && (a.Median > 6 || b.Median > 6)) conflicts.Add(axis == 0 ? "x" : "y");
        var all = SelectDistances(sourceEdges, width, height, scale, Enumerable.Range(0,edges.Length).Where(i => edges[i] != 0));
        var (middle,within3,within6) = Summarize(all);
        float index = (all.Length-1) * .9f;
        int low = (int)MathF.Floor(index), high = (int)MathF.Ceiling(index);
        float fraction = index-low, difference = all[high]-all[low];
        double p90 = fraction >= .5f ? all[high]-difference*(1f-fraction) : all[low]+difference*fraction;
        using var wide = Geometry.Kernel(7);
        using var explained = new Mat();
        Cv2.Dilate(projected, explained, wide);
        Cv2.BitwiseAnd(query, explained, explained);
        double coverage = Cv2.CountNonZero(explained)/(double)Cv2.CountNonZero(query);
        bool verified = middle <= 4.5 && within3 >= .45 && within6 >= .62 && coverage >= .68 && conflicts.Count == 0;
        return new JsonObject { ["verified"] = verified,
            ["reason"] = verified ? "selected-route-current-frame-residual-verified" : "selected-route-current-frame-residual-failed",
            ["queryPixels"] = queryPixels, ["queryEdgeCount"] = edgeCount, ["queryEdgeGridCells3x3"] = cells,
            ["wallNormalResiduals"] = normalResiduals, ["conflictingWallAxes"] = conflicts,
            ["medianPixels"] = middle, ["p90Pixels"] = p90, ["withinThreePixels"] = within3,
            ["withinSixPixels"] = within6, ["explainedPixelFraction"] = coverage };
    }

    private static (double Median,double Three,double Six) Summarize(float[] distances)
    {
        Array.Sort(distances);
        int mid = distances.Length/2;
        double median = distances.Length % 2 == 1 ? distances[mid] : (distances[mid-1]+distances[mid])/2f;
        return (median,distances.Count(v => v <= 3)/(double)distances.Length, distances.Count(v => v <= 6)/(double)distances.Length);
    }

    private static float[] SelectDistances(byte[] edges,int width,int height,double scale,IEnumerable<int> indexes)
    {
        using var raw = Geometry.FromBytes(width, height, edges);
        using var inverse = new Mat();
        using var distance = new Mat();
        Cv2.Threshold(raw, inverse, 0, 1, ThresholdTypes.BinaryInv);
        Cv2.DistanceTransform(inverse, distance, DistanceTypes.L2, DistanceTransformMasks.Mask3);
        var values = Geometry.Floats(distance);
        return indexes.Select(i => values[i]/(float)scale).ToArray();
    }

    internal static void Gradients(Mat mask, Mat gx, Mat gy)
    {
        using var pixels = new Mat();
        mask.ConvertTo(pixels, MatType.CV_32FC1);
        Cv2.Sobel(pixels,gx,MatType.CV_32FC1,1,0);
        Cv2.Sobel(pixels,gy,MatType.CV_32FC1,0,1);
    }

    internal static bool HasNormal(float x,float y,int direction) => direction switch {
        0 => Math.Abs(x) >= Math.Abs(y) && x > 0,
        1 => Math.Abs(x) >= Math.Abs(y) && x < 0,
        2 => Math.Abs(y) >= Math.Abs(x) && y > 0,
        _ => Math.Abs(y) >= Math.Abs(x) && y < 0
    };

    private static JsonObject Reject(string reason,int? pixels = null,int? edges = null)
    {
        var result = new JsonObject { ["verified"] = false, ["reason"] = reason };
        if (pixels is not null) result["queryPixels"] = pixels;
        if (edges is not null) result["queryEdgeCount"] = edges;
        return result;
    }
}
