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
