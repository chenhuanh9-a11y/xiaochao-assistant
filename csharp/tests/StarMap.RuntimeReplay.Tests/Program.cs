using System.Text.Json;
using System.Text.Json.Nodes;
using StarMap.Core.Session;
using StarMap.Runtime;

if (args.Length == 5 && args[0] is "--identity-prior-only" or "--unselected-floor-only")
{
    string priorOnlyOutput = Path.GetFullPath(args[4]);
    if (Directory.Exists(priorOnlyOutput)) throw new IOException("Preserve previous evidence; use a new output.");
    Directory.CreateDirectory(priorOnlyOutput);
    var priorOnlyChecks = new List<object>(); var priorOnlyFailures = new List<string>();
    try
    {
        Action<bool, string> priorOnlyCheck = (passed, name) => {
            priorOnlyChecks.Add(new { name, passed });
            if (!passed) { priorOnlyFailures.Add(name); throw new InvalidOperationException(name); }
        };
        if (args[0] == "--identity-prior-only")
            await IdentityEntryRuntimeRegression.RunPrior(args[1], args[2], args[3], priorOnlyOutput, priorOnlyCheck);
        else await IdentityEntryRuntimeRegression.RunUnselectedFloor(args[1], args[2], args[3], priorOnlyOutput, priorOnlyCheck);
    }
    catch (Exception error)
    {
        if (priorOnlyFailures.Count == 0) priorOnlyFailures.Add(error.Message);
        File.WriteAllText(Path.Combine(priorOnlyOutput, "error.txt"), error.ToString());
    }
    File.WriteAllText(Path.Combine(priorOnlyOutput, "summary.json"), JsonSerializer.Serialize(new {
        passed = priorOnlyFailures.Count == 0, checkCount = priorOnlyChecks.Count,
        failures = priorOnlyFailures, checks = priorOnlyChecks }, new JsonSerializerOptions { WriteIndented = true }));
    Console.WriteLine($"Targeted Runtime: {priorOnlyChecks.Count-priorOnlyFailures.Count}/{priorOnlyChecks.Count}; {priorOnlyOutput}");
    return priorOnlyFailures.Count == 0 ? 0 : 1;
}

if (args.Length is < 3 or > 8) throw new ArgumentException("RESOURCE_ROOT ORIGINAL_ALL405_REPLAY NEW_OUTPUT [ORIGINAL_GROUP_LIFETIMES] [COORDINATE_POLICY_ORACLE] [ORIGINAL_ESTABLISHED_PROBES] [ORIGINAL_RUNTIME_IDENTITY_ENTRY] [ORIGINAL_RUNTIME_IDENTITY_PRIOR]");
string root = Path.GetFullPath(args[0]), oracle = Path.GetFullPath(args[1]), output = Path.GetFullPath(args[2]);
if (Directory.Exists(output)) throw new IOException("Preserve previous evidence; choose a new output.");
Directory.CreateDirectory(output);
var index = JsonNode.Parse(File.ReadAllBytes(Path.Combine(oracle, "all-samples.json")))!["samples"]!.AsArray()
    .ToDictionary(row => row!["id"]!.GetValue<string>(), row => row!.AsObject());
var checks = new List<object>(); var failures = new List<string>();
var port = new RecordedCapture(index);
var runtime = new LiveRuntime(port, root);
var sequence = new SessionSequencer();

if (args.Length >= 4) await Run("actual-geometry-group-lifetimes", () => GeometryGroupRuntimeRegression.Run(root, oracle, args[3], output, Check));
if (args.Length >= 5) await Run("coordinate-policy", () => { CoordinatePolicyRegression.Run(args[4], output, Check); return Task.CompletedTask; });
if (args.Length >= 6) await Run("actual-default-established-rejections", () => GeometryGroupRuntimeRegression.RunEstablished(root, oracle, args[5], output, Check));
if (args.Length >= 7) await Run("actual-runtime-identity-entry", () => IdentityEntryRuntimeRegression.Run(root, oracle, args[6], output, Check));
if (args.Length == 8) await Run("actual-runtime-identity-prior", () => IdentityEntryRuntimeRegression.RunPrior(root, oracle, args[7], output, Check));

await Run("S1-single-supported-map-still-needs-human-identity", async () =>
{
    Check((await Ask("set_context", new { season = "s1", mode = "nightmare", profileId = "wang-qingxin" })).Receipt.Success, "S1 real resource binding");
    Check((await Ask("start_round")).Receipt.Success, "S1 round starts");
    foreach (int number in new[] { 315, 316, 317, 318, 319 })
    {
        string id = "batch02-event-" + number.ToString("D6");
        port.Select(id); await Ask("capture_now");
        var current = runtime.ReadState();
        Check(!current.IdentityLocked && !runtime.TryReadRender(out _), id + " sequence observation cannot guess identity");
        if (number == 316)
            Check(!current.MapOpen && !current.NativeGeometryVerified, "closed frame in sequence revokes current geometry without inventing floor");
        else
        {
            // The independent original session moves earlier positive maps to
            // the front; a fresh standalone scan has a different row order.
            var expectedOrder = JsonNode.Parse(File.ReadAllBytes(Path.Combine(Path.GetDirectoryName(oracle)!,
                "s1-sequence-order-python-01.json")))!["cases"]![id]!;
            Check(current.Candidates.Select(c => c.MapId).SequenceEqual(Ids(expectedOrder["supportedMapIds"]))
                && current.Diagnostics.GetProperty("report").GetProperty("checkedMapIds").EnumerateArray()
                    .Select(value => value.GetString()!).SequenceEqual(Ids(expectedOrder["checkedMapIds"])),
                id + " current support and complete scan order match the original history scheduler");
        }
        Save(id + "-sequence", current);
    }
    var state = runtime.ReadState();
    var expected = Original("batch02-event-000319");
    Check(state.MapOpen && state.Context.FloorId == "-1", "fresh S1 floor observed in real runtime");
    Check(state.Candidates.Select(c => c.MapId).SequenceEqual(Ids(expected["supportedMapIds"])), "full catalog scan matches original positive candidates");
    Check(state.Candidates.Count == 1 && !state.IdentityLocked && state.Candidates.All(c => !c.IdentityVerified), "one supported candidate does not silently lock identity");
    Check(!runtime.TryReadRender(out _) && !runtime.ReadSnapshot().LiveAuthorization, "unconfirmed candidate has no display authority");
    Save("s1-single-candidate", state);
});

await Run("human-C1-choice-commits-canonical-material-on-real-path", async () =>
{
    // This is the user's explicit C1 action, not a solver seed or automatic answer.
    var result = await Ask("confirm_route", new { mapId = "107012" });
    var state = runtime.ReadState();
    Check(result.Receipt.Success && state.IdentityLocked && state.Context.MapId == "107012" && state.IdentitySource == "manual", "human identity commits before fit");
    Check(state.NativeGeometryVerified && state.NativePose is not null, "shared fit consumed by runtime");
    MatrixEqual(state.NativePose!.ToMatrix(), Original("batch02-event-000319")["poses"]!["107012"]!["matrixSourceToClient"]!, "native matrix agrees with independent original");
    Check(state.Display.CanonicalVerified && runtime.TryReadRender(out var packet), "canonical adjudication reaches owned pixel material");
    runtime.TryReadRender(out packet);
    Check(packet is not null && !packet.IsLive && !runtime.ReadSnapshot().LiveAuthorization, "recorded real-client replay remains non-live");
    Check(packet!.Material.CopyPixels().Length == packet.Material.Height * packet.Material.Stride, "actual selected bridge produces complete owned pixels");
    var snapshotText = JsonSerializer.Serialize(runtime.ReadSnapshot());
    Check(snapshotText.Length < 200000 && !snapshotText.Contains("Bgra", StringComparison.Ordinal), "snapshot omits pixel buffers");
    Save("s1-confirmed-fit", new { state, bridge = packet.Material.Bridge });
});

await Run("hidden-render-payload-is-retained-and-hidden-refresh-still-solves", async () =>
{
    var before = runtime.ReadState(); runtime.TryReadRender(out var original); int calls = port.Calls;
    Check((await Ask("toggle_sheet_visibility")).Receipt.Success && !runtime.ReadState().AlignedVisible,
        "explicit runtime visibility action changes only the presentation preference");
    Check(runtime.TryReadRender(out var hidden) && ReferenceEquals(hidden, original) && port.Calls == calls
        && runtime.ReadState().Context == before.Context && ReferenceEquals(runtime.ReadState().NativePose, before.NativePose),
        "hiding keeps the exact current canonical packet and native pose without capture or refit");
    port.Select("batch02-event-000319"); var result = await Ask("capture_now");
    Check(result.Receipt.Success && !runtime.ReadState().AlignedVisible && port.Calls == calls + 1
        && runtime.ReadState().NativeGeometryVerified && runtime.TryReadRender(out var refreshed),
        "hidden refresh executes the real current-frame fit and keeps the new material hidden");
    runtime.TryReadRender(out refreshed);
    MatrixEqual(runtime.ReadState().NativePose!.ToMatrix(), Original("batch02-event-000319")["poses"]!["107012"]!["matrixSourceToClient"]!,
        "hidden refresh consumes the independently verified native matrix");
    runtime.SetAlignedVisibility(true);
    Check(runtime.ReadState().AlignedVisible && runtime.TryReadRender(out var shown) && ReferenceEquals(shown, refreshed)
        && port.Calls == calls + 1, "showing reuses the hidden result rather than capturing or solving again");
    Save("visibility-retained-material", runtime.ReadState());
});

await Run("immediate-visibility-control-does-not-supersede-inflight-capture", async () =>
{
    var hold = port.HoldNext();
    var captureTask = Ask("capture_now");
    try
    {
        await hold.Started.Task.WaitAsync(TimeSpan.FromSeconds(5));
        var before = runtime.ReadState();
        Check(before.WorkActive && !captureTask.IsCompleted, "actual capture is held after the production request starts");
        runtime.SetAlignedVisibility(false);
        Check(!runtime.ReadState().AlignedVisible && runtime.ReadState().WorkActive && !captureTask.IsCompleted
            && runtime.ReadState().Context == before.Context, "F11 control leaves the admitted request context and work intact");
        hold.Release.TrySetResult(); var result = await captureTask;
        Check(result.Receipt.Success && runtime.ReadState().NativeGeometryVerified && runtime.TryReadRender(out var packet)
            && !runtime.ReadState().AlignedVisible, "the original capture request still commits real canonical material while hidden");
        runtime.TryReadRender(out packet); runtime.SetAlignedVisibility(true);
        Check(runtime.TryReadRender(out var restored) && ReferenceEquals(packet, restored),
            "restore after inflight completion does not replace its material");
    }
    finally { hold.Release.TrySetResult(); await captureTask; }
});

await Run("author-preference-replaces-material-without-capture-or-refit", async () =>
{
    var before = runtime.ReadState(); runtime.TryReadRender(out var old);
    int calls = port.Calls;
    Check((await Ask("set_context", new { season = "s1", mode = "nightmare", profileId = "shared-structure" })).Receipt.Success, "preference accepted");
    var state = runtime.ReadState();
    Check(port.Calls == calls && state.Context.FrameId == before.Context.FrameId && state.Context.MapId == before.Context.MapId, "author change keeps actual capture and identity");
    MatrixEqual(state.NativePose!.ToMatrix(), JsonSerializer.SerializeToNode(before.NativePose!.ToMatrix())!, "author change keeps native pose");
    Check(runtime.TryReadRender(out var next) && next!.Material.Bridge.CanonicalToClient == old!.Material.Bridge.CanonicalToClient, "author change keeps canonical pose while replacing selected pixels");
    Save("author-change", state);
});

await Run("unavailable-author-can-recover-without-recapturing-or-refitting", async () =>
{
    var before = runtime.ReadState(); int calls = port.Calls;
    await Ask("set_context", new { season = "s1", mode = "nightmare", profileId = "custom:unavailable-test" });
    Check(!runtime.TryReadRender(out _) && runtime.ReadState().NativeGeometryVerified, "unavailable author removes pixels without removing verified geometry");
    await Ask("set_context", new { season = "s1", mode = "nightmare", profileId = "shared-structure" });
    Check(runtime.TryReadRender(out _) && runtime.ReadState().Display.Prepared, "switching back restores material from the same canonical grant");
    Check(port.Calls == calls && runtime.ReadState().Context.FrameId == before.Context.FrameId, "recovery has no new capture");
    MatrixEqual(runtime.ReadState().NativePose!.ToMatrix(), JsonSerializer.SerializeToNode(before.NativePose!.ToMatrix())!, "recovery has no new fit");
    Save("author-recovery", runtime.ReadState());
});

await Run("closed-map-revokes-material-keeps-human-choice", async () =>
{
    await Ask("close_map"); var state = runtime.ReadState();
    Check(state.IdentityLocked && state.Context.MapId == "107012" && !state.MapOpen, "closing map preserves chosen identity");
    Check(!runtime.TryReadRender(out _) && !state.Display.Prepared && !state.NativeGeometryVerified, "close withdraws render and geometry");
});

await Run("locked-C1-sparse-new-floor-estimates-independent-pose", async () =>
{
    port.Select("batch02-event-000323");
    var result = await Ask("capture_now"); var state = runtime.ReadState();
    Check(result.Receipt.Success && state.Context.FloorId == "-2" && state.Context.MapId == "107012", "sparse target-floor fit preserves C1 lock");
    Check(!state.Diagnostics.GetProperty("priorUsed").GetBoolean(), "new floor has no borrowed prior or scale");
    MatrixEqual(state.NativePose!.ToMatrix(), Original("batch02-event-000323")["poses"]!["107012"]!["matrixSourceToClient"]!, "target-floor matrix agrees with independent original");
    Check(runtime.TryReadRender(out _) && state.Display.CanonicalVerified, "independent target-floor canonical pose consumed");
    Save("s1-sparse-target-floor", state);
});

await Run("closed-frame-and-obsolete-binding-cannot-repaint", async () =>
{
    port.Select("batch02-event-000316");
    var failed = await Ask("capture_now"); var state = runtime.ReadState();
    Check(!failed.Receipt.Success && state.IdentityLocked && state.Context.MapId == "107012", "failed observation cannot retract human identity");
    Check(!state.NativeGeometryVerified && !runtime.TryReadRender(out _) && !state.Display.Prepared, "closed-map image cannot reuse previous render");
    port.Advance();
    Check(!runtime.TryReadRender(out _) && !runtime.ReadSnapshot().LiveAuthorization, "obsolete capture cannot authorize display");
});

await Run("S1-difficult-unknowns-and-similar-candidates-stay-unconfirmed", async () =>
{
    await Ask("end_round");
    Check((await Ask("set_context", new { season = "s1", mode = "difficult", profileId = "wang-qingxin" })).Receipt.Success, "difficult binds own catalog");
    await Ask("start_round");
    foreach (int number in new[] { 254, 255, 256 })
    {
        string id = "batch02-event-" + number.ToString("D6"); port.Select(id);
        await Ask("capture_now"); var state = runtime.ReadState(); var expected = Original(id);
        Check(state.Candidates.Select(c => c.MapId).SequenceEqual(Ids(expected["supportedMapIds"])), id + " actual positive candidates match original");
        Check(!state.IdentityLocked && !runtime.TryReadRender(out _), id + " no guessed identity or render");
        Check(state.Diagnostics.GetProperty("report").GetProperty("excludedMapIds").GetArrayLength() == 0, id + " failed fits remain UNKNOWN");
        Save(id, state);
    }
});

await Run("cold-fit-with-missing-material-keeps-geometry-and-recovers", async () =>
{
    await Ask("end_round");
    await Ask("set_context", new { season = "s1", mode = "nightmare", profileId = "custom:unavailable-test" });
    await Ask("start_round"); port.Select("batch02-event-000319");
    await Ask("capture_now"); await Ask("confirm_route", new { mapId = "107012" });
    var before = runtime.ReadState(); int calls = port.Calls;
    Check(before.NativeGeometryVerified && before.Display.CanonicalVerified && !runtime.TryReadRender(out _), "missing preferred material cannot erase cold canonical fit");
    await Ask("set_context", new { season = "s1", mode = "nightmare", profileId = "shared-structure" });
    Check(runtime.TryReadRender(out _) && port.Calls == calls, "cold missing-material recovery uses same observation");
    await Ask("set_context", new { season = "s1", mode = "nightmare", profileId = "custom:unavailable-test" });
    await Ask("close_map");
    await Ask("set_context", new { season = "s1", mode = "nightmare", profileId = "shared-structure" });
    Check(!runtime.TryReadRender(out _) && !runtime.ReadState().Display.CanonicalVerified, "closing during missing material prevents later preference repaint");
    runtime.InvalidateResources("recorded-resource-rebind");
    await Ask("set_context", new { season = "s1", mode = "nightmare", profileId = "shared-structure" });
    Check(!runtime.TryReadRender(out _) && !runtime.ReadState().IdentityLocked, "resource generation cannot recover old canonical grant");
});

await Run("resource-invalidation-during-missing-material-revokes-canonical-directly", async () =>
{
    await Ask("start_round"); port.Select("batch02-event-000319");
    await Ask("capture_now"); await Ask("confirm_route", new { mapId = "107012" });
    Check(runtime.TryReadRender(out _), "independent resource case begins with an active canonical material");
    await Ask("set_context", new { season = "s1", mode = "nightmare", profileId = "custom:unavailable-test" });
    Check(runtime.ReadState().Display.CanonicalVerified && !runtime.TryReadRender(out _), "resource case still owns canonical grant while material is missing");
    runtime.InvalidateResources("missing-material-resource-invalidated");
    await Ask("set_context", new { season = "s1", mode = "nightmare", profileId = "shared-structure" });
    Check(!runtime.TryReadRender(out _) && !runtime.ReadState().Display.CanonicalVerified && !runtime.ReadState().IdentityLocked,
        "resource invalidation directly prevents old canonical material recovery without an earlier close");
});

await Run("S0-front-similarity-and-human-A1-consume-real-Host-fit", async () =>
{
    await Ask("end_round");
    await Ask("set_context", new { season = "s0", mode = "nightmare", profileId = "shared-structure" });
    await Ask("start_round"); port.Select("batch01-event-000006"); await Ask("capture_now");
    var grouped = runtime.ReadState();
    Save("s0-front-initial-observation", grouped);
    var originalDecision = JsonNode.Parse(File.ReadAllBytes(Path.Combine(Path.GetDirectoryName(oracle)!,
        "s0-identity-consumer-python-01", "batch01-event-000006", "decision.json")))!;
    Check(grouped.IdentityLocked && grouped.IdentityKind == "group"
        && grouped.IdentityGroupId == originalDecision["state"]!["groupId"]!.GetValue<string>()
        && grouped.IdentityMemberIds.SequenceEqual(Ids(originalDecision["memberIds"]))
        && grouped.Context.MapId == originalDecision["selectedMapId"]!.GetValue<string>(),
        "registered group and display reference match the independent original consumer without claiming a unique map");
    Check(grouped.Candidates[0].IdentityKind == "group" && !grouped.Candidates[0].IdentityVerified
        && grouped.Candidates[0].MemberIds.Contains("nightmare-12"), "automatic candidates fold the registered group and retain A1 as a member");
    Check(grouped.NativeGeometryVerified && grouped.Display.CanonicalVerified && runtime.TryReadRender(out _),
        "registered group initial reference still requires actual Host fit and canonical material");
    Check(JsonSerializer.Serialize(runtime.ReadSnapshot()).Length < 200000,
        "native condition samples stay outside published runtime snapshots");
    await Ask("close_map");
    Check(!runtime.TryReadRender(out _), "group close withdraws current material");
    port.Select("batch01-event-000006"); await Ask("capture_now");
    Check(runtime.ReadState().IdentityKind == "group" && runtime.ReadState().IdentitySource == "group-default",
        "reopening revalidates the group instead of making its reference a durable unique identity");
    var selected = await Ask("confirm_route", new { mapId = "nightmare-12" });
    var state = runtime.ReadState();
    var expected = JsonNode.Parse(File.ReadAllBytes(Path.Combine(oracle, "batch01-event-000006", "downstream-fit.json")))!["candidates"]!
        .AsArray().Single(row => row!["mapId"]!.GetValue<string>() == "nightmare-12")!["fit"]!;
    Check(selected.Receipt.Success && state.IdentityLocked && state.IdentityKind == "map"
        && state.IdentityGroupId is null && state.IdentityMemberIds.Count == 0 && state.IdentitySource == "manual"
        && state.NativeGeometryVerified, "human A1 becomes the unique chosen identity and reaches production fit");
    MatrixEqual(state.NativePose!.ToMatrix(), expected["matrixSourceToClient"]!, "S0 native pose agrees with independent original Host");
    Check(state.Display.CanonicalVerified && runtime.TryReadRender(out _), "S0 actual canonical material reaches consumer");
    Save("s0-front-human-A1", state);
});

await Run("registered-group-choice-retains-predecessor-until-material-ready", async () =>
{
    await Ask("end_round");
    await Ask("set_context", new { season = "s0", mode = "nightmare", profileId = "shared-structure" });
    await Ask("start_round"); port.Select("batch01-event-000006"); await Ask("capture_now");
    var before = runtime.ReadState(); runtime.TryReadRender(out var predecessor); int calls = port.Calls;
    Check(runtime.ReadSnapshot().AvailableCommands.Contains("select_group_member") && before.IdentityMemberIds.Count == 3,
        "registered members have their own scoped action instead of unrestricted manual confirmation");
    var invalid = await Ask("select_group_member", GroupPayload("nightmare-31"));
    var stale = await Ask("select_group_member", new { mapId = "nightmare-12", groupId = before.IdentityGroupId,
        groupGeneration = before.GroupChoice.Generation + 1, cycleId = before.GroupChoice.CycleId, floor = before.Context.FloorId });
    Check(!invalid.Receipt.Success && !stale.Receipt.Success && runtime.ReadPendingGroupMaterial() is null
        && runtime.TryReadRender(out var unchanged) && ReferenceEquals(unchanged, predecessor),
        "outside-group and stale-scope choices preserve the current member and exact material");
    var selected = await Ask("select_group_member", GroupPayload("nightmare-12"));
    var pending = runtime.ReadPendingGroupMaterial();
    Check(selected.Receipt.Success && pending is { MapId: "nightmare-12" } && runtime.ReadState().Context.MapId == before.Context.MapId
        && ReferenceEquals(runtime.ReadState().NativePose, before.NativePose)
        && runtime.TryReadRender(out unchanged) && ReferenceEquals(unchanged, predecessor) && port.Calls == calls,
        "actual target Host fit stages material without changing predecessor pose pixels or capture");
    Check(runtime.RejectGroupMaterial(pending!, "recorded-consumer-rejected") && runtime.ReadState().Context.MapId == before.Context.MapId
        && runtime.ReadState().GroupChoice is { DesiredMapId: "nightmare-12", Status: "awaiting-geometry" }
        && runtime.TryReadRender(out unchanged) && ReferenceEquals(unchanged, predecessor),
        "consumer refusal retains the exact predecessor and desired member for retry");
    await Ask("select_group_member", GroupPayload("nightmare-12"));
    var nextPending = runtime.ReadPendingGroupMaterial();
    Check(nextPending is not null && !runtime.AcceptGroupMaterialReady(pending!) && !runtime.RejectGroupMaterial(pending!, "late-refusal")
        && ReferenceEquals(runtime.ReadPendingGroupMaterial(), nextPending), "late callbacks cannot accept or clear a newer candidate");
    // Explicit Recorded consumer seam; real WPF material readiness is checked separately.
    Check(runtime.AcceptGroupMaterialReady(nextPending!) && runtime.ReadState() is { IdentitySource: "group-manual", NativeGeometryVerified: true }
        && runtime.ReadState().Context.MapId == "nightmare-12" && runtime.ReadState().IdentityMemberIds.SequenceEqual(before.IdentityMemberIds)
        && runtime.ReadState().GroupChoice is { HeldMapId: "nightmare-12", Status: "confirmed" }
        && runtime.TryReadRender(out var accepted) && ReferenceEquals(accepted, nextPending) && !accepted!.IsLive,
        "Recorded material-ready ACK commits target identity pose grant and pixels together without live authority");
    var expected = JsonNode.Parse(File.ReadAllBytes(Path.Combine(oracle, "batch01-event-000006", "downstream-fit.json")))!["candidates"]!
        .AsArray().Single(row => row!["mapId"]!.GetValue<string>() == "nightmare-12")!["fit"]!;
    MatrixEqual(runtime.ReadState().NativePose!.ToMatrix(), expected["matrixSourceToClient"]!, "group choice uses independent original Host geometry");
    var duplicate = await Ask("select_group_member", GroupPayload("nightmare-12"));
    Check(duplicate.Receipt is { Success: true, Reason: "already-current" } && runtime.ReadPendingGroupMaterial() is null,
        "choosing the already held member is idempotent");
    runtime.TryReadRender(out var heldPredecessor);
    await Ask("select_group_member", GroupPayload("nightmare-19"));
    var cancelledTarget = runtime.ReadPendingGroupMaterial();
    var returned = await Ask("select_group_member", GroupPayload("nightmare-12"));
    Check(cancelledTarget is not null && returned.Receipt.Success && !runtime.AcceptGroupMaterialReady(cancelledTarget)
        && runtime.ReadState().GroupChoice is { DesiredMapId: "nightmare-12", HeldMapId: "nightmare-12", PendingMapId: null, Status: "confirmed" }
        && runtime.TryReadRender(out var held) && ReferenceEquals(held, heldPredecessor),
        "returning to the held member cancels a pending sibling without replacing current material");
    await Ask("close_map"); port.Select("batch02-event-000018"); await Ask("capture_now");
    Check(runtime.ReadState().Context.MapId == "nightmare-12" && runtime.ReadState().Context.FloorId == "2"
        && runtime.ReadState().GroupChoice.HeldMapId == "nightmare-12" && runtime.ReadState().IdentitySource == "group-manual",
        "a real other-floor opening keeps the held member even when that floor cannot produce a front-door registered group");
    port.Select("batch01-event-000006"); await Ask("capture_now"); calls = port.Calls;
    await Ask("close_map");
    var waiting = await Ask("select_group_member", GroupPayload("nightmare-19"));
    Check(waiting.Receipt.Success && runtime.ReadState().Context.MapId == "nightmare-12" && runtime.ReadPendingGroupMaterial() is null
        && runtime.ReadState().GroupChoice is { DesiredMapId: "nightmare-19", Status: "awaiting-geometry" },
        "closed-map selection waits without changing committed identity");
    port.Select("batch01-event-000006"); await Ask("capture_now");
    var reopened = runtime.ReadPendingGroupMaterial();
    Check(reopened is { MapId: "nightmare-19" } && runtime.ReadState().Context.MapId == "nightmare-12"
        && runtime.ReadState().GroupChoice.HeldMapId == "nightmare-12" && port.Calls == calls + 1,
        "fresh reopening revalidates the group and retries desired geometry while retaining the held member");
    Check(runtime.AcceptGroupMaterialReady(reopened!) && runtime.ReadState().Context.MapId == "nightmare-19",
        "retried identity changes only after its own material-ready ACK");
    await Ask("select_group_member", GroupPayload("nightmare-12"));
    var beforeAuthor = runtime.ReadPendingGroupMaterial();
    await Ask("set_context", new { season = "s0", mode = "nightmare", profileId = "custom:unavailable-test" });
    Check(beforeAuthor is not null && !runtime.AcceptGroupMaterialReady(beforeAuthor) && runtime.ReadState().Context.MapId == "nightmare-19"
        && runtime.ReadState().GroupChoice.DesiredMapId == "nightmare-12", "author changes revoke only pending material and retain desired and held identity");
    port.Select("batch01-event-000006"); await Ask("capture_now");
    Check(runtime.ReadState().Context.MapId == "nightmare-19" && runtime.ReadState().GroupChoice.Status == "awaiting-geometry"
        && runtime.ReadState().GroupChoice.Reason == "custom-display-unavailable" && runtime.ReadPendingGroupMaterial() is null,
        "successful geometry with missing target material cannot commit a member and reports the actual material failure");
    await Ask("set_context", new { season = "s0", mode = "nightmare", profileId = "shared-structure" });
    await Ask("select_group_member", GroupPayload("nightmare-12"));
    var beforeClose = runtime.ReadPendingGroupMaterial();
    await Ask("close_map");
    Check(beforeClose is not null && !runtime.AcceptGroupMaterialReady(beforeClose) && !runtime.TryReadRender(out _)
        && runtime.ReadState().Context.MapId == "nightmare-19", "close revokes materials and rejects delayed ACK while retaining held identity");
    await Ask("end_round");
    Check(runtime.ReadState().GroupChoice is { DesiredMapId: null, HeldMapId: null, PendingMapId: null }
        && runtime.ReadState().IdentityMemberIds.Count == 0, "end round clears desired pending and held group choice");
    Save("registered-group-member-transactions", runtime.ReadState());
});

await Run("unconfirmed-desired-member-survives-failed-target-floor", async () =>
{
    await Ask("end_round");
    await Ask("set_context", new { season = "s0", mode = "nightmare", profileId = "shared-structure" });
    await Ask("start_round"); port.Select("batch01-event-000006"); await Ask("capture_now");
    await Ask("close_map"); await Ask("select_group_member", GroupPayload("nightmare-12"));
    port.Select("batch02-event-000018"); await Ask("capture_now");
    Check(runtime.ReadState().GroupChoice is { DesiredMapId: "nightmare-12", HeldMapId: null, Status: "awaiting-geometry" }
        && runtime.ReadState().Context.MapId == "nightmare-04" && runtime.ReadState().IdentityKind == "group"
        && runtime.ReadPendingGroupMaterial() is null && !runtime.TryReadRender(out _),
        "a failed target-floor fit keeps the original group reference and unconfirmed intent while withdrawing current material");
    port.Select("batch01-event-000006"); await Ask("capture_now");
    var recovered = runtime.ReadPendingGroupMaterial();
    Check(recovered is { MapId: "nightmare-12" } && runtime.ReadState().GroupChoice.HeldMapId is null,
        "returning to the group reference retries the retained choice without inventing a hold");
    Check(runtime.AcceptGroupMaterialReady(recovered!) && runtime.ReadState().GroupChoice.HeldMapId == "nightmare-12",
        "recovered desired member still requires its own material-ready ACK");
    await Ask("end_round");
});

foreach (string id in new[] { "batch02-event-000053", "batch02-event-000097", "batch02-event-000147" })
await Run(id + "-automatic-S0-identity-reaches-canonical-material", async () =>
{
    await Ask("end_round");
    string mode = index[id]["mode"]!.GetValue<string>();
    await Ask("set_context", new { season = "s0", mode, profileId = "shared-structure" });
    await Ask("start_round"); port.Select(id);
    var result = await Ask("capture_now"); var state = runtime.ReadState();
    // All independent answers are opened after the production solve finishes.
    var expected = JsonNode.Parse(File.ReadAllBytes(Path.Combine(Path.GetDirectoryName(oracle)!,
        "s0-identity-consumer-python-01", id, "decision.json")))!;
    string map = expected["selectedMapId"]!.GetValue<string>();
    Check(expected["uniqueHardIdentityCandidate"]!.GetValue<bool>() && state.IdentityKind == "map"
        && state.IdentityLocked && state.IdentitySource == "automatic-hard-gate" && state.Context.MapId == map,
        id + " full current-rank predicate commits the independently recognized unique map");
    var fit = JsonNode.Parse(File.ReadAllBytes(Path.Combine(oracle, id, "downstream-fit.json")))!["candidates"]!
        .AsArray().Single(row => row!["mapId"]!.GetValue<string>() == map)!["fit"]!;
    Check(result.Receipt.Success && state.NativeGeometryVerified, id + " actual entry candidate reaches Host fit");
    MatrixEqual(state.NativePose!.ToMatrix(), fit["matrixSourceToClient"]!, id + " original Host matrix is consumed");
    Check(state.Display.CanonicalVerified && runtime.TryReadRender(out var packet) && !packet!.IsLive
        && !runtime.ReadSnapshot().LiveAuthorization, id + " verified material remains Recorded");
    Save(id + "-automatic-runtime", state);
});

runtime.SetAlignedVisibility(false); await Ask("close_map"); await Ask("end_round");
Check(!runtime.ReadState().AlignedVisible, "closing and ending a round retain the independent visibility preference");
await Ask("start_round");
Check(!runtime.ReadState().AlignedVisible, "starting the next round does not reset visibility");
runtime.Shutdown();
Check(!runtime.SetAlignedVisibility(true) && !runtime.ReadState().AlignedVisible, "shutdown rejects late presentation writes");
Check(!runtime.TryReadRender(out _) && runtime.ReadSnapshot().AvailableCommands.Count == 0, "shutdown revokes all display and operations");
Save("summary", new { passed = failures.Count == 0, checkCount = checks.Count, failures, checks,
    scope = "Production LiveRuntime and original current-client PNG/resource replay. Capture port only is replaced; no native capture, live grant, overlay, GUI or game acceptance." });
Console.WriteLine($"Runtime replay: {checks.Count - failures.Count}/{checks.Count}; evidence {output}");
return failures.Count == 0 ? 0 : 1;

Task<RuntimeOperationResult> Ask(string action, object? payload = null) => runtime.DispatchAsync(new(action,
    JsonSerializer.SerializeToElement(payload ?? new { }), new(sequence.Issue(), runtime.ReadSnapshot().Context)), CancellationToken.None).AsTask();
JsonObject Original(string id) => JsonNode.Parse(File.ReadAllBytes(Path.Combine(oracle, id, "recognition.json")))!.AsObject();
object GroupPayload(string map)
{
    var state = runtime.ReadState();
    return new { mapId = map, groupId = state.IdentityGroupId, groupGeneration = state.GroupChoice.Generation,
        cycleId = state.GroupChoice.CycleId, floor = state.Context.FloorId };
}
string[] Ids(JsonNode? value) => value!.AsArray().Select(v => v!.GetValue<string>()).ToArray();
void Save(string name, object value) => File.WriteAllText(Path.Combine(output, name + ".json"), JsonSerializer.Serialize(value, new JsonSerializerOptions { WriteIndented = true }));
void Check(bool condition, string name)
{
    checks.Add(new { name, passed = condition });
    if (!condition) { failures.Add(name); throw new InvalidOperationException(name); }
}
void MatrixEqual(double[][] actual, JsonNode expected, string name)
{
    var wanted = expected.Deserialize<double[][]>()!;
    Check(actual.Length == 3 && wanted.Length == 3 && actual.All(row => row.Length == 3) && wanted.All(row => row.Length == 3)
        && actual.SelectMany(row => row).Zip(wanted.SelectMany(row => row)).All(pair => Math.Abs(pair.First - pair.Second) <= 1e-5), name);
}
async Task Run(string name, Func<Task> body)
{
    try { await body(); }
    catch (Exception error)
    {
        Save(name + "-failure", new { state = runtime.ReadState(), error = error.ToString() });
        if (!failures.Contains(error.Message)) failures.Add(name + ": " + error);
        Console.Error.WriteLine(name + ": " + error);
    }
}
