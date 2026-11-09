using System.Reflection;
using System.Text.Json;
using System.Text.Json.Nodes;
using OpenCvSharp;
using StarMap.Core.Identity;
using StarMap.Core.Resources;
using StarMap.Vision;

internal static class EstablishedIdentityChecks
{
    internal static int Run(string root, string originalOracle, string output)
    {
        output = Path.GetFullPath(output);
        if (Directory.Exists(output)) throw new IOException("Preserve previous evidence; use a new output.");
        Directory.CreateDirectory(output);
        var oracle = JsonNode.Parse(File.ReadAllBytes(originalOracle))!;
        string directory = Path.GetDirectoryName(Path.GetFullPath(originalOracle))!;
        var resources = ResourceCatalog.Load(root);
        var pipelines = new Dictionary<string, S0IdentityRankPipeline>(StringComparer.Ordinal);
        var results = new JsonArray(); int failures = 0;
        foreach (var row in oracle["cases"]!.AsArray())
        {
            string id = Text(row!, "id"), mode = Text(row!, "mode");
            string destination = Path.Combine(output, id); Directory.CreateDirectory(destination);
            var differences = new JsonArray(); var unequalScalars = new JsonArray();
            try
            {
                if (!pipelines.TryGetValue(mode, out var pipeline))
                    pipelines[mode] = pipeline = new(resources, EntryPassportCatalog.Load(root, "s0", mode));
                var raw = row!["rawCapture"]!;
                if (DateTimeOffset.Parse(Text(raw, "capturedAt")) < new DateTimeOffset(2026, 11, 5, 0, 0, 0, TimeSpan.Zero)
                    || Text(raw, "season") != "s0" || Text(raw, "mode") != mode)
                    throw new InvalidDataException("Current capture provenance does not match the declared input mode.");
                using var frame = Cv2.ImDecode(File.ReadAllBytes(Text(raw, "image")), ImreadModes.Color);
                if (frame.Empty() || frame.Width != raw["physicalClient"]!["Width"]!.GetValue<int>()
                    || frame.Height != raw["physicalClient"]!["Height"]!.GetValue<int>())
                    throw new InvalidDataException("Decoded input does not match original physical geometry.");
                if (pipeline.NativeResourceKey != Text(row["resourceScope"]!, "indexDigest"))
                    throw new InvalidDataException("Actual index differs from original component resource scope.");
                var input = row["typedInput"]!; NativeConditionPrior? prior = null;
                if (row["priorSource"]?["tuple"] is JsonArray tuple)
                {
                    var domains = new JsonObject();
                    foreach (var binding in tuple[3]!.AsArray())
                        domains[binding![0]!.GetValue<string>()] = binding[1]!.DeepClone();
                    // Explicit component input seam. Only real catalog condition
                    // IDs scope the tested retrieval; no expected map/pose enters.
                    prior = (NativeConditionPrior)typeof(NativeConditionPrior)
                        .GetConstructors(BindingFlags.NonPublic | BindingFlags.Instance).Single()
                        .Invoke([Context(input["priorContext"]!), domains,
                            input["priorityMapIds"]!.Deserialize<string[]>()!]);
                }
                var result = pipeline.RankEstablished(frame, Text(row, "requestedFloor"), prior,
                    Context(input["requestContext"]!), TimeSpan.FromSeconds(row["budgetSeconds"]!.GetValue<double>()));
                var actual = result.Rows;
                File.WriteAllText(Path.Combine(destination, "actual-rows.json"), actual.ToJsonString(Pretty));
                File.WriteAllText(Path.Combine(destination, "actual-evidence.json"), result.Evidence.ToJsonString(Pretty));
                // Open the independent expected output only after production ran.
                var expected = JsonNode.Parse(File.ReadAllBytes(Path.Combine(directory, Text(row["expected"]!, "rankRowsPath"))));
                // Original pose retrieval iterates a Python set. These two
                // collections are consumed by their explicit IDs, not position.
                // Preserve every payload/cardinality and reject duplicate IDs;
                // raw production rows remain saved above without reordering.
                Compare(CanonicalNative(expected), CanonicalNative(actual), "rows");
            }
            catch (Exception error) { differences.Add(new JsonObject { ["exception"] = error.ToString() }); }
            File.WriteAllText(Path.Combine(destination, "differences.json"), differences.ToJsonString(Pretty));
            File.WriteAllText(Path.Combine(destination, "unequal-scalars.json"), unequalScalars.ToJsonString(Pretty));
            bool passed = differences.Count == 0; if (!passed) failures++;
            results.Add(new JsonObject { ["id"] = id, ["passed"] = passed, ["differences"] = differences.Count, ["unequalScalars"] = unequalScalars.Count });
            void Compare(JsonNode? expected, JsonNode? actual, string path)
            {
                if (expected is JsonObject a && actual is JsonObject b)
                {
                    foreach (string key in a.Select(pair => pair.Key).Union(b.Select(pair => pair.Key), StringComparer.Ordinal))
                    {
                        if (key.EndsWith("Ms", StringComparison.Ordinal) || key == "elapsedSeconds") continue;
                        if (a.ContainsKey(key) != b.ContainsKey(key))
                        {
                            differences.Add(new JsonObject { ["path"] = path + "." + key,
                                ["expectedPresent"] = a.ContainsKey(key), ["actualPresent"] = b.ContainsKey(key) });
                            continue;
                        }
                        Compare(a[key], b[key], path + "." + key);
                    }
                }
                else if (expected is JsonArray ea && actual is JsonArray aa)
                {
                    if (ea.Count != aa.Count) differences.Add(new JsonObject { ["path"] = path, ["expectedCount"] = ea.Count, ["actualCount"] = aa.Count });
                    for (int i = 0; i < Math.Min(ea.Count, aa.Count); i++) Compare(ea[i], aa[i], path + "[" + i + "]");
                }
                else if (expected is JsonValue ev && actual is JsonValue av && ev.TryGetValue<double>(out double x) && av.TryGetValue<double>(out double y))
                {
                    if (x != y) unequalScalars.Add(new JsonObject { ["path"] = path, ["expected"] = x, ["actual"] = y, ["absoluteDifference"] = Math.Abs(x-y) });
                    if (Math.Abs(x-y) > 1e-7 + Math.Abs(x)*1e-8) differences.Add(new JsonObject { ["path"] = path, ["expected"] = x, ["actual"] = y });
                }
                else if (!JsonNode.DeepEquals(expected, actual)) differences.Add(new JsonObject { ["path"] = path,
                    ["expected"] = expected?.DeepClone(), ["actual"] = actual?.DeepClone() });
            }
        }
        File.WriteAllText(Path.Combine(output, "summary.json"), new JsonObject { ["cases"] = results,
            ["passed"] = results.Count > 0 && failures == 0,
            ["scope"] = "Original reviewed-cohort rank component with synthetic prior from real catalog IDs and actual current PNGs; no runtime prior-establishment, unique map truth, GUI/game or default timing acceptance. Every unequal non-timing scalar is retained."
        }.ToJsonString(Pretty));
        Console.WriteLine($"Established identity component: {results.Count-failures}/{results.Count}; {output}");
        return failures == 0 && results.Count > 0 ? 0 : 1;
    }
    private static readonly JsonSerializerOptions Pretty = new() { WriteIndented = true };
    private static JsonNode? CanonicalNative(JsonNode? source)
    {
        var copy = source?.DeepClone(); Walk(copy); return copy;
        static void Walk(JsonNode? node)
        {
            if (node is JsonObject row)
            {
                if (row["conditionObservations"] is JsonArray observations)
                {
                    var ids = observations.Select(value => (Text(value!, "domainId"), Text(value!, "conditionId"))).ToArray();
                    if (ids.Distinct().Count() != ids.Length) throw new InvalidDataException("Duplicate condition observation IDs.");
                    foreach (var observation in observations)
                    {
                        if (observation?["pairs"] is not JsonArray pairs) continue;
                        var keys = pairs.Select(value => (Text(value!, "poseA"), Text(value!, "poseB"), Text(value!, "viewerPoseId"))).ToArray();
                        if (keys.Distinct().Count() != keys.Length) throw new InvalidDataException("Duplicate condition pair IDs.");
                        observation["pairs"] = new JsonArray(pairs.OrderBy(value => Text(value!, "poseA"), StringComparer.Ordinal)
                            .ThenBy(value => Text(value!, "poseB"), StringComparer.Ordinal).ThenBy(value => Text(value!, "viewerPoseId"), StringComparer.Ordinal)
                            .Select(value => value!.DeepClone()).ToArray());
                    }
                    row["conditionObservations"] = new JsonArray(observations.OrderBy(value => Text(value!, "domainId"), StringComparer.Ordinal)
                        .ThenBy(value => Text(value!, "conditionId"), StringComparer.Ordinal).Select(value => value!.DeepClone()).ToArray());
                }
                foreach (var child in row.Select(pair => pair.Value).ToArray()) Walk(child);
            }
            else if (node is JsonArray array) foreach (var child in array) Walk(child);
        }
    }
    private static string Text(JsonNode node, string key) => node[key]!.GetValue<string>();
    private static NativeIdentityContext Context(JsonNode value) => new(Text(value, "season"), Text(value, "mode"),
        Text(value, "roundId"), Text(value, "floor"), Text(value, "resourceKey"), value["mapId"]?.GetValue<string>());
}
