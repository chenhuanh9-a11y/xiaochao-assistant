using System.IO;
using System.Reflection;
using System.Text.Json;
using System.Text.Json.Nodes;
using System.Windows;
using System.Windows.Controls;
using System.Windows.Media;
using System.Windows.Media.Imaging;
using StarMap.App;
using StarMap.App.Platform;
using StarMap.Core.Settings;
using StarMap.Core.Session;
using StarMap.Core.Identity;

internal static class S0StructureIndexWorkspaceRegression
{
    internal static async Task Run(string resources,string replay,string output,Action<bool,string> check)
    {
        var index=JsonNode.Parse(await File.ReadAllBytesAsync(Path.Combine(replay,"all-samples.json")))!["samples"]!.AsArray()
            .ToDictionary(row=>row!["id"]!.GetValue<string>(),row=>row!.AsObject());
        var capture=new RecordedCapture(index);capture.Select("batch01-event-000006",false);
        var page=new RuntimePage(capture,_=>{});var evidence=new JsonArray();
        var window=new Window {Content=page,Left=-20000,Top=-20000,Width=920,Height=1100,ShowActivated=false,
            ShowInTaskbar=false,WindowStartupLocation=WindowStartupLocation.Manual,Background=(Brush)Application.Current.FindResource("StarMap.Canvas")};
        try
        {
            check(await page.Configure(resources,SettingsDocument.CreateNew()),"S0-index-WPF-binds-default-production-resources-and-Session");
            var consumed=new List<SessionCompletion>();Field<SessionService>(page,"session").ResultConsumed+=consumed.Add;
            window.Show();var start=Field<Button>(page,"start");var acquire=Field<Button>(page,"acquire");
            var list=Field<ListBox>(page,"candidates");var confirm=Field<Button>(page,"confirm");
            await Until(()=>start.IsEnabled);await Click(start);await Click(acquire);Save("nightmare-provisional",true);
            check(page.State is {IdentityLocked:false,IdentityKind:"candidate",RecognitionPath:"structure-index",Context.MapId:null}
                && page.State.RemainingMapIds.Count==26 && page.ReadRender() is not null,
                "actual-S0-buttons-consume-conditional-index-material-without-a-unique-or-family-identity");
            check(Field<TextBlock>(page,"mapIdentity").Text.Contains("临时参考")
                && Field<TextBlock>(page,"candidateCaption").Text.Contains("尚未锁定")
                && Field<StackPanel>(page,"groupArea").Visibility==Visibility.Collapsed
                && list.Items.Count==26 && confirm.IsEnabled,
                "unresolved-nightmare-candidates-remain-selectable-and-distinct-from-the-registered-group-control");
            var names=EntryPassportCatalog.Load(resources,"s0","nightmare").Maps.ToDictionary(row=>row.MapId,row=>row.Name);
            check(page.State!.Candidates.Zip(list.Items.Cast<object>()).All(pair=>pair.First.Name==names[pair.First.MapId]
                && pair.Second.ToString()==names[pair.First.MapId]+" · 待确认"),
                "actual-candidate-controls-use-resource-declared-map-names-instead-of-internal-GUIDs");
            check(Field<TextBlock>(page,"detail").Text=="已贴合临时候选，继续探图筛选地图。",
                "S0-temporary-fit-status-is-readable-Chinese-in-the-actual-TextBlock");
            var material=page.ReadRender()!;var bitmap=await Task.Run(()=>WpfDisplayMaterialConsumer.PrepareMaterialReady(material.Material));
            check(bitmap.IsFrozen && bitmap.PixelWidth==material.Material.Width && !page.SessionView!.Runtime.LiveAuthorization,
                "actual-WPF-material-consumer-prepares-recorded-candidate-pixels-without-native-live-authority");
            check(!page.RememberMaterialReady(material) && page.ReadMaterialToRemember() is null,
                "actual-WPF-ready-callback-cannot-promote-a-provisional-map-into-steady-learning");
            var predecessor=page.ReadRender();list.SelectedIndex=page.State!.Candidates.ToList().FindIndex(row=>row.MapId=="nightmare-12");
            await page.DrainAsync();
            check(!page.State!.IdentityLocked && page.State.Candidates[page.State.SelectedCandidateIndex].MapId=="nightmare-12"
                && ReferenceEquals(page.ReadRender(),predecessor),"real-candidate-selection-is-an-acknowledged-draft-before-human-confirmation");
            await Until(()=>confirm.IsEnabled);long staleBeforeConfirm=page.SessionView!.StaleResults;
            var hold=capture.HoldNext(ignoreCancellation:true);
            confirm.RaiseEvent(new(Button.ClickEvent));
            try
            {
                await hold.Started.Task.WaitAsync(TimeSpan.FromSeconds(10));
                await Until(()=>Field<TextBlock>(page,"mapIdentity").Text.Contains("已锁定")
                    && Field<TextBlock>(page,"candidateCaption").Text.Contains("等待结构对齐"));
                check(page.State is {IdentityLocked:true,IdentitySource:"manual",Context.MapId:"nightmare-12",NativeGeometryVerified:false}
                    && page.ReadRender() is null,"actual-confirm-button-publishes-human-identity-and-waiting-copy-before-blocked-capture");
                Save("human-identity-before-fit",true);page.HandleShortcut("map");
            }
            finally{hold.Release.TrySetResult();}
            await page.DrainAsync();
            check(page.State is {MapOpen:false,IdentityLocked:true,Context.MapId:"nightmare-12"} && page.ReadRender() is null,
                "map-close-through-production-input-policy-cannot-be-undone-by-the-late-manual-capture");
            long retiredAfterClose=page.SessionView!.StaleResults;
            check(retiredAfterClose==staleBeforeConfirm+1 && consumed.Count(row=>row.Action=="confirm_route")==1,
                "Session-retires-exactly-the-one-confirmation-deliberately-cancelled-before-its-late-capture");
            page.HandleShortcut("map");await page.DrainAsync();
            check(page.State is {IdentityLocked:true,NativeGeometryVerified:true} && page.ReadRender() is not null,
                "fresh-reopening-aligns-only-the-user-selected-map-and-restores-current-material");

            await Dispatch("end_round");
            check(await page.Configure(resources,new SettingsDocument(new JsonObject{["gameSeason"]="s0",["gameMode"]="difficult"})),
                "same-WPF-page-can-bind-the-next-mode-after-ending-the-round");
            capture.Select("batch02-event-000097",false);await Click(start);await Click(acquire);Save("difficult-narrowed");
            check(page.State!.RemainingMapIds.Count==26 && Field<TextBlock>(page,"mapIdentity").Text.Contains("剩余 26 张")
                && list.Items.Count==26 && ReferenceEquals(list,Field<ListBox>(page,"candidates")),
                "actual-page-consumes-the-narrowed-pool-with-stable-controls");
            await Dispatch("close_map");capture.Select("batch02-event-000099",false);await Click(acquire);Save("sparse-floor2",true);
            check(page.State is {RecognitionPath:"structure-index",Context.FloorId:"2",IdentityLocked:false,NativeGeometryVerified:false}
                && page.State.RemainingMapIds.Count==26 && list.Items.Count==26 && page.ReadRender() is null,
                "sparse-floor2-withdraws-pixels-but-preserves-the-same-page-candidate-inventory");
            check(Field<TextBlock>(page,"detail").Text=="当前结构不足以可靠贴合，已保留候选；请继续探图或手动选图。",
                "unresolved-S0-fit-gives-an-actionable-status-instead-of-an-internal-reason-token");
            await Until(()=>confirm.IsEnabled);await Click(confirm);Save("sparse-human-choice");
            check(page.State is {IdentityLocked:true,IdentitySource:"manual",NativeGeometryVerified:false}
                && Field<TextBlock>(page,"mapIdentity").Text.Contains("已锁定")
                && Field<TextBlock>(page,"candidateCaption").Text.Contains("等待结构对齐") && page.ReadRender() is null,
                "human-choice-remains-visible-when-sparse-current-floor-cannot-align");
            await Dispatch("end_round");await Click(start);await Click(acquire);Save("ordinary-floor2-no-door");
            check(page.State is {RecognitionPath:"ordinary",Context.FloorId:"2",IdentityLocked:false}
                && page.State.RemainingMapIds.Count==0 && page.ReadRender() is null
                && Field<TextBlock>(page,"detail").Text=="当前尚未确认地图，请先在一层识别或手动选图。",
                "ordinary-initial-floor2-capability-limit-reaches-the-real-page-with-a-readable-next-action");
            await Dispatch("close_map");capture.Select("batch02-event-000097",false);
            capture.TransformPixels=(pixels,width,height,stride)=>
            {
                for(int y=(int)(height*.19);y<(int)(height*.61);y++)
                    for(int x=(int)(width*.84);x<(int)(width*.92);x++)
                    {int p=y*stride+x*4;pixels[p]=40;pixels[p+1]=32;pixels[p+2]=24;pixels[p+3]=255;}
            };
            await Click(acquire);Save("unreadable-floor-door-entered");
            check(page.State is {RecognitionPath:"structure-index",Context.FloorId:null,IdentityLocked:false}
                && page.State.RemainingMapIds.Count==0 && page.ReadRender() is null && Field<Button>(page,"applyFloor").IsEnabled
                && Field<TextBlock>(page,"detail").Text=="当前楼层尚未读清，请等待高亮稳定后重新读取，或手动选择楼层。",
                "unknown-floor-door-entry-preserves-strategy-and-offers-the-real-manual-floor-control-without-fit-authority");
            Field<ComboBox>(page,"floor").SelectedValue="1";await Click(Field<Button>(page,"applyFloor"));Save("operator-floor-recovered");
            check(page.State is {ManualFloor:"1",ObservedFloor:null,Context.FloorId:"1",NativeGeometryVerified:true,IdentityLocked:false}
                && page.State.RemainingMapIds.Count==30 && page.ReadRender() is not null,
                "actual-floor-apply-button-recovers-current-fit-without-turning-an-operator-floor-declaration-into-permanent-index-exclusions");
            capture.TransformPixels=null;
            check(page.SessionView!.StaleResults==retiredAfterClose && !page.SessionView.Runtime.LiveAuthorization,
                "subsequent-WPF-identity-and-mode-changes-add-no-unexpected-retired-results-or-native-live-grants");
            await File.WriteAllTextAsync(Path.Combine(output,"s0-ui-session-completions.json"),JsonSerializer.Serialize(consumed));
        }
        finally{await page.CloseAsync();window.Close();}

        async Task Click(Button button)
        {
            await Until(()=>button.IsEnabled);button.RaiseEvent(new(Button.ClickEvent));
            // The real acquire button owns a three-second async countdown
            // before it submits Session work; draining the lane alone is early.
            await Until(()=>Field<CancellationTokenSource?>(page,"pendingCapture") is null);
            await page.DrainAsync();
        }
        async Task Dispatch(string action){page.Dispatch(action);await page.DrainAsync();}
        void Save(string label,bool pixels=false)
        {
            page.UpdateLayout();
            evidence.Add(JsonSerializer.SerializeToNode(new {label,state=page.State,session=page.SessionView,
                identity=Field<TextBlock>(page,"mapIdentity").Text,caption=Field<TextBlock>(page,"candidateCaption").Text,
                detail=Field<TextBlock>(page,"detail").Text,rows=Field<ListBox>(page,"candidates").Items.Count,available=page.ReadRender() is not null}));
            File.WriteAllText(Path.Combine(output,"s0-index-workspace.json"),evidence.ToJsonString(new JsonSerializerOptions{WriteIndented=true}));
            if(!pixels)return;
            var bitmap=new RenderTargetBitmap((int)Math.Ceiling(page.ActualWidth),(int)Math.Ceiling(page.ActualHeight),96,96,PixelFormats.Pbgra32);
            var visual=new DrawingVisual();using(var drawing=visual.RenderOpen())
            {var bounds=new Rect(0,0,page.ActualWidth,page.ActualHeight);drawing.DrawRectangle(window.Background,null,bounds);
                drawing.DrawRectangle(new VisualBrush(page),null,bounds);}
            bitmap.Render(visual);var encoder=new PngBitmapEncoder();encoder.Frames.Add(BitmapFrame.Create(bitmap));
            using var stream=File.Create(Path.Combine(output,"s0-ui-"+label+".png"));encoder.Save(stream);
        }
    }
    static async Task Until(Func<bool> ready)
    {var started=System.Diagnostics.Stopwatch.StartNew();while(!ready()){if(started.Elapsed>TimeSpan.FromSeconds(10))throw new TimeoutException("S0 WPF control did not settle.");await Task.Delay(10);}}
    static T Field<T>(object owner,string name)=>(T)owner.GetType().GetField(name,BindingFlags.Instance|BindingFlags.NonPublic)!.GetValue(owner)!;
}
