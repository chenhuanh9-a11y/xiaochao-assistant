using OpenCvSharp;
using System.Text.Json;
using System.Text.Json.Nodes;

namespace StarMap.Vision;

internal sealed class SideEntryRankerSource
{
    internal JsonElement Row { get; }
    internal string MapId { get; }
    internal string Floor { get; }
    internal double Unit { get; }
    internal Point2d[] Points { get; }
    internal Point2d[][] Rays { get; }
    internal (int A,int B)[] Edges { get; }
    internal Point2d[] Deltas { get; }
    internal double[] Denominators { get; }

    internal SideEntryRankerSource(JsonElement row)
    {
        Row=row; MapId=Text(row,"mapId"); Floor=Text(row,"floor"); Unit=row.GetProperty("unit").GetDouble();
        var turns=row.GetProperty("turns").EnumerateArray().ToArray();
        Points=turns.Select(t=>Point(t.GetProperty("point"))).ToArray();
        Rays=turns.Select(t=>t.GetProperty("rays").EnumerateArray().Select(Point).ToArray()).ToArray();
        Edges=turns.Select((t,i)=>(A:i,B:t.GetProperty("next").GetInt32())).Where(p=>p.B>=0 && p.B<Points.Length).ToArray();
        Deltas=Edges.Select(p=>Points[p.B]-Points[p.A]).ToArray();
        Denominators=Deltas.Select(p=>Numerics.Dot(p,p)).ToArray();
    }

    internal sealed record Pose(double Scale,Point2d Shift,(int Q,int S)[] Pairs,Point2d Extent)
    {
        internal JsonObject ToJson()=>new() { ["scale"]=Scale,["translation"]=JsonPoint(Shift),["chainCount"]=Pairs.Length,
            ["extentGrid"]=JsonPoint(Extent),["pairs"]=new JsonArray(Pairs.Select(p=>(JsonNode)new JsonArray(p.Q,p.S)).ToArray()) };
    }
    internal sealed record Seed(double Scale,Point2d Shift,double Radius,int Index);
    internal sealed record Search(List<Pose> Poses,bool Complete,int Exact,int Local,int Outside,int Rejected,int[] Rings);

    internal Search Propose(IReadOnlyList<Corner> query,(int A,int B)[] queryEdges,int minimumDimension,
        SideEntryRankerIndex.Prepared? prepared,Func<bool> expired,CancellationToken cancellation)
    {
        bool complete=true;
        var compatible=new bool[query.Count,Points.Length];
        var oriented=new List<(int Q,int S)>();
        for(int q=0;q<query.Count;q++)
            for(int s=0;s<Points.Length;s++)
                if(Numerics.Dot(query[q].Rays[0],Rays[s][0])>=.95 && Numerics.Dot(query[q].Rays[1],Rays[s][1])>=.95)
                { compatible[q,s]=true; oriented.Add((q,s)); }
        var seeds=new List<Seed>(); var seen=new HashSet<(long,long,long)>();
        foreach(var (a,b) in queryEdges)
        {
            cancellation.ThrowIfCancellationRequested();
            if(expired()) { complete=false; break; }
            for(int i=0;i<Edges.Length;i++)
            {
                if((i&255)==0)
                {
                    cancellation.ThrowIfCancellationRequested();
                    if(expired()) { complete=false; break; }
                }
                var pair=Edges[i];
                if(!compatible[a,pair.A] || !compatible[b,pair.B] || Denominators[i]<Math.Pow(.6*Unit,2)) continue;
                var dq=query[b].Point-query[a].Point;
                double scale=Numerics.Dot(dq,Deltas[i])/Denominators[i];
                if(scale*Unit<8 || scale*Unit>minimumDimension/4d || Numerics.Norm(dq-Deltas[i]*scale)>3) continue;
                var shift=query[a].Point-Points[pair.A]*scale;
                var key=(RoundKey(scale*Unit*4),RoundKey(shift.X/3),RoundKey(shift.Y/3));
                if(!seen.Add(key)) continue;
                seeds.Add(new(scale,shift,prepared?.Radius[i]??0,seeds.Count));
            }
        }
        // The prepared path discards an unfinished seed batch, as the original
        // _seeds does. It must never claim a complete local competition.
        if(prepared!=null && !complete) return new([],false,0,0,0,0,[0,0,0,0]);
        int local=0,outside=0,rejected=0,exact=0; var rings=new int[4];
        IEnumerable<Seed> ordered=seeds;
        if(prepared!=null)
        {
            var selected=new List<Seed>();
            var orientationGroups=prepared.CompatibleGroups(query.Count,compatible);
            foreach(var seed in seeds)
            {
                cancellation.ThrowIfCancellationRequested();
                if(seed.Radius<=3) { local++; selected.Add(seed); }
                else
                {
                    outside++;
                    if(prepared.PossibleTurns(query,orientationGroups,seed)>=4) selected.Add(seed); else rejected++;
                }
            }
            ordered=selected.OrderBy(s=>Ring(s.Radius));
        }
        var scored=new List<(int Index,Pose Pose)>(); var poses=new List<Pose>();
        foreach(var seed in ordered)
        {
            cancellation.ThrowIfCancellationRequested();
            if(expired()) { complete=false; break; }
            exact++;
            if(prepared!=null) rings[Ring(seed.Radius)]++;
            var pairs=PairsAt(seed.Scale,seed.Shift);
            if(pairs.Length<4) continue;
            var extent=Extent(pairs);
            if(Math.Min(extent.X,extent.Y)<1) continue;
            var pose=new Pose(seed.Scale,seed.Shift,pairs,extent);
            if(prepared==null)
            {
                if(!poses.Any(h=>Numerics.Norm(seed.Shift-h.Shift)+Math.Abs(seed.Scale-h.Scale)*Unit<3)) poses.Add(pose);
            }
            else scored.Add((seed.Index,pose));
        }
        if(prepared!=null)
            foreach(var (_,pose) in scored.OrderBy(p=>p.Index))
                if(!poses.Any(h=>Numerics.Norm(pose.Shift-h.Shift)+Math.Abs(pose.Scale-h.Scale)*Unit<3)) poses.Add(pose);
        var continuous=new List<Pose>();
        foreach(var h in poses.OrderByDescending(p=>p.Pairs.Length).Take(3))
        {
            var pairs=h.Pairs; double scale=h.Scale; var shift=h.Shift;
            for(int iteration=0;iteration<3;iteration++)
            {
                var src=pairs.Select(p=>Points[p.S]).ToArray(); var dst=pairs.Select(p=>query[p.Q].Point).ToArray();
                var a=Mean(src); var b=Mean(dst);
                var cross=new double[pairs.Length*2]; var square=new double[cross.Length];
                for(int i=0;i<pairs.Length;i++)
                {
                    var u=src[i]-a; var v=dst[i]-b;
                    cross[2*i]=u.X*v.X; cross[2*i+1]=u.Y*v.Y;
                    square[2*i]=u.X*u.X; square[2*i+1]=u.Y*u.Y;
                }
                scale=Pairwise(cross)/Pairwise(square); shift=b-a*scale;
                var updated=PairsAt(scale,shift);
                if(updated.Length<4) break;
                pairs=updated;
            }
            if(scale*Unit<8 || scale*Unit>minimumDimension/4d || !double.IsFinite(scale)) continue;
            continuous.Add(new(scale,shift,pairs,Extent(pairs)));
        }
        poses=[];
        foreach(var h in continuous.OrderByDescending(p=>p.Pairs.Length))
            if(!poses.Any(other=>Points.Max(p=>Numerics.Norm(p*(h.Scale-other.Scale)+h.Shift-other.Shift))<=3)) poses.Add(h);
        return new(poses,complete,exact,local,outside,rejected,rings);

        (int Q,int S)[] PairsAt(double scale,Point2d shift)
        {
            var hits=oriented.Where(p=>Numerics.Norm(query[p.Q].Point-(Points[p.S]*scale+shift))<=3).ToArray();
            var qc=new int[query.Count]; var sc=new int[Points.Length];
            foreach(var p in hits) { qc[p.Q]++; sc[p.S]++; }
            return hits.Where(p=>qc[p.Q]==1 && sc[p.S]==1).ToArray();
        }
        Point2d Extent((int Q,int S)[] pairs)
        {
            var ps=pairs.Select(p=>Points[p.S]).ToArray();
            return new((ps.Max(p=>p.X)-ps.Min(p=>p.X))/Unit,(ps.Max(p=>p.Y)-ps.Min(p=>p.Y))/Unit);
        }
    }

    internal JsonObject RowFor(Pose pose,string profile)
    {
        var matched=pose.Pairs.Select(p=>Points[p.S]).ToArray();
        var gates=Row.GetProperty("gates").EnumerateArray().Where(g=>matched.Min(p=>Numerics.Norm(p-Point(g.GetProperty("center"))))<=12*Unit).ToArray();
        return new() { ["mapId"]=MapId,["floor"]=Floor,["region"]=JsonNode.Parse(Row.GetProperty("region").GetRawText()),
            ["profileId"]=profile,["entryKind"]=gates.Length>0?"side-neighborhood":"local-structure",["score"]=(double)pose.Pairs.Length,
            ["hypothesis"]=pose.ToJson(),["matrixSourceToClient"]=Matrix(pose.Scale,pose.Shift),
            ["sideGates"]=new JsonArray(gates.Select(g=>JsonNode.Parse(g.GetRawText())).ToArray()),
            ["sourceProduct"]=Text(Row,"sourceProduct"),["identityVerified"]=false,
            ["evidenceScope"]="oriented measured wall candidates; side icon itself is not detected" };
    }

    private static int Ring(double radius)=>radius<=3?0:radius<=5?1:radius<=8?2:3;
    private static long RoundKey(double x)=>checked((long)Math.Round(x,MidpointRounding.ToEven));
    internal static Point2d Point(JsonElement n)=>new(n[0].GetDouble(),n[1].GetDouble());
    internal static Point2d Point(JsonNode n)=>new(Numerics.Scalar(n[0]!),Numerics.Scalar(n[1]!));
    internal static string Text(JsonElement n,string key)=>n.GetProperty(key).GetString()!;
    internal static JsonArray JsonPoint(Point2d p)=>new(p.X,p.Y);
    internal static JsonArray Matrix(double s,Point2d t)=>new(new JsonArray(s,0,t.X),new JsonArray(0,s,t.Y),new JsonArray(0,0,1));
    private static Point2d Mean(Point2d[] a)
    {
        // NumPy's axis=0 reduction walks the two strided columns in row order.
        double x=0,y=0; foreach(var p in a) { x+=p.X; y+=p.Y; } return new(x/a.Length,y/a.Length);
    }

    // NumPy float64 contiguous reduction (also used for its (N,2) cross products).
    private static double Pairwise(ReadOnlySpan<double> values)
    {
        if(values.Length<8) { double sum=-0d; foreach(double v in values) sum+=v; return sum; }
        if(values.Length>128) { int cut=values.Length/2/8*8; return Pairwise(values[..cut])+Pairwise(values[cut..]); }
        Span<double> r=stackalloc double[8]; values[..8].CopyTo(r); int i=8;
        for(;i+7<values.Length;i+=8) for(int j=0;j<8;j++) r[j]+=values[i+j];
        double result=((r[0]+r[1])+(r[2]+r[3]))+((r[4]+r[5])+(r[6]+r[7]));
        for(;i<values.Length;i++) result+=values[i]; return result;
    }
}
