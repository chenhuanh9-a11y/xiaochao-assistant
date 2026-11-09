using OpenCvSharp;
using System.Text.Json.Nodes;

namespace StarMap.Vision;

internal static class MainEntryVerification
{
    internal static JsonObject Verify(Mat frame, Observation observation, JsonNode hypothesis, StructureGeometry reference)
    {
        double s=Numerics.Scalar(hypothesis["scale"]!);
        var t=hypothesis["translation"]!;
        double[][] initial=[[s,0,Numerics.Scalar(t[0]!)],[0,s,Numerics.Scalar(t[1]!)],[0,0,1]];
        var matrix=initial;
        var regions=Regions(initial);
        var lines=Geometry.Lines(frame,observation);
        JsonObject? wall=null,residual=null;
        if(regions["regionalEdgeVerified"]!.GetValue<bool>())
        {
            var (result, fitted)=Fit(initial,provideSourceLines:true);
            wall=result;
            if(fitted!=null) matrix=fitted;
            Rect? visible=EntryObservation.VisibleBounds(frame);
            JsonObject Residual(double[][] pose)
            {
                if(visible==null) return new JsonObject {["verified"]=false,["reason"]="no-visible-map-component"};
                // The independent residual crop comes from visible_map_component, not the entry search component.
                using var current = new Observation(frame,new Mat(),visible.Value,new JsonObject());
                return FrameResidual.Evaluate(frame,current,reference.Validation,pose);
            }
            residual=Residual(matrix);
            if(!residual["verified"]!.GetValue<bool>() && wall["scaleRefinement"]?["accepted"]?.GetValue<bool>()==true)
            {
                var (fallback,recovered)=Fit(initial,provideSourceLines:false);
                matrix=recovered??initial;
                wall["scaleRefinement"]!["authorRejected"]=true;
                wall["scaleRefinement"]!["accepted"]=false;
                wall["fallbackReason"]=fallback["reason"]!.DeepClone();
                wall["success"]=fallback["success"]!.DeepClone();
                residual=Residual(matrix);
            }
        }
        var raster=MainEntryWallRaster.Check(lines,reference,matrix);
        return new() { ["success"]=regions["regionalEdgeVerified"]!.GetValue<bool>() && wall?["success"]?.GetValue<bool>()==true &&
            residual?["verified"]?.GetValue<bool>()==true && raster["verified"]!.GetValue<bool>(), ["reason"]="entry-current-frame-validation",
            ["regions"]=regions, ["residual"]=residual, ["wallRefinement"]=wall, ["entryWalls"]=raster, ["matrixSourceToClient"]=Matrix(matrix) };

        JsonObject Regions(double[][] pose)
        {
            using var projected=Geometry.Warp(reference.Clean,pose,frame.Size());
            using var excluded=reference.Unknown==null?null:Geometry.Warp(reference.Unknown,pose,frame.Size());
            return RegionalGeometry.Evaluate(frame,observation,projected,excluded);
        }

        (JsonObject Report,double[][]? Matrix) Fit(double[][] pose,bool provideSourceLines)
        {
            var known=reference.KnownBoundary;
            IReadOnlyList<Wall>? declared=null;
            if(provideSourceLines && known!=null && reference.Unknown!=null)
            {
                declared=reference.NativeWalls;
                if(declared==null)
                {
                    using var supported=new Mat();using var kernel=Geometry.Kernel(3);
                    Cv2.Dilate(known,supported,kernel);
                    supported.SetTo(Scalar.Black,reference.Unknown);
                    declared=PartialWalls.ExplicitWallSegments(reference.Clean,known,supported);
                }
            }
            IReadOnlyList<Wall> source;
            if(known!=null)
            {
                var native=declared??PartialWalls.ExplicitWallSegments(reference.Clean,known);
                source=native.Select(line=>line with {
                    Normal=line.Normal*pose[line.Axis][line.Axis]+pose[line.Axis][2],
                    Lo=line.Lo*pose[1-line.Axis][1-line.Axis]+pose[1-line.Axis][2],
                    Hi=line.Hi*pose[1-line.Axis][1-line.Axis]+pose[1-line.Axis][2] }).ToArray();
            }
            else
            {
                using var projected=Geometry.Warp(reference.Clean,pose,frame.Size());
                source=Geometry.VisibleWalls(projected);
            }
            var groups=new List<PartialWalls.Plane>();
            foreach(var q in lines)
            {
                var match=source.Where(v=>v.Axis==q.Axis && v.Sign==q.Sign && Math.Abs(q.Normal-v.Normal)<=48 &&
                    (known!=null ? Math.Min(q.Hi,v.Hi)-Math.Max(q.Lo,v.Lo)+1>=Math.Min(12,Math.Min(q.Hi-q.Lo-1,v.Hi-v.Lo-1)) :
                     Math.Min(q.Hi,v.Hi)-Math.Max(q.Lo,v.Lo)>=Math.Min(12,q.Hi-q.Lo-1)))
                    .OrderBy(v=>Math.Abs(q.Normal-v.Normal)).FirstOrDefault();
                if(match!=null && !groups.Any(g=>g.Axis==q.Axis && g.Sign==q.Sign && Math.Abs(g.QueryNormal-q.Normal)<=3))
                    groups.Add(new(q.Axis,q.Sign,q.Normal,match.Normal,q.Normal-match.Normal,[q.Lo,q.Hi],[match.Lo,match.Hi]));
            }
            JsonObject? translation=null,similarity=null;
            var held=new List<double>();
            (JsonObject,double[][]?) End(bool success,string reason,double[][]? m=null) => (new JsonObject {
                ["success"]=success,["reason"]=reason,["refinement"]=translation,["scaleRefinement"]=similarity },success?m:null);
            for(int axis=0;axis<2;axis++)
            {
                var planes=groups.Where(g=>g.Axis==axis).ToArray();
                if(planes.Length<2 || planes.Max(g=>g.QueryNormal)-planes.Min(g=>g.QueryNormal)<24)
                    return End(false,"partial-wall-independent-planes-insufficient");
                for(int i=0;i<planes.Length;i++) held.Add(Math.Abs(planes[i].Delta-
                    Numerics.Median(planes.Where((_,j)=>i!=j).Select(g=>g.Delta))));
            }
            if(!Valid(held)) return End(false,"partial-wall-held-out-failed");
            if(!Valid(groups.Select(g=>Math.Abs(g.Delta)))) return End(false,"partial-wall-candidate-failed");
            bool scaleAccepted=false;
            bool regional=regions["regionalEdgeVerified"]!.GetValue<bool>();
            JsonObject? acceptedRaster=null;
            if(declared!=null)
            {
                similarity=PartialWalls.FitPartialWallSimilarity(groups,declared,pose);
                if(similarity["success"]!.GetValue<bool>())
                {
                    var changed=Numerics.Matrix(similarity["matrix"]!.AsArray());
                    var after=Regions(changed);
                    var measured=reference.Unknown==null?null:MainEntryWallRaster.Check(lines,reference,changed);
                    scaleAccepted=after["regionalEdgeVerified"]!.GetValue<bool>() && (measured==null || measured["verified"]!.GetValue<bool>());
                    similarity["accepted"]=scaleAccepted; similarity["regions"]=after; similarity["knownWallRaster"]=measured;
                    if(scaleAccepted){ pose=changed; regional=true; acceptedRaster=measured; }
                }
            }
            if(!scaleAccepted)
            {
                translation=PartialWalls.FitPartialWallTranslation(groups);
                if(translation["success"]!.GetValue<bool>())
                {
                    var changed=pose.Select(x=>x.ToArray()).ToArray();
                    changed[0][2]+=Numerics.Scalar(translation["correction"]![0]!);
                    changed[1][2]+=Numerics.Scalar(translation["correction"]![1]!);
                    translation["translation"]=new JsonArray(changed[0][2],changed[1][2]);
                    regional=Regions(changed)["regionalEdgeVerified"]!.GetValue<bool>();
                    if(regional) pose=changed;
                }
            }
            if(reference.Unknown!=null && !(acceptedRaster??MainEntryWallRaster.Check(lines,reference,pose))["verified"]!.GetValue<bool>())
                return End(false,"partial-known-wall-conflict");
            return End(regional,scaleAccepted?"partial-wall-native-similarity":"partial-wall-fixed-linear",pose);
        }
    }

    private static bool Valid(IEnumerable<double> values)
    {
        var errors=values.Order().ToArray();double q=.9*(errors.Length-1);int i=(int)Math.Floor(q);
        return Numerics.Median(errors)<=3 && errors[i]+(errors[(int)Math.Ceiling(q)]-errors[i])*(q-i)<=8 && errors[^1]<=20;
    }
    private static JsonArray Matrix(double[][] matrix)=>new(matrix.Select(row=>(JsonNode)new JsonArray(row.Select(v=>(JsonNode)JsonValue.Create(v)!).ToArray())).ToArray());
}
