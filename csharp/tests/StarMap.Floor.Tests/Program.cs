using System.Globalization;
using System.Security.Cryptography;
using System.Text.Json;
using System.Text.Json.Nodes;
using System.Text.RegularExpressions;
using OpenCvSharp;
using StarMap.Vision;

internal static class Program
{
    private static readonly JsonSerializerOptions Pretty = new() { WriteIndented = true };

    private static int Main(string[] args)
    {
        if (args.Length != 4) throw new ArgumentException("Expected --oracle INDEX --output NEW_DIRECTORY");
        var options = args.Chunk(2).ToDictionary(pair => pair[0], pair => Path.GetFullPath(pair[1]));
        string output = options["--output"], index = options["--oracle"];
        string directory = Path.GetDirectoryName(index)!;
        Directory.CreateDirectory(output);
        if (Directory.EnumerateFileSystemEntries(output).Any()) throw new IOException("Evidence output must be new or empty.");
        var cases = new List<object>();
        bool passed = true;
        foreach (var node in Read(index)!["cases"]!.AsArray())
        {
            var input = node!.AsObject();
            string name = Str(input, "name"), origin = Str(input, "origin");
            if (Path.GetFileName(name) != name || name is "." or "..") throw new InvalidDataException("Invalid case name.");
            string dest = Path.Combine(output, name);
            Directory.CreateDirectory(dest);
            try
            {
                using var frame = ReadFrame(directory, input);
                var result = FloorObservation.Observe(frame, Str(input, "season"), Str(input, "mode"),
                    input["settings"]!.AsObject(), Enum.Parse<S1FloorUse>(Str(input, "use")));
                var actual = new JsonObject {
                    ["mapObservation"] = result.MapObservation, ["floorRead"] = result.FloorRead,
                    ["floorId"] = result.FloorId, ["selectedFloor"] = result.SelectedFloor,
                    ["mapOpen"] = result.MapOpen, ["automaticEligible"] = result.AutomaticEligible,
                    ["confidence"] = result.Confidence
                };
                string actualPath = Path.Combine(dest, "actual.json");
                Save(actualPath, actual);
                var differences = new List<object>();
                Compare("", Read(Path.Combine(directory, Str(input, "expected"))), Read(actualPath), differences);
                Save(Path.Combine(dest, "differences.json"), differences);
                cases.Add(new {name, origin, result.MapOpen, result.SelectedFloor, result.AutomaticEligible, reason = result.FloorRead["reason"], differences = differences.Count});
                passed &= differences.Count == 0;
            }
            catch (Exception error)
            {
                Save(Path.Combine(dest, "error.json"), new { type = error.GetType().FullName, error.Message, error.StackTrace });
                cases.Add(new {name, origin, error=error.Message});
                passed = false;
            }
        }
        passed &= cases.Count != 0;
        var summary = new { passed, cases };
        Save(Path.Combine(output, "summary.json"), summary);
        Console.WriteLine(JsonSerializer.Serialize(summary));
        return passed ? 0 : 1;
    }

    private static Mat ReadFrame(string directory, JsonObject input)
    {
        string path = Path.GetFullPath(Str(input, "image")), origin = Str(input, "origin");
        var bytes = File.ReadAllBytes(path);
        if (origin == "synthetic")
        {
            if (!Str(input, "name").StartsWith("synthetic-", StringComparison.Ordinal)
                || Path.GetRelativePath(directory, path) != Str(input, "name") + ".png")
                throw new InvalidDataException("Synthetic fixtures must be newly rendered beside the oracle index.");
            return Decode(bytes);
        }
        if (origin != "recorded") throw new InvalidDataException("Unsupported fixture origin.");
        var proof = input["capture"]!.AsObject();
        string recordPath = Path.GetFullPath(Str(proof, "record"));
        var record = Read(recordPath)!.AsObject();
        string rawTime = Str(record, "capturedAt");
        if (Str(record, "evidenceKind") != "live-client-capture"
            || !Str(record, "imageScope").StartsWith("Full physical target client area only", StringComparison.Ordinal)
            || record["cropProvenance"]?.ToString() == "color-component-unvalidated"
            || !Regex.IsMatch(rawTime, @"(?:Z|[+-]\d{2}:\d{2})$") || rawTime != Str(proof, "capturedAt")
            || !DateTimeOffset.TryParse(rawTime, CultureInfo.InvariantCulture, DateTimeStyles.None, out var date)
            || DateOnly.FromDateTime(date.Date) < new DateOnly(2026, 11, 5))
            throw new InvalidDataException("A dated full-client capture event is required.");
        var declared = Path.GetFullPath(Path.Combine(Path.GetDirectoryName(recordPath)!, Str(record, "samplePath")));
        if (!string.Equals(declared, path, OperatingSystem.IsWindows() ? StringComparison.OrdinalIgnoreCase : StringComparison.Ordinal)
            || !Convert.ToHexString(SHA256.HashData(bytes)).Equals(Str(proof, "pngSha256"), StringComparison.OrdinalIgnoreCase))
            throw new InvalidDataException("Capture event and original PNG disagree.");
        var frame = Decode(bytes);
        var meta = record["Metadata"]!;
        if (!JsonNode.DeepEquals(meta["Before"], meta["After"])
            || frame.Cols != meta["Before"]!["Width"]!.GetValue<int>()
            || frame.Rows != meta["Before"]!["Height"]!.GetValue<int>())
        {
            frame.Dispose();
            throw new InvalidDataException("Capture geometry disagrees with original pixels.");
        }
        return frame;
    }

    private static Mat Decode(byte[] bytes)
    {
        var frame = Cv2.ImDecode(bytes, ImreadModes.Color);
        if (!frame.Empty()) return frame;
        frame.Dispose();
        throw new InvalidDataException("BGR decode failed.");
    }

    private static JsonNode? Read(string path) => JsonNode.Parse(File.ReadAllText(path));
    private static string Str(JsonObject node,string key)=>node[key]!.GetValue<string>();
    private static void Save(string path,object value)=>File.WriteAllText(path,JsonSerializer.Serialize(value,Pretty));

    private static void Compare(string path,JsonNode? expected,JsonNode? actual,List<object> differences)
    {
        if(expected is JsonObject e && actual is JsonObject a)
            foreach(var k in e.Select(p=>p.Key).Union(a.Select(p=>p.Key),StringComparer.Ordinal))
            {
                string child=path+"/"+k;
                if(e.ContainsKey(k)!=a.ContainsKey(k)) differences.Add(new {path=child,expectedPresent=e.ContainsKey(k),actualPresent=a.ContainsKey(k)});
                else Compare(child,e[k],a[k],differences);
            }
        else if(expected is JsonArray left && actual is JsonArray right)
        {
            if(left.Count!=right.Count) differences.Add(new {path,expectedCount=left.Count,actualCount=right.Count});
            for(int i=0;i<Math.Min(left.Count,right.Count);i++) Compare(path+"/"+i,left[i],right[i],differences);
        }
        else if(expected?.GetValueKind()==JsonValueKind.Number && actual?.GetValueKind()==JsonValueKind.Number)
        {
            double x=double.Parse(expected.ToJsonString(),CultureInfo.InvariantCulture),y=double.Parse(actual.ToJsonString(),CultureInfo.InvariantCulture);
            if(x!=y) differences.Add(new {path,expected=x,actual=y,delta=y-x});
        }
        else if(!JsonNode.DeepEquals(expected,actual)) differences.Add(new {path,expected,actual});
    }
}
