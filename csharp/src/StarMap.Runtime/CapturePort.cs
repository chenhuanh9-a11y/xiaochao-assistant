namespace StarMap.Runtime;

/// <summary>Opaque exact physical window identity, binding generation, and captured frame identity.</summary>
public sealed record CaptureStamp(string WindowIdentity, long Generation, string FrameId);

/// <summary>Owned immutable top-down BGRA32 client pixels; constructor copies the supplied memory.</summary>
public sealed class CapturedClientFrame
{
    private readonly byte[] _pixels;
    public CapturedClientFrame(ReadOnlyMemory<byte> bgra, int width, int height, int stride,
        CaptureStamp stamp, DateTimeOffset capturedAt)
    {
        if (width <= 0 || height <= 0 || stride < checked(width * 4)
            || bgra.Length != checked(stride * height) || bgra.Length > 64 * 1024 * 1024)
            throw new ArgumentException("capture-pixel-layout-invalid");
        ArgumentException.ThrowIfNullOrWhiteSpace(stamp.WindowIdentity);
        ArgumentException.ThrowIfNullOrWhiteSpace(stamp.FrameId);
        _pixels = bgra.ToArray(); Width = width; Height = height; Stride = stride;
        Stamp = stamp; CapturedAt = capturedAt;
    }
    public ReadOnlyMemory<byte> Bgra => _pixels;
    public int Width { get; }
    public int Height { get; }
    public int Stride { get; }
    public CaptureStamp Stamp { get; }
    public DateTimeOffset CapturedAt { get; }
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
