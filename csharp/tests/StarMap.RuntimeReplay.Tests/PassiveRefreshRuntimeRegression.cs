using System.Text.Json;
using System.Text.Json.Nodes;
using StarMap.Core.Session;
using StarMap.Runtime;

internal static class PassiveRefreshRuntimeRegression
{
    internal static async Task Run(string resources, string originalReplay, string originalOracle,
        string output, Action<bool, string> check)
    {
        var index = JsonNode.Parse(await File.ReadAllBytesAsync(Path.Combine(originalReplay, "all-samples.json")))!["samples"]!.AsArray()
            .ToDictionary(row => row!["id"]!.GetValue<string>(), row => row!.AsObject());
        var original = JsonNode.Parse(await File.ReadAllBytesAsync(originalOracle))!;
        var evidence = new JsonArray();
        foreach (var scenario in original["cases"]!.AsArray())
        {
            string name = scenario!["name"]!.GetValue<string>();
            var capture = new RecordedCapture(index); var runtime = new LiveRuntime(capture, resources); var tickets = new SessionSequencer();
            try
            {
                foreach (var step in scenario["steps"]!.AsArray())
                {
                    string action = step!["action"]!.GetValue<string>();
                    if (action == "bind_capture") continue; // Recorded OS binding seam only.
                    var payload = step["payload"] ?? JsonNode.Parse(await File.ReadAllBytesAsync(
                        Path.Combine(Path.GetDirectoryName(originalOracle)!, step["file"]!.GetValue<string>())))!["payload"];
                    if (step["input"]?["id"] is { } input) capture.Select(input.GetValue<string>(), false);
                    var previous = runtime.ReadState(); runtime.TryReadRender(out var previousPacket);
                    int before = capture.Calls;
                    RuntimeOperationResult? result = null;
                    switch (action)
                    {
                        case "material-ack":
                            bool acknowledged = runtime.TryReadRender(out var offered) && runtime.AcknowledgeRenderMaterial(offered!).Accepted;
                            check(acknowledged == step["result"]!["success"]!.GetValue<bool>(),
                                name + " explicit recorded ACK admits only the original current material, including stale rejection"); break;
                        case "remember_render_material":
                            runtime.TryReadRender(out var packet);
                            check(packet is not null, name + " material remember receives the current packet");
                            await runtime.RememberRenderMaterialAsync(packet!.MaterialToken, runtime.ReadSnapshot().Context, default);
                            check((string?)typeof(LiveRuntime).GetField("_rememberedMaterial", System.Reflection.BindingFlags.Instance
                                | System.Reflection.BindingFlags.NonPublic)!.GetValue(runtime) == packet.MaterialToken,
                                name + " real material consumer remembers the current geometric group"); break;
                        default: result = await Ask(action, payload); break;
                    }
                    var state = runtime.ReadState(); bool available = runtime.TryReadRender(out var currentPacket);
                    evidence.Add(JsonSerializer.SerializeToNode(new { name, action, result = result?.Receipt, state,
                        captureDelta = capture.Calls-before, available, material = currentPacket?.Material.Bridge,
                        originalConsumer = step["consumer"], originalState = step["after"], originalFits = step["hostFits"] }));
                    await File.WriteAllTextAsync(Path.Combine(output, "passive-refresh.json"), evidence.ToJsonString(new JsonSerializerOptions { WriteIndented = true }));
                    check(capture.Calls-before == (step["countDelta"]?["capture-provider"]?.GetValue<int>() ?? 0),
                        name + "/" + action + " actual capture count matches the original public path");
                    check(state.MapOpen == step["after"]!["mapOpen"]!.GetValue<bool>()
                        && state.ProbeMisses == step["after"]!["probeMisses"]!.GetValue<int>(),
                        name + "/" + action + " open cycle and passive miss count match original");
                    string? expectedMap = step["after"]!["mapId"]?.GetValue<string>();
                    check(state.Context.MapId == Normalize(expectedMap), name + "/" + action + " identity is retained without new ranking");
                    bool expectedAvailable = step["consumer"]!["current"]!["available"]!.GetValue<bool>();
                    check(available == expectedAvailable && !runtime.ReadSnapshot().LiveAuthorization,
                        name + "/" + action + " actual pixel consumer agrees with original, recorded never grants LiveAuthorization");
                    if (action == "refresh_map")
                    {
                        check(result!.Receipt.Success == step["result"]!["success"]!.GetValue<bool>(), name + " refresh verdict matches original");
                        check(state.Context.FloorId == Normalize(step["after"]!["floor"]?.ToString()), name + " effective floor matches original");
                        if (state.MapOpen)
                            check((state.Context.FrameId == previous.Context.FrameId)
                                == (step["after"]!["frameId"]!.ToString() == step["before"]!["frameId"]!.ToString()),
                                name + " refresh retains or advances its own observation at the same boundary as original");
                        if (previousPacket is not null)
                            check((available && currentPacket!.MaterialToken == previousPacket.MaterialToken)
                                == step["consumer"]!["previous"]!["available"]!.GetValue<bool>(), name + " old material token has the original consumer lifetime");
                        if (!expectedAvailable)
                            check(!state.NativeGeometryVerified && !state.Display.CanonicalVerified, name + " rejected current frame has no current geometry/display authority");
                        if (expectedAvailable)
                        {
                            var source = step["consumer"]!["current"]!["render"]!;
                            check(state.NativeGeometryVerified && currentPacket!.Material.Bridge.CanonicalToClient is { } canonical
                                && EqualMatrix(canonical.ToArray(), source["canonicalMatrix"]!), name + " final canonical material matrix matches original after real solve");
                            var fit = step["hostFits"]!.AsArray().Last(row => row!["success"]?.GetValue<bool>() == true)!;
                            check(state.Reason == fit["reason"]!.GetValue<string>() && state.NativePose is not null
                                && EqualMatrix(state.NativePose.ToMatrix(), fit["matrixSourceToClient"]!), name + " original Host/0059 verdict and native matrix reach actual runtime consumer");
                        }
                    }
                }
            }
            finally { runtime.Shutdown(); }
            Task<RuntimeOperationResult> Ask(string action, object? payload = null) => runtime.DispatchAsync(new(action,
                JsonSerializer.SerializeToElement(payload ?? new { }), new(tickets.Issue(), runtime.ReadSnapshot().Context)), default).AsTask();
        }
        await Lifetimes();
        await RunContext(resources, originalReplay, output, check);

        async Task Lifetimes()
        {
            var capture = new RecordedCapture(index); var runtime = new LiveRuntime(capture, resources); var tickets = new SessionSequencer();
            try
            {
                await Ask("set_context", new { season = "s0", mode = "nightmare", profileId = "shared-structure" });
                await Ask("start_round"); capture.Select("batch01-event-000006", false); await Ask("capture_now");
                runtime.TryReadRender(out var originalPacket);
                runtime.AcknowledgeRenderMaterial(originalPacket!);
                await runtime.RememberRenderMaterialAsync(originalPacket!.MaterialToken, runtime.ReadSnapshot().Context, default);
                var frame = (CapturedClientFrame)typeof(LiveRuntime).GetField("_frame", System.Reflection.BindingFlags.Instance
                    | System.Reflection.BindingFlags.NonPublic)!.GetValue(runtime)!;
                var acquired = await capture.CaptureAsync(default);
                check(!capture.IsCurrent(frame.Stamp) && capture.IsObservationContextCurrent(frame)
                    && runtime.TryReadRender(out var retained) && ReferenceEquals(retained, originalPacket),
                    "new acquisition replaces latest stamp without replacing the authorized published observation context");
                var hold = capture.HoldNext(); int before = capture.Calls;
                var request = new SessionRuntimeRequest("refresh_map", JsonSerializer.SerializeToElement(new { }),
                    new(tickets.Issue(), runtime.ReadSnapshot().Context));
                var refreshing = runtime.DispatchAsync(request, default).AsTask();
                try
                {
                    await hold.Started.Task.WaitAsync(TimeSpan.FromSeconds(10));
                    var coalesced = await Ask("refresh_map");
                    check(coalesced.Receipt is { Success: true, Reason: "refresh-coalesced" } && capture.Calls == before+1
                        && !capture.IsCurrent(acquired.Frame!.Stamp) && runtime.ReadSnapshotFor(request.Context).OwnedByRequest
                        && runtime.TryReadRender(out retained) && ReferenceEquals(retained, originalPacket),
                        "native-style acquisition admission clears the latest stamp, while busy passive ticks preserve the owned published observation");
                }
                finally { hold.Release.TrySetResult(); }
                check((await refreshing).Receipt.Success, "held default refresh finishes through the real shared fit");
                capture.Advance();
                check(!capture.IsObservationContextCurrent(frame) && !runtime.TryReadRender(out _) && !runtime.ReadState().MapOpen,
                    "binding generation change rejects old observation context and pixels");
                runtime.InvalidateCapture("window-binding-changed");
                foreach (string boundary in new[] { "close", "resource" })
                {
                    if (boundary == "resource")
                    { await Ask("end_round"); await Ask("set_context", new { season = "s0", mode = "nightmare", profileId = "shared-structure" }); await Ask("start_round"); }
                    await Ask("capture_now"); var pending = capture.HoldNext();
                    var operation = Ask("refresh_map");
                    try
                    {
                        await pending.Started.Task.WaitAsync(TimeSpan.FromSeconds(10));
                        if (boundary == "close") runtime.InvalidateCapture("map-closed");
                        else runtime.InvalidateResources("resources-changed");
                    }
                    finally { pending.Release.TrySetResult(); }
                    var completed = await operation;
                    check(!completed.Receipt.Success && !runtime.ReadState().MapOpen && !runtime.TryReadRender(out _),
                        "held passive refresh cannot publish after " + boundary + " invalidation");
                }
                await Ask("end_round"); int calls = capture.Calls;
                var inactive = await Ask("refresh_map");
                check(!inactive.Receipt.Success && inactive.Receipt.Reason == "map-refresh-not-active" && capture.Calls == calls,
                    "passive maintenance cannot create a new map cycle after end");
            }
            finally { runtime.Shutdown(); }
            Task<RuntimeOperationResult> Ask(string action, object? payload = null) => runtime.DispatchAsync(new(action,
                JsonSerializer.SerializeToElement(payload ?? new { }), new(tickets.Issue(), runtime.ReadSnapshot().Context)), default).AsTask();
        }
    }

    internal static async Task RunContext(string resources, string originalReplay, string output, Action<bool, string> check)
    {
        var index = JsonNode.Parse(await File.ReadAllBytesAsync(Path.Combine(originalReplay, "all-samples.json")))!["samples"]!.AsArray()
            .ToDictionary(row => row!["id"]!.GetValue<string>(), row => row!.AsObject());
        var capture = new RecordedCapture(index); capture.Select("batch01-event-000006", false);
        var runtime = new LiveRuntime(capture, resources); var tickets = new SessionSequencer();
        try
        {
            await Ask("set_context", new { season = "s0", mode = "nightmare", profileId = "shared-structure" }); await Ask("start_round");
            await Ask("capture_now"); runtime.TryReadRender(out var before);
            check(before is not null && runtime.ReadState().MapOpen, "geometry boundary begins with actual positive observation and material");
            var geometry = before!.Client!.Geometry;
            capture.GeometryOverride = geometry with { Left = geometry.Left+64, Top = geometry.Top+40 };
            var moved = runtime.ReadState(); var snapshot = runtime.ReadSnapshot();
            // Mirrors the original aligned host's getter-first ordering. Its
            // context-loss observation explicitly invalidates the old cycle;
            // a pure read alone must not pretend to be that lifecycle action.
            bool invalidated = runtime.InvalidateObsoleteDisplay();
            capture.GeometryOverride = null;
            var returned = runtime.ReadState(); bool revived = runtime.TryReadRender(out _);
            await File.WriteAllTextAsync(Path.Combine(output, "passive-geometry-context.json"), JsonSerializer.Serialize(new {
                moved, snapshot, invalidated, returned, revived,
                scope = "Recorded raw pixels unchanged. Explicit OS-context seam moves and returns the client. Original aligned consumer observes context loss before another capture; old cycle must stay invalid."
            }, new JsonSerializerOptions { WriteIndented = true }));
            check(!moved.NativeGeometryVerified && invalidated && !returned.MapOpen && !revived,
                "aligned context-loss observation retires the old cycle so returning geometry cannot revive old pixels");
            int calls = capture.Calls; var passive = await Ask("refresh_map");
            check(!passive.Receipt.Success && capture.Calls == calls, "obsolete display context cannot passively reopen its retired cycle");
            var result = await Ask("map", new { openIntent = true, inputSequence = 1 });
            check(result.Receipt.Success && capture.Calls == calls+1 && runtime.TryReadRender(out var after)
                && after!.Stamp != before.Stamp, "a newly observed opening can publish only a fresh verified material after context loss");
        }
        finally { runtime.Shutdown(); }
        Task<RuntimeOperationResult> Ask(string action, object? payload = null) => runtime.DispatchAsync(new(action,
            JsonSerializer.SerializeToElement(payload ?? new { }), new(tickets.Issue(), runtime.ReadSnapshot().Context)), default).AsTask();
    }

    static string? Normalize(string? value) => string.IsNullOrEmpty(value) ? null : value;
    static bool EqualMatrix(double[][] actual, JsonNode expected)
    {
        var wanted = expected.Deserialize<double[][]>()!;
        return actual.Length == 3 && wanted.Length == 3 && actual.All(row => row.Length == 3) && wanted.All(row => row.Length == 3)
            && actual.SelectMany(row => row).Zip(wanted.SelectMany(row => row)).All(pair => Math.Abs(pair.First-pair.Second) <= 1e-5);
    }
}
