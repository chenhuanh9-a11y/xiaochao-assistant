using System.IO.Compression;
using System.Security.Cryptography;
using System.Text.Json;
using System.Text.Json.Nodes;
using StarMap.Core.Resources;
using static StarMap.Core.Identity.NativeIdentityJson;

namespace StarMap.Core.Identity;

public readonly record struct NativeSamplePoint(int X, int Y);
public sealed class NativeConditionFeature(JsonElement row, JsonElement declaration, (double X, double Y)[] points, HashSet<int> main)
{
    public JsonElement Row { get; } = row;
    public JsonElement Declaration { get; } = declaration;
    public int Count => points.Length;
    public (double X, double Y) Point(int index) => points[index];
    public bool IsMain(int index) => main.Contains(index);
}

/// <summary>The existing schema-2 native condition declarations and their real typed resource bindings.</summary>
public sealed class NativeConditionCatalog
{
    private readonly ResourceCatalog resources;
    private readonly EntryPassportCatalog entries;
    private readonly Dictionary<string, JsonObject> references = new(StringComparer.Ordinal), domains = new(StringComparer.Ordinal), poses = new(StringComparer.Ordinal), conditions = new(StringComparer.Ordinal);
    private readonly Dictionary<string, List<JsonObject>> comparisons = new(StringComparer.Ordinal);
    private readonly Dictionary<(string, string), NativeConditionFeature> features = new();
    private readonly Dictionary<string, JsonElement> featureFiles = new(StringComparer.Ordinal);
    private readonly Dictionary<string, (long Length, long Ticks, string Hash)> digests = new(StringComparer.OrdinalIgnoreCase);
    private readonly Dictionary<string, (JsonElement Metadata, int[] Shape)> referenceBindings = new(StringComparer.Ordinal);
    private readonly Dictionary<int, IReadOnlyList<NativeSamplePoint>> sampleViews = new();
    private JsonObject sampleSpec = new();
    private NpyArray? points, offsets;
    private readonly string indexPath;
    public string Mode => entries.Mode;
    public string Season => entries.Season;
    public string ResourceKey { get; } = "";
    public string? Error { get; }
    public IReadOnlyList<string> ReferenceIds => references.Keys.ToArray();
    public IReadOnlyList<string> DomainIds => domains.Keys.ToArray();
    public IReadOnlyList<string> PoseIds => poses.Keys.ToArray();
    public IReadOnlyList<string> ConditionIds => conditions.Keys.ToArray();
    public JsonObject Reference(string id) => references[id].DeepClone().AsObject();
    public JsonObject Domain(string id) => domains[id].DeepClone().AsObject();
    public JsonObject Pose(string id) => poses[id].DeepClone().AsObject();
    public JsonObject Condition(string id) => conditions[id].DeepClone().AsObject();
    public bool HasReference(string id) => references.ContainsKey(id);
    public bool HasDomain(string id) => domains.ContainsKey(id);
    public bool HasCondition(string id) => conditions.ContainsKey(id);
    public IReadOnlyList<string> ConditionsFor(string domain) => conditions.Where(pair => Text(pair.Value,"domainId") == domain).Select(pair => pair.Key).ToArray();
    public IReadOnlyList<JsonObject> ComparisonsFor(string condition) => comparisons.GetValueOrDefault(condition)?.Select(row => row.DeepClone().AsObject()).ToArray() ?? [];

    public NativeConditionCatalog(ResourceCatalog resources, EntryPassportCatalog entries)
    {
        this.resources = resources; this.entries = entries;
        if (entries.Season != "s0" || !string.Equals(Path.TrimEndingDirectorySeparator(resources.RootPath), Path.TrimEndingDirectorySeparator(entries.ResourceRoot), StringComparison.OrdinalIgnoreCase)) throw new ArgumentException("Native conditions require the same S0 resource root.");
        string compressed = Local("references/native-similarity-groups.json.gz");
        indexPath = File.Exists(compressed) ? compressed : Local("references/native-similarity-groups.json");
        try
        {
            byte[] bytes = File.ReadAllBytes(indexPath); JsonObject payload;
            if (indexPath.EndsWith(".gz", StringComparison.Ordinal))
            { using var input = new MemoryStream(bytes); using var gzip = new GZipStream(input, CompressionMode.Decompress); using var reader = new StreamReader(gzip); payload = JsonNode.Parse(reader.ReadToEnd())!.AsObject(); }
            else payload = JsonNode.Parse(bytes)!.AsObject();
            ResourceKey = Hash(indexPath);
            if (Number(payload["schemaVersion"]) != 2 || Text(payload,"resourceKind") != "native-similarity-conditions") throw new InvalidDataException("similarity-index-schema");
            foreach (var node in Array(payload["references"]))
            {
                if (Text(node,"mode") != Mode) continue;
                var row = node!.DeepClone().AsObject(); row["profile"] = row["binding"]!["profile"]!.DeepClone();
                if (!entries.MapProfiles.ContainsKey(Text(row,"mapId"))) throw new InvalidDataException("similarity-reference-outside-pack");
                references.Add(Text(row,"referenceId"), row);
            }
            foreach (var node in Array(payload["domains"])) if (references.ContainsKey(Text(node,"source")) && Text(node,"kind") == "front") domains.Add(Text(node,"domainId"), node!.DeepClone().AsObject());
            foreach (var node in Array(payload["poses"])) if (domains.ContainsKey(Text(node,"domainId")))
            {
                var row = node!.DeepClone().AsObject(); string rid = Text(row,"target"), did = Text(row,"domainId");
                if (Text(references[rid],"floor") != Text(references[Text(domains[did],"source")],"floor")) throw new InvalidDataException("similarity-cross-floor-pose");
                if (!FiniteMatrix(row["targetReferenceToCondition"])) throw new InvalidDataException("similarity-local-reference-matrix");
                var matrix = Matrix(row["targetReferenceToCondition"]!);
                if (matrix[0][0] <= 0 || matrix[0][0] != matrix[1][1] || matrix[0][1] != 0 || matrix[1][0] != 0 || matrix[2][0] != 0 || matrix[2][1] != 0 || matrix[2][2] != 1) throw new InvalidDataException("similarity-local-reference-matrix");
                poses.Add(Text(row,"poseId"), row); _ = Feature(rid,"front");
            }
            foreach (var node in Array(payload["conditions"])) if (domains.ContainsKey(Text(node,"domainId")))
            {
                var row = node!.DeepClone().AsObject();
                if (Text(row,"mode") != Mode) throw new InvalidDataException("similarity-condition-mode");
                if (Strings(row["poseIds"]).Any(id => !poses.ContainsKey(id))) throw new InvalidDataException("similarity-condition-pose-missing");
                conditions.Add(Text(row,"conditionId"), row);
            }
            foreach (var node in Array(payload["comparisons"]))
            {
                string id = Text(node,"conditionId"); if (!conditions.ContainsKey(id)) continue;
                var row = node!.DeepClone().AsObject(); var bounds = Array(row["observationBounds"]);
                if (Text(row,"differenceScope") != "post-entry-positive-structure" || bounds.Count != 4 || bounds.Any(value => !double.IsFinite(Number(value))) ||
                    Number(bounds[2]) < Number(bounds[0]) || Number(bounds[3]) < Number(bounds[1]) || Number(row["minimumPositiveDistancePixels"]) != 3 ||
                    Text(row,"distanceEvaluationSpace") != "original-source-and-target-positive-pixel-centers" || !Flag(row,"persistedSampleDistanceChecked")) throw new InvalidDataException("similarity-condition-requires-post-entry-comparison-domain");
                if (!poses.ContainsKey(Text(row,"poseA")) || !poses.ContainsKey(Text(row,"poseB"))) throw new InvalidDataException("similarity-comparison-pose-missing");
                if (!comparisons.TryGetValue(id,out var list)) comparisons.Add(id,list=[]); list.Add(row);
            }
            sampleSpec = payload["samples"]!.DeepClone().AsObject();
        }
        catch (Exception error) when (error is IOException or JsonException or ArgumentException or InvalidOperationException or KeyNotFoundException)
        { Error = error.Message; }
    }

    public NativeConditionFeature Feature(string referenceId, string kind)
    {
        if (features.TryGetValue((referenceId,kind),out var cached)) return cached;
        var reference = references[referenceId]; var declared = reference["features"]![kind]!;
        string path = Local(Text(declared,"path")); RequireHash(path,Text(declared,"sha256"),"similarity-feature-changed");
        if (!featureFiles.TryGetValue(path,out var payload))
        { using var doc = JsonDocument.Parse(File.ReadAllBytes(path)); payload = doc.RootElement.Clone(); featureFiles.Add(path,payload); }
        int index = (int)Number(declared["rowIndex"]); var rows = payload.GetProperty("entries");
        if (index < 0 || index >= rows.GetArrayLength()) throw new InvalidDataException("similarity-feature-row-index");
        var row = rows[index];
        if (row.GetProperty("mapId").GetString() != Text(reference,"mapId") || row.GetProperty("floor").ToString() != Text(reference,"floor") || row.GetProperty("unit").GetDouble() != Number(declared["unit"])) throw new InvalidDataException("similarity-feature-binding-changed");
        (double X,double Y)[] xy = kind == "front" ? row.GetProperty("chains").EnumerateArray().Select(chain => (chain.GetProperty("points")[1][0].GetDouble(),chain.GetProperty("points")[1][1].GetDouble())).ToArray()
            : row.GetProperty("turns").EnumerateArray().Select(turn => (turn.GetProperty("point")[0].GetDouble(),turn.GetProperty("point")[1].GetDouble())).ToArray();
        var main = new HashSet<int>();
        if (kind == "front")
        {
            var outline = row.GetProperty("outline").EnumerateArray().Select(point => (X:point[0].GetDouble(),Y:point[1].GetDouble())).ToArray();
            for (int i=0;i<xy.Length;i++) if (outline.Any(point => Math.Sqrt(Math.Pow(point.X-xy[i].X,2)+Math.Pow(point.Y-xy[i].Y,2)) <= 3)) main.Add(i);
        }
        cached = new(row.Clone(), JsonSerializer.SerializeToElement(declared), xy, main); features.Add((referenceId,kind),cached); return cached;
    }

    public void ValidateDomains(IEnumerable<string> domainIds)
    {
        RequireHash(indexPath,ResourceKey,"similarity-index-changed");
        foreach (string did in domainIds)
        {
            if (!domains.ContainsKey(did)) throw new InvalidDataException("similarity-domain-changed");
            foreach (string rid in poses.Values.Where(p => Text(p,"domainId") == did).Select(p => Text(p,"target")).Distinct(StringComparer.Ordinal))
            {
                var reference = references[rid]; string path = Local(Text(reference,"resourcePath"));
                var entry = resources.Find(Mode,Season,Text(reference,"profile"),Text(reference,"mapId"),Text(reference,"floor"));
                if (entry is null || !string.Equals(entry.StructureNpzPath,path,StringComparison.OrdinalIgnoreCase)) throw new InvalidDataException("similarity-member-binding-changed");
                RequireHash(path,Text(reference,"resourceSha256"),"similarity-member-binding-changed");
                if (!referenceBindings.TryGetValue(path,out var actual))
                {
                    // Legacy map29 NPZs have no embedded metadata. The same
                    // typed production provider supplies their real external
                    // binding and mask shape, just as the original caller did.
                    var loaded=resources.LoadSharedStructure(entry).Value ?? throw new InvalidDataException("similarity-member-binding-changed");
                    actual=(loaded.Metadata.Clone(),loaded.ValidationMask.Shape.ToArray()); referenceBindings.Add(path,actual);
                }
                var meta=actual.Metadata;
                foreach (string field in new[] {"binding","structureVersionKey","sourceVersion","baseSnapshot"})
                    if (!meta.TryGetProperty(field,out var value) || !JsonNode.DeepEquals(JsonNode.Parse(value.GetRawText()),reference[field])) throw new InvalidDataException("similarity-member-binding-changed");
                if (!JsonNode.DeepEquals(new JsonArray(actual.Shape.Select(value=>(JsonNode)JsonValue.Create(value)!).ToArray()),reference["shapeHW"])) throw new InvalidDataException("similarity-member-binding-changed");
                foreach (var feature in reference["features"]!.AsObject()) RequireHash(Local(Text(feature.Value,"path")),Text(feature.Value,"sha256"),"similarity-feature-changed");
            }
        }
        RequireHash(Local(Text(sampleSpec,"resourcePath")),Text(sampleSpec,"sha256"),"similarity-samples-changed");
    }

    public IReadOnlyList<NativeSamplePoint> Samples(int id)
    {
        string path = Local(Text(sampleSpec,"resourcePath")); RequireHash(path,Text(sampleSpec,"sha256"),"similarity-samples-changed");
        if (sampleViews.TryGetValue(id,out var cached)) return cached;
        if (points is null || offsets is null)
        {
            // This is a pooled point collection, not an image dimension. The
            // shipped collection has 316416 rows; retain the normal byte bounds
            // and derive its row limit from the two int32 values per point.
            var limits = new NpyReadLimits();
            limits = limits with { MaxRank = 2, MaxDimension = checked((int)(limits.MaxArrayBytes / (2 * sizeof(int)))) };
            using var archive = NpzArchive.Open(path, limits);
            var loadedPoints = archive.ReadArray(Text(sampleSpec,"pointsArray"));
            var loadedOffsets = archive.ReadArray(Text(sampleSpec,"offsetsArray"));
            if (loadedPoints.Rank != 2 || loadedPoints.Shape[1] != 2 || loadedPoints.Dtype is not ("<i4" or "=i4") || loadedOffsets.Rank != 1 || loadedOffsets.Dtype is not ("<i8" or "=i8") || loadedOffsets.Shape[0]<2 || loadedOffsets.ReadNumericValue(0)!=0 || loadedOffsets.ReadNumericValue(loadedOffsets.Shape[0]-1)!=loadedPoints.Shape[0]) throw new InvalidDataException("similarity-sample-arrays");
            for (int i=1;i<loadedOffsets.Shape[0];i++) if (loadedOffsets.ReadNumericValue(i)<loadedOffsets.ReadNumericValue(i-1)) throw new InvalidDataException("similarity-sample-arrays");
            points = loadedPoints; offsets = loadedOffsets;
        }
        var pointArray=points ?? throw new InvalidDataException("similarity-sample-arrays");
        var offsetArray=offsets ?? throw new InvalidDataException("similarity-sample-arrays");
        if (id<0 || id+1>=offsetArray.Shape[0]) throw new InvalidDataException("similarity-sample-id");
        int start=checked((int)offsetArray.ReadNumericValue(id)),end=checked((int)offsetArray.ReadNumericValue(id+1));
        if (end-start>512) throw new InvalidDataException("similarity-sample-count");
        cached=System.Array.AsReadOnly(Enumerable.Range(start,end-start).Select(i=>new NativeSamplePoint(checked((int)pointArray.ReadNumericValue(i*2)),checked((int)pointArray.ReadNumericValue(i*2+1)))).ToArray());
        sampleViews.Add(id,cached); return cached;
    }
    private string Local(string relative)
    {
        string path=Path.GetFullPath(Path.Combine(entries.ModeRoot,relative));
        if (Path.IsPathRooted(relative) || !path.StartsWith(Path.GetFullPath(entries.ModeRoot)+Path.DirectorySeparatorChar,StringComparison.OrdinalIgnoreCase)) throw new InvalidDataException("similarity-path-outside-root"); return path;
    }
    private string Hash(string path)
    {
        var info=new FileInfo(path); if (digests.TryGetValue(path,out var cached) && cached.Length==info.Length && cached.Ticks==info.LastWriteTimeUtc.Ticks) return cached.Hash;
        string digest=Convert.ToHexString(SHA256.HashData(File.ReadAllBytes(path))).ToLowerInvariant(); digests[path]=(info.Length,info.LastWriteTimeUtc.Ticks,digest); return digest;
    }
    private void RequireHash(string path,string expected,string reason) { if (Hash(path)!=expected) throw new InvalidDataException(reason); }
}
