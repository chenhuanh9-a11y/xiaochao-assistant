using System.Text.Json;
using System.Text.Json.Nodes;

namespace StarMap.Core.Resources;

/// <summary>One exact typed structure resource loaded from an indexed NPZ.</summary>
public sealed class SharedStructureData
{
    internal SharedStructureData(
        JsonElement metadata,
        NpyArray? cleanMask,
        NpyArray validationMask,
        NpyArray? knownBoundary,
        NpyArray? unknownMask,
        NpyArray? nativeWalls)
    {
        Metadata = metadata;
        CleanMask = cleanMask;
        ValidationMask = validationMask;
        KnownBoundary = knownBoundary;
        UnknownMask = unknownMask;
        NativeWalls = nativeWalls;
    }

    public JsonElement Metadata { get; }
    public NpyArray? CleanMask { get; }
    public NpyArray ValidationMask { get; }
    public NpyArray? KnownBoundary { get; }
    public NpyArray? UnknownMask { get; }
    public NpyArray? NativeWalls { get; }
}

public sealed partial class ResourceCatalog
{
    /// <summary>
    /// Loads the indexed NPZ for this exact entry. Only metadata and the arrays named by its roles
    /// (or the explicit legacy-clean schema fields) are decompressed.
    /// </summary>
    public ResourceLoadResult<SharedStructureData> LoadSharedStructure(ResourceCatalogEntry entry)
    {
        ArgumentNullException.ThrowIfNull(entry);
        if (!ReferenceEquals(entry.Owner, this))
        {
            return ResourceLoadResult<SharedStructureData>.Failed(
                "catalog-entry-owner-mismatch", "Entry belongs to a different ResourceCatalog.", entry.StableKey);
        }

        if (entry.StructureStatus != ResourceBindingStatus.Ready || entry.StructureNpzPath is null)
        {
            var issue = entry.StructureIssue;
            return ResourceLoadResult<SharedStructureData>.Failed(
                issue?.Code ?? "typed-structure-unavailable",
                issue?.Message ?? "No exact typed structure resource is available for this entry.",
                entry.StableKey);
        }

        try
        {
            using var archive = NpzArchive.Open(entry.StructureNpzPath);
            return entry.StructureKind switch
            {
                "shared-structure" => LoadIndexedSharedStructure(archive, entry),
                "legacy-clean" => LoadLegacyStructure(archive, entry),
                _ => ResourceLoadResult<SharedStructureData>.Failed(
                    "typed-structure-kind-unsupported", $"Unsupported indexed structure kind '{entry.StructureKind}'.", entry.StableKey)
            };
        }
        catch (Exception error) when (error is IOException or UnauthorizedAccessException or InvalidDataException
            or JsonException or KeyNotFoundException or ArgumentException or OverflowException)
        {
            return ResourceLoadResult<SharedStructureData>.Failed(
                "typed-structure-read-failed", error.Message, entry.StableKey);
        }
    }

    private static ResourceLoadResult<SharedStructureData> LoadIndexedSharedStructure(NpzArchive archive, ResourceCatalogEntry entry)
    {
        if (!archive.ArrayNames.Contains("metadata", StringComparer.Ordinal))
        {
            return ResourceLoadResult<SharedStructureData>.Failed(
                "shared-structure-metadata-missing", "Shared structure NPZ has no metadata member.", entry.StableKey);
        }

        using var metadataDocument = JsonDocument.Parse(archive.ReadArray("metadata").ReadUnicodeScalar(), new JsonDocumentOptions { MaxDepth = 64 });
        var metadata = metadataDocument.RootElement;
        if (metadata.ValueKind != JsonValueKind.Object)
        {
            return ResourceLoadResult<SharedStructureData>.Failed(
                "shared-structure-metadata-invalid", "Shared structure NPZ metadata must be a JSON object.", entry.StableKey);
        }

        if (!IsSupportedSharedSource(StringProperty(metadata, "sourceKind")))
        {
            return ResourceLoadResult<SharedStructureData>.Failed(
                "shared-structure-source-unsupported", "Shared structure sourceKind is unsupported.", entry.StableKey);
        }

        if (!BindingMatches(metadata, entry))
        {
            return ResourceLoadResult<SharedStructureData>.Failed(
                "shared-structure-binding-mismatch", "NPZ metadata binding does not exactly match profile/map/floor/variant.", entry.StableKey);
        }

        if (!VersionMetadataMatches(metadata, entry))
        {
            return ResourceLoadResult<SharedStructureData>.Failed(
                "shared-structure-version-mismatch", "NPZ metadata version does not match the selected index row.", entry.StableKey);
        }

        if (!CanvasMetadataMatches(metadata, entry))
        {
            return ResourceLoadResult<SharedStructureData>.Failed(
                "shared-structure-canvas-mismatch", "NPZ canvas does not exactly match the indexed structure-display canvas.", entry.StableKey);
        }

        if (!TryGetObject(metadata, "roles", out var roles))
        {
            return ResourceLoadResult<SharedStructureData>.Failed(
                "shared-structure-roles-missing", "Shared structure metadata has no roles object.", entry.StableKey);
        }

        var cleanName = StringProperty(roles, "clean");
        var validationName = StringProperty(roles, "validation");
        if (cleanName is null || validationName is null)
        {
            // Compact products are accepted only when the metadata itself explicitly binds both roles.
            cleanName = StringProperty(roles, "clean");
            validationName = StringProperty(roles, "validation");
        }

        if (cleanName is null || validationName is null)
        {
            return ResourceLoadResult<SharedStructureData>.Failed(
                "shared-structure-mask-roles-missing", "Shared structure metadata must bind clean and validation arrays.", entry.StableKey);
        }

        var clean = ReadMask(archive, cleanName, entry);
        if (!clean.Success)
        {
            return ResourceLoadResult<SharedStructureData>.Failed(clean.Error!.Code, clean.Error.Message, entry.StableKey);
        }

        var validation = string.Equals(cleanName, validationName, StringComparison.Ordinal)
            ? clean
            : ReadMask(archive, validationName, entry);
        if (!validation.Success)
        {
            return ResourceLoadResult<SharedStructureData>.Failed(validation.Error!.Code, validation.Error.Message, entry.StableKey);
        }

        if (!ShapesEqual(clean.Value!, validation.Value!) || !ShapeMatchesDeclaration(clean.Value!, entry, metadata))
        {
            return ResourceLoadResult<SharedStructureData>.Failed(
                "shared-structure-mask-shape-mismatch", "Clean/validation masks do not match each other and the declared canvas dimensions.", entry.StableKey);
        }

        var knownName = StringProperty(roles, "known");
        var unknownName = StringProperty(roles, "unknown");
        if ((knownName is null) != (unknownName is null))
        {
            return ResourceLoadResult<SharedStructureData>.Failed(
                "shared-structure-known-unknown-mismatch", "Known and unknown mask roles must both be present or both be absent.", entry.StableKey);
        }

        var knownResult = knownName is null ? null : ReadMask(archive, knownName, entry);
        if (knownResult is { Success: false })
        {
            return ResourceLoadResult<SharedStructureData>.Failed(knownResult.Error!.Code, knownResult.Error.Message, entry.StableKey);
        }

        var unknownResult = unknownName is null ? null : ReadMask(archive, unknownName, entry);
        if (unknownResult is { Success: false })
        {
            return ResourceLoadResult<SharedStructureData>.Failed(unknownResult.Error!.Code, unknownResult.Error.Message, entry.StableKey);
        }

        if (knownResult is not null && (!ShapesEqual(clean.Value!, knownResult.Value!) || !ShapesEqual(clean.Value!, unknownResult!.Value!)))
        {
            return ResourceLoadResult<SharedStructureData>.Failed(
                "shared-structure-optional-mask-shape-mismatch", "Known/unknown masks do not match the clean mask dimensions.", entry.StableKey);
        }

        var wallsName = StringProperty(roles, "nativeWalls");
        NpyArray? nativeWalls = null;
        if (wallsName is not null)
        {
            nativeWalls = archive.ReadArray(wallsName);
            if (!NativeWallSegmentsValid(nativeWalls, clean.Value!))
            {
                return ResourceLoadResult<SharedStructureData>.Failed(
                    "shared-structure-walls-invalid", "Native wall rows must be finite N×6 numeric segments within the declared canvas.", entry.StableKey);
            }
        }

        var result = new SharedStructureData(
            metadata.Clone(), clean.Value!, validation.Value!, knownResult?.Value, unknownResult?.Value, nativeWalls);
        return ResourceLoadResult<SharedStructureData>.Loaded(result);
    }

    private static ResourceLoadResult<SharedStructureData> LoadLegacyStructure(NpzArchive archive, ResourceCatalogEntry entry)
    {
        var validation = ReadMask(archive, "validation_mask", entry);
        if (!validation.Success)
        {
            return ResourceLoadResult<SharedStructureData>.Failed(validation.Error!.Code, validation.Error.Message, entry.StableKey);
        }

        NpyArray? clean = null;
        if (archive.ArrayNames.Contains("clean_mask", StringComparer.Ordinal))
        {
            var cleanResult = ReadMask(archive, "clean_mask", entry);
            if (!cleanResult.Success)
            {
                return ResourceLoadResult<SharedStructureData>.Failed(cleanResult.Error!.Code, cleanResult.Error.Message, entry.StableKey);
            }

            clean = cleanResult.Value;
            if (!ShapesEqual(clean!, validation.Value!))
            {
                return ResourceLoadResult<SharedStructureData>.Failed(
                    "legacy-structure-mask-shape-mismatch", "Legacy clean and validation masks have different shapes.", entry.StableKey);
            }
        }

        if (!ShapeMatchesDeclaration(validation.Value!, entry, metadata: default))
        {
            return ResourceLoadResult<SharedStructureData>.Failed(
                "legacy-structure-canvas-shape-mismatch", "Legacy validation mask does not match its exact index canvas declaration.", entry.StableKey);
        }

        NpyArray? known = null;
        NpyArray? unknown = null;
        var hasKnown = archive.ArrayNames.Contains("known_boundary", StringComparer.Ordinal);
        var hasUnknown = archive.ArrayNames.Contains("unknown_mask", StringComparer.Ordinal);
        if (hasKnown != hasUnknown)
        {
            return ResourceLoadResult<SharedStructureData>.Failed(
                "legacy-structure-known-unknown-mismatch", "Legacy known_boundary and unknown_mask must both be present or absent.", entry.StableKey);
        }

        if (hasKnown)
        {
            var knownResult = ReadMask(archive, "known_boundary", entry);
            var unknownResult = ReadMask(archive, "unknown_mask", entry);
            if (!knownResult.Success || !unknownResult.Success)
            {
                var error = knownResult.Error ?? unknownResult.Error!;
                return ResourceLoadResult<SharedStructureData>.Failed(error.Code, error.Message, entry.StableKey);
            }

            if (!ShapesEqual(validation.Value!, knownResult.Value!) || !ShapesEqual(validation.Value!, unknownResult.Value!))
            {
                return ResourceLoadResult<SharedStructureData>.Failed(
                    "legacy-structure-optional-mask-shape-mismatch", "Legacy known/unknown masks do not match validation dimensions.", entry.StableKey);
            }

            known = knownResult.Value;
            unknown = unknownResult.Value;
        }

        NpyArray? nativeWalls = null;
        if (archive.ArrayNames.Contains("native_wall_segments", StringComparer.Ordinal))
        {
            nativeWalls = archive.ReadArray("native_wall_segments");
            if (!NativeWallSegmentsValid(nativeWalls, validation.Value!))
            {
                return ResourceLoadResult<SharedStructureData>.Failed(
                    "legacy-structure-walls-invalid", "Legacy native wall rows must be finite N×6 numeric segments within the declared canvas.", entry.StableKey);
            }
        }

        var metadata = BuildLegacyMetadata(entry);
        return ResourceLoadResult<SharedStructureData>.Loaded(new SharedStructureData(metadata, clean, validation.Value!, known, unknown, nativeWalls));
    }

    private static ResourceLoadResult<NpyArray> ReadMask(NpzArchive archive, string member, ResourceCatalogEntry entry)
    {
        var array = archive.ReadArray(member);
        var type = NpyArray.ParseDescriptor(array.Dtype);
        if (array.Rank != 2 || array.Shape[0] <= 0 || array.Shape[1] <= 0 || type.Kind is not ('u' or 'b') || type.Size != 1)
        {
            return ResourceLoadResult<NpyArray>.Failed(
                "structure-mask-type-or-rank-invalid", $"Mask '{member}' must be a two-dimensional one-byte unsigned/bool NPY array.", entry.StableKey);
        }

        foreach (var value in array.Bytes.Span)
        {
            if (value is not (0 or 1 or 255))
            {
                return ResourceLoadResult<NpyArray>.Failed(
                    "structure-mask-values-invalid", $"Mask '{member}' contains a value other than 0, 1, or 255.", entry.StableKey);
            }
        }

        return ResourceLoadResult<NpyArray>.Loaded(array);
    }

    private static bool ShapeMatchesDeclaration(NpyArray array, ResourceCatalogEntry entry, JsonElement metadata)
    {
        if (entry.DeclaredShape is { Count: 2 } declaredShape
            && (array.Shape[0] != declaredShape[0] || array.Shape[1] != declaredShape[1]))
        {
            return false;
        }

        if (TryShapeFromCanvas(Property(metadata, "canvas"), out var metadataShape)
            && (array.Shape[0] != metadataShape[0] || array.Shape[1] != metadataShape[1]))
        {
            return false;
        }

        if (TryShapeFromCanvas(entry.StructureCanvasMetadata, out var displayShape)
            && (array.Shape[0] != displayShape[0] || array.Shape[1] != displayShape[1]))
        {
            return false;
        }

        if (entry.CoordinateMetadata is { } coordinate)
        {
            var hasHeight = coordinate.ValueKind == JsonValueKind.Object && coordinate.TryGetProperty("coordinateHeight", out _);
            var hasWidth = coordinate.ValueKind == JsonValueKind.Object && coordinate.TryGetProperty("coordinateWidth", out _);
            if (hasHeight || hasWidth)
            {
                if (!TryIntProperty(coordinate, "coordinateHeight", out var height)
                    || !TryIntProperty(coordinate, "coordinateWidth", out var width)
                    || height <= 0 || width <= 0
                    || array.Shape[0] != height || array.Shape[1] != width)
                {
                    return false;
                }
            }
        }

        return true;
    }

    private static bool CanvasMetadataMatches(JsonElement metadata, ResourceCatalogEntry entry)
    {
        if (entry.StructureCanvasMetadata is not { } expected
            || expected.ValueKind != JsonValueKind.Object
            || !metadata.TryGetProperty("canvas", out var actual)
            || actual.ValueKind != JsonValueKind.Object
            || !IsValidNativeCanvas(expected, entry.Floor)
            || !IsValidNativeCanvas(actual, entry.Floor))
        {
            return false;
        }

        var expectedNode = JsonNode.Parse(expected.GetRawText());
        var actualNode = JsonNode.Parse(actual.GetRawText());
        return JsonNode.DeepEquals(expectedNode, actualNode);
    }

    private static bool IsValidNativeCanvas(JsonElement canvas, string floor)
    {
        if (!string.Equals(StringProperty(canvas, "floor"), floor, StringComparison.Ordinal)
            || string.IsNullOrWhiteSpace(StringProperty(canvas, "coordinateSpace"))
            || string.IsNullOrWhiteSpace(StringProperty(canvas, "layout"))
            || StringProperty(canvas, "axes") != "x-right,y-down"
            || !TryShapeFromCanvas(canvas, out var shape)
            || !TryIntProperty(canvas, "width", out var width)
            || !TryIntProperty(canvas, "height", out var height)
            || !TryIntProperty(canvas, "worldFloor", out var worldFloor))
        {
            return false;
        }

        return width > 0 && height > 0 && worldFloor > 0
            && width == shape[1] && height == shape[0];
    }

    private static bool NativeWallSegmentsValid(NpyArray walls, NpyArray canvasMask)
    {
        var type = NpyArray.ParseDescriptor(walls.Dtype);
        if (walls.Rank != 2 || walls.Shape[0] <= 0 || walls.Shape[1] != 6
            || type.Kind is not ('i' or 'u' or 'f')
            || canvasMask.Rank != 2)
        {
            return false;
        }

        var height = canvasMask.Shape[0];
        var width = canvasMask.Shape[1];
        Span<double> values = stackalloc double[6];
        for (var row = 0; row < walls.Shape[0]; row++)
        {
            for (var column = 0; column < values.Length; column++)
            {
                var value = walls.ReadNumericValue(checked(row * values.Length + column));
                if (!double.IsFinite(value))
                {
                    return false;
                }

                values[column] = value;
            }

            var axisValue = values[0];
            var sign = values[1];
            var normal = values[2];
            var low = values[3];
            var high = values[4];
            var edgeId = values[5];
            if (axisValue is not (0 or 1)
                || sign is not (-1 or 1)
                || low > high
                || normal < 0 || low < 0 || high < 0
                || edgeId < -1 || edgeId != Math.Floor(edgeId))
            {
                return false;
            }

            var axis = (int)axisValue;
            if (normal >= (axis == 0 ? width : height)
                || high >= (axis == 0 ? height : width))
            {
                return false;
            }
        }

        return true;
    }

    private static bool TryShapeFromCanvas(JsonElement? canvas, out int[] shape)
    {
        shape = [];
        if (canvas is not { } json || json.ValueKind != JsonValueKind.Object
            || !json.TryGetProperty("shapeHW", out var shapeValue) || shapeValue.ValueKind != JsonValueKind.Array)
        {
            return false;
        }

        var values = shapeValue.EnumerateArray().ToArray();
        if (values.Length != 2
            || values[0].ValueKind != JsonValueKind.Number || !values[0].TryGetInt32(out var height)
            || values[1].ValueKind != JsonValueKind.Number || !values[1].TryGetInt32(out var width)
            || height <= 0 || width <= 0)
        {
            return false;
        }

        shape = [height, width];
        return true;
    }

    private static bool ShapesEqual(NpyArray left, NpyArray right) => left.Shape.SequenceEqual(right.Shape);

    private static bool BindingMatches(JsonElement metadata, ResourceCatalogEntry entry)
    {
        if (!TryGetObject(metadata, "binding", out var binding))
        {
            return false;
        }

        return StringProperty(binding, "profile") == entry.Profile
            && StringProperty(binding, "mapId") == entry.MapId
            && StringProperty(binding, "floor") == entry.Floor
            && StringProperty(binding, "variant") == "variant-01";
    }

    private static bool VersionMetadataMatches(JsonElement metadata, ResourceCatalogEntry entry)
    {
        foreach (var (name, expected) in new[]
        {
            ("structureVersionKey", entry.StructureVersionKey),
            ("sourceVersion", entry.SourceVersion),
            ("baseSnapshot", entry.BaseSnapshot)
        })
        {
            if (expected is not null && StringProperty(metadata, name) != expected)
            {
                return false;
            }
        }

        return true;
    }

    private static bool IsSupportedSharedSource(string? sourceKind) =>
        sourceKind is "game-rendered-shared-structure" or "legacy-shared-binary";

    private static JsonElement BuildLegacyMetadata(ResourceCatalogEntry entry)
    {
        using var buffer = new MemoryStream();
        using (var writer = new Utf8JsonWriter(buffer))
        {
            writer.WriteStartObject();
            if (entry.StructureIndexMetadata is { } indexMetadata && indexMetadata.ValueKind == JsonValueKind.Object)
            {
                var replaced = new HashSet<string>(StringComparer.Ordinal)
                {
                    "sourceKind", "sourceClass", "sourceLayout", "structureVersionKey", "sourceVersion", "baseSnapshot", "binding", "canvas"
                };
                foreach (var property in indexMetadata.EnumerateObject())
                {
                    if (!replaced.Contains(property.Name))
                    {
                        property.WriteTo(writer);
                    }
                }
            }

            writer.WriteString("sourceKind", "legacy-shared-binary");
            writer.WriteString("sourceClass", StringProperty(entry.LegacyCanvasMetadata, "sourceClass"));
            writer.WriteNull("sourceLayout");
            writer.WriteString("structureVersionKey", entry.StructureVersionKey);
            writer.WriteString("sourceVersion", entry.SourceVersion);
            writer.WriteString("baseSnapshot", entry.BaseSnapshot);
            writer.WritePropertyName("binding");
            writer.WriteStartObject();
            writer.WriteString("profile", entry.Profile);
            writer.WriteString("mapId", entry.MapId);
            writer.WriteString("floor", entry.Floor);
            writer.WriteString("variant", "variant-01");
            writer.WriteEndObject();
            if (entry.LegacyCanvasMetadata is { } canvas)
            {
                writer.WritePropertyName("canvas");
                writer.WriteStartObject();
                foreach (var propertyName in new[] { "coordinateSpace", "shapeHW" })
                {
                    if (canvas.TryGetProperty(propertyName, out var property))
                    {
                        writer.WritePropertyName(propertyName);
                        property.WriteTo(writer);
                    }
                }

                if (TryShapeFromLegacyCanvas(canvas, out var shape))
                {
                    writer.WriteNumber("width", shape[1]);
                    writer.WriteNumber("height", shape[0]);
                }

                writer.WriteString("floor", entry.Floor);

                writer.WriteEndObject();
            }

            writer.WriteEndObject();
        }

        using var document = JsonDocument.Parse(buffer.ToArray());
        return document.RootElement.Clone();
    }

    private static bool TryShapeFromLegacyCanvas(JsonElement canvas, out int[] shape)
    {
        shape = [];
        if (!canvas.TryGetProperty("shapeHW", out var value) || value.ValueKind != JsonValueKind.Array)
        {
            return false;
        }

        var items = value.EnumerateArray().ToArray();
        if (items.Length != 2
            || items[0].ValueKind != JsonValueKind.Number || !items[0].TryGetInt32(out var height)
            || items[1].ValueKind != JsonValueKind.Number || !items[1].TryGetInt32(out var width))
        {
            return false;
        }

        shape = [height, width];
        return height > 0 && width > 0;
    }

}
