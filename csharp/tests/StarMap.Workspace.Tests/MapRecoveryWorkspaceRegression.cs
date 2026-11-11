using System.IO;
using System.Reflection;
using System.Text.Json;
using System.Text.Json.Nodes;
using System.Windows.Threading;
using StarMap.App;
using StarMap.Core.Session;
using StarMap.Core.Settings;

internal static class MapRecoveryWorkspaceRegression
{
    internal static async Task Run(string resources, string originalReplay, string output, Action<bool, string> check)
    {
        var index = JsonNode.Parse(await File.ReadAllBytesAsync(Path.Combine(originalReplay, "all-samples.json")))!["samples"]!.AsArray()
            .ToDictionary(row => row!["id"]!.GetValue<string>(), row => row!.AsObject());
        var capture = new RecordedCapture(index) { Foreground = true }; capture.Select("batch01-event-000002");
        var page = new RuntimePage(capture, _ => { });
        var timer = (DispatcherTimer)typeof(RuntimePage).GetField("observationTimer", BindingFlags.Instance | BindingFlags.NonPublic)!.GetValue(page)!;
        try
        {
            check(!timer.IsEnabled && capture.Calls == 0, "recovery-construction-does-not-capture-or-start-a-timer");
            check(await page.Configure(resources, SettingsDocument.CreateNew()), "recovery-workspace-binds-real-resources-through-session");
            page.Dispatch("start_round"); await page.DrainAsync();
            check(!page.IsVisible && timer.IsEnabled && capture.Calls == 0, "recovery-pulse-is-independent-of-page-visibility-and-idle-does-not-capture");
            var consumed = new List<SessionCompletion>();
            bool recoveryRetiredAtCollection = false;
            var session = (SessionService)typeof(RuntimePage).GetField("session", BindingFlags.Instance | BindingFlags.NonPublic)!.GetValue(page)!;
            session.ResultConsumed += completion =>
            {
                consumed.Add(completion);
                // This assertion measures recovery, not the next legitimate
                // passive refresh. Model foreground loss at receipt collection;
                // keep the actual observer timer running throughout.
                if (completion is { Action: "recover_map_open", Disposition: "Accepted" })
                {
                    recoveryRetiredAtCollection = page.State?.MapOpenRecovery is { Pending: false, Id: null };
                    capture.Foreground = false;
                }
            };
            page.HandleShortcut("map"); await page.DrainAsync();
            check(page.State!.MapOpenRecovery.Pending && page.State.MapOpenRecovery.Remaining == 8,
                "real-WPF-shortcut-reaches-preparation-and-arms-recovery-through-session");
            int preparedCalls = capture.Calls;
            capture.Select("batch01-event-000006", invalidateBinding: false);
            var deadline = System.Diagnostics.Stopwatch.StartNew();
            while (page.State?.NativeGeometryVerified != true || page.SessionView?.WorkerActive == true)
            {
                if (deadline.Elapsed > TimeSpan.FromSeconds(5)) throw new TimeoutException("Hidden recovery did not settle.");
                await Task.Delay(10);
            }
            await Task.Delay(250);
            await File.WriteAllTextAsync(Path.Combine(output, "map-recovery-boundary.json"), JsonSerializer.Serialize(new {
                preparedCalls, captureCalls = capture.Calls, state = page.State, session = page.SessionView,
                renderMap = page.ReadRender()?.MapId, consumed, foreground = capture.Foreground, recoveryRetiredAtCollection
            }, new JsonSerializerOptions { WriteIndented = true }));
            check(!capture.Foreground && timer.IsEnabled, "recovery-counter-is-observed-after-foreground-loss-with-the-production-timer-still-running");
            check(recoveryRetiredAtCollection && page.State?.MapOpenRecovery is { Pending: false, Id: null },
                "successful-recovery-retires-its-ticket-before-foreground-loss-and-keeps-it-retired-after-settlement");
            var recovered=page.State!;var material=page.ReadRender();
            check(!page.IsVisible && capture.Calls == preparedCalls+1
                && recovered is {IdentityLocked:false,RecognitionPath:"structure-index",Context.MapId:null}
                && recovered.RemainingMapIds.Count==26 && recovered.Display.CanonicalVerified
                && material is {IsLive:false} && material.MapId==recovered.ProvisionalMapId
                && recovered.RemainingMapIds.Contains(material.MapId) && material.Stamp.FrameId==recovered.Context.FrameId,
                "hidden-page-timer-recovers-one-current-index-candidate-without-manufacturing-identity");
            check(consumed.Count(row => row.Action == "recover_map_open" && row.Disposition == "Accepted") == 1
                && page.SessionView is { Busy: false, WorkerActive: false, StaleResults: 0 },
                "recovery-result-is-collected-through-the-real-session-without-stale-rebinding");
            await File.WriteAllTextAsync(Path.Combine(output, "map-recovery-workspace.json"), JsonSerializer.Serialize(new {
                state = page.State, session = page.SessionView, captureCalls = capture.Calls, consumed,
                scope = "Actual hidden WPF page/DispatcherTimer/Session/Runtime and recorded 2026-11-09 PNGs; no Show, native input, capture or game acceptance."
            }, new JsonSerializerOptions { WriteIndented = true }));
            page.HandleShortcut("map"); await page.DrainAsync();
            capture.Foreground = true;
            capture.Select("batch01-event-000002", invalidateBinding: false);
            page.HandleShortcut("map"); await page.DrainAsync();
            check(page.State!.MapOpenRecovery.Pending, "second-physical-opening-gets-a-new-bounded-ticket");
            await page.CloseAsync(); int closedCalls = capture.Calls;
            await Task.Delay(250);
            check(!timer.IsEnabled && capture.Calls == closedCalls && page.State is null,
                "closing-the-hidden-page-stops-recovery-and-drains-its-runtime-without-a-late-capture");
        }
        finally { await page.CloseAsync(); }
    }
}
