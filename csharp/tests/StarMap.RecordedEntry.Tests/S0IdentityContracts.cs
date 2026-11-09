using System.Text.Json.Nodes;
using StarMap.Core.Identity;
using StarMap.Vision;

internal static class S0IdentityContracts
{
    // State/control-flow probes use an already computed actual proposal. They
    // do not run rank again, supply map truth, or replace recorded comparisons.
    internal static JsonObject Solve(S0IdentityRankResult rank,S0IdentityDecisionResult decision,NativeIdentityContext context,string observationId)
    {
        var checks=new JsonObject();
        var rows=rank.Rows; var before=rank.Rows; rows.Clear();
        checks["rankRowsImmutable"]=JsonNode.DeepEquals(before,rank.Rows);
        var strategy=rank.Strategy; var strategyBefore=rank.Strategy.Diagnostics; strategy.Diagnostics.Clear();
        checks["producerSnapshotsImmutable"]=JsonNode.DeepEquals(strategyBefore,rank.Strategy.Diagnostics);
        var display=decision.AutomaticCandidates; var displayBefore=decision.AutomaticCandidates; display.Clear();
        checks["decisionSnapshotsImmutable"]=JsonNode.DeepEquals(displayBefore,decision.AutomaticCandidates);
        var proposal=rank.NativeProposal;
        var state=NativeSimilarityState.Empty().Apply(proposal,context,observationId);
        var stateBefore=state.Snapshot();
        var repeated=state.Apply(proposal,context,observationId);
        checks["repeatedObservationIdempotent"]=JsonNode.DeepEquals(stateBefore,repeated.Snapshot()) && state.MemberIds.SequenceEqual(repeated.MemberIds);
        proposal.Clear();
        checks["proposalNotRetainedByReference"]=JsonNode.DeepEquals(stateBefore,state.Snapshot());
        checks["stateSnapshotImmutable"]=SnapshotImmutable(state);
        var revoked=state.Apply(new() { ["status"]="none" },context,observationId+"/none");
        checks["noneRevokesPriorAndGroup"]=Cleared(revoked);
        foreach(var (name,changed) in new[]
        {
            ("round",context with {RoundId=context.RoundId+"/next"}),
            ("map",context with {MapId=(context.MapId??"")+"/different"}),
            ("floor",context with {Floor=context.Floor+"/different"}),
            ("resource",context with {ResourceKey=context.ResourceKey+"/different"}),
            ("mode",context with {Mode=context.Mode+"/different"}),
            ("season",context with {Season=context.Season+"/different"})
        })
        {
            var empty=new JsonObject { ["status"]="conditions",["mode"]=changed.Mode,["floor"]=changed.Floor,["resourceKey"]=changed.ResourceKey,
                ["complete"]=false,["conditionObservations"]=new JsonArray(),["originalIdentityHardGatePassed"]=false };
            var next=state.Apply(empty,changed,observationId+"/"+name);
            checks[name+"CannotInheritLedger"]=Cleared(next);
            if(state.Prior() is { } prior) checks[name+"PriorRejected"]=!prior.Matches(changed,changed.Mode,changed.Floor,changed.ResourceKey);
            if(rank.Context is not null)
            {
                bool rejected=false;
                try { _=S0IdentityDecision.Consume(rank,decision.NativeState,changed,observationId+"/stale"); }
                catch(ArgumentException) { rejected=true; }
                checks[name+"CannotConsumeAnotherRank"]=rejected;
            }
        }
        checks["oldPublishedStateUnchanged"]=JsonNode.DeepEquals(stateBefore,state.Snapshot());
        if(decision.IsRegisteredGroup)
        {
            var registered=decision.NativeState;
            checks["registrationDoesNotRetainNativePrior"]=registered.Prior() is null && registered.MemberReference is null;
            checks["provisionalGroupDoesNotConfirmMember"]=decision.SelectedProvisionalMapId is not { } member || !registered.TryConfirm(member,context.Floor,context,out _);
        }
        return new() { ["checks"]=checks,["hasActualConditionLedger"]=state.Prior() is not null,
            ["scope"]="Pure state and immutable-result control probes on actual current-rank data; changed contexts carry no observations" };
    }
    private static bool Cleared(NativeSimilarityState value)=>value.State=="UNLOCKED" && value.GroupId is null && value.MemberIds.Count==0 && value.Prior() is null;
    private static bool SnapshotImmutable(NativeSimilarityState value)
    { var original=value.Snapshot(); var exposed=value.Snapshot(); exposed.Clear(); return JsonNode.DeepEquals(original,value.Snapshot()); }
    internal static void Require(JsonObject result,List<JsonObject> differences)
    {
        foreach(var check in result["checks"]!.AsObject()) if(check.Value?.GetValue<bool>()!=true)
            ReplayJson.Difference(differences,"/identityContracts/"+check.Key,"required-current-state-boundary",JsonValue.Create(true),check.Value);
    }
}
