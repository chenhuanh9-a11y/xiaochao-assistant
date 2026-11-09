using System.Text.Json.Nodes;

namespace StarMap.Core.Selection;

public static class StructureEvidence
{
    /// <summary>Current shared-structure pose only. A supported pose is never a unique map identity.</summary>
    public static bool VerifiedPose(JsonNode? fit)
    {
        if (fit is not JsonObject || !True(fit["success"]) || Text(fit["coordinateSpace"]) != "shared-structure-to-client"
            || fit["selectedRouteCurrentFrameResidual"] is not JsonObject residual || !True(residual["verified"])
            || !True(residual["poseVerifiedAgainstSharedStructure"])) return false;
        if (fit["matrixSourceToClient"] is not JsonArray matrix || matrix.Count != 3) return false;
        var values = new double[3][];
        for (int y = 0; y < 3; y++)
        {
            if (matrix[y] is not JsonArray row || row.Count != 3) return false;
            values[y] = new double[3];
            for (int x = 0; x < 3; x++)
            {
                if (!MatrixNumber(row[x], out double number) || !double.IsFinite(number)) return false;
                values[y][x] = number;
            }
        }
        // np.allclose for the affine last row; all other restrictions mirror verified_pose.
        return Math.Abs(values[2][0]) <= 1e-8 && Math.Abs(values[2][1]) <= 1e-8 && Math.Abs(values[2][2] - 1) <= 1.001e-5
            && values[0][0] > 0 && Math.Abs(values[0][0] - values[1][1]) < 1e-8
            && Math.Abs(values[0][1]) < 1e-8 && Math.Abs(values[1][0]) < 1e-8;
    }

    public static JsonArray Strings(IEnumerable<string> values) => new(values.Select(v => (JsonNode?)JsonValue.Create(v)).ToArray());
    private static bool MatrixNumber(JsonNode? value, out double number)
    {
        // np.asarray(..., dtype=float) in verified_pose coerces booleans and numeric strings.
        if (Number(value, out number)) return true;
        if (value is not JsonValue scalar) return false;
        if (scalar.TryGetValue<bool>(out bool boolean)) { number = boolean ? 1 : 0; return true; }
        return scalar.TryGetValue<string>(out string? text) && double.TryParse(text,
            System.Globalization.NumberStyles.Float, System.Globalization.CultureInfo.InvariantCulture, out number);
    }
    internal static bool True(JsonNode? value) => value is JsonValue scalar && scalar.TryGetValue<bool>(out bool result) && result;
    internal static bool False(JsonNode? value) => value is JsonValue scalar && scalar.TryGetValue<bool>(out bool result) && !result;
    internal static string? Text(JsonNode? value) => value is JsonValue scalar && scalar.TryGetValue<string>(out string? result) ? result : null;
    internal static bool Number(JsonNode? value, out double number)
    {
        number = 0;
        if (value is not JsonValue scalar) return false;
        if (scalar.TryGetValue<double>(out number)) return true;
        if (scalar.TryGetValue<int>(out int integer)) { number = integer; return true; }
        if (scalar.TryGetValue<long>(out long whole)) { number = whole; return true; }
        return false;
    }
    internal static string[]? StringArray(JsonNode? node)
    {
        if (node is not JsonArray array) return null;
        var values = array.Select(Text).ToArray();
        return values.Any(v => v is null) ? null : values.Select(v => v!).ToArray();
    }
}
