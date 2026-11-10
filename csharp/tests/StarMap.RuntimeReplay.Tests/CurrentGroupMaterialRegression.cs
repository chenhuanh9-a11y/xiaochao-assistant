using System.Collections;
using System.Reflection;
using System.Text.Json;
using System.Text.Json.Nodes;
using StarMap.Core.Input;
using StarMap.Core.Session;
using StarMap.Runtime;

internal static class CurrentGroupMaterialRegression
{
    internal static async Task Run(string resources,string replay,string oracle,string output,Action<bool,string> check)
    {
        var original=JsonNode.Parse(await File.ReadAllBytesAsync(oracle))!["cases"]!.AsArray();
        var index=JsonNode.Parse(await File.ReadAllBytesAsync(Path.Combine(replay,"all-samples.json")))!["samples"]!.AsArray()
            .ToDictionary(row=>row!["id"]!.GetValue<string>(),row=>row!.AsObject());
        foreach(string scenario in new[]{"normal","drag","after-probe","reject","cancel","held-down","motion-retire","binding-retire","close-retire","before-stale"})
        {
            bool drag=scenario!="normal";
            var capture=new RecordedCapture(index);capture.Select("batch01-event-000006",false);
            var runtime=new LiveRuntime(capture,resources);var sequence=new SessionSequencer();
            using var cancellation=new CancellationTokenSource();
            try
            {
                runtime.SetIdvb0059Enabled(true);
                await Ask("set_context",new {season="s0",mode="nightmare",profileId="shared-structure"});
                await Ask("start_round");await Ask("capture_now");
                check(runtime.TryReadRender(out var initial) && runtime.ReadState().IdentityKind=="group",scenario+" actual recorded group has an authorized source");
                runtime.AcknowledgeRenderMaterial(initial!);
                await runtime.RememberRenderMaterialAsync(initial!.MaterialToken,runtime.ReadSnapshot().Context,default);
                var pointerContext=runtime.PointerContextSlot.Read()!;
                var geometry=((GameFloorClickContext)pointerContext.DownIntentSnapshot!).Geometry;
                if(drag)
                {
                    check(runtime.BeginGameMapDrag(Pointer("down",1,1)).Success,scenario+" source press");
                    Move("move",2,1,13,-7,13,-7);Move("up",3,1,15,-6,2,1);
                }
                if(scenario=="after-probe")await Ask("probe_map_after_drag",new {gestureId=1,pointerSequence=3});
                check(runtime.TryReadRender(out var source),scenario+" source remains drawable");
                var before=runtime.ReadState();var group=Field(runtime,"_geometryGroup");int steady=SteadyCount(runtime),calls=capture.Calls;
                check(steady>0,scenario+" real accepted source populated the same-floor cache");
                var expected=original.First(row=>row!["name"]!.GetValue<string>()==(scenario=="normal"?"normal":scenario=="after-probe"?"after-probe":"drag"))!;
                var expectedMatrix=expected["canonical"]!.AsArray().SelectMany(row=>row!.AsArray()).Select(v=>v!.GetValue<double>()).ToArray();
                var actualMatrix=source!.Material.Bridge.CanonicalToClient!.Value.ToArray().SelectMany(row=>row).ToArray();
                check(actualMatrix.Zip(expectedMatrix,(a,b)=>Math.Abs(a-b)).Max()<1e-6,scenario+" source canonical agrees with original real Host oracle");
                if(scenario=="before-stale")capture.Advance();
                await Choose(cancellation.Token);var pending=runtime.ReadPendingGroupMaterial();
                if(scenario=="before-stale")
                {
                    check(pending is null && capture.Calls==calls && runtime.ReadState().GroupChoice.HeldMapId is null,
                        scenario+" expired source cannot be promoted by selecting its current member");
                    continue;
                }
                check(capture.Calls==calls && pending is not null && pending.MaterialToken!=source.MaterialToken
                    && ReferenceEquals(pending.Material,source.Material) && ReferenceEquals(pending.Scene,source.Scene),
                    scenario+" current member stages a new ACK token with existing pixels and geometry instead of another solve");
                check(ReferenceEquals(runtime.ReadState().NativePose,before.NativePose) && runtime.ReadState().Context==before.Context
                    && runtime.PointerContextSlot.Read()==pointerContext && runtime.TryReadRender(out var displayed) && ReferenceEquals(displayed,source),
                    scenario+" source identity/pose/frame/context stay published until ACK");
                if(scenario is "reject" or "cancel")
                {
                    var retired=pending;
                    if(scenario=="reject")check(runtime.RejectGroupMaterial(retired!,"consumer unavailable"),scenario+" reject exact target");
                    else cancellation.Cancel();
                    check(runtime.ReadPendingGroupMaterial() is null && runtime.TryReadRender(out displayed) && ReferenceEquals(displayed,source),
                        scenario+" refusal or post-return cancellation retains the source");
                    await Choose();pending=runtime.ReadPendingGroupMaterial();
                    check(pending is not null && capture.Calls==calls && !runtime.AcceptGroupMaterialReady(retired!)
                        && !runtime.RejectGroupMaterial(retired!,"late"),scenario+" retry reuses current material and stale token cannot clear replacement");
                }
                if(scenario is "held-down" or "motion-retire")
                {
                    check(runtime.BeginGameMapDrag(Pointer("down",4,2)).Success && !runtime.AcceptGroupMaterialReady(pending!),scenario+" press fences material ACK");
                    Move("up",5,2,scenario=="motion-retire"?2:0,scenario=="motion-retire"?3:0,scenario=="motion-retire"?2:0,scenario=="motion-retire"?3:0);
                    if(scenario=="motion-retire")
                    {
                        check(runtime.ReadPendingGroupMaterial() is null && !runtime.AcceptGroupMaterialReady(pending!)
                            && runtime.TryReadRender(out var moved) && moved is {PresentationOffsetX:17,PresentationOffsetY:-3},
                            scenario+" actual motion retires old material and preserves newly accumulated translation");
                        continue;
                    }
                }
                if(scenario is "binding-retire" or "close-retire")
                {
                    if(scenario=="binding-retire")capture.Advance();else await Ask("close_map");
                    check(!runtime.AcceptGroupMaterialReady(pending!) && !runtime.TryReadRender(out _),scenario+" stale source cannot grant manual identity/display");
                    continue;
                }
                check(runtime.AcceptGroupMaterialReady(pending!),scenario+" current material ACK commits manual member");
                var after=runtime.ReadState();
                check(after.GroupChoice.Generation==before.GroupChoice.Generation+1 && after.GroupChoice.HeldMapId==before.Context.MapId
                    && after.Context==before.Context && after.NativePose==before.NativePose && runtime.PointerContextSlot.Read()==pointerContext,
                    scenario+" original manual generation advances without replacing the observation or pose");
                check(runtime.TryReadRender(out var accepted) && ReferenceEquals(accepted,pending) && accepted!.IsTransient==drag
                    && accepted.PresentationOffsetX==source.PresentationOffsetX && accepted.PresentationOffsetY==source.PresentationOffsetY
                    && ReferenceEquals(group,Field(runtime,"_geometryGroup")) && SteadyCount(runtime)==steady,
                    scenario+" ACK retains source group/proofs/cache and existing drag presentation");
                if(drag)check(runtime.ReadMaterialToRemember() is null && !runtime.AcknowledgeRenderMaterial(accepted!).Accepted,
                    scenario+" reused transient presentation is not a new learning observation");
                else
                {
                    check(ReferenceEquals(runtime.ReadMaterialToRemember(),accepted) && !runtime.AcknowledgeRenderMaterial(source).NeedsWorker,
                        scenario+" ordinary reused material has a new learning proposal and rejects the old source token");
                    var acknowledged=runtime.AcknowledgeRenderMaterial(accepted!);
                    await runtime.RememberRenderMaterialAsync(source.MaterialToken,before.Context,default);
                    check(acknowledged is {Accepted:true,NeedsWorker:true} && ReferenceEquals(runtime.ReadMaterialToRemember(),accepted)
                        && (string?)Field(runtime,"_rememberedMaterial")==source.MaterialToken,
                        scenario+" old queued maintenance after current ACK cannot consume or write the replacement proposal");
                    await runtime.RememberRenderMaterialAsync(accepted!.MaterialToken,after.Context,default);
                    check(runtime.ReadMaterialToRemember() is null && (string?)Field(runtime,"_rememberedMaterial")==accepted.MaterialToken
                        && ReferenceEquals(group,Field(runtime,"_geometryGroup")) && SteadyCount(runtime)==steady,
                        scenario+" new material remembers only the current same-floor group without resetting its cache");
                }
                await Choose();
                check(runtime.ReadPendingGroupMaterial() is null && capture.Calls==calls && runtime.ReadState().GroupChoice.Generation==after.GroupChoice.Generation,
                    scenario+" repeated confirmed choice is idempotent");
                check(runtime.BeginGameMapDrag(Pointer("down",10,4)).Success,scenario+" source context accepts next gesture");
                Move("up",11,4,2,3,2,3);
                check(runtime.TryReadRender(out var next) && next!.PresentationOffsetX==source.PresentationOffsetX+2
                    && next.PresentationOffsetY==source.PresentationOffsetY+3,scenario+" next gesture adds to retained offset exactly once");
                await File.WriteAllTextAsync(Path.Combine(output,scenario+".json"),JsonSerializer.Serialize(new {before=before.Context,after=after.Context,
                    captureDelta=capture.Calls-calls,steadyBefore=steady,steadyAfter=SteadyCount(runtime),
                    scope="Actual Runtime dispatch/ACK, real S0 registered group/resources/post-Nov5 PNG. Original actual Host oracle read only after fit. Capture/physical pointer supplied by fixture; no native/game or timing acceptance."},new JsonSerializerOptions{WriteIndented=true}));
                Task<RuntimeOperationResult> Choose(CancellationToken token=default)
                {
                    var state=runtime.ReadState();return Ask("select_group_member",new {mapId=state.Context.MapId,groupId=state.IdentityGroupId,
                        groupGeneration=state.GroupChoice.Generation,cycleId=state.GroupChoice.CycleId,floor=state.Context.FloorId},token);
                }
                GlobalInputPointerEvent Pointer(string phase,long seq,long gesture)=>new(phase,geometry.Left+geometry.Width/2,geometry.Top+geometry.Height/2,Now(),1,1,1,1,
                    ContextId:pointerContext.ContextId,Sequence:seq,GestureId:gesture,PressContextSnapshot:pointerContext);
                void Move(string phase,long seq,long gesture,double tx,double ty,double dx,double dy)
                {
                    var pointer=Pointer(phase,seq,gesture);var step=new MapDragStep(dx,dy,tx,ty,phase=="up");
                    check(runtime.PrepareGameMapDragMotion(pointer,step,sequence.Read()).Success && runtime.ApplyGameMapDragMotion(pointer,step).Success,scenario+" "+phase+seq);
                }
            }
            finally{runtime.Shutdown();}
            Task<RuntimeOperationResult> Ask(string action,object? payload=null,CancellationToken token=default)=>runtime.DispatchAsync(new(action,
                JsonSerializer.SerializeToElement(payload??new {}),new(sequence.Issue(),runtime.ReadSnapshot().Context)),token).AsTask();
        }
    }
    static object? Field(object owner,string name)=>owner.GetType().GetField(name,BindingFlags.Instance|BindingFlags.NonPublic)!.GetValue(owner);
    static int SteadyCount(LiveRuntime runtime)=>((IDictionary)Field(runtime,"_steadyFloors")!).Count;
    static double Now()=>System.Diagnostics.Stopwatch.GetTimestamp()/(double)System.Diagnostics.Stopwatch.Frequency;
}
