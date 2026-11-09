using System.Text.Json;
using StarMap.Core.Resources;

namespace StarMap.Core.Display;

internal sealed partial class DisplayAuthorInventory
{
    private sealed record AuthorBase(JsonElement Metadata,JsonElement Canvas,string Reference,string? NativeImage,string[] Dependencies);
    /// <summary>Port of generic_author_base._resolve: trusted base sources choose every dependency.</summary>
    private static AuthorBase ResolveAuthorBase(ResourceCatalog catalog,string season,string mode,string map,string floor,DisplayBridgeResolver.ResourceReads files)
    {
        var entry=catalog.Entries.SingleOrDefault(e=>e.Season==season&&e.Mode==mode&&e.MapId==map&&e.Floor==floor)
            ??throw Invalid("author-v2-base-layer-unavailable");
        if(entry.StructureKind!="shared-structure")throw Invalid("author-v2-base-layer-unavailable");
        var context=DisplayBridgeResolver.ReadContext(files,entry);
        var dependencies=new HashSet<string>(StringComparer.Ordinal);string prefix=season=="s0"?"_internal/":$"_internal/seasons/{season}/{mode}/";
        JsonElement Read(string relative){dependencies.Add(prefix+relative);return files.Json(context.Root,relative);}
        string sourceRelative;
        if(season=="s1")
        {
            dependencies.Add(prefix+"native-map-manifest.json");
            sourceRelative=Need(J.Get(context.Manifest,"runtimeInterfaces"),"identityPack");
            if(J.String(context.Manifest,"season")!=season||J.String(context.Manifest,"mode")!=mode)throw Invalid("author-v2-base-context-mismatch");
        }
        else if(mode=="difficult")
        {
            dependencies.Add("_internal/difficult-native-manifest.json");
            sourceRelative=Need(J.Get(context.Manifest,"runtimeInterfaces"),"sourceManifest");
        }
        else sourceRelative="references/nightmare-map-pack.json";
        var source=Read(sourceRelative);
        if(J.String(source,"mode") is { } declaredMode&&declaredMode!=mode||J.String(source,"season") is { } declaredSeason&&declaredSeason!=season)
            throw Invalid("author-v2-base-source-context-mismatch");
        var sourceMap=One(Rows(J.Get(source,"maps")),r=>J.String(r,"id")==map);
        var sourceFloors=J.Get(sourceMap,season=="s0"&&mode=="difficult"?"floors":"layers");
        if(J.Get(sourceFloors,floor) is null)throw Invalid("author-v2-base-source-floor-unavailable");
        if(J.Get(source,"floors") is {ValueKind:JsonValueKind.Array} floors&&!floors.EnumerateArray().Any(f=>f.ValueKind==JsonValueKind.String&&f.GetString()==floor))
            throw Invalid("author-v2-base-source-floor-unavailable");
        var index=Read(context.Index);
        if(J.Number(index,"schemaVersion")!=1||J.String(index,"resourceKind")!="shared-structure")throw Invalid("author-v2-base-index-invalid");
        string registryPath=Need(index,"structureVersionBindings");
        if(J.String(J.Get(context.Manifest,"runtimeInterfaces"),"versionBindings") is { } declaredRegistry&&declaredRegistry!=registryPath)
            throw Invalid("author-v2-base-registry-interface-mismatch");
        var registry=Read(registryPath);
        if(J.Number(registry,"schemaVersion")!=1||J.String(registry,"resourceKind")!="native-structure-version-bindings")throw Invalid("author-v2-base-registry-invalid");
        string profile=season=="s0"&&mode=="nightmare"?(J.String(sourceMap,"identityProfileId")??"shared-structure"):"shared-structure";
        var typed=One(Rows(J.Get(index,"entries")),r=>J.String(r,"mapId")==map&&J.String(r,"floor")==floor);
        if(J.String(typed,"profile")!=profile||(J.String(typed,"variant")??"variant-01")!="variant-01")throw Invalid("author-v2-base-identity-mismatch");
        string reference=Need(typed,"resourcePath");dependencies.Add(prefix+reference);
        string[] versionFields=["structureVersionKey","sourceVersion","baseSnapshot"];
        foreach(string field in versionFields)if(string.IsNullOrEmpty(J.String(typed,field)))throw Invalid("author-v2-base-version-missing");
        if(season=="s0"&&mode=="difficult")
        {
            var sourceFloor=J.Get(sourceFloors,floor);
            if(J.String(sourceFloor,"referenceResource")!=reference||versionFields.Any(f=>J.String(sourceFloor,f)!=J.String(typed,f)))
                throw Invalid("author-v2-base-source-floor-binding-mismatch");
        }
        var version=One(Rows(J.Get(registry,"entries")),r=>J.String(r,"mapId")==map&&J.String(J.Get(r,"resourceKey"),"floor")==floor);
        var key=J.Get(version,"resourceKey");var lookup=J.Get(version,"sharedLookup");
        if(J.String(typed,"structureVersionKey")!=$"{J.String(key,"layoutId")}@{J.String(key,"structureRawRecordSha256")}/{floor}"
            ||J.String(version,"sourceVersion")!=J.String(typed,"sourceVersion")||J.String(version,"typedReference")!=reference
            ||J.String(lookup,"profile")!=profile||J.String(lookup,"mapId")!=map||J.String(lookup,"floor")!=floor
            ||J.String(lookup,"variant")!="variant-01"||J.String(lookup,"resourcePath")!=reference
            ||J.String(sourceMap,"sourceLayout")!=J.String(key,"layoutId")||J.String(sourceMap,"structureContentSha256")!=J.String(key,"structureRawRecordSha256")
            ||J.String(sourceMap,"sourceVersion")!=J.String(typed,"sourceVersion"))throw Invalid("author-v2-base-registry-source-mismatch");
        var coordinate=Read(context.Coordinates);var adapter=new AuthorCoordinateAdapter(coordinate);
        if(adapter.Schema!=AuthorCoordinateAdapter.CanonicalSchema||adapter.ReferenceProfile!="shared-structure")throw Invalid("author-v2-base-coordinate-contract-invalid");
        var declared=J.Get(J.Get(J.Get(J.Get(coordinate,"maps"),map),"floors"),floor);
        if(J.String(declared,"referenceResource")!=reference||new[]{"structureVersionKey","sourceVersion"}.Any(f=>J.String(declared,f)!=J.String(typed,f))
            ||J.Get(declared,"baseSnapshot") is not null&&J.String(declared,"baseSnapshot")!=J.String(typed,"baseSnapshot"))throw Invalid("author-v2-base-coordinate-version-mismatch");
        string path=files.Local(context.Root,reference);files.BytesAbsolute(path,128*1024*1024);
        using var archive=NpzArchive.Open(path);using var metadataDocument=JsonDocument.Parse(archive.ReadArray("metadata").ReadUnicodeScalar());
        var metadata=metadataDocument.RootElement.Clone();var canvas=J.Get(metadata,"canvas");var shape=J.Ints(J.Get(canvas,"shapeHW"));
        var binding=J.Get(metadata,"binding");
        if(!Shape(shape)||J.String(binding,"profile")!=profile||J.String(binding,"mapId")!=map||J.String(binding,"floor")!=floor||J.String(binding,"variant")!="variant-01"
            ||versionFields.Any(f=>J.String(metadata,f)!=J.String(typed,f))||J.String(metadata,"sourceKind")!="game-rendered-shared-structure"
            ||string.IsNullOrEmpty(J.String(canvas,"coordinateSpace"))||J.String(canvas,"coordinateSpace")!=J.String(declared,"coordinateSpace")
            ||J.Number(declared,"coordinateHeight")!=shape[0]||J.Number(declared,"coordinateWidth")!=shape[1])throw Invalid("author-v2-base-typed-canvas-mismatch");
        string? native=null;
        if(season=="s1")
        {
            native=$"assets/game-native/{map}/{floor}.png";dependencies.Add(prefix+native);
            if(J.String(context.Manifest,"sourceVersion")!=J.String(typed,"sourceVersion"))throw Invalid("author-v2-base-manifest-version-mismatch");
            foreach(string dep in dependencies.Where(p=>p!=prefix+"native-map-manifest.json"))
            {
                string relative=dep[prefix.Length..];var row=One(Rows(J.Get(context.Manifest,"files")),r=>J.String(r,"path")==relative);
                var data=files.BytesAbsolute(files.Local(context.Root,relative),128*1024*1024);
                if(J.Number(row,"bytes")!=data.Length)throw Invalid("author-v2-base-inventory-mismatch");
                files.RequireDigest(files.Local(context.Root,relative),J.String(row,"sha256"));
            }
        }
        return new(metadata,canvas!.Value,prefix+reference,native is null?null:prefix+native,dependencies.Order(StringComparer.Ordinal).ToArray());
    }
    private static JsonElement One(IEnumerable<JsonElement> rows,Func<JsonElement,bool> predicate)
    {var matched=rows.Where(predicate).ToArray();return matched.Length==1?matched[0]:throw Invalid("author-base-binding-missing-or-ambiguous");}
    private static bool Shape(int[] shape)=>shape.Length==2&&shape.All(v=>v>0&&v<=8192)&&(long)shape[0]*shape[1]<=16*1024*1024;
}
