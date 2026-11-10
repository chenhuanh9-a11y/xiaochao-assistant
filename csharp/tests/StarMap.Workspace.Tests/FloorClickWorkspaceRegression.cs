using System.Diagnostics;
using System.IO;
using System.Reflection;
using System.Text.Json;
using System.Text.Json.Nodes;
using System.Windows.Threading;
using StarMap.App;
using StarMap.App.Platform;
using StarMap.Core.Input;
using StarMap.Core.Session;
using StarMap.Core.Settings;
using StarMap.Runtime;

internal static class FloorClickWorkspaceRegression
{
    internal static async Task Run(string resources,string replay,string output,Action<bool,string> check)
    {
        var index=JsonNode.Parse(await File.ReadAllBytesAsync(Path.Combine(replay,"all-samples.json")))!["samples"]!.AsArray()
            .ToDictionary(row=>row!["id"]!.GetValue<string>(),row=>row!.AsObject());
        var capture=new RecordedCapture(index);capture.Select("batch01-event-000006",false);
        var page=new RuntimePage(capture,_=>{});
        var shell=new WorkspaceWindow(Path.Combine(output,"pointer-shell"),null);
        var original=Field<RuntimePage>(shell,"runtimePage");await original.CloseAsync();
        typeof(WorkspaceWindow).GetField("runtimePage",BindingFlags.Instance|BindingFlags.NonPublic)!.SetValue(shell,page);
        Field<RuntimeOverlayHost>(shell,"runtimeOverlay").Dispose();
        var consume=typeof(WorkspaceWindow).GetMethod("ConsumeInput",BindingFlags.Instance|BindingFlags.NonPublic)!;
        var bindings=GlobalInputBindings.FromSettings(SettingsDocument.CreateNew());var source=new FixtureInputSource(bindings){NowSeconds=Now()};
        var deliveries=new List<WpfGlobalInputDelivery>();
        await using var host=new WpfGlobalInputController(Dispatcher.CurrentDispatcher,bindings,
            ()=>new(new(42,true,42,false,true,true),BindingGeneration:1,PointerTarget:new(42,"runtime-slot",0,page.GamePointerContextSlot),AllowPointerIntentPress:true),
            delivery=>{deliveries.Add(delivery);consume.Invoke(shell,[delivery]);},source);
        try
        {
            check(await page.Configure(resources,SettingsDocument.CreateNew()),"floor-click-workspace-binds-real-resources-with-only-capture/foreground/source-replaced");
            page.Dispatch("start_round");await page.DrainAsync();page.HandleShortcut("map");await page.DrainAsync();
            Console.WriteLine("floor-click-workspace trace: map-open-drained");
            await host.StartAsync();
            Console.WriteLine("floor-click-workspace trace: fixture-host-started");
            var runtime=Field<LiveRuntime>(page,"runtime");
            var old=page.GamePointerContextSlot!.Read()!;var oldContext=(GameFloorClickContext)old.DownIntentSnapshot!;
            // Derive the actual current rectangle from the same production
            // detector, not a hard-coded pre-Nov5 location.
            using var pixels=OpenCvSharp.Cv2.ImDecode(await File.ReadAllBytesAsync(index["batch01-event-000006"]["image"]!.GetValue<string>()),OpenCvSharp.ImreadModes.Color);
            var observed=StarMap.Vision.FloorObservation.Observe(pixels,"s0","nightmare");
            var rectangle=observed.FloorRead["controlRects"]!.AsArray().First(row=>row!["floor"]!.GetValue<int>()==2)!["rect"]!.AsArray();
            int x=(int)Math.Round(oldContext.Geometry.Left+(rectangle[0]!.GetValue<double>()+rectangle[2]!.GetValue<double>())*.5*oldContext.Geometry.Width);
            int y=(int)Math.Round(oldContext.Geometry.Top+(rectangle[1]!.GetValue<double>()+rectangle[3]!.GetValue<double>())*.5*oldContext.Geometry.Height);
            var hold=capture.HoldNext();page.Dispatch("refresh_map");
            await hold.Started.Task.WaitAsync(TimeSpan.FromSeconds(10));
            Console.WriteLine("floor-click-workspace trace: passive-capture-held");
            GlobalInputPointerEvent down;
            try
            {
                using(Dispatcher.CurrentDispatcher.DisableProcessing())
                {
                    source.NowSeconds=Now();down=new("down",x,y,source.NowSeconds,42,42,source.Generation,1,
                        ContextId:old.ContextId,Sequence:1,GestureId:1,PressContextSnapshot:old);
                    source.Post(new(GlobalInputNotificationKind.Pointer,source.Generation,Pointer:down));
                    UntilSync(()=>source.InSubscriber);
                    Console.WriteLine("floor-click-workspace trace: pointer-waits-for-dispatcher");
                    hold.Release.TrySetResult();
                    UntilSync(()=>page.GamePointerContextSlot.Read()?.DownIntentSnapshot is GameFloorClickContext current && current.ObservationOrdinal>oldContext.ObservationOrdinal);
                    Console.WriteLine("floor-click-workspace trace: observation-advanced-before-ui-delivery");
                    capture.Select("batch02-event-000018",false);source.NowSeconds=Now();
                }
            }
            finally{hold.Release.TrySetResult();}
            await Until(()=>deliveries.Any(item=>ReferenceEquals(item.Notification.Pointer,down)));
            Console.WriteLine("floor-click-workspace trace: press-delivered");
            await page.DrainAsync();
            Console.WriteLine("floor-click-workspace trace: replacement-refresh-drained");
            var delivered=deliveries.Single(item=>ReferenceEquals(item.Notification.Pointer,down));
            check(delivered.Pointer is null && delivered.PointerIntentPress==down,
                "real-channel-dispatcher-defers-queued-old-press-to-intent-only-after-a-passive-observation-advances");
            check(page.State is {MapOpen:true,Context.FloorId:"2",FloorClickIntent:null} && page.SessionView?.LastReceipt?.Action=="refresh_map",
                "actual-Workspace-ConsumeInput-RuntimePage-Session-uses-new-highlight-and-never-revives-the-old-floor");
            check(runtime.PointerContextSlot.Read()?.DownIntentSnapshot is GameFloorClickContext final && final.ObservationOrdinal>oldContext.ObservationOrdinal,
                "hidden-busy-page-publishes-current-pointer-data-without-a-native-business-callback");
            await File.WriteAllTextAsync(Path.Combine(output,"floor-click-workspace.json"),JsonSerializer.Serialize(new {
                state=page.State,session=page.SessionView,capture.Calls,
                scope="Actual WPF controller/channel/dispatcher, Workspace ConsumeInput, RuntimePage/Session/Runtime. Recorded capture and foreground/native source fixture, hidden shell with its original unconfigured page replaced by the injected-capture RuntimePage. No native input/capture/game or visible pixel acceptance."
            },new JsonSerializerOptions {WriteIndented=true}));
        }
        catch(Exception error){Console.Error.WriteLine("floor-click-workspace trace: "+error);throw;}
        finally
        {
            Console.WriteLine("floor-click-workspace trace: stopping-fixture-host");
            await host.StopAsync().WaitAsync(TimeSpan.FromSeconds(15));
            Console.WriteLine("floor-click-workspace trace: closing-page");
            await page.CloseAsync().WaitAsync(TimeSpan.FromSeconds(15));shell.Close();
        }
    }
    static double Now()=>Stopwatch.GetTimestamp()/(double)Stopwatch.Frequency;
    static T Field<T>(object owner,string name)=>(T)owner.GetType().GetField(name,BindingFlags.Instance|BindingFlags.NonPublic)!.GetValue(owner)!;
    static void UntilSync(Func<bool> done)
    {var clock=Stopwatch.StartNew();while(!done()){if(clock.Elapsed>TimeSpan.FromSeconds(15))throw new TimeoutException("queued pointer/frame barrier timed out");Thread.Sleep(1);}}
    static async Task Until(Func<bool> done)
    {var clock=Stopwatch.StartNew();while(!done()){if(clock.Elapsed>TimeSpan.FromSeconds(15))throw new TimeoutException("floor pointer did not reach the real WPF consumer");await Task.Delay(5);}}
}
