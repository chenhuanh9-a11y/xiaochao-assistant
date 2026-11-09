using System.Diagnostics;
using System.Runtime.InteropServices;
using Windows.Foundation;
using Windows.Foundation.Metadata;
using Windows.Graphics.Capture;
using Windows.Graphics.DirectX;
using Windows.Graphics.Imaging;
using Windows.Storage.Streams;

namespace StarMap.App.Platform;

public sealed record WindowsGraphicsCaptureSupport(bool Supported, string Code, string Message);
public sealed record WindowsGraphicsCaptureMetadata(WindowsGraphicsCaptureBounds Before,
    WindowsGraphicsCaptureBounds After, WindowsGraphicsCaptureCrop Crop, int ContentWidth, int ContentHeight,
    int SurfaceWidth, int SurfaceHeight, long BindingGeneration, long CaptureStartedQpc,
    long FrameArrivedQpc, long PixelsCopiedQpc, long ValidatedQpc, long QpcFrequency, long FrameSystemRelativeTicks,
    FrameStatistics Statistics, string PixelFormat, string AlphaMode,
    string Backend = "wgc-window-full-client", string CapabilityLimit = "Single exact HWND; BGRA8 SDR; no desktop/screen fallback or automatic retry");

public sealed class WindowsGraphicsCapturedClientFrame
{
    readonly byte[] pixels;
    internal WindowsGraphicsCapturedClientFrame(BoundWindow binding, byte[] pixels, WindowsGraphicsCaptureMetadata metadata)
    { Binding = binding; this.pixels = pixels; Metadata = metadata; }
    public BoundWindow Binding { get; }
    public ReadOnlyMemory<byte> Bgra32Pixels => pixels;
    public int Width => Metadata.After.Client.Width;
    public int Height => Metadata.After.Client.Height;
    public int Stride => checked(Width * 4);
    public WindowsGraphicsCaptureMetadata Metadata { get; }
}

/// <summary>
/// Explicit one-frame WGC window backend. Constructor performs no native calls. No picker,
/// permissions/settings mutation, activation, global listener or desktop capture is used.
/// The WindowsClientCapture owner must still validate binding generation and commit freshness.
/// </summary>
public sealed class WindowsGraphicsCaptureBackend
{
    readonly SemaphoreSlim captureGate = new(1, 1);

    public static WindowsGraphicsCaptureSupport GetSupport()
    {
        if (!OperatingSystem.IsWindowsVersionAtLeast(10, 0, 19041))
            return new(false, "capture-wgc-os-unsupported", "此 WGC 后端需要 Windows 10 2004 / 19041 或更高版本。");
        try
        {
            if (!ApiInformation.IsTypePresent("Windows.Graphics.Capture.GraphicsCaptureSession")
                || !ApiInformation.IsMethodPresent("Windows.Graphics.Capture.Direct3D11CaptureFramePool", "CreateFreeThreaded")
                || !ApiInformation.IsTypePresent("Windows.Graphics.Imaging.SoftwareBitmap"))
                return new(false, "capture-wgc-api-unsupported", "系统未提供所需 WGC free-threaded / SoftwareBitmap API。");
            return GraphicsCaptureSession.IsSupported()
                ? new(true, "capture-wgc-available", "系统支持 WGC；具体目标、设备与裁剪仍须在请求内验证。")
                : new(false, "capture-wgc-unavailable", "系统未允许或不支持 WGC；程序不会修改权限或安全设置。");
        }
        catch (Exception error) when (error is COMException or TypeLoadException or DllNotFoundException or EntryPointNotFoundException or NotSupportedException)
        { return new(false, "capture-wgc-probe-failed", "无法确认 WGC 能力：" + error.Message); }
    }

    public Task<WindowsGraphicsCapturedClientFrame> CaptureAsync(BoundWindow binding, TimeSpan timeout, CancellationToken token = default)
        => CaptureRequestAsync(binding, timeout, ownedFixture: false, token, frameOwned: null);

    // The fixture observer runs only after a real WGC frame is owned, on the capture worker.
    // It can arrange cancellation/owned-window close; production has no observer or replacement IO.
    internal Task<WindowsGraphicsCapturedClientFrame> CaptureOwnedFixtureAsync(BoundWindow binding, TimeSpan timeout,
        CancellationToken token = default, Action? frameOwned = null,
        Action<int, int, ReadOnlyMemory<byte>, WindowsGraphicsCaptureCrop>? surfaceCopied = null)
        => CaptureRequestAsync(binding, timeout, ownedFixture: true, token, frameOwned, surfaceCopied);

    async Task<WindowsGraphicsCapturedClientFrame> CaptureRequestAsync(BoundWindow binding, TimeSpan timeout,
        bool ownedFixture, CancellationToken token, Action? frameOwned,
        Action<int, int, ReadOnlyMemory<byte>, WindowsGraphicsCaptureCrop>? surfaceCopied = null)
    {
        ArgumentNullException.ThrowIfNull(binding);
        if (timeout <= TimeSpan.Zero || timeout > TimeSpan.FromSeconds(30)) throw new ArgumentOutOfRangeException(nameof(timeout));
        token.ThrowIfCancellationRequested();
        if ((binding.Identity.ProcessId == Environment.ProcessId) != ownedFixture)
            throw new ClientCaptureException("capture-wgc-window-scope", "普通 WGC 入口只接受明确的外部绑定；fixture 入口只接受本进程窗口。");
        if (!await captureGate.WaitAsync(0, token).ConfigureAwait(false))
            throw new ClientCaptureException("capture-wgc-busy", "已有一次 WGC 请求执行中；本次未排队、未采集。");
        try
        {
            var result = await Task.Run(() => CaptureCore(binding, timeout, !ownedFixture, token, frameOwned, surfaceCopied), token).ConfigureAwait(false);
            token.ThrowIfCancellationRequested();
            return result;
        }
        finally { captureGate.Release(); }
    }

    WindowsGraphicsCapturedClientFrame CaptureCore(BoundWindow binding, TimeSpan timeout, bool foreground,
        CancellationToken callerToken, Action? frameOwned,
        Action<int, int, ReadOnlyMemory<byte>, WindowsGraphicsCaptureCrop>? surfaceCopied)
    {
        // Keep the App's lower supported OS intact; only this optional backend requires 19041.
        // An actual local guard also lets CA1416 prove the IsCursorCaptureEnabled call below.
        if (!OperatingSystem.IsWindowsVersionAtLeast(10, 0, 19041))
            throw new ClientCaptureException("capture-wgc-os-unsupported", "此 WGC 后端需要 Windows 10 2004 / 19041 或更高版本。");
        var support = GetSupport();
        if (!support.Supported) throw new ClientCaptureException(support.Code, support.Message);
        using var apartment = new WindowsGraphicsCaptureInterop.Apartment();
        using var deadline = CancellationTokenSource.CreateLinkedTokenSource(callerToken);
        deadline.CancelAfter(timeout);
        var token = deadline.Token;
        var started = Stopwatch.GetTimestamp();
        var before = WindowsGraphicsCaptureInterop.Bounds(binding.Identity, foreground);
        _ = WindowsGraphicsCaptureGeometry.ByteCount(before.Client.Width, before.Client.Height);
        WindowsGraphicsCaptureInterop.Device? device = null;
        GraphicsCaptureItem? item = null;
        Direct3D11CaptureFramePool? pool = null;
        GraphicsCaptureSession? session = null;
        var eventGate = new object();
        var arrived = new TaskCompletionSource(TaskCreationOptions.RunContinuationsAsynchronously);
        Direct3D11CaptureFrame? waitingFrame = null;
        bool closed = false, targetClosed = false;
        long arrivedAt = 0;
        Exception? failure = null;
        Exception? receiverFailure = null;
        TypedEventHandler<Direct3D11CaptureFramePool, object>? frameHandler = null;
        TypedEventHandler<GraphicsCaptureItem, object>? closedHandler = null;
        try
        {
            token.ThrowIfCancellationRequested();
            device = new();
            item = WindowsGraphicsCaptureInterop.ItemForWindow(binding.Identity.Handle);
            var initialSize = item.Size;
            _ = WindowsGraphicsCaptureGeometry.Resolve(before, initialSize.Width, initialSize.Height);
            pool = Direct3D11CaptureFramePool.CreateFreeThreaded(device.Projected, DirectXPixelFormat.B8G8R8A8UIntNormalized, 1, initialSize);
            frameHandler = (sender, _) =>
            {
                lock (eventGate)
                {
                    if (closed) return;
                    try
                    {
                        var next = sender.TryGetNextFrame();
                        if (next is null) return;
                        if (waitingFrame is not null || arrived.Task.IsCompleted) { next.Dispose(); return; }
                        waitingFrame = next; arrivedAt = Stopwatch.GetTimestamp();
                        arrived.TrySetResult();
                    }
                    catch (Exception error) { receiverFailure ??= error; arrived.TrySetException(error); }
                }
            };
            closedHandler = (_, _) =>
            {
                lock (eventGate)
                {
                    if (closed) return;
                    targetClosed = true;
                    arrived.TrySetException(new ClientCaptureException("capture-wgc-window-closed", "目标窗口在 WGC 请求期间关闭；本次帧已丢弃。", before.Client));
                }
            };
            item.Closed += closedHandler;
            pool.FrameArrived += frameHandler;
            session = pool.CreateCaptureSession(item);
            session.IsCursorCaptureEnabled = false;
            token.ThrowIfCancellationRequested();
            session.StartCapture();
            arrived.Task.WaitAsync(token).GetAwaiter().GetResult();
            Direct3D11CaptureFrame frame;
            lock (eventGate)
            {
                token.ThrowIfCancellationRequested();
                if (targetClosed) throw new ClientCaptureException("capture-wgc-window-closed", "WGC 目标已关闭；不提交迟到帧。", before.Client);
                frame = waitingFrame ?? throw new ClientCaptureException("capture-wgc-no-frame", "WGC 未返回可拥有的帧。");
                waitingFrame = null;
            }
            using (frame)
            {
                frameOwned?.Invoke();
                token.ThrowIfCancellationRequested();
                var content = frame.ContentSize;
                if (content.Width != initialSize.Width || content.Height != initialSize.Height)
                    throw new ClientCaptureException("capture-wgc-context-changed", "WGC ContentSize 在本次请求内改变；单帧请求不重采样或复用旧尺寸。", before.Client);
                var crop = WindowsGraphicsCaptureGeometry.Resolve(before, content.Width, content.Height);
                // Deep-copy the original surface. No resize/convert or desktop capture fallback.
                using var bitmap = SoftwareBitmap.CreateCopyFromSurfaceAsync(frame.Surface).AsTask(token).GetAwaiter().GetResult();
                token.ThrowIfCancellationRequested();
                if (bitmap.BitmapPixelFormat != BitmapPixelFormat.Bgra8 || bitmap.PixelWidth != content.Width || bitmap.PixelHeight != content.Height)
                    throw new ClientCaptureException("capture-wgc-surface-format-unsupported", "WGC 软件帧须为与 ContentSize 完全一致的原始 BGRA8 surface。", before.Client);
                var surface = new byte[WindowsGraphicsCaptureGeometry.ByteCount(bitmap.PixelWidth, bitmap.PixelHeight)];
                var buffer = new Windows.Storage.Streams.Buffer((uint)surface.Length) { Length = (uint)surface.Length };
                bitmap.CopyToBuffer(buffer);
                using (var reader = DataReader.FromBuffer(buffer)) reader.ReadBytes(surface);
                token.ThrowIfCancellationRequested();
                // Owned-fixture evidence only, after the real readback and before the exact same crop.
                surfaceCopied?.Invoke(content.Width, content.Height, surface, crop);
                var pixels = WindowsGraphicsCaptureGeometry.Crop(surface, content.Width, content.Height, crop, token);
                var copied = Stopwatch.GetTimestamp();
                var after = WindowsGraphicsCaptureInterop.Bounds(binding.Identity, foreground);
                if (after != before) throw new ClientCaptureException("capture-wgc-context-changed", "WGC 采集期间窗口位置、客户区、DWM 边界或 DPI 改变；本帧已丢弃。", before.Client);
                var statistics = WindowsClientCapture.MeasureBgra(pixels, token);
                if (statistics.IsBlank) throw new ClientCaptureException("capture-wgc-blank-frame", "WGC 完整客户区是黑帧或低变化帧；不能作为识别输入。", before.Client) { Statistics = statistics };
                token.ThrowIfCancellationRequested();
                lock (eventGate)
                {
                    if (targetClosed) throw new ClientCaptureException("capture-wgc-window-closed", "WGC 目标已关闭；不提交迟到帧。", before.Client);
                    if (receiverFailure is not null) throw new ClientCaptureException("capture-wgc-frame-receiver-failed", "WGC 帧接收期间发生异常；本次帧已丢弃。", before.Client, receiverFailure);
                }
                if (WindowsGraphicsCaptureInterop.Bounds(binding.Identity, foreground) != before)
                    throw new ClientCaptureException("capture-wgc-context-changed", "WGC 像素检查期间窗口上下文已改变；本帧已丢弃。", before.Client);
                token.ThrowIfCancellationRequested();
                return new(binding, pixels, new(before, after, crop, content.Width, content.Height,
                    bitmap.PixelWidth, bitmap.PixelHeight, binding.Generation, started, arrivedAt, copied,
                    Stopwatch.GetTimestamp(), Stopwatch.Frequency, frame.SystemRelativeTime.Ticks, statistics,
                    bitmap.BitmapPixelFormat.ToString(), bitmap.BitmapAlphaMode.ToString()));
            }
        }
        catch (OperationCanceledException error) when (!callerToken.IsCancellationRequested)
        {
            bool wasClosed; lock (eventGate) wasClosed = targetClosed;
            var code = wasClosed ? "capture-wgc-window-closed" : token.IsCancellationRequested ? "capture-wgc-timeout" : "capture-wgc-operation-canceled";
            var wrapped = new ClientCaptureException(code,
                wasClosed ? "目标窗口关闭，WGC 已取消；未提交迟到帧。" : token.IsCancellationRequested
                    ? "WGC 单次请求超时；未提交像素，资源正在正常释放。" : "WGC 原生操作被取消；未提交像素。", before.Client, error);
            failure = wrapped; throw wrapped;
        }
        catch (COMException error)
        {
            var deviceLost = error.HResult is unchecked((int)0x887A0005) or unchecked((int)0x887A0006) or unchecked((int)0x887A0007);
            var wrapped = new ClientCaptureException(deviceLost ? "capture-wgc-device-lost" : "capture-wgc-native-unavailable",
                $"WGC {(deviceLost ? "设备丢失" : "原生操作不可用")} (0x{error.HResult:X8})；不自动转向屏幕采集。", before.Client, error);
            failure = wrapped; throw wrapped;
        }
        catch (Exception error) when (error is DllNotFoundException or EntryPointNotFoundException or TypeLoadException or PlatformNotSupportedException)
        {
            var wrapped = new ClientCaptureException("capture-wgc-interop-unavailable", "系统缺少本次 WGC 互操作能力；程序不会变更安全设置或尝试屏幕采集。", before.Client, error);
            failure = wrapped; throw wrapped;
        }
        catch (Exception error) { failure = error; throw; }
        finally
        {
            Direct3D11CaptureFrame? orphan; bool endedWithClosedTarget; Exception? lateReceiverFailure;
            lock (eventGate)
            {
                closed = true; orphan = waitingFrame; waitingFrame = null;
                endedWithClosedTarget = targetClosed; lateReceiverFailure = receiverFailure;
            }
            var cleanup = new List<string>();
            void Release(Action action, string name) { try { action(); } catch (Exception error) { cleanup.Add(name + ": " + error.Message); } }
            if (pool is not null && frameHandler is not null) Release(() => pool.FrameArrived -= frameHandler, "FrameArrived unsubscribe");
            if (item is not null && closedHandler is not null) Release(() => item.Closed -= closedHandler, "Closed unsubscribe");
            if (orphan is not null) Release(orphan.Dispose, "unclaimed frame close");
            if (session is not null) Release(session.Dispose, "session close");
            if (pool is not null) Release(pool.Dispose, "frame pool close");
            if (device is not null) Release(device.Dispose, "D3D device close");
            if (cleanup.Count != 0)
            {
                var message = string.Join("; ", cleanup);
                if (failure is not null) failure.Data["WgcCleanupErrors"] = message;
                else throw new ClientCaptureException("capture-wgc-cleanup-failed", "WGC 资源释放失败：" + message, before.Client);
            }
            if (failure is null)
            {
                if (endedWithClosedTarget) throw new ClientCaptureException("capture-wgc-window-closed", "WGC 关闭资源前目标已关闭；本次帧已丢弃。", before.Client);
                if (lateReceiverFailure is not null) throw new ClientCaptureException("capture-wgc-frame-receiver-failed", "WGC 接收结束时发生异常；本次帧已丢弃。", before.Client, lateReceiverFailure);
                callerToken.ThrowIfCancellationRequested();
                if (token.IsCancellationRequested) throw new ClientCaptureException("capture-wgc-timeout", "WGC 资源已释放，但请求期限已过；本次帧已丢弃。", before.Client);
            }
        }
    }
}
