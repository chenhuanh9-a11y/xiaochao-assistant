using System.Collections;
using System.Reflection;
using System.Text.Json;
using System.Text.Json.Nodes;
using StarMap.Core.Alignment;
using StarMap.Core.Session;
using StarMap.Runtime;

internal static class CurrentPoseRenewalRegression
{
    internal static async Task Run(string resources, string replay, string oracle, string output, Action<bool, string> check)
    {
        var index = JsonNode.Parse(await File.ReadAllBytesAsync(Path.Combine(replay, "all-samples.json")))!["samples"]!.AsArray()
            .ToDictionary(row => row!["id"]!.GetValue<string>(), row => row!.AsObject());
        var manifest = JsonNode.Parse(await File.ReadAllBytesAsync(oracle))!;
        var paths = manifest["oracles"] is JsonArray files ? files.Select(p => p!.GetValue<string>()).ToArray() : [oracle];
        var evidence = new JsonArray();
        foreach (string path in paths)
        foreach (var scenario in JsonNode.Parse(await File.ReadAllBytesAsync(path))!["cases"]!.AsArray())
        {
            string name = scenario!["name"]!.GetValue<string>();
            var capture = new RecordedCapture(index); var runtime = new LiveRuntime(capture, resources); var sequence = new SessionSequencer();
            var offered = new Dictionary<string, RuntimeRenderPacket>();
            try
            {
                foreach (var step in scenario["steps"]!.AsArray())
                {
                    string action = step!["action"]!.GetValue<string>();
                    var payload = step["payload"]!; string? sourceToken = payload["renderToken"]?.GetValue<string>();
                    var before = runtime.ReadState(); runtime.TryReadRender(out var previous);
                    var group = Field(runtime, "_geometryGroup"); var core = (AlignmentCoreV2?)Field(runtime, "_alignment");
                    var pose = core?.ActivePose; var cache = Cache(runtime); int calls = capture.Calls;
                    RuntimeOperationResult? result = null; RenderMaterialAcknowledgement? acknowledgement = null;
                    if (step["input"]?["id"] is { } input) capture.Select(input.GetValue<string>(), false);
                    switch (action)
                    {
                        case "bind_capture": break; // Only the recorded OS port is replaced.
                        case "configure-settings":
                            check(runtime.SetIdvb0059Enabled(payload["settings"]!["idvb0059Enabled"]!.GetValue<bool>()), name + " public setting update succeeds");
                            check(ReferenceEquals(group, Field(runtime, "_geometryGroup")) && cache.SequenceEqual(Cache(runtime)),
                                name + " disabling cache preserves geometry and existing prior rows");
                            break;
                        case "material-ack":
                            acknowledgement = runtime.AcknowledgeRenderMaterial(offered[sourceToken!]);
                            check(acknowledgement.Accepted == step["result"]!["success"]!.GetValue<bool>(), name + " late original receipt ACK agrees with source");
                            break;
                        case "remember_render_material":
                            await runtime.RememberRenderMaterialAsync(offered[sourceToken!].MaterialToken, runtime.ReadSnapshot().Context, default);
                            var remembered = Field(runtime, "_materialProposal");
                            check(remembered is not null && (bool)Field(remembered, "Consumed")! && runtime.ReadMaterialToRemember() is null,
                                name + " actual worker consumes the current accepted proposal once");
                            if (step["result"]!["success"]!.GetValue<bool>())
                                check(Cache(runtime).Length != 0 && ReferenceEquals(Field(runtime, "_steadyOwner"), Property(group!, "Model")),
                                    name + " worker writes the actual current group's floor prior");
                            else check(cache.SequenceEqual(Cache(runtime)), name + " disabled worker leaves existing prior rows untouched");
                            break;
                        default: result = await Ask(action, payload); break;
                    }
                    var after = runtime.ReadState(); runtime.TryReadRender(out var current);
                    // Keep the FIRST actual packet for each source token, exercising late ACKs.
                    if (step["after"]?["render"]?["token"] is { } token && current is not null)
                        offered.TryAdd(token.GetValue<string>(), current);
                    string? expectedReason = step["result"]?["reason"]?.GetValue<string>();
                    if (action == "refresh_map")
                    {
                        check(capture.Calls == calls + 1, name + " refresh uses one fresh actual PNG capture");
                        if (expectedReason == "current-pose-refreshed")
                        {
                            check(result!.Receipt.Success && result.Receipt.Reason == expectedReason && current is not null && previous is not null
                                && current.SharesMaterial(previous) && ReferenceEquals(group, Field(runtime, "_geometryGroup"))
                                && ReferenceEquals(pose, core!.ActivePose), name + " residual renewal preserves pixels, token, group and persistent pose");
                            check(current is not null && previous is not null && !ReferenceEquals(current, previous)
                                && current.Generation == previous.Generation + 1 && current.Stamp.FrameId != previous.Stamp.FrameId
                                && !ReferenceEquals(Property(current, "Authorization"), Property(previous, "Authorization"))
                                && ReferenceEquals(Property(current, "Authorization"), core!.DisplayAuthorization),
                                name + " same material is consumed through fresh observation authority");
                            var measured = after.Diagnostics.GetProperty("residual");
                            var original = step["residuals"]![0]!["residual"]!;
                            check(measured.GetProperty("verified").GetBoolean()
                                && measured.GetProperty("queryEdgeCount").GetInt32() == original["queryEdgeCount"]!.GetValue<int>()
                                && Math.Abs(measured.GetProperty("withinThreePixels").GetDouble() - original["withinThreePixels"]!.GetValue<double>()) < 1e-12,
                                name + " actual fresh residual agrees numerically with independent Python replay");
                            var proposal = Field(runtime, "_materialProposal")!;
                            check(ReferenceEquals(Field(proposal, "Packet"), current) && ReferenceEquals(Field(proposal, "Frame"), Field(runtime, "_frame"))
                                && !(bool)Field(proposal, "Consumed")!, name + " renewal registers a fresh learnable proposal including after rebase");
                            if (name == "disabled-after-remember") check((bool)Field(proposal, "Accepted")!, name + " legitimate readiness carries to the new unconsumed proposal");
                        }
                        else check(current is null && !result!.Receipt.Success && runtime.ReadMaterialToRemember() is null,
                            name + " changed floor cannot renew or acknowledge old material");
                    }
                    if (action == "set_context" && expectedReason == "route-display-reprojected")
                    {
                        await File.WriteAllTextAsync(Path.Combine(output, "reprojected.json"), JsonSerializer.Serialize(new {
                            receipt = result!.Receipt, materialChanged = current is not null && previous is not null && !current.SharesMaterial(previous),
                            stampSame = current?.Stamp == previous?.Stamp, sameGroup = ReferenceEquals(group, Field(runtime, "_geometryGroup")),
                            proposal = runtime.ReadMaterialToRemember()?.MaterialToken, oldAcknowledgement = runtime.AcknowledgeRenderMaterial(previous!), after
                        }, new JsonSerializerOptions { WriteIndented = true }));
                        check(result!.Receipt.Reason == expectedReason && current is not null && previous is not null
                            && !current.SharesMaterial(previous) && current.Stamp == previous.Stamp
                            && ReferenceEquals(group, Field(runtime, "_geometryGroup")) && runtime.ReadMaterialToRemember() is null
                            && !runtime.AcknowledgeRenderMaterial(previous).Accepted,
                            name + " public author intent replaces material without inventing a geometry proposal");
                    }
                    evidence.Add(JsonSerializer.SerializeToNode(new { name, action, result = result?.Receipt, acknowledgement,
                        before, after, captureDelta = capture.Calls-calls, sameMaterial = current is not null && previous is not null && current.SharesMaterial(previous),
                        expectedReason, oracle = path }));
                    await File.WriteAllTextAsync(Path.Combine(output, "current-pose.json"), evidence.ToJsonString(new JsonSerializerOptions { WriteIndented = true }));
                }
            }
            finally { runtime.Shutdown(); }
            Task<RuntimeOperationResult> Ask(string action, JsonNode payload) => runtime.DispatchAsync(new(action,
                JsonSerializer.SerializeToElement(payload), new(sequence.Issue(), runtime.ReadSnapshot().Context)), default).AsTask();
        }
        await LateAcknowledgementDuringCapture();
        await RetryPendingGroupChoice();

        async Task RetryPendingGroupChoice()
        {
            var capture = new RecordedCapture(index); var runtime = new LiveRuntime(capture, resources); var sequence = new SessionSequencer();
            try
            {
                await Ask("set_context", new { season = "s0", mode = "nightmare", profileId = "shared-structure" });
                await Ask("start_round"); capture.Select("batch01-event-000006", false); await Ask("capture_now");
                var initial = runtime.ReadState(); var scope = initial.GroupChoice;
                var choice = await Ask("select_group_member", new { mapId = "nightmare-12", groupId = initial.IdentityGroupId,
                    groupGeneration = scope.Generation, cycleId = scope.CycleId, floor = "1" });
                var pending = runtime.ReadPendingGroupMaterial();
                await File.WriteAllTextAsync(Path.Combine(output, "renewal-group-selection.json"), JsonSerializer.Serialize(new {
                    result = choice.Receipt, state = runtime.ReadState(), pending = pending?.MapId
                }, new JsonSerializerOptions { WriteIndented = true }));
                check(pending is not null && runtime.RejectGroupMaterial(pending, "recorded-material-readiness-failure-control")
                    && runtime.ReadState().GroupChoice.Status == "awaiting-geometry" && runtime.TryReadRender(out _),
                    "actual registered-member selection retains old visible material after explicit consumer failure");
                var result = await Ask("refresh_map"); var after = runtime.ReadState();
                await File.WriteAllTextAsync(Path.Combine(output, "renewal-group-retry.json"), JsonSerializer.Serialize(new {
                    result = result.Receipt, after, pending = runtime.ReadPendingGroupMaterial()?.MapId,
                    scope = "Real current PNG, solver, selection and renewal; only material failure is explicit consumer seam. No pose injection."
                }, new JsonSerializerOptions { WriteIndented = true }));
                check(result.Receipt.Success && after.GroupChoice.DesiredMapId == "nightmare-12"
                    && runtime.ReadPendingGroupMaterial()?.MapId == "nightmare-12" && after.GroupChoice.Status == "awaiting-material",
                    "successful current-pose renewal still retries the user's waiting registered-member selection");
                check(after.Context.MapId == "nightmare-04" && after.Diagnostics.GetProperty("stage").GetString() == "current-pose-renewal"
                    && runtime.AcceptGroupMaterialReady(runtime.ReadPendingGroupMaterial()!) && runtime.ReadState().Context.MapId == "nightmare-12",
                    "actual renewal precedes target retry and target identity commits only after explicit material readiness");
            }
            finally { runtime.Shutdown(); }
            Task<RuntimeOperationResult> Ask(string action, object? payload = null) => runtime.DispatchAsync(new(action,
                JsonSerializer.SerializeToElement(payload ?? new { }), new(sequence.Issue(), runtime.ReadSnapshot().Context)), default).AsTask();
        }

        async Task LateAcknowledgementDuringCapture()
        {
            var capture = new RecordedCapture(index); var runtime = new LiveRuntime(capture, resources); var sequence = new SessionSequencer();
            try
            {
                await Ask("set_context", new { season = "s0", mode = "nightmare", profileId = "shared-structure" });
                await Ask("start_round"); capture.Select("batch01-event-000006", false); await Ask("capture_now");
                runtime.TryReadRender(out var first); var queuedLifetime = runtime.ReadSnapshot().Context;
                var hold = capture.HoldNext(); var refresh = Ask("refresh_map"); await hold.Started.Task.WaitAsync(TimeSpan.FromSeconds(10));
                Task? remember = null;
                try
                {
                    var acknowledgement = runtime.AcknowledgeRenderMaterial(first!);
                    check(acknowledgement.Accepted && acknowledgement.NeedsWorker, "late ACK during held refresh accepts stable material");
                    remember = runtime.RememberRenderMaterialAsync(first!.MaterialToken, queuedLifetime, default).AsTask();
                    check(!remember.IsCompleted, "material worker queues behind the actual capture lane");
                }
                finally { hold.Release.TrySetResult(); }
                var result = await refresh; if (remember is not null) await remember;
                var proposal = Field(runtime, "_materialProposal")!;
                check(result.Receipt.Reason == "current-pose-refreshed" && (bool)Field(proposal, "Accepted")! && (bool)Field(proposal, "Consumed")!
                    && ReferenceEquals(Field(proposal, "Frame"), Field(runtime, "_frame")) && Cache(runtime).Length != 0,
                    "queued old-frame intent consumes the latest verified same-material proposal and fresh cache");
                await Ask("close_map");
                check(!runtime.AcknowledgeRenderMaterial(first!).Accepted && runtime.ReadMaterialToRemember() is null,
                    "close retires authority and all late acknowledgement offers");
            }
            finally { runtime.Shutdown(); }
            Task<RuntimeOperationResult> Ask(string action, object? payload = null) => runtime.DispatchAsync(new(action,
                JsonSerializer.SerializeToElement(payload ?? new { }), new(sequence.Issue(), runtime.ReadSnapshot().Context)), default).AsTask();
        }
    }
    static object? Field(object value, string name) => value.GetType().GetField(name, BindingFlags.Instance | BindingFlags.Public | BindingFlags.NonPublic)!.GetValue(value);
    static object? Property(object value, string name) => value.GetType().GetProperty(name, BindingFlags.Instance | BindingFlags.Public | BindingFlags.NonPublic)!.GetValue(value);
    static object[] Cache(LiveRuntime runtime) => ((IDictionary)Field(runtime, "_steadyFloors")!).Values.Cast<object>().ToArray();
}
