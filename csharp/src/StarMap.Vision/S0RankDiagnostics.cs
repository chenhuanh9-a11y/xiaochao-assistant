using System.Text.Json.Nodes;
using static StarMap.Core.Identity.NativeIdentityJson;

namespace StarMap.Vision;

internal static class S0RankDiagnostics
{
    private static readonly string[] Fields=("reason error complete elapsedMs queryTurns minimumQueryTurns queryPairs queryChains minimumQueryChains evaluatedMapIds queryPixels minimumQueryPixels queryKeypointCount exitObserved observationStatus entryObserved verificationComplete coverageComplete identityAmbiguous poseAmbiguous competitiveHypotheses selectionPolicy continuousRefinementMs continuousRefinementReason poseRecoveryEvaluated poseRecoveryCandidates poseRecoveryEquivalent entryInventoryComplete entryInventoryExpectedMapIds entryInventoryAvailableMapIds entryInventoryMissingMapIds entryInventoryUnexpectedMapIds entryInventoryProposalOnly sideDoorPath sideDoorResourceError seedCorrespondences sideLocalIndexError localSearchMaps localSeedTotal outsideSeedsScreened outsideSeedsRejected ringEvaluations sideOptionalDoorMs sideOptionalDoorUsed sideOptionalDoorError").Split(' ');
    internal static JsonObject Build(JsonObject evidence)
    {
        var result=evidence["recognitionPath"]!.DeepClone().AsObject(); result["sideResourceError"]=null;
        foreach(var (label,key,rowsKey) in new[]{("passport","entryPassport","entryPassportCandidates"),("main","tilemapEntry","tilemapCandidates"),("side","tilemapSide","tilemapSideCandidates")})
        {
            var rows=Array(evidence[rowsKey]); var diagnostic=Project(evidence[key],Fields); var reasons=new JsonObject();
            foreach(var row in rows) { string reason=Text(row?["verification"],"reason"); if(reason.Length==0) reason=Text(row,"reason"); if(reason.Length==0) reason="not-recorded"; reasons[reason]=(int)Number(reasons[reason])+1; }
            diagnostic["candidateCount"]=rows.Count; diagnostic["candidateReasons"]=reasons;
            diagnostic["verifiedMapIds"]=Strings(rows.Where(row=>Flag(row?["verification"],"success")||Flag(row,"identityVerified")).Select(row=>Text(row,"mapId")).Distinct(StringComparer.Ordinal).Order(StringComparer.Ordinal));
            if(label=="side" && Text(result,"sideProducer")=="passport") diagnostic["candidateDetailsIn"]="passport";
            else diagnostic["bestCandidates"]=new JsonArray(rows.OrderByDescending(row=>Number(row?["score"])).Take(4).Select(row=>(JsonNode)Candidate(row!)).ToArray());
            result[label]=diagnostic;
        }
        var passport=evidence["entryPassport"]; var examined=Array(passport?["examined"]); var counts=new JsonObject();
        foreach(var row in examined) { string reason=Text(row,"reason"); if(reason.Length==0) reason="not-recorded"; counts[reason]=(int)Number(counts[reason])+1; }
        result["passport"]!["examinedCount"]=examined.Count; result["passport"]!["examinedReasons"]=counts;
        result["passport"]!["observations"]=new JsonArray(Array(passport?["observations"]).Select(row=>(JsonNode)new JsonObject { ["reliable"]=row?["reliable"]?.DeepClone(),["iconScore"]=row?["icon"]?["score"]?.DeepClone(),["direction"]=row?["direction"]?["direction"]?.DeepClone(),["directionReason"]=row?["direction"]?["reason"]?.DeepClone() }).ToArray());
        var source=evidence["sourceStructureCandidates"]; var summary=new JsonObject();
        foreach(string key in new[]{"reason","complete","featureStatus","queryKeypointCount","elapsedMs","evaluatedEntryCount","heldOutDecisionReason","variantExclusiveEvidence"}) summary[key]=source?[key]?.DeepClone();
        summary["candidateCount"]=Array(source?["candidates"]).Count; summary["candidates"]=source?["candidates"]?.DeepClone() ?? new JsonArray(); summary["automaticCandidate"]=source?["automaticCandidate"] is null?null:Project(source["automaticCandidate"],"runtimeMapId","sourceGroup","sourceCandidateId","producerVariantLabel","producerSourceFloorLabel","producerVariantUncertain","gameFloor","identityVerified","identityEvidence","poseSubmissionAllowed","displayEligible","heldOutVerification");
        summary["displayEligible"]=false; summary["identityHardGatePassed"]=false; result["sourceStructures"]=summary; return result;
    }
    private static JsonObject Candidate(JsonNode row)
    {
        var check=row["verification"]; var entry=row["entryEvidence"]; var residual=check?["residual"];
        var result=Project(row,"mapId","floor","profileId","score","entryKind","sourceEntryId","competitionComplete","poseAmbiguous");
        result["verified"]=check?["success"]?.DeepClone(); result["reason"]=check?["reason"]?.DeepClone(); result["verificationEvaluated"]=check?["evaluated"]?.DeepClone();
        result["matrixSourceToClient"]=row["matrixSourceToClient"]?.DeepClone(); result["verificationMatrixSourceToClient"]=check?["matrixSourceToClient"]?.DeepClone();
        foreach(string key in new[]{"validationPolicy","referenceChannel","regions","entryWalls"}) result[key]=check?[key]?.DeepClone();
        result["entry"]=Project(entry,"localGeometryVerified","directionCompatible","sourceEntryId"); result["regionalEdgeVerified"]=check?["regions"]?["regionalEdgeVerified"]?.DeepClone();
        result["author"]=Project(residual,"verified","reason","medianPixels","p90Pixels","withinThreePixels","withinSixPixels","explainedPixelFraction"); result["wallsVerified"]=check?["entryWalls"]?["verified"]?.DeepClone();
        result["hypothesis"]=Project(row["hypothesis"],"chainCount","extentGrid"); result["exitContradicted"]=(row["entryCrossCheck"] ?? entry?["allObservedExits"])?["contradicted"]?.DeepClone();
        result["structureVariants"]=new JsonArray(Array(check?["structureVariants"]).Select(variant=>(JsonNode)new JsonObject { ["name"]=variant?["name"]?.DeepClone(),["verified"]=variant?["verified"]?.DeepClone(),["regionalEdgeVerified"]=variant?["regions"]?["regionalEdgeVerified"]?.DeepClone(),["regions"]=variant?["regions"]?.DeepClone(),["walls"]=variant?["walls"]?.DeepClone() ?? new JsonObject() }).ToArray()); return result;
    }
}
