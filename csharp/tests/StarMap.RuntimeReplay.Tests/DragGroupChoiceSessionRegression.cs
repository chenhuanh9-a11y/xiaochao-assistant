using System.Collections.Concurrent;
using System.Diagnostics;
using System.Text.Json;
using System.Text.Json.Nodes;
using StarMap.Core.Input;
using StarMap.Core.Session;
using StarMap.Runtime;

internal static class DragGroupChoiceSessionRegression
{
    internal static Task Run(string resources,Dictionary<string,JsonObject> index,string output,Action<bool,string> check)=>Task.Run(()=>
    {
        var capture=new RecordedCapture(index);capture.Select("batch01-event-000006",false);
        var runtime=new LiveRuntime(capture,resources);var setup=new SessionSequencer();var owner=new OwnerQueue();
        var bridge=new Handoff(runtime);var clock=new DeadlineClock();SessionService? session=null;
        try
        {
            Ask("set_context",new {season="s0",mode="nightmare",profileId="shared-structure"});Ask("start_round");Ask("capture_now");
            var state=runtime.ReadState();var context=runtime.PointerContextSlot.Read()!;
            var g=((GameFloorClickContext)context.DownIntentSnapshot!).Geometry;
            GlobalInputPointerEvent Pointer(string phase,long seq,int dx,int dy)=>new(phase,g.Left+g.Width/2+dx,g.Top+g.Height/2+dy,Now(),1,1,1,1,
                ContextId:context.ContextId,Sequence:seq,GestureId:1,PressContextSnapshot:context);
            check(runtime.BeginGameMapDrag(Pointer("down",1,0,0)).Success,"session expiry fixture starts on actual registered source");
            var up=Pointer("up",2,15,-6);var step=new MapDragStep(15,-6,15,-6,true);
            check(runtime.PrepareGameMapDragMotion(up,step,setup.Read()).Success && runtime.ApplyGameMapDragMotion(up,step).ProbeRequired,"session expiry fixture applies real drag protocol");
            runtime.TryReadRender(out var source);
            session=new(owner,bridge,clock);
            session.Dispatch("select_group_member",JsonSerializer.SerializeToElement(new {mapId="nightmare-12",groupId=state.IdentityGroupId,
                groupGeneration=state.GroupChoice.Generation,cycleId=state.GroupChoice.CycleId,floor=state.Context.FloorId}),TimeSpan.FromSeconds(10));
            owner.Until(()=>bridge.Returned.Task.IsCompleted);
            // The real Runtime returned and disposed its operation registration.
            // Only the handoff to Session is delayed; no solver/state is replaced.
            var expired=runtime.ReadPendingGroupMaterial();
            check(expired is {MapId:"nightmare-12"} && session.Read().WorkerActive && runtime.ReadState().Context.FrameId==state.Context.FrameId,
                "real Session waits after real Runtime returned with a staged fresh target");
            clock.Advance(TimeSpan.FromSeconds(11));owner.Pump();
            check(session.Read().LastFailure is {Phase:"timeout"} && runtime.ReadState().MapOpen && runtime.ReadPendingGroupMaterial() is null
                && runtime.ReadState().GroupChoice is {PendingMapId:null,DesiredMapId:"nightmare-12",Status:"awaiting-geometry"}
                && runtime.TryReadRender(out var retained) && ReferenceEquals(retained,source) && runtime.PointerContextSlot.Read()==context,
                "actual Session deadline after Runtime return releases target slot and preserves source opening");
            bridge.Release.TrySetResult();owner.Until(()=>!session.Read().WorkerActive);
            check(session.Read().StaleResults>=1 && runtime.ReadState().Context.MapId==state.Context.MapId,
                "expired handoff result is rejected by actual Session collection");
            session.Dispatch("refresh_map");owner.Until(()=>!session.Read().WorkerActive && runtime.ReadPendingGroupMaterial() is not null);
            check(!runtime.AcceptGroupMaterialReady(expired!) && !runtime.RejectGroupMaterial(expired!,"expired old target")
                && runtime.ReadPendingGroupMaterial() is {MapId:"nightmare-12"},"Session timeout permits fresh retry and late material cannot clear replacement");
            File.WriteAllText(Path.Combine(output,"session-expiry.json"),JsonSerializer.Serialize(new {view=session.Read(),state=runtime.ReadState(),capture.Calls,
                scope="Actual Session timer/result gate and Runtime/solvers/resources; only recorded OS capture, controlled deadline clock and a post-Runtime result-handoff delay. No native/game acceptance."},new JsonSerializerOptions{WriteIndented=true}));
        }
        finally
        {
            bridge.Release.TrySetResult();session?.Shutdown();runtime.Shutdown();
            if(session is not null)owner.Until(()=>session.DrainAsync().IsCompleted);
        }
        void Ask(string action,object? payload=null)=>runtime.DispatchAsync(new(action,JsonSerializer.SerializeToElement(payload??new {}),
            new(setup.Issue(),runtime.ReadSnapshot().Context)),default).AsTask().GetAwaiter().GetResult();
    });
    static double Now()=>Stopwatch.GetTimestamp()/(double)Stopwatch.Frequency;

    sealed class OwnerQueue:ISessionDispatcher
    {
        readonly int thread=Environment.CurrentManagedThreadId;readonly ConcurrentQueue<Action> callbacks=new();
        public bool CheckAccess()=>thread==Environment.CurrentManagedThreadId;
        public void Post(Action action)=>callbacks.Enqueue(action);
        public void Pump(){while(callbacks.TryDequeue(out var action))action();}
        public void Until(Func<bool> done)
        {
            var watch=Stopwatch.StartNew();
            while(true){Pump();if(done())return;if(watch.Elapsed>TimeSpan.FromSeconds(20))throw new TimeoutException("group Session handoff");Thread.Sleep(1);}
        }
    }
    sealed class Handoff(LiveRuntime actual):ISessionRuntime,ISessionRuntimeProgress,IRuntimeRecoveryControl
    {
        bool hold=true;
        public TaskCompletionSource Returned {get;}=new(TaskCreationOptions.RunContinuationsAsynchronously);
        public TaskCompletionSource Release {get;}=new(TaskCreationOptions.RunContinuationsAsynchronously);
        public RuntimeSnapshot ReadSnapshot()=>actual.ReadSnapshot();
        public SessionRuntimeRead ReadSnapshotFor(SessionRequestContext request)=>actual.ReadSnapshotFor(request);
        public void CancelMapOpenRecovery(string reason)=>actual.CancelMapOpenRecovery(reason);
        public async ValueTask<RuntimeOperationResult> DispatchAsync(SessionRuntimeRequest request,CancellationToken token)
        {
            var result=await actual.DispatchAsync(request,token).ConfigureAwait(false);
            if(hold){hold=false;Returned.TrySetResult();await Release.Task.ConfigureAwait(false);}
            return result;
        }
    }
    sealed class DeadlineClock:TimeProvider
    {
        DateTimeOffset now=DateTimeOffset.UtcNow;readonly List<DeadlineTimer> timers=[];
        public override DateTimeOffset GetUtcNow()=>now;
        public override ITimer CreateTimer(TimerCallback callback,object? state,TimeSpan dueTime,TimeSpan period)
        {
            if(period!=Timeout.InfiniteTimeSpan)throw new InvalidOperationException("Only Session one-shot deadlines are controlled here.");
            var timer=new DeadlineTimer(this,callback,state);timer.Change(dueTime,period);timers.Add(timer);return timer;
        }
        public void Advance(TimeSpan elapsed){now+=elapsed;foreach(var timer in timers.ToArray())timer.Fire();}
        sealed class DeadlineTimer(DeadlineClock clock,TimerCallback callback,object? state):ITimer
        {
            DateTimeOffset due=DateTimeOffset.MaxValue;bool disposed;
            public bool Change(TimeSpan delay,TimeSpan period)
            {if(disposed)return false;due=delay==Timeout.InfiniteTimeSpan?DateTimeOffset.MaxValue:clock.now+delay;return true;}
            public void Fire(){if(!disposed && due<=clock.now){due=DateTimeOffset.MaxValue;callback(state);}}
            public void Dispose()=>disposed=true;
            public ValueTask DisposeAsync(){Dispose();return ValueTask.CompletedTask;}
        }
    }
}
