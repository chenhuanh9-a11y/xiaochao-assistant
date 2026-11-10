using System.IO;
using System.Reflection;
using System.Text.Json.Nodes;
using System.Windows;
using System.Windows.Controls;
using StarMap.App;
using StarMap.Core.Identity;
using StarMap.Core.Settings;
using StarMap.Runtime;
using StarMap.Vision;

internal static class ManualLearningWorkspaceRegression
{
    internal static async Task Run(string resources,string replay,string output,Action<bool,string> check)
    {
        var index=JsonNode.Parse(await File.ReadAllBytesAsync(Path.Combine(replay,"all-samples.json")))!["samples"]!.AsArray()
            .ToDictionary(row=>row!["id"]!.GetValue<string>(),row=>row!.AsObject());
        // Recorded pixels explicitly simulate the trusted capture composition;
        // this is not live/game acceptance. No input or native capture is used.
        var capture=new RecordedCapture(index){EvidenceKind=CaptureEvidenceKind.LiveClient};capture.Select("batch01-event-000006",false);
        var data=new ExperimentalDataRoot(Path.Combine(output,"manual-label-workspace"));
        var page=new RuntimePage(capture,_=>{},data);
        var window=new Window {Content=page,Left=-20000,Top=-20000,Width=900,Height=900,ShowActivated=false,ShowInTaskbar=false,WindowStartupLocation=WindowStartupLocation.Manual};
        try
        {
            check(await page.Configure(resources,SettingsDocument.CreateNew()),"manual-label-WPF-configures-the-explicit-isolated-data-root");
            page.Dispatch("start_round");await page.DrainAsync();page.HandleShortcut("map");await page.DrainAsync();
            check(!Directory.Exists(data.Path),"automatic-live-composition-fit-does-not-write-a-human-label");
            var catalog=EntryPassportCatalog.Load(resources,"s0","nightmare");page.SetMaps(catalog.MapProfiles.Keys);
            var picker=Field<ComboBox>(page,"manualMap");var button=Field<Button>(page,"pick");
            window.Show();
            if(picker.Parent is StackPanel panel && panel.Parent is Expander expander)expander.IsExpanded=true;
            await Until(()=>picker.Items.Cast<string>().Contains("nightmare-12") && button.IsVisible && button.IsEnabled);
            picker.SelectedItem="nightmare-12";button.RaiseEvent(new(Button.ClickEvent));await page.DrainAsync();
            var store=new ManualConfirmationMemoryStore(data,catalog);
            check(page.State is {IdentityLocked:true,NativeGeometryVerified:true,ManualLearning.Status:"saved"}
                && store.Records().Count==1 && page.ReadRender() is not null,
                "actual-WPF-picker-Session-Runtime-persists-the-human-label-without-using-it-as-pose-authority");
            await Until(()=>button.IsEnabled);
            using(var locked=new FileStream(store.LogPath,FileMode.Open,FileAccess.ReadWrite,FileShare.None))
            {button.RaiseEvent(new(Button.ClickEvent));await page.DrainAsync();}
            check(page.State is {IdentityLocked:true,NativeGeometryVerified:true,ManualLearning.Status:"error",ManualLearning.Saved:false}
                && page.ReadRender() is not null && store.Records().Count==1 && Field<TextBlock>(page,"detail").Text.Contains("人工标签保存失败"),
                "actual-locked-label-log-retains-WPF-pose-and-shows-the-independent-write-error");
        }
        finally{window.Content=null;window.Close();await page.CloseAsync();}
    }
    static T Field<T>(object owner,string name)=>(T)owner.GetType().GetField(name,BindingFlags.Instance|BindingFlags.NonPublic)!.GetValue(owner)!;
    static async Task Until(Func<bool> ready)
    {
        var clock=System.Diagnostics.Stopwatch.StartNew();
        while(!ready()){if(clock.Elapsed>TimeSpan.FromSeconds(10))throw new TimeoutException("manual-label picker did not settle");await Task.Delay(10);}
    }
}
