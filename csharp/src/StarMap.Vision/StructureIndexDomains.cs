using System.Text.Json.Nodes;
using OpenCvSharp;
using StarMap.Core.Recognition;

namespace StarMap.Vision;

public sealed partial class StructureIndexFilter
{
    internal readonly record struct Hypothesis(double Scale, double X, double Y);
    private sealed record Domain(Hypothesis[] Hypotheses, Hypothesis Representative, JsonObject Evidence, double OffsetRadiusPx=0);
    // An unfinished feasible search is not an empty feasible set. Do not let
    // callers rank/prune with only the domains that happened to finish first.
    private sealed class CoordinateBudgetExceededException(string stage) : Exception(stage)
    {
        public string Stage { get; } = stage;
    }

    private static List<Domain> DomainsFromCommon(Mat frame, Observation context, FastPrepared prepared, Func<bool> cancelled)
    {
        if (prepared.Chains.Length < 6) return [];
        var query = Geometry.ObservedChains(frame,context);
        if (query.Count < 6) return [];
        // Propose intentionally returns a winner. ChainSeeds is the unranked complete bounded seed set.
        var seeds = FastAlignment.ChainSeeds(query,prepared,cancelled,
            budgetExceeded: () => throw new CoordinateBudgetExceededException("common-chain-seeds"));
        if (seeds.Count == 0) return [];
        var domains = new List<Domain>(); var seen = new HashSet<string>(StringComparer.Ordinal);
        foreach (var seed in seeds)
        {
            if (cancelled()) return [];
            var pairs = new List<(int Query,int Source)>();
            for (int qi = 0; qi < query.Count; qi++)
            {
                var node = query[qi];
                double[] measuredLengths = [Length(node.Points[0],node.Points[1]),Length(node.Points[2],node.Points[1])];
                for (int si = 0; si < prepared.Chains.Length; si++)
                {
                    var chain = prepared.Chains[si];
                    // NumPy src(float32)*Python scale stays float32 before adding float64 translation.
                    double dx = (float)(chain[1][0]*(float)seed.Scale)+seed.Translation[0]-node.Points[1][0];
                    double dy = (float)(chain[1][1]*(float)seed.Scale)+seed.Translation[1]-node.Points[1][1];
                    if (dx*dx+dy*dy > 48*48) continue;
                    bool oriented = true, adjacent = true;
                    for (int arm = 0; arm < 2; arm++)
                    {
                        if (prepared.Rays[si][arm][0]*node.Rays[arm][0]+prepared.Rays[si][arm][1]*node.Rays[arm][1] < .95) oriented = false;
                        int endpoint = arm == 0 ? 0 : 2;
                        float x = (chain[endpoint][0]-chain[1][0])*(float)seed.Scale, y = (chain[endpoint][1]-chain[1][1])*(float)seed.Scale;
                        float length = (float)Math.Sqrt(x*x+y*y);
                        if (Math.Abs(length-measuredLengths[arm]) > 3) adjacent = false;
                    }
                    if (oriented && adjacent) pairs.Add((qi,si));
                }
            }
            string key = string.Join(';',pairs.Select(p => p.Query+","+p.Source));
            if (!seen.Add(key)) continue;
            var domain = DomainFromPairs(prepared.Chains,query,pairs,cancelled);
            if (domain is null) continue;
            domain.Evidence["proposalSearch"] = "all-common-chain-seeds";
            domain.Evidence["commonSeedCount"] = seeds.Count;
            domain.Evidence["perCandidateFreeFit"] = false;
            domains.Add(domain);
        }
        return domains;
    }

    private static Domain? DomainFromPairs(float[][][] sourceChains, IReadOnlyList<FastObservedChain> query,
        List<(int Query,int Source)> allPairs, Func<bool> cancelled)
    {
        var queryCounts = allPairs.GroupBy(p => p.Query).ToDictionary(g => g.Key,g => g.Count());
        var sourceCounts = allPairs.GroupBy(p => p.Source).ToDictionary(g => g.Key,g => g.Count());
        var pairs = allPairs.Where(p => queryCounts[p.Query] == 1 && sourceCounts[p.Source] == 1).ToArray();
        if (pairs.Length < 6) return null;
        var vertices = new Dictionary<(double X,double Y),(double X,double Y)>();
        foreach (var (q,s) in pairs)
        {
            for (int i = 0; i < 3; i++)
            {
                var a = sourceChains[s][i]; var b = query[q].Points[i];
                var key = ((double)a[0],(double)a[1]); var value = (b[0],b[1]);
                if (vertices.TryGetValue(key,out var old) && old != value) return null;
                vertices[key] = value;
            }
        }
        if (vertices.Count < 6) return null;
        var source = vertices.Keys.Select(p => new[]{p.X,p.Y}).ToArray();
        var observed = vertices.Values.Select(p => new[]{p.X,p.Y}).ToArray();
        const double error = 3; double low = .03, high = 6;
        for (int i = 0; i < source.Length; i++)
        {
            if (cancelled()) return null;
            for (int j = 0; j < i; j++)
            for (int axis = 0; axis < 2; axis++)
            {
                double ds = source[i][axis]-source[j][axis], dq = observed[i][axis]-observed[j][axis];
                if (Math.Abs(ds) < 1) { if (Math.Abs(dq) > 2*error) return null; continue; }
                if (ds < 0) { ds = -ds; dq = -dq; }
                low = Math.Max(low,(dq-2*error)/ds); high = Math.Min(high,(dq+2*error)/ds);
            }
        }
        if (!(0 < low && low <= high) || high/low > 1.12) return null;
        double step = Math.Min(.00025,Math.Max(.00001,(high-low)/100)); var hypotheses = new List<Hypothesis>();
        foreach (double scale in NumpyCompat.Arange(low,high+step*.5,step))
        {
            if (cancelled()) return null;
            double lx = double.NegativeInfinity, ly = double.NegativeInfinity, hx = double.PositiveInfinity, hy = double.PositiveInfinity;
            for (int i = 0; i < source.Length; i++)
            {
                double x = observed[i][0]-scale*source[i][0], y = observed[i][1]-scale*source[i][1];
                lx = Math.Max(lx,x-error); ly = Math.Max(ly,y-error); hx = Math.Min(hx,x+error); hy = Math.Min(hy,y+error);
            }
            if (lx > hx || ly > hy) continue;
            var xs = Linspace(lx,hx,Math.Max(2,(int)Math.Ceiling((hx-lx)/.5)+1));
            var ys = Linspace(ly,hy,Math.Max(2,(int)Math.Ceiling((hy-ly)/.5)+1));
            foreach (double x in xs) foreach (double y in ys) hypotheses.Add(new(scale,x,y));
            if (hypotheses.Count > 20000) throw new CoordinateBudgetExceededException("common-pair-domain");
        }
        if (hypotheses.Count == 0) return null;
        double best = double.PositiveInfinity; var representative = hypotheses[0]; var residual = new double[source.Length];
        foreach (var hypothesis in hypotheses)
        {
            if (cancelled()) return null;
            for (int i = 0; i < source.Length; i++)
            {
                double x = source[i][0]*hypothesis.Scale+hypothesis.X-observed[i][0], y = source[i][1]*hypothesis.Scale+hypothesis.Y-observed[i][1];
                residual[i] = x*x+y*y;
            }
            double mean = FastAlignment.PairwiseSum(residual,0,residual.Length)/residual.Length;
            if (mean < best) { best = mean; representative = hypothesis; }
        }
        return new(hypotheses.ToArray(),representative,new()
        {
            ["basis"] = "current-floor-common-chain-vertices", ["commonChains"] = pairs.Length, ["commonVertices"] = source.Length,
            ["scaleRange"] = new JsonArray(low,high), ["pointCoordinateErrorBoundPx"] = error
        });
    }

    private static Domain? EntranceUnionDomain(Mat frame, Observation context, PoolData data, StructureGateAnchor anchor, Func<bool> cancelled,bool compactOffsets=false)
    {
        if (data.UnionChains.Length == 0 || !TryAnchor(anchor,out var center,out double error)) return null;
        var query = Geometry.ObservedChains(frame,context); var intervals = new List<(double Low,double High)>(); var supportingQueries = new HashSet<int>();
        double tolerance = error+3;
        for (int qi = 0; qi < query.Count; qi++)
        {
            if (cancelled()) return null;
            var node = query[qi];
            for (int si = 0; si < data.UnionChains.Length; si++)
            {
                var rays = data.UnionRays[si];
                if (Enumerable.Range(0,2).Any(arm => rays[arm][0]*node.Rays[arm][0]+rays[arm][1]*node.Rays[arm][1] < .95)) continue;
                var chain = data.UnionChains[si]; double low = .03,high = 6;
                bool impossible = false;
                for (int vertex = 0; vertex < 3 && !impossible; vertex++)
                for (int axis = 0; axis < 2; axis++)
                {
                    double native = chain[vertex][axis], measured = node.Points[vertex][axis]-(axis == 0 ? center.X : center.Y);
                    if (Math.Abs(native) < 1e-9)
                    {
                        if (Math.Abs(measured) > tolerance) { high = -1; impossible = true; break; }
                        continue;
                    }
                    double a = (measured-tolerance)/native,b = (measured+tolerance)/native;
                    low = Math.Max(low,Math.Min(a,b)); high = Math.Min(high,Math.Max(a,b));
                    if (low > high) { impossible = true; break; }
                }
                if (0 < low && low <= high) { intervals.Add((low,high)); supportingQueries.Add(qi); }
            }
        }
        if (intervals.Count == 0) return null;
        var merged = new List<(double Low,double High)>();
        foreach (var interval in intervals.OrderBy(i => i.Low).ThenBy(i => i.High))
        {
            if (merged.Count > 0 && interval.Low <= merged[^1].High) merged[^1] = (merged[^1].Low,Math.Max(merged[^1].High,interval.High));
            else merged.Add(interval);
        }
        var scales = new List<double>();
        foreach (var (low,high) in merged) scales.AddRange(Linspace(low,high,Math.Max(2,(int)Math.Ceiling((high-low)/.00025)+1)));
        var offsets = compactOffsets?[0d]:Linspace(-error,error,Math.Max(2,(int)Math.Ceiling(2*error))+1);
        if ((long)scales.Count*offsets.Length*offsets.Length > 120000)
            throw new CoordinateBudgetExceededException("entrance-union-domain");
        var hypotheses = new List<Hypothesis>();
        foreach (double scale in scales) foreach (double dx in offsets) foreach (double dy in offsets)
            hypotheses.Add(new(scale,center.X+scale*data.Origin.X+dx,center.Y+scale*data.Origin.Y+dy));
        var evidence = new JsonObject
        {
            ["basis"] = "entrance-plus-all-pool-chain-scale-union", ["sourceUnionChains"] = data.UnionChains.Length,
            ["observedSupportingChains"] = supportingQueries.Count, ["scaleRange"] = new JsonArray(merged[0].Low,merged[^1].High),
            ["scaleIntervalCount"] = merged.Count,
            ["scaleIntervals"] = new JsonArray(merged.Take(32).Select(i => (JsonNode)new JsonArray(i.Low,i.High)).ToArray()),
            ["pointCoordinateErrorBoundPx"] = 3.0, ["anchorErrorPx"] = error, ["scaleFromGlyph"] = false, ["perCandidateFreeFit"] = false
        };
        if(compactOffsets)
        {
            evidence["offsetDomain"]="continuous-box-conservative-lower-bound";
            evidence["scaleDomain"]="original-bounded-discrete-scale-grid";
            evidence["enumeratedTranslationOffsets"]=false;
        }
        return new(hypotheses.ToArray(),hypotheses[hypotheses.Count/2],evidence,compactOffsets?error:0);
    }

    private static Domain? IntersectAnchor(Domain domain, StructureGateAnchor anchor, Point2d origin)
    {
        if (!TryAnchor(anchor,out var center,out double error)) return null;
        var hypotheses = domain.Hypotheses.Where(h => Math.Max(Math.Abs(h.X-h.Scale*origin.X-center.X),Math.Abs(h.Y-h.Scale*origin.Y-center.Y)) <= error+1e-6).ToArray();
        if (hypotheses.Length == 0) return null;
        var nominal = domain.Representative; var best = hypotheses[0]; double distance = double.PositiveInfinity;
        foreach (var hypothesis in hypotheses)
        {
            double x = hypothesis.X-nominal.X,y = hypothesis.Y-nominal.Y,scale = hypothesis.Scale-nominal.Scale;
            double next = x*x+y*y+1e6*scale*scale;
            if (next < distance) { distance = next; best = hypothesis; }
        }
        var evidence = (JsonObject)domain.Evidence.DeepClone();
        evidence["entranceAnchorApplied"] = true; evidence["entranceCenterClient"] = new JsonArray(center.X,center.Y); evidence["anchorErrorPx"] = error;
        return new(hypotheses,best,evidence,domain.OffsetRadiusPx);
    }

    private static bool TryAnchor(StructureGateAnchor anchor, out Point2d center, out double error)
    {
        center = anchor.CenterPx ?? default; error = anchor.LocalizationUncertaintyPx ?? 3;
        return anchor.Status == "located" && anchor.CenterPx is not null && double.IsFinite(center.X) && double.IsFinite(center.Y) && double.IsFinite(error) && 0 <= error && error <= 20;
    }
    private static double Length(double[] a,double[] b) { double x = a[0]-b[0],y = a[1]-b[1]; return Math.Sqrt(x*x+y*y); }
    private static double[] Linspace(double start,double stop,int count)
    {
        if (count <= 0) return []; if (count == 1) return [start];
        var result = new double[count]; double step = (stop-start)/(count-1);
        for (int i = 0; i < count-1; i++) result[i] = start+i*step;
        result[^1] = stop; return result;
    }
}
