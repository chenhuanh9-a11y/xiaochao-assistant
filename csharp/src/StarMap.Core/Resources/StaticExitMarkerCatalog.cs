using System.Text.Json;

namespace StarMap.Core.Resources;

public sealed record StaticExitMarkerPoint(string Kind, double X, double Y);
public sealed record StaticExitMarkerSet(bool CoverageComplete, IReadOnlyList<StaticExitMarkerPoint> Points, string Reason);

/// <summary>Normalizes the existing static_exit_markers payload into a bound S0 reference.
/// These are exit widget anchors, never doorway endpoints, door states or a measured pose.</summary>
public sealed class StaticExitMarkerCatalog
{
    private readonly JsonElement _source;

    public StaticExitMarkerCatalog(JsonElement source) => _source=source.Clone();

    public StaticExitMarkerSet Resolve(ResourceCatalogEntry entry,SharedStructureData reference)
    {
        StaticExitMarkerSet Unknown(string reason)=>new(false,Array.Empty<StaticExitMarkerPoint>(),reason);
        if(entry.Season!="s0")return Unknown("static-exit-season-unavailable");
        try
        {
            var meta=reference.Metadata;
            var common=CommonFloorSpace.Read(entry,reference,out _);
            if(common is null)return Unknown("static-exit-reference-space-unavailable");
            var geometry=meta.GetProperty("sourceGeometry");
            string? catalog=geometry.GetProperty("sourceCatalog").GetProperty("sourceFileSha256").GetString();
            if(string.IsNullOrEmpty(catalog)||catalog!=_source.GetProperty("sources").GetProperty("catalogSha256").GetString())
                return Unknown("static-exit-source-catalog-mismatch");
            var rules=_source.GetProperty("rules");
            var voxel=rules.GetProperty("voxelSize");
            var anchor=rules.GetProperty("widget").GetProperty("anchor");
            if(voxel.GetArrayLength()!=3||voxel[0].GetDouble()!=60||voxel[2].GetDouble()!=60
                ||anchor.GetArrayLength()!=2||anchor[0].GetDouble()!=.5||anchor[1].GetDouble()!=.5
                ||rules.GetProperty("iconPath").GetString()!="system/battle_adv/map/btn_exit_nml.png")
                return Unknown("static-exit-marker-semantics-unavailable");
            var canvas=meta.GetProperty("canvas");
            string layout=canvas.GetProperty("layout").ToString();
            int worldFloor=canvas.GetProperty("worldFloor").GetInt32();
            var records=_source.GetProperty("records").EnumerateArray()
                .Where(row=>row.GetProperty("levelId").ToString()==layout).ToArray();
            if(records.Length!=1)return Unknown("static-exit-layout-unavailable");
            var record=records[0];
            if(!record.GetProperty("staticExitInputsComplete").GetBoolean()||record.GetProperty("issues").GetArrayLength()!=0)
                return Unknown("static-exit-coverage-incomplete");
            var inverse=common.ReferenceToCommon.Inverse();
            var points=new List<StaticExitMarkerPoint>();
            foreach(var mark in record.GetProperty("marks").EnumerateArray())
            {
                if(mark.GetProperty("worldFloor").GetInt32()!=worldFloor)continue;
                string kind=mark.GetProperty("kind").GetString()!;
                if(kind is not ("front-door" or "side-door"))return Unknown("static-exit-kind-unavailable");
                var plane=mark.GetProperty("mapPlaneXZ");
                if(plane.GetArrayLength()!=2)return Unknown("static-exit-point-invalid");
                // source_renderer.js maps this 60px/voxel plane into world
                // X/right,Y/down. CommonFloorSpace retains product/crop bridges.
                var point=inverse.Apply(plane[0].GetDouble()*128d/60,-plane[1].GetDouble()*128d/60);
                if(!double.IsFinite(point.X)||!double.IsFinite(point.Y))return Unknown("static-exit-point-invalid");
                points.Add(new(kind,point.X,point.Y));
            }
            return points.Count==0?Unknown("static-exit-floor-unavailable")
                :new(true,points.AsReadOnly(),"source-bound-static-exit-widget-anchors");
        }
        catch(Exception error)when(error is KeyNotFoundException or InvalidOperationException or FormatException
            or ArgumentException or InvalidDataException or OverflowException)
        {return Unknown("static-exit-data-unavailable");}
    }
}
