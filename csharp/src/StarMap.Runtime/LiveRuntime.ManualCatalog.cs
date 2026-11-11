using System.Text.Json;
using StarMap.Core.Resources;

namespace StarMap.Runtime;

public sealed record ManualMapCatalogContext(string Season, string Mode, string ProfileId, string? RoundId, string ResourceId);
public sealed record ManualMapCatalogEntry(string MapId, string Name, string Direction, string? ThumbnailPath, string ThumbnailStamp);
public sealed record RuntimeManualMapCatalog(ManualMapCatalogContext Context, IReadOnlyList<ManualMapCatalogEntry> Maps);

public sealed partial class LiveRuntime
{
    /// <summary>Read-only projection of the prepared pack. File stats belong on a worker;
    /// no engine construction, ranking, capture or structure loading occurs here.</summary>
    public RuntimeManualMapCatalog? ReadManualMapCatalog(CancellationToken cancellationToken = default)
    {
        BoundResources bound;
        ManualMapCatalogContext context;
        lock (_sync)
        {
            if (_closed || _resources is null) return null;
            bound = _resources;
            context = new(_selection.Season, _selection.Mode, _selection.ProfileId, _roundId, bound.Id);
        }
        var maps = new List<ManualMapCatalogEntry>();
        foreach (var map in bound.Passports.Maps)
        {
            cancellationToken.ThrowIfCancellationRequested();
            var entries = bound.Catalog.Entries.Where(row => row.Season == context.Season && row.Mode == context.Mode
                && row.MapId == map.MapId && row.Profile == bound.Passports.MapProfiles[map.MapId]).ToArray();
            string direction = entries.Select(row => String(row.SourceMetadata, "direction")).FirstOrDefault(value => value.Length > 0) ?? "";
            IEnumerable<string?> paths = context.Season == "s1"
                ? entries.Where(row => row.Floor == bound.Passports.DefaultEntryFloor && row.PreviewRole == "native-rendered-floor-layer").Select(row => row.PreviewPath)
                : context.Mode == "difficult" ? entries.Select(row => row.FullReferencePath)
                : new[] { bound.Passports.DefaultEntryFloor, "1", "2", "B1" }.Distinct(StringComparer.Ordinal)
                    .SelectMany(floor => entries.Where(row => row.Floor == floor).Select(row => row.IdentityLayerPath));
            string? path = null; string stamp = "";
            foreach (var candidate in paths.Where(value => value is not null).Distinct(StringComparer.OrdinalIgnoreCase))
            {
                cancellationToken.ThrowIfCancellationRequested();
                try
                {
                    var file = new FileInfo(candidate!);
                    if (!file.Exists) continue;
                    path = file.FullName; stamp = file.LastWriteTimeUtc.Ticks + "-" + file.Length; break;
                }
                catch (Exception error) when (error is IOException or UnauthorizedAccessException) { }
            }
            maps.Add(new(map.MapId, map.Name, direction, path, stamp));
        }
        return new(context, maps.AsReadOnly());

        static string String(JsonElement? row, string name) => row is { ValueKind: JsonValueKind.Object } value
            && value.TryGetProperty(name, out var property) && property.ValueKind == JsonValueKind.String ? property.GetString() ?? "" : "";
    }
}
