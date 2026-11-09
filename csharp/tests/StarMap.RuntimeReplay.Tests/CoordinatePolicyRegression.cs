using System.Text.Json;
using System.Text.Json.Nodes;
using System.Reflection;
using StarMap.Core.Alignment;
using StarMap.Core.Display;
using StarMap.Runtime;

internal static class CoordinatePolicyRegression
{
    internal static void Run(string originalOracle, string output, Action<bool, string> check)
    {
        var input = JsonNode.Parse(File.ReadAllBytes(originalOracle))!; var results = new JsonArray();
        foreach (var row in input["cases"]!.AsArray())
        {
            string id = row!["id"]!.GetValue<string>();
            var adapter = new AuthorCoordinateAdapter(JsonSerializer.SerializeToElement(input["documents"]![row["document"]!.GetValue<string>()]));
            var source = Matrix3.TryRead(JsonSerializer.SerializeToElement(row["source"])) ?? throw new InvalidDataException("Oracle input must be finite 3x3.");
            var actual = adapter.CrossFloorSearchSeed(row["mapId"]!.GetValue<string>(), row["fromFloor"]!.GetValue<string>(), row["toFloor"]!.GetValue<string>(),
                source, row["profile"]!.GetValue<string>());
            double maximumDifference = 0; int scalars = 0;
            bool equal = Equivalent(row["expected"], actual);
            results.Add(new JsonObject { ["id"] = id, ["actual"] = actual?.DeepClone(), ["passed"] = equal,
                ["scalarCount"] = scalars, ["maximumAbsoluteDifference"] = maximumDifference, ["provenance"] = row["provenance"]!.DeepClone() });
            File.WriteAllText(Path.Combine(output, "coordinate-policy.json"), results.ToJsonString(new JsonSerializerOptions { WriteIndented = true }));
            check(equal, id + " original static coordinate policy matches (proposal only, no live/display authorization)");
            bool Equivalent(JsonNode? a, JsonNode? b)
            {
                if (a is JsonObject ao && b is JsonObject bo) return ao.Count == bo.Count && ao.All(pair => bo.ContainsKey(pair.Key) && Equivalent(pair.Value, bo[pair.Key]));
                if (a is JsonArray aa && b is JsonArray ba) return aa.Count == ba.Count && aa.Zip(ba).All(pair => Equivalent(pair.First, pair.Second));
                if (a is JsonValue av && b is JsonValue bv && av.TryGetValue<double>(out var x) && bv.TryGetValue<double>(out var y))
                { scalars++; maximumDifference = Math.Max(maximumDifference, Math.Abs(x-y)); return Math.Abs(x-y) <= 1e-12; }
                return JsonNode.DeepEquals(a, b);
            }
        }
        var graphResults = new JsonArray();
        foreach (var row in input["graphCases"]?.AsArray() ?? [])
        {
            string id = row!["id"]!.GetValue<string>();
            var adapter = new AuthorCoordinateAdapter(JsonSerializer.SerializeToElement(input["documents"]![row["document"]!.GetValue<string>()]));
            var group = new RoundLayerGroup(row["mapId"]!.GetValue<string>(), row["profile"]!.GetValue<string>(),
                row["anchor"]!.GetValue<string>(), Matrix3.Identity);
            // Observe the production graph writer, including its edge admission,
            // rather than reimplementing the policy in this test.
            typeof(LiveRuntime).GetMethod("PrimeRelations", BindingFlags.Static | BindingFlags.NonPublic)!.Invoke(null, [group, adapter]);
            var actual = JsonSerializer.SerializeToNode(group.Relations.ToDictionary(pair => pair.Key, pair => pair.Value.ToArray()))!;
            bool same = group.Relations.Count == row["relations"]!.AsObject().Count
                && group.Relations.All(pair => row["relations"]![pair.Key]?.Deserialize<double[][]>() is { } expected
                    && pair.Value.ToArray().SelectMany(value => value).Zip(expected.SelectMany(value => value))
                        .All(values => Math.Abs(values.First-values.Second) <= 1e-12));
            graphResults.Add(new JsonObject { ["id"] = id, ["actual"] = actual, ["passed"] = same });
            File.WriteAllText(Path.Combine(output, "coordinate-graph.json"), graphResults.ToJsonString(new JsonSerializerOptions { WriteIndented = true }));
            check(same, id + " actual graph admission and reachable target relations match original");
        }
        var before = new RoundLayerGroup("copy-fixture", "shared-structure", "1", Matrix3.Identity, floors: ["1", "2"]);
        before.MarkGeometryVerified("1", true, false); var snapshot = JsonSerializer.Serialize(before.Snapshot());
        var staged = before.Copy(); staged.ApplyTranslation(12, -7, "candidate", new(1));
        staged.SetRelation("2", Matrix3.Identity); staged.MarkGeometryVerified("2", true, false);
        check(JsonSerializer.Serialize(before.Snapshot()) == snapshot && before.DisplayedOverlay is null && before.DisplayedFloor is null,
            "staging-group-root-relations-and-proofs-does-not-mutate-predecessor-or-fake-displayed-pixels");
    }
}
