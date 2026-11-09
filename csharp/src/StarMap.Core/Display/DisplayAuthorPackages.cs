using System.Security.Cryptography;
using System.Text;
using System.Text.Encodings.Web;
using System.Text.Json;
using System.Text.RegularExpressions;
using StarMap.Core.Resources;

namespace StarMap.Core.Display;

internal sealed partial class DisplayAuthorInventory
{
    private static readonly string[] LegacyS1=["s1-jiaye-handnotes","s1-aichicu-routes"];
    // Existing source approvals from app/s1_author_package.py, not archive self-approval.
    private static readonly HashSet<string> ApprovedS1=["bde70366bbdaec962d14ffbb1b45f3423d5f0dde703eb2068bf28b2d97c8655c","8e5a11d41c2bdd5c4fdc55a19089e1005e8fd47434bde75affa40f8928ac4b9d"];
    private static readonly HashSet<string> ApprovedTransparent=["919b1f71fbdfaa3c3ff48433960ac9ba2323d6da3ba3da2165239a3a8686a24c"];
    private void LoadInstalledPackages(ResourceCatalog catalog,ResourceCatalogEntry target,DisplayBridgeResolver.ResourceReads files,IDisplayRasterValidator raster)
    {
        string install=Directory.GetParent(catalog.RootPath)!.FullName;
        var discovered=new SortedSet<string>(StringComparer.Ordinal);
        foreach(string relative in new[]{"work/map-packages","work/map-packages/author-snapshots/active"})
        {
            string folder=files.Local(install,relative);if(!Directory.Exists(folder))continue;
            foreach(string path in Directory.EnumerateFiles(folder,"*.json"))
            {string id=Path.GetFileNameWithoutExtension(path);if(Generic(id))discovered.Add(id);}
        }
        if(target.Season=="s1")discovered.UnionWith(LegacyS1);
        foreach(string profile in discovered)
        {
            string receipt=files.Local(install,$"work/map-packages/{profile}.json");
            string activation=files.Local(install,$"work/map-packages/author-snapshots/active/{profile}.json");
            if(!File.Exists(receipt)&&!File.Exists(activation))continue;
            try
            {
                var package=new InstalledPackage(catalog.RootPath,profile,files);
                if(package.Season!=target.Season)continue;
                if(Generic(profile)&&!package.Owned.Keys.Any(p=>p.StartsWith($"_internal/assets/authors/{profile}/{target.Season}/{target.Mode}/",StringComparison.Ordinal)))continue;
                LoadPackage(catalog,target,profile,package,files,raster);
            }
            catch(Exception error)when(ReadError(error)){Errors[profile]=error.Message;}
        }
    }
    private void LoadPackage(ResourceCatalog catalog,ResourceCatalogEntry target,string profile,InstalledPackage package,
        DisplayBridgeResolver.ResourceReads files,IDisplayRasterValidator raster)
    {
        byte[] raw=package.Read($"_internal/references/author-packs/{profile}/display-bindings.json");
        using var parsed=JsonDocument.Parse(raw);var doc=parsed.RootElement;
        bool generic=Generic(profile),transparent=profile=="s1-aichicu-routes";
        string expectedSchema=generic?"xingtu-author-display-bindings-v2":"xingtu-s1-author-display-bindings-v1";
        if(J.String(doc,"schema")!=expectedSchema||J.String(doc,"profileId")!=profile||J.String(doc,"season")!=package.Season
            ||!J.False(doc,"identityAuthority")||!J.False(doc,"structureValidationAuthority")||!J.False(doc,"crossFloorProjectionAllowed")
            ||!J.True(doc,"currentFrameGeometryFitRequired")||generic&&(!Label(J.String(doc,"authorName"))||!Label(J.String(doc,"displayName"))))
            throw Invalid("author-package-binding-context-mismatch");
        if(!generic&&(!(transparent?ApprovedTransparent:ApprovedS1).Contains(Convert.ToHexStringLower(SHA256.HashData(raw)))
            ||J.String(doc,"sourceVersion")!="2.104.3099014"))throw Invalid("s1-author-registration-not-reviewed");
        var rows=Rows(J.Get(doc,"layers"));if(rows.Length<1||rows.Length>1000)throw Invalid("author-package-invalid-layers");
        var expectedOwned=new HashSet<string>(StringComparer.Ordinal){$"_internal/references/author-packs/{profile}/display-bindings.json"};
        var required=new HashSet<string>(StringComparer.Ordinal);var seen=new HashSet<(string Mode,string Map,string Floor)>();
        var registered=new Dictionary<(string Profile,string Map,string Floor),Registered>();var layers=new List<Layer>();bool incompatible=false;
        foreach(var row in rows)
        {
            string mode=Need(row,"mode"),map=Need(row,"mapId"),floor=Need(row,"floor");
            if(!seen.Add((mode,map,floor))||J.String(row,"season")!=package.Season)throw Invalid("author-package-duplicate-or-foreign-layer");
            string path=Need(row,"displayPath"),extension=Path.GetExtension(path).TrimStart('.');
            string expected=generic?$"_internal/assets/authors/{profile}/{package.Season}/{mode}/{map}/{floor}.{extension}":
                $"_internal/assets/authors/{profile}/{mode}/{map}/B{Math.Abs(int.Parse(floor,System.Globalization.CultureInfo.InvariantCulture))}.{(transparent?"png":"jpeg")}";
            if(path!=expected||generic&&!GenericImagePath(path,profile,package.Season)||!generic&&(package.Season!="s1"||mode is not ("nightmare" or "difficult")||floor is not ("-1" or "-2" or "-3")))
                throw Invalid("author-package-display-namespace-mismatch");
            expectedOwned.Add(path);var inventory=package.Owned.GetValueOrDefault(path);
            var shape=J.Ints(J.Get(row,"displayShapeHW"));var referenceShape=J.Ints(J.Get(row,"referenceShapeHW"));
            if(inventory.ValueKind!=JsonValueKind.Object||J.String(row,"displaySha256")!=J.String(inventory,"sha256")
                ||J.Number(row,"displayBytes")!=J.Number(inventory,"bytes")||!Shape(shape)||!Shape(referenceShape))
                throw Invalid("author-package-display-inventory-mismatch");
            var matrix=ReadAffine(J.Get(row,"matrixDisplayToReference"));ValidatePackageMatrix(matrix,generic);
            string physical=package.PathFor(path);bool usable=true;
            try {ValidatePackageImage(package.Read(path),shape,extension,generic,transparent,raster);}
            catch(Exception error)when(ReadError(error)&&package.Managed&&generic){usable=false;}
            if(package.Season=="s1"&&mode=="difficulty2")continue;
            if(!usable)incompatible=true;
            if(usable)
            {
                try
                {
                    var basis=ResolveAuthorBase(catalog,package.Season,mode,map,floor,files);
                    if(generic)required.UnionWith(basis.Dependencies);
                    if(Need(row,"referenceResource")!=basis.Reference||!referenceShape.SequenceEqual(J.Ints(J.Get(basis.Canvas,"shapeHW")))
                        ||J.String(row,"coordinateSpace")!=J.String(basis.Canvas,"coordinateSpace")
                        ||new[]{"structureVersionKey","sourceVersion","baseSnapshot"}.Any(k=>J.String(row,k)!=J.String(basis.Metadata,k)))
                        throw Invalid("author-package-reference-binding-mismatch");
                    var referenceInventory=package.Dependencies.GetValueOrDefault(basis.Reference);
                    if(referenceInventory.ValueKind!=JsonValueKind.Object||J.String(row,"referenceSha256")!=J.String(referenceInventory,"sha256"))
                        throw Invalid("author-package-reference-inventory-mismatch");
                    if(basis.NativeImage is { } native)
                    {
                        var nativeInventory=package.Dependencies.GetValueOrDefault(native);
                        if(J.String(row,"nativeImageResource")!=native||nativeInventory.ValueKind!=JsonValueKind.Object
                            ||J.String(row,"nativeImageSha256")!=J.String(nativeInventory,"sha256"))throw Invalid("author-package-native-inventory-mismatch");
                    }
                    if(generic)foreach(string dependency in basis.Dependencies)package.Read(dependency);
                    else
                    {
                        var registration=J.Get(row,"registration");
                        if(J.String(registration,"status")!="offline-reviewed"||J.String(registration,"method")!="affine-features-full-outline-review-v1"
                            ||!double.IsFinite(J.Number(registration,"outlineIoU"))||J.Number(registration,"outlineIoU")<.98||J.Number(registration,"outlineIoU")>1
                            ||!Integer(J.Get(registration,"inliers"),out long inliers)||inliers<12||!Digest(J.String(registration,"evidenceSha256")))
                            throw Invalid("s1-author-registration-evidence-missing");
                    }
                }
                catch(Exception error)when(ReadError(error)&&package.Managed&&generic){usable=false;incompatible=true;}
            }
            if(usable&&mode==target.Mode)
            {
                registered[(profile,map,floor)]=new(physical,matrix,true,true,generic?"user-provided":"offline-reviewed",row.Clone());
                layers.Add(new(map,floor,"variant-01",physical,null));
            }
        }
        if(!expectedOwned.SetEquals(package.Owned.Keys))throw Invalid("author-package-unlisted-or-missing-display-file");
        if(!generic)
        {
            var expectedLayers=catalog.Entries.Where(e=>e.Season=="s1"&&(e.Mode is "nightmare" or "difficult")&&(e.Floor is "-1" or "-2" or "-3"))
                .Select(e=>(e.Mode,e.MapId,e.Floor)).ToHashSet();
            if(!seen.SetEquals(expectedLayers))throw Invalid("s1-author-layer-coverage-mismatch");
            var missing=Rows(J.Get(doc,"missingAuthorLayers"));
            var expectedMissing=catalog.Entries.Where(e=>e.Season=="s1"&&(e.Mode is "nightmare" or "difficult")&&e.Floor=="-4")
                .Select(e=>(e.Mode,e.MapId,e.Floor)).ToHashSet();
            if(missing.Length!=expectedMissing.Count||!missing.Select(r=>(Need(r,"mode"),Need(r,"mapId"),Need(r,"floor"))).ToHashSet().SetEquals(expectedMissing)
                ||missing.Any(r=>J.String(r,"fallback")!="canonical-native-if-available"))throw Invalid("s1-author-missing-floor-contract");
            foreach(string mode in new[]{"nightmare","difficult"})
            {
                string prefix=$"_internal/seasons/s1/{mode}/";
                required.UnionWith(new[]{"native-map-manifest.json","references/shared-structure-index.json","references/coordinate-pack.json","references/structure-version-bindings.json"}.Select(r=>prefix+r));
                foreach(var layer in expectedLayers.Where(l=>l.Mode==mode))
                {required.Add(prefix+$"references/entries/{layer.MapId}-{layer.Floor}-shared.npz");required.Add(prefix+$"assets/game-native/{layer.MapId}/{layer.Floor}.png");}
            }
        }
        var activeDependencies=package.Dependencies.Keys.Where(p=>package.Season!="s1"||!p.StartsWith("_internal/seasons/s1/difficulty2/",StringComparison.Ordinal)).ToHashSet(StringComparer.Ordinal);
        if((!package.Managed||!incompatible)&&!required.SetEquals(activeDependencies))throw Invalid("author-package-dependency-closure-mismatch");
        foreach(string dependency in required)if(!package.Managed||!generic)package.Read(dependency);
        if(layers.Count>0){Profiles[profile]=new(profile,true,true,layers);OptionalProfiles.Add(profile);foreach(var p in registered)Bindings[p.Key]=p.Value;}
    }
    private static void ValidatePackageMatrix(DisplayAffine m,bool generic)
    {
        if(m.ToArray().SelectMany(r=>r).Any(v=>Math.Abs(v)>100000))throw Invalid("author-package-display-transform-invalid");
        if(!generic)
        {if(m.Determinant<1e-4||m.Determinant>1e4||Math.Abs(m.M00-m.M11)>1e-8||Math.Abs(m.M01+m.M10)>1e-8)throw Invalid("s1-author-invalid-display-transform");return;}
        double sum=Math.Sqrt((m.M00+m.M11)*(m.M00+m.M11)+(m.M10-m.M01)*(m.M10-m.M01));
        double difference=Math.Sqrt((m.M00-m.M11)*(m.M00-m.M11)+(m.M10+m.M01)*(m.M10+m.M01));
        double largest=(sum+difference)/2,smallest=m.Determinant/largest;
        if(m.Determinant<=0||smallest<.01-1e-12||largest>100+1e-10)throw Invalid("author-v2-invalid-display-affine");
    }
    private static void ValidatePackageImage(byte[] bytes,int[] shape,string extension,bool generic,bool transparent,IDisplayRasterValidator raster)
    {
        if(generic)
        {
            var header=DisplayImageHeader.Read(bytes,extension);
            if(header.Height!=shape[0]||header.Width!=shape[1])throw Invalid("author-package-image-canvas-mismatch");
            return;
        }
        bool png=extension=="png",jpeg=extension=="jpeg";
        if(png)
        {
            if(bytes.Length<33||!bytes.AsSpan(0,8).SequenceEqual(new byte[]{137,80,78,71,13,10,26,10})||bytes[24]!=8
                ||bytes[25] is not (2 or 6)||transparent&&bytes[25]!=6)throw Invalid("author-package-image-format-mismatch");
            for(int offset=8;offset+12<=bytes.Length;)
            {
                int length=System.Buffers.Binary.BinaryPrimitives.ReadInt32BigEndian(bytes.AsSpan(offset,4));
                if(length<0||(long)offset+12+length>bytes.Length)throw Invalid("author-package-image-format-mismatch");
                if(bytes.AsSpan(offset+4,4).SequenceEqual("acTL"u8))throw Invalid("author-package-image-format-mismatch");offset+=12+length;
            }
        }
        else if(!jpeg||bytes.Length<2||bytes[0]!=255||bytes[1]!=216)throw Invalid("author-package-image-format-mismatch");
        var image=raster.Inspect(bytes);
        if(image.Height!=shape[0]||image.Width!=shape[1]||png&&image.Channels is not (3 or 4)||transparent&&image.Channels!=4||jpeg&&image.Channels is not (1 or 3))
            throw Invalid("author-package-image-canvas-mismatch");
    }
    private static bool GenericImagePath(string path,string profile,string season)
    {
        string prefix=$"_internal/assets/authors/{profile}/{season}/";
        if(!path.StartsWith(prefix,StringComparison.Ordinal))return false;var p=path[prefix.Length..].Split('/');
        if(p.Length!=3)return false;string extension=Path.GetExtension(p[2]).TrimStart('.'),floor=Path.GetFileNameWithoutExtension(p[2]);
        return Regex.IsMatch(p[0],"^[a-zA-Z0-9][a-zA-Z0-9_-]{0,79}$")&&Regex.IsMatch(p[1],"^[a-zA-Z0-9][a-zA-Z0-9_-]{0,79}$")
            &&Regex.IsMatch(floor,"^-?[a-zA-Z0-9][a-zA-Z0-9_-]{0,15}$")&&(extension is "png" or "jpeg");
    }
    private static bool Label(string? value)=>!string.IsNullOrWhiteSpace(value)&&value.Length<=120&&!value.Any(c=>c<32||c is '<' or '>');
    private static bool Digest(string? value)=>value is not null&&Regex.IsMatch(value,"^[a-f0-9]{64}$");
    private static bool Integer(JsonElement? value,out long result){result=0;return value is {ValueKind:JsonValueKind.Number} v&&v.TryGetInt64(out result);}
    private sealed class InstalledPackage
    {
        private readonly string root,install,profile;
        private readonly DisplayBridgeResolver.ResourceReads files;
        private readonly Dictionary<string,string> stored=new(StringComparer.OrdinalIgnoreCase);
        internal bool Managed{get;}
        internal string Season{get;}
        internal Dictionary<string,JsonElement> Owned{get;}
        internal Dictionary<string,JsonElement> Dependencies{get;}
        internal InstalledPackage(string root,string profile,DisplayBridgeResolver.ResourceReads files)
        {
            this.root=root;this.profile=profile;this.files=files;install=Directory.GetParent(root)!.FullName;
            string activationRelative=$"work/map-packages/author-snapshots/active/{profile}.json";
            string activationPath=files.Local(install,activationRelative);JsonElement receipt;
            if(Generic(profile)&&File.Exists(activationPath))
            {
                Managed=true;var activation=ReadJson(activationPath,4*1024*1024);
                if(!activation.EnumerateObject().Select(p=>p.Name).ToHashSet().SetEquals(new[]{"schema","packageId","season","snapshotId","manifestSha256","generation"})
                    ||J.String(activation,"schema")!="xingtu-author-resource-activation-v1"||J.String(activation,"packageId")!=profile
                    ||!Id(J.String(activation,"generation"))||!SeasonId(J.String(activation,"season")))throw Invalid("author-snapshot-invalid-activation");
                string snapshot=J.String(activation,"snapshotId")??throw Invalid("author-snapshot-inactive");
                if(!Id(snapshot)||!Digest(J.String(activation,"manifestSha256")))throw Invalid("author-snapshot-invalid-activation");
                string directory=files.Local(install,$"work/map-packages/author-snapshots/versions/{profile}/{snapshot}");
                string manifestPath=files.Local(directory,"manifest.json");var manifest=ReadJson(manifestPath,4*1024*1024);
                files.RequireDigest(manifestPath,J.String(activation,"manifestSha256"));
                if(!manifest.EnumerateObject().Select(p=>p.Name).ToHashSet().SetEquals(new[]{"schema","packageId","snapshotId","receipt","contentId"})
                    ||J.String(manifest,"schema")!="xingtu-author-resource-snapshot-v1"||J.String(manifest,"packageId")!=profile||J.String(manifest,"snapshotId")!=snapshot)
                    throw Invalid("author-snapshot-invalid-manifest");
                receipt=J.Get(manifest,"receipt")??throw Invalid("author-snapshot-invalid-receipt");
                if(J.String(receipt,"season")!=J.String(activation,"season")||CanonicalDigest(receipt)!=J.String(manifest,"contentId"))
                    throw Invalid("author-snapshot-content-identity-mismatch");
                Season=Need(receipt,"season");Owned=Inventory(J.Get(receipt,"files"));Dependencies=Inventory(J.Get(receipt,"requiresFiles"));
                int i=0;foreach(var row in Owned.Values.OrderBy(r=>Need(r,"path"),StringComparer.Ordinal))
                    stored[Need(row,"path")]=files.Local(directory,$"files/{i++:D4}{Path.GetExtension(Need(row,"path"))}");
            }
            else
            {
                receipt=ReadJson(files.Local(install,$"work/map-packages/{profile}.json"),2*1024*1024);
                Season=Need(receipt,"season");Owned=Inventory(J.Get(receipt,"files"));Dependencies=Inventory(J.Get(receipt,"requiresFiles"));
            }
            if(J.String(receipt,"packageId")!=profile||J.Number(receipt,"revision")!=1||!Digest(J.String(receipt,"archiveSha256"))||!SeasonId(Season)
                ||J.String(receipt,"schema")!=(Generic(profile)?"xingtu-author-display-package-v2":"xingtu-s1-author-display-package-v1"))
                throw Invalid("invalid-installed-author-package-receipt");
            if(Generic(profile)&&(!Integer(J.Get(receipt,"revision"),out long revision)||revision!=1))throw Invalid("author-v2-receipt-context-mismatch");
            if(!Owned.ContainsKey($"_internal/references/author-packs/{profile}/display-bindings.json")||Owned.Count<2)
                throw Invalid("author-package-receipt-incomplete");
            foreach(string path in Owned.Keys)
                if(path!=$"_internal/references/author-packs/{profile}/display-bindings.json"
                    &&(Generic(profile)?!GenericImagePath(path,profile,Season):!path.StartsWith($"_internal/assets/authors/{profile}/",StringComparison.Ordinal)))
                    throw Invalid("author-package-receipt-file-scope-mismatch");
            foreach(string dependency in Dependencies.Keys)
                if(dependency.Contains("/author-packs/",StringComparison.Ordinal)||dependency.Contains("/authors/",StringComparison.Ordinal)
                    ||Path.GetExtension(dependency) is not (".json" or ".npz" or ".png" or ".webp" or ".jpeg")
                    ||!(Season=="s0"?(dependency.StartsWith("_internal/references/",StringComparison.Ordinal)||dependency.StartsWith("_internal/assets/",StringComparison.Ordinal)||dependency=="_internal/difficult-native-manifest.json"):
                        dependency.StartsWith($"_internal/seasons/{Season}/",StringComparison.Ordinal)))throw Invalid("author-package-dependency-scope-mismatch");
        }
        internal string PathFor(string relative)
        {if(!relative.StartsWith("_internal/",StringComparison.Ordinal))throw Invalid("author-package-resource-path-invalid");
            return stored.GetValueOrDefault(relative)??files.Local(root,relative[10..]);}
        internal byte[] Read(string relative)
        {
            if(!Owned.TryGetValue(relative,out var row)&&!Dependencies.TryGetValue(relative,out row))throw Invalid("author-package-resource-undeclared");
            string path=PathFor(relative);DisplayFileAccess.RequireRegular(path);
            var data=files.BytesAbsolute(path,128*1024*1024);
            if(J.Number(row,"bytes")!=data.Length)throw Invalid("author-package-resource-changed");
            files.RequireDigest(path,J.String(row,"sha256"));MarkStrict(path);return data;
        }
        private JsonElement ReadJson(string path,int maximum)
        {DisplayFileAccess.RequireRegular(path);using var doc=JsonDocument.Parse(files.BytesAbsolute(path,maximum));MarkStrict(path);return doc.RootElement.Clone();}
        private void MarkStrict(string path)
        {int i=files.Snapshots.FindIndex(s=>PathEquals(s.Path,path));if(i>=0)files.Snapshots[i]=files.Snapshots[i] with {RequiresRegularSingleLink=true};}
        private static bool SeasonId(string? value)=>value is not null&&Regex.IsMatch(value,"^s(?:0|[1-9][0-9]{0,3})$");
        private static bool Id(string? value)=>value is not null&&Regex.IsMatch(value,"^[a-f0-9]{32}$");
        private static Dictionary<string,JsonElement> Inventory(JsonElement? value)
        {
            var rows=Rows(value);if(rows.Length<1||rows.Length>2000)throw Invalid("author-package-file-inventory-invalid");
            var result=new Dictionary<string,JsonElement>(StringComparer.OrdinalIgnoreCase);
            foreach(var row in rows)
            {string path=Need(row,"path");if(row.EnumerateObject().Count()!=3||!path.StartsWith("_internal/",StringComparison.Ordinal)
                ||path.Split('/').Any(p=>p is "." or ".." or "")||path.Contains('\\')||!Integer(J.Get(row,"bytes"),out long length)||length<0||length>128*1024*1024
                ||!Digest(J.String(row,"sha256"))||!result.TryAdd(path,row.Clone()))throw Invalid("author-package-file-record-invalid");}
            return result;
        }
    }
    private static string CanonicalDigest(JsonElement value)
    {
        using var buffer=new MemoryStream();using(var writer=new Utf8JsonWriter(buffer,new(){Encoder=JavaScriptEncoder.UnsafeRelaxedJsonEscaping}))Write(value,writer);
        return Convert.ToHexStringLower(SHA256.HashData(buffer.ToArray()));
        static void Write(JsonElement node,Utf8JsonWriter writer)
        {if(node.ValueKind==JsonValueKind.Object){writer.WriteStartObject();foreach(var p in node.EnumerateObject().OrderBy(p=>p.Name,StringComparer.Ordinal)){writer.WritePropertyName(p.Name);Write(p.Value,writer);}writer.WriteEndObject();}
            else if(node.ValueKind==JsonValueKind.Array){writer.WriteStartArray();foreach(var item in node.EnumerateArray())Write(item,writer);writer.WriteEndArray();}else node.WriteTo(writer);}
    }
}
