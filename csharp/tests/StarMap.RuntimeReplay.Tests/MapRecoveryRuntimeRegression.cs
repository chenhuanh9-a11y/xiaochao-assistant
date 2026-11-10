using System.Text.Json;
using System.Text.Json.Nodes;
using System.Diagnostics;
using StarMap.Core.Session;
using StarMap.Runtime;

internal static class MapRecoveryRuntimeRegression
{
    internal static async Task Run(string resources, string originalReplay, string originalOracle,
        string output, Action<bool, string> check)
    {
        var index = JsonNode.Parse(await File.ReadAllBytesAsync(Path.Combine(originalReplay, "all-samples.json")))!["samples"]!.AsArray()
            .ToDictionary(row => row!["id"]!.GetValue<string>(), row => row!.AsObject());
        var original = JsonNode.Parse(await File.ReadAllBytesAsync(originalOracle))!;
        var controls = JsonNode.Parse(await File.ReadAllBytesAsync(Path.Combine(Path.GetDirectoryName(originalOracle)!, "comparison-controls.json")))!;
        var evidence = new JsonArray();
        foreach (var scenario in original["cases"]!.AsArray().Concat(controls["cases"]!.AsArray()))
        {
            string name = scenario!["id"]!.GetValue<string>();
            var clock = new RecoveryClock(); var port = new RecordedCapture(index);
            var runtime = new LiveRuntime(port, resources, new() { TimeProvider = clock });
            var tickets = new SessionSequencer(); var ids = new Dictionary<string, string>();
            bool bound = false;
            try
            {
                foreach (var step in scenario["steps"]!.AsArray())
                {
                    string action = step!["action"]!.GetValue<string>();
                    var payload = step["payload"]!.DeepClone().AsObject();
                    int calls = port.Calls; var previous = runtime.ReadRenderForTest();
                    RuntimeOperationResult? result = null;
                    if (step["input"]?["id"] is { } input)
                        port.Select(input.GetValue<string>(), invalidateBinding: false);
                    switch (action)
                    {
                        case "bind_capture":
                            if (bound) { port.Advance(); runtime.InvalidateCapture("window-binding-changed"); await Ask("end_round"); }
                            bound = true; break; // Explicit OS binding seam; no native APIs.
                        case "advance-clock": clock.Advance(TimeSpan.FromSeconds(payload["seconds"]!.GetValue<double>())); break;
                        case "public-invalidate-resources": runtime.InvalidateResources("resources-changed"); break;
                        case "set_route_profile": result = await Ask("set_context", payload); break;
                        case "material-ack":
                            check(runtime.TryReadRender(out var offered) && runtime.AcknowledgeRenderMaterial(offered!).Accepted,
                                name + " actual current material is acknowledged at the explicit recorded consumer seam"); break;
                        case "remember_render_material":
                            runtime.TryReadRender(out var packet);
                            check(packet is not null, name + " actual material consumer receives current packet");
                            await runtime.RememberRenderMaterialAsync(packet!.MaterialToken, runtime.ReadSnapshot().Context, default);
                            check((string?)typeof(LiveRuntime).GetField("_rememberedMaterial", System.Reflection.BindingFlags.Instance
                                | System.Reflection.BindingFlags.NonPublic)!.GetValue(runtime) == packet.MaterialToken,
                                name + " actual material consumer remembers current group"); break;
                        default:
                            if (action == "recover_map_open") payload["recoveryId"] = ids[payload["recoveryId"]!.GetValue<string>()];
                            if (action == "map") clock.Resume();
                            result = await Ask(action, payload);
                            if (action == "map") clock.Freeze();
                            break;
                    }
                    var state = runtime.ReadState(); var expected = step["after"]!["recovery"]!;
                    var recovery = state.MapOpenRecovery;
                    if (expected["id"] is { } sourceId && recovery.Id is { } ownId)
                    {
                        string key = sourceId.GetValue<string>();
                        if (ids.TryGetValue(key, out var known)) check(known == ownId, name + "/" + action + " retains the same own public ticket");
                        else ids.Add(key, ownId);
                    }
                    evidence.Add(JsonSerializer.SerializeToNode(new { name, action, result = result?.Receipt,
                        recovery, captureDelta = port.Calls-calls, state, expected }));
                    await File.WriteAllTextAsync(Path.Combine(output, "map-recovery.json"), evidence.ToJsonString(new JsonSerializerOptions { WriteIndented = true }));
                    check(recovery.Pending == expected["pending"]!.GetValue<bool>(), name + "/" + action + " pending matches original public lifecycle");
                    if (expected["id"] is not null)
                        check(recovery.Remaining == expected["remaining"]!.GetValue<int>() && recovery.Due == expected["due"]!.GetValue<bool>()
                            && recovery.Reason == expected["reason"]!.GetValue<string>(), name + "/" + action + " budget, due and scope reason match original");
                    else check(recovery.Id is null, name + "/" + action + " no residual ticket");
                    if (action == "recover_map_open")
                    {
                        check(port.Calls-calls == (step["countDelta"]?["capture-provider"]?.GetValue<int>() ?? 0),
                            name + " recovery capture count matches original single-probe or zero-capture refusal");
                        string reason = step["result"]!["reason"]!.GetValue<string>();
                        if (reason != "current-frame-pose-authorized")
                            check(result!.Receipt.Reason == reason, name + " recovery receipt matches original " + reason);
                        if (reason == "cancelled-or-stale" && previous is not null)
                            check(ReferenceEquals(previous, runtime.ReadRenderForTest()), name + " old id cannot revoke recovered material");
                    }
                    if (action == "map" && step["result"]!["reason"]!.GetValue<string>() == "map-open-preparation-deadline")
                        check(result!.Receipt.Reason == "map-open-preparation-deadline" && port.Calls-calls is >= 1 and <= 21,
                            name + " real closed map retains preparation deadline and bounded initial probes");
                    if (action == "map" && step["after"]!["render"] is null)
                    {
                        string? wantedMap = step["after"]!["mapId"]?.GetValue<string>();
                        check(!state.NativeGeometryVerified && !runtime.TryReadRender(out _)
                            && state.Context.MapId == (string.IsNullOrEmpty(wantedMap) ? null : wantedMap)
                            && state.MapOpen == step["after"]!["mapOpen"]!.GetValue<bool>(),
                            name + " rejected or ambiguous original map has no current pose/material and retains only its original identity");
                    }
                    if (step["displayAuthorized"]?.GetValue<bool>() == true)
                    {
                        check(state is { MapOpen: true, NativeGeometryVerified: true } && state.Context.MapId == "nightmare-04"
                            && runtime.TryReadRender(out _) && !runtime.ReadSnapshot().LiveAuthorization,
                            name + "/" + action + " original automatic map and real canonical material remain recorded-only");
                        var wanted = step["after"]!["render"]?["canonicalMatrix"];
                        if (wanted is not null)
                        {
                            runtime.TryReadRender(out var packet);
                            check(packet?.Material.Bridge.CanonicalToClient is { } canonical && state.Display.CanonicalVerified
                                && canonical.ToArray().SelectMany(row => row).Zip(wanted.Deserialize<double[][]>()!.SelectMany(row => row))
                                    .All(pair => Math.Abs(pair.First-pair.Second) < 1e-5),
                                name + " final canonical material matrix matches the independent original render");
                        }
                        var fit = step["fits"]?.AsArray().LastOrDefault(row => row?["fit"]?["success"]?.GetValue<bool>() == true);
                        if (fit?["fit"]?["matrixSourceToClient"] is { } matrix)
                            check(state.NativePose!.ToMatrix().SelectMany(row => row).Zip(matrix.Deserialize<double[][]>()!.SelectMany(row => row))
                                .All(pair => Math.Abs(pair.First-pair.Second) < 1e-5), name + " independent original Host native matrix matches after solving");
                    }
                }
            }
            finally { runtime.Shutdown(); }
            Task<RuntimeOperationResult> Ask(string action, object? payload = null) => runtime.DispatchAsync(new(action,
                JsonSerializer.SerializeToElement(payload ?? new { }), new(tickets.Issue(), runtime.ReadSnapshot().Context)), default).AsTask();
        }
        await AdversarialControls();
        await RunLate(resources, originalReplay, output, check);

        async Task AdversarialControls()
        {
            var clock = new RecoveryClock(); var port = new RecordedCapture(index);
            var runtime = new LiveRuntime(port, resources, new() { TimeProvider = clock }); var tickets = new SessionSequencer();
            var rows = new JsonArray();
            try
            {
                await Ask("set_context", new { season = "s0", mode = "nightmare", profileId = "shared-structure" });
                await Ask("start_round"); port.Select("batch01-event-000002", false);
                int calls = port.Calls; await Ask("capture_now");
                check(port.Calls == calls+1 && !runtime.ReadState().MapOpenRecovery.Pending,
                    "ordinary capture_now is one observation and never arms preparation recovery");
                await Arm(1);
                string id = runtime.ReadState().MapOpenRecovery.Id!; calls = port.Calls;
                var expired = await Ask("map", new { inputSequence = 1, openIntent = true });
                var invalid = await Ask("map", new { inputSequence = 2, openIntent = "true" });
                check(expired.Receipt.Reason == "map-input-sequence-expired" && invalid.Receipt.Reason == "map-input-intent-invalid"
                    && port.Calls == calls && runtime.ReadState().MapOpenRecovery.Id == id,
                    "rejected duplicate sequence or invalid intent cannot replace the current recovery ticket");
                await Ask("set_context", new { profileId = "suju" });
                await Ask("set_context", new { profileId = "shared-structure" });
                check(runtime.ReadState().MapOpenRecovery is { Pending: false, Reason: "scope-changed" },
                    "profile A to B to A cannot revive the old display generation");
                var stale = await Ask("recover_map_open", new { recoveryId = id });
                check(stale.Receipt.Reason == "map-open-recovery-exhausted" && port.Calls == calls,
                    "profile bounce refuses without capture");
                foreach (string boundary in new[] { "invalidate", "binding", "expiry" })
                {
                    await Arm(boundary == "invalidate" ? 2 : boundary == "binding" ? 3 : 4);
                    clock.Advance(TimeSpan.FromSeconds(.201));
                    id = runtime.ReadState().MapOpenRecovery.Id!;
                    port.Select("batch01-event-000006", false); var hold = port.HoldNext(); calls = port.Calls;
                    var recovery = Ask("recover_map_open", new { recoveryId = id }); await hold.Started.Task;
                    if (boundary == "invalidate") runtime.InvalidateCapture("map-closed");
                    else if (boundary == "binding") port.Advance();
                    else clock.Advance(TimeSpan.FromSeconds(2.001));
                    hold.Release.TrySetResult(); var result = await recovery;
                    rows.Add(JsonSerializer.SerializeToNode(new { boundary, result, state = runtime.ReadState(), calls = port.Calls-calls }));
                    await File.WriteAllTextAsync(Path.Combine(output, "map-recovery-inflight.json"), rows.ToJsonString(new JsonSerializerOptions { WriteIndented = true }));
                    check(!result.Receipt.Success && !runtime.ReadState().NativeGeometryVerified && !runtime.TryReadRender(out _)
                        && !runtime.ReadState().MapOpenRecovery.Pending && port.Calls == calls+1,
                        "in-flight recovery cannot publish after " + boundary + " changed while its capture was held");
                }
            }
            finally { runtime.Shutdown(); }
            async Task Arm(int sequence)
            {
                port.Select("batch01-event-000002", false); clock.Resume();
                await Ask("map", new { openIntent = true, inputSequence = sequence }); clock.Freeze();
                check(runtime.ReadState().MapOpenRecovery.Pending, "adversarial control arms from real closed input " + sequence);
            }
            Task<RuntimeOperationResult> Ask(string action, object? payload = null) => runtime.DispatchAsync(new(action,
                JsonSerializer.SerializeToElement(payload ?? new { }), new(tickets.Issue(), runtime.ReadSnapshot().Context)), default).AsTask();
        }
    }

    internal static async Task RunLate(string resources, string originalReplay, string output, Action<bool, string> check)
    {
        var index = JsonNode.Parse(await File.ReadAllBytesAsync(Path.Combine(originalReplay, "all-samples.json")))!["samples"]!.AsArray()
            .ToDictionary(row => row!["id"]!.GetValue<string>(), row => row!.AsObject());
        var evidence = new JsonArray();
        foreach (string kind in new[] { "late-unknown-floor", "late-usable-floor", "expired-recovery-unknown-floor",
            "late-unknown-floor-manual", "late-usable-floor-manual" })
        {
            var clock = new RecoveryClock(); var port = new RecordedCapture(index);
            var runtime = new LiveRuntime(port, resources, new() { TimeProvider = clock }); var tickets = new SessionSequencer();
            try
            {
                await Ask("set_context", new { season = "s0", mode = "nightmare", profileId = "shared-structure" });
                await Ask("start_round");
                bool manual = kind.EndsWith("manual", StringComparison.Ordinal);
                if (manual) await Ask("set_floor", new { floor = kind.Contains("usable", StringComparison.Ordinal) ? "2" : "1" });
                bool recovery = kind.StartsWith("expired", StringComparison.Ordinal);
                if (recovery)
                {
                    port.Select("batch01-event-000002", false); await Ask("map", new { openIntent = true, inputSequence = 1 });
                    clock.Freeze(); clock.Advance(TimeSpan.FromSeconds(.201));
                }
                string input = kind.Contains("usable", StringComparison.Ordinal) ? "batch01-event-000006" : "batch01-event-000009";
                port.Select(input, false); var hold = port.HoldNext(); int calls = port.Calls;
                var operation = recovery ? Ask("recover_map_open", new { recoveryId = runtime.ReadState().MapOpenRecovery.Id })
                    : Ask("map", new { openIntent = true, inputSequence = 1 });
                await hold.Started.Task;
                clock.Advance(TimeSpan.FromSeconds(recovery || manual && kind.Contains("usable", StringComparison.Ordinal) ? 2.051 : 1.051)); hold.Release.TrySetResult();
                var result = await operation; var state = runtime.ReadState();
                evidence.Add(JsonSerializer.SerializeToNode(new { kind, input, result, state, captureDelta = port.Calls-calls,
                    scope = "Real post-20261105 saved PNG. Acquisition held while the existing injected TimeProvider crosses the deadline. No floor/observation/solver replacement." }));
                await File.WriteAllTextAsync(Path.Combine(output, "map-recovery-late.json"), evidence.ToJsonString(new JsonSerializerOptions { WriteIndented = true }));
                check(port.Calls == calls+1, kind + " exactly one late capture");
                if (kind == "late-unknown-floor-manual")
                    check(state.MapOpen && state.Context.FloorId == "1" && state.ManualFloor == "1" && state.ObservedFloor is null
                        && state.Context.FrameId is not null && !state.MapOpenRecovery.Pending,
                        kind + " preparation uses raw unknown floor, then the retained observation consumes the explicit manual floor");
                else if (kind == "late-unknown-floor")
                    check(state.MapOpen && state.Context.FloorId is null && state.Context.FrameId is not null
                        && !state.MapOpenRecovery.Pending && result.Receipt.Reason == "current-floor-unavailable",
                        kind + " original positive map stays available for manual floor correction without arming recovery");
                else if (recovery)
                    check(!state.MapOpen && !state.MapOpenRecovery.Pending && result.Receipt.Reason == "map-open-recovery-exhausted",
                        kind + " expiry still rejects unknown-floor pixels");
                else check(!state.MapOpen && state.MapOpenRecovery.Pending && result.Receipt.Reason == "map-open-preparation-deadline",
                    kind + " usable geometry after preparation deadline cannot bypass the deadline");
                if (kind != "late-unknown-floor-manual")
                    check(!state.NativeGeometryVerified && !runtime.TryReadRender(out _), kind + " rejected or unresolved late frame has no geometry/material");
                if (kind == "late-usable-floor-manual")
                    check(state.ManualFloor == "2" && state.ObservedFloor is null,
                        kind + " rejected raw observation does not mutate or expire the manual floor policy");
            }
            finally { runtime.Shutdown(); }
            Task<RuntimeOperationResult> Ask(string action, object? payload = null) => runtime.DispatchAsync(new(action,
                JsonSerializer.SerializeToElement(payload ?? new { }), new(tickets.Issue(), runtime.ReadSnapshot().Context)), default).AsTask();
        }
    }

    // Preparation uses real monotonic time and real timers. Only after it settles
    // is logical recovery time frozen/advanced, mirroring the original clock seam.
    private sealed class RecoveryClock : TimeProvider
    {
        readonly object sync = new();
        long anchor = Stopwatch.GetTimestamp(), logical = Stopwatch.GetTimestamp(); bool frozen;
        public override long TimestampFrequency => Stopwatch.Frequency;
        public override long GetTimestamp() { lock (sync) return logical + (frozen ? 0 : Stopwatch.GetTimestamp()-anchor); }
        public override ITimer CreateTimer(TimerCallback callback, object? state, TimeSpan dueTime, TimeSpan period)
            => System.CreateTimer(callback, state, dueTime, period);
        public void Freeze() { lock (sync) { logical = GetTimestamp(); frozen = true; } }
        public void Resume() { lock (sync) { logical = GetTimestamp(); anchor = Stopwatch.GetTimestamp(); frozen = false; } }
        public void Advance(TimeSpan value) { Freeze(); lock (sync) logical += (long)(value.TotalSeconds*TimestampFrequency); }
    }

    private static RuntimeRenderPacket? ReadRenderForTest(this LiveRuntime runtime)
        => runtime.TryReadRender(out var packet) ? packet : null;
}
