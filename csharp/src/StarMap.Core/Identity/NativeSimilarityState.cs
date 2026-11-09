using System.Text.Json.Nodes;
using static StarMap.Core.Identity.NativeIdentityJson;

namespace StarMap.Core.Identity;

public sealed record NativeIdentityContext(string Season,string Mode,string RoundId,string Floor,string ResourceKey,string? MapId=null);

/// <summary>Immutable-by-copy condition scope emitted only from an accumulated state.</summary>
public sealed class NativeConditionPrior
{
    private readonly JsonObject domains;
    private readonly string[] priority;
    internal NativeConditionPrior(NativeIdentityContext context,JsonObject domains,IEnumerable<string> priority)
    { Context=context; this.domains=domains.DeepClone().AsObject(); this.priority=priority.ToArray(); }
    public NativeIdentityContext Context { get; }
    public JsonObject Domains => domains.DeepClone().AsObject();
    public IReadOnlyList<string> PriorityMapIds => System.Array.AsReadOnly(priority);
    public bool Matches(NativeIdentityContext? current,string mode,string floor,string resourceKey) => current is not null && Context==current && current.Mode==mode && current.Floor==floor && current.ResourceKey==resourceKey;
}

/// <summary>Pure original condition ledger. Apply returns a new value; Runtime owns all publication and commits.</summary>
public sealed class NativeSimilarityState
{
    private readonly JsonObject data;
    public NativeIdentityContext? Context { get; }
    private NativeSimilarityState(JsonObject data,NativeIdentityContext? context) { this.data=data.DeepClone().AsObject(); Context=context; }
    public static NativeSimilarityState Empty() => new(Initial(),null);
    public IReadOnlyList<string> MemberIds => System.Array.AsReadOnly(Strings(data["members"]));
    public string? ProvisionalMemberId => data["provisionalMemberId"]?.GetValue<string>();
    public string State => Text(data,"state");
    public string? GroupId => data["groupId"]?.GetValue<string>();
    public string? ConditionReason => data["conditionReason"]?.GetValue<string>();
    public bool RegisteredGroup => State=="GROUP_LOCKED" && ConditionReason=="registered-logical-family" && GroupId is not null && MemberIds.Count>1;
    public JsonObject? MemberReference => data["memberReference"]?.DeepClone().AsObject();
    public bool TryConfirm(string mapId,string floor,NativeIdentityContext context,out NativeSimilarityState confirmed)
    {
        confirmed=this;
        if(!Flag(data,"pending") || Context!=context || ProvisionalMemberId!=mapId || Text(data,"floor")!=floor) return false;
        var d=data.DeepClone().AsObject(); d["state"]="MEMBER_VERIFIED"; d["verifiedMemberId"]=mapId; d["generation"]=(int)Number(d["generation"])+1; d["pending"]=false;
        confirmed=new(d,Context); return true;
    }
    public NativeSimilarityState PreferDisplayMember(IEnumerable<string> availableIds)
    {
        var available=MemberIds.Intersect(availableIds,StringComparer.Ordinal).Order(StringComparer.Ordinal).ToArray();
        if(State!="GROUP_LOCKED" || available.Length==0 || Flag(data,"pending") || Array(data["priority"]).Count>0 || ProvisionalMemberId is { } current && available.Contains(current,StringComparer.Ordinal)) return this;
        var d=data.DeepClone().AsObject(); d["provisionalMemberId"]=available[0]; d["generation"]=(int)Number(d["generation"])+1; return new(d,Context);
    }
    public NativeSimilarityState WithInitialDisplayMember(IEnumerable<string> fittedMembers)
    {
        var d=data.DeepClone().AsObject(); var members=MemberIds; var fitted=members.Intersect(fittedMembers,StringComparer.Ordinal).Order(StringComparer.Ordinal).ToArray(); string? current=ProvisionalMemberId;
        d["provisionalMemberId"]=current is not null && fitted.Contains(current,StringComparer.Ordinal)?current:fitted.FirstOrDefault() ?? (current is not null && members.Contains(current,StringComparer.Ordinal)?current:members.Order(StringComparer.Ordinal).FirstOrDefault());
        return new(d,Context);
    }

    public NativeSimilarityState Apply(JsonObject proposal,NativeIdentityContext context,string observationId,bool groupOnly=false)
    {
        bool newOwner=Context is not null && (Context.Season!=context.Season || Context.Mode!=context.Mode || Context.RoundId!=context.RoundId || Context.MapId!=context.MapId);
        var d=data.DeepClone().AsObject(); if(newOwner) Reset(d);
        if (Text(proposal,"status")!="conditions" || Text(proposal,"mode")!=context.Mode || Text(proposal,"floor")!=context.Floor || Text(proposal,"resourceKey")!=context.ResourceKey)
        { Reset(d); return new(d,context); }
        if (Text(d,"conditionMode")!=context.Mode || Text(d,"floor")!=context.Floor || Text(d,"resourceKey")!=context.ResourceKey) Reset(d);
        d["conditionMode"]=context.Mode; d["floor"]=context.Floor; d["resourceKey"]=context.ResourceKey; d["groupId"]="conditions";
        d["priority"]=new JsonArray(); d["verifiedMemberId"]=null;
        var observations=Array(d["observations"]);
        if (!Strings(observations).Contains(observationId,StringComparer.Ordinal)) { observations.Add(observationId); if(observations.Count>64) observations.RemoveAt(0); }
        var domains=d["domains"]!.AsObject(); var active=new HashSet<string>(StringComparer.Ordinal); var records=Array(proposal["conditionObservations"]);
        foreach(var node in records)
        {
            var observed=node!.AsObject(); string did=Text(observed,"domainId"),cid=Text(observed,"conditionId"); active.Add(did);
            if (domains[did] is not JsonObject domain) domains[did]=domain=new() { ["bindings"]=new JsonObject(),["pairs"]=new JsonObject(),["conditions"]=new JsonArray(),["members"]=new JsonArray(),["poses"]=new JsonObject() };
            if(!Strings(domain["conditions"]).Contains(cid,StringComparer.Ordinal)) domain["conditions"]!.AsArray().Add(cid);
            if (Complete(observed) && SameSet(observed["activeViews"],observed["expectedPoseIds"]))
                domain["bindings"]![cid]=new JsonObject { ["poses"]=observed["poseMaps"]!.DeepClone(),["members"]=observed["memberIds"]!.DeepClone() };
            if (Flag(observed,"ambiguousViews") || Flag(observed,"conflictingViews")) continue;
            foreach(var pair in Array(observed["pairs"]))
            {
                string key=Text(pair,"poseA")+"\0"+Text(pair,"poseB"); var all=domain["pairs"]!.AsObject();
                if(all[key] is not JsonObject record) all[key]=record=new() { ["poseA"]=pair!["poseA"]!.DeepClone(),["poseB"]=pair["poseB"]!.DeepClone(),["a"]=new JsonObject { ["samples"]=new JsonArray(),["observed"]=new JsonArray() },["b"]=new JsonObject { ["samples"]=new JsonArray(),["observed"]=new JsonArray() } };
                foreach(string side in new[]{"a","b"}) foreach(string field in new[]{"samples","observed"}) UnionPoints(record[side]![field]!.AsArray(),Array(pair![side]?[field]));
            }
        }
        var cohorts=new List<HashSet<string>>();
        foreach(var item in domains)
        {
            var domain=item.Value!.AsObject(); var bindings=domain["bindings"]!.AsObject().Select(pair=>pair.Value!).ToArray();
            var members=bindings.Length==0?new HashSet<string>(StringComparer.Ordinal):Strings(bindings[0]["members"]).ToHashSet(StringComparer.Ordinal);
            foreach(var binding in bindings.Skip(1)) members.IntersectWith(Strings(binding["members"]));
            domain["members"]=Strings(members.Order(StringComparer.Ordinal)); var maps=new JsonObject();
            foreach(var binding in bindings) foreach(var pair in binding["poses"]!.AsObject()) if(members.Contains(pair.Value!.ToString())) maps[pair.Key]=pair.Value.DeepClone();
            domain["poses"]=maps; if(bindings.Length>0) cohorts.Add(members);
        }
        var eligible=cohorts.Count==0?new HashSet<string>(StringComparer.Ordinal):new HashSet<string>(cohorts[0],StringComparer.Ordinal);
        foreach(var cohort in cohorts.Skip(1)) eligible.IntersectWith(cohort);
        d["members"]=Strings(eligible.Order(StringComparer.Ordinal)); d["active"]=Strings(active.Order(StringComparer.Ordinal));
        bool complete=Flag(proposal,"complete") && records.Count>0 && records.All(node=>Complete(node!) &&
            (Flag(node,"entryEstablished") && Array(node?["activeViews"]).Count>0 || SameSet(node?["activeViews"],node?["expectedPoseIds"]) && Strings(node?["activeViews"]).Select(pid=>node!["poseMaps"]![pid]!.ToString()).Distinct(StringComparer.Ordinal).Count()>=2));
        d["conditionComplete"]=complete;
        var winners=active.Order(StringComparer.Ordinal).Select(id=>Winner(domains[id]!.AsObject())).ToArray();
        string? positive=winners.Length>0 && winners[0] is not null && winners.All(winner=>winner==winners[0]) ? winners[0] : null;
        if(positive is not null && !eligible.Contains(positive)) positive=null;
        string? old=d["provisionalMemberId"]?.GetValue<string>();
        string? candidate=Flag(proposal,"originalIdentityHardGatePassed")?proposal["candidateMemberId"]?.GetValue<string>():complete?positive:null;
        if(candidate is null && (old is null || !eligible.Contains(old))) candidate=eligible.Order(StringComparer.Ordinal).FirstOrDefault();
        if(candidate is not null) d["provisionalMemberId"]=candidate;
        if(old!=d["provisionalMemberId"]?.GetValue<string>()) d["generation"]=(int)Number(d["generation"])+1;
        d["pending"]=Flag(proposal,"originalIdentityHardGatePassed"); d["memberReference"]=Flag(d,"pending")?proposal["winnerReference"]?.DeepClone():null;
        if(positive is not null && complete) d["priority"]=Strings(new[]{positive}.Concat(Strings(d["members"]).Where(mid=>mid!=positive)));
        d["conditionReason"]=Array(d["priority"]).Count>0?"positive-consensus":Flag(d,"pending")?"original-identity-pending":!complete?"condition-incomplete":"conflicting-or-unobserved-positive-evidence";
        bool established=domains.Any(pair=>pair.Value!["bindings"]!.AsObject().Count>0);
        d["groupId"]=established || Flag(d,"pending")?"conditions":null; d["state"]=d["groupId"] is null?"UNLOCKED":"GROUP_LOCKED";
        if(groupOnly && eligible.Count>1) { d["pending"]=false; d["memberReference"]=null; d["verifiedMemberId"]=null; }
        return new(d,context);
    }

    public NativeSimilarityState WithRegisteredGroup(JsonObject declared,IEnumerable<string> fittedMembers)
    {
        var d=data.DeepClone().AsObject(); string? previous=ProvisionalMemberId; Reset(d);
        var members=Strings(declared["memberIds"]); var fitted=members.Intersect(fittedMembers,StringComparer.Ordinal).Order(StringComparer.Ordinal).ToArray();
        d["state"]="GROUP_LOCKED"; d["groupId"]=declared["groupId"]!.DeepClone(); d["resourceKey"]=declared["resourceKey"]!.DeepClone(); d["members"]=Strings(members);
        d["floor"]=Context?.Floor; d["provisionalMemberId"]=previous is not null && fitted.Contains(previous,StringComparer.Ordinal)?previous:fitted.FirstOrDefault() ?? (previous is not null && members.Contains(previous,StringComparer.Ordinal)?previous:members.Order(StringComparer.Ordinal).FirstOrDefault());
        d["conditionReason"]="registered-logical-family"; return new(d,Context);
    }
    public NativeSimilarityState ResetCopy() { var d=data.DeepClone().AsObject(); Reset(d); return new(d,Context); }
    public NativeConditionPrior? Prior()
    {
        if(Context is null || data["conditionMode"] is null) return null;
        var established=new JsonObject();
        foreach(var pair in data["domains"]!.AsObject()) if(pair.Value!["bindings"]!.AsObject().Count>0) established[pair.Key]=Strings(pair.Value["bindings"]!.AsObject().Select(binding=>binding.Key).Order(StringComparer.Ordinal));
        return established.Count==0?null:new(Context,established,Strings(data["priority"]));
    }
    public JsonObject Snapshot()
    {
        var result=Project(data,"state","groupId","resourceKey","provisionalMemberId","verifiedMemberId","floor","generation");
        result["observationCount"]=Array(data["observations"]).Count; result["positiveSampleCount"]=0; result["authorizesIdentity"]=false; result["authorizesPose"]=false;
        if(data["conditionMode"] is not null)
        {
            var domains=data["domains"]!.AsObject(); result["domainCount"]=domains.Count; result["activeDomainIds"]=data["active"]!.DeepClone();
            result["conditionComparisonComplete"]=data["conditionComplete"]!.DeepClone(); result["conditionReason"]=data["conditionReason"]?.DeepClone();
            result["positiveSampleCount"]=domains.Sum(domain=>domain.Value!["pairs"]!.AsObject().Sum(pair=>Array(pair.Value!["a"]?["observed"]).Count+Array(pair.Value["b"]?["observed"]).Count));
        }
        return result;
    }
    private static JsonObject Initial() => new() { ["generation"]=0,["state"]="UNLOCKED",["groupId"]=null,["resourceKey"]=null,["provisionalMemberId"]=null,["verifiedMemberId"]=null,["floor"]=null,["observations"]=new JsonArray(),["members"]=new JsonArray(),["pending"]=false,["domains"]=new JsonObject(),["conditionMode"]=null,["active"]=new JsonArray(),["priority"]=new JsonArray(),["conditionComplete"]=false,["conditionReason"]=null };
    private static void Reset(JsonObject d) { int generation=(int)Number(d["generation"])+1; d.Clear(); foreach(var pair in Initial()) d[pair.Key]=pair.Value?.DeepClone(); d["generation"]=generation; }
    private static bool SameSet(JsonNode? a,JsonNode? b)=>Strings(a).ToHashSet(StringComparer.Ordinal).SetEquals(Strings(b));
    private static bool Complete(JsonNode row)=>Flag(row,"completeForeignMapCompetition") && Flag(row,"currentCompetitionComplete") && !Flag(row,"ambiguousViews") && !Flag(row,"conflictingViews") && Array(row["outsideCompetitors"]).Count==0;
    private static void UnionPoints(JsonArray target,JsonArray values)
    { var keys=target.Select(value=>value!.ToJsonString()).ToHashSet(StringComparer.Ordinal); foreach(var value in values) if(keys.Add(value!.ToJsonString())) target.Add(value.DeepClone()); }
    private static string? Winner(JsonObject domain)
    {
        var members=Strings(domain["members"]); var maps=domain["poses"]!.AsObject(); var wins=members.ToDictionary(mid=>mid,_=>new HashSet<string>(StringComparer.Ordinal),StringComparer.Ordinal);
        for(int i=0;i<members.Length;i++) for(int j=i+1;j<members.Length;j++)
        {
            string a=members[i],b=members[j]; var required=maps.Where(pair=>pair.Value!.ToString()==a).SelectMany(pa=>maps.Where(pair=>pair.Value!.ToString()==b).Select(pb=>(pa.Key,pb.Key))).ToHashSet();
            var covered=new HashSet<(string,string)>(); var votes=new List<string?>();
            foreach(var item in domain["pairs"]!.AsObject())
            {
                var pair=item.Value!; string pa=Text(pair,"poseA"),pb=Text(pair,"poseB"); if(!maps.ContainsKey(pa)||!maps.ContainsKey(pb)) continue;
                string ma=maps[pa]!.ToString(),mb=maps[pb]!.ToString(); if(!new HashSet<string>([ma,mb]).SetEquals([a,b])) continue;
                covered.Add(ma==a?(pa,pb):(pb,pa)); int ha=Array(pair["a"]?["observed"]).Count,hb=Array(pair["b"]?["observed"]).Count;
                double fa=ha/(double)Math.Max(1,Array(pair["a"]?["samples"]).Count),fb=hb/(double)Math.Max(1,Array(pair["b"]?["samples"]).Count);
                votes.Add(ha>0 && fa>fb?ma:hb>0 && fb>fa?mb:null);
            }
            if(required.IsSubsetOf(covered) && votes.Count>0 && votes[0] is { } winner && votes.All(vote=>vote==winner)) wins[winner].Add(winner==a?b:a);
        }
        return members.Length>1?members.FirstOrDefault(mid=>wins[mid].Count==members.Length-1):null;
    }
}
