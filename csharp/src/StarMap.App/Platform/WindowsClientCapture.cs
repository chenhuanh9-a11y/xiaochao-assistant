using System.ComponentModel;
using System.Diagnostics;
using System.Runtime.InteropServices;
using System.Windows.Media;
using System.Windows.Media.Imaging;

namespace StarMap.App.Platform;

public sealed record ClientGeometry(int Left, int Top, int Width, int Height, uint Dpi);
public sealed record WindowIdentity(nint Handle, int ProcessId, uint ThreadId,
    long ProcessStartUtcTicks, string WindowClass, string ProcessName, string Title);
public sealed record WindowCandidate(WindowIdentity Identity, ClientGeometry Geometry)
{
    public nint Handle => Identity.Handle;
    public int ProcessId => Identity.ProcessId;
    public string ProcessName => Identity.ProcessName;
    public string Title => Identity.Title;
    public string DisplayName => $"{Title} · {ProcessName} / PID {ProcessId} · {Geometry.Width}×{Geometry.Height} · DPI {Geometry.Dpi} · 0x{Handle:X}";
    public override string ToString() => DisplayName;
}
public sealed record WindowInspectionIssue(nint Handle, string Code, string Message);
public sealed record WindowCandidates(IReadOnlyList<WindowCandidate> Items,
    IReadOnlyList<WindowInspectionIssue> Issues, int Inspected, int Skipped, bool Truncated);

/// <summary>Only WindowsClientCapture can create an authorized binding. No capture occurs in Bind.</summary>
public sealed class BoundWindow
{
    internal BoundWindow(WindowIdentity identity, ClientGeometry geometry, long generation)
    { Identity = identity; GeometryAtBinding = geometry; Generation = generation; }
    public WindowIdentity Identity { get; }
    public ClientGeometry GeometryAtBinding { get; }
    public long Generation { get; }
}

public sealed record FrameStatistics(double BgrMean, double BgrPopulationDeviation, bool IsBlank);
public sealed record CaptureMetadata(string Backend, bool CompatibilityCandidate,
    ClientGeometry Before, ClientGeometry After, long CaptureStartedQpc, long PixelsCopiedQpc,
    long ValidatedQpc, long QpcFrequency, FrameStatistics Statistics,
    string PixelLayout = "top-down BGRA32, opaque alpha synthesized from GDI BGR",
    string CapabilityLimit = "Client-DC GDI only; WGC and owner-overlay-excluding production fallback are not implemented");

public sealed class CapturedClientFrame
{
    readonly byte[] pixels;
    internal CapturedClientFrame(BoundWindow binding, BitmapSource image, byte[] pixels, CaptureMetadata metadata)
    { Binding = binding; Image = image; this.pixels = pixels; Metadata = metadata; }
    public BoundWindow Binding { get; }
    public BitmapSource Image { get; }
    public ReadOnlyMemory<byte> Bgra32Pixels => pixels;
    public int Stride => checked(Metadata.After.Width * 4);
    public CaptureMetadata Metadata { get; }
}

public sealed class ClientCaptureException : Exception
{
    public ClientCaptureException(string code, string message, ClientGeometry? geometry = null, Exception? inner = null)
        : base(message, inner) { Code = code; Geometry = geometry; }
    public string Code { get; }
    public ClientGeometry? Geometry { get; }
    public FrameStatistics? Statistics { get; init; }
}

/// <summary>
/// Explicit, single-frame full-client capture. Construction does nothing; call enumeration only
/// from an explicit UI refresh action. Bind/Unbind own the binding generation. Call ValidateForCommit
/// again on the UI result-consumption path, alongside the caller's mode/floor/request generation.
/// No process activation, input, hooks, file writes, desktop DC or automatic fallback exists here.
/// </summary>
public sealed class WindowsClientCapture : IDisposable
{
    readonly object gate = new();
    readonly SemaphoreSlim captureGate = new(1, 1);
    BoundWindow? current;
    long generation;
    bool disposed;

    public static WindowCandidates EnumerateCandidates(int maximum = 64, CancellationToken cancellationToken = default)
    {
        if (maximum is < 1 or > 128) throw new ArgumentOutOfRangeException(nameof(maximum));
        cancellationToken.ThrowIfCancellationRequested();
        using var dpi = CaptureNative.PhysicalCoordinates();
        var items = new List<WindowCandidate>();
        var issues = new List<WindowInspectionIssue>();
        int inspected = 0, skipped = 0;
        bool truncated = false;
        Exception? stopped = null;
        CaptureNative.EnumWindowsCallback callback = (hwnd, _) =>
        {
            try
            {
                cancellationToken.ThrowIfCancellationRequested();
                if (inspected >= 1024 || items.Count >= maximum) { truncated = true; return false; }
                inspected++;
                if (!CaptureNative.IsCandidate(hwnd) || CaptureNative.ProcessId(hwnd) == Environment.ProcessId) return true;
                string title = CaptureNative.Title(hwnd);
                if (string.IsNullOrWhiteSpace(title)) return true;
                var identity = CaptureNative.Identity(hwnd);
                var geometry = CaptureNative.Geometry(hwnd);
                CaptureNative.ValidateIdentity(identity, requireForeground: false);
                items.Add(new(identity, geometry));
            }
            catch (OperationCanceledException error) { stopped = error; return false; }
            catch (Exception error) when (error is Win32Exception or InvalidOperationException or ArgumentException or ClientCaptureException)
            {
                skipped++;
                if (issues.Count < 16) issues.Add(new(hwnd, "window-inspection-unavailable", error.Message));
            }
            catch (Exception error) { stopped = error; return false; }
            return true;
        };
        Marshal.SetLastPInvokeError(0);
        bool completed = CaptureNative.EnumWindows(callback, 0);
        int nativeError = Marshal.GetLastPInvokeError();
        GC.KeepAlive(callback);
        if (stopped is not null) System.Runtime.ExceptionServices.ExceptionDispatchInfo.Capture(stopped).Throw();
        cancellationToken.ThrowIfCancellationRequested();
        if (!completed && !truncated) throw new Win32Exception(nativeError, "枚举窗口失败，未返回默认成功结果。");
        return new(items.AsReadOnly(), issues.AsReadOnly(), inspected, skipped, truncated);
    }

    public BoundWindow Bind(WindowCandidate candidate) => BindCore(candidate.Handle, ownedFixture: false, candidate.Identity);

    // Deliberately unavailable to settings/config. A same-process fixture is an explicit test seam.
    internal BoundWindow BindOwnedFixture(nint hwnd) => BindCore(hwnd, ownedFixture: true);
    internal BoundWindow BindOwnedFixture(WindowCandidate candidate) => BindCore(candidate.Handle, ownedFixture: true, candidate.Identity);

    BoundWindow BindCore(nint hwnd, bool ownedFixture, WindowIdentity? expectedIdentity = null)
    {
        long ticket;
        lock (gate)
        {
            ObjectDisposedException.ThrowIf(disposed, this);
            // A failed replacement must not leave the old target authorized. Also prevent a
            // slower earlier Bind from winning over a later Bind/Unbind made on another thread.
            current = null;
            ticket = ++generation;
        }
        using var dpi = CaptureNative.PhysicalCoordinates();
        if (expectedIdentity is not null) CaptureNative.ValidateIdentity(expectedIdentity, requireForeground: false);
        WindowIdentity identity = CaptureNative.Identity(hwnd);
        bool own = identity.ProcessId == Environment.ProcessId;
        if (own != ownedFixture)
            throw new ClientCaptureException("capture-window-scope", ownedFixture
                ? "自测入口只允许当前进程自有的 fixture。" : "窗口列表与外部绑定不接受本程序窗口，请选择明确的外部目标。");
        ClientGeometry geometry = CaptureNative.Geometry(hwnd);
        CaptureNative.ValidateIdentity(identity, requireForeground: false);
        if (expectedIdentity is not null) CaptureNative.ValidateIdentity(expectedIdentity, requireForeground: false);
        lock (gate)
        {
            ObjectDisposedException.ThrowIf(disposed, this);
            if (ticket != generation)
                throw new ClientCaptureException("capture-binding-superseded", "该绑定请求已被更新的选择取代，不再授权目标。");
            current = new(identity, geometry, ticket);
            return current;
        }
    }

    public void Unbind()
    {
        lock (gate) { current = null; generation++; }
    }

    public void ValidateBinding(BoundWindow binding, bool requireForeground = true)
    {
        using var dpi = CaptureNative.PhysicalCoordinates();
        EnsureCurrent(binding);
        CaptureNative.ValidateIdentity(binding.Identity, requireForeground);
        _ = CaptureNative.Geometry(binding.Identity.Handle);
        CaptureNative.ValidateIdentity(binding.Identity, requireForeground);
        EnsureCurrent(binding);
    }

    public void ValidateForCommit(CapturedClientFrame frame)
    {
        ArgumentNullException.ThrowIfNull(frame);
        using var dpi = CaptureNative.PhysicalCoordinates();
        EnsureCurrent(frame.Binding);
        CaptureNative.ValidateIdentity(frame.Binding.Identity, requireForeground: true);
        if (CaptureNative.Geometry(frame.Binding.Identity.Handle) != frame.Metadata.After)
            throw new ClientCaptureException("capture-context-changed", "采集后窗口位置、客户区尺寸或 DPI 已改变；旧帧不得提交。", frame.Metadata.After);
        CaptureNative.ValidateIdentity(frame.Binding.Identity, requireForeground: true);
        EnsureCurrent(frame.Binding);
    }

    public async Task<CapturedClientFrame> CaptureAsync(BoundWindow binding, CancellationToken cancellationToken = default)
    {
        ArgumentNullException.ThrowIfNull(binding);
        EnsureCurrent(binding);
        if (!await captureGate.WaitAsync(0, cancellationToken).ConfigureAwait(false))
            throw new ClientCaptureException("capture-busy", "已有一次客户区采集正在执行；本次未排队、未采集。");
        try { return await Task.Run(() => CaptureCore(binding, cancellationToken), cancellationToken).ConfigureAwait(false); }
        finally { captureGate.Release(); }
    }

    CapturedClientFrame CaptureCore(BoundWindow binding, CancellationToken cancellationToken)
    {
        using var dpi = CaptureNative.PhysicalCoordinates();
        cancellationToken.ThrowIfCancellationRequested();
        EnsureCurrent(binding);
        CaptureNative.ValidateIdentity(binding.Identity, requireForeground: true);
        // Full-client intent: derive this transaction's ROI here, never from GeometryAtBinding.
        ClientGeometry before = CaptureNative.Geometry(binding.Identity.Handle);
        long started = Stopwatch.GetTimestamp();
        byte[] pixels = CaptureNative.CopyClientBgra(binding.Identity.Handle, before, cancellationToken);
        long copied = Stopwatch.GetTimestamp();
        cancellationToken.ThrowIfCancellationRequested();
        CaptureNative.ValidateIdentity(binding.Identity, requireForeground: true);
        ClientGeometry after = CaptureNative.Geometry(binding.Identity.Handle);
        if (before != after) throw new ClientCaptureException("capture-context-changed", "窗口在采集期间移动、缩放或改变 DPI；已丢弃本次帧。", before);
        FrameStatistics statistics = MeasureBgra(pixels, cancellationToken);
        if (statistics.IsBlank)
            throw new ClientCaptureException("game-client-dc-returned-blank-frame",
                "窗口客户区 GDI 返回黑帧或低变化全帧，不能作为识别输入；当前没有已验证的 WGC/安全备用采集。", before) { Statistics = statistics };
        cancellationToken.ThrowIfCancellationRequested();
        EnsureCurrent(binding);
        CaptureNative.ValidateIdentity(binding.Identity, requireForeground: true);
        if (CaptureNative.Geometry(binding.Identity.Handle) != before)
            throw new ClientCaptureException("capture-context-changed", "像素检查期间窗口几何或 DPI 改变；已丢弃本次帧。", before);
        var bitmap = BitmapSource.Create(before.Width, before.Height, before.Dpi, before.Dpi,
            PixelFormats.Bgra32, null, pixels, checked(before.Width * 4));
        bitmap.Freeze();
        cancellationToken.ThrowIfCancellationRequested();
        EnsureCurrent(binding);
        CaptureNative.ValidateIdentity(binding.Identity, requireForeground: true);
        if (CaptureNative.Geometry(binding.Identity.Handle) != before)
            throw new ClientCaptureException("capture-context-changed", "冻结图像期间窗口上下文改变；已丢弃本次帧。", before);
        return new(binding, bitmap, pixels, new("client-dc-gdi", true, before, after, started,
            copied, Stopwatch.GetTimestamp(), Stopwatch.Frequency, statistics));
    }

    // Same full-frame all-BGR sample population thresholds as client_frame_is_blank.
    // Alpha is excluded. This is not a spatial-flatness test: uniform colored RGB may have variance.
    internal static FrameStatistics MeasureBgra(ReadOnlySpan<byte> pixels, CancellationToken cancellationToken = default)
    {
        if (pixels.Length == 0 || pixels.Length % 4 != 0) throw new ArgumentException("完整 BGRA32 像素数组不能为空，长度须为四的倍数。", nameof(pixels));
        long sum = 0, squareSum = 0;
        for (int i = 0; i < pixels.Length; i += 4)
        {
            if ((i & 0xffff) == 0) cancellationToken.ThrowIfCancellationRequested();
            for (int channel = 0; channel < 3; channel++) { int value = pixels[i + channel]; sum += value; squareSum += value * value; }
        }
        long count = pixels.Length / 4L * 3;
        double mean = (double)sum / count;
        double deviation = Math.Sqrt(Math.Max(0, (double)squareSum / count - mean * mean));
        // As in Python, retain a two-pass calculation near the strict numeric boundary.
        if (Math.Abs(mean - 1) <= 1e-8 || Math.Abs(deviation - 1) <= 1e-8)
        {
            double squaredDifferences = 0;
            for (int i = 0; i < pixels.Length; i += 4)
            {
                if ((i & 0xffff) == 0) cancellationToken.ThrowIfCancellationRequested();
                for (int channel = 0; channel < 3; channel++) { double delta = pixels[i + channel] - mean; squaredDifferences += delta * delta; }
            }
            deviation = Math.Sqrt(squaredDifferences / count);
        }
        return new(mean, deviation, mean < 1 || deviation < 1);
    }

    void EnsureCurrent(BoundWindow binding)
    {
        lock (gate)
        {
            ObjectDisposedException.ThrowIf(disposed, this);
            if (!ReferenceEquals(binding, current) || binding.Generation != generation)
                throw new ClientCaptureException("capture-binding-superseded", "窗口绑定已解除或更换；旧请求不得采集或提交。");
        }
    }

    public void Dispose()
    {
        lock (gate) { disposed = true; current = null; generation++; }
        // Do not dispose captureGate while a native call is unwinding. No WaitHandle is allocated.
    }
}
