namespace StarMap.Runtime;

// Adapted from Pillow 12.3.0 Image.py, libImaging/Resample.c, Convert.c,
// AlphaComposite.c and ImagingUtils.h. See ThirdParty/Pillow-LICENSE.txt.
// BGRA is a channel permutation of RGBA; alpha remains the fourth byte.
internal static class PillowCompatibleResampler
{
    const int Precision = 22;
    sealed record Coefficients(int Start, int[] Weights);

    internal static byte[] ResizeBgra(byte[] source, int width, int height, int targetWidth, int targetHeight,
        CancellationToken cancellation)
    {
        if (source.Length != DisplayMaterialPreparer.PixelLength(width, height)) throw new InvalidDataException("icon-canvas-mismatch");
        DisplayMaterialPreparer.PixelLength(targetWidth, targetHeight);
        cancellation.ThrowIfCancellationRequested();
        if (width == targetWidth && height == targetHeight) return (byte[])source.Clone();
        var data = (byte[])source.Clone();
        for (int i = 0; i < data.Length; i += 4)
        {
            if ((i & 0xffff) == 0) cancellation.ThrowIfCancellationRequested();
            int alpha = data[i + 3];
            for (int channel = 0; channel < 3; channel++)
            { int value = data[i + channel] * alpha + 128; data[i + channel] = (byte)ShiftForDiv255(value); }
        }
        // Image.resize performs the vertical pass first for very tall images.
        bool verticalFirst = height > (long)width * 100 && targetHeight < height;
        if (verticalFirst)
        {
            if (height != targetHeight) data = Vertical(data, width, height, targetHeight, cancellation);
            if (width != targetWidth) data = Horizontal(data, width, targetHeight, targetWidth, cancellation);
        }
        else
        {
            if (width != targetWidth) data = Horizontal(data, width, height, targetWidth, cancellation);
            if (height != targetHeight) data = Vertical(data, targetWidth, height, targetHeight, cancellation);
        }
        for (int i = 0; i < data.Length; i += 4)
        {
            if ((i & 0xffff) == 0) cancellation.ThrowIfCancellationRequested();
            int alpha = data[i + 3];
            if (alpha is 0 or 255) continue;
            for (int channel = 0; channel < 3; channel++)
                data[i + channel] = (byte)Math.Min(255, 255 * data[i + channel] / alpha);
        }
        return data;
    }

    static Coefficients[] Precompute(int input, int output, CancellationToken cancellation)
    {
        double scale = (double)input / output, filterScale = Math.Max(1, scale), support = 3 * filterScale;
        var coefficients = new Coefficients[output];
        for (int x = 0; x < output; x++)
        {
            cancellation.ThrowIfCancellationRequested();
            double center = (x + .5) * scale;
            int start = Math.Max(0, (int)(center - support + .5));
            int stop = Math.Min(input, (int)(center + support + .5));
            var weights = new double[stop - start]; double sum = 0;
            for (int k = 0; k < weights.Length; k++)
                sum += weights[k] = Lanczos((k + start - center + .5) * (1 / filterScale));
            var fixedWeights = new int[weights.Length];
            for (int k = 0; k < weights.Length; k++)
            {
                double normalized = sum == 0 ? weights[k] : weights[k] / sum;
                fixedWeights[k] = (int)(normalized * (1 << Precision) + (normalized < 0 ? -.5 : .5));
            }
            coefficients[x] = new(start, fixedWeights);
        }
        return coefficients;
    }
    static double Lanczos(double x) => x < -3 || x >= 3 ? 0 : Sinc(x) * Sinc(x / 3);
    static double Sinc(double x) { if (x == 0) return 1; x *= Math.PI; return Math.Sin(x) / x; }
    static byte Clip(int value) => (byte)Math.Clamp(value >> Precision, 0, 255);
    static int ShiftForDiv255(int value) => ((value >> 8) + value) >> 8;

    static byte[] Horizontal(byte[] source, int width, int height, int target, CancellationToken cancellation)
    {
        var result = new byte[DisplayMaterialPreparer.PixelLength(target, height)];
        var coefficients = Precompute(width, target, cancellation);
        for (int y = 0; y < height; y++)
        {
            cancellation.ThrowIfCancellationRequested();
            for (int x = 0; x < target; x++)
            {
                var c = coefficients[x];
                for (int channel = 0; channel < 4; channel++)
                {
                    int value = 1 << (Precision - 1);
                    for (int k = 0; k < c.Weights.Length; k++) value += source[(y * width + c.Start + k) * 4 + channel] * c.Weights[k];
                    result[(y * target + x) * 4 + channel] = Clip(value);
                }
            }
        }
        return result;
    }
    static byte[] Vertical(byte[] source, int width, int height, int target, CancellationToken cancellation)
    {
        var result = new byte[DisplayMaterialPreparer.PixelLength(width, target)];
        var coefficients = Precompute(height, target, cancellation);
        for (int y = 0; y < target; y++)
        {
            cancellation.ThrowIfCancellationRequested();
            var c = coefficients[y];
            for (int x = 0; x < width; x++)
                for (int channel = 0; channel < 4; channel++)
                {
                    int value = 1 << (Precision - 1);
                    for (int k = 0; k < c.Weights.Length; k++) value += source[((c.Start + k) * width + x) * 4 + channel] * c.Weights[k];
                    result[(y * width + x) * 4 + channel] = Clip(value);
                }
        }
        return result;
    }

    internal static void AlphaComposite(byte[] destination, int width, int height, byte[] source, int sourceWidth,
        int sourceHeight, int left, int top, CancellationToken cancellation)
    {
        // Use wide clipping arithmetic: an off-canvas entrance must not wrap.
        int x0 = (int)Math.Min(sourceWidth, Math.Max(0L, -(long)left));
        int y0 = (int)Math.Min(sourceHeight, Math.Max(0L, -(long)top));
        int x1 = (int)Math.Min(sourceWidth, (long)width - left), y1 = (int)Math.Min(sourceHeight, (long)height - top);
        if (x0 >= x1 || y0 >= y1) return;
        for (int y = y0; y < y1; y++)
        {
            cancellation.ThrowIfCancellationRequested();
            for (int x = x0; x < x1; x++)
            {
                int s = (y * sourceWidth + x) * 4, d = ((y + top) * width + x + left) * 4;
                int alpha = source[s + 3]; if (alpha == 0) continue;
                int blend = destination[d + 3] * (255 - alpha), outAlpha255 = alpha * 255 + blend;
                int coef1 = alpha * 255 * 255 * (1 << 7) / outAlpha255, coef2 = 255 * (1 << 7) - coef1;
                for (int channel = 0; channel < 3; channel++)
                    destination[d + channel] = (byte)(ShiftForDiv255(source[s + channel] * coef1 + destination[d + channel] * coef2 + (0x80 << 7)) >> 7);
                destination[d + 3] = (byte)ShiftForDiv255(outAlpha255 + 0x80);
            }
        }
    }
}
