using System.IO;
using StarMap.App.Platform;

internal static class Program
{
    [STAThread]
    static void Main(string[] args)
    {
        if (args.Contains("--owned-fixture", StringComparer.Ordinal))
        {
            var option = Array.IndexOf(args, "--output");
            var output = option >= 0 && option + 1 < args.Length ? Path.GetFullPath(args[option + 1])
                : throw new ArgumentException("--owned-fixture requires --output <new directory>");
            Environment.ExitCode = OwnedFixture.Run(output, softwareRender: args.Contains("--software-render", StringComparer.Ordinal));
            return;
        }
        Environment.ExitCode = PureAsync().GetAwaiter().GetResult();
    }

    static async Task<int> PureAsync()
    {
        var failed = 0; var checks = 0;
        void Check(bool condition, string name)
        {
            checks++; Console.WriteLine((condition ? "ok " : "FAIL ") + name);
            if (!condition) failed++;
        }
        bool Reject(Action action, string code)
        { try { action(); return false; } catch (ClientCaptureException error) { return error.Code == code; } }
        var client = new ClientGeometry(113, 129, 317, 211, 144);
        var window = new WindowsGraphicsCaptureRect(90, 80, 450, 370);
        var dwm = new WindowsGraphicsCaptureRect(100, 100, 440, 350);
        var bounds = new WindowsGraphicsCaptureBounds(client, window, dwm, 0);
        var crop = WindowsGraphicsCaptureGeometry.Resolve(bounds, 340, 250);
        Check(crop.Left == 13 && crop.Top == 29 && crop.Width == 317 && crop.Height == 211
            && crop.SurfaceScreenBounds == dwm && crop.Source.Contains("DWMWA_EXTENDED_FRAME_BOUNDS", StringComparison.Ordinal), "measured-dwm-origin-irregular-insets");
        var windowCrop = WindowsGraphicsCaptureGeometry.Resolve(bounds, 360, 290);
        Check(windowCrop.Left == 23 && windowCrop.Top == 49 && windowCrop.Source.Contains("GetWindowRect", StringComparison.Ordinal), "measured-windowrect-origin");
        Check(Reject(() => WindowsGraphicsCaptureGeometry.Resolve(bounds, 341, 250), "capture-wgc-surface-bounds-unsupported"), "unmatched-content-not-resized-or-guessed");
        Check(Reject(() => WindowsGraphicsCaptureGeometry.Resolve(bounds with { Window = dwm with { Left = 101, Right = 441 } }, 340, 250), "capture-wgc-crop-ambiguous"), "equal-size-different-origins-rejected");
        Check(Reject(() => WindowsGraphicsCaptureGeometry.Resolve(bounds with { Client = client with { Left = 99 } }, 340, 250), "capture-wgc-client-outside-surface"), "client-outside-surface-rejected");
        Check(Reject(() => WindowsGraphicsCaptureGeometry.ByteCount(int.MaxValue, int.MaxValue), "capture-wgc-frame-too-large"), "frame-byte-overflow-rejected");
        Check(Reject(() => WindowsGraphicsCaptureGeometry.ByteCount(8192, 8192), "capture-wgc-frame-too-large"), "frame-budget-before-allocation");
        Check(Reject(() => WindowsGraphicsCaptureGeometry.ByteCount(0, 4), "capture-wgc-frame-too-large"), "empty-frame-rejected");
        var negative = bounds with { Client = client with { Left = -487, Top = -271 },
            ExtendedFrame = new(-500, -300, -160, -50), Window = new(-510, -320, -150, -30) };
        var negativeCrop = WindowsGraphicsCaptureGeometry.Resolve(negative, 340, 250);
        Check(negativeCrop.Left == 13 && negativeCrop.Top == 29, "negative-screen-origin-keeps-client-pixels");
        var bytes = Enumerable.Range(0, 7 * 5 * 4).Select(value => (byte)value).ToArray();
        var small = new WindowsGraphicsCaptureCrop(2, 1, 3, 2, new(0, 0, 7, 5), "synthetic measured rectangle");
        var pixels = WindowsGraphicsCaptureGeometry.Crop(bytes, 7, 5, small, default);
        var expected = bytes.AsSpan((1 * 7 + 2) * 4, 12).ToArray().Concat(bytes.AsSpan((2 * 7 + 2) * 4, 12).ToArray()).ToArray();
        Check(pixels.SequenceEqual(expected), "crop-preserves-every-bgra-byte-alpha-and-row-order");
        var backend = new WindowsGraphicsCaptureBackend(); // No IO, session or device.
        var identity = new WindowIdentity(42, Environment.ProcessId, 1, 1, "fixture", "fixture", "fixture");
        var binding = new BoundWindow(identity, client, 7);
        using var canceled = new CancellationTokenSource(); canceled.Cancel();
        var preCanceled = false;
        try { await backend.CaptureOwnedFixtureAsync(binding, TimeSpan.FromSeconds(1), canceled.Token); }
        catch (OperationCanceledException) { preCanceled = true; }
        Check(preCanceled, "precancel-before-any-native-probe-or-device");
        var ordinaryOwn = false;
        try { await backend.CaptureAsync(binding, TimeSpan.FromSeconds(1)); }
        catch (ClientCaptureException error) { ordinaryOwn = error.Code == "capture-wgc-window-scope"; }
        Check(ordinaryOwn, "production-api-rejects-own-fixture-before-native");
        var zeroTimeout = false;
        try { await backend.CaptureOwnedFixtureAsync(binding, TimeSpan.Zero); }
        catch (ArgumentOutOfRangeException) { zeroTimeout = true; }
        Check(zeroTimeout, "invalid-timeout-before-native");
        Console.WriteLine($"{checks} checks, {failed} failures. Measured-geometry/raw-byte and no-start guards only; no WGC session, window, native capture or game acceptance.");
        return failed == 0 ? 0 : 1;
    }
}
