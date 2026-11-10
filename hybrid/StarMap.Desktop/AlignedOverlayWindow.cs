using System.ComponentModel;
using System.Runtime.InteropServices;
using System.Windows;
using System.Windows.Controls;
using System.Windows.Interop;
using System.Windows.Media;
using System.Windows.Threading;

namespace StarMap.Desktop;

/// <summary>Passive physical-client surface; it never authorizes a pose.</summary>
public sealed class AlignedOverlayWindow : Window, IDisposable
{
    private readonly RuntimeViewModel runtime;
    private readonly Func<System.Text.Json.JsonElement, bool> isCurrent;
    private readonly Canvas canvas = new() { ClipToBounds = true };
    private readonly Image image = new() { Stretch = Stretch.Fill };
    private readonly DispatcherTimer timer;
    private bool disposed;
    private (int X, int Y, int Width, int Height)? placedBounds;

    public AlignedOverlayWindow(RuntimeViewModel runtime, Func<System.Text.Json.JsonElement, bool> isCurrent)
    {
        this.runtime = runtime; this.isCurrent = isCurrent;
        Title = "星图 · 当前帧贴合层";
        WindowStyle = WindowStyle.None; ResizeMode = ResizeMode.NoResize;
        AllowsTransparency = true; Background = Brushes.Transparent; ShowInTaskbar = false;
        ShowActivated = false; Focusable = false; Topmost = true;
        Width = Height = 1;
        canvas.Children.Add(image);
        Content = new Viewbox { Stretch = Stretch.Fill, Child = canvas };
        SourceInitialized += (_, _) =>
        {
            var hwnd = new WindowInteropHelper(this).Handle;
            Marshal.SetLastPInvokeError(0);
            var existing = GetWindowLong(hwnd, -20);
            if (existing == 0 && Marshal.GetLastPInvokeError() != 0) throw new Win32Exception(Marshal.GetLastPInvokeError());
            Marshal.SetLastPInvokeError(0);
            if (SetWindowLong(hwnd, -20, existing | 0x08000000 | 0x80 | 0x20) == 0 && Marshal.GetLastPInvokeError() != 0)
                throw new Win32Exception(Marshal.GetLastPInvokeError());
        };
        runtime.PropertyChanged += OnRuntimeChanged;
        timer = new DispatcherTimer(TimeSpan.FromMilliseconds(50), DispatcherPriority.Normal, (_, _) => Present(), Dispatcher);
    }

    private void OnRuntimeChanged(object? sender, PropertyChangedEventArgs e)
    {
        if (e.PropertyName is nameof(RuntimeViewModel.NativeMaterial) or nameof(RuntimeViewModel.ServiceUnavailable)) Present();
    }

    private void Present()
    {
        if (disposed) return;
        var render = runtime.ActiveRender;
        var current = render.HasValue && isCurrent(render.Value);
        if (runtime.IsLive && runtime.NativeMaterial is not null && render.HasValue && !current && !runtime.Busy)
        {
            // Revoke the backend observation as well as this surface. Returning
            // to the same HWND later must not resurrect the withdrawn token.
            _ = runtime.WithdrawNativeDisplayAsync();
        }
        if (!runtime.IsLive || runtime.ServiceUnavailable || runtime.NativeMaterial is null || !render.HasValue || !current)
        {
            if (IsVisible) Hide();
            if (image.Source is not null) image.Source = null;
            return;
        }
        try
        {
            var value = render.Value;
            if (!ReferenceEquals(image.Source, runtime.NativeMaterial)) image.Source = runtime.NativeMaterial;
            if (image.Width != runtime.ImageWidth) image.Width = runtime.ImageWidth;
            if (image.Height != runtime.ImageHeight) image.Height = runtime.ImageHeight;
            if (image.RenderTransform is not MatrixTransform old || runtime.OverlayTransform is not MatrixTransform next || old.Matrix != next.Matrix)
                image.RenderTransform = runtime.OverlayTransform;
            if (canvas.Width != runtime.ClientWidth) canvas.Width = runtime.ClientWidth;
            if (canvas.Height != runtime.ClientHeight) canvas.Height = runtime.ClientHeight;
            var wasVisible = IsVisible;
            if (!wasVisible) Show();
            var bounds = (value.GetProperty("originX").GetInt32(), value.GetProperty("originY").GetInt32(),
                value.GetProperty("clientWidth").GetInt32(), value.GetProperty("clientHeight").GetInt32());
            if (!wasVisible || placedBounds != bounds)
            {
                var hwnd = new WindowInteropHelper(this).Handle;
                if (!SetWindowPos(hwnd, new nint(-1), bounds.Item1, bounds.Item2, bounds.Item3, bounds.Item4, 0x0010 | 0x0200))
                    throw new Win32Exception(Marshal.GetLastWin32Error());
                placedBounds = bounds;
            }
        }
        catch (Exception error) when (error is Win32Exception or InvalidOperationException)
        {
            if (IsVisible) Hide();
            _ = runtime.WithdrawNativeDisplayAsync(error.Message);
        }
    }

    public void Dispose()
    {
        if (disposed) return;
        disposed = true; timer.Stop(); runtime.PropertyChanged -= OnRuntimeChanged; Close();
    }
    [DllImport("user32.dll", EntryPoint = "GetWindowLongW", SetLastError = true)] private static extern int GetWindowLong(nint hwnd, int index);
    [DllImport("user32.dll", EntryPoint = "SetWindowLongW", SetLastError = true)] private static extern int SetWindowLong(nint hwnd, int index, int value);
    [DllImport("user32.dll", SetLastError = true)] [return: MarshalAs(UnmanagedType.Bool)]
    private static extern bool SetWindowPos(nint hwnd, nint after, int x, int y, int width, int height, uint flags);
}
