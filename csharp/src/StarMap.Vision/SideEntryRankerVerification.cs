using OpenCvSharp;
using System.Text.Json.Nodes;

namespace StarMap.Vision;

internal static class SideEntryRankerVerification
{
    // This is MapLocalEntryIdentity.verify's fixed-pose path. Main entry's
    // extra wall/scale refinement is deliberately not part of side validation.
    internal static JsonObject Verify(Mat frame,Observation current,JsonObject row,StructureGeometry reference)
    {
        if(SideEntryRankerIndex.Contradicted(row)) return new() { ["success"]=false,["reason"]="side-door-boundary-contradiction" };
        var matrix=Numerics.Matrix(row["matrixSourceToClient"]!.AsArray());
        var lines=Geometry.Lines(frame,current);
        var walls=MainEntryWallRaster.Check(lines,reference,matrix);
        using var projected=Geometry.Warp(reference.Clean,matrix,frame.Size());
        using var unknown=reference.Unknown==null?null:Geometry.Warp(reference.Unknown,matrix,frame.Size());
        var regions=RegionalGeometry.Evaluate(frame,current,projected,unknown);
        bool compatible=regions["regionalEdgeVerified"]!.GetValue<bool>() && walls["verified"]!.GetValue<bool>();
        JsonObject? residual=null;
        if(compatible)
        {
            var bounds=EntryObservation.VisibleBounds(frame);
            // The production shared provider's author-visible callback is
            // authoritative, including failure. Do not silently re-extract.
            if(bounds==null) return new() { ["success"]=false,["reason"]="side-author-observation-unavailable" };
            using var observation=new Observation(frame,new Mat(),bounds.Value,new());
            residual=FrameResidual.Evaluate(frame,observation,reference.Validation,matrix);
        }
        var variants=new JsonArray(new JsonObject { ["name"]="source-present",["basis"]=null,["regions"]=regions.DeepClone(),
            ["walls"]=walls.DeepClone(),["verified"]=compatible });
        return new() { ["success"]=compatible && residual?["verified"]?.GetValue<bool>()==true,["reason"]="side-current-frame-evidence",
            ["regions"]=regions,["residual"]=residual,["validationPolicy"]="whole-structure-and-shared-structure",
            ["entryWalls"]=new JsonObject { ["verified"]=walls["verified"]!.DeepClone(),
                ["variants"]=new JsonArray(new JsonObject { ["name"]="source-present",["result"]=walls,["basis"]=null }),
                ["scope"]="all current observed fragments, not only fitted passport planes" },
            ["structureVariants"]=variants,["compatibleStructureVariants"]=compatible?new JsonArray("source-present"):new JsonArray(),
            ["referenceChannel"]="typed-shared-structure-validation-mask",["matrixSourceToClient"]=row["matrixSourceToClient"]!.DeepClone() };
    }
}
