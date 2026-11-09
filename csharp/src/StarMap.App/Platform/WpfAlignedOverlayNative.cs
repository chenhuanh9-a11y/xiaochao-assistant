using System.ComponentModel;
using System.Runtime.InteropServices;

namespace StarMap.App.Platform;

public enum WpfOverlayCaptureExclusionState { NotRequested, UnsupportedOs, ApiUnavailable, SetFailed, ReadbackFailed, ValueMismatch, RequestedValueObserved }

/// <summary>API capability/readback only. Even a matching value is not proof of capture-pixel exclusion.</summary>
public sealed record WpfOverlayCaptureExclusion(WpfOverlayCaptureExclusionState State, uint RequestedValue = 0x11,
    bool SetSucceeded = false, int SetError = 0, bool ReadbackSucceeded = false, int ReadbackError = 0, uint? ObservedValue = null)
{
    public bool RequestedValueObserved => State == WpfOverlayCaptureExclusionState.RequestedValueObserved;
    public bool PixelExclusionVerified => false;
}

internal static class WpfAlignedOverlayNative
{
    internal const int PopupStyle = unchecked((int)0x80000000);
    internal const int NoActivate = 0x08000000, ToolWindow = 0x80, Transparent = 0x20, Layered = 0x80000;
    internal const int RequiredExtendedStyle = NoActivate | ToolWindow | Transparent | Layered;
    internal const uint NoSize = 1, NoMove = 2, NoZOrder = 4, NoActivatePosition = 0x10, ShowWindow = 0x40, HideWindow = 0x80;
    [StructLayout(LayoutKind.Sequential)]
    internal struct WindowPosition
    {
        internal nint Window, InsertAfter;
        internal int X, Y, Width, Height;
        internal uint Flags;
    }

    /// <summary>Pure WINDOWPOS policy. NOACTIVATE here is defensive only: Win32 may ignore its modification in this message.</summary>
    internal static WindowPosition ConstrainPosition(WindowPosition position, WpfOverlayClientGeometry? client, bool allowVisible)
    {
        position.Flags |= NoActivatePosition;
        if (client is { } rectangle)
        {
            position.X = rectangle.Left; position.Y = rectangle.Top;
            position.Width = rectangle.Width; position.Height = rectangle.Height;
            position.Flags &= ~(NoMove | NoSize);
        }
        else position.Flags |= NoMove | NoSize | NoZOrder;
        if (!allowVisible) position.Flags = (position.Flags & ~ShowWindow) | HideWindow;
        return position;
    }
    [DllImport("user32.dll", SetLastError = true)] [return: MarshalAs(UnmanagedType.Bool)]
    static extern bool SetWindowPos(nint window, nint after, int x, int y, int width, int height, uint flags);
    [DllImport("user32.dll", EntryPoint = "GetWindowLongW", SetLastError = true)] static extern int GetWindowLong(nint window, int index);
    [DllImport("user32.dll")] [return: MarshalAs(UnmanagedType.Bool)] internal static extern bool IsWindowVisible(nint window);
    [DllImport("user32.dll")] internal static extern uint GetDpiForWindow(nint window);
    [DllImport("user32.dll", SetLastError = true)] static extern nint SetThreadDpiAwarenessContext(nint context);
    [DllImport("user32.dll")] static extern nint GetWindowDpiAwarenessContext(nint window);
    [DllImport("user32.dll")] static extern int GetAwarenessFromDpiAwarenessContext(nint context);
    [DllImport("user32.dll", SetLastError = true)] [return: MarshalAs(UnmanagedType.Bool)]
    static extern bool SetWindowDisplayAffinity(nint window, uint affinity);
    [DllImport("user32.dll", SetLastError = true)] [return: MarshalAs(UnmanagedType.Bool)]
    static extern bool GetWindowDisplayAffinity(nint window, out uint affinity);

    internal static IDisposable PhysicalCoordinates() => new DpiScope();
    sealed class DpiScope : IDisposable
    {
        nint previous;
        internal DpiScope()
        {
            previous = SetThreadDpiAwarenessContext((nint)(-4));
            if (previous == 0) throw Error("SetThreadDpiAwarenessContext(PER_MONITOR_AWARE_V2)");
        }
        public void Dispose()
        {
            nint old = previous; previous = 0;
            if (old != 0 && SetThreadDpiAwarenessContext(old) == 0) throw Error("RestoreThreadDpiAwarenessContext");
        }
    }

    internal static void ValidatePassiveWindow(nint handle)
    {
        Marshal.SetLastPInvokeError(0);
        int style = GetWindowLong(handle, -20);
        if (style == 0 && Marshal.GetLastWin32Error() != 0) throw Error("GetWindowLong(GWL_EXSTYLE)");
        if ((style & RequiredExtendedStyle) != RequiredExtendedStyle)
            throw new InvalidOperationException("Overlay HWND is missing NOACTIVATE, TOOLWINDOW, TRANSPARENT or LAYERED.");
        if (GetAwarenessFromDpiAwarenessContext(GetWindowDpiAwarenessContext(handle)) != 2)
            throw new InvalidOperationException("Overlay HWND is not per-monitor DPI aware.");
    }

    internal static void Place(nint window, WpfOverlayClientGeometry client, bool show)
    {
        client.Validate(); using var dpi = PhysicalCoordinates();
        // No logical screen-origin conversion: these coordinates are already physical and may be negative.
        if (!SetWindowPos(window, (nint)(-1), client.Left, client.Top, client.Width, client.Height,
            NoActivatePosition | (show ? ShowWindow : 0))) throw Error("SetWindowPos(overlay physical client)");
    }

    internal static void Hide(nint window)
    {
        if (!SetWindowPos(window, 0, 0, 0, 0, 0, NoActivatePosition | NoMove | NoSize | HideWindow | 4))
            throw Error("SetWindowPos(overlay hide)");
    }

    internal static WpfOverlayCaptureExclusion RequestCaptureExclusion(nint window)
    {
        if (!OperatingSystem.IsWindowsVersionAtLeast(10, 0, 19041))
            return new(WpfOverlayCaptureExclusionState.UnsupportedOs);
        try
        {
            bool set = SetWindowDisplayAffinity(window, 0x11);
            int setError = set ? 0 : Marshal.GetLastWin32Error();
            bool read = GetWindowDisplayAffinity(window, out uint observed);
            int readError = read ? 0 : Marshal.GetLastWin32Error();
            var state = !set ? WpfOverlayCaptureExclusionState.SetFailed : !read ? WpfOverlayCaptureExclusionState.ReadbackFailed
                : observed != 0x11 ? WpfOverlayCaptureExclusionState.ValueMismatch : WpfOverlayCaptureExclusionState.RequestedValueObserved;
            return new(state, 0x11, set, setError, read, readError, read ? observed : null);
        }
        catch (EntryPointNotFoundException) { return new(WpfOverlayCaptureExclusionState.ApiUnavailable); }
        catch (DllNotFoundException) { return new(WpfOverlayCaptureExclusionState.ApiUnavailable); }
    }

    static Win32Exception Error(string operation) => new(Marshal.GetLastWin32Error(), operation);
}
