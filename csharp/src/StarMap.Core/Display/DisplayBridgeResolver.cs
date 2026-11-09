using System.Security.Cryptography;
using System.Text.Json;
using StarMap.Core.Resources;

namespace StarMap.Core.Display;

/// <summary>
/// Resolves one exact resource binding on every call. No cross-floor/cache fallback and no pose writer.
/// referenceToClient always maps the selected NPZ's current-reference pixels, not canonical pixels.
/// </summary>
public sealed partial class DisplayBridgeResolver(IDisplayRasterValidator raster)
{
    public DisplayBridgeResult Resolve(ResourceCatalog catalog, ResourceCatalogEntry entry, string displayProfile,
        DisplayAffine referenceToClient, string variant = "variant-01") => ResolveCore(catalog,entry,displayProfile,referenceToClient,variant,false,false);

    /// <summary>Display selection may explicitly use a typed legacy reference, without asserting a native bridge.</summary>
    public DisplayBridgeResult ResolveDisplayReference(ResourceCatalog catalog,ResourceCatalogEntry entry,string displayProfile,
        DisplayAffine referenceToClient,string variant="variant-01",bool neutral=false) => ResolveCore(catalog,entry,displayProfile,referenceToClient,variant,true,neutral);

    private DisplayBridgeResult ResolveCore(ResourceCatalog catalog,ResourceCatalogEntry entry,string displayProfile,
        DisplayAffine referenceToClient,string variant,bool allowLegacyReference,bool neutral)
    {
        try
        {
            if (!ReferenceEquals(entry.Owner,catalog)) return Reject("resource-catalog-owner-mismatch");
            if (!referenceToClient.IsInvertible) return Reject("reference-to-client-invalid");
            if (variant != "variant-01" && !DisplayAuthorInventory.IsCoffin(entry,displayProfile)) return Reject("display-variant-not-bound-to-selected-reference");
            if (entry.StructureKind == "legacy-clean") return allowLegacyReference
                ? ResolveLegacyDisplay(catalog,entry,displayProfile,referenceToClient,variant,neutral)
                : Reject("legacy-clean-native-bridge-unavailable");
            if (entry.StructureStatus != ResourceBindingStatus.Ready || entry.StructureNpzPath is null)
                return Reject(entry.StructureIssue?.Code ?? "typed-reference-unavailable");
            var files = new ResourceReads(catalog.RootPath);
            var context = ReadContext(files,entry);
            var pack = files.Json(context.Root,context.Coordinates);
            var adapter = new AuthorCoordinateAdapter(pack);
            if (adapter.ReferenceProfile != "shared-structure") return Reject("native-to-canonical-reference-profile-unavailable");
            var declared = J.Get(J.Get(J.Get(J.Get(pack,"maps"),entry.MapId),"floors"),entry.Floor);
            if (declared is null || !J.Equal(declared,entry.CoordinateMetadata)) return Reject("coordinate-resource-context-changed");

            // Bind to the current index, not merely the catalog's earlier copy.
            var index = files.Json(context.Root,context.Index);
            var indexRows = Array(J.Get(index,"entries")).Where(row =>
                J.String(row,"profile") == entry.Profile && J.String(row,"mapId") == entry.MapId
                && J.String(row,"floor") == entry.Floor && (J.String(row,"variant") ?? "variant-01") == "variant-01").ToArray();
            if (indexRows.Length != 1 || !J.Equal(indexRows[0],entry.StructureIndexMetadata)) return Reject("typed-reference-index-changed");
            string referenceRelative = J.String(indexRows[0],"resourcePath") ?? throw Gap("typed-reference-path-missing");
            string referencePath = files.Local(context.Root,referenceRelative);
            if (!string.Equals(referencePath,Path.GetFullPath(entry.StructureNpzPath),StringComparison.OrdinalIgnoreCase))
                return Reject("typed-reference-path-mismatch");
            files.BytesAbsolute(referencePath,128*1024*1024);
            var loaded = catalog.LoadSharedStructure(entry);
            if (!loaded.Success) return Reject(loaded.Error?.Code ?? "typed-reference-load-failed");
            var structure = loaded.Value!;
            if (structure.CleanMask is null) return Reject("clean-reference-unavailable");
            var mask = structure.CleanMask;
            int height = mask.Shape[0], width = mask.Shape[1];
            var metadata = structure.Metadata;
            var canvas = J.Get(metadata,"canvas");
            var geometry = J.Get(metadata,"sourceGeometry");
            var referenceToNative = DisplayAffine.Identity;
            if (J.Get(metadata,"calibration") is { } calibration)
            {
                if (!DisplayAffine.TryRead(J.Get(calibration,"matrixCurrentReferenceToNative"),out referenceToNative))
                    return Reject("calibrated-reference-native-bridge-unavailable");
            }
            else if (J.String(metadata,"sourceKind") != "game-rendered-shared-structure")
                return Reject("unprojected-native-reference-unavailable");
            if (canvas is null || J.String(canvas,"floor") != entry.Floor || J.String(canvas,"axes") != "x-right,y-down"
                || !J.Ints(J.Get(canvas,"shapeHW")).SequenceEqual(new[] {height,width})
                || J.Number(canvas,"width") != width || J.Number(canvas,"height") != height
                || J.String(canvas,"coordinateSpace") != J.String(declared,"coordinateSpace")
                || J.Number(declared,"coordinateWidth") != width || J.Number(declared,"coordinateHeight") != height
                || J.String(declared,"referenceResource") != referenceRelative)
                return Reject("native-canonical-canvas-binding-mismatch");
            RequireNativeSourceBinding(files,context,entry,declared,metadata,canvas,referenceRelative);
            RequireVersions(declared,metadata,allowMissingBase:true);
            if (entry.StructureCanvasMetadata is { } catalogCanvas && !J.Equal(canvas,catalogCanvas))
                return Reject("structure-display-canvas-changed");
            var displayDeclaration = files.Json(context.Root,context.StructureDisplay);
            var displayRows = Array(J.Get(displayDeclaration,"entries")).Where(row => J.String(row,"mapId") == entry.MapId && J.String(row,"floor") == entry.Floor).ToArray();
            if (displayRows.Length != 1 || !J.Equal(J.Get(displayRows[0],"canvas"),canvas)
                || J.String(displayRows[0],"referenceResource") != referenceRelative)
                return Reject("structure-display-reference-binding-mismatch");
            if (!adapter.TryProfileToCanonical("shared-structure",entry.MapId,entry.Floor,out var nativeToCanonical,out var sourceReason))
                return Reject(sourceReason);

            // The neutral pixels are the actual current-reference clean NPZ channel.
            // Their canonical bridge includes the reference calibration; treating them
            // as native pixels would apply inverse(referenceToNative) a second time.
            if (displayProfile == "shared-structure" && (entry.Season != "s1" || neutral))
                return Finish(null,"neutral-structure",nativeToCanonical*referenceToNative,width,height);

            if (entry.Season == "s1" && displayProfile == "shared-structure")
            {
                if (J.Get(metadata,"calibration") is not null) return Reject("s1-calibrated-display-bridge-unavailable");
                if (!nativeToCanonical.AlmostEquals(DisplayAffine.Identity,0)
                    || !DisplayAffine.TryRead(J.Get(declared,"matrixCanonicalToLegacyRecognition"),out var legacy)
                    || !legacy.AlmostEquals(DisplayAffine.Identity,0)) return Reject("s1-unexpected-native-projection");
                var image = ResolveSeasonNative(files,context,entry,metadata,canvas!.Value,geometry,referenceRelative,
                    mask.Bytes,width,height);
                return Finish(image,"game-native",nativeToCanonical,width,height);
            }

            if (entry.Season == "s0" && entry.Mode == "nightmare" && displayProfile == "base-map")
            {
                string libraryRoot = files.Local(files.Root,"assets/custom-maps");
                var library = files.Json(libraryRoot,"manifest.json");
                if (J.String(library,"schema") != "xingtu-custom-maps-v1") return Reject("base-map-manifest-unsupported");
                var maps = Array(J.Get(library,"maps")).Where(m => J.String(m,"mapId") == entry.MapId).ToArray();
                var baseLayer = maps.Length == 1 ? J.Get(J.Get(maps[0],"floors"),entry.Floor) : null;
                if (baseLayer is null || !J.Ints(J.Get(baseLayer,"nativeShape")).SequenceEqual(new[] {height,width}))
                    return Reject("base-map-native-canvas-mismatch");
                byte[] binary = mask.Bytes.ToArray();
                for (int i = 0; i < binary.Length; i++) binary[i] = (byte)(binary[i] == 0 ? 0 : 1);
                if (Convert.ToHexStringLower(SHA256.HashData(binary)) != J.String(baseLayer,"maskSha256"))
                    return Reject("base-map-native-mask-mismatch");
                if (!DisplayAffine.TryRead(J.Get(baseLayer,"matrixImageToNative"),out var imageToNative))
                    return Reject("base-map-image-native-bridge-invalid");
                string image = files.Local(libraryRoot,J.String(baseLayer,"image") ?? throw Gap("base-map-image-missing"));
                var bytes = files.BytesAbsolute(image,32*1024*1024);
                files.RequireDigest(image,J.String(baseLayer,"imageSha256"));
                string topology = files.Local(libraryRoot,J.String(baseLayer,"topology") ?? throw Gap("base-map-topology-missing"));
                files.BytesAbsolute(topology,32*1024*1024);
                files.RequireDigest(topology,J.String(baseLayer,"topologySha256"));
                var pixels = raster.Inspect(bytes);
                if (!pixels.HasVisiblePixels || !J.Ints(J.Get(baseLayer,"imageSize")).SequenceEqual(new[] {pixels.Width,pixels.Height}))
                    return Reject("base-map-image-dimensions-invalid");
                bool includesExits=J.True(J.Get(library,"art"),"includesExits");
                string? icon=files.Local(libraryRoot,"exit-icon.png");
                if(includesExits||!File.Exists(icon))icon=null;else files.BytesAbsolute(icon,32*1024*1024);
                int iconSize=Math.Max(20,(int)Math.Round(Math.Max(pixels.Width,pixels.Height)*.025*Math.Sqrt(Math.Abs(imageToNative.Determinant))));
                var entrances=new List<DisplayAtlasEntrance>();
                foreach(var entrance in DisplayAuthorInventory.Rows(J.Get(baseLayer,"entrances")))
                {
                    double x=J.Number(entrance,"x"),y=J.Number(entrance,"y");var native=imageToNative.Apply(x,y);
                    if(!double.IsFinite(native.X)||!double.IsFinite(native.Y))return Reject("base-map-entrance-coordinate-invalid");
                    entrances.Add(new(J.String(entrance,"label")??"",x,y,checked((int)Math.Round(native.X-iconSize/2d)),checked((int)Math.Round(native.Y-iconSize/2d))));
                }
                // The Python provider first warps these source pixels to native and then exposes identity.
                // Returning the raw image requires retaining the explicit image->native transform here.
                return Finish(image,"base-map-source-image",nativeToCanonical*imageToNative,pixels.Width,pixels.Height)
                    with {BaseAtlas=new(imageToNative,width,height,includesExits,icon,iconSize,System.Array.AsReadOnly(entrances.ToArray()))};
            }

            var inventory=DisplayAuthorInventory.Load(catalog,entry,files,raster);
            var registered=inventory.Binding(displayProfile,entry.MapId,entry.Floor);
            if (registered is not null)
            {
                var selected=inventory.FindLayer(displayProfile,entry.MapId,entry.Floor,variant);
                if(!inventory.Profiles.TryGetValue(displayProfile,out var registeredProfile)||!registeredProfile.Aligned)return Reject("author-profile-unavailable");
                if (!registered.Usable) return Reject("author-registration-or-version-unavailable");
                if (selected is null || !string.Equals(selected.Path,registered.Path,StringComparison.OrdinalIgnoreCase))
                    return Reject("author-binding-path-mismatch");
                var pixels=raster.Inspect(files.BytesAbsolute(registered.Path,128*1024*1024));
                if (!registered.AllowConversion && pixels.Channels!=4)
                    return Reject("author-display-canvas-or-image-unavailable");
                return Finish(registered.Path,"author-floor-display",registered.Matrix,pixels.Width,pixels.Height,pixels.Channels,registered.AllowConversion);
            }
            if (inventory.OptionalProfiles.Contains(displayProfile)) return Reject("author-floor-image-not-declared");
            if (!adapter.TryProfileToCanonical(displayProfile,entry.MapId,entry.Floor,out var profileToCanonical,out var profileReason))
                return Reject(profileReason);
            if (DisplayAuthorInventory.IsCoffin(entry,displayProfile))
            {
                var coffin=inventory.FindLayer(displayProfile,entry.MapId,entry.Floor,variant);
                if (coffin is null || !inventory.Profiles.TryGetValue(displayProfile,out var coffinProfile) || !coffinProfile.Aligned)
                    return Reject("author-variant-unavailable");
                var evidenceBinding=J.Get(J.Get(adapter.Floor(displayProfile,entry.MapId,entry.Floor),"evidence"),"binding");
                if (J.String(evidenceBinding,"variant") is { } declaredVariant && declaredVariant!=variant)
                    return Reject("author-variant-coordinate-binding-mismatch");
                var pixels=raster.Inspect(files.BytesAbsolute(coffin.Path,128*1024*1024));
                if (pixels.Channels!=4) return Reject("author-display-canvas-or-image-unavailable");
                return Finish(coffin.Path,"author-floor-display",profileToCanonical,pixels.Width,pixels.Height);
            }
            var authorCatalog = files.Json(context.Root,context.AuthorCatalog);
            if (J.Number(authorCatalog,"schemaVersion") != 1) return Reject("author-catalog-schema-unsupported");
            var profiles = Array(J.Get(authorCatalog,"profiles")).Where(p => J.String(p,"id") == displayProfile).ToArray();
            if (profiles.Length != 1 || !J.True(profiles[0],"runtimeEnabled") || !J.True(profiles[0],"alignedOverlayEligible"))
                return Reject("author-profile-unavailable");
            var variants = Array(J.Get(J.Get(profiles[0],"maps"),entry.MapId)).Where(v => J.String(v,"id") == variant).ToArray();
            if (variants.Length != 1) return Reject("author-variant-unavailable");
            var author = variants[0];
            string? relative = J.String(J.Get(author,"layers"),entry.Floor);
            var layer = J.Get(J.Get(author,"layerMeta"),entry.Floor);
            if (relative is null || layer is null) return Reject("author-floor-image-not-declared");
            if (J.String(author,"resourceBase") != "resource-root") return Reject("author-external-asset-root-not-bound");
            if (J.String(layer,"role") != "display-only") return Reject("author-layer-role-invalid");
            RequireVersions(layer,metadata,allowMissingBase:true);
            var basis = adapter.Floor(displayProfile,entry.MapId,entry.Floor);
            RequireAuthorBasisVersions(basis,metadata);
            string space = J.String(canvas,"coordinateSpace") ?? "";
            // Already-baked images either explicitly declare their display->native offset,
            // or prove the same native coordinate domain. Image dimensions alone never suffice.
            if (DisplayAffine.TryRead(J.Get(layer,"matrixDisplayToNative"),out var declaredDisplayToNative))
            {
                if (!J.True(layer,"alreadyBaked") || !(nativeToCanonical*declaredDisplayToNative).AlmostEquals(profileToCanonical))
                    return Reject("author-baked-offset-bridge-mismatch");
                if (J.String(layer,"coordinateSpace") != space+":display-offset" && J.String(layer,"coordinateSpace") != space)
                    return Reject("author-display-coordinate-domain-mismatch");
            }
            else
            {
                if (!J.True(layer,"alreadyBaked") || J.String(layer,"coordinateSpace") != space
                    || !nativeToCanonical.AlmostEquals(profileToCanonical)) return Reject("author-display-native-bridge-unavailable");
                if (J.String(layer,"sourceLayout") is { } layout && layout != J.String(canvas,"layout"))
                    return Reject("author-display-layout-mismatch");
                if (J.Get(layer,"sourceWorldFloor") is not null && J.Number(layer,"sourceWorldFloor") != J.Number(canvas,"worldFloor"))
                    return Reject("author-display-world-floor-mismatch");
            }
            string path = files.Local(context.Root,relative);
            var imageBytes = files.BytesAbsolute(path,32*1024*1024);
            files.RequireDigest(path,J.String(layer,"sha256"));
            var imageInfo = raster.Inspect(imageBytes);
            if (imageInfo.Channels!=4 || imageInfo.Width != J.Number(layer,"width") || imageInfo.Height != J.Number(layer,"height"))
                return Reject("author-image-pixels-or-dimensions-invalid");
            return Finish(path,"author-floor-display",profileToCanonical,imageInfo.Width,imageInfo.Height);

            DisplayBridgeResult Finish(string? imagePath,string kind,DisplayAffine displayToCanonical,int imageWidth,int imageHeight,int? channels=null,bool allowConversion=false)
            {
                var referenceToCanonical = nativeToCanonical*referenceToNative;
                var displayToReference = referenceToCanonical.Inverse()*displayToCanonical;
                var canonicalToClient = referenceToClient*referenceToCanonical.Inverse();
                int sourceChannels=channels ?? (imagePath is null ? 4 : raster.Inspect(files.BytesAbsolute(imagePath,128*1024*1024)).Channels);
                var snapshots = files.Snapshots.ToArray();
                if (!snapshots.All(s => s.IsCurrent())) return Reject("display-resources-changed-during-resolution");
                return new(true,"accepted",entry.StableKey,displayProfile,imagePath,kind,nativeToCanonical,
                    displayToCanonical,displayToReference,referenceToClient*displayToReference,canonicalToClient,
                    System.Array.AsReadOnly(snapshots),imageWidth,imageHeight)
                { CanonicalPackId = adapter.PackId, ReferenceToCanonical = referenceToCanonical, Reference = structure,
                    ImageChannels = sourceChannels,
                    AllowBgraFormatConversion = allowConversion || kind is "game-native" or "base-map-source-image" };
            }
        }
        catch (Exception e) when (e is IOException or UnauthorizedAccessException or JsonException or InvalidDataException
            or ArgumentException or OverflowException or NotSupportedException)
        { return Reject(e.Message); }

        DisplayBridgeResult Reject(string reason) => new(false,reason,entry.StableKey,displayProfile);
    }

    private string ResolveSeasonNative(ResourceReads files,Context context,ResourceCatalogEntry entry,JsonElement metadata,
        JsonElement canvas,JsonElement? geometry,string referenceRelative,ReadOnlyMemory<byte> mask,int width,int height)
    {
        var manifest = context.Manifest ?? throw Gap("s1-manifest-unavailable");
        if (J.String(manifest,"season") != entry.Season || J.String(manifest,"mode") != entry.Mode
            || J.String(manifest,"sourceVersion") != J.String(metadata,"sourceVersion")) throw Gap("s1-manifest-scope-mismatch");
        var inventory = Array(J.Get(manifest,"files"));
        if (J.Get(geometry,"projectionApplied") is not { ValueKind: JsonValueKind.False }
            || !J.Ints(J.Get(geometry,"nativeShapeHW")).SequenceEqual(new[] {height,width})
            || !int.TryParse(entry.Floor,out int nativeFloor) || J.Number(geometry,"gameFloor") != nativeFloor
            || J.Number(canvas,"worldFloor") != 5+nativeFloor || J.Number(geometry,"sourceHeight") != 5+nativeFloor)
            throw Gap("s1-unprojected-renderer-binding-unavailable");
        string rendererDigest = J.String(geometry,"rendererImageSha256") ?? throw Gap("s1-renderer-digest-unavailable");
        string relative = $"assets/game-native/{entry.MapId}/{entry.Floor}.png";
        var row = UniqueInventory(relative);
        string imageDigest = J.String(row,"sha256") ?? throw Gap("s1-image-digest-unavailable");
        string imagePath = files.Local(context.Root,relative);
        var bytes = files.BytesAbsolute(imagePath,32*1024*1024);
        CheckInventory(imagePath,row,bytes.Length);
        bool overlay = imageDigest != rendererDigest;
        if (overlay)
        {
            const string registration = "references/native-display-bindings.json";
            var registrationRow = UniqueInventory(registration);
            var registrationPath = files.Local(context.Root,registration);
            var registrationBytes = files.BytesAbsolute(registrationPath,2*1024*1024);
            CheckInventory(registrationPath,registrationRow,registrationBytes.Length);
            using var doc = JsonDocument.Parse(registrationBytes);
            var registrationDoc = doc.RootElement;
            if (J.String(registrationDoc,"schema") != "xingtu-s1-native-display-bindings-v1"
                || J.String(registrationDoc,"season") != entry.Season || J.String(registrationDoc,"mode") != entry.Mode
                || J.String(registrationDoc,"sourceVersion") != J.String(metadata,"sourceVersion")
                || J.String(registrationDoc,"producerContract") != "room-render-native-plus-doorframe-playerstart-v1"
                || J.Get(registrationDoc,"coordinateChanges") is not { ValueKind: JsonValueKind.Array } changes || changes.GetArrayLength() != 0)
                throw Gap("s1-overlay-registration-scope-mismatch");
            var matches = Array(J.Get(registrationDoc,"entries")).Where(r => J.String(J.Get(r,"binding"),"mapId") == entry.MapId
                && J.String(J.Get(r,"binding"),"floor") == entry.Floor).ToArray();
            if (matches.Length != 1) throw Gap("s1-overlay-registration-binding-unavailable");
            var record = matches[0];
            var referenceInventory = UniqueInventory(referenceRelative);
            files.RequireDigest(files.Local(context.Root,referenceRelative),J.String(referenceInventory,"sha256"));
            if (!J.Equal(J.Get(record,"binding"),J.Get(metadata,"binding")) || !J.Equal(J.Get(record,"canvas"),canvas)
                || J.String(record,"referenceResource") != referenceRelative || J.String(record,"referenceSha256") != J.String(referenceInventory,"sha256")
                || J.String(record,"rendererImageSha256") != rendererDigest || J.String(record,"displayResource") != relative
                || J.String(record,"displaySha256") != imageDigest || J.String(geometry,"alphaRule") != "alpha>0"
                || !DisplayAffine.TryRead(J.Get(record,"matrixDisplayToReference"),out var bridge) || !bridge.AlmostEquals(DisplayAffine.Identity,0))
                throw Gap("s1-overlay-reference-bridge-mismatch");
            RequireVersions(record,metadata,false);
        }
        var info = raster.Inspect(bytes,overlay ? mask : null,width,height);
        if (info.Width != width || info.Height != height || info.Channels is not (3 or 4) || !info.HasVisiblePixels
            || overlay && info.FootprintMatches != true) throw Gap("s1-image-pixels-or-footprint-mismatch");
        return imagePath;

        JsonElement UniqueInventory(string relativePath)
        {
            var rows = inventory.Where(r => J.String(r,"path") == relativePath).ToArray();
            return rows.Length == 1 ? rows[0] : throw Gap("s1-resource-not-uniquely-inventoried:"+relativePath);
        }
        void CheckInventory(string path,JsonElement row,int size)
        {
            if (J.Number(row,"bytes") != size) throw Gap("s1-resource-byte-count-mismatch");
            files.RequireDigest(path,J.String(row,"sha256"));
        }
    }

    private static void RequireVersions(JsonElement? row,JsonElement metadata,bool allowMissingBase)
    {
        foreach (string key in new[] {"structureVersionKey","sourceVersion","baseSnapshot"})
        {
            var expected = J.String(metadata,key);
            var actual = J.String(row,key);
            if (key == "baseSnapshot" && allowMissingBase && actual is null) continue;
            if (string.IsNullOrEmpty(expected) || actual != expected) throw Gap("display-resource-version-mismatch:"+key);
        }
    }

    private static void RequireAuthorBasisVersions(JsonElement? row,JsonElement metadata)
    {
        // The original author profile chart owns structureVersionKey. Its
        // sourceVersion/baseSnapshot are optional: sourceVersion is required
        // on the typed reference, coordinate map floor and author layer above.
        // load_shared_version_context does not require this later duplication
        // on a profile chart (the production Suju baked chart omits it).
        foreach(string key in new[]{"structureVersionKey","sourceVersion","baseSnapshot"})
        {
            var actual=J.String(row,key);
            if(key!="structureVersionKey"&&J.Get(row,key) is null)continue;
            var expected=J.String(metadata,key);
            if(string.IsNullOrEmpty(expected)||actual!=expected)throw Gap("display-resource-version-mismatch:"+key);
        }
    }

    private static void RequireNativeSourceBinding(ResourceReads files,Context context,ResourceCatalogEntry entry,
        JsonElement? declared,JsonElement metadata,JsonElement? canvas,string referenceRelative)
    {
        // S0 nightmare / S1 producers duplicate these bindings in their coordinate pack.
        // The difficult producer instead owns them in the declared source manifest.
        // Never interpret an omitted coordinate-pack field as a conflicting native value.
        bool hasLayout = J.Get(declared,"sourceLayout") is not null;
        bool hasWorldFloor = J.Get(declared,"worldFloor") is not null;
        if (hasLayout && J.String(declared,"sourceLayout") != J.String(canvas,"layout"))
            throw Gap("coordinate-source-layout-mismatch");
        if (hasWorldFloor && J.Number(declared,"worldFloor") != J.Number(canvas,"worldFloor"))
            throw Gap("coordinate-source-world-floor-mismatch");
        string? sourceRelative = J.String(J.Get(context.Manifest,"runtimeInterfaces"),"sourceManifest");
        if (sourceRelative is null)
        {
            if (!hasLayout || !hasWorldFloor) throw Gap("native-source-layout-floor-binding-unavailable");
            return;
        }
        // Reading via ResourceReads includes this independently selected declaration in
        // the result's existing content snapshots and IsCurrent() revalidation.
        var source = files.Json(context.Root,sourceRelative);
        if (J.String(source,"mode") != entry.Mode) throw Gap("native-source-manifest-mode-mismatch");
        var maps = Array(J.Get(source,"maps")).Where(m => J.String(m,"id") == entry.MapId).ToArray();
        if (maps.Length != 1 || J.String(maps[0],"sourceLayout") != J.String(canvas,"layout")
            || J.String(maps[0],"sourceVersion") != J.String(metadata,"sourceVersion")
            || J.String(maps[0],"identityProfileId") != entry.Profile)
            throw Gap("native-source-manifest-map-binding-mismatch");
        var floor = J.Get(J.Get(maps[0],"floors"),entry.Floor);
        if (floor is null || J.Number(floor,"sourceHeight") != J.Number(canvas,"worldFloor")
            || J.String(floor,"referenceResource") != referenceRelative)
            throw Gap("native-source-manifest-floor-binding-mismatch");
        RequireVersions(floor,metadata,false);
    }
    internal sealed record Context(string Root,string Coordinates,string Index,string StructureDisplay,string AuthorCatalog,JsonElement? Manifest);
    internal static Context ReadContext(ResourceReads files,ResourceCatalogEntry entry)
    {
        if (entry.Season == "s0" && entry.Mode == "nightmare")
            return new(files.Root,"references/nightmare-canonical-coordinate-pack.json","references/nightmare-shared-structure-index.json",
                "references/nightmare-structure-display.json","references/nightmare-author-route-catalog.json",null);
        string relative = entry.Season == "s0" && entry.Mode == "difficult" ? "" : $"seasons/{entry.Season}/{entry.Mode}";
        string root = relative.Length == 0 ? files.Root : files.Local(files.Root,relative);
        var manifest = files.Json(root,relative.Length == 0 ? "difficult-native-manifest.json" : "native-map-manifest.json");
        if (relative.Length > 0 && (J.String(manifest,"season") != entry.Season || J.String(manifest,"mode") != entry.Mode))
            throw Gap("display-manifest-context-mismatch");
        var api = J.Get(manifest,"runtimeInterfaces");
        return new(root,Required("coordinatePack"),Required("typedIndex"),Required("neutralDisplay"),Required("authorCatalog"),manifest);
        string Required(string key) => J.String(api,key) ?? throw Gap("display-interface-missing:"+key);
    }
    private static JsonElement[] Array(JsonElement? value) => value is { ValueKind: JsonValueKind.Array } a ? a.EnumerateArray().ToArray() : [];
    private static InvalidDataException Gap(string reason) => new(reason);

    internal sealed class ResourceReads(string root)
    {
        public string Root { get; } = Path.GetFullPath(root);
        public List<DisplayResourceSnapshot> Snapshots { get; } = [];
        public string Local(string scope,string relative)
        {
            if (string.IsNullOrWhiteSpace(relative) || Path.IsPathRooted(relative)
                || relative.Split(['/','\\'],StringSplitOptions.RemoveEmptyEntries).Any(p => p is "." or ".."))
                throw Gap("display-resource-path-invalid");
            string path = Path.GetFullPath(Path.Combine(scope,relative));
            string prefix = Path.TrimEndingDirectorySeparator(Path.GetFullPath(scope))+Path.DirectorySeparatorChar;
            if (!path.StartsWith(prefix,StringComparison.OrdinalIgnoreCase)) throw Gap("display-resource-path-escape");
            return path;
        }
        public byte[] BytesAbsolute(string path,int maxBytes)
        {
            var info = new FileInfo(path);
            if (!info.Exists) throw Gap("display-resource-file-missing:"+Path.GetRelativePath(Root,path));
            if (info.Length <= 0 || info.Length > maxBytes) throw Gap("display-resource-size-invalid");
            byte[] bytes = File.ReadAllBytes(path);
            if (bytes.Length != info.Length) throw Gap("display-resource-changed-during-read");
            string digest = Convert.ToHexStringLower(SHA256.HashData(bytes));
            var snapshot = new DisplayResourceSnapshot(path,bytes.Length,digest);
            var previous = Snapshots.Find(s => string.Equals(s.Path,path,StringComparison.OrdinalIgnoreCase));
            if (previous is not null && (previous.Length!=snapshot.Length || previous.Sha256!=snapshot.Sha256)) throw Gap("display-resource-changed-during-resolution");
            if (previous is null) Snapshots.Add(snapshot);
            return bytes;
        }
        public JsonElement Json(string scope,string relative)
        {
            using var doc = JsonDocument.Parse(BytesAbsolute(Local(scope,relative),32*1024*1024),new JsonDocumentOptions {MaxDepth=64});
            if (doc.RootElement.ValueKind != JsonValueKind.Object) throw Gap("display-resource-json-not-object");
            return doc.RootElement.Clone();
        }
        public void RequireDigest(string path,string? expected)
        {
            var snapshot = Snapshots.Single(s => string.Equals(s.Path,path,StringComparison.OrdinalIgnoreCase));
            if (expected is null || expected.Length != 64 || snapshot.Sha256 != expected) throw Gap("display-resource-hash-mismatch");
        }
    }
}
