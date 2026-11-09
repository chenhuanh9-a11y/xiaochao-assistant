using System.IO;
using System.Windows.Media;
using System.Windows.Media.Imaging;
using StarMap.Core.Resources;

namespace StarMap.App;

internal static class ResourcePreview
{
    const int MaximumSide = 1600;
    const long MaximumImageBytes = 64L * 1024 * 1024;
    const long MaximumSourcePixels = 64L * 1024 * 1024;

    internal static BitmapSource LoadImage(string path, CancellationToken cancellation)
    {
        cancellation.ThrowIfCancellationRequested();
        using var stream = File.OpenRead(path);
        if (stream.Length > MaximumImageBytes) throw new InvalidDataException("预览图片超过 64 MiB。请使用较小的楼层预览。");
        var decoder = BitmapDecoder.Create(stream, BitmapCreateOptions.DelayCreation, BitmapCacheOption.OnDemand);
        var first = decoder.Frames[0];
        int width = first.PixelWidth, height = first.PixelHeight;
        if (width <= 0 || height <= 0 || (long)width * height > MaximumSourcePixels)
            throw new InvalidDataException("预览图片画布超过 64 百万像素。");
        cancellation.ThrowIfCancellationRequested();
        stream.Position = 0;
        var image = new BitmapImage();
        image.BeginInit(); image.CacheOption = BitmapCacheOption.OnLoad;
        if (width >= height && width > MaximumSide) image.DecodePixelWidth = MaximumSide;
        else if (height > MaximumSide) image.DecodePixelHeight = MaximumSide;
        image.StreamSource = stream; image.EndInit(); image.Freeze();
        cancellation.ThrowIfCancellationRequested();
        return image;
    }

    internal static BitmapSource FromMask(NpyArray mask, CancellationToken cancellation)
    {
        if (mask.Shape.Count != 2 || mask.Shape[0] <= 0 || mask.Shape[1] <= 0)
            throw new InvalidDataException("结构 mask 不是有效二维数组。");
        int height = mask.Shape[0], width = mask.Shape[1];
        if (mask.Bytes.Length != checked(width * height)) throw new InvalidDataException("预览只支持逐字节二值 mask。");
        var pixels = Outline(mask.Bytes.Span, width, height, cancellation);
        var bitmap = BitmapSource.Create(width, height, 96, 96, PixelFormats.Gray8, null, pixels, width);
        bitmap.Freeze();
        return bitmap;
    }

    // The stored masks describe occupied regions. A pale fill and their actual
    // four-neighbour boundary keep the fallback legible without inventing walls.
    internal static byte[] Outline(ReadOnlySpan<byte> mask, int width, int height, CancellationToken cancellation)
    {
        if (width <= 0 || height <= 0 || mask.Length != checked(width * height)) throw new ArgumentException("Invalid mask dimensions.");
        byte[] pixels = new byte[mask.Length];
        for (int y = 0; y < height; y++)
        {
            cancellation.ThrowIfCancellationRequested();
            for (int x = 0; x < width; x++)
            {
                int i = y * width + x;
                pixels[i] = mask[i] == 0 ? (byte)250
                    : x == 0 || y == 0 || x == width - 1 || y == height - 1
                        || mask[i - 1] == 0 || mask[i + 1] == 0 || mask[i - width] == 0 || mask[i + width] == 0
                        ? (byte)40 : (byte)225;
            }
        }
        return pixels;
    }
}
