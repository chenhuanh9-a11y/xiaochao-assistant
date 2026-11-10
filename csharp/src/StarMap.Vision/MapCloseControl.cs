using OpenCvSharp;

namespace StarMap.Vision;

public readonly record struct MapCloseControlBounds(int Left,int Top,int Right,int Bottom);
public sealed record MapCloseControlObservation(int Width,int Height,MapCloseControlBounds? Bounds);

/// <summary>Current-client visible X evidence. The search band alone grants no hit.</summary>
public static class MapCloseControl
{
    public static MapCloseControlObservation Observe(Mat? frame)
    {
        if(frame is null || frame.Empty() || frame.Dims!=2 || frame.Depth()!=MatType.CV_8U
            || frame.Channels() is not (3 or 4) || Math.Min(frame.Width,frame.Height)<100)
            return new(frame?.Width??0,frame?.Height??0,null);
        int width=frame.Width,height=frame.Height,left=(int)(width*.88),bottom=(int)(height*.30);
        using var region=new Mat(frame,new Rect(left,0,width-left,bottom));
        using var gray=new Mat();using var blurred=new Mat();using var mask=new Mat();
        Cv2.CvtColor(region,gray,frame.Channels()==4?ColorConversionCodes.BGRA2GRAY:ColorConversionCodes.BGR2GRAY);
        int blur=Math.Max(3,(int)Math.Round(Math.Min(width,height)/280d)|1);
        Cv2.GaussianBlur(gray,blurred,new Size(blur,blur),0);
        Cv2.Threshold(blurred,mask,70,1,ThresholdTypes.Binary);
        using var labels=new Mat();using var stats=new Mat();using var centers=new Mat();
        int count=Cv2.ConnectedComponentsWithStats(mask,labels,stats,centers,PixelConnectivity.Connectivity8);
        MapCloseControlBounds? found=null;
        for(int index=1;index<count;index++)
        {
            int x=stats.At<int>(index,0),y=stats.At<int>(index,1),w=stats.At<int>(index,2),h=stats.At<int>(index,3);
            if(Math.Min(w,h)<.024*height || Math.Max(w,h)>.085*height || (double)w/h<.75 || (double)w/h>1.3)continue;
            using var component=new Mat(mask,new Rect(x,y,w,h));using var shape=new Mat();
            Cv2.Resize(component,shape,new Size(32,32),0,0,InterpolationFlags.Nearest);
            int diagonalCount=0,diagonalSum=0,sideCount=0,sideSum=0;
            for(int row=0;row<32;row++)for(int column=0;column<32;column++)
            {
                double xx=column/31d,yy=row/31d;int value=shape.At<byte>(row,column);
                if(Math.Abs(xx-yy)<.21 || Math.Abs(xx+yy-1)<.21){diagonalCount++;diagonalSum+=value;}
                else{sideCount++;sideSum+=value;}
            }
            if((double)diagonalSum/diagonalCount<.55 || (double)sideSum/sideCount>.22)continue;
            if(found is not null)return new(width,height,null);
            int pad=(int)Math.Round(Math.Min(w,h)*.12);
            found=new(left+x-pad,y-pad,left+x+w+pad,y+h+pad);
        }
        return new(width,height,found);
    }

    public static bool Hit(MapCloseControlObservation? observation,double screenX,double screenY,IReadOnlyList<double> clientBounds)
    {
        if(observation?.Bounds is not { } rect || clientBounds.Count!=4 || !clientBounds.All(double.IsFinite)
            || !double.IsFinite(screenX) || !double.IsFinite(screenY)
            || observation.Width!=clientBounds[2]-clientBounds[0] || observation.Height!=clientBounds[3]-clientBounds[1])return false;
        double x=screenX-clientBounds[0],y=screenY-clientBounds[1];
        return rect.Left<=x && x<rect.Right && rect.Top<=y && y<rect.Bottom;
    }
}
