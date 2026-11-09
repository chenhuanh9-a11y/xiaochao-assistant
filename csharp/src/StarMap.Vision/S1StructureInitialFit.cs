using System.Diagnostics;
using System.Text.Json.Nodes;
using OpenCvSharp;
using static StarMap.Vision.S1StructureObservations;

namespace StarMap.Vision;

/// <summary>Unknown-scale initial fit. Seeds come exclusively from current measured/marker-excluded chains.</summary>
internal static class S1StructureInitialFit
{
    private sealed record Chain(FastObservedChain Geometry, bool Measured);
    private sealed record Seed(int Query, int Source, double Scale, double[] Translation);
    private sealed record Candidate(double[][] Matrix, JsonObject Geometry, (int Query, int Source)[] Pairs,
        Point2d[] SourcePoints, Point2d[] QueryPoints, HashSet<string>? WallIdentity = null, bool Marker = false);

    public static JsonObject Evaluate(Mat frame, Observation context, StructureGeometry reference, Func<bool> cancelled)
    {
        var timer = Stopwatch.StartNew();
        var query = Geometry.ObservedChains(frame, context);
        var marker = MarkerProposals(frame, context, query);
        var allQuery = query.Concat(marker).ToArray();
        var sourceNodes = Geometry.Contour(reference.Clean); var source = new List<Chain>();
        var known = reference.KnownBoundary is null ? null : Geometry.Bytes(reference.KnownBoundary);
        for (int i = 0; i < sourceNodes.Count; i++)
        {
            var node = sourceNodes[i]; int[] ids = [node.Previous, i, node.Next];
            if (ids.Any(j => !sourceNodes[j].Valid)) continue;
            var points = ids.Select(j => sourceNodes[j].Point).ToArray(); bool measured = true;
            if (known is not null)
                foreach (var endpoint in new[] { points[0], points[2] })
                {
                    var delta = endpoint - points[1]; int intervals = (int)Math.Ceiling(Numerics.Norm(delta));
                    for (int j = 0; j <= intervals; j++)
                    {
                        var p = points[1] + delta*(j == intervals ? 1d : j*(1d/intervals));
                        if (known[Numerics.Rint(p.Y)*reference.Clean.Cols+Numerics.Rint(p.X)] == 0) { measured = false; break; }
                    }
                    if (!measured) break;
                }
            source.Add(new(new(points.Select(p => new[] { p.X, p.Y }).ToArray(), node.Rays.Select(p => new[] { p.X, p.Y }).ToArray()) { Index = i }, measured));
        }
        var candidateDiagnostics = new JsonArray(); var pointRejections = new JsonArray(); var stages = new JsonArray();
        var diagnostic = new JsonObject { ["queryChains"] = query.Count, ["markerOccludedProposalChains"] = marker.Count, ["sourceChains"] = source.Count,
            ["candidates"] = candidateDiagnostics, ["pointRejections"] = pointRejections, ["stages"] = stages, ["seedBudget"] = 1024, ["validationBudget"] = 8,
            ["poseIndexProvider"] = new JsonObject { ["attempted"] = false, ["reason"] = "pose-index-disabled", ["candidateCount"] = 0 } };
        JsonObject Finish(bool success, string reason)
        {
            diagnostic["elapsedMs"] = timer.Elapsed.TotalMilliseconds;
            return new() { ["success"] = success, ["reason"] = reason, ["diagnostics"] = diagnostic };
        }
        if (cancelled()) return Finish(false, "structure-work-obsolete");
        if (allQuery.Length == 0) return Finish(false, "initial-structure-supported-chains-insufficient");
        var seeds = new List<Seed>();
        for (int qi = 0; qi < allQuery.Length; qi++)
        {
            if (cancelled()) return Finish(false, "structure-work-obsolete");
            var q = allQuery[qi];
            for (int si = 0; si < source.Count; si++)
            {
                var s = source[si].Geometry;
                if (Enumerable.Range(0, 2).Any(arm => q.Rays[arm][0]*s.Rays[arm][0]+q.Rays[arm][1]*s.Rays[arm][1] < .95)) continue;
                double numerator = 0, denominator = 0;
                for (int arm = 0; arm < 2; arm++) for (int axis = 0; axis < 2; axis++)
                {
                    int endpoint = arm == 0 ? 0 : 2;
                    double sd = s.Points[endpoint][axis]-s.Points[1][axis], qd = q.Points[endpoint][axis]-q.Points[1][axis];
                    numerator += qd*sd; denominator += sd*sd;
                }
                double scale = numerator/denominator;
                if (!(scale > 0)) continue;
                bool valid = true;
                for (int arm = 0; arm < 2; arm++)
                {
                    int endpoint = arm == 0 ? 0 : 2;
                    double x = q.Points[endpoint][0]-q.Points[1][0]-scale*(s.Points[endpoint][0]-s.Points[1][0]);
                    double y = q.Points[endpoint][1]-q.Points[1][1]-scale*(s.Points[endpoint][1]-s.Points[1][1]);
                    if (Math.Sqrt(x*x+y*y) > 3) valid = false;
                }
                if (!valid) continue;
                seeds.Add(new(qi, si, scale, [q.Points[1][0]-scale*s.Points[1][0], q.Points[1][1]-scale*s.Points[1][1]]));
                if (seeds.Count > 1024) { diagnostic["seedCount"] = seeds.Count; return Finish(false, "initial-structure-descriptor-budget-unresolved"); }
            }
        }
        diagnostic["seedCount"] = seeds.Count;
        stages.Add(new JsonObject { ["stage"] = "chain-descriptors", ["elapsedMs"] = timer.Elapsed.TotalMilliseconds });
        if (seeds.Count == 0) return Finish(false, "initial-structure-no-chain-ratios");
        var pointSeeds = seeds.Where(seed => seed.Query < query.Count).ToArray();
        diagnostic["measuredSeedCount"] = pointSeeds.Length;
        var seen = new HashSet<string>(); var maximal = new List<HashSet<(int Query, int Source)>>();
        var consensusStarted = Stopwatch.GetTimestamp();
        foreach (var seed in pointSeeds)
        {
            if (cancelled()) return Finish(false, "structure-work-obsolete");
            var pairs = new HashSet<(int Query, int Source)>();
            foreach (var other in pointSeeds)
                if (Explains(source[other.Source].Geometry, query[other.Query], seed.Scale, seed.Translation)) pairs.Add((other.Query, other.Source));
            var unique = pairs.Where(pair => pairs.Count(p => p.Query == pair.Query) == 1 && pairs.Count(p => p.Source == pair.Source) == 1).OrderBy(p => p.Query).ThenBy(p => p.Source).ToArray();
            string key = string.Join(';', unique.Select(p => $"{p.Query},{p.Source}"));
            if (unique.Length < 6 || !seen.Add(key)) continue;
            var set = unique.ToHashSet(); if (maximal.Any(old => set.IsSubsetOf(old))) continue;
            maximal.RemoveAll(old => old.IsProperSubsetOf(set)); maximal.Add(set);
        }
        diagnostic["consensusCount"] = seen.Count;
        bool changed = true;
        while (changed)
        {
            changed = false;
            for (int i = 0; i < maximal.Count && !changed; i++) for (int j = i+1; j < maximal.Count; j++)
            {
                var common = maximal[i].Intersect(maximal[j]).ToArray(); if (common.Length < 6) continue;
                var positions = common.Select(p => Point(query[p.Query].Points[1])).ToArray();
                var union = maximal[i].Union(maximal[j]).ToHashSet();
                if (Spread(positions) < 24 || union.Select(p => p.Query).Distinct().Count() != union.Count || union.Select(p => p.Source).Distinct().Count() != union.Count) continue;
                maximal[i] = union; maximal.RemoveAt(j); changed = true; break;
            }
        }
        diagnostic["maximalCorrespondenceSets"] = maximal.Count;
        var candidates = new List<Candidate>();
        foreach (var pairs in maximal.OrderByDescending(p => p.Count).ThenBy(p => string.Join(';', p.OrderBy(v => v.Query).ThenBy(v => v.Source).Select(v => $"{v.Query:D8},{v.Source:D8}")), StringComparer.Ordinal))
        {
            if (cancelled()) return Finish(false, "structure-work-obsolete");
            var unique = pairs.OrderBy(p => p.Query).ThenBy(p => p.Source).ToArray();
            if (unique.Any(p => !source[p.Source].Measured)) continue;
            var src = unique.Select(p => Point(source[p.Source].Geometry.Points[1])).ToArray();
            var observed = unique.Select(p => Point(query[p.Query].Points[1])).ToArray();
            if (Spread(observed) < 24) continue;
            var geometry = PointGeometry.FitAxisLockedPointGeometry(src, observed, [[1,0,0],[0,1,0],[0,0,1]], observed,
                frame.Size(), new double[src.Length], new double[src.Length], query.Count, maximumRelativeScaleCorrection: double.PositiveInfinity,
                distributeTemporalSupport: true);
            if (!Success(geometry) || geometry["scaleObservable"]?.GetValue<bool>() != true)
            {
                pointRejections.Add(new JsonObject { ["pairs"] = unique.Length, ["reason"] = geometry["reason"]?.DeepClone(), ["scaleObservable"] = geometry["scaleObservable"]?.DeepClone(), ["h1Validation"] = geometry["h1Validation"]?.DeepClone() });
                continue;
            }
            var matrix = Matrix(geometry["selected"]!["matrixSourceToClient"]!);
            if (candidates.Any(c => PoseDistance(matrix, c.Matrix, reference.Clean.Size()) <= 3)) continue;
            candidates.Add(new(matrix, geometry, unique, src, observed));
            if (candidates.Count > 8) { diagnostic["candidateCount"] = candidates.Count; return Finish(false, "initial-structure-validation-budget-unresolved"); }
        }
        diagnostic["poseIndexCandidates"] = new JsonObject { ["count"] = 0, ["validated"] = new JsonArray() };
        var qlines = Geometry.Lines(frame, context); var slines = Geometry.VisibleWalls(reference.Clean, reference.KnownBoundary, sourceNodes);
        var identities = new List<HashSet<string>>(); var wallCache = new Dictionary<string, S1StructureInitialWalls.WallFit?>();
        foreach (var seed in seeds)
        {
            if (cancelled()) return Finish(false, "structure-work-obsolete");
            if (candidates.Any(c => c.Pairs.Length > 0 && Explains(source[seed.Source].Geometry, allQuery[seed.Query], c.Matrix[0][0], [c.Matrix[0][2], c.Matrix[1][2]]))) continue;
            var wall = S1StructureInitialWalls.Fit(qlines, slines, seed.Scale, seed.Translation, reference, wallCache);
            if (wall is null) continue;
            var identity = wall.Planes.Select(p => $"{p.Axis},{p.Sign},{p.QueryEdge},{p.SourceEdge}").ToHashSet();
            if (identities.Any(old => identity.IsSubsetOf(old))) continue;
            var subsets = identities.Where(old => old.IsProperSubsetOf(identity)).ToArray();
            if (subsets.Length > 0) { candidates.RemoveAll(c => c.WallIdentity is not null && subsets.Any(old => c.WallIdentity.SetEquals(old))); identities.RemoveAll(subsets.Contains); }
            identities.Add(identity);
            if (candidates.Any(c => c.Pairs.Length > 0 && wall.Planes.All(p => Math.Abs(c.Matrix[p.Axis][p.Axis]*p.SourceNormal+c.Matrix[p.Axis][2]-p.QueryNormal) <= 6))) continue;
            if (candidates.Any(c => PoseDistance(wall.Matrix, c.Matrix, reference.Clean.Size()) <= 3)) continue;
            candidates.Add(new(wall.Matrix, new() { ["wallScale"] = wall.Evidence() }, [], [], [], identity, seed.Query >= query.Count));
            if (candidates.Count > 8) { diagnostic["candidateCount"] = candidates.Count; return Finish(false, "initial-structure-validation-budget-unresolved"); }
        }
        diagnostic["candidateCount"] = candidates.Count;
        stages.Add(new JsonObject { ["stage"] = "scale-consensus", ["elapsedMs"] = Stopwatch.GetElapsedTime(consensusStarted).TotalMilliseconds });
        var accepted = new List<(Candidate Candidate, JsonObject Residual)>();
        var validationStarted = Stopwatch.GetTimestamp();
        foreach (var c in candidates)
        {
            if (cancelled()) return Finish(false, "structure-work-obsolete");
            using var projected = Geometry.Warp(reference.Clean, c.Matrix, frame.Size());
            using var unknown = reference.Unknown is null ? null : Geometry.Warp(reference.Unknown, c.Matrix, frame.Size());
            var regions = RegionalGeometry.Evaluate(frame, context, projected, unknown);
            var residual = regions["regionalEdgeVerified"]!.GetValue<bool>() ? FrameResidual.Evaluate(frame, context, reference.Validation, c.Matrix) : null;
            bool passed = residual?["verified"]?.GetValue<bool>() == true;
            candidateDiagnostics.Add(new JsonObject { ["matrix"] = Matrix(c.Matrix), ["pointGeometry"] = c.Geometry.DeepClone(),
                ["pairs"] = new JsonArray(c.Pairs.Select(p => (JsonNode)new JsonArray(p.Query, p.Source)).ToArray()),
                ["sourcePoints"] = Points(c.SourcePoints), ["queryPoints"] = Points(c.QueryPoints), ["regions"] = regions, ["residual"] = residual?.DeepClone(), ["accepted"] = passed });
            if (passed && !accepted.Any(old => PoseDistance(c.Matrix, old.Candidate.Matrix, reference.Clean.Size()) <= 3)) accepted.Add((c, residual!));
        }
        stages.Add(new JsonObject { ["stage"] = "current-validation", ["elapsedMs"] = Stopwatch.GetElapsedTime(validationStarted).TotalMilliseconds });
        if (cancelled()) return Finish(false, "structure-work-obsolete");
        if (accepted.Count != 1) return Finish(false, accepted.Count > 0 ? "initial-structure-ambiguous" : "initial-structure-current-geometry-failed");
        var selected = accepted[0]; var result = Finish(true, "initial-structure-candidate");
        result["matrixSourceToClient"] = Matrix(selected.Candidate.Matrix); result["coordinateSpace"] = "author-render-to-client"; result["routeProfileId"] = "shared-structure";
        result["markerOccludedWallProposal"] = selected.Candidate.Marker;
        selected.Residual["validationBasis"] = "actual-rendered-profile-structural-mask"; selected.Residual["poseVerifiedAgainstRenderedProfile"] = true;
        result["selectedRouteCurrentFrameResidual"] = selected.Residual;
        var scaleProof = ScaleObservation(selected.Candidate, selected.Residual);
        result["currentFrameScaleObservation"] = scaleProof;
        return result;
    }

    private static Point2d Point(double[] values) => new(values[0], values[1]);
    private static JsonArray Points(IEnumerable<Point2d> values) => new(values.Select(p => (JsonNode)new JsonArray(p.X, p.Y)).ToArray());
    private static double Spread(Point2d[] values) => Math.Min(values.Max(p => p.X)-values.Min(p => p.X), values.Max(p => p.Y)-values.Min(p => p.Y));
    private static bool Explains(FastObservedChain source, FastObservedChain query, double scale, double[] translation)
    {
        for (int i = 0; i < 3; i++)
        {
            double x = source.Points[i][0]*scale+translation[0]-query.Points[i][0], y = source.Points[i][1]*scale+translation[1]-query.Points[i][1];
            if (Math.Sqrt(x*x+y*y) > 3) return false;
        }
        return true;
    }
    private static JsonObject? ScaleObservation(Candidate candidate, JsonObject residual)
    {
        JsonObject? proof = null;
        var geometry = candidate.Geometry;
        if (geometry["scaleObservable"]?.GetValue<bool>() == true && Success(geometry["h1"]) && geometry["mode"]?.ToString() == "scale-plus-translation"
            && PoseDistance(Matrix(geometry["h1"]!["matrixSourceToClient"]!), candidate.Matrix, new Size(1, 1)) <= 1e-10)
            proof = new() { ["success"] = true, ["scaleObservable"] = true, ["basis"] = "disjoint-point-h1", ["validation"] = geometry["h1Validation"]?.DeepClone(), ["supportPointCount"] = geometry["supportPointCount"]?.DeepClone() };
        if (proof is null && geometry["wallScale"] is JsonObject wall && wall["heldOutNormals"] is JsonObject held && wall["rasterValidation"]?["verified"]?.GetValue<bool>() == true)
            proof = new() { ["success"] = true, ["scaleObservable"] = true, ["basis"] = "directed-wall-held-out", ["validation"] = held.DeepClone(), ["supportPointCount"] = wall["planes"]!.AsArray().Count };
        if (proof?["validation"]?["count"] is null || proof["validation"]!["count"]!.GetValue<int>() == 0 || residual["verified"]?.GetValue<bool>() != true) return null;
        return new() { ["routeProfileId"] = "shared-structure", ["geometry"] = proof, ["scaleMatrixSourceToClient"] = Matrix(candidate.Matrix), ["matrixSourceToClient"] = Matrix(candidate.Matrix) };
    }
}
