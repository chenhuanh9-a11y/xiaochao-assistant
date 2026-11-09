using OpenCvSharp;
using System.Text.Json.Nodes;

namespace StarMap.Vision;

/// <summary>Directed distance of every measured current wall fragment to the unsimplified source raster.</summary>
internal static class MainEntryWallRaster
{
    internal static JsonObject Check(IReadOnlyList<Wall> lines,StructureGeometry reference,double[][] matrix,Mat? cleanOverride=null)
    {
        if(lines.Count==0) return new JsonObject {["verified"]=false,["reason"]="no-observed-fragments"};
        var fragments=new List<(int Direction,Point[] Points)>();
        foreach(var line in lines)
        {
            int start=(int)Math.Ceiling(line.Lo),end=(int)Math.Floor(line.Hi),n=Numerics.Rint(line.Normal);
            var points=Enumerable.Range(start,Math.Max(0,end-start+1)).Select(i=>line.Axis==0?new Point(n,i):new Point(i,n)).ToArray();
            if(points.Length>0) fragments.Add((line.Axis==0?(line.Sign>0?0:1):(line.Sign>0?3:2),points));
        }
        var all=fragments.SelectMany(f=>f.Points).ToArray();
        if(all.Length==0) throw new InvalidDataException("Observed fragments contain no raster samples.");
        int x0=all.Min(p=>p.X)-8,y0=all.Min(p=>p.Y)-8;
        var size=new Size(all.Max(p=>p.X)+9-x0,all.Max(p=>p.Y)+9-y0);
        double[][] shifted=[[matrix[0][0],0,matrix[0][2]-x0],[0,matrix[0][0],matrix[1][2]-y0],[0,0,1]];
        using var mask=Geometry.Warp(cleanOverride??reference.Clean,shifted,size);
        using var unknown=reference.Unknown==null?null:Geometry.Warp(reference.Unknown,shifted,size);
        var missing=unknown==null?new byte[checked(size.Width*size.Height)]:Geometry.Bytes(unknown);
        var (x,y)=Gradients(mask);
        float[]? wx=null,wy=null;
        if(reference.KnownBoundary!=null)
        {
            using var split=(cleanOverride??reference.Clean).Clone();
            split.SetTo(Scalar.Black,reference.KnownBoundary);
            using var projected=Geometry.Warp(split,shifted,size);
            (wx,wy)=Gradients(projected);
        }
        var distances=new float[4][];
        for(int direction=0;direction<4;direction++)
        {
            var inverse=new byte[x.Length];
            for(int i=0;i<x.Length;i++)
                inverse[i]=(byte)(missing[i]==0 && (FrameResidual.HasNormal(x[i],y[i],direction) ||
                    wx!=null && FrameResidual.HasNormal(wx[i],wy![i],direction)) ? 0 : 1);
            using var field=Geometry.FromBytes(size.Width,size.Height,inverse);
            using var distance=new Mat();
            Cv2.DistanceTransform(field,distance,DistanceTypes.L2,DistanceTransformMasks.Mask3);
            distances[direction]=Geometry.Floats(distance);
        }
        int measured=0; var errors=new List<double>(); var worst=new JsonArray();
        foreach(var f in fragments)
        {
            float max=float.NegativeInfinity; Point location=default;
            foreach(var p in f.Points)
            {
                int i=(p.Y-y0)*size.Width+p.X-x0;
                if(missing[i]!=0) continue;
                measured++; float error=distances[f.Direction][i];
                if(error>max){max=error;location=p;}
            }
            if(float.IsNegativeInfinity(max)) continue;
            errors.Add(max);
            worst.Add(new JsonObject { ["client"]=new JsonArray(location.X,location.Y),["direction"]=f.Direction,["errorPixels"]=(double)max });
        }
        return new JsonObject {["verified"]=errors.Count>0 && errors.Max()<=6,["maxPixels"]=errors.Count==0?null:JsonValue.Create(errors.Max()),
            ["fragmentMaxPixels"]=new JsonArray(errors.Select(v=>(JsonNode)JsonValue.Create(v)!).ToArray()),
            ["fragmentCount"]=lines.Count,["observedPixels"]=all.Length,["measuredPixels"]=measured,
            ["sourceUnknownPixels"]=all.Length-measured,["worstPoints"]=worst};
    }

    private static (float[] X,float[] Y) Gradients(Mat mask)
    {
        using var gx=new Mat();using var gy=new Mat();
        FrameResidual.Gradients(mask,gx,gy);
        return(Geometry.Floats(gx),Geometry.Floats(gy));
    }
}
