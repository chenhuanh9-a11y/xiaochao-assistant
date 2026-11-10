using System.Globalization;
using System.Security.Cryptography;
using System.Text;
using System.Text.Json;
using System.Text.Json.Nodes;
using OpenCvSharp;
using StarMap.Core.Identity;
using StarMap.Core.Settings;

namespace StarMap.Vision;

/// <summary>Human-label storage only; it grants no identity, pose or display authority.</summary>
public interface IManualConfirmationMemory
{
    JsonObject RecordConfirmation(string mapId,string floor,Mat frame,int[] clientBounds);
    void Reload();
}

/// <summary>The label is durable; a subsequent maintenance step failed.</summary>
public sealed class ManualConfirmationPostSaveException(string stage,Exception inner)
    : IOException("label-saved-"+stage+"-failed:"+inner.Message,inner)
{
    public string Stage {get;}=stage;
}

/// <summary>Original S0 confirmations.jsonl/JPEG format in an explicitly isolated data root.</summary>
public sealed class ManualConfirmationMemoryStore : IManualConfirmationMemory
{
    readonly object sync=new();
    readonly EntryPassportCatalog catalog;
    readonly Dictionary<string,bool> validation=new(StringComparer.Ordinal);
    readonly TimeProvider clock;
    public ManualConfirmationMemoryStore(ExperimentalDataRoot dataRoot,EntryPassportCatalog catalog,TimeProvider? clock=null)
    {
        if(catalog.Season!="s0")throw new ArgumentException("season-manual-learning-unregistered");
        this.catalog=catalog;this.clock=clock??TimeProvider.System;
        Root=Path.Combine(dataRoot.Path,"work",catalog.Mode+"-memory");
    }
    public string Root {get;}
    public string LogPath=>Local("confirmations.jsonl");
    public void Reload(){lock(sync)validation.Clear();}

    public JsonObject RecordConfirmation(string mapId,string floor,Mat frame,int[] clientBounds)
    {
        if(!catalog.MapProfiles.ContainsKey(mapId) || floor is not ("1" or "2" or "B1"))
            throw new ArgumentException("invalid-nightmare-memory-label");
        lock(sync)
        {
            string? relative=null,hash=null;JsonArray? size=null;
            bool accepted=ValidSample(frame);
            if(accepted)
            {
                var bounds=Crop(frame.Width,frame.Height);
                using var crop=new Mat(frame,bounds);
                Cv2.ImEncode(".jpg",crop,out var bytes,new ImageEncodingParam(ImwriteFlags.JpegQuality,92));
                if(bytes.Length>0)
                {
                    hash=Convert.ToHexStringLower(SHA256.HashData(bytes));
                    relative=$"samples/{mapId}/{floor}/{hash[..20]}.jpg";
                    string destination=Local(relative);Directory.CreateDirectory(Path.GetDirectoryName(destination)!);Local(relative);
                    if(!File.Exists(destination))
                    {
                        string temporary=destination+"."+Guid.NewGuid().ToString("N")+".tmp";
                        try
                        {
                            using(var stream=new FileStream(temporary,FileMode.CreateNew,FileAccess.Write,FileShare.None))
                            {stream.Write(bytes);stream.Flush(true);}
                            Local(relative);File.Move(temporary,destination,true);
                        }
                        finally{if(File.Exists(temporary)){CheckPath(temporary);File.Delete(temporary);}}
                    }
                    size=new(bounds.Width,bounds.Height);validation[relative]=true;
                }
            }
            var row=new JsonObject {
                ["schemaVersion"]=1,["time"]=clock.GetLocalNow().ToString("yyyy-MM-dd'T'HH:mm:ss.fffzzz",CultureInfo.InvariantCulture),
                ["packId"]=catalog.PackId,["mapId"]=mapId,["mapName"]=catalog.Maps.First(map=>map.MapId==mapId).Name,["floor"]=floor,
                ["labelSource"]="human-confirmed-in-daily-assistant",["state"]="candidate-memory",["runtimeDisplayEnabled"]=false,
                ["samplePath"]=relative,["sampleSha256"]=hash,["sampleSize"]=size,["sampleEvidenceAccepted"]=accepted && relative is not null,
                ["clientBounds"]=new JsonArray(clientBounds.Select(value=>(JsonNode)JsonValue.Create(value)!).ToArray())};
            Directory.CreateDirectory(Root);string log=LogPath;
            using(var stream=new FileStream(log,FileMode.OpenOrCreate,FileAccess.ReadWrite,FileShare.Read))
            {
                bool separator=false;
                if(stream.Length>0){stream.Position=stream.Length-1;separator=stream.ReadByte()!='\n';}
                stream.Position=stream.Length;
                if(separator)stream.WriteByte((byte)'\n');
                stream.Write(Encoding.UTF8.GetBytes(row.ToJsonString()+"\n"));stream.Flush(true);
            }
            try{if(relative is not null)Prune(mapId,floor);}
            catch(Exception error) when(error is not OutOfMemoryException)
            {throw new ManualConfirmationPostSaveException("retention",error);}
            return row;
        }
    }

    public IReadOnlyList<JsonObject> Records()
    {
        lock(sync)
        {
            if(!File.Exists(LogPath))return [];
            var rows=new List<JsonObject>();
            foreach(string line in File.ReadLines(LogPath,Encoding.UTF8))
            {
                JsonObject? row;
                try{row=JsonNode.Parse(line) as JsonObject;}catch(JsonException){continue;}
                if(row is not null && catalog.MapProfiles.ContainsKey(Text(row,"mapId")) && Text(row,"floor") is "1" or "2" or "B1")rows.Add(row);
            }
            return rows.AsReadOnly();
        }
    }
    public IReadOnlyList<JsonObject> ResolvedRecords()
    {
        lock(sync)
        {
            var unkeyed=new List<(int Index,JsonObject Row)>();
            var keyed=new Dictionary<string,(int Index,JsonObject Row,HashSet<(string,string)> Labels)>(StringComparer.Ordinal);
            int index=0;
            foreach(var row in Records())
            {
                string hash=Text(row,"sampleSha256");
                if(hash.Length==0)unkeyed.Add((index,row));
                else
                {
                    var labels=keyed.TryGetValue(hash,out var previous)?previous.Labels:[];
                    labels.Add((Text(row,"mapId"),Text(row,"floor")));keyed[hash]=(index,row,labels);
                }
                index++;
            }
            unkeyed.AddRange(keyed.Values.Where(value=>value.Labels.Count==1).Select(value=>(value.Index,value.Row)));
            return unkeyed.OrderBy(value=>value.Index).Select(value=>value.Row).ToArray();
        }
    }
    public IReadOnlyDictionary<string,IReadOnlyList<string>> ExemplarPaths(string floor,int limit=6)
    {
        lock(sync)
        {
            var grouped=catalog.Maps.ToDictionary(map=>map.MapId,_=>new List<string>(),StringComparer.Ordinal);
            foreach(var row in ResolvedRecords().Reverse())
            {
                string map=Text(row,"mapId"),relative=Text(row,"samplePath");var paths=grouped[map];
                if(Text(row,"floor")!=floor || paths.Count>=limit || relative.Length==0)continue;
                string path=Local(relative);
                if(!paths.Contains(path) && Usable(row,relative,path))paths.Add(path);
            }
            return grouped.ToDictionary(pair=>pair.Key,pair=>(IReadOnlyList<string>)pair.Value.AsReadOnly(),StringComparer.Ordinal);
        }
    }
    bool Usable(JsonObject row,string relative,string path)
    {
        if(row["sampleEvidenceAccepted"] is JsonValue recorded && recorded.TryGetValue<bool>(out bool accepted))return accepted && File.Exists(path);
        if(validation.TryGetValue(relative,out bool cached))return cached;
        bool valid=false;
        if(File.Exists(path))
        {using var image=Cv2.ImDecode(File.ReadAllBytes(path),ImreadModes.Color);valid=ValidSample(image);}
        validation[relative]=valid;return valid;
    }
    bool ValidSample(Mat frame)
    {
        if(frame.Empty())return false;
        try{return FloorObservation.Observe(frame,"s0",catalog.Mode).MapObservation["detected"]?.GetValue<bool>()==true;}
        catch(Exception error) when(error is not OutOfMemoryException){return false;}
    }
    void Prune(string map,string floor)
    {
        string directory=Local($"samples/{map}/{floor}");
        var comparer=OperatingSystem.IsWindows()?StringComparer.OrdinalIgnoreCase:StringComparer.Ordinal;
        var keep=ExemplarPaths(floor)[map].ToHashSet(comparer);
        var owned=new Dictionary<string,string>(comparer);
        foreach(var row in Records().Where(row=>Text(row,"mapId")==map && Text(row,"floor")==floor))
        {
            string hash=Text(row,"sampleSha256").ToLowerInvariant(),relative=Text(row,"samplePath");
            if(hash.Length!=64 || !hash.All(Uri.IsHexDigit) || relative.Length==0)continue;
            string path=Local(relative),expected=Local($"samples/{map}/{floor}/{hash[..20]}.jpg");
            if(comparer.Equals(path,expected))owned[path]=hash;
        }
        var files=Directory.EnumerateFiles(directory,"*.jpg").Where(owned.ContainsKey).Select(path=>new FileInfo(path))
            .OrderBy(file=>file.LastWriteTimeUtc).ThenBy(file=>file.Name,StringComparer.Ordinal).ToArray();
        keep.UnionWith(files.TakeLast(12).Select(file=>file.FullName));
        foreach(var file in files.Where(file=>!keep.Contains(file.FullName)))
        {
            try
            {
                CheckPath(file.FullName);
                // The filename/key cannot prove user data still has its saved
                // contents. Reuse the existing JPEG digest before deleting it.
                if(Convert.ToHexStringLower(SHA256.HashData(File.ReadAllBytes(file.FullName)))!=owned[file.FullName])continue;
                File.Delete(file.FullName);validation.Remove(Path.GetRelativePath(Root,file.FullName).Replace('\\','/'));
            }
            catch(Exception error) when(error is IOException or UnauthorizedAccessException){ }
        }
    }
    public static Rect Crop(int width,int height)=>width/(double)Math.Max(height,1)>=1.45
        ?new((int)(width*.10),(int)(height*.05),(int)(width*.88)-(int)(width*.10),(int)(height*.94)-(int)(height*.05))
        :new(0,0,width,height);
    static string Text(JsonObject row,string key)=>row[key] is JsonValue value && value.TryGetValue<string>(out var text)?text??"":"";
    string Local(string relative)
    {
        if(Path.IsPathFullyQualified(relative))throw new IOException("memory-path-outside-root");
        string full=Path.GetFullPath(Path.Combine(Root,relative.Replace('/',Path.DirectorySeparatorChar)));
        CheckPath(full);return full;
    }
    void CheckPath(string full)
    {
        var comparison=OperatingSystem.IsWindows()?StringComparison.OrdinalIgnoreCase:StringComparison.Ordinal;
        if(!full.StartsWith(Path.TrimEndingDirectorySeparator(Root)+Path.DirectorySeparatorChar,comparison))throw new IOException("memory-path-outside-root");
        for(string? part=full;part is not null;part=Path.GetDirectoryName(part))
        {
            if((File.Exists(part)||Directory.Exists(part)) && (File.GetAttributes(part)&FileAttributes.ReparsePoint)!=0)
                throw new IOException("memory-linked-path-not-allowed");
        }
    }
}
