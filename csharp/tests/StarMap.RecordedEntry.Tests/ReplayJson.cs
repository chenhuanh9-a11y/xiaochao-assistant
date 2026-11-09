using System.Globalization;
using System.Text.Json;
using System.Text.Json.Nodes;
using System.Text.Json.Serialization;

internal static class ReplayJson
{
    internal static readonly JsonSerializerOptions Pretty = new()
    {
        WriteIndented = true, NumberHandling = JsonNumberHandling.AllowNamedFloatingPointLiterals
    };
    internal static JsonNode Read(string path) => JsonNode.Parse(File.ReadAllBytes(path)) ?? throw new InvalidDataException("Empty JSON: " + path);
    internal static string Text(JsonObject row, string key) => row[key]?.GetValue<string>() ?? throw new InvalidDataException("Missing " + key);
    internal static bool Flag(JsonNode? row, string key) => row?[key] is JsonValue value && value.TryGetValue<bool>(out bool flag) && flag;
    internal static double Number(JsonNode? value) => value is null ? 0 : double.Parse(value.ToString(), CultureInfo.InvariantCulture);
    internal static void Save(string path, object? value) => File.WriteAllText(path, JsonSerializer.Serialize(value, Pretty));
    internal static JsonArray Strings(IEnumerable<string> values) => new(values.Select(value => (JsonNode)JsonValue.Create(value)!).ToArray());
    internal static JsonObject Project(JsonObject source, IEnumerable<string> fields)
    {
        var result = new JsonObject();
        foreach (string key in fields) if (source.ContainsKey(key)) result[key] = source[key]?.DeepClone();
        return result;
    }
    internal static bool IsTiming(string key) => key.EndsWith("Ms", StringComparison.Ordinal) || key == "elapsedSeconds";
    internal static void Difference(List<JsonObject> differences, string path, string kind, JsonNode? expected, JsonNode? actual)
        => differences.Add(new JsonObject { ["path"] = path, ["kind"] = kind, ["expected"] = expected?.DeepClone(), ["actual"] = actual?.DeepClone() });

    // Independent expected fields are mandatory; additional C# diagnostics are
    // retained in actual.json. Timing is recorded but never compared.
    internal static void Compare(string path, JsonNode? expected, JsonNode? actual, List<JsonObject> differences,
        double absoluteTolerance = 1e-9, double relativeTolerance = 1e-12)
    {
        if (expected is JsonObject left && actual is JsonObject right)
        {
            foreach (var pair in left)
            {
                if (IsTiming(pair.Key)) continue;
                string child = path + "/" + pair.Key.Replace("~", "~0").Replace("/", "~1");
                if (!right.ContainsKey(pair.Key)) Difference(differences, child, "missing-field", pair.Value, null);
                else Compare(child, pair.Value, right[pair.Key], differences, absoluteTolerance, relativeTolerance);
            }
        }
        else if (expected is JsonArray a && actual is JsonArray b)
        {
            if (a.Count != b.Count) Difference(differences, path, "array-cardinality", JsonValue.Create(a.Count), JsonValue.Create(b.Count));
            for (int i = 0; i < Math.Min(a.Count, b.Count); i++) Compare(path + "/" + i, a[i], b[i], differences, absoluteTolerance, relativeTolerance);
        }
        else if (expected?.GetValueKind() == JsonValueKind.Number && actual?.GetValueKind() == JsonValueKind.Number)
        {
            double x = Number(expected), y = Number(actual), tolerance = Math.Max(absoluteTolerance, Math.Abs(x) * relativeTolerance);
            if (!double.IsFinite(x) || !double.IsFinite(y) || Math.Abs(x - y) > tolerance)
            {
                Difference(differences, path, "numeric", expected, actual);
                differences[^1]["delta"] = y - x; differences[^1]["tolerance"] = tolerance;
            }
        }
        else if (!JsonNode.DeepEquals(expected, actual)) Difference(differences, path, "value", expected, actual);
    }
}
