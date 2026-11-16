namespace StarMap.Core.Recognition;

using System.Security.Cryptography;

/// <summary>
/// Little-endian structure compared strictly with the Python harness.
/// Magic is the eight bytes <c>XGRID1</c>, NUL, <c>0x01</c>.
/// Reason 1 is <c>fast-insufficient-edges</c> and hashes the fine points.
/// Reason 2 is the grid result and hashes the coarse points plus rows.
/// Scores are the float32 values promoted back to float64, matching
/// <c>float(np.float32)</c>.
/// </summary>
public static class SliceCanonical
{
    public static byte[] StructureBytes(GridSliceResult result)
    {
        var points = result.HashedPoints;
        var rows = result.Rows;
        int reason = result.Reason == GridSlice.InsufficientEdges ? 1 : 2;
        var buffer = new byte[24 + points.Length * 8 + rows.Length * 32];
        var span = buffer.AsSpan();
        span[0] = (byte)'X';
        span[1] = (byte)'G';
        span[2] = (byte)'R';
        span[3] = (byte)'I';
        span[4] = (byte)'D';
        span[5] = (byte)'1';
        span[6] = 0;
        span[7] = 1;
        System.Buffers.Binary.BinaryPrimitives.WriteInt32LittleEndian(span.Slice(8), reason);
        System.Buffers.Binary.BinaryPrimitives.WriteInt32LittleEndian(span.Slice(12), result.GatePointCount);
        System.Buffers.Binary.BinaryPrimitives.WriteInt32LittleEndian(span.Slice(16), points.Length);
        System.Buffers.Binary.BinaryPrimitives.WriteInt32LittleEndian(span.Slice(20), rows.Length);
        int offset = 24;
        foreach (var point in points)
        {
            System.Buffers.Binary.BinaryPrimitives.WriteSingleLittleEndian(span.Slice(offset), point.X);
            System.Buffers.Binary.BinaryPrimitives.WriteSingleLittleEndian(span.Slice(offset + 4), point.Y);
            offset += 8;
        }
        foreach (var row in rows)
        {
            WriteDouble(span, ref offset, row.Score);
            WriteDouble(span, ref offset, row.Scale);
            WriteDouble(span, ref offset, row.X);
            WriteDouble(span, ref offset, row.Y);
        }
        return buffer;
    }

    public static string StructureSha256(GridSliceResult result)
        => Sha256(StructureBytes(result));

    public static string PointsSha256(GridSliceResult result)
    {
        var buffer = new byte[result.HashedPoints.Length * 8];
        var span = buffer.AsSpan();
        int offset = 0;
        foreach (var point in result.HashedPoints)
        {
            System.Buffers.Binary.BinaryPrimitives.WriteSingleLittleEndian(span.Slice(offset), point.X);
            System.Buffers.Binary.BinaryPrimitives.WriteSingleLittleEndian(span.Slice(offset + 4), point.Y);
            offset += 8;
        }
        return Sha256(buffer);
    }

    private static void WriteDouble(Span<byte> span, ref int offset, double value)
    {
        System.Buffers.Binary.BinaryPrimitives.WriteInt64LittleEndian(
            span.Slice(offset), BitConverter.DoubleToInt64Bits(value));
        offset += 8;
    }

    private static string Sha256(byte[] buffer)
        => Convert.ToHexString(SHA256.HashData(buffer)).ToLowerInvariant();
}
