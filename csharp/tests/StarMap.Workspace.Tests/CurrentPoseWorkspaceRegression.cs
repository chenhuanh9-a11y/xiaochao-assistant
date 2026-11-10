using System.IO;
using System.Reflection;
using System.Text.Json.Nodes;
using System.Windows.Media.Imaging;
using System.Windows.Threading;
using StarMap.App;
using StarMap.App.Platform;
using StarMap.Core.Settings;
using StarMap.Runtime;

internal static class CurrentPoseWorkspaceRegression
{
    internal static async Task Run(string resources, string replay, Action<bool, string> check)
    {
        var index = JsonNode.Parse(await File.ReadAllBytesAsync(Path.Combine(replay, "all-samples.json")))!["samples"]!.AsArray()
            .ToDictionary(row => row!["id"]!.GetValue<string>(), row => row!.AsObject());
        var capture = new RecordedCapture(index); capture.Select("batch01-event-000006", false);
        var page = new RuntimePage(capture, _ => { }); using var native = new WindowsRuntimeCapturePort();
        RuntimeOverlayHost? host = null;
        try
        {
            var settings = new SettingsDocument(new JsonObject { ["gameSeason"] = "s0", ["gameMode"] = "nightmare", ["idvb0059Enabled"] = false });
            check(await page.Configure(resources, settings), "renewal-WPF-uses-public-disabled-setting");
            page.Dispatch("start_round"); await page.DrainAsync(); page.Dispatch("capture_now"); await page.DrainAsync();
            var runtime = Field<LiveRuntime>(page, "runtime"); var first = page.ReadRender()!;
            var bitmap = await Task.Run(() => WpfDisplayMaterialConsumer.PrepareMaterialReady(first.Material));
            var hold = capture.HoldNext(); page.Dispatch("refresh_map"); await hold.Started.Task.WaitAsync(TimeSpan.FromSeconds(10));
            try { check(bitmap.IsFrozen && page.RememberMaterialReady(first), "real-WPF-old-bitmap-ready-ACK-is-queued-while-refresh-worker-is-busy"); }
            finally { hold.Release.TrySetResult(); }
            await page.DrainAsync();
            check(page.ReadRender()!.SharesMaterial(first) && page.ReadRender()!.Stamp != first.Stamp && page.ReadMaterialToRemember() is null,
                "real-Session-late-material-work-consumes-fresh-renewal-proposal");
            check(await page.Configure(resources, settings) && ReferenceEquals(runtime, Field<LiveRuntime>(page, "runtime")),
                "public-cache-setting-preserves-the-active-runtime-owner");
            var reports = new List<string>();
            host = new(Dispatcher.CurrentDispatcher, page.ReadRender, native, reports.Add,
                readMaterialToRemember: page.ReadMaterialToRemember, rememberMaterialReady: page.RememberMaterialReady);
            page.Dispatch("refresh_map"); await page.DrainAsync(); await Consumed();
            var prepared = Field<object>(host, "preparedMaterial");
            var ready = (BitmapSource)prepared.GetType().GetProperty("Bitmap")!.GetValue(prepared)!;
            page.Dispatch("refresh_map"); await page.DrainAsync(); await Consumed();
            check(ReferenceEquals(prepared, Field<object>(host, "preparedMaterial")) && ready.IsFrozen,
                "real-overlay-material-cache-reuses-the-bitmap-while-consuming-each-fresh-proposal");
            var layer = page.ReadRender()!.Material; var projection = new WpfDisplayProjectionCache();
            using var surface = new WpfAlignedOverlaySurface();
            var client = new WpfOverlayClientGeometry(-20, 30, 8, 8, 96);
            var matrix = WpfOverlayHomography.Identity; var ticket = surface.Reserve(new object());
            var pixels = await projection.PrepareAsync(ticket, layer, matrix, client, 1);
            surface.Withdraw(); var fresh = surface.Reserve(new object());
            var reused = await projection.PrepareAsync(fresh, layer, matrix, client, .5);
            check(ReferenceEquals(pixels.Image, reused.Image) && ReferenceEquals(pixels.ClientPixels, reused.ClientPixels)
                && ReferenceEquals(reused.Ticket, fresh) && reused.Opacity == .5 && !surface.TryApply(ticket, pixels),
                "production-projection-cache-reuses-pixels-with-new-ticket-and-opacity-without-reviving-old-permission");
            var moved = await projection.PrepareAsync(fresh, layer, matrix with { M02 = 1 }, client, .5);
            var dpi = await projection.PrepareAsync(fresh, layer, matrix with { M02 = 1 }, client with { Dpi = 144 }, .5);
            check(!ReferenceEquals(reused.Image, moved.Image) && !ReferenceEquals(moved.Image, dpi.Image),
                "changed-transform-or-complete-client-context-requires-a-new-projection");
            page.Dispatch("close_map"); await page.DrainAsync();
            check(!page.RememberMaterialReady(first) && reports.Count == 0 && native.Binding is null
                && Field<WpfAlignedOverlaySurface>(host, "surface").NativeHandle == 0,
                "closed-WPF-rejects-late-ACK-and-recorded-tests-never-show-a-native-window");
        }
        finally { host?.Dispose(); await page.CloseAsync(); }

        async Task Consumed()
        {
            var clock = System.Diagnostics.Stopwatch.StartNew();
            while (page.ReadMaterialToRemember() is not null || page.SessionView!.WorkerActive)
            { if (clock.Elapsed > TimeSpan.FromSeconds(10)) throw new TimeoutException("Fresh WPF proposal was not consumed."); await Task.Delay(10); }
            await page.DrainAsync();
        }
    }
    static T Field<T>(object owner, string name) => (T)owner.GetType().GetField(name, BindingFlags.Instance | BindingFlags.NonPublic)!.GetValue(owner)!;
}
