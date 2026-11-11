using System.Text.Json;

namespace StarMap.Core.Resources;

public enum ResourceBindingStatus
{
    Ready,
    Missing,
    Unsupported,
    Invalid
}

public sealed record ResourceCatalogIssue(string Code, string Message, string? Context = null);

public sealed record ResourceLoadResult<T>(T? Value, ResourceCatalogIssue? Error) where T : class
{
    public bool Success => Value is not null && Error is null;

    internal static ResourceLoadResult<T> Loaded(T value) => new(value, null);
    internal static ResourceLoadResult<T> Failed(string code, string message, string? context = null) =>
        new(null, new ResourceCatalogIssue(code, message, context));
}

public sealed class ResourceCatalogEntry
{
    internal ResourceCatalogEntry(
        ResourceCatalog owner,
        string stableKey,
        string mode,
        string season,
        string profile,
        string mapId,
        string floor,
        string displayName,
        string? previewPath,
        string? previewRelativePath,
        string? previewRole,
        bool previewIsFloorSpecific,
        string? imageLayerPath,
        string? imageLayerRelativePath,
        string? structureNpzPath,
        string? structureNpzRelativePath,
        string structureKind,
        ResourceBindingStatus structureStatus,
        ResourceCatalogIssue? structureIssue,
        string? sourceLayout,
        string? sourceVersion,
        string? structureVersionKey,
        string? baseSnapshot,
        JsonElement? sourceMetadata,
        JsonElement? coordinateMetadata,
        JsonElement? structureIndexMetadata,
        JsonElement? structureCanvasMetadata,
        JsonElement? legacyCanvasMetadata,
        int[]? declaredShape)
    {
        Owner = owner;
        StableKey = stableKey;
        Mode = mode;
        Season = season;
        Profile = profile;
        MapId = mapId;
        Floor = floor;
        DisplayName = displayName;
        PreviewPath = previewPath;
        PreviewRelativePath = previewRelativePath;
        PreviewRole = previewRole;
        PreviewIsFloorSpecific = previewIsFloorSpecific;
        ImageLayerPath = imageLayerPath;
        ImageLayerRelativePath = imageLayerRelativePath;
        StructureNpzPath = structureNpzPath;
        StructureNpzRelativePath = structureNpzRelativePath;
        StructureKind = structureKind;
        StructureStatus = structureStatus;
        StructureIssue = structureIssue;
        SourceLayout = sourceLayout;
        SourceVersion = sourceVersion;
        StructureVersionKey = structureVersionKey;
        BaseSnapshot = baseSnapshot;
        SourceMetadata = sourceMetadata;
        CoordinateMetadata = coordinateMetadata;
        StructureIndexMetadata = structureIndexMetadata;
        StructureCanvasMetadata = structureCanvasMetadata;
        LegacyCanvasMetadata = legacyCanvasMetadata;
        DeclaredShape = declaredShape is null ? null : Array.AsReadOnly((int[])declaredShape.Clone());
    }

    internal ResourceCatalog Owner { get; }
    internal IReadOnlyList<int>? DeclaredShape { get; }

    public string StableKey { get; }
    public string Mode { get; }
    public string Season { get; }
    public string Profile { get; }
    public string MapId { get; }
    public string Floor { get; }
    public string DisplayName { get; }

    /// <summary>Existing image path inside the supplied resource root, or null when no declared preview exists.</summary>
    public string? PreviewPath { get; }
    public string? PreviewRelativePath { get; }
    public string? PreviewRole { get; }
    public bool PreviewIsFloorSpecific { get; }
    /// <summary>Declared full-map reference for display-only manual selection, independent of a floor preview.</summary>
    public string? FullReferencePath { get; internal init; }
    /// <summary>Image-valued identity-pack layer, before optional author/native preview preferences.</summary>
    public string? IdentityLayerPath { get; internal init; }
    /// <summary>Existing, explicitly declared image layer. NPZ structure paths are never returned here.</summary>
    public string? ImageLayerPath { get; }
    public string? ImageLayerRelativePath { get; }

    /// <summary>Existing NPZ path reached through an exact profile/map/floor typed index binding.</summary>
    public string? StructureNpzPath { get; }
    public string? StructureNpzRelativePath { get; }
    public string StructureKind { get; }
    public ResourceBindingStatus StructureStatus { get; }
    public ResourceCatalogIssue? StructureIssue { get; }

    public string? SourceLayout { get; }
    public string? SourceVersion { get; }
    public string? StructureVersionKey { get; }
    public string? BaseSnapshot { get; }
    public JsonElement? SourceMetadata { get; }
    public JsonElement? CoordinateMetadata { get; }
    public JsonElement? StructureIndexMetadata { get; }
    public JsonElement? StructureCanvasMetadata { get; }
    public JsonElement? LegacyCanvasMetadata { get; }
}

/// <summary>
/// Read-only catalog assembled from the resource pack manifests and their typed indexes.
/// Catalog loading reads declarations only; NPZ arrays are opened only by LoadSharedStructure.
/// </summary>
public sealed partial class ResourceCatalog
{
    private const string SharedStructureKind = "shared-structure";
    private const string LegacyStructureKind = "legacy-clean";
    private const long MaxJsonFileBytes = 32L * 1024 * 1024;

    private readonly List<ResourceCatalogEntry> _entries = [];
    private readonly List<ResourceCatalogIssue> _issues = [];
    private readonly Dictionary<string, ResourceCatalogEntry> _byKey = new(StringComparer.Ordinal);

    private ResourceCatalog(string rootPath)
    {
        RootPath = Path.GetFullPath(rootPath);
    }

    public string RootPath { get; }
    public IReadOnlyList<ResourceCatalogEntry> Entries => _entries.AsReadOnly();
    public IReadOnlyList<ResourceCatalogEntry> CatalogEntries => Entries;
    public IReadOnlyList<ResourceCatalogIssue> Issues => _issues.AsReadOnly();
    public IReadOnlyList<ResourceCatalogIssue> LoadIssues => Issues;

    public static ResourceCatalog Load(string rootPath)
    {
        ArgumentException.ThrowIfNullOrWhiteSpace(rootPath);
        var catalog = new ResourceCatalog(rootPath);
        if (!Directory.Exists(catalog.RootPath))
        {
            catalog._issues.Add(new ResourceCatalogIssue("resource-root-missing", "Resource root directory does not exist.", catalog.RootPath));
            return catalog;
        }

        if (File.Exists(Path.Combine(catalog.RootPath, "references", "nightmare-map-pack.json")))
        {
            catalog.LoadS0Nightmare();
        }

        if (File.Exists(Path.Combine(catalog.RootPath, "difficult-native-manifest.json")))
        {
            catalog.LoadS0Difficult();
        }

        catalog.LoadSeasonContexts();
        return catalog;
    }

    public ResourceCatalogEntry? Find(string mode, string season, string profile, string mapId, string floor)
    {
        ArgumentNullException.ThrowIfNull(mode);
        ArgumentNullException.ThrowIfNull(season);
        ArgumentNullException.ThrowIfNull(profile);
        ArgumentNullException.ThrowIfNull(mapId);
        ArgumentNullException.ThrowIfNull(floor);
        var key = StableKey(mode, season, profile, mapId, floor);
        return _byKey.GetValueOrDefault(key);
    }

    private void LoadS0Nightmare()
    {
        const string context = "s0/nightmare";
        TryBuildContext(
            mode: "nightmare",
            season: "s0",
            identityPackPath: "references/nightmare-map-pack.json",
            typedIndexPath: "references/nightmare-shared-structure-index.json",
            coordinatePackPath: "references/nightmare-canonical-coordinate-pack.json",
            structureDisplayPath: "references/nightmare-structure-display.json",
            structureVersionPath: "references/nightmare-structure-version-bindings.json",
            displayBindingsPath: null,
            fullReferencesPath: null,
            legacyDeclaration: null,
            context,
            pathBase: string.Empty);
    }

    private void LoadS0Difficult()
    {
        const string context = "s0/difficult";
        if (!TryReadObject("difficult-native-manifest.json", context, out var manifest))
        {
            return;
        }

        if (!TryGetObject(manifest, "runtimeInterfaces", out var interfaces))
        {
            AddIssue("unsupported-manifest", "Difficult manifest has no runtimeInterfaces object.", context);
            return;
        }

        var packPath = StringProperty(interfaces, "identityPack");
        var indexPath = StringProperty(interfaces, "typedIndex");
        var coordinatePath = StringProperty(interfaces, "coordinatePack");
        var displayPath = StringProperty(interfaces, "neutralDisplay");
        var versionPath = StringProperty(interfaces, "versionBindings");
        var fullReferencesPath = StringProperty(interfaces, "fullReferences");
        if (packPath is null || indexPath is null || coordinatePath is null || displayPath is null || versionPath is null)
        {
            AddIssue("unsupported-manifest", "Difficult runtimeInterfaces omit a required resource declaration.", context);
            return;
        }

        var legacyDeclaration = interfaces.TryGetProperty("legacyClean", out var legacy) && legacy.ValueKind == JsonValueKind.Object
            ? legacy.Clone()
            : (JsonElement?)null;
        TryBuildContext(
            mode: "difficult",
            season: "s0",
            identityPackPath: packPath,
            typedIndexPath: indexPath,
            coordinatePackPath: coordinatePath,
            structureDisplayPath: displayPath,
            structureVersionPath: versionPath,
            displayBindingsPath: null,
            fullReferencesPath,
            legacyDeclaration,
            context,
            sourceManifest: manifest,
            pathBase: string.Empty);
    }

    private void LoadSeasonContexts()
    {
        var seasonsPath = Path.Combine(RootPath, "seasons");
        if (!Directory.Exists(seasonsPath))
        {
            return;
        }

        foreach (var seasonDirectory in Directory.EnumerateDirectories(seasonsPath).OrderBy(path => path, StringComparer.OrdinalIgnoreCase))
        {
            foreach (var modeDirectory in Directory.EnumerateDirectories(seasonDirectory).OrderBy(path => path, StringComparer.OrdinalIgnoreCase))
            {
                var manifestPath = Path.Combine(modeDirectory, "native-map-manifest.json");
                if (!File.Exists(manifestPath))
                {
                    continue;
                }

                var relativeManifest = Path.GetRelativePath(RootPath, manifestPath);
                var context = Path.GetRelativePath(RootPath, modeDirectory).Replace('\\', '/');
                if (!TryReadObject(relativeManifest, context, out var manifest))
                {
                    continue;
                }

                var season = StringProperty(manifest, "season");
                var mode = StringProperty(manifest, "mode");
                if (!TryIntProperty(manifest, "schemaVersion", out var manifestSchema) || manifestSchema != 1
                    || season is null || mode is null || !TryGetObject(manifest, "runtimeInterfaces", out var interfaces))
                {
                    AddIssue("unsupported-manifest", "Season manifest has an unsupported schema or lacks its season, mode, or runtimeInterfaces declaration.", context);
                    continue;
                }

                if (!string.Equals(season, Path.GetFileName(seasonDirectory), StringComparison.Ordinal)
                    || !string.Equals(mode, Path.GetFileName(modeDirectory), StringComparison.Ordinal))
                {
                    AddIssue("manifest-context-mismatch", "Season/mode manifest identity does not match its declared directory.", context);
                    continue;
                }

                var identityPack = StringProperty(interfaces, "identityPack");
                var typedIndex = StringProperty(interfaces, "typedIndex");
                var coordinatePack = StringProperty(interfaces, "coordinatePack");
                var structureDisplay = StringProperty(interfaces, "neutralDisplay");
                var structureVersions = StringProperty(interfaces, "versionBindings");
                if (identityPack is null || typedIndex is null || coordinatePack is null || structureDisplay is null || structureVersions is null)
                {
                    AddIssue("unsupported-manifest", "Season runtimeInterfaces omit a required resource declaration.", context);
                    continue;
                }

                TryBuildContext(
                    mode,
                    season,
                    CombineRelative(context, identityPack),
                    CombineRelative(context, typedIndex),
                    CombineRelative(context, coordinatePack),
                    CombineRelative(context, structureDisplay),
                    CombineRelative(context, structureVersions),
                    CombineRelative(context, "references/native-display-bindings.json"),
                    fullReferencesPath: null,
                    legacyDeclaration: null,
                    context,
                    sourceManifest: manifest,
                    pathBase: context);
            }
        }
    }

    private void TryBuildContext(
        string mode,
        string season,
        string identityPackPath,
        string typedIndexPath,
        string coordinatePackPath,
        string structureDisplayPath,
        string? structureVersionPath,
        string? displayBindingsPath,
        string? fullReferencesPath,
        JsonElement? legacyDeclaration,
        string context,
        JsonElement? sourceManifest = null,
        string pathBase = "")
    {
        if (!TryReadObject(identityPackPath, context, out var pack))
        {
            return;
        }

        if (StringProperty(pack, "mode") is { } packMode && !string.Equals(packMode, mode, StringComparison.Ordinal)
            || StringProperty(pack, "season") is { } packSeason && !string.Equals(packSeason, season, StringComparison.Ordinal))
        {
            AddIssue("map-pack-context-mismatch", "Identity pack mode or season does not match its manifest context.", context);
            return;
        }

        var sharedIndex = ReadSharedIndex(typedIndexPath, context, pathBase);
        var legacyIndex = legacyDeclaration is { } declaration ? ReadLegacyIndex(declaration, context) : new Dictionary<BindingKey, TypedStructureRow>();
        _ = MergeIndexes(sharedIndex, legacyIndex);
        var coordinatePack = ReadOptionalObject(coordinatePackPath, context, "coordinate-pack-unavailable");
        var structureDisplay = ReadOptionalObject(structureDisplayPath, context, "structure-display-unavailable");
        var structureVersions = structureVersionPath is null ? default(JsonElement?) : ReadOptionalObject(structureVersionPath, context, "structure-version-bindings-unavailable");
        var displayBindings = displayBindingsPath is null ? default(JsonElement?) : ReadOptionalObject(displayBindingsPath, context, "native-display-bindings-unavailable");
        var fullReferences = fullReferencesPath is null ? default(JsonElement?) : ReadOptionalObject(fullReferencesPath, context, "full-reference-index-unavailable");

        var structureDisplayRows = IndexRows(structureDisplay, row =>
            (StringProperty(row, "mapId"), StringProperty(row, "floor")), context, "structure-display");
        var versionRows = IndexRows(structureVersions, row =>
        {
            var floor = FirstNonEmpty(StringProperty(row, "floor"), StringProperty(Property(row, "resourceKey"), "floor"));
            return (StringProperty(row, "mapId"), floor);
        }, context, "structure-version-bindings");
        var nativeDisplayRows = IndexRows(displayBindings, row =>
        {
            var binding = row.TryGetProperty("binding", out var bindingValue) && bindingValue.ValueKind == JsonValueKind.Object ? bindingValue : default;
            return (StringProperty(binding, "mapId"), StringProperty(binding, "floor"));
        }, context, "native-display-bindings");
        var fullReferenceRows = IndexRows(fullReferences, row => (StringProperty(row, "mapId"), null), context, "full-reference-index");

        var identityProfiles = pack.TryGetProperty("identityProfiles", out var profiles) && profiles.ValueKind == JsonValueKind.Object
            ? profiles
            : default;
        if (!pack.TryGetProperty("maps", out var maps) || maps.ValueKind != JsonValueKind.Array)
        {
            AddIssue("unsupported-map-pack", "Identity pack has no maps array.", context);
            return;
        }

        foreach (var map in maps.EnumerateArray())
        {
            if (map.ValueKind != JsonValueKind.Object)
            {
                AddIssue("invalid-map-row", "Identity pack contains a non-object map row.", context);
                continue;
            }

            var mapId = StringProperty(map, "id");
            var displayName = StringProperty(map, "name");
            var profile = StringProperty(map, "identityProfileId");
            if (mapId is null)
            {
                AddIssue("invalid-map-row", "Map row has no id.", context);
                continue;
            }

            profile ??= StringProperty(identityProfiles, mapId);
            if (profile is null)
            {
                AddIssue("map-profile-unavailable", $"Map '{mapId}' has no identity profile declaration.", context);
                continue;
            }

            if (!map.TryGetProperty("layers", out var layers) || layers.ValueKind != JsonValueKind.Object)
            {
                AddIssue("map-layers-unavailable", $"Map '{mapId}' has no floor layer object; no floor entries were inferred.", context);
                continue;
            }

            foreach (var floorLayer in layers.EnumerateObject())
            {
                var floor = floorLayer.Name;
                var layerPath = floorLayer.Value.ValueKind == JsonValueKind.String ? floorLayer.Value.GetString() : null;
                AddMapFloor(
                    mode, season, profile, mapId, displayName ?? mapId, floor, layerPath, map,
                    sharedIndex, legacyIndex, coordinatePack, structureDisplayRows, versionRows,
                    nativeDisplayRows, fullReferenceRows, context, pathBase);
            }
        }

        AddUnconsumedIndexRows(sharedIndex, mode, season, context);
        AddUnconsumedIndexRows(legacyIndex, mode, season, context);
    }

    private void AddMapFloor(
        string mode,
        string season,
        string profile,
        string mapId,
        string displayName,
        string floor,
        string? layerRelativePath,
        JsonElement mapMetadata,
        Dictionary<BindingKey, TypedStructureRow> sharedIndex,
        Dictionary<BindingKey, TypedStructureRow> legacyIndex,
        JsonElement? coordinatePack,
        Dictionary<(string? MapId, string? Floor), JsonElement> structureDisplayRows,
        Dictionary<(string? MapId, string? Floor), JsonElement> versionRows,
        Dictionary<(string? MapId, string? Floor), JsonElement> nativeDisplayRows,
        Dictionary<(string? MapId, string? Floor), JsonElement> fullReferenceRows,
        string context,
        string pathBase)
    {
        var lookupKey = new BindingKey(profile, mapId, "variant-01", floor);
        var row = sharedIndex.GetValueOrDefault(lookupKey);
        if (row is null)
        {
            row = legacyIndex.GetValueOrDefault(lookupKey);
        }

        var structureStatus = ResourceBindingStatus.Ready;
        ResourceCatalogIssue? structureIssue = null;
        string? structurePath = null;
        var structureKind = row?.Kind ?? "unbound";

        if (layerRelativePath is null && row is null)
        {
            structureStatus = ResourceBindingStatus.Missing;
            structureIssue = new ResourceCatalogIssue("floor-layer-path-missing", "Map declares this floor without a resource path.", context);
        }

        if (row is null)
        {
            var competingProfiles = sharedIndex.Keys.Concat(legacyIndex.Keys)
                .Where(key => key.MapId == mapId && key.Floor == floor).ToArray();
            structureStatus = ResourceBindingStatus.Unsupported;
            var code = competingProfiles.Length > 0 ? "profile-binding-mismatch" : "typed-structure-binding-missing";
            var message = competingProfiles.Length > 0
                ? $"No typed structure is registered for profile '{profile}' at {mapId}/{floor}; another profile is registered."
                : $"No typed structure index row is registered for {profile}/{mapId}/{floor}.";
            structureIssue = new ResourceCatalogIssue(code, message, context);
        }
        else if (!string.Equals(row.Profile, profile, StringComparison.Ordinal)
            || !string.Equals(row.MapId, mapId, StringComparison.Ordinal)
            || !string.Equals(row.Floor, floor, StringComparison.Ordinal)
            || !string.Equals(row.Variant, "variant-01", StringComparison.Ordinal))
        {
            structureStatus = ResourceBindingStatus.Invalid;
            structureIssue = new ResourceCatalogIssue("typed-structure-binding-mismatch", "Typed index row does not match the declared profile/map/floor/variant.", context);
        }
        else if (!TryResolveLocal(row.ResolvedRelativePath, out structurePath, out var pathError))
        {
            structureStatus = ResourceBindingStatus.Invalid;
            structureIssue = new ResourceCatalogIssue("typed-structure-path-invalid", pathError, context);
        }
        else if (!IsNpzPath(row.ResolvedRelativePath))
        {
            structureStatus = ResourceBindingStatus.Unsupported;
            structureIssue = new ResourceCatalogIssue("typed-structure-format-unsupported", "Typed structure index does not point to an NPZ file.", context);
            structurePath = null;
        }
        else if (!File.Exists(structurePath))
        {
            structureStatus = ResourceBindingStatus.Missing;
            structureIssue = new ResourceCatalogIssue("typed-structure-file-missing", $"Declared typed structure file is missing: {row.ResolvedRelativePath}", context);
            structurePath = null;
        }
        else if (layerRelativePath is not null && IsNpzPath(layerRelativePath)
            && TryResolveLocal(QualifyPath(layerRelativePath, pathBase), out var declaredLayerPath, out _)
            && !PathsEqual(structurePath, declaredLayerPath))
        {
            structureStatus = ResourceBindingStatus.Invalid;
            structureIssue = new ResourceCatalogIssue("typed-layer-index-path-mismatch", "Identity pack NPZ path does not equal the exact typed-index resource path.", context);
            structurePath = null;
        }

        if (structureStatus != ResourceBindingStatus.Ready && structureIssue is not null)
        {
            _issues.Add(structureIssue with { Context = StableKey(mode, season, profile, mapId, floor) });
        }

        var coordinateMetadata = FindCoordinateMetadata(coordinatePack, mapId, floor);
        JsonElement? structureDisplay = structureDisplayRows.TryGetValue((mapId, floor), out var displayRowValue)
            ? displayRowValue
            : null;
        var structureCanvas = structureDisplay is { } displayRow
            && displayRow.ValueKind == JsonValueKind.Object
            && displayRow.TryGetProperty("canvas", out var canvasValue)
            && canvasValue.ValueKind == JsonValueKind.Object
                ? canvasValue.Clone()
                : default(JsonElement?);
        JsonElement? versionRow = versionRows.TryGetValue((mapId, floor), out var versionRowValue)
            ? versionRowValue
            : null;
        JsonElement? nativeDisplay = nativeDisplayRows.TryGetValue((mapId, floor), out var nativeDisplayValue)
            ? nativeDisplayValue
            : null;
        JsonElement? fullReference = fullReferenceRows.TryGetValue((mapId, null), out var fullReferenceValue)
            ? fullReferenceValue
            : null;
        var legacyCanvas = row?.LegacyCanvasMetadata;

        var floorImageRelative = FloorImagePath(layerRelativePath, nativeDisplay, versionRow, pathBase);
        var previewRelative = floorImageRelative;
        var previewRole = floorImageRelative is null ? null : PreviewRole(layerRelativePath, nativeDisplay, versionRow);
        var previewIsFloorSpecific = floorImageRelative is not null;
        if (previewRelative is null)
        {
            previewRelative = QualifyOptional(StringProperty(fullReference, "resourcePath"), pathBase)
                ?? QualifyOptional(StringProperty(mapMetadata, "oneFlow"), pathBase);
            if (previewRelative is not null)
            {
                previewRole = fullReference is not null ? "full-reference-map" : "map-one-flow-preview";
                previewIsFloorSpecific = false;
            }
        }

        var imagePath = ResolveExistingImage(floorImageRelative, context, StableKey(mode, season, profile, mapId, floor), reportMissing: false);
        if (floorImageRelative is not null && imagePath is null)
        {
            AddIssue("image-layer-file-missing", $"Declared floor image does not exist: {floorImageRelative}", StableKey(mode, season, profile, mapId, floor));
        }
        var previewPath = ResolveExistingImage(previewRelative, context, StableKey(mode, season, profile, mapId, floor), reportMissing: true);

        var stableKey = StableKey(mode, season, profile, mapId, floor);
        var coordinateSource = coordinateMetadata ?? legacyCanvas;
        var sourceVersion = FirstNonEmpty(
            StringProperty(mapMetadata, "sourceVersion"),
            row?.SourceVersion,
            StringProperty(coordinateMetadata, "sourceVersion"));
        var structureVersionKey = FirstNonEmpty(
            row?.StructureVersionKey,
            StringProperty(coordinateMetadata, "structureVersionKey"),
            StringProperty(structureCanvas, "structureVersionKey"));
        var baseSnapshot = FirstNonEmpty(
            row?.BaseSnapshot,
            StringProperty(coordinateMetadata, "baseSnapshot"),
            StringProperty(structureCanvas, "baseSnapshot"));

        AddEntry(new ResourceCatalogEntry(
            this,
            stableKey,
            mode,
            season,
            profile,
            mapId,
            floor,
            displayName,
            previewPath,
            previewRelative is null ? null : NormalizeRelative(previewRelative),
            previewRole,
            previewIsFloorSpecific,
            imagePath,
            floorImageRelative is null ? null : NormalizeRelative(floorImageRelative),
            structurePath,
            row is null ? null : NormalizeRelative(row.ResolvedRelativePath),
            structureKind,
            structureStatus,
            structureIssue,
            StringProperty(mapMetadata, "sourceLayout"),
            sourceVersion,
            structureVersionKey,
            baseSnapshot,
            mapMetadata.Clone(),
            coordinateSource,
            row?.RawMetadata,
            structureCanvas,
            legacyCanvas,
            row?.DeclaredShape)
        {
            IdentityLayerPath = IsImagePath(layerRelativePath)
                ? ResolveExistingImage(QualifyOptional(layerRelativePath, pathBase), context, stableKey, reportMissing: false) : null,
            FullReferencePath = ResolveExistingImage(
                QualifyOptional(StringProperty(fullReference, "resourcePath"), pathBase)
                    ?? QualifyOptional(StringProperty(mapMetadata, "manualReference"), pathBase),
                context, stableKey, reportMissing: false)
        });
    }

    private Dictionary<BindingKey, TypedStructureRow> ReadSharedIndex(string relativePath, string context, string pathBase)
    {
        var result = new Dictionary<BindingKey, TypedStructureRow>();
        if (!TryReadObject(relativePath, context, out var index))
        {
            return result;
        }

        if (!TryIntProperty(index, "schemaVersion", out var schemaVersion) || schemaVersion != 1
            || StringProperty(index, "resourceKind") != SharedStructureKind
            || !index.TryGetProperty("entries", out var entries) || entries.ValueKind != JsonValueKind.Array)
        {
            AddIssue("unsupported-shared-structure-index", "Shared structure index schema is unsupported.", context);
            return result;
        }

        foreach (var row in entries.EnumerateArray())
        {
            var profile = StringProperty(row, "profile");
            var mapId = StringProperty(row, "mapId");
            var floor = StringProperty(row, "floor");
            var variant = StringProperty(row, "variant") ?? "variant-01";
            var resourcePath = StringProperty(row, "resourcePath");
            if (profile is null || mapId is null || floor is null || resourcePath is null)
            {
                AddIssue("invalid-shared-structure-row", "Shared structure index row omits profile/map/floor/resourcePath.", context);
                continue;
            }

            var key = new BindingKey(profile, mapId, variant, floor);
            if (!result.TryAdd(key, new TypedStructureRow(
                profile,
                mapId,
                floor,
                variant,
                resourcePath,
                QualifyPath(resourcePath, pathBase),
                SharedStructureKind,
                row.Clone(),
                StringProperty(row, "sourceVersion"),
                StringProperty(row, "structureVersionKey"),
                StringProperty(row, "baseSnapshot"),
                null,
                null)))
            {
                AddIssue("duplicate-shared-structure-row", $"Duplicate typed index binding {profile}/{mapId}/{floor}/{variant}.", context);
            }
        }

        return result;
    }

    private Dictionary<BindingKey, TypedStructureRow> ReadLegacyIndex(JsonElement legacyDeclaration, string context)
    {
        var result = new Dictionary<BindingKey, TypedStructureRow>();
        var indexPath = StringProperty(legacyDeclaration, "index");
        var resourceRoot = StringProperty(legacyDeclaration, "resourceRoot");
        var profileId = StringProperty(legacyDeclaration, "profileId");
        if (indexPath is null || resourceRoot is null || profileId is null
            || !TryReadObject(indexPath, context, out var index))
        {
            AddIssue("legacy-structure-index-unavailable", "Legacy resource declaration or index is unavailable.", context);
            return result;
        }

        if (!TryIntProperty(index, "schemaVersion", out var schemaVersion) || schemaVersion != 2
            || StringProperty(index, "validationMaskContract") != "author_route_structure_mask"
            || !index.TryGetProperty("entries", out var entries) || entries.ValueKind != JsonValueKind.Array)
        {
            AddIssue("unsupported-legacy-structure-index", "Legacy structure index schema is unsupported.", context);
            return result;
        }

        var allowedMaps = new HashSet<string>(StringArrayProperty(legacyDeclaration, "mapIds"), StringComparer.Ordinal);
        var declaredCanvases = legacyDeclaration.TryGetProperty("referenceCanvas", out var canvasMap)
            && canvasMap.ValueKind == JsonValueKind.Object
                ? canvasMap
                : default;
        foreach (var row in entries.EnumerateArray())
        {
            var rowProfile = StringProperty(row, "profileId");
            var mapId = StringProperty(row, "mapId");
            var floor = StringProperty(row, "floor");
            var variant = StringProperty(row, "variantId") ?? "variant-01";
            var resourcePath = StringProperty(row, "resourcePath");
            if (rowProfile != profileId || mapId is null || floor is null || resourcePath is null || !allowedMaps.Contains(mapId))
            {
                continue;
            }

            if (declaredCanvases.ValueKind != JsonValueKind.Object
                || !declaredCanvases.TryGetProperty(floor, out var canvas) || canvas.ValueKind != JsonValueKind.Object)
            {
                AddIssue("legacy-canvas-missing", $"Legacy canvas declaration is missing for {mapId}/{floor}.", context);
                continue;
            }

            var canvasPath = StringProperty(canvas, "resourcePath");
            var resolvedLegacyPath = CombineRelative(resourceRoot, resourcePath);
            if (canvasPath is not null && !PathsEqual(RelativeToRoot(resolvedLegacyPath), RelativeToRoot(canvasPath)))
            {
                AddIssue("legacy-resource-path-mismatch", $"Legacy index and canvas declare different resource paths for {mapId}/{floor}.", context);
                continue;
            }

            var shape = IntArrayProperty(canvas, "shapeHW");
            if (shape is null || shape.Length != 2 || shape.Any(value => value <= 0))
            {
                AddIssue("legacy-canvas-shape-invalid", $"Legacy canvas shape is invalid for {mapId}/{floor}.", context);
                continue;
            }

            var key = new BindingKey(profileId, mapId, variant, floor);
            if (!result.TryAdd(key, new TypedStructureRow(
                profileId,
                mapId,
                floor,
                variant,
                resourcePath,
                resolvedLegacyPath,
                LegacyStructureKind,
                row.Clone(),
                StringProperty(row, "sourceCommit"),
                StringProperty(canvas, "structureVersionKey"),
                StringProperty(canvas, "baseSnapshot"),
                canvas.Clone(),
                shape)))
            {
                AddIssue("duplicate-legacy-structure-row", $"Duplicate legacy typed binding {profileId}/{mapId}/{floor}/{variant}.", context);
            }
        }

        return result;
    }

    private Dictionary<BindingKey, TypedStructureRow> MergeIndexes(
        Dictionary<BindingKey, TypedStructureRow> sharedIndex,
        Dictionary<BindingKey, TypedStructureRow> legacyIndex)
    {
        var merged = new Dictionary<BindingKey, TypedStructureRow>(sharedIndex);
        foreach (var pair in legacyIndex)
        {
            if (!merged.TryAdd(pair.Key, pair.Value))
            {
                AddIssue("duplicate-structure-binding", $"Shared and legacy indexes both declare {pair.Key.Profile}/{pair.Key.MapId}/{pair.Key.Floor}.");
            }
        }

        return merged;
    }

    private void AddUnconsumedIndexRows(Dictionary<BindingKey, TypedStructureRow> index, string mode, string season, string context)
    {
        // A pack row is the authoritative candidate list. Extra index rows are diagnostics only;
        // they are never turned into synthetic map/floor entries.
        foreach (var binding in index.Keys)
        {
            if (!_byKey.ContainsKey(StableKey(mode, season, binding.Profile, binding.MapId, binding.Floor)))
            {
                AddIssue("typed-index-entry-not-in-map-pack", $"Typed index declares {binding.Profile}/{binding.MapId}/{binding.Floor} without a map-pack floor row.", context);
            }
        }
    }

    private Dictionary<BindingKey, TypedStructureRow> ReadAllContextIndexes(
        Dictionary<BindingKey, TypedStructureRow> sharedIndex,
        Dictionary<BindingKey, TypedStructureRow> legacyIndex) => MergeIndexes(sharedIndex, legacyIndex);

    private Dictionary<(string? MapId, string? Floor), JsonElement> IndexRows(
        JsonElement? document,
        Func<JsonElement, (string? MapId, string? Floor)> selector,
        string context,
        string label)
    {
        var rows = new Dictionary<(string? MapId, string? Floor), JsonElement>();
        if (document is not { } json || json.ValueKind != JsonValueKind.Object
            || !json.TryGetProperty("entries", out var entries) || entries.ValueKind != JsonValueKind.Array)
        {
            return rows;
        }

        foreach (var row in entries.EnumerateArray())
        {
            if (row.ValueKind != JsonValueKind.Object)
            {
                AddIssue($"invalid-{label}-row", $"{label} row must be a JSON object.", context);
                continue;
            }
            var key = selector(row);
            if (key.MapId is null || key.Floor is null && label is not "full-reference-index")
            {
                AddIssue($"invalid-{label}-row", $"{label} row is missing its mapId or floor binding.", context);
                continue;
            }

            if (!rows.TryAdd(key, row.Clone()))
            {
                AddIssue($"duplicate-{label}-row", $"Duplicate {label} row for {key.MapId}/{key.Floor}.", context);
            }
        }

        return rows;
    }

    private JsonElement? FindCoordinateMetadata(JsonElement? coordinatePack, string mapId, string floor)
    {
        if (coordinatePack is not { } coordinates || coordinates.ValueKind != JsonValueKind.Object
            || !coordinates.TryGetProperty("maps", out var maps)
            || maps.ValueKind != JsonValueKind.Object || !maps.TryGetProperty(mapId, out var map)) return null;
        if (map.ValueKind != JsonValueKind.Object)
        {
            AddIssue("invalid-coordinate-map", "Coordinate map metadata must be a JSON object.", $"{mapId}/{floor}");
            return null;
        }
        if (!map.TryGetProperty("floors", out var floors) || floors.ValueKind != JsonValueKind.Object
            || !floors.TryGetProperty(floor, out var floorRow) || floorRow.ValueKind != JsonValueKind.Object)
        {
            return null;
        }

        return floorRow.Clone();
    }

    private string? FloorImagePath(string? layerRelativePath, JsonElement? nativeDisplay, JsonElement? versionRow, string pathBase)
    {
        var nativeImage = QualifyOptional(StringProperty(nativeDisplay, "displayResource"), pathBase);
        if (IsImagePath(nativeImage))
        {
            return nativeImage;
        }

        var authorImage = QualifyOptional(StringProperty(Property(versionRow, "authorDisplay"), "resource"), pathBase);
        if (IsImagePath(authorImage))
        {
            return authorImage;
        }

        var layerImage = QualifyOptional(layerRelativePath, pathBase);
        return IsImagePath(layerImage) ? layerImage : null;
    }

    private static string QualifyPath(string relativePath, string pathBase) =>
        string.IsNullOrEmpty(pathBase) ? NormalizeRelative(relativePath) : CombineRelative(pathBase, relativePath);

    private static string? QualifyOptional(string? relativePath, string pathBase) =>
        relativePath is null ? null : QualifyPath(relativePath, pathBase);

    private static string? PreviewRole(string? layerRelativePath, JsonElement? nativeDisplay, JsonElement? versionRow)
    {
        if (StringProperty(nativeDisplay, "displayResource") is not null)
        {
            return "native-rendered-floor-layer";
        }

        if (StringProperty(Property(versionRow, "authorDisplay"), "resource") is not null)
        {
            return "author-floor-display";
        }

        return IsImagePath(layerRelativePath) ? "identity-pack-floor-image" : null;
    }

    private string? ResolveExistingImage(string? relativePath, string context, string entryKey, bool reportMissing)
    {
        if (relativePath is null)
        {
            return null;
        }

        if (!IsImagePath(relativePath))
        {
            if (reportMissing)
            {
                AddIssue("preview-path-not-image", $"Declared preview path is not an image: {relativePath}", entryKey);
            }

            return null;
        }

        if (!TryResolveLocal(relativePath, out var fullPath, out var error))
        {
            if (reportMissing)
            {
                AddIssue("preview-path-invalid", error, entryKey);
            }

            return null;
        }

        if (File.Exists(fullPath))
        {
            return fullPath;
        }

        if (reportMissing)
        {
            AddIssue("preview-file-missing", $"Declared image preview does not exist: {relativePath}", entryKey);
        }

        return null;
    }

    private bool TryReadObject(string relativePath, string context, out JsonElement json)
    {
        json = default;
        if (!TryResolveLocal(relativePath, out var fullPath, out var pathError))
        {
            AddIssue("resource-declaration-path-invalid", pathError, context);
            return false;
        }

        try
        {
            var info = new FileInfo(fullPath);
            if (!info.Exists)
            {
                AddIssue("resource-declaration-missing", $"Declared JSON resource is missing: {relativePath}", context);
                return false;
            }

            if (info.Length <= 0 || info.Length > MaxJsonFileBytes)
            {
                AddIssue("resource-json-size-invalid", $"Declared JSON resource exceeds its {MaxJsonFileBytes} byte limit: {relativePath}", context);
                return false;
            }

            using var document = JsonDocument.Parse(File.ReadAllBytes(fullPath), new JsonDocumentOptions { MaxDepth = 64 });
            if (document.RootElement.ValueKind != JsonValueKind.Object)
            {
                AddIssue("resource-json-not-object", $"Declared JSON resource must contain an object: {relativePath}", context);
                return false;
            }

            json = document.RootElement.Clone();
            return true;
        }
        catch (Exception error) when (error is IOException or UnauthorizedAccessException or JsonException or InvalidDataException)
        {
            AddIssue("resource-json-read-failed", $"Could not read {relativePath}: {error.Message}", context);
            return false;
        }
    }

    private JsonElement? ReadOptionalObject(string? relativePath, string context, string issueCode)
    {
        if (relativePath is null)
        {
            return null;
        }

        if (TryReadObject(relativePath, context, out var json))
        {
            return json;
        }

        if (!_issues.Any(issue => issue.Context == context && issue.Code is "resource-declaration-path-invalid" or "resource-declaration-missing" or "resource-json-size-invalid" or "resource-json-not-object" or "resource-json-read-failed"))
        {
            AddIssue(issueCode, $"Optional resource declaration unavailable: {relativePath}", context);
        }

        return null;
    }

    private bool TryResolveLocal(string? relativePath, out string fullPath, out string error)
    {
        fullPath = string.Empty;
        error = "Resource path is empty.";
        if (string.IsNullOrWhiteSpace(relativePath))
        {
            return false;
        }

        if (Path.IsPathRooted(relativePath)
            || relativePath.Split(['/', '\\'], StringSplitOptions.RemoveEmptyEntries).Any(part => part is "." or ".."))
        {
            error = $"Resource path must remain relative to the selected root: {relativePath}";
            return false;
        }

        try
        {
            fullPath = Path.GetFullPath(Path.Combine(RootPath, relativePath));
            var prefix = Path.EndsInDirectorySeparator(RootPath) ? RootPath : RootPath + Path.DirectorySeparatorChar;
            if (!fullPath.StartsWith(prefix, StringComparison.OrdinalIgnoreCase))
            {
                error = $"Resource path escapes the selected root: {relativePath}";
                return false;
            }

            error = string.Empty;
            return true;
        }
        catch (Exception exception) when (exception is ArgumentException or IOException or NotSupportedException)
        {
            error = $"Resource path is invalid: {relativePath} ({exception.Message})";
            return false;
        }
    }

    private void AddEntry(ResourceCatalogEntry entry)
    {
        if (_byKey.TryAdd(entry.StableKey, entry))
        {
            _entries.Add(entry);
            return;
        }

        AddIssue("duplicate-catalog-entry", $"Duplicate catalog key {entry.StableKey}.");
    }

    private void AddIssue(string code, string message, string? context = null) => _issues.Add(new ResourceCatalogIssue(code, message, context));

    private static string StableKey(string mode, string season, string profile, string mapId, string floor) =>
        string.Join('/', season, mode, profile, mapId, floor);

    private static string CombineRelative(string root, string relative) =>
        (root.TrimEnd('/', '\\') + "/" + relative.TrimStart('/', '\\')).Replace('\\', '/');

    private string RelativeToRoot(string path) => NormalizeRelative(path);

    private static string NormalizeRelative(string? relativePath) => relativePath?.Replace('\\', '/') ?? string.Empty;

    private bool PathsEqual(string left, string right)
    {
        if (!TryResolveLocal(left, out var fullLeft, out _) || !TryResolveLocal(right, out var fullRight, out _))
        {
            return string.Equals(NormalizeRelative(left), NormalizeRelative(right), StringComparison.OrdinalIgnoreCase);
        }

        return string.Equals(fullLeft, fullRight, StringComparison.OrdinalIgnoreCase);
    }

    private static bool IsImagePath(string? path) => Path.GetExtension(path ?? string.Empty).ToLowerInvariant() is ".png" or ".webp" or ".jpg" or ".jpeg" or ".bmp";
    private static bool IsNpzPath(string? path) => string.Equals(Path.GetExtension(path ?? string.Empty), ".npz", StringComparison.OrdinalIgnoreCase);

    private static string? FirstNonEmpty(params string?[] values) => values.FirstOrDefault(value => !string.IsNullOrWhiteSpace(value));

    private static JsonElement? Property(JsonElement? element, string name)
    {
        return element is { } value && value.ValueKind == JsonValueKind.Object && value.TryGetProperty(name, out var property)
            ? property
            : null;
    }

    private static bool TryGetObject(JsonElement element, string name, out JsonElement value)
    {
        if (element.ValueKind == JsonValueKind.Object && element.TryGetProperty(name, out value) && value.ValueKind == JsonValueKind.Object)
        {
            return true;
        }

        value = default;
        return false;
    }

    private static string? StringProperty(JsonElement element, string name) =>
        element.ValueKind == JsonValueKind.Object && element.TryGetProperty(name, out var value) && value.ValueKind == JsonValueKind.String
            ? value.GetString()
            : null;

    private static string? StringProperty(JsonElement? element, string name) =>
        element is { } value ? StringProperty(value, name) : null;

    private static string? StringProperty(JsonElement element, string name, string fallback) => StringProperty(element, name) ?? fallback;

    private static bool TryIntProperty(JsonElement element, string name, out int number)
    {
        number = 0;
        return element.ValueKind == JsonValueKind.Object
            && element.TryGetProperty(name, out var value)
            && value.ValueKind == JsonValueKind.Number
            && value.TryGetInt32(out number);
    }

    private static IReadOnlyList<string> StringArrayProperty(JsonElement element, string name)
    {
        if (element.ValueKind != JsonValueKind.Object || !element.TryGetProperty(name, out var value) || value.ValueKind != JsonValueKind.Array)
        {
            return [];
        }

        return value.EnumerateArray().Where(item => item.ValueKind == JsonValueKind.String)
            .Select(item => item.GetString()!).ToArray();
    }

    private static int[]? IntArrayProperty(JsonElement element, string name)
    {
        if (element.ValueKind != JsonValueKind.Object || !element.TryGetProperty(name, out var value) || value.ValueKind != JsonValueKind.Array)
        {
            return null;
        }

        var values = new List<int>();
        foreach (var item in value.EnumerateArray())
        {
            if (item.ValueKind != JsonValueKind.Number || !item.TryGetInt32(out var number))
            {
                return null;
            }

            values.Add(number);
        }

        return values.ToArray();
    }

    private static string RelativePathFromRoot(string combinedRelative) => NormalizeRelative(combinedRelative);

    private readonly record struct BindingKey(string Profile, string MapId, string Variant, string Floor);

    private sealed record TypedStructureRow(
        string Profile,
        string MapId,
        string Floor,
        string Variant,
        string DeclaredRelativePath,
        string ResolvedRelativePath,
        string Kind,
        JsonElement RawMetadata,
        string? SourceVersion,
        string? StructureVersionKey,
        string? BaseSnapshot,
        JsonElement? LegacyCanvasMetadata,
        int[]? DeclaredShape);
}
