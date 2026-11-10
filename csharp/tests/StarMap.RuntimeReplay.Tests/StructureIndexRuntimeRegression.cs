using System.Text.Json;
using System.Text.Json.Nodes;
using StarMap.Core.Session;
using StarMap.Runtime;

internal static class StructureIndexRuntimeRegression
{
    internal static async Task Run(string resources, string replay, string indexOracle, string output, Action<bool, string> check)
    {
        var rows = JsonNode.Parse(await File.ReadAllBytesAsync(Path.Combine(replay, "all-samples.json")))!["samples"]!.AsArray();
        var index = rows.ToDictionary(row => row!["id"]!.GetValue<string>(), row => row!.AsObject());
        var original = JsonNode.Parse(await File.ReadAllBytesAsync(indexOracle))!["runs"]!.AsArray()
            .Single(row => row!["run"]!.GetValue<string>() == "run-03")!["states"]!.AsArray();
        var capture = new RecordedCapture(index); var runtime = new LiveRuntime(capture, resources); var sequence = new SessionSequencer();
        var observations = new JsonArray(); string? previousFrame = null;
        try
        {
            check((await Ask("set_context", new { season = "s1", mode = "difficult", profileId = "shared-structure" })).Receipt.Success,
                "actual S1 difficult resource context is bound");
            check((await Ask("start_round")).Receipt.Success, "one round owns the complete chronological S1 difficult corpus");
            foreach (var source in original)
            {
                string id = source!["input"]!["id"]!.GetValue<string>(); capture.Select(id, false);
                int calls = capture.Calls; var result = await Ask("capture_now"); var state = runtime.ReadState();
                bool available = runtime.TryReadRender(out var packet);
                observations.Add(JsonSerializer.SerializeToNode(new { id, result = result.Receipt, state, available,
                    materialMap = packet?.MapId, stamp = packet?.Stamp, bridge = packet?.Material.Bridge,
                    originalPool = source["monotonicAfter"], originalIndexReason = source["monotonicReason"] }));
                await File.WriteAllTextAsync(Path.Combine(output, "index-round.json"), observations.ToJsonString(new JsonSerializerOptions { WriteIndented = true }));
                check(capture.Calls == calls+1, id + " uses exactly one actual recorded capture");
                check(state.RemainingMapIds.SequenceEqual(source["monotonicAfter"]!.AsArray().Select(value => value!.GetValue<string>())),
                    id + " runtime pool matches original comparator plus the requested retain-on-conflict rule");
                check((state.RecognitionPath == "structure-index") == source["indexEnteredAfterDerived"]!.GetValue<bool>(),
                    id + " door entry remains active across later floors and missing glyphs");
                check(!runtime.ReadSnapshot().LiveAuthorization, id + " recorded material never obtains native live authority");
                if (available)
                {
                    check(packet!.MapId == (state.Context.MapId ?? state.ProvisionalMapId) && state.RemainingMapIds.Contains(packet.MapId)
                        && packet.Stamp.FrameId == state.Context.FrameId && state.Context.FrameId != previousFrame
                        && state.Display.CanonicalVerified && state.NativeGeometryVerified,
                        id + " actual pixel consumer uses a surviving map and fresh current-frame Core authority");
                    check(state.IdentityLocked == (state.Context.MapId is not null) && (state.IdentityLocked
                        ? state.RemainingMapIds.Count == 1 && state.ProvisionalMapId is null : state.IdentityKind == "candidate"),
                        id + " temporary pixels do not become formal identity before unique elimination");
                }
                else check(!state.NativeGeometryVerified && !state.Display.CanonicalVerified,
                    id + " unavailable current geometry has no old display authority");
                previousFrame = state.Context.FrameId;
            }
            check(observations.Count == 62 && runtime.ReadState() is { IdentityLocked: true, Context.MapId: "106008" },
                "unseeded all62 sequence reaches the single remaining structural identity106008");
            await Ask("reselect_map");
            check(runtime.ReadState().RemainingMapIds.SequenceEqual(["106008"]), "same-round reselection cannot restore the full catalogue");
            await Ask("end_round"); await Ask("start_round");
            check(runtime.ReadState() is { IdentityLocked: false, RecognitionPath: "ordinary" } next
                && next.RemainingMapIds.Count == 14 && next.ProvisionalMapId is null, "only a new round resets the pool and door-entry route");
            await BadIndexFallback();
        }
        finally { runtime.Shutdown(); }
        Task<RuntimeOperationResult> Ask(string action, object? payload = null) => runtime.DispatchAsync(new(action,
            JsonSerializer.SerializeToElement(payload ?? new { }), new(sequence.Issue(), runtime.ReadSnapshot().Context)), default).AsTask();

        async Task BadIndexFallback()
        {
            foreach (var flaw in new[] {
                (Name: "wrong-version", File: "s1_entrance_coordinate_index.json", Content: "{\"schema\":\"wrong-version\"}"),
                (Name: "missing-index-fields", File: "s1_entrance_coordinate_index.json", Content: "{}"),
                (Name: "wrong-index-field-type", File: "s1_entrance_coordinate_index.json", Content: "{\"schema\":[]}"),
                (Name: "missing-template-fields", File: "template_manifest.json", Content: "{}") })
            {
                string assets = Path.Combine(output, "bad-assets-" + flaw.Name); Directory.CreateDirectory(assets);
                foreach (string file in new[] { "native_glyph_template.png", "template_manifest.json", "s1_entrance_coordinate_index.json" })
                    File.Copy(Path.Combine(AppContext.BaseDirectory, "s1-private-assets", file), Path.Combine(assets, file));
                await File.WriteAllTextAsync(Path.Combine(assets, flaw.File), flaw.Content);
                var otherCapture = new RecordedCapture(index); otherCapture.Select("batch02-event-000255", false);
                var other = new LiveRuntime(otherCapture, resources, new() { StructureIndexAssetDirectory = assets });
                var tickets = new SessionSequencer();
                try
                {
                    await Other("set_context", new { season = "s1", mode = "difficult", profileId = "shared-structure" });
                    await Other("start_round"); var result = await Other("capture_now"); var state = other.ReadState();
                    check(result.Receipt.Success && state.ProvisionalMapId == "106008" && !state.IdentityLocked && other.TryReadRender(out _)
                        && state.RemainingMapIds.Count == 14 && state.RecognitionPath == "ordinary"
                        && state.Diagnostics.GetProperty("index").GetProperty("reason").GetString() == "structure-index-resources-unavailable",
                        flaw.Name + " reports capability failure but preserves ordinary fitting and the unfiltered round pool");
                    await File.WriteAllTextAsync(Path.Combine(output, "index-fallback-" + flaw.Name + ".json"), JsonSerializer.Serialize(state));
                }
                finally { other.Shutdown(); }
                Task<RuntimeOperationResult> Other(string action, object? payload = null) => other.DispatchAsync(new(action,
                    JsonSerializer.SerializeToElement(payload ?? new { }), new(tickets.Issue(), other.ReadSnapshot().Context)), default).AsTask();
            }
        }
    }
}
