namespace StarMap.Runtime;

/// <summary>Opaque exact physical window identity, binding generation, and captured frame identity.</summary>
public sealed record CaptureStamp(string WindowIdentity, long Generation, string FrameId);

public enum CaptureEvidenceKind { Unspecified, Synthetic, RecordedClient, LiveClient }

/// <summary>Physical screen coordinates of this exact full client, never WPF DIPs.</summary>
public sealed record CapturedClientGeometry(int Left, int Top, int Width, int Height, uint Dpi);

public sealed record ClientFrameContext(CaptureEvidenceKind EvidenceKind, CapturedClientGeometry Geometry, string Backend);

/// <summary>Owned immutable top-down BGRA32 client pixels; constructor copies the supplied memory.</summary>
public sealed class CapturedClientFrame
{
    private readonly byte[] _pixels;
    public CapturedClientFrame(ReadOnlyMemory<byte> bgra, int width, int height, int stride,
        CaptureStamp stamp, DateTimeOffset capturedAt, ClientFrameContext? context = null)
    {
        if (width <= 0 || height <= 0 || stride < checked(width * 4)
            || bgra.Length != checked(stride * height) || bgra.Length > 64 * 1024 * 1024)
            throw new ArgumentException("capture-pixel-layout-invalid");
        ArgumentException.ThrowIfNullOrWhiteSpace(stamp.WindowIdentity);
        ArgumentException.ThrowIfNullOrWhiteSpace(stamp.FrameId);
        if (context is not null && (context.Geometry is null || context.Geometry.Width != width || context.Geometry.Height != height
            || context.Geometry.Dpi == 0 || string.IsNullOrWhiteSpace(context.Backend)))
            throw new ArgumentException("capture-context-pixel-geometry-mismatch");
        _pixels = bgra.ToArray(); Width = width; Height = height; Stride = stride;
        Stamp = stamp; CapturedAt = capturedAt; Context = context;
    }
    public ReadOnlyMemory<byte> Bgra => _pixels;
    public int Width { get; }
    public int Height { get; }
    public int Stride { get; }
    public CaptureStamp Stamp { get; }
    public DateTimeOffset CapturedAt { get; }
    public ClientFrameContext? Context { get; }
}

public sealed record ClientCaptureResult(CapturedClientFrame? Frame, string Reason = "");

/// <summary>
/// Explicit client-only capture. Never binds/enables listeners or starts a round.
/// Capture and native pixel copying must still require the target foreground.
/// IsCurrent is a bounded thread-safe post-capture check of the original window's
/// identity, geometry, binding/capture generation and frame lifetime. It does not
/// require the target foreground, so the assistant can inspect/select candidates.
/// Input/overlay foreground authorization belongs to the host, not this stamp.
/// Preview shares the binding/lane but must not replace the runtime current stamp.
/// Implementations must not call back into LiveRuntime from these read methods.
/// </summary>
public interface ICapturePort
{
    bool IsBound { get; }
    ValueTask<ClientCaptureResult> CaptureAsync(CancellationToken cancellationToken);
    bool IsCurrent(CaptureStamp stamp);
}
