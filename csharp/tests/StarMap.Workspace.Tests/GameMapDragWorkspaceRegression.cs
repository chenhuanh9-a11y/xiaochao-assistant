using System.Diagnostics;
using System.IO;
using System.Reflection;
using System.Text.Json;
using System.Text.Json.Nodes;
using System.Windows;
using System.Windows.Controls;
using System.Windows.Media;
using System.Windows.Media.Imaging;
using System.Windows.Threading;
using StarMap.App;
using StarMap.App.Platform;
using StarMap.Core.Display;
using StarMap.Core.Input;
using StarMap.Core.Settings;
using StarMap.Runtime;

internal static class GameMapDragWorkspaceRegression
{
    internal static async Task Run(string resources,string replay,string output,Action<bool,string> check)
    {
        var index=JsonNode.Parse(await File.ReadAllBytesAsync(Path.Combine(replay,"all-samples.json")))!["samples"]!.AsArray()
            .ToDictionary(row=>row!["id"]!.GetValue<string>(),row=>row!.AsObject());
        foreach(var item in new[]{("s0","nightmare","batch01-event-000006",(string?)null),
            ("s0","difficult","batch02-event-000147",null),("s1","nightmare","batch02-event-000315","107012"),
            ("s1","difficult","batch02-event-000307","106008")})
        {
            string name=item.Item1+"-"+item.Item2;
            var settings=new SettingsDocument(new JsonObject {["gameSeason"]=item.Item1,["gameMode"]=item.Item2,["routeProfileId"]="shared-structure"});
            var capture=new RecordedCapture(index);capture.Select(item.Item3,false);
            var page=new RuntimePage(capture,_=>{});var shell=new WorkspaceWindow(Path.Combine(output,"drag-shell-"+name),null);
            await Field<RuntimePage>(shell,"runtimePage").CloseAsync();
            typeof(WorkspaceWindow).GetField("runtimePage",BindingFlags.Instance|BindingFlags.NonPublic)!.SetValue(shell,page);
            Field<RuntimeOverlayHost>(shell,"runtimeOverlay").Dispose();
            var consume=typeof(WorkspaceWindow).GetMethod("ConsumeInput",BindingFlags.Instance|BindingFlags.NonPublic)!;
            var bindings=GlobalInputBindings.FromSettings(settings);var source=new FixtureInputSource(bindings){NowSeconds=Now()};
            var deliveries=new List<WpfGlobalInputDelivery>();long sequence=0;
            await using var host=new WpfGlobalInputController(Dispatcher.CurrentDispatcher,bindings,
                ()=>new(new(42,true,42,false,true,true),BindingGeneration:1,PointerTarget:new(42,"runtime-slot",0,page.GamePointerContextSlot),AllowPointerIntentPress:true),
                delivery=>{deliveries.Add(delivery);consume.Invoke(shell,[delivery]);},source);
            RecordedCapture.Hold? hold=null;
            try
            {
                check(await page.Configure(resources,settings),name+" drag real workspace config");
                if(item.Item4 is { } map)
                {page.Dispatch("prepare_manual_alignment");await page.DrainAsync();page.Dispatch("confirm_route",new {mapId=map});await page.DrainAsync();}
                else{page.Dispatch("start_round");await page.DrainAsync();page.HandleShortcut("map");await page.DrainAsync();}
                await host.StartAsync();
                var runtime=Field<LiveRuntime>(page,"runtime");
                check(runtime.TryReadRender(out var original),name+" original real-resource render");
                var state=page.State!;var context=page.GamePointerContextSlot!.Read()!;
                var ready=await Task.Run(()=>WpfRuntimeSceneMaterial.Prepare(original!));
                using var surface=new WpfAlignedOverlaySurface();var ticket=surface.Reserve(new object());
                var visual=new WpfAlignedOverlayVisual();visual.Apply(ready.Frame(ticket,original!,.7),96,96);
                var images=((Canvas)visual.Children[0]).Children.OfType<Image>().Where(i=>i.Source is not null).ToArray();
                var sources=images.Select(i=>i.Source).ToArray();var transforms=images.Select(i=>i.RenderTransform).ToArray();
                var matrices=transforms.Cast<MatrixTransform>().Select(t=>t.Matrix).ToArray();
                var geometry=((GameFloorClickContext)context.DownIntentSnapshot!).Geometry;
                int x=geometry.Left+geometry.Width/2,y=geometry.Top+geometry.Height/2;
                hold=capture.HoldNext();page.Dispatch(state.ManualAlignmentSession?"observe_manual_floor":"refresh_map");
                await hold.Started.Task.WaitAsync(TimeSpan.FromSeconds(10));
                await Pointer("down",x,y);
                check(runtime.TryReadRender(out var held) && ReferenceEquals(held,original),name+" actual down leaves pixels and pose unchanged");
                await Pointer("move",x+13,y-7);hold.Release.TrySetResult();await page.DrainAsync();
                check(runtime.TryReadRender(out var moved) && moved!.IsTransient && moved.SharesMaterial(original!)
                    && page.State!.NativePose==state.NativePose && page.GamePointerContextSlot.Read()==context,
                    name+" real controller/Workspace/Page/Session cancellation preserves presentation-only drag");
                check(page.SessionView?.WorkerActive==false && runtime.ReadMaterialToRemember() is null,
                    name+" old capture drains without learning the moved display");
                visual.Apply(ready.Frame(ticket,moved!,.7),96,96);
                check(images.Select(i=>i.Source).SequenceEqual(sources) && images.Select(i=>i.RenderTransform).SequenceEqual(transforms)
                    && images.Select((image,i)=>Math.Abs(((MatrixTransform)image.RenderTransform).Matrix.OffsetX-matrices[i].OffsetX-13)<1e-7
                        && Math.Abs(((MatrixTransform)image.RenderTransform).Matrix.OffsetY-matrices[i].OffsetY+7)<1e-7).All(v=>v),
                    name+" every visible/hidden Image, bitmap and transform reused with exactly one root translation");
                var basis=original!.Material.Bridge.DisplayToClient!.Value;
                var expected=new DisplayAffine(basis.M00,basis.M01,basis.M02+13,basis.M10,basis.M11,basis.M12-7);
                var currentBitmap=ready.Layers.Single(layer=>layer.Floor==original.Scene!.SelectedFloor).Image;
                foreach(int dpi in new[]{96,120,144,192})
                {
                    var actual=ready.Frame(ticket,moved!,.7);visual.Apply(actual,dpi,dpi);
                    var independent=new WpfAlignedOverlayVisual();
                    independent.Apply(WpfAlignedOverlaySurface.PrepareAffine(ticket,currentBitmap,WpfRuntimeSceneMaterial.Matrix(expected),actual.Client,.7),dpi,dpi);
                    byte[] shown=Render(visual,geometry.Width,geometry.Height,dpi),wanted=Render(independent,geometry.Width,geometry.Height,dpi);
                    check(shown.AsSpan().SequenceEqual(wanted) && Enumerable.Range(0,shown.Length/4).Any(p=>shown[p*4+3]!=0),
                        name+" actual translated scene pixels equal independent unmodified single-floor bridge plus physical delta at DPI "+dpi);
                }
                await Pointer("up",x+15,y-6);await page.DrainAsync();
                check(runtime.TryReadRender(out var released) && released!.PresentationOffsetX==15 && released.PresentationOffsetY== -6
                    && page.State!.NativePose==state.NativePose && page.SessionView?.LastReceipt is {Action:"probe_map_after_drag",Success:true},
                    name+" actual release includes final delta and routes only to post-drag observation");
                runtime.InvalidateCapture("map-closed");page.SuspendInput("map-closed");
                check(!runtime.TryReadRender(out _),name+" hard close withdraws translated authorization");
                await File.WriteAllTextAsync(Path.Combine(output,"drag-workspace-"+name+".json"),JsonSerializer.Serialize(new {
                    capture.Calls,deliveries=deliveries.Count,offset=new[]{15,-6},scope="Actual hidden Workspace/Page/Session/controller bounded channel and WPF software pixels at four DPIs. Only OS capture/input/foreground replaced. C1 user choice; 106008 simulated choice. No native/game acceptance."
                },new JsonSerializerOptions{WriteIndented=true}));
                async Task Pointer(string phase,int px,int py)
                {
                    source.NowSeconds=Now();var pointer=new GlobalInputPointerEvent(phase,px,py,source.NowSeconds,42,42,source.Generation,1,
                        ContextId:context.ContextId,Sequence:++sequence,GestureId:1,PressContextSnapshot:context);
                    source.Post(new(GlobalInputNotificationKind.Pointer,source.Generation,Pointer:pointer));
                    var clock=Stopwatch.StartNew();while(!deliveries.Any(d=>ReferenceEquals(d.Notification.Pointer,pointer)))
                    {if(clock.Elapsed>TimeSpan.FromSeconds(10))throw new TimeoutException("drag pointer delivery");await Task.Delay(5);}
                    check(deliveries.Last(d=>ReferenceEquals(d.Notification.Pointer,pointer)).Pointer==pointer,name+" strict "+phase+" delivered");
                }
            }
            finally{hold?.Release.TrySetResult();await host.StopAsync();await page.CloseAsync();shell.Close();}
        }
    }
    static double Now()=>Stopwatch.GetTimestamp()/(double)Stopwatch.Frequency;
    static T Field<T>(object owner,string name)=>(T)owner.GetType().GetField(name,BindingFlags.Instance|BindingFlags.NonPublic)!.GetValue(owner)!;
    static byte[] Render(FrameworkElement visual,int width,int height,int dpi)
    {
        double w=width*96d/dpi,h=height*96d/dpi;visual.Measure(new(w,h));visual.Arrange(new Rect(0,0,w,h));visual.UpdateLayout();
        var bitmap=new RenderTargetBitmap(width,height,dpi,dpi,PixelFormats.Pbgra32);bitmap.Render(visual);
        var result=new byte[checked(width*height*4)];bitmap.CopyPixels(result,width*4,0);return result;
    }
}
