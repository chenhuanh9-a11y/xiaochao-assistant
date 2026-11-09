namespace StarMap.App.Platform;

public readonly record struct WindowsGraphicsCaptureRect(int Left, int Top, int Right, int Bottom)
{
    public int Width => checked(Right - Left);
    public int Height => checked(Bottom - Top);
}

public sealed record WindowsGraphicsCaptureBounds(ClientGeometry Client, WindowsGraphicsCaptureRect Window,
    WindowsGraphicsCaptureRect? ExtendedFrame, int DwmResult);

public sealed record WindowsGraphicsCaptureCrop(int Left, int Top, int Width, int Height,
    WindowsGraphicsCaptureRect SurfaceScreenBounds, string Source);

/// <summary>Measured geometry only. No assumed border/title sizes, scaling or screen fallback.</summary>
internal static class WindowsGraphicsCaptureGeometry
{
    internal const long MaximumFrameBytes = 128L * 1024 * 1024;

    internal static int ByteCount(int width, int height)
    {
        long bytes;
        try { bytes = checked((long)width * height * 4); }
        catch (OverflowException) { throw new ClientCaptureException("capture-wgc-frame-too-large", "WGC BGRA 尺寸乘积溢出；未分配像素。"); }
        if (width <= 0 || height <= 0 || bytes <= 0 || bytes > MaximumFrameBytes)
            throw new ClientCaptureException("capture-wgc-frame-too-large", "WGC 单帧须为正尺寸且不超过 128 MiB；未分配像素。");
        return checked((int)bytes);
    }

    internal static WindowsGraphicsCaptureCrop Resolve(WindowsGraphicsCaptureBounds bounds, int contentWidth, int contentHeight)
    {
        _ = ByteCount(contentWidth, contentHeight);
        bool Matches(WindowsGraphicsCaptureRect rect) => rect.Width == contentWidth && rect.Height == contentHeight;
        var extendedMatches = bounds.ExtendedFrame is { } extended && Matches(extended);
        var windowMatches = Matches(bounds.Window);
        if (extendedMatches && windowMatches && bounds.ExtendedFrame!.Value != bounds.Window)
            throw new ClientCaptureException("capture-wgc-crop-ambiguous", "WGC 内容尺寸对应两个不同的实测屏幕边界，不能猜测客户区原点。", bounds.Client);
        WindowsGraphicsCaptureRect surface;
        string source;
        if (extendedMatches)
        {
            surface = bounds.ExtendedFrame!.Value;
            source = "ContentSize == DWMWA_EXTENDED_FRAME_BOUNDS; client origin from ClientToScreen";
        }
        else if (windowMatches)
        {
            surface = bounds.Window;
            source = "ContentSize == GetWindowRect; client origin from ClientToScreen";
        }
        else throw new ClientCaptureException("capture-wgc-surface-bounds-unsupported",
            "WGC ContentSize 与本次实测 DWM/窗口边界不一致；不缩放、不猜边框，也不扩大为屏幕采集。", bounds.Client);
        var left = checked(bounds.Client.Left - surface.Left);
        var top = checked(bounds.Client.Top - surface.Top);
        if (left < 0 || top < 0 || (long)left + bounds.Client.Width > contentWidth || (long)top + bounds.Client.Height > contentHeight)
            throw new ClientCaptureException("capture-wgc-client-outside-surface", "完整客户区不在本次 WGC window surface 内；本帧已丢弃。", bounds.Client);
        _ = ByteCount(bounds.Client.Width, bounds.Client.Height);
        return new(left, top, bounds.Client.Width, bounds.Client.Height, surface, source);
    }

    internal static byte[] Crop(byte[] surface, int surfaceWidth, int surfaceHeight, WindowsGraphicsCaptureCrop crop, CancellationToken token)
    {
        if (surface.Length != ByteCount(surfaceWidth, surfaceHeight)) throw new ArgumentException("WGC surface 字节数与 BGRA 尺寸不一致。", nameof(surface));
        if (crop.Left < 0 || crop.Top < 0 || (long)crop.Left + crop.Width > surfaceWidth || (long)crop.Top + crop.Height > surfaceHeight)
            throw new ArgumentException("客户区裁剪超出原始 surface。", nameof(crop));
        var pixels = new byte[ByteCount(crop.Width, crop.Height)];
        var stride = checked(crop.Width * 4);
        for (var row = 0; row < crop.Height; row++)
        {
            token.ThrowIfCancellationRequested();
            Buffer.BlockCopy(surface, checked(((row + crop.Top) * surfaceWidth + crop.Left) * 4), pixels, row * stride, stride);
        }
        return pixels;
    }
}
