using System.Text;
using System.Text.Json;
using System.Text.Json.Nodes;

namespace StarMap.Core.Settings;

/// <summary>
/// Read-only view of the existing custom-galleries.json format. Import copies
/// a selected file into the isolated experiment directory; route editing and
/// persistence remain a separate increment.
/// </summary>
public sealed class CustomGalleriesDocument
{
    public const string FileName = "custom-galleries.json";
    public const string Schema = "xingtu-custom-gallery-v1";
    public const int MaxPointsPerMap = 128;

    private static readonly string[] SupportedFloors = ["B1", "1", "2"];
    private readonly JsonObject _values;

    private CustomGalleriesDocument(JsonObject values, bool hasFile)
    {
        _values = (JsonObject)values.DeepClone();
        HasFile = hasFile;
    }

    public bool HasFile { get; }
    public JsonObject Values => (JsonObject)_values.DeepClone();

    public IReadOnlyList<CustomGallerySummary> Galleries
    {
        get
        {
            var list = (JsonArray)_values["galleries"]!;
            return list.Select(ReadGallerySummary).ToArray();
        }
    }

    public static async Task<DocumentLoadResult<CustomGalleriesDocument>> LoadAsync(
        ExperimentalDataRoot root,
        CancellationToken cancellationToken = default)
    {
        ArgumentNullException.ThrowIfNull(root);
        try
        {
            JsonDocumentFile.EnsureNotReparsePoint(root.Path);
            var bytes = await JsonDocumentFile.ReadOptionalAsync(root.CustomGalleriesPath, cancellationToken).ConfigureAwait(false);
            var value = bytes is null ? CreateEmpty() : JsonDocumentFile.ParseObject(bytes);
            ValidateSchema(value);
            return DocumentLoadResult<CustomGalleriesDocument>.Success(new CustomGalleriesDocument(value, bytes is not null));
        }
        catch (Exception error) when (IsDataError(error))
        {
            return DocumentLoadResult<CustomGalleriesDocument>.Failure(error);
        }
    }

    /// <summary>Copies a selected gallery file into an empty experiment root; never edits the source.</summary>
    public static async Task<DocumentLoadResult<CustomGalleriesDocument>> ImportAsync(
        ExperimentalDataRoot root,
        string sourcePath,
        CancellationToken cancellationToken = default)
    {
        ArgumentNullException.ThrowIfNull(root);
        ArgumentException.ThrowIfNullOrWhiteSpace(sourcePath);
        string? temporary = null;
        try
        {
            var sourceBytes = await File.ReadAllBytesAsync(sourcePath, cancellationToken).ConfigureAwait(false);
            var parsed = JsonDocumentFile.ParseObject(sourceBytes);
            ValidateSchema(parsed);
            Directory.CreateDirectory(root.Path);
            JsonDocumentFile.EnsureNotReparsePoint(root.Path);
            JsonDocumentFile.EnsureNotReparsePoint(root.CustomGalleriesPath);
            if (File.Exists(root.CustomGalleriesPath))
                throw new IOException("The experimental gallery copy already exists. Load it or choose another experiment root.");

            temporary = Path.Combine(root.Path, $".{FileName}.{Guid.NewGuid():N}.import.tmp");
            await using (var stream = new FileStream(temporary, FileMode.CreateNew, FileAccess.Write,
                             FileShare.None, 4096, FileOptions.Asynchronous | FileOptions.WriteThrough))
            {
                await stream.WriteAsync(sourceBytes, cancellationToken).ConfigureAwait(false);
                await stream.FlushAsync(cancellationToken).ConfigureAwait(false);
                stream.Flush(flushToDisk: true);
            }

            var stagedBytes = await File.ReadAllBytesAsync(temporary, cancellationToken).ConfigureAwait(false);
            if (!stagedBytes.AsSpan().SequenceEqual(sourceBytes))
                throw new IOException("The staged gallery copy does not match the selected source.");
            ValidateSchema(JsonDocumentFile.ParseObject(stagedBytes));

            JsonDocumentFile.EnsureNotReparsePoint(root.CustomGalleriesPath);
            File.Move(temporary, root.CustomGalleriesPath);
            temporary = null;
            JsonDocumentFile.EnsureNotReparsePoint(root.CustomGalleriesPath);
            var copiedBytes = await File.ReadAllBytesAsync(root.CustomGalleriesPath, cancellationToken).ConfigureAwait(false);
            if (!copiedBytes.AsSpan().SequenceEqual(sourceBytes))
                throw new IOException("The copied gallery file did not match the selected source.");

            var copiedValues = JsonDocumentFile.ParseObject(copiedBytes);
            ValidateSchema(copiedValues);
            return DocumentLoadResult<CustomGalleriesDocument>.Success(new CustomGalleriesDocument(copiedValues, hasFile: true));
        }
        catch (Exception error) when (IsDataError(error))
        {
            return DocumentLoadResult<CustomGalleriesDocument>.Failure(error);
        }
        finally
        {
            if (temporary is not null && File.Exists(temporary))
            {
                JsonDocumentFile.EnsureNotReparsePoint(temporary);
                File.Delete(temporary);
            }
        }
    }

    public IReadOnlyList<CustomRoutePoint> ReadRoute(string galleryId, string mapId)
    {
        ArgumentException.ThrowIfNullOrWhiteSpace(galleryId);
        ArgumentException.ThrowIfNullOrWhiteSpace(mapId);
        var gallery = ((JsonArray)_values["galleries"]!).OfType<JsonObject>()
            .FirstOrDefault(value => TryReadString(value["id"], out var id) && id == galleryId);
        if (gallery is null)
            throw new KeyNotFoundException($"Custom gallery '{galleryId}' was not found.");
        if (gallery["maps"] is not JsonObject maps || maps[mapId] is not JsonObject route)
            return Array.Empty<CustomRoutePoint>();
        return ReadRoutePoints(galleryId, mapId, route);
    }

    private static IReadOnlyList<CustomRoutePoint> ReadRoutePoints(string galleryId, string mapId, JsonObject route)
    {
        if (route["points"] is not JsonArray points)
            throw new JsonException($"Route '{galleryId}/{mapId}' does not contain a points array.");
        if (points.Count > MaxPointsPerMap)
            throw new JsonException($"Route '{galleryId}/{mapId}' exceeds {MaxPointsPerMap} points.");

        var result = new List<CustomRoutePoint>(points.Count);
        foreach (var pointNode in points)
        {
            if (pointNode is not JsonObject point
                || !TryReadString(point["floor"], out var floor) || !SupportedFloors.Contains(floor, StringComparer.Ordinal)
                || !TryReadFiniteDouble(point["x"], out var x)
                || !TryReadFiniteDouble(point["y"], out var y))
                throw new JsonException($"Route '{galleryId}/{mapId}' contains an invalid point.");

            var kind = TryReadString(point["kind"], out var rawKind) && rawKind == "room" ? "room" : "via";
            var room = kind == "room" && TryReadString(point["room"], out var rawRoom) && rawRoom.Length > 0
                ? rawRoom
                : null;
            var color = TryReadString(point["color"], out var rawColor) && IsColor(rawColor)
                ? rawColor.ToUpperInvariant()
                : "#63CAFF";
            var isStart = point["start"] is JsonValue start && TryReadBoolean(start, out var startValue) && startValue;
            var extensions = (JsonObject)point.DeepClone();
            foreach (var key in new[] { "floor", "x", "y", "kind", "room", "color", "start" })
                extensions.Remove(key);
            result.Add(new CustomRoutePoint(floor, x, y, kind, room, color, isStart, extensions));
        }
        return result;
    }

    private static CustomGallerySummary ReadGallerySummary(JsonNode? node)
    {
        if (node is not JsonObject gallery || !TryReadString(gallery["id"], out var id))
            throw new JsonException("A custom gallery is missing its ID.");
        var name = TryReadString(gallery["name"], out var storedName) && storedName.Length > 0
            ? storedName[..Math.Min(storedName.Length, 24)]
            : "未命名图库";
        if (gallery["maps"] is not JsonObject maps)
            throw new JsonException($"Custom gallery '{id}' does not contain a maps object.");
        var mapIds = new List<string>();
        foreach (var pair in maps)
        {
            if (pair.Value is not JsonObject route || route["points"] is not JsonArray)
                throw new JsonException($"Custom gallery '{id}' contains an invalid route for map '{pair.Key}'.");
            mapIds.Add(pair.Key);
        }
        return new CustomGallerySummary(id, name, gallery.ContainsKey("source"), mapIds);
    }

    private static void ValidateSchema(JsonObject value)
    {
        if (!TryReadString(value["schema"], out var schema) || schema != Schema)
            throw new JsonException($"Unsupported custom gallery schema; expected '{Schema}'.");
        if (value["galleries"] is not JsonArray galleries)
            throw new JsonException("A custom gallery document must contain a galleries array.");
        var identifiers = new HashSet<string>(StringComparer.Ordinal);
        foreach (var node in galleries)
        {
            if (node is not JsonObject gallery || !TryReadString(gallery["id"], out var id) || string.IsNullOrWhiteSpace(id)
                || !identifiers.Add(id))
                throw new JsonException("Every custom gallery must have a unique, nonempty ID.");
            if (gallery["maps"] is not JsonObject maps)
                throw new JsonException($"Custom gallery '{id}' does not contain a maps object.");
            foreach (var (mapId, route) in maps)
            {
                if (string.IsNullOrWhiteSpace(mapId) || route is not JsonObject routeObject)
                    throw new JsonException($"Custom gallery '{id}' contains an invalid route for map '{mapId}'.");
                _ = ReadRoutePoints(id, mapId, routeObject);
            }
        }
    }

    private static JsonObject CreateEmpty() => new()
    {
        ["schema"] = Schema,
        ["galleries"] = new JsonArray(),
        ["legacyRouteMigrations"] = new JsonArray()
    };

    private static bool TryReadString(JsonNode? value, out string text)
    {
        try
        {
            if (value is JsonValue scalar)
            {
                text = scalar.GetValue<string>();
                return true;
            }
        }
        catch (InvalidOperationException)
        {
        }
        text = string.Empty;
        return false;
    }

    private static bool TryReadFiniteDouble(JsonNode? value, out double number)
    {
        try
        {
            if (value is JsonValue scalar)
            {
                number = scalar.GetValue<double>();
                return double.IsFinite(number);
            }
        }
        catch (InvalidOperationException)
        {
        }
        catch (FormatException)
        {
        }
        number = 0;
        return false;
    }

    private static bool TryReadBoolean(JsonNode value, out bool result)
    {
        try
        {
            if (value is JsonValue scalar)
            {
                result = scalar.GetValue<bool>();
                return true;
            }
        }
        catch (InvalidOperationException)
        {
        }
        result = false;
        return false;
    }

    private static bool IsColor(string value)
        => value.Length == 7 && value[0] == '#'
           && value.Skip(1).All(character => Uri.IsHexDigit(character));

    private static bool IsDataError(Exception error)
        => error is IOException or UnauthorizedAccessException or JsonException or DecoderFallbackException
            or ArgumentException or InvalidOperationException or NotSupportedException or FormatException;
}

public sealed record CustomGallerySummary(string Id, string Name, bool ReadOnly, IReadOnlyList<string> MapIds);

public sealed record CustomRoutePoint(
    string Floor,
    double X,
    double Y,
    string Kind,
    string? Room,
    string Color,
    bool IsStart,
    JsonObject ExtensionFields);
