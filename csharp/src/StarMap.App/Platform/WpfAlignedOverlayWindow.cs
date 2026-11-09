using System.Windows;
using System.Windows.Interop;
using System.Windows.Media;

namespace StarMap.App.Platform;

/// <summary>Only the OS HWND/placement/device-DPI boundary is replaceable in offscreen tests.</summary>
internal interface IWpfAlignedOverlayWindow : IDisposable
{
    nint Handle { get; }
    bool IsCreated { get; }
    bool IsVisible { get; }
    uint NativeDpi { get; }
    DpiScale DeviceDpi { get; }
    WpfOverlayCaptureExclusion CaptureExclusion { get; }
    void Ensure(WpfOverlayClientGeometry client, FrameworkElement visual, HwndSourceHook hook, HwndDpiChangedEventHandler dpiChanged);
    void ValidatePassive();
    void Place(WpfOverlayClientGeometry client, bool show);
    void Hide();
    void Retire();
}

internal sealed class WpfAlignedOverlayWindow : IWpfAlignedOverlayWindow
{
    HwndSource? source;
    HwndDpiChangedEventHandler? dpiChanged;
    public nint Handle => source?.Handle ?? 0;
    public bool IsCreated => source is not null;
    public bool IsVisible => source is not null && WpfAlignedOverlayNative.IsWindowVisible(source.Handle);
    public uint NativeDpi => source is null ? 0 : WpfAlignedOverlayNative.GetDpiForWindow(source.Handle);
    public DpiScale DeviceDpi
    {
        get
        {
            if (source?.CompositionTarget is not { } target) throw new InvalidOperationException("Overlay composition target is unavailable.");
            var transform = target.TransformToDevice;
            return new(transform.M11, transform.M22);
        }
    }
    public WpfOverlayCaptureExclusion CaptureExclusion { get; private set; } = new(WpfOverlayCaptureExclusionState.NotRequested);

    public void Ensure(WpfOverlayClientGeometry client, FrameworkElement visual, HwndSourceHook hook, HwndDpiChangedEventHandler onDpiChanged)
    {
        if (source is not null) return;
        using var dpi = WpfAlignedOverlayNative.PhysicalCoordinates();
        // Creation starts at the caller's full physical target rectangle, never an initial (0,0) followed by a DPI-changing move.
        var parameters = new HwndSourceParameters("星图 · 当前帧贴合层", client.Width, client.Height)
        {
            WindowStyle = WpfAlignedOverlayNative.PopupStyle,
            ExtendedWindowStyle = WpfAlignedOverlayNative.RequiredExtendedStyle,
            PositionX = client.Left, PositionY = client.Top, UsesPerPixelOpacity = true,
            AcquireHwndFocusInMenuMode = false, TreatAsInputRoot = false, HwndSourceHook = hook
        };
        source = new HwndSource(parameters) { RootVisual = visual, SizeToContent = SizeToContent.Manual };
        source.CompositionTarget!.BackgroundColor = Colors.Transparent;
        dpiChanged = onDpiChanged; source.DpiChanged += dpiChanged;
        ValidatePassive(); CaptureExclusion = WpfAlignedOverlayNative.RequestCaptureExclusion(source.Handle);
    }

    public void ValidatePassive() => WpfAlignedOverlayNative.ValidatePassiveWindow(Handle);
    public void Place(WpfOverlayClientGeometry client, bool show) => WpfAlignedOverlayNative.Place(Handle, client, show);
    public void Hide() { if (source is not null) WpfAlignedOverlayNative.Hide(source.Handle); }
    public void Retire()
    {
        var retired = source; source = null;
        if (retired is null) return;
        if (dpiChanged is not null) retired.DpiChanged -= dpiChanged;
        dpiChanged = null;
        CaptureExclusion = new(WpfOverlayCaptureExclusionState.NotRequested);
        try { retired.RootVisual = null; }
        finally { retired.Dispose(); }
    }
    public void Dispose() => Retire();
}
