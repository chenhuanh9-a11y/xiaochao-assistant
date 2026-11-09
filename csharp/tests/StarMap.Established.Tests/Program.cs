using System.Text.Json;
using System.Text.Json.Nodes;
using OpenCvSharp;
using StarMap.Core.Identity;
using StarMap.Core.Resources;
using StarMap.Core.Selection;
using StarMap.Vision;
using StarMap.Vision.Steady;

if (args.Length != 3) throw new ArgumentException("RESOURCE_ROOT ORIGINAL_PROBE_CASES NEW_OUTPUT");
string resources = Path.GetFullPath(args[0]), output = Path.GetFullPath(args[2]);
if (Directory.Exists(output)) throw new IOException("Preserve previous evidence; choose a new output.");
Directory.CreateDirectory(output);
var input = Read(args[1]); var catalog = ResourceCatalog.Load(resources);
var results = new JsonArray(); var failures = new List<string>();
foreach (var source in input["cases"]!.AsArray())
{
    string id = Text(source!, "id"), mode = Text(source!, "mode"), map = Text(source!, "mapId");
    string dest = Path.Combine(output, id); Directory.CreateDirectory(dest);
    try
    {
        var passport = EntryPassportCatalog.Load(resources, "s0", mode);
        var target = catalog.Find(mode, "s0", passport.MapProfiles[map], map, "1") ?? throw new InvalidDataException("Seed reference absent.");
        using var seed = Decode(source!["seed"]!);
        using var coldHost = new S0HostFit(catalog, passport);
        var cold = coldHost.Fit(seed, target);
        Save(Path.Combine(dest, "cold-fit.json"), cold.Fit);
        if (!StructureEvidence.VerifiedPose(cold.Fit) || cold.Reference is null || cold.CanonicalPackId is null)
            throw new InvalidDataException("Own current-run cold rejected; expected pose cannot substitute.");
        var matrix = cold.Fit["matrixSourceToClient"]!.Deserialize<double[][]>()!;
        var seedDifferences = new Comparison();
        seedDifferences.Compare(source["seedExpectedFit"]!["matrixSourceToClient"], JsonSerializer.SerializeToNode(matrix), "seed.matrix");
        if (seedDifferences.Errors.Count != 0) throw new InvalidDataException("Own cold differs: " + seedDifferences.Errors[0]);
        var coverage = Idvb0059Steady.ObserveCoverage(seed, cold.Reference, matrix);
        object owner = new(); const long epoch = 1;
        var steps = new JsonArray(); int ordinal = 0;
        foreach (var step in source["steps"]!.AsArray())
        {
            foreach (var originalCall in step!["solveCalls"]!.AsArray())
            {
                string stepId = Text(step, "id"), stepDest = Path.Combine(dest, $"step-{ordinal++:D3}-{stepId}");
                Directory.CreateDirectory(stepDest);
                using var frame = Decode(step["raw"]!);
                var observed = FloorObservation.Observe(frame, "s0", mode);
                string floor = observed.FloorId ?? throw new InvalidDataException("Own floor unavailable.");
                if (floor != Text(originalCall!, "floor") || floor != target.Floor)
                    throw new InvalidDataException("A target floor must be independently prepared; this suite contains same-floor recovery only.");
                var context = new EstablishedGroupContext(map, floor, "shared-structure", Clone(matrix),
                    new HashSet<string> { "1" }, new HashSet<string> { "1" }, new HashSet<string> { "1" });
                var prior = new S0HostPosePrior(owner, epoch, target, Clone(matrix));
                var state = new S0HostGroupState(owner, epoch, mode, "s0", map, "shared-structure",
                    context.VerifiedFloors, context.RelationFloors, prior, context);
                var options = new S0HostFitOptions { OwnerScope = owner, Epoch = epoch, Group = state };
                var call = new S0HostStageContext(frame, target, cold.Reference, cold.CanonicalPackId, null, options, () => false);
                var numerical = coldHost.EvaluateEstablishedGroup(call);
                Save(Path.Combine(stepDest, "actual.json"), new JsonObject {
                    ["fit"] = numerical.Fit.DeepClone(), ["diagnostics"] = numerical.Diagnostics.DeepClone(),
                    ["inputSource"] = "own-current-run-cold", ["baseMatrix"] = JsonSerializer.SerializeToNode(matrix),
                    ["floorObservation"] = observed.FloorRead.DeepClone() });
                var compared = new Comparison();
                compared.Compare(originalCall!["fit"], numerical.Fit, "fit");
                compared.Compare(originalCall["diagnostics"], numerical.Diagnostics, "diagnostics");
                bool configured = step["configuration"]?["fastEnabled"]?.GetValue<bool>() == false;
                var steadyEvidence = new JsonArray();
                var services = new S0HostFitServices(Steady: stage => {
                    // The default oracle reached recovery only after a real0059
                    // miss. An unexpected current C# acceptance is a divergence,
                    // never replaced with null to force the recovery test.
                    var proposed = Idvb0059Steady.Solve(stage.Frame, stage.Reference, matrix[0][0], coverage);
                    steadyEvidence.Add(new JsonObject { ["accepted"] = proposed.Accepted, ["reason"] = proposed.Reason,
                        ["diagnostics"] = proposed.Diagnostics.DeepClone() });
                    if (proposed.Accepted) throw new InvalidDataException("Own0059 accepted where original default Host reached recovery.");
                    return null;
                });
                using var host = new S0HostFit(catalog, passport, services, entry =>
                    ReferenceEquals(entry, target) ? new(cold.Reference, null) : catalog.LoadSharedStructure(entry));
                var full = host.Fit(frame, target, options: options with { SteadyRequested = !configured, FastAlignmentEnabled = !configured });
                Save(Path.Combine(stepDest, "actual-host.json"), new JsonObject { ["fit"] = full.Fit.DeepClone(),
                    ["stages"] = full.Stages.DeepClone(), ["events"] = full.Events.DeepClone(), ["steady"] = steadyEvidence });
                compared.Compare(step["fit"], full.Fit, "host.fit");
                if (!full.Stages.Any(stage => stage?["stage"]?.ToString()?.StartsWith("established-", StringComparison.Ordinal) == true))
                    compared.Errors.Add("Actual Host did not execute established recovery.");
                if (coldHost.EvaluateEstablishedGroup(call with { Cancelled = () => true }).Fit["reason"]?.ToString() != "structure-work-obsolete")
                    compared.Errors.Add("Cancelled proposal not rejected.");
                if (coldHost.EvaluateEstablishedGroup(call with { Options = options with { OwnerScope = new() } }).Fit["reason"]?.ToString() != "s0-host-group-context-mismatch")
                    compared.Errors.Add("Foreign owner was admitted.");
                Save(Path.Combine(stepDest, "comparison.json"), JsonSerializer.SerializeToNode(new {
                    passed = compared.Errors.Count == 0, errors = compared.Errors, scalarCount = compared.ScalarCount,
                    exactScalarCount = compared.ExactScalarCount, numericDifferences = compared.Differences,
                    scope = configured ? "Supported fast/0059-disabled original Host path; not a default-path positive claim"
                        : "Actual default Host: own0059 miss, fast, established current-frame producer and final residual" })!);
                bool passed = compared.Errors.Count == 0;
                if (!passed) failures.Add(id + "/" + stepId + ": " + compared.Errors[0]);
                steps.Add(new JsonObject { ["id"] = stepId, ["passed"] = passed, ["configuredRecovery"] = configured,
                    ["success"] = numerical.Fit["success"]?.DeepClone(), ["errors"] = JsonSerializer.SerializeToNode(compared.Errors) });
            }
        }
        results.Add(new JsonObject { ["id"] = id, ["steps"] = steps });
    }
    catch (Exception error) { failures.Add(id + ": " + error); Save(Path.Combine(dest, "error.json"), new JsonObject { ["error"] = error.ToString() }); }
}
Save(Path.Combine(output, "summary.json"), JsonSerializer.SerializeToNode(new { passed = failures.Count == 0, failures, cases = results,
    scope = "Original saved-frame numerical/Host recovery replay. Own cold input only; no expected transform seed, Runtime/GUI/live authorization or unique identity claim.",
    tolerance = "abs1e-5+rel1e-8; every unequal scalar retained independently in comparison files; timing keys excluded" })!);
Console.WriteLine(JsonSerializer.Serialize(new { passed = failures.Count == 0, failures = failures.Select(f => f.Split('\n')[0]), output }));
return failures.Count == 0 ? 0 : 1;

static JsonNode Read(string path) => JsonNode.Parse(File.ReadAllBytes(path))!;
static string Text(JsonNode value, string key) => value[key]!.GetValue<string>();
static double[][] Clone(double[][] matrix) => matrix.Select(row => row.ToArray()).ToArray();
static void Save(string path, JsonNode value) => File.WriteAllText(path, value.ToJsonString(new JsonSerializerOptions { WriteIndented = true }));
static Mat Decode(JsonNode raw)
{
    string recordPath = Path.GetFullPath(Text(raw, "captureRecord")), image = Path.GetFullPath(Text(raw, "image"));
    var record = Read(recordPath);
    if (Path.GetFullPath(Path.Combine(Path.GetDirectoryName(recordPath)!, Text(record, "samplePath"))) != image)
        throw new InvalidDataException("Original capture path mismatch.");
    var stamp = DateTimeOffset.Parse(Text(record, "capturedAt"));
    if (stamp < new DateTimeOffset(2026, 11, 5, 0, 0, 0, TimeSpan.Zero) || stamp != DateTimeOffset.Parse(Text(raw, "capturedAt")))
        throw new InvalidDataException("Old or mismatched capture time.");
    if (!JsonNode.DeepEquals(record["Metadata"]!["Before"], raw["physicalClient"]) || !JsonNode.DeepEquals(record["Metadata"]!["After"], raw["physicalClient"])
        || !JsonNode.DeepEquals(record["userLabel"], record["labelAtCommit"]) || record["labelIsUserSupplied"]?.GetValue<bool>() != true
        || record["userLabel"]!["Season"]!.GetValue<string>() != Text(raw, "season")
        || record["userLabel"]!["Mode"]!.GetValue<string>() != Text(raw, "mode"))
        throw new InvalidDataException("Capture geometry or user mode proof mismatch.");
    var frame = Cv2.ImDecode(File.ReadAllBytes(image), ImreadModes.Color);
    if (frame.Cols != raw["physicalClient"]!["Width"]!.GetValue<int>() || frame.Rows != raw["physicalClient"]!["Height"]!.GetValue<int>())
    { frame.Dispose(); throw new InvalidDataException("Actual PNG disagrees with physical client."); }
    return frame;
}

sealed class Comparison
{
    static readonly HashSet<string> Timings = ["elapsedMs", "elapsedSeconds", "milliseconds", "timingMs", "totalMs"];
    public List<string> Errors { get; } = new();
    public List<object> Differences { get; } = new();
    public int ScalarCount { get; private set; }
    public int ExactScalarCount { get; private set; }
    public void Compare(JsonNode? expected, JsonNode? actual, string path)
    {
        if (expected is JsonObject e && actual is JsonObject a)
        {
            foreach (var key in e.Select(p => p.Key).Union(a.Select(p => p.Key)))
            {
                if (Timings.Contains(key)) continue;
                if (!e.ContainsKey(key) || !a.ContainsKey(key)) { Errors.Add(path + "." + key + ": key mismatch"); continue; }
                Compare(e[key], a[key], path + "." + key);
            }
            return;
        }
        if (expected is JsonArray ea && actual is JsonArray aa)
        {
            if (ea.Count != aa.Count) Errors.Add(path + ": array length mismatch");
            for (int i = 0; i < Math.Min(ea.Count, aa.Count); i++) Compare(ea[i], aa[i], path + "[" + i + "]");
            return;
        }
        if (expected is JsonValue ev && actual is JsonValue av && Number(ev, out double en) && Number(av, out double an))
        {
            ScalarCount++;
            if (BitConverter.DoubleToInt64Bits(en) == BitConverter.DoubleToInt64Bits(an)) ExactScalarCount++;
            else
            {
                double difference = Math.Abs(en - an); bool within = difference <= 1e-5 + 1e-8 * Math.Abs(en);
                Differences.Add(new { path, expected = en, actual = an, absoluteDifference = difference, withinTolerance = within });
                if (!within) Errors.Add(path + ": numeric mismatch");
            }
            return;
        }
        if (!JsonNode.DeepEquals(expected, actual)) Errors.Add(path + ": value/type mismatch");
    }
    static bool Number(JsonValue value, out double number)
    {
        if (value.TryGetValue<double>(out number)) return true;
        if (value.TryGetValue<int>(out int integer)) { number = integer; return true; }
        if (value.TryGetValue<long>(out long wide)) { number = wide; return true; }
        number = 0; return false;
    }
}
