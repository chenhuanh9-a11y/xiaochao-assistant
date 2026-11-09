using System.Text.Json.Nodes;
using OpenCvSharp;
using static StarMap.Vision.FloorPixels;

namespace StarMap.Vision;

internal static class MapOpenObservationShapes
{
    internal static JsonObject Observe(Mat frame,double floorSearchRight,int minimumHeight)
    {
        int w=frame.Cols,h=frame.Rows;if(w<1000||h<minimumHeight)return new(){["verified"]=false,["reason"]="s1-map-ui-size-unsupported"};
        var v=Viewport(w,h);int right=R(v.X+v.Width*floorSearchRight);
        using var roi=new Mat(frame,new Rect(right,0,w-right,h));using var band=roi.Clone();using var small=Gray(band);
        using var gray=new Mat(h,w,MatType.CV_8UC1,Scalar.Black);
        using(var dst=new Mat(gray,new Rect(right,0,w-right,h)))Cv2.GaussianBlur(small,dst,new Size(),Math.Max(1,v.Width/1920d));
        var shapes=new JsonObject();JsonObject End(bool ok,string reason)=>new(){["verified"]=ok,["reason"]=reason,["shapes"]=shapes};
        foreach(var kind in new[]{"close","plus","minus"})
        {
            var found=Match(gray,v,kind,right);
            if(found==null)return End(false,"s1-map-ui-"+kind+"-unverified");shapes[kind]=found;
        }
        double[] close=Numbers(shapes["close"]!["center"]!),plus=Numbers(shapes["plus"]!["center"]!),minus=Numbers(shapes["minus"]!["center"]!);
        var xs=new[]{close[0],plus[0],minus[0]};
        double gap=(plus[1]-close[1])/v.Width,zoom=(minus[1]-plus[1])/v.Width,ratio=N(shapes["plus"]!["bounds"]![2])/N(shapes["minus"]!["bounds"]![2]);
        if(xs.Max()-xs.Min()>.010*v.Width || gap is <.13 or >.18 || zoom is <.22 or >.28 || ratio is <.85 or >1.15)
            return End(false,"s1-map-ui-group-misaligned");
        int start=R(plus[1]+.025*v.Width),end=R(minus[1]-.025*v.Width),middle=R((plus[0]+minus[0])/2);
        int half=Math.Max(1,R(.002*v.Width)),side=Math.Max(half+1,R(.010*v.Width));double best=-1;int position=0;
        var pixels=Geometry.Bytes(gray);
        if(end>start)
            for(int x=middle-R(.005*v.Width);x<=middle+R(.005*v.Width);x++)
            {
                if(x-side-half<right||x+side+half>=w)continue;int count=0;
                for(int y=start;y<end;y++)
                {
                    float Avg(int cx){int sum=0;for(int xx=cx-half;xx<=cx+half;xx++)sum+=pixels[y*w+xx];return sum/(float)(half*2+1);}
                    if(Avg(x)-Math.Max(Avg(x-side),Avg(x+side))>=12)count++;
                }
                double support=count/(double)(end-start);if(support>best){best=support;position=x;}
            }
        if(best<.85)return End(false,"s1-map-ui-rail-unverified");
        var result=End(true,"s1-map-ui-shape-group");
        result["rail"]=new JsonObject{["support"]=best,["x"]=position,["startY"]=start,["endY"]=end};return result;
    }

    private static JsonObject? Match(Mat image,Rect v,string kind,int right)
    {
        (double y0,double y1)=kind switch{"close"=>(.02,.18),"plus"=>(.25,.46),_=>(.66,.86)};
        var roi=Corners(Math.Max(right,R(v.X+.92*v.Width)),R(v.Y+y0*v.Height),R(v.X+.99*v.Width),R(v.Y+y1*v.Height));
        if(roi.Width==0||roi.Height==0)return null;
        using var gray=new Mat(image,roi);
        var candidates=new List<(double Score,JsonObject Row,double X,double Y)>();
        double threshold=kind=="close"?.62:.65;
        foreach(double frac in new[]{.024,.026,.028,.030,.032,.034,.036,.038})
        {
            int size=Math.Max(12,R(v.Width*frac));
            if(size>=Math.Min(roi.Width,roi.Height))continue;
            using var template=Template(kind,size);using var map=new Mat();Cv2.MatchTemplate(gray,template,map,TemplateMatchModes.CCoeffNormed);
            int n=0;
            for(;n<8;n++)
            {
                Cv2.MinMaxLoc(map,out _,out double score,out _,out Point peak);
                if(!double.IsFinite(score)||score<threshold)break;
                using var patch=new Mat(gray,new Rect(peak.X,peak.Y,size,size));var contrasts=Contrasts(patch,kind);
                if(kind=="close"?contrasts.Min()>=12:contrasts[0]>=12 && (kind=="plus"?contrasts[1]>=12:contrasts[1]<8))
                {
                    int x=roi.X+peak.X,y=roi.Y+peak.Y;double cx=x+size/2d,cy=y+size/2d;
                    candidates.Add((score,new JsonObject{["score"]=score,["bounds"]=J(new[]{x,y,size,size}),["center"]=J(new[]{cx,cy}),["contrasts"]=J(contrasts)},cx,cy));
                }
                int radius=Math.Max(1,R(size*.65));var excluded=new Rect(peak.X-radius,peak.Y-radius,2*radius+1,2*radius+1).Intersect(new(0,0,map.Cols,map.Rows));
                using var block=new Mat(map,excluded);block.SetTo(Scalar.All(-1));
            }
            if(n==8){Cv2.MinMaxLoc(map,out _,out double remain);if(remain>=threshold)return null;}
        }
        if(candidates.Count==0)return null;
        var first=candidates.OrderByDescending(c=>c.Score).First();
        return candidates.Any(c=>Math.Sqrt(Math.Pow(c.X-first.X,2)+Math.Pow(c.Y-first.Y,2))>v.Width*.010)?null:first.Row;
    }

    private static Mat Template(string kind,int n)
    {
        using var raw=new Mat(n,n,MatType.CV_8UC1,Scalar.Black);
        if(kind=="close")
        {
            int p=R(n*.12),t=Math.Max(2,R(n*.20)),end=n-1-p;
            Cv2.Line(raw,new Point(p,p),new(end,end),Scalar.White,t,LineTypes.AntiAlias);
            Cv2.Line(raw,new Point(end,p),new(p,end),Scalar.White,t,LineTypes.AntiAlias);
        }
        else
        {
            var c=new Point(n/2,n/2);Cv2.Circle(raw,c,R(n*.45),Scalar.All(180),-1,LineTypes.AntiAlias);
            int l=R(n*.19),r=n-1-l,t=Math.Max(2,R(n*.12));
            Cv2.Line(raw,new Point(l,c.Y),new(r,c.Y),Scalar.All(30),t,LineTypes.AntiAlias);
            if(kind=="plus")Cv2.Line(raw,new Point(c.X,l),new(c.X,r),Scalar.All(30),t,LineTypes.AntiAlias);
        }
        var result=new Mat();Cv2.GaussianBlur(raw,result,new Size(),Math.Max(.7,n*(kind=="close"?.025:.02)));return result;
    }

    private static double[] Contrasts(Mat patch,string kind)
    {
        using var resized=new Mat();Cv2.Resize(patch,resized,new(64,64),0,0,InterpolationFlags.Area);
        var pixels=Geometry.Bytes(resized);float Mean(Func<double,double,bool> include)
        {
            int sum=0,count=0;
            for(int y=0;y<64;y++)for(int x=0;x<64;x++)if(include((x-31.5)/64,(y-31.5)/64)){sum+=pixels[y*64+x];count++;}
            return sum/(float)count;
        }
        if(kind=="close")
            return new[]{(1,1),(1,-1),(-1,1),(-1,-1)}.Select(s=>{
                bool Q(double x,double y)=>x*s.Item1>.10&&y*s.Item2>.10&&Math.Abs(x)<.39&&Math.Abs(y)<.39;
                return (double)(Mean((x,y)=>Q(x,y)&&Math.Abs(Math.Abs(x)-Math.Abs(y))<.065)-
                    Mean((x,y)=>Q(x,y)&&Math.Abs(Math.Abs(x)-Math.Abs(y))>.17));
            }).ToArray();
        double bg=Mean((x,y)=>Math.Abs(x) is >.12 and <.24 && Math.Abs(y) is >.12 and <.24);
        return [bg-Mean((x,y)=>Math.Abs(y)<.055 && Math.Abs(x) is >.12 and <.25),
            bg-Mean((x,y)=>Math.Abs(x)<.055 && Math.Abs(y) is >.12 and <.25)];
    }
}
