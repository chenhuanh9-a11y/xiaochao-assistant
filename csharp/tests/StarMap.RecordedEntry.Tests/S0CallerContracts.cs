using System.Text.Json;
using System.Text.Json.Nodes;
using OpenCvSharp;
using StarMap.Core.Identity;
using StarMap.Core.Resources;
using StarMap.Vision;
using static ReplayJson;

internal static class S0CallerContracts
{
    // These deliberately invalid/control-flow inputs are separate from every
    // recorded-frame numerical result. Real A supplies target and baseline;
    // the independent original caller answers are read only afterwards.
    internal static JsonObject Solve(ResourceCatalog resources, EntryPassportCatalog catalog, Mat frame,
        ResourceCatalogEntry target, JsonObject baseline)
    {
        object owner = new();
        var anchored = new JsonObject { ["mapId"] = target.MapId, ["floor"] = target.Floor, ["entryKind"] = "side", ["entryPoseCandidates"] = new JsonArray() };
        var cases = new JsonObject();
        using var plain = new S0HostFit(resources, catalog);
        var cold = new S0HostFitOptions { FastAlignmentEnabled = false };
        Add("anchored-empty-poses-terminal", plain.Fit(frame, target, anchored, cold), 0);
        Add("cancellation-terminal", plain.Fit(frame, target, anchored, cold, () => true), 0);
        foreach (bool terminal in new[] { true, false })
        {
            int calls = 0;
            using var steady = new S0HostFit(resources, catalog, new(Steady: _ => { calls++; return terminal ? new JsonObject { ["success"] = false, ["reason"] = "steady-terminal-probe" } : null; }));
            var result = steady.Fit(frame, target, anchored, cold with { OwnerScope = owner, Epoch = 7, SteadyRequested = true });
            Add(terminal ? "steady-failure-terminal" : "steady-null-then-entry-rejection", result, calls);
        }
        var capability = new JsonObject();
        var missingSteady = plain.Fit(frame, target, anchored, cold with { OwnerScope = owner, Epoch = 7, SteadyRequested = true });
        capability["unavailableSteadyRejected"] = missingSteady.Fit["reason"]?.ToString() == "s0-steady-fit-unavailable" && !Stages(missingSteady).Contains("initial");
        var group = new S0HostGroupState(owner, 7, target.Mode, target.Season, target.MapId, "shared-structure",
            new HashSet<string> { target.Floor }, new HashSet<string> { target.Floor });
        var missingGroup = plain.Fit(frame, target, null, new() { OwnerScope = owner, Epoch = 7, Group = group });
        capability["unavailableGroupRejected"] = missingGroup.Fit["reason"]?.ToString() == "s0-established-group-fit-unavailable" &&
            !Stages(missingGroup).Contains("initial") && !Stages(missingGroup).Contains("fast-global");
        var staleGroup = plain.Fit(frame, target, null, cold with { OwnerScope = new object(), Epoch = 7, Group = group });
        capability["staleGroupRejected"] = staleGroup.Fit["reason"]?.ToString() == "s0-host-group-context-mismatch" && staleGroup.Stages.Count == 0;
        var other = catalog.Floors.Where(floor => floor != target.Floor).Select(floor => resources.Find(target.Mode, target.Season, target.Profile, target.MapId, floor)).FirstOrDefault(entry => entry is not null)
            ?? throw new InvalidDataException("Caller prior boundary probe requires another actual registered floor of the same A map.");
        var matrix = baseline["matrixSourceToClient"]!.Deserialize<double[][]>()!;
        var wrongFloor = plain.Fit(frame, target, null, new() { OwnerScope = owner, Epoch = 7, PosePrior = new(owner, 7, other, matrix) });
        capability["wrongFloorPriorRejected"] = !Stages(wrongFloor).Contains("fast-prior") && wrongFloor.Events.Any(row => row?["reason"]?.ToString() == "pose-prior-context-or-matrix-mismatch");
        var staleEpoch = plain.Fit(frame, target, null, new() { OwnerScope = owner, Epoch = 7, PosePrior = new(owner, 6, target, matrix) });
        capability["staleEpochPriorRejected"] = !Stages(staleEpoch).Contains("fast-prior") && staleEpoch.Events.Any(row => row?["reason"]?.ToString() == "pose-prior-context-or-matrix-mismatch");
        return new() { ["originalCases"] = cases, ["capabilityChecks"] = capability,
            ["scope"] = "Control-flow and state-scope probes on actual A-derived target/frame; separate from recorded-frame numerical acceptance" };
        void Add(string name, S0HostFitResult result, int calls) => cases[name] = new JsonObject { ["fit"] = result.Fit.DeepClone(), ["steadyCalls"] = calls, ["stages"] = result.Stages.DeepClone() };
    }
    internal static void CompareOriginal(string oracle, JsonObject actual, List<JsonObject> differences)
    {
        Compare("/callerContracts/original", Read(oracle)["cases"], actual["originalCases"], differences);
        foreach (var check in actual["capabilityChecks"]!.AsObject())
            if (check.Value?.GetValue<bool>() != true) Difference(differences, "/callerContracts/capability/" + check.Key, "required-state-boundary", JsonValue.Create(true), check.Value);
    }
    private static string[] Stages(S0HostFitResult result) => result.Stages.Select(row => row!["stage"]!.GetValue<string>()).ToArray();
}
