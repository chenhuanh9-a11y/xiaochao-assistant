using System.Runtime.InteropServices;
using System.Runtime.Intrinsics;
using OpenCvSharp;
using static StarMap.Vision.FloorPixels;

namespace StarMap.Vision;

internal static class FloorObservationGlyphs
{
    private const int W=96,H=64,D=40;
    private static readonly int[] Labels=[1,2,-1,-2,3,4,7,9,-3,-4,-7,-9];
    private sealed record Exemplar(int Label,float[] Values);
    private static readonly Lazy<Exemplar[]> Bank=new(Build);
    internal sealed record Match(int Label,double Score,double Aspect);

    internal static Match? Classify(Mat patch,bool selected)
    {
        if(patch.Empty())return null;
        var pixels=Geometry.Bytes(patch);float mid=(float)Quantile(pixels,.5);
        // Percentile of the signed luminance, before clipping negatives.
        var signed=pixels.Select(v=>selected?mid-v:v-mid).ToArray();
        var ordered=signed.Order().ToArray();
        int lo=(int)Math.Floor((pixels.Length-1)*.99),hi=(int)Math.Ceiling((pixels.Length-1)*.99);
        float frac=(float)((pixels.Length-1)*.99-lo);
        float strength=frac>=.5f ? ordered[hi]-(ordered[hi]-ordered[lo])*(1-frac) : ordered[lo]+(ordered[hi]-ordered[lo])*frac;
        if(strength<12)return null;
        var ink=signed.Select(v=>Math.Clamp(v/strength*255f,0f,255f)).ToArray();
        using var image=Mat.FromPixelData(patch.Rows,patch.Cols,MatType.CV_32FC1,ink);
        using var mask=Geometry.FromBytes(patch.Cols,patch.Rows,ink.Select(v=>(byte)(v>80?1:0)).ToArray());
        var stats=Components(mask);
        int height=patch.Rows,width=patch.Cols;
        var digits=stats.Where(b=>.3*height<b.H && b.H<.95*height).ToList();
        if(digits.Count!=1)return null;
        var digit=digits[0];
        if(digit.X==0||digit.Y==0||digit.X+digit.W>=width||digit.Y+digit.H>=height)return null;
        var marks=stats.Where(b=>b.X+b.W<digit.X && digit.X-b.X-b.W<=.6*digit.H && .2*digit.H<=b.W && b.W<=.9*digit.H &&
            b.H<=.5*digit.H && Math.Abs(b.Y+b.H/2d-(digit.Y+digit.H/2d))<=.3*digit.H).ToArray();
        int signs=marks.Count(b=>b.H<=.25*digit.H && b.W>=2*b.H);
        if(signs>1||signs!=marks.Length)return null;
        double scale=D/(double)digit.H;
        using var affine=Warp(image,W,H,scale,0,60-scale*(digit.X+(digit.W-1)/2d),scale,31.5-scale*(digit.Y+(digit.H-1)/2d));
        using var filtered=new Mat();Cv2.GaussianBlur(affine,filtered,new Size(),.5);
        var vector=Normalize(filtered);float best=float.NegativeInfinity;int label=0;
        foreach(var exemplar in Bank.Value)
        {
            if(signs!=0&&exemplar.Label>0)continue;
            float score=Dot(exemplar.Values,vector);
            if(score>best){best=score;label=exemplar.Label;}
        }
        return new(label,best,digit.W/(double)digit.H);
    }

    private static Exemplar[] Build()
    {
        var rows=new List<Exemplar>();
        foreach(int label in Labels)
            foreach(double aspect in Math.Abs(label)==1?new[]{.12,.18,.24,.30,.36}:new[]{.35,.45,.55,.65,.75})
                foreach(int thickness in new[]{1,2})
                    foreach(double shear in new[]{-.12,0,.12})
                        foreach(var (sw,gap) in label<0?new[]{(.3,.1),(.45,.2),(.6,.2)}:new[]{(.45,.2)})
                        {
                            using var template=Template(label,aspect,thickness,shear,sw,gap);
                            rows.Add(new(label,Normalize(template)));
                        }
        return rows.ToArray();
    }

    private static Mat Template(int label,double aspect,int thickness,double shear,double signWidth,double signGap)
    {
        using var big=new Mat(128,128,MatType.CV_8UC1,Scalar.Black);
        Cv2.PutText(big,Math.Abs(label).ToString(System.Globalization.CultureInfo.InvariantCulture),new(16,96),HersheyFonts.HersheyPlain,3,
            Scalar.All(255),thickness*3,LineTypes.AntiAlias);
        using var binary=Mask(big,20);using var cropped=new Mat(big,Cv2.BoundingRect(binary));
        using var glyph=new Mat();Cv2.Resize(cropped,glyph,new Size(Math.Max(4,R(D*aspect)),D),0,0,InterpolationFlags.Area);
        using var padded=new Mat(D+32,glyph.Cols+32,MatType.CV_8UC1,Scalar.Black);
        using(var region=new Mat(padded,new Rect(16,16,glyph.Cols,D)))glyph.CopyTo(region);
        using var sheared=Warp(padded,padded.Cols,padded.Rows,1,shear,-shear*(16+D/2d),1,0);
        using var mask=Mask(sheared,20);var bounds=Cv2.BoundingRect(mask);
        using var centered=new Mat(H,W,MatType.CV_32FC1,Scalar.Black);
        if(bounds.Width<=W&&bounds.Height<=H)
        {
            using var src=new Mat(sheared,bounds);using var dst=new Mat(centered,new Rect((W-bounds.Width)/2,(H-bounds.Height)/2,bounds.Width,bounds.Height));
            src.ConvertTo(dst,MatType.CV_32FC1);
        }
        using var smooth=new Mat();Cv2.GaussianBlur(centered,smooth,new Size(),.5);
        var result=Warp(smooth,W,H,1,0,12,1,0);
        if(label<0)
        {
            using var threshold=new Mat();Cv2.Compare(result,40,threshold,CmpTypes.GT);
            int sw=R(D*signWidth), left=Cv2.BoundingRect(threshold).X-R(D*signGap)-sw;
            Cv2.Line(result,new Point(left,H/2),new Point(left+sw-1,H/2),Scalar.All(255),3,LineTypes.AntiAlias);
        }
        return result;
    }
    private static Mat Warp(Mat image,int w,int h,double sx,double shear,double tx,double sy,double ty)
    {
        using var affine=Mat.FromArray(new float[,]{{(float)sx,(float)shear,(float)tx},{0,(float)sy,(float)ty}});
        var result=new Mat();Cv2.WarpAffine(image,result,affine,new Size(w,h),InterpolationFlags.Linear);
        return result;
    }

    private static float[] Normalize(Mat mat)
    {
        var v=Geometry.Floats(mat);float mean=Sum(v)/v.Length;
        for(int i=0;i<v.Length;i++) v[i]-=mean;
        float norm=MathF.Sqrt(SquaredNorm(v));
        for(int i=0;i<v.Length;i++) v[i]/=Math.Max(1e-9f,norm);
        return v;
    }

    // The original contiguous float32 norm uses OpenBLAS sdot. Its four 8-wide
    // accumulators are folded to four lanes before horizontal reduction.
    private static float SquaredNorm(float[] values)
    {
        Span<float> lanes=stackalloc float[32];
        lanes.Clear();
        for(int i=0;i<values.Length;i+=32)
            for(int k=0;k<32;k++)
            {
                float x=values[i+k];
                lanes[k]=MathF.FusedMultiplyAdd(x,x,lanes[k]);
            }
        Span<float> four=stackalloc float[4];
        for(int k=0;k<4;k++)
        {
            float a=lanes[k]+lanes[4+k],b=lanes[8+k]+lanes[12+k];
            float c=lanes[16+k]+lanes[20+k],d=lanes[24+k]+lanes[28+k];
            four[k]=(a+b)+(c+d);
        }
        return (four[0]+four[1])+(four[2]+four[3]);
    }
    private static float Sum(ReadOnlySpan<float> a)
    {
        if(a.Length<8){float s=0;foreach(float v in a)s+=v;return s;}
        if(a.Length>128){int n=a.Length/2/8*8;return Sum(a[..n])+Sum(a[n..]);}
        Span<float> t=stackalloc float[8];a[..8].CopyTo(t);int i=8;
        for(;i+7<a.Length;i+=8)for(int j=0;j<8;j++)t[j]+=a[i+j];
        float sum=((t[0]+t[1])+(t[2]+t[3]))+((t[4]+t[5])+(t[6]+t[7]));
        for(;i<a.Length;i++)sum+=a[i];return sum;
    }
    private static float Dot(float[] a,float[] b)
    {
        // NumPy 2.2.6 contiguous-contiguous einsum, float32 SSE baseline:
        // each 16-element block visits four lanes in reverse vector order.
        // Unlike sdot, each multiplication rounds before addition.
        var left=MemoryMarshal.Cast<float,Vector128<float>>(a.AsSpan());
        var right=MemoryMarshal.Cast<float,Vector128<float>>(b.AsSpan());
        var sum=Vector128<float>.Zero;
        for(int i=0;i<left.Length;i+=4)
        {
            sum+=left[i+3]*right[i+3];
            sum+=left[i+2]*right[i+2];
            sum+=left[i+1]*right[i+1];
            sum+=left[i]*right[i];
        }
        return (sum.GetElement(0)+sum.GetElement(1))+(sum.GetElement(2)+sum.GetElement(3));
    }
}
