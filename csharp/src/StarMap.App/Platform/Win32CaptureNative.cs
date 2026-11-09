using System.ComponentModel;
using System.Diagnostics;
using System.Runtime.InteropServices;
using System.Text;

namespace StarMap.App.Platform;

internal static class CaptureNative
{
    [StructLayout(LayoutKind.Sequential)] internal struct Rect { public int Left, Top, Right, Bottom; }
    [StructLayout(LayoutKind.Sequential)] internal struct Point { public int X, Y; }
    [StructLayout(LayoutKind.Sequential)] struct BitmapInfo
    {
        public uint Size; public int Width, Height; public ushort Planes, BitCount;
        public uint Compression, SizeImage; public int XPelsPerMeter, YPelsPerMeter; public uint ClrUsed, ClrImportant, Color;
    }
    internal delegate bool EnumWindowsCallback(nint hwnd, nint parameter);
    [DllImport("user32.dll", SetLastError = true)] [return: MarshalAs(UnmanagedType.Bool)] internal static extern bool EnumWindows(EnumWindowsCallback callback, nint parameter);
    [DllImport("user32.dll")] [return: MarshalAs(UnmanagedType.Bool)] static extern bool IsWindow(nint hwnd);
    [DllImport("user32.dll")] [return: MarshalAs(UnmanagedType.Bool)] static extern bool IsWindowVisible(nint hwnd);
    [DllImport("user32.dll")] [return: MarshalAs(UnmanagedType.Bool)] static extern bool IsIconic(nint hwnd);
    [DllImport("user32.dll")] static extern nint GetAncestor(nint hwnd, uint flags);
    [DllImport("user32.dll")] static extern nint GetDesktopWindow();
    [DllImport("user32.dll")] static extern nint GetShellWindow();
    [DllImport("user32.dll")] internal static extern nint GetForegroundWindow();
    [DllImport("user32.dll", SetLastError = true)] static extern uint GetWindowThreadProcessId(nint hwnd, out uint processId);
    [DllImport("user32.dll", CharSet = CharSet.Unicode, SetLastError = true)] static extern int GetClassNameW(nint hwnd, StringBuilder value, int count);
    [DllImport("user32.dll", CharSet = CharSet.Unicode)] static extern int GetWindowTextW(nint hwnd, StringBuilder value, int count);
    [DllImport("user32.dll", SetLastError = true)] [return: MarshalAs(UnmanagedType.Bool)] static extern bool GetClientRect(nint hwnd, out Rect rect);
    [DllImport("user32.dll", SetLastError = true)] [return: MarshalAs(UnmanagedType.Bool)] static extern bool ClientToScreen(nint hwnd, ref Point point);
    [DllImport("user32.dll")] static extern uint GetDpiForWindow(nint hwnd);
    [DllImport("user32.dll", SetLastError = true)] static extern nint SetThreadDpiAwarenessContext(nint context);
    [DllImport("user32.dll", SetLastError = true)] static extern nint GetDC(nint hwnd);
    [DllImport("user32.dll")] static extern int ReleaseDC(nint hwnd, nint dc);
    [DllImport("gdi32.dll", SetLastError = true)] static extern nint CreateCompatibleDC(nint dc);
    [DllImport("gdi32.dll", SetLastError = true)] static extern nint CreateDIBSection(nint dc, ref BitmapInfo info, uint usage, out nint bits, nint section, uint offset);
    [DllImport("gdi32.dll", SetLastError = true)] static extern nint SelectObject(nint dc, nint value);
    [DllImport("gdi32.dll")] [return: MarshalAs(UnmanagedType.Bool)] static extern bool DeleteObject(nint value);
    [DllImport("gdi32.dll")] [return: MarshalAs(UnmanagedType.Bool)] static extern bool DeleteDC(nint dc);
    [DllImport("gdi32.dll", SetLastError = true)] [return: MarshalAs(UnmanagedType.Bool)] static extern bool BitBlt(nint destination, int x, int y, int width, int height, nint source, int sourceX, int sourceY, uint operation);
    [DllImport("gdi32.dll")] [return: MarshalAs(UnmanagedType.Bool)] static extern bool GdiFlush();

    // These are thread-local transactions, never a process DPI-awareness mutation.
    internal static IDisposable PhysicalCoordinates() => new DpiScope();
    sealed class DpiScope : IDisposable
    {
        nint previous;
        public DpiScope()
        {
            previous = SetThreadDpiAwarenessContext((nint)(-4)); // PER_MONITOR_AWARE_V2
            if (previous == 0) throw Error("SetThreadDpiAwarenessContext");
        }
        public void Dispose()
        {
            nint restore = previous; previous = 0;
            if (restore != 0 && SetThreadDpiAwarenessContext(restore) == 0) throw Error("RestoreThreadDpiAwarenessContext");
        }
    }

    internal static bool IsCandidate(nint hwnd) => hwnd != 0 && hwnd != GetDesktopWindow() && hwnd != GetShellWindow()
        && IsWindow(hwnd) && IsWindowVisible(hwnd) && !IsIconic(hwnd) && GetAncestor(hwnd, 2) == hwnd;

    internal static uint ProcessId(nint hwnd)
    {
        if (GetWindowThreadProcessId(hwnd, out uint processId) == 0 || processId == 0) throw Error("GetWindowThreadProcessId");
        return processId;
    }
    internal static string Title(nint hwnd)
    { var value = new StringBuilder(512); GetWindowTextW(hwnd, value, value.Capacity); return value.ToString(); }
    static string ClassName(nint hwnd)
    {
        var value = new StringBuilder(256);
        if (GetClassNameW(hwnd, value, value.Capacity) == 0) throw Error("GetClassNameW");
        return value.ToString();
    }
    internal static WindowIdentity Identity(nint hwnd)
    {
        if (!IsCandidate(hwnd)) throw new ClientCaptureException("capture-window-unavailable", "目标须是可见、未最小化的确切顶层窗口；桌面不允许绑定。");
        uint thread = GetWindowThreadProcessId(hwnd, out uint processId);
        if (thread == 0 || processId == 0) throw Error("GetWindowThreadProcessId");
        using var process = Process.GetProcessById(checked((int)processId));
        return new(hwnd, process.Id, thread, process.StartTime.ToUniversalTime().Ticks, ClassName(hwnd), process.ProcessName, Title(hwnd));
    }
    internal static void ValidateIdentity(WindowIdentity expected, bool requireForeground)
    {
        if (!IsCandidate(expected.Handle)) throw new ClientCaptureException("capture-window-unavailable", "已绑定目标关闭、隐藏、最小化或不再是可用顶层窗口。");
        uint thread = GetWindowThreadProcessId(expected.Handle, out uint processId);
        if (thread != expected.ThreadId || processId != expected.ProcessId)
            throw new ClientCaptureException("capture-window-identity-changed", "HWND 对应的进程/线程已改变，请重新绑定。");
        using var process = Process.GetProcessById(expected.ProcessId);
        if (process.StartTime.ToUniversalTime().Ticks != expected.ProcessStartUtcTicks || ClassName(expected.Handle) != expected.WindowClass)
            throw new ClientCaptureException("capture-window-identity-changed", "窗口类或进程启动身份已改变，请重新绑定。");
        if (requireForeground && GetForegroundWindow() != expected.Handle)
            throw new ClientCaptureException("capture-foreground-unavailable", "已绑定窗口当前不在前台；程序不会自动切换前台。");
    }
    internal static ClientGeometry Geometry(nint hwnd)
    {
        if (!GetClientRect(hwnd, out var rect)) throw Error("GetClientRect");
        var point = new Point();
        if (!ClientToScreen(hwnd, ref point)) throw Error("ClientToScreen");
        int width = checked(rect.Right - rect.Left), height = checked(rect.Bottom - rect.Top);
        uint dpi = GetDpiForWindow(hwnd);
        if (width <= 0 || height <= 0 || dpi == 0)
            throw new ClientCaptureException("capture-client-unavailable", "无法获取正尺寸客户区或有效 DPI。");
        return new(point.X, point.Y, width, height, dpi);
    }

    internal static byte[] CopyClientBgra(nint hwnd, ClientGeometry context, CancellationToken token)
    {
        long byteCount = (long)context.Width * context.Height * 4;
        if (byteCount > 128L * 1024 * 1024)
            throw new ClientCaptureException("capture-frame-too-large", "客户区超过本次单帧采集的 128 MiB 上限；未分配像素或请求截图。", context);
        token.ThrowIfCancellationRequested();
        nint source = 0, memory = 0, bitmap = 0, old = 0;
        Exception? failure = null;
        try
        {
            // Never GetDC(NULL), CAPTUREBLT, PrintWindow, or a desktop/full-screen fallback.
            source = GetDC(hwnd); if (source == 0) throw Error("GetDC(target)");
            memory = CreateCompatibleDC(source); if (memory == 0) throw Error("CreateCompatibleDC");
            var info = new BitmapInfo { Size = 40, Width = context.Width, Height = -context.Height, Planes = 1, BitCount = 32 };
            bitmap = CreateDIBSection(memory, ref info, 0, out nint bits, 0, 0);
            if (bitmap == 0 || bits == 0) throw Error("CreateDIBSection");
            old = SelectObject(memory, bitmap);
            if (old == 0 || old == (nint)(-1)) { old = 0; throw Error("SelectObject"); }
            token.ThrowIfCancellationRequested();
            if (!BitBlt(memory, 0, 0, context.Width, context.Height, source, 0, 0, 0x00CC0020)) throw Error("BitBlt(target client)");
            // CreateDIBSection requires GDI completion before CPU access to its pixel pointer.
            // https://learn.microsoft.com/en-us/windows/win32/api/wingdi/nf-wingdi-createdibsection
            if (!GdiFlush()) throw new ClientCaptureException("capture-gdi-flush-failed", "GDI 未完成像素复制；本次帧已丢弃。", context);
            token.ThrowIfCancellationRequested();
            var pixels = new byte[checked((int)byteCount)];
            Marshal.Copy(bits, pixels, 0, pixels.Length);
            for (int i = 3; i < pixels.Length; i += 4) pixels[i] = 255; // GDI alpha is undefined; preserve BGR exactly.
            return pixels;
        }
        catch (Win32Exception error)
        {
            var wrapped = new ClientCaptureException("capture-client-dc-native-error", error.Message, context, error);
            failure = wrapped;
            throw wrapped;
        }
        catch (Exception error) { failure = error; throw; }
        finally
        {
            var problems = new List<string>();
            if (old != 0 && memory != 0)
            { nint selected = SelectObject(memory, old); if (selected == 0 || selected == (nint)(-1)) problems.Add("SelectObject(restore)"); }
            if (bitmap != 0 && !DeleteObject(bitmap)) problems.Add("DeleteObject(bitmap)");
            if (memory != 0 && !DeleteDC(memory)) problems.Add("DeleteDC(memory)");
            if (source != 0 && ReleaseDC(hwnd, source) == 0) problems.Add("ReleaseDC(target)");
            if (problems.Count > 0)
            {
                string message = string.Join(", ", problems);
                if (failure is not null) failure.Data["GdiCleanupErrors"] = message;
                else throw new ClientCaptureException("capture-gdi-cleanup-failed", "原生采集资源释放失败：" + message, context);
            }
        }
    }
    static Win32Exception Error(string operation) => new(Marshal.GetLastPInvokeError(), operation + " 失败。");
}
