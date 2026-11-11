using OpenCvSharp;
using StarMap.Core.Resources;

namespace StarMap.Vision;

public sealed partial class StructureIndexFilter
{
    internal sealed record LocalSource(string Id,ResourceCatalogEntry? Entry,int Width,int Height,Point[] Doors,string? UnknownReason=null);
    internal sealed record LocalComparison(double[][] Minima,string?[] UnknownReasons,long FieldReads,int ExplainedPoints);
    internal sealed record LocalField(int Width,int Height,float[] Values)
    {
        internal double ReadLowerBound(int x,int y)
        {
            if(x>=0&&y>=0&&x<Width&&y<Height)return Values[y*Width+x];
            long dx=x<0?-(long)x:x>=Width?(long)x-Width+1:0;
            long dy=y<0?-(long)y:y>=Height?(long)y-Height+1:0;
            // Mask3's cheapest unit move is .955. A path from outside to
            // any real edge needs at least this many moves; .95 rounds down.
            // This is a lower bound, not infinity at the local rectangle.
            return .95*Math.Max(dx,dy);
        }
    }

    internal static LocalField PrepareLocalField(int width,int height,byte[] pixels)
    {
        using var mask=Geometry.FromBytes(width,height,pixels);
        using var halo=new Mat();
        Cv2.CopyMakeBorder(mask,halo,1,1,1,1,BorderTypes.Constant,Scalar.All(0));
        using var edge=new Mat();using var kernel=Geometry.Kernel(3);
        Cv2.MorphologyEx(halo,edge,MorphTypes.Gradient,kernel);
        using var inverse=new Mat();Cv2.Threshold(edge,inverse,0,1,ThresholdTypes.BinaryInv);
        using var field=new Mat();Cv2.DistanceTransform(inverse,field,DistanceTypes.L2,DistanceTransformMasks.Mask3);
        return new(field.Cols,field.Rows,Geometry.Floats(field));
    }

    // Same common plane and rounding as dense CandidateMinima. Each native
    // field is read at every declared door offset; no union of solid masks.
    // Groups bound field storage without rebuilding a field for every H batch.
    // OffsetRadiusPx covers an entire translation box by a distance lower bound.
    internal static LocalComparison? CandidateLocalMinima(float[][] points,Hypothesis[] hypotheses,Point origin,
        LocalSource[] sources,Func<LocalSource,LocalField?> acquire,Func<bool> cancelled,double offsetRadiusPx=0,bool stopExplained=true)
    {
        if(!double.IsFinite(offsetRadiusPx)||offsetRadiusPx<0)throw new ArgumentOutOfRangeException(nameof(offsetRadiusPx));
        var minima=sources.Select(_=>Enumerable.Repeat(double.PositiveInfinity,points.Length).ToArray()).ToArray();
        var unknown=sources.Select(source=>source.UnknownReason).ToArray();
        long reads=0;int explained=0;
        if(hypotheses.Length==0)return null;
        const int batchSize=512,groupSize=4;
        int slots=checked(Math.Min(batchSize,hypotheses.Length)*points.Length);
        var xs=new int[slots];var ys=new int[slots];var radii=offsetRadiusPx==0?null:new long[slots];
        for(int group=0;group<sources.Length;group+=groupSize)
        {
            if(cancelled())return null;
            int size=Math.Min(groupSize,sources.Length-group);
            var fields=new LocalField?[size];
            for(int map=0;map<size;map++)
            {
                if(cancelled())return null;
                var source=sources[group+map];
                if(unknown[group+map] is not null)continue;
                if(source.Doors.Length==0){unknown[group+map]="coordinate-anchors-unavailable";continue;}
                try{fields[map]=acquire(source);}
                catch(Exception error)when(error is IOException or UnauthorizedAccessException or InvalidDataException or ArgumentException)
                {unknown[group+map]="coordinate-field-unavailable";}
                if(fields[map] is null)unknown[group+map]??="coordinate-field-unavailable";
            }
            if(fields.All(field=>field is null))continue;
            for(int start=0;start<hypotheses.Length;start+=batchSize)
            {
                if(cancelled())return null;
                var active=new List<int>(points.Length);
                for(int p=0;p<points.Length;p++)
                for(int map=0;map<size;map++)
                    if(fields[map] is not null&&(!stopExplained||minima[group+map][p]!=0)){active.Add(p);break;}
                if(active.Count==0)break; // Every point in this group already has a non-contradiction proof.
                int stop=Math.Min(hypotheses.Length,start+batchSize);
                for(int h=start;h<stop;h++)
                {
                    var item=hypotheses[h];int row=(h-start)*points.Length;
                    foreach(int p in active)
                    {
                        // Do not move integer offsets into RoundToEven or
                        // replace division by reciprocal multiplication.
                        int x=checked((int)Math.Round((points[p][0]-item.X)/item.Scale,MidpointRounding.ToEven));
                        int y=checked((int)Math.Round((points[p][1]-item.Y)/item.Scale,MidpointRounding.ToEven));
                        xs[row+p]=x;ys[row+p]=y;
                        if(radii is not null)
                        {
                            long lx=checked((long)Math.Round((points[p][0]-(item.X+offsetRadiusPx))/item.Scale,MidpointRounding.ToEven));
                            long hx=checked((long)Math.Round((points[p][0]-(item.X-offsetRadiusPx))/item.Scale,MidpointRounding.ToEven));
                            long ly=checked((long)Math.Round((points[p][1]-(item.Y+offsetRadiusPx))/item.Scale,MidpointRounding.ToEven));
                            long hy=checked((long)Math.Round((points[p][1]-(item.Y-offsetRadiusPx))/item.Scale,MidpointRounding.ToEven));
                            radii[row+p]=checked(Math.Max(Math.Abs(lx-x),Math.Abs(hx-x))+Math.Max(Math.Abs(ly-y),Math.Abs(hy-y)));
                        }
                    }
                }
                for(int map=0;map<size;map++)
                {
                    if(cancelled())return null;
                    if(fields[map] is not { } field)continue;
                    var source=sources[group+map];var minimum=minima[group+map];
                    for(int h=start;h<stop;h++)
                    {
                        int row=(h-start)*points.Length;double scale=hypotheses[h].Scale;
                        foreach(int p in active)
                        {
                            if(stopExplained&&minimum[p]==0)continue;
                            foreach(var door in source.Doors)
                            {
                                int x=checked(xs[row+p]+origin.X+door.X+1),y=checked(ys[row+p]+origin.Y+door.Y+1);
                                double bound=field.ReadLowerBound(x,y);reads++;
                                if(radii is not null)bound=Math.Max(0,bound-radii[row+p]);
                                minimum[p]=Math.Min(minimum[p],bound*scale);
                                // A min already <=8 can never become an unavoidable
                                // miss. Zero remains a valid lower bound for all H;
                                // no later sample can change this point's predicate.
                                if(stopExplained&&minimum[p]<=CoordinateMissThresholdPx)
                                {minimum[p]=0;explained++;break;}
                            }
                        }
                    }
                }
            }
        }
        return cancelled()?null:new(minima,unknown,reads,explained);
    }
}
