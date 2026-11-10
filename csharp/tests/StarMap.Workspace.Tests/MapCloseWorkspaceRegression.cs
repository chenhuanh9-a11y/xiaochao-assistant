using System.Diagnostics;
using System.IO;
using System.Reflection;
using System.Text.Json;
using System.Text.Json.Nodes;
using System.Windows.Threading;
using StarMap.App;
using StarMap.App.Platform;
using StarMap.Core.Input;
using StarMap.Core.Settings;
using StarMap.Runtime;

internal static class MapCloseWorkspaceRegression
{
    internal static async Task Run(string resources,string replay,string oracle,string output,Action<bool,string> check)
    {
        var index=JsonNode.Parse(await File.ReadAllBytesAsync(Path.Combine(replay,"all-samples.json")))!["samples"]!.AsArray()
            .ToDictionary(r=>r!["id"]!.GetValue<string>(),r=>r!.AsObject());
        var rows=JsonNode.Parse(await File.ReadAllBytesAsync(oracle))!["cases"]!.AsArray();
        foreach(var row in rows)foreach(string scenario in new[]{"idle","busy","failure"})
        {
            bool busy=scenario=="busy";
            string name=row!["name"]!.GetValue<string>()+"-"+scenario,sample=row["sample"]!.GetValue<string>();
            string season=row["season"]!.GetValue<string>(),mode=row["mode"]!.GetValue<string>();
            var settings=new SettingsDocument(new JsonObject {["gameSeason"]=season,["gameMode"]=mode,["routeProfileId"]="shared-structure"});
            var capture=new RecordedCapture(index);capture.Select(sample,false);
            var page=new RuntimePage(capture,_=>{});var shell=new WorkspaceWindow(Path.Combine(output,"close-shell-"+name),null);
            await Field<RuntimePage>(shell,"runtimePage").CloseAsync();
            typeof(WorkspaceWindow).GetField("runtimePage",BindingFlags.Instance|BindingFlags.NonPublic)!.SetValue(shell,page);
            Field<RuntimeOverlayHost>(shell,"runtimeOverlay").Dispose();
            var consume=typeof(WorkspaceWindow).GetMethod("ConsumeInput",BindingFlags.Instance|BindingFlags.NonPublic)!;
            var bindings=GlobalInputBindings.FromSettings(settings);var source=new FixtureInputSource(bindings){NowSeconds=Now()};
            var delivered=new List<WpfGlobalInputDelivery>();long pointerSequence=0;
            await using var host=new WpfGlobalInputController(Dispatcher.CurrentDispatcher,bindings,
                ()=>new(new(42,true,42,false,true,true),BindingGeneration:1,PointerTarget:new(42,"runtime-slot",0,page.GamePointerContextSlot),AllowPointerIntentPress:true),
                item=>{delivered.Add(item);consume.Invoke(shell,[item]);},source);
            RecordedCapture.Hold? hold=null;
            try
            {
                check(await page.Configure(resources,settings),name+" real production page binds the requested resources");
                if(row["manualMap"]?.GetValue<string>() is { } selected)
                {page.Dispatch("prepare_manual_alignment");await Drain();page.Dispatch("confirm_route",new {mapId=selected});await Drain();}
                else{page.Dispatch("start_round");await Drain();}
                await host.StartAsync();
                if(page.State?.MapOpen!=true)await MapKey();
                var runtime=Field<LiveRuntime>(page,"runtime");var before=page.State!;var snapshot=page.GamePointerContextSlot!.Read()!;
                check(snapshot.DownIntentSnapshot is GameFloorClickContext && before.MapOpen && before.RoundActive,
                    name+" actual current PNG creates pointer scope even when a manual map cannot fit");
                var context=(GameFloorClickContext)snapshot.DownIntentSnapshot!;
                // Same non-X navigation probe used by the original detector
                // oracle: it must neither close nor introduce another capture.
                int calls=capture.Calls;
                await Pointer(context.Geometry.Left+(int)Math.Round(.945*context.Geometry.Width),
                    context.Geometry.Top+(int)Math.Round(.18*context.Geometry.Height),snapshot);
                check(page.State!.MapOpen && capture.Calls==calls,name+" navigation control does not close or refresh the map");
                if(scenario=="failure")
                {
                    capture.ThrowNext(new IOException("isolated failed source capture"));page.Dispatch("refresh_map");await Drain();
                    var renewed=page.GamePointerContextSlot!.Read();
                    check(renewed is not null && page.State!.MapOpen && page.SessionView?.LastReceipt is {Action:"refresh_map",Success:false}
                        && page.State.Context.FrameId is null && !runtime.TryReadRender(out _),
                        name+" actual Session consumes its own acquisition failure while retaining only raw control evidence");
                    snapshot=renewed??throw new InvalidDataException("No current raw control scope after the failed acquisition.");
                }
                if(busy)
                {hold=capture.HoldNext();page.Dispatch("refresh_map");await hold.Started.Task.WaitAsync(TimeSpan.FromSeconds(10));}
                int closeCalls=capture.Calls;var xy=row["point"]!.AsArray();
                var press=await Pointer((int)xy[0]!.GetValue<double>(),(int)xy[1]!.GetValue<double>(),snapshot!);
                var immediate=page.State!;
                await Save("immediate",new {before,immediate,capture.Calls,delivery=delivered.Single(d=>ReferenceEquals(d.Notification.Pointer,press)).PointerIntentPress is not null});
                check(!immediate.MapOpen && !runtime.TryReadRender(out _) && runtime.PointerContextSlot.Read() is null
                    && !Field<OrderedMapInput>(page,"mapInput").OpenIntent,
                    name+" actual Workspace down revokes display and resets G phase before queued close completes");
                Matches(immediate,row["immediate"]!,"immediate");
                hold?.Release.TrySetResult();await Drain();
                var closed=page.State!;await Save("closed",new {closed,session=page.SessionView,capture.Calls});
                check(capture.Calls==closeCalls && page.SessionView?.LastReceipt is {Action:"close_map",Success:true},
                    name+" close cancels old work and consumes close_map without taking a new screenshot");
                Matches(closed,row["after"]!,"closed");
                check(closed.Context.MapId==before.Context.MapId && closed.RemainingMapIds.SequenceEqual(before.RemainingMapIds),
                    name+" close retains this round's identity and monotonically filtered candidate pool");
                long issued=page.SessionView!.Sequence.LastIssuedSequence;
                await Pointer((int)xy[0]!.GetValue<double>(),(int)xy[1]!.GetValue<double>(),snapshot);
                check(page.SessionView!.Sequence.LastIssuedSequence==issued && !page.State!.MapOpen,
                    name+" repeated old close press issues no second job");
                await MapKey();var reopened=page.State!;
                await Save("reopened",new {reopened,session=page.SessionView,capture.Calls});
                Matches(reopened,row["reopened"]!,"reopened");
                check(reopened.MapOpen && runtime.PointerContextSlot.Read() is not null && Field<OrderedMapInput>(page,"mapInput").OpenIntent,
                    name+" next real host G edge opens and observes the map again");
                issued=page.SessionView!.Sequence.LastIssuedSequence;
                var stale=await Pointer((int)xy[0]!.GetValue<double>(),(int)xy[1]!.GetValue<double>(),snapshot);
                check(delivered.Single(d=>ReferenceEquals(d.Notification.Pointer,stale)).PointerIntentPress is null
                    && page.State!.MapOpen && page.SessionView!.Sequence.LastIssuedSequence==issued,
                    name+" old pre-close context cannot close the newly opened map");
            }
            finally
            {
                hold?.Release.TrySetResult();await host.StopAsync().WaitAsync(TimeSpan.FromSeconds(15));
                await page.CloseAsync().WaitAsync(TimeSpan.FromSeconds(15));shell.Close();
            }
            void Matches(LiveRuntimeState state,JsonNode expected,string phase)
                =>check(state.Context.MapId==expected["mapId"]?.GetValue<string>() && state.Context.FloorId==expected["floor"]?.GetValue<string>()
                    && state.ObservedFloor==expected["observedFloor"]?.GetValue<string>() && state.MapOpen==expected["mapOpen"]!.GetValue<bool>()
                    && state.ManualAlignmentSession==expected["manualAlignment"]!.GetValue<bool>() && state.RoundActive==expected["roundActive"]!.GetValue<bool>(),
                    name+" "+phase+" state matches the actual original resource/PNG runtime");
            Task Save(string phase,object value)=>File.WriteAllTextAsync(Path.Combine(output,"map-close-"+name+"-"+phase+".json"),
                JsonSerializer.Serialize(new {value,scope="Actual WPF source/channel/Dispatcher/Workspace/Page/Session/Runtime with recorded capture and fixed foreground/native-source seam. Hidden shell, no native mouse/game or visual acceptance. S1 IDs are explicit simulated human choices, not automatic solver truth."},new JsonSerializerOptions{WriteIndented=true}));
            async Task Drain()=>await page.DrainAsync().WaitAsync(TimeSpan.FromSeconds(20));
            async Task MapKey()
            {
                source.NowSeconds=Now();var key=source.Key('G',true)!;source.Key('G',false);
                await Until(()=>delivered.Any(d=>ReferenceEquals(d.Notification.Key,key)));await Drain();
            }
            async Task<GlobalInputPointerEvent> Pointer(int x,int y,GlobalInputPointerContextSnapshot context)
            {
                source.NowSeconds=Now();long seq=++pointerSequence;
                var down=new GlobalInputPointerEvent("down",x,y,source.NowSeconds,42,42,source.Generation,1,
                    ContextId:context.ContextId,Sequence:seq,GestureId:seq,PressContextSnapshot:context);
                source.Post(new(GlobalInputNotificationKind.Pointer,source.Generation,Pointer:down));
                await Until(()=>delivered.Any(d=>ReferenceEquals(d.Notification.Pointer,down)));
                source.NowSeconds=Now();var up=down with {Phase="up",CreatedAtSeconds=source.NowSeconds,Sequence=++pointerSequence};
                source.Post(new(GlobalInputNotificationKind.Pointer,source.Generation,Pointer:up));
                await Until(()=>delivered.Any(d=>ReferenceEquals(d.Notification.Pointer,up)));return down;
            }
        }
    }
    static T Field<T>(object target,string name)=>(T)target.GetType().GetField(name,BindingFlags.Instance|BindingFlags.NonPublic)!.GetValue(target)!;
    static double Now()=>Stopwatch.GetTimestamp()/(double)Stopwatch.Frequency;
    static async Task Until(Func<bool> done)
    {var clock=Stopwatch.StartNew();while(!done()){if(clock.Elapsed>TimeSpan.FromSeconds(15))throw new TimeoutException("close pointer/key did not reach actual WPF consumer");await Task.Delay(5);}}
}
