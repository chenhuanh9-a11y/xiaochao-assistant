using System.Text.Json;
using System.Text.Json.Nodes;
using OpenCvSharp;
using StarMap.Core.Identity;
using StarMap.Core.Resources;
using static StarMap.Vision.SideEntryRankerSource;

namespace StarMap.Vision;

/// <summary>Only consumes the shipped optional index; never prepares live data.</summary>
internal sealed class SideEntryRankerIndex
{
    internal IReadOnlyDictionary<string,JsonElement> Doors { get; }
    internal IReadOnlyDictionary<(string MapId,string Floor),Prepared> Entries { get; }
    internal string? DoorError { get; }
    internal string? SearchError { get; }

    internal sealed class Prepared
    {
        internal double[] Radius { get; }
        private readonly double cell;
        private readonly byte[] lower;
        private readonly int[] groups;
        private readonly int groupCount,width,height;
        internal Prepared(double[] radius,double cell,byte[] lower,int[] groups,int groupCount,int width,int height)
        { Radius=radius; this.cell=cell; this.lower=lower; this.groups=groups; this.groupCount=groupCount; this.width=width; this.height=height; }

        internal int[][] CompatibleGroups(int queryCount,bool[,] compatible)
        {
            var result=new int[queryCount][];
            for(int qi=0;qi<queryCount;qi++)
            {
                var selected=new bool[groupCount];
                for(int si=0;si<groups.Length;si++) if(compatible[qi,si]) selected[groups[si]]=true;
                result[qi]=Enumerable.Range(0,groupCount).Where(g=>selected[g]).ToArray();
            }
            return result;
        }
        internal int PossibleTurns(IReadOnlyList<Corner> query,int[][] compatibleGroups,Seed seed)
        {
            int count=0;
            for(int qi=0;qi<query.Count;qi++)
            {
                var selected=compatibleGroups[qi];
                if(selected.Length==0) continue;
                var p=new Point2d((query[qi].Point.X-seed.Shift.X)/seed.Scale/cell,
                    (query[qi].Point.Y-seed.Shift.Y)/seed.Scale/cell);
                double rx=Math.Round(p.X,MidpointRounding.ToEven),ry=Math.Round(p.Y,MidpointRounding.ToEven);
                // Out-of-field points stay possible: clipping must never hide a rival.
                if(rx<0 || rx>=width || ry<0 || ry>=height) { count++; continue; }
                int pixel=(int)ry*width+(int)rx; byte distance=255;
                foreach(int g in selected) distance=Math.Min(distance,lower[g*width*height+pixel]);
                if(distance*seed.Scale<=3) count++;
            }
            return count;
        }
    }

    internal SideEntryRankerIndex(EntryPassportCatalog catalog,SideEntryRankerSource[] sources)
    {
        var doors=new Dictionary<string,JsonElement>(StringComparer.Ordinal);
        if(catalog.Mode!="nightmare") DoorError="not-nightmare";
        else
        {
            try
            {
                var data=Read(Path.Combine(catalog.EntryRoot,"side-door-anchors.json"));
                var index=Read(Path.Combine(Path.GetDirectoryName(catalog.EntryRoot)!,"nightmare-shared-structure-index.json"));
                if(data.GetProperty("schemaVersion").GetInt32()!=1 || Text(data,"mode")!=catalog.Mode || Text(data,"profileId")!=catalog.ProfileId)
                    throw new InvalidDataException("side-door mode/profile mismatch");
                var targets=sources.Where(s=>s.Floor=="2").ToDictionary(s=>s.MapId,StringComparer.Ordinal);
                var bindings=index.GetProperty("entries").EnumerateArray().Where(r=>Text(r,"profile")==catalog.ProfileId && Text(r,"floor")=="2")
                    .ToDictionary(r=>Text(r,"mapId"),StringComparer.Ordinal);
                foreach(var door in data.GetProperty("entries").EnumerateArray()) doors.Add(Text(door,"mapId"),door);
                if(targets.Count==0 || !doors.Keys.ToHashSet(StringComparer.Ordinal).SetEquals(targets.Keys))
                    throw new InvalidDataException("side-door inventory incomplete");
                foreach(var (mid,door) in doors)
                {
                    var item=targets[mid]; var binding=bindings[mid];
                    if(Text(door,"floor")!="2" || Text(door,"structureVersionKey")!=Text(binding,"structureVersionKey") ||
                        Text(door,"sourceVersion")!=Text(binding,"sourceVersion") ||
                        Path.GetFileName(Text(binding,"resourcePath"))!=Text(item.Row,"sourceProduct") ||
                        Text(door,"layoutId")!=Text(item.Row.GetProperty("canvas"),"layout") ||
                        Text(door,"outwardDirection") is not ("N" or "S" or "W" or "E"))
                        throw new InvalidDataException("side-door source mismatch: "+mid);
                    var center=Point(door.GetProperty("boundaryCenter")); var canvas=item.Row.GetProperty("canvas");
                    if(!double.IsFinite(center.X) || !double.IsFinite(center.Y) || center.X<0 || center.Y<0 ||
                        center.X>=canvas.GetProperty("width").GetDouble() || center.Y>=canvas.GetProperty("height").GetDouble())
                        throw new InvalidDataException("side-door boundary outside canvas: "+mid);
                }
            }
            catch(Exception e) when(OptionalFailure(e)) { DoorError=e.Message; doors.Clear(); }
        }
        Doors=doors;
        var prepared=new Dictionary<(string,string),Prepared>();
        if(doors.Count==0) SearchError="side-door-source-unavailable";
        else
        {
            try
            {
                var targets=sources.Where(s=>s.Floor=="2").ToDictionary(s=>(s.MapId,s.Floor));
                using var archive=NpzArchive.Open(Path.Combine(catalog.EntryRoot,"side-entry-search.npz"));
                using var metadata=JsonDocument.Parse(archive.ReadArray("metadata").ReadUnicodeScalar());
                if(metadata.RootElement.GetProperty("schemaVersion").GetInt32()!=1) throw new InvalidDataException("side local index version mismatch");
                foreach(var record in metadata.RootElement.GetProperty("entries").EnumerateArray())
                {
                    var binding=(Text(record,"mapId"),Text(record,"floor"));
                    var source=targets[binding]; var door=doors[source.MapId];
                    if(Text(record,"sourceVersion")!=Text(door,"sourceVersion") || Text(record,"structureVersionKey")!=Text(door,"structureVersionKey"))
                        throw new InvalidDataException("side local index source mismatch");
                    string key=Text(record,"key");
                    NpyArray Get(string name)=>archive.ReadArray(key+"_"+name);
                    var points=Get("points"); var rays=Get("rays"); var pairs=Get("pairs");
                    if(!Shape(points,source.Points.Length,2) || !Shape(rays,source.Points.Length,2,2) || !Shape(pairs,source.Edges.Length,2) ||
                        !Enumerable.Range(0,source.Points.Length).All(i=>points.ReadNumericValue(i*2)==source.Points[i].X && points.ReadNumericValue(i*2+1)==source.Points[i].Y &&
                            Enumerable.Range(0,2).All(j=>rays.ReadNumericValue(i*4+j*2)==source.Rays[i][j].X && rays.ReadNumericValue(i*4+j*2+1)==source.Rays[i][j].Y)) ||
                        !Enumerable.Range(0,source.Edges.Length).All(i=>pairs.ReadNumericValue(i*2)==source.Edges[i].A && pairs.ReadNumericValue(i*2+1)==source.Edges[i].B))
                        throw new InvalidDataException("side local index contour mismatch");
                    var lower=Get("lower"); var radius=Get("radius"); var deltas=Get("deltas"); var denoms=Get("denoms");
                    var groups=Get("groups"); var first=Get("first"); var cell=Get("cell");
                    if(lower.Rank!=3 || lower.Dtype!="|u1" || !Shape(radius,source.Edges.Length) || !Shape(deltas,source.Edges.Length,2) ||
                        !Shape(denoms,source.Edges.Length) || !Shape(groups,source.Points.Length) || !Shape(first,lower.Shape[0]) ||
                        cell.Rank!=0 || cell.ReadNumericValue(0)!=source.Unit/12)
                        throw new InvalidDataException("invalid side local index arrays");
                    // np.unique rounds then sorts the flattened four ray components lexicographically.
                    var rounded=source.Rays.Select(r=>(A:Numerics.Rint(r[0].X),B:Numerics.Rint(r[0].Y),C:Numerics.Rint(r[1].X),D:Numerics.Rint(r[1].Y))).ToArray();
                    var order=Enumerable.Range(0,rounded.Length).OrderBy(i=>rounded[i].A).ThenBy(i=>rounded[i].B)
                        .ThenBy(i=>rounded[i].C).ThenBy(i=>rounded[i].D).ToArray();
                    var ids=new Dictionary<(int,int,int,int),int>();
                    foreach(int i in order) if(!ids.ContainsKey(rounded[i])) ids.Add(rounded[i],ids.Count);
                    var groupValues=Enumerable.Range(0,source.Points.Length).Select(i=>checked((int)groups.ReadNumericValue(i))).ToArray();
                    if(ids.Count!=lower.Shape[0] || groupValues.Where((g,i)=>g!=ids[rounded[i]]).Any()) throw new InvalidDataException("side local index orientation mismatch");
                    prepared.Add(binding,new(Enumerable.Range(0,source.Edges.Length).Select(i=>radius.ReadNumericValue(i)).ToArray(),
                        cell.ReadNumericValue(0),lower.Bytes.ToArray(),groupValues,lower.Shape[0],lower.Shape[2],lower.Shape[1]));
                }
                if(!prepared.Keys.ToHashSet().SetEquals(targets.Keys)) throw new InvalidDataException("side local index inventory incomplete");
            }
            catch(Exception e) when(OptionalFailure(e)) { SearchError=e.Message; prepared.Clear(); }
        }
        Entries=prepared;
    }

    internal static bool Contradicted(JsonObject row)
    {
        if(row["sideDoorConstraint"] is not JsonObject c) return false;
        var door=c["door"]!; var observed=c["observed"]!;
        if(door["outwardDirection"]!.ToString()!=observed["direction"]!.ToString()) return true;
        var m=Numerics.Matrix(row["matrixSourceToClient"]!.AsArray());
        return Numerics.Norm(Point(door["boundaryCenter"]!)*m[0][0]+new Point2d(m[0][2],m[1][2])-Point(observed["center"]!))>
            Math.Max(6,m[0][0]*Numerics.Scalar(c["unit"]!)*.2);
    }
    private static JsonElement Read(string path) { using var doc=JsonDocument.Parse(File.ReadAllBytes(path)); return doc.RootElement.Clone(); }
    private static bool Shape(NpyArray array,params int[] shape)=>array.Shape.SequenceEqual(shape);
    private static bool OptionalFailure(Exception e)=>e is IOException or UnauthorizedAccessException or JsonException or InvalidDataException or
        InvalidOperationException or KeyNotFoundException or ArgumentException or OverflowException or IndexOutOfRangeException;
}
