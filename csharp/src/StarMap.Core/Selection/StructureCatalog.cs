using System.Collections.ObjectModel;
using System.Security.Cryptography;
using System.Text;
using System.Text.Json;
using StarMap.Core.Resources;
using StarMap.Core.Identity;

namespace StarMap.Core.Selection;

/// <summary>A selected structure resource registration. It grants no identity or display authority.</summary>
public sealed class StructureCatalog
{
    private static readonly string[] S1Floors = ["-1", "-2", "-3", "-4"];
    private readonly ResourceCatalog? _resourceOwner;
    private readonly IReadOnlyDictionary<(string MapId,string Floor),ResourceCatalogEntry>? _references;
    public string Season { get; }
    public string Mode { get; }
    public string ResourceKey { get; }
    public IReadOnlyList<string> MapIds { get; }
    public IReadOnlyList<string> Floors { get; }
    public IReadOnlyDictionary<string, string> MapNames { get; }
    public string? ModeRoot { get; }

    public StructureCatalog(string season, string mode, string resourceKey, IEnumerable<string> mapIds,
        IEnumerable<string> floors, IReadOnlyDictionary<string, string>? mapNames = null, string? modeRoot = null)
        : this(season,mode,resourceKey,mapIds,floors,mapNames,modeRoot,false) { }

    private StructureCatalog(string season,string mode,string resourceKey,IEnumerable<string> mapIds,
        IEnumerable<string> floors,IReadOnlyDictionary<string,string>? mapNames,string? modeRoot,bool boundRegistration)
    {
        var ids = mapIds.ToArray(); var levels = floors.ToArray();
        bool s1Scope=season=="s1"&&levels.SequenceEqual(S1Floors);
        bool s0Scope=boundRegistration&&season=="s0"&&mode is ("nightmare" or "difficult")&&levels.Length>0
            &&levels.All(v=>!string.IsNullOrEmpty(v))&&levels.Distinct(StringComparer.Ordinal).Count()==levels.Length;
        if (!(s1Scope||s0Scope) || mode is not ("difficulty2" or "difficult" or "nightmare") || string.IsNullOrEmpty(resourceKey)
            || ids.Length == 0 || ids.Any(string.IsNullOrEmpty) || ids.Distinct(StringComparer.Ordinal).Count() != ids.Length
            ) throw new ArgumentException("s1-structure-catalog-invalid");
        Season = season; Mode = mode; ResourceKey = resourceKey;
        MapIds = Array.AsReadOnly(ids); Floors = Array.AsReadOnly(levels); ModeRoot = modeRoot;
        MapNames = new ReadOnlyDictionary<string, string>(ids.ToDictionary(id => id,
            id => mapNames?.GetValueOrDefault(id) ?? id, StringComparer.Ordinal));
    }

    private StructureCatalog(ResourceCatalog resourceOwner, string season, string mode, string resourceKey,
        IEnumerable<string> mapIds, IEnumerable<string> floors, IReadOnlyDictionary<string,string> mapNames, string modeRoot,
        IReadOnlyDictionary<(string MapId,string Floor),ResourceCatalogEntry>? references=null)
        : this(season,mode,resourceKey,mapIds,floors,mapNames,modeRoot,true)
    { _resourceOwner=resourceOwner;_references=references; }

    /// <summary>Only the resource factory can bind a selected catalog to its actual resource owner.</summary>
    public bool IsBoundTo(ResourceCatalog resources) => ReferenceEquals(_resourceOwner,resources);

    /// <summary>Exact registered input, including its actual profile. Missing floors remain missing.</summary>
    public ResourceCatalogEntry? ReferenceFor(ResourceCatalog resources,string mapId,string floor)
    {
        if(!IsBoundTo(resources))throw new ArgumentException("structure-catalog-resource-owner-mismatch",nameof(resources));
        if(!MapIds.Contains(mapId,StringComparer.Ordinal)||!Floors.Contains(floor,StringComparer.Ordinal))return null;
        return _references is not null?_references.GetValueOrDefault((mapId,floor))
            :resources.Find(Mode,Season,"shared-structure",mapId,floor);
    }

    /// <summary>S0 registration from its actual passport/pack scope. This does not declare door coordinates complete.
    /// The caller supplies its existing resource-generation identity; no new content fingerprint is invented here.</summary>
    public static StructureCatalog FromS0Resources(ResourceCatalog resources,EntryPassportCatalog passports,string resourceGeneration)
    {
        if(passports.Season!="s0"||!string.Equals(Path.GetFullPath(resources.RootPath),passports.ResourceRoot,StringComparison.OrdinalIgnoreCase))
            throw new ArgumentException("s0-structure-catalog-context-mismatch",nameof(passports));
        string[] ids=passports.Maps.Select(map=>map.MapId).Order(StringComparer.Ordinal).ToArray();
        var references=new Dictionary<(string MapId,string Floor),ResourceCatalogEntry>();
        // Do not manufacture a complete cartesian product: a declared unavailable
        // floor has no usable entry and must later produce UNKNOWN, not exclusion.
        foreach(var pair in passports.PackFloors)
        {
            if(!ids.Contains(pair.MapId,StringComparer.Ordinal)||!passports.Floors.Contains(pair.Floor))
                throw new InvalidDataException("s0-structure-floor-registration-invalid");
            if(passports.UnavailablePackFloors.Contains(pair))continue;
            var entry=resources.Find(passports.Mode,passports.Season,passports.MapProfiles[pair.MapId],pair.MapId,pair.Floor);
            if(entry is not null)references.Add(pair,entry);
        }
        return new(resources,passports.Season,passports.Mode,resourceGeneration,ids,passports.Floors,
            passports.Maps.ToDictionary(map=>map.MapId,map=>map.Name,StringComparer.Ordinal),passports.ModeRoot,
            new ReadOnlyDictionary<(string MapId,string Floor),ResourceCatalogEntry>(references));
    }

    /// <summary>Uses the native manifest's complete content inventory, exactly as the Python native context does.</summary>
    public static StructureCatalog FromResources(ResourceCatalog resources, string mode)
    {
        if (mode is not ("difficulty2" or "difficult" or "nightmare")) throw new ArgumentException("s1-structure-catalog-context-mismatch");
        string root = Path.Combine(resources.RootPath, "seasons", "s1", mode);
        using var manifestFile = JsonDocument.Parse(File.ReadAllBytes(Path.Combine(root, "native-map-manifest.json")));
        var manifest = manifestFile.RootElement;
        if (manifest.GetProperty("schemaVersion").GetInt32() != 1 || manifest.GetProperty("season").GetString() != "s1"
            || manifest.GetProperty("mode").GetString() != mode || manifest.GetProperty("sourceVersion").GetString() != "2.104.3099014")
            throw new InvalidDataException("s1-structure-catalog-context-mismatch");
        var inventory = new HashSet<string>(StringComparer.OrdinalIgnoreCase);
        foreach (var row in manifest.GetProperty("files").EnumerateArray())
        {
            string path = Local(root, row.GetProperty("path").GetString()!);
            if (!inventory.Add(path) || !File.Exists(path) || new FileInfo(path).Length != row.GetProperty("bytes").GetInt64())
                throw new InvalidDataException("s1-native-file-inventory-mismatch");
            using var stream = File.OpenRead(path);
            if (!Convert.ToHexStringLower(SHA256.HashData(stream)).Equals(row.GetProperty("sha256").GetString(), StringComparison.Ordinal))
                throw new InvalidDataException("s1-native-file-hash-mismatch");
        }
        if (inventory.Count == 0) throw new InvalidDataException("s1-native-file-inventory-missing");
        var interfaces = manifest.GetProperty("runtimeInterfaces");
        // S1NativeContext's existing REQUIRED_INTERFACES inventory membership.
        // identityEntries declares a directory; its three explicit declaration files are required instead.
        string[] requiredInterfaces = ["identityPack", "typedIndex", "versionBindings", "coordinatePack", "neutralDisplay", "authorCatalog"];
        var required = requiredInterfaces.Select(name => Local(root,interfaces.GetProperty(name).GetString()!)).ToList();
        string entryRoot = Local(root,interfaces.GetProperty("identityEntries").GetString()!);
        required.AddRange(new[] { "entries.json", "side-entries.json", "entry-passports.json" }.Select(name => Path.Combine(entryRoot,name)));
        if (required.Any(path => !inventory.Contains(path))) throw new InvalidDataException("s1-native-required-file-missing");
        string packPath = Local(root, interfaces.GetProperty("identityPack").GetString()!);
        using var packFile = JsonDocument.Parse(File.ReadAllBytes(packPath));
        var pack = packFile.RootElement;
        var floors = pack.GetProperty("floors").EnumerateArray().Select(v => v.GetString()!).ToArray();
        if (pack.GetProperty("schemaVersion").GetInt32() != 1 || pack.GetProperty("season").GetString() != "s1" || pack.GetProperty("mode").GetString() != mode
            || pack.GetProperty("defaultEntryFloor").GetInt32() != -1 || !floors.SequenceEqual(S1Floors))
            throw new InvalidDataException("s1-structure-catalog-context-mismatch");
        var maps = pack.GetProperty("maps").EnumerateArray().ToArray();
        var ids = maps.Select(row => row.GetProperty("id").GetString()!).Order(StringComparer.Ordinal).ToArray();
        // Existing season_map_resources.EXPECTED_LAYOUTS registration, not a geometric identity claim.
        string group = mode == "difficulty2" ? "105" : mode == "difficult" ? "106" : "107";
        var registered = Enumerable.Range(1, 15).Where(n => group != "106" || n != 6).Select(n => group + n.ToString("D3")).ToArray();
        if (!ids.SequenceEqual(registered) || maps.Any(row => row.GetProperty("sourceLayout").GetString() != row.GetProperty("id").GetString()
            || row.GetProperty("sourceVersion").GetString() != "2.104.3099014" || row.GetProperty("identityProfileId").GetString() != "shared-structure"
            || !row.GetProperty("layers").EnumerateObject().Select(p => p.Name).ToHashSet().SetEquals(S1Floors)))
            throw new InvalidDataException("s1-structure-catalog-context-mismatch");
        var actual = resources.Entries.Where(e => e.Season == "s1" && e.Mode == mode).ToArray();
        if (actual.Length != ids.Length * floors.Length || !actual.Select(e => e.MapId).ToHashSet(StringComparer.Ordinal).SetEquals(ids)
            || actual.Any(e => e.Profile != "shared-structure" || !floors.Contains(e.Floor)
                || e.StructureNpzPath is null || !inventory.Contains(e.StructureNpzPath)))
            throw new InvalidDataException("s1-structure-catalog-context-mismatch");
        string canonical = Canonical(manifest);
        return new(resources, "s1", mode, Convert.ToHexStringLower(SHA256.HashData(Encoding.UTF8.GetBytes(canonical))), ids, floors,
            maps.ToDictionary(row => row.GetProperty("id").GetString()!, row => row.GetProperty("name").GetString()!, StringComparer.Ordinal), root);
    }

    private static string Local(string root, string relative)
    {
        string path = Path.GetFullPath(Path.Combine(root, relative));
        if (Path.IsPathRooted(relative) || !path.StartsWith(Path.GetFullPath(root) + Path.DirectorySeparatorChar, StringComparison.OrdinalIgnoreCase))
            throw new InvalidDataException("s1-native-path-outside-mode-root");
        return path;
    }

    // Python json.dumps(sort_keys=True, separators=(",", ":"), ensure_ascii=True).
    private static string Canonical(JsonElement value) => value.ValueKind switch
    {
        JsonValueKind.Object => "{" + string.Join(",", value.EnumerateObject().OrderBy(p => p.Name, StringComparer.Ordinal)
            .Select(p => Quote(p.Name) + ":" + Canonical(p.Value))) + "}",
        JsonValueKind.Array => "[" + string.Join(",", value.EnumerateArray().Select(Canonical)) + "]",
        JsonValueKind.String => Quote(value.GetString()!),
        _ => value.GetRawText()
    };
    private static string Quote(string text)
    {
        var result = new StringBuilder("\"");
        foreach (char ch in text) result.Append(ch switch
        {
            '"' => "\\\"", '\\' => "\\\\", '\b' => "\\b", '\f' => "\\f", '\n' => "\\n", '\r' => "\\r", '\t' => "\\t",
            _ => ch < 32 || ch > 127 ? "\\u" + ((int)ch).ToString("x4") : ch.ToString()
        });
        return result.Append('"').ToString();
    }
}
