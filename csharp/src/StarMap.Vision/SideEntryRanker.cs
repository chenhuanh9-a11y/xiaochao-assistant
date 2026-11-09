using System.Diagnostics;
using System.Text.Json;
using System.Text.Json.Nodes;
using OpenCvSharp;
using StarMap.Core.Identity;
using StarMap.Core.Resources;

namespace StarMap.Vision;

public sealed record SideEntryResult(JsonArray Rows,JsonObject Diagnostics,JsonArray Turns);

/// <summary>Stateless current-frame side structure proposals. It never grants identity, display or session state.</summary>
public sealed partial class SideEntryRanker
{
    private readonly EntryPassportCatalog catalog;
    private readonly ResourceCatalog resources;
    private readonly SideEntryRankerSource[] sources;
    private readonly SideEntryRankerIndex index;

    public SideEntryRanker(EntryPassportCatalog catalog,ResourceCatalog resources)
    {
        if(!string.Equals(Path.TrimEndingDirectorySeparator(catalog.ResourceRoot),Path.TrimEndingDirectorySeparator(resources.RootPath),
            OperatingSystem.IsWindows()?StringComparison.OrdinalIgnoreCase:StringComparison.Ordinal))
            throw new ArgumentException("Entry graph and typed structure provider must share a resource root.");
        this.catalog=catalog; this.resources=resources;
        sources=catalog.SideEntries.Select(row=>new SideEntryRankerSource(row)).ToArray();
        index=new(catalog,sources);
    }

    /// <summary>The full client frame and complete registered source universe generate poses; no pose/map ground truth is accepted.</summary>
    public SideEntryResult Rank(Mat frame,string? floor,TimeSpan? budget=null,string? knownMapId=null,
        IReadOnlySet<string>? candidateMapIds=null,CancellationToken cancellation=default)
    {
        cancellation.ThrowIfCancellationRequested();
        var clock=Stopwatch.StartNew(); var limit=budget??TimeSpan.FromSeconds(2);
        var rows=new List<JsonObject>(); var turns=new JsonArray();
        var meta=new JsonObject();
        bool Expired()=>clock.Elapsed>=limit;
        if(floor!=null && !catalog.Floors.Contains(floor)) return Finish("unsupported-floor",true);
        if(limit<=TimeSpan.Zero) return Finish("side-budget-incomplete",false);
        using var pixels=frame.Clone();
        using var context=EntryObservation.Capture(pixels);
        var query=context==null?new List<Corner>():MainEntryGate.Corners(pixels,context.Geometry);
        // Rebuild adjacency after support filtering; stale contour indexes must
        // never become correspondence edges in this smaller measured graph.
        List<Corner> originals=context==null?[]:Geometry.Nodes(context.Geometry);
        var lookup=new Dictionary<Corner,int>(ReferenceEqualityComparer.Instance);
        for(int i=0;i<query.Count;i++) lookup.Add(query[i],i);
        var qpairs=query.Select((n,i)=>(A:i,B:n.Next)).Where(p=>p.B>=0 && p.B<originals.Count && lookup.ContainsKey(originals[p.B]))
            .Select(p=>(p.A,lookup[originals[p.B]])).ToArray();
        foreach(var n in query) turns.Add(new JsonObject { ["point"]=SideEntryRankerSource.JsonPoint(n.Point),
            ["rays"]=new JsonArray(n.Rays.Select(p=>(JsonNode)SideEntryRankerSource.JsonPoint(p)).ToArray()),
            ["lengths"]=JsonSerializer.SerializeToNode(n.Lengths),["next"]=n.Next });
        if(query.Count<4 || qpairs.Length==0)
        {
            meta["queryTurns"]=query.Count; meta["minimumQueryTurns"]=4; meta["queryPairs"]=qpairs.Length;
            return Finish("side-visible-structure-insufficient",true);
        }
        bool localSearch=floor=="2" && knownMapId==null && index.Entries.Count>0;
        bool complete=true; int seeds=0,localMaps=0,localSeeds=0,outside=0,rejected=0; var rings=new int[4];
        var evaluated=new JsonArray();
        foreach(var source in sources)
        {
            cancellation.ThrowIfCancellationRequested();
            if(floor!=null && source.Floor!=floor || knownMapId!=null && source.MapId!=knownMapId ||
                candidateMapIds!=null && !candidateMapIds.Contains(source.MapId)) continue;
            if(Expired()) { complete=false; break; }
            evaluated.Add(source.MapId+"/"+source.Floor);
            if(source.Edges.Length==0) continue;
            var prepared=localSearch && index.Entries.TryGetValue((source.MapId,source.Floor),out var found)?found:null;
            var proposed=source.Propose(query,qpairs,Math.Min(pixels.Rows,pixels.Cols),prepared,Expired,cancellation);
            complete &= proposed.Complete; seeds+=proposed.Exact;
            if(prepared!=null)
            {
                localMaps++; localSeeds+=proposed.Local; outside+=proposed.Outside; rejected+=proposed.Rejected;
                for(int i=0;i<4;i++) rings[i]+=proposed.Rings[i];
            }
            foreach(var pose in proposed.Poses) rows.Add(source.RowFor(pose,catalog.MapProfiles[source.MapId]));
        }
        rows=rows.OrderByDescending(Score).ToList();
        double top=rows.Count>0?Score(rows[0]):0;
        var competitors=rows.Where(r=>Score(r)>=Math.Max(4,top-1)).ToList();
        List<JsonObject>? optionalDoor=null;
        if(localSearch && complete && top>=6 && competitors.Count>1 && !Expired())
        {
            var observedAt=clock.Elapsed;
            try
            {
                if(catalog.ExitIconPath==null) throw new NotSupportedException("This context has no side-exit observer.");
                using var detector=new SideDoorObservation(catalog.ExitIconPath);
                var observed=detector.Observe(pixels,context,cancellation).Boundary;
                if(observed!=null)
                {
                    foreach(var row in rows)
                    {
                        string mid=row["mapId"]!.ToString(),f=row["floor"]!.ToString();
                        var source=sources.First(s=>s.MapId==mid && s.Floor==f);
                        row["sideDoorConstraint"]=new JsonObject { ["door"]=JsonNode.Parse(index.Doors[mid].GetRawText()),
                            ["observed"]=observed.DeepClone(),["unit"]=source.Unit };
                    }
                    var possible=competitors.Where(r=>!SideEntryRankerIndex.Contradicted(r)).ToList();
                    if(possible.Count>0 && possible.Count<competitors.Count) optionalDoor=possible;
                    else foreach(var row in rows) row.Remove("sideDoorConstraint");
                }
            }
            catch(Exception e) when(e is IOException or InvalidDataException or ArgumentException or KeyNotFoundException or InvalidOperationException or OpenCVException or NotSupportedException)
            {
                foreach(var row in rows) row.Remove("sideDoorConstraint");
                meta["sideOptionalDoorError"]=e.Message;
            }
            meta["sideOptionalDoorMs"]=(clock.Elapsed-observedAt).TotalMilliseconds;
        }
        bool verificationComplete=complete;
        var active=optionalDoor??competitors;
        // Resource decoding is bounded to the at-most-three current competitors.
        // Their exact typed reference is reused only within this rank.
        var products=new Dictionary<(string,string),StructureGeometry?>();
        try
        {
            VerifyRows(active);
            if(optionalDoor!=null)
            {
                bool usable=active.Any(r=>True(r["verification"],"success") && True(r["verification"]?["residual"],"verified") &&
                    r["hypothesis"]!["chainCount"]!.GetValue<int>()>=6 && r["hypothesis"]!["extentGrid"]!.AsArray().Min(v=>Numerics.Scalar(v!))>=2);
                if(!usable)
                {
                    foreach(var row in rows) row.Remove("sideDoorConstraint");
                    active=competitors; optionalDoor=null;
                    VerifyRows(active);
                }
            }
        }
        finally { foreach(var p in products.Values) p?.Dispose(); }
        foreach(var row in rows)
        {
            if(row["verification"]==null)
            {
                string reason=SideEntryRankerIndex.Contradicted(row)?"current-side-door-geometry":
                    !complete?"side-search-incomplete-unverified":!competitors.Contains(row)?"side-outside-score-band-unverified":
                    competitors.Count>3?"side-competition-limit-unverified":"side-verification-budget-incomplete";
                row["verification"]=new JsonObject { ["success"]=false,["evaluated"]=false,["reason"]=reason };
            }
            row["competitionComplete"]=complete;
            row["poseAmbiguous"]=competitors.Count(r=>r["mapId"]!.ToString()==row["mapId"]!.ToString() && r["floor"]!.ToString()==row["floor"]!.ToString())>1;
        }
        meta["queryTurns"]=query.Count; meta["competitiveHypotheses"]=competitors.Count; meta["seedCorrespondences"]=seeds;
        meta["verificationComplete"]=verificationComplete && active.Count<=3;
        meta["identityAmbiguous"]=competitors.Select(r=>(r["mapId"]!.ToString(),r["floor"]!.ToString())).Distinct().Count()!=1;
        meta["localSearchMaps"]=localMaps; meta["localSeedTotal"]=localSeeds; meta["outsideSeedsScreened"]=outside;
        meta["outsideSeedsRejected"]=rejected; meta["ringEvaluations"]=JsonSerializer.SerializeToNode(rings);
        meta["sideOptionalDoorUsed"]=optionalDoor!=null; meta["sideDoorPath"]=localSearch?"door-local-index":"ordinary-structure";
        meta["sideDoorResourceError"]=index.DoorError; meta["sideLocalIndexError"]=index.SearchError;
        meta["evaluatedMapFloors"]=evaluated;
        return Finish(complete?"side-local-candidates":"side-budget-incomplete",complete);

        void VerifyRows(List<JsonObject> selected)
        {
            if(!complete || selected.Count>3) return;
            foreach(var row in selected)
            {
                cancellation.ThrowIfCancellationRequested();
                if(Expired()) { verificationComplete=false; break; }
                if(row["verification"]!=null) continue;
                string mid=row["mapId"]!.ToString(),f=row["floor"]!.ToString();
                if(!products.TryGetValue((mid,f),out var product))
                {
                    var reference=resources.Find(catalog.Mode,catalog.Season,catalog.MapProfiles[mid],mid,f);
                    var data=reference==null?null:resources.LoadSharedStructure(reference).Value;
                    product=data?.CleanMask==null?null:StructureGeometry.Open(data);
                    products.Add((mid,f),product);
                }
                row["verification"]=SideEntryRankerIndex.Contradicted(row)?
                    new JsonObject { ["success"]=false,["reason"]="side-door-boundary-contradiction" }:
                    product==null?new JsonObject { ["success"]=false,["reason"]="side-resource-unavailable" }:
                    SideEntryRankerVerification.Verify(pixels,context!.Geometry,row,product);
            }
        }
        SideEntryResult Finish(string reason,bool done)
        {
            meta["reason"]=reason; meta["complete"]=done; meta["elapsedMs"]=clock.Elapsed.TotalMilliseconds;
            var expected=catalog.PackFloors.Where(p=>floor==null || p.Floor==floor).ToHashSet();
            var available=sources.Select(s=>(s.MapId,s.Floor)).ToHashSet();
            meta["sideInventoryComplete"]=expected.IsSubsetOf(available);
            meta["sideInventoryMissingMapFloors"]=JsonSerializer.SerializeToNode(expected.Except(available).OrderBy(p=>p.MapId,StringComparer.Ordinal).ThenBy(p=>p.Floor,StringComparer.Ordinal)
                .Select(p=>new {mapId=p.MapId,floor=p.Floor}));
            meta["semanticGaps"]=JsonNode.Parse(catalog.SemanticGaps.GetRawText());
            meta["passportError"]=catalog.NativePassportError;
            return new(new JsonArray(rows.Select(r=>(JsonNode)r).ToArray()),meta,turns);
        }
    }

    private static double Score(JsonObject row)=>Numerics.Scalar(row["score"]!);
    private static bool True(JsonNode? row,string key)=>row?[key]?.GetValue<bool>()==true;
}
