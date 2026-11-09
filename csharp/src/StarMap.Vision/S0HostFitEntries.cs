using System.Text.Json.Nodes;
using OpenCvSharp;
using StarMap.Core.Resources;
using static StarMap.Vision.S1StructureObservations;

namespace StarMap.Vision;

public sealed partial class S0HostFit
{
    private JsonObject? ReverifyEntry(Mat frame, ResourceCatalogEntry target, Prepared reference, JsonObject? candidate,
        S0HostFitOptions options, S0HostStageContext stageContext, Func<bool> obsolete, CancellationToken cancellation, Action<string, JsonObject?> log)
    {
        if (candidate is null || !SameTarget(candidate, target)) return null;
        bool isSide = candidate["locatorKind"]?.ToString() == "side" || candidate["entryKind"]?.ToString() == "side";
        bool anchored = isSide && (candidate.ContainsKey("entryPoseCandidates") || Flag(candidate, "exitAnchoredEntry"));
        var candidates = anchored && candidate["entryPoseCandidates"] is JsonArray poses ? poses : new JsonArray(candidate.DeepClone());
        var accepted = new List<(JsonObject Check, JsonObject Residual)>();
        foreach (var node in candidates)
        {
            if (obsolete()) return Failure("structure-work-obsolete");
            if (node is not JsonObject item || !SameTarget(item, target)) continue;
            try
            {
                JsonObject? check = null;
                if (isSide)
                {
                    if (item["floor"] is null || item["matrixSourceToClient"] is not JsonArray { Count: 3 } matrix ||
                        matrix.Any(row => row is not JsonArray { Count: 3 } values || values.Any(value => value is null)))
                        throw new InvalidDataException("Side entry candidate has no target floor/matrix for reverification.");
                    check = side.VerifyCandidate(frame, item, cancellation);
                }
                else if (item["hypothesis"] is JsonObject hypothesis)
                {
                    if (hypothesis["scale"] is not JsonValue || hypothesis["translation"] is not JsonArray { Count: 2 } translation || translation.Any(value => value is not JsonValue))
                        throw new InvalidDataException("Entry hypothesis has no scale/translation for main reverification.");
                    // MapEntryGeometryIdentity.verify resolves its own first
                    // registered main item by mapId. With the shared provider
                    // installed it loads that item's floor, replacing route;
                    // the Host target-floor mask is not this verifier's input.
                    // A raw local-structure candidate may deliberately fail
                    // this main check before target-floor initial recovery.
                    var mainRegistration = catalog.MainEntries.FirstOrDefault(row => row.GetProperty("mapId").GetString() == target.MapId);
                    if (mainRegistration.ValueKind == System.Text.Json.JsonValueKind.Undefined)
                        throw new InvalidDataException("Main entry registration unavailable for reverification.");
                    string registeredFloor = mainRegistration.GetProperty("floor").GetString()!;
                    var mainTarget = resources.Find(catalog.Mode, catalog.Season, catalog.MapProfiles[target.MapId], target.MapId, registeredFloor);
                    var mainReference = mainTarget is null ? null : Prepare(mainTarget, out _);
                    log("entry-main-reverification-reference", new()
                    {
                        ["registeredMainFloor"] = registeredFloor, ["resourceKey"] = mainTarget?.StableKey,
                        ["hostTargetResourceKey"] = target.StableKey
                    });
                    using var observation = EntryObservation.Capture(frame);
                    if (mainReference is null) check = Failure("entry-resource-unavailable");
                    else if (observation is not null) check = MainEntryVerification.Verify(frame, observation.Geometry, hypothesis, mainReference.Geometry);
                }
                if (IsSuccess(check))
                {
                    var residual = CanonicalResidual(frame, reference, Matrix(check!["matrixSourceToClient"]!));
                    if (ResidualVerified(residual)) accepted.Add((check!, residual));
                }
            }
            catch (Exception error) when (ResourceFailure(error)) { log("entry-seed-reverification-unavailable", new() { ["error"] = error.Message }); }
        }
        if (obsolete()) return Failure("structure-work-obsolete");
        if (accepted.Count == 1)
        {
            var (check, residual) = accepted[0];
            return new() { ["success"] = true, ["reason"] = "difficult-entry-current-frame-candidate", ["matrixSourceToClient"] = check["matrixSourceToClient"]!.DeepClone(),
                ["coordinateSpace"] = "shared-structure-to-client", ["routeProfileId"] = "shared-structure", ["selectedRouteCurrentFrameResidual"] = residual,
                ["strategy"] = "verified-entry-current-frame", ["difficultEntryValidation"] = check.DeepClone() };
        }
        if (!anchored) return null;
        var rejected = new JsonObject { ["success"] = false, ["reason"] = accepted.Count > 0 ? "side-entry-pose-ambiguous" : "side-entry-pose-unverified" };
        if (options.PoseIndexCandidatesEnabled)
        {
            var indexed = services.PoseIndexInitial?.Invoke(stageContext, true) ?? Failure("s0-pose-index-fit-unavailable");
            if (Reason(indexed) != "pose-index-no-candidates")
            {
                indexed["entryFallback"] = "confirmed-map-floor-pose-index"; indexed["difficultEntryValidation"] = rejected; return indexed;
            }
        }
        return new() { ["success"] = false, ["reason"] = "difficult-entry-current-frame-rejected", ["difficultEntryValidation"] = rejected,
            ["entryReverification"] = new JsonObject { ["candidateCount"] = candidates.Count, ["acceptedPoseCount"] = accepted.Count, ["reason"] = rejected["reason"]!.DeepClone() } };
    }

    private JsonObject? RegisteredFloor(Mat frame, ResourceCatalogEntry target, Prepared reference, S0HostGroupState? group,
        Func<bool> obsolete, CancellationToken cancellation, Action<string, JsonObject?> log)
    {
        if (obsolete()) return Failure("structure-work-obsolete");
        if (target.Profile != "shared-structure" || !catalog.RegisteredFloors.Contains((target.MapId, target.Floor)) || group?.VerifiedFloors.Contains(target.Floor) == true) return null;
        log("registered-floor-local-started", null);
        JsonObject Accepted(JsonObject candidate, JsonObject diagnostics, string reason)
        {
            var check = candidate["verification"]!.AsObject(); var residual = check["residual"]!.DeepClone().AsObject();
            residual["validationBasis"] = "actual-rendered-profile-structural-mask"; residual["poseVerifiedAgainstRenderedProfile"] = true;
            return new() { ["success"] = true, ["reason"] = reason, ["matrixSourceToClient"] = check["matrixSourceToClient"]!.DeepClone(),
                ["coordinateSpace"] = "shared-structure-to-client", ["routeProfileId"] = "shared-structure", ["selectedRouteCurrentFrameResidual"] = residual,
                ["difficultEntryValidation"] = check.DeepClone(), ["localFloorDiagnostics"] = diagnostics.DeepClone() };
        }
        try
        {
            var result = main.Rank(frame, target.Floor, TimeSpan.FromSeconds(.35), knownMapId: target.MapId, cancellation: cancellation);
            if (obsolete()) return Failure("structure-work-obsolete");
            var selected = result.Rows.FirstOrDefault(node => node is JsonObject row && SameTarget(row, target) && Flag(row, "poseVerified") && IsSuccess(row["verification"]))?.AsObject();
            if (selected is not null)
            {
                log("registered-floor-main-attempt", new() { ["success"] = true, ["diagnostics"] = result.Diagnostics.DeepClone() });
                return Accepted(selected, result.Diagnostics, "registered-floor-main-geometry");
            }
        }
        catch (Exception error) when (ResourceFailure(error))
        {
            log("registered-floor-main-unavailable", new() { ["error"] = error.Message });
            return Failure("registered-floor-main-resource-unavailable");
        }
        JsonArray candidates; JsonObject diagnostic;
        try
        {
            var result = side.Rank(frame, target.Floor, TimeSpan.FromSeconds(.35), knownMapId: target.MapId, cancellation: cancellation);
            candidates = result.Rows; diagnostic = result.Diagnostics;
        }
        catch (Exception error) when (ResourceFailure(error))
        {
            candidates = new(); diagnostic = new() { ["complete"] = false, ["reason"] = "registered-floor-resource-unavailable", ["error"] = error.Message };
        }
        if (obsolete()) return Failure("structure-work-obsolete");
        if (candidates.Any(node => node is not JsonObject row || !SameTarget(row, target))) return Failure("registered-floor-candidate-scope-mismatch");
        if (Flag(diagnostic, "complete") && diagnostic["competitiveHypotheses"]?.GetValue<int>() == 1 && candidates.Count > 0 &&
            !IsSuccess(candidates[0]?["verification"]) && Flag(candidates[0]?["verification"]?["regions"], "regionalEdgeVerified"))
        {
            try { candidates[0] = side.RefineCandidate(frame, candidates[0]!.AsObject(), cancellation); }
            catch (Exception error) when (ResourceFailure(error)) { diagnostic["refinementError"] = error.Message; }
        }
        if (obsolete()) return Failure("structure-work-obsolete");
        if (candidates.Any(node => node is not JsonObject row || !SameTarget(row, target))) return Failure("registered-floor-candidate-scope-mismatch");
        var winner = SideEntryDecision.VerifiedStructureCandidate(catalog, candidates, diagnostic, target.Floor, new(), frame.Size());
        if (winner is not null && !Flag(winner, "poseVerified")) winner = null;
        log("registered-floor-local-attempt", new() { ["success"] = winner is not null, ["diagnostics"] = diagnostic.DeepClone(),
            ["refinementMs"] = candidates.FirstOrDefault()?["continuousRefinementMs"]?.DeepClone() ?? System.Text.Json.Nodes.JsonValue.Create(0d) });
        return winner is null ? null : Accepted(winner, diagnostic, "registered-floor-local-geometry");
    }

    private static bool SameTarget(JsonObject candidate, ResourceCatalogEntry target) => candidate["mapId"]?.ToString() == target.MapId &&
        (!candidate.ContainsKey("floor") || candidate["floor"]?.ToString() == target.Floor);
}
