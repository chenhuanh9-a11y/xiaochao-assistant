using System.Runtime.InteropServices;
using System.Security.Cryptography;
using System.Text.Json;
using OpenCvSharp;
using StarMap.Core.Display;
using StarMap.Core.Resources;
using StarMap.Vision;

internal static class Program
{
    private static readonly JsonSerializerOptions JsonOptions=new(){WriteIndented=true};
    private static int Main(string[] args)
    {
        if(args.Length!=2){Console.Error.WriteLine("Usage: StarMap.DisplaySelection.Tests <oracle.json> <new-evidence-dir>");return 1;}
        string output=Path.GetFullPath(args[1]);var failures=new List<object>();int count=0;
        if(Directory.Exists(output)&&Directory.EnumerateFileSystemEntries(output).Any())
        {Console.Error.WriteLine("Use a new empty evidence directory.");return 1;}
        Directory.CreateDirectory(Path.Combine(output,"cases"));
        try
        {
            using var oracle=JsonDocument.Parse(File.ReadAllBytes(args[0]));
            var catalogs=new Dictionary<string,ResourceCatalog>(StringComparer.OrdinalIgnoreCase);
            var selector=new DisplayLayerSelector(new OpenCvDisplayRasterValidator());
            foreach(var row in oracle.RootElement.GetProperty("cases").EnumerateArray())
            {
                string label=Text(row,"label");count++;
                try
                {
                    string root=Text(row,"resourceRoot");
                    if(!catalogs.TryGetValue(root,out var catalog)){catalog=ResourceCatalog.Load(root);catalogs.Add(root,catalog);}
                    var matches=catalog.Entries.Where(e=>e.Season==Text(row,"season")&&e.Mode==Text(row,"mode")
                        &&e.MapId==Text(row,"mapId")&&e.Floor==Text(row,"floor")).ToArray();
                    var entry=matches.FirstOrDefault(e=>e.Profile=="shared-structure")??matches.First();
                    var result=selector.Select(catalog,entry,Text(row,"preferred"),Affine(row.GetProperty("referenceToClient")),Text(row,"variant"));
                    var variants=row.TryGetProperty("variants",out _)?selector.CoffinVariants(catalog,entry,Text(row,"preferred")):null;
                    // Preserve a rejected concrete bridge reason even when the
                    // original policy correctly returns its neutral fallback.
                    DisplayBridgeResult? requestedBridge=null;
                    if(row.TryGetProperty("expected",out var declaredExpected)&&Optional(declaredExpected,"displayKind")=="author-route")
                        requestedBridge=new DisplayBridgeResolver(new OpenCvDisplayRasterValidator()).ResolveDisplayReference(catalog,entry,
                            Text(row,"preferred"),Affine(row.GetProperty("referenceToClient")),Text(row,"variant"));
                    // Record the actual verdict and matrices before any assertion.
                    File.WriteAllText(Path.Combine(output,"cases",$"{count:D4}.json"),JsonSerializer.Serialize(new{label,result,variants,requestedBridge},JsonOptions));
                    Require(result.Prepared==row.GetProperty("prepared").GetBoolean(),"verdict: "+result.Reason);
                    Require(!result.AuthorizesMapIdentity&&!result.AuthorizesStructurePose&&!result.AuthorizesCrossFloorProjection,"display granted authority");
                    if(!result.Prepared){Require(result.Reason==Text(row,"reason"),"failure reason: "+result.Reason);continue;}
                    var expected=row.GetProperty("expected");var bridge=result.Bridge!;
                    Require(result.PreferredProfileId==Text(expected,"preferredProfileId"),"preferred intent changed");
                    Require(result.EffectiveProfileId==Text(expected,"effectiveProfileId"),"effective profile: "+result.EffectiveProfileId);
                    Require(result.AuthorStatus==Text(expected,"authorStatus"),"author status: "+result.AuthorStatus);
                    Require(result.Fallback==expected.GetProperty("fallback").GetBoolean(),"fallback verdict");
                    Require(result.FallbackReason==Text(expected,"fallbackReason"),"fallback reason: "+result.FallbackReason);
                    Require(result.VariantId==Optional(expected,"variantId"),"variant");
                    Require(result.DisplayKind==Text(expected,"displayKind"),"display kind: "+result.DisplayKind);
                    Require(result.DisplayLabel==Text(expected,"displayLabel"),"display label: "+result.DisplayLabel);
                    if(row.TryGetProperty("providerErrors",out var originalErrors))
                        Require(result.ProviderErrors.Keys.ToHashSet().SetEquals(originalErrors.EnumerateObject().Select(p=>p.Name)),"provider error owners differ");
                    Compare(bridge.ReferenceToCanonical,expected.GetProperty("referenceToCanonical"),"reference/canonical");
                    Compare(bridge.CanonicalToClient,expected.GetProperty("canonicalToClient"),"canonical/client");
                    Compare(bridge.DisplayToReference,expected.GetProperty("displayToReference"),"display/reference");
                    Compare(bridge.DisplayToClient,expected.GetProperty("displayToClient"),"display/client");
                    Require(result.IsCurrent(),"fresh selection resources stale");
                    var shape=expected.GetProperty("shape").EnumerateArray().Select(v=>v.GetInt32()).ToArray();
                    Require(bridge.Width==shape[1]&&bridge.Height==shape[0],"actual pixel dimensions");
                    if(Optional(expected,"imagePath") is { } imagePath)
                        Require(string.Equals(bridge.ImagePath,Path.GetFullPath(imagePath),StringComparison.OrdinalIgnoreCase),"different source bitmap");
                    string pixels=Convert.ToHexStringLower(SHA256.HashData(Pixels(bridge)));
                    Require(pixels==Text(expected,"pixelSha256"),"actual source BGRA pixels differ");
                    if(expected.TryGetProperty("atlas",out var atlas))
                    {
                        Require(bridge.BaseAtlas is not null,"base producer recipe absent");
                        Compare(bridge.BaseAtlas!.ImageToNative,atlas.GetProperty("imageToNative"),"atlas/native");
                        int[] native=atlas.GetProperty("nativeShape").EnumerateArray().Select(v=>v.GetInt32()).ToArray();
                        Require(bridge.BaseAtlas.NativeWidth==native[1]&&bridge.BaseAtlas.NativeHeight==native[0],"atlas native canvas");
                        Require(bridge.BaseAtlas.ArtIncludesExits==atlas.GetProperty("artIncludesExits").GetBoolean(),"atlas entrance ownership");
                    }
                    if(variants is not null)
                    {
                        var original=row.GetProperty("variants").EnumerateArray().ToArray();Require(variants.Count==original.Length,"variant option count");
                        for(int i=0;i<variants.Count;i++)Require(variants[i].Id==Text(original[i],"id")&&variants[i].Name==Text(original[i],"name")
                            &&variants[i].Available==original[i].GetProperty("available").GetBoolean()&&variants[i].Reason==Text(original[i],"reason"),"variant option "+i);
                    }
                }
                catch(Exception error)
                {failures.Add(new{label,error=error.ToString()});Console.Error.WriteLine("FAIL "+label+": "+error.Message);}
            }
            foreach(var row in oracle.RootElement.GetProperty("cases").EnumerateArray().Where(r=>Text(r,"label") is "synthetic/generic-rgba" or "synthetic/generic-snapshot"))
            {
                foreach(string mutation in new[]{"image","declaration"})
                {
                    string label=Text(row,"label")+"/same-path-revalidation/"+mutation;count++;
                    try
                    {
                        string original=Text(row,"resourceRoot"),install=Directory.GetParent(original)!.FullName;
                        string scratch=Path.Combine(output,"scratch",count.ToString("D4"));
                        foreach(string path in Directory.EnumerateFiles(install,"*",SearchOption.AllDirectories))
                        {string target=Path.Combine(scratch,Path.GetRelativePath(install,path));Directory.CreateDirectory(Path.GetDirectoryName(target)!);File.Copy(path,target);}
                        var catalog=ResourceCatalog.Load(Path.Combine(scratch,"_internal"));
                        var entry=catalog.Entries.Single(e=>e.Season==Text(row,"season")&&e.Mode==Text(row,"mode")&&e.MapId==Text(row,"mapId")&&e.Floor==Text(row,"floor"));
                        var before=selector.Select(catalog,entry,Text(row,"preferred"),Affine(row.GetProperty("referenceToClient")));
                        Require(before.Prepared&&before.DisplayKind=="author-route","copied package not prepared");
                        string changed=mutation=="image"?before.Bridge!.ImagePath!:
                            before.Bridge!.Resources!.First(r=>r.Path.Contains("work"+Path.DirectorySeparatorChar+"map-packages",StringComparison.OrdinalIgnoreCase)&&r.Path.EndsWith(".json",StringComparison.OrdinalIgnoreCase)).Path;
                        var stamp=File.GetLastWriteTimeUtc(changed);byte[] bytes=File.ReadAllBytes(changed);bytes[^1]^=1;File.WriteAllBytes(changed,bytes);File.SetLastWriteTimeUtc(changed,stamp);
                        bool current=before.IsCurrent();var after=selector.Select(catalog,entry,Text(row,"preferred"),Affine(row.GetProperty("referenceToClient")));
                        File.WriteAllText(Path.Combine(output,"cases",$"{count:D4}.json"),JsonSerializer.Serialize(new{label,changed,before,current,after},JsonOptions));
                        Require(!current,"same-size/timestamp change retained prior selection");
                        Require(after.Prepared&&after.DisplayKind=="shared-structure"&&after.PreferredProfileId==Text(row,"preferred"),"changed package revived author pixels or lost preference");
                    }
                    catch(Exception error){failures.Add(new{label,error=error.ToString()});Console.Error.WriteLine("FAIL "+label+": "+error.Message);}
                }
            }
        }
        catch(Exception error){failures.Add(new{label="harness",error=error.ToString()});}
        File.WriteAllText(Path.Combine(output,"summary.json"),JsonSerializer.Serialize(new{cases=count,passed=count-failures.Count,failures},JsonOptions));
        Console.WriteLine($"Display selection: {count-failures.Count}/{count}; evidence {output}");return failures.Count==0?0:1;
    }
    private static byte[] Pixels(DisplayBridgeResult bridge)
    {
        if(bridge.ImagePath is null)
        {
            var clean=bridge.Reference!.CleanMask!.Bytes.Span;var result=new byte[checked(bridge.Width*bridge.Height*4)];
            for(int i=0;i<clean.Length;i++)if(clean[i]!=0){result[4*i]=218;result[4*i+1]=228;result[4*i+2]=238;result[4*i+3]=255;}
            return result;
        }
        using var original=Cv2.ImDecode(File.ReadAllBytes(bridge.ImagePath),ImreadModes.Unchanged);using var converted=new Mat();
        int channels=original.Channels();Mat source=original;
        if(channels!=4)
        {Require(bridge.AllowBgraFormatConversion,"undeclared image format conversion");Cv2.CvtColor(original,converted,channels==1?ColorConversionCodes.GRAY2BGRA:ColorConversionCodes.BGR2BGRA);source=converted;}
        int length=checked(bridge.Width*bridge.Height*4);var bytes=new byte[length];Marshal.Copy(source.Data,bytes,0,length);return bytes;
    }
    private static string Text(JsonElement row,string key)=>row.GetProperty(key).GetString()!;
    private static string? Optional(JsonElement row,string key)=>row.TryGetProperty(key,out var value)&&value.ValueKind==JsonValueKind.String?value.GetString():null;
    private static DisplayAffine Affine(JsonElement value)=>DisplayAffine.TryRead(value,out var result)?result:throw new InvalidDataException("bad oracle matrix");
    private static void Compare(DisplayAffine? actual,JsonElement expected,string label)=>Require(actual is { } matrix&&matrix.AlmostEquals(Affine(expected),1e-8),label+" differs");
    private static void Require(bool ok,string message){if(!ok)throw new InvalidDataException(message);}
}
