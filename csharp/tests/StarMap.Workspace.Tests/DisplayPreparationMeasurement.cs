using System.Diagnostics;
using System.IO;
using System.Reflection;
using System.Text.Json;
using System.Text.Json.Nodes;
using System.Windows.Threading;
using StarMap.App;
using StarMap.App.Platform;
using StarMap.Core.Display;
using StarMap.Core.Identity;
using StarMap.Core.Resources;
using StarMap.Core.Settings;
using StarMap.Runtime;
using StarMap.Vision;

// A diagnostic entry in the existing WPF runner. Timings are observations,
// never acceptance thresholds or evidence of physical first-visible pixels.
internal static class DisplayPreparationMeasurement
{
    internal static async Task Run(string resources,string replay,string output,Action<bool,string> check)
    {
        var index=JsonNode.Parse(await File.ReadAllBytesAsync(Path.Combine(replay,"all-samples.json")))!["samples"]!.AsArray()
            .ToDictionary(row=>row!["id"]!.GetValue<string>(),row=>row!.AsObject());
        var rows=new List<object>();
        foreach(var (season,mode,sample) in new[]{("s0","nightmare","batch01-event-000006"),
            ("s0","difficult","batch02-event-000147"),("s1","nightmare","batch02-event-000319"),
            ("s1","difficult","batch02-event-000261")})
        {
            string label=season+"-"+mode;
            var capture=new RecordedCapture(index);capture.Select(sample,false);
            var settings=new SettingsDocument(new JsonObject{["gameSeason"]=season,["gameMode"]=mode,["routeProfileId"]="shared-structure"});
            var messages=new List<string>();var page=new RuntimePage(capture,messages.Add);
            using var materialCapture=new WindowsRuntimeCapturePort();
            RuntimeOverlayHost? host=null;
            try
            {
                var clock=Stopwatch.StartNew();bool configured=await page.Configure(resources,settings);
                double configureMs=clock.Elapsed.TotalMilliseconds;
                check(configured,label+" measurement binds actual page/resources");
                page.SetAlignedVisibility(false);
                var runtime=Field<LiveRuntime>(page,"runtime");
                Field<DispatcherTimer>(page,"observationTimer").Stop();
                page.Dispatch("start_round");await page.DrainAsync();
                host=new RuntimeOverlayHost(Dispatcher.CurrentDispatcher,page.ReadRender,materialCapture,messages.Add,visible:()=>false,
                    readMaterialToRemember:page.ReadMaterialToRemember,rememberMaterialReady:page.RememberMaterialReady);
                clock.Restart();page.HandleShortcut("map");await page.DrainAsync();
                double inputToRuntimeDrainMs=clock.Elapsed.TotalMilliseconds;
                var packet=page.ReadRender();
                if(packet is null)
                {
                    rows.Add(new{label,sample,configureMs,inputToRuntimeDrainMs,available=false,state=runtime.ReadState(),messages});
                    Save();
                    // Preserve unavailable inputs. This measurement does not
                    // turn an unresolved recognition into a passing map fit.
                    if(season=="s0")check(false,label+" known recorded source must remain drawable");
                    continue;
                }
                while(Field<WpfRuntimeSceneMaterial?>(host,"preparedMaterial") is null || runtime.ReadMaterialToRemember() is not null)
                {
                    if(clock.Elapsed>TimeSpan.FromSeconds(20))throw new TimeoutException(label+" material readiness");
                    await Task.Delay(5);
                }
                double inputToObservedHostReadyMs=clock.Elapsed.TotalMilliseconds;
                await page.DrainAsync();
                var ready=Field<WpfRuntimeSceneMaterial>(host,"preparedMaterial");
                var state=runtime.ReadState();int captureBefore=capture.Calls;
                string packetMap=packet.MapId,packetFloor=(string)Property(Property(packet,"Authorization"),"Floor");
                var bound=Field<object>(runtime,"_resources");
                var catalog=(ResourceCatalog)Property(bound,"Catalog");
                var passports=(EntryPassportCatalog)Property(bound,"Passports");
                var layers=packet.Scene?.Sources.Floors.ToDictionary(p=>p.Key,p=>p.Value.Material)
                    ??new Dictionary<string,PreparedDisplayLayer>{{packetFloor,packet.Material}};
                var componentRows=new List<object>();
                foreach(var (floor,material) in layers)
                {
                    var entry=catalog.Find(mode,season,passports.MapProfiles[packetMap],packetMap,floor)!;
                    var reference=material.Bridge.Reference!;
                    var native=floor==packetFloor
                        ?material.Bridge.CanonicalToClient!.Value*material.Bridge.ReferenceToCanonical!.Value:DisplayAffine.Identity;
                    var selectTimes=new List<double>();var coldTimes=new List<double>();var warmTimes=new List<double>();
                    var checkTimes=new List<double>();var bitmapTimes=new List<double>();
                    var producer=new DisplayMaterialPreparer(Math.Max(1,layers.Count*2));
                    PreparedDisplayPixels? lastPixels=null;
                    for(int trial=0;trial<8;trial++)
                    {
                        var select=Measure(()=>new DisplayLayerSelector(new OpenCvDisplayRasterValidator()).Select(catalog,entry,"shared-structure",native),selectTimes);
                        var cold=Measure(()=>new DisplayMaterialPreparer().Prepare(select,reference),coldTimes);
                        check(cold.Layer is not null,label+" "+floor+" measured cold producer has actual pixels "+trial);
                        var warm=Measure(()=>producer.Prepare(select,reference),warmTimes);
                        check(warm.Layer is not null,label+" "+floor+" measured retained producer has actual pixels "+trial);
                        if(lastPixels is not null)check(ReferenceEquals(lastPixels,warm.Layer!.Pixels),label+" "+floor+" retained pixel identity "+trial);
                        lastPixels=warm.Layer!.Pixels;
                        bool current=Measure(()=>material.IsCurrent(),checkTimes);
                        check(current,label+" "+floor+" actual resource snapshots remain current "+trial);
                        var bitmap=Measure(()=>WpfDisplayMaterialConsumer.PrepareMaterialReady(material),bitmapTimes);
                        check(bitmap.IsFrozen && bitmap.PixelWidth==material.Width && bitmap.PixelHeight==material.Height,
                            label+" "+floor+" actual WPF consumer dimensions "+trial);
                        // Equality is outside every timed segment.
                        check(cold.Layer!.CopyPixels().SequenceEqual(material.CopyPixels())
                            && warm.Layer.CopyPixels().SequenceEqual(material.CopyPixels()),label+" "+floor+" measured producer agrees with Runtime pixels "+trial);
                    }
                    componentRows.Add(new{floor,current=floor==packetFloor,width=material.Width,height=material.Height,
                        bytes=(long)material.Width*material.Height*4,kind=material.Bridge.DisplayKind,
                        selection=Stats(selectTimes),newProducer=Stats(coldTimes),retainedProducer=Stats(warmTimes),
                        resourceCurrent=Stats(checkTimes),wpfBitmap=Stats(bitmapTimes)});
                }
                var renderReadTimes=new List<double>();
                for(int trial=0;trial<20;trial++)check(Measure(page.ReadRender,renderReadTimes) is not null,label+" measured public render remains current "+trial);
                var refreshTimes=new List<double>();var hostReadyTimes=new List<double>();var sceneReuse=new List<bool>();var wpfReuse=new List<bool>();
                for(int trial=0;trial<8;trial++)
                {
                    clock.Restart();bool refreshed=await page.Configure(resources,settings);refreshTimes.Add(clock.Elapsed.TotalMilliseconds);
                    Field<DispatcherTimer>(page,"observationTimer").Stop();await page.DrainAsync();
                    var next=page.ReadRender();check(refreshed && next is not null,label+" actual Page/Session display reapplication "+trial);
                    while(!Field<WpfRuntimeSceneMaterial>(host,"preparedMaterial").Matches(next!))
                    {if(clock.Elapsed>TimeSpan.FromSeconds(20))throw new TimeoutException(label+" refreshed material");await Task.Delay(5);}
                    hostReadyTimes.Add(clock.Elapsed.TotalMilliseconds);
                    sceneReuse.Add(ReferenceEquals(packet.Scene?.Sources,next!.Scene?.Sources));
                    wpfReuse.Add(ReferenceEquals(ready,Field<WpfRuntimeSceneMaterial>(host,"preparedMaterial")));
                }
                check(capture.Calls==captureBefore,label+" display measurements and actual reapplication do not capture new frames");
                check(!packet.IsLive,label+" recorded material cannot authorize native display");
                rows.Add(new{label,sample,available=true,configureMs,inputToRuntimeDrainMs,inputToObservedHostReadyMs,
                    map=packetMap,floor=packetFloor,identityMap=state.Context.MapId,state.NativeGeometryVerified,state.IdentityLocked,state.IdentityKind,
                    components=componentRows,publicRenderRead=Stats(renderReadTimes),pageReapplication=Stats(refreshTimes),
                    observedReapplicationHostReady=Stats(hostReadyTimes),sceneReuse,wpfReuse,captureDelta=capture.Calls-captureBefore,
                    declaredUniqueResourceBytes=layers.Values.SelectMany(l=>l.Bridge.Resources??[]).DistinctBy(s=>s.Path).Sum(s=>s.Length),messages});
                Save();
            }
            finally{host?.Dispose();await page.CloseAsync();}
        }
        void Save()=>File.WriteAllText(Path.Combine(output,"display-preparation.json"),JsonSerializer.Serialize(new{rows,
            scope="Actual production Page/Session/Runtime with post-Nov5 recorded captures and hidden production WPF host. Component probes call actual selector/preparer/resource checks/WPF consumer. New preparer means application-cache cold, not OS/disk/process cold; retainedProducer includes its first miss. First loop samples include JIT. Host readiness is observed after 5ms polling and owner scheduling; neither readiness nor runtime drain is physical visible pixel. No timing acceptance thresholds, native capture/input/game or synthetic relation provider. S1 unresolved inputs remain unavailable rather than being supplied an identity.",
            statistics="Raw samples plus nearest-rank p50/p95/max, per named input only; eight observations are a small component diagnostic, not a workload performance distribution."},new JsonSerializerOptions{WriteIndented=true}));
    }
    static T Field<T>(object owner,string name)=>(T)owner.GetType().GetField(name,BindingFlags.Instance|BindingFlags.NonPublic)!.GetValue(owner)!;
    static object Property(object owner,string name)=>owner.GetType().GetProperty(name,BindingFlags.Instance|BindingFlags.Public|BindingFlags.NonPublic)!.GetValue(owner)!;
    static T Measure<T>(Func<T> action,List<double> samples)
    {long start=Stopwatch.GetTimestamp();T value=action();samples.Add(Stopwatch.GetElapsedTime(start).TotalMilliseconds);return value;}
    static object Stats(List<double> samples)
    {var sorted=samples.Order().ToArray();return new{count=samples.Count,firstMs=samples[0],p50Ms=sorted[(int)Math.Ceiling(sorted.Length*.5)-1],
        p95Ms=sorted[(int)Math.Ceiling(sorted.Length*.95)-1],maxMs=sorted[^1],rawMs=samples};}
}
