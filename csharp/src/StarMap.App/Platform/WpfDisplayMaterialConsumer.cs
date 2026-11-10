using System.Windows.Media;
using System.Windows.Media.Imaging;
using StarMap.Runtime;

namespace StarMap.App.Platform;

internal static class WpfDisplayMaterialConsumer
{
    // Corresponds to the original hidden Image.Ready: WPF has consumed the
    // complete producer BGRA buffer. This neither creates nor shows a HWND.
    internal static BitmapSource PrepareMaterialReady(PreparedDisplayLayer material)
    {
        var image = BitmapSource.Create(material.Width, material.Height, 96, 96, PixelFormats.Bgra32,
            null, material.CopyPixels(), material.Stride);
        image.Freeze();
        return image;
    }
}

/// <summary>One immutable full-client projection, independent of frame tickets, visibility and opacity.</summary>
internal sealed class WpfDisplayProjectionCache
{
    readonly object sync = new();
    PreparedDisplayLayer? material;
    WpfAlignedOverlayPreparedFrame? pixels;

    internal async ValueTask<WpfAlignedOverlayPreparedFrame> PrepareAsync(WpfAlignedOverlayTicket ticket,
        PreparedDisplayLayer layer, WpfOverlayHomography matrix, WpfOverlayClientGeometry client, double opacity)
    {
        lock (sync)
            if (ReferenceEquals(material, layer) && pixels is { } cached && cached.DisplayToClient == matrix && cached.Client == client)
                return WpfAlignedOverlaySurface.ReuseProjection(ticket, cached, opacity);
        var prepared = await Task.Run(() => WpfAlignedOverlaySurface.Prepare(ticket,
            new(layer.Width, layer.Height, layer.Stride, layer.CopyPixels()), matrix, client, opacity)).ConfigureAwait(false);
        lock (sync) { material = layer; pixels = prepared; }
        return prepared;
    }

    internal void Clear() { lock (sync) { material = null; pixels = null; } }
}
