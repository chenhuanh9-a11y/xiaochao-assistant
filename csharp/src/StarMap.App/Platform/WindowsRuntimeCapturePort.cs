using System.ComponentModel;
using System.Diagnostics;
using System.Globalization;
using System.Windows.Media;
using System.Windows.Media.Imaging;
using StarMap.Runtime;
using RuntimeFrame = StarMap.Runtime.CapturedClientFrame;

namespace StarMap.App.Platform;

public enum ClientCapturePolicy { PreferWgc, ClientDcOnly }
public sealed record ClientPreviewFrame(BitmapSource Image, ClientGeometry Geometry, string Backend);

/// <summary>One binding owner and one capture lane shared by preview and the real runtime.</summary>
public sealed class WindowsRuntimeCapturePort : ICapturePort, IDisposable
{
    readonly object sync = new();
    readonly SemaphoreSlim captureLane = new(1, 1);
    readonly WindowsClientCapture bindingOwner = new();
    readonly WindowsGraphicsCaptureBackend wgc = new();
    readonly ClientCapturePolicy policy;
    BoundWindow? binding;
    string windowIdentity = "";
    CaptureStamp? currentStamp;
    Action? validatePixels;
    long frameSequence;
    bool disposed;
    // The host hides its own overlay for the complete acquisition even when
    // capture-exclusion affinity is unavailable. No native input is involved.
    internal Func<CancellationToken, ValueTask<IAsyncDisposable>>? AcquireOverlayCaptureLease { get; set; }

    public WindowsRuntimeCapturePort(ClientCapturePolicy policy = ClientCapturePolicy.PreferWgc) => this.policy = policy;
    public bool IsBound { get { lock (sync) return !disposed && binding is not null; } }
    public CaptureBindingScope? BindingScope { get { lock (sync) return !disposed && binding is { } selected
        ? new(windowIdentity, selected.Generation) : null; } }
    public bool IsForeground => IsBindingForeground();
    public BoundWindow? Binding { get { lock (sync) return disposed ? null : binding; } }

    internal bool IsBindingForeground()
    {
        BoundWindow? selected;
        lock (sync) selected = disposed ? null : binding;
        if (selected is null) return false;
        try { bindingOwner.ValidateBinding(selected, requireForeground: true); }
        catch (Exception error) when (CaptureError(error)) { return false; }
        lock (sync) return !disposed && ReferenceEquals(selected, binding);
    }

    public BoundWindow Bind(WindowCandidate candidate)
    {
        lock (sync)
        {
            ObjectDisposedException.ThrowIf(disposed, this);
            ClearBinding();
            var selected = bindingOwner.Bind(candidate);
            binding = selected; windowIdentity = Guid.NewGuid().ToString("N");
            return selected;
        }
    }

    public void Unbind()
    {
        lock (sync) { if (disposed) return; ClearBinding(); }
    }

    void ClearBinding()
    {
        binding = null; currentStamp = null; validatePixels = null; windowIdentity = "";
        bindingOwner.Unbind();
    }

    public bool IsCurrent(CaptureStamp stamp)
    {
        Action? validate;
        lock (sync)
        {
            if (disposed || binding is null || currentStamp != stamp) return false;
            validate = validatePixels;
        }
        try { if (validate is null) return false; validate(); }
        catch (Exception error) when (CaptureError(error)) { return false; }
        lock (sync) return !disposed && binding is not null && currentStamp == stamp;
    }

    public bool IsObservationContextCurrent(RuntimeFrame frame)
        => ObservationContextCurrent(frame.Stamp, frame.Context, requireForeground: false);

    internal bool IsForegroundObservationCurrent(CaptureStamp stamp, ClientFrameContext? context)
        => ObservationContextCurrent(stamp, context, requireForeground: true);

    bool ObservationContextCurrent(CaptureStamp stamp, ClientFrameContext? context, bool requireForeground)
    {
        BoundWindow? selected;
        lock (sync) selected = !disposed && binding is { } candidate && stamp.WindowIdentity == windowIdentity
            && stamp.Generation == candidate.Generation ? candidate : null;
        if (selected is null || context is not { EvidenceKind: CaptureEvidenceKind.LiveClient }) return false;
        var geometry = context.Geometry;
        try { ValidateGeometry(selected, new(geometry.Left, geometry.Top, geometry.Width, geometry.Height, geometry.Dpi), requireForeground); }
        catch (Exception error) when (CaptureError(error)) { return false; }
        lock (sync) return !disposed && ReferenceEquals(selected, binding) && stamp.WindowIdentity == windowIdentity;
    }

    public async ValueTask<ClientCaptureResult> CaptureAsync(CancellationToken cancellationToken)
    {
        try
        {
            var frame = await CapturePixels(cancellationToken, publishRuntimeFrame: true).ConfigureAwait(false);
            return new(new RuntimeFrame(frame.Pixels, frame.Geometry.Width, frame.Geometry.Height,
                checked(frame.Geometry.Width * 4), frame.Stamp, frame.CapturedAt,
                new(CaptureEvidenceKind.LiveClient, new(frame.Geometry.Left, frame.Geometry.Top,
                    frame.Geometry.Width, frame.Geometry.Height, frame.Geometry.Dpi), frame.Backend)));
        }
        catch (OperationCanceledException) { throw; }
        catch (Exception error) when (CaptureError(error))
        { return new(null, error is ClientCaptureException capture ? capture.Code : "capture-unavailable"); }
    }

    public async Task<ClientPreviewFrame> CapturePreviewAsync(CancellationToken cancellationToken)
    {
        var frame = await CapturePixels(cancellationToken, publishRuntimeFrame: false).ConfigureAwait(false);
        var image = BitmapSource.Create(frame.Geometry.Width, frame.Geometry.Height, 96, 96,
            PixelFormats.Bgra32, null, frame.Pixels.ToArray(), checked(frame.Geometry.Width * 4));
        image.Freeze();
        cancellationToken.ThrowIfCancellationRequested();
        frame.ValidateLifetime();
        return new(image, frame.Geometry, frame.Backend);
    }

    async Task<PixelAcquisition> CapturePixels(CancellationToken token, bool publishRuntimeFrame)
    {
        token.ThrowIfCancellationRequested();
        // Preview and runtime share this lane. A second request is rejected,
        // never queued behind an image belonging to another UI intent.
        if (!await captureLane.WaitAsync(0, token).ConfigureAwait(false))
            throw new ClientCaptureException("capture-busy", "已有一次客户区采集执行中。");
        try
        {
            await using var overlayLease = AcquireOverlayCaptureLease is { } acquire
                ? await acquire(token).ConfigureAwait(false) : null;
            BoundWindow selected; string identity;
            lock (sync)
            {
                ObjectDisposedException.ThrowIf(disposed, this);
                selected = binding ?? throw new ClientCaptureException("capture-not-bound", "请先连接游戏窗口。");
                identity = windowIdentity;
                if (publishRuntimeFrame) { currentStamp = null; validatePixels = null; }
            }
            bindingOwner.ValidateBinding(selected);
            var utcAnchor = DateTimeOffset.UtcNow;
            long anchorQpc = Stopwatch.GetTimestamp();
            ReadOnlyMemory<byte> pixels; ClientGeometry geometry; long copiedQpc; string backend;
            Action validate;
            var support = policy == ClientCapturePolicy.PreferWgc ? WindowsGraphicsCaptureBackend.GetSupport() : null;
            if (support?.Supported == true)
            {
                var captured = await wgc.CaptureAsync(selected, TimeSpan.FromSeconds(2), token).ConfigureAwait(false);
                pixels = captured.Bgra32Pixels; geometry = captured.Metadata.After.Client;
                copiedQpc = captured.Metadata.PixelsCopiedQpc; backend = captured.Metadata.Backend;
                validate = () => ValidateGeometry(selected, geometry, requireForeground: false);
            }
            else
            {
                // Only capability absence selects GDI. Cancellation, timeout,
                // identity/geometry changes and device failures never trigger fallback.
                var captured = await bindingOwner.CaptureAsync(selected, token).ConfigureAwait(false);
                pixels = captured.Bgra32Pixels; geometry = captured.Metadata.After;
                copiedQpc = captured.Metadata.PixelsCopiedQpc; backend = captured.Metadata.Backend;
                validate = () => ValidateGeometry(selected, geometry, requireForeground: false);
            }
            // Foreground is required while acquiring pixels. Afterwards the
            // assistant itself can receive focus for candidate confirmation;
            // this does not revoke the captured client's identity or geometry.
            token.ThrowIfCancellationRequested(); ValidateGeometry(selected, geometry, requireForeground: true);
            var stamp = new CaptureStamp(identity, selected.Generation,
                Interlocked.Increment(ref frameSequence).ToString(CultureInfo.InvariantCulture));
            lock (sync)
            {
                ObjectDisposedException.ThrowIf(disposed, this);
                if (!ReferenceEquals(selected, binding) || identity != windowIdentity)
                    throw new ClientCaptureException("capture-binding-superseded", "采集后窗口已解除或重新绑定。");
                if (publishRuntimeFrame) { currentStamp = stamp; validatePixels = validate; }
            }
            return new(pixels, geometry, stamp,
                utcAnchor + TimeSpan.FromSeconds((copiedQpc - anchorQpc) / (double)Stopwatch.Frequency), backend, () =>
                {
                    validate();
                    lock (sync)
                        if (disposed || !ReferenceEquals(selected, binding) || identity != windowIdentity)
                            throw new ClientCaptureException("capture-binding-superseded", "窗口连接已改变，请重新采集。");
                });
        }
        finally { captureLane.Release(); }
    }

    void ValidateGeometry(BoundWindow selected, ClientGeometry expected, bool requireForeground)
    {
        bindingOwner.ValidateBinding(selected, requireForeground);
        using var physical = CaptureNative.PhysicalCoordinates();
        if (CaptureNative.Geometry(selected.Identity.Handle) != expected)
            throw new ClientCaptureException("capture-context-changed", "采集后客户区位置、尺寸或 DPI 已改变。");
        bindingOwner.ValidateBinding(selected, requireForeground);
    }

    static bool CaptureError(Exception error) => error is ClientCaptureException or Win32Exception
        or InvalidOperationException or ArgumentException or NotSupportedException;

    public void Dispose()
    {
        lock (sync)
        {
            if (disposed) return;
            disposed = true; ClearBinding(); bindingOwner.Dispose();
        }
        // Native work unwinds through its request cancellation/timeout; do not
        // dispose the semaphore while a request still owns its finally block.
    }

    sealed record PixelAcquisition(ReadOnlyMemory<byte> Pixels, ClientGeometry Geometry, CaptureStamp Stamp,
        DateTimeOffset CapturedAt, string Backend, Action ValidateLifetime);
}
