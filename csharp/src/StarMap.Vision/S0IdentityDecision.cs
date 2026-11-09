using System.Security.Cryptography;
using System.Text;
using System.Text.Json.Nodes;
using StarMap.Core.Identity;
using static StarMap.Core.Identity.NativeIdentityJson;

namespace StarMap.Vision;

public sealed class S0IdentityDecisionResult
{
    private readonly JsonObject detail;
    private readonly JsonObject entries;
    internal S0IdentityDecisionResult(NativeSimilarityState state,JsonObject detail,JsonObject entries)
    { NativeState=state; this.detail=detail; this.entries=entries; }
    public NativeSimilarityState NativeState { get; }
    public string? SelectedProvisionalMapId => detail["selectedMapId"]?.GetValue<string>();
    public bool IsRegisteredGroup => NativeState.RegisteredGroup;
    public bool UniqueHardIdentityCandidate => detail["uniqueHardIdentityCandidate"]?.GetValue<bool>() == true;
    public JsonArray AutomaticCandidates => detail["automaticCandidates"]!.DeepClone().AsArray();
    public JsonArray CandidateSummaries => new(detail["automaticCandidates"]!.AsArray().Select(row =>
        (JsonNode)Project(row!,"key","mapId","identityKind","groupId","memberIds","name","score",
            "identityHardGatePassed","strongSuggestion")).ToArray());
    public JsonObject? EntryCandidate(string mapId) => entries[mapId]?.DeepClone().AsObject();
    public JsonObject EntryCandidatesByMap => entries.DeepClone().AsObject();
    public JsonObject Snapshot() => detail.DeepClone().AsObject();
    public JsonObject Summary() => Project(detail,"state","memberIds","conditionReason","entranceKind",
        "nativeEvidenceSummary","registeredFamily","blockMemberUnique","registeredGroup","selectedMapId",
        "uniqueHardIdentityCandidate","authorizesIdentity","authorizesPose","requiresRuntimeCommit");
}

/// <summary>Original current-rank/native-state/confirmed-registry consumer. It never writes Runtime or grants pose/display.</summary>
public static class S0IdentityDecision
{
    public static S0IdentityDecisionResult Consume(S0IdentityRankResult rank,NativeSimilarityState? previous,
        NativeIdentityContext context,string observationId,bool groupOnly=true,bool floorRequired=false,string? existingConfirmation=null)
    {
        var owner=rank.Owner;
        if(rank.Context is not null && rank.Context!=context || context.Season!=owner.Entries.Season || context.Mode!=owner.Entries.Mode || context.Floor!=rank.Floor || context.ResourceKey!=owner.NativeResourceKey || string.IsNullOrEmpty(context.RoundId))
            throw new ArgumentException("Current rank/native state context mismatch.",nameof(context));
        var rows=rank.RowsForConsumption; var proposal=rank.NativeProposal; var accepted=rows.Where(row=>Flag(row,"identityHardGatePassed")).ToArray();
        if(Flag(proposal,"originalIdentityHardGatePassed") && (accepted.Length!=1 || Text(accepted[0],"mapId")!=Text(proposal,"candidateMemberId"))) proposal["originalIdentityHardGatePassed"]=false;
        var entries=new JsonObject(); var seeds=new List<string>();
        foreach(var row in rows)
        {
            string map=Text(row,"mapId"); if(row?["difficultEntry"] is not JsonObject entry || Text(entry,"mapId")!=map || entry["floor"] is not null && Text(entry,"floor")!=rank.Floor) continue;
            entries[map]=entry.DeepClone();
            if(Flag(entry["verification"],"success") && !Flag(entry,"poseAmbiguous") && !Flag(entry["entryCrossCheck"],"contradicted") && FiniteMatrix(entry["verification"]?["matrixSourceToClient"])) seeds.Add(map);
        }
        var state=(previous??NativeSimilarityState.Empty()).Apply(proposal,context,observationId,groupOnly);
        if(groupOnly && state.MemberIds.Count>1 && state.GroupId is not null) state=state.WithInitialDisplayMember(seeds);
        if(!groupOnly && state.State=="GROUP_LOCKED" && state.Prior() is not null && context.Floor=="1") state=state.PreferDisplayMember(seeds);
        var originalNative=proposal.DeepClone().AsObject(); var observations=Array(proposal["conditionObservations"]);
        var nativeSummary=new JsonObject { ["status"]=Text(proposal,"status"),["complete"]=Flag(proposal,"complete"),["observationCount"]=observations.Count,
            ["foreignCompetitionCompleteCount"]=observations.Count(row=>Flag(row,"completeForeignMapCompetition")),["currentCompetitionCompleteCount"]=observations.Count(row=>Flag(row,"currentCompetitionComplete")),
            ["ambiguousCount"]=observations.Count(row=>Flag(row,"ambiguousViews")),["conflictCount"]=observations.Count(row=>Flag(row,"conflictingViews")),["outsideCount"]=observations.Count(row=>Array(row?["outsideCompetitors"]).Count>0),["eligibleMembers"]=Strings(state.MemberIds) };
        string entrance=Entrance(owner,rows,rank.Floor); var top=rows.FirstOrDefault(); bool nativeGroup=state.MemberIds.Count>1 && state.State=="GROUP_LOCKED";
        bool hardTop=accepted.Length==1 && ReferenceEquals(top,accepted[0]) && Flag(top,"strongSuggestion"); bool block=false;
        var family=new JsonObject { ["status"]="none" }; var unresolved=new JsonArray();
        if(entrance=="front" && (nativeGroup || hardTop))
        {
            family=owner.Registry.Resolve(hardTop?Text(top,"mapId"):state.ProvisionalMemberId,rank.Floor,entrance,nativeGroup && !hardTop?state.MemberIds:null);
            block=Text(family,"status")!="none";
            if(Text(family,"status")=="group")
            {
                state=state.WithRegisteredGroup(family["candidates"]![0]!.AsObject(),seeds);
                proposal=new() { ["status"]="logical-group",["reason"]="registered-logical-family" };
            }
            else if(block)
            {
                state=state.ResetCopy(); proposal=new() { ["status"]="logical-group-pending",["reason"]="registered-family-"+Text(family,"status") };
                foreach(var declared in Array(family["candidates"]))
                {
                    var row=declared!.DeepClone().AsObject(); row["key"]=row["groupId"]?.DeepClone(); row["mapId"]=""; row["identityKind"]="group"; row["name"]="待确认的相似地图组"; unresolved.Add(row);
                }
            }
        }
        var automatic=block && !state.RegisteredGroup?unresolved:Automatic(rows,state.RegisteredGroup?state:null);
        string? selected=state.RegisteredGroup && state.ProvisionalMemberId is { } member && state.MemberIds.Contains(member,StringComparer.Ordinal)?member:null;
        bool hardLock=selected is null && !block && !state.RegisteredGroup && accepted.Length==1 && Flag(top,"strongSuggestion") && Flag(top,"identityHardGatePassed") && !floorRequired && string.IsNullOrEmpty(existingConfirmation);
        if(hardLock) selected=Text(top,"mapId");
        var detail=new JsonObject { ["state"]=state.Snapshot(),["memberIds"]=Strings(state.MemberIds),["conditionReason"]=state.ConditionReason,
            ["entranceKind"]=entrance,["nativeProposal"]=originalNative,["proposal"]=proposal,["nativeEvidenceSummary"]=nativeSummary,["registeredFamily"]=family,
            ["blockMemberUnique"]=block,["registeredGroup"]=state.RegisteredGroup,["automaticCandidates"]=automatic,["selectedMapId"]=selected,["uniqueHardIdentityCandidate"]=hardLock,
            ["authorizesIdentity"]=false,["authorizesPose"]=false,["requiresRuntimeCommit"]=true };
        return new(state,detail,entries);
    }
    private static string Entrance(S0IdentityRankPipeline owner,JsonArray rows,string floor)
    {
        for(int i=0;i<rows.Count;i++)
        {
            var row=rows[i]!; var entry=row["difficultEntry"];
            if(entry is null || Text(entry,"mapId")!=Text(row,"mapId") || entry["floor"] is not null && Text(entry,"floor")!=floor) continue;
            string kind=Text(entry,"entryKind"); bool front=kind is "main" or "main-entry-passport";
            if(i==0 && !front && (Text(entry,"locatorKind")=="side" || kind is "side-entry-passport" or "side-neighborhood" or "local-structure")) return "side";
            var check=entry["verification"]; bool geometry=Flag(check,"success") || Flag(check?["regions"],"regionalEdgeVerified") && Flag(check?["residual"],"verified");
            if(front && geometry && !Flag(entry,"poseAmbiguous") && !Flag(entry["entryCrossCheck"],"contradicted")) return "front";
        }
        var proposal=rows.FirstOrDefault()?["nativeSimilarity"];
        if(Text(proposal,"mode")==owner.Entries.Mode && Text(proposal,"floor")==floor && Text(proposal,"resourceKey")==owner.NativeResourceKey)
            foreach(var observed in Array(proposal?["conditionObservations"]))
            {
                string cid=Text(observed,"conditionId"); if(!owner.Conditions.HasCondition(cid)) continue; var condition=owner.Conditions.Condition(cid);
                if(Text(condition,"kind")=="front" && Text(condition,"mode")==owner.Entries.Mode && Text(condition,"floor")==floor && Text(observed,"domainId")==Text(condition,"domainId") &&
                    observed?["entryEstablished"] is JsonValue flag && flag.TryGetValue<bool>(out bool established) && !established && Flag(observed,"completeForeignMapCompetition") && Flag(observed,"currentCompetitionComplete") &&
                    Array(observed?["activeViews"]).Count>0 && !Flag(observed,"ambiguousViews") && Array(observed?["outsideCompetitors"]).Count==0) return "front";
            }
        return "unknown";
    }
    private static JsonArray Automatic(JsonArray rows,NativeSimilarityState? state)
    {
        var result=new JsonArray(); var members=state?.MemberIds.Order(StringComparer.Ordinal).ToArray()??[];
        if(state is {State:"GROUP_LOCKED"} && members.Length>1)
        {
            string key="group:"+Convert.ToHexString(SHA256.HashData(Encoding.UTF8.GetBytes(string.Join('\0',members)))).ToLowerInvariant()[..16];
            var scores=rows.Where(row=>members.Contains(Text(row,"mapId"),StringComparer.Ordinal)).Select(row=>Number(row?["score"])).ToArray();
            result.Add(new JsonObject { ["key"]=key,["mapId"]="",["identityKind"]="group",["groupId"]=state.GroupId,["memberIds"]=Strings(members),["name"]="相似地图组",["score"]=scores.Length>0?JsonValue.Create(scores.Max()):null,["identityHardGatePassed"]=false,["strongSuggestion"]=false });
        }
        foreach(var node in rows)
        {
            if(members.Length>1 && members.Contains(Text(node,"mapId"),StringComparer.Ordinal)) continue;
            var row=node!.DeepClone().AsObject(); row["identityKind"]="map"; row["key"]=row["mapId"]!.DeepClone(); result.Add(row);
        }
        return result;
    }
}
