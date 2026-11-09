using System.Runtime.InteropServices;
using OpenCvSharp;
using StarMap.Core.Display;
using StarMap.Vision;

namespace StarMap.Runtime;

/// <summary>custom_maps.render_game_layer with an empty route: one art warp, then entrance icons.</summary>
internal static class DisplayAtlasRenderer
{
    internal static byte[] Render(Mat image, DisplayBaseAtlasRecipe recipe, DisplayBridgeResult bridge,
        CancellationToken cancellation)
    {
        DisplayMaterialPreparer.PixelLength(recipe.NativeWidth, recipe.NativeHeight);
        if (!recipe.ImageToNative.IsInvertible) throw new InvalidDataException("display-atlas-affine-invalid");
        cancellation.ThrowIfCancellationRequested();
        using var matrix = new Mat(2, 3, MatType.CV_64FC1);
        var m = recipe.ImageToNative;
        matrix.Set(0, 0, m.M00); matrix.Set(0, 1, m.M01); matrix.Set(0, 2, m.M02);
        matrix.Set(1, 0, m.M10); matrix.Set(1, 1, m.M11); matrix.Set(1, 2, m.M12);
        using var native = new Mat();
        // Channel permutation commutes with OpenCV's independent channel warp.
        // Keep straight BGRA here; premultiplication belongs only to Pillow resize.
        Cv2.WarpAffine(image, native, matrix, new Size(recipe.NativeWidth, recipe.NativeHeight),
            InterpolationFlags.Linear, BorderTypes.Constant, new Scalar(0, 0, 0, 0));
        var pixels = DisplayMaterialPreparer.CopyBgra(native, cancellation);
        if (!recipe.ArtIncludesExits && recipe.ExitIconPath is { } path)
        {
            var encoded = DisplayMaterialPreparer.ReadImage(bridge, path);
            cancellation.ThrowIfCancellationRequested();
            // Known headers are bounded before native allocation. Unknown
            // codecs retain their existing decoder and post-decode checks.
            EncodedDisplayImageInfo? header = null;
            bool iconAvailable = true;
            try { header = DisplayImageDecodeBudget.RequireWithinLimit(encoded, DisplayMaterialPreparer.MaximumPixelBytes); }
            catch (InvalidDataException error) when (error.Message == DisplayImageDecodeBudget.TruncatedPngReason)
            {
                // The original Pillow producer maps this optional icon's
                // truncated-chunk decode failure to None. File snapshot and
                // surface-budget errors retain their fatal behavior.
                iconAvailable = false;
            }
            if (iconAvailable)
            {
                using var decoded = DecodeOptionalIcon(encoded);
                if (decoded is not null) CompositeEntrances(pixels, decoded, recipe, header, cancellation);
            }
        }
        bool visible = false;
        for (int i = 0; i < pixels.Length; i += 4)
        {
            if ((i & 0xffff) == 0) cancellation.ThrowIfCancellationRequested();
            if (pixels[i + 3] == 0) pixels[i] = pixels[i + 1] = pixels[i + 2] = 0;
            else visible = true;
        }
        if (!visible) throw new InvalidDataException("display-atlas-producer-empty");
        return pixels;
    }

    static Mat? DecodeOptionalIcon(byte[] encoded)
    {
        // CustomMapLibrary.exit_icon returns None when Pillow cannot open/decode
        // this optional image. Snapshot/file-read failures remain fatal above;
        // only the decoder's unavailable-icon outcome omits entrance drawing.
        try
        {
            var decoded = Cv2.ImDecode(encoded, ImreadModes.Unchanged);
            if (!decoded.Empty()) return decoded;
            decoded.Dispose();
        }
        catch (OpenCVException) { }
        return null;
    }

    static void CompositeEntrances(byte[] pixels, Mat decoded, DisplayBaseAtlasRecipe recipe,
        EncodedDisplayImageInfo? header, CancellationToken cancellation)
    {
        DisplayMaterialPreparer.PixelLength(recipe.ExitIconSize, recipe.ExitIconSize);
        DisplayMaterialPreparer.RequireNativeCanvas(decoded);
        int width = decoded.Cols, height = decoded.Rows, channels = decoded.Channels();
        if (channels is not (1 or 3 or 4))
            throw new InvalidDataException("display-atlas-icon-format-unavailable");
        byte[] iconPixels;
        if (decoded.Depth() == MatType.CV_16U && header?.PngBitDepth == 16)
            iconPixels = ConvertPillowPng16(decoded, width, height, channels, header, cancellation);
        else
        {
            if (decoded.Depth() != MatType.CV_8U) throw new InvalidDataException("display-atlas-icon-format-unavailable");
            using var converted = new Mat();
            Mat icon = decoded;
            if (channels != 4)
            {
                // CustomMapLibrary.exit_icon explicitly converts its icon to RGBA.
                Cv2.CvtColor(decoded, converted, channels == 1
                    ? ColorConversionCodes.GRAY2BGRA : ColorConversionCodes.BGR2BGRA);
                icon = converted;
            }
            DisplayMaterialPreparer.RequireNativeCanvas(icon);
            if (icon.Cols != width || icon.Rows != height || icon.Type() != MatType.CV_8UC4)
                throw new InvalidDataException("display-atlas-icon-converted-canvas-or-format-changed");
            iconPixels = DisplayMaterialPreparer.CopyBgra(icon, cancellation);
            if (header is not null) ApplyPngTransparency(iconPixels, width, height, header, cancellation);
        }
        var resized = PillowCompatibleResampler.ResizeBgra(iconPixels, width, height,
            recipe.ExitIconSize, recipe.ExitIconSize, cancellation);
        foreach (var entrance in recipe.Entrances)
        {
            cancellation.ThrowIfCancellationRequested();
            // Coordinates are already the original producer's rounded native top/left.
            PillowCompatibleResampler.AlphaComposite(pixels, recipe.NativeWidth, recipe.NativeHeight,
                resized, recipe.ExitIconSize, recipe.ExitIconSize, entrance.NativeLeft, entrance.NativeTop, cancellation);
        }
    }

    static byte[] ConvertPillowPng16(Mat decoded, int width, int height, int channels,
        EncodedDisplayImageInfo? header, CancellationToken cancellation)
    {
        var pixels = new byte[DisplayMaterialPreparer.PixelLength(width, height)];
        var row = new short[checked(width * channels)];
        for (int y = 0; y < height; y++)
        {
            cancellation.ThrowIfCancellationRequested();
            // Marshal preserves each unsigned sample's native bit pattern. This
            // avoids ConvertTo's rounding when Pillow requires a high-byte floor.
            Marshal.Copy(decoded.Ptr(y), row, 0, row.Length);
            for (int x = 0; x < width; x++)
            {
                int source = x * channels, target = (y * width + x) * 4;
                if (channels == 1)
                {
                    // Pillow opens grayscale PNG16 as I;16, then convert(RGBA)
                    // saturates integer gray rather than reducing its bit depth.
                    byte gray = (byte)Math.Min(255, (int)unchecked((ushort)row[source]));
                    pixels[target] = pixels[target + 1] = pixels[target + 2] = gray;
                    pixels[target + 3] = header is { PngColorType: 0, PngTransparentGray: { } grayKey }
                        && gray == unchecked((byte)grayKey) ? (byte)0 : (byte)255;
                }
                else
                {
                    // Pillow's RGB/RGBA/gray-alpha PNG16 reader keeps high bytes;
                    // OpenCV already expands gray-alpha into native BGRA16 here.
                    for (int channel = 0; channel < 3; channel++)
                        pixels[target + channel] = (byte)(unchecked((ushort)row[source + channel]) >> 8);
                    pixels[target + 3] = channels == 4 ? (byte)(unchecked((ushort)row[source + 3]) >> 8) : (byte)255;
                    if (header is { PngColorType: 2, PngTransparentRgb: { } rgbKey })
                    {
                        // OpenCV reports source tRNS in the 16-bit decode. Pillow
                        // converts RGB samples to their high bytes first, then
                        // compares those bytes to the key's low bytes.
                        bool transparent = pixels[target + 2] == unchecked((byte)rgbKey.Red)
                            && pixels[target + 1] == unchecked((byte)rgbKey.Green)
                            && pixels[target] == unchecked((byte)rgbKey.Blue);
                        pixels[target + 3] = transparent ? (byte)0 : (byte)255;
                    }
                }
            }
        }
        return pixels;
    }

    static void ApplyPngTransparency(byte[] pixels, int width, int height,
        EncodedDisplayImageInfo header, CancellationToken cancellation)
    {
        if (header.PngColorType == 0 && header.PngTransparentGray is { } grayKey
            && header.PngBitDepth is 1 or 2 or 4 or 8)
        {
            // Pillow opens 1-bit grayscale as mode 1 and normalizes its tRNS
            // key to 0/255. For 2/4-bit images Pillow opens mode L: samples
            // are expanded, but the nonzero key remains unscaled in info.
            int pillowKey = header.PngBitDepth switch
            {
                1 => grayKey switch { 0 => 0, 1 => 255, _ => -1 },
                2 or 4 or 8 => grayKey,
                _ => -1
            };
            if (pillowKey < 0 || pillowKey > byte.MaxValue) return;
            for (int i = 0; i < checked(width * height); i++)
            {
                if ((i & 0x3fff) == 0) cancellation.ThrowIfCancellationRequested();
                int p = i * 4;
                if (pixels[p] == pillowKey) pixels[p + 3] = 0;
            }
        }
        else if (header.PngBitDepth == 8 && header.PngColorType == 2
            && header.PngTransparentRgb is { } rgbKey)
        {
            for (int i = 0; i < checked(width * height); i++)
            {
                if ((i & 0x3fff) == 0) cancellation.ThrowIfCancellationRequested();
                int p = i * 4;
                if (pixels[p + 2] == rgbKey.Red && pixels[p + 1] == rgbKey.Green && pixels[p] == rgbKey.Blue)
                    pixels[p + 3] = 0;
            }
        }
    }
}
