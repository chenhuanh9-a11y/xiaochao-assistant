namespace StarMap.Core.Recognition;

/// <summary>
/// 1:1 transcription of the coarse-grid slice in
/// <c>app/fast_structure_alignment.py</c> (NumPy 2.2.6).
/// Constants and comparisons are the Python literals. Do not retune them.
/// </summary>
public static class GridSlice
{
    /// <summary>Fine observation count checked by <c>propose</c> before <c>_grid</c> (line 328 / 330).</summary>
    public const int FineEdgeLimit = 300;

    /// <summary><c>len(points) &lt; 40</c> returns <c>fast-insufficient-edges</c> (line 330).</summary>
    public const int MinimumEdgePoints = 40;

    /// <summary>Coarse points actually passed to <c>_grid</c> (line 333).</summary>
    public const int CoarseEdgeLimit = 96;

    public const string InsufficientEdges = "fast-insufficient-edges";
    public const string GridResult = "grid";

    public static GridSliceResult Evaluate(
        ReadOnlySpan<byte> edge,
        int edgeHeight,
        int edgeWidth,
        ReadOnlySpan<ushort> distance,
        int preparedHeight,
        int preparedWidth,
        double scale,
        double? priorX,
        double? priorY,
        Func<bool>? cancelled)
    {
        var fine = NumpyCompat.SampleEdges(edge, edgeHeight, edgeWidth, FineEdgeLimit);
        if (fine.Length < MinimumEdgePoints)
        {
            return new GridSliceResult(
                InsufficientEdges,
                fine.Length,
                fine,
                Array.Empty<GridRow>(),
                Cancelled: false);
        }

        var coarse = NumpyCompat.SampleEdges(edge, edgeHeight, edgeWidth, CoarseEdgeLimit);
        var search = NumpyCompat.Grid(
            coarse, distance, preparedHeight, preparedWidth, scale, priorX, priorY, cancelled);
        return new GridSliceResult(GridResult, fine.Length, coarse, search.Rows, search.Cancelled);
    }
}

public readonly record struct SamplePoint(float X, float Y);

public readonly record struct GridRow(double Score, double Scale, double X, double Y);

public readonly record struct GridSearch(GridRow[] Rows, bool Cancelled);

public sealed record GridSliceResult(
    string Reason,
    int GatePointCount,
    SamplePoint[] HashedPoints,
    GridRow[] Rows,
    bool Cancelled);

/// <summary>
/// NumPy 2.2.6 operations used by <c>sample_edges</c> and <c>_grid</c>.
/// <c>arange</c> length is <c>ceil((stop-start)/step)</c> and the step actually
/// stored is <c>(start+step)-start</c>. <c>quantile</c> uses the linear method,
/// including the <c>t &gt;= 0.5</c> lerp branch. <c>rint</c> is ties-to-even.
/// </summary>
static class NumpyCompat
{
    public static SamplePoint[] SampleEdges(ReadOnlySpan<byte> edge, int height, int width, int limit)
    {
        if (height < 0 || width < 0 || (long)height * width != edge.Length)
            throw new ArgumentException("edge-shape");
        if (limit < 1)
            throw new ArgumentOutOfRangeException(nameof(limit));

        var raw = new List<(int X, int Y)>();
        for (int y = 0; y < height; y++)
        {
            int row = y * width;
            for (int x = 0; x < width; x++)
            {
                if (edge[row + x] != 0)
                    raw.Add((x, y));
            }
        }
        if (raw.Count == 0)
            return Array.Empty<SamplePoint>();
        if (raw.Count <= limit)
            return ToFloat(raw);

        int heightDiv = Math.Max(1, height);
        int widthDiv = Math.Max(1, width);
        var bins = new int[raw.Count];
        var unique = new SortedSet<int>();
        for (int i = 0; i < raw.Count; i++)
        {
            int bin = (raw[i].Y * 8 / heightDiv) * 8 + (raw[i].X * 8 / widthDiv);
            bins[i] = bin;
            unique.Add(bin);
        }

        int quota = Math.Max(1, (limit + unique.Count - 1) / unique.Count);
        var sampled = new List<(int X, int Y)>();
        foreach (int bin in unique)
        {
            var group = new List<int>();
            for (int i = 0; i < raw.Count; i++)
            {
                if (bins[i] == bin)
                    group.Add(i);
            }
            int take = Math.Min(quota, group.Count);
            foreach (int index in LinspaceIndex(0, group.Count - 1, take))
                sampled.Add(raw[group[index]]);
        }
        if (sampled.Count > limit)
        {
            var trimmed = new List<(int X, int Y)>(limit);
            foreach (int index in LinspaceIndex(0, sampled.Count - 1, limit))
                trimmed.Add(sampled[index]);
            sampled = trimmed;
        }
        return ToFloat(sampled);
    }

    public static GridSearch Grid(
        IReadOnlyList<SamplePoint> points,
        ReadOnlySpan<ushort> distance,
        int preparedHeight,
        int preparedWidth,
        double scale,
        double? priorX,
        double? priorY,
        Func<bool>? cancelled)
    {
        if (points.Count == 0)
            throw new InvalidOperationException("grid-empty-points");
        if (preparedHeight < 1 || preparedWidth < 1
            || (long)preparedHeight * preparedWidth != distance.Length)
            throw new ArgumentException("distance-shape");
        if (priorX.HasValue != priorY.HasValue)
            throw new ArgumentException("prior");

        double[] tx;
        double[] ty;
        if (priorX.HasValue)
        {
            var offsets = Arange(-32.0, 32.1, 4.0);
            tx = new double[offsets.Length];
            ty = new double[offsets.Length];
            for (int i = 0; i < offsets.Length; i++)
            {
                tx[i] = priorX.Value + offsets[i];
                ty[i] = priorY!.Value + offsets[i];
            }
        }
        else
        {
            var referenceX = new double[points.Count];
            var referenceY = new double[points.Count];
            for (int i = 0; i < points.Count; i++)
            {
                referenceX[i] = points[i].X / scale;
                referenceY[i] = points[i].Y / scale;
            }
            double medianX = Median(referenceX);
            double medianY = Median(referenceY);
            double stride = Math.Max(3.0, 5.0 / scale);
            var xs = Arange(-medianX, preparedWidth - medianX + stride, stride);
            var ys = Arange(-medianY, preparedHeight - medianY + stride, stride);
            if ((long)xs.Length * ys.Length > 350_000)
                return new GridSearch(Array.Empty<GridRow>(), false);
            tx = new double[xs.Length];
            ty = new double[ys.Length];
            for (int i = 0; i < xs.Length; i++)
                tx[i] = -scale * xs[i];
            for (int i = 0; i < ys.Length; i++)
                ty[i] = -scale * ys[i];
        }

        return ScoreAndSuppress(points, distance, preparedHeight, preparedWidth, scale, tx, ty, cancelled);
    }

    private static GridSearch ScoreAndSuppress(
        IReadOnlyList<SamplePoint> points,
        ReadOnlySpan<ushort> distance,
        int preparedHeight,
        int preparedWidth,
        double scale,
        double[] tx,
        double[] ty,
        Func<bool>? cancelled)
    {
        int pointCount = points.Count;
        int txCount = tx.Length;
        int tyCount = ty.Length;
        int translationCount = txCount * tyCount;
        if (translationCount == 0)
            throw new InvalidOperationException("grid-empty-search");

        float scale32 = (float)scale;
        var xx = new int[txCount * pointCount];
        var yy = new int[tyCount * pointCount];
        var validX = new bool[txCount * pointCount];
        var validY = new bool[tyCount * pointCount];
        for (int col = 0; col < txCount; col++)
        {
            for (int point = 0; point < pointCount; point++)
            {
                int rounded = RintToInt32((points[point].X - tx[col]) / scale32);
                int flat = col * pointCount + point;
                validX[flat] = rounded >= 0 && rounded < preparedWidth;
                xx[flat] = Math.Clamp(rounded, 0, preparedWidth - 1);
            }
        }
        for (int row = 0; row < tyCount; row++)
        {
            for (int point = 0; point < pointCount; point++)
            {
                int rounded = RintToInt32((points[point].Y - ty[row]) / scale32);
                int flat = row * pointCount + point;
                validY[flat] = rounded >= 0 && rounded < preparedHeight;
                yy[flat] = Math.Clamp(rounded, 0, preparedHeight - 1);
            }
        }

        var values = new float[translationCount];
        if (translationCount < 4096)
        {
            for (int start = 0; start < translationCount; start += 1024)
            {
                if (cancelled is not null && cancelled())
                    return new GridSearch(Array.Empty<GridRow>(), true);
                int stop = Math.Min(start + 1024, translationCount);
                for (int index = start; index < stop; index++)
                {
                    int col = index % txCount;
                    int row = index / txCount;
                    int hit3 = 0;
                    int hit6 = 0;
                    for (int point = 0; point < pointCount; point++)
                    {
                        if (!validX[col * pointCount + point] || !validY[row * pointCount + point])
                            continue;
                        ushort stored = distance[yy[row * pointCount + point] * preparedWidth + xx[col * pointCount + point]];
                        float scaled = stored * scale32;
                        double pixels = (double)scaled * 0.25;
                        if (pixels <= 3.0)
                            hit3++;
                        if (pixels <= 6.0)
                            hit6++;
                    }
                    values[index] = (float)((2.0 * hit3 + hit6) / (3.0 * pointCount));
                }
            }
        }
        else
        {
            var cutoffs = new double[65536];
            for (int i = 0; i < cutoffs.Length; i++)
            {
                float scaled = (float)i * scale32;
                cutoffs[i] = (double)scaled * 0.25;
            }
            int limit3 = SearchSortedRight(cutoffs, 3.0) - 1;
            int limit6 = SearchSortedRight(cutoffs, 6.0) - 1;
            var totals = new uint[translationCount];
            for (int point = 0; point < pointCount; point++)
            {
                if (cancelled is not null && cancelled())
                    return new GridSearch(Array.Empty<GridRow>(), true);
                if (!TryInterval(validX, pointCount, point, txCount, out int x0, out int x1)
                    || !TryInterval(validY, pointCount, point, tyCount, out int y0, out int y1))
                    continue;
                for (int row = y0; row < y1; row++)
                {
                    int yIndex = yy[row * pointCount + point];
                    for (int col = x0; col < x1; col++)
                    {
                        int stored = distance[yIndex * preparedWidth + xx[col * pointCount + point]];
                        uint add = 0;
                        if (stored <= limit3)
                            add += 2;
                        if (stored <= limit6)
                            add += 1;
                        totals[row * txCount + col] += add;
                    }
                }
            }
            for (int i = 0; i < totals.Length; i++)
                values[i] = (float)(totals[i] / (3.0 * pointCount));
        }

        var translationsX = new double[translationCount];
        var translationsY = new double[translationCount];
        for (int row = 0; row < tyCount; row++)
        {
            for (int col = 0; col < txCount; col++)
            {
                int index = row * txCount + col;
                translationsX[index] = tx[col];
                translationsY[index] = ty[row];
            }
        }

        var rows = new List<GridRow>(8);
        for (int peak = 0; peak < 8; peak++)
        {
            int best = 0;
            float bestValue = values[0];
            for (int i = 1; i < values.Length; i++)
            {
                if (values[i] > bestValue)
                {
                    bestValue = values[i];
                    best = i;
                }
            }
            if (values[best] < 0f)
                break;
            rows.Add(new GridRow(values[best], scale, translationsX[best], translationsY[best]));
            double originX = translationsX[best];
            double originY = translationsY[best];
            for (int i = 0; i < values.Length; i++)
            {
                double dx = translationsX[i] - originX;
                double dy = translationsY[i] - originY;
                if (dx * dx + dy * dy < 144.0)
                    values[i] = -1f;
            }
        }
        return new GridSearch(rows.ToArray(), false);
    }

    private static bool TryInterval(bool[] valid, int pointCount, int point, int axisCount, out int first, out int end)
    {
        first = -1;
        end = -1;
        for (int axis = 0; axis < axisCount; axis++)
        {
            if (!valid[axis * pointCount + point])
                continue;
            if (first < 0)
                first = axis;
            end = axis + 1;
        }
        return first >= 0;
    }

    private static int SearchSortedRight(double[] values, double target)
    {
        int low = 0;
        int high = values.Length;
        while (low < high)
        {
            int mid = low + ((high - low) >> 1);
            if (values[mid] <= target)
                low = mid + 1;
            else
                high = mid;
        }
        return low;
    }

    private static int RintToInt32(double value)
    {
        double rounded = Math.Round(value, MidpointRounding.ToEven);
        if (rounded is > int.MaxValue or < int.MinValue)
            throw new OverflowException("grid-coordinate");
        return (int)rounded;
    }

    public static double Median(ReadOnlySpan<double> values)
    {
        if (values.Length == 0)
            throw new ArgumentException("quantile-empty");
        var sorted = values.ToArray();
        Array.Sort(sorted);
        double virtualIndex = (sorted.Length - 1) * 0.5;
        int previous = (int)Math.Floor(virtualIndex);
        double gamma = virtualIndex - previous;
        if (gamma == 0)
            return sorted[previous];
        double left = sorted[previous];
        double right = sorted[previous + 1];
        double difference = right - left;
        double interpolated = left + difference * gamma;
        if (gamma >= 0.5)
            interpolated = right - difference * (1 - gamma);
        return interpolated;
    }

    public static double[] Arange(double start, double stop, double step)
    {
        if (step == 0 || double.IsNaN(step) || double.IsNaN(start) || double.IsNaN(stop))
            throw new ArgumentException("arange");
        double length = Math.Ceiling((stop - start) / step);
        if (length <= 0 || double.IsInfinity(length))
            return Array.Empty<double>();
        int count = checked((int)length);
        double adjusted = (start + step) - start;
        var values = new double[count];
        for (int i = 0; i < count; i++)
            values[i] = start + i * adjusted;
        return values;
    }

    private static int[] LinspaceIndex(int start, int stop, int count)
    {
        if (count == 1)
            return new[] { start };
        var indexes = new int[count];
        double step = (stop - (double)start) / (count - 1);
        for (int i = 0; i < count - 1; i++)
            indexes[i] = (int)(start + i * step);
        indexes[count - 1] = stop;
        return indexes;
    }

    private static SamplePoint[] ToFloat(List<(int X, int Y)> points)
    {
        var result = new SamplePoint[points.Count];
        for (int i = 0; i < points.Count; i++)
            result[i] = new SamplePoint(points[i].X, points[i].Y);
        return result;
    }
}
