using System.Text.Json.Serialization;

namespace StarMap.Bridge;

public sealed record BrowseContext(string Season, string Mode, string Label);
public sealed record WorkerHello(BrowseContext[] Contexts, string Python);
public sealed record RouteProfile(string Id, string Name);
public sealed record BrowseRow(string Key, string Title, string MapId, string ProfileId,
    string Variant, string Floor, string FloorLabel, bool Available)
{
    [JsonIgnore] public string Description => $"{FloorLabel} · {Variant}";
    [JsonIgnore] public string Availability => Available ? "" : "图片缺失";
}
public sealed record BrowseCatalog(long CatalogId, string Season, string Mode,
    RouteProfile[] Profiles, BrowseRow[] Rows, string[] Warnings);
public sealed record BrowsePreview(long CatalogId, string Key, int Width, int Height, string PngBase64);

public sealed class WorkerException(string code, string message) : Exception(message)
{
    public string Code { get; } = code;
}
