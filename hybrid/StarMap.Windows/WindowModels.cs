namespace StarMap.Windows;

/// <summary>Stable facts identifying one native window and the process that owns it.</summary>
public sealed record WindowIdentity(
    nint Hwnd,
    uint ProcessId,
    long ProcessCreationUtcTicks,
    string ExecutablePath);

/// <summary>Client area dimensions and screen origin in physical pixels, with a half-open extent.</summary>
public readonly record struct ClientGeometry(
    int Width,
    int Height,
    int Left,
    int Top,
    uint Dpi)
{
    public int Right => checked(Left + Width);
    public int Bottom => checked(Top + Height);
}

/// <summary>A rectangle in client coordinates. Right and Bottom are exclusive.</summary>
public readonly record struct ClientRect(int Left, int Top, int Right, int Bottom)
{
    public int Width => checked(Right - Left);
    public int Height => checked(Bottom - Top);
}

/// <summary>One coherent read of native window and client-area facts.</summary>
public sealed record WindowSnapshot(
    WindowIdentity Identity,
    string Title,
    bool Visible,
    bool Minimized,
    ClientGeometry Geometry,
    nint ForegroundHwnd);

/// <summary>Identity and geometry against which captured pixels were acquired.</summary>
public sealed record ClientCaptureContext(WindowIdentity Identity, ClientGeometry Geometry);

/// <summary>Owned, top-down 32-bit BGRA pixels and their native acquisition context.</summary>
public sealed record CapturedClientFrame(
    int Width,
    int Height,
    int Stride,
    ReadOnlyMemory<byte> Bgra32,
    ClientCaptureContext Context,
    ClientRect RequestedClientBounds,
    ClientRect ClientBounds,
    string Acquisition);
