using System.Collections.ObjectModel;
using StarMap.Core.Display;
using StarMap.Core.Resources;
using StarMap.Vision;

namespace StarMap.Runtime;

/// <summary>One immutable installed floor source and its resource-declared placement. No live pose.</summary>
public sealed record RuntimeSceneFloor(string Floor, PreparedDisplayLayer Material,
    DisplayAffine ReferenceToCommon, DisplayAffine DisplayToCommon);

/// <summary>All available sources for one map/preference/resource owner. Never a display grant or solver prior.</summary>
public sealed class RuntimeSceneSources
{
    internal RuntimeSceneSources(string map, string profile, string space, Dictionary<string, RuntimeSceneFloor> floors,
        Dictionary<string, string> unavailable)
    { MapId=map; Profile=profile; SpaceId=space; Floors=new ReadOnlyDictionary<string, RuntimeSceneFloor>(floors);
        Unavailable=new ReadOnlyDictionary<string,string>(unavailable); }
    public string MapId { get; }
    public string Profile { get; }
    public string SpaceId { get; }
    public IReadOnlyDictionary<string, RuntimeSceneFloor> Floors { get; }
    public IReadOnlyDictionary<string, string> Unavailable { get; }
    internal bool IsCurrent() => Floors.Values.All(f => f.Material.IsCurrent());
    internal bool SameSources(Dictionary<string,RuntimeSceneFloor> floors,Dictionary<string,string> unavailable) =>
        Floors.Count==floors.Count && Unavailable.Count==unavailable.Count
        && Unavailable.All(p=>unavailable.TryGetValue(p.Key,out var reason) && reason==p.Value)
        && Floors.All(p=>floors.TryGetValue(p.Key,out var floor) && ReferenceEquals(p.Value.Material.Pixels,floor.Material.Pixels)
            && p.Value.ReferenceToCommon==floor.ReferenceToCommon && p.Value.DisplayToCommon==floor.DisplayToCommon)
        && IsCurrent();
}

/// <summary>Read-only projection derived from the current verified reference pose, with one shared root for every floor.</summary>
public sealed record RuntimeSceneFrame(RuntimeSceneSources Sources, string SelectedFloor, DisplayAffine CommonToClient);

public sealed partial class LiveRuntime
{
    private RuntimeSceneSources? PrepareMapScene(BoundResources bound, ResourceCatalogEntry current,
        PreparedRuntimeDisplay display, CancellationToken cancellation)
    {
        if (display.Material.Layer is not { } material) return null;
        var common=CommonFloorSpace.Read(current,display.NativeReference,out _);
        if(common is null) return null; // Known legacy floor-local data remains independently drawable.
        string profile=display.Selection.PreferredProfileId;
        var cached=bound.Scenes.FirstOrDefault(s => s.MapId==current.MapId && s.Profile==profile && s.SpaceId==common.SpaceId
            && s.Floors.TryGetValue(current.Floor,out var f) && ReferenceEquals(f.Material.Pixels,material.Pixels));
        if(cached is not null && cached.Unavailable.Count==0 && cached.IsCurrent())
        { bound.Scenes.Remove(cached);bound.Scenes.Insert(0,cached);return cached; }
        var floors=new Dictionary<string,RuntimeSceneFloor>(StringComparer.Ordinal);
        var unavailable=new Dictionary<string,string>(StringComparer.Ordinal);
        foreach(string floor in bound.Passports.Floors)
        {
            cancellation.ThrowIfCancellationRequested();
            var entry=bound.Catalog.Find(bound.Passports.Mode,bound.Passports.Season,bound.Passports.MapProfiles[current.MapId],current.MapId,floor);
            if(entry is null){unavailable[floor]="target-floor-resource-unavailable";continue;}
            var reference=floor==current.Floor?display.NativeReference:bound.LoadReference(entry).Value;
            if(reference is null){unavailable[floor]="target-floor-reference-unavailable";continue;}
            var space=CommonFloorSpace.Read(entry,reference,out string reason);
            if(space is null || space.SpaceId!=common.SpaceId){unavailable[floor]=space is null?reason:"common-space-binding-mismatch";continue;}
            var layer=material;
            if(floor!=current.Floor)
            {
                var selection=new DisplayLayerSelector(new OpenCvDisplayRasterValidator()).Select(bound.Catalog,entry,profile,DisplayAffine.Identity);
                var prepared=bound.DisplayMaterials.Prepare(selection,reference,cancellation);
                if(prepared.Layer is not { } other){unavailable[floor]=prepared.Reason;continue;}
                layer=other;
            }
            if(layer.Bridge.DisplayToReference is not { } toReference)
            {unavailable[floor]="display-reference-bridge-unavailable";continue;}
            floors.Add(floor,new(floor,layer,space.ReferenceToCommon,space.ReferenceToCommon*toReference));
        }
        cancellation.ThrowIfCancellationRequested();
        if(!floors.ContainsKey(current.Floor))return null;
        // A partial scene retries missing layers, but an unchanged gap must not
        // replace healthy resident bitmaps/Images on each fresh current-floor fit.
        var scene=cached is not null && cached.SameSources(floors,unavailable)?cached
            :new RuntimeSceneSources(current.MapId,profile,common.SpaceId,floors,unavailable);
        bound.Scenes.RemoveAll(s=>s.MapId==current.MapId && s.Profile==profile);
        bound.Scenes.Insert(0,scene);if(bound.Scenes.Count>2)bound.Scenes.RemoveAt(2);
        return scene;
    }

    private static RuntimeSceneFrame? ProjectMapScene(PreparedRuntimeDisplay display, string floor) =>
        display.Scene is { } scene && scene.Floors.TryGetValue(floor,out var layer)
            ? new(scene,floor,display.NativeToClient*layer.ReferenceToCommon.Inverse()) : null;
}
