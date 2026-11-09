using System.Buffers.Binary;

namespace StarMap.Vision;

public sealed record EncodedDisplayImageInfo(long Width, long Height, int DecodedBytesPerPixel,
    int? PngBitDepth = null, int? PngColorType = null)
{
    public ushort? PngTransparentGray { get; init; }
    public (ushort Red, ushort Green, ushort Blue)? PngTransparentRgb { get; init; }
    public bool PngChunkLengthsValid { get; init; } = true;
}

/// <summary>
/// Header-only bounds for the installed PNG/JPEG/WebP display formats, before native decoding.
/// Unknown codecs keep their existing decoder behavior and post-decode checks; this is not an all-codec allocation limit.
/// Does not change the stricter installed-package image contract or establish successful decoding.
/// </summary>
public static class DisplayImageDecodeBudget
{
    public const long MaximumPixelBytes = 128L * 1024 * 1024;
    public const string TruncatedPngReason = "display-image-decode-invalid-truncated-png-chunk";

    public static EncodedDisplayImageInfo? RequireWithinLimit(ReadOnlySpan<byte> encoded, long maximumBytes = MaximumPixelBytes)
    {
        if (maximumBytes <= 0) throw new ArgumentOutOfRangeException(nameof(maximumBytes));
        var info = TryRead(encoded);
        if (info is { PngChunkLengthsValid: false }) throw new InvalidDataException(TruncatedPngReason);
        // Bound both the unchanged native canvas and the eventual BGRA canvas.
        // Division keeps attacker-controlled dimensions from overflowing Int64.
        if (info is not null && info.Width > maximumBytes / Math.Max(4, info.DecodedBytesPerPixel) / info.Height)
            throw new InvalidDataException("display-image-surface-budget-exceeded-before-decode");
        return info;
    }

    public static EncodedDisplayImageInfo? TryRead(ReadOnlySpan<byte> encoded)
    {
        if (encoded.Length >= 8 && encoded[..8].SequenceEqual(new byte[] { 137, 80, 78, 71, 13, 10, 26, 10 }))
            return Png(encoded);
        if (encoded.Length >= 2 && encoded[0] == 255 && encoded[1] == 216) return Jpeg(encoded);
        if (encoded.Length >= 12 && encoded[..4].SequenceEqual("RIFF"u8) && encoded.Slice(8, 4).SequenceEqual("WEBP"u8))
            return WebP(encoded);
        return null;
    }

    // https://www.w3.org/TR/png/ : IHDR sample depth/type and tRNS expansion.
    static EncodedDisplayImageInfo? Png(ReadOnlySpan<byte> bytes)
    {
        if (bytes.Length < 33 || U32Be(bytes, 8) != 13 || !bytes.Slice(12, 4).SequenceEqual("IHDR"u8)) return null;
        long width = U32Be(bytes, 16), height = U32Be(bytes, 20);
        int depth = bytes[24], type = bytes[25];
        bool legal = type switch { 0 => depth is 1 or 2 or 4 or 8 or 16, 3 => depth is 1 or 2 or 4 or 8,
            2 or 4 or 6 => depth is 8 or 16, _ => false };
        if (!legal || width == 0 || height == 0 || bytes[26] != 0 || bytes[27] != 0 || bytes[28] > 1) return null;
        bool transparent = type is 4 or 6;
        ushort? grayKey = null;
        (ushort Red, ushort Green, ushort Blue)? rgbKey = null;
        bool lengthsValid = true;
        for (int offset = 33; offset <= bytes.Length - 8;)
        {
            uint length = U32Be(bytes, offset);
            // The decoder can allocate from a claimed chunk length before it
            // discovers truncation. Keep the known IHDR dimensions on this miss.
            if ((long)offset + 12 + length > bytes.Length) { lengthsValid = false; break; }
            var chunk = bytes.Slice(offset + 4, 4);
            if (chunk.SequenceEqual("tRNS"u8))
            {
                transparent = true;
                if (type == 0 && length == 2) grayKey = BinaryPrimitives.ReadUInt16BigEndian(bytes.Slice(offset + 8, 2));
                if (type == 2 && length == 6) rgbKey = (BinaryPrimitives.ReadUInt16BigEndian(bytes.Slice(offset + 8, 2)),
                    BinaryPrimitives.ReadUInt16BigEndian(bytes.Slice(offset + 10, 2)), BinaryPrimitives.ReadUInt16BigEndian(bytes.Slice(offset + 12, 2)));
            }
            if (chunk.SequenceEqual("IEND"u8)) break;
            offset = checked(offset + 12 + (int)length);
        }
        // The pinned OpenCV decoder retains grayscale+tRNS as one channel.
        // Pillow-compatible alpha is reconstructed later in the icon producer.
        int channels = type == 0 ? 1 : transparent ? 4 : 3;
        return new(width, height, channels * (depth == 16 ? 2 : 1), depth, type)
        { PngTransparentGray = grayKey, PngTransparentRgb = rgbKey, PngChunkLengthsValid = lengthsValid };
    }

    static EncodedDisplayImageInfo? Jpeg(ReadOnlySpan<byte> bytes)
    {
        int offset = 2;
        while (offset < bytes.Length)
        {
            if (bytes[offset++] != 255) return null;
            while (offset < bytes.Length && bytes[offset] == 255) offset++;
            if (offset >= bytes.Length) return null;
            int marker = bytes[offset++];
            if (marker is 0 or 216 or 217 or 218) return null;
            if (marker is >= 208 and <= 215 or 1) continue;
            if (offset > bytes.Length - 2) return null;
            int length = BinaryPrimitives.ReadUInt16BigEndian(bytes.Slice(offset, 2));
            if (length < 2 || offset > bytes.Length - length) return null;
            if (marker is >= 192 and <= 207 && marker is not (196 or 200 or 204))
            {
                if (length < 8) return null;
                int height = BinaryPrimitives.ReadUInt16BigEndian(bytes.Slice(offset + 3, 2));
                int width = BinaryPrimitives.ReadUInt16BigEndian(bytes.Slice(offset + 5, 2));
                int components = bytes[offset + 7], depth = bytes[offset + 2];
                if (width == 0 || height == 0 || components is not (1 or 3 or 4) || depth is < 1 or > 16) return null;
                return new(width, height, (components == 1 ? 1 : 3) * (depth > 8 ? 2 : 1));
            }
            offset += length;
        }
        return null;
    }

    // https://developers.google.com/speed/webp/docs/riff_container
    // https://developers.google.com/speed/webp/docs/webp_lossless_bitstream_specification
    static EncodedDisplayImageInfo? WebP(ReadOnlySpan<byte> bytes)
    {
        for (int offset = 12; offset <= bytes.Length - 8;)
        {
            uint length = BinaryPrimitives.ReadUInt32LittleEndian(bytes.Slice(offset + 4, 4));
            if ((long)offset + 8 + length > bytes.Length) return null;
            var type = bytes.Slice(offset, 4); int start = offset + 8;
            if (type.SequenceEqual("VP8X"u8) && length >= 10)
                return new(U24Le(bytes, start + 4) + 1L, U24Le(bytes, start + 7) + 1L, 4);
            if (type.SequenceEqual("VP8L"u8) && length >= 5 && bytes[start] == 0x2f)
            {
                uint bits = BinaryPrimitives.ReadUInt32LittleEndian(bytes.Slice(start + 1, 4));
                return new((bits & 0x3fff) + 1L, ((bits >> 14) & 0x3fff) + 1L, 4);
            }
            if (type.SequenceEqual("VP8 "u8) && length >= 10 && bytes.Slice(start + 3, 3).SequenceEqual(new byte[] { 0x9d, 1, 0x2a }))
            {
                int width = BinaryPrimitives.ReadUInt16LittleEndian(bytes.Slice(start + 6, 2)) & 0x3fff;
                int height = BinaryPrimitives.ReadUInt16LittleEndian(bytes.Slice(start + 8, 2)) & 0x3fff;
                return width > 0 && height > 0 ? new(width, height, 4) : null;
            }
            long next = (long)offset + 8 + length + (length & 1);
            if (next > bytes.Length) return null;
            offset = (int)next;
        }
        return null;
    }

    static uint U32Be(ReadOnlySpan<byte> bytes, int offset) => BinaryPrimitives.ReadUInt32BigEndian(bytes.Slice(offset, 4));
    static int U24Le(ReadOnlySpan<byte> bytes, int offset) => bytes[offset] | bytes[offset + 1] << 8 | bytes[offset + 2] << 16;
}
