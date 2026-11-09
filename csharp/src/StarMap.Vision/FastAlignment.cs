using System.Diagnostics;
using System.Runtime.InteropServices;
using StarMap.Core.Recognition;
using OpenCvSharp;

namespace StarMap.Vision;

public sealed record FastObservedChain(double[][] Points, double[][] Rays)
{
    public int Index { get; init; } = -1;
}
public sealed record FastPrepared(int Width, int Height, ushort[] Distance,
    float[][][] Chains, float[][][] Rays, double Pitch)
{
    internal StarMap.Core.Resources.SharedStructureData? Source { get; init; }
}
public sealed record FastPose(double Score, double Scale, double[] Translation, bool TranslationFloat32);
public sealed record FastProposalResult(IReadOnlyList<FastPose> Poses,
    Dictionary<string, object?> Diagnostics, Dictionary<string, object?> Trace);

/// <summary>Original sparse fast proposal stage. A proposal never grants fit authority.</summary>
public static class FastAlignment
{
    public static FastProposalResult Propose(Mat observedEdges,
        IReadOnlyList<FastObservedChain> observedChains, FastPrepared prepared,
        double[][]? prior = null, bool localOnly = false, Func<bool>? cancelled = null) =>
        Propose(observedEdges, () => observedChains, prepared, prior, localOnly, cancelled);

    internal static FastProposalResult Propose(Mat observedEdges, Func<IReadOnlyList<FastObservedChain>> observedChains,
        FastPrepared prepared, double[][]? prior, bool localOnly, Func<bool>? cancelled)
    {
        var watch = Stopwatch.StartNew();
        var trace = new Dictionary<string, object?>();
        FastProposalResult Fail(string reason) => new([], new() { ["reason"] = reason }, trace);
        if (cancelled?.Invoke() == true) return Fail("structure-work-obsolete");
        if (observedEdges.Empty()) return Fail("fast-observation-unavailable");
        if (observedEdges.Type() != MatType.CV_8UC1)
            throw new ArgumentException("Observed edges must be an 8-bit single-channel mask.", nameof(observedEdges));
        if (prior is not null) ValidatePrior(prior, localOnly);
        var edgeBytes = Geometry.Bytes(observedEdges);
        var points = SampleEdges(edgeBytes, observedEdges, GridSlice.FineEdgeLimit);
        trace["points"] = points;
        if (points.Length < 40)
            return new([], new() { ["reason"] = "fast-insufficient-edges", ["points"] = points.Length }, trace);
        var coarse = SampleEdges(edgeBytes, observedEdges, GridSlice.CoarseEdgeLimit);
        trace["coarse"] = coarse;
        var rows = new List<FastPose>();
        var searches = new List<Dictionary<string, object?>>();
        List<FastPose> Search(double scale, double[][]? seed)
        {
            var started = Stopwatch.StartNew();
            var found = GridSearch(coarse, prepared, scale, seed, cancelled);
            searches.Add(new() { ["scope"] = seed is null ? "global" : "local",
                ["elapsedMs"] = started.Elapsed.TotalMilliseconds,
                ["bestScore"] = found.Count > 0 ? found[0].Score : null });
            return found;
        }
        if (prior is not null)
        {
            rows = Search(prior[0][0], prior);
            if ((rows.Count == 0 || rows[0].Score < .50) && !localOnly)
                rows.AddRange(Search(prior[0][0], null));
        }
        else
        {
            var seeds = prepared.Chains.Length == 0 ? [] : ChainSeeds(observedChains(), prepared, cancelled);
            trace["chainSeeds"] = seeds;
            if (seeds.Count > 0)
            {
                var scales = seeds.Select(s => (float)s.Scale).ToArray();
                var translations = seeds.Select(s => s.Translation.Select(x => (float)x).ToArray()).ToArray();
                var scored = Scores(coarse, prepared, scales, translations);
                for (int i = 0; i < scored.Length; i++)
                    rows.Add(new(scored[i], scales[i], translations[i].Select(x => (double)x).ToArray(), true));
            }
            if (rows.Count == 0)
            {
                double pitch = Pitch(observedEdges);
                trace["pitch"] = pitch;
                if (pitch > 0 && prepared.Pitch > 0)
                {
                    double scale = pitch / prepared.Pitch;
                    if (scale >= .03 && scale <= 6) rows.AddRange(Search(scale, null));
                }
            }
        }
        if (cancelled?.Invoke() == true) return Fail("structure-work-obsolete");
        trace["rawCandidates"] = rows;
        var retained = Distinct(rows, maximum: 8);
        trace["retained"] = retained;
        var refined = new List<FastPose>();
        foreach (var row in retained)
        {
            if (cancelled?.Invoke() == true) return Fail("structure-work-obsolete");
            refined.Add(Refine(points, prepared, row, prior is not null));
        }
        if (cancelled?.Invoke() == true) return Fail("structure-work-obsolete");
        trace["allRefined"] = refined;
        refined = Distinct(refined, minimum: 12, maximum: 3);
        trace["refinedDistinct"] = refined;
        var diagnostics = new Dictionary<string, object?>
        {
            ["proposalMs"] = watch.Elapsed.TotalMilliseconds,
            ["points"] = points.Length, ["coarsePoints"] = coarse.Length,
            ["scaleLocked"] = prior is not null, ["candidateCount"] = refined.Count,
            ["scores"] = refined.Select(r => r.Score).ToArray(), ["searchStages"] = searches
        };
        if (refined.Count == 0 || refined[0].Score < .50)
        {
            diagnostics["reason"] = "fast-low-score";
            return new([], diagnostics, trace);
        }
        if (refined.Count > 1 && refined[0].Score - refined[1].Score < .06)
        {
            diagnostics["reason"] = "fast-ambiguous";
            return new([], diagnostics, trace);
        }
        diagnostics["reason"] = "fast-proposal";
        return new([refined[0]], diagnostics, trace);
    }

    private static void ValidatePrior(double[][] prior, bool localOnly)
    {
        if (prior.Length != 3 || prior.Any(row => row is null || row.Length != 3 || row.Any(x => !double.IsFinite(x))))
            throw new ArgumentException("Prior must be a finite 3x3 matrix.", nameof(prior));
        // Acknowledged hints use s1_pose_prior.isotropic_matrix. Verified group priors
        // use np.allclose in _try_alignment after composing two coordinate adapters.
        double scale = prior[0][0];
        double offDiagonal = localOnly ? 1e-10 : 1e-8;
        double diagonal = localOnly ? 1e-10 : 1e-8 + 1e-5 * Math.Abs(scale);
        if (scale <= 0 || Math.Abs(prior[1][1] - scale) > diagonal
            || Math.Abs(prior[0][1]) > offDiagonal || Math.Abs(prior[1][0]) > offDiagonal
            || (localOnly && (Math.Abs(prior[2][0]) > 1e-12 || Math.Abs(prior[2][1]) > 1e-12 || Math.Abs(prior[2][2] - 1) > 1e-12)))
            throw new ArgumentException("Prior must be a finite, axis-locked source-to-client transform.", nameof(prior));
    }

    public static float[][] SampleEdges(Mat edge, int limit) => SampleEdges(Geometry.Bytes(edge), edge, limit);

    private static float[][] SampleEdges(byte[] bytes, Mat edge, int limit) =>
        NumpyCompat.SampleEdges(bytes, edge.Rows, edge.Cols, limit).Select(p => new[] {p.X, p.Y}).ToArray();

    public static double Pitch(Mat edge)
    {
        int width = edge.Cols;
        if (width == 0) return 0;
        var signal = new double[width];
        var pixels = Geometry.Bytes(edge);
        int height = edge.Rows;
        for (int y = 0; y < height; y++)
            for (int x = 0; x < width; x++)
                if (pixels[y * width + x] != 0) signal[x]++;
        double mean = PairwiseSum(signal, 0, width) / width;
        for (int x = 0; x < width; x++) signal[x] -= mean;
        if (signal.Sum(x => x * x) < 1e-8) return 0;
        int size = 1; while (size <= 2 * width - 1) size <<= 1;
        using var padded = new Mat(1, size, MatType.CV_64FC1, Scalar.All(0));
        Marshal.Copy(signal, 0, padded.Data, signal.Length);
        using var spectrum = new Mat(); Cv2.Dft(padded, spectrum, DftFlags.ComplexOutput);
        using var power = new Mat(1, size, MatType.CV_64FC2);
        for (int i = 0; i < size; i++)
        {
            var z = spectrum.At<Vec2d>(0, i);
            power.Set(0, i, new Vec2d(z.Item0 * z.Item0 + z.Item1 * z.Item1, 0));
        }
        using var inverse = new Mat(); Cv2.Dft(power, inverse, DftFlags.Inverse | DftFlags.Scale | DftFlags.RealOutput);
        var corr = new double[size]; Marshal.Copy(inverse.Data, corr, 0, size);
        var peaks = new List<int>();
        for (int i = 12; i < width / 2 - 1; i++)
            if (corr[i] > corr[i - 1] && corr[i] > corr[i + 1] && corr[i] > corr[0] * .12) peaks.Add(i);
        if (peaks.Count == 0) return 0;
        int best = peaks[0]; foreach (int i in peaks) if (corr[i] > corr[best]) best = i;
        foreach (int i in peaks)
            if (i < best && corr[i] >= .8 * corr[best] && Math.Abs(best / (double)i - Math.Round(best / (double)i)) < .15) { best = i; break; }
        return best;
    }

    public static List<FastPose> ChainSeeds(IReadOnlyList<FastObservedChain> query, FastPrepared prepared, Func<bool>? cancelled = null)
    {
        var rows = new List<FastPose>();
        if (prepared.Chains.Length == 0) return rows;
        Span<float> source = stackalloc float[4];
        Span<double> delta = stackalloc double[4];
        foreach (var node in query.Take(160))
        {
            if (cancelled?.Invoke() == true) return [];
            for (int arm = 0; arm < 2; arm++)
            for (int axis = 0; axis < 2; axis++)
                delta[arm * 2 + axis] = node.Points[arm * 2][axis] - node.Points[1][axis];
            for (int i = 0; i < prepared.Chains.Length; i++)
            {
                bool orientation = true;
                for (int arm = 0; arm < 2; arm++)
                    if (node.Rays[arm][0] * prepared.Rays[i][arm][0] + node.Rays[arm][1] * prepared.Rays[i][arm][1] < .95) orientation = false;
                if (!orientation) continue;
                float denominator = 0; double numerator = 0;
                for (int arm = 0; arm < 2; arm++) for (int axis = 0; axis < 2; axis++)
                {
                    int k = arm * 2 + axis, endpoint = arm == 0 ? 0 : 2;
                    source[k] = prepared.Chains[i][endpoint][axis] - prepared.Chains[i][1][axis];
                    denominator += source[k] * source[k]; numerator += delta[k] * source[k];
                }
                double scale = numerator / Math.Max(denominator, 1e-8f), error = 0;
                for (int arm = 0; arm < 2; arm++)
                {
                    double dx = delta[arm * 2] - scale * source[arm * 2], dy = delta[arm * 2 + 1] - scale * source[arm * 2 + 1];
                    error = Math.Max(error, Math.Sqrt(dx * dx + dy * dy));
                }
                if (scale >= .03 && scale <= 6 && error <= 4)
                    rows.Add(new(0, scale, [node.Points[1][0] - scale * prepared.Chains[i][1][0], node.Points[1][1] - scale * prepared.Chains[i][1][1]], false));
            }
            if (rows.Count > 4096) return [];
        }
        return rows;
    }

    public static float[] Scores(float[][] points, FastPrepared prepared, float[] scales, float[][] translations)
    {
        var answer = new float[translations.Length];
        for (int j = 0; j < translations.Length; j++)
        {
            float scale = scales.Length == 1 ? scales[0] : scales[j]; long hit3 = 0, hit6 = 0;
            foreach (var point in points)
            {
                int x = (int)Math.Round((point[0] - translations[j][0]) / scale), y = (int)Math.Round((point[1] - translations[j][1]) / scale);
                if (x < 0 || y < 0 || x >= prepared.Width || y >= prepared.Height) continue;
                float distance = (prepared.Distance[y * prepared.Width + x] * scale) * .25f;
                if (distance <= 3) hit3++; if (distance <= 6) hit6++;
            }
            answer[j] = (float)((2 * hit3 + hit6) / (3.0 * points.Length));
        }
        return answer;
    }

    public static List<FastPose> Distinct(IEnumerable<FastPose> rows, double minimum = 12, int maximum = 8)
    {
        var selected = new List<FastPose>();
        foreach (var row in rows.OrderByDescending(r => r.Score))
        {
            bool distinct = selected.All(old =>
            {
                double dx = row.Translation[0] - old.Translation[0], dy = row.Translation[1] - old.Translation[1], norm;
                if (row.TranslationFloat32 && old.TranslationFloat32)
                {
                    float x = (float)dx, y = (float)dy, squared = x * x + y * y; norm = (float)Math.Sqrt(squared);
                }
                else norm = Math.Sqrt(dx * dx + dy * dy);
                return norm >= minimum || Math.Abs(row.Scale / old.Scale - 1) > .025;
            });
            if (distinct) { selected.Add(row); if (selected.Count == maximum) break; }
        }
        return selected;
    }

    public static FastPose Refine(float[][] points, FastPrepared prepared, FastPose input, bool lockScale)
    {
        float cx = 0, cy = 0; foreach (var p in points) { cx += p[0]; cy += p[1]; } cx /= points.Length; cy /= points.Length;
        bool anchorFloat = input.TranslationFloat32;
        double ax, ay;
        if (anchorFloat)
        {
            ax = (float)((cx - (float)input.Translation[0]) / (float)input.Scale);
            ay = (float)((cy - (float)input.Translation[1]) / (float)input.Scale);
        }
        else { ax = (cx - input.Translation[0]) / input.Scale; ay = (cy - input.Translation[1]) / input.Scale; }
        float tx = (float)input.Translation[0], ty = (float)input.Translation[1]; double currentScale = input.Scale;
        double best = Loss(points, prepared, (float)currentScale, tx, ty);
        foreach (var step in new[] { (dt: 3.0, ds: .01), (dt: 1.0, ds: .003), (dt: .25, ds: .001), (dt: .125, ds: .0003) })
        {
            double[] scales = lockScale ? [currentScale] : [currentScale * (1 - step.ds), currentScale, currentScale * (1 + step.ds)];
            var candidates = new List<(double Scale, float X, float Y, double Loss)>();
            foreach (double scale in scales)
            {
                double ox, oy;
                if (lockScale) { ox = tx; oy = ty; }
                else if (anchorFloat) { ox = (float)(cx - (float)((float)ax * (float)scale)); oy = (float)(cy - (float)((float)ay * (float)scale)); }
                else { ox = cx - ax * scale; oy = cy - ay * scale; }
                foreach (double dx in new[] { -step.dt, 0, step.dt }) foreach (double dy in new[] { -step.dt, 0, step.dt })
                {
                    float x = (float)(ox + dx), y = (float)(oy + dy);
                    candidates.Add((scale, x, y, Loss(points, prepared, (float)scale, x, y)));
                }
            }
            var winner = candidates[0]; foreach (var candidate in candidates) if (candidate.Loss < winner.Loss) winner = candidate;
            if (winner.Loss <= best)
            {
                best = winner.Loss; currentScale = winner.Scale; tx = winner.X; ty = winner.Y;
                ax = (float)((cx - tx) / (float)currentScale); ay = (float)((cy - ty) / (float)currentScale); anchorFloat = true;
            }
        }
        float confidence = Scores(points, prepared, [(float)currentScale], [[tx, ty]])[0];
        return new(confidence, currentScale, [tx, ty], true);
    }

    static double Loss(float[][] points, FastPrepared prepared, float scale, float tx, float ty)
    {
        var costs = new double[points.Length];
        for (int i = 0; i < points.Length; i++)
        {
            float x = (points[i][0] - tx) / scale, y = (points[i][1] - ty) / scale;
            double distance = 12;
            if (x >= 0 && y >= 0 && x < prepared.Width - 1 && y < prepared.Height - 1)
            {
                int x0 = (int)Math.Floor(x), y0 = (int)Math.Floor(y); double fx = (double)x - x0, fy = (double)y - y0;
                int row = y0 * prepared.Width;
                distance = ((1 - fx) * (1 - fy) * prepared.Distance[row + x0] + fx * (1 - fy) * prepared.Distance[row + x0 + 1]
                    + (1 - fx) * fy * prepared.Distance[row + prepared.Width + x0] + fx * fy * prepared.Distance[row + prepared.Width + x0 + 1]) * scale * .25;
                distance = Math.Min(distance, 12);
            }
            costs[i] = distance <= 1.5 ? .5 * distance * distance : 1.5 * (distance - .75);
        }
        return PairwiseSum(costs, 0, costs.Length) / costs.Length;
    }

    // NumPy's contiguous float64 reduction groups eight accumulators and
    // recursively partitions larger blocks; keep tie-breaking losses stable.
    static double PairwiseSum(double[] values, int start, int count)
    {
        if (count < 8) { double sum = -0.0; for (int i = 0; i < count; i++) sum += values[start + i]; return sum; }
        if (count <= 128)
        {
            double[] r = new double[8]; Array.Copy(values, start, r, 0, 8); int i = 8;
            for (; i < count - count % 8; i += 8) for (int k = 0; k < 8; k++) r[k] += values[start + i + k];
            double sum = ((r[0] + r[1]) + (r[2] + r[3])) + ((r[4] + r[5]) + (r[6] + r[7]));
            for (; i < count; i++) sum += values[start + i]; return sum;
        }
        int middle = count / 2; middle -= middle % 8;
        return PairwiseSum(values, start, middle) + PairwiseSum(values, start + middle, count - middle);
    }

    static List<FastPose> GridSearch(float[][] points, FastPrepared prepared, double scale, double[][]? prior, Func<bool>? cancelled)
    {
        var samples = points.Select(p => new SamplePoint(p[0], p[1])).ToArray();
        var grid = NumpyCompat.Grid(samples, prepared.Distance, prepared.Height, prepared.Width, scale,
            prior?[0][2], prior?[1][2], cancelled);
        return grid.Rows.Select(r => new FastPose(r.Score, r.Scale, [r.X, r.Y], false)).ToList();
    }
}
