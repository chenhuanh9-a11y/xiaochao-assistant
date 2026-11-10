using System.Collections;
using System.Reflection;
using System.Text.Json;
using System.Text.Json.Nodes;
using StarMap.Core.Alignment;
using StarMap.Core.Session;
using StarMap.Runtime;
using StarMap.Vision.Steady;

internal static class GeometryGroupRuntimeRegression
{
    internal static async Task RunEstablished(string resources, string originalReplay, string probeOracle, string output, Action<bool, string> check)
    {
        var index = JsonNode.Parse(await File.ReadAllBytesAsync(Path.Combine(originalReplay, "all-samples.json")))!["samples"]!.AsArray()
            .ToDictionary(row => row!["id"]!.GetValue<string>(), row => row!.AsObject());
        var input = JsonNode.Parse(await File.ReadAllBytesAsync(probeOracle))!;
        var results = new JsonArray();
        foreach (var scenario in input["cases"]!.AsArray())
        foreach (var step in scenario!["steps"]!.AsArray().Where(row => row!["solveCalls"]!.AsArray().Count > 0))
        {
            string id = step!["id"]!.GetValue<string>(), map = scenario["mapId"]!.GetValue<string>();
            var port = new RecordedCapture(index); var runtime = new LiveRuntime(port, resources); var tickets = new SessionSequencer();
            try
            {
                await Ask("set_context", new { season = "s0", mode = scenario["mode"]!.GetValue<string>(), profileId = "shared-structure" });
                await Ask("start_round");
                // This is an explicit manual reference, including the ambiguous
                // A1 group, rather than an independently confirmed map identity.
                await Ask("confirm_route", new { mapId = map });
                port.Select(scenario["seed"]!["id"]!.GetValue<string>()); await Ask("capture_now");
                var before = Model(runtime); runtime.TryReadRender(out var material);
                check(before is not null && material is not null && runtime.ReadState().NativeGeometryVerified,
                    id + " own cold seed reaches the actual canonical group and material");
                runtime.AcknowledgeRenderMaterial(material!);
                await runtime.RememberRenderMaterialAsync(material!.MaterialToken, runtime.ReadSnapshot().Context, default);
                var rowsBefore = Rows(runtime);
                await Ask("close_map"); port.Select(id); await Ask("capture_now");
                var state = runtime.ReadState(); var after = Model(runtime);
                results.Add(JsonSerializer.SerializeToNode(new { id, map, state, before = before!.Snapshot(), after = after?.Snapshot(),
                    sameGroup = ReferenceEquals(before, after), rows = Rows(runtime).Select(row => new { row.Floor, row.Scale, row.Coverage }) }));
                await File.WriteAllTextAsync(Path.Combine(output, "default-established-runtime.json"), new JsonObject {
                    ["cases"] = results.DeepClone(), ["scope"] = "Actual default Runtime Host/canonical group/0059 miss/recovery rejection and cache consumption. Each saved frame starts from its own recomputed cold seed and explicit material ACK seam. Manual reference only; no natural game continuity, unique identity, condition-inventory producer or GUI claim."
                }.ToJsonString(new JsonSerializerOptions { WriteIndented = true }));
                var stages = state.Diagnostics.GetProperty("stages").EnumerateArray();
                check(stages.Any(row => row.GetProperty("stage").GetString()?.StartsWith("established-", StringComparison.Ordinal) == true)
                    && state.Diagnostics.GetProperty("steadyAttempt").GetProperty("attempted").GetBoolean(),
                    id + " real0059 miss reaches actual established recovery rather than a substituted success path");
                check(!state.NativeGeometryVerified && state.Reason == step["fit"]!["reason"]!.GetValue<string>()
                    && state.NativePose is null && !runtime.TryReadRender(out _) && runtime.ReadMaterialToRemember() is null,
                    id + " original default rejection withdraws the current pose and material");
                var rowsAfter = Rows(runtime);
                check(ReferenceEquals(before, after) && rowsBefore.Count == rowsAfter.Count
                    && rowsBefore.Zip(rowsAfter).All(pair => ReferenceEquals(pair.First.Owner, pair.Second.Owner)
                        && pair.First.Scale == pair.Second.Scale && JsonNode.DeepEquals(pair.First.Coverage, pair.Second.Coverage))
                    && state.IdentityLocked && state.Context.MapId == map,
                    id + " rejection preserves the actual previous group and cache without withdrawing manual identity");
            }
            finally { runtime.Shutdown(); }
            Task<RuntimeOperationResult> Ask(string action, object? payload = null) => runtime.DispatchAsync(new(action,
                JsonSerializer.SerializeToElement(payload ?? new { }), new(tickets.Issue(), runtime.ReadSnapshot().Context)), default).AsTask();
        }
    }

    internal static async Task Run(string resources, string originalReplay, string lifetimeOracle, string output, Action<bool, string> check)
    {
        var index = JsonNode.Parse(await File.ReadAllBytesAsync(Path.Combine(originalReplay, "all-samples.json")))!["samples"]!.AsArray()
            .ToDictionary(row => row!["id"]!.GetValue<string>(), row => row!.AsObject());
        var scenarios = JsonNode.Parse(await File.ReadAllBytesAsync(lifetimeOracle))!["cases"]!.AsArray()
            .Where(row => row!["steps"]!.AsArray().All(step => step!["steadyEnabled"]!.GetValue<bool>()));
        var evidence = new List<object>();
        foreach (var scenario in scenarios)
        {
            var port = new RecordedCapture(index); var runtime = new LiveRuntime(port, resources); var tickets = new SessionSequencer();
            string name = scenario!["name"]!.GetValue<string>(); string map = scenario["mapId"]!.GetValue<string>();
            try
            {
                await Ask("set_context", new { season = "s0", mode = "nightmare", profileId = "shared-structure" });
                await Ask("start_round"); int ordinal = 0;
                foreach (var step in scenario["steps"]!.AsArray())
                {
                    string id = step!["id"]!.GetValue<string>();
                    var before = Model(runtime);
                    if (ordinal++ > 0) await Ask("close_map");
                    port.Select(id); await Ask("capture_now");
                    if (ordinal == 1) await Ask("confirm_route", new { mapId = map });
                    var state = runtime.ReadState(); var after = Model(runtime); var expected = step["afterGroup"]!;
                    bool success = step["fit"]!["success"]!.GetValue<bool>();
                    string checkpoint = Path.Combine(output, name + "-" + ordinal.ToString("D2") + "-" + id + ".json");
                    await File.WriteAllTextAsync(checkpoint, JsonSerializer.Serialize(new { name, id, phase = "before-material-ACK", state,
                        beforeGroup = before?.Snapshot(), afterGroup = after?.Snapshot(), sameGroup = ReferenceEquals(before, after),
                        rows = Rows(runtime).Select(row => new { row.Floor, row.Scale, row.Coverage, currentOwner = ReferenceEquals(row.Owner, after) }) },
                        new JsonSerializerOptions { WriteIndented = true }));
                    check(state.NativeGeometryVerified == success && state.Reason == step["fit"]!["reason"]!.GetValue<string>(), name + "/" + id + " actual runtime verdict matches original Host/core");
                    check(after is not null && after.AnchorFloor == expected["anchorFloor"]!.GetValue<string>()
                        && after.MapId == map && after.RouteProfileId == expected["routeProfileId"]!.GetValue<string>()
                        && after.VerifiedFloors.SequenceEqual(Strings(expected["verified"]))
                        && after.CanonicalGeometryVerifiedFloors.SequenceEqual(Strings(expected["canonicalVerified"]))
                        && after.Relations.Keys.Order(StringComparer.Ordinal).SequenceEqual(expected["relations"]!.AsObject().Select(p => p.Key).Order(StringComparer.Ordinal)),
                        name + "/" + id + " actual single root and verified/relation floors match original");
                    Matrix(after!.RootPose.ToArray(), expected["root"]!, name + "/" + id + " actual canonical root");
                    check(success ? !ReferenceEquals(before, after) : ReferenceEquals(before, after),
                        name + "/" + id + " only successful canonical/core commit replaces the group owner");
                    if (success)
                    {
                        runtime.TryReadRender(out var packet);
                        check(packet is not null && runtime.ReadMaterialToRemember() == packet,
                            name + "/" + id + " current material proposes the same group-bound remember token");
                        // Same explicit consumer ACK seam as the original oracle.
                        // WPF readiness itself is covered separately in Workspace.Tests.
                        runtime.AcknowledgeRenderMaterial(packet!);
                        await runtime.RememberRenderMaterialAsync(packet!.MaterialToken, runtime.ReadSnapshot().Context, default);
                    }
                    var rows = Rows(runtime); var expectedRows = step["afterState"]!["rows"]!.AsArray();
                    await File.WriteAllTextAsync(checkpoint + ".ack.json", JsonSerializer.Serialize(new { name, id, state,
                        group = after?.Snapshot(), rows = rows.Select(row => new { row.Floor, row.Scale, row.Coverage, currentOwner = ReferenceEquals(row.Owner, after) }) },
                        new JsonSerializerOptions { WriteIndented = true }));
                    check(rows.Count == expectedRows.Count, name + "/" + id + " remember retains only the current owner floor rows");
                    for (int i = 0; i < rows.Count; i++)
                    {
                        var row = rows[i]; var original = expectedRows[i]!;
                        check(row.Floor == original["key"]![1]!.GetValue<string>() && ReferenceEquals(row.Owner, after)
                            && Math.Abs(row.Scale - original["scale"]!.GetValue<double>()) <= 1e-5,
                            name + "/" + id + " scale row belongs to the actual current target-floor owner");
                        check(JsonNode.DeepEquals(row.Coverage?["entries"], original["coverage"]?["entries"]),
                            name + "/" + id + " exact coverage proposal/refresh state matches original");
                    }
                    evidence.Add(new { name, id, state, group = after!.Snapshot(), rows = rows.Select(row => new { row.Floor, row.Scale, row.Coverage }) });
                }
                if (scenario["authorReprojection"] is not null)
                {
                    var before = Model(runtime); var rowsBefore = Rows(runtime);
                    await Ask("set_context", new { season = "s0", mode = "nightmare", profileId = "suju" });
                    check(ReferenceEquals(before, Model(runtime)) && runtime.ReadMaterialToRemember() is null
                        && Rows(runtime).Zip(rowsBefore).All(pair => ReferenceEquals(pair.First.Owner, pair.Second.Owner)
                            && pair.First.Scale == pair.Second.Scale && JsonNode.DeepEquals(pair.First.Coverage, pair.Second.Coverage)),
                        name + " author-only replacement preserves group/cache and proposes no new geometry remember");
                }
            }
            finally { runtime.Shutdown(); }
            Task<RuntimeOperationResult> Ask(string action, object? payload = null) => runtime.DispatchAsync(new(action,
                JsonSerializer.SerializeToElement(payload ?? new { }), new(tickets.Issue(), runtime.ReadSnapshot().Context)), default).AsTask();
        }
        await File.WriteAllTextAsync(Path.Combine(output, "geometry-group-lifetimes.json"), JsonSerializer.Serialize(new {
            evidence, scope = "Actual LiveRuntime capture/Host/group/core/material-maintenance path. Only OS capture and consumer ACK are explicit recorded seams. Three all-enabled original scenarios only. Tracking fields not yet ported are not asserted. No GUI/game/unique identity claim."
        }, new JsonSerializerOptions { WriteIndented = true }));

        void Matrix(double[][] actual, JsonNode expected, string name)
        {
            var wanted = expected.Deserialize<double[][]>()!;
            check(actual.Length == 3 && wanted.Length == 3 && actual.All(row => row.Length == 3) && wanted.All(row => row.Length == 3)
                && actual.SelectMany(row => row).Zip(wanted.SelectMany(row => row)).All(pair => Math.Abs(pair.First - pair.Second) <= 1e-5), name);
        }
    }
    static string[] Strings(JsonNode? value) => value!.AsArray().Select(row => row!.GetValue<string>()).Order(StringComparer.Ordinal).ToArray();
    static object? Field(object target, string name) => target.GetType().GetField(name, BindingFlags.NonPublic | BindingFlags.Instance)!.GetValue(target);
    static object? Property(object target, string name) => target.GetType().GetProperty(name)!.GetValue(target);
    static RoundLayerGroup? Model(LiveRuntime runtime) => Field(runtime, "_geometryGroup") is { } group ? (RoundLayerGroup)Property(group, "Model")! : null;
    sealed record Row(string Floor, RoundLayerGroup Owner, double Scale, JsonObject? Coverage);
    static List<Row> Rows(LiveRuntime runtime)
    {
        var result = new List<Row>();
        foreach (DictionaryEntry pair in (IDictionary)Field(runtime, "_steadyFloors")!)
            result.Add(new((string)Property(pair.Key, "Floor")!, (RoundLayerGroup)Property(pair.Value!, "Owner")!,
                (double)Property(pair.Value!, "Scale")!, (Property(pair.Value!, "Coverage") as SteadyCoverageState)?.Snapshot()));
        return result.OrderBy(row => row.Floor, StringComparer.Ordinal).ToList();
    }
}
