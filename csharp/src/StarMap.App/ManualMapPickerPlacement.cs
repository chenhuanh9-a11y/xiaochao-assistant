using System.ComponentModel;
using System.Runtime.InteropServices;
using System.Windows;
using System.Windows.Interop;
using System.Windows.Threading;
using StarMap.App.Platform;

namespace StarMap.App;

internal readonly record struct PickerPhysicalRect(int Left, int Top, int Width, int Height)
{
    internal int Right => checked(Left + Width);
    internal int Bottom => checked(Top + Height);
}

/// <summary>Own-window placement only. Native sizes are measured after WPF's
/// Loaded DPI/layout work; capture DPI never sizes this window.</summary>
internal sealed class ManualMapPickerPlacement : IDisposable
{
    readonly Window window;
    readonly Action<string> status;
    DispatcherOperation? pending;
    TaskCompletionSource settled = Completed();
    long revision;
    bool disposed;

    internal ManualMapPickerPlacement(Window window, Action<string> status)
    {
        this.window = window; this.status = status;
        window.IsVisibleChanged += VisibilityChanged; window.Closed += Closed;
    }
    internal string? LastError { get; private set; }
    internal Task DrainAsync() => settled.Task;

    internal void Request(PickerPhysicalRect? client, Func<bool>? stillCurrent = null)
    {
        window.Dispatcher.VerifyAccess(); Cancel();
        if (disposed || !window.IsVisible) return;
        LastError = null; var completion = settled = new(TaskCreationOptions.RunContinuationsAsynchronously);
        long request = revision; nint hwnd = new WindowInteropHelper(window).Handle;
        bool Current() => !disposed && request == revision && window.IsVisible && hwnd != 0
            && new WindowInteropHelper(window).Handle == hwnd && (stillCurrent?.Invoke() ?? true);
        void Run(Action stage)
        {
            if (!Current()) { completion.TrySetResult(); return; }
            try { stage(); }
            catch (Exception error) when (error is Win32Exception or InvalidOperationException or OverflowException or ArgumentOutOfRangeException)
            { LastError = error.ToString(); completion.TrySetResult(); status("选图窗口定位失败，请关闭面板后重试。"); }
        }
        void Queue(Action stage) => pending = window.Dispatcher.BeginInvoke(DispatcherPriority.Loaded, new Action(() => Run(stage)));
        Run(() =>
        {
            nint primary = PrimaryMonitor();
            nint target = client is { Width: > 0, Height: > 0 } rect ? MonitorAt(rect) : primary;
            MoveToMonitor(hwnd, target);
            Queue(() =>
            {
                window.UpdateLayout(); var size = ReadWindow(hwnd);
                if (client is { } anchor && (anchor.Width < size.Width || anchor.Height < size.Height))
                {
                    // One-way fallback: a smaller primary DPI must never make
                    // this request reconsider the original game monitor.
                    MoveToMonitor(hwnd, primary);
                    Queue(() => Finish(hwnd, null, primary, completion));
                }
                else Finish(hwnd, client, target, completion);
            });
        });
    }

    void Finish(nint hwnd, PickerPhysicalRect? client, nint monitor, TaskCompletionSource completion)
    {
        window.UpdateLayout(); var size = ReadWindow(hwnd); var work = WorkArea(monitor);
        var location = Plan(client ?? work, work, size.Width, size.Height);
        Move(hwnd, location.X, location.Y); pending = null; completion.TrySetResult();
    }

    internal static (int X, int Y) Plan(PickerPhysicalRect anchor, PickerPhysicalRect work, int width, int height)
    {
        if (work.Width <= 0 || work.Height <= 0 || width <= 0 || height <= 0)
            throw new ArgumentOutOfRangeException(nameof(work));
        int x = checked(anchor.Left + (anchor.Width - width) / 2);
        int y = checked(anchor.Top + (anchor.Height - height) / 2);
        return (Math.Max(work.Left, Math.Min(x, work.Right - width)),
            Math.Max(work.Top, Math.Min(y, work.Bottom - height)));
    }

    static void MoveToMonitor(nint hwnd, nint monitor)
    {
        using var physical = CaptureNative.PhysicalCoordinates();
        if (MonitorFromWindow(hwnd, 1) == monitor) return;
        var area = WorkArea(monitor); var size = ReadWindow(hwnd);
        Move(hwnd, checked(area.Left + (area.Width - size.Width) / 2), checked(area.Top + (area.Height - size.Height) / 2));
    }
    internal static PickerPhysicalRect ReadWindow(nint hwnd)
    {
        using var physical = CaptureNative.PhysicalCoordinates();
        if (!GetWindowRect(hwnd, out var rect)) throw new Win32Exception(Marshal.GetLastWin32Error());
        return rect.Value;
    }
    internal static PickerPhysicalRect PrimaryWorkArea() => WorkArea(PrimaryMonitor());
    static nint PrimaryMonitor() => MonitorFromPoint(new(0, 0), 1);
    static nint MonitorAt(PickerPhysicalRect client)
    {
        using var physical = CaptureNative.PhysicalCoordinates();
        return MonitorFromPoint(new(checked(client.Left + client.Width / 2), checked(client.Top + client.Height / 2)), 1);
    }
    static PickerPhysicalRect WorkArea(nint monitor)
    {
        using var physical = CaptureNative.PhysicalCoordinates();
        var info = new MonitorInfo { Size = Marshal.SizeOf<MonitorInfo>() };
        if (!GetMonitorInfo(monitor, ref info)) throw new Win32Exception(Marshal.GetLastWin32Error());
        return info.Work.Value;
    }
    static void Move(nint hwnd, int x, int y)
    {
        using var physical = CaptureNative.PhysicalCoordinates();
        // Preserve the actual WPF/native size; never resize from game DPI.
        if (!SetWindowPos(hwnd, 0, x, y, 0, 0, 1 | 4 | 0x10)) throw new Win32Exception(Marshal.GetLastWin32Error());
    }
    void VisibilityChanged(object sender, DependencyPropertyChangedEventArgs e) { if (!window.IsVisible) Cancel(); }
    void Closed(object? sender, EventArgs e) => Dispose();
    void Cancel()
    { revision++; pending?.Abort(); pending = null; settled.TrySetResult(); }
    public void Dispose()
    {
        if (disposed) return;
        disposed = true; Cancel(); window.IsVisibleChanged -= VisibilityChanged; window.Closed -= Closed;
    }
    static TaskCompletionSource Completed()
    { var source = new TaskCompletionSource(TaskCreationOptions.RunContinuationsAsynchronously); source.SetResult(); return source; }

    [StructLayout(LayoutKind.Sequential)] readonly record struct NativePoint(int X, int Y);
    [StructLayout(LayoutKind.Sequential)] struct NativeRect
    { public int Left, Top, Right, Bottom; internal readonly PickerPhysicalRect Value => new(Left, Top, Right - Left, Bottom - Top); }
    [StructLayout(LayoutKind.Sequential)] struct MonitorInfo
    { public int Size; public NativeRect Monitor, Work; public uint Flags; }
    [DllImport("user32.dll")] static extern nint MonitorFromPoint(NativePoint point, uint flags);
    [DllImport("user32.dll")] static extern nint MonitorFromWindow(nint hwnd, uint flags);
    [DllImport("user32.dll", EntryPoint = "GetMonitorInfoW", SetLastError = true)] [return: MarshalAs(UnmanagedType.Bool)]
    static extern bool GetMonitorInfo(nint monitor, ref MonitorInfo info);
    [DllImport("user32.dll", SetLastError = true)] [return: MarshalAs(UnmanagedType.Bool)]
    static extern bool GetWindowRect(nint hwnd, out NativeRect rectangle);
    [DllImport("user32.dll", SetLastError = true)] [return: MarshalAs(UnmanagedType.Bool)]
    static extern bool SetWindowPos(nint hwnd, nint after, int x, int y, int width, int height, uint flags);
}
