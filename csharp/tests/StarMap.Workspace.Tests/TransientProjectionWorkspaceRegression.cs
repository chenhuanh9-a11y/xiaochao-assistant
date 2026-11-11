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
using StarMap.Core.Session;
using StarMap.Runtime;

internal static class TransientProjectionWorkspaceRegression
{
    internal static async Task Run(string resources,string replay,string output,Action<bool,string> check)
    {
        var index=JsonNode.Parse(await File.ReadAllBytesAsync(Path.Combine(replay,"all-samples.json")))!["samples"]!.AsArray()
            .ToDictionary(row=>row!["id"]!.GetValue<string>(),row=>row!.AsObject());
        string root=Path.GetDirectoryName(replay)!;
        var original=JsonNode.Parse(await File.ReadAllBytesAsync(Path.Combine(root,"phase26-original-recorded-projection-02","case.json")))!;
        var originalClick=JsonNode.Parse(await File.ReadAllBytesAsync(Path.Combine(root,"phase26-original-game-click-projection-01","case.json")))!;
        var capture=new RecordedCapture(index);capture.Select("batch01-event-000006",false);
        var settings=new SettingsDocument(new JsonObject {["gameSeason"]="s0",["gameMode"]="nightmare",["routeProfileId"]="suju"});
        var statuses=new List<string>();var page=new RuntimePage(capture,statuses.Add);var shell=new WorkspaceWindow(Path.Combine(output,"projection-shell"),null);
        await Field<RuntimePage>(shell,"runtimePage").CloseAsync();
        typeof(WorkspaceWindow).GetField("runtimePage",BindingFlags.Instance|BindingFlags.NonPublic)!.SetValue(shell,page);
        Field<RuntimeOverlayHost>(shell,"runtimeOverlay").Dispose();
        var consume=typeof(WorkspaceWindow).GetMethod("ConsumeInput",BindingFlags.Instance|BindingFlags.NonPublic)!;
        var bindings=GlobalInputBindings.FromSettings(settings);var inputSource=new FixtureInputSource(bindings){NowSeconds=Now()};
        var deliveries=new List<WpfGlobalInputDelivery>();long sequence=0;GlobalInputPointerContextSnapshot context=null!;
        await using var input=new WpfGlobalInputController(Dispatcher.CurrentDispatcher,bindings,
            ()=>new(new(42,true,42,false,true,true),BindingGeneration:1,PointerTarget:new(42,"projection-slot",0,page.GamePointerContextSlot),AllowPointerIntentPress:true),
            delivery=>{deliveries.Add(delivery);consume.Invoke(shell,[delivery]);},inputSource);
        using var materialCapture=new WindowsRuntimeCapturePort();bool allowProjectionAck=false;var messages=new List<string>();
        try
        {
            check(await page.Configure(resources,settings),"projection workspace binds real resources");
            var runtime=Field<LiveRuntime>(page,"runtime");SyntheticProjectionCoordinates.Install(runtime,resources,"nightmare");
            runtime.SetIdvb0059Enabled(true);page.Dispatch("start_round");await page.DrainAsync();page.HandleShortcut("map");await page.DrainAsync();
            check(page.State is {IdentityLocked:false,RecognitionPath:"structure-index"} && page.ReadRender() is not null,
                "projection-setup-first-exercises-the-default-conditional-index-reference");
            // Cross-floor projection uses an explicitly chosen reference, as
            // opposed to giving an unresolved index candidate persistent pose authority.
            page.Dispatch("confirm_route",new {mapId="nightmare-04"});await page.DrainAsync();
            check(page.State is {IdentityLocked:true,IdentitySource:"manual",NativeGeometryVerified:true},
                "projection-reference-is-explicitly-chosen-through-real-Session-before-shared-root-display-tests");
            page.SetAlignedVisibility(false);
            using var materialHost=new RuntimeOverlayHost(Dispatcher.CurrentDispatcher,page.ReadRender,materialCapture,messages.Add,
                visible:()=>page.AlignedVisible,readPendingMaterial:()=>allowProjectionAck?page.ReadPendingGroupMaterial():null,
                acceptMaterialReady:page.AcceptGroupMaterialReady,rejectMaterial:page.RejectGroupMaterial,
                readMaterialToRemember:page.ReadMaterialToRemember,rememberMaterialReady:page.RememberMaterialReady);
            var clock=Stopwatch.StartNew();
            while(Field<WpfRuntimeSceneMaterial?>(materialHost,"preparedMaterial") is null || runtime.ReadMaterialToRemember() is not null)
            {if(clock.Elapsed>TimeSpan.FromSeconds(15))throw new TimeoutException("projection source material warmup");await Task.Delay(5);}
            await page.DrainAsync();var ready=Field<WpfRuntimeSceneMaterial>(materialHost,"preparedMaterial");var source=page.ReadRender()!;
            check(source is not null && ready.Layers.Count>1,"projection source resident floor bitmaps are prepared by the production host");
            using var surface=new WpfAlignedOverlaySurface();var ticket=surface.Reserve(new object());var visual=new WpfAlignedOverlayVisual();
            visual.Apply(ready.Frame(ticket,source!,.7),96,96);
            var images=((Canvas)visual.Children[0]).Children.OfType<Image>().Where(i=>i.Source is not null).ToArray();
            var bitmaps=images.Select(i=>i.Source).ToArray();var transforms=images.Select(i=>i.RenderTransform).ToArray();
            context=page.GamePointerContextSlot!.Read()!;var g=((GameFloorClickContext)context.DownIntentSnapshot!).Geometry;
            await input.StartAsync();
            int x=g.Left+g.Width/2,y=g.Top+g.Height/2;
            await Pointer("down",x,y,1);await Pointer("move",x+13,y-7,1);await Pointer("up",x+15,y-6,1);await page.DrainAsync();
            var group=typeof(LiveRuntime).GetField("_geometryGroup",BindingFlags.Instance|BindingFlags.NonPublic)!.GetValue(runtime);
            using var pixels=OpenCvSharp.Cv2.ImDecode(await File.ReadAllBytesAsync(index["batch01-event-000006"]["image"]!.GetValue<string>()),OpenCvSharp.ImreadModes.Color);
            var read=StarMap.Vision.FloorObservation.Observe(pixels,"s0","nightmare");
            var rect=read.FloorRead["controlRects"]!.AsArray().First(row=>row!["floor"]!.GetValue<int>()==2)!["rect"]!.AsArray();
            int fx=(int)Math.Round(g.Left+(rect[0]!.GetValue<double>()+rect[2]!.GetValue<double>())*.5*g.Width);
            int fy=(int)Math.Round(g.Top+(rect[1]!.GetValue<double>()+rect[3]!.GetValue<double>())*.5*g.Height);
            capture.Select("batch02-event-000018",false);await Pointer("down",fx,fy,2);await Pointer("up",fx,fy,2);await page.DrainAsync();
            check(page.State!.Context.FloorId==originalClick["floor"]!.GetValue<string>() && page.ReadPendingGroupMaterial() is null
                && page.ReadRender() is null && ReferenceEquals(group,typeof(LiveRuntime).GetField("_geometryGroup",BindingFlags.Instance|BindingFlags.NonPublic)!.GetValue(runtime)),
                "projection physical control path matches original observed intent-only result and preserves its trusted group");
            var floors=Field<ComboBox>(page,"floor");floors.SelectedValue="2";
            Field<Button>(page,"applyFloor").RaiseEvent(new RoutedEventArgs(Button.ClickEvent));await page.DrainAsync();
            var pending=page.ReadPendingGroupMaterial();var observed=page.State!;
            check(pending is {ProjectionOnly:true,PresentationOffsetX:15,PresentationOffsetY:-6} && observed.NativePose is null
                && observed.Context.FrameId==pending.Stamp.FrameId && page.ReadRender() is null,
                "projection actual floor button and Session publish target observation before the delayed material ACK");
            allowProjectionAck=true;clock.Restart();
            while(page.ReadRender() is not {ProjectionOnly:true})
            {if(clock.Elapsed>TimeSpan.FromSeconds(15))throw new TimeoutException("projection consumer ACK");await Task.Delay(5);}
            await page.DrainAsync();var projected=page.ReadRender()!;
            check(ReferenceEquals(projected,pending) && page.State!.NativePose is null && runtime.ReadMaterialToRemember() is null
                && ReferenceEquals(ready,Field<WpfRuntimeSceneMaterial>(materialHost,"preparedMaterial")),
                "projection production WPF readiness installs a display-only grant using existing frozen floor bitmaps");
            check(DisplayAffine.TryRead(JsonSerializer.SerializeToElement(original["displayMatrix"]),out var expected),"projection independent original matrix is finite");
            var selected=ready.Layers.Single(layer=>layer.Floor=="2");
            foreach(int dpi in new[]{96,120,144,192})
            {
                var actualFrame=ready.Frame(ticket,projected,.7);visual.Apply(actualFrame,dpi,dpi);var reference=new WpfAlignedOverlayVisual();
                reference.Apply(WpfAlignedOverlaySurface.PrepareAffine(ticket,selected.Image,WpfRuntimeSceneMaterial.Matrix(expected),actualFrame.Client,.7),dpi,dpi);
                byte[] actual=Render(visual,g.Width,g.Height,dpi),wanted=Render(reference,g.Width,g.Height,dpi);
                check(actual.AsSpan().SequenceEqual(wanted) && Enumerable.Range(0,actual.Length/4).Any(i=>actual[i*4+3]!=0),
                    "projection common-scene pixels equal original independent target matrix with one pan offset at DPI "+dpi);
            }
            check(images.Select(i=>i.Source).SequenceEqual(bitmaps) && images.Select(i=>i.RenderTransform).SequenceEqual(transforms)
                && images.Single(i=>ReferenceEquals(i.Source,selected.Image)).Visibility==Visibility.Visible
                && images.Where(i=>!ReferenceEquals(i.Source,selected.Image)).All(i=>i.Visibility!=Visibility.Visible),
                "projection keeps all Image/BitmapSource/transform instances resident and changes only target visibility and common matrix");
            int authorCalls=capture.Calls;var authorBefore=page.State!.Context;
            check(await page.Configure(resources,new SettingsDocument(new JsonObject {["gameSeason"]="s0",["gameMode"]="nightmare",["routeProfileId"]="shared-structure"})),
                "projection saved author preference passes through real RuntimePage configuration and Session");
            await page.DrainAsync();var author=page.ReadRender();
            check(author is {ProjectionOnly:true,PresentationOffsetX:15,PresentationOffsetY:-6} && author.MaterialToken!=projected.MaterialToken
                && capture.Calls==authorCalls && page.State!.Context.FrameId==authorBefore.FrameId && page.State.Context.FloorId=="2"
                && page.State.Context.ProfileId=="shared-structure" && page.State.NativePose is null
                && !page.State.Display.CanonicalVerified && runtime.ReadMaterialToRemember() is null,
                "projection author configuration replaces display token without capture, identity/geometry promotion or pan duplication");
            clock.Restart();
            while(Field<WpfRuntimeSceneMaterial?>(materialHost,"preparedMaterial") is not { } next || !next.Matches(author!))
            {if(clock.Elapsed>TimeSpan.FromSeconds(15))throw new TimeoutException("projection author material consumer");await Task.Delay(5);}
            var authorReady=Field<WpfRuntimeSceneMaterial>(materialHost,"preparedMaterial");
            var authorOriginal=JsonNode.Parse(await File.ReadAllBytesAsync(Path.Combine(root,"phase27-original-projection-author-nightmare-03","cases.json")))!["cases"]!.AsArray()
                .First(row=>row!["scenario"]!.GetValue<string>()=="same-profile")!["render"]!;
            check(DisplayAffine.TryRead(JsonSerializer.SerializeToElement(authorOriginal["matrix"]),out var authorExpected),
                "projection shared-layer author target uses an independent original current-frame matrix");
            var authorLayer=authorReady.Layers.Single(layer=>layer.Floor=="2");
            foreach(int dpi in new[]{96,120,144,192})
            {
                var frame=authorReady.Frame(ticket,author!,.7);visual.Apply(frame,dpi,dpi);var reference=new WpfAlignedOverlayVisual();
                reference.Apply(WpfAlignedOverlaySurface.PrepareAffine(ticket,authorLayer.Image,WpfRuntimeSceneMaterial.Matrix(authorExpected),frame.Client,.7),dpi,dpi);
                byte[] actual=Render(visual,g.Width,g.Height,dpi),wanted=Render(reference,g.Width,g.Height,dpi);
                check(actual.AsSpan().SequenceEqual(wanted) && Enumerable.Range(0,actual.Length/4).Any(i=>actual[i*4+3]!=0),
                    "projection author consumer software pixels preserve original geometry and one offset at DPI "+dpi);
            }
            int authorCaptureDelta=capture.Calls-authorCalls;
            var session=Field<SessionService>(page,"session");var beforeCancel=runtime.ReadState().Context;
            var reappliedSettings=new SettingsDocument(new JsonObject {["gameSeason"]="s0",["gameMode"]="nightmare",["routeProfileId"]="suju"});
            Field<DispatcherTimer>(page,"observationTimer").Stop();
            bool cancelledApplied;
            using(var held=new ProjectionAuthorMaterialHold(runtime))
            {
                var applying=page.Configure(resources,reappliedSettings);clock.Restart();
                while(runtime.ReadState().Context.ProfileId!="suju" || Field<object?>(session,"_active") is null)
                {if(clock.Elapsed>TimeSpan.FromSeconds(10))throw new TimeoutException("page author cancellation admission");await Task.Delay(1);}
                var active=Field<object>(session,"_active");
                check(!applying.IsCompleted && held.Held && session.Read().PendingAction=="set_context",
                    "projection page author worker is held at the actual material cache before controlled host cancellation");
                ((CancellationTokenSource)active.GetType().GetProperty("Cancellation")!.GetValue(active)!).Cancel();
                held.Release();cancelledApplied=await applying;await page.DrainAsync();
            }
            Field<DispatcherTimer>(page,"observationTimer").Stop();session.RefreshRuntime();
            check(page.ReadRender() is null && runtime.ReadState().MapOpen && runtime.ReadState().Context.FrameId==beforeCancel.FrameId
                && runtime.ReadState().Context.ProfileId=="suju" && capture.Calls==authorCalls,
                "projection cancelled page work retains observation and saved selection while public pixels are hidden");
            var retrySessionBefore=session.Read();
            bool reapplied=await page.Configure(resources,reappliedSettings);await page.DrainAsync();
            Field<DispatcherTimer>(page,"observationTimer").Stop();var retryRender=page.ReadRender();
            await File.WriteAllTextAsync(Path.Combine(output,"projection-author-reapply-ui.json"),JsonSerializer.Serialize(new {cancelledApplied,reapplied,
                hasRender=retryRender is not null,state=runtime.ReadState(),retrySessionBefore,retrySessionAfter=session.Read(),statuses,captureDelta=capture.Calls-authorCalls,
                scope="The actual Session work CancellationTokenSource is cancelled while the real material cache is held; retry uses unmodified RuntimePage.Configure/Session. Background observation timer is stopped to isolate display operations; no Runtime state or solver is replaced."},new JsonSerializerOptions{WriteIndented=true}));
            check(reapplied && retryRender is {ProjectionOnly:true} && retryRender.MaterialToken!=author!.MaterialToken
                && capture.Calls==authorCalls && runtime.ReadState().Context.FrameId==beforeCancel.FrameId
                && runtime.ReadState().NativePose is null && runtime.ReadMaterialToRemember() is null,
                "projection same saved settings reapplication reaches Runtime display retry through the actual Page/Session");
            check(!cancelledApplied,"projection cancelled settings work is not reported applied merely because selection was saved");
            var failedSettings=new SettingsDocument(new JsonObject {["gameSeason"]="s0",["gameMode"]="nightmare",["routeProfileId"]="shared-structure"});
            bool failedApplied;
            using(var badDeclaration=new ProjectionAuthorCoordinateFault(runtime,"nightmare",runtime.ReadState().Context.MapId!))
                failedApplied=await page.Configure(resources,failedSettings);
            await page.DrainAsync();Field<DispatcherTimer>(page,"observationTimer").Stop();
            check(!failedApplied && page.ReadRender() is null && runtime.ReadState().MapOpen
                && runtime.ReadState().Context.FrameId==beforeCancel.FrameId && capture.Calls==authorCalls,
                "projection real display-declaration rejection preserves observation and reports incomplete page application");
            check(await page.Configure(resources,failedSettings),"projection restored metadata allows saved static preference application");
            await page.DrainAsync();Field<DispatcherTimer>(page,"observationTimer").Stop();
            check(page.ReadRender() is null && runtime.ReadState().NativePose is null && capture.Calls==authorCalls,
                "projection page reapplication after true material failure never revives its retired accepted source");
            Field<Button>(page,"acquire").RaiseEvent(new RoutedEventArgs(Button.ClickEvent));await page.DrainAsync();clock.Restart();
            while(page.ReadRender() is not {ProjectionOnly:true})
            {if(clock.Elapsed>TimeSpan.FromSeconds(15))throw new TimeoutException("projection explicit page recovery");await Task.Delay(5);}
            check(capture.Calls==authorCalls+1 && page.State!.Context.FrameId!=beforeCancel.FrameId && page.State.NativePose is null
                && runtime.ReadMaterialToRemember() is null,"projection actual read button obtains the fresh observation required after material failure");
            var nativeSurface=Field<WpfAlignedOverlaySurface>(materialHost,"surface");
            check(nativeSurface.NativeHandle==0 && !nativeSurface.IsVisible && materialCapture.Binding is null && messages.Count==0,
                "projection recorded fixture and material ACK never authorize a native window or Live game display");
            await File.WriteAllTextAsync(Path.Combine(output,"projection-workspace.json"),JsonSerializer.Serialize(new {observed=observed.Context,
                after=page.State!.Context,authorCaptureDelta,recoveryCaptureDelta=capture.Calls-authorCalls,deliveries=deliveries.Count,scope="Default unresolved index first, then explicit manual reference for actual UI floor/read buttons, author configuration, pointer/controller/Workspace/Page/Session/material-ready host and software pixels at four DPIs. Relation data, host cancellation and display declaration fault are scoped fixtures. Authors fall back to shared structure. No unique identity truth, native relation, author pixels, game/physical input/GPU-present or latency acceptance."},new JsonSerializerOptions{WriteIndented=true}));
            async Task Pointer(string phase,int px,int py,long gesture)
            {
                inputSource.NowSeconds=Now();var p=new GlobalInputPointerEvent(phase,px,py,inputSource.NowSeconds,42,42,inputSource.Generation,1,
                    ContextId:context.ContextId,Sequence:++sequence,GestureId:gesture,PressContextSnapshot:context);
                inputSource.Post(new(GlobalInputNotificationKind.Pointer,inputSource.Generation,Pointer:p));var timer=Stopwatch.StartNew();
                while(!deliveries.Any(d=>ReferenceEquals(d.Notification.Pointer,p)))
                {if(timer.Elapsed>TimeSpan.FromSeconds(10))throw new TimeoutException("projection pointer delivery");await Task.Delay(5);}
            }
        }
        finally{await input.StopAsync();await page.CloseAsync();shell.Close();}
    }
    static double Now()=>Stopwatch.GetTimestamp()/(double)Stopwatch.Frequency;
    static T Field<T>(object owner,string name)=>(T)owner.GetType().GetField(name,BindingFlags.Instance|BindingFlags.NonPublic)!.GetValue(owner)!;
    static byte[] Render(FrameworkElement visual,int width,int height,int dpi)
    {
        double w=width*96d/dpi,h=height*96d/dpi;visual.Measure(new(w,h));visual.Arrange(new Rect(0,0,w,h));visual.UpdateLayout();
        var bitmap=new RenderTargetBitmap(width,height,dpi,dpi,PixelFormats.Pbgra32);bitmap.Render(visual);
        var bytes=new byte[checked(width*height*4)];bitmap.CopyPixels(bytes,width*4,0);return bytes;
    }
}
