using System.Text.Json;
using System.Text.Json.Nodes;
using OpenCvSharp;
using StarMap.Core.Identity;

namespace StarMap.Vision;

/// <summary>Checks the current measured exits against one candidate pose and its declared inventory.</summary>
public static class EntryPassportEvidence
{
    public static JsonObject CheckObserved(EntryPassportCatalog catalog, JsonObject candidate, JsonArray currentObservations) =>
        Check(catalog, candidate, currentObservations, () => currentObservations);

    internal static JsonObject Check(EntryPassportCatalog catalog, JsonObject row, JsonArray? observations, Func<JsonArray> observe)
    {
        string mapId = row["mapId"]!.GetValue<string>();
        string floor = row["floor"]!.ToString();
        if (row["season"] is { } season && season.ToString() != catalog.Season ||
            row["mode"] is { } mode && mode.ToString() != catalog.Mode ||
            row["profileId"] is { } profile && (!catalog.MapProfiles.TryGetValue(mapId,out var registered) || profile.ToString() != registered))
            throw new ArgumentException("Candidate is from a different identity resource context.",nameof(row));
        if (row["sideDoorConstraint"] is JsonObject constraint && !BoundaryMatches(row, constraint))
            return new JsonObject { ["applicable"] = true, ["contradicted"] = true, ["checks"] = new JsonArray(),
                ["reason"] = "current-side-door-geometry", ["coverageComplete"] = true };

        bool IsCandidate(JsonElement item) => item.GetProperty("floor").GetString() == floor && item.GetProperty("mapId").GetString() == mapId;
        var items = catalog.Passports.Where(IsCandidate).ToArray();
        var coverage = catalog.Coverage.Where(IsCandidate).ToArray();
        bool complete = coverage.Length > 0 && coverage.All(c => c.GetProperty("complete").GetBoolean());
        if (observations == null && items.Length == 0 && !complete)
            return new JsonObject { ["applicable"] = false, ["contradicted"] = false, ["checks"] = new JsonArray(),
                ["coverageComplete"] = false, ["observationStatus"] = "source-inventory-incomplete" };

        observations ??= observe();
        var matrix = Numerics.Matrix(row["matrixSourceToClient"]!.AsArray());
        var checks = new JsonArray();
        bool anyApplicable = false, anyContradicted = false;
        foreach (var item in observations)
        {
            var observed = item!.AsObject();
            if (observed["reliable"]!.GetValue<bool>() == false || observed["direction"]?["reliable"]?.GetValue<bool>() != true)
                continue;
            string cardinal = observed["direction"]!["direction"]!.GetValue<string>() switch
            {
                "左" => "W", "右" => "E", "上" => "N", "下" => "S",
                _ => throw new InvalidDataException("Reliable exit has an unrecognized direction.")
            };
            var center = Point(observed["icon"]!["center"]!);
            var nearby = items.Where(p => Numerics.Norm(Project(Point(p.GetProperty("center")),matrix) - center)
                <= Math.Max(6d, p.GetProperty("unit").GetDouble() * matrix[0][0] * .2)).ToArray();
            var directional = nearby.Where(p => p.GetProperty("family").GetString() == "side" && p.GetProperty("directionKnown").GetBoolean()).ToArray();
            bool contrary = directional.Length != 0 && directional.Length == nearby.Length &&
                directional.All(p => !p.GetProperty("outwardDirections").EnumerateArray().Any(d => d.GetString() == cardinal));
            bool mainPossible = items.Where(p => p.GetProperty("family").GetString() == "main").Any(p => MainContains(p, center, matrix));
            bool outside = complete && nearby.Length == 0 && !mainPossible;
            bool applicable = directional.Length > 0 || outside;
            anyApplicable |= applicable;
            anyContradicted |= contrary || outside;
            checks.Add(new JsonObject { ["applicable"] = applicable, ["contradicted"] = contrary || outside,
                ["mainFamilyPossible"] = mainPossible, ["sourceEntryIds"] = new JsonArray(nearby.Select(p =>
                    JsonNode.Parse(p.GetProperty("sourceEntryId").GetRawText())).ToArray()), ["direction"] = cardinal,
                ["reason"] = outside ? "exit-outside-complete-source-inventory" : "nearby-entry-direction" });
        }
        return new JsonObject { ["applicable"] = anyApplicable, ["contradicted"] = anyContradicted,
            ["checks"] = checks, ["coverageComplete"] = complete };
    }

    private static bool BoundaryMatches(JsonObject row, JsonObject constraint)
    {
        var door = constraint["door"]!;
        var observed = constraint["observed"]!;
        if (door["outwardDirection"]!.GetValue<string>() != observed["direction"]!.GetValue<string>()) return false;
        var matrix = Numerics.Matrix(row["matrixSourceToClient"]!.AsArray());
        double scale = matrix[0][0], unit = Numerics.Scalar(constraint["unit"]!);
        var delta = Point(door["boundaryCenter"]!) * scale + new Point2d(matrix[0][2],matrix[1][2]) - Point(observed["center"]!);
        return Numerics.Norm(delta) <= Math.Max(6d, scale*unit*.2);
    }

    private static bool MainContains(JsonElement passport, Point2d clientCenter, double[][] matrix)
    {
        string? id = passport.GetProperty("sourceEntryId").GetString();
        var main = passport.GetProperty("localInstances").EnumerateArray().First(i => i.GetProperty("instance").GetString() == id);
        double unit = passport.GetProperty("unit").GetDouble();
        var center = Point(passport.GetProperty("center"));
        var outline = main.GetProperty("outline").EnumerateArray().Select(v =>
        {
            var p = Project(Point(v)*unit + center, matrix);
            return new Point2f((float)p.X,(float)p.Y);
        }).ToArray();
        if (outline.Length < 3) throw new InvalidDataException("Registered main entry has no usable outline.");
        return Cv2.PointPolygonTest(outline, new Point2f((float)clientCenter.X,(float)clientCenter.Y), true) >= -6d;
    }

    private static Point2d Project(Point2d p, double[][] m) =>
        new(m[0][0]*p.X + m[0][1]*p.Y + m[0][2], m[1][0]*p.X + m[1][1]*p.Y + m[1][2]);
    private static Point2d Point(JsonNode n) => new(Numerics.Scalar(n[0]!),Numerics.Scalar(n[1]!));
    private static Point2d Point(JsonElement n) => new(n[0].GetDouble(),n[1].GetDouble());
}
