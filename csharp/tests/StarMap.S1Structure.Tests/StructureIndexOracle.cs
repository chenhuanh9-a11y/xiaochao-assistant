using System.Text.Json;
using System.Text.Json.Nodes;
using OpenCvSharp;
using StarMap.Core.Resources;
using StarMap.Core.Selection;
using StarMap.Vision;

internal static class StructureIndexOracle
{
    internal static int Run(string resources, string oracle, string output,bool sharedPreparation=false)
    {
        if (Directory.Exists(output)) throw new IOException("Preserve previous evidence; use a new output.");
        Directory.CreateDirectory(output); StructurePoolRegression.Run();
        var source = JsonNode.Parse(File.ReadAllBytes(oracle))!;
        var catalog = ResourceCatalog.Load(resources);
        var results = new JsonArray(); var filters = new Dictionary<string, StructureIndexFilter>();
        try
        {
            int number = 0;
            foreach (var row in source["cases"]!.AsArray())
            {
                string mode = row!["input"]!["mode"]!.GetValue<string>();
                if (!filters.TryGetValue(mode, out var filter))
                    filters.Add(mode, filter = new(catalog, StructureCatalog.FromResources(catalog, mode), row["assetRoot"]!.GetValue<string>()));
                var errors = new List<string>();
                if (DateTimeOffset.Parse(row["input"]!["capturedAt"]!.GetValue<string>()) < new DateTimeOffset(2026, 11, 5, 0, 0, 0, TimeSpan.Zero))
                    throw new InvalidDataException("Pre-2026-11-05 capture rejected");
                using var frame = Cv2.ImDecode(File.ReadAllBytes(row["input"]!["image"]!.GetValue<string>()), ImreadModes.Color);
                var expected = row["expected"]!;
                var ids = row["inputMapIds"]!.AsArray().Select(id => id!.GetValue<string>()).ToArray();
                var timer = System.Diagnostics.Stopwatch.StartNew();
                using var prepared=sharedPreparation?new StructureFramePreparation(frame):null;
                Func<bool> cancelled=()=>row["cancelled"]?.GetValue<bool>()==true;
                var actual = prepared is null?filter.Evaluate(frame, ids, row["floor"]!.GetValue<string>(),cancelled)
                    :filter.Evaluate(prepared,ids,row["floor"]!.GetValue<string>(),filter.DetectAnchor(prepared,cancelled),cancelled);
                double elapsed = timer.Elapsed.TotalMilliseconds;
                foreach (string key in new[] { "mode", "resourceKey", "floor", "reason", "coordinateVerified", "evaluatedMapIds", "contradictedMapIds",
                    "unknownMapIds", "gateStatus", "gateReason", "candidateChecks", "poseAvailable", "matrixEntranceRendererToClient", "coordinateEvidence" })
                    Compare(expected[key], actual[key], key, errors);
                File.WriteAllText(Path.Combine(output, (++number).ToString("D3") + "-report.json"), actual.ToJsonString(Json));
                results.Add(JsonSerializer.SerializeToNode(new { id = row["id"]!.GetValue<string>(), passed = errors.Count == 0, elapsedMs = elapsed, differences = errors }));
                SaveSummary();
            }
            return results.All(row => row!["passed"]!.GetValue<bool>()) ? 0 : 1;
        }
        finally { foreach (var filter in filters.Values) filter.Dispose(); }
        void SaveSummary() => File.WriteAllText(Path.Combine(output, "summary.json"), new JsonObject {
            ["passed"] = results.Count == source["cases"]!.AsArray().Count && results.All(row => row!["passed"]!.GetValue<bool>()), ["count"] = results.Count,
            ["expectedCount"] = source["cases"]!.AsArray().Count, ["cases"] = results.DeepClone(),
            ["scope"] = "Actual original comparator inputs and reports; same current PNG/resources/index; no pose answers supplied to solver; pool policy tested separately; offline only"
        }.ToJsonString(Json));
    }
    static readonly JsonSerializerOptions Json = new() { WriteIndented = true };
    static void Compare(JsonNode? expected, JsonNode? actual, string path, List<string> errors)
    {
        if (expected is JsonObject left && actual is JsonObject right)
        { foreach (var item in left) Compare(item.Value, right[item.Key], path + "." + item.Key, errors); return; }
        if (expected is JsonArray a && actual is JsonArray b && a.Count == b.Count)
        { for (int i = 0; i < a.Count; i++) Compare(a[i], b[i], path + "[" + i + "]", errors); return; }
        if (expected is JsonValue x && actual is JsonValue y && x.GetValueKind() == JsonValueKind.Number && y.GetValueKind() == JsonValueKind.Number
            && Math.Abs(double.Parse(x.ToJsonString(), System.Globalization.CultureInfo.InvariantCulture)
                - double.Parse(y.ToJsonString(), System.Globalization.CultureInfo.InvariantCulture)) <= 1e-6) return;
        if (!JsonNode.DeepEquals(expected, actual)) errors.Add(path + ": " + expected?.ToJsonString() + " != " + actual?.ToJsonString());
    }
}
