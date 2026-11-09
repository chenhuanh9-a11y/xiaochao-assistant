using System.Globalization;
using System.Text.Json;
using System.Text.Json.Nodes;

namespace StarMap.Core.Identity;

public static class NativeIdentityJson
{
    public static string Text(JsonNode? node, string key) => node?[key]?.ToString() ?? "";
    public static bool Flag(JsonNode? node, string key) => node?[key] is JsonValue value && value.TryGetValue<bool>(out bool b) && b;
    public static double Number(JsonNode? node) => node is null ? 0 : double.Parse(node.ToString(), CultureInfo.InvariantCulture);
    public static JsonArray Array(JsonNode? node) => node as JsonArray ?? new();
    public static string[] Strings(JsonNode? node) => Array(node).Select(value => value!.ToString()).ToArray();
    public static JsonArray Strings(IEnumerable<string> values) => new(values.Select(value => (JsonNode)JsonValue.Create(value)!).ToArray());
    public static JsonObject Project(JsonNode? source, params string[] keys)
    {
        var result = new JsonObject(); if (source is not JsonObject row) return result;
        foreach (string key in keys) if (row.ContainsKey(key)) result[key] = row[key]?.DeepClone(); return result;
    }
    public static JsonObject Object(JsonElement value) => JsonNode.Parse(value.GetRawText())!.AsObject();
    public static double[][] Matrix(JsonNode node) => node.AsArray().Select(row => row!.AsArray().Select(Number).ToArray()).ToArray();
    public static bool FiniteMatrix(JsonNode? node) => node is JsonArray { Count: 3 } m && m.All(row => row is JsonArray { Count: 3 } r && r.All(value => value is not null && double.TryParse(value.ToString(), NumberStyles.Float, CultureInfo.InvariantCulture, out double x) && double.IsFinite(x)));
}
