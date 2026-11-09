using System.IO;
using System.Text.Json;
using System.Text.Json.Nodes;
using System.Windows.Media;
using System.Windows.Media.Imaging;
using StarMap.App;
using StarMap.Core.Settings;
using StarMap.Core.Input;
using StarMap.Runtime;

internal static class RuntimeWorkspaceRegression
{
    internal static async Task Run(string captureRecordPath, string resourceRoot, string output, Action<bool, string> check)
    {
        using var record = JsonDocument.Parse(File.ReadAllBytes(captureRecordPath));
        var capturedAt = record.RootElement.GetProperty("capturedAt").GetDateTimeOffset();
        if (capturedAt < new DateTimeOffset(2026, 11, 5, 0, 0, 0, TimeSpan.Zero))
            throw new InvalidDataException("Only current post-20261105 captures may be replayed.");
        string path = Path.Combine(Path.GetDirectoryName(captureRecordPath)!, record.RootElement.GetProperty("samplePath").GetString()!);
        using var stream = File.OpenRead(path);
        var decoded = BitmapFrame.Create(stream, BitmapCreateOptions.PreservePixelFormat, BitmapCacheOption.OnLoad);
        var bitmap = new FormatConvertedBitmap(decoded, PixelFormats.Bgra32, null, 0);
        int stride = bitmap.PixelWidth * 4;
        var bytes = new byte[stride * bitmap.PixelHeight]; bitmap.CopyPixels(bytes, stride, 0);
        var capture = new CurrentCapture(bytes, bitmap.PixelWidth, bitmap.PixelHeight, capturedAt);
        var messages = new List<string>();
        var page = new RuntimePage(capture, messages.Add);
        try
        {
            var ordered = new OrderedMapInput(() => 42);
            var edges = new[] { ordered.Press(), ordered.Press(), ordered.Press() };
            check(edges.Select(edge => edge.OpenIntent).SequenceEqual(new[] { true, false, true })
                && edges.Select(edge => edge.Sequence).SequenceEqual(new long[] { 1, 2, 3 }),
                "ordered-map-input-matches-original-open-close-open-edges");
            ordered.Reset(true);
            check(!ordered.Reconcile(false, 2) && ordered.Close().Sequence == 4,
                "ordered-map-input-reset-keeps-sequence-and-rejects-late-visual-state");
            ordered.Reset();
            check(ordered.Press() is { Sequence: 5, OpenIntent: true, CreatedAtSeconds: 42 },
                "ordered-map-input-reopens-with-new-sequence-after-reset");
            check(capture.Calls == 0 && page.State is null && !page.IsVisible, "runtime-page-construction-is-inert-and-hidden");
            check(await page.Configure(resourceRoot, SettingsDocument.CreateNew()), "runtime-page-binds-actual-context-through-session");
            check(capture.Calls == 0 && page.State is { RoundActive: false }, "runtime-page-resource-binding-does-not-capture-or-start");
            check(page.BeginResourceLoad() && page.Dispatch("start_round") is null && page.State is { RoundActive: false },
                "runtime-page-resource-lease-prevents-round-start-during-resource-read");
            page.EndResourceLoad();
            string originalResource = page.State!.Context.ResourceId!;
            check(!await page.Configure(Path.Combine(output, "missing-runtime-resources"), SettingsDocument.CreateNew())
                && page.State!.Context.ResourceId == originalResource, "runtime-page-failed-resource-replacement-preserves-current-runtime");
            check(await page.Configure(resourceRoot, SettingsDocument.CreateNew(), reloadResources: true)
                && page.State!.Context.ResourceId != originalResource, "runtime-page-explicit-same-path-reload-rebinds-actual-resources");
            capture.Bound = false;
            check(page.SessionView!.Runtime.AvailableCommands.Contains("start_round"), "runtime-page-preparation-remains-available-without-game-window");
            page.Dispatch("start_round"); await page.DrainAsync();
            check(page.State is { RoundActive: true, MapOpen: false, NativeGeometryVerified: false } && capture.Calls == 0,
                "runtime-page-start-prepares-without-game-or-capture-authority");
            capture.Bound = true;
            var activeContext = page.State!.Context;
            check(!await page.Configure(resourceRoot, new SettingsDocument(new JsonObject { ["gameMode"] = "difficult" }))
                && page.State!.Context == activeContext && messages.Last().Contains("尚未应用到本局"),
                "runtime-page-active-round-mode-refusal-reports-saved-but-not-applied");
            page.Dispatch("capture_now"); await page.DrainAsync();
            var observed = page.State!;
            File.WriteAllText(Path.Combine(output, "actual-first-map-read.json"), JsonSerializer.Serialize(new {
                capture.Calls, observed, session = page.SessionView, messages
            }, new JsonSerializerOptions { WriteIndented = true }));
            check(capture.Calls > 0 && observed.MapOpen && observed.Context.FloorId == "1" && observed.Candidates.Count > 0,
                "runtime-page-session-consumes-current-floor-and-real-main-ranking");
            check(page.SessionView is { Busy: false, WorkerActive: false } && page.SessionView.Runtime.Context == observed.Context,
                "runtime-page-final-session-context-matches-actual-producer");
            string mapId = observed.Candidates[0].MapId;
            page.Dispatch("confirm_route", new { mapId }); await page.DrainAsync();
            var confirmed = page.State!;
            check(confirmed.IdentityLocked && confirmed.Context.MapId == mapId && confirmed.IdentitySource == "manual",
                "runtime-page-human-map-choice-reaches-runtime-identity-owner");
            check(page.SessionView!.Runtime.LiveAuthorization == false,
                "runtime-page-does-not-promote-native-fit-to-display-authorization");
            page.HandleShortcut("escape"); await page.DrainAsync();
            var heldCapture = new TaskCompletionSource(TaskCreationOptions.RunContinuationsAsynchronously);
            var captureEntered = new TaskCompletionSource(TaskCreationOptions.RunContinuationsAsynchronously);
            capture.HoldNext = heldCapture; capture.Entered = captureEntered;
            int callsWhileHeld;
            try
            {
                page.HandleShortcut("map");
                await captureEntered.Task.WaitAsync(TimeSpan.FromSeconds(5));
                page.HandleShortcut("map");
                check(page.State is { MapOpen: false, NativeGeometryVerified: false } && page.SessionView!.WorkerActive,
                    "runtime-page-second-physical-map-edge-revokes-pending-open-before-capture-exits");
                callsWhileHeld = capture.Calls;
                page.HandleShortcut("map");
                check(capture.Calls == callsWhileHeld && page.SessionView!.QueuedCount == 1,
                    "runtime-page-third-physical-map-edge-replaces-close-with-one-fresh-open");
            }
            finally { heldCapture.TrySetResult(); }
            await page.DrainAsync();
            check(page.State is { MapOpen: true, IdentityLocked: true, NativeGeometryVerified: true }
                && page.State.Context.FrameId == capture.Calls.ToString() && capture.Calls > callsWhileHeld,
                "runtime-page-open-close-open-consumes-only-new-frame-through-real-session-fit");
            page.HandleShortcut("toggle-round"); await page.DrainAsync();
            page.HandleShortcut("toggle-round"); await page.DrainAsync();
            int callsBeforeNextRound = capture.Calls;
            page.HandleShortcut("map"); await page.DrainAsync();
            check(page.State is { RoundActive: true, MapOpen: true } && capture.Calls > callsBeforeNextRound,
                "runtime-page-toggle-round-end-start-resets-physical-map-intent");
            var applying = page.Configure(resourceRoot, new SettingsDocument(new JsonObject { ["routeProfileId"] = "display-preference-regression" }));
            check(!applying.IsCompleted && page.SessionView!.PendingAction == "set_context",
                "runtime-page-display-setting-is-pending-through-real-session");
            page.HandleShortcut("map");
            check(page.SessionView!.PendingAction == "set_context",
                "runtime-page-physical-map-edge-during-configure-preserves-explicit-context-work");
            check(await applying && page.State!.Context.ProfileId == "display-preference-regression",
                "runtime-page-configure-completes-despite-pass-through-map-edge");
            var delayedRead = (Task)typeof(RuntimePage).GetMethod("CaptureAfterDelay",
                System.Reflection.BindingFlags.Instance | System.Reflection.BindingFlags.NonPublic)!.Invoke(page, null)!;
            page.HandleShortcut("end-round"); await page.DrainAsync();
            page.HandleShortcut("start-round"); await page.DrainAsync();
            int callsBeforeOldDelay = capture.Calls;
            await delayedRead; await page.DrainAsync();
            check(capture.Calls == callsBeforeOldDelay && page.State is { RoundActive: true, MapOpen: false },
                "runtime-page-old-delayed-read-cannot-capture-after-shortcut-end-and-new-round");
            var fixedSettings = new SettingsDocument(new JsonObject { ["floorControlMode"] = "fixed" });
            string activeResource = confirmed.Context.ResourceId!;
            check(!await page.Configure(resourceRoot, fixedSettings) && page.State!.Context.ResourceId == activeResource,
                "runtime-page-defers-observer-settings-without-replacing-active-round");
            page.Dispatch("end_round"); await page.DrainAsync();
            check(await page.Configure(resourceRoot, fixedSettings) && page.State!.Context.ResourceId != activeResource,
                "runtime-page-idle-apply-rebinds-changed-floor-observation-settings");
            page.Dispatch("start_round"); await page.DrainAsync();
            page.Dispatch("capture_now"); await page.DrainAsync();
            File.WriteAllText(Path.Combine(output, "actual-fixed-observer.json"), JsonSerializer.Serialize(page.State));
            // Original LiveRuntime._observe on this same current PNG returns
            // floor="1", floorSource="fixed", mapOpen=true. Fixed disables
            // expanded column search; it does not disable the default reader.
            check(page.State!.Context.FloorId == "1" && page.State.Diagnostics.GetProperty("floor").GetProperty("floorSource").GetString() == "fixed",
                "runtime-page-changed-fixed-setting-reaches-actual-current-frame-observer");
            page.Dispatch("end_round"); await page.DrainAsync();
            await page.Configure(resourceRoot, SettingsDocument.CreateNew());
            page.Dispatch("start_round"); await page.DrainAsync();
            page.Dispatch("capture_now"); await page.DrainAsync();
            page.Dispatch("confirm_route", new { mapId }); await page.DrainAsync();
            capture.Bound = false; page.BindingChanged();
            check(page.State is { IdentityLocked: true, NativeGeometryVerified: false, MapOpen: false },
                "runtime-page-binding-change-revokes-image-and-pose-preserves-choice");
            await page.CloseAsync();
            check(page.Dispatch("start_round") is null && page.State is null, "runtime-page-close-drains-and-rejects-new-intent");
            var closingPage = new RuntimePage(capture, messages.Add);
            var configuring = closingPage.Configure(resourceRoot, SettingsDocument.CreateNew());
            await closingPage.CloseAsync();
            check(!await configuring && closingPage.State is null,
                "runtime-page-close-cancels-and-drains-proposed-context-before-return");
            // Real S0 nightmare declarations without the separate difficult
            // manifest make the next context fail in its normal loader.
            string subset = Path.Combine(output, "nightmare-only-context");
            string subsetEntries = Path.Combine(subset, "references", "nightmare-entries");
            Directory.CreateDirectory(subsetEntries);
            foreach (string name in new[] { "nightmare-map-pack.json", "nightmare-shared-structure-index.json", "nightmare-canonical-coordinate-pack.json", "nightmare-structure-display.json", "nightmare-structure-version-bindings.json" })
                File.Copy(Path.Combine(resourceRoot, "references", name), Path.Combine(subset, "references", name));
            foreach (string source in Directory.EnumerateFiles(Path.Combine(resourceRoot, "references", "nightmare-entries")))
                File.Copy(source, Path.Combine(subsetEntries, Path.GetFileName(source)));
            var subsetPage = new RuntimePage(capture, messages.Add);
            try
            {
                bool boundSubset = await subsetPage.Configure(subset, SettingsDocument.CreateNew());
                File.WriteAllText(Path.Combine(output, "mode-fixture-bind.json"), JsonSerializer.Serialize(new { boundSubset, messages }));
                check(boundSubset, "runtime-page-mode-failure-fixture-binds-real-nightmare-declarations");
                var priorContext = subsetPage.State!.Context;
                var difficult = new SettingsDocument(new JsonObject { ["gameMode"] = "difficult" });
                bool changed = await subsetPage.Configure(subset, difficult);
                File.WriteAllText(Path.Combine(output, "failed-mode-change.json"), JsonSerializer.Serialize(new { changed, priorContext, after = subsetPage.State }));
                check(!changed && subsetPage.State!.Context == priorContext,
                    "runtime-page-failed-same-root-mode-change-never-claims-old-resources-as-new-context");
            }
            finally { await subsetPage.CloseAsync(); }
            File.WriteAllText(Path.Combine(output, "runtime-workspace.json"), JsonSerializer.Serialize(new {
                capturedAt, observed, confirmed, messages,
                scope = "Unshown real WPF page -> SessionService -> LiveRuntime -> current Floor/Main/Fit; only capture port substituted. No live window, input hooks, overlay or player acceptance."
            }, new JsonSerializerOptions { WriteIndented = true }));
        }
        finally { await page.CloseAsync(); }
    }

    sealed class CurrentCapture(byte[] bytes, int width, int height, DateTimeOffset capturedAt) : ICapturePort
    {
        readonly object sync = new();
        CaptureStamp? current;
        int calls;
        bool bound = true;
        internal int Calls => Volatile.Read(ref calls);
        internal bool Bound { get { lock (sync) return bound; } set { lock (sync) bound = value; } }
        internal TaskCompletionSource? HoldNext, Entered;
        public bool IsBound => Bound;
        public bool IsCurrent(CaptureStamp stamp) { lock (sync) return bound && stamp == current; }
        public async ValueTask<ClientCaptureResult> CaptureAsync(CancellationToken token)
        {
            token.ThrowIfCancellationRequested(); int call = Interlocked.Increment(ref calls);
            var hold = HoldNext; HoldNext = null;
            if (hold is not null) { Entered?.TrySetResult(); await hold.Task.ConfigureAwait(false); }
            var stamp = new CaptureStamp("current-recorded-client", 1, call.ToString());
            lock (sync) current = stamp;
            return new(new(bytes, width, height, width * 4, stamp, capturedAt));
        }
    }
}
