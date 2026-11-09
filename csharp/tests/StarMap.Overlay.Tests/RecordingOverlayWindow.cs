using System.Reflection;
using System.Runtime.InteropServices;
using System.Windows;
using System.Windows.Interop;
using StarMap.App.Platform;

/// <summary>Replaces only native HWND/placement/DPI calls. No HWND, ShowWindow, activation, input or capture exists here.</summary>
internal sealed class RecordingOverlayWindow : IWpfAlignedOverlayWindow
{
    HwndSourceHook? hook;
    HwndDpiChangedEventHandler? dpiChanged;
    public nint Handle => 0;
    public bool IsCreated { get; private set; }
    public bool IsVisible { get; private set; }
    public uint NativeDpi { get; set; } = 96;
    public DpiScale DeviceDpi { get; set; } = new(1, 1);
    public WpfOverlayCaptureExclusion CaptureExclusion => new(WpfOverlayCaptureExclusionState.NotRequested);
    internal int CreationCount { get; private set; }
    internal int EnsureCallCount { get; private set; }
    internal int PlacementCallCount { get; private set; }
    internal int ShowCommandCount { get; private set; }
    internal int RetirementCount { get; private set; }
    internal int UnhandledDpiCount { get; private set; }
    internal WpfOverlayClientGeometry? CreationClient { get; private set; }
    internal WpfAlignedOverlayNative.WindowPosition LastPosition { get; private set; }
    internal bool FailNextPlacement { get; set; }
    internal Action<WpfOverlayClientGeometry, bool>? BeforePlacement { get; set; }

    public void Ensure(WpfOverlayClientGeometry client, FrameworkElement visual, HwndSourceHook onMessage, HwndDpiChangedEventHandler onDpiChanged)
    {
        EnsureCallCount++;
        if (IsCreated) return;
        IsCreated = true; CreationCount++; CreationClient = client; hook = onMessage; dpiChanged = onDpiChanged;
    }
    public void ValidatePassive() { if (!IsCreated) throw new InvalidOperationException("No recording window exists."); }
    public void Place(WpfOverlayClientGeometry client, bool show)
    {
        PlacementCallCount++; BeforePlacement?.Invoke(client, show);
        if (FailNextPlacement) { FailNextPlacement = false; throw new InvalidOperationException("Injected OS placement failure."); }
        if (show) ShowCommandCount++;
        var constrained = Message(new()
        {
            Window = (nint)101, InsertAfter = (nint)(-1), X = client.Left, Y = client.Top, Width = client.Width, Height = client.Height,
            Flags = WpfAlignedOverlayNative.NoActivatePosition | (show ? WpfAlignedOverlayNative.ShowWindow : 0)
        });
        LastPosition = constrained.Position;
        if ((LastPosition.Flags & WpfAlignedOverlayNative.HideWindow) != 0) IsVisible = false;
        else if ((LastPosition.Flags & WpfAlignedOverlayNative.ShowWindow) != 0) IsVisible = true;
    }
    public void Hide()
    {
        IsVisible = false;
        if (!IsCreated) return;
        LastPosition = Message(new() { Flags = WpfAlignedOverlayNative.HideWindow | WpfAlignedOverlayNative.NoActivatePosition
            | WpfAlignedOverlayNative.NoMove | WpfAlignedOverlayNative.NoSize | WpfAlignedOverlayNative.NoZOrder }).Position;
    }
    internal (WpfAlignedOverlayNative.WindowPosition Position, bool Handled) Message(WpfAlignedOverlayNative.WindowPosition position)
    {
        nint memory = Marshal.AllocHGlobal(Marshal.SizeOf<WpfAlignedOverlayNative.WindowPosition>());
        try
        {
            Marshal.StructureToPtr(position, memory, false); bool handled = false;
            hook?.Invoke(0, 0x46, 0, memory, ref handled);
            return (Marshal.PtrToStructure<WpfAlignedOverlayNative.WindowPosition>(memory), handled);
        }
        finally { Marshal.FreeHGlobal(memory); }
    }
    internal HwndDpiChangedEventArgs RaiseDpiChange(double newDpi, Rect suggested)
    {
        var args = (HwndDpiChangedEventArgs)Activator.CreateInstance(typeof(HwndDpiChangedEventArgs),
            BindingFlags.Instance | BindingFlags.Public | BindingFlags.NonPublic, null,
            [DeviceDpi, new DpiScale(newDpi / 96, newDpi / 96), suggested], null)!;
        dpiChanged?.Invoke(this, args);
        if (!args.Handled) UnhandledDpiCount++;
        return args;
    }
    public void Retire() { if (IsCreated) RetirementCount++; IsCreated = false; IsVisible = false; }
    public void Dispose() => Retire();
}
