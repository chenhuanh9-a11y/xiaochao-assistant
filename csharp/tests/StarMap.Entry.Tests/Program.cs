using System.Globalization;
using System.Text.Json;
using System.Text.Json.Nodes;
using OpenCvSharp;
using StarMap.Vision;

namespace StarMap.Entry.Tests;

internal static class Program
{
    private static readonly JsonSerializerOptions Json = new() { WriteIndented = true };

    private static int Main(string[] args)
    {
        if (args.Length is not (6 or 8)) throw new ArgumentException(
            "Use --fixture-index index.json --resources packRoot --output newDirectory [--no-context-oracle directory].");
        var options = Enumerable.Range(0,args.Length/2).ToDictionary(i => args[2*i],i => Path.GetFullPath(args[2*i+1]));
        string index = options["--fixture-index"], resources = options["--resources"], output = options["--output"];
        if (Directory.Exists(output)) throw new IOException("Replay output must be a new directory.");
        Directory.CreateDirectory(output);
        options.TryGetValue("--no-context-oracle",out var independent);
        string icon = Path.Combine(resources,"references","difficult-exit-icon.json");
        using var detector = new SideDoorObservation(icon);
        bool passed = true;
        var cases = new List<object>();
        Rect? firstMeasuredBounds = null;
        foreach (var row in Read(index).AsArray())
        {
            string name = row!["case"]!.GetValue<string>();
            if (Path.GetFileName(name) != name || name is "." or "..") throw new InvalidDataException("Invalid fixture name.");
            string source = Path.Combine(Path.GetDirectoryName(index)!,name), dest = Path.Combine(output,name);
            Directory.CreateDirectory(dest);
            try
            {
                var framePath = Read(Path.Combine(source,"input.json"))["sourcePath"]!.GetValue<string>();
                using var frame = Cv2.ImDecode(File.ReadAllBytes(Path.IsPathRooted(framePath) ? framePath : Path.Combine(source,framePath)),ImreadModes.Color);
                if (frame.Empty()) throw new InvalidDataException("Could not decode original frame.");
                using var entry = EntryObservation.Capture(frame);
                bool seededBlankBounds = entry is null && firstMeasuredBounds.HasValue;
                // Original black fixture perturbs the first recorded input while keeping its measured crop.
                // Require the image to really be black before permitting this partial substitution.
                if (entry is null)
                {
                    using var channels = frame.Reshape(1);
                    if (Cv2.CountNonZero(channels) != 0 || !seededBlankBounds)
                        throw new InvalidDataException("Recorded entry geometry unavailable.");
                }
                firstMeasuredBounds ??= entry?.Geometry.Bounds;
                var scoped = entry is null ? detector.ObserveBounds(frame,firstMeasuredBounds!.Value) : detector.Observe(frame,entry);
                var passport = detector.Observe(frame);
                var actual = AsOracle(scoped);
                var fullClient = AsOracle(passport);
                Save(Path.Combine(dest,"actual.json"),new {
                    entryBounds = entry is null ? null : Bounds(entry.Geometry.Bounds),
                    otherBounds = entry?.OtherBounds.Select(b=>Bounds(b)).ToArray(), seededBlankBounds,
                    scopedBounds = Bounds(scoped.Bounds), scopedReason = scoped.Reason, scoped = actual,
                    passportBounds = Bounds(passport.Bounds), passportReason = passport.Reason, passport = fullClient
                });

                // Only now open the geometry and exit evidence expected by the independent Python fixtures.
                var differences = new List<object>();
                Compare("entryBounds",Read(Path.Combine(source,"input.json"))["bounds"],Bounds(scoped.Bounds),differences);
                Compare("scoped",Read(Path.Combine(source,"expected.json")),actual,differences);
                if (independent is not null)
                {
                    var oracle = Read(Path.Combine(independent,name,"expected.json")).AsObject();
                    if (!oracle.ContainsKey("observations") || !oracle.ContainsKey("boundary"))
                        throw new InvalidDataException("No-context oracle must include observations and boundary.");
                    foreach (var pair in oracle)
                    {
                        if (pair.Key == "bounds") Compare("passport.bounds",pair.Value,Bounds(passport.Bounds),differences);
                        else if (fullClient.ContainsKey(pair.Key))
                            Compare("passport."+pair.Key,pair.Value,fullClient[pair.Key],differences);
                        else throw new InvalidDataException("Unknown no-context oracle field: "+pair.Key);
                    }
                }
                Save(Path.Combine(dest,"differences.json"),differences);
                passed &= differences.Count == 0;
                cases.Add(new {name,differences=differences.Count,scopedCount=scoped.Observations.Count,
                    scopedBoundary=scoped.Boundary,passportCount=passport.Observations.Count,passportBoundary=passport.Boundary,
                    passportCompared=independent is not null});
            }
            catch (Exception error)
            {
                passed = false;
                Save(Path.Combine(dest,"error.json"),new {type=error.GetType().Name,error.Message,error.StackTrace});
                cases.Add(new {name,error=error.Message});
            }
        }
        var synthetic = SyntheticCases.Run(detector,icon);
        Save(Path.Combine(output,"synthetic.json"),synthetic);
        passed &= synthetic.All(c => c.Passed) && cases.Count > 0;
        var report = new {passed,syntheticCount=synthetic.Count,cases};
        Save(Path.Combine(output,"summary.json"),report);
        Console.WriteLine(JsonSerializer.Serialize(report,Json));
        return passed ? 0 : 1;
    }

    private static JsonObject AsOracle(SideDoorObservationResult result) => new() {
        ["anchors"] = result.Anchors.DeepClone(), ["observations"] = result.Observations.DeepClone(),
        ["boundary"] = result.Boundary?.DeepClone()
    };

    private static JsonArray? Bounds(Rect? b) => b is null ? null : new JsonArray(b.Value.X,b.Value.Y,b.Value.Right,b.Value.Bottom);
    private static JsonNode Read(string path) => JsonNode.Parse(File.ReadAllBytes(path))!;
    private static void Save(string path, object value) => File.WriteAllText(path,JsonSerializer.Serialize(value,Json));

    private static void Compare(string path,JsonNode? expected,JsonNode? actual,List<object> differences)
    {
        if (expected is JsonArray a && actual is JsonArray b)
        {
            if (a.Count != b.Count) differences.Add(new {path,expectedCount=a.Count,actualCount=b.Count});
            for (int i=0;i<Math.Min(a.Count,b.Count);i++) Compare(path+"/"+i,a[i],b[i],differences);
        }
        else if (expected is JsonObject e && actual is JsonObject v)
        {
            foreach (var key in e.Select(p=>p.Key).Union(v.Select(p=>p.Key),StringComparer.Ordinal))
            {
                if (e.ContainsKey(key) != v.ContainsKey(key))
                    differences.Add(new {path=path+"/"+key,expectedPresent=e.ContainsKey(key),actualPresent=v.ContainsKey(key)});
                else Compare(path+"/"+key,e[key],v[key],differences);
            }
        }
        else if (expected?.GetValueKind()==JsonValueKind.Number && actual?.GetValueKind()==JsonValueKind.Number)
        {
            double ev=double.Parse(expected.ToJsonString(),CultureInfo.InvariantCulture);
            double av=double.Parse(actual.ToJsonString(),CultureInfo.InvariantCulture);
            if (ev != av) differences.Add(new {path,expected=ev,actual=av,delta=av-ev});
        }
        else if (!JsonNode.DeepEquals(expected,actual)) differences.Add(new {path,expected,actual});
    }
}
