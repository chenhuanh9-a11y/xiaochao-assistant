using System.Security.Cryptography;
using System.Text.Json;
using System.Text.RegularExpressions;
using StarMap.Core.Resources;

namespace StarMap.Core.Display;

internal sealed partial class DisplayAuthorInventory
{
    internal sealed record Layer(string Map,string Floor,string Variant,string Path,JsonElement? Metadata,string Name="");
    internal sealed record Profile(string Id,bool RuntimeEnabled,bool Aligned,List<Layer> Layers);
    internal sealed record Registered(string Path,DisplayAffine Matrix,bool Usable,bool AllowConversion,string Status,JsonElement Declaration);
    internal readonly Dictionary<string,Profile> Profiles=new(StringComparer.Ordinal);
    internal readonly HashSet<string> OptionalProfiles=new(StringComparer.Ordinal);
    internal readonly Dictionary<string,string> Errors=new(StringComparer.Ordinal);
    internal readonly Dictionary<(string Profile,string Map,string Floor),Registered> Bindings=[];
    internal string? AuthorError{get;private set;}
    internal JsonElement? Version{get;private set;}
    internal AuthorCoordinateAdapter CoordinateAdapter{get;private set;}=null!;
    internal static readonly string[] NightmareOptional=["zhan10-nightmare","suju-speed-nightmare","wang-qingxin","nightingale","lianghapi-single"];
    internal static readonly string[] DifficultOptional=["suju-speed-difficult","aichicu-difficult"];
    internal static bool Generic(string profile)=>Regex.IsMatch(profile,"^author-[a-z0-9](?:[a-z0-9-]{0,46}[a-z0-9])?$");
    internal static bool IsCoffin(ResourceCatalogEntry e,string profile)=>e.Season=="s0"&&e.Mode=="nightmare"&&profile=="zhan10-coffin"&&e.MapId=="nightmare-01"&&e.Floor=="1";
    internal Registered? Binding(string profile,string map,string floor)=>Bindings.GetValueOrDefault((profile,map,floor));
    internal Layer? FindLayer(string profile,string map,string floor,string? variant=null)
    {
        if(!Profiles.TryGetValue(profile,out var p))return null;
        var variants=p.Layers.Where(l=>l.Map==map).Select(l=>l.Variant).Distinct().ToArray();
        string? selected=variant??variants.FirstOrDefault();
        var row=p.Layers.FirstOrDefault(l=>l.Map==map&&l.Floor==floor&&l.Variant==selected);
        return row is not null&&File.Exists(row.Path)?row:null;
    }
    internal bool Declared(string profile,string map,string floor)=>Profiles.TryGetValue(profile,out var p)&&p.Layers.Any(l=>l.Map==map&&l.Floor==floor);
    internal static DisplayAuthorInventory Load(ResourceCatalog catalog,ResourceCatalogEntry target,DisplayBridgeResolver.ResourceReads files,IDisplayRasterValidator raster)
    {
        var result=new DisplayAuthorInventory();var context=DisplayBridgeResolver.ReadContext(files,target);
        JsonElement? rawCatalog=null;
        try {rawCatalog=files.Json(context.Root,context.AuthorCatalog);var parsed=ReadCatalog(rawCatalog.Value,context.Root,files,files.Local(context.Root,context.AuthorCatalog)).ToArray();foreach(var p in parsed)result.Profiles.Add(p.Id,p);}
        catch(Exception error)when(ReadError(error)){rawCatalog=null;result.Profiles.Clear();result.Errors["base-catalog"]=error.Message;}
        var coordinate=files.Json(context.Root,context.Coordinates);var adapter=new AuthorCoordinateAdapter(coordinate);result.CoordinateAdapter=adapter;
        var index=files.Json(context.Root,context.Index);
        string? versionsPath=J.String(index,"structureVersionBindings")??J.String(J.Get(context.Manifest,"runtimeInterfaces"),"versionBindings");
        JsonElement? versions=null;
        if(versionsPath is not null)
        {
            versions=files.Json(context.Root,versionsPath);
            result.Version=Rows(J.Get(versions,"entries")).SingleOrDefault(r=>J.String(r,"mapId")==target.MapId
                &&(J.String(r,"floor")??J.String(J.Get(r,"resourceKey"),"floor"))==target.Floor);
        }
        string originalAuthor=target.Mode=="difficult"?"zhan10-speed":"suju";
        var version=result.Version;var resourceKey=J.Get(version,"resourceKey");var author=J.Get(version,"authorDisplay");
        var baseProfile=result.Profiles.GetValueOrDefault(originalAuthor);
        var authorLayer=baseProfile?.Layers.FirstOrDefault(l=>l.Map==target.MapId&&l.Floor==target.Floor
            &&PathEquals(l.Path,J.String(author,"resource") is { } r?files.Local(context.Root,r):null));
        var authorRaw=Rows(J.Get(rawCatalog,"profiles")).FirstOrDefault(p=>J.String(p,"id")==originalAuthor);
        var authorVariant=Rows(J.Get(J.Get(authorRaw,"maps"),target.MapId)).FirstOrDefault(v=>J.String(J.Get(v,"layers"),target.Floor)==J.String(author,"resource"));
        result.AuthorError=version is not {ValueKind:JsonValueKind.Object}?null:rawCatalog is null?"author-not-installed":
            !J.Equal(J.Get(author,"baseResourceKey"),resourceKey)
            ||J.String(authorLayer?.Metadata,"structureVersionKey")!=target.StructureVersionKey
            ||J.String(adapter.Floor(originalAuthor,target.MapId,target.Floor),"structureVersionKey")!=target.StructureVersionKey
            ||J.String(authorVariant,"baseSnapshot")!=J.String(author,"originalBaseSnapshot")
                ?"author-structure-version-or-base-snapshot-mismatch":null;
        if(target.Season=="s0")
            foreach(string profile in target.Mode=="nightmare"?NightmareOptional:DifficultOptional)
            {
                string folder=$"references/author-packs/{profile}";
                if(!File.Exists(files.Local(catalog.RootPath,folder+"/catalog.json")))continue;
                try {result.LoadOptional(catalog,target,profile,folder,adapter,files);}
                catch(Exception error)when(ReadError(error)){result.Errors[profile]=error.Message;}
            }
        result.LoadInstalledPackages(catalog,target,files,raster);
        return result;
    }
    private void LoadOptional(ResourceCatalog catalog,ResourceCatalogEntry target,string profile,string folder,AuthorCoordinateAdapter adapter,DisplayBridgeResolver.ResourceReads files)
    {
        var raw=files.Json(catalog.RootPath,folder+"/catalog.json");var payload=files.Json(catalog.RootPath,folder+"/display-bindings.json");
        var parsed=ReadCatalog(raw,catalog.RootPath,files,files.Local(catalog.RootPath,folder+"/catalog.json")).SingleOrDefault(p=>p.Id==profile)??throw Invalid("optional-author-profile-missing");
        if(J.String(payload,"schema")!="canonical-author-display-bindings-v1"||J.String(payload,"profileId")!=profile
            ||J.String(payload,"canonicalPackId")!=adapter.PackId||J.String(payload,"coordinateReferenceProfileId")!=adapter.ReferenceProfile)
            throw Invalid("optional-author-display-context-mismatch");
        var expected=catalog.Entries.Where(e=>e.Mode==target.Mode&&e.Season==target.Season).ToDictionary(e=>(e.MapId,e.Floor));
        var declared=parsed.Layers.Select(l=>(l.Map,l.Floor)).ToHashSet();
        if(declared.Count==0||declared.Any(k=>!expected.ContainsKey(k)))throw Invalid("optional-author-catalog-has-no-current-layers");
        var rows=new Dictionary<(string Profile,string Map,string Floor),Registered>();
        foreach(var row in Rows(J.Get(payload,"entries")))
        {
            string map=Need(row,"mapId"),floor=Need(row,"floor");var key=(profile,map,floor);
            if(rows.ContainsKey(key)||!expected.TryGetValue((map,floor),out var entry))throw Invalid("optional-author-duplicate-or-foreign-binding");
            var resource=J.Get(row,"nativeResourceKey");string versionKey=$"{J.String(resource,"layoutId")}@{J.String(resource,"structureRawRecordSha256")}/{floor}";
            string path=files.Local(catalog.RootPath,Need(row,"assetPath"));
            var selected=parsed.Layers.FirstOrDefault(l=>l.Map==map&&l.Floor==floor);
            var bridge=J.Get(row,"coordinateBridge");
            if(J.String(row,"profileId")!=profile||J.String(resource,"floor")!=floor||versionKey!=entry.StructureVersionKey
                ||J.String(row,"structureVersionKey")!=versionKey||(J.String(row,"sourceVersion")??J.String(payload,"sourceVersion"))!=entry.SourceVersion
                ||!File.Exists(path)||selected is null||!PathEquals(path,selected.Path)
                ||!J.False(row,"identityAuthority")||!J.False(row,"structureValidationAuthority")||!J.True(row,"currentFrameGeometryFitRequired")
                ||!J.False(row,"crossFloorProjectionAllowed")||!J.True(bridge,"baseToNativeIsIdentity")||!J.True(bridge,"authorGeometryErrorUnchanged"))
                throw Invalid("optional-author-layer-native-binding-mismatch:"+map+"/"+floor);
            var matrix=ReadAffine(J.Get(row,"matrixAssetToCanonical"),1e-12);
            var inherited=ReadAffine(J.Get(row,"matrixAssetToBase"),1e-12);
            if(matrix!=inherited)throw Invalid("optional-author-inherited-display-matrix-invalid");
            string status=J.String(J.Get(row,"registration"),"status")??"";
            rows.Add(key,new(path,matrix,status=="confirmed",false,status,row.Clone()));
        }
        if(!rows.Keys.Select(k=>(k.Map,k.Floor)).ToHashSet().SetEquals(declared))throw Invalid("optional-author-inventory-incomplete");
        Profiles[profile]=parsed;OptionalProfiles.Add(profile);foreach(var p in rows)Bindings[p.Key]=p.Value;
    }
    private static IEnumerable<Profile> ReadCatalog(JsonElement raw,string root,DisplayBridgeResolver.ResourceReads files,string catalogPath)
    {
        if(J.Number(raw,"schemaVersion")!=1)throw Invalid("author-catalog-schema-unsupported");
        var seen=new HashSet<string>(StringComparer.Ordinal);
        foreach(var p in Rows(J.Get(raw,"profiles")))
        {
            string id=Need(p,"id");if(!seen.Add(id))throw Invalid("author-catalog-duplicate-profile");var layers=new List<Layer>();
            if(J.Get(p,"maps") is {ValueKind:JsonValueKind.Object} maps)
                foreach(var map in maps.EnumerateObject())foreach(var variant in Rows(map.Value))
                {
                    string variantId=Need(variant,"id");string resourceBase=J.String(variant,"resourceBase")??"asset-root";
                    if(resourceBase is not ("asset-root" or "resource-root"))throw Invalid("unsupported-author-resource-base");
                    if(J.Get(variant,"layers") is {ValueKind:JsonValueKind.Object} floors)
                        foreach(var floor in floors.EnumerateObject())
                        {if(floor.Value.ValueKind!=JsonValueKind.String)throw Invalid("author-catalog-layer-path-invalid");
                            string scope=resourceBase=="resource-root"?Path.GetDirectoryName(Path.GetDirectoryName(catalogPath))!:root;
                            layers.Add(new(map.Name,floor.Name,variantId,files.Local(scope,floor.Value.GetString()!),J.Get(J.Get(variant,"layerMeta"),floor.Name)?.Clone(),J.String(variant,"displayName")??""));}
                }
            yield return new(id,J.True(p,"runtimeEnabled"),J.True(p,"alignedOverlayEligible"),layers);
        }
    }
    internal static DisplayAffine ReadAffine(JsonElement? value,double minimumDeterminant=0)
    {
        if(value is not {ValueKind:JsonValueKind.Array} a||a.GetArrayLength()!=3)throw Invalid("author-display-affine-invalid");
        var rows=a.EnumerateArray().Select(r=>r.ValueKind==JsonValueKind.Array?r.EnumerateArray().ToArray():[]).ToArray();
        if(rows.Any(r=>r.Length!=3))throw Invalid("author-display-affine-invalid");var v=new double[9];int i=0;
        foreach(var r in rows)foreach(var n in r){if(n.ValueKind!=JsonValueKind.Number||!n.TryGetDouble(out v[i])||!double.IsFinite(v[i]))throw Invalid("author-display-affine-invalid");i++;}
        var result=new DisplayAffine(v[0],v[1],v[2],v[3],v[4],v[5]);
        if(v[6]!=0||v[7]!=0||v[8]!=1||!double.IsFinite(result.Determinant)||Math.Abs(result.Determinant)<=minimumDeterminant)
            throw Invalid("author-display-affine-invalid");return result;
    }
    internal static JsonElement[] Rows(JsonElement? value)=>value is {ValueKind:JsonValueKind.Array} a?a.EnumerateArray().ToArray():[];
    internal static string Need(JsonElement? row,string key)=>J.String(row,key)??throw Invalid("author-declaration-field-missing:"+key);
    internal static bool PathEquals(string? a,string? b)=>a is not null&&b is not null&&string.Equals(Path.GetFullPath(a),Path.GetFullPath(b),StringComparison.OrdinalIgnoreCase);
    internal static bool ReadError(Exception e)=>e is IOException or UnauthorizedAccessException or InvalidDataException or JsonException or ArgumentException or InvalidOperationException or OverflowException;
    internal static InvalidDataException Invalid(string reason)=>new(reason);
}
