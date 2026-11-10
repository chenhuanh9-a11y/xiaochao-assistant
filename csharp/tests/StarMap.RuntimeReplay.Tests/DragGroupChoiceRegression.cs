using System.Text.Json;
using System.Text.Json.Nodes;
using StarMap.Core.Input;
using StarMap.Core.Session;
using StarMap.Runtime;

internal static class DragGroupChoiceRegression
{
    internal static async Task Run(string resources,string replay,string output,Action<bool,string> check,string? oracle=null)
    {
        var original=oracle is null?null:JsonNode.Parse(await File.ReadAllBytesAsync(oracle));
        var index=JsonNode.Parse(await File.ReadAllBytesAsync(Path.Combine(replay,"all-samples.json")))!["samples"]!.AsArray()
            .ToDictionary(row=>row!["id"]!.GetValue<string>(),row=>row!.AsObject());
        foreach(string scenario in new[]{"probe-only-cancel","probe-cancel","post-return-cancel","accept","reject-retry","held-down","late-capture","retry-cancel","fit-reject"})
        {
            var capture=new RecordedCapture(index);capture.Select("batch01-event-000006",false);
            var runtime=new LiveRuntime(capture,resources);var sequence=new SessionSequencer();
            RecordedCapture.Hold? hold=null;Task<RuntimeOperationResult>? inFlight=null;
            using var targetCancellation=new CancellationTokenSource();
            try
            {
                await Ask("set_context",new {season="s0",mode="nightmare",profileId="shared-structure"});
                await Ask("start_round");await Ask("capture_now");
                var state=runtime.ReadState();
                check(state.IdentityKind=="group" && state.Context.MapId!="nightmare-12" && state.IdentityMemberIds.Contains("nightmare-12"),scenario+" actual registered source group");
                var pointerContext=runtime.PointerContextSlot.Read()!;
                var g=((GameFloorClickContext)pointerContext.DownIntentSnapshot!).Geometry;
                var down=Pointer("down",1,1,0,0);
                check(runtime.BeginGameMapDrag(down).Success,scenario+" down");
                Move("move",2,1,13,-7,13,-7);Move("up",3,1,15,-6,2,1);
                check(runtime.TryReadRender(out var source) && source!.IsTransient,scenario+" actual source translated");
                if(scenario=="probe-only-cancel")
                {
                    hold=capture.HoldNext(ignoreCancellation:true);
                    inFlight=Ask("probe_map_after_drag",new {gestureId=1,pointerSequence=3},targetCancellation.Token);
                    await hold.Started.Task.WaitAsync(TimeSpan.FromSeconds(10));targetCancellation.Cancel();
                    check(runtime.ReadState().MapOpen && runtime.TryReadRender(out var duringProbeCancel) && ReferenceEquals(duringProbeCancel,source),
                        scenario+" cancelling a pure noncooperative observation cannot revoke the source opening");
                    hold.Release.TrySetResult();await inFlight.WaitAsync(TimeSpan.FromSeconds(20));inFlight=null;
                    check(runtime.PointerContextSlot.Read()==pointerContext && runtime.ReadState().NativePose==state.NativePose
                        && runtime.TryReadRender(out var afterProbeCancel) && ReferenceEquals(afterProbeCancel,source),
                        scenario+" late cancelled observation cannot change source frame/pose/context");
                    continue;
                }
                int calls=capture.Calls;var sourceState=runtime.ReadState();
                if(scenario=="fit-reject")capture.TransformPixels=(pixels,width,height,stride)=>
                {
                    // Derived negative: remove explored structure, leave real map controls intact.
                    for(int py=(int)(height*.14);py<(int)(height*.89);py++)for(int px=(int)(width*.1);px<(int)(width*.84);px++)
                    {int p=py*stride+px*4;pixels[p]=40;pixels[p+1]=32;pixels[p+2]=24;pixels[p+3]=255;}
                };
                if(scenario=="late-capture")hold=capture.HoldNext();
                inFlight=Ask("select_group_member",new {mapId="nightmare-12",groupId=state.IdentityGroupId,
                    groupGeneration=state.GroupChoice.Generation,cycleId=state.GroupChoice.CycleId,floor=state.Context.FloorId},
                    scenario=="post-return-cancel"?targetCancellation.Token:default);
                if(hold is not null)
                {
                    await hold.Started.Task.WaitAsync(TimeSpan.FromSeconds(10));
                    check(runtime.PointerContextSlot.Read()==pointerContext && runtime.TryReadRender(out var blocked) && ReferenceEquals(blocked,source),scenario+" capture does not publish staged frame");
                    check(runtime.BeginGameMapDrag(Pointer("down",4,2,0,0)).Success,scenario+" next press reserves source while target capture is pending");
                    Move("move",5,2,2,3,2,3);
                    hold.Release.TrySetResult();await inFlight.WaitAsync(TimeSpan.FromSeconds(20));inFlight=null;
                    check(runtime.ReadPendingGroupMaterial() is null && runtime.ReadState().Context.FrameId==sourceState.Context.FrameId
                        && runtime.PointerContextSlot.Read()==pointerContext && runtime.TryReadRender(out var afterOld)
                        && afterOld!.PresentationOffsetX==17 && afterOld.PresentationOffsetY== -3,
                        scenario+" old target capture cannot publish after a newer real drag");
                    continue;
                }
                await inFlight;inFlight=null;
                var pending=runtime.ReadPendingGroupMaterial();
                if(scenario=="fit-reject")
                {
                    check(capture.Calls==calls+1 && pending is null && runtime.TryReadRender(out var retainedAfterReject) && ReferenceEquals(retainedAfterReject,source)
                        && runtime.ReadState().Context.FrameId==sourceState.Context.FrameId && runtime.ReadState().NativePose==sourceState.NativePose
                        && runtime.PointerContextSlot.Read()==pointerContext && runtime.ReadState().GroupChoice is {DesiredMapId:"nightmare-12",Status:"awaiting-geometry"},
                        scenario+" real solver rejects structure-erased derived pixels without replacing source pose/frame/context");
                    capture.TransformPixels=null;await Ask("refresh_map");pending=runtime.ReadPendingGroupMaterial();
                }
                check(capture.Calls==calls+(scenario=="fit-reject"?3:1) && pending is {MapId:"nightmare-12"} && pending.Stamp.FrameId!=source!.Stamp.FrameId
                    && pending.Generation==source.Generation+1,scenario+" target material is fitted on exactly one fresh staged observation");
                if(original is not null)
                {
                    var expected=original["pending"]!["canonicalMatrix"]!.AsArray().SelectMany(r=>r!.AsArray()).Select(v=>v!.GetValue<double>()).ToArray();
                    var actual=pending!.Material.Bridge.CanonicalToClient!.Value.ToArray().SelectMany(r=>r).ToArray();
                    check(actual.Length==expected.Length && actual.Zip(expected,(a,b)=>Math.Abs(a-b)).Max()<1e-6,scenario+" staged canonical geometry agrees with independent original full Host fit");
                }
                check(runtime.ReadState().Context.FrameId==sourceState.Context.FrameId && runtime.ReadState().NativePose==sourceState.NativePose
                    && runtime.PointerContextSlot.Read()==pointerContext && runtime.TryReadRender(out var beforeAck) && ReferenceEquals(beforeAck,source),
                    scenario+" source frame/context/pose and exact translated packet remain until ACK");
                if(scenario=="post-return-cancel")
                {
                    var expired=pending;targetCancellation.Cancel();
                    check(runtime.ReadPendingGroupMaterial() is null && runtime.ReadState().GroupChoice is {PendingMapId:null,DesiredMapId:"nightmare-12",Status:"awaiting-geometry"}
                        && runtime.TryReadRender(out var retainedAfterReturn) && ReferenceEquals(retainedAfterReturn,source)
                        && runtime.PointerContextSlot.Read()==pointerContext,scenario+" cancellation after Runtime returns releases its pending slot without closing source");
                    await Ask("refresh_map");pending=runtime.ReadPendingGroupMaterial();
                    check(pending is not null && !runtime.AcceptGroupMaterialReady(expired!) && !runtime.RejectGroupMaterial(expired!,"late expired material"),
                        scenario+" expired target cannot block retry or clear its replacement");
                }
                if(scenario is "retry-cancel" or "probe-cancel")
                {
                    runtime.RejectGroupMaterial(pending!,"retry cancellation fixture");hold=capture.HoldNext(scenario=="probe-cancel"?1:2,ignoreCancellation:true);
                    using var cancellation=new CancellationTokenSource();inFlight=Ask("refresh_map",null,cancellation.Token);
                    await hold.Started.Task.WaitAsync(TimeSpan.FromSeconds(10));cancellation.Cancel();
                    check(runtime.ReadState().MapOpen && runtime.TryReadRender(out var retainedDuringCancel) && ReferenceEquals(retainedDuringCancel,source),
                        scenario+" synchronous cancellation keeps source while noncooperative target capture is still held");
                    hold.Release.TrySetResult();
                    await inFlight.WaitAsync(TimeSpan.FromSeconds(20));inFlight=null;
                    check(runtime.ReadState().MapOpen && runtime.PointerContextSlot.Read()==pointerContext
                        && runtime.TryReadRender(out var afterCancel) && ReferenceEquals(afterCancel,source)
                        && runtime.ReadState().GroupChoice is {DesiredMapId:"nightmare-12",Status:"awaiting-geometry"},
                        scenario+" cancelling a passive target retry cannot close its source opening");
                    continue;
                }
                if(scenario=="reject-retry")
                {
                    check(runtime.RejectGroupMaterial(pending!,"consumer-refused") && runtime.TryReadRender(out beforeAck) && ReferenceEquals(beforeAck,source)
                        && runtime.ReadState().GroupChoice is {DesiredMapId:"nightmare-12",Status:"awaiting-geometry"},scenario+" material failure retains source and desired member");
                    var old=pending;await Ask("refresh_map");pending=runtime.ReadPendingGroupMaterial();
                    check(pending is not null && pending.Stamp!=old!.Stamp && !runtime.AcceptGroupMaterialReady(old)
                        && runtime.PointerContextSlot.Read()==pointerContext,scenario+" passive retry captures target again without publishing its frame");
                }
                if(scenario=="held-down")
                {
                    check(runtime.BeginGameMapDrag(Pointer("down",4,2,0,0)).Success && !runtime.AcceptGroupMaterialReady(pending!),scenario+" ACK between down and first motion cannot replace press frame");
                    Move("move",5,2,2,3,2,3);
                    check(!runtime.AcceptGroupMaterialReady(pending!) && runtime.ReadPendingGroupMaterial() is null
                        && runtime.PointerContextSlot.Read()==pointerContext && runtime.TryReadRender(out var moved)
                        && moved!.PresentationOffsetX==17 && moved.PresentationOffsetY== -3,scenario+" first motion retires pending target without clearing desired choice");
                    continue;
                }
                check(runtime.AcceptGroupMaterialReady(pending!),scenario+" material ACK commits staged target");
                var committed=runtime.ReadState();var freshPointer=runtime.PointerContextSlot.Read()!;
                check(committed.GroupChoice.Generation==state.GroupChoice.Generation+1,scenario+" original manual member ACK advances group generation");
                var staleChoice=await Ask("select_group_member",new {mapId="nightmare-19",groupId=state.IdentityGroupId,
                    groupGeneration=state.GroupChoice.Generation,cycleId=state.GroupChoice.CycleId,floor=state.Context.FloorId});
                check(!staleChoice.Receipt.Success && runtime.ReadPendingGroupMaterial() is null && runtime.ReadState().Context.MapId=="nightmare-12",
                    scenario+" pre-ACK group intent cannot acquire post-ACK identity scope");
                check(runtime.TryReadRender(out var selected) && ReferenceEquals(selected,pending) && selected!.MapId=="nightmare-12"
                    && selected.PresentationOffsetX==0 && !selected.IsTransient && committed.Context.FrameId==pending!.Stamp.FrameId
                    && committed.NativePose!.FrameId==pending.Stamp.FrameId && freshPointer!=pointerContext
                    && committed.GroupChoice is {HeldMapId:"nightmare-12",Status:"confirmed"},scenario+" ACK publishes fresh target context and clears only old offset");
                pointerContext=freshPointer;
                check(runtime.BeginGameMapDrag(Pointer("down",4,2,0,0)).Success,scenario+" target accepts next gesture on fresh context");
                Move("up",5,2,2,3,2,3);
                check(runtime.TryReadRender(out var next) && next!.PresentationOffsetX==2 && next.PresentationOffsetY==3,
                    scenario+" next drag starts from target canonical without old source offset");
                await File.WriteAllTextAsync(Path.Combine(output,scenario+".json"),JsonSerializer.Serialize(new {source=sourceState.Context,target=committed.Context,capture.Calls,
                    scope="Actual registered source group and real target fit on post-Nov5 recorded pixels. Public dispatch/ACK; only OS capture replaced. Explicit member choice is an intent, not unique image truth. No native/game acceptance."},new JsonSerializerOptions{WriteIndented=true}));
                GlobalInputPointerEvent Pointer(string phase,long seq,long gesture,int dx,int dy)=>new(phase,g.Left+g.Width/2+dx,g.Top+g.Height/2+dy,Now(),1,1,1,1,
                    ContextId:pointerContext.ContextId,Sequence:seq,GestureId:gesture,PressContextSnapshot:pointerContext);
                void Move(string phase,long seq,long gesture,double tx,double ty,double dx,double dy)
                {
                    var pointer=Pointer(phase,seq,gesture,(int)tx,(int)ty);var step=new MapDragStep(dx,dy,tx,ty,phase=="up");
                    check(runtime.PrepareGameMapDragMotion(pointer,step,sequence.Read()).Success && runtime.ApplyGameMapDragMotion(pointer,step).Success,scenario+" "+phase+seq);
                }
            }
            finally{hold?.Release.TrySetResult();runtime.Shutdown();if(inFlight is not null)await inFlight.WaitAsync(TimeSpan.FromSeconds(20));}
            Task<RuntimeOperationResult> Ask(string action,object? payload=null,CancellationToken token=default)=>runtime.DispatchAsync(new(action,JsonSerializer.SerializeToElement(payload??new {}),
                new(sequence.Issue(),runtime.ReadSnapshot().Context)),token).AsTask();
        }
        await DragGroupChoiceSessionRegression.Run(resources,index,output,check);
    }
    static double Now()=>System.Diagnostics.Stopwatch.GetTimestamp()/(double)System.Diagnostics.Stopwatch.Frequency;
}
