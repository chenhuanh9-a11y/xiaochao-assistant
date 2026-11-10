using System.IO;
using System.Reflection;
using System.Runtime.InteropServices;
using System.Text.Json;
using System.Windows;
using System.Windows.Media;
using System.Windows.Media.Imaging;
using System.Windows.Threading;
using StarMap.App.Platform;

internal static class Program
{
    [STAThread]
    static int Main(string[] args)
    {
        int option = Array.IndexOf(args, "--output");
        if (option < 0 || option + 1 == args.Length) throw new ArgumentException("--output <new-evidence-directory> is required");
        string output = Path.GetFullPath(args[option + 1]);
        if (Directory.Exists(output) || File.Exists(output)) throw new IOException("Preserve existing evidence; use a new output directory.");
        Directory.CreateDirectory(output);
        int qtOracle = Array.IndexOf(args, "--qt-oracle");
        if (qtOracle >= 0) return SourceAffineRegression.CompareQt(args[qtOracle + 1], output);
        if (args.Contains("--source-affine")) return SourceAffineRegression.Run(output);
        if (args.Contains("--geometry-boundary")) return OverlayGeometryBoundaryRegression.Run(output);
        var checks = new List<object>(); int exit = 0;
        void Check(bool passed, string name)
        {
            checks.Add(new { name, passed }); Console.WriteLine((passed ? "ok " : "FAIL ") + name);
            if (!passed) throw new InvalidDataException(name);
        }
        void Reject(Action action, string name)
        {
            bool rejected = false;
            try { action(); } catch (Exception e) when (e is ArgumentException or InvalidDataException or OperationCanceledException or InvalidOperationException) { rejected = true; }
            Check(rejected, name);
        }

        try
        {
            // All pixels are synthetic. No app startup, overlay HWND, Show/Activate, capture, input or game access.
            byte[] colors = [0, 0, 255, 255, 0, 255, 0, 255, 255, 0, 0, 255, 255, 255, 255, 255];
            var display = new WpfOverlayBgra32(2, 2, 8, colors);
            colors[2] = 1;
            Check(display.CopyPixels()[2] == 255, "display-constructor-owns-a-copy");
            byte[] escaped = display.CopyPixels(); escaped[2] = 2;
            Check(display.CopyPixels()[2] == 255, "display-readback-cannot-mutate-material");
            var client = new WpfOverlayClientGeometry(0, 0, 2, 2, 96);
            var identity = WpfAlignedOverlayRasterizer.Project(display, WpfOverlayHomography.Identity, client);
            Check(identity.CopyPixels().SequenceEqual(display.CopyPixels()), "identity-H-keeps-exact-pixel-centers-and-alpha");

            var offset = new WpfOverlayHomography(1, 0, 1, 0, 1, 1, 0, 0, 1);
            var translated = WpfAlignedOverlayRasterizer.Project(display, offset, new(0, 0, 4, 4, 96));
            Check(Pixel(translated, 1, 1, 255, 0, 0) && Pixel(translated, 2, 1, 0, 255, 0)
                && Pixel(translated, 1, 2, 0, 0, 255) && Pixel(translated, 2, 2, 255, 255, 255)
                && Alpha(translated, 0, 0) == 0 && Alpha(translated, 3, 3) == 0, "H-translation-clips-once-in-full-client-domain");
            var movedClient = new WpfOverlayClientGeometry(-1920, -300, 4, 4, 144);
            var moved = WpfAlignedOverlayRasterizer.Project(display, offset, movedClient);
            Check(moved.CopyPixels().SequenceEqual(translated.CopyPixels()), "negative-screen-origin-and-DPI-never-enter-H");
            var dips = movedClient.DipSize(144, 144);
            Check(Math.Abs(dips.Width - 8d / 3) < 1e-12 && movedClient.Left == -1920 && movedClient.Top == -300,
                "only-client-viewport-converts-to-DIP-once");

            var rotated = WpfAlignedOverlayRasterizer.Project(display, new(0, -1, 2, 1, 0, 0, 0, 0, 1), client);
            Check(Pixel(rotated, 0, 0, 0, 0, 255) && Pixel(rotated, 1, 0, 255, 0, 0)
                && Pixel(rotated, 0, 1, 255, 255, 255) && Pixel(rotated, 1, 1, 0, 255, 0), "independent-clockwise-rotation-oracle");
            var scaled = WpfAlignedOverlayRasterizer.Project(display, new(2, 0, 0, 0, 2, 0, 0, 0, 1), new(0, 0, 4, 4, 96));
            Check(Pixel(scaled, 0, 0, 255, 0, 0) && Pixel(scaled, 3, 0, 0, 255, 0)
                && Pixel(scaled, 0, 3, 0, 0, 255) && Pixel(scaled, 3, 3, 255, 255, 255)
                && Pixel(scaled, 1, 1, 159, 64, 64, tolerance: 1), "scale-two-bilinear-center-oracle");

            // For H=(x,y)/(1+x/2), target (.5,.5) comes from (2/3,2/3).
            // Its sample weights are 25/36,5/36,5/36,1/36, derived without the production inverse.
            var perspective = WpfAlignedOverlayRasterizer.Project(display, new(1, 0, 0, 0, 1, 0, .5, 0, 1), client);
            Check(Pixel(perspective, 0, 0, 184, 43, 43, tolerance: 1) && Alpha(perspective, 1, 0) == 0,
                "full-projective-H-is-not-silently-treated-as-affine");
            var normalized = WpfAlignedOverlayRasterizer.Project(display, new(7, 0, 0, 0, 7, 0, 3.5, 0, 7), client);
            Check(normalized.CopyPixels().Zip(perspective.CopyPixels()).All(p => Math.Abs(p.First - p.Second) <= 1),
                "homogeneous-H-scale-does-not-change-projection");

            var alphaDisplay = new WpfOverlayBgra32(2, 1, 8, [0, 0, 255, 255, 255, 0, 0, 0]);
            var alphaScale = WpfAlignedOverlayRasterizer.Project(alphaDisplay, new(2, 0, 0, 0, 1, 0, 0, 0, 1), new(0, 0, 4, 1, 96));
            Check(Pixel(alphaScale, 1, 0, 255, 0, 0, 191, 1), "transparent-neighbor-does-not-add-blue-fringe");
            byte[] padded = [0, 0, 255, 255, 0, 255, 0, 255, 77, 77, 77, 77,
                255, 0, 0, 255, 255, 255, 255, 255, 88, 88, 88, 88];
            Check(WpfAlignedOverlayRasterizer.Project(new(2, 2, 12, padded), WpfOverlayHomography.Identity, client)
                .CopyPixels().SequenceEqual(identity.CopyPixels()), "padded-source-stride-is-not-treated-as-tight-pixels");

            Reject(() => new WpfOverlayBgra32(2, 2, 8, new byte[15]), "short-source-is-rejected");
            Reject(() => WpfAlignedOverlayRasterizer.Project(display, WpfOverlayHomography.Identity, new(0, 0, 0, 2, 96)), "zero-client-size-is-rejected");
            Reject(() => WpfAlignedOverlayRasterizer.Project(display, new(double.NaN, 0, 0, 0, 1, 0, 0, 0, 1), client), "nonfinite-H-is-rejected");
            Reject(() => WpfAlignedOverlayRasterizer.Project(display, default, client), "singular-H-is-rejected");
            Reject(() => WpfAlignedOverlayRasterizer.Project(display, new(1, 0, 0, 0, 1, 0, 1, 0, -1), client), "projective-horizon-crossing-is-rejected");
            using (var cancel = new CancellationTokenSource())
            {
                cancel.Cancel(); Reject(() => WpfAlignedOverlayRasterizer.Project(display, WpfOverlayHomography.Identity, client, cancel.Token), "pre-canceled-projection-does-not-produce-material");
            }

            using var surface = new WpfAlignedOverlaySurface();
            Check(surface.NativeHandle == 0 && !surface.IsVisible && !surface.HasMaterial, "constructor-does-not-create-or-show-HWND");
            object owner = new(); var ticket = surface.Reserve(owner);
            var prepared = WpfAlignedOverlaySurface.Prepare(ticket, display, WpfOverlayHomography.Identity, client);
            Check(ReferenceEquals(ticket.OwnerToken, owner) && prepared.Image.IsFrozen && surface.NativeHandle == 0,
                "preparation-retains-opaque-source-and-creates-only-frozen-material");
            Reject(() => WpfAlignedOverlaySurface.Prepare(ticket, display, WpfOverlayHomography.Identity, client, double.NaN), "nonfinite-opacity-is-rejected");
            var visual = new WpfAlignedOverlayVisual(); visual.Apply(prepared, 96, 96);
            var rendered = Render(visual, 2, 2, 96);
            Check(Read(rendered).SequenceEqual(display.CopyPixels()), "actual-production-viewport-renders-four-synthetic-pixels-offscreen");
            Save(rendered, Path.Combine(output, "identity-offscreen.png"));
            var highDpiFrame = WpfAlignedOverlaySurface.Prepare(ticket, display, offset, movedClient);
            visual.Apply(highDpiFrame, 144, 144);
            var highDpi = Render(visual, 4, 4, 144);
            Check(Read(highDpi).SequenceEqual(translated.CopyPixels()), "production-viewport-144DPI-and-negative-origin-use-one-conversion");
            Save(highDpi, Path.Combine(output, "dpi144-offscreen.png"));
            var half = WpfAlignedOverlaySurface.Prepare(ticket, display, WpfOverlayHomography.Identity, client, .5);
            visual.Apply(half, 96, 96); byte[] halfPixels = Read(Render(visual, 2, 2, 96));
            Check(Math.Abs(halfPixels[2] - 128) <= 1 && Math.Abs(halfPixels[3] - 128) <= 1,
                "production-viewport-opacity-applies-once-to-premultiplied-output");
            visual.ClearMaterial(); Check(!visual.HasMaterial && visual.Width == 0 && visual.Height == 0, "withdraw-visual-path-drops-material-and-size");

            surface.Withdraw();
            Check(!surface.IsCurrent(ticket) && !surface.TryApply(ticket, prepared) && !surface.ShowCurrent(ticket)
                && surface.NativeHandle == 0 && !surface.HasMaterial, "withdraw-rejects-late-result-before-any-native-window-creation");
            var renewed = surface.Reserve(owner);
            Check(surface.IsCurrent(renewed) && !surface.IsCurrent(ticket) && !surface.TryApply(renewed, prepared),
                "reusing-owner-token-does-not-revive-old-prepared-material");
            using (var other = new WpfAlignedOverlaySurface())
                Check(!other.TryApply(renewed, prepared) && other.NativeHandle == 0, "ticket-cannot-cross-surface-instances");
            surface.Dispose();
            Check(!surface.IsCurrent(renewed) && !surface.TryApply(renewed, prepared) && !surface.ShowCurrent(renewed), "dispose-revokes-owner-and-cannot-reopen-from-late-work");
            Reject(() => surface.Reserve(new object()), "disposed-surface-cannot-reserve-new-work");

            // Pure hook dispatch exercises the production native-policy branches without creating a HWND.
            MethodInfo hook = typeof(WpfAlignedOverlaySurface).GetMethod("WindowMessage", BindingFlags.Instance | BindingFlags.NonPublic)!;
            object[] hitArgs = [(nint)0, 0x84, (nint)0, (nint)0, false];
            object[] mouseArgs = [(nint)0, 0x21, (nint)0, (nint)0, false];
            Check((nint)hook.Invoke(surface, hitArgs)! == (nint)(-1) && (bool)hitArgs[4]
                && (nint)hook.Invoke(surface, mouseArgs)! == (nint)3 && (bool)mouseArgs[4], "production-hook-is-hit-transparent-and-mouse-noactivate");
            Check((WpfAlignedOverlayNative.RequiredExtendedStyle & 0x080000A0) == 0x080000A0,
                "native-style-contains-real-NOACTIVATE-TRANSPARENT-TOOLWINDOW");
            var readback = new WpfOverlayCaptureExclusion(WpfOverlayCaptureExclusionState.RequestedValueObserved, 0x11, true, 0, true, 0, 0x11);
            Check(readback.RequestedValueObserved && !readback.PixelExclusionVerified
                && surface.CaptureExclusion.State == WpfOverlayCaptureExclusionState.NotRequested,
                "capture-affinity-readback-is-not-labeled-pixel-exclusion-proof");

            var native = new RecordingOverlayWindow();
            using var staged = new WpfAlignedOverlaySurface(Dispatcher.CurrentDispatcher, native);
            var original = staged.Reserve(new object());
            var originalFrame = WpfAlignedOverlaySurface.Prepare(original, display, WpfOverlayHomography.Identity, client);
            Check(staged.TryApply(original, originalFrame) && staged.HasMaterial && staged.IsCurrent(original)
                && native.CreationCount == 1 && native.ShowCommandCount == 0 && !staged.IsVisible,
                "real-surface-apply-commits-current-material-with-recording-only-native-boundary");
            Check(Read(Render(staged.Visual, 2, 2, 96)).SequenceEqual(display.CopyPixels()),
                "real-surface-visual-renders-current-material-offscreen");
            var abandoned = staged.Stage(new object());
            var abandonedFrame = WpfAlignedOverlaySurface.Prepare(abandoned, rotated, WpfOverlayHomography.Identity, client);
            Check(staged.IsCurrent(original) && staged.IsPending(abandoned)
                && Read(Render(staged.Visual, 2, 2, 96)).SequenceEqual(display.CopyPixels()),
                "stage-keeps-current-ticket-and-actual-current-pixels");
            var sibling = staged.Stage(new object());
            Check(abandoned.PreparationCancellation.IsCancellationRequested && !staged.IsPending(abandoned)
                && staged.IsPending(sibling) && staged.IsCurrent(original) && !staged.TryApply(abandoned, abandonedFrame),
                "new-stage-cancels-only-old-pending-and-rejects-its-late-material");
            Reject(() => WpfAlignedOverlaySurface.Prepare(abandoned, display, WpfOverlayHomography.Identity, client),
                "replaced-pending-preparation-observes-cancellation");
            Check(!staged.TryApply(sibling, abandonedFrame) && staged.IsCurrent(original)
                && Read(Render(staged.Visual, 2, 2, 96)).SequenceEqual(display.CopyPixels()),
                "wrong-pending-material-cannot-replace-current-pixels");
            var siblingFrame = WpfAlignedOverlaySurface.Prepare(sibling, rotated, WpfOverlayHomography.Identity, client);
            Check(staged.TryApply(sibling, siblingFrame) && staged.IsCurrent(sibling) && !staged.IsPending(sibling)
                && !staged.IsCurrent(original) && original.PreparationCancellation.IsCancellationRequested
                && Read(Render(staged.Visual, 2, 2, 96)).SequenceEqual(rotated.CopyPixels()),
                "successful-sibling-apply-atomically-switches-real-pixels-and-owner");
            Check(!staged.TryApply(original, originalFrame) && native.CreationCount == 1 && native.ShowCommandCount == 0,
                "old-current-cannot-overwrite-committed-sibling-or-show-window");

            var failing = staged.Stage(new object());
            var failingFrame = WpfAlignedOverlaySurface.Prepare(failing, display, WpfOverlayHomography.Identity, client);
            native.FailNextPlacement = true;
            Reject(() => staged.TryApply(failing, failingFrame), "staged-native-placement-failure-is-explicit");
            Check(staged.IsCurrent(sibling) && !staged.IsPending(failing) && staged.HasMaterial
                && Read(Render(staged.Visual, 2, 2, 96)).SequenceEqual(rotated.CopyPixels()),
                "failed-sibling-apply-restores-current-material-and-owner");

            var pendingAtDpi = staged.Stage(new object());
            var lateAtDpi = WpfAlignedOverlaySurface.Prepare(pendingAtDpi, display, WpfOverlayHomography.Identity, client);
            var dpiArgs = native.RaiseDpiChange(144, new Rect(9999, 8888, 20, 20));
            Check(dpiArgs.Handled && native.RetirementCount == 1 && native.UnhandledDpiCount == 0
                && !native.IsCreated && !staged.HasMaterial && !staged.IsCurrent(sibling) && !staged.IsPending(pendingAtDpi)
                && sibling.PreparationCancellation.IsCancellationRequested && pendingAtDpi.PreparationCancellation.IsCancellationRequested,
                "dpi-event-stops-framework-default-and-retires-target-after-revoking-both-slots");
            Check(!staged.TryApply(pendingAtDpi, lateAtDpi) && !staged.ShowCurrent(sibling) && native.CreationCount == 1,
                "dpi-retirement-never-recreates-from-late-old-work");
            native.NativeDpi = 144; native.DeviceDpi = new DpiScale(1.5, 1.5);
            var afterDpi = staged.Reserve(new object());
            var physicalTarget = new WpfOverlayClientGeometry(-3200, -240, 2, 2, 96);
            var afterDpiFrame = WpfAlignedOverlaySurface.Prepare(afterDpi, display, WpfOverlayHomography.Identity, physicalTarget);
            Check(staged.TryApply(afterDpi, afterDpiFrame) && native.CreationCount == 2 && native.CreationClient == physicalTarget
                && staged.ActualDpi == 144 && Math.Abs(staged.Visual.Width - 4d / 3) < 1e-12
                && Read(Render(staged.Visual, 2, 2, 144)).SequenceEqual(display.CopyPixels()),
                "new-explicit-grant-recreates-at-physical-target-and-uses-surface-DPI-independent-of-capture-DPI");

            var position = native.Message(new()
            {
                Window = (nint)123, InsertAfter = (nint)456, X = 9999, Y = 8888, Width = 20, Height = 30,
                Flags = WpfAlignedOverlayNative.NoZOrder | WpfAlignedOverlayNative.NoMove | WpfAlignedOverlayNative.NoSize | 0x4000
            });
            Check(!position.Handled && position.Position.Window == (nint)123 && position.Position.InsertAfter == (nint)456
                && position.Position.X == -3200 && position.Position.Y == -240 && position.Position.Width == 2 && position.Position.Height == 2
                && (position.Position.Flags & (WpfAlignedOverlayNative.NoMove | WpfAlignedOverlayNative.NoSize)) == 0
                && (position.Position.Flags & WpfAlignedOverlayNative.NoActivatePosition) != 0,
                "production-WINDOWPOS-hook-constrains-physical-rect-without-consuming-WPF-message");
            Check(Marshal.SizeOf<WpfAlignedOverlayNative.WindowPosition>() == (IntPtr.Size == 8 ? 40 : 28)
                && Marshal.OffsetOf<WpfAlignedOverlayNative.WindowPosition>(nameof(WpfAlignedOverlayNative.WindowPosition.Flags)).ToInt32() == IntPtr.Size * 2 + 16,
                "WINDOWPOS-layout-has-native-pointer-size-and-UINT-flags-offset");

            var pendingAtWithdraw = staged.Stage(new object());
            var lateAtWithdraw = WpfAlignedOverlaySurface.Prepare(pendingAtWithdraw, rotated, WpfOverlayHomography.Identity, physicalTarget);
            staged.Withdraw();
            var revokedPosition = native.Message(new() { X = 700, Y = 800, Width = 100, Height = 100, Flags = WpfAlignedOverlayNative.ShowWindow });
            Check(!staged.IsCurrent(afterDpi) && !staged.IsPending(pendingAtWithdraw) && !staged.HasMaterial
                && !staged.TryApply(pendingAtWithdraw, lateAtWithdraw) && !staged.ShowCurrent(afterDpi)
                && (revokedPosition.Position.Flags & WpfAlignedOverlayNative.ShowWindow) == 0
                && (revokedPosition.Position.Flags & (WpfAlignedOverlayNative.HideWindow | WpfAlignedOverlayNative.NoMove | WpfAlignedOverlayNative.NoSize))
                    == (WpfAlignedOverlayNative.HideWindow | WpfAlignedOverlayNative.NoMove | WpfAlignedOverlayNative.NoSize),
                "withdraw-revokes-both-slots-and-late-position-message-cannot-request-show-or-new-geometry");
            var mismatched = staged.Reserve(new object());
            var mismatchedFrame = WpfAlignedOverlaySurface.Prepare(mismatched, display, WpfOverlayHomography.Identity, physicalTarget);
            native.DeviceDpi = new DpiScale(1, 1);
            Reject(() => staged.TryApply(mismatched, mismatchedFrame), "surface-device-and-native-DPI-mismatch-is-rejected");
            Check(!staged.HasMaterial && !staged.IsCurrent(mismatched) && native.ShowCommandCount == 0,
                "device-DPI-rejection-does-not-present-material-or-show");
            native.DeviceDpi = new DpiScale(1.5, 1.5);
            var reservePredecessor = staged.Reserve(new object());
            var reservePredecessorFrame = WpfAlignedOverlaySurface.Prepare(reservePredecessor, display, WpfOverlayHomography.Identity, physicalTarget);
            Check(staged.TryApply(reservePredecessor, reservePredecessorFrame), "current-material-can-be-reapplied-after-DPI-mismatch-recovery");
            var reservePending = staged.Stage(new object());
            var finalCurrent = staged.Reserve(new object());
            Check(!staged.HasMaterial && !staged.IsCurrent(reservePredecessor) && !staged.IsPending(reservePending)
                && reservePredecessor.PreparationCancellation.IsCancellationRequested && reservePending.PreparationCancellation.IsCancellationRequested
                && staged.IsCurrent(finalCurrent), "explicit-Reserve-still-withdraws-old-material-and-both-slots");
            var finalPending = staged.Stage(new object());
            var finalLate = WpfAlignedOverlaySurface.Prepare(finalPending, display, WpfOverlayHomography.Identity, physicalTarget);
            staged.Dispose();
            Check(!staged.IsCurrent(finalCurrent) && !staged.IsPending(finalPending)
                && finalCurrent.PreparationCancellation.IsCancellationRequested && finalPending.PreparationCancellation.IsCancellationRequested
                && !staged.TryApply(finalPending, finalLate) && native.ShowCommandCount == 0,
                "dispose-revokes-current-and-pending-with-no-native-show");
        }
        catch (Exception error) { exit = 1; checks.Add(new { name = "failure", passed = false, error = error.ToString() }); Console.Error.WriteLine(error); }
        File.WriteAllText(Path.Combine(output, "result.json"), JsonSerializer.Serialize(new
        {
            exit, checks,
            scope = "Linked production projection, real surface ticket/visual commit and RenderTargetBitmap; synthetic pixels only. The OS HWND/placement/device-DPI boundary alone is a recording port for positive commits. No overlay HWND was created or shown.",
            pending = new[] { "Native visible pixels and foreground preservation", "Real WS_EX styles/hit transparency", "Mixed-monitor negative-origin placement and DPI transition", "SetWindowDisplayAffinity API and capture-pixel exclusion", "Runtime grant/foreground host integration and real-game use", "End-to-end timing" }
        }, new JsonSerializerOptions { WriteIndented = true }));
        return exit;
    }

    static byte Alpha(WpfOverlayBgra32 image, int x, int y) => image.CopyPixels()[y * image.Stride + x * 4 + 3];
    static bool Pixel(WpfOverlayBgra32 image, int x, int y, int r, int g, int b, int a = 255, int tolerance = 0)
    {
        byte[] pixels = image.CopyPixels(); int at = y * image.Stride + x * 4;
        return Math.Abs(pixels[at] - b) <= tolerance && Math.Abs(pixels[at + 1] - g) <= tolerance
            && Math.Abs(pixels[at + 2] - r) <= tolerance && Math.Abs(pixels[at + 3] - a) <= tolerance;
    }
    static RenderTargetBitmap Render(FrameworkElement visual, int width, int height, double dpi)
    {
        VisualTreeHelper.SetRootDpi(visual, new DpiScale(dpi / 96, dpi / 96));
        visual.InvalidateMeasure(); visual.InvalidateArrange();
        visual.Measure(new Size(width * 96d / dpi, height * 96d / dpi));
        visual.Arrange(new Rect(0, 0, width * 96d / dpi, height * 96d / dpi)); visual.UpdateLayout();
        var bitmap = new RenderTargetBitmap(width, height, dpi, dpi, PixelFormats.Pbgra32); bitmap.Render(visual); return bitmap;
    }
    static byte[] Read(BitmapSource bitmap)
    { var pixels = new byte[bitmap.PixelWidth * bitmap.PixelHeight * 4]; bitmap.CopyPixels(pixels, bitmap.PixelWidth * 4, 0); return pixels; }
    static void Save(BitmapSource bitmap, string path)
    { var encoder = new PngBitmapEncoder(); encoder.Frames.Add(BitmapFrame.Create(bitmap)); using var file = File.Create(path); encoder.Save(file); }
}
