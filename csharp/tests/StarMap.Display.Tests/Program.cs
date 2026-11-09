using System.Text.Json;
using System.Text.Json.Nodes;
using StarMap.Core.Display;
using StarMap.Core.Resources;
using StarMap.Vision;

internal static class Program
{
    private static readonly JsonSerializerOptions JsonOptions = new() { WriteIndented=true };
    private static int Main(string[] args)
    {
        string? evidenceDir=null;
        var actualCases=new List<object>();
        var failures=new List<object>();
        int adapterCount=0,bridgeCount=0,cornersCount=0,calibrationCount=0;
        try
        {
            if (args.Length is < 2 or > 4 || args.Length==4 && args[2]!="--output")
                throw new ArgumentException("Usage: StarMap.Display.Tests <resource-root> <python-oracle.json> [<new-evidence-dir> | --output <new-evidence-dir>]");
            string root=Path.GetFullPath(args[0]);
            string oraclePath=Path.GetFullPath(args[1]);
            evidenceDir=Path.GetFullPath(args.Length==4 ? args[3] : args.Length==3 ? args[2]
                : Path.Combine(Path.GetDirectoryName(oraclePath)!,"display-csharp-"+DateTime.UtcNow.ToString("yyyyMMdd-HHmmss")+"-"+Guid.NewGuid().ToString("N")));
            if (Directory.Exists(evidenceDir) && Directory.EnumerateFileSystemEntries(evidenceDir).Any())
                throw new ArgumentException("Use a new empty evidence directory; previous evidence will not be overwritten.");
            Directory.CreateDirectory(Path.Combine(evidenceDir,"cases"));
            using var oracle=JsonDocument.Parse(File.ReadAllBytes(oraclePath));
            var catalog=ResourceCatalog.Load(root);
            var raster=new OpenCvDisplayRasterValidator();
            var resolver=new DisplayBridgeResolver(raster);
            var accepted=new List<DisplayBridgeResult>();

            foreach (var row in oracle.RootElement.GetProperty("adapterCases").EnumerateArray())
                RunCase("adapter",Label(row),row,() =>
                {
                    using var document=row.TryGetProperty("document",out var inline)
                        ? JsonDocument.Parse(inline.GetRawText())
                        : JsonDocument.Parse(File.ReadAllBytes(Path.Combine(root,Text(row,"path"))));
                    var adapter=new AuthorCoordinateAdapter(document.RootElement);
                    bool ok=adapter.TryProfileToCanonical(Text(row,"profile"),Text(row,"mapId"),Text(row,"floor"),out var matrix,out var reason);
                    Record("adapter",Label(row),new {accepted=ok,reason,matrix=ok ? matrix.ToArray() : null});
                    adapterCount++;
                    Require(ok==row.GetProperty("accepted").GetBoolean(),"Adapter verdict: "+reason);
                    if (ok) Compare(matrix,row.GetProperty("matrix"),"adapter "+Label(row));
                });
            foreach (var row in oracle.RootElement.GetProperty("bridgeCases").EnumerateArray())
            {
                string label=Text(row,"season")+"/"+Text(row,"mode")+"/"+Label(row);
                RunCase("bridge",label,row,() =>
                {
                    var entry=catalog.Entries.Single(e => e.Season==Text(row,"season") && e.Mode==Text(row,"mode")
                        && e.Profile==Text(row,"identityProfile") && e.MapId==Text(row,"mapId") && e.Floor==Text(row,"floor"));
                    var result=resolver.Resolve(catalog,entry,Text(row,"profile"),Affine(row.GetProperty("referenceToClient")));
                    RecordBridge("bridge",label,result);
                    bridgeCount++;
                    if (result.Accepted) accepted.Add(result);
                    Require(result.Accepted==row.GetProperty("accepted").GetBoolean(),"Bridge verdict: "+result.Reason);
                    Require(!result.AuthorizesMapIdentity && !result.AuthorizesStructurePose && !result.AuthorizesCrossFloorProjection,"Display granted authority");
                    if (result.Accepted)
                    {
                        var expected=row.GetProperty("expected");
                        Compare(result.NativeToCanonical!.Value,expected.GetProperty("nativeToCanonical"),"native/canonical");
                        Compare(result.DisplayToCanonical!.Value,expected.GetProperty("displayToCanonical"),"display/canonical");
                        Compare(result.DisplayToReference!.Value,expected.GetProperty("displayToReference"),"display/reference");
                        Compare(result.DisplayToClient!.Value,expected.GetProperty("displayToClient"),"display/client");
                        Compare(result.CanonicalToClient!.Value,expected.GetProperty("canonicalToClient"),"canonical/client");
                        Require(result.IsCurrent(),"Fresh bridge resource snapshot not current");
                        if (row.GetProperty("imagePath").ValueKind==JsonValueKind.String)
                            Require(string.Equals(result.ImagePath,Path.GetFullPath(Text(row,"imagePath")),StringComparison.OrdinalIgnoreCase),"Bridge used a different real image");
                    }
                });
            }
            foreach (var row in oracle.RootElement.GetProperty("calibrationCases").EnumerateArray())
                RunCase("calibration",Text(row,"label"),row,() =>
                {
                    var fixtureCatalog=ResourceCatalog.Load(Text(row,"resourceRoot"));
                    var entry=fixtureCatalog.Entries.Single(e => e.Season==Text(row,"season") && e.Mode==Text(row,"mode")
                        && e.Profile==Text(row,"identityProfile") && e.MapId==Text(row,"mapId") && e.Floor==Text(row,"floor"));
                    var pose=Affine(row.GetProperty("referenceToClient"));
                    var result=resolver.Resolve(fixtureCatalog,entry,"shared-structure",pose);
                    RecordBridge("calibration",Text(row,"label"),result);
                    calibrationCount++;
                    Require(result.Accepted,"Calibrated clean reference rejected: "+result.Reason);
                    var structure=fixtureCatalog.LoadSharedStructure(entry);
                    Require(structure.Success,"Calibrated NPZ load failed");
                    Require(DisplayAffine.TryRead(structure.Value!.Metadata.GetProperty("calibration").GetProperty("matrixCurrentReferenceToNative"),out var calibration)
                        && !calibration.AlmostEquals(DisplayAffine.Identity),"Fixture did not reach a nonidentity NPZ calibration");
                    var expected=row.GetProperty("expected");
                    var probe=row.GetProperty("probe");
                    var referencePoint=probe.GetProperty("referencePoint").EnumerateArray().Select(n=>n.GetDouble()).ToArray();
                    var canonicalPoint=probe.GetProperty("canonicalPoint").EnumerateArray().Select(n=>n.GetDouble()).ToArray();
                    var clientPoint=probe.GetProperty("clientPoint").EnumerateArray().Select(n=>n.GetDouble()).ToArray();
                    var fromClean=result.DisplayToClient!.Value.Apply(referencePoint[0],referencePoint[1]);
                    var fromCanonical=result.CanonicalToClient!.Value.Apply(canonicalPoint[0],canonicalPoint[1]);
                    Record("calibration-probes",Text(row,"label"),new {calibration=calibration.ToArray(),referencePoint,canonicalPoint,
                        fromClean=new[]{fromClean.X,fromClean.Y},fromCanonical=new[]{fromCanonical.X,fromCanonical.Y},expectedClientPoint=clientPoint});
                    Require(result.DisplayKind=="neutral-structure" && result.ImagePath is null,"Fixture did not use the actual clean NPZ display channel");
                    // These expectations come from the original neutral consumer and direct scalar
                    // geometry. They do not infer display coordinates from a stored author matrix.
                    Require(result.DisplayToReference!.Value.AlmostEquals(DisplayAffine.Identity),"Clean NPZ pixels were transformed out of their own reference domain");
                    Require(result.DisplayToClient!.Value.AlmostEquals(pose),"Clean NPZ pixels did not retain the caller pose");
                    Compare(result.NativeToCanonical!.Value,expected.GetProperty("nativeToCanonical"),"calibrated native/canonical");
                    Compare(result.DisplayToCanonical!.Value,expected.GetProperty("displayToCanonical"),"calibrated clean/canonical");
                    Compare(result.CanonicalToClient!.Value,expected.GetProperty("canonicalToClient"),"calibrated canonical/client");
                    Require(Math.Abs(fromClean.X-clientPoint[0])<=1e-9 && Math.Abs(fromClean.Y-clientPoint[1])<=1e-9,
                        "Clean pixel scalar projection disagrees with original neutral consumer");
                    Require(Math.Abs(fromCanonical.X-clientPoint[0])<=1e-9 && Math.Abs(fromCanonical.Y-clientPoint[1])<=1e-9,
                        "Canonical scalar projection disagrees with independent fixture geometry");
                    Require(result.IsCurrent(),"Calibrated result resource snapshots not current");
                });
            foreach (var row in oracle.RootElement.GetProperty("nativeCornerCases").EnumerateArray())
                RunCase("native-corners",Text(row,"label"),row,() =>
                {
                    var request=new NativeCornerValidationRequest(Text(row,"profile"),Text(row,"mapId"),Text(row,"floor"),Text(row,"variant"),
                        Text(row,"nativeLayout"),row.GetProperty("nativeFloor").GetInt32(),Text(row,"defaultProfile"),Text(row,"canonicalProfile"),
                        Text(row,"authorPath"),Text(row,"nativeMaskPath"),row.GetProperty("nativeWidth").GetInt32(),row.GetProperty("nativeHeight").GetInt32(),
                        row.GetProperty("nativeBytes").GetInt64(),Text(row,"nativeRelativePath"),Affine(row.GetProperty("primaryProfileToCanonical")),
                        Affine(row.GetProperty("primaryProfileToNative")),Affine(row.GetProperty("candidateProfileToCanonical")),row.GetProperty("evidence"));
                    var verdict=raster.ValidateNativeCorners(request);
                    Record("native-corners",Text(row,"label"),verdict);
                    cornersCount++;
                    Require(verdict.Accepted==row.GetProperty("accepted").GetBoolean(),"Native corner verdict: "+string.Join(';',verdict.Reasons));
                });
            foreach (var legacy in catalog.Entries.Where(e => e.StructureKind=="legacy-clean"))
                RunCase("rejection",legacy.StableKey,null,() =>
                {
                    var result=resolver.Resolve(catalog,legacy,"zhan10-speed",DisplayAffine.Identity);
                    RecordBridge("rejection",legacy.StableKey,result);
                    Require(!result.Accepted && result.Reason=="legacy-clean-native-bridge-unavailable","Legacy-clean was silently treated as native");
                });
            var example=catalog.Entries.First(e => e.StructureStatus==ResourceBindingStatus.Ready && e.StructureKind!="legacy-clean");
            Rejection("singular-client-pose",() => resolver.Resolve(catalog,example,"shared-structure",new(0,0,0,0,0,0)));
            Rejection("foreign-variant",() => resolver.Resolve(catalog,example,"shared-structure",DisplayAffine.Identity,"other-variant"));
            Rejection("foreign-catalog-owner",() => resolver.Resolve(ResourceCatalog.Load(root),example,"shared-structure",DisplayAffine.Identity));

            RunCase("replacement","s1-same-path-same-length",null,() =>
            {
                var original=accepted.FirstOrDefault(r => r.DisplayKind=="game-native" && r.ImagePath is not null)
                    ?? throw new InvalidOperationException("No accepted real S1 image available for replacement regression");
                string scratch=NewScratch("image-replacement");
                foreach (var resource in original.Resources!) Copy(resource.Path,scratch);
                string relativeScope=Path.GetRelativePath(root,original.ImagePath!).Replace('\\','/').Split("/assets/")[0];
                Copy(Path.Combine(root,relativeScope,"references/map-pack.json"),scratch);
                Copy(Path.Combine(root,relativeScope,"references/structure-version-bindings.json"),scratch);
                var clone=ResourceCatalog.Load(scratch);
                var entry=clone.Entries.Single(e => e.StableKey==original.BindingKey);
                var initial=resolver.Resolve(clone,entry,"shared-structure",DisplayAffine.Identity);
                RecordBridge("replacement","s1-before-replacement",initial);
                Require(initial.Accepted,"Copied actual binding failed: "+initial.Reason);
                byte[] bytes=File.ReadAllBytes(initial.ImagePath!);
                bytes[^1]^=1;
                File.WriteAllBytes(initial.ImagePath!,bytes);
                bool priorCurrent=initial.IsCurrent();
                var replaced=resolver.Resolve(clone,entry,"shared-structure",DisplayAffine.Identity);
                RecordBridge("replacement","s1-after-same-length-replacement",replaced);
                Record("replacement","s1-stale-snapshot",new {priorCurrent,scratch});
                Require(!priorCurrent,"Same-length replacement retained stale resource authority");
                Require(!replaced.Accepted,"Changed real image admitted: "+replaced.Reason);
            });
            RunCase("source-binding","difficult-declared-source-height",null,() =>
            {
                var original=accepted.FirstOrDefault(r => r.BindingKey.StartsWith("s0/difficult/",StringComparison.Ordinal)
                    && r.DisplayKind=="neutral-structure") ?? throw new InvalidOperationException("No accepted difficult binding available");
                string scratch=NewScratch("source-height-replacement");
                foreach (var resource in original.Resources!) Copy(resource.Path,scratch);
                var manifest=JsonNode.Parse(File.ReadAllBytes(Path.Combine(root,"difficult-native-manifest.json")))!;
                var api=manifest["runtimeInterfaces"]!;
                Copy(Path.Combine(root,api["identityPack"]!.GetValue<string>()),scratch);
                Copy(Path.Combine(root,api["versionBindings"]!.GetValue<string>()),scratch);
                var clone=ResourceCatalog.Load(scratch);
                var entry=clone.Entries.Single(e => e.StableKey==original.BindingKey);
                var initial=resolver.Resolve(clone,entry,"shared-structure",DisplayAffine.Identity);
                RecordBridge("source-binding","difficult-before-source-height-change",initial);
                Require(initial.Accepted,"Copied difficult binding failed: "+initial.Reason);
                string sourcePath=Path.Combine(scratch,api["sourceManifest"]!.GetValue<string>());
                var source=JsonNode.Parse(File.ReadAllBytes(sourcePath))!;
                var map=source["maps"]!.AsArray().Single(m => m!["id"]!.GetValue<string>()==entry.MapId)!;
                var floor=map["floors"]![entry.Floor]!;
                floor["sourceHeight"]=floor["sourceHeight"]!.GetValue<int>()+1;
                File.WriteAllText(sourcePath,source.ToJsonString(JsonOptions));
                bool priorCurrent=initial.IsCurrent();
                var replaced=resolver.Resolve(clone,entry,"shared-structure",DisplayAffine.Identity);
                RecordBridge("source-binding","difficult-after-source-height-change",replaced);
                Record("source-binding","difficult-stale-source-snapshot",new {priorCurrent,scratch});
                Require(!priorCurrent,"Changed source manifest retained stale snapshot authority");
                Require(!replaced.Accepted && replaced.Reason=="native-source-manifest-floor-binding-mismatch","Wrong independently declared sourceHeight admitted");
            });
            Flush();
            Console.WriteLine(JsonSerializer.Serialize(new {adapters=adapterCount,bridges=bridgeCount,nativeCorners=cornersCount,calibrations=calibrationCount,
                accepted=accepted.Count,failures=failures.Count,evidenceDir,
                scope="production resources / static and synthetic poses; no live game acceptance"}));
            return failures.Count==0 ? 0 : 1;

            void RunCase(string kind,string label,JsonElement? expected,Action action)
            {
                try { action(); }
                catch (Exception error)
                {
                    failures.Add(new {kind,label,message=error.Message,exception=error.GetType().FullName,expected=expected?.Clone()});
                    Record(kind,label+"/failure",new {exception=error.GetType().FullName,message=error.Message});
                    Console.Error.WriteLine($"FAIL {kind} {label}: {error.Message}");
                }
            }
            void Record(string kind,string label,object actual)
            {
                var item=new {sequence=actualCases.Count+1,kind,label,actual};
                actualCases.Add(item);
                // Persist actual verdicts/matrices before comparing against the oracle.
                File.WriteAllText(Path.Combine(evidenceDir,"cases",$"{actualCases.Count:D4}-{kind}.json"),JsonSerializer.Serialize(item,JsonOptions));
            }
            void RecordBridge(string kind,string label,DisplayBridgeResult result) => Record(kind,label,new
            {
                accepted=result.Accepted,reason=result.Reason,bindingKey=result.BindingKey,profile=result.Profile,
                imagePath=result.ImagePath,displayKind=result.DisplayKind,width=result.Width,height=result.Height,
                nativeToCanonical=result.NativeToCanonical?.ToArray(),displayToCanonical=result.DisplayToCanonical?.ToArray(),
                displayToReference=result.DisplayToReference?.ToArray(),displayToClient=result.DisplayToClient?.ToArray(),
                canonicalToClient=result.CanonicalToClient?.ToArray(),resources=result.Resources,
                authorizesMapIdentity=result.AuthorizesMapIdentity,authorizesStructurePose=result.AuthorizesStructurePose,
                authorizesCrossFloorProjection=result.AuthorizesCrossFloorProjection
            });
            void Flush() => File.WriteAllText(Path.Combine(evidenceDir,"display-actual.json"),JsonSerializer.Serialize(new
                {adapters=adapterCount,bridges=bridgeCount,nativeCorners=cornersCount,calibrations=calibrationCount,failures,actualCases},JsonOptions));
            void Rejection(string label,Func<DisplayBridgeResult> action) => RunCase("rejection",label,null,() =>
            {
                var result=action();RecordBridge("rejection",label,result);Require(!result.Accepted,"Invalid input admitted");
            });
            string NewScratch(string label)
            {
                string path=Path.Combine(evidenceDir,"scratch",label+"-"+Guid.NewGuid().ToString("N"));
                Directory.CreateDirectory(path);return path;
            }
            void Copy(string source,string scratch)
            {
                string target=Path.GetFullPath(Path.Combine(scratch,Path.GetRelativePath(root,source)));
                Require(target.StartsWith(scratch+Path.DirectorySeparatorChar,StringComparison.OrdinalIgnoreCase),"Fixture copy escaped scratch");
                Directory.CreateDirectory(Path.GetDirectoryName(target)!);File.Copy(source,target,true);
            }
        }
        catch (Exception error)
        {
            Console.Error.WriteLine(error.GetType().Name+": "+error.Message);
            if (evidenceDir is not null && Directory.Exists(evidenceDir))
            {
                try { File.WriteAllText(Path.Combine(evidenceDir,"fatal-"+Guid.NewGuid().ToString("N")+".json"),
                    JsonSerializer.Serialize(new {error=error.Message,exception=error.GetType().FullName,actualCases,failures},JsonOptions)); }
                catch (Exception writeError) { Console.Error.WriteLine("Evidence write failed: "+writeError.Message); }
            }
            return 1;
        }
    }
    private static string Text(JsonElement row,string key)=>row.GetProperty(key).GetString()!;
    private static string Label(JsonElement row)=>$"{Text(row,"mapId")}/{Text(row,"floor")}/{Text(row,"profile")}";
    private static DisplayAffine Affine(JsonElement row)
    {
        var a=row.EnumerateArray().Select(r=>r.EnumerateArray().Select(n=>n.GetDouble()).ToArray()).ToArray();
        return new(a[0][0],a[0][1],a[0][2],a[1][0],a[1][1],a[1][2]);
    }
    private static void Compare(DisplayAffine actual,JsonElement expected,string context)=>Require(actual.AlmostEquals(Affine(expected),1e-9),context+": matrix differs from production Python");
    private static void Require(bool condition,string message){if(!condition)throw new InvalidOperationException(message);}
}
