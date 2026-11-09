using System.IO;
using System.Text.Json;
using System.Windows;
using System.Windows.Media;
using System.Windows.Media.Imaging;
using System.Windows.Threading;
using StarMap.App.Platform;

/// <summary>Geometry admission regression against real Surface/Visual. Only the OS window boundary is a recorder.</summary>
internal static class OverlayGeometryBoundaryRegression
{
    internal static int Run(string output)
    {
        var checks = new List<object>(); var cases = new List<object>(); int failures = 0;
        void Check(bool passed, string name)
        {
            checks.Add(new { name, passed }); if (!passed) failures++;
            Console.WriteLine((passed ? "ok " : "FAIL ") + name);
        }
        var originalClient = new WpfOverlayClientGeometry(-600, 100, 2, 2, 96);
        (string Name, WpfOverlayClientGeometry Geometry)[] changes =
        [
            ("left", originalClient with { Left = -563 }),
            ("top", originalClient with { Top = 71 }),
            ("width", originalClient with { Width = 3 }),
            ("height", originalClient with { Height = 3 }),
            ("capture-dpi-context", originalClient with { Dpi = 144 }),
            ("origin-and-size", new(-563, 71, 4, 3, 96))
        ];
        var red = new WpfOverlayBgra32(2, 2, 8, Solid(2, 2, 0, 0, 255));
        var green = new WpfOverlayBgra32(4, 4, 16, Solid(4, 4, 0, 255, 0));
        foreach (bool sibling in new[] { true, false })
        foreach (var change in changes)
        {
            string label = (sibling ? "stage/" : "same-current/") + change.Name;
            var native = new RecordingOverlayWindow();
            using var surface = new WpfAlignedOverlaySurface(Dispatcher.CurrentDispatcher, native);
            var placements = new List<object>();
            try
            {
                var original = surface.Reserve(new object());
                var originalFrame = WpfAlignedOverlaySurface.Prepare(original, red, WpfOverlayHomography.Identity, originalClient);
                Check(surface.TryApply(original, originalFrame) && surface.ShowCurrent(original)
                    && native.IsVisible && native.ShowCommandCount == 1 && surface.NativeHandle == 0,
                    label + "/recording-only-current-is-visible");
                byte[] oldPixels = Render(surface.Visual, 2, 2);
                Check(oldPixels.SequenceEqual(Solid(2, 2, 0, 0, 255)) && At(native.LastPosition, originalClient),
                    label + "/old-physical-rect-and-red-material-established");
                int ensures = native.EnsureCallCount, calls = native.PlacementCallCount;
                var candidate = sibling ? surface.Stage(new object()) : original;
                var prepared = WpfAlignedOverlaySurface.Prepare(candidate, green, WpfOverlayHomography.Identity, change.Geometry);
                native.BeforePlacement = (requested, show) => placements.Add(new
                {
                    requested, show, visibleBeforePlacement = native.IsVisible,
                    oldOwnerStillCurrent = surface.IsCurrent(original),
                    oldPixelsStillRed = Render(surface.Visual, 2, 2).SequenceEqual(Solid(2, 2, 0, 0, 255))
                });
                bool accepted = surface.TryApply(candidate, prepared);
                native.BeforePlacement = null;
                int ensureDelta = native.EnsureCallCount - ensures, placementDelta = native.PlacementCallCount - calls;
                bool ownerPreserved = surface.IsCurrent(original) && !original.PreparationCancellation.IsCancellationRequested;
                bool pixelsPreserved = Render(surface.Visual, 2, 2).SequenceEqual(oldPixels);
                bool positionPreserved = At(native.LastPosition, originalClient);
                Check(!accepted, label + "/different-complete-client-geometry-is-rejected");
                Check(ensureDelta == 0 && placementDelta == 0 && placements.Count == 0,
                    label + "/rejection-precedes-every-native-Ensure-and-Place");
                Check(ownerPreserved && pixelsPreserved && positionPreserved && surface.IsVisible,
                    label + "/rejection-preserves-old-owner-material-position-and-visibility");

                // A new explicit Reserve is the independent return path for every rejected geometry change.
                var replacement = surface.Reserve(new object());
                Check(!surface.IsCurrent(original) && original.PreparationCancellation.IsCancellationRequested
                    && !surface.HasMaterial && !surface.IsVisible,
                    label + "/explicit-Reserve-revokes-and-hides-old-owner");
                var replacementFrame = WpfAlignedOverlaySurface.Prepare(replacement, green, WpfOverlayHomography.Identity, change.Geometry);
                Check(surface.TryApply(replacement, replacementFrame) && At(native.LastPosition, change.Geometry)
                    && surface.IsCurrent(replacement) && !surface.IsVisible,
                    label + "/new-Reserve-can-apply-new-geometry-hidden");
                Check(Render(surface.Visual, change.Geometry.Width, change.Geometry.Height)
                    .SequenceEqual(Solid(change.Geometry.Width, change.Geometry.Height, 0, 255, 0)),
                    label + "/new-geometry-has-independent-green-pixel-oracle");
                Check(surface.ShowCurrent(replacement) && surface.IsVisible && native.ShowCommandCount == 2,
                    label + "/new-owner-explicit-show-is-only-recorded-not-native");
                int afterEnsures = native.EnsureCallCount, afterPlaces = native.PlacementCallCount;
                Check(!surface.TryApply(original, originalFrame) && !surface.ShowCurrent(original)
                    && native.EnsureCallCount == afterEnsures && native.PlacementCallCount == afterPlaces,
                    label + "/old-token-cannot-reposition-or-show-after-Reserve");
                cases.Add(new { label, originalClient, proposedClient = change.Geometry, accepted, ensureDelta, placementDelta,
                    ownerPreserved, pixelsPreserved, positionPreserved, placements });
            }
            catch (Exception error)
            {
                native.BeforePlacement = null;
                Check(false, label + "/unexpected-exception"); cases.Add(new { label, error = error.ToString(), placements });
            }
        }
        int exit = failures == 0 ? 0 : 1;
        File.WriteAllText(Path.Combine(output, "result.json"), JsonSerializer.Serialize(new
        {
            exit, checks, cases,
            scope = "Actual production Surface.TryApply/ShowCurrent/Stage/Reserve and WPF viewport pixels. RecordingOverlayWindow replaces OS HWND/Ensure/Place/Hide/DPI only; positive ShowCurrent records state without creating or showing any HWND.",
            pending = new[] { "Native pixels during move/resize", "Foreground/focus", "OS placement and DPI behavior", "Real-game acceptance" }
        }, new JsonSerializerOptions { WriteIndented = true }));
        return exit;
    }

    static bool At(WpfAlignedOverlayNative.WindowPosition position, WpfOverlayClientGeometry client) => position.X == client.Left
        && position.Y == client.Top && position.Width == client.Width && position.Height == client.Height;
    static byte[] Solid(int width, int height, byte blue, byte green, byte red)
    {
        var pixels = new byte[width * height * 4];
        for (int at = 0; at < pixels.Length; at += 4) { pixels[at] = blue; pixels[at + 1] = green; pixels[at + 2] = red; pixels[at + 3] = 255; }
        return pixels;
    }
    static byte[] Render(FrameworkElement visual, int width, int height)
    {
        VisualTreeHelper.SetRootDpi(visual, new DpiScale(1, 1));
        visual.InvalidateMeasure(); visual.InvalidateArrange();
        visual.Measure(new Size(width, height)); visual.Arrange(new Rect(0, 0, width, height)); visual.UpdateLayout();
        var bitmap = new RenderTargetBitmap(width, height, 96, 96, PixelFormats.Pbgra32); bitmap.Render(visual);
        var pixels = new byte[width * height * 4]; bitmap.CopyPixels(pixels, width * 4, 0); return pixels;
    }
}
