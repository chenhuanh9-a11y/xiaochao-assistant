using System.Text;
using System.Text.Encodings.Web;
using System.Text.Json;
using System.Text.Json.Nodes;

namespace StarMap.Core.Settings;

internal static class JsonDocumentFile
{
    private static readonly UTF8Encoding StrictUtf8 = new(false, true);
    private static readonly JsonSerializerOptions WriteOptions = new()
    {
        WriteIndented = true,
        Encoder = JavaScriptEncoder.UnsafeRelaxedJsonEscaping
    };

    public static JsonObject ParseObject(byte[] bytes)
    {
        var text = StrictUtf8.GetString(bytes);
        if (text.Length > 0 && text[0] == '\uFEFF')
            text = text[1..];

        var node = JsonNode.Parse(text, documentOptions: new JsonDocumentOptions
        {
            AllowTrailingCommas = false,
            CommentHandling = JsonCommentHandling.Disallow,
            MaxDepth = 128
        });
        return node as JsonObject ?? throw new JsonException("The settings file must contain a JSON object.");
    }

    public static byte[] Serialize(JsonObject value)
        => JsonSerializer.SerializeToUtf8Bytes(value, WriteOptions);

    public static async Task<byte[]?> ReadOptionalAsync(string path, CancellationToken cancellationToken)
    {
        EnsureNotReparsePoint(path);
        if (!File.Exists(path))
            return null;
        return await File.ReadAllBytesAsync(path, cancellationToken).ConfigureAwait(false);
    }

    public static async Task<byte[]> WriteAtomicallyAsync(
        ExperimentalDataRoot root,
        string path,
        byte[] payload,
        byte[]? expectedCurrent,
        CancellationToken cancellationToken)
    {
        EnsureContained(root, path);
        Directory.CreateDirectory(root.Path);
        EnsureNotReparsePoint(root.Path);
        EnsureNotReparsePoint(path);

        var current = await ReadOptionalAsync(path, cancellationToken).ConfigureAwait(false);
        if (!BytesEqual(current, expectedCurrent))
            throw new IOException("The file changed since it was loaded. Reload it before saving.");

        var temporary = Path.Combine(root.Path, $".{Path.GetFileName(path)}.{Guid.NewGuid():N}.tmp");
        var temporaryCreated = false;
        try
        {
            await using (var stream = new FileStream(temporary, FileMode.CreateNew, FileAccess.Write,
                             FileShare.None, 4096, FileOptions.Asynchronous | FileOptions.WriteThrough))
            {
                temporaryCreated = true;
                await stream.WriteAsync(payload, cancellationToken).ConfigureAwait(false);
                await stream.FlushAsync(cancellationToken).ConfigureAwait(false);
                stream.Flush(flushToDisk: true);
            }

            EnsureNotReparsePoint(temporary);
            EnsureNotReparsePoint(path);
            current = await ReadOptionalAsync(path, cancellationToken).ConfigureAwait(false);
            if (!BytesEqual(current, expectedCurrent))
                throw new IOException("The file changed while it was being saved. Reload it before saving.");

            if (expectedCurrent is null)
                File.Move(temporary, path);
            else
                File.Move(temporary, path, overwrite: true);
            temporaryCreated = false;

            EnsureNotReparsePoint(path);
            var readBack = await File.ReadAllBytesAsync(path, cancellationToken).ConfigureAwait(false);
            if (!readBack.AsSpan().SequenceEqual(payload))
                throw new IOException("The saved file did not match the bytes written.");
            return readBack;
        }
        finally
        {
            if (temporaryCreated && File.Exists(temporary))
            {
                EnsureNotReparsePoint(temporary);
                File.Delete(temporary);
            }
        }
    }

    public static void EnsureContained(ExperimentalDataRoot root, string path)
    {
        var fullPath = Path.GetFullPath(path);
        var parent = Path.TrimEndingDirectorySeparator(Path.GetDirectoryName(fullPath) ?? string.Empty);
        var comparison = OperatingSystem.IsWindows() ? StringComparison.OrdinalIgnoreCase : StringComparison.Ordinal;
        if (!string.Equals(parent, Path.TrimEndingDirectorySeparator(root.Path), comparison))
            throw new IOException("The data file must be directly inside the explicit experimental root.");
    }

    public static void EnsureNotReparsePoint(string path)
    {
        try
        {
            if ((File.GetAttributes(path) & FileAttributes.ReparsePoint) != 0)
                throw new IOException($"Refusing to access a linked data path: {Path.GetFileName(path)}");
        }
        catch (FileNotFoundException)
        {
        }
        catch (DirectoryNotFoundException)
        {
        }
    }

    public static bool BytesEqual(byte[]? left, byte[]? right)
        => left is null ? right is null : right is not null && left.AsSpan().SequenceEqual(right);
}
