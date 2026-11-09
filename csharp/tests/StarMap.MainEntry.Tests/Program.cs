using System.Globalization;
using System.Security.Cryptography;
using System.Text.Json;
using System.Text.Json.Nodes;
using System.Text.Json.Serialization;
using OpenCvSharp;
using StarMap.Core.Identity;
using StarMap.Core.Resources;
using StarMap.Vision;

internal static class Program
{
    private static readonly JsonSerializerOptions Pretty=new() {WriteIndented=true,NumberHandling=JsonNumberHandling.AllowNamedFloatingPointLiterals};

    private static int Main(string[] args)
    {
        if(args.Length!=6) throw new ArgumentException("Expected --resources ROOT --oracle INDEX --output NEW_DIRECTORY");
        var options=args.Chunk(2).ToDictionary(p=>p[0],p=>Path.GetFullPath(p[1]));
        var oracle=options["--oracle"]; var dir=Path.GetDirectoryName(oracle)!; var output=options["--output"];
        if(Directory.Exists(output)&&Directory.EnumerateFileSystemEntries(output).Any()) throw new IOException("Evidence output must be new or empty.");
        Directory.CreateDirectory(output);
        var index=Read(oracle)!.AsObject();
        var structures=ResourceCatalog.Load(options["--resources"]);
        var rankers=new Dictionary<(string Season,string Mode),MainEntryRanker>();
        var cases=new List<object>(); bool passed=true;
        foreach(var node in index["cases"]!.AsArray())
        {
            var input=node!.AsObject(); string name=Str(input,"name"), origin=Str(input,"origin");
            if(Path.GetFileName(name)!=name || name is "." or "..") throw new InvalidDataException("Invalid case name.");
            string dest=Path.Combine(output,name); Directory.CreateDirectory(dest);
            try
            {
                var image=Path.GetFullPath(Str(input,"image"));
                var bytes=ReadFrame(input,image,dir);
                using var pixels=Cv2.ImDecode(bytes,ImreadModes.Color);
                if(pixels.Empty()) throw new InvalidDataException("BGR decode failed.");
                var key=(Str(input,"season"),Str(input,"mode"));
                if(!rankers.TryGetValue(key,out var ranker))
                    rankers[key]=ranker=new(EntryPassportCatalog.Load(options["--resources"],key.Item1,key.Item2),structures);
                var actual=ranker.Rank(pixels,Str(input,"floor"),TimeSpan.FromSeconds(input["seconds"]!.GetValue<int>()));
                var result=new JsonObject {["rows"]=actual.Rows,["diagnostics"]=actual.Diagnostics,["frontGate"]=actual.FrontGate,["corners"]=actual.Corners};
                string actualPath=Path.Combine(dest,"actual.json"); Save(actualPath,result);
                // Independent result is read only after the original image and complete candidate universe have produced actual rows.
                var diff=new List<object>(); Compare("",Read(Path.Combine(dir,Str(input,"expected"))),Read(actualPath),diff);
                Save(Path.Combine(dest,"differences.json"),diff);
                passed &= diff.Count==0;
                cases.Add(new {name,origin,corners=actual.Corners.Count,candidates=actual.Rows.Count,differences=diff.Count});
            }
            catch(Exception error)
            {
                Save(Path.Combine(dest,"error.json"),new {type=error.GetType().FullName,error.Message,error.StackTrace});
                cases.Add(new {name,origin,error=error.Message}); passed=false;
            }
        }
        var wall=CheckSupportOverload();
        Save(Path.Combine(output,"wall-actual.json"),wall);
        var wallDiff=new List<object>(); Compare("",Read(Path.Combine(dir,Str(index,"wallExpected"))),wall,wallDiff);
        Save(Path.Combine(output,"wall-differences.json"),wallDiff);
        passed &= cases.Count>0 && wallDiff.Count==0;
        var summary=new {passed,wallDifferences=wallDiff.Count,cases};
        Save(Path.Combine(output,"summary.json"),summary); Console.WriteLine(JsonSerializer.Serialize(summary));
        return passed?0:1;
    }

    private static byte[] ReadFrame(JsonObject input,string image,string dir)
    {
        string kind=Str(input,"origin");
        if(kind=="synthetic" && Str(input,"name").StartsWith("synthetic-",StringComparison.Ordinal) &&
            Path.GetFileName(image)==Str(input,"name")+".png" && Path.GetRelativePath(dir,image)==Path.GetFileName(image))
            return File.ReadAllBytes(image);
        if(kind!="recorded") throw new InvalidDataException("Only newly rendered fixtures or evidenced current full-client captures are accepted.");
        var proof=input["capture"]!.AsObject(); var spec=proof["spec"]!.AsObject();
        string journal=Path.GetFullPath(Str(spec,"captureRecord"));
        var evt=spec["line"] is { } l ? JsonNode.Parse(File.ReadLines(journal).ElementAt(l.GetValue<int>()-1))! : Read(journal)!;
        string declared=Path.GetFullPath(Path.Combine(Path.GetDirectoryName(journal)!,Field(evt,spec["imageField"]!)));
        if(!Path.GetRelativePath(Path.GetDirectoryName(image)!,declared).Equals(Path.GetFileName(image),
                OperatingSystem.IsWindows()?StringComparison.OrdinalIgnoreCase:StringComparison.Ordinal))
            throw new InvalidDataException("Capture event does not bind this image.");
        string rawTime=Field(evt,spec["timeField"]!);
        if(!DateTimeOffset.TryParse(rawTime,CultureInfo.InvariantCulture,DateTimeStyles.None,out var timestamp) ||
            !(rawTime.EndsWith('Z') || System.Text.RegularExpressions.Regex.IsMatch(rawTime,@"[+-]\d{2}:\d{2}$")) ||
            DateOnly.FromDateTime(timestamp.Date)<new DateOnly(2026,11,5) ||
            timestamp!=DateTimeOffset.Parse(Str(proof,"capturedAt"),CultureInfo.InvariantCulture) ||
            Field(evt,spec["kindField"]!)!="live-client-capture" || evt["cropProvenance"]?.ToString()=="color-component-unvalidated")
            throw new InvalidDataException("A current full-client capture event is required.");
        if(Str(input,"season")!=Str(spec,"season") || Str(input,"mode")!=Str(spec,"mode") || Str(input,"floor")!=spec["floor"]!.ToString() ||
            Str(proof,"layout")!=(Str(input,"season")=="s0"?"s0-vertical":"s1-signed-pixel-column"))
            throw new InvalidDataException("Capture and independent control read disagree on context.");
        var bytes=File.ReadAllBytes(image);
        if(!Convert.ToHexString(SHA256.HashData(bytes)).Equals(Str(proof,"pngSha256"),StringComparison.OrdinalIgnoreCase))
            throw new InvalidDataException("PNG differs from the pixels checked by the original current-layout detector.");
        return bytes;

        static string Field(JsonNode current,JsonNode keys)
        {
            foreach(var k in keys.AsArray()) current=current[k!.GetValue<string>()]??throw new InvalidDataException("Missing capture metadata.");
            return current.GetValue<string>();
        }
    }

    private static JsonObject CheckSupportOverload()
    {
        using var clean=new Mat(96,96,MatType.CV_8UC1,Scalar.Black);
        using var wall=clean.Clone();
        using(var r=new Mat(clean,new Rect(10,10,76,76))) r.SetTo(Scalar.All(1));
        using(var r=new Mat(wall,new Rect(47,20,1,56))) r.SetTo(Scalar.All(1));
        using var kernel=new Mat(3,3,MatType.CV_8UC1,Scalar.All(1)); using var supported=new Mat();
        Cv2.Dilate(wall,supported,kernel);
        using(var hidden=new Mat(supported,new Rect(0,64,96,32))) hidden.SetTo(Scalar.Black);
        JsonNode? Spans(IEnumerable<Wall> rows) => JsonSerializer.SerializeToNode(rows.Select(x=>new {axis=x.Axis,sign=x.Sign,normal=x.Normal,lo=x.Lo,hi=x.Hi}));
        return new() {["old"]=Spans(PartialWalls.ExplicitWallSegments(clean,wall)),
            ["expanded"]=Spans(PartialWalls.ExplicitWallSegments(clean,wall,supported))};
    }

    private static JsonNode? Read(string path)=>JsonNode.Parse(File.ReadAllBytes(path));
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
