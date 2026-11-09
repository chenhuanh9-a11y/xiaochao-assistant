using System.IO;
using System.Diagnostics;
using System.Text.Json;
using System.Windows;
using System.Windows.Controls;
using System.Windows.Interop;
using System.Windows.Media;
using System.Windows.Media.Imaging;
using System.Windows.Threading;
using StarMap.App.Platform;

internal static class OwnedFixture
{
    internal static int Run(string output, bool softwareRender = false)
    {
        if (Directory.Exists(output) || File.Exists(output)) throw new IOException("Use a new output directory; preserve prior WGC evidence.");
        Directory.CreateDirectory(output);
        var fixtureStartedQpc = Stopwatch.GetTimestamp();
        // Explicit diagnostic preference in this standalone owned-fixture process only.
        // Default mode does not assign a rendering preference or change the production App.
        if (softwareRender) RenderOptions.ProcessRenderMode = RenderMode.SoftwareOnly;
        var support = WindowsGraphicsCaptureBackend.GetSupport();
        if (!support.Supported)
        {
            File.WriteAllText(Path.Combine(output, "result.json"), JsonSerializer.Serialize(new { exit = 2, support, scope = "WGC unavailable; no capture/GUI acceptance" }));
            Console.Error.WriteLine(support.Code + ": " + support.Message);
            return 2;
        }
        var app = new Application { ShutdownMode = ShutdownMode.OnExplicitShutdown };
        var scene = new Grid();
        scene.RowDefinitions.Add(new()); scene.RowDefinitions.Add(new());
        scene.ColumnDefinitions.Add(new()); scene.ColumnDefinitions.Add(new());
        foreach (var (color, row, column) in new[] { (Colors.Red, 0, 0), (Colors.Lime, 0, 1), (Colors.Blue, 1, 0), (Colors.Yellow, 1, 1) })
        {
            var block = new Border { Background = new SolidColorBrush(color) };
            Grid.SetRow(block, row); Grid.SetColumn(block, column); scene.Children.Add(block);
        }
        // Only this fixture is shown. Never Activate/Focus/SetForegroundWindow or touch another window.
        var window = new Window { Title = "StarMap · owned WGC color fixture", Width = 529, Height = 413,
            Content = scene, ResizeMode = ResizeMode.NoResize, ShowActivated = false, Topmost = false };
        long sourceInitializedQpc = 0, contentRenderedQpc = 0, loadedQpc = 0;
        string? targetRenderPreference = null;
        window.SourceInitialized += (_, _) => sourceInitializedQpc = Stopwatch.GetTimestamp();
        window.ContentRendered += (_, _) => contentRenderedQpc = Stopwatch.GetTimestamp();
        window.Loaded += async (_, _) =>
        {
            loadedQpc = Stopwatch.GetTimestamp();
            targetRenderPreference = (PresentationSource.FromVisual(window) as HwndSource)?.CompositionTarget?.RenderMode.ToString();
            var checks = new List<object>(); var metadata = new List<WindowsGraphicsCaptureMetadata>();
            var diagnostics = new List<object>();
            var exit = 0;
            void Check(bool condition, string name, bool continueOnFailure = false)
            {
                checks.Add(new { name, passed = condition }); Console.WriteLine((condition ? "ok " : "FAIL ") + name);
                if (!condition) { exit = 1; if (!continueOnFailure) throw new InvalidDataException(name); }
            }
            try
            {
                await window.Dispatcher.InvokeAsync(() => { }, DispatcherPriority.ApplicationIdle);
                await Task.Delay(250); // Only fixture paint readiness, not a capture performance measurement.
                using var owner = new WindowsClientCapture();
                var realBinding = owner.BindOwnedFixture(new WindowInteropHelper(window).Handle);
                // Full-client intent must ignore stale discovery/UI dimensions at binding time.
                var binding = new BoundWindow(realBinding.Identity, realBinding.GeometryAtBinding with { Width = 1, Height = 1 }, realBinding.Generation);
                var backend = new WindowsGraphicsCaptureBackend();
                for (var cycle = 0; cycle < 3; cycle++)
                {
                    var currentCycle = cycle;
                    var frame = await backend.CaptureOwnedFixtureAsync(binding, TimeSpan.FromSeconds(5), surfaceCopied:
                        (width, height, surfaceBytes, crop) => diagnostics.Add(new {
                            cycle = currentCycle, crop,
                            pixels = FixtureDiagnostics.Save(output, $"owned-surface-{currentCycle:D2}", width, height, realBinding.GeometryAtBinding.Dpi,
                                surfaceBytes, PixelFormats.Bgra32, "Actual SoftwareBitmap/CopyToBuffer/DataReader BGRA before client crop") }));
                    metadata.Add(frame.Metadata);
                    diagnostics.Add(new { cycle, pixels = FixtureDiagnostics.Save(output, $"owned-client-{cycle:D2}",
                        frame.Width, frame.Height, frame.Metadata.After.Client.Dpi, frame.Bgra32Pixels, PixelFormats.Bgra32,
                        "Actual returned WGC client bytes, saved before all color/geometry assertions") });
                    if (cycle == 0)
                    {
                        try { diagnostics.Add(new { clientDc = FixtureDiagnostics.ReadOwnedClientDc(output, realBinding) }); }
                        catch (Exception error) { diagnostics.Add(new { clientDcError = error.ToString() }); }
                        try { diagnostics.Add(new { logicalVisual = FixtureDiagnostics.RenderLogicalScene(output, window, scene) }); }
                        catch (Exception error) { diagnostics.Add(new { logicalVisualError = error.ToString() }); }
                    }
                    Check(frame.Width == realBinding.GeometryAtBinding.Width && frame.Height == realBinding.GeometryAtBinding.Height
                        && frame.Bgra32Pixels.Length == frame.Width * frame.Height * 4 && frame.Stride == frame.Width * 4,
                        $"cycle-{cycle}/current-full-client-native-size");
                    Check(frame.Metadata.Before == frame.Metadata.After && frame.Metadata.Crop.Width == frame.Width
                        && frame.Metadata.Crop.Height == frame.Height && frame.Metadata.ContentWidth == frame.Metadata.SurfaceWidth
                        && frame.Metadata.ContentHeight == frame.Metadata.SurfaceHeight,
                        $"cycle-{cycle}/stable-content-and-measured-crop");
                    var bytes = frame.Bgra32Pixels.ToArray();
                    bool Pixel(int x, int y, byte r, byte g, byte b)
                    {
                        var at = (y * frame.Width + x) * 4;
                        return bytes[at] == b && bytes[at + 1] == g && bytes[at + 2] == r && bytes[at + 3] == 255;
                    }
                    Check(Pixel(2, 2, 255, 0, 0) && Pixel(frame.Width - 3, 2, 0, 255, 0)
                        && Pixel(2, frame.Height - 3, 0, 0, 255) && Pixel(frame.Width - 3, frame.Height - 3, 255, 255, 0)
                        && Pixel(frame.Width / 4, frame.Height / 4, 255, 0, 0)
                        && Pixel(frame.Width * 3 / 4, frame.Height * 3 / 4, 255, 255, 0),
                        $"cycle-{cycle}/actual-client-corners-and-quadrants-bgra", continueOnFailure: true);
                }
                using var cancel = new CancellationTokenSource();
                var ownedFrameCanceled = false; var frameWasOwned = false;
                try
                {
                    await backend.CaptureOwnedFixtureAsync(binding, TimeSpan.FromSeconds(5), cancel.Token,
                        () => { frameWasOwned = true; cancel.Cancel(); });
                }
                catch (OperationCanceledException) { ownedFrameCanceled = true; }
                Check(frameWasOwned && ownedFrameCanceled, "real-frame-owned-then-canceled-never-returned");
                var recovered = await backend.CaptureOwnedFixtureAsync(binding, TimeSpan.FromSeconds(5));
                Check(recovered.Width == realBinding.GeometryAtBinding.Width && recovered.Metadata.Before == recovered.Metadata.After,
                    "capture-recovers-after-owned-frame-cancellation");
                var closedFrameRejected = false;
                try
                {
                    await backend.CaptureOwnedFixtureAsync(binding, TimeSpan.FromSeconds(5), frameOwned:
                        () => window.Dispatcher.Invoke(window.Close));
                }
                catch (ClientCaptureException) { closedFrameRejected = true; }
                Check(closedFrameRejected, "own-window-closed-after-frame-arrival-does-not-return-late-frame");
            }
            catch (Exception error) { exit = 1; checks.Add(new { name = "failure", passed = false, error = error.ToString() }); Console.Error.WriteLine(error); }
            finally
            {
                await File.WriteAllTextAsync(Path.Combine(output, "result.json"), JsonSerializer.Serialize(new {
                    exit, support, checks, metadata, diagnostics,
                    rendering = new { softwareRenderRequested = softwareRender,
                        processPreference = RenderOptions.ProcessRenderMode.ToString(),
                        capabilityTier = RenderCapability.Tier >> 16, capabilityTierRaw = RenderCapability.Tier,
                        targetPreference = targetRenderPreference,
                        fixtureStartedQpc, sourceInitializedQpc, loadedQpc, contentRenderedQpc, qpcFrequency = Stopwatch.Frequency },
                    scope = "Actual one-window WGC/SoftwareBitmap, owned unactivated color fixture only. No game, user-window resize/activation, input, desktop fallback or end-to-end runtime acceptance."
                }, new JsonSerializerOptions { WriteIndented = true }));
                if (window.IsVisible) window.Close();
                app.Shutdown(exit);
            }
        };
        return app.Run(window);
    }
}
