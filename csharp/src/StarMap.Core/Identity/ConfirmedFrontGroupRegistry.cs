using System.Security.Cryptography;
using System.Text;
using System.Text.Json;
using System.Text.Json.Nodes;
using StarMap.Core.Resources;
using static StarMap.Core.Identity.NativeIdentityJson;

namespace StarMap.Core.Identity;

/// <summary>The existing user-confirmed S0/F1/front registrations, never a dynamic candidate cohort.</summary>
public sealed class ConfirmedFrontGroupRegistry
{
    private readonly EntryPassportCatalog catalog;
    private readonly ResourceCatalog resources;
    private readonly JsonArray groups = new();
    public string ResourceKey { get; }
    public ConfirmedFrontGroupRegistry(ResourceCatalog resources, EntryPassportCatalog catalog)
    {
        this.resources=resources; this.catalog=catalog;
        string[][] memberships = catalog.Season!="s0" ? [] : catalog.Mode switch
        {
            "nightmare" => [["04","12","19"],["03","09"],["07","08","20"]],
            "difficult" => [["08","25"],["03","10"],["04","21"],["20","26"],["05","09-2"]],
            _ => []
        };
        string prefix=catalog.Mode=="nightmare"?"nightmare-":"difficult-map-";
        foreach (var numbers in memberships) groups.Add(new JsonObject { ["groupId"]=$"confirmed-front:{catalog.Mode}:"+string.Join('-',numbers),
            ["mode"]=catalog.Mode,["entranceKind"]="front",["floor"]="1",["memberIds"]=Strings(numbers.Select(number=>prefix+number)) });
        ResourceKey=Convert.ToHexString(SHA256.HashData(Encoding.UTF8.GetBytes(PythonJson(groups)))).ToLowerInvariant();
    }
    public JsonObject Resolve(string? mapId,string floor,string entranceKind,IEnumerable<string>? requiredMembers=null)
    {
        if (catalog.Season!="s0" || floor!="1" || entranceKind!="front") return new() { ["status"]="none",["reason"]="outside-reviewed-front-entry-scope" };
        var group=mapId is null?null:groups.FirstOrDefault(row=>Strings(row?["memberIds"]).Contains(mapId,StringComparer.Ordinal))?.AsObject();
        if (group is null) return new() { ["status"]="none" };
        try
        {
            var members=Strings(group["memberIds"]).Order(StringComparer.Ordinal).ToArray();
            if (members.Length<2 || members.Any(map=>!catalog.MapProfiles.ContainsKey(map))) throw new InvalidDataException("similarity-registration-members");
            if (requiredMembers is not null && requiredMembers.Except(members,StringComparer.Ordinal).Any()) throw new InvalidDataException("similarity-registration-observation-mismatch");
            foreach(string map in members)
            {
                var entry=resources.Find(catalog.Mode,catalog.Season,catalog.MapProfiles[map],map,"1");
                var reference=entry is null?null:resources.LoadSharedStructure(entry).Value;
                if (reference is null || !reference.Metadata.TryGetProperty("binding",out var binding) || binding.GetProperty("mapId").GetString()!=map ||
                    binding.GetProperty("floor").ToString()!="1" || !reference.Metadata.TryGetProperty("structureVersionKey",out var key) || string.IsNullOrEmpty(key.GetString()))
                    throw new InvalidDataException("confirmed-front-group-binding-unavailable:"+map+"/1");
            }
            return new() { ["status"]="group",["candidates"]=new JsonArray(new JsonObject { ["groupId"]=group["groupId"]!.DeepClone(),["memberIds"]=Strings(members),["resourceKey"]=ResourceKey }) };
        }
        catch(Exception error) when(error is IOException or ArgumentException or KeyNotFoundException or InvalidOperationException)
        { return new() { ["status"]="unavailable",["reason"]=error.Message }; }
    }
    private static string PythonJson(JsonNode? value) => value switch
    {
        JsonArray array => "["+string.Join(", ",array.Select(PythonJson))+"]",
        JsonObject row => "{"+string.Join(", ",row.OrderBy(pair=>pair.Key,StringComparer.Ordinal).Select(pair=>JsonSerializer.Serialize(pair.Key)+": "+PythonJson(pair.Value)))+"}",
        null => "null", _ => value.ToJsonString()
    };
}
