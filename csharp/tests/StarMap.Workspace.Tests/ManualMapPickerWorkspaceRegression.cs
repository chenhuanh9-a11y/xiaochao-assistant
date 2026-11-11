using System.IO;
using System.Reflection;
using System.Runtime.InteropServices;
using System.Text.Json;
using System.Text.Json.Nodes;
using System.Windows;
using System.Windows.Controls;
using System.Windows.Interop;
using System.Windows.Media;
using System.Windows.Media.Imaging;
using System.Windows.Threading;
using StarMap.App;
using StarMap.App.Platform;
using StarMap.Core.Input;
using StarMap.Core.Session;
using StarMap.Core.Settings;
using StarMap.Runtime;

internal static class ManualMapPickerWorkspaceRegression
{
    internal static async Task Run(string resources,string replay,string output,Action<bool,string> check)
    {
        Rules(check);
        var samples=JsonNode.Parse(await File.ReadAllBytesAsync(Path.Combine(replay,"all-samples.json")))!["samples"]!.AsArray()
            .ToDictionary(row=>row!["id"]!.GetValue<string>(),row=>row!.AsObject());
        var capture=new RecordedCapture(samples);capture.Select("batch02-event-000097",false);
        var messages=new List<string>();int scopeChanges=0;
        var page=new RuntimePage(capture,messages.Add,manualPickerWindowFactory:NewWindow);
        var service=Field<ManualMapPickerService>(page,"manualPicker");
        page.ManualPickerScopeChanged=()=>scopeChanges++;
        var shell=new WorkspaceWindow(Path.Combine(output,"picker-shell"),null);
        await Field<RuntimePage>(shell,"runtimePage").CloseAsync();
        typeof(WorkspaceWindow).GetField("runtimePage",BindingFlags.Instance|BindingFlags.NonPublic)!.SetValue(shell,page);
        var host=new Window {Content=page,Left=-20000,Top=-20000,Width=920,Height=1100,ShowActivated=false,ShowInTaskbar=false};
        var evidence=new JsonArray();
        try
        {
            check(await page.Configure(resources,SettingsDocument.CreateNew()),"picker-real-page-binds-current-prepared-resources");
            check(!page.ToggleManualPicker() && service.Window is null && capture.Calls==0,
                "picker-before-round-creates-no-window-and-performs-no-capture");
            host.Show();await Until(()=>Field<Button>(page,"start").IsEnabled);
            Field<Button>(page,"start").RaiseEvent(new(Button.ClickEvent));await page.DrainAsync();
            await Until(()=>Field<Button>(page,"openPicker").IsEnabled);
            Field<Button>(page,"openPicker").RaiseEvent(new(Button.ClickEvent));await service.DrainAsync();
            check(service.Visible && service.State is not null && service.Keys.Count==8 && scopeChanges>0 && capture.Calls==0,
                "actual-open-button-reads-only-prepared-catalog-and-enables-scoped-picker-keys");
            var picker=service.Window!;await picker.DrainImagesAsync();
            check(!picker.ShowActivated && picker.Topmost && !picker.ShowInTaskbar && picker.IsVisible,
                "actual-owned-picker-uses-nonactivating-topmost-tool-window-properties");
            int extendedStyle=GetWindowLong(new WindowInteropHelper(picker).Handle,-20);
            check((extendedStyle&0x08000080)==0x08000080 && (extendedStyle&0x20)==0,
                "actual-picker-HWND-is-NOACTIVATE-and-TOOLWINDOW-without-click-through");
            var runtime=Field<LiveRuntime>(page,"runtime");
            var catalog=await Task.Run(()=>runtime.ReadManualMapCatalog()) ?? throw new InvalidDataException("No bound picker catalog");
            var original=JsonNode.Parse(await File.ReadAllBytesAsync(Path.Combine(Path.GetDirectoryName(Path.GetFullPath(replay))!,"phase33-manual-picker-original-02.json")))!;
            await File.WriteAllTextAsync(Path.Combine(output,"manual-picker-s0-catalog.json"),JsonSerializer.Serialize(new {catalog,state=page.State,capture.Calls},new JsonSerializerOptions{WriteIndented=true}));
            check(MatchesOriginal(catalog)
                && !page.State!.IdentityLocked && page.ReadRender() is null && capture.Calls==0,
                "picker-catalog-preserves-source-order-names-and-display-thumbnails-without-identity-or-pose-writes");
            var groups=original["contexts"]!["s0/nightmare"]!["groups"]!.AsArray();
            check(service.State!.Stage=="groups" && service.State.PageCount==2 && service.State.Options.Zip(groups.Take(4)).All(pair=>
                pair.First.Label==pair.Second!["label"]!.GetValue<string>() && pair.First.Count==pair.Second["mapIds"]!.AsArray().Count),
                "actual-picker-first-group-page-matches-original-grouping-and-member-counts");
            var firstLabel=Descendants((Button)picker.FindName("ManualPickerOption0")).OfType<TextBlock>().First(row=>row.IsVisible);
            var foreground=firstLabel.Foreground;for(int i=0;i<32;i++)service.Synchronize();
            check(ReferenceEquals(foreground,firstLabel.Foreground),"unchanged-picker-pulses-do-not-rewrite-option-controls-or-recreate-brushes");
            Save("groups");
            if(service.State!.Stage=="groups")Click(picker,"ManualPickerOption0");
            await picker.DrainImagesAsync();
            check(service.State!.Stage=="maps" && Descendants(picker).OfType<TextBlock>().Any(row=>row.Text=="预览不可用" && row.IsVisible),
                "actual-group-card-enters-the-map-stage-and-declared-missing-images-show-an-explicit-placeholder");
            Save("maps-missing");service.HandleKey("Backspace");
            var available=catalog.Maps.First(row=>row.ThumbnailPath is not null);await ChooseMap(available,submit:false);
            await picker.DrainImagesAsync();
            var options=service.State!.Options;string wanted=available.MapId;
            check(service.State.Stage=="maps" && options.Count<=4
                && Descendants(picker).OfType<Image>().Any(image=>image.Source is BitmapSource {IsFrozen:true}),
                "actual-group-card-enters-the-map-stage-and-consumes-real-frozen-thumbnails");
            Save("maps");Click(picker,"ManualPickerOption"+options.ToList().FindIndex(row=>row.MapId==wanted));await page.DrainAsync();
            check(!service.Visible && service.Keys.Count==0 && page.State is {IdentityLocked:true,IdentitySource:"manual",NativeGeometryVerified:false}
                && page.State.Context.MapId==wanted && page.ReadRender() is null && capture.Calls==0,
                "actual-map-card-commits-identity-through-Session-before-any-frame-or-fit-and-closes-key-scope");

            page.Dispatch("end_round");await page.DrainAsync();
            check(await page.Configure(resources,new(new JsonObject {["gameSeason"]="s0",["gameMode"]="difficult",["routeProfileId"]="shared-structure"})),
                "picker-same-page-can-bind-next-mode-after-round-end");
            page.Dispatch("start_round");await page.DrainAsync();page.Dispatch("capture_now");await page.DrainAsync();
            var before=page.State!;
            // Configure replaced the owner; query the actual new prepared runtime.
            runtime=Field<LiveRuntime>(page,"runtime");var d=await Task.Run(()=>runtime.ReadManualMapCatalog());
            await File.WriteAllTextAsync(Path.Combine(output,"manual-picker-difficult-catalog.json"),JsonSerializer.Serialize(new {catalog=d,state=before},new JsonSerializerOptions{WriteIndented=true}));
            check(before.RemainingMapIds.Count==26 && d is not null && d.Context.ResourceId==before.Context.ResourceId
                && MatchesOriginal(d),
                "difficult-catalog-matches-original-full-reference-availability-without-substituting-other-previews");
            var excluded=d!.Maps.First(row=>!before.RemainingMapIds.Contains(row.MapId));
            check(page.ToggleManualPicker(),"picker-can-open-after-default-index-narrowing");await service.DrainAsync();
            long issued=page.SessionView!.Sequence.LastIssuedSequence;await ChooseMap(excluded);
            check(service.Visible && page.SessionView!.Sequence.LastIssuedSequence==issued && !page.State!.IdentityLocked
                && page.State.RemainingMapIds.SequenceEqual(before.RemainingMapIds) && messages.Any(text=>text.Contains("已在本局排除")),
                "excluded-map-click-keeps-picker-open-and-cannot-reset-the-single-round-pool");
            var keys=Enumerable.Repeat("",SettingsDocument.ActionOrder.Length).ToArray();keys[^1]="F8";
            var bindings=GlobalInputBindings.FromSettings("G",keys);var policy=new GlobalInputDispatcher(bindings);
            policy.Reset(bindings,new(1,[])); // same generation admission established by the real Started notification
            Deliver(0x1b);await page.DrainAsync();
            check(!service.Visible && service.Keys.Count==0 && !page.State!.MapOpen
                && page.State.RemainingMapIds.SequenceEqual(before.RemainingMapIds),
                "real-policy-Escape-delivery-closes-picker-and-retains-existing-map-close-semantics");
            Deliver(0x77);await service.DrainAsync();
            check(service.Visible,"configured-manual-picker-action-reaches-actual-Workspace-consumer");
            var initial=service.State!;
            if(initial.Stage=="groups")Deliver(0x70);
            check(service.State!.Stage=="maps","scoped-F1-delivery-routes-to-picker-instead-of-an-unrelated-candidate-action");
            page.Dispatch("end_round");await page.DrainAsync();service.Synchronize();
            check(!service.Visible && service.Keys.Count==0 && !picker.IsVisible,
                "round-end-retires-visible-picker-and-scoped-keys-even-before-the-page-render-timer");
            capture.Select("batch02-event-000315",false);
            check(await page.Configure(resources,new(new JsonObject {["gameSeason"]="s1",["gameMode"]="nightmare",["routeProfileId"]="shared-structure"})),
                "picker-can-bind-actual-S1-resources-after-the-S0-round");
            page.Dispatch("start_round");await page.DrainAsync();
            check(!page.ToggleManualPicker() && !service.Visible,"S1-automatic-round-does-not-admit-the-old-manual-picker-workflow");
            page.Dispatch("end_round");await page.DrainAsync();page.Dispatch("prepare_manual_alignment");await page.DrainAsync();
            check(page.ToggleManualPicker(),"S1-prepared-manual-session-admits-the-picker");await service.DrainAsync();
            runtime=Field<LiveRuntime>(page,"runtime");var s1=await Task.Run(()=>runtime.ReadManualMapCatalog());
            await File.WriteAllTextAsync(Path.Combine(output,"manual-picker-s1-catalog.json"),JsonSerializer.Serialize(s1,new JsonSerializerOptions{WriteIndented=true}));
            check(s1 is {Context.Season:"s1",Context.Mode:"nightmare"} && s1.Context.ResourceId==page.State!.Context.ResourceId
                && s1.Maps.Count==15 && MatchesOriginal(s1),
                "S1-catalog-uses-its-current-native-entry-floor-displays-and-never-S0-thumbnails");
            var hold=capture.HoldNext();
            try
            {
                await ChooseMap(s1!.Maps.Single(row=>row.MapId=="107012"),drain:false);
                await hold.Started.Task.WaitAsync(TimeSpan.FromSeconds(10));
                check(!service.Visible && page.State is {ManualAlignmentSession:true,IdentityLocked:true,IdentitySource:"manual",NativeGeometryVerified:false,Context.MapId:"107012"}
                    && page.ReadRender() is null,"S1-real-picker-confirmation-locks-identity-before-its-held-current-floor-acquisition");
            }
            finally {hold.Release.TrySetResult();await page.DrainAsync();}
            check(page.State is {ManualAlignmentSession:true,IdentityLocked:true,NativeGeometryVerified:true,Context.MapId:"107012",Context.FloorId:"-1"}
                && page.ReadRender() is not null,"S1-picker-selection-is-consumed-by-the-existing-manual-observation-and-fit-path");
            page.Dispatch("end_round");await page.DrainAsync();
            check(await page.Configure(resources,new(new JsonObject {["gameSeason"]="s1",["gameMode"]="difficult",["routeProfileId"]="shared-structure"})),
                "picker-can-bind-S1-difficult-catalog-after-the-manual-session");
            runtime=Field<LiveRuntime>(page,"runtime");var s1d=await Task.Run(()=>runtime.ReadManualMapCatalog());
            check(s1d is not null && s1d.Maps.Count==14 && MatchesOriginal(s1d) && !service.Visible,
                "S1-difficult-prepared-catalog-matches-original-native-displays-without-starting-a-round");
            await StaleCatalog(check);
            await Thumbnails(output,check);
            await ManualMapPickerPlacementWorkspaceRegression.Run(output,check);
            await File.WriteAllTextAsync(Path.Combine(output,"manual-picker-workspace.json"),evidence.ToJsonString(new JsonSerializerOptions {WriteIndented=true}));

            bool MatchesOriginal(RuntimeManualMapCatalog actual)
            {
                var expected=original["contexts"]![actual.Context.Season+"/"+actual.Context.Mode]!["maps"]!.AsArray();
                return actual.Maps.Count==expected.Count && actual.Maps.Zip(expected).All(pair=>
                    pair.First.MapId==pair.Second!["id"]!.GetValue<string>() && pair.First.Name==pair.Second["name"]!.GetValue<string>()
                    && pair.First.Direction==pair.Second["direction"]!.GetValue<string>()
                    && string.Equals(pair.First.ThumbnailPath,pair.Second["thumbnailPath"]?.GetValue<string>(),StringComparison.OrdinalIgnoreCase)
                    && (pair.First.ThumbnailPath is null?pair.First.ThumbnailStamp.Length==0:pair.First.ThumbnailStamp.Length>0));
            }

            void Deliver(int vk)
            {
                policy.SetPickerKeys(page.ManualPickerKeys);
                var scope=new GlobalInputScope(42,true,42,false,true,true);
                double now=System.Diagnostics.Stopwatch.GetTimestamp()/(double)System.Diagnostics.Stopwatch.Frequency;
                var down=new GlobalInputKeyEvent(vk,vk,true,[vk],now,42,1,false,false);
                var dispatch=policy.Dispatch(down,scope,now);
                typeof(WorkspaceWindow).GetMethod("ConsumeInput",BindingFlags.Instance|BindingFlags.NonPublic)!.Invoke(shell,
                    [new WpfGlobalInputDelivery(new(GlobalInputNotificationKind.Key,1,Key:down),dispatch)]);
                policy.Dispatch(down with {IsDown=false,Held=[]},scope,now);
            }
            async Task ChooseMap(ManualMapCatalogEntry map,bool drain=true,bool submit=true)
            {
                string[] parts=map.Name.Split('/').Select(part=>part.Trim()).ToArray();
                string group=parts.Length>=2&&parts.All(part=>part.Length>0)?parts[0]
                    : map.Direction.Trim() is {Length:>0} dir && dir is not ("unknown" or "全部")?dir:"其他";
                while(service.State!.Stage=="groups")
                {
                    int index=service.State.Options.ToList().FindIndex(row=>row.Label==group);
                    if(index>=0){Click(picker,"ManualPickerOption"+index);break;}
                    int previous=service.State.Page;Click(picker,"ManualPickerNext");
                    if(service.State.Page==previous)throw new InvalidDataException("Group missing from picker");
                }
                while(true)
                {
                    int index=service.State!.Options.ToList().FindIndex(row=>row.MapId==map.MapId);
                    if(index>=0){if(submit)Click(picker,"ManualPickerOption"+index);break;}
                    int previous=service.State.Page;Click(picker,"ManualPickerNext");
                    if(service.State.Page==previous)throw new InvalidDataException("Map missing from picker");
                }
                if(drain)await page.DrainAsync();
            }
            void Save(string label)
            {
                picker.UpdateLayout();evidence.Add(JsonSerializer.SerializeToNode(new {label,options=service.State?.Options,page=service.State?.Page,
                    context=page.State!.Context,capture.Calls,scope="Owned offscreen WPF window; recorded capture port, no native game input/capture or physical-pixel acceptance"}));
                var bitmap=new RenderTargetBitmap((int)Math.Ceiling(picker.ActualWidth),(int)Math.Ceiling(picker.ActualHeight),96,96,PixelFormats.Pbgra32);
                bitmap.Render(picker);var encoder=new PngBitmapEncoder();encoder.Frames.Add(BitmapFrame.Create(bitmap));
                using var stream=File.Create(Path.Combine(output,"picker-"+label+".png"));encoder.Save(stream);
            }
        }
        finally {await page.CloseAsync();host.Close();shell.Close();}
    }

    static void Rules(Action<bool,string> check)
    {
        ManualMapPickerEntry E(string id,string name,string direction="")=>new(id,name,direction,null,"");
        var state=new ManualMapPickerState([E("a","其他图"),E("b","右中 / B"),E("c","左中 / C"),E("d","左中右 / D"),
            E("e","左右 / E"),E("f","新组 / F"),E("g","另一组 / G")],"nightmare");
        check(state.Stage=="groups" && state.Options.Select(row=>row.Label).SequenceEqual(["左中右","左中","左右","右中"])
            && state.Options.Select(row=>row.Key).SequenceEqual(["F1","F2","F3","F4"]),"picker-rules-preserve-legacy-preferred-order-and-four-option-keys");
        state.Turn(1);state.Choose(1);
        check(state.Stage=="maps" && state.Choose(0)?.MapId=="g" && state.Options[0].Label=="G"
            && state.Back() && state.Page==1,"picker-rules-return-to-the-original-group-page-and-strip-only-the-group-prefix");
        state.Turn(int.MaxValue);check(state.Page==1&&!state.Turn(1)&&state.Choose(3) is null,"picker-rules-clamp-pages-and-ignore-nonexistent-option-slots");
        var single=new ManualMapPickerState(Enumerable.Range(0,6).Select(i=>E(i.ToString(),"原名 "+i,"全部")).ToArray(),"difficult");
        single.Turn(1);check(single.SingleGroup && single.Stage=="maps" && single.PageCount==2 && single.Options.Count==2
            && single.Choose(1)?.MapId=="5" && !single.Back(),"picker-rules-single-group-skips-category-and-retains-source-map-order");
        var direction=new ManualMapPickerState([E("u","  原名  ","UNKNOWN")],"difficult");
        check(direction.GroupLabel=="UNKNOWN" && direction.Options[0].Label=="  原名  ","picker-rules-match-original-case-sensitive-direction-and-unprefixed-title");
    }

    static async Task StaleCatalog(Action<bool,string> check)
    {
        var a=new ManualMapCatalogContext("s0","nightmare","shared-structure","round-a","resources-a");
        var b=a with {RoundId="round-b",ResourceId="resources-b"};var current=a;
        var release=new TaskCompletionSource<RuntimeManualMapCatalog?>(TaskCreationOptions.RunContinuationsAsynchronously);int calls=0;
        var service=new ManualMapPickerService(Dispatcher.CurrentDispatcher,()=>new(current,true,""),_=>++calls==1?release.Task:
            Task.FromResult<RuntimeManualMapCatalog?>(new(b,[new("new-map","新局地图","",null,"")])),_=>null,()=>{},_=>{},createWindow:NewWindow);
        try
        {
            service.Open();current=b;service.Synchronize();service.Open();
            check(calls==1 && service.State is null,"picker-coalesces-a-new-context-behind-one-held-catalog-job");
            release.SetResult(new(a,[new("old-map","旧局地图","",null,"")]));await service.DrainAsync();
            check(calls==2 && service.Visible && service.State!.Options.Single().MapId=="new-map" && service.LastError is null,
                "late-old-catalog-cannot-revive-after-round-resource-change-and-only-latest-request-runs");
        }
        finally {release.TrySetResult(null);await service.CloseAsync();}
        int attempts=0;
        var retry=new ManualMapPickerService(Dispatcher.CurrentDispatcher,()=>new(b,true,""),_=>++attempts==1
            ?Task.FromException<RuntimeManualMapCatalog?>(new IOException("deliberate catalog read failure"))
            :Task.FromResult<RuntimeManualMapCatalog?>(new(b,[new("retry-map","重试地图","",null,"")])),_=>null,()=>{},_=>{},createWindow:NewWindow);
        try
        {
            retry.Open();await retry.DrainAsync();
            check(retry.Visible && retry.State is null && retry.LastError!.Contains("deliberate catalog read failure")
                && Descendants(retry.Window!).OfType<TextBlock>().Any(row=>row.Text.Contains("读取失败")),
                "catalog-worker-failure-remains-visible-with-recoverable-copy-and-preserved-error");
            retry.Hide();retry.Open();await retry.DrainAsync();
            check(attempts==2 && retry.State!.Options.Single().MapId=="retry-map" && retry.LastError is null,
                "next-explicit-picker-open-recovers-after-catalog-read-failure");
        }
        finally {await retry.CloseAsync();}
    }

    static async Task Thumbnails(string output,Action<bool,string> check)
    {
        string first=Path.Combine(output,"thumbnail-blue.png"),last=Path.Combine(output,"thumbnail-green.png");
        Png(first,Colors.Blue);Png(last,Colors.Lime);
        var started=new TaskCompletionSource(TaskCreationOptions.RunContinuationsAsynchronously);
        using var release=new ManualResetEventSlim();int calls=0;
        var paths=new System.Collections.Concurrent.ConcurrentQueue<string>();
        var window=new ManualMapPickerWindow((path,_)=>
        {
            paths.Enqueue(path);if(Interlocked.Increment(ref calls)==1){started.TrySetResult();release.Wait();}
            return ResourcePreview.LoadImage(path,default); // only completion scheduling is held; decoder is real
        }) {Left=-20000,Top=-20000};
        ManualMapPickerOption Option(string path,string stamp="1")=>new(0,"F1","缩略图","fixture-map",1,path,stamp);
        void Render(ManualMapPickerOption option,bool busy=false)=>window.Render("测试选图","","","",[option],busy);
        try
        {
            Render(Option(first));window.Show();await started.Task.WaitAsync(TimeSpan.FromSeconds(10));
            for(int i=0;i<64;i++)Render(Option(Path.Combine(output,"retired-not-decoded-"+i+".png")));
            Render(Option(last));
            check(calls==1 && Descendants(window).OfType<Image>().All(image=>image.Source is null),
                "held-real-thumbnail-decode-keeps-old-pixels-revoked-during-many-page-changes");
            release.Set();await Until(()=>calls==2 && Descendants(window).OfType<Image>().Any(row=>row.Source is not null));
            await window.DrainImagesAsync();
            var image=Descendants(window).OfType<Image>().Single(row=>row.Source is not null);var bitmap=(BitmapSource)image.Source;
            var pixel=new byte[4];bitmap.CopyPixels(new Int32Rect(0,0,1,1),pixel,4,0);
            check(calls==2 && paths.SequenceEqual([first,last]) && bitmap.IsFrozen && pixel[1]==255 && pixel[0]==0,
                "only-first-and-latest-thumbnail-jobs-run-and-latest-real-pixels-reach-the-current-card");
            Render(Option(last),true);Render(Option(last));await window.DrainImagesAsync();
            check(calls==2 && ReferenceEquals(image.Source,bitmap),"busy-state-only-render-reuses-the-prepared-thumbnail-instance");
            window.Hide();window.Show();await window.DrainImagesAsync();
            check(calls==2 && ReferenceEquals(image.Source,bitmap),"picker-reopen-reuses-only-the-bounded-immutable-thumbnail-cache");
            Render(Option(last,"changed-stamp"));await window.DrainImagesAsync();
            check(calls==3 && !ReferenceEquals(image.Source,bitmap),"changed-resource-stamp-prepares-a-new-thumbnail-instead-of-reusing-old-pixels");
            Render(Option(Path.Combine(output,"missing-thumbnail.png")));await window.DrainImagesAsync();
            check(Descendants(window).OfType<Image>().All(row=>row.Source is null)
                && Descendants(window).OfType<TextBlock>().Any(row=>row.Text=="预览不可用" && row.IsVisible),
                "failed-real-file-decode-displays-a-placeholder-and-cannot-retain-the-previous-image");
            int beforeRetry;
            using(var held=new FileStream(last,FileMode.Open,FileAccess.ReadWrite,FileShare.None))
            {
                Render(Option(last,"same-stamp-after-lock"));await window.DrainImagesAsync();beforeRetry=calls;
                check(Descendants(window).OfType<Image>().All(row=>row.Source is null),
                    "real-exclusive-file-lock-shows-an-unavailable-thumbnail-without-old-pixels");
            }
            window.Hide();window.Show();await window.DrainImagesAsync();
            check(calls==beforeRetry+1 && Descendants(window).OfType<Image>().Any(row=>row.Source is BitmapSource {IsFrozen:true}),
                "reopening-retries-a-transient-decode-failure-with-the-same-file-stamp-after-unlock");
        }
        finally {release.Set();window.Dispose();await window.DrainImagesAsync();}

        var closingStarted=new TaskCompletionSource(TaskCreationOptions.RunContinuationsAsynchronously);
        using var closingRelease=new ManualResetEventSlim();
        var closing=new ManualMapPickerWindow((path,_)=>{closingStarted.TrySetResult();closingRelease.Wait();return ResourcePreview.LoadImage(path,default);})
            {Left=-20000,Top=-20000};
        try
        {
            closing.Render("测试关闭","","","",[Option(first)],false);closing.Show();
            await closingStarted.Task.WaitAsync(TimeSpan.FromSeconds(10));closing.Dispose();closingRelease.Set();await closing.DrainImagesAsync();
            check(!closing.IsVisible && Descendants(closing).OfType<Image>().All(row=>row.Source is null),
                "disposed-picker-drains-a-deliberately-late-real-decode-without-reviving-any-card-or-window");
        }
        finally {closingRelease.Set();closing.Dispose();await closing.DrainImagesAsync();}

        static void Png(string path,Color color)
        {
            var bytes=Enumerable.Range(0,16*16).SelectMany(_=>new byte[]{color.B,color.G,color.R,color.A}).ToArray();
            var encoder=new PngBitmapEncoder();encoder.Frames.Add(BitmapFrame.Create(BitmapSource.Create(16,16,96,96,PixelFormats.Bgra32,null,bytes,64)));
            using var file=File.Create(path);encoder.Save(file);
        }
    }

    static ManualMapPickerWindow NewWindow()=>new(){Left=-20000,Top=-20000,WindowStartupLocation=WindowStartupLocation.Manual};
    [DllImport("user32.dll",EntryPoint="GetWindowLongW")] static extern int GetWindowLong(nint hwnd,int index);
    static void Click(Window window,string name)
    {var button=Descendants(window).OfType<Button>().Single(row=>row.Name==name);if(!button.IsEnabled)throw new InvalidDataException("Disabled picker button: "+name);button.RaiseEvent(new(Button.ClickEvent));}
    static IEnumerable<DependencyObject> Descendants(DependencyObject node)
    {yield return node;foreach(object child in LogicalTreeHelper.GetChildren(node))if(child is DependencyObject next)foreach(var item in Descendants(next))yield return item;}
    static T Field<T>(object owner,string name)=>(T)owner.GetType().GetField(name,BindingFlags.Instance|BindingFlags.NonPublic)!.GetValue(owner)!;
    static async Task Until(Func<bool> ready)
    {var timer=System.Diagnostics.Stopwatch.StartNew();while(!ready()){if(timer.Elapsed>TimeSpan.FromSeconds(10))throw new TimeoutException("Manual picker control did not settle");await Task.Delay(10);}}
}
