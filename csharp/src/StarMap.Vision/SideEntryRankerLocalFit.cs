using OpenCvSharp;
using System.Text.Json;
using System.Text.Json.Nodes;

namespace StarMap.Vision;

/// <summary>The passport-only local initial_wall_geometry estimator, with per-item/frame assignment memoization.</summary>
internal sealed class SideEntryRankerLocalFit
{
    internal sealed record Fit(double[][] Matrix,JsonArray Planes,JsonObject HeldOut);
    private readonly IReadOnlyList<Wall> query,source;
    private readonly Dictionary<string,Fit?> cache=new(StringComparer.Ordinal);
    internal SideEntryRankerLocalFit(IReadOnlyList<Wall> query,IReadOnlyList<Wall> source) { this.query=query; this.source=source; }

    internal Fit? Evaluate(double scale,Point2d translation,CancellationToken cancellation)
    {
        var local=query.Select((q,i)=>(Line:q,Index:i)).Where(p=>source.Any(s=>Distance(p.Line,s,scale,translation)<=3)).ToArray();
        if(local.Length==0) return null;
        var choices=new int[local.Length];
        for(int i=0;i<local.Length;i++)
        {
            cancellation.ThrowIfCancellationRequested();
            double nearest=double.PositiveInfinity,lo=double.PositiveInfinity,hi=double.NegativeInfinity; int selected=-1;
            for(int j=0;j<source.Count;j++)
            {
                double distance=Distance(local[i].Line,source[j],scale,translation);
                if(distance<=3) { lo=Math.Min(lo,source[j].Normal); hi=Math.Max(hi,source[j].Normal); }
                if(distance<nearest) { nearest=distance; selected=j; }
            }
            if(nearest<=3 && hi-lo>3) return null;
            choices[i]=nearest<=3?selected:-1;
        }
        string key=string.Join(',',local.Select(p=>p.Index))+":"+string.Join(',',choices);
        if(cache.TryGetValue(key,out var cached)) return cached;
        cache[key]=null;
        var groups=new List<(Wall Q,Wall S)>();
        for(int i=0;i<local.Length;i++)
        {
            if(choices[i]<0) continue;
            var q=local[i].Line; var s=source[choices[i]];
            if(groups.Any(g=>g.Q.Axis==q.Axis && g.Q.Sign==q.Sign && Math.Abs(g.Q.Normal-q.Normal)<=3)) continue;
            if(groups.Any(g=>g.Q.Axis==q.Axis && g.Q.Sign==q.Sign && Math.Abs(g.S.Normal-s.Normal)<=3)) return null;
            groups.Add((q,s));
        }
        for(int axis=0;axis<2;axis++)
        {
            var g=groups.Where(p=>p.Q.Axis==axis).ToArray();
            if(g.Length<2 || g.Max(p=>p.Q.Normal)-g.Min(p=>p.Q.Normal)<24) return null;
        }
        var full=Solve(groups);
        if(full==null || full[0]<=0) return null;
        var shift=new Point2d(full[1],full[2]);
        if(local.Any(p=>source.Min(s=>Distance(p.Line,s,full[0],shift))>6)) return null;
        var errors=new List<double>();
        for(int i=0;i<groups.Count;i++)
        {
            cancellation.ThrowIfCancellationRequested();
            var model=Solve(groups.Where((_,j)=>i!=j).ToList());
            if(model==null || model[0]<=0) return null;
            errors.Add(Math.Abs(groups[i].S.Normal*model[0]+model[groups[i].Q.Axis+1]-groups[i].Q.Normal));
        }
        var held=Statistics(errors);
        if(Numerics.Scalar(held["medianPixels"]!)>3 || Numerics.Scalar(held["p90Pixels"]!)>8 || Numerics.Scalar(held["maxPixels"]!)>20) return null;
        var planes=new JsonArray(groups.Select(g=>(JsonNode)new JsonObject { ["axis"]=g.Q.Axis,["sign"]=g.Q.Sign,
            ["queryNormal"]=g.Q.Normal,["sourceNormal"]=g.S.Normal,["queryEdge"]=g.Q.EdgeId<0?null:JsonValue.Create(g.Q.EdgeId),
            ["sourceEdge"]=g.S.EdgeId<0?null:JsonValue.Create(g.S.EdgeId) }).ToArray());
        var result=new Fit([[full[0],0,full[1]],[0,full[0],full[2]],[0,0,1]],planes,held);
        cache[key]=result; return result;
    }

    private static double Distance(Wall q,Wall s,double scale,Point2d translation)
    {
        if(q.Axis!=s.Axis || q.Sign!=s.Sign) return double.PositiveInfinity;
        double shift=q.Axis==0?translation.Y:translation.X;
        if(Math.Min(q.Hi,scale*s.Hi+shift)-Math.Max(q.Lo,scale*s.Lo+shift)<Math.Min(12,q.Hi-q.Lo-1)) return double.PositiveInfinity;
        return Math.Abs(scale*s.Normal+(q.Axis==0?translation.X:translation.Y)-q.Normal);
    }
    private static double[]? Solve(IReadOnlyList<(Wall Q,Wall S)> groups)
    {
        using var design=new Mat(groups.Count,3,MatType.CV_64FC1); using var observed=new Mat(groups.Count,1,MatType.CV_64FC1);
        for(int i=0;i<groups.Count;i++)
        {
            design.Set(i,0,groups[i].S.Normal); design.Set(i,1,groups[i].Q.Axis==0?1d:0); design.Set(i,2,groups[i].Q.Axis==1?1d:0);
            observed.Set(i,0,groups[i].Q.Normal);
        }
        using var singular=new Mat(); using var u=new Mat(); using var vt=new Mat();
        Cv2.SVDecomp(design,singular,u,vt,SVD.Flags.NoUV);
        double tolerance=singular.At<double>(0,0)*2.220446049250313e-16*Math.Max(groups.Count,3);
        if(Enumerable.Range(0,(int)singular.Total()).Count(i=>singular.At<double>(i,0)>tolerance)!=3) return null;
        using var solution=new Mat();
        if(!Cv2.Solve(design,observed,solution,DecompTypes.SVD)) return null;
        var result=Enumerable.Range(0,3).Select(i=>solution.At<double>(i,0)).ToArray();
        return result.All(double.IsFinite)?result:null;
    }
    private static JsonObject Statistics(List<double> values)
    {
        var x=values.Order().ToArray(); double at=.9*(x.Length-1); int lo=(int)Math.Floor(at),hi=(int)Math.Ceiling(at);
        return new() { ["count"]=x.Length,["medianPixels"]=Numerics.Median(x),["p90Pixels"]=x[lo]+(x[hi]-x[lo])*(at-lo),["maxPixels"]=x[^1] };
    }
    internal static List<Wall> Walls(JsonElement rows)=>rows.EnumerateArray().Select(r=>new Wall(r.GetProperty("axis").GetInt32(),r.GetProperty("sign").GetInt32(),
        r.GetProperty("normal").GetDouble(),r.GetProperty("lo").GetDouble(),r.GetProperty("hi").GetDouble(),
        r.TryGetProperty("edgeId",out var id) && id.ValueKind==JsonValueKind.Number?id.GetInt32():-1)).ToList();
    internal static JsonArray WallsJson(IEnumerable<Wall> rows)=>new(rows.Select(r=>
    {
        var o=new JsonObject { ["axis"]=r.Axis,["sign"]=r.Sign,["normal"]=r.Normal,["lo"]=r.Lo,["hi"]=r.Hi };
        if(r.EdgeId>=0) o["edgeId"]=r.EdgeId; return (JsonNode)o;
    }).ToArray());
}
