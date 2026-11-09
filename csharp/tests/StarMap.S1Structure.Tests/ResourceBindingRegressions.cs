using System.Text.Json;
using System.Text.Json.Nodes;
using OpenCvSharp;
using StarMap.Core.Resources;
using StarMap.Core.Selection;
using StarMap.Vision;

internal static class ResourceBindingRegressions
{
    public static int Run(string resourceRoot,string fixtureRoot,string output)
    {
        if (Directory.Exists(output)) throw new IOException("Use a new regression output directory.");
        Directory.CreateDirectory(output);
        var json = new JsonSerializerOptions { WriteIndented = true };
        var fixture = JsonNode.Parse(File.ReadAllBytes(Path.Combine(fixtureRoot,"fixture.json")))!;
        string Text(string key) => fixture[key]!.GetValue<string>();
        string mode = Text("mode"), floor = Text("floor"), otherMode = mode == "nightmare" ? "difficult" : "nightmare";
        var resources = ResourceCatalog.Load(resourceRoot);
        var catalog = StructureCatalog.FromResources(resources,mode);
        var otherCatalog = StructureCatalog.FromResources(resources,otherMode);
        using var frame = Cv2.ImDecode(File.ReadAllBytes(Text("image")),ImreadModes.Color);
        var bounds = new Rect(0,0,frame.Cols,frame.Rows);
        using var pipeline = new SharedStructureFitPipeline(resources);
        var cases = new List<object>(); int failures = 0;
        void Save(string name,object value) => File.WriteAllText(Path.Combine(output,name),JsonSerializer.Serialize(value,json));
        void Record(string name,bool passed,object details)
        {
            if (!passed) failures++;
            cases.Add(new { name,passed,details });
        }
        bool ExpectedRejection(Action operation,out string? error)
        {
            error = null;
            try { operation(); return false; }
            catch (Exception exception) when (exception is InvalidDataException or ArgumentException or InvalidOperationException)
            { error = exception.GetType().Name+": "+exception.Message; return true; }
        }

        var positive = pipeline.CreateScanner(catalog).Scan(frame,new(floor));
        var positiveSession = new StructureEvidenceSession(catalog,"resource-binding-positive");
        var positiveTicket = positiveSession.BeginObservation(floor);
        bool positiveAccepted = positiveSession.Commit(positive,positiveTicket);
        string? positiveMap = positive["supportedMapIds"]!.AsArray().FirstOrDefault()?.GetValue<string>();
        bool preparePositive = false;
        using (var prepared = pipeline.PrepareScan(catalog,frame,bounds))
            if (positiveMap is not null) preparePositive = StructureEvidence.VerifiedPose(prepared.Fit(frame,positiveMap,floor,()=>false,bounds));
        Record("same-owner-complete-catalog-positive",positiveAccepted && preparePositive
            && positive["checkedMapIds"]!.AsArray().Count == catalog.MapIds.Count
            && positive["resourceKey"]!.GetValue<string>() == catalog.ResourceKey,
            new { positiveAccepted,preparePositive,checkedCount=positive["checkedMapIds"]!.AsArray().Count,catalogCount=catalog.MapIds.Count });
        Save("same-owner-report.json",positive);

        void InvalidCatalog(string name,StructureCatalog invalid,bool runOldProducer)
        {
            StructureCandidateScanner? scanner = null;
            bool createRejected = ExpectedRejection(()=>scanner=pipeline.CreateScanner(invalid),out string? createError);
            Record(name+"-CreateScanner",createRejected,new { createRejected,createError,invalid.ResourceKey,invalid.ModeRoot,mapIds=invalid.MapIds });
            bool prepareRejected = ExpectedRejection(()=> { using var prepared=pipeline.PrepareScan(invalid,frame,bounds); },out string? prepareError);
            Record(name+"-PrepareScan",prepareRejected,new { prepareRejected,prepareError });
            if (!createRejected && runOldProducer)
            {
                // This records the real pre-fix failure path. No fit, prior or result is substituted.
                var actual = scanner!.Scan(frame,new(floor));
                var session = new StructureEvidenceSession(invalid,"invalid-binding-"+name);
                bool accepted = session.Commit(actual,session.BeginObservation(floor));
                Save(name+"-wrong-resource-report.json",actual);
                Save(name+"-wrong-resource-session.json",new { accepted,snapshot=session.Snapshot(),solverResourceRoot=resources.RootPath,
                    reportedResourceKey=actual["resourceKey"]!.GetValue<string>(),actualCatalogResourceKey=catalog.ResourceKey });
            }
        }
        InvalidCatalog("foreign-resource-key",new(catalog.Season,catalog.Mode,otherCatalog.ResourceKey,catalog.MapIds,catalog.Floors,catalog.MapNames,catalog.ModeRoot),true);
        InvalidCatalog("different-mode-root",new(catalog.Season,catalog.Mode,catalog.ResourceKey,catalog.MapIds,catalog.Floors,catalog.MapNames,otherCatalog.ModeRoot),false);
        InvalidCatalog("shortened-catalog",new(catalog.Season,catalog.Mode,catalog.ResourceKey,catalog.MapIds.SkipLast(1),catalog.Floors,catalog.MapNames,catalog.ModeRoot),true);
        var otherOwner = ResourceCatalog.Load(resourceRoot);
        InvalidCatalog("foreign-owner-same-content",StructureCatalog.FromResources(otherOwner,mode),false);

        string copiedRoot = Text("copiedResourceRoot");
        var copiedResources = ResourceCatalog.Load(copiedRoot);
        StructureCatalog? copiedCatalog = null;
        bool inventoryRejected = ExpectedRejection(()=>copiedCatalog=StructureCatalog.FromResources(copiedResources,mode),out string? inventoryError);
        var originalProof = fixture["python"]!;
        bool pythonRejected = originalProof["missingInventoryRejected"]!.GetValue<bool>() && originalProof["changedPackRejected"]!.GetValue<bool>();
        Record("coordinate-pack-must-belong-to-inventory",inventoryRejected && pythonRejected,
            new { inventoryRejected,inventoryError,pythonRejected,coordinatePackStillExists=File.Exists(Text("coordinatePackPath")),
                changedPackId=Text("changedPackId"),python=originalProof.DeepClone() });
        if (!inventoryRejected)
        {
            using var copiedPipeline = new SharedStructureFitPipeline(copiedResources);
            var actual = copiedPipeline.CreateScanner(copiedCatalog!).Scan(frame,new(floor));
            Save("unlisted-coordinate-pack-report.json",actual);
            var ids = actual["supportedMapIds"]!.AsArray();
            var packs = ids.Select(id=>actual["poses"]![id!.GetValue<string>()]!["selectedRouteCurrentFrameResidual"]!["coordinatePackId"]!.GetValue<string>()).Distinct().ToArray();
            Save("unlisted-coordinate-pack-consumption.json",new { copiedCatalog!.ResourceKey,packIds=packs,expectedUnlistedPack=Text("changedPackId"),supported=ids.DeepClone() });
        }
        var summary = new { passed=failures==0,failures,cases,
            scope="Real ResourceCatalog/FromResources/CreateScanner/PrepareScan and unchanged solver; Python original rejected the exact evidence clone; no original resource or result mutation" };
        Save("summary.json",summary); Console.WriteLine(JsonSerializer.Serialize(summary,json)); return failures==0?0:1;
    }
}
