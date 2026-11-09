using System.Runtime.InteropServices;
using System.Collections.Concurrent;
using System.Diagnostics;
using System.Text.Json;
using System.Text.Json.Nodes;
using OpenCvSharp;
using StarMap.Core.Identity;
using StarMap.Core.Resources;
using StarMap.Core.Session;
using StarMap.Runtime;
using StarMap.Vision;

var options = args.Chunk(2).ToDictionary(pair => pair[0], pair => pair[1]);
if (!options.TryGetValue("--resources", out var resourceRoot) || !options.TryGetValue("--capture-record", out var recordPath)
    || !options.TryGetValue("--output", out var output))
    throw new ArgumentException("Use --resources ROOT --capture-record NEW_CAPTURE_JSON --output NEW_DIRECTORY");
if (Directory.Exists(output)) throw new IOException("output-already-exists");
using var captureRecord = JsonDocument.Parse(File.ReadAllBytes(recordPath));
var recordedAt = captureRecord.RootElement.GetProperty("capturedAt").GetDateTimeOffset();
if (recordedAt < new DateTimeOffset(2026, 11, 5, 0, 0, 0, TimeSpan.Zero))
    throw new InvalidDataException("pre-20261105-frames-prohibited");
var imagePath = Path.Combine(Path.GetDirectoryName(Path.GetFullPath(recordPath))!, captureRecord.RootElement.GetProperty("samplePath").GetString()!);
using var original = Cv2.ImRead(imagePath, ImreadModes.Color);
if (original.Empty()) throw new InvalidDataException("capture-png-unreadable");
using var pixels = new Mat(); Cv2.CvtColor(original, pixels, ColorConversionCodes.BGR2BGRA);
var bgra = new byte[checked(pixels.Rows * pixels.Cols * 4)]; Marshal.Copy(pixels.Data, bgra, 0, bgra.Length);
Directory.CreateDirectory(output);
var reports = new List<object>();
var failed = 0;
void Check(bool passed, string reason) { if (!passed) throw new InvalidOperationException(reason); }
async Task Run(string name, Func<Task> test)
{
    try { await test(); reports.Add(new { name, passed = true }); Console.WriteLine("ok " + name); }
    catch (Exception error) { failed++; reports.Add(new { name, passed = false, error = error.ToString() }); Console.Error.WriteLine("FAIL " + name + " " + error.Message); }
}
var port = new FixtureCapture(bgra, pixels.Width, pixels.Height, recordedAt);
var runtime = new LiveRuntime(port, resourceRoot);
var driver = new Driver(runtime);
await Run("construction-does-not-bind-load-capture-or-start-round", () =>
{
    var initial = runtime.ReadState();
    Check(!initial.ResourcesBound && !initial.RoundActive && !initial.IdentityLocked && !initial.NativeGeometryVerified && port.Calls == 0, "constructor is inert");
    return Task.CompletedTask;
});
await Run("explicit-bind-start-and-actual-current-floor-main-producer", async () =>
{
    Check((await driver.Ask("set_context", new { season = "s0", mode = "nightmare", profileId = "display-preference-only" })).Receipt.Success, "actual context bound");
    Check(port.Calls == 0 && !runtime.ReadState().RoundActive, "binding does not capture or start round");
    port.IsBound = false;
    Check(runtime.ReadSnapshot().AvailableCommands.Contains("start_round")
        && (await driver.Ask("start_round")).Receipt.Success && port.Calls == 0
        && !runtime.ReadSnapshot().LiveEngineAvailable && !runtime.ReadSnapshot().LiveAuthorization,
        "explicit preparation without game does not grant capture or input authority");
    Check(!(await driver.Ask("capture_now")).Receipt.Success && port.Calls == 0, "unbound prepared round cannot acquire pixels");
    port.IsBound = true;
    var result = await driver.Ask("capture_now");
    var state = runtime.ReadState();
    var floor = FloorObservation.Observe(original, "s0", "nightmare");
    Check(floor.AutomaticEligible && floor.FloorId is not null && state.Context.FloorId == floor.FloorId, "current floor comes from actual frame controls");
    var passports = EntryPassportCatalog.Load(resourceRoot, "s0", "nightmare");
    var pipeline = new S0IdentityRankPipeline(ResourceCatalog.Load(resourceRoot), passports);
    var context = pipeline.Context(state.Context.RoundId!, floor.FloorId!);
    var expected = pipeline.Rank(original, floor.FloorId!, new LiveRuntimeOptions().MainEntryBudget, context: context);
    var decision = S0IdentityDecision.Consume(expected, null, context, "independent-runtime-wiring-check");
    var candidates = decision.CandidateSummaries;
    Check(state.Candidates.Select(c => c.MapId).SequenceEqual(candidates.Select(row => row!["mapId"]!.GetValue<string>()))
        && state.Candidates.Select(c => c.IdentityKind).SequenceEqual(candidates.Select(row => row!["identityKind"]!.GetValue<string>())),
        "runtime consumes the complete current S0 candidate consumer, including registered groups");
    Check(state.IdentityLocked == (decision.SelectedProvisionalMapId is not null)
        && (state.IdentityKind == "group") == decision.IsRegisteredGroup,
        "runtime distinguishes a registered group reference from unique identity");
    Check(!result.Snapshot.RoundWorkActive && !runtime.ReadSnapshot().LiveAuthorization, "final snapshot settled and no canonical/display authority claimed");
    File.WriteAllText(Path.Combine(output, "actual-main.json"), JsonSerializer.Serialize(new { state, direct = expected, requestResult = result }));
});
await Run("manual-confirmation-locks-before-fit-and-consumes-actual-native-fit", async () =>
{
    var before = runtime.ReadState();
    Check(before.MapOpen && before.Candidates.Count > 0, "fixture supplies a current frame for an explicit human map choice");
    string mapId = before.IdentityKind == "group" ? before.Context.MapId! : before.Candidates.First(c => c.MapId.Length > 0).MapId;
    var passports = EntryPassportCatalog.Load(resourceRoot, "s0", "nightmare");
    var chosen = new RuntimeCandidate(mapId, passports.MapProfiles[mapId], 0, false, false);
    var confirmation = await driver.Ask("confirm_route", new { mapId = chosen.MapId });
    var state = runtime.ReadState();
    Check(state.IdentityLocked && state.Context.MapId == chosen.MapId && state.IdentitySource == "manual", "manual identity survives pose refusal");
    var catalog = ResourceCatalog.Load(resourceRoot);
    var entry = catalog.Find("nightmare", "s0", chosen.IdentityProfileId, chosen.MapId, before.Context.FloorId!)!;
    var reference = catalog.LoadSharedStructure(entry).Value!;
    var path = Path.Combine(resourceRoot, "references", "fast-structure", Path.GetFileNameWithoutExtension(entry.StructureNpzPath) + "-fast.npz");
    Check(File.Exists(path), "actual producer path requires prebuilt fast resource");
    using var geometry = StructureGeometry.Open(reference);
    using var observation = Geometry.Current(original);
    var expected = observation is null ? new FastFitResult(false, "fast-observation-unavailable", null)
        : FastStructureFit.Evaluate(original, observation, geometry, FastStructure.LoadPrepared(path, reference));
    Check(state.NativeGeometryVerified == expected.GeometryVerified && confirmation.Receipt.Success == expected.GeometryVerified, "real fast fit verdict preserved");
    if (expected.Matrix is not null && expected.GeometryVerified)
        Check(JsonNode.DeepEquals(JsonSerializer.SerializeToNode(state.NativePose!.ToMatrix()), JsonSerializer.SerializeToNode(expected.Matrix)), "native matrix exactly matches independently executed fit");
    Check(state.Diagnostics.GetProperty("stage").GetString() == "s0-host-fit"
        && !state.Diagnostics.GetProperty("priorUsed").GetBoolean()
        && typeof(LiveRuntime).GetField("_geometryGroup", System.Reflection.BindingFlags.NonPublic
            | System.Reflection.BindingFlags.Instance)!.GetValue(runtime) is null,
        "the legacy capture fixture without physical context cannot turn a native pose into an authoritative reusable geometric group");
    Check(state.Context.ProfileId == "display-preference-only" && state.Diagnostics.GetProperty("nativeReferenceKey").GetString() == entry.StableKey,
        "display preference never changes the actual fitted structure resource");
    Check(!runtime.ReadSnapshot().LiveAuthorization && (!state.Display.Prepared || state.Display.CanonicalVerified),
        "recorded material requires canonical verification and never grants live display");
    File.WriteAllText(Path.Combine(output, "actual-fit.json"), JsonSerializer.Serialize(new { state, direct = expected, confirmation }));
});
await Run("close-preserves-human-identity-and-floor-switch-never-borrows-prior", async () =>
{
    var locked = runtime.ReadState().Context.MapId;
    await driver.Ask("close_map");
    Check(runtime.ReadState().IdentityLocked && runtime.ReadState().Context.MapId == locked && !runtime.ReadState().MapOpen && !runtime.ReadState().NativeGeometryVerified, "close revokes pixels and pose without retracting human identity");
    Check((await driver.Ask("set_floor", new { floorId = "2" })).Receipt.Success, "explicit different floor selected while closed");
    var switched = await driver.Ask("capture_now");
    var state = runtime.ReadState();
    Check(state.Context.FloorId == "2" && state.Context.MapId == locked, "target floor remains independent");
    if (state.Diagnostics.TryGetProperty("stage", out var stage) && stage.GetString() == "s0-host-fit")
        Check(!state.Diagnostics.GetProperty("priorUsed").GetBoolean(), "1F verified prior cannot seed 2F");
    else Check(!switched.Receipt.Success, "unavailable target floor explicitly refuses pose");
});
await Run("binding-frame-drift-withholds-old-pixels-without-mutating-identity", () =>
{
    port.AdvanceGeneration();
    var state = runtime.ReadState();
    Check(!state.MapOpen && !state.NativeGeometryVerified && state.NativePose is null && state.Context.FrameId is null && state.IdentityLocked,
        "old capture context cannot authorize current geometry");
    runtime.InvalidateCapture("binding-replaced");
    return Task.CompletedTask;
});
foreach (var boundary in new[] { "cancel", "close", "resources" })
await Run("late-capture-rejected-after-" + boundary, async () =>
{
    if (!runtime.ReadState().ResourcesBound) Check((await driver.Ask("set_context")).Receipt.Success, "resources rebind after invalidation");
    if (!runtime.ReadState().RoundActive) Check((await driver.Ask("start_round")).Receipt.Success, "round explicitly restarted");
    var hold = port.HoldNext();
    using var cancellation = new CancellationTokenSource();
    var pending = driver.Ask("capture_now", cancellation: cancellation.Token);
    await hold.Started.Task.WaitAsync(TimeSpan.FromSeconds(5));
    if (boundary == "cancel") cancellation.Cancel();
    else if (boundary == "resources") runtime.InvalidateResources("test-resource-replacement");
    else runtime.InvalidateCapture("test-map-close");
    Check(!runtime.ReadState().MapOpen && !runtime.ReadState().NativeGeometryVerified, "boundary revokes visible grants immediately");
    hold.Release();
    var late = await pending;
    Check(!late.Receipt.Success && !runtime.ReadState().MapOpen && !runtime.ReadState().NativeGeometryVerified && runtime.ReadState().Candidates.Count == 0,
        "returned old pixels never commit after boundary");
});
await Run("resource-rebind-author-change-stale-context-and-shared-mode-lifecycle", async () =>
{
    var obsolete = runtime.ReadSnapshot().Context;
    Check((await driver.Ask("set_context", new { season = "s0", mode = "nightmare", profileId = "new-display-author" })).Receipt.Success, "fresh resource binding recovers");
    var refused = await driver.Ask("capture_now", context: obsolete);
    Check(!refused.Receipt.Success && refused.Receipt.Reason == "request-runtime-context-mismatch", "old resource instance cannot run a new operation");
    foreach (var mode in new[] { "nightmare", "difficult" })
    {
        await driver.Ask("end_round");
        Check((await driver.Ask("set_context", new { season = "s0", mode, profileId = "author-does-not-select-identity" })).Receipt.Success, "actual target mode binds its own resources");
        Check((await driver.Ask("start_round")).Receipt.Success && runtime.ReadState().Context.Mode == mode, "shared lifecycle activates exact target mode");
        await driver.Ask("end_round");
        Check(!runtime.ReadState().RoundActive && !runtime.ReadState().IdentityLocked, "shared lifecycle ends and clears identity");
    }
});
await Run("new-synthetic-black-frame-preparation-fails-without-identity", async () =>
{
    var synthetic = new FixtureCapture(new byte[1334 * 750 * 4], 1334, 750, DateTimeOffset.UtcNow);
    var black = new LiveRuntime(synthetic, resourceRoot);
    var calls = new Driver(black);
    Check((await calls.Ask("set_context")).Receipt.Success && (await calls.Ask("start_round")).Receipt.Success, "synthetic uses actual bound production runtime");
    var result = await calls.Ask("map", new { openIntent = true, inputSequence = 1 });
    Check(!result.Receipt.Success && result.Receipt.Reason == "map-open-preparation-deadline" && !black.ReadState().IdentityLocked && !black.ReadState().NativeGeometryVerified,
        "new synthetic black input cannot fabricate open floor or identity");
    File.WriteAllText(Path.Combine(output, "synthetic-black.json"), JsonSerializer.Serialize(new { evidenceKind = "new-synthetic-black-frame", state = black.ReadState(), captureCalls = synthetic.Calls }));
});
await Run("shutdown-is-final-and-rejects-returned-capture", async () =>
{
    Check((await driver.Ask("start_round")).Receipt.Success, "explicit final round starts");
    var hold = port.HoldNext();
    var pending = driver.Ask("capture_now");
    await hold.Started.Task.WaitAsync(TimeSpan.FromSeconds(5));
    runtime.Shutdown();
    Check(runtime.ReadState().Closed && !runtime.ReadState().RoundActive && !runtime.ReadSnapshot().EngineConnected
        && runtime.ReadSnapshot().AvailableCommands.Count == 0, "shutdown revokes before worker exit");
    hold.Release();
    Check(!(await pending).Receipt.Success && !(await driver.Ask("set_context")).Receipt.Success
        && runtime.ReadState().Closed && !runtime.ReadState().NativeGeometryVerified, "late frame and new commands cannot reopen closed runtime");
});

foreach (var lateProbe in new[] { 1, 21 })
await Run("preparation-deadline-rejects-valid-late-probe-" + lateProbe, async () =>
{
    var clock = new AdvancingClock();
    var starts = new List<object>();
    var capture = new FixtureCapture(bgra, pixels.Width, pixels.Height, recordedAt)
    {
        PixelsForCall = call => call < lateProbe ? new byte[bgra.Length] : bgra,
        BeforeReturn = call => { starts.Add(new { call, ticks = clock.GetTimestamp() }); if (call == lateProbe) clock.Advance(TimeSpan.FromSeconds(2)); },
    };
    var actual = new LiveRuntime(capture, resourceRoot, new() { TimeProvider = clock });
    var calls = new Driver(actual);
    Check((await calls.Ask("set_context")).Receipt.Success && (await calls.Ask("start_round")).Receipt.Success, "late probe uses actual bound runtime");
    string? existingHumanMap = null;
    if (lateProbe == 21)
    {
        // The source only promises an upper bound of 21. Its unlocked first
        // probe waits .05s, so a zero-cost unlocked run has only 20 starts
        // before 1.05s. A legitimate locked identity starts immediately and
        // makes the 21st near-deadline probe reachable under the original max.
        existingHumanMap = EntryPassportCatalog.Load(resourceRoot, "s0", "nightmare").MapProfiles.Keys.First();
        Check((await calls.Ask("confirm_route", new { mapId = existingHumanMap })).Receipt.Success && capture.Calls == 0,
            "real manual confirmation locks identity without creating capture or pose evidence");
    }
    var before = actual.ReadState();
    var result = await calls.Ask("map", new { openIntent = true, inputSequence = 1 });
    var state = actual.ReadState();
    Check(state.Diagnostics.GetProperty("phase").GetString() == "observation-finished-after-deadline"
        && state.Diagnostics.GetProperty("probes").GetInt32() == lateProbe
        && state.Diagnostics.GetProperty("elapsedMs").GetDouble() > state.Diagnostics.GetProperty("budgetMs").GetDouble()
        && state.Diagnostics.GetProperty("capturedFrameId").GetString() is not null,
        "initial late capture is still observed and retains phase, probe, deadline and current frame diagnostics");
    File.WriteAllText(Path.Combine(output, "late-probe-" + lateProbe + ".json"), JsonSerializer.Serialize(new
    { before, result, state, capture.Calls, starts, clockTicks = clock.GetTimestamp(), floatingProbeLimit = Math.Ceiling(new LiveRuntimeOptions().PreparationTimeout.TotalSeconds / new LiveRuntimeOptions().PreparationInterval.TotalSeconds) }));
    if (lateProbe == 1)
        Check(capture.Calls == lateProbe && !result.Receipt.Success && result.Receipt.Reason == "map-open-preparation-deadline"
            && !state.MapOpen && state.Context.FrameId is null && !state.IdentityLocked && !state.NativeGeometryVerified && state.Candidates.Count == 0,
            "a current valid frame completing after deadline never enters rank or fit");
    else
        Check(capture.Calls == lateProbe && !result.Receipt.Success && result.Receipt.Reason == "map-open-preparation-deadline"
            && !state.MapOpen && state.Context.FrameId is null && !state.NativeGeometryVerified && state.Candidates.Count == 0
            && state.IdentityLocked && state.Context.MapId == existingHumanMap && state.Context.MapId == before.Context.MapId
            && state.IdentitySource == "manual" && state.IdentitySource == before.IdentitySource
            && state.Context.ResourceId == before.Context.ResourceId && state.Context.RoundId == before.Context.RoundId,
            "locked immediate-first tail probe rejects late pixels while preserving only the preexisting human identity");
});

await Run("unlocked-preparation-original-cadence-has-20-starts-before-deadline", async () =>
{
    var clock = new AdvancingClock(); var starts = new List<long>();
    var capture = new FixtureCapture(new byte[bgra.Length], pixels.Width, pixels.Height, recordedAt)
    { BeforeReturn = _ => starts.Add(clock.GetTimestamp()) };
    var actual = new LiveRuntime(capture, resourceRoot, new() { TimeProvider = clock }); var calls = new Driver(actual);
    Check((await calls.Ask("set_context")).Receipt.Success && (await calls.Ask("start_round")).Receipt.Success, "unlocked cadence uses actual runtime");
    var result = await calls.Ask("map", new { openIntent = true, inputSequence = 1 }); var state = actual.ReadState();
    File.WriteAllText(Path.Combine(output, "unlocked-cadence.json"), JsonSerializer.Serialize(new { result, state, capture.Calls, starts, clockTicks = clock.GetTimestamp(), origin = "new synthetic black probes; clock boundary only" }));
    Check(capture.Calls == 20 && starts.SequenceEqual(Enumerable.Range(1, 20).Select(i => TimeSpan.FromMilliseconds(i * 50).Ticks))
        && clock.GetTimestamp() == TimeSpan.FromSeconds(1.05).Ticks && !result.Receipt.Success && result.Receipt.Reason == "map-open-preparation-deadline"
        && !state.IdentityLocked && !state.MapOpen && state.Context.FrameId is null && !state.NativeGeometryVerified && state.Candidates.Count == 0,
        "original unlocked delayed-first and max-tail policy is retained rather than changed to force a 21st probe");
});

await Run("preparation-deadline-rechecked-after-current-frame-observation", async () =>
{
    var clock = new AdvancingClock();
    var advanced = false;
    var capture = new FixtureCapture(bgra, pixels.Width, pixels.Height, recordedAt)
    {
        BeforeCurrentCheck = _ => { if (!advanced) { advanced = true; clock.Advance(TimeSpan.FromSeconds(2)); } },
    };
    var actual = new LiveRuntime(capture, resourceRoot, new() { TimeProvider = clock }); var calls = new Driver(actual);
    Check((await calls.Ask("set_context")).Receipt.Success && (await calls.Ask("start_round")).Receipt.Success, "post-capture clock context starts");
    var result = await calls.Ask("map", new { openIntent = true, inputSequence = 1 });
    Check(advanced && !result.Receipt.Success && result.Receipt.Reason == "map-open-preparation-deadline"
        && !actual.ReadState().MapOpen && actual.ReadState().Candidates.Count == 0 && actual.ReadState().Context.FrameId is null,
        "passing capture-return check does not exempt observation from the same deadline");
});

await Run("manual-floor-hold-expires-into-current-observed-floor", async () =>
{
    var clock = new AdvancingClock(); var capture = new FixtureCapture(bgra, pixels.Width, pixels.Height, recordedAt);
    var actual = new LiveRuntime(capture, resourceRoot, new() { TimeProvider = clock }); var calls = new Driver(actual);
    Check((await calls.Ask("set_context")).Receipt.Success && (await calls.Ask("start_round")).Receipt.Success, "manual hold context starts");
    Check((await calls.Ask("set_floor", new { floor = "2" })).Receipt.Success, "original floor payload accepted");
    clock.Advance(TimeSpan.FromSeconds(3));
    await calls.Ask("capture_now");
    Check(actual.ReadState().Context.FloorId == "1", "expired manual 2F must use this real current 1F observation");
});

await Run("manual-floor-hold-keeps-intent-before-expiry-and-releases-on-fresh-evidence", async () =>
{
    var clock = new AdvancingClock(); var capture = new FixtureCapture(bgra, pixels.Width, pixels.Height, recordedAt);
    var actual = new LiveRuntime(capture, resourceRoot, new() { TimeProvider = clock }); var calls = new Driver(actual);
    await calls.Ask("set_context"); await calls.Ask("start_round"); await calls.Ask("set_floor", new { floor = "2" });
    clock.Advance(TimeSpan.FromSeconds(1)); await calls.Ask("capture_now");
    Check(actual.ReadState().Context.FloorId == "2" && actual.ReadState().ManualFloor == "2" && actual.ReadState().ObservedFloor == "1",
        "current conflicting sensor reading does not defeat the two-second user hold");
    clock.Advance(TimeSpan.FromSeconds(1)); await calls.Ask("capture_now");
    Check(actual.ReadState().Context.FloorId == "1" && actual.ReadState().ManualFloor is null && actual.ReadState().ObservedFloor == "1",
        "fresh latest evidence releases expired override rather than preserving a permanent manual floor");
});

foreach (var autoFloor in new string?[] { null, "", "auto" })
await Run("manual-floor-auto-payload-" + (autoFloor is null ? "missing" : autoFloor.Length == 0 ? "empty" : "auto"), async () =>
{
    var actual = new LiveRuntime(new FixtureCapture(bgra, pixels.Width, pixels.Height, recordedAt), resourceRoot);
    var calls = new Driver(actual);
    Check((await calls.Ask("set_context")).Receipt.Success && (await calls.Ask("start_round")).Receipt.Success, "auto floor context starts");
    Check((await calls.Ask("set_floor", new { floor = "2" })).Receipt.Success, "manual target selected");
    var result = await calls.Ask("set_floor", autoFloor is null ? new { } : (object)new { floor = autoFloor });
    Check(result.Receipt.Success && actual.ReadState().Context.FloorId is null, "None/empty/auto clears target when no current sensor floor exists");
    await calls.Ask("capture_now");
    Check(actual.ReadState().Context.FloorId == "1", "auto mode consumes current 1F controls");
});

foreach (var stage in new[] { "rank", "fit" })
await Run("real-session-refresh-preserves-own-" + stage + "-progress", () =>
{
    using var combo = new SessionCombo(new LiveRuntime(new FixtureCapture(bgra, pixels.Width, pixels.Height, recordedAt), resourceRoot));
    combo.DispatchAndDrain("set_context"); combo.DispatchAndDrain("start_round");
    if (stage == "fit") combo.DispatchAndDrain("capture_now");
    var chosen = stage == "fit" ? ManualTarget(combo.Runtime.ReadState()) : null;
    combo.Session.Dispatch(stage == "rank" ? "capture_now" : "confirm_route", chosen is null ? null : JsonSerializer.SerializeToElement(new { mapId = chosen }));
    combo.Until(() =>
    {
        var state = combo.Runtime.ReadState();
        return stage == "rank" ? state.MapOpen && state.Context.FrameId is not null && state.Candidates.Count == 0
            : state.IdentityLocked && !state.NativeGeometryVerified;
    }, pumpFirst: false);
    Check(combo.Session.Read().Busy && combo.Session.Read().WorkerActive, "refresh occurs before original session completion consumption");
    combo.Session.RefreshRuntime();
    Check(combo.Session.Read().Busy, "the real SessionService must not cancel this request's own published progress");
    combo.Drain();
    var view = combo.Session.Read();
    Check(view.StaleResults == 0 && view.LastReceipt?.Action == (stage == "rank" ? "capture_now" : "confirm_route"),
        "original current receipt remains consumable through the real result gate");
    if (stage == "fit") Check(combo.Runtime.ReadState().IdentityLocked && combo.Runtime.ReadState().IdentitySource == "manual"
        && combo.Runtime.ReadState().NativeGeometryVerified, "explicit map payload passes the real session even after automatic identity and completes fit after progress refresh");
    File.WriteAllText(Path.Combine(output, "session-own-" + stage + ".json"), JsonSerializer.Serialize(new { view, state = combo.Runtime.ReadState() }));
    return Task.CompletedTask;
});

await Run("real-session-progress-attribution-does-not-survive-external-resource-invalidation", () =>
{
    using var combo = new SessionCombo(new LiveRuntime(new FixtureCapture(bgra, pixels.Width, pixels.Height, recordedAt), resourceRoot));
    combo.DispatchAndDrain("set_context"); combo.DispatchAndDrain("start_round");
    combo.Session.Dispatch("capture_now");
    combo.Until(() => combo.Runtime.ReadState() is { MapOpen: true, Candidates.Count: 0 }, pumpFirst: false);
    combo.Session.RefreshRuntime(); Check(combo.Session.Read().Busy, "own frame progress remains current before external invalidation");
    combo.Runtime.InvalidateResources("external-resource-replacement");
    combo.Session.RefreshRuntime();
    Check(!combo.Session.Read().Busy && combo.Session.Read().Runtime.Context.ResourceId is null, "external resource boundary still invalidates the session request");
    combo.Drain();
    Check(combo.Session.Read().LastReceipt is null && combo.Session.Read().StaleResults == 1
        && !combo.Runtime.ReadState().NativeGeometryVerified && !combo.Runtime.ReadState().ResourcesBound,
        "old rank result cannot be accepted through progress attribution");
    return Task.CompletedTask;
});

await Run("default-s1-uses-real-vertical-observation-and-keeps-unverified-identity-unlocked", async () =>
{
    var capture = new FixtureCapture(bgra, pixels.Width, pixels.Height, recordedAt);
    var actual = new LiveRuntime(capture, resourceRoot); var calls = new Driver(actual);
    Check((await calls.Ask("set_context", new { season = "s1", mode = "nightmare" })).Receipt.Success, "actual S1 resources bind independently");
    Check((await calls.Ask("start_round")).Receipt.Success, "S1 starts in its own resource scope");
    await calls.Ask("capture_now");
    var observed = FloorObservation.Observe(original, "s1", "nightmare", s1Use: S1FloorUse.StructureCandidate);
    var state = actual.ReadState();
    Check(capture.Calls > 0 && actual.ReadSnapshot().AvailableCommands.Contains("capture_now")
        && !state.CapabilityGaps.Contains("s1-floor-vertical-consumer-unavailable")
        && !state.CapabilityGaps.Contains("s1-structure-identity-consumer-unavailable"),
        "S1 default runtime invokes the current-frame producer");
    Check(!state.IdentityLocked && !actual.ReadSnapshot().LiveAuthorization
        && (state.Context.FloorId is null || state.Context.FloorId == observed.FloorId),
        "this recorded fixture cannot invent S1 identity or fill floor from S0 history");
});

await Run("real-session-same-request-context-advance-between-snapshot-and-owner-read", () =>
{
    var capture = new FixtureCapture(bgra, pixels.Width, pixels.Height, recordedAt);
    var actual = new LiveRuntime(capture, resourceRoot);
    var ingress = new InterleavedRuntime(actual);
    using var combo = new SessionCombo(actual, ingress);
    combo.DispatchAndDrain("set_context"); combo.DispatchAndDrain("start_round"); combo.DispatchAndDrain("capture_now");
    var chosen = ManualTarget(actual.ReadState());
    combo.Session.Dispatch("confirm_route", JsonSerializer.SerializeToElement(new { mapId = chosen })); combo.Drain();
    Check(actual.ReadState() is { MapOpen: true, IdentityLocked: true } && actual.ReadState().Context.FrameId is not null,
        "real current F1 and manual identity established before floor change");
    var hold = capture.HoldNext();
    try
    {
        combo.Session.Dispatch("set_floor", JsonSerializer.SerializeToElement(new { floor = "2" }));
        combo.Until(() => hold.Started.Task.IsCompleted);
        RuntimeContext? readContext = null, advancedContext = null;
        ingress.AfterSnapshotRead = snapshot =>
        {
            readContext = snapshot.Context;
            Check(readContext.FloorId == "2" && readContext.FrameId is null, "read cA after real floor revoke/publish");
            hold.Release();
            var wait = Stopwatch.StartNew();
            while (actual.ReadState().Context.FrameId is null)
            { if (wait.Elapsed > TimeSpan.FromSeconds(10)) throw new TimeoutException("same-request cB not published"); Thread.Sleep(1); }
            advancedContext = actual.ReadSnapshot().Context;
            Check(advancedContext.FloorId == "2" && advancedContext.FrameId is not null && advancedContext != readContext,
                "same actual worker advanced to cB before the old owner check");
        };
        combo.Session.RefreshRuntime();
        var afterRefresh = combo.Session.Read();
        File.WriteAllText(Path.Combine(output, "session-snapshot-owner-interleave.json"), JsonSerializer.Serialize(new
        { readContext, advancedContext, ingress.LastRequest, afterRefresh, runtime = actual.ReadState() }));
        Check(readContext is not null && advancedContext is not null && afterRefresh.Busy,
            "one request publishing cB cannot revoke the atomically attributed cA snapshot");
        combo.Drain();
        Check(combo.Session.Read().StaleResults == 0 && combo.Session.Read().LastReceipt?.Action == "set_floor",
            "original ticket consumes the actual floor fit receipt without a self-cancellation");
    }
    finally { if (!hold.Result.Task.IsCompleted) hold.Release(); }
    return Task.CompletedTask;
});

foreach (var timing in new[] { "before-atomic-read", "after-atomic-read" })
foreach (var boundary in new[] { "capture", "resources" })
await Run("real-session-atomic-progress-rejects-" + boundary + "-revocation-" + timing, () =>
{
    var capture = new FixtureCapture(bgra, pixels.Width, pixels.Height, recordedAt);
    var actual = new LiveRuntime(capture, resourceRoot); var ingress = new InterleavedRuntime(actual);
    using var combo = new SessionCombo(actual, ingress);
    combo.DispatchAndDrain("set_context"); combo.DispatchAndDrain("start_round"); combo.DispatchAndDrain("capture_now");
    var chosen = ManualTarget(actual.ReadState());
    combo.Session.Dispatch("confirm_route", JsonSerializer.SerializeToElement(new { mapId = chosen })); combo.Drain();
    var hold = capture.HoldNext();
    try
    {
        combo.Session.Dispatch("set_floor", JsonSerializer.SerializeToElement(new { floor = "2" }));
        combo.Until(() => hold.Started.Task.IsCompleted);
        var revoked = false;
        void Revoke()
        {
            revoked = true;
            if (boundary == "capture") actual.InvalidateCapture("atomic-read-external-capture-revoke");
            else actual.InvalidateResources("atomic-read-external-resource-revoke");
        }
        if (timing == "before-atomic-read") ingress.BeforeSnapshotRead = Revoke;
        else ingress.AfterSnapshotRead = _ => Revoke();
        combo.Session.RefreshRuntime();
        // A bundled read only attests its linearization point. If invalidation
        // happens after that point, the next read must observe revoked ownership
        // even when cA already had FrameId=null and visible fields stay equal.
        combo.Session.RefreshRuntime();
        var view = combo.Session.Read();
        Check(revoked && !view.Busy && !actual.ReadState().MapOpen && !actual.ReadState().NativeGeometryVerified,
            "external epoch/capture invalidation still revokes progress and the actual producer");
        hold.Release(); combo.Drain();
        Check(combo.Session.Read().StaleResults == 1 && combo.Session.Read().LastReceipt is null
            && actual.ReadState().Candidates.Count == 0 && actual.ReadState().NativePose is null,
            "a returned old capture cannot cross an external boundary through the atomic progress seam");
        File.WriteAllText(Path.Combine(output, "atomic-" + timing + "-" + boundary + ".json"), JsonSerializer.Serialize(new
        { view, finalView = combo.Session.Read(), state = actual.ReadState(), ingress.LastRequest }));
    }
    finally { if (!hold.Result.Task.IsCompleted) hold.Release(); }
    return Task.CompletedTask;
});

await Run("real-session-final-consumption-rejects-revoked-epoch-without-another-refresh", () =>
{
    var capture = new FixtureCapture(bgra, pixels.Width, pixels.Height, recordedAt);
    var actual = new LiveRuntime(capture, resourceRoot);
    using var combo = new SessionCombo(actual);
    combo.DispatchAndDrain("set_context"); combo.DispatchAndDrain("start_round"); combo.DispatchAndDrain("capture_now");
    var chosen = ManualTarget(actual.ReadState());
    combo.Session.Dispatch("confirm_route", JsonSerializer.SerializeToElement(new { mapId = chosen })); combo.Drain();
    var consumed = new List<SessionCompletion>(); combo.Session.ResultConsumed += consumed.Add;
    var hold = capture.HoldNext();
    try
    {
        combo.Session.Dispatch("set_floor", JsonSerializer.SerializeToElement(new { floor = "2" }));
        combo.Until(() => hold.Started.Task.IsCompleted);
        combo.Session.RefreshRuntime();
        var acceptedProgress = combo.Session.Read();
        Check(acceptedProgress.Busy && acceptedProgress.Runtime.Context is { FloorId: "2", FrameId: null }, "cA progress accepted before epoch revocation");
        actual.InvalidateCapture("final-consumption-epoch-revoked");
        var sameContext = actual.ReadSnapshot().Context;
        Check(sameContext == acceptedProgress.Runtime.Context, "external epoch changes while visible cA remains equal");
        hold.Release(); combo.Drain(); // Deliberately no RefreshRuntime after invalidation.
        var finalView = combo.Session.Read();
        File.WriteAllText(Path.Combine(output, "final-no-refresh-revocation.json"), JsonSerializer.Serialize(new
        { acceptedProgress, sameContext, finalView, consumed, state = actual.ReadState() }));
        Check(finalView.LastReceipt is null && finalView.StaleResults == 1 && consumed.Count == 1 && consumed[0].Disposition != "Accepted"
            && !actual.ReadState().MapOpen && actual.ReadState().NativePose is null,
            "Complete itself must reject a revoked request without relying on another UI refresh");
    }
    finally { if (!hold.Result.Task.IsCompleted) hold.Release(); }
    return Task.CompletedTask;
});
File.WriteAllText(Path.Combine(output, "summary.json"), JsonSerializer.Serialize(new { passed = failed == 0, sourceCaptureRecord = recordPath,
    recordedAt, pixels.Width, pixels.Height, evidence = "Recorded post-20261105 pixels; production C# vision/state, capture port fixture only. No current game/window/display acceptance.", reports }, new JsonSerializerOptions { WriteIndented = true }));
return failed == 0 ? 0 : 1;

static string ManualTarget(LiveRuntimeState state) => state.IdentityKind == "group" ? state.Context.MapId!
    : state.Candidates.First(candidate => candidate.MapId.Length > 0).MapId;

sealed class Driver(LiveRuntime runtime)
{
    private readonly SessionSequencer _sequence = new();
    public Task<RuntimeOperationResult> Ask(string action, object? payload = null, CancellationToken cancellation = default, RuntimeContext? context = null)
    {
        var state = context ?? runtime.ReadSnapshot().Context;
        return runtime.DispatchAsync(new(action, JsonSerializer.SerializeToElement(payload ?? new { }), new(_sequence.Issue(), state)), cancellation).AsTask();
    }
}

sealed class FixtureCapture(byte[] pixels, int width, int height, DateTimeOffset capturedAt) : ICapturePort
{
    private readonly object _sync = new();
    private long _generation = 1, _sequence;
    private CaptureStamp? _current;
    private CaptureHold? _hold;
    public int Calls { get; private set; }
    public Func<int, byte[]>? PixelsForCall { get; init; }
    public Action<int>? BeforeReturn { get; init; }
    public Action<CaptureStamp>? BeforeCurrentCheck { get; init; }
    public bool IsBound { get; set; } = true;
    public bool IsCurrent(CaptureStamp stamp) { BeforeCurrentCheck?.Invoke(stamp); lock (_sync) return stamp == _current && stamp.Generation == _generation; }
    public ValueTask<ClientCaptureResult> CaptureAsync(CancellationToken cancellationToken)
    {
        lock (_sync)
        {
            Calls++;
            if (_hold is { } hold) { _hold = null; hold.Started.TrySetResult(); return new(hold.Result.Task); }
            return ValueTask.FromResult(Fresh());
        }
    }
    public void AdvanceGeneration() { lock (_sync) _generation++; }
    public CaptureHold HoldNext() { lock (_sync) return _hold = new(this); }
    private ClientCaptureResult Fresh()
    {
        BeforeReturn?.Invoke(Calls);
        _current = new("recorded-fixture-window", _generation, "fixture-frame-" + ++_sequence);
        return new(new(PixelsForCall?.Invoke(Calls) ?? pixels, width, height, width * 4, _current, capturedAt));
    }
    public sealed class CaptureHold(FixtureCapture owner)
    {
        public TaskCompletionSource Started { get; } = new(TaskCreationOptions.RunContinuationsAsynchronously);
        public TaskCompletionSource<ClientCaptureResult> Result { get; } = new(TaskCreationOptions.RunContinuationsAsynchronously);
        public void Release() { lock (owner._sync) Result.TrySetResult(owner.Fresh()); }
    }
}

sealed class AdvancingClock : TimeProvider
{
    private long _ticks;
    public override long TimestampFrequency => TimeSpan.TicksPerSecond;
    public override long GetTimestamp() => Interlocked.Read(ref _ticks);
    public override DateTimeOffset GetUtcNow() => DateTimeOffset.UnixEpoch + TimeSpan.FromTicks(GetTimestamp());
    public void Advance(TimeSpan elapsed) => Interlocked.Add(ref _ticks, elapsed.Ticks);
    public override ITimer CreateTimer(TimerCallback callback, object? state, TimeSpan dueTime, TimeSpan period)
    {
        var timer = new ClockTimer();
        ThreadPool.QueueUserWorkItem(_ => { if (!timer.Disposed) { Advance(dueTime); callback(state); } });
        return timer;
    }
    private sealed class ClockTimer : ITimer
    {
        public volatile bool Disposed;
        public bool Change(TimeSpan dueTime, TimeSpan period) => false;
        public void Dispose() => Disposed = true;
        public ValueTask DisposeAsync() { Dispose(); return ValueTask.CompletedTask; }
    }
}

sealed class SessionCombo : IDisposable
{
    private readonly OwnerQueue _owner = new();
    public LiveRuntime Runtime { get; }
    public SessionService Session { get; }
    public SessionCombo(LiveRuntime runtime, ISessionRuntime? ingress = null) { Runtime = runtime; Session = new(_owner, ingress ?? runtime); }
    public void DispatchAndDrain(string action) { Session.Dispatch(action); Drain(); }
    public void Drain() => Until(() => Session.DrainAsync().IsCompleted);
    public void Until(Func<bool> done, bool pumpFirst = true)
    {
        var deadline = Stopwatch.StartNew();
        while (true)
        {
            if (pumpFirst) _owner.Pump();
            if (done()) return;
            if (!pumpFirst) _owner.Pump();
            if (deadline.Elapsed > TimeSpan.FromSeconds(15)) throw new TimeoutException("real-session-progress-window-or-drain-unavailable");
            Thread.Sleep(1);
        }
    }
    public void Dispose() { Session.Shutdown(); Runtime.Shutdown(); Drain(); }
    private sealed class OwnerQueue : ISessionDispatcher
    {
        private readonly int _thread = Environment.CurrentManagedThreadId;
        private readonly ConcurrentQueue<Action> _callbacks = new();
        public bool CheckAccess() => Environment.CurrentManagedThreadId == _thread;
        public void Post(Action action) => _callbacks.Enqueue(action);
        public void Pump() { while (_callbacks.TryDequeue(out var callback)) callback(); }
    }
}

// A scheduling-only decorator: all state, capture, ranking, fit, receipts and
// attribution are delegated unchanged to the actual production runtime.
sealed class InterleavedRuntime(LiveRuntime actual) : ISessionRuntime, ISessionRuntimeProgress, IRuntimeRecoveryControl
{
    private SessionRuntimeRequest? _lastRequest;
    public SessionRuntimeRequest? LastRequest => Volatile.Read(ref _lastRequest);
    public Action<RuntimeSnapshot>? AfterSnapshotRead { get; set; }
    public Action? BeforeSnapshotRead { get; set; }
    public RuntimeSnapshot ReadSnapshot()
    {
        var before = BeforeSnapshotRead; BeforeSnapshotRead = null; before?.Invoke();
        var snapshot = actual.ReadSnapshot();
        var after = AfterSnapshotRead; AfterSnapshotRead = null;
        after?.Invoke(snapshot);
        return snapshot;
    }
    public SessionRuntimeRead ReadSnapshotFor(SessionRequestContext request)
    {
        var before = BeforeSnapshotRead; BeforeSnapshotRead = null; before?.Invoke();
        var read = actual.ReadSnapshotFor(request);
        var after = AfterSnapshotRead; AfterSnapshotRead = null;
        after?.Invoke(read.Snapshot);
        return read;
    }
    public ValueTask<RuntimeOperationResult> DispatchAsync(SessionRuntimeRequest request, CancellationToken cancellationToken)
    { Volatile.Write(ref _lastRequest, request); return actual.DispatchAsync(request, cancellationToken); }
    public void CancelMapOpenRecovery(string reason) => actual.CancelMapOpenRecovery(reason);
}
