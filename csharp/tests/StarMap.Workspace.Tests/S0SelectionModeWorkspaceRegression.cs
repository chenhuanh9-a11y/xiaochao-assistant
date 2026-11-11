using System.IO;
using System.Reflection;
using System.Text.Json;
using System.Text.Json.Nodes;
using System.Windows;
using System.Windows.Controls;
using StarMap.App;
using StarMap.Core.Resources;
using StarMap.Core.Settings;

internal static class S0SelectionModeWorkspaceRegression
{
    internal static async Task RunFirstSave(string resources,string replay,string output,Action<bool,string> check)
    {
        var samples=JsonNode.Parse(await File.ReadAllBytesAsync(Path.Combine(replay,"all-samples.json")))!["samples"]!.AsArray()
            .ToDictionary(row=>row!["id"]!.GetValue<string>(),row=>row!.AsObject());
        await FirstPreferenceSave(resources,samples,output,check);
    }

    internal static async Task Run(string resources,string replay,string output,Action<bool,string> check)
    {
        var samples=JsonNode.Parse(await File.ReadAllBytesAsync(Path.Combine(replay,"all-samples.json")))!["samples"]!.AsArray()
            .ToDictionary(row=>row!["id"]!.GetValue<string>(),row=>row!.AsObject());
        var capture=new RecordedCapture(samples);capture.Select("batch02-event-000147",false);
        var page=new RuntimePage(capture,_=>{},manualPickerWindowFactory:()=>new(){Left=-20000,Top=-20000,
            WindowStartupLocation=WindowStartupLocation.Manual});
        var host=new Window {Content=page,Left=-20000,Top=-20000,Width=920,Height=1100,ShowActivated=false,ShowInTaskbar=false};
        var picker=Field<ManualMapPickerService>(page,"manualPicker");var evidence=new JsonArray();
        try
        {
            check(await page.Configure(resources,Settings("manual")),"manual-mode-page-configures-through-real-session");
            // Workspace.ConfigureRuntime binds the inline map list only after
            // Configure accepts the resource context. Reproduce that consumer.
            var catalog=await Task.Run(()=>ResourceCatalog.Load(resources));
            page.SetMaps(catalog.Entries.Where(entry=>entry.Season=="s0" && entry.Mode=="difficult").Select(entry=>entry.MapId));
            host.Show();page.Dispatch("start_round");await page.DrainAsync();
            page.HandleShortcut("map");await page.DrainAsync();Save("manual-open");
            check(page.State is {MapSelectionMode:"manual",ManualSelectionPending:true,MapOpen:true,Context.FloorId:"1",IdentityLocked:false}
                && page.State.Candidates.Count==0 && page.ReadRender() is null && !picker.Visible
                && Field<TextBlock>(page,"mapIdentity").Text=="等待手动选图"
                && Field<TextBlock>(page,"detail").Text=="地图和楼层已读取，请手动选择本局地图。",
                "actual-map-input-reads-manual-floor-and-waiting-copy-without-auto-picker-or-reference");
            Field<Button>(page,"openPicker").RaiseEvent(new(Button.ClickEvent));await picker.DrainAsync();
            check(picker.Visible,"manual-map-button-opens-current-catalog");
            check(await page.Configure(resources,Settings("auto"),selectionModeOnly:true) && !picker.Visible && page.State is {MapSelectionMode:"auto",IdentityLocked:false},
                "actual-manual-to-auto-settings-application-closes-transient-picker");
            page.Dispatch("capture_now");await page.DrainAsync();Save("automatic-preview");
            check(page.State is {RecognitionPath:"structure-index",ManualSelectionPending:false} && page.State.RemainingMapIds.Count==30
                && page.ReadRender() is not null,"automatic-settings-application-resumes-real-index-without-starting-another-round");
            string round=page.State!.Context.RoundId!;
            check(await page.Configure(resources,Settings("manual"),selectionModeOnly:true) && page.State is {MapSelectionMode:"manual",ManualSelectionPending:true}
                && page.State.Context.RoundId==round && page.State.RemainingMapIds.Count==30 && page.ReadRender() is null,
                "actual-auto-to-manual-settings-application-retires-unconfirmed-preview-and-keeps-round-pool");
            var maps=Field<ComboBox>(page,"manualMap");maps.SelectedItem="difficult-map-15";
            Field<Button>(page,"pick").RaiseEvent(new(Button.ClickEvent));await page.DrainAsync();Save("chosen-reference");
            check(page.State is {IdentityLocked:true,Context.MapId:"difficult-map-15",NativeGeometryVerified:true,ManualSelectionPending:false}
                && page.ReadRender() is not null,"existing-manual-confirm-control-locks-and-fits-the-explicit-reference-in-manual-mode");
            var chosen=page.State!;
            check(await page.Configure(resources,Settings("auto"),selectionModeOnly:true) && page.State!.IdentityLocked
                && page.State.Context.MapId==chosen.Context.MapId && page.State.Context.RoundId==round
                && page.State.NativePose==chosen.NativePose && page.ReadRender() is not null,
                "actual-preference-apply-preserves-chosen-identity-and-own-floor-pose");
            var beforeFit=page.State!;var knownHold=capture.HoldNext(ignoreCancellation:true);Task<bool>? knownApply=null;
            page.Dispatch("capture_now");
            try
            {
                await knownHold.Started.Task.WaitAsync(TimeSpan.FromSeconds(10));
                knownApply=page.Configure(resources,Settings("manual"),selectionModeOnly:true);
            }
            finally {knownHold.Release.TrySetResult();}
            bool knownApplied=await knownApply!;await page.DrainAsync();Save("chosen-fit-during-policy-change");
            check(knownApplied && page.State is {MapSelectionMode:"manual",IdentityLocked:true,NativeGeometryVerified:true,MapOpen:true}
                && page.State.Context.MapId==beforeFit.Context.MapId && page.State.Context.FrameId!=beforeFit.Context.FrameId
                && page.ReadRender() is not null,
                "actual-preference-only-application-consumes-in-flight-chosen-map-fit-and-its-new-frame");
            page.Dispatch("end_round");await page.DrainAsync();page.Dispatch("start_round");await page.DrainAsync();
            check(await page.Configure(resources,Settings("auto"),selectionModeOnly:true),"next-unresolved-round-can-reapply-auto-before-late-result-test");
            var hold=capture.HoldNext(ignoreCancellation:true);Task<bool>? apply=null;
            page.Dispatch("capture_now");
            try
            {
                await hold.Started.Task.WaitAsync(TimeSpan.FromSeconds(10));
                apply=page.Configure(resources,Settings("manual"),selectionModeOnly:true);
            }
            finally {hold.Release.TrySetResult();}
            bool appliedLate=await apply!;await page.DrainAsync();Save("manual-after-late-settlement");
            check(appliedLate && page.State is {MapSelectionMode:"manual",IdentityLocked:false}
                && page.State.RemainingMapIds.Count==0 && page.ReadRender() is null,
                "settings-application-retires-a-deliberately-late-automatic-result-before-it-can-publish");
            page.HandleShortcut("map");await page.DrainAsync();Save("manual-after-late");
            check(page.State is {ManualSelectionPending:true,MapOpen:true} && page.ReadRender() is null && !picker.Visible,
                "fresh-map-input-after-policy-change-still-reaches-manual-observation-without-stale-material");
            await File.WriteAllTextAsync(Path.Combine(output,"s0-selection-mode-workspace.json"),evidence.ToJsonString(new JsonSerializerOptions{WriteIndented=true}));
        }
        finally {await page.CloseAsync();host.Close();}
        await FirstPreferenceSave(resources,samples,output,check);
        void Save(string label)
        {
            evidence.Add(JsonSerializer.SerializeToNode(new {label,state=page.State,capture.Calls,material=page.ReadRender() is not null,picker.Visible,
                manualMap=Field<ComboBox>(page,"manualMap").SelectedItem,manualMapCount=Field<ComboBox>(page,"manualMap").Items.Count}));
            File.WriteAllText(Path.Combine(output,"s0-selection-mode-workspace.json"),evidence.ToJsonString(new JsonSerializerOptions{WriteIndented=true}));
        }
    }

    static async Task FirstPreferenceSave(string resources,Dictionary<string,JsonObject> samples,string output,Action<bool,string> check)
    {
        string root=Path.Combine(output,"selection-first-save");var shell=new WorkspaceWindow(root,null);
        var capture=new RecordedCapture(samples);capture.Select("batch01-event-000006",false);
        var page=new RuntimePage(capture,_=>{});
        await Field<RuntimePage>(shell,"runtimePage").CloseAsync();
        typeof(WorkspaceWindow).GetField("runtimePage",BindingFlags.Instance|BindingFlags.NonPublic)!.SetValue(shell,page);
        var host=new Window {Content=page,Left=-20000,Top=-20000,Width=920,Height=1100,ShowActivated=false,ShowInTaskbar=false};
        try
        {
            await Invoke(shell,"LoadSettings");await Invoke(shell,"LoadResources",resources);host.Show();
            page.Dispatch("start_round");await page.DrainAsync();
            Field<ComboBox>(page,"manualMap").SelectedItem="nightmare-12";
            Field<Button>(page,"pick").RaiseEvent(new(Button.ClickEvent));await page.DrainAsync();
            page.HandleShortcut("map");await page.DrainAsync();
            await File.WriteAllTextAsync(Path.Combine(output,"selection-first-save-entry.json"),JsonSerializer.Serialize(new {state=page.State,capture.Calls,
                selected=Field<ComboBox>(page,"manualMap").SelectedItem,mapCount=Field<ComboBox>(page,"manualMap").Items.Count,
                pickEnabled=Field<Button>(page,"pick").IsEnabled,session=page.SessionView,settingsFile=File.Exists(Path.Combine(root,"ui-settings.json"))},new JsonSerializerOptions{WriteIndented=true}));
            check(page.State is {IdentityLocked:true,NativeGeometryVerified:true,Context.MapId:"nightmare-12"}
                && !File.Exists(Path.Combine(root,"ui-settings.json")),
                "actual-Workspace-can-select-and-fit-a-reference-before-its-first-settings-file");
            var before=page.State!;var hold=capture.HoldNext(ignoreCancellation:true);Task? saving=null;
            page.Dispatch("capture_now");
            try
            {
                await hold.Started.Task.WaitAsync(TimeSpan.FromSeconds(10));
                Field<ComboBox>(shell,"mapSelectionMode").SelectedValue="manual";
                saving=Invoke(shell,"SaveSettings");
                var timer=System.Diagnostics.Stopwatch.StartNew();
                while(!saving.IsCompleted && !Field<bool>(page,"configuring"))
                {
                    if(timer.Elapsed>TimeSpan.FromSeconds(10))throw new TimeoutException("First preference save did not reach the runtime");
                    await Task.Delay(10);
                }
            }
            finally {hold.Release.TrySetResult();}
            await saving!;await page.DrainAsync();
            await File.WriteAllTextAsync(Path.Combine(output,"selection-first-save-workspace.json"),JsonSerializer.Serialize(new {before,after=page.State,
                material=page.ReadRender() is not null,saved=JsonNode.Parse(await File.ReadAllBytesAsync(Path.Combine(root,"ui-settings.json")))},new JsonSerializerOptions{WriteIndented=true}));
            check(page.State is {MapSelectionMode:"manual",MapOpen:true,IdentityLocked:true,NativeGeometryVerified:true}
                && page.State.Context.MapId==before.Context.MapId && page.State.Context.FrameId!=before.Context.FrameId && page.ReadRender() is not null,
                "first-real-settings-save-keeps-the-known-map-fit-while-materializing-default-action-hotkeys");
        }
        finally {await page.CloseAsync();host.Close();shell.Close();}
    }

    static SettingsDocument Settings(string mode)=>new(new JsonObject {["gameSeason"]="s0",["gameMode"]="difficult",
        ["routeProfileId"]="shared-structure",["mapSelectionMode"]=mode});
    static T Field<T>(object owner,string name)=>(T)owner.GetType().GetField(name,BindingFlags.Instance|BindingFlags.NonPublic)!.GetValue(owner)!;
    static Task Invoke(object owner,string method,params object[] arguments)=>(Task)owner.GetType().GetMethod(method,BindingFlags.Instance|BindingFlags.NonPublic)!.Invoke(owner,arguments)!;
}
