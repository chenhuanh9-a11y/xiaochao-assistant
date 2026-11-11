using System.IO;
using System.Reflection;
using System.Text.Json;
using System.Windows;
using System.Windows.Interop;
using System.Windows.Threading;
using StarMap.App;

internal static class ManualMapPickerPlacementWorkspaceRegression
{
    internal static async Task Run(string output,Action<bool,string> check)
    {
        var cases=new[]
        {
            (new PickerPhysicalRect(-1800,100,1000,600),new PickerPhysicalRect(-1920,0,1920,1040),930,540,(-1765,130)),
            (new PickerPhysicalRect(-1000,-1000,1000,800),new PickerPhysicalRect(0,40,1920,1000),620,360,(0,40)),
            (new PickerPhysicalRect(1800,900,1200,800),new PickerPhysicalRect(0,0,1920,1040),620,400,(1300,640)),
            (new PickerPhysicalRect(0,0,1920,1040),new PickerPhysicalRect(0,0,1920,1040),930,540,(495,250))
        };
        foreach(var row in cases)check(ManualMapPickerPlacement.Plan(row.Item1,row.Item2,row.Item3,row.Item4)==row.Item5,
            "physical-picker-planner-preserves-negative-origin-and-work-area-boundaries-"+row.Item5);
        var window=new ManualMapPickerWindow {Opacity=0,Left=-20000,Top=-20000};
        var shell=new WorkspaceWindow(Path.Combine(output,"picker-placement-shell"),null);
        try
        {
            window.Render("窗口定位检查","仅检查自有不可见窗口的原生几何。","","",[],false);
            window.Show();window.UpdateLayout();
            var hwnd=new WindowInteropHelper(window).Handle;
            var before=ManualMapPickerPlacement.ReadWindow(hwnd);
            // The actual production Workspace callback, with no bound game.
            typeof(WorkspaceWindow).GetMethod("PlaceManualPicker",BindingFlags.Instance|BindingFlags.NonPublic)!.Invoke(shell,[window]);
            var placement=(ManualMapPickerPlacement)typeof(WorkspaceWindow).GetField("pickerPlacement",BindingFlags.Instance|BindingFlags.NonPublic)!.GetValue(shell)!;
            await placement.DrainAsync();
            var work=ManualMapPickerPlacement.PrimaryWorkArea();var primary=ManualMapPickerPlacement.ReadWindow(hwnd);
            check(placement.LastError is null && Within(primary,work) && Math.Abs(window.Width-620)<0.01,
                "actual-Workspace-placement-callback-clamps-owned-transparent-window-to-primary-work-area-without-resizing");
            // Only the client rectangle is simulated. Monitor queries, WPF
            // Loaded layout, native own-HWND reads/moves and cancellation run.
            placement.Request(new(-400,-300,1100,800));
            placement.Request(new(0,0,300,200));await placement.DrainAsync();
            var small=ManualMapPickerPlacement.ReadWindow(hwnd);var expected=ManualMapPickerPlacement.Plan(work,work,small.Width,small.Height);
            check(placement.LastError is null && (small.Left,small.Top)==expected && Within(small,work)
                && small.Width==primary.Width && small.Height==primary.Height,
                "latest-small-client-request-falls-back-once-to-primary-and-preserves-own-native-size");
            placement.Request(new(-1000,-1000,1600,1000));await placement.DrainAsync();
            var offscreen=ManualMapPickerPlacement.ReadWindow(hwnd);
            check(placement.LastError is null && Within(offscreen,work),
                "production-native-placement-clamps-an-explicitly-offscreen-client-to-reachable-work-area");
            placement.Request(new(0,0,300,200));window.Hide();await placement.DrainAsync();
            await Dispatcher.Yield(DispatcherPriority.Background);
            check(!window.IsVisible && placement.LastError is null,"hidden-picker-cancels-queued-placement-and-is-never-reshown");
            await File.WriteAllTextAsync(Path.Combine(output,"manual-picker-native-placement.json"),JsonSerializer.Serialize(new
            {before,work,primary,small,offscreen,window.Width,scope="Actual own-HWND geometry and Workspace callback; Opacity0 prevents visible desktop disruption. Client rectangles explicitly simulated. No game HWND, capture/input, physical visible pixels or mixed-DPI monitor acceptance."},new JsonSerializerOptions{WriteIndented=true}));
        }
        finally {window.Dispose();await window.DrainImagesAsync();shell.Close();}
    }
    static bool Within(PickerPhysicalRect window,PickerPhysicalRect work)=>window.Left>=work.Left && window.Top>=work.Top
        && window.Right<=work.Right && window.Bottom<=work.Bottom;
}
