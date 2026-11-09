using OpenCvSharp;
using System.Text.Json;
using System.Text.Json.Nodes;

namespace StarMap.Vision;

internal sealed class MainEntrySource
{
    public JsonElement Row { get; }
    public string MapId { get; }
    public string Floor { get; }
    private readonly double unit;
    private readonly Point2d center;
    private readonly Point2d[] points;
    private readonly Point2d[][] rays;
    private readonly bool[] main;

    public static MainEntrySource Read(JsonElement row) => new(row);
    internal static JsonArray PointJson(Point2d p) => new(p.X,p.Y);
    private static Point2d Point(JsonElement node) => new(node[0].GetDouble(),node[1].GetDouble());

    private MainEntrySource(JsonElement row)
    {
        Row=row; MapId=row.GetProperty("mapId").GetString()!; Floor=row.GetProperty("floor").GetString()!;
        unit=row.GetProperty("unit").GetDouble(); center=Point(row.GetProperty("center"));
        var outline=row.GetProperty("outline").EnumerateArray().Select(Point).ToArray();
        var chains=row.GetProperty("chains").EnumerateArray().ToArray();
        if (chains.Length==0 || outline.Length==0) throw new InvalidDataException("Main entrance has no chains or outline.");
        points=new Point2d[chains.Length]; rays=new Point2d[chains.Length][]; main=new bool[chains.Length];
        for(int i=0;i<chains.Length;i++)
        {
            var triplet=chains[i].GetProperty("points");
            var directions=chains[i].GetProperty("rays");
            if (triplet.GetArrayLength()!=3 || directions.GetArrayLength()!=2) throw new InvalidDataException("Invalid entrance chain shape.");
            foreach(var v in triplet.EnumerateArray().Concat(directions.EnumerateArray()))
                if(v.GetArrayLength()!=2 || !double.IsFinite(v[0].GetDouble()) || !double.IsFinite(v[1].GetDouble()))
                    throw new InvalidDataException("Invalid entrance chain vector.");
            points[i]=Point(triplet[1]); rays[i]=directions.EnumerateArray().Select(Point).ToArray();
            main[i]=outline.Any(p=>Numerics.Norm(p-points[i])<=3);
        }
    }

    internal sealed record Hypothesis(double Scale,Point2d Translation, Point2d Anchor, (int Q,int S)[] Pairs,
        int MainCount,Point2d NeighborExtent)
    {
        internal JsonObject ToJson()=>new() { ["scale"]=Scale,["translation"]=PointJson(Translation),
            ["chainCount"]=Pairs.Length,["mainChains"]=MainCount,["neighborChains"]=Pairs.Length-MainCount,
            ["pairs"]=new JsonArray(Pairs.Select(p=>(JsonNode)new JsonArray(p.Q,p.S)).ToArray()),
            ["neighborExtentGrid"]=PointJson(NeighborExtent) };
    }

    internal List<Hypothesis> Hypotheses(IReadOnlyList<Corner> query,int minFrameDimension,CancellationToken token)
    {
        var oriented=new List<(int Q,int S)>();
        var atSource=points.Select(_=>new List<int>()).ToArray();
        for (int q=0;q<query.Count;q++)
            for (int s=0;s<points.Length;s++)
                if (Numerics.Dot(query[q].Rays[0],rays[s][0])>=.95 && Numerics.Dot(query[q].Rays[1],rays[s][1])>=.95)
                {
                    oriented.Add((q,s)); atSource[s].Add(q);
                }
        var hypotheses=new List<Hypothesis>();
        var mainIds=Enumerable.Range(0,main.Length).Where(i=>main[i]).ToArray();
        for(int ai=0;ai<mainIds.Length;ai++)
        {
            token.ThrowIfCancellationRequested();
            int si=mainIds[ai];
            foreach(int sj in mainIds.Skip(ai+1))
            {
                var delta=points[sj]-points[si];
                double denom=Numerics.Dot(delta,delta);
                if(denom<Math.Pow(.7*unit,2)) continue;
                // meshgrid(qa,qb) iterates qa fastest.
                foreach(int qb in atSource[sj])
                    foreach(int qa in atSource[si])
                    {
                        var dq=query[qb].Point-query[qa].Point;
                        double scale=Numerics.Dot(dq,delta)/denom;
                        if (Numerics.Norm(dq-delta*scale)>2 || scale*unit<8 || scale*unit>minFrameDimension/4d) continue;
                        Score(scale,query[qa].Point-points[si]*scale);
                    }
            }
        }
        var distinct=new List<Hypothesis>();
        foreach(var h in hypotheses.OrderByDescending(h=>h.Pairs.Length))
            if(!distinct.Any(other=>Equivalent(other,h))) distinct.Add(h);
        return distinct;

        bool Equivalent(Hypothesis a,Hypothesis b) => Numerics.Norm(a.Anchor-b.Anchor)+Math.Abs(a.Scale-b.Scale)*unit*12<=3;

        void Score(double scale,Point2d translation)
        {
            var anchor=center*scale+translation;
            if(hypotheses.Any(h=>Numerics.Norm(anchor-h.Anchor)<3 && Math.Abs(scale-h.Scale)*unit<.25)) return;
            (int Q,int S)[] pairs=[];
            int mainCount=0;
            for(int iter=0;iter<3;iter++)
            {
                var hits=oriented.Where(p=>Numerics.Norm(query[p.Q].Point-(points[p.S]*scale+translation))<=3).ToArray();
                var qc=new int[query.Count]; var sc=new int[points.Length];
                foreach(var p in hits){ qc[p.Q]++; sc[p.S]++; }
                pairs=hits.Where(p=>qc[p.Q]==1 && sc[p.S]==1).ToArray();
                mainCount=pairs.Count(p=>main[p.S]);
                if(pairs.Length<4 || mainCount<2) return;
                var a=new Point2d(0,0); var b=new Point2d(0,0);
                foreach(var p in pairs) { a+=points[p.S]; b+=query[p.Q].Point; }
                a=new(a.X/pairs.Length,a.Y/pairs.Length); b=new(b.X/pairs.Length,b.Y/pairs.Length);
                double[] cross=new double[2*pairs.Length],square=new double[2*pairs.Length];
                for(int i=0;i<pairs.Length;i++)
                {
                    var u=points[pairs[i].S]-a; var v=query[pairs[i].Q].Point-b;
                    cross[2*i]=u.X*v.X; cross[2*i+1]=u.Y*v.Y;
                    square[2*i]=u.X*u.X; square[2*i+1]=u.Y*u.Y;
                }
                double proposed=Pairwise(cross)/Pairwise(square);
                var shift=b-a*proposed;
                if(iter!=2 || pairs.All(p=>Numerics.Norm(query[p.Q].Point-(points[p.S]*proposed+shift))<=3))
                { scale=proposed; translation=shift; }
            }
            var neighbors=pairs.Where(p=>!main[p.S]).Select(p=>points[p.S]).ToArray();
            var extent=neighbors.Length==0 ? new Point2d(0,0) : new Point2d(
                (neighbors.Max(p=>p.X)-neighbors.Min(p=>p.X))/unit,(neighbors.Max(p=>p.Y)-neighbors.Min(p=>p.Y))/unit);
            var refined=new Hypothesis(scale,translation,center*scale+translation,pairs,mainCount,extent);
            int old=hypotheses.FindIndex(h=>Equivalent(h,refined));
            if(old<0) hypotheses.Add(refined);
            else if(pairs.Length>hypotheses[old].Pairs.Length) hypotheses[old]=refined;
        }
    }

    // NumPy float64 add.reduce over the contiguous flattened (N,2) products.
    private static double Pairwise(ReadOnlySpan<double> values)
    {
        if(values.Length<8) { double s=-0d; foreach(double v in values) s+=v; return s; }
        if(values.Length>128)
        {
            int cut=(values.Length/2)/8*8;
            return Pairwise(values[..cut])+Pairwise(values[cut..]);
        }
        Span<double> sums=stackalloc double[8];
        values[..8].CopyTo(sums);
        int i=8;
        for(;i+7<values.Length;i+=8) for(int j=0;j<8;j++) sums[j]+=values[i+j];
        double result=((sums[0]+sums[1])+(sums[2]+sums[3]))+((sums[4]+sums[5])+(sums[6]+sums[7]));
        for(;i<values.Length;i++) result+=values[i];
        return result;
    }
}
