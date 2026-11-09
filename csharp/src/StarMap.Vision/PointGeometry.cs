using OpenCvSharp;
using System.Text.Json.Nodes;

namespace StarMap.Vision;

/// <summary>Measured native-chain correspondences and disjoint H0/H1 point proof.</summary>
public static class PointGeometry
{
    private static Point2d Sub(Point2d a, Point2d b) => new(a.X - b.X, a.Y - b.Y);
    private static Point2d Add(Point2d a, Point2d b) => new(a.X + b.X, a.Y + b.Y);
    private static Point2d Mul(Point2d a, double s) => new(a.X * s, a.Y * s);
    private static double Norm(Point2d a) => Numerics.Norm(a);
    private static double Value(JsonObject obj, string key) => Numerics.Scalar(obj[key]!);
    private static bool Success(JsonObject obj) => obj["success"]?.GetValue<bool>() == true;
    private static JsonObject Fail(string reason, int? count = null)
    {
        var result = new JsonObject { ["success"] = false, ["reason"] = reason };
        if (count is not null) result["acceptedPointCount"] = count.Value;
        return result;
    }

    // The real frame is required because observed-chain extraction may need to
    // compute current supported edges lazily. Query is never used as a frame.
    public static JsonObject NativePointGeometry(Mat frame, FastPrepared prepared, double[][] matrix,
        Observation context, bool lockScale)
    {
        var query = Geometry.ObservedChains(frame, context);
        double scale = matrix[0][0];
        var pairs = new List<(int Q, int S)>();
        var qcount = new int[query.Count]; var scount = new int[prepared.Chains.Length];
        for (int qi = 0; qi < query.Count; qi++)
        {
            var node = query[qi];
            var center = new Point2d(node.Points[1][0], node.Points[1][1]);
            var lengths = new[] { Norm(new Point2d(node.Points[0][0] - center.X, node.Points[0][1] - center.Y)),
                                  Norm(new Point2d(node.Points[2][0] - center.X, node.Points[2][1] - center.Y)) };
            for (int si = 0; si < prepared.Chains.Length; si++)
            {
                var source = prepared.Chains[si];
                // prepared.chains is float32 in the original NPZ. NumPy keeps
                // its scalar multiplication and arm norm in float32 here.
                var sourceCenter = new Point2d(source[1][0] * (float)scale + matrix[0][2],
                                               source[1][1] * (float)scale + matrix[1][2]);
                var d = Sub(sourceCenter, center);
                if (Numerics.Dot(d, d) > 48 * 48) continue;
                bool oriented = true, adjacent = true;
                for (int arm = 0; arm < 2; arm++)
                {
                    if (prepared.Rays[si][arm][0] * node.Rays[arm][0] + prepared.Rays[si][arm][1] * node.Rays[arm][1] < .95)
                        oriented = false;
                    int endpoint = arm == 0 ? 0 : 2;
                    float dx = (source[endpoint][0] - source[1][0]) * (float)scale;
                    float dy = (source[endpoint][1] - source[1][1]) * (float)scale;
                    float sourceLength = (float)Math.Sqrt(dx * dx + dy * dy);
                    if (Math.Abs(sourceLength - lengths[arm]) > 3) adjacent = false;
                }
                if (oriented && adjacent) { pairs.Add((qi, si)); qcount[qi]++; scount[si]++; }
            }
        }
        var unique = pairs.Where(p => qcount[p.Q] == 1 && scount[p.S] == 1).ToArray();
        if (unique.Length < 6) return new JsonObject { ["success"] = false, ["reason"] = "native-adjacent-pairs-insufficient", ["support"] = unique.Length };
        var observed = unique.Select(p => new Point2d(query[p.Q].Points[1][0], query[p.Q].Points[1][1])).ToArray();
        var native = unique.Select(p => new Point2d(prepared.Chains[p.S][1][0], prepared.Chains[p.S][1][1])).ToArray();
        var result = FitAxisLockedPointGeometry(native, observed, matrix, observed, frame.Size(),
            new double[native.Length], new double[native.Length], query.Count,
            maximumRelativeScaleCorrection: lockScale ? 0 : .08);
        string model = result["h0"] is JsonObject h0 && Success(h0) ? "h0" : "h1";
        result["structureModel"] = result[model] is JsonObject selected && Success(selected) ? model : null;
        result["nativeChainSupport"] = unique.Length;
        return result;
    }

    /// <summary>Port of nightmare_candidate.fit_axis_locked_point_geometry, including its temporal split option.</summary>
    public static JsonObject FitAxisLockedPointGeometry(Point2d[] sourcePoints, Point2d[] observedPoints,
        double[][] matrix, Point2d[] localPoints, Size queryShape, double[] forwardBackward,
        double[] flowErrorValues, int initialCount, JsonNode? component = null,
        double evaluationScale = 1, double maximumRelativeScaleCorrection = .08,
        bool distributeTemporalSupport = false)
    {
        var timer = System.Diagnostics.Stopwatch.StartNew();
        int count = localPoints.Length;
        if (sourcePoints.Length != count || observedPoints.Length != count || forwardBackward.Length != count || flowErrorValues.Length != count)
            throw new ArgumentException("Point geometry array lengths differ");
        if (count == 0) return Fail("front-local-support-validation-split-failed", 0);
        var baseline = sourcePoints.Select(p => new Point2d(
            p.X * matrix[0][0] + p.Y * matrix[0][1], p.X * matrix[1][0] + p.Y * matrix[1][1])).ToArray();
        var supportMask = localPoints.Select(p =>
            (((int)Math.Floor(p.X / Math.Max(1, queryShape.Width / 4.0)) +
              (int)Math.Floor(p.Y / Math.Max(1, queryShape.Height / 4.0))) % 2) == 0).ToArray();
        if (supportMask.Count(x => x) < 3 || supportMask.Count(x => !x) < 3)
        {
            System.Array.Fill(supportMask, false);
            var order = Enumerable.Range(0, count).OrderBy(i => localPoints[i].Y).ThenBy(i => localPoints[i].X).ToArray();
            for (int i = 0; i < order.Length; i += 2) supportMask[order[i]] = true;
        }
        var supportGeometry = baseline.Where((_, i) => supportMask[i]).ToArray();
        var initialSingular = SingularValues(supportGeometry);
        var initialSpread = Spread(supportGeometry);
        if (distributeTemporalSupport && count >= 6 && (initialSingular.Length < 2 ||
            initialSingular[^1] / Math.Max(initialSingular[0], 1e-9) < .10 || Math.Min(initialSpread.X, initialSpread.Y) < 24))
        {
            var chosen = new List<int> { 0 };
            var distances = baseline.Select(p => Norm(Sub(p, baseline[0]))).ToArray();
            for (int j = 0; j < count / 2 - 1; j++)
            {
                foreach (int index in chosen) distances[index] = -1;
                int best = 0; for (int i = 1; i < count; i++) if (distances[i] > distances[best]) best = i;
                chosen.Add(best);
                for (int i = 0; i < count; i++) distances[i] = Math.Min(distances[i], Norm(Sub(baseline[i], baseline[best])));
            }
            System.Array.Fill(supportMask, false); foreach (int index in chosen) supportMask[index] = true;
        }
        var support = Enumerable.Range(0, count).Where(i => supportMask[i]).ToArray();
        var validation = Enumerable.Range(0, count).Where(i => !supportMask[i]).ToArray();
        if (support.Length < 3 || validation.Length < 3) return Fail("front-local-support-validation-split-failed", count);

        using var supportBaselineMat = new Mat(support.Length, 2, MatType.CV_32FC1);
        using var supportObservedMat = new Mat(support.Length, 2, MatType.CV_32FC1);
        for (int i = 0; i < support.Length; i++)
        {
            int index = support[i];
            supportBaselineMat.Set(i, 0, (float)baseline[index].X); supportBaselineMat.Set(i, 1, (float)baseline[index].Y);
            supportObservedMat.Set(i, 0, (float)observedPoints[index].X); supportObservedMat.Set(i, 1, (float)observedPoints[index].Y);
        }
        using var inliers = new Mat();
        using var ransac = Cv2.EstimateAffinePartial2D(supportBaselineMat, supportObservedMat, inliers,
            RobustEstimationAlgorithms.RANSAC, 3, 1200, .995, 10);
        if (ransac is null || ransac.Empty() || inliers.Empty()) return Fail("front-local-support-ransac-failed", count);
        var fitIndices = support.Where((_, i) => inliers.At<byte>(i, 0) > 0).ToArray();
        if (fitIndices.Length < 3) return Fail("front-local-support-inliers-insufficient", count);
        var fitBaseline = fitIndices.Select(i => baseline[i]).ToArray();
        var fitObserved = fitIndices.Select(i => observedPoints[i]).ToArray();
        var singular = SingularValues(fitBaseline);
        double condition = singular.Length >= 2 ? singular[^1] / Math.Max(singular[0], 1e-9) : 0;
        var spread = Spread(fitBaseline);
        bool scaleObservable = condition >= .10 && Math.Min(spread.X, spread.Y) >= 24;
        var (h0Factor, h0Translation) = RobustFit(fitBaseline, fitObserved, 1);
        var (h1Factor, h1Translation) = RobustFit(fitBaseline, fitObserved, null);
        if (!double.IsFinite(h0Factor) || !double.IsFinite(h1Factor) || h1Factor <= 0) return Fail("front-local-fit-nonfinite");
        var h0Residual = Residuals(validation.Select(i => Add(Mul(baseline[i], h0Factor), h0Translation)).ToArray(), validation.Select(i => observedPoints[i]).ToArray());
        var h1Residual = Residuals(validation.Select(i => Add(Mul(baseline[i], h1Factor), h1Translation)).ToArray(), validation.Select(i => observedPoints[i]).ToArray());
        double h0Rms = Value(h0Residual, "rmsPixels"), h1Rms = Value(h1Residual, "rmsPixels");
        double improvement = double.IsFinite(h0Rms) && double.IsFinite(h1Rms) ? (h0Rms - h1Rms) / Math.Max(h0Rms, 1) : 0;
        double scaleError = h1Factor - 1;
        int distinct = observedPoints.Select(p => (Math.Round(p.X / 4, MidpointRounding.ToEven), Math.Round(p.Y / 4, MidpointRounding.ToEven))).Distinct().Count();
        double medianForwardBackward = Numerics.Median(forwardBackward), medianFlowError = Numerics.Median(flowErrorValues);
        double ambiguity = Math.Min(1, .45 * medianForwardBackward / 2.5 + .25 * medianFlowError / 100 + .30 * (1 - distinct / (double)Math.Max(1, count)));
        bool ResidualValid(JsonObject residual) => Value(residual, "medianPixels") <= 3 && Value(residual, "p90Pixels") <= 8 && Value(residual, "maxPixels") <= 20;
        bool h0Valid = ResidualValid(h0Residual) && ambiguity <= .45;
        bool h1Valid = scaleObservable && Math.Abs(scaleError) <= maximumRelativeScaleCorrection && Math.Abs(scaleError) >= .003 &&
            ResidualValid(h1Residual) && improvement >= .20 && ambiguity <= .45;
        var h0 = Model(h0Factor, h0Translation, h0Residual, h0Valid, matrix);
        var h1 = Model(h1Factor, h1Translation, h1Residual, h1Valid, matrix);
        var selected = h1Valid ? h1 : h0Valid ? h0 : null;
        return new JsonObject
        {
            ["success"] = selected is not null,
            ["reason"] = h1Valid ? "front-local-scale-plus-translation" : h0Valid ? "front-local-translation-only" : "front-local-held-out-geometry-failed",
            ["mode"] = h1Valid ? "scale-plus-translation" : h0Valid ? "translation-only" : null,
            ["selected"] = selected?.DeepClone(), ["h0"] = h0, ["h1"] = h1,
            ["sameVisibleComponent"] = true, ["visibleComponent"] = component?.DeepClone(),
            ["initialPointCount"] = initialCount, ["acceptedPointCount"] = count,
            ["supportPointCount"] = fitBaseline.Length, ["validationPointCount"] = validation.Length,
            ["geometryCondition"] = condition, ["scaleObservable"] = scaleObservable,
            ["supportSpreadPixels"] = new JsonArray(spread.X, spread.Y),
            ["matchAcceptanceRatio"] = count / (double)Math.Max(initialCount, 1), ["matchAmbiguity"] = ambiguity,
            ["medianForwardBackwardPixels"] = medianForwardBackward, ["medianPatchError"] = medianFlowError,
            ["h0Validation"] = h0Residual, ["h1Validation"] = h1Residual,
            ["scaleFactor"] = h1Factor, ["scaleError"] = scaleError, ["improvement"] = improvement,
            ["evaluationScale"] = evaluationScale, ["preprocessAndFitMs"] = timer.Elapsed.TotalMilliseconds
        };
    }

    private static Point2d Spread(Point2d[] points) => points.Length == 0 ? new() :
        new(points.Max(p => p.X) - points.Min(p => p.X), points.Max(p => p.Y) - points.Min(p => p.Y));

    private static double[] SingularValues(Point2d[] points)
    {
        if (points.Length == 0) return System.Array.Empty<double>();
        var mean = new Point2d(points.Average(p => p.X), points.Average(p => p.Y));
        using var centered = new Mat(points.Length, 2, MatType.CV_64FC1);
        for (int i = 0; i < points.Length; i++) { centered.Set(i, 0, points[i].X - mean.X); centered.Set(i, 1, points[i].Y - mean.Y); }
        using var singular = new Mat(); using var u = new Mat(); using var vt = new Mat();
        Cv2.SVDecomp(centered, singular, u, vt, SVD.Flags.NoUV);
        return Enumerable.Range(0, (int)singular.Total()).Select(i => singular.At<double>(i, 0)).ToArray();
    }

    private static (double Factor, Point2d Translation) RobustFit(Point2d[] baseline, Point2d[] observed, double? fixedFactor)
    {
        var weights = Enumerable.Repeat(1.0, baseline.Length).ToArray();
        double factor = fixedFactor ?? 1;
        var initial = observed.Select((p, i) => Sub(p, Mul(baseline[i], factor))).ToArray();
        var translation = new Point2d(Numerics.Median(initial.Select(p => p.X)), Numerics.Median(initial.Select(p => p.Y)));
        for (int iteration = 0; iteration < 6; iteration++)
        {
            double weightSum = Math.Max(weights.Sum(), 1e-9);
            if (fixedFactor is null)
            {
                var weightedBaseline = new Point2d(baseline.Select((p, i) => p.X * weights[i]).Sum() / weightSum, baseline.Select((p, i) => p.Y * weights[i]).Sum() / weightSum);
                var weightedObserved = new Point2d(observed.Select((p, i) => p.X * weights[i]).Sum() / weightSum, observed.Select((p, i) => p.Y * weights[i]).Sum() / weightSum);
                double numerator = 0, denominator = 0;
                for (int i = 0; i < baseline.Length; i++)
                {
                    var b = Sub(baseline[i], weightedBaseline); var o = Sub(observed[i], weightedObserved);
                    numerator += Numerics.Dot(b, o) * weights[i]; denominator += Numerics.Dot(b, b) * weights[i];
                }
                factor = numerator / Math.Max(denominator, 1e-9);
            }
            var delta = observed.Select((p, i) => Sub(p, Mul(baseline[i], factor))).ToArray();
            translation = new Point2d(delta.Select((p, i) => p.X * weights[i]).Sum() / weightSum, delta.Select((p, i) => p.Y * weights[i]).Sum() / weightSum);
            var errors = observed.Select((p, i) => Norm(Sub(p, Add(Mul(baseline[i], factor), translation)))).ToArray();
            double centerError = Numerics.Median(errors), sigma = Math.Max(.5, 1.4826 * Numerics.Median(errors.Select(e => Math.Abs(e - centerError))));
            for (int i = 0; i < weights.Length; i++) weights[i] = Math.Min(1, 2.5 * sigma / Math.Max(errors[i], 1e-6));
        }
        return (factor, translation);
    }

    private static JsonObject Residuals(Point2d[] predicted, Point2d[] observed)
    {
        var values = predicted.Select((p, i) => Norm(Sub(observed[i], p))).ToArray();
        if (values.Length == 0) throw new InvalidOperationException("Point holdout is empty");
        var ordered = values.Order().ToArray(); double index = (ordered.Length - 1) * .9;
        int lower = (int)Math.Floor(index), upper = (int)Math.Ceiling(index);
        double percentile = ordered[lower] + (ordered[upper] - ordered[lower]) * (index - lower);
        return new JsonObject { ["count"] = values.Length, ["medianPixels"] = Numerics.Median(values), ["p90Pixels"] = percentile,
            ["maxPixels"] = ordered[^1], ["rmsPixels"] = Math.Sqrt(values.Average(v => v * v)),
            ["withinTwoPixels"] = values.Count(v => v <= 2) / (double)values.Length, ["withinThreePixels"] = values.Count(v => v <= 3) / (double)values.Length };
    }

    private static JsonObject Model(double factor, Point2d translation, JsonObject residual, bool valid, double[][] baseline)
    {
        var proof = new JsonObject { ["verified"] = valid, ["verificationSource"] = "front-local-held-out-geometry",
            ["validationBasis"] = "disjoint-current-frame-structure-points", ["validationPointCount"] = residual["count"]!.DeepClone() };
        foreach (string field in new[] { "medianPixels", "p90Pixels", "maxPixels", "rmsPixels", "withinTwoPixels", "withinThreePixels" }) proof[field] = residual[field]!.DeepClone();
        return new JsonObject { ["success"] = valid, ["scaleFactor"] = factor, ["scaleError"] = factor - 1,
            ["translation"] = new JsonArray(translation.X, translation.Y),
            ["matrixSourceToClient"] = new JsonArray(new JsonArray(baseline[0][0] * factor, baseline[0][1] * factor, translation.X),
                new JsonArray(baseline[1][0] * factor, baseline[1][1] * factor, translation.Y), new JsonArray(0, 0, 1)),
            ["selectedRouteCurrentFrameResidual"] = proof };
    }
}
