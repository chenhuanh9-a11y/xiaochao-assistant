using System.Collections.Concurrent;
using System.Diagnostics;
using System.Text.Json;
using System.Text.Json.Nodes;
using StarMap.Core.Input;
using StarMap.Core.Session;
using StarMap.Runtime;

internal static class FloorClickSessionRegression
{
    internal static Task Run(string resources,string replay,string oracle,string output,Action<bool,string> check)=>Task.Run(()=>
    {
        var index=JsonNode.Parse(File.ReadAllBytes(Path.Combine(replay,"all-samples.json")))!["samples"]!.AsArray()
            .ToDictionary(row=>row!["id"]!.GetValue<string>(),row=>row!.AsObject());
        var cases=JsonNode.Parse(File.ReadAllBytes(oracle))!["cases"]!.AsArray();
        foreach(string scenario in new[]{"late-entry","ordered","active-capture"})
        {
            var capture=new RecordedCapture(index);capture.Select("batch01-event-000006",false);
            var runtime=new LiveRuntime(capture,resources);var setup=new SessionSequencer();var owner=new OwnerQueue();
            var ingress=new Ingress(runtime,scenario!="active-capture");SessionService? session=null;
            RecordedCapture.Hold? captureHold=null;
            try
            {
                Ask("set_context",new {season="s0",mode="nightmare",profileId="shared-structure"});Ask("start_round");Ask("capture_now");
                var snapshot=runtime.PointerContextSlot.Read()!;
                var row=cases.First(row=>row!["name"]!.GetValue<string>()=="nightmare-ordered")!;
                var second=row["steps"]![1]!;var first=row["steps"]![0]!;
                var down=Press(first,snapshot,1);var back=Press(second,snapshot,2);
                session=new(owner,ingress);
                int before=capture.Calls;
                if(scenario=="active-capture")captureHold=capture.HoldNext();
                session.Dispatch("refresh_map",JsonSerializer.SerializeToElement(new {oldGenericRefresh=true}));
                owner.Until(()=>scenario=="active-capture"?captureHold!.Started.Task.IsCompleted:ingress.Started.Task.IsCompleted);
                var clicked=runtime.ObserveGameFloorClick(down,session.Read().Sequence);
                check(clicked.Success && runtime.ReadState() is {MapOpen:true,FloorClickIntent.FloorId:"2"} && !runtime.TryReadRender(out _),scenario+" real synchronous intent accepts while generic Session work is busy");
                if(scenario=="late-entry")
                {
                    // Let uncancelled R0 actually enter after the intent. Its
                    // ticket must be refused before it can claim a newer epoch.
                    ingress.Release.TrySetResult();owner.Until(()=>ingress.Returned.Task.IsCompleted);
                    check(ingress.Last?.Receipt.Reason=="request-retired-by-floor-intent" && capture.Calls==before
                        && runtime.ReadState() is {MapOpen:true,FloorClickIntent.FloorId:"2"},"retired generic R0 cannot enter or clear the newer intent before UI supersession");
                }
                session.RefreshRuntime();session.Dispatch("refresh_map",JsonSerializer.SerializeToElement(new {floorClickSequence=1}));
                if(scenario=="ordered")
                {
                    back=back with {CreatedAtSeconds=Now()};
                    check(runtime.ObserveGameFloorClick(back,session.Read().Sequence).Success,"second queued press uses its original observation through the owned click epoch span");
                    session.RefreshRuntime();session.Dispatch("refresh_map",JsonSerializer.SerializeToElement(new {floorClickSequence=2}));
                    capture.Select("batch01-event-000006",false);
                }
                else capture.Select("batch02-event-000018",false);
                ingress.Release.TrySetResult();captureHold?.Release.TrySetResult();
                owner.Until(()=>session.DrainAsync().IsCompleted);
                string expected=scenario=="ordered"?"1":"2";
                check(runtime.ReadState().Context.FloorId==expected && runtime.ReadState().FloorClickIntent is null && runtime.ReadState().MapOpen,
                    scenario+" final fresh highlight consumes only the latest intent and retains the opening");
                check(session.Read().Runtime.Context.FloorId==expected && capture.Calls==before+(scenario=="active-capture"?2:1),
                    scenario+" actual Session consumes the current Runtime and no obsolete queued capture runs");
                if(scenario=="ordered")check(runtime.TryReadRender(out _),"latest ordered click returns the actually fitted original floor rather than the cancelled other-layer pose");
                File.WriteAllText(Path.Combine(output,scenario+".json"),JsonSerializer.Serialize(new {state=runtime.ReadState(),session=session.Read(),capture.Calls},new JsonSerializerOptions {WriteIndented=true}));
            }
            finally
            {
                ingress.Release.TrySetResult();captureHold?.Release.TrySetResult();
                session?.Shutdown();runtime.Shutdown();if(session is not null)owner.Until(()=>session.DrainAsync().IsCompleted);
            }
            void Ask(string action,object? payload=null)=>runtime.DispatchAsync(new(action,JsonSerializer.SerializeToElement(payload??new {}),
                new(setup.Issue(),runtime.ReadSnapshot().Context)),default).AsTask().GetAwaiter().GetResult();
        }
    });
    static double Now()=>Stopwatch.GetTimestamp()/(double)Stopwatch.Frequency;
    static GlobalInputPointerEvent Press(JsonNode step,GlobalInputPointerContextSnapshot snapshot,long sequence)
        =>new("down",step["point"]![0]!.GetValue<int>(),step["point"]![1]!.GetValue<int>(),Now(),1,1,1,1,
            ContextId:snapshot.ContextId,Sequence:sequence,GestureId:sequence,PressContextSnapshot:snapshot);
    sealed class OwnerQueue:ISessionDispatcher
    {
        readonly int thread=Environment.CurrentManagedThreadId;readonly ConcurrentQueue<Action> callbacks=new();
        public bool CheckAccess()=>thread==Environment.CurrentManagedThreadId;
        public void Post(Action callback)=>callbacks.Enqueue(callback);
        public void Until(Func<bool> done)
        {
            var clock=Stopwatch.StartNew();
            while(true){while(callbacks.TryDequeue(out var callback))callback();if(done())return;
                if(clock.Elapsed>TimeSpan.FromSeconds(20))throw new TimeoutException("actual floor-click Session barrier/drain timed out");Thread.Sleep(1);}
        }
    }
    sealed class Ingress(LiveRuntime actual,bool block):ISessionRuntime,ISessionRuntimeProgress,IRuntimeRecoveryControl
    {
        bool pending=block;
        internal TaskCompletionSource Started=new(TaskCreationOptions.RunContinuationsAsynchronously),Release=new(TaskCreationOptions.RunContinuationsAsynchronously),Returned=new(TaskCreationOptions.RunContinuationsAsynchronously);
        internal RuntimeOperationResult? Last;
        public RuntimeSnapshot ReadSnapshot()=>actual.ReadSnapshot();
        public SessionRuntimeRead ReadSnapshotFor(SessionRequestContext request)=>actual.ReadSnapshotFor(request);
        public void CancelMapOpenRecovery(string reason)=>actual.CancelMapOpenRecovery(reason);
        public async ValueTask<RuntimeOperationResult> DispatchAsync(SessionRuntimeRequest request,CancellationToken token)
        {
            bool held=pending && request.Action=="refresh_map";
            if(held){pending=false;Started.TrySetResult();await Release.Task.WaitAsync(TimeSpan.FromSeconds(15)).ConfigureAwait(false);}
            var result=await actual.DispatchAsync(request,token).ConfigureAwait(false);
            if(held){Last=result;Returned.TrySetResult();}return result;
        }
    }
}
