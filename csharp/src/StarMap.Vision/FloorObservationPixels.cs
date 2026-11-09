using System.Text.Json;
using System.Text.Json.Nodes;
using OpenCvSharp;

namespace StarMap.Vision;

internal static class FloorPixels
{
    internal sealed record Blob(int X,int Y,int W,int H,int Area);
    internal static List<Blob> Components(Mat mask)
    {
        using var labels=new Mat(); using var stats=new Mat(); using var centers=new Mat();
        int count=Cv2.ConnectedComponentsWithStats(mask,labels,stats,centers,PixelConnectivity.Connectivity8);
        var result=new List<Blob>(count-1);
        for (int i=1;i<count;i++) result.Add(new(stats.At<int>(i,0),stats.At<int>(i,1),stats.At<int>(i,2),stats.At<int>(i,3),stats.At<int>(i,4)));
        return result;
    }
    internal static int R(double n)=>Numerics.Rint(n);
    internal static JsonNode? J(object? v)=>JsonSerializer.SerializeToNode(v);
    internal static double N(JsonNode? v)=>Numerics.Scalar(v!);
    internal static double[] Numbers(JsonNode n)=>n.AsArray().Select(v=>N(v)).ToArray();
    internal static string? S(JsonObject o,string k)=>o[k]?.ToString();
    internal static bool Flag(JsonObject o,string k)=>o[k]?.GetValue<bool>()==true;
    internal static void Merge(JsonObject dst,JsonObject src) { foreach(var p in src) dst[p.Key]=p.Value?.DeepClone(); }
    internal static Mat Mask(Mat img,double threshold,bool above=true)
    {
        var mask=new Mat();
        Cv2.Threshold(img,mask,threshold,1,above?ThresholdTypes.Binary:ThresholdTypes.BinaryInv);
        return mask;
    }
    internal static double Quantile(byte[] pixels,double fraction)
    {
        if(pixels.Length==0) return double.NaN;
        Span<int> hist=stackalloc int[256];
        foreach(var p in pixels) hist[p]++;
        double at=(pixels.Length-1)*fraction; int lo=(int)Math.Floor(at),hi=(int)Math.Ceiling(at);
        int seen=0,lower=-1;
        for(int i=0;i<256;i++)
        {
            seen+=hist[i];
            if(lower<0&&seen>lo) lower=i;
            if(seen>hi) return lower+(i-lower)*(at-lo);
        }
        return 255;
    }
    internal static Rect Viewport(int w,int h)
    {
        double aspect=w/(double)h, target=16d/9;
        if(Math.Abs(aspect-target)<=.01) return new(0,0,w,h);
        if(aspect<target){int sh=Math.Clamp(R(w/target),1,h);return new(0,(h-sh)/2,w,sh);}
        int sw=Math.Clamp(R(h*target),1,w);return new((w-sw)/2,0,sw,h);
    }
    internal static Rect Region(Rect v,double x,double y,double rw,double rh)=>Corners(v.X+(int)Math.Floor(x*v.Width),v.Y+(int)Math.Floor(y*v.Height),
        v.X+(int)Math.Ceiling((x+rw)*v.Width),v.Y+(int)Math.Ceiling((y+rh)*v.Height));
    internal static Rect Corners(int l,int t,int r,int b)=>new(l,t,Math.Max(0,r-l),Math.Max(0,b-t));
    internal static Mat Gray(Mat bgr){var gray=new Mat();Cv2.CvtColor(bgr,gray,ColorConversionCodes.BGR2GRAY);return gray;}
    internal static Mat Expose(Mat image,double gain){var result=new Mat();Cv2.ConvertScaleAbs(image,result,gain);return result;}

}
