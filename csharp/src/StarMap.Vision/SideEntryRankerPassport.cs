using System.Diagnostics;
using System.Text.Json;
using System.Text.Json.Nodes;
using OpenCvSharp;

namespace StarMap.Vision;

public sealed partial class SideEntryRanker
{
    /// <summary>Production rank_entry: measured exits, complete passport inventory, local walls/rooms and current typed pixels.</summary>
    public SideEntryResult RankEntry(Mat frame,string? floor,TimeSpan? budget=null,IReadOnlySet<string>? candidateMapIds=null,
        CancellationToken cancellation=default)
    {
        cancellation.ThrowIfCancellationRequested();
        var clock=Stopwatch.StartNew(); var limit=budget??TimeSpan.FromSeconds(2);
        using var pixels=frame.Clone(); using var observed=new EntryPassportFrame(catalog,pixels);
        var preflight=observed.Inspect(floor,limit-clock.Elapsed,cancellation);
        var meta=preflight.Diagnostics; var rows=new List<JsonObject>();
        if(!preflight.NeedsLocalGeometry) return Finish(meta["reason"]!.ToString());
        meta["complete"]=true;
        using var context=EntryObservation.Capture(pixels);
        if(context==null) return Finish("entry-observation-unavailable");
        var qlines=Geometry.Lines(pixels,context.Geometry);
        if(qlines.Count==0) return Finish("entry-observed-walls-insufficient");
        var observations=meta["observations"]!.AsArray();
        var exits=observations.Where(o=>o!["reliable"]!.GetValue<bool>()).ToArray();
        var examined=meta["examined"]!.AsArray();
        var products=new Dictionary<(string,string),StructureGeometry?>();
        try
        {
        for(int observationId=0;observationId<exits.Length;observationId++)
        {
            cancellation.ThrowIfCancellationRequested();
            var exit=exits[observationId]!.AsObject(); var rectangle=exit["rectangle"] as JsonObject; var direction=exit["direction"]!;
            if(direction["reliable"]?.GetValue<bool>()!=true || rectangle?["success"]?.GetValue<bool>()!=true)
            {
                meta["complete"]=false; examined.Add(new JsonObject { ["observation"]=observationId,["reason"]="exit-direction-or-local-width-unresolved" }); continue;
            }
            string outward=direction["direction"]!.ToString() switch { "左"=>"W","右"=>"E","上"=>"N","下"=>"S",_=>throw new InvalidDataException("Unknown reliable exit direction.") };
            var bounds=rectangle["bounds"]!; double l=Number(bounds[0]),t=Number(bounds[1]),r=Number(bounds[2]),b=Number(bounds[3]);
            double width=outward is "E" or "W"?b-t:r-l;
            var iconCenter=SideEntryRankerSource.Point(exit["icon"]!["center"]!);
            var localCenter=outward switch { "W"=>new Point2d(l+width/2,(t+b)/2),"E"=>new Point2d(r-width/2,(t+b)/2),
                "N"=>new Point2d((l+r)/2,t+width/2),_=>new Point2d((l+r)/2,b-width/2) };
            foreach(var item in catalog.Passports)
            {
                cancellation.ThrowIfCancellationRequested();
                string mid=Text(item,"mapId"),f=Text(item,"floor");
                if(floor!=null && f!=floor || candidateMapIds!=null && !candidateMapIds.Contains(mid)) continue;
                if(Expired()) return Incomplete("entry-budget-incomplete");
                var report=new JsonObject { ["mapId"]=mid,["floor"]=f,["sourceEntryId"]=Text(item,"sourceEntryId"),["family"]=Text(item,"family"),["observation"]=observationId };
                examined.Add(report);
                if(item.GetProperty("directionKnown").GetBoolean() && !item.GetProperty("outwardDirections").EnumerateArray().Any(d=>d.GetString()==outward))
                { report["reason"]="source-exit-direction-incompatible"; continue; }
                var slines=SideEntryRankerLocalFit.Walls(item.GetProperty("knownWallLines"));
                var fitter=new SideEntryRankerLocalFit(qlines,slines);
                var center=SideEntryRankerSource.Point(item.GetProperty("center")); double initial=width/item.GetProperty("unit").GetDouble();
                var fits=new List<SideEntryRankerLocalFit.Fit>();
                foreach(double ratio in new[]{1d,.97,1.03,.94,1.06})
                    foreach(var anchor in new[]{localCenter,iconCenter})
                    {
                        var fit=fitter.Evaluate(initial*ratio,anchor-center*(initial*ratio),cancellation);
                        if(fit!=null && Bound(fit)) fits.Add(fit);
                    }
                report["partialPoses"]=fits.Count;
                fits=fits.Distinct<SideEntryRankerLocalFit.Fit>(ReferenceEqualityComparer.Instance).ToList();
                report["reason"]=fits.Count>0?"local-wall-pose-candidates":"exit-local-wall-geometry-incompatible"; report["poses"]=fits.Count;
                if(fits.Count>0)
                {
                    report["localWallCorrespondenceDomain"]=new JsonObject { ["queryLines"]=SideEntryRankerLocalFit.WallsJson(qlines),["sourceLines"]=JsonNode.Parse(item.GetProperty("knownWallLines").GetRawText()) };
                    report["initialPoses"]=new JsonArray(fits.Select(fit=>(JsonNode)new JsonObject { ["matrixSourceToClient"]=MatrixJson(fit.Matrix),["planes"]=fit.Planes.DeepClone() }).ToArray());
                }
                var verified=new Dictionary<(double,double,double),JsonObject>();
                foreach(var fit in fits)
                {
                    if(Expired()) return Incomplete("entry-budget-incomplete");
                    var chain=new List<SideEntryRankerLocalFit.Fit> {fit}; bool converged=false;
                    for(int iteration=0;iteration<4;iteration++)
                    {
                        cancellation.ThrowIfCancellationRequested();
                        if(Expired()) return Incomplete("entry-budget-incomplete");
                        var matrix=chain[^1].Matrix;
                        var next=fitter.Evaluate(matrix[0][0],new(matrix[0][2],matrix[1][2]),cancellation);
                        if(next==null) break;
                        if(Equal(matrix,next.Matrix)) { converged=true; break; }
                        if(!Bound(next) || chain.Any(previous=>Equal(previous.Matrix,next.Matrix))) break;
                        bool extends=Extends(chain[^1],next); chain.Add(next); if(!extends) break;
                    }
                    var evaluated=new List<JsonObject>(); bool finished=true;
                    for(int i=chain.Count-1;i>=0;i--)
                    {
                        cancellation.ThrowIfCancellationRequested();
                        if(Expired()) { finished=false; break; }
                        var row=BuildRow(chain[i]); evaluated.Add(row);
                        if(i==chain.Count-1 && converged && IsTrue(row["verification"],"success") && IsTrue(row["entryEvidence"],"localGeometryVerified")) break;
                    }
                    var history=new JsonArray(chain.Select(stage=>
                    {
                        var check=evaluated.FirstOrDefault(row=>Equal(Numerics.Matrix(row["matrixSourceToClient"]!.AsArray()),stage.Matrix))?["verification"];
                        return (JsonNode)new JsonObject { ["matrixSourceToClient"]=MatrixJson(stage.Matrix),["planes"]=stage.Planes.DeepClone(),["heldOutNormals"]=stage.HeldOut.DeepClone(),
                            ["verification"]=check?.DeepClone(),["status"]=check!=null?"current-frame-verified":"intermediate-fit-not-authorized" };
                    }).ToArray());
                    foreach(var row in evaluated)
                    { row["entryEvidence"]!["localRefinementHistory"]=history.DeepClone(); row["entryEvidence"]!["localRefinementConverged"]=converged; rows.Add(row); }
                    var first=evaluated.FirstOrDefault(row=>IsTrue(row["verification"],"success") && IsTrue(row["entryEvidence"],"localGeometryVerified"));
                    if(first!=null)
                    {
                        first["competitionComplete"]=false; meta["complete"]=false; meta["verificationComplete"]=false; meta["entryObserved"]=true;
                        meta["selectionPolicy"]="first-verified-entry"; return Finish("entry-first-verified");
                    }
                    if(!finished) return Incomplete("entry-budget-incomplete");
                    if(rows.Count>64) return Incomplete("entry-candidate-budget-incomplete");
                }
                bool Bound(SideEntryRankerLocalFit.Fit fit)=>Numerics.Norm(Project(center,fit.Matrix)-iconCenter)<=Math.Max(6,width*.2) && Math.Abs(fit.Matrix[0][0]/initial-1)<=.08;
                JsonObject BuildRow(SideEntryRankerLocalFit.Fit fit)
                {
                    var room=RoomEvidence(item,fit.Matrix,qlines,context.Geometry.Query);
                    var row=new JsonObject { ["mapId"]=mid,["floor"]=f,["region"]=JsonNode.Parse(item.GetProperty("region").GetRawText()),["profileId"]=catalog.MapProfiles[mid],
                        ["sourceEntryId"]=Text(item,"sourceEntryId"),["entryKind"]=Text(item,"family")=="side"?"side-entry-passport":"main-entry-passport",["score"]=(double)fit.Planes.Count,
                        ["matrixSourceToClient"]=MatrixJson(fit.Matrix),["identityVerified"]=false,["sourceProduct"]=Text(item,"sourceProduct"),
                        ["entryEvidence"]=new JsonObject { ["sourceEntryId"]=Text(item,"sourceEntryId"),["observationId"]=observationId,["exit"]=exit.DeepClone(),
                            ["directionCompatible"]=true,["sourceDirectionKnown"]=item.GetProperty("directionKnown").GetBoolean(),["localGeometryVerified"]=room["verified"]!.DeepClone(),
                            ["roomGeometry"]=room,["refinement"]=new JsonObject { ["kind"]="continuous-local-wall-normal-fit",["planes"]=fit.Planes.DeepClone(),["heldOutNormals"]=fit.HeldOut.DeepClone() } } };
                    var key=(fit.Matrix[0][0],fit.Matrix[0][2],fit.Matrix[1][2]);
                    if(!verified.TryGetValue(key,out var check))
                    {
                        if(!products.TryGetValue((mid,f),out var product)) products[(mid,f)]=product=OpenProduct(mid,f);
                        verified[key]=check=product==null?new JsonObject { ["success"]=false,["reason"]="side-resource-unavailable" }:
                            SideEntryRankerVerification.Verify(pixels,context.Geometry,row,product,item);
                    }
                    row["verification"]=check.DeepClone();
                    var all=EntryPassportEvidence.CheckObserved(catalog,row,observations); row["entryEvidence"]!["allObservedExits"]=all;
                    if(IsTrue(all,"contradicted")) { row["verification"]!["success"]=false; row["verification"]!["reason"]="entry-other-current-exit-contradiction"; }
                    return row;
                }
            }
        }
        var (retained,groups)=PoseGroups(rows,context.Geometry.Bounds); rows=retained; meta["equivalentVerifiedPoseGroups"]=groups;
        var possible=rows.Where(row=>IsTrue(row["verification"],"success")).ToList();
        var family=rows.Where(row=>IsTrue(row["entryEvidence"],"localGeometryVerified") && IsTrue(row["entryEvidence"],"directionCompatible") && IsTrue(row["entryEvidence"]?["exit"],"reliable")).ToList();
        bool side=family.Any(row=>row["entryKind"]!.ToString()=="side-entry-passport"),main=family.Any(row=>row["entryKind"]!.ToString()=="main-entry-passport");
        bool complete=meta["complete"]!.GetValue<bool>();
        meta["entryObserved"]=side && !main && complete; meta["verificationComplete"]=complete;
        meta["identityAmbiguous"]=possible.Select(row=>(row["mapId"]!.ToString(),row["floor"]!.ToString())).Distinct().Count()!=1;
        meta["poseAmbiguous"]=possible.Count!=1;
        foreach(var row in rows) { row["competitionComplete"]=complete; row["poseAmbiguous"]=possible.Count(p=>p["mapId"]!.ToString()==row["mapId"]!.ToString() && p["floor"]!.ToString()==row["floor"]!.ToString())>1; }
        return Finish(main || !side?"entry-family-unresolved":possible.Count==0 && complete?"entry-local-verification-failed":complete?"entry-local-competition":"entry-local-observation-incomplete");
        }
        finally { foreach(var product in products.Values) product?.Dispose(); }

        bool Expired()=>clock.Elapsed>=limit;
        SideEntryResult Incomplete(string reason) { meta["complete"]=false; return Finish(reason); }
        SideEntryResult Finish(string reason)
        {
            meta["reason"]=reason; meta["elapsedMs"]=clock.Elapsed.TotalMilliseconds;
            return new(new JsonArray(rows.Select(row=>(JsonNode)row).ToArray()),meta,new());
        }
    }

    private StructureGeometry? OpenProduct(string mapId,string floor)
    {
        if(!catalog.MapProfiles.TryGetValue(mapId,out var profile)) return null;
        var entry=resources.Find(catalog.Mode,catalog.Season,profile,mapId,floor);
        var data=entry==null?null:resources.LoadSharedStructure(entry).Value;
        return data?.CleanMask==null?null:StructureGeometry.Open(data);
    }
    private static bool IsTrue(JsonNode? row,string key)=>row?[key]?.GetValue<bool>()==true;
    private static double Number(JsonNode? node)=>Numerics.Scalar(node!);
    private static string Text(JsonElement item,string key)=>item.GetProperty(key).GetString()!;
    private static JsonArray MatrixJson(double[][] matrix)=>new(matrix.Select(row=>(JsonNode)new JsonArray(row.Select(v=>(JsonNode)JsonValue.Create(v)!).ToArray())).ToArray());
    private static Point2d Project(Point2d point,double[][] matrix)=>new(point.X*matrix[0][0]+point.Y*matrix[0][1]+matrix[0][2],point.X*matrix[1][0]+point.Y*matrix[1][1]+matrix[1][2]);
    private static bool Equal(double[][] a,double[][] b)=>a.SelectMany(x=>x).SequenceEqual(b.SelectMany(x=>x));
    private static bool Extends(SideEntryRankerLocalFit.Fit a,SideEntryRankerLocalFit.Fit b)=>a.Planes.All(g=>
        g!["queryEdge"]!=null && g["sourceEdge"]!=null && b.Planes.Any(h=>new[]{"axis","sign","queryEdge","sourceEdge"}.All(key=>JsonNode.DeepEquals(g[key],h![key]))));

    private static JsonObject RoomEvidence(JsonElement item,double[][] matrix,IReadOnlyList<Wall> query,Mat mask)
    {
        var witnesses=new JsonArray(); var pixels=Geometry.Bytes(mask); double scale=matrix[0][0],unit=item.GetProperty("unit").GetDouble();
        var center=SideEntryRankerSource.Point(item.GetProperty("center"));
        foreach(var room in item.GetProperty("localInstances").EnumerateArray())
        {
            if(Text(room,"kind")!="room" || room.GetProperty("connections").GetArrayLength()==0) continue;
            var planes=new List<(int,int,double)>();
            foreach(var source in SideEntryRankerLocalFit.Walls(room.GetProperty("knownWallLines")))
                foreach(var line in query)
                {
                    double normal=scale*source.Normal+matrix[source.Axis][2],lo=scale*source.Lo+matrix[1-source.Axis][2],hi=scale*source.Hi+matrix[1-source.Axis][2];
                    var key=(source.Axis,line.Sign,line.Normal);
                    if(line.Axis==source.Axis && line.Sign==source.Sign && Math.Abs(normal-line.Normal)<=3 && Math.Min(hi,line.Hi)-Math.Max(lo,line.Lo)>=12 && !planes.Contains(key)) planes.Add(key);
                }
            var ports=new JsonArray();
            foreach(var port in room.GetProperty("ports").EnumerateArray())
            {
                if(Text(port,"name")!=Text(room,"connectingPort")) continue;
                var points=port.GetProperty("points").EnumerateArray().Select(SideEntryRankerSource.Point).ToArray();
                var p=new Point2d(points.Sum(v=>v.X)/points.Length,points.Sum(v=>v.Y)/points.Length)*unit+center;
                p=Project(p,matrix); int x=Numerics.Rint(p.X),y=Numerics.Rint(p.Y);
                if(x<1 || y<1 || x>=mask.Cols-1 || y>=mask.Rows-1) continue;
                int sum=0; for(int yy=y-1;yy<=y+1;yy++) for(int xx=x-1;xx<=x+1;xx++) sum+=pixels[yy*mask.Cols+xx];
                if(sum/9d>=.5) ports.Add(Text(port,"name"));
            }
            if(planes.Count>=2 && ports.Count>0)
                witnesses.Add(new JsonObject { ["instance"]=Text(room,"instance"),["material"]=JsonNode.Parse(room.GetProperty("material").GetRawText()),
                    ["roomTypeId"]=JsonNode.Parse(room.GetProperty("roomTypeId").GetRawText()),["rotation"]=JsonNode.Parse(room.GetProperty("rotation").GetRawText()),
                    ["supportedPlanes"]=new JsonArray(planes.Select(p=>(JsonNode)new JsonArray(p.Item1,p.Item2,p.Item3)).ToArray()),["visiblePorts"]=ports,
                    ["connectionPath"]=JsonNode.Parse(room.GetProperty("connections").GetRawText()) });
        }
        return new() { ["verified"]=witnesses.Count>0,["rooms"]=witnesses };
    }

    private static (List<JsonObject>,JsonArray) PoseGroups(List<JsonObject> rows,Rect bounds)
    {
        var retained=new List<JsonObject>(); var groups=new List<List<JsonObject>>();
        foreach(var row in rows)
        {
            if(!IsTrue(row["verification"],"success") || !IsTrue(row["entryEvidence"],"localGeometryVerified")) { retained.Add(row); continue; }
            var group=groups.FirstOrDefault(g=>g.All(other=>SamePose(row,other)));
            if(group==null) { groups.Add([row]); retained.Add(row); } else group.Add(row);
        }
        return (retained,new JsonArray(groups.Where(g=>g.Count>1).Select(g=>(JsonNode)new JsonArray(g.Select(row=>row.DeepClone()).ToArray())).ToArray()));
        bool SamePose(JsonObject a,JsonObject b)
        {
            if(new[]{"mapId","floor","sourceEntryId","entryKind"}.Any(key=>!JsonNode.DeepEquals(a[key],b[key]))) return false;
            var left=Numerics.Matrix(a["matrixSourceToClient"]!.AsArray()); var right=Numerics.Matrix(b["matrixSourceToClient"]!.AsArray());
            Point2d[] client=[new(bounds.Left,bounds.Top),new(bounds.Right,bounds.Top),new(bounds.Left,bounds.Bottom),new(bounds.Right,bounds.Bottom)];
            return new[]{left,right}.All(domain=>client.All(point=>
            {
                var source=new Point2d((point.X-domain[0][2])/domain[0][0],(point.Y-domain[1][2])/domain[0][0]);
                return Numerics.Norm(Project(source,left)-Project(source,right))<=3;
            }));
        }
    }
}
