using System.IO.Compression;

namespace StarMap.Core.Resources;

public sealed record NpyReadLimits(
    long MaxArchiveBytes = 512L * 1024 * 1024,
    long MaxArrayBytes = 256L * 1024 * 1024,
    int MaxHeaderBytes = 1 * 1024 * 1024,
    int MaxRank = 4,
    int MaxDimension = 32_768);

/// <summary>Read-only, bounded NPZ access. Array payloads are decompressed only when requested.</summary>
public sealed class NpzArchive : IDisposable
{
    private readonly FileStream _file;
    private readonly ZipArchive _archive;
    private readonly IReadOnlyDictionary<string, ZipArchiveEntry> _members;
    private readonly NpyReadLimits _limits;
    private bool _disposed;

    private NpzArchive(FileStream file, ZipArchive archive, IReadOnlyDictionary<string, ZipArchiveEntry> members, NpyReadLimits limits)
    {
        _file = file;
        _archive = archive;
        _members = members;
        _limits = limits;
    }

    public IEnumerable<string> ArrayNames => _members.Keys;

    public static NpzArchive Open(string path, NpyReadLimits? limits = null)
    {
        ArgumentException.ThrowIfNullOrWhiteSpace(path);
        limits ??= new NpyReadLimits();
        if (limits.MaxArchiveBytes <= 0 || limits.MaxArrayBytes <= 0 || limits.MaxHeaderBytes <= 0
            || limits.MaxRank <= 0 || limits.MaxDimension <= 0)
        {
            throw new ArgumentOutOfRangeException(nameof(limits), "NPY limits must be positive.");
        }

        var file = new FileStream(path, FileMode.Open, FileAccess.Read, FileShare.Read);
        try
        {
            if (file.Length <= 0 || file.Length > limits.MaxArchiveBytes)
            {
                throw new InvalidDataException("NPZ archive exceeds its file-size limit.");
            }

            var archive = new ZipArchive(file, ZipArchiveMode.Read, leaveOpen: true);
            var members = new Dictionary<string, ZipArchiveEntry>(StringComparer.Ordinal);
            foreach (var entry in archive.Entries)
            {
                if (entry.FullName.EndsWith("/", StringComparison.Ordinal)
                    || entry.FullName.Contains('/') || entry.FullName.Contains('\\'))
                {
                    throw new InvalidDataException("NPZ entries must be flat NPY members.");
                }

                if (!entry.FullName.EndsWith(".npy", StringComparison.Ordinal))
                {
                    throw new InvalidDataException($"Unsupported NPZ member '{entry.FullName}'.");
                }

                var name = entry.FullName[..^4];
                if (string.IsNullOrWhiteSpace(name) || name is "." or ".."
                    || entry.Length <= 0 || entry.Length > limits.MaxArrayBytes + limits.MaxHeaderBytes + 16L)
                {
                    throw new InvalidDataException($"NPZ member '{entry.FullName}' exceeds its size or name limit.");
                }

                if (!members.TryAdd(name, entry))
                {
                    throw new InvalidDataException($"Duplicate NPZ member '{entry.FullName}'.");
                }
            }

            return new NpzArchive(file, archive, members, limits);
        }
        catch
        {
            file.Dispose();
            throw;
        }
    }

    public NpyArray ReadArray(string name)
    {
        ObjectDisposedException.ThrowIf(_disposed, this);
        ArgumentException.ThrowIfNullOrWhiteSpace(name);
        if (!_members.TryGetValue(name, out var entry))
        {
            throw new KeyNotFoundException($"NPZ array '{name}' is missing.");
        }

        using var stream = entry.Open();
        return NpyArray.Read(stream, _limits.MaxArrayBytes, _limits.MaxHeaderBytes, _limits.MaxRank, _limits.MaxDimension);
    }

    public void Dispose()
    {
        if (_disposed)
        {
            return;
        }

        _disposed = true;
        _archive.Dispose();
        _file.Dispose();
    }
}
