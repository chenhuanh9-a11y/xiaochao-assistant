using System.Diagnostics;
using System.IO;
using System.Windows;
using System.Windows.Controls;
using System.Windows.Media;
using System.Windows.Media.Imaging;
using StarMap.App.Platform;

internal static class FixtureDiagnostics
{
    internal static object Save(string output, string name, int width, int height, double dpi,
        ReadOnlyMemory<byte> memory, PixelFormat format, string acquisition)
    {
        var bytes = memory.ToArray();
        // Save original bytes first: a later PNG/layout/color assertion must not erase the failure.
        var raw = name + ".bgra";
        using (var stream = new FileStream(Path.Combine(output, raw), FileMode.CreateNew, FileAccess.Write)) stream.Write(bytes);
        var png = name + ".png";
        var bitmap = BitmapSource.Create(width, height, dpi, dpi, format, null, bytes, checked(width * 4));
        bitmap.Freeze();
        var encoder = new PngBitmapEncoder(); encoder.Frames.Add(BitmapFrame.Create(bitmap));
        using (var stream = new FileStream(Path.Combine(output, png), FileMode.CreateNew, FileAccess.Write)) encoder.Save(stream);
        var samples = new List<object>();
        foreach (var (label, x, y) in new[] {
            ("top-left",2,2),("top-right",width-3,2),("bottom-left",2,height-3),("bottom-right",width-3,height-3),
            ("quarter",width/4,height/4),("three-quarter",width*3/4,height*3/4) })
        {
            if (x < 0 || y < 0 || x >= width || y >= height) continue;
            var at = checked((y * width + x) * 4);
            samples.Add(new { label, x, y, b = bytes[at], g = bytes[at+1], r = bytes[at+2], a = bytes[at+3] });
        }
        return new { acquisition, raw, png, width, height, stride = checked(width * 4), dpi,
            pixelFormat = format.ToString(), bytes = bytes.Length, recordedQpc = Stopwatch.GetTimestamp(),
            statistics = WindowsClientCapture.MeasureBgra(bytes), samples };
    }

    internal static object ReadOwnedClientDc(string output, BoundWindow binding)
    {
        using var dpi = CaptureNative.PhysicalCoordinates();
        if (binding.Identity.ProcessId != Environment.ProcessId) throw new InvalidOperationException("Diagnostic DC read is owned-window only.");
        CaptureNative.ValidateIdentity(binding.Identity, requireForeground: false);
        var before = CaptureNative.Geometry(binding.Identity.Handle);
        var started = Stopwatch.GetTimestamp();
        var bytes = CaptureNative.CopyClientBgra(binding.Identity.Handle, before, default);
        var after = CaptureNative.Geometry(binding.Identity.Handle);
        CaptureNative.ValidateIdentity(binding.Identity, requireForeground: false);
        if (after != before) throw new InvalidOperationException("Owned diagnostic client geometry changed.");
        var pixels = Save(output, "owned-client-dc-after-wgc", before.Width, before.Height, before.Dpi,
            bytes, PixelFormats.Bgra32, "Independent target-client DC after WGC; no desktop DC, no activation; different live sampling instant");
        return new { startedQpc = started, before, after, pixels };
    }

    internal static object RenderLogicalScene(string output, Window window, Grid scene)
    {
        // This runs only after the first native WGC and DC evidence has been saved. Rendering an
        // offscreen WPF visual is independent evidence of layout, not proof of HWND presentation.
        var dpi = VisualTreeHelper.GetDpi(scene);
        var width = checked((int)Math.Ceiling(scene.ActualWidth * dpi.DpiScaleX));
        var height = checked((int)Math.Ceiling(scene.ActualHeight * dpi.DpiScaleY));
        var rendered = new RenderTargetBitmap(width, height, dpi.PixelsPerInchX, dpi.PixelsPerInchY, PixelFormats.Pbgra32);
        rendered.Render(scene);
        var bytes = new byte[checked(width * height * 4)]; rendered.CopyPixels(bytes, width * 4, 0);
        var pixels = Save(output, "owned-logical-visual-after-native", width, height, dpi.PixelsPerInchX,
            bytes, PixelFormats.Pbgra32, "Offscreen WPF RenderTargetBitmap after both native captures; not HWND/OS presentation acceptance");
        return new { windowVisible = window.IsVisible, window.IsActive, window.WindowState, window.ShowActivated, window.Topmost,
            processRenderPreference = RenderOptions.ProcessRenderMode.ToString(), capabilityTier = RenderCapability.Tier >> 16,
            targetRenderPreference = (PresentationSource.FromVisual(window) as System.Windows.Interop.HwndSource)?.CompositionTarget?.RenderMode.ToString(),
            window.ActualWidth, window.ActualHeight, sceneWidth = scene.ActualWidth, sceneHeight = scene.ActualHeight,
            scene.IsLoaded, sceneVisible = scene.IsVisible, scene.RenderSize,
            children = scene.Children.Cast<Border>().Select(child => new {
                row = Grid.GetRow(child), column = Grid.GetColumn(child), background = child.Background.ToString(),
                child.ActualWidth, child.ActualHeight, child.IsVisible, child.RenderSize }).ToArray(), pixels };
    }
}
