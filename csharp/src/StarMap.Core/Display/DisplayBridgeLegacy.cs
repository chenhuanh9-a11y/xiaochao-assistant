using System.Text.Json;
using StarMap.Core.Resources;

namespace StarMap.Core.Display;

public sealed partial class DisplayBridgeResolver
{
    private DisplayBridgeResult ResolveLegacyDisplay(ResourceCatalog catalog,ResourceCatalogEntry entry,string profile,DisplayAffine referenceToClient,string variant,bool neutral)
    {
        var files=new ResourceReads(catalog.RootPath);var context=ReadContext(files,entry);
        var loaded=catalog.LoadSharedStructure(entry);
        if(!loaded.Success||loaded.Value!.CleanMask is null)return new(false,loaded.Error?.Code??"legacy-clean-reference-unavailable",entry.StableKey,profile);
        var reference=loaded.Value;var canvas=J.Get(reference.Metadata,"canvas");var declared=entry.CoordinateMetadata;
        int height=reference.CleanMask!.Shape[0],width=reference.CleanMask.Shape[1];
        var pack=files.Json(context.Root,context.Coordinates);var adapter=new AuthorCoordinateAdapter(pack);
        var current=J.Get(J.Get(J.Get(J.Get(pack,"maps"),entry.MapId),"floors"),entry.Floor);
        if(!J.Equal(current,declared)||J.String(current,"coordinateSpace")!=J.String(canvas,"coordinateSpace")
            ||J.Number(current,"coordinateHeight")!=height||J.Number(current,"coordinateWidth")!=width
            ||J.String(current,"referenceLayerRole")!="legacy-clean-binary-only-not-native"
            ||J.String(current,"referenceResource")!=entry.StructureNpzRelativePath
            ||J.String(entry.LegacyCanvasMetadata,"sourceClass")!="legacy-clean-working-draft"
            ||J.Get(entry.LegacyCanvasMetadata,"sourceLayout") is not {ValueKind:JsonValueKind.Null})
            throw Gap("legacy-clean-display-reference-binding-mismatch");
        RequireVersions(current,reference.Metadata,false);
        string path=entry.StructureNpzPath??throw Gap("legacy-clean-reference-file-missing");files.BytesAbsolute(path,128*1024*1024);
        files.RequireDigest(path,J.String(entry.LegacyCanvasMetadata,"resourceSHA256"));
        if(!adapter.TryProfileToCanonical("shared-structure",entry.MapId,entry.Floor,out var referenceToCanonical,out var reason))throw Gap(reason);
        var legacy=J.Get(J.Get(context.Manifest,"runtimeInterfaces"),"legacyClean");
        if(!J.Equal(J.Get(J.Get(legacy,"referenceCanvas"),entry.Floor),entry.LegacyCanvasMetadata))throw Gap("legacy-clean-canvas-declaration-changed");
        if(J.String(legacy,"index") is { } index)
        {
            var currentIndex=files.Json(catalog.RootPath,index);
            var rows=DisplayAuthorInventory.Rows(J.Get(currentIndex,"entries")).Where(r=>J.String(r,"profileId")==entry.Profile&&J.String(r,"mapId")==entry.MapId
                &&J.String(r,"floor")==entry.Floor&&(J.String(r,"variantId")??"variant-01")=="variant-01").ToArray();
            if(rows.Length!=1||!J.Equal(rows[0],entry.StructureIndexMetadata))throw Gap("legacy-clean-index-declaration-changed");
        }
        if(!DisplayAffine.TryRead(J.Get(current,"matrixCanonicalToLegacyRecognition"),out var canonicalToLegacy)
            ||!(referenceToCanonical*canonicalToLegacy).AlmostEquals(DisplayAffine.Identity))throw Gap("legacy-clean-canonical-chart-inverse-mismatch");
        DisplayAffine displayToCanonical=referenceToCanonical;string? image=null;int channels=4;
        if(profile!="shared-structure"&&!neutral)
        {
            var inventory=DisplayAuthorInventory.Load(catalog,entry,files,raster);
            var layer=inventory.FindLayer(profile,entry.MapId,entry.Floor,variant);
            if(layer is null||!inventory.Profiles.TryGetValue(profile,out var selected)||!selected.Aligned
                ||!adapter.TryProfileToCanonical(profile,entry.MapId,entry.Floor,out displayToCanonical,out reason))throw Gap("legacy-author-display-binding-unavailable");
            var matrix=DisplayAuthorInventory.ReadAffine(J.Get(layer.Metadata,"matrixDisplayToLegacyReference"),1e-12);
            if(!(referenceToCanonical*matrix).AlmostEquals(displayToCanonical))throw Gap("legacy-author-display-coordinate-mismatch");
            RequireVersions(layer.Metadata,reference.Metadata,false);image=layer.Path;
            var pixels=raster.Inspect(files.BytesAbsolute(image,128*1024*1024));channels=pixels.Channels;
            files.RequireDigest(image,J.String(layer.Metadata,"sha256"));
            if(channels!=4||pixels.Width!=J.Number(layer.Metadata,"width")||pixels.Height!=J.Number(layer.Metadata,"height"))throw Gap("legacy-author-display-image-invalid");
            width=pixels.Width;height=pixels.Height;
        }
        var displayToReference=referenceToCanonical.Inverse()*displayToCanonical;var snapshots=files.Snapshots.ToArray();
        if(!snapshots.All(s=>s.IsCurrent()))throw Gap("display-resources-changed-during-resolution");
        return new(true,"accepted",entry.StableKey,profile,image,image is null?"neutral-structure":"author-floor-display",null,
            displayToCanonical,displayToReference,referenceToClient*displayToReference,referenceToClient*referenceToCanonical.Inverse(),
            System.Array.AsReadOnly(snapshots),width,height)
            {CanonicalPackId=adapter.PackId,ReferenceToCanonical=referenceToCanonical,Reference=reference,ImageChannels=channels};
    }
}
