using System.Runtime.InteropServices;
using System.Text.Json.Nodes;
using OpenCvSharp;
using static StarMap.Vision.Numerics;

namespace StarMap.Vision;
public sealed partial class SideDoorObservation
{
    private sealed class WallCluster
    {
        internal List<int> Coordinates { get; } = [];
        internal List<(int Start, int End)> Intervals { get; set; } = [];
        internal int Coordinate { get; set; }
        internal int Support { get; set; }
    }

    static JsonObject Appearance(Mat frame, Rect rect)
    {
        using var crop = new Mat(frame, rect);
        byte[] data = Geometry.Bytes(crop);
        int n = crop.Rows * crop.Cols;
        var means = new float[n];
        int bright = 0, veryBright = 0, neutral = 0, wide = 0, yellow = 0, dark = 0;
        for (int i = 0; i < n; i++)
        {
            int blue = data[i * 3], green = data[i * 3 + 1], red = data[i * 3 + 2], chroma = Math.Max(red, Math.Max(green, blue)) - Math.Min(red, Math.Min(green, blue));
            float mean = (red + green + blue) / 3f;
            means[i] = mean;
            if (mean >= 75)
            {
                bright++;
                if (chroma <= 28)
                    neutral++;
                if (chroma <= 36)
                    wide++;
            }

            if (mean >= 120)
                veryBright++;
            if (mean < 55)
                dark++;
            if (red >= blue + 24 && green >= blue + 18 && mean >= 70)
                yellow++;
        }

        float average = SumFloat(means, 0, n) / n;
        var squared = new float[n];
        for (int i = 0; i < n; i++)
        {
            float delta = means[i] - average;
            squared[i] = delta * delta;
        }

        float deviation = MathF.Sqrt(SumFloat(squared, 0, n) / n);
        return new JsonObject
        {
            ["brightPixels"] = bright,
            ["brightRatio"] = bright / (double)n,
            ["veryBrightRatio"] = veryBright / (double)n,
            ["neutralRatio"] = neutral / (double)Math.Max(1, bright),
            ["wideNeutralRatio"] = wide / (double)Math.Max(1, bright),
            ["yellowRatio"] = yellow / (double)n,
            ["darkRatio"] = dark / (double)n,
            ["meanBrightness"] = (double)average,
            ["brightnessDeviation"] = (double)deviation
        };
    }

    static bool AppearanceOk(JsonObject a) => Number(a, "brightPixels") >= 10 && Number(a, "brightRatio") <= .98 && Number(a, "wideNeutralRatio") >= .7 && (Number(a, "veryBrightRatio") >= .01 || Number(a, "brightnessDeviation") >= 12);
    static float SumFloat(float[] a, int start, int count)
    {
        if (count < 8)
        {
            float sum = -0f;
            for (int i = 0; i < count; i++)
                sum += a[start + i];
            return sum;
        }

        if (count <= 128)
        {
            var r = new float[8];
            Array.Copy(a, start, r, 0, 8);
            int i = 8;
            for (; i < count - count % 8; i += 8)
                for (int k = 0; k < 8; k++)
                    r[k] += a[start + i + k];
            float sum = ((r[0] + r[1]) + (r[2] + r[3])) + ((r[4] + r[5]) + (r[6] + r[7]));
            for (; i < count; i++)
                sum += a[start + i];
            return sum;
        }

        int middle = count / 2;
        middle -= middle % 8;
        return SumFloat(a, start, middle) + SumFloat(a, start + middle, count - middle);
    }

    static List<WallCluster> AxisWalls(Mat edge, bool horizontal, double size)
    {
        int minimum = Math.Max(12, Rint(size * .70));
        var lines = Cv2.HoughLinesP(edge, 1, Math.PI / 360, Math.Max(10, Rint(size * .55)), minimum, Math.Max(8, Rint(size * .65)));
        var raw = new List<(int Coordinate, int Start, int End)>();
        foreach (var line in lines)
        {
            int dx = Math.Abs(line.P2.X - line.P1.X), dy = Math.Abs(line.P2.Y - line.P1.Y);
            if (horizontal && dx >= Math.Max(minimum, dy * 5))
                raw.Add((Rint((line.P1.Y + line.P2.Y) / 2.0), Math.Min(line.P1.X, line.P2.X), Math.Max(line.P1.X, line.P2.X)));
            else if (!horizontal && dy >= Math.Max(minimum, dx * 5))
                raw.Add((Rint((line.P1.X + line.P2.X) / 2.0), Math.Min(line.P1.Y, line.P2.Y), Math.Max(line.P1.Y, line.P2.Y)));
        }

        var clusters = new List<WallCluster>();
        int tolerance = Math.Max(2, Rint(size * .13));
        foreach (var row in raw.OrderBy(r => r.Coordinate).ThenBy(r => r.Start).ThenBy(r => r.End))
        {
            var nearest = clusters.OrderBy(c => Math.Abs(Median(c.Coordinates.Select(x => (double)x)) - row.Coordinate)).FirstOrDefault();
            if (nearest is null || Math.Abs(Median(nearest.Coordinates.Select(x => (double)x)) - row.Coordinate) > tolerance)
            {
                nearest = new();
                clusters.Add(nearest);
            }

            nearest.Coordinates.Add(row.Coordinate);
            nearest.Intervals.Add((row.Start, row.End));
        }

        foreach (var c in clusters)
        {
            var merged = new List<(int Start, int End)>();
            foreach (var span in c.Intervals.Where(v => v.End > v.Start).OrderBy(v => v.Start).ThenBy(v => v.End))
                if (merged.Count == 0 || span.Start > merged[^1].End + tolerance)
                    merged.Add(span);
                else
                    merged[^1] = (merged[^1].Start, Math.Max(merged[^1].End, span.End));
            c.Intervals = merged;
            c.Coordinate = Rint(Median(c.Coordinates.Select(x => (double)x)));
            c.Support = merged.Sum(v => v.End - v.Start);
        }

        return clusters;
    }

    static bool[] Brown(Mat image)
    {
        var data = Geometry.Bytes(image);
        var brown = new bool[image.Rows * image.Cols];
        for (int i = 0; i < brown.Length; i++)
        {
            int b = data[i * 3], g = data[i * 3 + 1], r = data[i * 3 + 2];
            double mean = (b + g + r) / 3.0;
            int chroma = Math.Max(r, Math.Max(g, b)) - Math.Min(r, Math.Min(g, b));
            brown[i] = r >= b + 6 && r >= g - 7 && mean >= 55 && mean <= 215 && chroma <= 105;
        }

        return brown;
    }

    static (int Start, int End, double Consistency) ColorSpan(Mat image, byte[] mask, bool[] brown, bool horizontal, int crossStart, int crossEnd, int spanStart, int spanEnd, double position, double size)
    {
        int imageWidth = image.Cols, imageHeight = image.Rows;
        int spanLimit = horizontal ? imageWidth : imageHeight, crossLimit = horizontal ? imageHeight : imageWidth;
        spanStart = Math.Max(0, Math.Min(spanLimit - 1, spanStart));
        spanEnd = Math.Max(spanStart + 1, Math.Min(spanLimit, spanEnd));
        crossStart = Math.Max(0, Math.Min(crossLimit - 1, crossStart + 2));
        crossEnd = Math.Max(crossStart + 1, Math.Min(crossLimit, crossEnd - 2));
        int n = spanEnd - spanStart;
        var values = new float[n];
        var known = new bool[n];
        for (int p = spanStart; p < spanEnd; p++)
        {
            int valid = 0, colored = 0;
            for (int c = crossStart; c < crossEnd; c++)
            {
                int index = horizontal ? c * imageWidth + p : p * imageWidth + c;
                if (mask[index] > 0)
                {
                    valid++;
                    if (brown[index])
                        colored++;
                }
            }

            known[p - spanStart] = valid / (double)(crossEnd - crossStart) >= .20;
            values[p - spanStart] = valid > 0 ? (float)(colored / (double)valid) : 0;
        }

        if (n == 0)
            return (spanStart, spanEnd, 0);
        int smoothWidth = Math.Max(5, Rint(size * .35)) | 1;
        using var raw = new Mat(1, n, MatType.CV_32FC1);
        Marshal.Copy(values, 0, raw.Data, n);
        using var smoothed = new Mat();
        Cv2.Blur(raw, smoothed, new Size(smoothWidth, 1));
        var smooth = Geometry.Floats(smoothed);
        double normalized = (position - spanStart) / Math.Max(spanEnd - spanStart, 1);
        bool start = normalized <= .50;
        int inward = Math.Max(Rint(size * 1.5), Rint((crossEnd - crossStart) * .65));
        int probe = start ? Math.Min(spanEnd - 1, Math.Max(spanStart, Rint(position) + inward)) : Math.Max(spanStart, Math.Min(spanEnd - 1, Rint(position) - inward));
        int localProbe = probe - spanStart;
        bool target = smooth[localProbe] >= .43;
        int mismatchRun = Math.Max(5, Rint(size * .35));
        int Scan(int step)
        {
            int cursor = localProbe, mismatch = 0, boundary = step < 0 ? 0 : n;
            while (cursor >= 0 && cursor < n)
            {
                bool same = (smooth[cursor] >= .43) == target;
                if (!known[cursor])
                    same = true;
                mismatch = same ? 0 : mismatch + 1;
                if (mismatch >= mismatchRun)
                {
                    boundary = step < 0 ? cursor + mismatchRun : cursor - mismatchRun + 1;
                    break;
                }

                cursor += step;
            }

            return boundary;
        }

        int localStart = Math.Max(0, Math.Min(localProbe, Scan(-1))), localEnd = Math.Min(n, Math.Max(localProbe + 1, Scan(1))), consistent = 0;
        for (int i = localStart; i < localEnd; i++)
            if ((smooth[i] >= .43) == target)
                consistent++;
        return (spanStart + localStart, spanStart + localEnd, localEnd > localStart ? consistent / (double)(localEnd - localStart) : 0);
    }

    static JsonObject LocalRectangle(Mat frame, Rect bounds, JsonObject live)
    {
        int x0 = Math.Max(0, bounds.Left), y0 = Math.Max(0, bounds.Top), x1 = Math.Min(frame.Cols, bounds.Right), y1 = Math.Min(frame.Rows, bounds.Bottom);
        if (x1 - x0 < 20 || y1 - y0 < 20)
            return new()
            {
                ["success"] = false,
                ["reason"] = "invalid-visible-map-bounds"
            };
        using var patch = new Mat(frame, new Rect(x0, y0, x1 - x0, y1 - y0));
        using var mask = Geometry.ColorMask(patch);
        using var edge = new Mat();
        using var kernel = Geometry.Kernel(3);
        Cv2.MorphologyEx(mask, edge, MorphTypes.Gradient, kernel);
        double size = live["size"]!.AsArray().Max(v => Scalar(v!)), ix = Scalar(live["center"]![0]!) - x0, iy = Scalar(live["center"]![1]!) - y0;
        if (ix < 0 || iy < 0 || ix >= patch.Cols || iy >= patch.Rows)
            return new()
            {
                ["success"] = false,
                ["reason"] = "door-anchor-outside-map"
            };
        var horizontal = AxisWalls(edge, true, size);
        var vertical = AxisWalls(edge, false, size);
        var candidates = new List<JsonObject>();
        var maskData = Geometry.Bytes(mask);
        bool[]? brown = null;
        void Pairs(List<WallCluster> walls, bool alongX)
        {
            double cross = alongX ? iy : ix, longitudinal = alongX ? ix : iy;
            for (int i = 0; i < walls.Count; i++)
                for (int j = i + 1; j < walls.Count; j++)
                {
                    var first = walls[i];
                    var second = walls[j];
                    int a = Math.Min(first.Coordinate, second.Coordinate), b = Math.Max(first.Coordinate, second.Coordinate);
                    double separation = b - a;
                    if (separation < Math.Max(10, size * .65) || separation > size * 9 || cross < a - size * .55 || cross > b + size * .55)
                        continue;
                    var intervals = first.Intervals.Concat(second.Intervals).ToArray();
                    if (intervals.Length == 0)
                        continue;
                    int start = intervals.Min(v => v.Start), end = intervals.Max(v => v.End);
                    if (end - start < Math.Max(14, separation * .80) || longitudinal < start - size * .70 || longitudinal > end + size * .70)
                        continue;
                    brown ??= Brown(patch);
                    var span = ColorSpan(patch, maskData, brown, alongX, a, b, start, end, longitudinal, size);
                    int rs = Math.Min(span.Start, Rint(longitudinal - size * .52)), re = Math.Max(span.End, Rint(longitudinal + size * .52));
                    double longSize = re - rs;
                    if (longSize < separation * 1.02)
                        continue;
                    double normalized = (longitudinal - rs) / Math.Max(longSize, 1), endpointness = Math.Abs(normalized - .5) * 2;
                    if (endpointness < .28)
                        continue;
                    double support = Math.Min(1, (first.Support + second.Support) / Math.Max(2.0 * (end - start), 1)), aspect = longSize / Math.Max(separation, 1), coverage = Math.Min(1, longSize / Math.Max((double)(alongX ? patch.Cols : patch.Rows), 1));
                    double centered = Math.Max(0, 1 - Math.Abs(cross - (a + b) / 2.0) / Math.Max(separation * .5, 1));
                    double score = support * .22 + span.Consistency * .18 + Math.Min(endpointness, 1) * .20 + Math.Min(aspect, 4) / 4 * .08 + coverage * .20 + centered * .12;
                    int cs = Math.Min(a, Rint(cross - size * .15)), ce = Math.Max(b, Rint(cross + size * .15));
                    var rectangle = alongX ? new JsonArray(rs + x0, cs + y0, re + x0, ce + y0) : new JsonArray(cs + x0, rs + y0, ce + x0, re + y0);
                    candidates.Add(new() { ["bounds"] = rectangle, ["orientation"] = alongX ? "horizontal" : "vertical", ["score"] = score, ["wallSupport"] = support, ["colorConsistency"] = span.Consistency, ["aspectRatio"] = aspect, ["spanCoverage"] = coverage, ["crossCenteredness"] = centered, ["normalizedDoorPosition"] = normalized });
                }
        }

        Pairs(horizontal, true);
        Pairs(vertical, false);
        if (candidates.Count == 0)
            return new()
            {
                ["success"] = false,
                ["reason"] = "local-rectangle-not-found"
            };
        candidates = candidates.OrderByDescending(c => Number(c, "score")).ToList();
        var best = candidates[0];
        double runner = candidates.Count > 1 ? Number(candidates[1], "score") : 0;
        best["success"] = Number(best, "score") >= .53 && Number(best, "colorConsistency") >= .72 && Math.Abs(Number(best, "normalizedDoorPosition") - .5) >= .18;
        best["reason"] = Number(best, "score") >= .53 ? "local-exit-rectangle" : "local-rectangle-weak";
        best["distinctMargin"] = Number(best, "score") - runner;
        best["candidateCount"] = candidates.Count;
        return best;
    }

    static JsonObject Direction(Rect bounds, JsonObject live, JsonObject rectangle)
    {
        bool local = Bool(rectangle, "success") && Number(rectangle, "score") >= .63 && Number(rectangle, "wallSupport") >= .44 && Number(rectangle, "colorConsistency") >= .85;
        double[] b = local ? rectangle["bounds"]!.AsArray().Select(v => Scalar(v!)).ToArray() : [bounds.Left, bounds.Top, bounds.Right, bounds.Bottom];
        double x0 = b[0], y0 = b[1], x1 = b[2], y1 = b[3], cx = live["center"]![0]!.GetValue<double>(), cy = live["center"]![1]!.GetValue<double>(), width = x1 - x0, height = y1 - y0;
        if (width <= 1 || height <= 1 || cx < x0 || cx > x1 || cy < y0 || cy > y1)
            return new()
            {
                ["direction"] = null,
                ["reliable"] = false,
                ["marginPixels"] = 0.0,
                ["rectangleOrientation"] = "unknown",
                ["reason"] = "invalid-map-rectangle"
            };
        double hr = width / height, vr = height / width, nx = (cx - x0) / width, ny = (cy - y0) / height;
        bool iconReliable = Number(live, "score") >= .78 && Number(live, "distinctMargin") >= .10;
        string? name = null;
        string orientation = "unknown", reason = "rectangle-too-square";
        double margin = 0, minAxis = local ? 1.02 : 1.35, endpoint = local ? .64 : .68, begin = 1 - endpoint;
        if (hr >= minAxis)
        {
            orientation = "horizontal";
            margin = (nx - .5) * width;
            if (nx >= endpoint)
            {
                name = "右";
                reason = "horizontal-door-at-right-end";
            }
            else if (nx <= begin)
            {
                name = "左";
                margin = (.5 - nx) * width;
                reason = "horizontal-door-at-left-end";
            }
            else
                reason = "horizontal-door-not-at-end";
        }
        else if (vr >= minAxis)
        {
            orientation = "vertical";
            if (ny <= begin)
            {
                name = "上";
                margin = (.5 - ny) * height;
                reason = "vertical-door-at-top-end";
            }
            else if (ny >= endpoint)
            {
                name = "下";
                margin = (ny - .5) * height;
                reason = "vertical-door-at-bottom-end";
            }
            else
                reason = "vertical-door-not-at-end";
        }

        bool localReliable = local && Number(live, "score") >= .58, reliable = (iconReliable || localReliable) && name is not null;
        return new()
        {
            ["direction"] = reliable ? name : null,
            ["reliable"] = reliable,
            ["marginPixels"] = Math.Max(0, margin),
            ["rectangleOrientation"] = orientation,
            ["rectangleAspectRatio"] = Math.Max(hr, vr),
            ["normalizedDoorPosition"] = new JsonArray(nx, ny),
            ["rectangleBounds"] = new JsonArray(x0, y0, x1, y1),
            ["rectangleSource"] = local ? "local-exit-rectangle" : "visible-map-envelope",
            ["localRectangleAccepted"] = local,
            ["localRectangle"] = rectangle.DeepClone(),
            ["reason"] = iconReliable || localReliable ? reason : "door-anchor-not-distinct"
        };
    }

    static bool Bool(JsonObject o, string key) => o[key]?.GetValue<bool>() == true;
    static double Number(JsonObject o, string key) => o[key] is { } value ? Scalar(value) : 0;
}
