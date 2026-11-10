using System.Text.Json;
using System.Text.Json.Nodes;
using StarMap.Core.Input;
using StarMap.Core.Session;
using StarMap.Runtime;

internal static class MapCloseFailureRegression
{
    internal static async Task Run(string resources,string replay,string oracle,string output,Action<bool,string> check)
    {
        var index=JsonNode.Parse(await File.ReadAllBytesAsync(Path.Combine(replay,"all-samples.json")))!["samples"]!.AsArray()
            .ToDictionary(r=>r!["id"]!.GetValue<string>(),r=>r!.AsObject());
        var rows=JsonNode.Parse(await File.ReadAllBytesAsync(oracle))!["cases"]!.AsArray();
        foreach(var row in rows)foreach(bool throws in new[]{false,true})
        {
            string name=row!["name"]!.GetValue<string>()+(throws?"-throw":"-null");
            var capture=new RecordedCapture(index);capture.Select(row["sample"]!.GetValue<string>(),false);
            var runtime=new LiveRuntime(capture,resources);var sequence=new SessionSequencer();
            try
            {
                await Ask("set_context",new {season=row["season"]!.GetValue<string>(),mode=row["mode"]!.GetValue<string>(),profileId="shared-structure"});
                if(row["manualMap"]?.GetValue<string>() is { } map)
                {await Ask("prepare_manual_alignment");await Ask("confirm_route",new {mapId=map});}
                else{await Ask("start_round");await Ask("capture_now");}
                var old=runtime.PointerContextSlot.Read()!;var before=runtime.ReadState();
                if(throws)capture.ThrowNext(new IOException("isolated failed source capture"));else capture.FailNext();
                var result=await Ask("refresh_map");var after=runtime.ReadState();var current=runtime.PointerContextSlot.Read();
                var expected=row["failure"]!["state"]!;
                await File.WriteAllTextAsync(Path.Combine(output,name+".json"),JsonSerializer.Serialize(new {result=result.Receipt,before,after,current},new JsonSerializerOptions{WriteIndented=true}));
                check(!result.Receipt.Success && after.MapOpen==expected["mapOpen"]!.GetValue<bool>() && after.Context.MapId==expected["mapId"]!.GetValue<string>()
                    && after.Context.FloorId==expected["floor"]?.GetValue<string>() && after.ObservedFloor==expected["observedFloor"]?.GetValue<string>(),
                    name+" acquisition failure preserves original opening/identity and clears only the required manual floor");
                check((current is not null)==row["failure"]!["contextAvailable"]!.GetValue<bool>() && after.Context.FrameId is null && !runtime.TryReadRender(out _),
                    name+" raw close evidence remains separate from retired numeric pixels and pose");
                var xy=row["point"]!.AsArray();var press=new GlobalInputPointerEvent("down",(int)xy[0]!.GetValue<double>(),(int)xy[1]!.GetValue<double>(),Now(),1,1,1,1,
                    ContextId:old.ContextId,Sequence:1,GestureId:1,PressContextSnapshot:old);
                check(!runtime.ObserveGameFloorClick(press,sequence.Read()).CloseControlHit,name+" old press cannot survive the failure epoch");
                press=press with {ContextId=current!.ContextId,PressContextSnapshot=current};
                check(runtime.ObserveGameFloorClick(press,sequence.Read()).CloseControlHit==row["failure"]!["currentHit"]!["closeControlHit"]!.GetValue<bool>(),
                    name+" new current press retains the original visible X decision");
            }
            finally{runtime.Shutdown();}
            Task<RuntimeOperationResult> Ask(string action,object? payload=null)=>runtime.DispatchAsync(new(action,JsonSerializer.SerializeToElement(payload??new {}),
                new(sequence.Issue(),runtime.ReadSnapshot().Context)),default).AsTask();
        }
        // Match the real Windows capture-busy early return: no new pixels,
        // while IsCurrent still reports its previous stamp. Only the OS port
        // and storage-factory side effect are replaced; Runtime policy is real.
        var inner=new RecordedCapture(index){EvidenceKind=CaptureEvidenceKind.LiveClient};inner.Select("batch01-event-000006",false);
        var preserving=new StampPreservingBusy(inner);int memoryCalls=0;
        var owner=new LiveRuntime(preserving,resources,new(){ConfirmationMemoryFactory=_=>{memoryCalls++;throw new InvalidOperationException("old pixels crossed into human memory");}});
        var tickets=new SessionSequencer();
        try
        {
            await AskBusy("set_context",new {season="s0",mode="nightmare",profileId="shared-structure"});await AskBusy("start_round");await AskBusy("capture_now");
            var context=(GameFloorClickContext)owner.PointerContextSlot.Read()!.DownIntentSnapshot!;var map=owner.ReadState().Context.MapId;
            preserving.BusyNext=true;await AskBusy("confirm_route",new {mapId=map});
            check(preserving.IsCurrent(context.Stamp) && memoryCalls==0 && owner.ReadState().Context.FrameId is null
                && owner.PointerContextSlot.Read() is not null && !owner.TryReadRender(out _),
                "simulated-live capture-busy preserves old stamp but raw control retention cannot save old pixels as a human label");
        }
        finally{owner.Shutdown();}
        Task<RuntimeOperationResult> AskBusy(string action,object? payload=null)=>owner.DispatchAsync(new(action,JsonSerializer.SerializeToElement(payload??new {}),
            new(tickets.Issue(),owner.ReadSnapshot().Context)),default).AsTask();
    }
    static double Now()=>System.Diagnostics.Stopwatch.GetTimestamp()/(double)System.Diagnostics.Stopwatch.Frequency;
    sealed class StampPreservingBusy(RecordedCapture inner):ICapturePort
    {
        internal bool BusyNext;
        public bool IsBound=>inner.IsBound;
        public bool IsForeground=>inner.IsForeground;
        public CaptureEvidenceKind EvidenceKind=>inner.EvidenceKind;
        public CaptureBindingScope? BindingScope=>inner.BindingScope;
        public bool IsCurrent(CaptureStamp stamp)=>inner.IsCurrent(stamp);
        public bool IsObservationContextCurrent(CapturedClientFrame frame)=>inner.IsObservationContextCurrent(frame);
        public ValueTask<ClientCaptureResult> CaptureAsync(CancellationToken token)
        {if(BusyNext){BusyNext=false;return ValueTask.FromResult(new ClientCaptureResult(null,"capture-busy"));}return inner.CaptureAsync(token);}
    }
}
