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
            var deliveries=new List<WpfGlobalInputDelivery>();long sequence=0,gesture=1;
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
                object? groupEvidence=null;double[] finalOffset=[15,-6];
                if(item.Item1=="s0" && item.Item2=="nightmare")
                {
                    string sourceFrame=page.State!.Context.FrameId!;int calls=capture.Calls;
                    page.RefreshGroupControls();var members=Field<ComboBox>(page,"groupMember");var choose=Field<Button>(page,"chooseGroupMember");
                    bool allowCurrentAck=false;string currentMap=page.State.Context.MapId!;
                    page.SetAlignedVisibility(false);using var currentMaterialCapture=new WindowsRuntimeCapturePort();
                    var currentMessages=new List<string>();
                    using(var currentHost=new RuntimeOverlayHost(Dispatcher.CurrentDispatcher,page.ReadRender,currentMaterialCapture,currentMessages.Add,
                        visible:()=>page.AlignedVisible,readPendingMaterial:()=>allowCurrentAck?page.ReadPendingGroupMaterial():null,
                        acceptMaterialReady:page.AcceptGroupMaterialReady,rejectMaterial:page.RejectGroupMaterial,
                        readMaterialToRemember:page.ReadMaterialToRemember,rememberMaterialReady:page.RememberMaterialReady))
                    {
                        var warmClock=Stopwatch.StartNew();
                        while(Field<WpfRuntimeSceneMaterial?>(currentHost,"preparedMaterial") is null)
                        {if(warmClock.Elapsed>TimeSpan.FromSeconds(15))throw new TimeoutException("current source material warmup");await Task.Delay(5);}
                        var frozenSource=Field<WpfRuntimeSceneMaterial>(currentHost,"preparedMaterial");
                        members.SelectedValue=currentMap;choose.RaiseEvent(new RoutedEventArgs(Button.ClickEvent));await page.DrainAsync();
                        var sameMember=page.ReadPendingGroupMaterial();
                        check(sameMember is not null && sameMember.MaterialToken!=released!.MaterialToken && capture.Calls==calls
                            && ReferenceEquals(sameMember.Material,released.Material) && ReferenceEquals(page.ReadRender(),released)
                            && page.State!.GroupChoice.HeldMapId is null && page.GamePointerContextSlot.Read()==context,
                            "current-group actual UI/Session stages existing translated material without a new capture or identity commit");
                        allowCurrentAck=true;warmClock.Restart();
                        while(page.State?.GroupChoice.HeldMapId!=currentMap)
                        {if(warmClock.Elapsed>TimeSpan.FromSeconds(15))throw new TimeoutException("current material ACK");await Task.Delay(5);}
                        await page.DrainAsync();released=page.ReadRender();
                        check(ReferenceEquals(released,sameMember) && released is {IsTransient:true,PresentationOffsetX:15,PresentationOffsetY:-6}
                            && capture.Calls==calls && page.State!.Context.FrameId==sourceFrame && page.State.NativePose==state.NativePose
                            && page.GamePointerContextSlot.Read()==context && ReferenceEquals(frozenSource,Field<WpfRuntimeSceneMaterial>(currentHost,"preparedMaterial")),
                            "current-group production material consumer reuses frozen bitmaps and retains the source observation and drag offset");
                        visual.Apply(ready.Frame(ticket,released!,.7),96,96);
                        check(images.Select(i=>i.Source).SequenceEqual(sources) && images.Select(i=>i.RenderTransform).SequenceEqual(transforms)
                            && images.Select((image,i)=>Math.Abs(((MatrixTransform)image.RenderTransform).Matrix.OffsetX-matrices[i].OffsetX-15)<1e-7
                                && Math.Abs(((MatrixTransform)image.RenderTransform).Matrix.OffsetY-matrices[i].OffsetY+6)<1e-7).All(v=>v)
                            && runtime.ReadMaterialToRemember() is null && currentMessages.Count==0,
                            "current-group real Image/BitmapSource/transforms remain resident without double translation or transient learning");
                        var currentSurface=Field<WpfAlignedOverlaySurface>(currentHost,"surface");
                        check(currentSurface.NativeHandle==0 && !currentSurface.IsVisible && currentMaterialCapture.Binding is null,
                            "current-group recorded material ACK remains separate from native display permission");
                    }
                    page.RefreshGroupControls();
                    check(choose.IsEnabled && members.Items.Cast<RuntimeGroupMember>().Any(member=>member.MapId=="nightmare-12"),
                        "drag-group real UI offers only the registered target");
                    members.SelectedValue="nightmare-12";hold=capture.HoldNext();
                    choose.RaiseEvent(new RoutedEventArgs(Button.ClickEvent));await hold.Started.Task.WaitAsync(TimeSpan.FromSeconds(10));
                    check(page.State!.Context.FrameId==sourceFrame && page.GamePointerContextSlot.Read()==context
                        && ReferenceEquals(page.ReadRender(),released),"drag-group actual button captures fresh target while source frame/pixels stay published");
                    hold.Release.TrySetResult();await page.DrainAsync();var target=page.ReadPendingGroupMaterial();
                    check(target is {MapId:"nightmare-12"} && capture.Calls==calls+1 && target.Stamp.FrameId!=sourceFrame
                        && target.Generation==released!.Generation+1 && ReferenceEquals(page.ReadRender(),released)
                        && page.State!.Context.FrameId==sourceFrame && page.GamePointerContextSlot.Read()==context,
                        "drag-group Session collects target without publishing staged observation");
                    page.SetAlignedVisibility(false);using var materialCapture=new WindowsRuntimeCapturePort();
                    var messages=new List<string>();
                    using(var materialHost=new RuntimeOverlayHost(Dispatcher.CurrentDispatcher,page.ReadRender,materialCapture,messages.Add,
                        visible:()=>page.AlignedVisible,readPendingMaterial:page.ReadPendingGroupMaterial,acceptMaterialReady:page.AcceptGroupMaterialReady,
                        rejectMaterial:page.RejectGroupMaterial,readMaterialToRemember:page.ReadMaterialToRemember,rememberMaterialReady:page.RememberMaterialReady))
                    {
                        var clock=Stopwatch.StartNew();while(page.State?.GroupChoice.HeldMapId!="nightmare-12")
                        {if(clock.Elapsed>TimeSpan.FromSeconds(15))throw new TimeoutException("fresh group material ACK");await Task.Delay(5);}
                        await page.DrainAsync();
                        check(ReferenceEquals(page.ReadRender(),target) && page.State!.Context.FrameId==target!.Stamp.FrameId
                            && page.State.NativePose!.FrameId==target.Stamp.FrameId && !target.IsTransient && target.PresentationOffsetX==0
                            && page.GamePointerContextSlot.Read()!=context,"drag-group actual WPF readiness callback atomically publishes fresh target frame/context without old offset");
                        var actualSurface=Field<WpfAlignedOverlaySurface>(materialHost,"surface");
                        check(actualSurface.NativeHandle==0 && !actualSurface.IsVisible && materialCapture.Binding is null && messages.Count==0,
                            "drag-group hidden recorded ACK never grants a native window or Live display");
                        context=page.GamePointerContextSlot.Read()!;gesture=2;
                        await Pointer("down",x,y);await Pointer("up",x+2,y+3);await page.DrainAsync();
                        check(page.ReadRender() is {PresentationOffsetX:2,PresentationOffsetY:3} && page.State!.Context.MapId=="nightmare-12",
                            "drag-group next physical pointer lane starts from new target with no source-offset carryover");
                        finalOffset=[2,3];groupEvidence=new {sourceFrame,targetFrame=target!.Stamp.FrameId,page.State.GroupChoice};
                    }
                }
                runtime.InvalidateCapture("map-closed");page.SuspendInput("map-closed");
                check(!runtime.TryReadRender(out _),name+" hard close withdraws translated authorization");
                await File.WriteAllTextAsync(Path.Combine(output,"drag-workspace-"+name+".json"),JsonSerializer.Serialize(new {
                    capture.Calls,deliveries=deliveries.Count,offset=finalOffset,groupEvidence,scope="Actual Workspace/Page/Session/controller bounded channel and WPF software pixels at four DPIs; registered-group UI button and production hidden material-ready host/ACK. Only OS capture/input/foreground replaced. C1 user choice; 106008 simulated choice. No native/game acceptance."
                },new JsonSerializerOptions{WriteIndented=true}));
                async Task Pointer(string phase,int px,int py)
                {
                    source.NowSeconds=Now();var pointer=new GlobalInputPointerEvent(phase,px,py,source.NowSeconds,42,42,source.Generation,1,
                        ContextId:context.ContextId,Sequence:++sequence,GestureId:gesture,PressContextSnapshot:context);
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
