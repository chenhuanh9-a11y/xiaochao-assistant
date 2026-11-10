using System.IO;
using System.Text.Json;
using System.Windows;
using System.Windows.Controls;
using System.Windows.Media;
using System.Windows.Media.Imaging;
using System.Windows.Threading;
using StarMap.Bridge;
using StarMap.Desktop;

internal static class RuntimeWorkspaceChecks
{
    private static int checks;
    private static void Check(bool value, string message)
    {
        if (!value) throw new InvalidOperationException(message);
        Console.WriteLine("PASS " + message); checks++;
    }
    public static async Task RunAsync(string sourceRoot, string python, string resources, string indexPath)
    {
        using var index = JsonDocument.Parse(File.ReadAllText(indexPath));
        var sample = index.RootElement.GetProperty("samples").EnumerateArray().Single(row => row.GetProperty("id").GetString() == "batch02-event-000097").Clone();
        if (DateTimeOffset.Parse(sample.GetProperty("capturedAt").GetString()!) < new DateTimeOffset(2026, 11, 5, 0, 0, 0, TimeSpan.Zero))
            throw new InvalidDataException("This check requires current-layout capture provenance.");
        var frame = sample.GetProperty("image").GetString()!;
        var physical = sample.GetProperty("physicalClient").Clone();
        var data = Path.Combine(sourceRoot, "work", "hybrid-runtime-ui", Guid.NewGuid().ToString("N"));
        var frames = Path.Combine(data, "frames");
        Directory.CreateDirectory(frames);
        RecordingConnection? recorded = null;
        var window = new MainWindow(new(sourceRoot, python, null, data), (root, installation) =>
            recorded = new RecordingConnection(HybridWorker.Start(python, Path.Combine(sourceRoot, "hybrid/python/runtime_worker.py"), root, data, frames, installation)));
        window.Show();
        try
        {
            await window.ConnectAsync(resources);
            Check(recorded!.SupportsRuntime, "actual shell connects gallery and runtime through one Python process");
            Check(window.Gallery.Rows is null && window.Gallery.Preview is null, "initially hidden gallery defers list and image work");
            window.Runtime.Context = window.Runtime.Contexts.First(row => row.Season == "s0" && row.Mode == "difficult");
            await UntilAsync(() => !window.Runtime.Busy);
            await window.LoadFrameAsync(frame, physical);
            Check(window.Runtime.HasFrame && window.Runtime.CanStart, "selected current-layout PNG reaches production frame adapter");
            var home = (RuntimePage)window.FindName("HomePage");
            ((Button)home.FindName("StartRoundButton")).RaiseEvent(new RoutedEventArgs(Button.ClickEvent));
            await UntilAsync(() => !window.Runtime.Busy && window.Runtime.RoundPreparing);
            Check(!window.Runtime.RoundActive && window.Runtime.CanCapture && !window.Runtime.CanStart,
                "start preserves production prepared phase until the first actual capture");
            Check(!((Button)window.FindName("OpenResourcesButton")).IsEnabled, "resource root cannot change during an active round");
            ((Button)home.FindName("CaptureButton")).RaiseEvent(new RoutedEventArgs(Button.ClickEvent));
            window.ShowGallery();
            await UntilAsync(() => !window.Runtime.Busy);
            Check(window.Runtime.RoundActive && window.Runtime.MapName == "difficult-map-23", "hidden home still consumes actual runtime identity and round state");
            Check(window.Runtime.AlignedImage is null, "hidden page defers image publication");
            var state = window.Runtime.Snapshot!.Value;
            var token = state.GetProperty("render").GetProperty("token").GetString();
            var epoch = state.GetProperty("epoch").GetInt64();
            var pid = recorded.ProcessId;
            await window.Gallery.ReloadAsync();
            Check(window.Gallery.Rows is not null, "gallery reads while the same process owns runtime resource lease");
            Check(window.Runtime.Snapshot!.Value.GetProperty("mode").GetString() == "difficult" &&
                  window.Runtime.Snapshot!.Value.GetProperty("epoch").GetInt64() == epoch && recorded.ProcessId == pid,
                "browsing independent context does not change runtime context or process");
            window.ShowHome();
            await UntilAsync(() => window.Runtime.AlignedImage is not null);
            Check(window.Runtime.Snapshot!.Value.GetProperty("render").GetProperty("token").GetString() == token,
                "returning to persistent home reuses current runtime result without recognition");
            Check(PixelsEqual((BitmapSource)window.Runtime.AlignedImage!, recorded.LastRender!.Value), "WPF image pixels equal actual Python render payload");
            var h = recorded.LastRender!.Value.GetProperty("matrix");
            var matrix = ((MatrixTransform)window.Runtime.OverlayTransform).Matrix;
            Check(matrix.M11 == h[0][0].GetDouble() && matrix.M12 == h[1][0].GetDouble()
                && matrix.M21 == h[0][1].GetDouble() && matrix.M22 == h[1][1].GetDouble()
                && matrix.OffsetX == h[0][2].GetDouble() && matrix.OffsetY == h[1][2].GetDouble(),
                "actual preview applies Python material-to-client matrix");
            Check(((Canvas)home.FindName("RuntimeViewport")).ClipToBounds, "transformed preview clips against full client canvas");
            var bitmap = window.Runtime.AlignedImage;
            await window.Runtime.ToggleSheetAsync();
            Check(window.Runtime.AlignedImage is null && window.Runtime.RoundActive, "visibility action hides pixels while keeping round active");
            await window.Runtime.ToggleSheetAsync();
            await UntilAsync(() => window.Runtime.AlignedImage is not null);
            Check(ReferenceEquals(bitmap, window.Runtime.AlignedImage), "visibility restore reuses immutable material bitmap");
            var selectedFloor = window.Runtime.Floors.First(row => row.Id != window.Runtime.SelectedFloor?.Id);
            window.Runtime.SelectedFloor = selectedFloor;
            window.ShowGallery(); window.ShowHome();
            await UntilAsync(() => window.Runtime.AlignedImage is not null);
            Check(ReferenceEquals(selectedFloor, window.Runtime.SelectedFloor), "page return preserves an unsubmitted target-floor selection");
            await window.Gallery.ReloadAsync();
            Check(window.Gallery.Rows is null && window.Gallery.Preview is null, "hidden gallery keeps only the latest catalog without projecting its page");
            var catalogCalls = recorded.CatalogCalls;
            window.ShowGallery();
            await UntilAsync(() => window.Gallery.Rows is not null);
            Check(recorded.CatalogCalls == catalogCalls, "gallery return projects the completed catalog without requery");
            window.ShowHome();
            await UntilAsync(() => window.Runtime.AlignedImage is not null);
            await SaveRendersAsync(window, sourceRoot);
            recorded.FailNextCapture = true;
            await window.Runtime.CaptureAsync();
            Check(window.Runtime.HasRound && window.Runtime.CanCapture && window.Runtime.CanEnd && !window.Runtime.CanChangeContext
                && !window.Runtime.ServiceUnavailable && window.Runtime.AlignedImage is null,
                "actual service error reconciles surviving round and allows retry/end without unlocking context");
            await window.Runtime.CaptureAsync();
            Check(window.Runtime.AlignedImage is not null, "production capture recovers after actual RPC failure");
            recorded.HoldCaptureReply();
            var cancelled = window.Runtime.CaptureAsync();
            await recorded.Held.Task.WaitAsync(TimeSpan.FromSeconds(20));
            await window.Runtime.CancelAsync();
            recorded.Release.TrySetResult();
            await cancelled;
            Check(window.Runtime.HasRound && window.Runtime.MapName == "difficult-map-23" && window.Runtime.AlignedImage is null && window.Runtime.CanCapture,
                "cancel fences a late real success and closes the map while keeping round identity");
            await window.Runtime.CaptureAsync();
            Check(window.Runtime.AlignedImage is not null, "actual capture reopens successfully after cancelling work");
            recorded.HoldCaptureReply();
            var pending = window.Runtime.CaptureAsync();
            await recorded.Held.Task.WaitAsync(TimeSpan.FromSeconds(20));
            await window.Runtime.EndRoundAsync();
            recorded.Release.TrySetResult();
            await pending;
            Check(!window.Runtime.HasRound && window.Runtime.AlignedImage is null,
                "late real successful capture cannot repaint after end-round consumer transition");
            await window.Runtime.StartRoundAsync();
            await window.Runtime.CaptureAsync();
            var previous = recorded;
            await previous.CloseAsync();
            await window.Runtime.CaptureAsync();
            Check(window.Runtime.ServiceUnavailable && window.Runtime.HasRound && !window.Runtime.CanChangeContext
                && ((Button)window.FindName("OpenResourcesButton")).IsEnabled,
                "disconnected service preserves known round and offers explicit reconnect");
            await window.ConnectAsync(resources);
            Check(previous.HasExited && recorded.ProcessId != previous.ProcessId && !window.Runtime.HasRound
                && !window.Runtime.ServiceUnavailable && window.Runtime.CanChangeContext,
                "explicit shell reconnect drains old process and starts a fresh independent session");
        }
        finally
        {
            recorded?.Release.TrySetResult();
            window.Close();
            await UntilAsync(() => !window.IsVisible);
        }
        Check(recorded!.HasExited, "actual shell closes the unified Python process");
        Console.WriteLine($"{checks} runtime workspace checks passed; recorded frames and programmatic WPF events, not native input/game acceptance.");
    }

    private static bool PixelsEqual(BitmapSource image, JsonElement render)
    {
        using var stream = new MemoryStream(Convert.FromBase64String(render.GetProperty("pngBase64").GetString()!));
        var decoded = BitmapFrame.Create(stream, BitmapCreateOptions.PreservePixelFormat, BitmapCacheOption.OnLoad);
        var left = new FormatConvertedBitmap(image, PixelFormats.Pbgra32, null, 0);
        var right = new FormatConvertedBitmap(decoded, PixelFormats.Pbgra32, null, 0);
        if (left.PixelWidth != right.PixelWidth || left.PixelHeight != right.PixelHeight) return false;
        var stride = left.PixelWidth * 4;
        var a = new byte[stride * left.PixelHeight]; var b = new byte[a.Length];
        left.CopyPixels(a, stride, 0); right.CopyPixels(b, stride, 0);
        return a.AsSpan().SequenceEqual(b);
    }

    private static async Task SaveRendersAsync(MainWindow window, string sourceRoot)
    {
        var directory = Path.Combine(sourceRoot, "work", "hybrid-runtime-renders", Guid.NewGuid().ToString("N"));
        Directory.CreateDirectory(directory);
        foreach (var size in new[] { (W: 1080, H: 760), (W: 760, H: 560) })
        {
            window.Width = size.W; window.Height = size.H; window.UpdateLayout();
            await window.Dispatcher.InvokeAsync(() => { }, DispatcherPriority.ApplicationIdle);
            var dpi = VisualTreeHelper.GetDpi(window);
            var bitmap = new RenderTargetBitmap((int)Math.Ceiling(window.ActualWidth * dpi.DpiScaleX),
                (int)Math.Ceiling(window.ActualHeight * dpi.DpiScaleY), dpi.PixelsPerInchX, dpi.PixelsPerInchY, PixelFormats.Pbgra32);
            bitmap.Render(window);
            var encoder = new PngBitmapEncoder(); encoder.Frames.Add(BitmapFrame.Create(bitmap));
            using var file = File.Create(Path.Combine(directory, $"home-{size.W}x{size.H}.png")); encoder.Save(file);
            if (size.W == 760)
            {
                var home = (RuntimePage)window.FindName("HomePage");
                var scroll = (ScrollViewer)home.FindName("PageScroll");
                Check(scroll.ScrollableHeight > 0 && ((ListBox)home.FindName("RuntimeCandidateList")).ActualHeight >= 180,
                    "minimum window keeps usable candidate/preview area with reachable scrolling");
                scroll.ScrollToBottom();
                await window.Dispatcher.InvokeAsync(() => { }, DispatcherPriority.ApplicationIdle);
                var bottom = new RenderTargetBitmap(bitmap.PixelWidth, bitmap.PixelHeight, dpi.PixelsPerInchX, dpi.PixelsPerInchY, PixelFormats.Pbgra32);
                bottom.Render(window);
                var bottomEncoder = new PngBitmapEncoder(); bottomEncoder.Frames.Add(BitmapFrame.Create(bottom));
                using var bottomFile = File.Create(Path.Combine(directory, "home-760x560-scrolled.png")); bottomEncoder.Save(bottomFile);
                scroll.ScrollToTop();
            }
        }
        Console.WriteLine("Runtime WPF RenderTargetBitmap evidence (not desktop capture): " + directory);
    }

    private static async Task UntilAsync(Func<bool> ready)
    {
        var deadline = DateTime.UtcNow.AddSeconds(30);
        while (!ready())
        {
            if (DateTime.UtcNow > deadline) throw new TimeoutException("Runtime workspace consumer did not reach expected state.");
            await Task.Delay(20);
        }
    }

    private sealed class RecordingConnection(IWorkerConnection inner) : IWorkerConnection
    {
        private bool holdNextCapture;
        public bool FailNextCapture { get; set; }
        public TaskCompletionSource Held { get; private set; } = new(TaskCreationOptions.RunContinuationsAsynchronously);
        public TaskCompletionSource Release { get; private set; } = new(TaskCreationOptions.RunContinuationsAsynchronously);
        public void HoldCaptureReply()
        {
            holdNextCapture = true;
            Held = new(TaskCreationOptions.RunContinuationsAsynchronously);
            Release = new(TaskCreationOptions.RunContinuationsAsynchronously);
        }
        public JsonElement? LastRender { get; private set; }
        public int CatalogCalls { get; private set; }
        public int ProcessId => inner.ProcessId;
        public bool HasExited => inner.HasExited;
        public bool SupportsRuntime => inner.SupportsRuntime;
        public Task<WorkerHello> HelloAsync(CancellationToken cancellation = default) => inner.HelloAsync(cancellation);
        public Task<BrowseCatalog> CatalogAsync(string season, string mode, CancellationToken cancellation = default)
        {
            CatalogCalls++;
            return inner.CatalogAsync(season, mode, cancellation);
        }
        public Task<BrowsePreview> PreviewAsync(long catalogId, string key, CancellationToken cancellation = default) => inner.PreviewAsync(catalogId, key, cancellation);
        public Task CloseAsync() => inner.CloseAsync();
        public ValueTask DisposeAsync() => inner.DisposeAsync();
        public async Task<JsonElement> InvokeAsync(string operation, object? arguments = null, CancellationToken cancellation = default)
        {
            var args = JsonSerializer.SerializeToElement(arguments);
            var capture = operation == "dispatch" && args.TryGetProperty("action", out var action) && action.GetString() == "capture_now";
            if (FailNextCapture && capture)
            {
                FailNextCapture = false;
                // Fault injection at the RPC operation seam: the real service rejects
                // this unknown operation and invalidates its actual current render.
                return await inner.InvokeAsync("test-unsupported-runtime-operation", cancellation: cancellation);
            }
            var result = await inner.InvokeAsync(operation, arguments, cancellation);
            if (result.TryGetProperty("render", out var render) && render.ValueKind == JsonValueKind.Object) LastRender = render.Clone();
            if (holdNextCapture && capture)
            {
                holdNextCapture = false;
                Held.TrySetResult();
                await Release.Task; // Deliberate consumer-side late success; production solver/reply unchanged.
            }
            return result;
        }
    }
}
