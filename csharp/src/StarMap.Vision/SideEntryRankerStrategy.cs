using System.Diagnostics;
using System.Text.Json.Nodes;
using OpenCvSharp;

namespace StarMap.Vision;

public sealed record EntryStrategyResult(SideEntryResult Passport,MainEntryResult? Main,SideEntryResult? Side,
    JsonObject? SideWinner,JsonObject Diagnostics,JsonObject FrontGate,JsonArray? OriginalRecoveryMapIds=null);

public sealed partial class SideEntryRanker
{
    /// <summary>Registered entry strategies in production order, sharing one frame and budget. No state/display submission occurs.</summary>
    public EntryStrategyResult RankStrategy(Mat frame,string floor,TimeSpan? budget=null,CancellationToken cancellation=default)
        => RankStrategyCore(frame,floor,budget,cancellation,null);

    internal EntryStrategyResult RankStrategyCore(Mat frame,string floor,TimeSpan? budget,CancellationToken cancellation,
        Func<IReadOnlyList<JsonObject>,IReadOnlyList<JsonObject>>? prioritize,IReadOnlySet<string>? establishedMembers=null)
    {
        cancellation.ThrowIfCancellationRequested(); var clock=Stopwatch.StartNew(); var limit=budget??TimeSpan.FromSeconds(1);
        TimeSpan Remaining()=>limit>clock.Elapsed?limit-clock.Elapsed:TimeSpan.Zero;
        using var pixels=frame.Clone();
        if(catalog.Season!="s0")
        {
            // S1 production takes qt_s1_structure_rank, not this entrance path.
            var gap=new SideEntryResult(new(),new JsonObject { ["reason"]="entry-coverage-incomplete",["complete"]=true,["exitObserved"]=null,
                ["entryObserved"]=false,["coverageComplete"]=false,["observationStatus"]="source-inventory-incomplete" },new());
            return new(gap,null,null,null,new JsonObject { ["reason"]="side-entry-strategy-unavailable",["season"]=catalog.Season,["sideProducer"]="not-run" },new());
        }
        JsonObject gate; HashSet<string>? allowed;
        if(establishedMembers is not null)
        {
            // This scope was derived from bound native conditions by the S0
            // caller. A second front-gate prefilter must not remove its rivals.
            gate=new JsonObject { ["reason"]="established-group-only",["applied"]=false,["automaticGateDetection"]=false };
            allowed=establishedMembers.ToHashSet(StringComparer.Ordinal);
        }
        else using(var context=EntryObservation.Capture(pixels))
        {
            List<Corner> corners=context==null?[]:MainEntryGate.Corners(pixels,context.Geometry);
            (gate,allowed)=MainEntryGate.Select(pixels,context,corners,catalog,floor,true);
        }
        SideEntryResult passport;
        try { passport=RankEntry(pixels,floor,Remaining(),allowed,cancellation); }
        catch(Exception e) when(ResourceFailure(e))
        {
            passport=new(new(),new JsonObject { ["reason"]="entry-resource-unavailable",["error"]=e.Message,["exitObserved"]=null,["entryObserved"]=false,["complete"]=false },new());
        }
        bool fallback=EntryIdentityDecision.PassportFallbackAllowed(passport.Diagnostics);
        bool selected=!fallback && IsTrue(passport.Diagnostics,"entryObserved"),unresolved=!fallback && !selected;
        MainEntryResult? main=null; var mainRows=new JsonArray();
        var mainDiagnostics=new JsonObject { ["reason"]=unresolved?passport.Diagnostics["reason"]!.ToString():"side-entry-path-selected",["complete"]=!unresolved };
        if(!selected && !unresolved)
        {
            // Main rank retains its original inventory and verification rules.
            // Established scope bypasses only the ordinary front-gate filter.
            main=new MainEntryRanker(catalog,resources).Rank(pixels,floor,Remaining(),cancellation:cancellation,
                enableFrontGate:establishedMembers is null,candidateMapIds:establishedMembers);
            mainRows=main.Rows; mainDiagnostics=main.Diagnostics;
            foreach(var node in mainRows.Where(r=>IsTrue(r,"identityVerified")))
            {
                var row=node!.AsObject();
                var proposed=new JsonObject { ["mapId"]=row["mapId"]!.DeepClone(),["floor"]=floor,["profileId"]=row["profileId"]!.DeepClone(),
                    ["matrixSourceToClient"]=row["verification"]!["matrixSourceToClient"]!.DeepClone() };
                try { row["entryCrossCheck"]=Check(proposed); }
                catch(Exception e) when(ResourceFailure(e)) { row["entryCrossCheck"]=new JsonObject { ["reason"]="entry-cross-check-unavailable" }; row["identityVerified"]=false; row["poseVerified"]=false; }
                if(IsTrue(row["entryCrossCheck"],"contradicted")) { row["identityVerified"]=false; row["poseVerified"]=false; }
            }
        }
        string producer="not-run"; SideEntryResult? side=null; JsonObject? winner=null; var originalRecoveryMapIds=new JsonArray();
        if(selected)
        {
            producer="passport"; side=passport;
            winner=EntryIdentityDecision.VerifiedCandidate(catalog,side.Rows,side.Diagnostics,floor);
        }
        if(!selected && fallback && !mainRows.Any(row=>IsTrue(row,"identityVerified")))
        {
            producer="local-structure";
            try
            {
                side=Rank(pixels,floor,Remaining(),candidateMapIds:allowed,cancellation:cancellation);
                if(side.Rows.Count>0 && IsTrue(side.Diagnostics,"complete") && side.Diagnostics["competitiveHypotheses"]?.GetValue<int>()==1 &&
                    Remaining()>TimeSpan.Zero && !IsTrue(side.Rows[0]!["verification"],"success"))
                {
                    side.Rows[0]=RefineCandidate(pixels,side.Rows[0]!.AsObject(),cancellation);
                    side.Diagnostics["continuousRefinementMs"]=side.Rows[0]!["continuousRefinementMs"]?.DeepClone();
                    side.Diagnostics["continuousRefinementReason"]=side.Rows[0]!["continuousRefinementReason"]?.DeepClone();
                }
                if(side.Rows.Count>0)
                {
                    double band=Math.Max(4,side.Rows.Max(row=>Number(row!["score"]))-1);
                    foreach(var row in side.Rows.Where(row=>Number(row!["score"])>=band)) row!["entryCrossCheck"]=Check(row.AsObject());
                }
                winner=SideEntryDecision.VerifiedStructureCandidate(catalog,side.Rows,side.Diagnostics,floor,mainRows,pixels.Size());
                if(winner==null && IsTrue(side.Diagnostics,"complete") && Remaining()>TimeSpan.Zero)
                {
                    var all=side.Rows.Select(row=>row!.AsObject()).ToArray();
                    var originalProposals=RecoveryProposals(all);
                    foreach(var proposal in originalProposals) originalRecoveryMapIds.Add(proposal["mapId"]!.DeepClone());
                    var reordered=prioritize?.Invoke(all)??all;
                    if(reordered.Count!=all.Length || all.Any(row=>reordered.Count(other=>ReferenceEquals(row,other))!=1)) throw new InvalidDataException("Native priority must preserve the actual producer inventory.");
                    bool priorityApplied=all.Where((row,i)=>!ReferenceEquals(row,reordered[i])).Any();
                    var proposals=priorityApplied?RecoveryProposals(reordered):originalProposals;
                    var accepted=new List<JsonObject>(); int evaluated=0; var completed=new HashSet<JsonObject>(ReferenceEqualityComparer.Instance);
                    foreach(var candidate in proposals)
                    {
                        cancellation.ThrowIfCancellationRequested(); if(Remaining()<=TimeSpan.Zero) break;
                        if(candidate["verification"]?["evaluated"]?.GetValue<bool>()==false) candidate["verification"]=VerifyCandidate(pixels,candidate,cancellation);
                        if(!IsTrue(candidate["verification"],"success") && candidate["continuousRefinementReason"]==null)
                            CopyInto(candidate,RefineCandidate(pixels,candidate,cancellation));
                        candidate["entryCrossCheck"]=Check(candidate); evaluated++; completed.Add(candidate);
                        if(IsTrue(candidate["verification"],"success") && !IsTrue(candidate["entryCrossCheck"],"contradicted")) accepted.Add(candidate);
                    }
                    side.Diagnostics["poseRecoveryEvaluated"]=evaluated; side.Diagnostics["poseRecoveryCandidates"]=proposals.Count;
                    var unverifiedPriorityRivals=new List<string>();
                    if(priorityApplied)
                    {
                        double top=all.Length>0?all.Max(row=>Number(row["score"])):0;
                        var required=originalProposals.ToList();
                        foreach(var row in all) if((IsTrue(row["verification"],"success") || Number(row["score"])>=Math.Max(4,top-1)) && !required.Contains(row)) required.Add(row);
                        unverifiedPriorityRivals=required.Where(row=>!completed.Contains(row) && !IsTrue(row["verification"],"success") &&
                            (row["continuousRefinementReason"]==null || row["verification"]?["evaluated"]?.GetValue<bool>()==false)).Select(row=>row["mapId"]!.ToString()).ToList();
                        foreach(var row in required) if(IsTrue(row["verification"],"success") && !IsTrue(row["entryCrossCheck"],"contradicted") && !accepted.Contains(row)) accepted.Add(row);
                        side.Diagnostics["groupPriorityUnverifiedCompetitors"]=new JsonArray(unverifiedPriorityRivals.Select(map=>(JsonNode)JsonValue.Create(map)!).ToArray());
                    }
                    bool equivalent=accepted.Count==1;
                    if(accepted.Count>1 && accepted.Select(row=>row["mapId"]!.ToString()).Distinct().Count()==1)
                    {
                        using var current=EntryObservation.Capture(pixels);
                        Point2d[] points=current==null?[]:MainEntryGate.Corners(pixels,current.Geometry).Select(n=>n.Point).ToArray();
                        equivalent=SideEntryDecision.EquivalentObservedPoses(new JsonArray(accepted.Select(row=>row.DeepClone()).ToArray()),points);
                    }
                    if(unverifiedPriorityRivals.Count>0) equivalent=false;
                    side.Diagnostics["poseRecoveryEquivalent"]=equivalent;
                    if(evaluated==proposals.Count && accepted.Count>0 && equivalent)
                    {
                        var candidate=accepted[0].DeepClone().AsObject(); candidate["poseAmbiguous"]=false;
                        winner=SideEntryDecision.VerifiedStructureCandidate(catalog,new JsonArray(candidate),new JsonObject { ["complete"]=true,["verificationComplete"]=true,
                            ["competitiveHypotheses"]=1,["identityAmbiguous"]=false },floor,mainRows,pixels.Size());
                    }
                }
            }
            catch(Exception e) when(ResourceFailure(e)) { side=new(side?.Rows??new(),new JsonObject { ["reason"]="side-resource-unavailable",["error"]=e.Message,["complete"]=false },side?.Turns??new()); }
        }
        cancellation.ThrowIfCancellationRequested();
        var diagnostics=new JsonObject { ["fallbackAllowed"]=fallback,["passportSelected"]=selected,["passportUnresolved"]=unresolved,["sideProducer"]=producer,
            ["sideWinnerMapId"]=winner?["mapId"]?.DeepClone(),["mainDiagnostics"]=mainDiagnostics.DeepClone(),["elapsedMs"]=clock.Elapsed.TotalMilliseconds,
            ["scope"]="registered entry strategies only; source-family advisory scan and session native-group priority remain caller-owned" };
        return new(passport,main,side,winner,diagnostics,gate,originalRecoveryMapIds);

        JsonObject Check(JsonObject row)=>EntryPassportEvidence.CheckObserved(catalog,row,passport.Diagnostics["observations"]?.AsArray()??new());
    }

    private static List<JsonObject> RecoveryProposals(IEnumerable<JsonObject> rows)
    {
        var all=rows.ToList(); double top=all.Count>0?all.Max(row=>Number(row["score"])):0,band=Math.Max(6,top*.65);
        var proposals=new List<JsonObject>(); var represented=new HashSet<string>(StringComparer.Ordinal);
        foreach(var row in all)
        {
            if(Number(row["score"])<6) continue;
            if(represented.Add(row["profileId"]!.ToString()) && !(row["continuousRefinementReason"]!=null && !IsTrue(row["verification"],"success"))) proposals.Add(row);
        }
        proposals=proposals.Take(3).ToList();
        foreach(var first in proposals.OrderBy(row=>Number(row["score"])).ToArray())
        {
            if(proposals.Count>=3) break;
            var second=all.FirstOrDefault(row=>!ReferenceEquals(row,first) && row["mapId"]!.ToString()==first["mapId"]!.ToString() && row["floor"]!.ToString()==first["floor"]!.ToString());
            if(second!=null) proposals.Add(second);
        }
        foreach(var row in all)
        {
            if(proposals.Count>=3) break;
            if(Number(row["score"])>=band && row["continuousRefinementReason"]==null && !proposals.Contains(row)) proposals.Add(row);
        }
        return proposals;
    }
    private static void CopyInto(JsonObject target,JsonObject source) { foreach(var pair in source) target[pair.Key]=pair.Value?.DeepClone(); }
    private static bool ResourceFailure(Exception e)=>e is IOException or ArgumentException or InvalidOperationException or KeyNotFoundException or OpenCVException or System.Text.Json.JsonException;
}
