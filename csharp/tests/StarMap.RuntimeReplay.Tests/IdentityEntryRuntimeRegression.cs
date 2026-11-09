using System.Reflection;
using System.Text.Json;
using System.Text.Json.Nodes;
using StarMap.Core.Identity;
using StarMap.Core.Session;
using StarMap.Runtime;

internal static class IdentityEntryRuntimeRegression
{
    internal static async Task RunUnselectedFloor(string resources, string originalReplay, string originalOracle, string output, Action<bool, string> check)
    {
        var index = JsonNode.Parse(await File.ReadAllBytesAsync(Path.Combine(originalReplay, "all-samples.json")))!["samples"]!.AsArray()
            .ToDictionary(row => row!["id"]!.GetValue<string>(), row => row!.AsObject());
        var input = JsonNode.Parse(await File.ReadAllBytesAsync(originalOracle))!; var evidence = new JsonArray();
        foreach (var scenario in input["cases"]!.AsArray())
        {
            var port = new RecordedCapture(index); var runtime = new LiveRuntime(port, resources); var tickets = new SessionSequencer();
            try
            {
                foreach (var step in scenario!["steps"]!.AsArray())
                {
                    string action = step!["action"]!.GetValue<string>(); if (action == "bind_capture") continue;
                    if (action == "capture_now") port.Select(step["input"]!["id"]!.GetValue<string>());
                    var result = await Ask(action, step["payload"]);
                    if (action != "capture_now") continue;
                    var state = runtime.ReadState(); var expected = step["after"]!;
                    evidence.Add(JsonSerializer.SerializeToNode(new { id = scenario["id"]!.GetValue<string>(), state,
                        receiptSuccess = result.Receipt.Success, nativeIdentityPublished = NativeIdentity(runtime) is not null }));
                    await File.WriteAllTextAsync(Path.Combine(output, "runtime-unselected-floor.json"), evidence.ToJsonString(new JsonSerializerOptions { WriteIndented = true }));
                    check(result.Receipt.Success == step["result"]!["success"]!.GetValue<bool>()
                        && state.Reason == step["result"]!["reason"]!.GetValue<string>(),
                        scenario["id"] + " actual pending/disabled receipt matches original public entry");
                    check(state.MapOpen && state.Context.FloorId == expected["floor"]!.GetValue<string>()
                        && state.IdentitySource == expected["identitySource"]!.GetValue<string>()
                        && !state.IdentityLocked && state.NativePose is null && !runtime.TryReadRender(out _) && !runtime.ReadSnapshot().LiveAuthorization,
                        scenario["id"] + " floor observation stays current without map/geometry/display authority");
                    check(NativeIdentity(runtime) is null, scenario["id"] + " no S0 identity rank is published in disabled or S1 structure-only entry");
                    var wanted = step["result"]?["structureEvidence"]?["currentSupportedMapIds"] as JsonArray;
                    check(wanted is not null
                            ? state.Candidates.Select(row => row.MapId).SequenceEqual(wanted.Select(row => row!.GetValue<string>()))
                            : state.Candidates.Count == 0,
                        scenario["id"] + " current structure-only candidates or empty disabled inventory match original");
                }
            }
            finally { runtime.Shutdown(); }
            Task<RuntimeOperationResult> Ask(string action, object? payload = null) => runtime.DispatchAsync(new(action,
                JsonSerializer.SerializeToElement(payload ?? new { }), new(tickets.Issue(), runtime.ReadSnapshot().Context)), default).AsTask();
        }
    }

    internal static async Task RunPrior(string resources, string originalReplay, string priorOracle, string output, Action<bool, string> check)
    {
        var index = JsonNode.Parse(await File.ReadAllBytesAsync(Path.Combine(originalReplay, "all-samples.json")))!["samples"]!.AsArray()
            .ToDictionary(row => row!["id"]!.GetValue<string>(), row => row!.AsObject());
        var input = JsonNode.Parse(await File.ReadAllBytesAsync(priorOracle))!; var evidence = new JsonArray();
        foreach (var scenario in input["cases"]!.AsArray())
        {
            var port = new RecordedCapture(index); var runtime = new LiveRuntime(port, resources); var tickets = new SessionSequencer();
            try
            {
                await Ask("set_context", new { season = "s0", mode = "nightmare", profileId = "shared-structure" });
                await Ask("start_round");
                foreach (var step in scenario!["steps"]!.AsArray().Where(row => row!["action"]!.GetValue<string>() == "capture_now"))
                {
                    string id = step!["input"]!["id"]!.GetValue<string>(); port.Select(id); await Ask("capture_now");
                    var state = runtime.ReadState(); var native = (NativeSimilarityState)NativeIdentity(runtime)!;
                    var prior = native.Prior(); var snapshot = native.Snapshot();
                    var expected = step["after"]!; var rank = step["rankEvaluations"]!.AsArray().Last()!;
                    evidence.Add(JsonSerializer.SerializeToNode(new { id, state, native = snapshot,
                        priorContext = prior?.Context, domains = prior?.Domains }));
                    await File.WriteAllTextAsync(Path.Combine(output, "runtime-actual-prior.json"), evidence.ToJsonString(new JsonSerializerOptions { WriteIndented = true }));
                    check(!state.IdentityLocked && state.Context.MapId is null && state.NativePose is null
                        && state.IdentitySource == expected["identitySource"]!.GetValue<string>()
                        && !runtime.TryReadRender(out _) && !runtime.ReadSnapshot().LiveAuthorization,
                        id + " actual established condition ledger does not authorize a unique map, pose or material");
                    var wantedPrior = expected["identityPrior"]!.AsArray(); var bindings = wantedPrior[3]!.AsArray();
                    check(prior is not null && prior.Context.Mode == wantedPrior[0]!.GetValue<string>()
                        && prior.Context.Floor == wantedPrior[1]!.GetValue<string>() && prior.Context.ResourceKey == wantedPrior[2]!.GetValue<string>()
                        && prior.Domains.Count == bindings.Count && bindings.All(binding => prior.Domains[binding![0]!.GetValue<string>()] is JsonArray found
                            && found.Select(value => value!.GetValue<string>()).ToHashSet(StringComparer.Ordinal)
                                .SetEquals(binding[1]!.AsArray().Select(value => value!.GetValue<string>()))),
                        id + " actual Runtime state derives the same original six bindings without injected priors");
                    check(state.Diagnostics.GetProperty("sourceAdvisory").GetProperty("reason").GetString() == rank["sourceStructureReason"]!.GetValue<string>(),
                        id + " actual rank consumer uses the original normal or established-only producer scope");
                    var expectedRows = rank["candidateRows"]!.AsArray();
                    check(state.Candidates.Count == expectedRows.Count && state.Candidates.Zip(expectedRows).All(pair =>
                        pair.First.MapId == pair.Second!["mapId"]!.GetValue<string>() && pair.First.Score == pair.Second["score"]!.GetValue<double>()
                        && pair.First.IdentityVerified == pair.Second["identityHardGatePassed"]!.GetValue<bool>()),
                        id + " complete current candidate cohort and scores match original Runtime");
                    check(snapshot["observationCount"]!.GetValue<int>() == expected["nativeSnapshot"]!["observationCount"]!.GetValue<int>(),
                        id + " repeated original pixels do not become a second distinct identity observation");
                }
            }
            finally { runtime.Shutdown(); }
            Task<RuntimeOperationResult> Ask(string action, object? payload = null) => runtime.DispatchAsync(new(action,
                JsonSerializer.SerializeToElement(payload ?? new { }), new(tickets.Issue(), runtime.ReadSnapshot().Context)), default).AsTask();
        }
    }

    internal static async Task Run(string resources, string originalReplay, string originalRuntime, string output, Action<bool, string> check)
    {
        var index = JsonNode.Parse(await File.ReadAllBytesAsync(Path.Combine(originalReplay, "all-samples.json")))!["samples"]!.AsArray()
            .ToDictionary(row => row!["id"]!.GetValue<string>(), row => row!.AsObject());
        var summary = JsonNode.Parse(await File.ReadAllBytesAsync(Path.Combine(originalRuntime, "summary.json")))!;
        var observations = new JsonArray();
        foreach (var scenario in summary["cases"]!.AsArray())
        {
            string directory = scenario!["directory"]!.GetValue<string>(), name = scenario["name"]!.GetValue<string>();
            var port = new RecordedCapture(index); var runtime = new LiveRuntime(port, resources); var tickets = new SessionSequencer();
            int publishedRanks = 0;
            try
            {
                await Ask("set_context", new { season = "s0", mode = "nightmare", profileId = "shared-structure" });
                await Ask("start_round");
                foreach (string path in Directory.EnumerateFiles(directory, "*-capture_now.json").Order(StringComparer.Ordinal))
                {
                    var source = JsonNode.Parse(await File.ReadAllBytesAsync(path))!;
                    string id = source["input"]!["id"]!.GetValue<string>();
                    object? beforeIdentity = NativeIdentity(runtime);
                    port.Select(id); await Ask("capture_now");
                    if (!ReferenceEquals(beforeIdentity, NativeIdentity(runtime))) publishedRanks++;
                    var state = runtime.ReadState();
                    observations.Add(JsonSerializer.SerializeToNode(new { name, id, state, publishedRanks }));
                    await File.WriteAllTextAsync(Path.Combine(output, "runtime-identity-entry.json"), new JsonObject {
                        ["observations"] = observations.DeepClone(),
                        ["scope"] = "Actual automatic Runtime entry; source original Runtime public dispatch with saved PNG capture and explicit material ACK seam. No manually supplied map, pose, prior or identity result. Published native-state object changes observe the RankS0 writer; no GUI/game/unique identity claim."
                    }.ToJsonString(new JsonSerializerOptions { WriteIndented = true }));
                    int sourceRanks = source["countsAfter"]!["identity-stage-result"]!.GetValue<int>();
                    check(publishedRanks == sourceRanks, name + "/" + id + " actual identity publication count follows original Runtime entry");
                    var expected = source["after"]!;
                    check(state.Context.MapId == expected["mapId"]!.GetValue<string>()
                        && state.IdentitySource == expected["identitySource"]!.GetValue<string>()
                        && state.Context.FloorId == expected["floor"]!.GetValue<string>(),
                        name + "/" + id + " automatic provisional member/source/floor match original without a manual seed");
                    bool success = source["result"]!["success"]!.GetValue<bool>();
                    string reason = (source["result"]!["poseReason"] ?? source["result"]!["reason"])!.GetValue<string>();
                    check(state.NativeGeometryVerified == success && state.Reason == reason,
                        name + "/" + id + " actual Host verdict and consumed fit reason match original");
                    runtime.TryReadRender(out var packet);
                    check((packet is not null) == success && !runtime.ReadSnapshot().LiveAuthorization,
                        name + "/" + id + " failed fit withdraws current material and Recorded never grants live authority");
                    if (success)
                    {
                        string fitPath = source["calls"]!.AsArray().Last(row => row!["kind"]!.GetValue<string>() == "host-fit-exit")!["file"]!.GetValue<string>();
                        var originalFit = JsonNode.Parse(await File.ReadAllBytesAsync(Path.Combine(directory, fitPath)))!["fit"]!;
                        var wanted = originalFit["matrixSourceToClient"]!.Deserialize<double[][]>()!;
                        check(state.NativePose is not null && state.NativePose.ToMatrix().SelectMany(row => row)
                            .Zip(wanted.SelectMany(row => row)).All(pair => Math.Abs(pair.First-pair.Second) <= 1e-5),
                            name + "/" + id + " own automatic native matrix matches original after production solve");
                        await runtime.RememberRenderMaterialAsync(packet!.MaterialToken, runtime.ReadSnapshot().Context, default);
                    }
                }
            }
            finally { runtime.Shutdown(); }
            Task<RuntimeOperationResult> Ask(string action, object? payload = null) => runtime.DispatchAsync(new(action,
                JsonSerializer.SerializeToElement(payload ?? new { }), new(tickets.Issue(), runtime.ReadSnapshot().Context)), default).AsTask();
        }
        await Transactions(Path.Combine(Path.GetDirectoryName(originalRuntime)!, "comparison.json"));

        async Task Transactions(string path)
        {
            var input = JsonNode.Parse(await File.ReadAllBytesAsync(path))!; var evidence = new JsonArray();
            foreach (var scenario in input["cases"]!.AsArray().Where(row => row!["id"]!.GetValue<string>() is "manual-current" or "manual-desired" or "reset"))
            {
                string name = scenario!["id"]!.GetValue<string>();
                var port = new RecordedCapture(index); var runtime = new LiveRuntime(port, resources); var tickets = new SessionSequencer();
                int published = 0, expectedPublications = 0;
                try
                {
                    foreach (var step in scenario["steps"]!.AsArray())
                    {
                        string action = step!["action"]!.GetValue<string>();
                        if (action == "bind_capture") continue; // The recorded port is already the explicit OS binding seam.
                        object? previous = NativeIdentity(runtime); bool? acceptedAck = null;
                        if (action == "capture_now") port.Select(step["input"]!["id"]!.GetValue<string>());
                        switch (action)
                        {
                            case "material-ack":
                                acceptedAck = runtime.TryReadRender(out _); break;
                            case "sibling-ack":
                                var pending = runtime.ReadPendingGroupMaterial();
                                acceptedAck = pending is not null && runtime.AcceptGroupMaterialReady(pending); break;
                            case "remember_render_material":
                                if (runtime.TryReadRender(out var material))
                                    await runtime.RememberRenderMaterialAsync(material!.MaterialToken, runtime.ReadSnapshot().Context, default);
                                break;
                            case "select_group_member":
                                var current = runtime.ReadState();
                                await Ask(action, new { mapId = step["payload"]!["mapId"]!.GetValue<string>(),
                                    groupId = current.IdentityGroupId, groupGeneration = current.GroupChoice.Generation,
                                    cycleId = current.GroupChoice.CycleId, floor = current.Context.FloorId });
                                break;
                            default: await Ask(action, step["payload"]); break;
                        }
                        object? identity = NativeIdentity(runtime);
                        if (identity is not null && !ReferenceEquals(previous, identity)) published++;
                        expectedPublications += step["countDelta"]?["identity-stage-result"]?.GetValue<int>() ?? 0;
                        var state = runtime.ReadState(); var expected = step["after"]!;
                        var pendingMaterial = runtime.ReadPendingGroupMaterial(); bool rendered = runtime.TryReadRender(out _);
                        evidence.Add(JsonSerializer.SerializeToNode(new { name, action, state, published, expectedPublications, acceptedAck,
                            pendingMap = pendingMaterial?.MapId, rendered }));
                        await File.WriteAllTextAsync(Path.Combine(output, "runtime-identity-transactions.json"), evidence.ToJsonString(new JsonSerializerOptions { WriteIndented = true }));
                        check(published == expectedPublications, name + "/" + step["index"] + " identity publication follows original reset/selection entry");
                        string source = state.IdentitySource == "group-manual" ? "manual-group-member" : state.IdentitySource;
                        check((state.Context.MapId ?? "") == expected["mapId"]!.GetValue<string>() && source == expected["identitySource"]!.GetValue<string>(),
                            name + "/" + step["index"] + " current member and manual/default source match original");
                        string? held = expected["manualGroupChoice"] is JsonArray heldTuple ? heldTuple[3]!.GetValue<string>() : null;
                        check(state.GroupChoice.HeldMapId == held && pendingMaterial?.MapId == expected["pendingSibling"]?["mapId"]?.GetValue<string>(),
                            name + "/" + step["index"] + " pending target and held member commit only at the actual consumer ACK seam");
                        check(rendered == step["displayAuthorized"]!.GetValue<bool>() && !runtime.ReadSnapshot().LiveAuthorization
                            && acceptedAck is not false,
                            name + "/" + step["index"] + " current display authority stays separate from pending target and Recorded context");
                    }
                }
                finally { runtime.Shutdown(); }
                Task<RuntimeOperationResult> Ask(string action, object? payload = null) => runtime.DispatchAsync(new(action,
                    JsonSerializer.SerializeToElement(payload ?? new { }), new(tickets.Issue(), runtime.ReadSnapshot().Context)), default).AsTask();
            }
        }
    }
    private static object? NativeIdentity(LiveRuntime runtime) => typeof(LiveRuntime)
        .GetField("_nativeIdentity", BindingFlags.NonPublic | BindingFlags.Instance)!.GetValue(runtime);
}
