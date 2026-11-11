using System.IO;
using System.Reflection;
using System.Text.Json;
using System.Text.Json.Nodes;
using System.Windows.Threading;
using StarMap.App;
using StarMap.App.Platform;
using StarMap.Core.Input;
using StarMap.Core.Identity;
using StarMap.Core.Session;
using StarMap.Core.Settings;
using StarMap.Runtime;

internal static class PassiveRefreshWorkspaceRegression
{
    internal static async Task Run(string resources, string originalReplay, string output, Action<bool, string> check)
    {
        var index = JsonNode.Parse(await File.ReadAllBytesAsync(Path.Combine(originalReplay, "all-samples.json")))!["samples"]!.AsArray()
            .ToDictionary(row => row!["id"]!.GetValue<string>(), row => row!.AsObject());
        var capture = new RecordedCapture(index); capture.Select("batch01-event-000006", false);
        var page = new RuntimePage(capture, _ => { });
        using var native = new WindowsRuntimeCapturePort();
        RuntimeOverlayHost? host = null;
        System.Windows.Window? pickerWindow=null;
        try
        {
            check(await page.Configure(resources, SettingsDocument.CreateNew()), "passive-workspace-binds-real-resources-through-session");
            page.Dispatch("start_round"); await page.DrainAsync();
            page.HandleShortcut("map"); await page.DrainAsync();
            var runtime = Field<LiveRuntime>(page, "runtime"); var session = Field<SessionService>(page, "session");
            var input = Field<OrderedMapInput>(page, "mapInput"); var initial = page.ReadRender();
            check(!page.IsVisible && initial is not null && input.OpenIntent && capture.Calls == 1,
                "passive-workspace-real-map-shortcut-establishes-hidden-current-observation");
            check(!page.State!.IdentityLocked && !page.RememberMaterialReady(initial!) && page.ReadMaterialToRemember() is null,
                "default-provisional-WPF-material-cannot-establish-0059-learning-before-human-identity");
            // Positive cache/timer assertions need an actual chosen reference;
            // the default index preview deliberately has no learning authority.
            page.Dispatch("confirm_route",new {mapId="nightmare-12"});await page.DrainAsync();initial=page.ReadRender();
            check(page.State is {IdentityLocked:true,IdentitySource:"manual",NativeGeometryVerified:true}
                && initial is not null,"passive-cache-test-establishes-its-reference-through-real-human-confirmation");
            host = new(Dispatcher.CurrentDispatcher, page.ReadRender, native, _ => { }, visible: () => false,
                readPendingMaterial: page.ReadPendingGroupMaterial, acceptMaterialReady: page.AcceptGroupMaterialReady,
                rejectMaterial: page.RejectGroupMaterial, readMaterialToRemember: page.ReadMaterialToRemember,
                rememberMaterialReady: page.RememberMaterialReady, observeDisplayContext: () => { page.ObserveDisplayContext(); });
            await Until(() => Field<string?>(runtime, "_rememberedMaterial") == initial!.MaterialToken && page.SessionView?.WorkerActive == false);
            check(page.ReadMaterialToRemember() is null, "passive-workspace-real-WPF-material-ready-and-session-maintenance-establish-0059-prerequisite");
            int before = capture.Calls;
            await Task.Delay(250);
            check(capture.Calls == before, "recorded-workspace-passive-timer-is-inert-without-explicit-foreground-admission");
            var hold = capture.HoldNext(); capture.Foreground = true;
            try
            {
                await hold.Started.Task.WaitAsync(TimeSpan.FromSeconds(10));
                session.RefreshRuntime(); page.ObserveOpenMap(); page.ObserveOpenMap();
                check(page.SessionView is { Busy: true, WorkerActive: true, QueuedCount: 0, StaleResults: 0 }
                    && page.State!.MapOpen && ReferenceEquals(page.ReadRender(), initial) && capture.Calls == before+1,
                    "hidden-passive-timer-preserves-owned-observation-progress-and-coalesces-busy-ticks");
            }
            finally { capture.Foreground = false; hold.Release.TrySetResult(); }
            await page.DrainAsync();
            var refreshed = page.ReadRender();
            check(page.State is { NativeGeometryVerified: true, Reason: "idvb0059-steady", ProbeMisses: 0 }
                && refreshed is not null && !ReferenceEquals(refreshed, initial) && capture.Calls == before+1,
                "hidden-timer-consumes-the-real-default-0059-fit-and-publishes-new-recorded-canonical-material");
            await Until(() => Field<string?>(runtime, "_rememberedMaterial") == refreshed!.MaterialToken && page.SessionView?.WorkerActive == false);
            capture.Select("batch01-event-000002", false);
            page.Dispatch("refresh_map"); await page.DrainAsync();
            check(page.State is { MapOpen: true, ProbeMisses: 1, NativeGeometryVerified: false }
                && page.ReadRender() is null && input.OpenIntent,
                "first-negative-passive-frame-revokes-material-without-changing-physical-open-intent");
            long sequence = input.Sequence;
            page.Dispatch("refresh_map"); await page.DrainAsync();
            check(page.State is { MapOpen: false, ProbeMisses: 2 } && !input.OpenIntent && input.Sequence == sequence,
                "second-negative-confirms-close-and-reconciles-the-same-input-sequence");
            capture.Select("batch01-event-000006", false);
            page.HandleShortcut("map"); await page.DrainAsync();
            check(input.OpenIntent && input.Sequence == sequence+1 && page.State is { MapOpen: true, NativeGeometryVerified: true }
                && page.ReadRender() is not null,
                "physical-G-after-passive-close-reopens-and-captures-instead-of-being-misread-as-close");
            var geometry = page.ReadRender()!.Client!.Geometry;
            capture.GeometryOverride = geometry with { Left = geometry.Left+64, Top = geometry.Top+40 };
            await Until(() => page.State?.Reason == "capture-context-changed");
            before = capture.Calls; capture.GeometryOverride = null; await Task.Delay(250);
            check(page.State is { MapOpen: false, NativeGeometryVerified: false } && page.ReadRender() is null && capture.Calls == before,
                "real-hidden-overlay-observer-retires-lost-context-and-old-pixels-stay-revoked-when-geometry-returns");
            await File.WriteAllTextAsync(Path.Combine(output, "passive-refresh-workspace.json"), JsonSerializer.Serialize(new {
                state = page.State, session = page.SessionView, capture.Calls, input.Sequence, input.OpenIntent,
                scope = "Default provisional material has no learning authority; positive timer/cache flow follows explicit human reference selection through real Session/Runtime. Recorded foreground/capture seams only; no native capture/input/game."
            }, new JsonSerializerOptions { WriteIndented = true }));
            host.Dispose(); host = null;
            page.Dispatch("end_round"); await page.DrainAsync();
            check(await page.Configure(resources, new SettingsDocument(new JsonObject { ["gameSeason"] = "s1", ["gameMode"] = "nightmare" })),
                "passive-workspace-switches-to-actual-S1-resource-context");
            page.Dispatch("start_round"); await page.DrainAsync();
            capture.Select("batch02-event-000319", false); page.HandleShortcut("map"); await page.DrainAsync();
            var candidate=page.ReadRender();before=capture.Calls;var candidateHold=capture.HoldNext();capture.Foreground=true;
            try
            {
                await candidateHold.Started.Task.WaitAsync(TimeSpan.FromSeconds(10));
                page.ObserveOpenMap();page.ObserveOpenMap();
                check(page.SessionView is {WorkerActive:true,QueuedCount:0,PendingAction:"observe_candidate_floor"}
                    && capture.Calls==before+1 && ReferenceEquals(candidate,page.ReadRender()),
                    "hidden-S1-timer-dispatches-dedicated-candidate-probe-and-coalesces-busy-ticks");
            }
            finally{capture.Foreground=false;candidateHold.Release.TrySetResult();}
            await page.DrainAsync();
            check(page.State is {MapOpen:true,Reason:"candidate-floor-current"} && ReferenceEquals(candidate,page.ReadRender()),
                "hidden-S1-same-floor-probe-retains-exact-material-without-S0-refresh-or-pose-renewal");
            var picker=Field<System.Windows.Controls.ComboBox>(page,"manualMap");
            var pickButton=Field<System.Windows.Controls.Button>(page,"pick");
            // Standalone RuntimePage receives the same real map list through
            // the production host API. Show only an offscreen, nonactivating
            // test window so its normal visible-content reconciliation runs.
            page.SetMaps(EntryPassportCatalog.Load(resources,"s1","nightmare").MapProfiles.Keys);
            pickerWindow=new(){Content=page,Left=-20000,Top=-20000,Width=900,Height=900,
                ShowActivated=false,ShowInTaskbar=false,WindowStartupLocation=System.Windows.WindowStartupLocation.Manual};
            pickerWindow.Show();
            if(picker.Parent is System.Windows.Controls.StackPanel manualPanel && manualPanel.Parent is System.Windows.Controls.Expander manualExpander)
                manualExpander.IsExpanded=true;
            await Until(()=>picker.Items.Cast<string>().Contains("107012") && pickButton.IsVisible && pickButton.IsEnabled);
            check(picker.Items.Count==EntryPassportCatalog.Load(resources,"s1","nightmare").Maps.Count,
                "picker-is-loaded-from-real-resources-through-the-production-visible-page-host-contract");
            picker.SelectedItem="107012";capture.Select("batch02-event-000323",false);
            var confirmationHold=capture.HoldNext();before=capture.Calls;
            pickButton.RaiseEvent(new(System.Windows.Controls.Button.ClickEvent));
            try
            {
                await confirmationHold.Started.Task.WaitAsync(TimeSpan.FromSeconds(10));
                check(page.State is {IdentityLocked:true,IdentitySource:"manual",Context.MapId:"107012",WorkActive:true}
                    && page.SessionView?.PendingAction=="confirm_route" && page.ReadRender() is null && capture.Calls==before+1,
                    "actual-map-picker-click-publishes-identity-before-its-blocked-fresh-capture");
            }
            finally{confirmationHold.Release.TrySetResult();}
            await page.DrainAsync();
            check(page.State?.Context.FloorId=="-2" && page.ReadRender()?.Stamp!=candidate!.Stamp && input.OpenIntent,
                "picker-session-runtime-consumer-fits-the-new-layer-instead-of-the-retained-candidate-frame");
            await Until(()=>pickButton.IsEnabled);
            before=capture.Calls;long confirmationSequence=input.Sequence;capture.FailNext();
            pickButton.RaiseEvent(new(System.Windows.Controls.Button.ClickEvent));await page.DrainAsync();
            check(page.State is {MapOpen:true,IdentityLocked:true,Context.MapId:"107012",NativeGeometryVerified:false}
                && capture.Calls==before+1 && page.ReadRender() is null && input.OpenIntent && input.Sequence==confirmationSequence,
                "failed-picker-capture-retains-human-identity-and-physical-opening-without-old-pixels");
            await Until(()=>pickButton.IsEnabled);
            pickButton.RaiseEvent(new(System.Windows.Controls.Button.ClickEvent));await page.DrainAsync();
            check(page.ReadRender() is not null && page.State?.Context.FloorId=="-2" && input.Sequence==confirmationSequence,
                "picker-retries-fresh-capture-after-failure-without-an-extra-G-edge");
            check(page.State is {ManualAlignmentSession:false} && runtime.ReadSnapshot().AvailableCommands.Contains("observe_candidate_floor"),
                "ordinary-map-choice-preserves-the-ordinary-floor-policy-instead-of-creating-a-manual-session");
            await Until(()=>pickButton.IsEnabled && pickButton.IsVisible);
            capture.Select("batch02-event-000401",false);
            pickButton.RaiseEvent(new(System.Windows.Controls.Button.ClickEvent));await page.DrainAsync();
            check(page.State is {MapOpen:false,Reason:"map-not-observed",Context.MapId:"107012"}
                && !input.OpenIntent && input.Sequence==confirmationSequence && page.ReadRender() is null,
                "picker-fresh-negative-reconciles-the-existing-physical-G-edge-without-clearing-identity");
            capture.Select("batch02-event-000319",false);page.HandleShortcut("map");await page.DrainAsync();
            check(page.State?.MapOpen==true && input.OpenIntent && page.ReadRender() is not null,
                "G-after-picker-negative-opens-a-new-cycle-instead-of-being-misread-as-close");
            var endButton=Field<System.Windows.Controls.Button>(page,"end");
            var manualStart=Field<System.Windows.Controls.Button>(page,"manualStart");
            var fitManual=Field<System.Windows.Controls.Button>(page,"fitManual");
            await Until(()=>endButton.IsEnabled);endButton.RaiseEvent(new(System.Windows.Controls.Button.ClickEvent));await page.DrainAsync();
            await Until(()=>manualStart.IsVisible && manualStart.IsEnabled);before=capture.Calls;
            manualStart.RaiseEvent(new(System.Windows.Controls.Button.ClickEvent));await page.DrainAsync();
            check(capture.Calls==before && page.State is {RoundActive:true,ManualAlignmentSession:true,MapOpen:false,Context.MapId:null,Context.FloorId:null},
                "actual-prepare-manual-button-starts-a-separate-empty-session-without-capture-or-floor-seed");
            await Until(()=>pickButton.IsEnabled);picker.SelectedItem="107012";capture.Select("batch02-event-000323",false);
            pickButton.RaiseEvent(new(System.Windows.Controls.Button.ClickEvent));await page.DrainAsync();
            check(capture.Calls==before+1 && page.State is {ManualAlignmentSession:true,MapOpen:true,Context.MapId:"107012",Context.FloorId:"-2"}
                && page.ReadRender() is not null && input.OpenIntent,
                "first-manual-picker-confirmation-captures-a-fresh-floor-without-any-retained-frame");
            pickerWindow.Hide();
            var manualHold=capture.HoldNext();before=capture.Calls;capture.Foreground=true;
            try
            {
                await manualHold.Started.Task.WaitAsync(TimeSpan.FromSeconds(10));
                check(page.SessionView?.PendingAction=="observe_manual_floor" && capture.Calls==before+1,
                    "hidden-S1-timer-uses-manual-observer-only-in-the-explicit-manual-session");
            }
            finally{capture.Foreground=false;manualHold.Release.TrySetResult();}
            await page.DrainAsync();
            check(page.ReadRender() is { } manual && manual.Stamp!=candidate!.Stamp && page.State?.Context.MapId=="107012",
                "manual-S1-observer-current-pixels-reach-real-WPF-workspace-consumer");
            pickerWindow.Hide();
            capture.Select("batch02-event-000401",false);capture.Foreground=true;
            try{await Until(()=>page.State?.MapOpen==false);}finally{capture.Foreground=false;}
            await page.DrainAsync();
            check(page.State is {Reason:"s1-map-closed",Context.MapId:"107012"} && !input.OpenIntent && page.ReadRender() is null,
                "single-S1-negative-reconciles-the-physical-G-edge-and-retains-manual-identity");
            pickerWindow.Show();await Until(()=>fitManual.IsVisible && fitManual.IsEnabled);
            capture.Select("batch02-event-000323",false);before=capture.Calls;
            fitManual.RaiseEvent(new(System.Windows.Controls.Button.ClickEvent));await page.DrainAsync();
            check(capture.Calls==before+1 && page.State is {ManualAlignmentSession:true,MapOpen:true,Context.FloorId:"-2"}
                && input.OpenIntent && page.ReadRender() is not null,
                "actual-fit-current-frame-button-restores-a-fresh-manual-opening-after-passive-close");
            pickerWindow.Hide();before=capture.Calls;page.HandleShortcut("map");await page.DrainAsync();
            check(capture.Calls==before && !input.OpenIntent && page.State is {ManualAlignmentSession:true,MapOpen:false,Context.MapId:"107012"},
                "manual-session-G-close-preserves-mode-and-selected-identity-without-acquiring-pixels");
            capture.Select("batch02-event-000319",false);page.HandleShortcut("map");await page.DrainAsync();
            check(input.OpenIntent && page.State is {ManualAlignmentSession:true,MapOpen:true,Context.FloorId:"-1"}
                && page.SessionView?.LastReceipt?.Action=="fit_manual_frame" && page.ReadRender() is not null,
                "manual-session-G-open-routes-through-fit-manual-frame-and-observes-the-new-floor");
            await page.CloseAsync(); before = capture.Calls; capture.Foreground = true; await Task.Delay(250);
            check(capture.Calls == before && page.State is null, "closed-background-observation-timer-does-not-acquire-another-frame");
        }
        finally { if(pickerWindow is not null){pickerWindow.Content=null;pickerWindow.Close();}host?.Dispose(); await page.CloseAsync(); }
    }

    static T Field<T>(object owner, string name) => (T)owner.GetType().GetField(name, BindingFlags.Instance | BindingFlags.NonPublic)!.GetValue(owner)!;
    static async Task Until(Func<bool> condition)
    {
        var clock = System.Diagnostics.Stopwatch.StartNew();
        while (!condition())
        {
            if (clock.Elapsed > TimeSpan.FromSeconds(10)) throw new TimeoutException("Passive refresh WPF consumer did not settle.");
            await Task.Delay(10);
        }
    }
}
