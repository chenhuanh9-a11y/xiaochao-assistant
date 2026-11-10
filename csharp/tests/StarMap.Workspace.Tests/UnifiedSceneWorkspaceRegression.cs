using System.IO;
using System.Diagnostics;
using System.Text.Json;
using System.Text.Json.Nodes;
using System.Windows;
using System.Windows.Controls;
using System.Windows.Media;
using System.Windows.Media.Imaging;
using StarMap.App.Platform;
using StarMap.Core.Display;
using StarMap.Core.Session;
using StarMap.Core.Resources;
using StarMap.Runtime;

internal static class UnifiedSceneWorkspaceRegression
{
    internal static async Task Run(string resources,string replay,string output,Action<bool,string> check)
    {
        var samples=JsonNode.Parse(await File.ReadAllBytesAsync(Path.Combine(replay,"all-samples.json")))!["samples"]!.AsArray()
            .ToDictionary(r=>r!["id"]!.GetValue<string>(),r=>r!.AsObject());
        var evidence=new List<object>();
        await PartialScene(resources,samples,output,check);
        var catalog=ResourceCatalog.Load(resources);
        var spaces=new Dictionary<(string Season,string Mode,string Map,string Floor),CommonFloorSpace>();
        int legacy=0;
        foreach(var entry in catalog.Entries.Where(e=>e.StructureStatus==ResourceBindingStatus.Ready))
        {
            var reference=catalog.LoadSharedStructure(entry).Value;
            check(reference is not null,entry.StableKey+" static scene reads the actual indexed NPZ");
            var space=CommonFloorSpace.Read(entry,reference!,out string reason);
            if(entry.Season=="s0" && entry.Mode=="difficult" && entry.MapId=="difficult-map-29")
            {legacy++;check(space is null,"legacy29 floor-local resource does not fabricate common coordinates");continue;}
            check(space is not null,entry.StableKey+" native source has an invertible resource-declared common plane: "+reason);
            spaces.Add((entry.Season,entry.Mode,entry.MapId,entry.Floor),space!);
        }
        check(spaces.Count==252 && legacy==2,"all installed native floors and both legacy gaps are accounted for");
        foreach(var sample in new[]{("s0","nightmare","nightmare-12","1","B1",420d,180d),
            ("s0","nightmare","nightmare-12","1","2",180d,0d),("s0","difficult","difficult-map-01","1","2",-180d,360d),
            ("s1","nightmare","107012","-1","-2",-360d,-360d),("s1","difficult","106008","-1","-2",-120d,-480d)})
        {
            var a=spaces[(sample.Item1,sample.Item2,sample.Item3,sample.Item4)];
            var b=spaces[(sample.Item1,sample.Item2,sample.Item3,sample.Item5)];
            var relation=a.ReferenceToCommon.Inverse()*b.ReferenceToCommon;
            check(a.SpaceId==b.SpaceId && relation.AlmostEquals(new(1,0,sample.Item6,0,1,sample.Item7),1e-10),
                sample.Item3+" static floor relation agrees with independently recorded renderer/index coordinates");
        }
        foreach(var item in new (string Season,string Mode,string Id,int Floors)[]{("s0","nightmare","batch01-event-000006",3),
            ("s0","difficult","batch02-event-000147",2),("s1","nightmare","batch02-event-000319",4),
            ("s1","difficult","batch02-event-000307",4)})
        {
            var capture=new RecordedCapture(samples);var runtime=new LiveRuntime(capture,resources);var sequence=new SessionSequencer();
            try
            {
                await Ask("set_context",new{season=item.Season,mode=item.Mode,profileId="shared-structure"});
                await Ask("start_round");capture.Select(item.Id,false);var clock=Stopwatch.StartNew();var result=await Ask("capture_now");
                double recordedFitMs=clock.Elapsed.TotalMilliseconds;
                runtime.TryReadRender(out var packet);
                await File.WriteAllTextAsync(Path.Combine(output,"scene-last-input.json"),JsonSerializer.Serialize(new{item,result=result.Receipt,state=runtime.ReadState()},new JsonSerializerOptions{WriteIndented=true}));
                check(packet?.Scene is { } && packet.Scene.Sources.Floors.Count==item.Floors,
                    item.Id+" actual first observation prepares all native floor sources before visiting them");
                var scene=packet!.Scene!;
                foreach(var floor in scene.Sources.Floors.Values)
                {
                    check(floor.Material.IsCurrent(),item.Id+"/"+floor.Floor+" source is actually loaded and resource-bound");
                    if(floor.Floor==scene.SelectedFloor)check((scene.CommonToClient*floor.DisplayToCommon).AlmostEquals(packet.Material.Bridge.DisplayToClient!.Value,1e-8),
                        item.Id+" current scene pixels agree with the independently verified existing display bridge");
                }
                clock.Restart();var ready=await Task.Run(()=>WpfRuntimeSceneMaterial.Prepare(packet));double bitmapPreparationMs=clock.Elapsed.TotalMilliseconds;
                using var surface=new WpfAlignedOverlaySurface();var ticket=surface.Reserve(new object());
                var frame=ready.Frame(ticket,packet,.7);var visual=new WpfAlignedOverlayVisual();visual.Apply(frame,96,96);
                var images=((Canvas)visual.Children[0]).Children.OfType<Image>().Where(i=>i.Source is not null).ToArray();
                check(images.Length==item.Floors && images.Count(i=>i.Visibility==Visibility.Visible)==1,
                    item.Id+" actual viewport holds every floor Image and shows only the authorized selected floor");
                var single=new WpfAlignedOverlayVisual();
                single.Apply(WpfAlignedOverlaySurface.PrepareAffine(ticket,ready.Layers.Single(l=>l.Floor==scene.SelectedFloor).Image,
                    WpfRuntimeSceneMaterial.Matrix(packet.Material.Bridge.DisplayToClient!.Value),frame.Client,.7),96,96);
                foreach(int dpi in new[]{96,120,144,192})
                {
                    visual.Apply(frame,dpi,dpi);
                    single.Apply(WpfAlignedOverlaySurface.PrepareAffine(ticket,ready.Layers.Single(l=>l.Floor==scene.SelectedFloor).Image,
                        WpfRuntimeSceneMaterial.Matrix(packet.Material.Bridge.DisplayToClient!.Value),frame.Client,.7),dpi,dpi);
                    byte[] shown=Render(visual,frame.Client.Width,frame.Client.Height,dpi),expected=Render(single,frame.Client.Width,frame.Client.Height,dpi);
                    check(shown.AsSpan().SequenceEqual(expected) && Enumerable.Range(0,shown.Length/4).Any(p=>shown[p*4+3]!=0),
                        item.Id+" actual full-client scene pixels equal the independently verified single-floor display at DPI "+dpi);
                }
                var sources=images.Select(i=>i.Source).ToArray();var transforms=images.Select(i=>i.RenderTransform).ToArray();
                // Display-only control exercises ALL installed layers, including
                // unobserved floors. It cannot create a Runtime/native grant.
                foreach(var floor in ready.Layers)
                {
                    var selected=WpfAlignedOverlaySurface.PrepareScene(ticket,ready,ready.Layers,floor.Floor,
                        frame.CommonToClient,frame.Client,.7);
                    visual.Apply(selected,96,96);
                    check(images.Select(i=>i.Source).SequenceEqual(sources) && images.Select(i=>i.RenderTransform).SequenceEqual(transforms)
                        && images.Count(i=>i.Visibility==Visibility.Visible)==1,
                        item.Id+"/"+floor.Floor+" software floor switch retains actual Image/source/transform instances");
                }
                var shift=new WpfOverlayHomography(1,0,17,0,1,-9,0,0,1);
                var before=images.Select(i=>((MatrixTransform)i.RenderTransform).Matrix).ToArray();
                visual.Apply(WpfAlignedOverlaySurface.PrepareScene(ticket,ready,ready.Layers,scene.SelectedFloor,
                    shift*frame.CommonToClient,frame.Client,.7),96,96);
                check(images.Select((i,n)=>Math.Abs(((MatrixTransform)i.RenderTransform).Matrix.OffsetX-before[n].OffsetX-17)<1e-7
                    && Math.Abs(((MatrixTransform)i.RenderTransform).Matrix.OffsetY-before[n].OffsetY+9)<1e-7).All(x=>x),
                    item.Id+" one root change moves every visible and hidden floor together");
                visual.ClearMaterial();
                check(!visual.HasMaterial && images.All(i=>i.Visibility!=Visibility.Visible) && images.Select(i=>i.Source).SequenceEqual(sources),
                    item.Id+" withdrawal hides all layers while keeping only immutable resident sources");
                var id=scene.Sources;capture.Select(item.Id,false);await Ask("capture_now");runtime.TryReadRender(out var next);
                check(next?.Scene is not null && ReferenceEquals(id,next.Scene.Sources) && !ReferenceEquals(packet,next)
                    && packet.Stamp.FrameId!=next.Stamp.FrameId,item.Id+" fresh independent fit reuses static scene but renews frame permission");
                if(item.Season=="s1" && item.Mode=="nightmare")
                {
                    // Explicit user-confirmed C1 is input only at the public
                    // confirmation step; never an automatic solver whitelist.
                    await Ask("confirm_route",new{mapId="107012"});
                    runtime.TryReadRender(out var confirmed);var held=confirmed!.Scene!.Sources;
                    capture.Select("batch02-event-000323",false);var switched=await Ask("capture_now");runtime.TryReadRender(out var sparse);
                    check(switched.Receipt.Success && sparse?.Scene?.SelectedFloor=="-2"
                        && ReferenceEquals(held,sparse.Scene.Sources) && ready.Matches(sparse),
                        "C1 sparse real323 independently fits the other floor without rebuilding scene sources or bitmap set");
                    visual.Apply(ready.Frame(ticket,sparse!,.7),96,96);
                    var target=ready.Layers.Single(l=>l.Floor=="-2").Image;
                    check(images.Select(i=>i.Source).SequenceEqual(sources) && images.Count(i=>i.Visibility==Visibility.Visible)==1
                        && ReferenceEquals(images.Single(i=>i.Visibility==Visibility.Visible).Source,target),
                        "actual current-floor result switches visibility to the resident -2 Image with the original source instances");
                }
                visual.ReleaseScene();check(((Canvas)visual.Children[0]).Children.OfType<Image>().All(i=>i.Source is null),
                    item.Id+" scene release drops all resident floor references");
                await Ask("close_map");check(!runtime.TryReadRender(out _),item.Id+" closed map cannot expose cached scene authority");
                evidence.Add(new{season=item.Season,mode=item.Mode,id=item.Id,result=result.Receipt,recordedFitMs,bitmapPreparationMs,scene.SelectedFloor,scene.CommonToClient,
                    floors=scene.Sources.Floors.Select(f=>new{floor=f.Key,f.Value.ReferenceToCommon,f.Value.DisplayToCommon}),scene.Sources.Unavailable,
                    scope="Actual recorded Runtime solve and WPF multi-image viewport. Explicit hidden-floor/root-shift display controls do not authorize native or game display."});
            }
            finally{runtime.Shutdown();}
            Task<RuntimeOperationResult> Ask(string action,object? payload=null)=>runtime.DispatchAsync(new(action,
                JsonSerializer.SerializeToElement(payload??new{}),new(sequence.Issue(),runtime.ReadSnapshot().Context)),default).AsTask();
        }
        await File.WriteAllTextAsync(Path.Combine(output,"unified-scenes.json"),JsonSerializer.Serialize(evidence,new JsonSerializerOptions{WriteIndented=true}));
    }
    static async Task PartialScene(string resources,Dictionary<string,JsonObject> samples,string output,Action<bool,string> check)
    {
        string scratch=Path.Combine(output,"partial-scene-resources");Directory.CreateDirectory(scratch);
        foreach(string file in Directory.EnumerateFiles(resources,"*",SearchOption.AllDirectories))
        {string target=Path.Combine(scratch,Path.GetRelativePath(resources,file));Directory.CreateDirectory(Path.GetDirectoryName(target)!);File.Copy(file,target);}
        var catalog=ResourceCatalog.Load(scratch);var hidden=catalog.Entries.Single(e=>e.Season=="s0" && e.Mode=="nightmare" && e.MapId=="nightmare-04" && e.Floor=="2");
        byte[] original=await File.ReadAllBytesAsync(hidden.StructureNpzPath!);
        var capture=new RecordedCapture(samples);var runtime=new LiveRuntime(capture,scratch);var sequence=new SessionSequencer();
        try
        {
            await Ask("set_context",new{season="s0",mode="nightmare",profileId="shared-structure"});
            // Only this independent test copy is damaged, after catalog binding.
            await File.WriteAllBytesAsync(hidden.StructureNpzPath!,[1,2,3]);
            await Ask("start_round");capture.Select("batch01-event-000006",false);await Ask("capture_now");runtime.TryReadRender(out var first);
            check(first?.Scene is { } scene && first.MapId=="nightmare-04" && scene.Sources.Floors.Count==2
                && scene.Sources.Unavailable.ContainsKey("2"),"real current-floor fit reports an unreadable hidden resource without losing healthy sources");
            var ready=await Task.Run(()=>WpfRuntimeSceneMaterial.Prepare(first!));
            using var surface=new WpfAlignedOverlaySurface();var ticket=surface.Reserve(new object());
            var visual=new WpfAlignedOverlayVisual();var firstFrame=ready.Frame(ticket,first!,.7);visual.Apply(firstFrame,96,96);
            var images=((Canvas)visual.Children[0]).Children.OfType<Image>().Where(i=>i.Source is not null).ToArray();
            var sources=images.Select(i=>i.Source).ToArray();var transforms=images.Select(i=>i.RenderTransform).ToArray();
            capture.Select("batch01-event-000006",false);await Ask("capture_now");runtime.TryReadRender(out var second);
            await File.WriteAllTextAsync(Path.Combine(output,"partial-scene.json"),JsonSerializer.Serialize(new{
                sameOwner=ReferenceEquals(first!.Scene!.Sources,second?.Scene?.Sources),first.Stamp,secondStamp=second?.Stamp,
                first.Scene.Sources.Unavailable,state=runtime.ReadState()},new JsonSerializerOptions{WriteIndented=true}));
            check(second is not null && second.Stamp.FrameId!=first.Stamp.FrameId && ReferenceEquals(first.Scene.Sources,second.Scene?.Sources)
                && ready.Matches(second),"persistent hidden gap retains the same partial scene and WPF bitmap set under a fresh actual fit");
            visual.Apply(ready.Frame(ticket,second!,.7),96,96);
            check(images.Select(i=>i.Source).SequenceEqual(sources) && images.Select(i=>i.RenderTransform).SequenceEqual(transforms)
                && ((Canvas)visual.Children[0]).Children.OfType<Image>().Where(i=>i.Source is not null).SequenceEqual(images),
                "partial scene retry retains actual healthy Image/source/transform instances");
            await File.WriteAllBytesAsync(hidden.StructureNpzPath!,original);
            capture.Select("batch01-event-000006",false);await Ask("capture_now");runtime.TryReadRender(out var repaired);
            check(repaired?.Scene is { } fixedScene && fixedScene.Sources.Floors.Count==3 && fixedScene.Sources.Unavailable.Count==0
                && !ReferenceEquals(first.Scene.Sources,fixedScene.Sources) && !ready.Matches(repaired),
                "repaired hidden resource is retried and deliberately replaces the partial source owner");
            await Ask("close_map");check(!runtime.TryReadRender(out _),"partial scene cache never revives closed-map authority");
            surface.Withdraw();check(!surface.TryApply(ticket,firstFrame) && surface.NativeHandle==0,
                "resident multi-floor pixels cannot apply an old revoked ticket or create a native window");
            visual.ReleaseScene();
        }
        finally{runtime.Shutdown();}
        Task<RuntimeOperationResult> Ask(string action,object? payload=null)=>runtime.DispatchAsync(new(action,
            JsonSerializer.SerializeToElement(payload??new{}),new(sequence.Issue(),runtime.ReadSnapshot().Context)),default).AsTask();
    }
    static byte[] Render(FrameworkElement visual,int width,int height,int dpi)
    {
        double w=width*96d/dpi,h=height*96d/dpi;
        visual.Measure(new(w,h));visual.Arrange(new Rect(0,0,w,h));visual.UpdateLayout();
        var bitmap=new RenderTargetBitmap(width,height,dpi,dpi,PixelFormats.Pbgra32);bitmap.Render(visual);
        var result=new byte[checked(width*height*4)];bitmap.CopyPixels(result,width*4,0);return result;
    }
}
