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
    private static readonly JsonSerializerOptions Pretty=new() { WriteIndented=true,NumberHandling=JsonNumberHandling.AllowNamedFloatingPointLiterals };
    private static int Main(string[] args)
    {
        if(args.Length!=6) throw new ArgumentException("Expected --resources ROOT --oracle INDEX --output NEW_DIRECTORY");
        var options=args.Chunk(2).ToDictionary(a=>a[0],a=>Path.GetFullPath(a[1]));
        string root=options["--resources"],oracle=options["--oracle"],output=options["--output"],directory=Path.GetDirectoryName(oracle)!;
        if(Directory.Exists(output) && Directory.EnumerateFileSystemEntries(output).Any()) throw new IOException("Evidence output must be new or empty.");
        Directory.CreateDirectory(output);
        var resources=ResourceCatalog.Load(root); var rankers=new Dictionary<(string,string),SideEntryRanker>();
        var cases=new List<object>(); bool passed=true;
        foreach(var node in Read(oracle)!["cases"]!.AsArray())
        {
            var input=node!.AsObject(); string name=Str(input,"name"),origin=Str(input,"origin");
            if(Path.GetFileName(name)!=name || name is "." or "..") throw new InvalidDataException("Invalid fixture name.");
            string dest=Path.Combine(output,name); Directory.CreateDirectory(dest);
            try
            {
                var bytes=FrameBytes(input,directory); using var frame=Cv2.ImDecode(bytes,ImreadModes.Color);
                if(frame.Empty()) throw new InvalidDataException("Cannot decode full-client BGR frame.");
                var key=(Str(input,"season"),Str(input,"mode"));
                if(!rankers.TryGetValue(key,out var ranker)) rankers[key]=ranker=new(EntryPassportCatalog.Load(root,key.Item1,key.Item2),resources);
                var actual=ranker.Rank(frame,Str(input,"floor"),TimeSpan.FromSeconds(input["seconds"]!.GetValue<int>()));
                var result=new JsonObject { ["rows"]=actual.Rows,["diagnostics"]=actual.Diagnostics,["turns"]=actual.Turns };
                Save(Path.Combine(dest,"actual.json"),result);
                // Read the independent answer only after full inventory search
                // has finished. Expected fields never enter candidate solving.
                var differences=new List<object>();
                Compare("",Read(Path.Combine(directory,Str(input,"expected"))),Read(Path.Combine(dest,"actual.json")),differences);
                Save(Path.Combine(dest,"differences.json"),differences);
                bool authority=actual.Rows.All(r=>r!["identityVerified"]?.GetValue<bool>()==false && r["poseVerified"]==null);
                bool cancel=CheckCancellation(ranker,frame,Str(input,"floor"));
                passed &= differences.Count==0 && authority && cancel;
                cases.Add(new {name,origin,turns=actual.Turns.Count,candidates=actual.Rows.Count,differences=differences.Count,authorityBoundary=authority,cancellation=cancel});
            }
            catch(Exception e)
            {
                Save(Path.Combine(dest,"error.json"),new {type=e.GetType().FullName,e.Message,e.StackTrace});
                cases.Add(new {name,origin,error=e.Message}); passed=false;
            }
        }
        passed &= cases.Count>0;
        var summary=new {passed,cases,numericTolerance="abs 1e-9 or relative 1e-12; all discrete fields and inventory cardinalities exact",
            scope="Original Python numerical replay, not GUI/game or Runtime pipeline acceptance"};
        Save(Path.Combine(output,"summary.json"),summary); Console.WriteLine(JsonSerializer.Serialize(summary)); return passed?0:1;
    }

    private static bool CheckCancellation(SideEntryRanker ranker,Mat frame,string floor)
    {
        using var source=new CancellationTokenSource(); source.Cancel();
        try { ranker.Rank(frame,floor,cancellation:source.Token); return false; }
        catch(OperationCanceledException) { return true; }
    }
    private static byte[] FrameBytes(JsonObject input,string directory)
    {
        string image=Path.GetFullPath(Str(input,"image")),origin=Str(input,"origin");
        if(origin=="synthetic")
        {
            if(!Str(input,"name").StartsWith("synthetic-",StringComparison.Ordinal) || Path.GetRelativePath(directory,image)!=Str(input,"name")+".png")
                throw new InvalidDataException("Fresh synthetic fixtures must sit beside the original oracle index.");
            return File.ReadAllBytes(image);
        }
        if(origin!="recorded") throw new InvalidDataException("Unsupported frame provenance.");
        var proof=input["capture"]!.AsObject(); var spec=proof["spec"]!.AsObject();
        string journal=Path.GetFullPath(Str(spec,"captureRecord"));
        var record=spec["line"] is { } l?JsonNode.Parse(File.ReadLines(journal).ElementAt(l.GetValue<int>()-1))!:Read(journal)!;
        string declared=Path.GetFullPath(Path.Combine(Path.GetDirectoryName(journal)!,Field(record,spec["imageField"]!)));
        if(!string.Equals(image,declared,OperatingSystem.IsWindows()?StringComparison.OrdinalIgnoreCase:StringComparison.Ordinal))
            throw new InvalidDataException("Capture event does not bind this full client image.");
        string rawTime=Field(record,spec["timeField"]!);
        if(!DateTimeOffset.TryParse(rawTime,CultureInfo.InvariantCulture,DateTimeStyles.None,out var timestamp) ||
            !(rawTime.EndsWith('Z') || System.Text.RegularExpressions.Regex.IsMatch(rawTime,@"[+-]\d{2}:\d{2}$")) ||
            DateOnly.FromDateTime(timestamp.Date)<new DateOnly(2026,11,5) || rawTime!=Str(proof,"capturedAt") ||
            Field(record,spec["kindField"]!)!="live-client-capture" || record["cropProvenance"]?.ToString()=="color-component-unvalidated" ||
            Str(input,"season")!=Str(spec,"season") || Str(input,"mode")!=Str(spec,"mode") || Str(input,"floor")!=spec["floor"]!.ToString() ||
            Str(proof,"layout")!=(Str(input,"season")=="s0"?"s0-vertical":"s1-signed-pixel-column"))
            throw new InvalidDataException("Only current, independently read full-client capture controls are accepted.");
        var bytes=File.ReadAllBytes(image);
        if(!Convert.ToHexString(SHA256.HashData(bytes)).Equals(Str(proof,"pngSha256"),StringComparison.OrdinalIgnoreCase))
            throw new InvalidDataException("PNG differs from the independently read pixels.");
        return bytes;
    }
    private static string Field(JsonNode row,JsonNode path) { foreach(var key in path.AsArray()) row=row[key!.GetValue<string>()]??throw new InvalidDataException("Missing capture metadata."); return row.GetValue<string>(); }
    private static string Str(JsonObject row,string key)=>row[key]!.GetValue<string>();
    private static JsonNode? Read(string path)=>JsonNode.Parse(File.ReadAllBytes(path));
    private static void Save(string path,object value)=>File.WriteAllText(path,JsonSerializer.Serialize(value,Pretty));
    private static void Compare(string path,JsonNode? expected,JsonNode? actual,List<object> differences)
    {
        // Extra C# diagnostics record inventory/cancellation scope. Every
        // original field, row, pose and failure reason remains mandatory.
        if(expected is JsonObject e && actual is JsonObject a)
            foreach(var pair in e)
                if(!a.ContainsKey(pair.Key)) differences.Add(new {path=path+"/"+pair.Key,missing=true});
                else Compare(path+"/"+pair.Key,pair.Value,a[pair.Key],differences);
        else if(expected is JsonArray left && actual is JsonArray right)
        {
            if(left.Count!=right.Count) differences.Add(new {path,expectedCount=left.Count,actualCount=right.Count});
            for(int i=0;i<Math.Min(left.Count,right.Count);i++) Compare(path+"/"+i,left[i],right[i],differences);
        }
        else if(expected?.GetValueKind()==JsonValueKind.Number && actual?.GetValueKind()==JsonValueKind.Number)
        {
            double x=double.Parse(expected.ToJsonString(),CultureInfo.InvariantCulture),y=double.Parse(actual.ToJsonString(),CultureInfo.InvariantCulture);
            double tolerance=Math.Max(1e-9,Math.Abs(x)*1e-12);
            if(!double.IsFinite(x) || !double.IsFinite(y) || Math.Abs(x-y)>tolerance) differences.Add(new {path,expected=x,actual=y,delta=y-x});
        }
        else if(!JsonNode.DeepEquals(expected,actual)) differences.Add(new {path,expected,actual});
    }
}
