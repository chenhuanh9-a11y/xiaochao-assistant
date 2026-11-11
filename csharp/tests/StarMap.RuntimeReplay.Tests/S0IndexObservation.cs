using System.Diagnostics;
using System.Text.Json;
using System.Text.Json.Nodes;
using OpenCvSharp;
using StarMap.Core.Identity;
using StarMap.Core.Resources;
using StarMap.Core.Selection;
using StarMap.Core.Session;
using StarMap.Runtime;
using StarMap.Vision;

// Observe the actual S0 entry and probe the existing candidate-independent
// glyph locator. Reusing a template here is not production S0 coordinate authority.
internal static class S0IndexObservation
{
    internal static async Task Run(string resources,string replay,string assets,string output,Action<bool,string> check,string? markerSource=null)
    {
        using var markerFile=markerSource is null?null:JsonDocument.Parse(await File.ReadAllBytesAsync(markerSource));
        var markerCatalog=markerFile is null?null:new StaticExitMarkerCatalog(markerFile.RootElement);
        var index=JsonNode.Parse(await File.ReadAllBytesAsync(Path.Combine(replay,"all-samples.json")))!["samples"]!.AsArray()
            .ToDictionary(row=>row!["id"]!.GetValue<string>(),row=>row!.AsObject());
        var rows=new List<object>();
        var resourceCatalog=ResourceCatalog.Load(resources);
        var registered=new Dictionary<string,(EntryPassportCatalog Passports,StructureCatalog Structure)>();
        StructureCandidatePool? sideLedger=null;
        using var locator=new StructureGateAnchorLocator(assets);
        foreach(var id in new[]{"batch01-event-000006","batch02-event-000053","batch02-event-000147","batch02-event-000097","batch02-event-000099"})
        {
            var row=index[id];string mode=row["mode"]!.GetValue<string>();
            check(row["season"]!.GetValue<string>()=="s0" && DateTimeOffset.Parse(row["capturedAt"]!.GetValue<string>())
                >=new DateTimeOffset(2026,11,5,0,0,0,TimeSpan.Zero),id+" uses the actual new S0 UI capture");
            using var bgr=Cv2.ImDecode(await File.ReadAllBytesAsync(row["image"]!.GetValue<string>()),ImreadModes.Color);
            var watch=Stopwatch.StartNew();var anchor=locator.Detect(bgr);double anchorMs=watch.Elapsed.TotalMilliseconds;
            if(!registered.TryGetValue(mode,out var registration))
            {
                var actual=EntryPassportCatalog.Load(resources,"s0",mode);
                registration=(actual,StructureCatalog.FromS0Resources(resourceCatalog,actual,"observed-resource-generation-"+Guid.NewGuid().ToString("N")));
                registered.Add(mode,registration);
            }
            var passports=registration.Passports;
            check(registration.Structure.IsBoundTo(resourceCatalog)
                &&registration.Structure.MapIds.ToHashSet().SetEquals(passports.MapProfiles.Keys),id+" generic registration retains the actual S0 map/resource scope");
            check(passports.PackFloors.All(pair=>ReferenceEquals(registration.Structure.ReferenceFor(resourceCatalog,pair.MapId,pair.Floor),
                passports.UnavailablePackFloors.Contains(pair)?null:resourceCatalog.Find(mode,"s0",passports.MapProfiles[pair.MapId],pair.MapId,pair.Floor))),
                id+" generic references preserve per-map profiles and missing floors");
            bool wrongOwnerRejected=false;
            try{registration.Structure.ReferenceFor(ResourceCatalog.Load(resources),registration.Structure.MapIds[0],registration.Structure.Floors[0]);}
            catch(ArgumentException){wrongOwnerRejected=true;}
            check(wrongOwnerRejected,id+" a separate resource owner cannot borrow the registered references");
            using var exitObserver=new SideDoorObservation(passports.ExitIconPath!);
            watch.Restart();var exits=exitObserver.Observe(bgr);double exitMs=watch.Elapsed.TotalMilliseconds;
            using var s0Observer=new S0StructureGateObserver(passports.ExitIconPath!);
            using(var stopped=new CancellationTokenSource())
            {
                stopped.Cancel();bool cancelled=false;
                try{s0Observer.Detect(bgr,stopped.Token);}catch(OperationCanceledException){cancelled=true;}
                check(cancelled,id+" cancelled S0 glyph work produces no anchor");
            }
            watch.Restart();var s0Anchor=s0Observer.Detect(bgr);double s0AnchorMs=watch.Elapsed.TotalMilliseconds;
            var capture=new RecordedCapture(index);capture.Select(id,false);
            var runtime=new LiveRuntime(capture,resources);var sequence=new SessionSequencer();
            try
            {
                await Ask("set_context",new{season="s0",mode,profileId="shared-structure"});
                await Ask("start_round");var result=await Ask("capture_now");var state=runtime.ReadState();
                runtime.TryReadRender(out var packet);
                JsonObject? structureIndex=null;
                object? sharedLedger=null;
                if(markerCatalog is not null&&state.Context.FloorId is { } observedFloor)
                {
                    using var structural=new StructureIndexFilter(resourceCatalog,registration.Structure,assets,passports,markerCatalog);
                    using var preparedFrame=new StructureFramePreparation(bgr);
                    var gate=structural.DetectAnchor(preparedFrame);
                    var planned=id=="batch02-event-000099"&&sideLedger is not null?sideLedger.Remaining:registration.Structure.MapIds;
                    structureIndex=structural.Evaluate(preparedFrame,planned,observedFloor,gate);
                    File.WriteAllText(Path.Combine(output,id+"-index.json"),structureIndex.ToJsonString(new JsonSerializerOptions{WriteIndented=true}));
                    check(structureIndex["identityAuthorized"]!.GetValue<bool>()==false&&structureIndex["poseAvailable"]?.GetValue<bool>()!=true,
                        id+" multi-door coordinate evidence cannot grant identity or a unique pose");
                    if(id=="batch02-event-000147")
                    {
                        check(structureIndex["coordinateVerified"]?.GetValue<bool>()==true
                            &&structureIndex["candidateChecks"]?.AsArray().Count==registration.Structure.MapIds.Count,
                            id+" real S0 Filter reaches complete candidate checks with per-map unknowns");
                        check(structureIndex["candidateChecks"]!.AsArray().Count(node=>node!["coordinateReason"]!.GetValue<string>()=="complete-source-bound-local-field")
                            ==passports.MapProfiles.Count(pair=>pair.Value=="shared-structure"),
                            id+" every declared native difficult reference participates without integer-roundtrip rejection");
                        check(packet is not null&&!structureIndex["contradictedMapIds"]!.AsArray().Any(node=>node!.GetValue<string>()==packet.MapId),
                            id+" independently supported conditional reference is not falsely excluded");
                        check(structural.ReferenceToClient(structureIndex,packet!.MapId,observedFloor) is null,
                            id+" multi-door comparison cannot export a representative as a native pose");
                        var foreignAnchor=structural.DetectAnchor(bgr);
                        var foreign=structural.Evaluate(preparedFrame,registration.Structure.MapIds,observedFloor,foreignAnchor);
                        check(foreign["coordinateVerified"]!.GetValue<bool>()==false&&foreign["reason"]!.GetValue<string>()=="coordinate-anchor-frame-mismatch",
                            id+" pixel-identical different-frame anchor cannot be reused by the Filter");
                    }
                    if(id=="batch02-event-000097")
                    {
                        check(structureIndex["coordinateVerified"]?.GetValue<bool>()==true
                            &&structureIndex["candidateChecks"]?.AsArray().Count==registration.Structure.MapIds.Count
                            &&structureIndex["contradictedMapIds"]!.AsArray().Count>0,
                            id+" sparse door-bound structure supplies real positive contradictions across the full S0 pool");
                        check(packet is not null&&!structureIndex["contradictedMapIds"]!.AsArray().Any(node=>node!.GetValue<string>()==packet.MapId),
                            id+" source Runtime conditional reference survives the sparse side observation");
                        sideLedger=new("recorded-side-ledger",registration.Structure.ResourceKey,registration.Structure.MapIds);
                        string update=Feed(sideLedger,structureIndex);
                        check(update=="structure-pool-narrowed"&&sideLedger.Remaining.Count<registration.Structure.MapIds.Count,
                            id+" actual Filter report narrows the shared Core candidate ledger");
                        sharedLedger=new{update,remaining=sideLedger.Remaining.ToArray(),scope="Actual Core ledger replay; Runtime index route is not enabled by this probe."};
                    }
                    if(id=="batch02-event-000099")
                    {
                        check(sideLedger is not null,id+" sparse target-floor ledger regression cannot skip its preceding narrowing");
                        var before=sideLedger!.Remaining.ToArray();string update=Feed(sideLedger,structureIndex);
                        check(structureIndex["coordinateVerified"]!.GetValue<bool>()==false&&before.SequenceEqual(sideLedger.Remaining)
                            &&sideLedger.Remaining.Count<registration.Structure.MapIds.Count,
                            id+" unknown sparse target-floor evidence keeps the narrowed pool without restoring all maps");
                        sharedLedger=new{update,before,remaining=sideLedger.Remaining.ToArray(),scope="Cross-floor Core ledger regression from actual reports; not live Runtime route acceptance."};
                    }
                }
                if(id=="batch02-event-000147"&&markerCatalog is not null)
                    check(packet is not null&&state.Context.FloorId is { } currentFloor
                        &&registration.Structure.ReferenceFor(resourceCatalog,packet.MapId,currentFloor) is not null
                        &&s0Anchor.CenterSupportBoundsPx is {Length:4},
                        id+" mandatory front-marker regression reaches material, floor, reference and glyph support");
                object? markerProbe=null;
                if(packet is not null && state.Context.FloorId is { } sourceFloor
                    &&registration.Structure.ReferenceFor(resourceCatalog,packet.MapId,sourceFloor) is { } sourceEntry)
                {
                    using var independent=bgr.Clone();
                    if(s0Anchor.CenterSupportBoundsPx is {Length:4} support)
                    {
                        var erase=new Rect((int)Math.Floor(support[0]),(int)Math.Floor(support[1]),
                            (int)Math.Ceiling(support[2])-(int)Math.Floor(support[0]),(int)Math.Ceiling(support[3])-(int)Math.Floor(support[1]));
                        Cv2.Rectangle(independent,erase.Intersect(new(0,0,bgr.Cols,bgr.Rows)),Scalar.All(0),-1);
                    }
                    using var fitter=new SharedStructureFitPipeline(resourceCatalog);
                    var independentFit=fitter.Fit(independent,sourceEntry);
                    using var originalHost=new S0HostFit(resourceCatalog,passports);
                    var hostResult=originalHost.Fit(independent,sourceEntry);
                    var reference=resourceCatalog.LoadSharedStructure(sourceEntry).Value;
                    if(id=="batch02-event-000147"&&markerCatalog is not null)
                        check(reference is not null,id+" mandatory front-marker reference actually loads");
                    var projected=new List<object>();
                    var staticMarkers=reference is null?null:markerCatalog?.Resolve(sourceEntry,reference);
                    var sourceProjections=new List<object>();
                    var hostProjections=new List<object>();
                    bool verifiedPose=StructureEvidence.VerifiedPose(independentFit);
                    bool hostVerifiedPose=StructureEvidence.VerifiedPose(hostResult.Fit);
                    if(markerCatalog is not null&&reference is not null)
                    {
                        check(mode=="difficult"?staticMarkers?.CoverageComplete==true:staticMarkers?.CoverageComplete==false,
                            id+" static markers require the actual reference source-catalog binding");
                        if(mode=="difficult")
                        {
                            var wrong=JsonNode.Parse(markerFile!.RootElement.GetRawText())!.AsObject();
                            wrong["sources"]!["catalogSha256"]="unrelated-source";
                            check(!new StaticExitMarkerCatalog(JsonSerializer.SerializeToElement(wrong)).Resolve(sourceEntry,reference).CoverageComplete,
                                id+" mismatched static source cannot supply coordinates");
                            var partial=JsonNode.Parse(markerFile.RootElement.GetRawText())!.AsObject();
                            foreach(var record in partial["records"]!.AsArray())record!["staticExitInputsComplete"]=false;
                            check(!new StaticExitMarkerCatalog(JsonSerializer.SerializeToElement(partial)).Resolve(sourceEntry,reference).CoverageComplete,
                                id+" incomplete static marker coverage cannot authorize exclusions");
                            var other=resourceCatalog.Entries.First(entry=>entry.Season=="s0"&&entry.Mode==mode&&entry.MapId!=sourceEntry.MapId
                                &&entry.Profile==sourceEntry.Profile&&entry.Floor==sourceEntry.Floor);
                            check(!markerCatalog.Resolve(other,reference).CoverageComplete,
                                id+" a different map entry cannot borrow the reference coordinates");
                            if(id=="batch02-event-000147")
                            {
                                // Isolated metadata bridge test, not a simulated
                                // game pose: currentRef -> native and renderer ->
                                // product must be composed in their declared order.
                                var meta=JsonNode.Parse(reference.Metadata.GetRawText())!.AsObject();
                                meta["sourceGeometry"]!["matrixRendererPixelToProduct"]=JsonSerializer.SerializeToNode(new double[][]{[.5,0,-7],[0,.5,-11],[0,0,1]});
                                meta["calibration"]=new JsonObject{["matrixCurrentReferenceToNative"]=JsonSerializer.SerializeToNode(new double[][]{[2,0,10],[0,2,20],[0,0,1]})};
                                var constructor=typeof(SharedStructureData).GetConstructors(System.Reflection.BindingFlags.Instance|System.Reflection.BindingFlags.NonPublic).Single();
                                var transformed=(SharedStructureData)constructor.Invoke([JsonSerializer.SerializeToElement(meta),reference.CleanMask,
                                    reference.ValidationMask,reference.KnownBoundary,reference.UnknownMask,reference.NativeWalls]);
                                var transformedMarkers=markerCatalog.Resolve(sourceEntry,transformed);
                                check(transformedMarkers.CoverageComplete&&transformedMarkers.Points.Any(point=>point.Kind=="front-door"&&point.X==146.5&&point.Y==488.5),
                                    id+" isolated product/crop/calibration composition retains genuinely fractional marker coordinates");
                            }
                        }
                    }
                    if(independentFit["matrixSourceToClient"] is { } matrix && s0Anchor.CenterPx is { } observed)
                    {
                        double[][] m=matrix.Deserialize<double[][]>()!;
                        foreach(var feature in passports.SideEntries.Where(feature=>feature.GetProperty("mapId").GetString()==packet.MapId
                            &&feature.GetProperty("floor").GetString()==sourceFloor))
                        foreach(var gate in feature.GetProperty("gates").EnumerateArray())
                        {
                            double x=gate.GetProperty("center")[0].GetDouble(),y=gate.GetProperty("center")[1].GetDouble();
                            var(cx,cy)=Project(m,x,y);
                            projected.Add(new{referenceCenter=new[]{x,y},clientCenter=new[]{cx,cy},
                                errorToObservedGlyph=Math.Sqrt((cx-observed.X)*(cx-observed.X)+(cy-observed.Y)*(cy-observed.Y)),gate});
                        }
                        foreach(var point in staticMarkers?.Points??[])
                        {
                            var(cx,cy)=Project(m,point.X,point.Y);
                            sourceProjections.Add(new{point.Kind,referenceCenter=new[]{point.X,point.Y},clientCenter=new[]{cx,cy},
                                errorToObservedGlyph=Math.Sqrt((cx-observed.X)*(cx-observed.X)+(cy-observed.Y)*(cy-observed.Y))});
                        }
                    }
                    if(id=="batch02-event-000147"&&markerCatalog is not null&&s0Anchor.CenterSupportBoundsPx is { } glyphSupport)
                    {
                        var m=independentFit["matrixSourceToClient"]!.Deserialize<double[][]>()!;
                        check(verifiedPose&&staticMarkers?.CoverageComplete==true&&staticMarkers.Points.Any(point=>
                            {var(x,y)=Project(m,point.X,point.Y);return point.Kind=="front-door"&&x>=glyphSupport[0]
                                &&x<=glyphSupport[2]&&y>=glyphSupport[1]&&y<=glyphSupport[3];}),
                            id+" independently fitted front marker projects inside the observed glyph support");
                    }
                    if(hostVerifiedPose&&hostResult.Fit["matrixSourceToClient"] is { } hostMatrix&&s0Anchor.CenterPx is { } glyphCenter)
                    {
                        var m=hostMatrix.Deserialize<double[][]>()!;
                        foreach(var point in staticMarkers?.Points??[])
                        {
                            var(x,y)=Project(m,point.X,point.Y);
                            hostProjections.Add(new{point.Kind,clientCenter=new[]{x,y},
                                errorToObservedGlyph=Math.Sqrt((x-glyphCenter.X)*(x-glyphCenter.X)+(y-glyphCenter.Y)*(y-glyphCenter.Y))});
                        }
                    }
                    markerProbe=new{map=packet.MapId,floor=sourceFloor,sourceEntry.Profile,sourceEntry.StructureKind,
                        sourceKind=reference?.Metadata.GetProperty("sourceKind").GetString(),canvas=reference?.Metadata.GetProperty("canvas"),
                        staticMarkers,sourceProjections,verifiedPose,independentFit,projected,hostVerifiedPose,hostFit=hostResult.Fit,hostResult.Stages,hostProjections,
                        scope="Conditional target is the actual Runtime material map, not injected identity truth. Independent full-client observational fit receives no pose/seed and glyph raster is blacked out in a clone only. Compiled side markers are partial, not a complete front/side coordinate source. Projection agreement cannot grant identity or validate every marker."};
                }
                rows.Add(new{id,mode,image=row["image"]!.GetValue<string>(),capturedAt=row["capturedAt"]!.GetValue<string>(),
                    anchor=anchor.ToJson(),anchorMs,s0Anchor=s0Anchor.ToJson(),s0AnchorMs,originalS0Exit=new{passports.ExitIconPath,exits.Reason,exits.Anchors,exits.Observations,exits.Boundary,
                        bounds=exits.Bounds is { } b?new{b.X,b.Y,b.Width,b.Height}:null,elapsedMs=exitMs},
                    registration=new{registration.Structure.Season,registration.Structure.Mode,registration.Structure.MapIds,registration.Structure.Floors,
                        profiles=passports.MapProfiles},structureIndex,sharedLedger,markerProbe,result.Receipt,state,hasRender=packet is not null,materialMap=packet?.MapId,captureCalls=capture.Calls});
                File.WriteAllText(Path.Combine(output,"s0-index-observation.json"),JsonSerializer.Serialize(new{rows,
                    assets=Path.GetFullPath(assets),markerSource,locator.TemplateSha256,locator.ProfileSha256,
                    scope="Actual S0 Runtime/source solvers/post-Nov5 recorded capture, plus standalone existing glyph locator probe using S1-declared assets and the original S0 no-context exit observer using its declared icon. This observes compatibility only: it neither supplies a synthetic coordinate index nor authorizes S0 gate/entrance coordinates. Runtime state/receipt and unlocated/ambiguous probes are retained. No native capture/input/game or unique identity truth."},
                    new JsonSerializerOptions{WriteIndented=true}));
                check(!runtime.ReadSnapshot().LiveAuthorization,id+" recorded probe cannot grant Live authority");
                check(id.EndsWith("000099",StringComparison.Ordinal)?!s0Anchor.GlyphPresenceVerified:s0Anchor.Status=="located",
                    id+" S0 appearance adapter follows the visible glyph rather than a geometry-only exit");
                check(s0Anchor.Diagnostics["coordinates_authorized"]!.GetValue<bool>()==false,
                    id+" glyph evidence does not create native marker coordinates");
            }
            finally{runtime.Shutdown();}
            Task<RuntimeOperationResult> Ask(string action,object? payload=null)=>runtime.DispatchAsync(new(action,
                JsonSerializer.SerializeToElement(payload??new{}),new(sequence.Issue(),runtime.ReadSnapshot().Context)),CancellationToken.None).AsTask();
        }
    }

    private static (double X,double Y) Project(double[][] matrix,double x,double y) =>
        (matrix[0][0]*x+matrix[0][1]*y+matrix[0][2],matrix[1][0]*x+matrix[1][1]*y+matrix[1][2]);

    private static string Feed(StructureCandidatePool pool,JsonObject report) => pool.Observe(
        report["coordinateVerified"]!.GetValue<bool>(),report["evaluatedMapIds"]!.AsArray().Select(node=>node!.GetValue<string>()).ToArray(),
        report["contradictedMapIds"]!.AsArray().Select(node=>node!.GetValue<string>()).ToArray(),
        report["candidateChecks"]?.AsArray().Select(node=>new StructureIndexCheck(node!["mapId"]!.GetValue<string>(),node["explicitContradiction"]!.GetValue<bool>())).ToArray()??[]);
}
