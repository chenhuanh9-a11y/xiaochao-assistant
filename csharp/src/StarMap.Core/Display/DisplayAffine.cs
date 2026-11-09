using System.Text.Json;

namespace StarMap.Core.Display;

/// <summary>Column-vector affine transform. A * B applies B first, then A.</summary>
public readonly record struct DisplayAffine(double M00, double M01, double M02, double M10, double M11, double M12)
{
    public static DisplayAffine Identity => new(1, 0, 0, 0, 1, 0);
    public double Determinant => M00 * M11 - M01 * M10;
    public bool IsFinite => double.IsFinite(M00) && double.IsFinite(M01) && double.IsFinite(M02)
        && double.IsFinite(M10) && double.IsFinite(M11) && double.IsFinite(M12);
    public bool IsInvertible => IsFinite && double.IsFinite(Determinant) && Math.Abs(Determinant) > 1e-9;
    public double[][] ToArray() => [[M00, M01, M02], [M10, M11, M12], [0, 0, 1]];
    public (double X, double Y) Apply(double x, double y) => (M00*x + M01*y + M02, M10*x + M11*y + M12);
    public DisplayAffine Inverse()
    {
        if (!IsInvertible) throw new InvalidDataException("Nonfinite or singular display affine.");
        var d = Determinant;
        var result = new DisplayAffine(M11/d, -M01/d, (M01*M12-M11*M02)/d,
            -M10/d, M00/d, (M10*M02-M00*M12)/d);
        if (!result.IsFinite) throw new InvalidDataException("Display affine inverse overflow.");
        return result;
    }
    public static DisplayAffine operator *(DisplayAffine a, DisplayAffine b)
    {
        var result = new DisplayAffine(a.M00*b.M00+a.M01*b.M10, a.M00*b.M01+a.M01*b.M11,
            a.M00*b.M02+a.M01*b.M12+a.M02, a.M10*b.M00+a.M11*b.M10,
            a.M10*b.M01+a.M11*b.M11, a.M10*b.M02+a.M11*b.M12+a.M12);
        if (!result.IsFinite) throw new InvalidDataException("Display affine composition overflow.");
        return result;
    }
    public bool AlmostEquals(DisplayAffine b, double tolerance = 1e-9) =>
        ToArray().SelectMany(x => x).Zip(b.ToArray().SelectMany(x => x)).All(x => Math.Abs(x.First-x.Second) <= tolerance);
    public static bool TryRead(JsonElement? value, out DisplayAffine matrix)
    {
        matrix = default;
        if (value is not { ValueKind: JsonValueKind.Array } array || array.GetArrayLength() != 3) return false;
        var rows = array.EnumerateArray().ToArray();
        if (rows.Any(row => row.ValueKind != JsonValueKind.Array || row.GetArrayLength() != 3)) return false;
        var n = new double[9];
        int i = 0;
        foreach (var row in rows)
            foreach (var number in row.EnumerateArray())
                if (number.ValueKind != JsonValueKind.Number || !number.TryGetDouble(out n[i++])) return false;
        if (n[6] != 0 || n[7] != 0 || n[8] != 1) return false;
        matrix = new(n[0],n[1],n[2],n[3],n[4],n[5]);
        return matrix.IsInvertible;
    }
}
