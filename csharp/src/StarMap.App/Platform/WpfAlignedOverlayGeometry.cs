using System.IO;

namespace StarMap.App.Platform;

/// <summary>
/// Full physical client rectangle plus caller capture-DPI context. Negative screen origins are valid.
/// Applied material and staged siblings share this entire value; Dpi is not the overlay's native/device DPI.
/// </summary>
public readonly record struct WpfOverlayClientGeometry(int Left, int Top, int Width, int Height, uint Dpi)
{
    internal const int MaximumPixelBytes = 128 * 1024 * 1024;

    internal void Validate()
    {
        if (Width <= 0 || Height <= 0 || Dpi == 0)
            throw new ArgumentOutOfRangeException(nameof(Width), "A positive full-client size and DPI are required.");
        _ = checked(Left + Width); _ = checked(Top + Height);
        if ((long)Width * Height * 4 > MaximumPixelBytes)
            throw new ArgumentOutOfRangeException(nameof(Width), "Overlay pixels exceed the 128 MiB frame limit.");
    }

    /// <summary>Only the final client-pixel viewport changes units. Screen origin and H never do.</summary>
    public (double Width, double Height) DipSize(double actualDpiX, double actualDpiY)
    {
        Validate();
        if (!double.IsFinite(actualDpiX) || !double.IsFinite(actualDpiY) || actualDpiX <= 0 || actualDpiY <= 0)
            throw new ArgumentOutOfRangeException(nameof(actualDpiX));
        return (Width * 96d / actualDpiX, Height * 96d / actualDpiY);
    }
}

/// <summary>Column-vector projective display-pixel → full physical client-pixel H.</summary>
public readonly record struct WpfOverlayHomography(double M00, double M01, double M02,
    double M10, double M11, double M12, double M20, double M21, double M22)
{
    public static WpfOverlayHomography Identity => new(1, 0, 0, 0, 1, 0, 0, 0, 1);
    public (double X, double Y) Apply(double x, double y)
    {
        double w = M20 * x + M21 * y + M22;
        return ((M00 * x + M01 * y + M02) / w, (M10 * x + M11 * y + M12) / w);
    }

    internal WpfOverlayHomography Inverse()
    {
        double[] values = [M00, M01, M02, M10, M11, M12, M20, M21, M22];
        if (values.Any(v => !double.IsFinite(v))) throw new InvalidDataException("Overlay H contains a nonfinite value.");
        double scale = values.Max(v => Math.Abs(v));
        if (scale == 0) throw new InvalidDataException("Overlay H is singular.");
        // Normalize before cofactors so equivalent homogeneous matrices do not overflow.
        double a = M00 / scale, b = M01 / scale, c = M02 / scale,
            d = M10 / scale, e = M11 / scale, f = M12 / scale,
            g = M20 / scale, h = M21 / scale, i = M22 / scale;
        double aa = e * i - f * h, ab = c * h - b * i, ac = b * f - c * e,
            ba = f * g - d * i, bb = a * i - c * g, bc = c * d - a * f,
            ca = d * h - e * g, cb = b * g - a * h, cc = a * e - b * d;
        double det = a * aa + b * ba + c * ca;
        if (!double.IsFinite(det) || det == 0) throw new InvalidDataException("Overlay H is singular.");
        // The adjugate alone is an equivalent inverse H; dividing by det is unnecessary.
        return new(aa, ab, ac, ba, bb, bc, ca, cb, cc);
    }

    internal (double X, double Y)[] Corners(int width, int height)
    {
        var matrix = this;
        (double X, double Y)[] input = [(0, 0), (width, 0), (0, height), (width, height)];
        double[] w = input.Select(p => matrix.M20 * p.X + matrix.M21 * p.Y + matrix.M22).ToArray();
        if (w.Any(v => !double.IsFinite(v) || v == 0) || w.Any(v => Math.Sign(v) != Math.Sign(w[0])))
            throw new InvalidDataException("Overlay H crosses the display's projective horizon.");
        var output = input.Select(p => matrix.Apply(p.X, p.Y)).ToArray();
        if (output.Any(p => !double.IsFinite(p.X) || !double.IsFinite(p.Y)))
            throw new InvalidDataException("Overlay H maps a display corner outside finite coordinates.");
        return output;
    }
}

/// <summary>Owned immutable top-down straight-alpha BGRA32 display pixels; no file/URL loading.</summary>
public sealed class WpfOverlayBgra32
{
    readonly byte[] pixels;
    public WpfOverlayBgra32(int width, int height, int stride, ReadOnlySpan<byte> bgra)
    {
        if (width <= 0 || height <= 0 || (long)width * 4 > int.MaxValue || stride < (long)width * 4
            || (long)stride * height > WpfOverlayClientGeometry.MaximumPixelBytes || bgra.Length != (long)stride * height)
            throw new ArgumentException("BGRA dimensions, stride and exact byte count must agree within the 128 MiB frame limit.");
        Width = width; Height = height; Stride = stride; pixels = bgra.ToArray();
    }
    public int Width { get; }
    public int Height { get; }
    public int Stride { get; }
    public byte[] CopyPixels() => (byte[])pixels.Clone();
    internal ReadOnlySpan<byte> Pixels => pixels;
}

/// <summary>Pure CPU projection. No capture, identity, resource lookup, DPI or window authority.</summary>
public static class WpfAlignedOverlayRasterizer
{
    public static WpfOverlayBgra32 Project(WpfOverlayBgra32 display, WpfOverlayHomography displayToClient,
        WpfOverlayClientGeometry client, CancellationToken cancellationToken = default)
    {
        ArgumentNullException.ThrowIfNull(display); client.Validate(); cancellationToken.ThrowIfCancellationRequested();
        var inverse = displayToClient.Inverse();
        var corners = displayToClient.Corners(display.Width, display.Height);
        int width = client.Width, height = client.Height, stride = checked(width * 4);
        var output = new byte[checked(stride * height)];
        // Projective edges stay straight when no horizon crosses the display. Clip before integer conversion.
        int left = (int)Math.Clamp(Math.Floor(corners.Min(p => p.X)), 0, width);
        int top = (int)Math.Clamp(Math.Floor(corners.Min(p => p.Y)), 0, height);
        int right = (int)Math.Clamp(Math.Ceiling(corners.Max(p => p.X)), 0, width);
        int bottom = (int)Math.Clamp(Math.Ceiling(corners.Max(p => p.Y)), 0, height);
        var source = display.Pixels;
        for (int y = top; y < bottom; y++)
        {
            cancellationToken.ThrowIfCancellationRequested();
            for (int x = left; x < right; x++)
            {
                // H maps pixel edges. Sample at destination pixel centers, then subtract the source half-pixel.
                var point = inverse.Apply(x + .5, y + .5);
                if (!(point.X >= 0 && point.X < display.Width && point.Y >= 0 && point.Y < display.Height)) continue;
                double sx = Math.Clamp(point.X - .5, 0, display.Width - 1), sy = Math.Clamp(point.Y - .5, 0, display.Height - 1);
                int x0 = (int)sx, y0 = (int)sy, x1 = Math.Min(x0 + 1, display.Width - 1), y1 = Math.Min(y0 + 1, display.Height - 1);
                double dx = sx - x0, dy = sy - y0;
                int p00 = y0 * display.Stride + x0 * 4, p10 = y0 * display.Stride + x1 * 4,
                    p01 = y1 * display.Stride + x0 * 4, p11 = y1 * display.Stride + x1 * 4;
                double w00 = (1 - dx) * (1 - dy), w10 = dx * (1 - dy), w01 = (1 - dx) * dy, w11 = dx * dy;
                double a00 = source[p00 + 3] * w00, a10 = source[p10 + 3] * w10,
                    a01 = source[p01 + 3] * w01, a11 = source[p11 + 3] * w11;
                double alpha = a00 + a10 + a01 + a11;
                int at = y * stride + x * 4;
                output[at + 3] = RoundByte(alpha);
                if (alpha <= 0) continue;
                // Interpolate premultiplied components, then return straight BGRA for WPF Bgra32.
                for (int channel = 0; channel < 3; channel++)
                    output[at + channel] = RoundByte((source[p00 + channel] * a00 + source[p10 + channel] * a10
                        + source[p01 + channel] * a01 + source[p11 + channel] * a11) / alpha);
            }
        }
        cancellationToken.ThrowIfCancellationRequested();
        return new(width, height, stride, output);
    }

    static byte RoundByte(double value) => (byte)Math.Clamp((int)Math.Round(value), 0, 255);
}
