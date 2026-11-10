using System.Diagnostics;
using System.Text.Json;
using System.Text.Json.Nodes;
using OpenCvSharp;

namespace StarMap.Vision;

/// <summary>Candidate-independent structure coordinates and positive geometric contradictions only.
/// Owns no round, candidate-pool mutation, identity, trusted pose or display state.</summary>
public sealed partial class StructureIndexFilter
{
    public JsonObject Evaluate(Mat frame, IReadOnlyList<string> candidateIds, string floor, Func<bool>? cancelled = null)
    {
        lock (_sync)
        {
            ObjectDisposedException.ThrowIf(_disposed,this);
            var timer = Stopwatch.StartNew();
            var anchor = _locator.Detect(frame,cancelled);
            double gateMs = timer.Elapsed.TotalMilliseconds;
            return EvaluatePrepared(frame,candidateIds,floor,anchor,cancelled ?? (() => false),timer,gateMs);
        }
    }

    /// <summary>Reuses a glyph observation produced from this exact frame, without a second match.</summary>
    public JsonObject Evaluate(Mat frame, IReadOnlyList<string> candidateIds, string floor, StructureGateAnchor anchor, Func<bool>? cancelled = null)
    {
        lock (_sync)
        {
            ObjectDisposedException.ThrowIf(_disposed,this);
            return EvaluatePrepared(frame,candidateIds,floor,anchor,cancelled ?? (() => false),Stopwatch.StartNew(),0);
        }
    }

    /// <summary>Serially borrows complete raw observations from the same operation as the following fit scan.</summary>
    public JsonObject Evaluate(StructureFramePreparation preparation,IReadOnlyList<string> candidateIds,string floor,
        StructureGateAnchor anchor,Func<bool>? cancelled=null)
    {
        lock(_sync)
        {
            ObjectDisposedException.ThrowIf(_disposed,this);
            return EvaluatePrepared(preparation.Frame,candidateIds,floor,anchor,cancelled??(()=>false),Stopwatch.StartNew(),0,preparation);
        }
    }

    private JsonObject EvaluatePrepared(Mat frame,IReadOnlyList<string> candidateIds,string floor,StructureGateAnchor anchor,
        Func<bool> cancelled,Stopwatch total,double gateMs,StructureFramePreparation? preparation=null)
    {
        string[] ids = candidateIds.Distinct(StringComparer.Ordinal).Order(StringComparer.Ordinal).ToArray();
        double observationMs = 0,resourceMs = 0,distanceComparisonMs = 0;
        var compare = Stopwatch.StartNew();
        JsonObject Result(string reason,JsonObject? extra = null)
        {
            var result = new JsonObject
            {
                ["schemaVersion"] = 1, ["mode"] = _catalog.Mode, ["resourceKey"] = _catalog.ResourceKey, ["floor"] = floor,
                ["evaluatedMapIds"] = JsonSerializer.SerializeToNode(ids), ["contradictedMapIds"] = new JsonArray(),
                ["unknownMapIds"] = JsonSerializer.SerializeToNode(ids), ["coordinateVerified"] = false, ["identityAuthorized"] = false,
                ["reason"] = reason, ["elapsedMs"] = compare.Elapsed.TotalMilliseconds,
                ["gateStatus"] = anchor.Status, ["gateReason"] = anchor.Reason, ["gateMs"] = gateMs,
                ["observationMs"] = observationMs, ["resourceMs"] = resourceMs, ["distanceComparisonMs"] = distanceComparisonMs,
                ["totalMs"] = total.Elapsed.TotalMilliseconds
            };
            if (extra is not null) foreach (var pair in extra) result[pair.Key] = pair.Value?.DeepClone();
            return result;
        }
        if (!ReferenceEquals(anchor.Frame,frame) || anchor.TemplateSha256 != _locator.TemplateSha256 || anchor.ProfileSha256 != _locator.ProfileSha256)
            return Result("coordinate-anchor-frame-mismatch");
        if (ids.Length == 0 || ids.Any(id => !_catalog.MapIds.Contains(id,StringComparer.Ordinal) || !_entries.TryGetProperty(id,out _))
            || !_catalog.Floors.Contains(floor,StringComparer.Ordinal)) return Result("coordinate-scope-invalid");
        if (cancelled()) return Result("coordinate-work-obsolete");
        var contexts = new List<Observation>();
        try
        {
            var extract = Stopwatch.StartNew();
            var islands = preparation?.Islands()??S1StructureObservations.Islands(frame,new Rect(0,0,frame.Cols,frame.Rows));
            if (islands.Count <= 32)
            {
                foreach (var island in islands)
                {
                    if (cancelled()) break;
                    var context = preparation is null?Geometry.FromComponent(frame,island.Bounds,island.Component):preparation.For(island);
                    if (context is null) continue;
                    bool retained = false;
                    try
                    {
                        if (Geometry.ObservedChains(frame,context).Count >= 6) { contexts.Add(context); retained = true; }
                    }
                    finally { if (!retained && preparation is null) context.Dispose(); }
                }
            }
            observationMs = extract.Elapsed.TotalMilliseconds;
            if (cancelled()) return Result("coordinate-work-obsolete");
            PoolData? data;
            var resources = Stopwatch.StartNew();
            try { data = Pool(ids,floor,cancelled); }
            catch (Exception error) when (error is KeyNotFoundException or InvalidOperationException or InvalidDataException or IOException or UnauthorizedAccessException or JsonException or ArgumentException or OverflowException)
            {
                resourceMs = resources.Elapsed.TotalMilliseconds;
                return Result("coordinate-resources-unavailable",new() { ["detail"] = error.Message });
            }
            resourceMs = resources.Elapsed.TotalMilliseconds;
            if (data is null || cancelled()) return Result("coordinate-work-obsolete");
            compare.Restart();
            StructureGateAnchor? gate = anchor.Status == "located" ? anchor : null;
            var solutions = new List<(int ContextIndex,Domain Domain)>();
            try
            {
                for (int index = 0; index < contexts.Count; index++)
                {
                    if (cancelled()) return Result("coordinate-work-obsolete");
                    var context = contexts[index];
                    Domain? domain = gate is not null && floor == "-1" ? EntranceUnionDomain(frame,context,data,gate,cancelled) : null;
                    List<Domain> domains = domain is null ? DomainsFromCommon(frame,context,data.Prepared,cancelled) : [domain];
                    foreach (var candidate in domains)
                    {
                        var constrained = gate is null ? candidate : IntersectAnchor(candidate,gate,data.Origin);
                        if (constrained is not null) solutions.Add((index,constrained));
                    }
                }
            }
            catch (CoordinateBudgetExceededException exhausted)
            {
                if (cancelled()) return Result("coordinate-work-obsolete");
                return Result("current-common-coordinate-budget-exhausted",new() { ["budgetStage"] = exhausted.Stage });
            }
            if (cancelled()) return Result("coordinate-work-obsolete");
            if (solutions.Count == 0) return Result("current-common-coordinate-unresolved");
            if (solutions.Select(s => s.ContextIndex).Distinct().Count() > 1) return Result("current-observation-context-ambiguous");
            var first = solutions[0]; var representative = first.Domain.Representative;
            // All basins are retained. None is ranked by candidate fit, coverage or identity.
            if (gate is null && solutions.Skip(1).Any(s => ExtentDistance(s.Domain.Representative,representative,data.Width,data.Height) > 6))
                return Result("current-common-coordinate-ambiguous");
            var hypotheses = solutions.SelectMany(s => s.Domain.Hypotheses).Distinct().OrderBy(h => h.Scale).ThenBy(h => h.X).ThenBy(h => h.Y).ToArray();
            if (cancelled()) return Result("coordinate-work-obsolete");
            if (hypotheses.Length > 120000) return Result("current-common-coordinate-budget-exhausted");
            var evidence = (JsonObject)first.Domain.Evidence.DeepClone(); evidence["validatedCommonDomains"] = solutions.Count;
            using var edge = Geometry.Observed(frame,contexts[first.ContextIndex]).Clone();
            if (gate?.CenterPx is { } center)
            {
                int x = Numerics.Rint(center.X),y = Numerics.Rint(center.Y);
                int x0 = Math.Max(0,x-20),y0 = Math.Max(0,y-20),x1 = Math.Min(edge.Cols,x+21),y1 = Math.Min(edge.Rows,y+21);
                if (x1 > x0 && y1 > y0) { using var exclusion = new Mat(edge,new Rect(x0,y0,x1-x0,y1-y0)); exclusion.SetTo(Scalar.All(0)); }
            }
            var points = FastAlignment.SampleEdges(edge,500);
            if (points.Length < 40) return Result("current-structure-insufficient");
            var distances=Stopwatch.StartNew();
            var minima=CandidateMinima(points,hypotheses,data.Width,data.Height,data.Fields,cancelled);
            distanceComparisonMs=distances.Elapsed.TotalMilliseconds;
            if(minima is null)return Result("coordinate-work-obsolete");
            var checks = new JsonArray(); var contradicted = new List<string>();
            for (int map = 0; map < data.Ids.Length; map++)
            {
                var minimum=minima[map];
                var bad = points.Where((_,p) => minimum[p] > 8).ToArray();
                double width = bad.Length == 0 ? 0 : bad.Max(p => p[0])-bad.Min(p => p[0]);
                double height = bad.Length == 0 ? 0 : bad.Max(p => p[1])-bad.Min(p => p[1]);
                bool explicitContradiction = bad.Length >= 12 && Math.Min(width,height) >= 12 && Math.Max(width,height) >= 24;
                if (explicitContradiction) contradicted.Add(data.Ids[map]);
                checks.Add(new JsonObject { ["mapId"] = data.Ids[map], ["unavoidableMissCount"] = bad.Length, ["sampleCount"] = points.Length,
                    ["unavoidableSpanPx"] = new JsonArray(width,height), ["explicitContradiction"] = explicitContradiction });
            }
            if (cancelled()) return Result("coordinate-work-obsolete");
            if (contradicted.Count == ids.Length) return Result("coordinate-or-catalogue-conflict",new() { ["candidateChecks"] = checks });
            bool poseAvailable = hypotheses.All(h => ProjectionDistance(h,representative,data.Width,data.Height) <= 6);
            double tx = representative.X-representative.Scale*data.Origin.X,ty = representative.Y-representative.Scale*data.Origin.Y;
            evidence["sharedHypothesisCount"] = hypotheses.Length; evidence["perCandidateFreeFit"] = false;
            return new JsonObject
            {
                ["schemaVersion"] = 1, ["mode"] = _catalog.Mode, ["resourceKey"] = _catalog.ResourceKey, ["floor"] = floor,
                ["evaluatedMapIds"] = JsonSerializer.SerializeToNode(ids), ["contradictedMapIds"] = JsonSerializer.SerializeToNode(contradicted),
                ["unknownMapIds"] = JsonSerializer.SerializeToNode(ids.Where(id => !contradicted.Contains(id,StringComparer.Ordinal)).ToArray()),
                ["coordinateVerified"] = true, ["poseAvailable"] = poseAvailable,
                ["matrixEntranceRendererToClient"] = new JsonArray(new JsonArray(representative.Scale,0,tx),new JsonArray(0,representative.Scale,ty),new JsonArray(0,0,1)),
                ["selectedContextIndex"] = first.ContextIndex, ["coordinateEvidence"] = evidence, ["candidateChecks"] = checks,
                ["identityAuthorized"] = false, ["reason"] = "same-coordinate-positive-contradictions-only", ["elapsedMs"] = compare.Elapsed.TotalMilliseconds,
                ["gateStatus"] = anchor.Status, ["gateReason"] = anchor.Reason, ["gateMs"] = gateMs,
                ["observationMs"] = observationMs, ["resourceMs"] = resourceMs, ["distanceComparisonMs"] = distanceComparisonMs,
                ["totalMs"] = total.Elapsed.TotalMilliseconds
            };
        }
        finally { if(preparation is null)foreach (var context in contexts) context.Dispose(); }
    }

    private static double ExtentDistance(Hypothesis a,Hypothesis b,int width,int height)
    {
        double x = a.X-b.X,y = a.Y-b.Y,s = a.Scale-b.Scale;
        return Math.Max(Math.Sqrt(x*x+y*y),Math.Sqrt((x+width*s)*(x+width*s)+(y+height*s)*(y+height*s)));
    }
    private static double ProjectionDistance(Hypothesis a,Hypothesis b,int width,int height)
    {
        double x = a.X-b.X,y = a.Y-b.Y;
        double farX = width*a.Scale+a.X-(width*b.Scale+b.X),farY = height*a.Scale+a.Y-(height*b.Scale+b.Y);
        return Math.Max(Math.Sqrt(x*x+y*y),Math.Sqrt(farX*farX+farY*farY));
    }
}
