using System.Reflection;
using System.Text.Json;
using System.Text.Json.Nodes;
using StarMap.Core.Alignment;
using StarMap.Core.Input;
using StarMap.Core.Session;
using StarMap.Runtime;

internal static class ProjectionAuthorRegression
{
    internal static async Task Run(string resources,string replay,string oracleRoot,string output,Action<bool,string> check)
    {
        TrackingRebase(oracleRoot,check);
        await PendingRefresh(resources,replay,output,check);
        var index=JsonNode.Parse(await File.ReadAllBytesAsync(Path.Combine(replay,"all-samples.json")))!["samples"]!.AsArray()
            .ToDictionary(row=>row!["id"]!.GetValue<string>(),row=>row!.AsObject());
        foreach(string mode in new[]{"nightmare","difficult"})
        foreach(string scenario in mode=="nightmare"?new[]{"cancel-published","cancel-refresh","cancel-before","active","active-human","after-probe","same-profile","explicit-refresh","pending","rejected","closed","failure"}
            :new[]{"cancel-published","cancel-refresh","cancel-before","active","after-probe","same-profile","explicit-refresh","pending","rejected","closed","failure"})
        {
            string name=mode+"/"+scenario,initial=mode=="nightmare"?"batch01-event-000006":"batch02-event-000147";
            string target=index.First(pair=>pair.Value["season"]!.GetValue<string>()=="s0" && pair.Value["mode"]!.GetValue<string>()==mode
                && pair.Value["recordedObservation"]?["floorId"]?.GetValue<string>()=="2"
                && JsonNode.DeepEquals(pair.Value["physicalClient"],index[initial]["physicalClient"])).Key;
            var capture=new RecordedCapture(index);capture.Select(initial,false);
            var runtime=new LiveRuntime(capture,resources);var sequence=new SessionSequencer();
            using var cancellation=new CancellationTokenSource();bool cancelFired=false;
            ProjectionAuthorMaterialHold? hold=null;Task<RuntimeOperationResult>? authorWork=null;
            ProjectionAuthorCoordinateFault? coordinateFault=null;
            try
            {
                runtime.SetIdvb0059Enabled(true);
                await Ask("set_context",new {season="s0",mode,profileId="shared-structure"});
                SyntheticProjectionCoordinates.Install(runtime,resources,mode);
                await Ask("start_round");await Ask("capture_now");
                check(runtime.TryReadRender(out _),name+" actual source solver grants the recorded current floor");
                var context=runtime.PointerContextSlot.Read()!;var g=((GameFloorClickContext)context.DownIntentSnapshot!).Geometry;
                check(runtime.BeginGameMapDrag(Pointer("down",1)).Success,name+" source drag begins");
                Move("move",2,13,-7,13,-7);Move("up",3,15,-6,2,1);
                await Ask("probe_map_after_drag",new {gestureId=1,pointerSequence=3});
                capture.Select(target,false);await Ask("set_floor",new {floor="2"});
                var projection=runtime.ReadPendingGroupMaterial();
                check(projection is {ProjectionOnly:true},name+" only the explicitly synthetic relation data admits pending projection");
                if(scenario=="rejected")check(runtime.RejectGroupMaterial(projection!,"recorded consumer refusal"),name+" reject source projection");
                else if(scenario!="pending")check(runtime.AcceptGroupMaterialReady(projection!),name+" accept source projection");
                if(scenario=="active-human")
                {
                    var state=runtime.ReadState();await Ask("select_group_member",new {mapId=state.Context.MapId,groupId=state.IdentityGroupId,
                        groupGeneration=state.GroupChoice.Generation,cycleId=state.GroupChoice.CycleId,floor=state.Context.FloorId});
                    check(runtime.ReadPendingGroupMaterial() is { } member && runtime.AcceptGroupMaterialReady(member),
                        name+" human identity confirmation replaces current packet before author selection");
                }
                if(scenario=="after-probe")await Ask("refresh_map");
                if(scenario=="closed")await Ask("close_map");
                var before=runtime.ReadState();var trusted=(AlignmentCoreV2)Field(runtime,"_alignment")!;
                string poses=JsonSerializer.Serialize(trusted.PoseByMap);var group=Field(runtime,"_geometryGroup");
                var model=group is null?null:(RoundLayerGroup)group.GetType().GetProperty("Model")!.GetValue(group)!;
                string groupState=JsonSerializer.Serialize(model?.Snapshot());
                runtime.TryReadRender(out var source);int calls=capture.Calls;
                string preferred=scenario is "same-profile" or "explicit-refresh" or "cancel-refresh"?"shared-structure":mode=="nightmare"?"suju":"zhan10-speed";
                RuntimeOperationResult result;
                var payload=new {profileId=preferred,refreshDisplay=scenario is "explicit-refresh" or "cancel-refresh"};
                if(scenario=="failure")coordinateFault=new(runtime,mode,before.Context.MapId!);
                if(scenario is "cancel-published" or "cancel-refresh")
                {
                    hold=new ProjectionAuthorMaterialHold(runtime);
                    Thread? workerThread=null;
                    authorWork=Task.Run(()=>{workerThread=Thread.CurrentThread;return Ask("set_context",payload,cancellation.Token);});
                    long started=System.Diagnostics.Stopwatch.GetTimestamp();
                    while((((SessionSelection)Field(runtime,"_selection")!).ProfileId!=preferred
                        || (Volatile.Read(ref workerThread)?.ThreadState & ThreadState.WaitSleepJoin)==0) && !authorWork.IsCompleted)
                    {if(System.Diagnostics.Stopwatch.GetElapsedTime(started)>TimeSpan.FromSeconds(10))throw new TimeoutException("author preference was not published");await Task.Delay(1);}
                    check(((SessionSelection)Field(runtime,"_selection")!).ProfileId==preferred && !authorWork.IsCompleted && hold.Held,
                        name+" real material cache lock holds the in-flight worker after preference publication");
                    lock(Field(runtime,"_sync")!){cancellation.Cancel();cancelFired=true;}
                    hold.Release();
                    result=await authorWork.WaitAsync(TimeSpan.FromSeconds(20));authorWork=null;hold.Dispose();hold=null;
                }
                else
                {
                    if(scenario=="cancel-before")cancellation.Cancel();
                    result=await Ask("set_context",payload,cancellation.Token);
                }
                coordinateFault?.Dispose();coordinateFault=null;
                var after=runtime.ReadState();
                // Read independent original outputs only after the production transaction.
                var original=JsonNode.Parse(await File.ReadAllBytesAsync(Path.Combine(oracleRoot,
                    "phase27-original-projection-author-"+mode+(scenario=="failure"?"-failure-04":"-03"),"cases.json")))!["cases"]!.AsArray()
                    .First(row=>row!["scenario"]!.GetValue<string>()==(scenario is "active-human" or "after-probe"?"active":scenario))!;
                await File.WriteAllTextAsync(Path.Combine(output,mode+"-"+scenario+"-transition.json"),JsonSerializer.Serialize(new {before,after,
                    captureDelta=capture.Calls-calls,result.Receipt,cancelFired},new JsonSerializerOptions{WriteIndented=true}));
                if(scenario is "cancel-published" or "cancel-refresh")check(cancelFired,name+" actual cancellation fires while real display preparation is held");
                check(capture.Calls==calls && after.Context.FrameId==before.Context.FrameId && after.Context.FloorId==before.Context.FloorId
                    && after.MapOpen==original["mapOpenAfter"]!.GetValue<bool>()
                    && after.Context.ProfileId==original["actualProfile"]!.GetValue<string>() && result.Receipt.Success==original["result"]!["success"]!.GetValue<bool>()
                    && ReferenceEquals(trusted,Field(runtime,"_alignment")) && poses==JsonSerializer.Serialize(trusted.PoseByMap),
                    name+" author selection uses no capture and preserves observed frame/floor and trusted geometry owner");
                bool visible=runtime.TryReadRender(out var changed);
                check(visible==original["afterCurrent"]!.GetValue<bool>() && runtime.ReadPendingGroupMaterial() is null
                    && !runtime.AcceptGroupMaterialReady(projection!),name+" current display and retired projection token match original accepted/pending lifetime");
                if(visible)
                {
                    var expected=original["render"]!;
                    check(changed is {ProjectionOnly:true,IsTransient:true} && (changed.MaterialToken==source!.MaterialToken)==(scenario is "same-profile" or "cancel-refresh" or "cancel-before")
                        && changed.PresentationOffsetX==15 && changed.PresentationOffsetY==-6 && after.NativePose is null
                        && !after.Display.CanonicalVerified && runtime.ReadMaterialToRemember() is null
                        && !runtime.AcknowledgeRenderMaterial(changed).Accepted,
                        name+" replacement pixels retain provisional authority and exactly one pan without learning");
                    var matrix=(changed!.PresentationTransform*changed.Material.Bridge.DisplayToClient!.Value).ToArray().SelectMany(row=>row).ToArray();
                    var expectedMatrix=expected["matrix"]!.AsArray().SelectMany(row=>row!.AsArray()).Select(v=>v!.GetValue<double>()).ToArray();
                    check(matrix.Zip(expectedMatrix,(a,b)=>Math.Abs(a-b)).Max()<1e-6
                        && after.Display.EffectiveProfile==expected["effectiveProfileId"]!.GetValue<string>(),
                        name+" real-resource shared fallback and rendered matrix agree with original (actual author layer unavailable)");
                }
                check(ReferenceEquals(group,Field(runtime,"_geometryGroup"))
                    && groupState==JsonSerializer.Serialize(model?.Snapshot()),
                    name+" actual shared fallback leaves the original trusted group state unchanged");
                if(scenario.StartsWith("cancel-",StringComparison.Ordinal))
                {
                    var retry=await Ask("set_context",new {profileId=preferred,refreshDisplay=true});
                    check(retry.Receipt.Success && runtime.TryReadRender(out var recovered) && recovered is {ProjectionOnly:true,IsTransient:true}
                        && runtime.ReadState().NativePose is null && capture.Calls==calls && runtime.ReadState().Context.FrameId==before.Context.FrameId
                        && original["retryAfterCancellation"]!["afterCurrent"]!.GetValue<bool>() && !runtime.AcceptGroupMaterialReady(projection!),
                        name+" a subsequent explicit display retry reuses accepted source authority without capture, fit or old token resurrection");
                }
                if(scenario=="failure")
                {
                    var retry=await Ask("set_context",new {profileId=preferred,refreshDisplay=true});
                    check(retry.Receipt.Success && !runtime.TryReadRender(out _) && runtime.ReadState().MapOpen
                        && runtime.ReadState().Context.FrameId==before.Context.FrameId && capture.Calls==calls
                        && !original["failureRecovery"]!["retryCurrent"]!.GetValue<bool>(),
                        name+" restored display metadata cannot resurrect source authority after an actual material rejection");
                    await Ask("set_floor",new {floor="2"});var fresh=runtime.ReadPendingGroupMaterial();
                    check(fresh is {ProjectionOnly:true} && !runtime.AcceptGroupMaterialReady(projection!)
                        && ReferenceEquals(fresh,runtime.ReadPendingGroupMaterial()) && runtime.AcceptGroupMaterialReady(fresh)
                        && runtime.TryReadRender(out _) && runtime.ReadState().NativePose is null && runtime.ReadMaterialToRemember() is null
                        && runtime.ReadState().Context.FrameId!=before.Context.FrameId && capture.Calls==calls+1
                        && original["failureRecovery"]!["freshAccepted"]!.GetValue<bool>() && poses==JsonSerializer.Serialize(trusted.PoseByMap),
                        name+" recovery requires a fresh accepted observation; late old ACK cannot clear its new display-only transaction");
                }
                await File.WriteAllTextAsync(Path.Combine(output,mode+"-"+scenario+".json"),JsonSerializer.Serialize(new {before=before.Context,
                    after=after.Context,result.Receipt,visible,captureDelta=capture.Calls-calls,
                    scope="Actual source solver/resources/post-Nov5 capture and Runtime author dispatch. Relation data is synthetic; failure additionally changes/restores only the in-memory display coordinate declaration to exercise the real bridge rejection. Both authors fall back to shared structure. No author pixel/native relation/game acceptance."},
                    new JsonSerializerOptions{WriteIndented=true}));
                GlobalInputPointerEvent Pointer(string phase,long n)=>new(phase,g.Left+g.Width/2,g.Top+g.Height/2,Now(),1,1,1,1,
                    ContextId:context.ContextId,Sequence:n,GestureId:1,PressContextSnapshot:context);
                void Move(string phase,long n,double tx,double ty,double dx,double dy)
                {var p=Pointer(phase,n);var step=new MapDragStep(dx,dy,tx,ty,phase=="up");check(runtime.PrepareGameMapDragMotion(p,step,sequence.Read()).Success
                    && runtime.ApplyGameMapDragMotion(p,step).Success,name+" "+phase+n);}
            }
            finally{coordinateFault?.Dispose();hold?.Dispose();if(authorWork is not null)await authorWork.WaitAsync(TimeSpan.FromSeconds(20));runtime.Shutdown();}
            Task<RuntimeOperationResult> Ask(string action,object? payload=null,CancellationToken token=default)=>runtime.DispatchAsync(new(action,
                JsonSerializer.SerializeToElement(payload??new {}),new(sequence.Issue(),runtime.ReadSnapshot().Context)),token).AsTask();
        }
    }
    private static object? Field(object instance,string name)=>instance.GetType().GetField(name,BindingFlags.Instance|BindingFlags.NonPublic)!.GetValue(instance);
    private static double Now()=>(double)System.Diagnostics.Stopwatch.GetTimestamp()/System.Diagnostics.Stopwatch.Frequency;
    private static async Task PendingRefresh(string resources,string replay,string output,Action<bool,string> check)
    {
        var index=JsonNode.Parse(await File.ReadAllBytesAsync(Path.Combine(replay,"all-samples.json")))!["samples"]!.AsArray()
            .ToDictionary(row=>row!["id"]!.GetValue<string>(),row=>row!.AsObject());
        foreach(string kind in new[]{"current","other"})
        {
            var capture=new RecordedCapture(index);capture.Select("batch01-event-000006",false);
            var runtime=new LiveRuntime(capture,resources);var sequence=new SessionSequencer();
            try
            {
                runtime.SetIdvb0059Enabled(true);await Ask("set_context",new {season="s0",mode="nightmare",profileId="shared-structure"});
                await Ask("start_round");await Ask("capture_now");var state=runtime.ReadState();
                string member=kind=="current"?state.Context.MapId!:state.IdentityMemberIds.First(id=>id!=state.Context.MapId);
                await Choose();var pending=runtime.ReadPendingGroupMaterial();
                check(pending is not null,"pending-refresh/"+kind+" real registered member stages material before refresh");
                int calls=capture.Calls;var before=runtime.ReadState();
                await Ask("set_context",new {profileId="shared-structure",refreshDisplay=true});
                await File.WriteAllTextAsync(Path.Combine(output,"pending-refresh-"+kind+".json"),JsonSerializer.Serialize(new {before,
                    after=runtime.ReadState(),hasSlot=Field(runtime,"_pendingGroupMaterial") is not null,readable=runtime.ReadPendingGroupMaterial() is not null},new JsonSerializerOptions{WriteIndented=true}));
                check(Field(runtime,"_pendingGroupMaterial") is null && runtime.ReadPendingGroupMaterial() is null
                    && !runtime.AcceptGroupMaterialReady(pending!) && runtime.TryReadRender(out _) && capture.Calls==calls
                    && runtime.ReadState().GroupChoice.DesiredMapId==member,
                    "pending-refresh/"+kind+" same-author explicit refresh retires the old dependent slot and keeps its desired identity");
                await Choose();var replacement=runtime.ReadPendingGroupMaterial();
                check(replacement is not null && !runtime.RejectGroupMaterial(pending!,"late")
                    && ReferenceEquals(replacement,runtime.ReadPendingGroupMaterial()) && runtime.AcceptGroupMaterialReady(replacement)
                    && runtime.ReadState().GroupChoice.HeldMapId==member,
                    "pending-refresh/"+kind+" actual picker retry can progress and a late token cannot clear its replacement");
                Task<RuntimeOperationResult> Choose()
                {var current=runtime.ReadState();return Ask("select_group_member",new {mapId=member,groupId=current.IdentityGroupId,
                    groupGeneration=current.GroupChoice.Generation,cycleId=current.GroupChoice.CycleId,floor=current.Context.FloorId});}
            }
            finally{runtime.Shutdown();}
            Task<RuntimeOperationResult> Ask(string action,object? payload=null)=>runtime.DispatchAsync(new(action,
                JsonSerializer.SerializeToElement(payload??new {}),new(sequence.Issue(),runtime.ReadSnapshot().Context)),default).AsTask();
        }
    }

    private static void TrackingRebase(string oracleRoot,Action<bool,string> check)
    {
        var original=JsonNode.Parse(File.ReadAllBytes(Path.Combine(oracleRoot,"phase27-original-display-rebase-01","cases.json")))!;
        foreach(var row in original["cases"]!.AsArray())
        {
            var input=row!["inputs"]!;string name="controlled-tracking/"+row["name"]!.GetValue<string>();
            var metadata=JsonSerializer.Deserialize<Dictionary<string,object?>>(input["tracking"]!.ToJsonString())!;
            var root=new Matrix3(new[]{new[]{.8,0d,13d},new[]{0d,.8,17d},new[]{0d,0d,1d}});
            var group=new RoundLayerGroup("fixture","before","1",root,new Dictionary<string,Matrix3>{{"2",Matrix3.Identity}},
                generation:7,cycleId:new(9),trackingFloor:"1",tracking:metadata,
                trackingByFloor:new Dictionary<string,IReadOnlyDictionary<string,object?>>{{"1",metadata}},displayedFloor:"1",displayedOverlay:new byte[2,3,4],
                verifiedFloors:["1","2"],renderProfileVerifiedFloors:["1","2"],canonicalGeometryVerifiedFloors:["1","2"],sourceStructureVerifiedFloors:["1"]);
            var previous=new Dictionary<string,Matrix3?>{{"1",Read(input["previousBridge"])}};
            var next=new Dictionary<string,Matrix3?>{{"1",Read(input["nextBridge"])}};
            group.RebaseDisplayProfile("after",previous,next);
            var expected=row["expected"]!;
            check(Same(JsonSerializer.SerializeToNode(group.Tracking),expected["tracking"])
                && Same(JsonSerializer.SerializeToNode(group.TrackingByFloor),expected["trackingByFloor"]),
                name+" author matrices/point, JSON null and invalid basis behavior match executed original helper");
            check(ReferenceEquals(group.RootPose,root) && group.Generation==7 && group.CycleId==new AlignmentCycleId(9)
                && group.CanonicalGeometryVerifiedFloors.SequenceEqual(new[]{"1","2"}) && group.SourceStructureVerifiedFloors.SequenceEqual(new[]{"1"})
                && group.Relations["2"].ToArray().SelectMany(v=>v).SequenceEqual(Matrix3.Identity.ToArray().SelectMany(v=>v))
                && group.DisplayedFloor=="1" && group.DisplayedOverlay is null && group.RenderProfileVerifiedFloors.Count==0 && group.RouteProfileId=="after",
                name+" display rebase clears author proof/overlay without changing native root, relations, floor, generation or geometry proofs");
        }
        static Matrix3? Read(JsonNode? node)=>node is null?null:Matrix3.TryRead(JsonSerializer.SerializeToElement(node));
    }
    private static bool Same(JsonNode? actual,JsonNode? expected)
    {
        if(actual is JsonObject a && expected is JsonObject b)return a.Count==b.Count && a.All(pair=>b.ContainsKey(pair.Key)&&Same(pair.Value,b[pair.Key]));
        if(actual is JsonArray x && expected is JsonArray y)return x.Count==y.Count && x.Zip(y,Same).All(v=>v);
        if(actual?.GetValueKind()==JsonValueKind.Number && expected?.GetValueKind()==JsonValueKind.Number)
        {double actualNumber=actual.GetValue<double>(),expectedNumber=expected.GetValue<double>();return Math.Abs(actualNumber-expectedNumber)<=1e-9*Math.Max(1,Math.Abs(expectedNumber));}
        return JsonNode.DeepEquals(actual,expected);
    }
}
