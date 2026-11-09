using System.Collections.Frozen;
using System.Security.Cryptography;
using System.Text.Json;

namespace StarMap.Core.Identity;

public sealed record EntryMapDefinition(string MapId, string Name);

/// <summary>The declarations consumed by entry recognition for one mode/season.</summary>
public sealed class EntryPassportCatalog
{
    public string Season { get; }
    public string Mode { get; }
    public string ProfileId { get; }
    public string ResourceRoot { get; }
    public IReadOnlySet<(string MapId, string Floor)> PackFloors { get; }
    public IReadOnlySet<(string MapId, string Floor)> UnavailablePackFloors { get; }
    public string ModeRoot { get; }
    public string EntryRoot { get; }
    public string? ExitIconPath { get; }
    public string? NativePassportError { get; }
    public string? LegacyPassportSource { get; }
    public IReadOnlyList<string> Floors { get; }
    public IReadOnlyDictionary<string, string> MapProfiles { get; }
    public IReadOnlyList<EntryMapDefinition> Maps { get; }
    public IReadOnlySet<(string MapId, string Floor)> RegisteredFloors { get; }
    public JsonElement SemanticGaps { get; }
    public IReadOnlyList<JsonElement> SideEntries { get; }
    public IReadOnlyList<JsonElement> MainEntries { get; }
    public IReadOnlyList<JsonElement> Passports { get; }
    public IReadOnlyList<JsonElement> Coverage { get; }

    public static EntryPassportCatalog Load(string resourceRoot, string season, string mode) =>
        new(resourceRoot, season, mode);

    private EntryPassportCatalog(string root, string season, string mode)
    {
        ArgumentException.ThrowIfNullOrWhiteSpace(root);
        if (!(season == "s0" && mode is ("nightmare" or "difficult")) &&
            !(season == "s1" && mode is ("nightmare" or "difficult" or "difficulty2")))
            throw new ArgumentException("Unsupported season/mode pair.");
        Season = season;
        Mode = mode;
        ResourceRoot = Path.GetFullPath(root);
        ModeRoot = season == "s0" ? ResourceRoot : Local(ResourceRoot, $"seasons/{season}/{mode}");
        bool legacyNightmare = season == "s0" && mode == "nightmare";
        ProfileId = legacyNightmare ? "zhan10-speed" : "shared-structure";
        string entryRelative, packRelative;
        JsonElement interfaces = default;
        Dictionary<string, JsonElement>? declaredFiles = null;
        if (legacyNightmare)
        {
            packRelative = "references/nightmare-map-pack.json";
            entryRelative = "references/nightmare-entries";
            ExitIconPath = Local(ModeRoot, "references/difficult-exit-icon.json");
        }
        else
        {
            var manifest = Read(Local(ModeRoot, season == "s1" ? "native-map-manifest.json" : "difficult-native-manifest.json"));
            interfaces = manifest.GetProperty("runtimeInterfaces");
            packRelative = String(interfaces, "identityPack");
            entryRelative = String(interfaces, "identityEntries");
            if (season == "s1")
            {
                if (manifest.GetProperty("schemaVersion").GetInt32() != 1 || String(manifest, "season") != season ||
                    String(manifest, "mode") != mode || manifest.GetProperty("capabilities").GetProperty("sideEntries").GetBoolean())
                    throw new InvalidDataException("Season entry inventory context/capability mismatch.");
                // S1 declares bytes and hashes for the consumed identity resources.
                declaredFiles = manifest.GetProperty("files").EnumerateArray().ToDictionary(
                    row => Local(ModeRoot, String(row, "path")), row => row, StringComparer.OrdinalIgnoreCase);
            }
            else ExitIconPath = Local(ModeRoot, String(interfaces, "exitIcon"));
        }
        EntryRoot = Local(ModeRoot, entryRelative);
        var pack = Read(Local(ModeRoot, packRelative), declaredFiles);
        if ((!legacyNightmare && pack.GetProperty("schemaVersion").GetInt32() != 1) ||
            (!legacyNightmare && String(pack, "mode") != mode) || (season == "s1" && String(pack, "season") != season))
            throw new InvalidDataException("Identity pack context mismatch.");
        Floors = Array.AsReadOnly(legacyNightmare ? ["1","2","B1"] :
            pack.GetProperty("floors").EnumerateArray().Select(x => x.ToString()).ToArray());
        if (season == "s1" && !Floors.SequenceEqual(["-1","-2","-3","-4"]))
            throw new InvalidDataException("S1 native floor registration mismatch.");
        var profiles = new Dictionary<string,string>(StringComparer.Ordinal);
        foreach (var map in pack.GetProperty("maps").EnumerateArray())
        {
            string profile = legacyNightmare && !map.TryGetProperty("identityProfileId", out _) ? ProfileId : String(map, "identityProfileId");
            profiles.Add(String(map,"id"), profile);
        }
        MapProfiles = profiles.ToFrozenDictionary(StringComparer.Ordinal);
        Maps = Array.AsReadOnly(pack.GetProperty("maps").EnumerateArray()
            .Select(map => new EntryMapDefinition(String(map,"id"), map.TryGetProperty("name", out var name) ? name.GetString() ?? String(map,"id") : String(map,"id"))).ToArray());
        PackFloors = pack.GetProperty("maps").EnumerateArray().SelectMany(map =>
            map.GetProperty("layers").EnumerateObject().Select(layer => (String(map, "id"), layer.Name))).ToFrozenSet();
        UnavailablePackFloors = pack.GetProperty("maps").EnumerateArray().SelectMany(map =>
            map.GetProperty("layers").EnumerateObject().Where(layer => layer.Value.ValueKind == JsonValueKind.Null)
                .Select(layer => (String(map, "id"), layer.Name))).ToFrozenSet();
        var main = Read(Local(EntryRoot, "entries.json"), declaredFiles);
        Header(main, Mode, ProfileId);
        MainEntries = Array.AsReadOnly(Rows(main,"entries"));
        var side = Read(Local(EntryRoot, "side-entries.json"), declaredFiles);
        Header(side, Mode, ProfileId);
        SemanticGaps = side.GetProperty("semanticGaps");
        var sides = Rows(side, "entries");
        foreach (var row in MainEntries.Concat(sides))
        {
            if (!profiles.TryGetValue(String(row,"mapId"), out var expected) ||
                String(row,"profileId") != expected || !Floors.Contains(String(row,"floor")))
                throw new InvalidDataException("Entry is outside its map/floor/profile registration.");
        }
        foreach (var row in sides)
        {
            if (!(row.GetProperty("unit").GetDouble() > 0)) throw new InvalidDataException("Invalid side graph unit.");
            foreach (var turn in row.GetProperty("turns").EnumerateArray())
            {
                Vector(turn.GetProperty("point"));
                var rays = turn.GetProperty("rays");
                if (rays.GetArrayLength() != 2) throw new InvalidDataException("Invalid side graph ray pair.");
                Vector(rays[0]);
                Vector(rays[1]);
                _ = turn.GetProperty("next").GetInt32();
            }
        }
        SideEntries = Array.AsReadOnly(sides);
        RegisteredFloors = sides.Select(row => (String(row,"mapId"),String(row,"floor"))).ToFrozenSet();
        var passports = new List<JsonElement>();
        var coverage = new List<JsonElement>();
        string nativePath = Local(EntryRoot, "entry-passports.json");
        // A missing native passport is a recorded capability gap. S1's hash inventory is independently mandatory.
        byte[]? declaredPassport = null;
        if (declaredFiles != null)
        {
            declaredPassport = File.ReadAllBytes(nativePath);
            Verify(nativePath, declaredFiles, declaredPassport);
        }
        try
        {
            var native = declaredPassport == null ? Read(nativePath) : Parse(declaredPassport);
            Header(native, Mode, ProfileId);
            AppendInventory(native, null, null);
        }
        catch (Exception error) when (error is IOException or UnauthorizedAccessException or JsonException or
            InvalidDataException or InvalidOperationException or KeyNotFoundException or FormatException)
        {
            NativePassportError = error.Message;
        }
        if (season == "s0" && mode == "difficult" && interfaces.TryGetProperty("legacyClean", out var legacy)
            && legacy.ValueKind == JsonValueKind.Object)
        {
            LegacyPassportSource = Local(ModeRoot, String(legacy, "passports"));
            string legacyProfile = String(legacy, "profileId");
            var extra = Read(LegacyPassportSource);
            Header(extra, Mode, legacyProfile);
            var allowed = legacy.GetProperty("mapIds").EnumerateArray().Select(v => v.GetString()!).ToHashSet(StringComparer.Ordinal);
            AppendInventory(extra, allowed, legacyProfile);
        }
        Passports = passports.AsReadOnly();
        Coverage = coverage.AsReadOnly();

        void AppendInventory(JsonElement doc, HashSet<string>? allowed, string? requiredProfile)
        {
            var entries = Rows(doc, "entries");
            var declarations = Rows(doc, "coverage");
            foreach (var row in entries.Concat(declarations))
            {
                string id = String(row, "mapId");
                if (!profiles.TryGetValue(id, out var profile) || !Floors.Contains(String(row,"floor")) ||
                    allowed != null && !allowed.Contains(id) || requiredProfile != null && requiredProfile != profile)
                    throw new InvalidDataException("Entrance passport expands its declared map/floor/profile scope.");
            }
            foreach (var row in declarations) _ = row.GetProperty("complete").GetBoolean();
            passports.AddRange(entries);
            coverage.AddRange(declarations);
        }
    }

    private static void Header(JsonElement doc, string mode, string profile)
    {
        if (doc.GetProperty("schemaVersion").GetInt32() != 1 || String(doc,"mode") != mode || String(doc,"profileId") != profile)
            throw new InvalidDataException("Entry resource mode/profile mismatch.");
    }

    private static void Vector(JsonElement item)
    {
        if (item.GetArrayLength() != 2 || !double.IsFinite(item[0].GetDouble()) || !double.IsFinite(item[1].GetDouble()))
            throw new InvalidDataException("Invalid side graph vector.");
    }

    private static JsonElement[] Rows(JsonElement doc, string key) => doc.GetProperty(key).EnumerateArray().ToArray();
    private static string String(JsonElement obj, string key) => obj.GetProperty(key).GetString() ??
        throw new InvalidDataException($"Expected string {key}.");
    private static string Local(string root, string relative)
    {
        var parts = relative.Replace('\\','/').Split('/');
        if (string.IsNullOrWhiteSpace(relative) || Path.IsPathRooted(relative) || parts.Any(part => part is ".." or "." or "" || part.Contains(':')))
            throw new InvalidDataException($"Resource path is outside its declared root: {relative}");
        var current = root;
        foreach (string part in parts)
        {
            current = Path.Combine(current, part);
            // Each child resolves against its mode root, including existing file and directory links.
            FileSystemInfo info = Directory.Exists(current) ? new DirectoryInfo(current) : new FileInfo(current);
            if (info.LinkTarget != null && info.ResolveLinkTarget(true) is { } target)
            {
                current = target.FullName;
                string fromRoot = Path.GetRelativePath(root, current);
                if (fromRoot == ".." || fromRoot.StartsWith(".." + Path.DirectorySeparatorChar) || Path.IsPathRooted(fromRoot))
                    throw new InvalidDataException($"Resource link escapes its declared root: {relative}");
            }
        }
        return current;
    }

    private static JsonElement Read(string path, Dictionary<string, JsonElement>? declarations = null)
    {
        byte[] bytes = File.ReadAllBytes(path);
        if (declarations != null) Verify(path, declarations, bytes);
        return Parse(bytes);
    }

    private static JsonElement Parse(byte[] bytes)
    {
        using var doc = JsonDocument.Parse(bytes);
        return doc.RootElement.Clone();
    }

    private static void Verify(string path, Dictionary<string, JsonElement> declarations, byte[] bytes)
    {
        if (!declarations.TryGetValue(path, out var declaration) ||
            bytes.LongLength != declaration.GetProperty("bytes").GetInt64() ||
            !String(declaration, "sha256").Equals(Convert.ToHexString(SHA256.HashData(bytes)), StringComparison.OrdinalIgnoreCase))
            throw new InvalidDataException($"S1 identity resource differs from its file inventory: {path}");
    }
}
