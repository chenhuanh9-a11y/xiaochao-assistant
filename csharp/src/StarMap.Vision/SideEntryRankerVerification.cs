using OpenCvSharp;
using System.Text.Json.Nodes;
using System.Text.Json;

namespace StarMap.Vision;

internal static class SideEntryRankerVerification
{
    // This is MapLocalEntryIdentity.verify's fixed-pose path. Main entry's
    // extra wall/scale refinement is deliberately not part of side validation.
    internal static JsonObject Verify(Mat frame,Observation current,JsonObject row,StructureGeometry reference,JsonElement? passport=null,
        IReadOnlyList<(string Name,Mat Clean,JsonNode? Basis)>? suppliedVariants=null)
    {
        if(SideEntryRankerIndex.Contradicted(row)) return new() { ["success"]=false,["reason"]="side-door-boundary-contradiction" };
        var matrix=Numerics.Matrix(row["matrixSourceToClient"]!.AsArray());
        bool local=row["entryEvidence"]?["localGeometryVerified"]?.GetValue<bool>()==true;
        var variants=suppliedVariants?.ToList()??Variants(row,reference,passport);
        var owned=suppliedVariants==null?variants.Where(v=>!ReferenceEquals(v.Clean,reference.Clean)).Select(v=>v.Clean).ToArray():[];
        try
        {
        var lines=Geometry.Lines(frame,current); var walls=new JsonArray(); var structural=new JsonArray(); var compatibleNames=new JsonArray();
        JsonObject regions=new() { ["regionalEdgeVerified"]=false };
        if(!local)
        {
            using var unknown=reference.Unknown==null?null:Geometry.Warp(reference.Unknown,matrix,frame.Size());
            foreach(var variant in variants)
            {
                if(variant.Clean.Size()!=reference.Clean.Size() || variant.Clean.Type()!=reference.Clean.Type())
                    throw new ArgumentException("A passport occupancy variant must retain its typed source canvas and mask type.");
                var measured=MainEntryWallRaster.Check(lines,reference,matrix,variant.Clean);
                using var projected=Geometry.Warp(variant.Clean,matrix,frame.Size());
                var regional=RegionalGeometry.Evaluate(frame,current,projected,unknown);
                bool ok=regional["regionalEdgeVerified"]!.GetValue<bool>() && measured["verified"]!.GetValue<bool>();
                walls.Add(new JsonObject { ["name"]=variant.Name,["result"]=measured.DeepClone(),["basis"]=variant.Basis?.DeepClone() });
                structural.Add(new JsonObject { ["name"]=variant.Name,["basis"]=variant.Basis?.DeepClone(),["regions"]=regional,
                    ["walls"]=measured,["verified"]=ok });
                if(ok) compatibleNames.Add(variant.Name);
            }
            regions=(structural.FirstOrDefault(v=>v!["verified"]!.GetValue<bool>())??structural.FirstOrDefault())?["regions"]?.DeepClone().AsObject()??regions;
        }
        bool compatible=compatibleNames.Count>0;
        JsonObject? residual=null;
        if(compatible || local)
        {
            var bounds=EntryObservation.VisibleBounds(frame);
            // The production shared provider's author-visible callback is
            // authoritative, including failure. Do not silently re-extract.
            if(bounds==null) return new() { ["success"]=false,["reason"]="side-author-observation-unavailable" };
            using var observation=new Observation(frame,new Mat(),bounds.Value,new());
            residual=FrameResidual.Evaluate(frame,observation,reference.Validation,matrix);
        }
        var result=new JsonObject { ["success"]=(compatible||local) && residual?["verified"]?.GetValue<bool>()==true,["reason"]="side-current-frame-evidence",
            ["regions"]=regions,["residual"]=residual,["validationPolicy"]=local?"local-fit-and-shared-structure":"whole-structure-and-shared-structure",
            ["matrixSourceToClient"]=row["matrixSourceToClient"]!.DeepClone() };
        if(!local)
        {
            result["entryWalls"]=new JsonObject { ["verified"]=walls.Any(v=>v!["result"]!["verified"]!.GetValue<bool>()),["variants"]=walls,
                ["scope"]="all current observed fragments, not only fitted passport planes" };
            if(variants.Count==0) { result["entryWalls"]!["reason"]="entry-passport-unavailable"; result["entryWalls"]!.AsObject().Remove("scope"); }
            result["structureVariants"]=structural; result["compatibleStructureVariants"]=compatibleNames;
            result["referenceChannel"]="typed-shared-structure-validation-mask";
        }
        return result;
        }
        finally { foreach(var mask in owned) mask.Dispose(); }
    }

    private static List<(string Name,Mat Clean,JsonNode? Basis)> Variants(JsonObject row,StructureGeometry reference,JsonElement? passport)
    {
        if(row["entryEvidence"]!=null && passport==null) return [];
        var variants=new List<(string,Mat,JsonNode?)> { ("source-present",reference.Clean,null) };
        if(passport is not { } item || !item.TryGetProperty("optionalRooms",out var rooms)) return variants;
        try
        {
            double unit=item.GetProperty("unit").GetDouble(); var center=SideEntryRankerSource.Point(item.GetProperty("center"));
            foreach(var room in rooms.EnumerateArray())
            {
                var absent=reference.Clean.Clone();
                try
                {
                    var outline=room.GetProperty("outline").EnumerateArray().Select(p=>SideEntryRankerSource.Point(p)*unit+center)
                        .Select(p=>new Point(Numerics.Rint(p.X),Numerics.Rint(p.Y))).ToArray();
                    Cv2.FillPoly(absent,[outline],Scalar.Black);
                    variants.Add(("absent:"+room.GetProperty("instance").GetString(),absent,JsonNode.Parse(room.GetProperty("basis").GetRawText())));
                }
                catch { absent.Dispose(); throw; }
            }
            return variants;
        }
        catch { foreach(var variant in variants.Skip(1)) variant.Item2.Dispose(); throw; }
    }
}
