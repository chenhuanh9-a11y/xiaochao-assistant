namespace StarMap.Vision;

public sealed partial class StructureIndexFilter
{
    // All maps use the same source plane. Only the distance field differs.
    // Keep each map/point's original increasing-hypothesis Math.Min order.
    // A cancelled batch never publishes its partially accumulated minima.
    private static double[][]? CandidateMinima(float[][] points,Hypothesis[] hypotheses,
        int width,int height,float[][] fields,Func<bool> cancelled)
    {
        var minima=new double[fields.Length][];
        for(int map=0;map<fields.Length;map++)
            minima[map]=Enumerable.Repeat(double.PositiveInfinity,points.Length).ToArray();
        const int batchSize=512;
        // With one candidate there is no repeated projection to reuse. Keep
        // the direct loop and avoid the batch buffer/readback overhead.
        if(fields.Length==1)
        {
            var field=fields[0];var minimum=minima[0];
            for(int start=0;start<hypotheses.Length;start+=batchSize)
            {
                if(cancelled())return null;
                int stop=Math.Min(hypotheses.Length,start+batchSize);
                for(int h=start;h<stop;h++)
                {
                    var hypothesis=hypotheses[h];
                    for(int p=0;p<points.Length;p++)
                    {
                        int x=(int)Math.Round((points[p][0]-hypothesis.X)/hypothesis.Scale,MidpointRounding.ToEven);
                        int y=(int)Math.Round((points[p][1]-hypothesis.Y)/hypothesis.Scale,MidpointRounding.ToEven);
                        double distance=x<0 || y<0 || x>=width || y>=height?10000:field[y*width+x]*hypothesis.Scale;
                        minimum[p]=Math.Min(minimum[p],distance);
                    }
                }
            }
            return cancelled()?null:minima;
        }
        var offsets=new int[checked(Math.Min(batchSize,hypotheses.Length)*points.Length)];
        for(int start=0;start<hypotheses.Length;start+=batchSize)
        {
            if(cancelled())return null;
            int stop=Math.Min(hypotheses.Length,start+batchSize);
            for(int h=start;h<stop;h++)
            {
                var hypothesis=hypotheses[h];int row=(h-start)*points.Length;
                for(int p=0;p<points.Length;p++)
                {
                    // Preserve division and ToEven; reciprocal multiplication
                    // would change half-integer boundary decisions.
                    int x=(int)Math.Round((points[p][0]-hypothesis.X)/hypothesis.Scale,MidpointRounding.ToEven);
                    int y=(int)Math.Round((points[p][1]-hypothesis.Y)/hypothesis.Scale,MidpointRounding.ToEven);
                    offsets[row+p]=x<0 || y<0 || x>=width || y>=height?-1:y*width+x;
                }
            }
            for(int map=0;map<fields.Length;map++)
            {
                if(cancelled())return null;
                var field=fields[map];var minimum=minima[map];
                for(int h=start;h<stop;h++)
                {
                    double scale=hypotheses[h].Scale;int row=(h-start)*points.Length;
                    for(int p=0;p<points.Length;p++)
                    {
                        int offset=offsets[row+p];
                        double distance=offset<0?10000:field[offset]*scale;
                        minimum[p]=Math.Min(minimum[p],distance);
                    }
                }
            }
        }
        return cancelled()?null:minima;
    }
}
