using System.Diagnostics;
using System.Text.Json.Nodes;
using OpenCvSharp;
using StarMap.Core.Identity;
using StarMap.Core.Resources;
using static StarMap.Core.Identity.NativeIdentityJson;

namespace StarMap.Vision;

/// <summary>Worker-owned immutable snapshots; getters never expose a mutable published JSON tree.</summary>
public sealed class S0IdentityRankResult
{
    private readonly JsonArray rows;
    private readonly JsonObject evidence;
    private readonly EntryStrategyResult strategy;
    internal S0IdentityRankResult(S0IdentityRankPipeline owner,string floor,JsonArray rows,JsonObject evidence,EntryStrategyResult strategy,NativeIdentityContext? context)
    { Owner=owner; Floor=floor; Context=context; this.rows=rows; this.evidence=evidence; this.strategy=Copy(strategy); }
    internal S0IdentityRankPipeline Owner { get; }
    public string Floor { get; }
    public NativeIdentityContext? Context { get; }
    public JsonArray Rows => rows.DeepClone().AsArray();
    // Consume only reads this newly owned tree. Public callers receive copies.
    internal JsonArray RowsForConsumption => rows;
    public JsonObject Evidence => evidence.DeepClone().AsObject();
    public JsonObject NativeProposal => rows.FirstOrDefault()?["nativeSimilarity"]?.DeepClone().AsObject() ?? new() { ["status"]="none" };
    public JsonObject SourceAdvisory => evidence["sourceStructureCandidates"]!.DeepClone().AsObject();
    public EntryStrategyResult Strategy => Copy(strategy);
    private static EntryStrategyResult Copy(EntryStrategyResult value)
    {
        SideEntryResult Side(SideEntryResult item)=>new(item.Rows.DeepClone().AsArray(),item.Diagnostics.DeepClone().AsObject(),item.Turns.DeepClone().AsArray());
        return new(Side(value.Passport),value.Main is null?null:new(value.Main.Rows.DeepClone().AsArray(),value.Main.Diagnostics.DeepClone().AsObject(),value.Main.FrontGate.DeepClone().AsObject(),value.Main.Corners.DeepClone().AsArray()),
            value.Side is null?null:Side(value.Side),value.SideWinner?.DeepClone().AsObject(),value.Diagnostics.DeepClone().AsObject(),value.FrontGate.DeepClone().AsObject(),value.OriginalRecoveryMapIds?.DeepClone().AsArray());
    }
}

/// <summary>Original S0 engine.rank caller: one current frame, one shared budget, registered producers then source/native consumers.</summary>
public sealed class S0IdentityRankPipeline
{
    private readonly SideEntryRanker ranker;
    private readonly S0SourceStructureAdvisory source;
    private readonly S0NativeConditionProposal native;
    internal EntryPassportCatalog Entries { get; }
    internal ConfirmedFrontGroupRegistry Registry { get; }
    internal NativeConditionCatalog Conditions { get; }
    public string NativeResourceKey => Conditions.ResourceKey;
    public string? NativeResourceError => Conditions.Error;
    public S0IdentityRankPipeline(ResourceCatalog resources,EntryPassportCatalog catalog)
    {
        if(catalog.Season!="s0") throw new ArgumentException("S0 identity pipeline cannot substitute for the S1 structural scanner.");
        Entries=catalog; ranker=new(catalog,resources); source=new(catalog); Conditions=new(resources,catalog); Registry=new(resources,catalog); native=new(Conditions);
    }
    public NativeIdentityContext Context(string roundId,string floor,string? confirmedMapId=null)=>new(Entries.Season,Entries.Mode,roundId,floor,NativeResourceKey,confirmedMapId);
    public S0IdentityRankResult Rank(Mat frame,string floor,TimeSpan? budget=null,NativeConditionPrior? prior=null,
        NativeIdentityContext? context=null,CancellationToken cancellation=default)
    {
        var clock=Stopwatch.StartNew(); var limit=budget??TimeSpan.FromSeconds(1);
        TimeSpan Remaining()=>limit>clock.Elapsed?limit-clock.Elapsed:TimeSpan.Zero;
        bool usablePrior=context?.Season==Entries.Season && prior?.Matches(context,Entries.Mode,floor,NativeResourceKey)==true;
        IReadOnlyList<JsonObject> Priority(IReadOnlyList<JsonObject> rows)
        {
            if(!usablePrior || prior!.PriorityMapIds.Count==0) return rows;
            try
            {
                var domains=prior!.Domains;
                if(domains.Any(pair=>!Conditions.HasDomain(pair.Key) || Text(Conditions.Reference(Text(Conditions.Domain(pair.Key),"source")),"floor")!=floor)) return rows;
                Conditions.ValidateDomains(domains.Select(pair=>pair.Key));
                var order=prior.PriorityMapIds.Select((map,i)=>(map,i)).ToDictionary(pair=>pair.map,pair=>pair.i,StringComparer.Ordinal);
                return rows.OrderBy(row=>order.GetValueOrDefault(Text(row,"mapId"),order.Count)).ToArray();
            }
            catch(Exception error) when(error is IOException or ArgumentException or KeyNotFoundException or InvalidOperationException) { return rows; }
        }
        var strategy=ranker.RankStrategyCore(frame,floor,Remaining(),cancellation,usablePrior?Priority:null);
        var main=strategy.Main?.Rows ?? new JsonArray(); var side=strategy.Side?.Rows ?? new JsonArray(); var mainDiagnostic=strategy.Diagnostics["mainDiagnostics"]!.AsObject();
        var registered=new JsonArray();
        foreach(var row in main.Where(row=>Flag(row,"identityVerified"))) registered.Add(new JsonObject { ["mapId"]=row!["mapId"]!.DeepClone(),["score"]=row["score"]!.DeepClone(),["identityEvidence"]=row["identityEvidence"]?.DeepClone(),["identityVerified"]=true });
        if(strategy.SideWinner is { } winner) registered.Add(new JsonObject { ["mapId"]=winner["mapId"]!.DeepClone(),["score"]=winner["score"]!.DeepClone(),["identityEvidence"]=winner["identityEvidence"]?.DeepClone(),["identityVerified"]=true });
        var sourceEvidence=source.Observe(Remaining()); sourceEvidence["registeredIdentityCandidates"]=registered;
        sourceEvidence["evaluatedAlongsideRegisteredIdentity"]=registered.Count>0; sourceEvidence["crossDomainScoresComparable"]=false;
        sourceEvidence["identityHardGatePassed"]=false; sourceEvidence["poseSubmissionAllowed"]=false; sourceEvidence["authorRouteDisplayAllowed"]=false;
        bool hasIdentity=main.Any(row=>Flag(row,"identityVerified")) || strategy.SideWinner is not null;
        var evidence=new JsonObject { ["candidates"]=new JsonArray(),["entryKind"]=hasIdentity?"main":"unknown" };
        if(hasIdentity) evidence["reason"]="tilemap-entry-verified";
        evidence["tilemapEntry"]=mainDiagnostic.DeepClone(); evidence["tilemapCandidates"]=main.DeepClone();
        evidence["tilemapSide"]=strategy.Side?.Diagnostics.DeepClone(); evidence["tilemapSideCandidates"]=side.DeepClone();
        evidence["entryPassport"]=strategy.Passport.Diagnostics.DeepClone(); evidence["entryPassportCandidates"]=strategy.Passport.Rows.DeepClone(); evidence["sourceStructureCandidates"]=sourceEvidence;
        evidence["recognitionPath"]=new JsonObject { ["passportSelected"]=strategy.Diagnostics["passportSelected"]?.DeepClone(),["fallbackAllowed"]=strategy.Diagnostics["fallbackAllowed"]?.DeepClone(),
            ["passportUnresolved"]=strategy.Diagnostics["passportUnresolved"]?.DeepClone(),["sideProducer"]=strategy.Diagnostics["sideProducer"]?.DeepClone(),["sideWinnerMapId"]=strategy.SideWinner?["mapId"]?.DeepClone(),
            ["sourceStructureCandidateCount"]=Array(sourceEvidence["candidates"]).Count,["sourceStructureIdentityAuthorized"]=false,["sourceStructureIdentityVerified"]=sourceEvidence["automaticCandidate"] is not null,
            ["sourceStructureConsumer"]="separate-structure-reference-branch",["frameSize"]=new JsonArray(frame.Cols,frame.Rows) };
        HashSet<string>? allowed=Flag(strategy.FrontGate,"applied")?Strings(strategy.FrontGate["selectedMapIds"]).ToHashSet(StringComparer.Ordinal):null;
        var list=Entries.Maps.Where(map=>allowed is null || allowed.Contains(map.MapId)).Select(map=>new JsonObject { ["mapId"]=map.MapId,["name"]=map.Name,["score"]=0d,
            ["strongSuggestion"]=false,["identityHardGatePassed"]=false,["inliers"]=0,["reason"]="difficult-entry-advisory-manual-confirmation-required",["mode"]=Entries.Mode,["entryKind"]=evidence["entryKind"]!.DeepClone(),["direction"]=null }).ToList();
        var byId=list.ToDictionary(row=>Text(row,"mapId"),StringComparer.Ordinal);
        foreach(var node in main)
        {
            var candidate=node!.AsObject(); var row=byId[Text(candidate,"mapId")]; bool verified=Flag(candidate,"identityVerified");
            row["score"]=200+Number(candidate["score"])+(verified?1000:0); row["entryKind"]="main"; row["strongSuggestion"]=verified; row["identityHardGatePassed"]=verified;
            row["identityEvidence"]=verified?"tilemap-entry-neighborhood-current-frame":"tilemap-entry-candidates";
            row["reason"]=Flag(candidate["entryCrossCheck"],"contradicted")?"entry-current-exit-contradicted":mainDiagnostic["reason"]?.ToString();
            row["difficultEntry"]=candidate.DeepClone(); row["identityElapsedMs"]=mainDiagnostic["elapsedMs"]?.DeepClone(); row["identityBudgetExceeded"]=mainDiagnostic["complete"] is not null && !Flag(mainDiagnostic,"complete");
        }
        foreach(var node in side)
        {
            var candidate=node!.AsObject(); if(!byId.TryGetValue(Text(candidate,"mapId"),out var row) || Flag(row,"identityHardGatePassed") || Number(row["score"])>=200+Number(candidate["score"])) continue;
            row["score"]=200+Number(candidate["score"]); row["entryKind"]=candidate["entryKind"]?.DeepClone(); row["strongSuggestion"]=false; row["identityHardGatePassed"]=false; row["identityEvidence"]="side-structure-candidates";
            row["reason"]=Flag(candidate["entryCrossCheck"],"contradicted")?"entry-current-exit-contradicted":strategy.Side?.Diagnostics["reason"]?.ToString();
            var entry=candidate.DeepClone().AsObject(); entry["locatorKind"]="side"; entry["identityVerified"]=false; row["difficultEntry"]=entry;
            row["identityElapsedMs"]=clock.Elapsed.TotalMilliseconds; row["identityBudgetExceeded"]=!Flag(strategy.Side?.Diagnostics,"complete");
        }
        if(strategy.SideWinner is { } selected && byId.TryGetValue(Text(selected,"mapId"),out var winning))
        {
            winning["score"]=1200+Number(selected["score"]); winning["entryKind"]=selected["entryKind"]?.DeepClone(); winning["strongSuggestion"]=true; winning["identityHardGatePassed"]=true;
            winning["identityEvidence"]=selected["identityEvidence"]?.DeepClone(); winning["reason"]="side-current-identity-verified"; winning["difficultEntry"]=selected.DeepClone();
            winning["identityElapsedMs"]=clock.Elapsed.TotalMilliseconds; winning["identityBudgetExceeded"]=false;
        }
        list=list.OrderByDescending(row=>Number(row["score"])).ToList();
        var diagnostic=S0RankDiagnostics.Build(evidence);
        var gate=strategy.FrontGate.DeepClone().AsObject(); gate["actualFloor"]=floor; gate["rankElapsedMs"]=clock.Elapsed.TotalMilliseconds;
        gate["evaluatedMainMapIds"]=mainDiagnostic["evaluatedMapIds"]?.DeepClone() ?? new JsonArray(); gate["acceptedMapIds"]=Strings(list.Where(row=>Flag(row,"identityHardGatePassed")).Select(row=>Text(row,"mapId")));
        gate["mainComplete"]=mainDiagnostic["complete"]?.DeepClone(); gate["sideComplete"]=strategy.Side?.Diagnostics["complete"]?.DeepClone(); diagnostic["frontGateObservation"]=gate;
        var resultRows=new JsonArray(list.Select(row=>(JsonNode)row).ToArray());
        if(resultRows.Count>0)
        {
            double frontTop=main.Count>0?main.Max(row=>Number(row?["score"])):0,sideTop=side.Count>0?side.Max(row=>Number(row?["score"])):0;
            var required=main.Where(row=>Number(row?["score"])>=Math.Max(10,frontTop*.65)).Select(row=>Text(row,"mapId")).ToHashSet(StringComparer.Ordinal);
            required.UnionWith(side.Where(row=>Number(row?["score"])>=Math.Max(4,sideTop-1)||Flag(row?["verification"],"success")).Select(row=>Text(row,"mapId")));
            required.UnionWith(Strings(strategy.OriginalRecoveryMapIds));
            resultRows[0]!["nativeSimilarity"]=native.Propose(frame,resultRows,main,mainDiagnostic,side,strategy.Side?.Diagnostics,floor,()=>clock.Elapsed>=limit,required,usablePrior?prior:null,context,cancellation);
        }
        diagnostic["elapsedMs"] = clock.Elapsed.TotalMilliseconds;
        foreach (var row in list) row["difficultRecognition"] = diagnostic.DeepClone();
        if (Entries.Mode == "difficult")
        {
            var missing = Entries.UnavailablePackFloors.Where(item => item.Floor == floor)
                .Select(item => item.MapId).Order(StringComparer.Ordinal).ToArray();
            if (missing.Length > 0)
            {
                var inventory = new JsonObject { ["complete"] = false, ["missingMapIds"] = Strings(missing),
                    ["expectedMapIds"] = Strings(Entries.Maps.Where(map => Entries.PackFloors.Contains((map.MapId,floor))).Select(map => map.MapId)) };
                foreach (var node in resultRows)
                {
                    var row = node!.AsObject();
                    row["nativeInventory"] = inventory.DeepClone();
                    row["nativeInventoryIdentitySuppressed"] = Flag(row,"identityHardGatePassed");
                    row["identityHardGatePassed"] = false; row["strongSuggestion"] = false;
                    row["reason"] = "native-identity-inventory-incomplete";
                    if (row["difficultEntry"] is JsonObject entry)
                    {
                        entry["entryInventoryComplete"] = false; entry["entryInventoryMissingMapIds"] = Strings(missing);
                        entry["entryInventoryIdentitySuppressed"] = Flag(entry,"identityVerified");
                        entry["identityVerified"] = false; entry["poseVerified"] = false;
                    }
                }
            }
        }
        cancellation.ThrowIfCancellationRequested(); return new(this,floor,resultRows,evidence,strategy,context);
    }
}
