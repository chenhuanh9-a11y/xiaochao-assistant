using System.Text.Json;
using System.Text.Json.Nodes;
using OpenCvSharp;
using StarMap.Core.Identity;
using StarMap.Vision;

internal static class Program
{
    private static readonly JsonSerializerOptions Pretty = new() { WriteIndented = true };

    private static int Main(string[] args)
    {
        if (args.Length != 6) throw new ArgumentException("Expected --resources ROOT --oracle INDEX --output NEW_DIRECTORY");
        var options = args.Chunk(2).ToDictionary(pair => pair[0], pair => Path.GetFullPath(pair[1]));
        var indexPath = options["--oracle"];
        var dir = Path.GetDirectoryName(indexPath)!;
        var index = JsonNode.Parse(File.ReadAllBytes(indexPath))!.AsObject();
        var output = options["--output"];
        if (Directory.Exists(output) && Directory.EnumerateFileSystemEntries(output).Any())
            throw new IOException("Evidence output must be empty.");
        Directory.CreateDirectory(output);
        var resources = options["--resources"];
        using var frame = Cv2.ImDecode(File.ReadAllBytes(index["frame"]!.GetValue<string>()), ImreadModes.Color);
        if (frame.Empty()) throw new InvalidDataException("Unable to decode recorded BGR frame.");
        using var black = new Mat(frame.Size(), MatType.CV_8UC3, Scalar.Black);
        var catalogs = new Dictionary<(string, string), EntryPassportCatalog>();
        var results = new List<object>();
        bool passed = true;
        foreach (var node in index["cases"]!.AsArray())
        {
            var input = node!.AsObject();
            var key = (Required(input,"season"), Required(input,"mode"));
            string name = Required(input,"name"), kind = Required(input,"kind");
            var dest = Path.Combine(output,name);
            Directory.CreateDirectory(dest);
            try
            {
                if (!catalogs.TryGetValue(key, out var catalog))
                    catalogs.Add(key, catalog = EntryPassportCatalog.Load(resources,key.Item1,key.Item2));
                Mat image = input["black"]?.GetValue<bool>() == true ? black : frame;
                JsonNode? actual = Execute(kind, catalog, input, image);
                Save(Path.Combine(dest,"actual.json"), actual);
                // Expected documents are only opened after the public production method has returned and been recorded.
                var expected = JsonNode.Parse(File.ReadAllBytes(Path.Combine(dir, Required(input,"expected"))));
                var differences = new List<object>();
                Compare("",expected,actual,differences);
                Save(Path.Combine(dest,"differences.json"), differences);
                results.Add(new {name,kind,differences=differences.Count});
                passed &= differences.Count == 0;
            }
            catch (Exception error)
            {
                passed = false;
                Save(Path.Combine(dest,"error.json"),new {type=error.GetType().FullName,error.Message,error.StackTrace});
                results.Add(new {name,kind,error=error.Message});
            }
        }
        passed &= results.Count > 0;
        var summary = new {passed,cases=results};
        Save(Path.Combine(output,"summary.json"),summary);
        Console.WriteLine(JsonSerializer.Serialize(summary));
        return passed ? 0 : 1;
    }

    private static JsonNode? Execute(string kind, EntryPassportCatalog catalog, JsonObject input, Mat image)
    {
        switch (kind)
        {
            case "catalog":
                static string[][] Keys(IEnumerable<JsonElement> rows) => rows.Select(row => new[] {
                    row.GetProperty("mapId").GetString()!, row.GetProperty("floor").GetString()!, row.GetProperty("profileId").GetString()! }).ToArray();
                return JsonSerializer.SerializeToNode(new {
                    floors=catalog.Floors, profile=catalog.ProfileId, profiles=catalog.MapProfiles,
                    registeredFloors=catalog.RegisteredFloors.OrderBy(k=>k.MapId,StringComparer.Ordinal)
                        .ThenBy(k=>k.Floor,StringComparer.Ordinal).Select(k=>new[] {k.MapId,k.Floor}),
                    semanticGaps=catalog.SemanticGaps,sideKeys=Keys(catalog.SideEntries),mainKeys=Keys(catalog.MainEntries),
                    passports=catalog.Passports,coverage=catalog.Coverage,hasPassportError=catalog.NativePassportError != null,
                    hasLegacySource=catalog.LegacyPassportSource != null });
            case "preflight":
                using (var current = new EntryPassportFrame(catalog,image))
                {
                    var preflight = current.Inspect(input["floor"]?.ToString(),TimeSpan.FromSeconds(input["seconds"]!.GetValue<int>()));
                    return new JsonObject {["needsLocalGeometry"]=preflight.NeedsLocalGeometry,["diagnostics"]=preflight.Diagnostics};
                }
            case "check":
                var row = input["row"]!.AsObject();
                if (input["observations"] is JsonArray supplied)
                    return EntryPassportEvidence.CheckObserved(catalog,row,supplied);
                using (var current = new EntryPassportFrame(catalog,image))
                    return current.Check(row);
            case "buffer-reuse":
                using (var buffer = image.Clone())
                using (var captured = new EntryPassportFrame(catalog,buffer))
                {
                    buffer.SetTo(Scalar.Black);
                    using var next = new EntryPassportFrame(catalog,buffer);
                    var candidate = input["row"]!.AsObject();
                    return new JsonObject { ["captured"] = captured.Check(candidate), ["next"] = next.Check(candidate) };
                }
            case "selection":
                return EntryIdentityDecision.VerifiedCandidate(catalog,input["rows"]!.AsArray(),
                    input["diagnostics"]!.AsObject(),Required(input,"floor"));
            case "fallback":
                return EntryIdentityDecision.PassportFallbackAllowed(input["diagnostics"]?.AsObject());
            default:
                throw new InvalidDataException("Unsupported oracle case " + kind);
        }
    }

    private static string Required(JsonObject input,string key) => input[key]!.GetValue<string>();
    private static void Save(string path,object? value) =>
        File.WriteAllText(path,JsonSerializer.Serialize(value,Pretty));

    private static void Compare(string path,JsonNode? expected,JsonNode? actual,List<object> diff)
    {
        if (expected is JsonObject e && actual is JsonObject a)
        {
            foreach (var key in e.Select(p=>p.Key).Union(a.Select(p=>p.Key),StringComparer.Ordinal))
            {
                var child = path+"/"+key;
                if (e.ContainsKey(key) != a.ContainsKey(key))
                    diff.Add(new {path=child,expectedPresent=e.ContainsKey(key),actualPresent=a.ContainsKey(key)});
                else Compare(child,e[key],a[key],diff);
            }
        }
        else if (expected is JsonArray items && actual is JsonArray output)
        {
            if (items.Count != output.Count) diff.Add(new {path,expectedCount=items.Count,actualCount=output.Count});
            for (int i=0;i<Math.Min(items.Count,output.Count);i++) Compare(path+"/"+i,items[i],output[i],diff);
        }
        else if (expected?.GetValueKind() == JsonValueKind.Number && actual?.GetValueKind() == JsonValueKind.Number)
        {
            double x = double.Parse(expected.ToJsonString(),System.Globalization.CultureInfo.InvariantCulture);
            double y = double.Parse(actual.ToJsonString(),System.Globalization.CultureInfo.InvariantCulture);
            if (x != y) diff.Add(new {path,expected=x,actual=y,delta=y-x});
        }
        else if (!JsonNode.DeepEquals(expected,actual)) diff.Add(new {path,expected,actual});
    }
}
