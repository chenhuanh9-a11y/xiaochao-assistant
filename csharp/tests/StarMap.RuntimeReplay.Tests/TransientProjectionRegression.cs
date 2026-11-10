using System.Reflection;
using System.Text.Json;
using System.Text.Json.Nodes;
using StarMap.Core.Alignment;
using StarMap.Core.Display;
using StarMap.Core.Input;
using StarMap.Core.Session;
using StarMap.Runtime;

internal static class TransientProjectionRegression
{
    internal static async Task Run(string resources,string replay,string oracle,string output,Action<bool,string> check)
    {
        var original=JsonNode.Parse(await File.ReadAllBytesAsync(oracle))!;
        check(original["cases"]!.AsArray().All(row=>row!["retainedCycleDuringCapture"]!.GetValue<bool>()),
            "original production set_floor retains its transient cycle at the capture boundary (controlled source/relation fixture)");
        var index=JsonNode.Parse(await File.ReadAllBytesAsync(Path.Combine(replay,"all-samples.json")))!["samples"]!.AsArray()
            .ToDictionary(row=>row!["id"]!.GetValue<string>(),row=>row!.AsObject());
        foreach(var mode in new[]{"nightmare","difficult"})
        {
            string initial=mode=="nightmare"?"batch01-event-000006":"batch02-event-000147";
            string target=index.First(pair=>pair.Value["season"]!.GetValue<string>()=="s0" && pair.Value["mode"]!.GetValue<string>()==mode
                && pair.Value["recordedObservation"]?["floorId"]?.GetValue<string>()=="2").Key;
            var capture=new RecordedCapture(index);capture.Select(initial,false);
            var runtime=new LiveRuntime(capture,resources);var sequence=new SessionSequencer();
            RecordedCapture.Hold? hold=null;Task<RuntimeOperationResult>? work=null;using var cancellation=new CancellationTokenSource();
            try
            {
                await Ask("set_context",new {season="s0",mode,profileId="shared-structure"});await Ask("start_round");await Ask("capture_now");
                check(runtime.TryReadRender(out var source),mode+" real resources and recorded source grant");
                var context=runtime.PointerContextSlot.Read()!;var g=((GameFloorClickContext)context.DownIntentSnapshot!).Geometry;
                check(runtime.BeginGameMapDrag(Pointer("down",1)).Success,mode+" down");
                Move("move",2,13,-7,13,-7);Move("up",3,15,-6,2,1);
                await Ask("probe_map_after_drag",new {gestureId=1,pointerSequence=3});
                var before=runtime.ReadState();var cycle=Field(runtime,"_dragCycle");var group=Field(runtime,"_geometryGroup");
                var core=(AlignmentCoreV2)Field(runtime,"_alignment")!;string poses=JsonSerializer.Serialize(core.PoseByMap);
                capture.Select(target,false);hold=capture.HoldNext();work=Ask("set_floor",new {floor="2"},cancellation.Token);
                await hold.Started.Task.WaitAsync(TimeSpan.FromSeconds(10));
                check(ReferenceEquals(cycle,Field(runtime,"_dragCycle")) && ReferenceEquals(group,Field(runtime,"_geometryGroup")),
                    mode+" explicit floor change retains the source cycle and group while actual capture is pending");
                hold.Release.TrySetResult();await work.WaitAsync(TimeSpan.FromSeconds(20));work=null;
                var after=runtime.ReadState();
                check(after.Context.FloorId=="2" && after.Context.FrameId!=before.Context.FrameId && after.NativePose is null,
                    mode+" valid target observation advances before projection admission without fabricating a target pose");
                check(after.Reason=="projection-relation-unavailable" && !runtime.TryReadRender(out _) && runtime.ReadPendingGroupMaterial() is null && poses==JsonSerializer.Serialize(core.PoseByMap)
                    && ReferenceEquals(group,Field(runtime,"_geometryGroup")),mode+" actual native resource seed gap refuses display while preserving trusted geometry");
                await File.WriteAllTextAsync(Path.Combine(output,"native-gap-"+mode+".json"),JsonSerializer.Serialize(new {initial,target,before=before.Context,after=after.Context,
                    scope="Actual Runtime/capture detector/real native resources and post-Nov5 PNG. Capture OS/pointer replaced. Target floor is measured by detector; no independent map/floor image truth or reviewed cross-floor relation is claimed."},new JsonSerializerOptions{WriteIndented=true}));
                GlobalInputPointerEvent Pointer(string phase,long n)=>new(phase,g.Left+g.Width/2,g.Top+g.Height/2,Now(),1,1,1,1,
                    ContextId:context.ContextId,Sequence:n,GestureId:1,PressContextSnapshot:context);
                void Move(string phase,long n,double tx,double ty,double dx,double dy)
                {var p=Pointer(phase,n);var step=new MapDragStep(dx,dy,tx,ty,phase=="up");check(runtime.PrepareGameMapDragMotion(p,step,sequence.Read()).Success && runtime.ApplyGameMapDragMotion(p,step).Success,mode+" "+phase);}
            }
            finally{cancellation.Cancel();hold?.Release.TrySetResult();if(work is not null)await work.WaitAsync(TimeSpan.FromSeconds(20));runtime.Shutdown();}
            Task<RuntimeOperationResult> Ask(string action,object? payload=null,CancellationToken token=default)=>runtime.DispatchAsync(new(action,
                JsonSerializer.SerializeToElement(payload??new {}),new(sequence.Issue(),runtime.ReadSnapshot().Context)),token).AsTask();
        }
        await SyntheticTransactions(resources,index,oracle,output,check);
        Policy(original,check);
    }

    private static async Task SyntheticTransactions(string resources,Dictionary<string,JsonObject> index,string originalPath,string output,Action<bool,string> check)
    {
        foreach(var mode in new[]{"nightmare","difficult"})
        foreach(var scenario in mode=="nightmare"?new[]{"held-return","accept","probe","reject","cancel","close","author","current-member"}:new[]{"accept","reject"})
        {
            string name="synthetic-provider/"+mode+"/"+scenario,initial=mode=="nightmare"?"batch01-event-000006":"batch02-event-000147";
            string target=index.First(pair=>pair.Value["season"]!.GetValue<string>()=="s0" && pair.Value["mode"]!.GetValue<string>()==mode
                && pair.Value["recordedObservation"]?["floorId"]?.GetValue<string>()=="2"
                && JsonNode.DeepEquals(pair.Value["physicalClient"],index[initial]["physicalClient"])).Key;
            var capture=new RecordedCapture(index);capture.Select(initial,false);var runtime=new LiveRuntime(capture,resources);var sequence=new SessionSequencer();
            using var cancellation=new CancellationTokenSource();
            try
            {
                runtime.SetIdvb0059Enabled(true);
                await Ask("set_context",new {season="s0",mode,profileId=mode=="nightmare"?"suju":"shared-structure"});
                SyntheticProjectionCoordinates.Install(runtime,resources,mode);
                await Ask("start_round");await Ask("capture_now");
                check(runtime.TryReadRender(out var source),name+" actual independent source fit");
                runtime.AcknowledgeRenderMaterial(source!);await runtime.RememberRenderMaterialAsync(source!.MaterialToken,runtime.ReadSnapshot().Context,default);
                if(scenario=="held-return")
                {
                    var state=runtime.ReadState();await Ask("select_group_member",new {mapId=state.Context.MapId,groupId=state.IdentityGroupId,
                        groupGeneration=state.GroupChoice.Generation,cycleId=state.GroupChoice.CycleId,floor=state.Context.FloorId});
                    check(runtime.ReadPendingGroupMaterial() is { } current && runtime.AcceptGroupMaterialReady(current),name+" establish real human hold on current A");
                }
                var context=runtime.PointerContextSlot.Read()!;var g=((GameFloorClickContext)context.DownIntentSnapshot!).Geometry;
                check(runtime.BeginGameMapDrag(Pointer("down",1,1)).Success,name+" source down");
                Move("move",2,1,13,-7,13,-7);Move("up",3,1,15,-6,2,1);
                await Ask("probe_map_after_drag",new {gestureId=1,pointerSequence=3});
                if(scenario=="held-return")
                {
                    var state=runtime.ReadState();string other=state.IdentityMemberIds.First(id=>id!=state.Context.MapId);
                    await Ask("select_group_member",new {mapId=other,groupId=state.IdentityGroupId,groupGeneration=state.GroupChoice.Generation,
                        cycleId=state.GroupChoice.CycleId,floor=state.Context.FloorId});
                    check(runtime.ReadPendingGroupMaterial() is { } choice && runtime.RejectGroupMaterial(choice,"actual consumer refusal of desired B"),
                        name+" retain desired B after real target fit and consumer rejection");
                }
                var before=runtime.ReadState();var group=Field(runtime,"_geometryGroup")!;
                var model=(RoundLayerGroup)group.GetType().GetProperty("Model")!.GetValue(group)!;
                string geometry=JsonSerializer.Serialize(model.Snapshot());var core=(AlignmentCoreV2)Field(runtime,"_alignment")!;
                string poses=JsonSerializer.Serialize(core.PoseByMap),anchors=AnchorSnapshot(core);
                var candidates=core.CandidateCache.ToDictionary(pair=>pair.Key,pair=>pair.Value.ToArray());
                var steady=(System.Collections.IDictionary)Field(runtime,"_steadyFloors")!;
                var steadyRows=steady.Keys.Cast<object>().ToDictionary(key=>key,key=>steady[key]);
                capture.Select(target,false);
                var result=await Ask(scenario=="probe"?"refresh_map":"set_floor",scenario=="probe"?null:new {floor="2"},cancellation.Token);
                var pending=runtime.ReadPendingGroupMaterial();var observed=runtime.ReadState();
                check(result.Receipt.Success && pending is {ProjectionOnly:true,IsTransient:true} && !runtime.TryReadRender(out _)
                    && observed.NativePose is null && observed.Context.FloorId=="2" && observed.Context.FrameId==pending.Stamp.FrameId
                    && observed.Context.FrameId!=before.Context.FrameId && pending.Generation==source.Generation+1,
                    name+" fresh observation is published before provisional material ACK, with no native pose or old-floor display");
                if(mode=="nightmare")
                {
                    // Read the independent original result only after the actual pipeline has produced its packet.
                    string root=Path.GetDirectoryName(Path.GetDirectoryName(originalPath)!)!;
                    var expected=JsonNode.Parse(await File.ReadAllBytesAsync(Path.Combine(root,"phase26-original-recorded-projection-02","case.json")))!;
                    var canonical=pending!.Material.Bridge.CanonicalToClient!.Value.ToArray().SelectMany(row=>row).ToArray();
                    var expectedCanonical=expected["targetCanonical"]!.AsArray().SelectMany(row=>row!.AsArray()).Select(v=>v!.GetValue<double>()).ToArray();
                    var display=(pending.PresentationTransform*pending.Material.Bridge.DisplayToClient!.Value).ToArray().SelectMany(row=>row).ToArray();
                    var expectedDisplay=expected["displayMatrix"]!.AsArray().SelectMany(row=>row!.AsArray()).Select(v=>v!.GetValue<double>()).ToArray();
                    check(canonical.Zip(expectedCanonical,(a,b)=>Math.Abs(a-b)).Max()<1e-6
                        && display.Zip(expectedDisplay,(a,b)=>Math.Abs(a-b)).Max()<1e-6,
                        name+" original real-resource/current-capture projection agrees on canonical and rendered matrices (relation alone is synthetic)");
                }
                if(scenario is "reject" or "cancel")
                {
                    var retired=pending;
                    if(scenario=="reject")check(runtime.RejectGroupMaterial(retired!,"fixture material rejection"),name+" reject pending material");
                    else cancellation.Cancel();
                    check(runtime.ReadPendingGroupMaterial() is null && !runtime.TryReadRender(out _) && runtime.ReadState().Context==observed.Context
                        && runtime.ReadState().MapOpen,name+" rejection/cancellation cannot roll back the accepted floor observation or revive old pixels");
                    await Ask("set_floor",new {floor="2"});pending=runtime.ReadPendingGroupMaterial();
                    check(pending is not null && !runtime.AcceptGroupMaterialReady(retired!) && !runtime.RejectGroupMaterial(retired!,"late"),
                        name+" a new capture can retry, and a late old ACK/reject cannot clear its replacement");
                }
                if(scenario=="held-return")
                {
                    var retired=pending;var state=runtime.ReadState();
                    await Ask("select_group_member",new {mapId=state.Context.MapId,groupId=state.IdentityGroupId,groupGeneration=state.GroupChoice.Generation,
                        cycleId=state.GroupChoice.CycleId,floor=state.Context.FloorId});
                    check(Field(runtime,"_pendingProjection") is null && runtime.ReadPendingGroupMaterial() is null
                        && runtime.ReadState().GroupChoice.DesiredMapId==state.Context.MapId && runtime.ReadState().Context==observed.Context,
                        name+" returning to held A retires the shared projection pending slot without rolling back observation");
                    var refreshed=await Ask("refresh_map");
                    check(refreshed.Receipt.Reason!="refresh-coalesced",name+" completed projection token cannot leave passive refresh blocked");
                    await Ask("set_floor",new {floor="2"});pending=runtime.ReadPendingGroupMaterial();
                    check(pending is not null && !runtime.AcceptGroupMaterialReady(retired!) && !runtime.RejectGroupMaterial(retired!,"late"),
                        name+" stale projection token cannot clear a subsequent explicit observation's replacement");
                }
                if(scenario is "close" or "author")
                {
                    if(scenario=="close")await Ask("close_map");else await Ask("set_context",new {profileId="shared-structure"});
                    check(runtime.ReadPendingGroupMaterial() is null && !runtime.AcceptGroupMaterialReady(pending!) && !runtime.TryReadRender(out _),
                        name+" changed display lifetime retires the old pending token");
                    continue;
                }
                check(runtime.AcceptGroupMaterialReady(pending!),name+" exact consumer ACK installs the provisional display grant");
                check(runtime.TryReadRender(out var projected) && ReferenceEquals(projected,pending) && runtime.ReadState().NativePose is null
                    && runtime.ReadState().Display is {Prepared:true,CanonicalVerified:false} && !runtime.AcknowledgeRenderMaterial(projected!).Accepted
                    && runtime.ReadMaterialToRemember() is null,name+" projection is visible-only and never authorizes native geometry or learning");
                check(ReferenceEquals(group,Field(runtime,"_geometryGroup")) && geometry==JsonSerializer.Serialize(model.Snapshot())
                    && ReferenceEquals(core,Field(runtime,"_alignment")) && poses==JsonSerializer.Serialize(core.PoseByMap)
                    && anchors==AnchorSnapshot(core) && candidates.Count==core.CandidateCache.Count
                    && candidates.All(pair=>core.CandidateCache.TryGetValue(pair.Key,out var rows) && rows.SequenceEqual(pair.Value))
                    && steadyRows.Count==steady.Count && steadyRows.All(pair=>steady.Contains(pair.Key) && ReferenceEquals(pair.Value,steady[pair.Key])),
                    name+" trusted core/root/proof pools/candidate cache and independent same-floor scale cache are unchanged");
                if(scenario=="current-member")
                {
                    var current=runtime.ReadState();await Ask("select_group_member",new {mapId=current.Context.MapId,groupId=current.IdentityGroupId,
                        groupGeneration=current.GroupChoice.Generation,cycleId=current.GroupChoice.CycleId,floor=current.Context.FloorId});
                    var member=runtime.ReadPendingGroupMaterial();
                    check(member is {ProjectionOnly:true} && runtime.AcceptGroupMaterialReady(member) && runtime.TryReadRender(out var held)
                        && ReferenceEquals(held,member) && runtime.ReadState().NativePose is null && runtime.ReadMaterialToRemember() is null,
                        name+" human current-member identity confirmation cannot promote provisional projection geometry");
                }
                context=runtime.PointerContextSlot.Read()!;
                check(runtime.BeginGameMapDrag(Pointer("down",10,2)).Success,name+" projected context supports another ordinary drag");
                Move("up",11,2,2,3,2,3);
                check(runtime.TryReadRender(out var moved) && moved is {ProjectionOnly:true,PresentationOffsetX:17,PresentationOffsetY:-3},
                    name+" later drag preserves projection provenance and adds its offset only once");
                await Ask("probe_map_after_drag",new {gestureId=2,pointerSequence=11});
                capture.Select(initial,false);await Ask("set_floor",new {floor="1"});var back=runtime.ReadPendingGroupMaterial();
                check(back is {ProjectionOnly:true} && runtime.AcceptGroupMaterialReady(back) && runtime.ReadState().NativePose is null
                    && geometry==JsonSerializer.Serialize(model.Snapshot()) && poses==JsonSerializer.Serialize(core.PoseByMap),
                    name+" generic reviewed provider supports the return projection without promoting historical target proofs");
                await File.WriteAllTextAsync(Path.Combine(output,"synthetic-"+mode+"-"+scenario+".json"),JsonSerializer.Serialize(new {initial,target,
                    before=before.Context,observed=observed.Context,after=runtime.ReadState().Context,
                    scope="Real Runtime, source solver, resources, display bridges and post-Nov5 PNG. Only relation-provider document is replaced with explicit synthetic reviewed rows before source fitting; no real native relation/target fit/game accuracy is claimed."},new JsonSerializerOptions{WriteIndented=true}));
                GlobalInputPointerEvent Pointer(string phase,long n,long gesture)=>new(phase,g.Left+g.Width/2,g.Top+g.Height/2,Now(),1,1,1,1,
                    ContextId:context.ContextId,Sequence:n,GestureId:gesture,PressContextSnapshot:context);
                void Move(string phase,long n,long gesture,double tx,double ty,double dx,double dy)
                {var p=Pointer(phase,n,gesture);var step=new MapDragStep(dx,dy,tx,ty,phase=="up");check(runtime.PrepareGameMapDragMotion(p,step,sequence.Read()).Success && runtime.ApplyGameMapDragMotion(p,step).Success,name+" "+phase+n);}
            }
            finally{runtime.Shutdown();}
            Task<RuntimeOperationResult> Ask(string action,object? payload=null,CancellationToken token=default)=>runtime.DispatchAsync(new(action,
                JsonSerializer.SerializeToElement(payload??new {}),new(sequence.Issue(),runtime.ReadSnapshot().Context)),token).AsTask();
        }
    }

    private static string AnchorSnapshot(AlignmentCoreV2 core)=>JsonSerializer.Serialize(core.AnchorPools.OrderBy(pair=>pair.Key.Map,StringComparer.Ordinal)
        .ThenBy(pair=>pair.Key.Floor,StringComparer.Ordinal).Select(pair=>new {pair.Key.Map,pair.Key.Floor,pair.Value}).ToArray());

    private static void Policy(JsonNode original,Action<bool,string> check)
    {
        var relation=new Matrix3(new[]{new[]{1d,0d,7d},new[]{0d,1d,-3d},new[]{0d,0d,1d}});
        var group=new RoundLayerGroup("policy","shared-structure","1",Matrix3.Identity,new Dictionary<string,Matrix3>{{"2",relation}},cycleId:new(1));
        group.MarkGeometryVerified("1",true,true);group.MarkGeometryVerified("2",true,true);
        var source=new SharedProjectionSource("policy","1",new(1),true,false);
        var seed=new JsonObject {["targetToSourceMatrix"]=JsonSerializer.SerializeToNode(relation.ToArray()),["boundedCrossFloorOnly"]=true};
        foreach(bool search in new[]{false,true})
        {
            seed["requiresCurrentFrameDisplayValidation"]=search;
            check(SharedProjectionPolicy.Evaluate(group,"policy","2",new(1),"shared-structure",source,seed).Accepted,"original policy first reviewed transition/"+search);
            var provisional=new SharedProjectionSource("policy","2",new(1),false,true,"1");
            bool actual=SharedProjectionPolicy.Evaluate(group,"policy","1",new(1),"shared-structure",provisional,seed).Accepted;
            bool expected=original["cases"]!.AsArray().First(row=>row!["name"]!.GetValue<string>()==(search?"search-only":"regular"))!["back"]!["success"]!.GetValue<bool>();
            check(actual==expected,"original search-only provenance prevents promotion even when both floors have historical proof/"+search);
            check(SharedProjectionPolicy.Evaluate(group,"policy","2",new(1),"shared-structure",provisional,seed).Accepted,
                "same target provisional reuse keeps the original source/"+search);
        }
        check(!SharedProjectionPolicy.Evaluate(group,"policy","B1",new(1),"shared-structure",source,seed).Accepted
            && !SharedProjectionPolicy.Evaluate(group,"policy","2",new(1),"wrong-profile",source,seed).Accepted,
            "unsupported floors and unrelated display profiles never inherit projection permission");
    }
    static object? Field(object owner,string name)=>owner.GetType().GetField(name,BindingFlags.Instance|BindingFlags.NonPublic)!.GetValue(owner);
    static double Now()=>System.Diagnostics.Stopwatch.GetTimestamp()/(double)System.Diagnostics.Stopwatch.Frequency;
}
