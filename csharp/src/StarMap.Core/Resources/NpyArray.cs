using System.Buffers.Binary;
using System.Collections.ObjectModel;
using System.Globalization;
using System.Text;
using System.Text.RegularExpressions;

namespace StarMap.Core.Resources;

/// <summary>A bounded, read-only view of one NPY member's uncompressed bytes.</summary>
public sealed class NpyArray
{
    private static readonly Regex DescriptorPattern = new("^(?<order>[<>=|])(?<kind>[biufU])(?<size>[0-9]+)$", RegexOptions.CultureInvariant);
    private static readonly Regex ShapePattern = new("['\"]shape['\"]\\s*:\\s*\\((?<shape>[^)]*)\\)", RegexOptions.CultureInvariant);
    private static readonly Regex DescriptorHeaderPattern = new("['\"]descr['\"]\\s*:\\s*['\"](?<descr>[^'\"]+)['\"]", RegexOptions.CultureInvariant);
    private static readonly Regex FortranPattern = new("['\"]fortran_order['\"]\\s*:\\s*(?<value>True|False)", RegexOptions.CultureInvariant);

    private readonly int[] _shape;
    private readonly ReadOnlyCollection<int> _readOnlyShape;
    private readonly byte[] _data;

    internal NpyArray(string descriptor, int[] shape, byte[] data)
    {
        Dtype = descriptor;
        _shape = shape;
        _readOnlyShape = Array.AsReadOnly(_shape);
        _data = data;
    }

    public string Dtype { get; }
    public IReadOnlyList<int> Shape => _readOnlyShape;
    public ReadOnlyMemory<byte> Bytes => _data;
    public int Rank => _shape.Length;

    /// <summary>Reads only scalar, little-endian Unicode fields used for NPZ metadata.</summary>
    public string ReadUnicodeScalar()
    {
        var descriptor = ParseDescriptor(Dtype);
        if (Rank != 0 || descriptor.Kind != 'U' || _data.Length % 4 != 0)
        {
            throw new InvalidDataException("Expected a scalar NPY Unicode value.");
        }

        if (descriptor.Order == '>' && BitConverter.IsLittleEndian)
        {
            throw new InvalidDataException("Big-endian NPY Unicode metadata is unsupported.");
        }

        var value = Encoding.UTF32.GetString(_data);
        return value.TrimEnd('\0');
    }

    public double ReadNumericValue(int elementIndex)
    {
        var type = ParseDescriptor(Dtype);
        if (type.Kind is not ('b' or 'i' or 'u' or 'f'))
        {
            throw new InvalidDataException($"NPY dtype '{Dtype}' is not numeric.");
        }

        var elementCount = 1L;
        foreach (var dimension in _shape)
        {
            elementCount = checked(elementCount * dimension);
        }

        if (elementIndex < 0 || elementIndex >= elementCount)
        {
            throw new ArgumentOutOfRangeException(nameof(elementIndex));
        }

        var offset = checked(elementIndex * type.Size);
        var bytes = _data.AsSpan(offset, type.Size);
        var littleEndian = type.Order == '<' || type.Order == '|' || type.Order == '=' && BitConverter.IsLittleEndian;
        return type.Kind switch
        {
            'b' => bytes[0] == 0 ? 0d : 1d,
            'u' => type.Size switch
            {
                1 => bytes[0],
                2 => littleEndian ? BinaryPrimitives.ReadUInt16LittleEndian(bytes) : BinaryPrimitives.ReadUInt16BigEndian(bytes),
                4 => littleEndian ? BinaryPrimitives.ReadUInt32LittleEndian(bytes) : BinaryPrimitives.ReadUInt32BigEndian(bytes),
                8 => littleEndian ? BinaryPrimitives.ReadUInt64LittleEndian(bytes) : BinaryPrimitives.ReadUInt64BigEndian(bytes),
                _ => throw new InvalidDataException($"Unsupported NPY dtype '{Dtype}'.")
            },
            'i' => type.Size switch
            {
                1 => unchecked((sbyte)bytes[0]),
                2 => littleEndian ? BinaryPrimitives.ReadInt16LittleEndian(bytes) : BinaryPrimitives.ReadInt16BigEndian(bytes),
                4 => littleEndian ? BinaryPrimitives.ReadInt32LittleEndian(bytes) : BinaryPrimitives.ReadInt32BigEndian(bytes),
                8 => littleEndian ? BinaryPrimitives.ReadInt64LittleEndian(bytes) : BinaryPrimitives.ReadInt64BigEndian(bytes),
                _ => throw new InvalidDataException($"Unsupported NPY dtype '{Dtype}'.")
            },
            'f' when type.Size == 4 => BitConverter.Int32BitsToSingle(littleEndian
                ? BinaryPrimitives.ReadInt32LittleEndian(bytes)
                : BinaryPrimitives.ReadInt32BigEndian(bytes)),
            'f' when type.Size == 8 => BitConverter.Int64BitsToDouble(littleEndian
                ? BinaryPrimitives.ReadInt64LittleEndian(bytes)
                : BinaryPrimitives.ReadInt64BigEndian(bytes)),
            _ => throw new InvalidDataException($"Unsupported NPY dtype '{Dtype}'.")
        };
    }

    internal static NpyArray Read(Stream stream, long maxArrayBytes, int maxHeaderBytes, int maxRank, int maxDimension)
    {
        Span<byte> magic = stackalloc byte[6];
        stream.ReadExactly(magic);
        ReadOnlySpan<byte> expectedMagic = [0x93, (byte)'N', (byte)'U', (byte)'M', (byte)'P', (byte)'Y'];
        if (!magic.SequenceEqual(expectedMagic))
        {
            throw new InvalidDataException("Invalid NPY magic.");
        }

        Span<byte> version = stackalloc byte[2];
        stream.ReadExactly(version);
        var major = version[0];
        var headerLength = major switch
        {
            1 => ReadUInt16(stream),
            2 or 3 => ReadUInt32(stream),
            _ => throw new InvalidDataException($"Unsupported NPY version {major}.{version[1]}.")
        };
        if (headerLength <= 0 || headerLength > maxHeaderBytes)
        {
            throw new InvalidDataException("NPY header exceeds its size limit.");
        }

        var headerBytes = new byte[checked((int)headerLength)];
        stream.ReadExactly(headerBytes);
        var header = major == 3 ? Encoding.UTF8.GetString(headerBytes) : Encoding.ASCII.GetString(headerBytes);
        var descriptorMatch = DescriptorHeaderPattern.Match(header);
        var fortranMatch = FortranPattern.Match(header);
        var shapeMatch = ShapePattern.Match(header);
        if (!descriptorMatch.Success || !fortranMatch.Success || !shapeMatch.Success)
        {
            throw new InvalidDataException("Unsupported or malformed NPY header.");
        }

        if (!string.Equals(fortranMatch.Groups["value"].Value, "False", StringComparison.Ordinal))
        {
            throw new InvalidDataException("Fortran-order NPY arrays are unsupported.");
        }

        var descriptor = descriptorMatch.Groups["descr"].Value;
        var type = ParseDescriptor(descriptor);
        var dimensions = ParseShape(shapeMatch.Groups["shape"].Value, maxRank, maxDimension);
        var elementSize = type.Kind == 'U' ? checked(type.Size * 4) : type.Size;
        var elementCount = 1L;
        foreach (var dimension in dimensions)
        {
            elementCount = checked(elementCount * dimension);
        }

        var dataLength = checked(elementCount * elementSize);
        if (dataLength < 0 || dataLength > maxArrayBytes || dataLength > int.MaxValue)
        {
            throw new InvalidDataException("NPY array exceeds its byte limit.");
        }

        var data = new byte[(int)dataLength];
        stream.ReadExactly(data);
        if (stream.ReadByte() != -1)
        {
            throw new InvalidDataException("NPY payload has trailing bytes.");
        }

        return new NpyArray(descriptor, dimensions, data);
    }

    internal static (char Order, char Kind, int Size) ParseDescriptor(string descriptor)
    {
        var match = DescriptorPattern.Match(descriptor);
        if (!match.Success)
        {
            throw new InvalidDataException($"Unsupported NPY dtype '{descriptor}'.");
        }

        var kind = match.Groups["kind"].Value[0];
        var order = match.Groups["order"].Value[0];
        var size = int.Parse(match.Groups["size"].Value, CultureInfo.InvariantCulture);
        var validSize = kind switch
        {
            'b' => size == 1,
            'i' or 'u' => size is 1 or 2 or 4 or 8,
            'f' => size is 4 or 8,
            'U' => size is > 0 and <= 262_144,
            _ => false
        };
        if (!validSize || order == '|' && size != 1)
        {
            throw new InvalidDataException($"Unsupported NPY dtype '{descriptor}'.");
        }

        return (order, kind, size);
    }

    private static int[] ParseShape(string text, int maxRank, int maxDimension)
    {
        if (string.IsNullOrWhiteSpace(text))
        {
            return [];
        }

        var pieces = text.Split(',', StringSplitOptions.TrimEntries | StringSplitOptions.RemoveEmptyEntries);
        if (pieces.Length > maxRank)
        {
            throw new InvalidDataException("NPY rank exceeds its limit.");
        }

        var shape = new int[pieces.Length];
        for (var index = 0; index < pieces.Length; index++)
        {
            if (!int.TryParse(pieces[index], NumberStyles.None, CultureInfo.InvariantCulture, out var dimension)
                || dimension < 0 || dimension > maxDimension)
            {
                throw new InvalidDataException("NPY shape contains an invalid dimension.");
            }

            shape[index] = dimension;
        }

        return shape;
    }

    private static ushort ReadUInt16(Stream stream)
    {
        Span<byte> bytes = stackalloc byte[2];
        stream.ReadExactly(bytes);
        return BinaryPrimitives.ReadUInt16LittleEndian(bytes);
    }

    private static uint ReadUInt32(Stream stream)
    {
        Span<byte> bytes = stackalloc byte[4];
        stream.ReadExactly(bytes);
        return BinaryPrimitives.ReadUInt32LittleEndian(bytes);
    }
}
