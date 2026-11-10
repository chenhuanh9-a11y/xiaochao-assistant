using System.Text.Json;
using System.Text.Json.Nodes;
using StarMap.Core.Input;
using StarMap.Core.Session;
using StarMap.Runtime;

internal static class GameMapDragRegression
{
    internal static async Task Run(string resources,string replay,string oracle,string output,Action<bool,string> check)
    {
        var index=JsonNode.Parse(await File.ReadAllBytesAsync(Path.Combine(replay,"all-samples.json")))!["samples"]!.AsArray()
            .ToDictionary(row=>row!["id"]!.GetValue<string>(),row=>row!.AsObject());
        foreach(var row in JsonNode.Parse(await File.ReadAllBytesAsync(oracle))!["cases"]!.AsArray())
        foreach(string schedule in new[]{"cancel-before-motion","idle","cancelled-capture","late-capture-error"})
        {
            string name=row!["name"]!.GetValue<string>()+"/"+schedule;
            var recorded=new RecordedCapture(index);recorded.Select(row["sample"]!.GetValue<string>(),false);
            var capture=new Capture(recorded,row["mode"]!.GetValue<string>());var runtime=new LiveRuntime(capture,resources);var sequence=new SessionSequencer();
            RecordedCapture.Hold? hold=null;Task<RuntimeOperationResult>? pending=null;
            using var cancellation=new CancellationTokenSource();
            try
            {
                await Ask("set_context",new {season=row["season"]!.GetValue<string>(),mode=row["mode"]!.GetValue<string>(),profileId="shared-structure"});
                if(row["manualMap"]?.GetValue<string>() is { } map)
                {await Ask("prepare_manual_alignment");await Ask("confirm_route",new {mapId=map});}
                else{await Ask("start_round");await Ask("capture_now");}
                var context=runtime.PointerContextSlot.Read()!;
                bool initialDisplay=runtime.TryReadRender(out var original);
                check(initialDisplay==row["before"]!["display"]!.GetValue<bool>(),name+" original initial display decision");
                if(!initialDisplay)
                {
                    // The original sparse 254 fixture has no display. Retain its
                    // negative, then use actual richer 307 for a positive drag.
                    check(!runtime.BeginGameMapDrag(new("down",500,500,Now(),1,1,1,1,ContextId:context.ContextId,
                        Sequence:1,GestureId:1,PressContextSnapshot:context)).Success,name+" sparse original frame grants no draggable pose");
                    recorded.Select("batch02-event-000307",false);await Ask("fit_manual_frame");
                    context=runtime.PointerContextSlot.Read()!;
                }
                check(runtime.TryReadRender(out original),name+" real-resource display before physical drag");
                var state=runtime.ReadState();
                if(schedule!="idle")
                {
                    capture.IgnoreCancellation=true;hold=recorded.HoldNext();
                    if(schedule=="late-capture-error")recorded.ThrowNext(new IOException("old noncooperative capture error"));
                    pending=Ask(state.ManualAlignmentSession?"observe_manual_floor":"refresh_map",null,cancellation.Token);
                    await hold.Started.Task.WaitAsync(TimeSpan.FromSeconds(10));
                }
                int calls=recorded.Calls;var geometry=((GameFloorClickContext)context.DownIntentSnapshot!).Geometry;
                int x=geometry.Left+geometry.Width/2,y=geometry.Top+geometry.Height/2;
                var down=Pointer("down",1,1,x,y);
                check(runtime.BeginGameMapDrag(down).Success,name+" strict down uses published observation");
                if(schedule=="cancel-before-motion")
                {
                    cancellation.Cancel();
                    check(runtime.TryReadRender(out var afterCancel) && ReferenceEquals(afterCancel,original) && runtime.PointerContextSlot.Read()==context,
                        name+" down reservation survives old token cancellation before any motion");
                }
                check(!runtime.AcknowledgeRenderMaterial(original!).Accepted,name+" down fences learning ACK before motion");
                var motion=new MapDragMotion(x,y,geometry.Left,geometry.Top,geometry.Width,geometry.Height);
                var move=Pointer("move",2,1,x+13,y-7);var step=motion.Consume("move",move.X,move.Y)!;
                check(runtime.PrepareGameMapDragMotion(move,step,sequence.Read()) is {Success:true,FirstMotion:true},name+" first motion retires issued tickets");
                cancellation.Cancel();
                check(runtime.ApplyGameMapDragMotion(move,step).Success,name+" translation survives cancellation reentry");
                check(runtime.TryReadRender(out var moved) && moved!.SharesMaterial(original!)
                    && ReferenceEquals(moved.Material.Pixels,original!.Material.Pixels) && ReferenceEquals(moved.Scene?.Sources,original.Scene?.Sources)
                    && moved.Stamp==original.Stamp && moved.Generation==original.Generation && moved.TransformRevision==1
                    && moved.PresentationOffsetX==13 && moved.PresentationOffsetY== -7,
                    name+" same resident images/token/frame with only presentation changed");
                check(runtime.ReadState().NativePose==state.NativePose && runtime.ReadState().Context.FrameId==state.Context.FrameId
                    && runtime.PointerContextSlot.Read()==context && recorded.Calls==calls && runtime.ReadMaterialToRemember() is null,
                    name+" no capture, canonical pose/frame/context rewrite or learnable translated proposal");
                if(pending is not null)
                {
                    hold!.Release.TrySetResult();var old=await pending.WaitAsync(TimeSpan.FromSeconds(20));pending=null;
                    check(!old.Receipt.Success && runtime.TryReadRender(out var afterOld) && ReferenceEquals(afterOld,moved),name+" old completion/error cannot revoke moved pixels");
                }
                var up=Pointer("up",3,1,x+15,y-6);step=motion.Consume("up",up.X,up.Y)!;
                check(runtime.PrepareGameMapDragMotion(up,step,sequence.Read()).Success && runtime.ApplyGameMapDragMotion(up,step).ProbeRequired,
                    name+" release includes its final delta and requests observation");
                runtime.TryReadRender(out var released);
                var probe=await Ask("probe_map_after_drag",new {gestureId=1,pointerSequence=3});
                var passive=await Ask(state.ManualAlignmentSession?"observe_manual_floor":"refresh_map");
                check(probe.Receipt.Success && passive.Receipt.Success && runtime.TryReadRender(out var afterProbe) && ReferenceEquals(afterProbe,released)
                    && afterProbe!.PresentationOffsetX==15 && afterProbe.PresentationOffsetY== -6
                    && runtime.ReadState().NativePose==state.NativePose && runtime.PointerContextSlot.Read()==context,
                    name+" repeated probe keeps exact translated packet and canonical observation");
                if(row["season"]!.GetValue<string>()=="s0")
                {
                    capture.HideFloorControls=true;
                    var unknown=await Ask("probe_map_after_drag",new {gestureId=1,pointerSequence=3});
                    check(capture.LastObservation is {MapOpen:true,AutomaticEligible:false},name+" derived actual PNG keeps map open but floor unknown");
                    capture.HideFloorControls=false;
                    var recovered=await Ask("refresh_map");
                    check(unknown.Receipt.Success && recovered.Receipt.Success && runtime.TryReadRender(out var afterUnknown) && ReferenceEquals(afterUnknown,released),
                        name+" unknown S0 floor is no contradiction and later good probe keeps original pixels");
                }
                down=Pointer("down",4,2,x,y);check(runtime.BeginGameMapDrag(down).Success,name+" next gesture retains strict context");
                up=Pointer("up",5,2,x+2,y+3);step=new(2,3,2,3,true);
                check(runtime.PrepareGameMapDragMotion(up,step,sequence.Read()).FirstMotion && runtime.ApplyGameMapDragMotion(up,step).ProbeRequired
                    && runtime.TryReadRender(out var second) && second!.PresentationOffsetX==17 && second.PresentationOffsetY== -3,
                    name+" release-only second gesture accumulates offset once");
                check(!runtime.ApplyGameMapDragMotion(up,step).Success,name+" duplicate release cannot apply twice");
                string oldFrame=runtime.ReadState().Context.FrameId!;
                var explicitCapture=await Ask(state.ManualAlignmentSession?"fit_manual_frame":"capture_now");
                check(!explicitCapture.Receipt.Success && explicitCapture.Receipt.Reason=="transient-projection-unavailable"
                    && runtime.ReadState().Context.FrameId!=oldFrame && !runtime.TryReadRender(out _),
                    name+" original explicit fresh capture cannot silently fit over an active transient cycle");
                runtime.InvalidateCapture("map-closed");
                check(!runtime.TryReadRender(out _) && !runtime.BeginGameMapDrag(down).Success,name+" close retires pixels and late gesture");
                await File.WriteAllTextAsync(Path.Combine(output,name.Replace('/','-')+".json"),JsonSerializer.Serialize(new {probe=probe.Receipt,passive=passive.Receipt,
                    originalFrame=state.Context.FrameId,originalPose=state.NativePose,offset=new[]{17,-3},recorded.Calls,
                    scope="Actual Runtime/resource solvers, post-Nov5 captured image; only OS capture replaced. Explicit S1 choices (106008 simulated, not identity truth). No native input or screen acceptance."},new JsonSerializerOptions{WriteIndented=true}));
                GlobalInputPointerEvent Pointer(string phase,long seq,long gesture,int px,int py)=>new(phase,px,py,Now(),1,1,1,1,
                    ContextId:context.ContextId,Sequence:seq,GestureId:gesture,PressContextSnapshot:context);
            }
            finally{hold?.Release.TrySetResult();runtime.Shutdown();if(pending is not null)await pending.WaitAsync(TimeSpan.FromSeconds(20));}
            Task<RuntimeOperationResult> Ask(string action,object? payload=null,CancellationToken token=default)=>runtime.DispatchAsync(new(action,
                JsonSerializer.SerializeToElement(payload??new {}),new(sequence.Issue(),runtime.ReadSnapshot().Context)),token).AsTask();
        }
    }
    static double Now()=>System.Diagnostics.Stopwatch.GetTimestamp()/(double)System.Diagnostics.Stopwatch.Frequency;
    sealed class Capture(RecordedCapture inner,string mode):ICapturePort
    {
        internal bool IgnoreCancellation;
        internal bool HideFloorControls;
        internal StarMap.Vision.FloorObservationResult? LastObservation;
        public bool IsBound=>inner.IsBound;
        public bool IsForeground=>inner.IsForeground;
        public CaptureBindingScope? BindingScope=>inner.BindingScope;
        public bool IsCurrent(CaptureStamp stamp)=>inner.IsCurrent(stamp);
        public bool IsObservationContextCurrent(CapturedClientFrame frame)=>inner.IsObservationContextCurrent(frame);
        public async ValueTask<ClientCaptureResult> CaptureAsync(CancellationToken token)
        {
            var result=await inner.CaptureAsync(IgnoreCancellation?default:token);
            if(!HideFloorControls || result.Frame is not { } frame)return result;
            var pixels=frame.Bgra.ToArray();
            for(int y=(int)(frame.Height*.19);y<(int)(frame.Height*.61);y++)
                for(int x=(int)(frame.Width*.84);x<(int)(frame.Width*.92);x++)
                {int p=y*frame.Stride+x*4;pixels[p]=40;pixels[p+1]=32;pixels[p+2]=24;pixels[p+3]=255;}
            using var bgra=OpenCvSharp.Mat.FromPixelData(frame.Height,frame.Width,OpenCvSharp.MatType.CV_8UC4,pixels);
            using var bgr=new OpenCvSharp.Mat();OpenCvSharp.Cv2.CvtColor(bgra,bgr,OpenCvSharp.ColorConversionCodes.BGRA2BGR);
            // Record the real detector's boundary, never inject its result into Runtime.
            LastObservation=StarMap.Vision.FloorObservation.Observe(bgr,"s0",mode);
            return new(new(pixels,frame.Width,frame.Height,frame.Stride,frame.Stamp,frame.CapturedAt,frame.Context));
        }
    }
}
