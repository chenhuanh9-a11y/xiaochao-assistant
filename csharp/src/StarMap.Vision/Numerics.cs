using OpenCvSharp;
using System.Text.Json.Nodes;
namespace StarMap.Vision;

internal static class Numerics
{
    public static double Scalar(JsonNode node)
    {
        var value = node.AsValue();
        if (value.TryGetValue<double>(out var d)) return d;
        if (value.TryGetValue<int>(out var i)) return i;
        if (value.TryGetValue<long>(out var l)) return l;
        if (value.TryGetValue<float>(out var f)) return f;
        throw new InvalidDataException("Numeric JSON value required.");
    }

    public static int Rint(double value) => (int)Math.Round(value, MidpointRounding.ToEven);
    public static double Norm(Point2d p) => Math.Sqrt(p.X*p.X + p.Y*p.Y);
    public static double Dot(Point2d a, Point2d b) => a.X*b.X + a.Y*b.Y;

    public static double[][] Matrix(JsonArray matrix) =>
        matrix.Select(row => row!.AsArray().Select(value => Scalar(value!)).ToArray()).ToArray();

    public static JsonArray Points(IEnumerable<Point2d> points) =>
        new(points.Select(p => (JsonNode)new JsonArray(p.X, p.Y)).ToArray());

    public static double Median(IEnumerable<double> values)
    {
        var sorted = values.Order().ToArray();
        int mid = sorted.Length / 2;
        return sorted.Length % 2 == 1 ? sorted[mid] : (sorted[mid-1]+sorted[mid])/2;
    }
}
