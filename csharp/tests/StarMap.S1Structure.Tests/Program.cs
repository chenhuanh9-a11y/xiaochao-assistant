using System.Text.Json;
using System.Text.Json.Nodes;
using System.Text.Json.Serialization;
using OpenCvSharp;
using StarMap.Core.Resources;
using StarMap.Core.Selection;
using StarMap.Vision;

internal static class Program
{
    private static readonly JsonSerializerOptions Json = new() { WriteIndented = true, NumberHandling = JsonNumberHandling.AllowNamedFloatingPointLiterals };
    private static int Main(string[] args)
    {
        if (args.Length == 4 && args[0] == "--fit-probe") return StructureFitProbe.Run(args[1],args[2],args[3]);
        if (args.Length == 4 && args[0] == "--resource-binding-regressions") return ResourceBindingRegressions.Run(args[1],args[2],args[3]);
        if (args.Length == 3 && args[0] == "--contour-oracle") return StructureFitProbe.ContourOracle(args[1],args[2]);
        if (args.Length is not (3 or 4)) { Console.Error.WriteLine("Use StarMap.S1Structure.Tests <resources> <production-python-oracle> <new-output-directory> [session-oracle.json]; or --state-oracle <session-oracle.json> <new-output-directory>"); return 2; }
        string output = Path.GetFullPath(args[2]); if (Directory.Exists(output)) throw new IOException("Use a new output directory."); Directory.CreateDirectory(output);
        var failures = new List<string>(); var cases = new List<object>();
        try { StateContracts(); cases.Add(new { id = "state-contracts", passed = true }); }
        catch (Exception error) { failures.Add("state-contracts: " + error); }
        if (args.Length == 4 || args[0] == "--state-oracle")
        {
            string statePath = args[0] == "--state-oracle" ? args[1] : args[3];
            try { int steps = SessionOracle(statePath); cases.Add(new { id = "original-session-oracle", passed = true, steps }); }
            catch (Exception error) { failures.Add("original-session-oracle: " + error); }
            if (args[0] == "--state-oracle")
            {
                var stateResult = new { passed = failures.Count == 0, cases, failures };
                Save(Path.Combine(output,"summary.json"),stateResult); Console.WriteLine(JsonSerializer.Serialize(stateResult,Json)); return failures.Count == 0 ? 0 : 1;
            }
        }
        var resources = ResourceCatalog.Load(args[0]);
        using var pipeline = new SharedStructureFitPipeline(resources);
        var catalogs = new Dictionary<string, StructureCatalog>();
        var singleTargetProofs = new HashSet<string>();
        bool fullIndex = !File.Exists(Path.Combine(args[1], "index.json"));
        var inputRows = fullIndex ? Read(Path.Combine(args[1], "all-samples.json"))["samples"]!.AsArray() : Read(Path.Combine(args[1], "index.json")).AsArray();
        int inputCount = 0, unresolvedCount = 0, scannedCount = 0;
        foreach (var node in inputRows)
        {
            var item = node!.AsObject();
            if (item["season"] is { } season && season.GetValue<string>() != "s1") continue;
            inputCount++;
            string id = Text(item, "id"), mode = Text(item, "mode");
            var sourceStatus = fullIndex ? Read(Path.Combine(args[1], id, "status.json")).AsObject() : item;
            string expectedStatus = fullIndex ? Text(sourceStatus,"stage") : Text(sourceStatus,"status");
            var differences = new List<string>(); var timer = System.Diagnostics.Stopwatch.StartNew();
            try
            {
                if (DateTimeOffset.Parse(Text(item,"capturedAt")) < new DateTimeOffset(2026,11,5,0,0,0,TimeSpan.Zero)) throw new InvalidDataException("Pre-2026-11-05 capture rejected");
                using var frame = Decode(Text(item, "image"));
                var observation = FloorObservation.Observe(frame, "s1", mode, s1Use: S1FloorUse.StructureCandidate);
                string? floor = observation.FloorId;
                bool canScan = observation.MapOpen == true && floor is not null;
                bool expectedScan = expectedStatus != "current-map-or-floor-unresolved";
                string dest = Path.Combine(output, id); Directory.CreateDirectory(dest);
                Save(Path.Combine(dest, "observation.json"), observation);
                if (canScan != expectedScan) differences.Add("current-map-or-floor-admission");
                if (floor != sourceStatus["floor"]?.GetValue<string>()) differences.Add("observed-floor");
                if (!canScan)
                {
                    unresolvedCount++;
                    var unavailable = new { id, passed = differences.Count == 0, stage = "current-map-or-floor-unresolved", mapOpen = observation.MapOpen,
                        floor, differences, elapsedMs = timer.Elapsed.TotalMilliseconds };
                    Save(Path.Combine(dest,"status.json"), unavailable); cases.Add(unavailable);
                    if (differences.Count > 0) failures.Add(id+": "+string.Join(',',differences));
                    continue;
                }
                scannedCount++;
                if (!catalogs.TryGetValue(mode, out var catalog)) { catalog = StructureCatalog.FromResources(resources, mode); catalogs.Add(mode, catalog); }
                var partials = new List<JsonObject>();
                var scanner = pipeline.CreateScanner(catalog);
                var session = new StructureEvidenceSession(catalog, "recorded-replay-" + id); var ticket = session.BeginObservation(floor!);
                var report = scanner.Scan(frame, new(floor!, "current-frame-verified", OnProgress: progress => {
                    if (!session.Commit(progress, ticket)) differences.Add("partial-session-commit-rejected"); partials.Add((JsonObject)progress.DeepClone()); }));
                if (!session.Commit(report, ticket)) differences.Add("final-session-commit-rejected");
                if (report["supportedMapIds"]!.AsArray().FirstOrDefault() is { } selected && singleTargetProofs.Add(mode+"/"+floor))
                {
                    // This is an explicit-target API equivalence check after the unrestricted scan.
                    // No expected map ID/pose is supplied to the scanner or used as a fit seed.
                    string map = selected.GetValue<string>();
                    var target = resources.Find(mode,"s1","shared-structure",map,floor!)!;
                    var direct = pipeline.Fit(frame,target);
                    if (!StructureEvidence.VerifiedPose(direct) || !JsonNode.DeepEquals(direct["matrixSourceToClient"],report["poses"]![map]!["matrixSourceToClient"]))
                        differences.Add("explicit-target-fit-equivalence");
                    Save(Path.Combine(dest,"explicit-target-fit.json"),direct);
                }
                if (!expectedScan)
                {
                    Save(Path.Combine(dest,"report.json"),report); cases.Add(new { id, passed = false, stage = "unexpected-scan-admission", differences });
                    failures.Add(id+": "+string.Join(',',differences)); continue;
                }
                var expected = Read(Path.Combine(args[1], id, fullIndex ? "recognition.json" : "report.json")).AsObject();
                foreach (string key in new[] { "season", "mode", "resourceKey", "floor", "floorSource", "observedFloor", "frameKey", "status", "scanComplete", "expectedMapIds", "checkedMapIds", "unscannedMapIds", "supportedMapIds", "unknownMapIds", "excludedMapIds", "outcomes", "competitionComplete", "uniqueIdentityVerified", "identityHardGatePassed", "automaticSelectionAuthorized", "liveAuthorization", "candidateReuse", "scanSkipped" })
                    if (!JsonNode.DeepEquals(report[key], expected[key])) differences.Add(key);
                foreach (var supported in expected["supportedMapIds"]!.AsArray())
                {
                    string map = supported!.GetValue<string>();
                    if (report["poses"]?[map] is not JsonObject actualFit) continue;
                    var expectedFit = expected["poses"]![map]!;
                    foreach (string key in new[] { "validationBasis", "validationSensorBasis", "coordinatePackId", "poseVerifiedAgainstCanonicalGeometry", "profileRenderDerivedFromCanonical", "poseVerifiedAgainstSharedStructure", "poseVerifiedAgainstRenderedProfile", "routeProfileIndependent" })
                        if (!JsonNode.DeepEquals(actualFit["selectedRouteCurrentFrameResidual"]![key],expectedFit["selectedRouteCurrentFrameResidual"]![key])) differences.Add(map+":residual-provenance:"+key);
                    var a = actualFit["matrixSourceToClient"]!.Deserialize<double[][]>()!; var b = expectedFit["matrixSourceToClient"]!.Deserialize<double[][]>()!;
                    double maximum = a.SelectMany((row, y) => row.Select((value, x) => Math.Abs(value-b[y][x]))).Max();
                    if (maximum > 1e-5) differences.Add(map + ":matrix:" + maximum.ToString("R"));
                    foreach (string key in new[] { "withinThreePixels", "withinSixPixels", "explainedPixelFraction", "medianPixels" })
                    {
                        double av = actualFit["selectedRouteCurrentFrameResidual"]![key]!.GetValue<double>(), bv = expectedFit["selectedRouteCurrentFrameResidual"]![key]!.GetValue<double>();
                        if (Math.Abs(av-bv) > 1e-6) differences.Add(map + ":residual:" + key + ":" + Math.Abs(av-bv).ToString("R"));
                    }
                }
                Save(Path.Combine(dest, "report.json"), report); Save(Path.Combine(dest, "rows.json"), StructureCandidateScanner.RankRows(report, catalog));
                Save(Path.Combine(dest, "partials.json"), partials); Save(Path.Combine(dest, "session.json"), session.Snapshot());
                cases.Add(new { id, passed = differences.Count == 0, elapsedMs = timer.Elapsed.TotalMilliseconds, differences, supported = report["supportedMapIds"]?.DeepClone() });
                if (differences.Count > 0) failures.Add(id + ": " + string.Join(',', differences));
            }
            catch (Exception error) { failures.Add(id + ": " + error); cases.Add(new { id, passed = false, error = error.ToString() }); }
        }
        var summary = new { passed = failures.Count == 0, inputCount, unresolvedCount, scannedCount, cases, failures,
            scope = "Original Python production scanner parity plus pure state regressions; every S1 input retained including unresolved admission; no independent pose/map truth or native acceptance" };
        Save(Path.Combine(output, "summary.json"), summary); Console.WriteLine(JsonSerializer.Serialize(summary, Json)); return failures.Count == 0 ? 0 : 1;
    }

    private static void StateContracts()
    {
        var cat = new StructureCatalog("s1", "difficult", "test-generation", ["a", "b", "c"], ["-1", "-2", "-3", "-4"]);
        using var frame = new Mat(8, 8, MatType.CV_8UC3, Scalar.All(1));
        JsonObject Pose() => new() { ["success"] = true, ["reason"] = "shared-fit", ["matrixSourceToClient"] = new JsonArray(new JsonArray(1d,0d,0d),new JsonArray(0d,1d,0d),new JsonArray(0d,0d,1d)), ["coordinateSpace"] = "shared-structure-to-client",
            ["selectedRouteCurrentFrameResidual"] = new JsonObject { ["verified"] = true, ["poseVerifiedAgainstSharedStructure"] = true, ["withinThreePixels"] = .95 } };
        JsonObject Unknown() => new() { ["success"] = false, ["reason"] = "no-chain-ratios" };
        void Check(bool value, string reason) { if (!value) throw new InvalidDataException(reason); }
        var calls = new List<string>();
        var scanner = new StructureCandidateScanner(cat, (f, map, floor, cancel, bounds) => { Check(bounds == new Rect(0,0,8,8), "full-domain"); calls.Add(map); return map == "c" ? Unknown() : Pose(); });
        var session = new StructureEvidenceSession(cat, "real-round", 2); var ticket = session.BeginObservation("-1");
        var partials = new List<JsonObject>();
        var report = scanner.Scan(frame, new("-1", OnProgress: p => { Check(session.Commit(p, ticket), "partial-commit"); partials.Add((JsonObject)p.DeepClone()); }));
        Check(calls.SequenceEqual(cat.MapIds), "full-catalog"); Check(report["unknownMapIds"]!.AsArray().Count == 1, "failed-is-unknown");
        Check(report["excludedMapIds"]!.AsArray().Count == 0 && !report["uniqueIdentityVerified"]!.GetValue<bool>(), "no-identity-authority");
        Check(session.Commit(report, ticket), "terminal-commit"); Check(!session.Commit(partials[0], ticket), "terminal-cannot-revive");
        var choice = session.ChooseCandidate("a", ticket); Check(choice["success"]!.GetValue<bool>() && !choice["displayAuthorized"]!.GetValue<bool>(), "manual-review-only");
        choice["pose"]!["matrixSourceToClient"]![0]![0] = 99d;
        Check(session.ChooseCandidate("a", ticket)["pose"]!["matrixSourceToClient"]![0]![0]!.GetValue<double>() == 1d, "mutation-isolation");
        var next = session.BeginObservation("-2"); Check(!session.ChooseCandidate("a", ticket)["success"]!.GetValue<bool>(), "new-floor-revokes");
        Check(!session.Commit(report, next), "floor-context-bound"); Check(session.PrioritySupported("-2").Count == 0, "floor-local-hints");
        var again = session.BeginObservation("-1"); Check(session.Commit(report, again), "same-frame-commit"); Check(session.Snapshot()["observationCount"]!.GetValue<int>() == 1, "same-frame-not-votes");
        var withdraw = session.BeginObservation("-1"); Check(session.WithdrawMaterial(withdraw), "withdraw"); Check(!session.Commit(report, withdraw), "withdraw-cannot-revive");
        bool cancelled = false; var cancelSession = new StructureEvidenceSession(cat, "cancel-round"); var cancelTicket = cancelSession.BeginObservation("-1");
        var cancelledReport = scanner.Scan(frame, new("-1", Cancelled: () => cancelled, OnProgress: p => { Check(cancelSession.Commit(p, cancelTicket), "partial-before-cancel"); cancelled = true; }));
        Check(cancelledReport["status"]!.GetValue<string>() == "cancelled" && cancelledReport["supportedMapIds"]!.AsArray().Count == 0, "cancel-withdraws");
        Check(cancelSession.Commit(cancelledReport, cancelTicket) && !cancelSession.ChooseCandidate("a", cancelTicket)["success"]!.GetValue<bool>(), "cancel-terminal-revokes");
        var budgetTicks = new Queue<double>([0,0,2,2]);
        var budget = new StructureCandidateScanner(cat, (f, mid, fl, c, b) => Pose(), () => budgetTicks.Dequeue()).Scan(frame, new("-1", BudgetSeconds: 1));
        Check(Text(budget, "status") == "budget-unresolved" && budget["unscannedMapIds"]!.AsArray().Count == 2, "cooperative-budget");
        var preferred = scanner.Scan(frame, new("-1", PreferredMapId: "b", PreferredMaterialCommitted: _ => true));
        Check(Text(preferred, "status") == "preferred-material-committed" && preferred["checkedMapIds"]!.AsArray().Count == 1, "material-commit-only-short-circuit");
        var subset = scanner.Scan(frame, new("-1", PlannedMapIds: ["a"])); Check(Text(subset,"status") == "subset-unresolved", "subset-not-complete");
        var changed = (JsonObject)report.DeepClone(); changed["resourceKey"] = "another-generation";
        var invalidTicket = session.BeginObservation("-1"); Check(!session.Commit(changed, invalidTicket), "resource-context-bound");
        session.EndRound(); Check(!session.Commit(report, invalidTicket) && session.PrioritySupported("-1").Count == 0, "end-round-clears");
    }
    private static Mat Decode(string path) => Cv2.ImDecode(File.ReadAllBytes(path), ImreadModes.Color);
    private static int SessionOracle(string path)
    {
        int count = 0;
        var source = Read(path);
        if (source["poseCases"] is JsonArray poses)
            foreach (var pose in poses)
            {
                count++; if (StructureEvidence.VerifiedPose(pose!["fit"]) != pose["expected"]!.GetValue<bool>())
                    throw new InvalidDataException("verified_pose oracle: "+pose["name"]);
            }
        foreach (var item in source["cases"]!.AsArray())
        {
            var catalog = new StructureCatalog("s1","difficult","state-generation",["a","b","c"],["-1","-2","-3","-4"]);
            var session = new StructureEvidenceSession(catalog,"round-1",item!["maximum"]!.GetValue<int>());
            var tickets = new Dictionary<string,ObservationTicket>(); int stepIndex = 0;
            foreach (var step in item["steps"]!.AsArray())
            {
                count++; var operation = step!["operation"]!.AsObject(); string op = Text(operation,"op");
                JsonNode? actual = null; string? error = null;
                ObservationTicket Ticket() => tickets[Text(operation,"ticket")];
                try
                {
                    switch (op)
                    {
                        case "begin":
                            var ticket = session.BeginObservation(Text(operation,"floor")); tickets[Text(operation,"ticket")] = ticket;
                            actual = new JsonObject { ["generation"] = session.Generation, ["floor"] = ticket.Floor }; break;
                        case "commit": actual = JsonValue.Create(session.Commit(operation["report"]!.AsObject(),Ticket())); break;
                        case "choose": actual = session.ChooseCandidate(Text(operation,"mapId"),Ticket()); break;
                        case "snapshot": actual = session.Snapshot(); break;
                        case "priority": actual = StructureEvidence.Strings(session.PriorityOrder(Text(operation,"floor"))); break;
                        case "withdraw": actual = JsonValue.Create(session.WithdrawMaterial(Ticket())); break;
                        case "foreign": actual = JsonValue.Create(session.Commit(operation["report"]!.AsObject(),new("other-session","round-1",session.Generation,"-1"))); break;
                        case "revoke": session.RevokeCurrent(); break;
                        case "end": session.EndRound(); break;
                        default: throw new InvalidDataException("Unknown oracle operation: "+op);
                    }
                }
                catch (Exception exception) when (exception is ArgumentException or InvalidOperationException) { error = exception.Message; }
                if (step["expectedError"] is { } expectedError)
                { if (error != expectedError.GetValue<string>()) throw new InvalidDataException($"{item["name"]} step {stepIndex}: error {error}"); }
                else if (error is not null || !JsonNode.DeepEquals(actual,step["expected"]))
                    throw new InvalidDataException($"{item["name"]} step {stepIndex}: expected {step["expected"]?.ToJsonString()}, actual {actual?.ToJsonString()}, error {error}");
                stepIndex++;
            }
        }
        return count;
    }
    private static string Text(JsonObject obj, string key) => obj[key]!.GetValue<string>();
    private static JsonNode Read(string path) => JsonNode.Parse(File.ReadAllBytes(path))!;
    private static void Save(string path, object? value) => File.WriteAllText(path, JsonSerializer.Serialize(value, Json));
}
